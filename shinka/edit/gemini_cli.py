"""Helpers for interacting with the Gemini CLI."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from shinka.tools.codex_session_registry import (
    register_session_process,
    remove_session_process,
    update_session_process,
)
from shinka.edit.cost_utils import calculate_cost

logger = logging.getLogger(__name__)


class GeminiUnavailableError(RuntimeError):
    """Raised when the Gemini CLI binary cannot be located."""


class GeminiExecutionError(RuntimeError):
    """Raised when a Gemini run fails or exceeds configured limits."""


def ensure_gemini_available(gemini_path: Optional[str] = None) -> Path:
    """Return the resolved path to the Gemini CLI binary."""
    candidate = gemini_path or shutil.which("gemini")

    # Fallback: check common NVM paths if not found in PATH
    if not candidate:
        try:
            home = Path.home()
            nvm_versions = home / ".nvm" / "versions" / "node"
            if nvm_versions.exists():
                # Look in all node versions, newest first
                for version_dir in sorted(nvm_versions.iterdir(), reverse=True):
                    bin_path = version_dir / "bin" / "gemini"
                    if bin_path.exists() and bin_path.is_file():
                        candidate = str(bin_path)
                        break
        except Exception:
            pass  # Ignore errors during fallback search

    if not candidate:
        raise GeminiUnavailableError(
            "Gemini CLI not found. Install it (e.g. `npm install -g @google/gemini-cli`) "
            "or add it to PATH."
        )

    resolved = Path(candidate)
    if not resolved.exists() or not resolved.is_file():
        raise GeminiUnavailableError(
            f"Gemini CLI binary not found at resolved path: {resolved}"
        )

    return resolved


def run_gemini_task(
    user_prompt: str,
    workdir: Path,
    *,
    system_prompt: Optional[str] = None,
    profile: Optional[str],
    sandbox: str,  # Ignored, gemini-cli handles its own sandbox via config?
    approval_mode: str,
    max_seconds: int,
    max_events: int,
    extra_cli_config: Dict[str, Any],
    codex_path: Optional[str] = None,  # Used as gemini_path override if needed
    cli_path: Optional[str] = None,  # Alias for codex_path
    resume_session_id: Optional[str] = None,
    session_kind: str = "unknown",
    # Metadata params (unused but accepted for API compat with agentic.py)
    parent_id: Optional[str] = None,
    generation: Optional[int] = None,
    patch_type: Optional[str] = None,
    results_dir: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    """Execute a Gemini CLI task and stream its JSON events with retry on capacity errors."""

    # Use cli_path if provided, fall back to codex_path for backward compat
    binary = ensure_gemini_available(cli_path or codex_path)

    cwd = str(workdir)

    cmd = [str(binary), "--output-format", "stream-json"]

    # Track model name for cost calculation
    model_name = profile or "gemini-2.5-flash"  # Default Gemini model

    if profile:
        cmd.extend(["--model", profile])

    if sandbox and str(sandbox).strip():
        cmd.append("--sandbox")

    if approval_mode == "full-auto" or approval_mode == "yolo":
        cmd.extend(["--approval-mode", "yolo"])
    elif approval_mode == "auto_edit":
        cmd.extend(["--approval-mode", "auto_edit"])
    else:
        cmd.extend(["--approval-mode", "default"])

    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])

    for key, value in extra_cli_config.items():
        # Reserved keys: no_extensions/debug_log handled separately, model is ShinkaAgent-only
        if key in {"no_extensions", "debug_log", "model"}:
            continue
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                cmd.append(f"--{key}")
        else:
            cmd.extend([f"--{key}", str(value)])

    # NOTE: Gemini CLI supports system prompts via the GEMINI_SYSTEM_MD environment variable.
    # When set, it points to a markdown file that REPLACES the default system prompt.
    # In agentic mode, the harness owns the system prompt - we write the combined prompt
    # (custom + AGENTIC_SYS_FORMAT) to a file and set GEMINI_SYSTEM_MD to point to it.

    # Load custom system prompt from shinka config if configured
    custom_system_prompt = None
    selected_system_prompt_file = None
    try:
        from shinka.webui.cli_profiles import GeminiConfigManager
        config_manager = GeminiConfigManager()
        gemini_config = config_manager.load_config()
        if gemini_config.system_prompt:
            custom_system_prompt = gemini_config.system_prompt
            logger.debug(f"Loaded custom system prompt from Gemini shinka config")
        # Check if a specific system prompt file is selected
        if gemini_config.extra_config.get("selected_system_prompt_file"):
            selected_system_prompt_file = gemini_config.extra_config["selected_system_prompt_file"]
            logger.debug(f"Using selected system prompt file: {selected_system_prompt_file}")
    except Exception as e:
        logger.debug(f"Could not load Gemini shinka config: {e}")

    # Build the system prompt file for GEMINI_SYSTEM_MD
    # Priority: selected file > custom prompt + harness prompt
    shinka_sys_path = None
    if selected_system_prompt_file and Path(selected_system_prompt_file).expanduser().exists():
        # Use the selected system prompt file directly
        shinka_sys_path = str(Path(selected_system_prompt_file).expanduser())
        logger.debug(f"Using selected system prompt file: {shinka_sys_path}")
    elif custom_system_prompt or system_prompt:
        # Write combined system prompt to shinka_system.md
        shinka_sys_path = Path.home() / ".gemini" / "shinka_system.md"
        combined_parts = []
        if custom_system_prompt:
            combined_parts.append(custom_system_prompt)
        if system_prompt:
            combined_parts.append(system_prompt)
        combined_system = "\n\n".join(combined_parts)
        try:
            shinka_sys_path.parent.mkdir(parents=True, exist_ok=True)
            shinka_sys_path.write_text(combined_system)
            shinka_sys_path = str(shinka_sys_path)
            logger.debug(f"Wrote combined system prompt to {shinka_sys_path}")
        except Exception as e:
            logger.warning(f"Failed to write system prompt file: {e}")
            shinka_sys_path = None

    # User prompt is passed directly (no prepending of system prompt)
    full_prompt = user_prompt

    max_retries = 5
    attempt = 0

    while True:
        attempt += 1
        start_time = time.monotonic()
        events_emitted = 0
        session_id: Optional[str] = None

        estimated_input_tokens = len(full_prompt) // 4 if full_prompt else 0
        estimated_output_tokens = 0

        # Pass prompt as positional argument; stdin streaming has caused hangs in
        # stream-json mode. This mirrors the CLI usage that works interactively.
        if full_prompt:
            cmd_with_prompt = cmd + [full_prompt]
        else:
            cmd_with_prompt = cmd[:]

        env = {**subprocess.os.environ, "NO_COLOR": "1"}
        if extra_cli_config.get("no_extensions"):
            env["GEMINI_NO_EXTENSIONS"] = "1"
        # Set GEMINI_SYSTEM_MD to point to the system prompt file
        if shinka_sys_path:
            env["GEMINI_SYSTEM_MD"] = shinka_sys_path
            logger.debug(f"Set GEMINI_SYSTEM_MD={shinka_sys_path}")

        # Check if sandbox should be disabled from selected profile
        try:
            from shinka.webui.cli_profiles import get_selected_profiles_manager
            selected_mgr = get_selected_profiles_manager()
            selection = selected_mgr.get_selected("gemini")
            if selection.get("sandbox_disabled"):
                env["GEMINI_SANDBOX"] = "0"
                logger.debug("Disabled Gemini sandbox via GEMINI_SANDBOX=0")
        except Exception as e:
            logger.debug(f"Could not load Gemini selected profile: {e}")

        stdout_capture = None
        stderr_capture = None
        if extra_cli_config.get("debug_log"):
            try:
                raw_dir = Path(cwd)
                stdout_capture = raw_dir / "gemini_stdout.log"
                stderr_capture = raw_dir / "gemini_stderr.log"
                stdout_capture.touch(exist_ok=True)
                stderr_capture.touch(exist_ok=True)
            except Exception:
                stdout_capture = None
                stderr_capture = None

        process = subprocess.Popen(
            cmd_with_prompt,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env,
        )

        prompt_preview = full_prompt.strip().splitlines()[0][:160] if full_prompt else ""
        register_session_process(
            process.pid,
            prompt_preview=prompt_preview,
            workdir=workdir,
            session_kind=session_kind,
            parent_id=parent_id,
            generation=generation,
            patch_type=patch_type,
            results_dir=results_dir,
        )

        pending_tools: Dict[str, Dict[str, Any]] = {}

        try:
            if not process.stdout:
                raise GeminiExecutionError("Gemini CLI did not provide stdout pipe.")

            while True:
                if max_seconds > 0 and time.monotonic() - start_time > max_seconds:
                    process.kill()
                    raise GeminiExecutionError(
                        f"Gemini task exceeded {max_seconds}s timeout."
                    )

                line = process.stdout.readline()
                if not line:
                    if process.poll() is not None:
                        total_tokens = estimated_input_tokens + estimated_output_tokens
                        yield {
                            "type": "usage",
                            "session_id": session_id,
                            "usage": {
                                "input_tokens": estimated_input_tokens,
                                "output_tokens": estimated_output_tokens,
                                "total_tokens": total_tokens,
                                "total_cost_usd": calculate_cost(
                                    model_name,
                                    estimated_input_tokens,
                                    estimated_output_tokens,
                                    "gemini",
                                ),
                            },
                            "model": model_name,
                        }
                        return
                    time.sleep(0.05)
                    continue

                line = line.strip()
                if not line:
                    continue

                if stdout_capture:
                    try:
                        stdout_capture.open("a", encoding="utf-8").write(line + "\n")
                    except Exception:
                        pass

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                events_emitted += 1
                if max_events and events_emitted > max_events:
                    process.kill()
                    raise GeminiExecutionError(
                        "Gemini emitted more events than allowed."
                    )

                event_type = event.get("type")

                if event_type == "init":
                    sid = event.get("session_id")
                    if sid:
                        session_id = sid
                        update_session_process(process.pid, session_id=sid)
                    yield event

                elif event_type == "message":
                    role = event.get("role")
                    if role == "assistant":
                        content = event.get("content")
                        if content:
                            estimated_output_tokens += len(content) // 4
                            yield {
                                "type": "agent_message",
                                "item": {
                                    "type": "agent_message",
                                    "text": content
                                },
                                "session_id": event.get("session_id")
                            }

                elif event_type == "tool_use":
                    tool_id = event.get("tool_id")
                    if tool_id:
                        pending_tools[tool_id] = {
                            "name": event.get("tool_name"),
                            "args": event.get("parameters")
                        }
                    yield event

                elif event_type == "tool_result":
                    tool_id = event.get("tool_id")
                    tool_info = pending_tools.pop(tool_id, {"name": "unknown", "args": {}})

                    status = event.get("status")
                    is_success = status == "success"
                    output = event.get("output") or ""
                    error = event.get("error")

                    if error:
                        err_msg = error.get("message", str(error))
                        if output:
                            output += f"\nError: {err_msg}"
                        else:
                            output = f"Error: {err_msg}"

                    tool_name = tool_info["name"]
                    args = tool_info["args"]
                    command_str = f"{tool_name}({json.dumps(args)})"
                    if tool_name.startswith("run_shell") and "command" in args:
                        command_str = args["command"]

                    estimated_output_tokens += len(output) // 4

                    yield {
                        "type": "command_execution",
                        "item": {
                            "type": "command_execution",
                            "command": command_str,
                            "status": status,
                            "exit_code": 0 if is_success else 1,
                            "stdout": output if is_success else "",
                            "stderr": output if not is_success else ""
                        },
                        "session_id": event.get("session_id")
                    }

                elif event_type == "error":
                    yield {
                        "type": "agent_message",
                        "item": {
                            "type": "agent_message",
                            "text": f"SYSTEM ERROR: {event.get('message')}"
                        }
                    }

        except GeminiExecutionError as exc:
            if process.poll() is None:
                process.kill()
            if attempt < max_retries and "capacity" in str(exc).lower():
                time.sleep(5 * attempt)
                continue
            raise

        finally:
            if process.poll() is None:
                process.kill()
            remove_session_process(process.pid)

            if stderr_capture and process.stderr:
                try:
                    err_tail = process.stderr.read()
                    if err_tail:
                        stderr_capture.open("a", encoding="utf-8").write(err_tail)
                except Exception:
                    pass
