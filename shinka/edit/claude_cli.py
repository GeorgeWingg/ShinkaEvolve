"""Helpers for interacting with the Claude Code CLI."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from shinka.tools.codex_session_registry import (
    register_session_process,
    remove_session_process,
    update_session_process,
)

logger = logging.getLogger(__name__)


class ClaudeUnavailableError(RuntimeError):
    """Raised when the Claude CLI binary cannot be located."""


class ClaudeExecutionError(RuntimeError):
    """Raised when a Claude run fails or exceeds configured limits."""


def ensure_claude_available(claude_path: Optional[str] = None) -> Path:
    """Return the resolved path to the Claude CLI binary.

    Args:
        claude_path: Optional override pointing directly to the CLI executable.

    Raises:
        ClaudeUnavailableError: If the binary cannot be found or executed.

    Returns:
        Path: Absolute path to the Claude CLI binary.
    """
    candidate = claude_path or shutil.which("claude")

    # Fallback: check common NVM paths if not found in PATH
    if not candidate:
        try:
            home = Path.home()
            nvm_versions = home / ".nvm" / "versions" / "node"
            if nvm_versions.exists():
                # Look in all node versions, newest first
                for version_dir in sorted(nvm_versions.iterdir(), reverse=True):
                    bin_path = version_dir / "bin" / "claude"
                    if bin_path.exists() and bin_path.is_file():
                        candidate = str(bin_path)
                        break
        except Exception:
            pass  # Ignore errors during fallback search

    if not candidate:
        raise ClaudeUnavailableError(
            "Claude CLI not found. Install it with `npm install -g @anthropic-ai/claude-code`, "
            "then run `claude` to authenticate."
        )

    resolved = Path(candidate)
    if not resolved.exists() or not resolved.is_file():
        raise ClaudeUnavailableError(
            f"Claude CLI binary not found at resolved path: {resolved}"
        )

    return resolved


def run_claude_task(
    user_prompt: str,
    workdir: Path,
    *,
    system_prompt: Optional[str] = None,
    profile: Optional[str],
    sandbox: str,
    approval_mode: str,
    max_seconds: int,
    max_events: int,
    extra_cli_config: Dict[str, Any],
    codex_path: Optional[str] = None,  # Used as claude_path override
    cli_path: Optional[str] = None,  # Alias for codex_path
    resume_session_id: Optional[str] = None,
    session_kind: str = "unknown",
    registry_workdir: Optional[Path] = None,
    # Metadata params (unused but accepted for API compat with agentic.py)
    parent_id: Optional[str] = None,
    generation: Optional[int] = None,
    patch_type: Optional[str] = None,
    results_dir: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    """Execute a Claude CLI task and stream its JSON events.

    This function matches the AgentRunner protocol signature exactly
    for drop-in compatibility with AgenticEditor and AgenticEvaluator.

    Event types from Claude CLI (stream-json mode):
    - system (subtype: init): Session initialization with session_id, model, tools
    - assistant: Messages with text or tool_use content blocks
    - user: Tool results with stdout/stderr
    - result: Final summary with cost, usage, duration

    Events are normalized to Shinka's expected format:
    - tool_use content → tracked in pending_tools
    - tool_result (user event) → command_execution
    - assistant text → agent_message
    - A synthetic usage event is emitted at session end

    Args:
        user_prompt: Natural language instruction for Claude.
        workdir: Workspace directory Claude should modify.
        system_prompt: Optional system instructions (passed via --system-prompt).
        profile: Optional model name/alias (e.g., 'sonnet', 'opus').
        sandbox: Sandbox policy (maps to --dangerously-skip-permissions for full-auto).
        approval_mode: Either `full-auto` or default.
        max_seconds: Wall-clock guardrail for the Claude process.
        max_events: Maximum number of JSON events to yield before aborting.
        extra_cli_config: Additional config (e.g., debug_log, allowed_tools).
        codex_path: Optional explicit path to the CLI binary.
        resume_session_id: Optional session UUID to resume via -r/--resume.
        session_kind: Label for session registry ("edit" or "eval").

    Raises:
        ClaudeExecutionError: If Claude fails, times out, or exceeds limits.
        ClaudeUnavailableError: If the CLI binary cannot be located.

    Yields:
        Parsed and normalized JSON events.
    """
    # Use cli_path if provided, fall back to codex_path for backward compat
    binary = ensure_claude_available(cli_path or codex_path)
    cwd = str(workdir)

    # Build CLI command
    # -p (--print): Non-interactive mode for piping
    # --output-format stream-json: JSON streaming output
    # --verbose: Required for stream-json output
    cmd = [str(binary), "-p", "--output-format", "stream-json", "--verbose"]

    # Load selected profile settings (model, permissions, thinking_level)
    selected_model = None
    selected_thinking_level = None
    skip_permissions = False

    try:
        from shinka.webui.cli_profiles import get_selected_profiles_manager
        selected_mgr = get_selected_profiles_manager()
        selection = selected_mgr.get_selected("claude")
        selected_model = selection.get("model")
        selected_thinking_level = selection.get("thinking_level")
        # Default to True if not explicitly set to False
        skip_permissions = selection.get("skip_permissions", True)
        logger.debug(f"Claude selected profile: model={selected_model}, thinking={selected_thinking_level}, skip_permissions={skip_permissions}")
    except Exception as e:
        logger.debug(f"Could not load Claude selected profile: {e}")
        skip_permissions = True  # Default to skipping for agentic mode

    # Model selection: explicit profile param > selected_profiles.json model
    model_to_use = profile or selected_model
    if model_to_use:
        cmd.extend(["--model", model_to_use])

    # Permission/sandbox handling
    if approval_mode == "full-auto" or (sandbox and str(sandbox).strip()):
        skip_permissions = True

    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")

    # Session resume
    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])

    # NOTE: Claude CLI supports --system-prompt. In agentic mode, the harness owns
    # the system prompt - task-specific context (task_sys_msg) is included in the
    # user prompt by the sampler. The system_prompt param here contains only
    # operational instructions (AGENTIC_SYS_FORMAT) which we pass via --system-prompt.
    # Claude combines this with its built-in system behavior.
    #
    # Additionally, we load any custom system prompt and allowed_tools from the
    # shinka profile config. This allows users to configure per-agent settings via the UI.

    # Load custom config from shinka profile if configured
    custom_system_prompt = None
    custom_allowed_tools = None
    try:
        from shinka.webui.cli_profiles import ClaudeConfigManager
        config_manager = ClaudeConfigManager()
        claude_config = config_manager.load_config()
        if claude_config.system_prompt:
            custom_system_prompt = claude_config.system_prompt
            logger.debug(f"Loaded custom system prompt from Claude shinka config")
        if claude_config.allowed_tools:
            custom_allowed_tools = claude_config.allowed_tools
            logger.debug(f"Loaded allowed_tools from Claude shinka config: {custom_allowed_tools}")
    except Exception as e:
        logger.debug(f"Could not load Claude shinka config: {e}")

    # Build the combined system prompt: thinking level + custom system prompt (if any) + harness system prompt
    # Thinking level keywords trigger extended thinking in Claude ("think", "think hard", "think harder", "ultrathink")
    thinking_prefix = ""
    if selected_thinking_level:
        thinking_map = {
            "think": "Think step by step.",
            "think_hard": "Think hard about this problem step by step.",
            "think_harder": "Think harder and more carefully about this problem.",
            "ultrathink": "Think very deeply and thoroughly about this problem. Take your time to consider all aspects.",
        }
        if selected_thinking_level in thinking_map:
            thinking_prefix = thinking_map[selected_thinking_level] + "\n\n"
            logger.debug(f"Added thinking prefix for level: {selected_thinking_level}")

    combined_system_prompt = None
    if custom_system_prompt and system_prompt:
        combined_system_prompt = f"{thinking_prefix}{custom_system_prompt}\n\n{system_prompt}"
    elif custom_system_prompt:
        combined_system_prompt = f"{thinking_prefix}{custom_system_prompt}"
    elif system_prompt:
        combined_system_prompt = f"{thinking_prefix}{system_prompt}" if thinking_prefix else system_prompt
    elif thinking_prefix:
        combined_system_prompt = thinking_prefix.strip()

    # Create temp files for system and user prompts to avoid ARG_MAX limits on macOS (~1MB)
    # Claude CLI supports --system-prompt-file for system prompt (print mode only)
    system_prompt_temp_path = None
    user_prompt_temp_path = None

    if combined_system_prompt:
        try:
            fd, system_prompt_temp_path = tempfile.mkstemp(prefix="claude_sysprompt_", suffix=".txt", text=True)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(combined_system_prompt)
            cmd.extend(["--system-prompt-file", system_prompt_temp_path])
        except Exception as e:
            logger.error(f"Failed to create system prompt temp file: {e}")
            raise ClaudeExecutionError(f"Failed to create system prompt temp file: {e}")

    if user_prompt:
        try:
            fd, user_prompt_temp_path = tempfile.mkstemp(prefix="claude_prompt_", text=True)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(user_prompt)
        except Exception as e:
            logger.error(f"Failed to create user prompt temp file: {e}")
            raise ClaudeExecutionError(f"Failed to create user prompt temp file: {e}")

    # Handle extra config flags
    for key, value in extra_cli_config.items():
        # Reserved keys: debug_log is handled separately, model is ShinkaAgent-only
        # allowed_tools is handled via shinka config
        if key in {"debug_log", "model", "allowed_tools"}:
            continue
        if value is None:
            continue
        # Convert snake_case to kebab-case for CLI flags
        flag_name = key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                cmd.append(f"--{flag_name}")
        elif isinstance(value, list):
            # For list args like --allowed-tools
            cmd.extend([f"--{flag_name}", ",".join(str(v) for v in value)])
        else:
            cmd.extend([f"--{flag_name}", str(value)])

    # Apply allowed_tools from shinka config if set
    if custom_allowed_tools:
        cmd.extend(["--allowed-tools", ",".join(custom_allowed_tools)])

    max_retries = 5
    attempt = 0

    try:
        while True:
            attempt += 1
            start_time = time.monotonic()
            events_emitted = 0
            session_id: Optional[str] = None

            # Token tracking for telemetry
            total_input_tokens = 0
            total_output_tokens = 0
            total_cost_usd = 0.0

            # Don't append user_prompt to args - will pipe via stdin (avoids ARG_MAX limits)
            cmd_with_prompt = cmd[:]

            # Environment setup
            env = {**subprocess.os.environ, "NO_COLOR": "1"}

            # Debug logging setup
            stdout_capture = None
            stderr_capture = None
            if extra_cli_config.get("debug_log"):
                try:
                    raw_dir = Path(cwd)
                    stdout_capture = raw_dir / "claude_stdout.log"
                    stderr_capture = raw_dir / "claude_stderr.log"
                    stdout_capture.touch(exist_ok=True)
                    stderr_capture.touch(exist_ok=True)
                except Exception:
                    stdout_capture = None
                    stderr_capture = None

            # Open user prompt file for piping to stdin
            # Use try/finally to ensure handle is closed even if Popen fails
            prompt_file_handle = None
            try:
                if user_prompt_temp_path:
                    try:
                        prompt_file_handle = open(user_prompt_temp_path, 'r', encoding='utf-8')
                    except Exception as e:
                        logger.error(f"Failed to open user prompt temp file for reading: {e}")
                        raise ClaudeExecutionError(f"Failed to open user prompt temp file: {e}")

                process = subprocess.Popen(
                    cmd_with_prompt,
                    stdin=prompt_file_handle if prompt_file_handle else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=cwd,
                    env=env,
                )
            finally:
                # Close our handle to the file; Popen has its own duplicate
                if prompt_file_handle:
                    prompt_file_handle.close()
                    prompt_file_handle = None

            lines = user_prompt.strip().splitlines() if user_prompt else []
            prompt_preview = lines[0][:160] if lines else ""
            register_session_process(
                process.pid,
                prompt_preview=prompt_preview,
                workdir=registry_workdir or workdir,
                session_kind=session_kind,
                parent_id=parent_id,
                generation=generation,
                patch_type=patch_type,
                results_dir=results_dir,
            )

            # Track pending tool calls for result correlation
            pending_tools: Dict[str, Dict[str, Any]] = {}

            try:
                if not process.stdout:
                    raise ClaudeExecutionError("Claude CLI did not provide stdout pipe.")

                while True:
                    if max_seconds > 0 and time.monotonic() - start_time > max_seconds:
                        process.kill()
                        raise ClaudeExecutionError(
                            f"Claude task exceeded {max_seconds}s timeout."
                        )

                    line = process.stdout.readline()
                    if not line:
                        if process.poll() is not None:
                            # Check exit code before returning
                            exit_code = process.returncode
                            if exit_code != 0:
                                stderr_content = ""
                                try:
                                    if process.stderr:
                                        stderr_content = process.stderr.read()
                                except Exception:
                                    pass
                                raise ClaudeExecutionError(
                                    f"Claude process exited with code {exit_code}. "
                                    f"Stderr: {stderr_content[:500] if stderr_content else 'N/A'}"
                                )
                            # Emit usage event at end
                            yield {
                                "type": "usage",
                                "session_id": session_id,
                                "usage": {
                                    "input_tokens": total_input_tokens,
                                    "output_tokens": total_output_tokens,
                                    "total_tokens": total_input_tokens + total_output_tokens,
                                    "total_cost_usd": total_cost_usd,
                                },
                            }
                            return
                        time.sleep(0.05)
                        continue

                    line = line.strip()
                    if not line:
                        continue

                    # Debug capture
                    if stdout_capture:
                        try:
                            with stdout_capture.open("a", encoding="utf-8") as f:
                                f.write(line + "\n")
                        except Exception:
                            pass

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    events_emitted += 1
                    if max_events and events_emitted > max_events:
                        process.kill()
                        raise ClaudeExecutionError(
                            "Claude emitted more events than allowed."
                        )

                    event_type = event.get("type")

                    # Handle system init event
                    if event_type == "system" and event.get("subtype") == "init":
                        sid = event.get("session_id")
                        if sid:
                            session_id = sid
                            update_session_process(process.pid, session_id=sid)
                        yield {
                            "type": "init",
                            "session_id": sid,
                            "model": event.get("model"),
                            "tools": event.get("tools", []),
                        }

                    # Handle assistant messages
                    elif event_type == "assistant":
                        message = event.get("message", {})
                        content_blocks = message.get("content", [])
                        usage = message.get("usage", {})

                        # Track usage from each assistant message
                        total_input_tokens += usage.get("input_tokens", 0)
                        total_output_tokens += usage.get("output_tokens", 0)

                        for block in content_blocks:
                            block_type = block.get("type")

                            if block_type == "text":
                                text = block.get("text", "")
                                if text:
                                    yield {
                                        "type": "agent_message",
                                        "item": {
                                            "type": "agent_message",
                                            "text": text,
                                        },
                                        "session_id": session_id,
                                    }

                            elif block_type == "tool_use":
                                tool_id = block.get("id")
                                tool_name = block.get("name")
                                tool_input = block.get("input", {})

                                if tool_id:
                                    pending_tools[tool_id] = {
                                        "name": tool_name,
                                        "input": tool_input,
                                    }

                                yield {
                                    "type": "tool_use",
                                    "tool_id": tool_id,
                                    "tool_name": tool_name,
                                    "parameters": tool_input,
                                    "session_id": session_id,
                                }

                    # Handle user messages (tool results)
                    elif event_type == "user":
                        message = event.get("message", {})
                        content_blocks = message.get("content", [])
                        tool_use_result = event.get("tool_use_result")

                        for block in content_blocks:
                            if block.get("type") == "tool_result":
                                tool_id = block.get("tool_use_id")
                                tool_content = block.get("content", "")
                                is_error = block.get("is_error", False)

                                # Get tool info from pending
                                tool_info = pending_tools.pop(tool_id, {"name": "unknown", "input": {}})
                                tool_name = tool_info.get("name", "unknown")
                                tool_input = tool_info.get("input", {})

                                # Extract stdout/stderr from tool_use_result if available
                                # tool_use_result may be a dict with stdout/stderr, or a string, or None
                                stdout = ""
                                stderr = ""
                                if isinstance(tool_use_result, dict):
                                    stdout = tool_use_result.get("stdout", "")
                                    stderr = tool_use_result.get("stderr", "")
                                elif isinstance(tool_use_result, str):
                                    # If it's a string, use it as stdout
                                    stdout = tool_use_result

                                # Build command string
                                if tool_name == "Bash" and "command" in tool_input:
                                    command_str = tool_input["command"]
                                else:
                                    command_str = f"{tool_name}({json.dumps(tool_input)})"

                                # Use content if stdout/stderr not available
                                if not stdout and not stderr:
                                    if is_error:
                                        stderr = tool_content
                                    else:
                                        stdout = tool_content

                                yield {
                                    "type": "command_execution",
                                    "item": {
                                        "type": "command_execution",
                                        "command": command_str,
                                        "status": "error" if is_error else "success",
                                        "exit_code": 1 if is_error else 0,
                                        "stdout": stdout,
                                        "stderr": stderr,
                                    },
                                    "session_id": session_id,
                                }

                    # Handle final result
                    elif event_type == "result":
                        total_cost_usd = event.get("total_cost_usd", 0.0)
                        usage = event.get("usage", {})
                        total_input_tokens = usage.get("input_tokens", total_input_tokens)
                        total_output_tokens = usage.get("output_tokens", total_output_tokens)

                        # Pass through result for logging
                        yield event

            except ClaudeExecutionError as exc:
                if process.poll() is None:
                    process.kill()
                # Retry on capacity/rate-limit errors
                err_str = str(exc).lower()
                if attempt < max_retries and ("capacity" in err_str or "rate" in err_str or "overloaded" in err_str):
                    time.sleep(5 * attempt)
                    continue
                raise

            finally:
                if process.poll() is None:
                    process.kill()
                remove_session_process(process.pid)

                # Capture stderr for debugging
                if stderr_capture and process.stderr:
                    try:
                        err_tail = process.stderr.read()
                        if err_tail:
                            with stderr_capture.open("a", encoding="utf-8") as f:
                                f.write(err_tail)
                    except Exception:
                        pass
    finally:
        # Clean up temp files
        for temp_path in [system_prompt_temp_path, user_prompt_temp_path]:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
