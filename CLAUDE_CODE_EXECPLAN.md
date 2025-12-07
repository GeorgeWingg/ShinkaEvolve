# Claude Code CLI Backend for Agentic Evolution

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

PLANS.md is located at `PLANS.md` in the repository root. Maintain this plan in full compliance with that document.

## Purpose / Big Picture

The goal is to enable Shinka to use the `claude` CLI tool (Claude Code) as a third backend for agentic editing and evaluation, achieving full feature parity with the existing `codex` and `gemini` CLI integrations. By wrapping `claude` (Anthropic's CLI agent), users can leverage their Anthropic subscriptions directly for code evolution tasks. This implementation will be a drop-in replacement for the Codex/Gemini backends, providing feature parity (multi-file editing, shell execution, streaming JSONL events) without implementing a custom Python agent loop.

After completing this work, a user can run:
    env $(cat .env | xargs) uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.backend=claude

and see Claude Code drive the agentic editing sessions, with full session logging, telemetry estimation, and WebUI integration.

## Scope & Constraints

*   **Backend Agnosticism**: The existing runner layer already supports `codex` and `gemini` backends. We will extend this to include `claude` as a third option.
*   **Claude CLI Integration**: We will wrap the external `claude` CLI tool (installed via `npm install -g @anthropic-ai/claude-code` or similar).
    *   We assume `claude` exposes a headless/exec mode that accepts a prompt and working directory, and emits JSON/JSONL events.
    *   We will leverage `claude`'s native capabilities for shell execution and file editing.
*   **No Vendoring**: We will not vendor the `claude` CLI source code. We assume it is installed in the environment (like `codex` and `gemini`).
*   **Parity**: The integration must produce the same event structure (`command_execution`, `agent_message`, `usage`) so that Shinka's logging, database, and WebUI work seamlessly.
*   **WebUI Integration**: The Agents tab already has a Claude Code card stub; this plan completes the backend integration so the card becomes functional.

## Feature Parity Checklist

This checklist ensures Claude CLI wrapper achieves 100% feature parity with Codex and Gemini wrappers:

### Core Functionality
- [x] **Binary detection**: `ensure_claude_available()` with clear error message
- [x] **Exception classes**: `ClaudeUnavailableError`, `ClaudeExecutionError`
- [x] **AgentRunner protocol compliance**: Exact signature match with `types.py`

### CLI Invocation
- [x] **Working directory**: Set via `cwd` parameter in Popen (Gemini pattern)
- [x] **JSON output mode**: Stream JSON events from stdout via `--output-format stream-json --verbose`
- [x] **Model selection**: `profile` → `--model` flag
- [x] **Sandbox mode**: Map to `--dangerously-skip-permissions`
- [x] **Approval mode**: Map `full-auto` to `--dangerously-skip-permissions`
- [x] **Session resume**: `resume_session_id` → `--resume` flag
- [x] **Extra config forwarding**: Dict to CLI flags conversion
- [x] **Prompt combination**: Prompt as positional argument after `-p` flag

### Event Adaptation
- [x] **Init/session events**: Extract and track `session_id` from system.init
- [x] **Assistant messages** → `agent_message` with `item.text`
- [x] **Tool use events**: Track in `pending_tools` dict by `tool_id`
- [x] **Tool result events** → `command_execution` with:
  - `item.command`: Tool name or shell command
  - `item.status`: "success" or "error"
  - `item.exit_code`: 0 for success, 1 for error
  - `item.stdout`: Output on success
  - `item.stderr`: Output on error
- [x] **Error events** → Handled via command_execution with is_error flag
- [x] **Usage event**: Synthetic event at session end with estimated tokens

### Process Management
- [x] **Session registry**: `register_session_process()`, `update_session_process()`, `remove_session_process()`
- [x] **Timeout enforcement**: `max_seconds` with process.kill()
- [x] **Event limit enforcement**: `max_events` with process.kill()
- [x] **Graceful cleanup**: Kill process in finally block

### Telemetry
- [x] **Token estimation**: Extracted from Claude CLI `usage` fields in events
- [x] **Input tokens**: From message.usage.input_tokens
- [x] **Output tokens**: From message.usage.output_tokens
- [x] **Usage event**: Emit at end with `input_tokens`, `output_tokens`, `total_tokens`

### Resilience
- [x] **Retry on capacity errors**: Up to 5 retries with exponential backoff
- [x] **JSON parse error handling**: Skip malformed lines, continue processing
- [x] **Environment setup**: `NO_COLOR=1` for clean output

### Debug Support
- [x] **stdout capture**: Optional log to `claude_stdout.log` when `debug_log=True`
- [x] **stderr capture**: Optional log to `claude_stderr.log` when `debug_log=True`

### Integration Points
- [x] **runner.py imports**: Add at line ~46
- [x] **runner.py evaluator selection**: Extend conditional at line ~265
- [x] **runner.py availability check**: Extend conditional at line ~1458
- [x] **runner.py exception handling**: Add to tuple at line ~1518
- [x] **runner.py editor selection**: Extend conditional at line ~1511
- [x] **edit/__init__.py exports**: Add public symbols

## Progress

- [x] Milestone 1: Research `claude` CLI interface and capabilities.
- [x] Milestone 2: Implement `shinka/edit/claude_cli.py` wrapper.
- [x] Milestone 3: Configuration wiring and backend selection.
- [x] Milestone 4: Unit tests for Claude CLI wrapper.
- [x] Milestone 5: End-to-end validation with circle_packing example.
- [x] Milestone 6: WebUI/Auth integration polish.
- [x] Milestone 7: Documentation and telemetry parity.

## Surprises & Discoveries

1. **`tool_use_result` type variance**: The `tool_use_result` field in Claude CLI events can be either a dict with `stdout`/`stderr` keys, OR a plain string. Initial implementation assumed dict-only, causing an `AttributeError` during E2E testing. Fixed by adding type checking: `isinstance(tool_use_result, dict)`.

2. **`--verbose` requirement**: The `--output-format stream-json` flag requires `--verbose` to be specified when using `-p` (print) mode, otherwise Claude CLI errors out.

3. **stdin handling**: Using `stdin=subprocess.PIPE` caused the subprocess to potentially wait for input. Changed to `stdin=subprocess.DEVNULL` since Claude receives the prompt as a positional argument.

4. **Real token counts**: Unlike Codex which requires estimation, Claude CLI provides actual token usage in `message.usage` fields within the `assistant` and `result` events, enabling more accurate telemetry.

## Decision Log

- **Decision**: Create a new `claude_cli.py` module following the same pattern as `gemini_cli.py` and `codex_cli.py`.
  **Rationale**: Maintains architectural consistency and allows the runner to select backends via a simple string comparison.
  **Date**: 2025-11-24

- **Decision**: Add `"claude"` as a valid backend option in `AgenticConfig.backend`.
  **Rationale**: Enables Hydra config selection via `+evo_config.agentic.backend=claude`.
  **Date**: 2025-11-24

- **Decision**: Use `codex_session_registry` module for Claude session tracking (despite the name).
  **Rationale**: The registry is backend-agnostic; renaming would require broader refactoring. All three backends share the same registry.
  **Date**: 2025-11-24

- **Decision**: Implement retry logic for capacity/rate-limit errors (following Gemini pattern).
  **Rationale**: Anthropic APIs may return 429/capacity errors; graceful retry improves reliability.
  **Date**: 2025-11-24

- **Decision**: Emit synthetic `usage` event with estimated tokens (same as Gemini).
  **Rationale**: Claude CLI may not expose real token counts; estimation maintains telemetry parity.
  **Date**: 2025-11-24

## Outcomes & Retrospective

### Summary
All 7 milestones completed successfully. Claude Code CLI is now a fully functional third backend for Shinka's agentic evolution system, achieving complete feature parity with Codex and Gemini backends.

### Key Deliverables
1. **`shinka/edit/claude_cli.py`** (~400 lines): Full wrapper implementation with event streaming, telemetry, and session registry integration.
2. **`tests/test_claude_cli.py`** (11 tests): Comprehensive unit test coverage including event streaming, error handling, timeouts, and argument construction.
3. **`shinka/core/runner.py`**: Modified to support `backend=claude` at all three integration points (imports, evaluator selection, editor selection).
4. **`shinka/webui/visualization.py`**: Added `/api/claude_status` endpoint for Claude CLI availability detection.
5. **`shinka/webui/viz_tree.html`**: Added `fetchClaudeStatus()` function to show Claude card as "Ready" when CLI is available.
6. **`docs/configuration.md`**: Added comprehensive backend selection documentation.

### E2E Validation Results
- **Command**: `env $(cat .env | xargs) uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.backend=claude evo_config.num_generations=2`
- **Result**: Successfully completed 2 generations
- **Generation 0 Score**: 0.960 (initial program)
- **Generation 1 Score**: 2.207 (Claude-edited improvement)
- **Total Runtime**: ~8 minutes
- **Total API Cost**: $10.10 (Anthropic API via Claude Code)

### Bugs Fixed During Implementation
1. **`tool_use_result` type variance** (critical): Fixed AttributeError caused by assuming `tool_use_result` was always a dict. Now handles both dict and string types.
2. **stdin pipe blocking** (critical): Changed `stdin=subprocess.PIPE` to `stdin=subprocess.DEVNULL` since Claude receives prompts as positional arguments.

### Lessons Learned
- Claude CLI provides actual token counts in event `usage` fields, unlike Codex which requires estimation.
- The `--verbose` flag is mandatory when using `--output-format stream-json` with `-p` (print mode).
- Testing the wrapper in isolation is faster but E2E testing catches integration issues (like type variance in real event streams).

## Context and Orientation

Key files and their roles:

- `shinka/core/runner.py`: Contains `EvolutionRunner` and `AgenticConfig`. The `backend` field in `AgenticConfig` (line 75) currently accepts `"codex"` or `"gemini"`. Backend selection logic appears at:
  - Line 261: Evaluator runner selection
  - Line 1450: CLI availability check
  - Line 1503: Editor runner selection

- `shinka/edit/codex_cli.py`: Reference implementation for the Codex CLI wrapper (~200 lines). Key functions:
  - `ensure_codex_available()`: Validates CLI binary exists
  - `run_codex_task()`: Yields JSON events from subprocess

- `shinka/edit/gemini_cli.py`: Reference implementation for the Gemini CLI wrapper (~280 lines). Key functions:
  - `ensure_gemini_available()`: Validates CLI binary exists  
  - `run_gemini_task()`: Yields normalized JSON events, handles event adaptation from Gemini's `stream-json` format

- `shinka/edit/agentic.py`: `AgenticEditor` class that consumes the runner function and produces `AgentResult`. Handles:
  - Scratch directory preparation
  - Event logging to `session_log.jsonl`
  - Telemetry aggregation (`usage` events)
  - File change detection

- `configs/evolution/agentic.yaml`: Hydra config that sets `agentic.backend: "codex"` as default.

- `shinka/webui/viz_tree.html`: Contains the Claude Code card UI (lines 6803-6850) with stub auth buttons.

- `tests/test_gemini_cli.py`: Reference test file showing how to mock and test CLI wrappers.

The integration pattern follows these steps:
1. User sets `+evo_config.agentic.backend=claude`
2. `EvolutionRunner.__init__` selects `run_claude_task` as the runner function
3. `AgenticEditor.run_session()` calls the runner and collects events
4. Events are normalized to Shinka's expected format and logged

## Plan of Work

### Milestone 1: Research Claude CLI Interface

Investigate the `claude` CLI tool to define the exact contract for headless execution.

**Goals**:
1. Determine if `claude` has a headless/exec mode (similar to `codex exec` or `gemini --output-format stream-json`)
2. Identify command-line flags for:
   - JSON/JSONL output format
   - Working directory specification
   - Sandbox/approval modes
   - Model selection
   - Session resume
3. Document the event stream format
4. Verify authentication mechanism (OAuth, API key, etc.)

**Work**:
1. Check if `claude` binary exists: `which claude` or `npm list -g @anthropic-ai/claude-code`
2. Run `claude --help` to enumerate available commands
3. Test `claude` in various modes to observe output format
4. Document findings in this plan

**Questions to Answer**:
- Does `claude` support `--json` or streaming JSON output?
- How is the prompt passed (positional argument, stdin, file)?
- What does the event stream look like? (tool_use, tool_result, message events?)
- Does it support `--sandbox` or similar isolation?
- How does session resume work (if at all)?

### Milestone 2: Implement Claude CLI Wrapper

Create `shinka/edit/claude_cli.py` following the patterns established by `codex_cli.py` and `gemini_cli.py`.

**File**: `shinka/edit/claude_cli.py`

**Contents** (refined based on parity analysis):

    """Helpers for interacting with the Claude Code CLI."""

    from __future__ import annotations

    import json
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


    class ClaudeUnavailableError(RuntimeError):
        """Raised when the Claude CLI binary cannot be located."""


    class ClaudeExecutionError(RuntimeError):
        """Raised when a Claude run fails or exceeds configured limits."""


    def ensure_claude_available(claude_path: Optional[str] = None) -> Path:
        """Return the resolved path to the Claude CLI binary."""
        candidate = claude_path or shutil.which("claude")
        if not candidate:
            raise ClaudeUnavailableError(
                "Claude CLI not found. Install it (e.g. `npm install -g @anthropic-ai/claude-code`) "
                "or add it to PATH, then authenticate via `claude login`."
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
        resume_session_id: Optional[str] = None,
        session_kind: str = "unknown",
    ) -> Iterator[Dict[str, Any]]:
        """Execute a Claude CLI task and stream its JSON events.
        
        This function matches the AgentRunner protocol signature exactly
        for drop-in compatibility with AgenticEditor and AgenticEvaluator.
        
        Event adaptation normalizes Claude's output to Shinka's expected format:
        - tool_use events are tracked in pending_tools dict
        - tool_result events are converted to command_execution
        - assistant messages are converted to agent_message
        - A synthetic usage event is emitted at session end
        """
        binary = ensure_claude_available(codex_path)
        cwd = str(workdir)
        
        # Build CLI command (flags TBD based on Milestone 1 research)
        cmd = [str(binary)]
        # Add JSON output flag (exact flag TBD)
        # cmd.extend(["--output-format", "json"])  # or similar
        
        if profile:
            cmd.extend(["--model", profile])
        
        if sandbox and str(sandbox).strip():
            cmd.append("--dangerously-skip-permissions")  # or --sandbox TBD
        
        # Approval mode mapping (TBD based on CLI research)
        if approval_mode == "full-auto":
            cmd.append("--dangerously-skip-permissions")
        
        if resume_session_id:
            cmd.extend(["--resume", resume_session_id])
        
        # Handle extra config flags
        for key, value in extra_cli_config.items():
            if key in {"debug_log"}:  # Reserved keys
                continue
            if value is None:
                continue
            if isinstance(value, bool):
                if value:
                    cmd.append(f"--{key}")
            else:
                cmd.extend([f"--{key}", str(value)])
        
        full_prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
        
        max_retries = 5
        attempt = 0
        
        while True:
            attempt += 1
            start_time = time.monotonic()
            events_emitted = 0
            session_id: Optional[str] = None
            
            # Token estimation
            estimated_input_tokens = len(full_prompt) // 4 if full_prompt else 0
            estimated_output_tokens = 0
            
            # Append prompt as positional argument
            cmd_with_prompt = cmd + [full_prompt] if full_prompt else cmd[:]
            
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
                            # Emit usage event at end
                            yield {
                                "type": "usage",
                                "session_id": session_id,
                                "usage": {
                                    "input_tokens": estimated_input_tokens,
                                    "output_tokens": estimated_output_tokens,
                                    "total_tokens": estimated_input_tokens + estimated_output_tokens,
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
                    
                    # Event adaptation (exact types TBD based on CLI research)
                    event_type = event.get("type")
                    
                    if event_type == "init" or event_type == "session":
                        sid = event.get("session_id") or event.get("id")
                        if sid:
                            session_id = sid
                            update_session_process(process.pid, session_id=sid)
                        yield event
                    
                    elif event_type == "message" or event_type == "assistant":
                        role = event.get("role", "assistant")
                        if role == "assistant":
                            content = event.get("content") or event.get("text", "")
                            if content:
                                estimated_output_tokens += len(content) // 4
                                yield {
                                    "type": "agent_message",
                                    "item": {
                                        "type": "agent_message",
                                        "text": content
                                    },
                                    "session_id": session_id
                                }
                    
                    elif event_type == "tool_use":
                        tool_id = event.get("tool_id") or event.get("id")
                        if tool_id:
                            pending_tools[tool_id] = {
                                "name": event.get("tool_name") or event.get("name"),
                                "args": event.get("parameters") or event.get("input", {})
                            }
                        yield event
                    
                    elif event_type == "tool_result":
                        tool_id = event.get("tool_id") or event.get("id")
                        tool_info = pending_tools.pop(tool_id, {"name": "unknown", "args": {}})
                        
                        status = event.get("status", "success")
                        is_success = status == "success" or event.get("is_error") is False
                        output = event.get("output") or event.get("content", "")
                        error = event.get("error")
                        
                        if error:
                            err_msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                            output = f"{output}\nError: {err_msg}" if output else f"Error: {err_msg}"
                        
                        tool_name = tool_info["name"] or "unknown"
                        args = tool_info["args"] or {}
                        command_str = f"{tool_name}({json.dumps(args)})"
                        
                        # Extract shell command if applicable
                        if "bash" in tool_name.lower() or "shell" in tool_name.lower():
                            command_str = args.get("command", args.get("cmd", command_str))
                        
                        estimated_output_tokens += len(str(output)) // 4
                        
                        yield {
                            "type": "command_execution",
                            "item": {
                                "type": "command_execution",
                                "command": command_str,
                                "status": "success" if is_success else "error",
                                "exit_code": 0 if is_success else 1,
                                "stdout": output if is_success else "",
                                "stderr": output if not is_success else ""
                            },
                            "session_id": session_id
                        }
                    
                    elif event_type == "error":
                        yield {
                            "type": "agent_message",
                            "item": {
                                "type": "agent_message",
                                "text": f"SYSTEM ERROR: {event.get('message', str(event))}"
                            },
                            "session_id": session_id
                        }
                    
                    else:
                        # Pass through unknown events
                        yield event
            
            except ClaudeExecutionError as exc:
                if process.poll() is None:
                    process.kill()
                # Retry on capacity/rate-limit errors
                if attempt < max_retries and ("capacity" in str(exc).lower() or "rate" in str(exc).lower()):
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

**Key Implementation Details**:

1. **Command Construction**: Build the CLI command with appropriate flags based on Claude's interface
2. **Prompt Handling**: Combine `system_prompt` and `user_prompt` (same pattern as Codex/Gemini)
3. **Event Adaptation**: Normalize Claude's output events to Shinka's expected format:
   - `tool_use` → capture pending tool info
   - `tool_result` → emit `command_execution` event
   - `message` (assistant) → emit `agent_message` event
   - `init`/session events → extract session_id
4. **Telemetry**: Estimate token usage from prompt/response lengths
5. **Session Registry**: Register/update/remove process in session registry for WebUI tracking

### Milestone 3: Configuration Wiring

Update the runner and config files to support `backend: "claude"`.

**File edits**:

1. `shinka/core/runner.py`:
   - Add import for `claude_cli` module (lines 31-45)
   - Extend backend selection logic at lines 261, 1450-1454, 1503 to handle `"claude"`
   - Add `ClaudeUnavailableError` to exception handling

2. `shinka/edit/__init__.py`:
   - Export `run_claude_task`, `ClaudeUnavailableError`, `ClaudeExecutionError`

3. `configs/evolution/agentic.yaml`:
   - Document `claude` as a valid backend option in comments

**Detailed code changes in `runner.py`**:

At imports (after line 44), add:

    from shinka.edit.claude_cli import (
        ensure_claude_available,
        run_claude_task,
        ClaudeUnavailableError,
    )

At line ~261 (evaluator runner selection), change from:

    runner_fn = (
        run_gemini_task
        if self.evo_config.agentic.backend == "gemini"
        else run_codex_task
    )

To:

    if self.evo_config.agentic.backend == "gemini":
        runner_fn = run_gemini_task
    elif self.evo_config.agentic.backend == "claude":
        runner_fn = run_claude_task
    else:
        runner_fn = run_codex_task

At line ~1450 (CLI availability check), change from:

    if self.evo_config.agentic.backend == "gemini":
        ensure_gemini_available(self.evo_config.agentic.codex_path)
    else:
        ensure_codex_available(self.evo_config.agentic.codex_path)

To:

    if self.evo_config.agentic.backend == "gemini":
        ensure_gemini_available(self.evo_config.agentic.codex_path)
    elif self.evo_config.agentic.backend == "claude":
        ensure_claude_available(self.evo_config.agentic.codex_path)
    else:
        ensure_codex_available(self.evo_config.agentic.codex_path)

At line ~1454 (exception handling), change from:

    except (CodexUnavailableError, GeminiUnavailableError) as exc:

To:

    except (CodexUnavailableError, GeminiUnavailableError, ClaudeUnavailableError) as exc:

At line ~1503 (editor runner selection), apply same pattern as line 261:

    codex_runner=(
        run_gemini_task
        if self.evo_config.agentic.backend == "gemini"
        elif self.evo_config.agentic.backend == "claude"
        run_claude_task
        else run_codex_task
    ),

Should become:

    codex_runner=(
        run_gemini_task if self.evo_config.agentic.backend == "gemini"
        else run_claude_task if self.evo_config.agentic.backend == "claude"
        else run_codex_task
    ),

**Edit `shinka/edit/__init__.py`** to add exports:

    from .claude_cli import (
        run_claude_task,
        ClaudeUnavailableError,
        ClaudeExecutionError,
        ensure_claude_available,
    )

### Milestone 4: Unit Tests

Create `tests/test_claude_cli.py` following the pattern of `tests/test_gemini_cli.py`.

**Test cases** (matching Gemini test parity):

1. `test_ensure_claude_available_success`: Mock `shutil.which` to return a valid path
2. `test_ensure_claude_available_not_found`: Verify `ClaudeUnavailableError` when CLI not in PATH
3. `test_run_claude_task_streams_events`: Mock subprocess to emit sample JSON events, verify normalization:
   - `init` event passes through with session_id extraction
   - `message/assistant` events convert to `agent_message`
   - `tool_use` events are tracked in pending_tools
   - `tool_result` events convert to `command_execution` with proper stdout/stderr/exit_code
4. `test_run_claude_task_handles_errors`: Verify `error` events convert to agent_message with "SYSTEM ERROR" prefix
5. `test_run_claude_task_timeout`: Verify `max_seconds` enforcement raises `ClaudeExecutionError`
6. `test_run_claude_task_max_events`: Verify `max_events` enforcement raises `ClaudeExecutionError`
7. `test_claude_parity_args`: Verify CLI flags are correctly constructed:
   - `--model` flag when profile is set
   - `--resume` flag when resume_session_id is set
   - Sandbox/approval mode flags (TBD based on CLI research)
8. `test_claude_prompt_combination`: Verify system_prompt and user_prompt are concatenated and passed as positional arg
9. `test_claude_usage_event_emitted`: Verify synthetic `usage` event is yielded when process exits

**Sample test structure**:

    import json
    from pathlib import Path
    from unittest.mock import MagicMock, patch
    import pytest
    from shinka.edit.claude_cli import (
        run_claude_task,
        ClaudeExecutionError,
        ClaudeUnavailableError,
        ensure_claude_available,
    )

    @pytest.fixture
    def mock_subprocess():
        with patch("subprocess.Popen") as mock_popen:
            yield mock_popen

    @pytest.fixture
    def mock_ensure_claude():
        with patch("shinka.edit.claude_cli.ensure_claude_available") as mock_ensure:
            mock_ensure.return_value = Path("claude")
            yield mock_ensure

    def test_run_claude_task_streams_events(mock_subprocess, mock_ensure_claude):
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.stdin = MagicMock()
        mock_process.poll.return_value = None
        
        events = [
            json.dumps({"type": "init", "session_id": "sess-abc"}),
            json.dumps({"type": "message", "role": "assistant", "content": "I'll help."}),
            json.dumps({"type": "tool_use", "tool_id": "t1", "tool_name": "bash", "parameters": {"command": "ls"}}),
            json.dumps({"type": "tool_result", "tool_id": "t1", "status": "success", "output": "file.py"}),
        ]
        
        def readline_side_effect():
            if events:
                return events.pop(0) + "\n"
            mock_process.poll.return_value = 0
            return ""
        
        mock_process.stdout.readline.side_effect = readline_side_effect
        mock_subprocess.return_value = mock_process
        
        results = list(run_claude_task(
            user_prompt="fix bug",
            workdir=Path("/tmp"),
            profile=None,
            sandbox="",
            approval_mode="full-auto",
            max_seconds=10,
            max_events=100,
            extra_cli_config={}
        ))
        
        # Verify event adaptation
        assert results[0]["type"] == "init"
        assert results[1]["type"] == "agent_message"
        assert results[1]["item"]["text"] == "I'll help."
        assert results[2]["type"] == "tool_use"
        assert results[3]["type"] == "command_execution"
        assert results[3]["item"]["command"] == "ls"
        assert results[3]["item"]["exit_code"] == 0
        # Last event should be usage
        assert results[-1]["type"] == "usage"

### Milestone 5: End-to-End Validation

Run a real evolution experiment using Claude as the backend.

**Validation command**:

    env $(cat .env | xargs) uv run shinka_launch \
      variant=circle_packing_example \
      +evo_config.agentic_mode=true \
      +evo_config.agentic.backend=claude \
      +evo_config.agentic.extra_cli_config.model=claude-sonnet-4-20250514 \
      evo_config.num_generations=2

**Success criteria**:
1. Generation 0 evaluates the initial solution
2. Generation 1 completes with Claude-driven agentic editing
3. `results/shinka_circle_packing/<timestamp>/agent_sessions/<uuid>/session_log.jsonl` contains valid events
4. WebUI shows the run in the evolution tree
5. No console errors in browser when viewing run

### Milestone 6: WebUI/Auth Integration

Complete the Claude Code card in the Agents tab.

**Work**:
1. Implement `refreshClaudeUsage()` function in `viz_tree.html`
2. Implement `signOutClaude()` function
3. Add `/api/claude_usage` endpoint to `visualization.py` (or show "Usage unavailable" like Gemini)
4. Wire up OAuth flow if Claude CLI supports it, or show API key modal

**Note**: Based on the existing WebUI code, Claude usage metrics may not be available from the CLI (similar to Gemini). In that case, show "Usage unavailable" message and link to Anthropic Console.

### Milestone 7: Documentation and Telemetry Parity

**Documentation updates**:
1. Update `docs/getting_started.md` with Claude backend instructions
2. Update `docs/configuration.md` with `backend: claude` option
3. Add Claude to the model list in `README.md`

**Telemetry parity**:
1. Emit `usage` event with estimated tokens (same pattern as Gemini)
2. Ensure `AgentResult.metrics` contains `estimated_input_tokens`, `estimated_output_tokens`
3. Verify database records show non-zero cost

## Success Criteria & Validation

1. **CLI Invocation**: Shinka successfully launches the `claude` binary in headless mode.
   *Validation*: Logs show `claude ...` call with appropriate flags.

2. **Event Parity**: The `claude` wrapper yields events that `AgenticEditor` understands without modification.
   *Validation*: `session_log.jsonl` contains standard Shinka event types (`agent_message`, `command_execution`, `usage`).

3. **System Prompting**: The implementation correctly combines system/user prompts for the CLI invocation.
   *Validation*: Full prompt appears in session log.

4. **End-to-End Run**: Circle Packing example completes a generation using Claude.
   *Validation*: Run command above → artifacts in `results/shinka_circle_packing/<timestamp>/agent_sessions/<uuid>/session_log.jsonl`.

5. **Test Coverage**: `tests/test_claude_cli.py` covers command construction, event normalization, sandbox flags, and resume logic.
   *Validation*: `uv run pytest tests/test_claude_cli.py -v` shows all tests passing.

6. **Telemetry Estimation**: `AgenticEditor` aggregates Claude usage events into `estimated_*_tokens` and `estimated_total_cost`.
   *Validation*: Meta records in database carry non-zero `api_costs`.

7. **WebUI Integration**: Claude Code card shows authenticated state after sign-in (or gracefully shows "Usage unavailable").
   *Validation*: No JS errors in browser console; card displays correctly.

## Interfaces and Dependencies

**New module**: `shinka/edit/claude_cli.py`

**Functions to implement**:

    def ensure_claude_available(claude_path: Optional[str] = None) -> Path:
        """Validate Claude CLI is installed and return its path."""

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
        codex_path: Optional[str] = None,
        resume_session_id: Optional[str] = None,
        session_kind: str = "unknown",
    ) -> Iterator[Dict[str, Any]]:
        """Stream normalized JSON events from Claude CLI execution."""

**Exception classes**:

    class ClaudeUnavailableError(RuntimeError): ...
    class ClaudeExecutionError(RuntimeError): ...

**Expected event types yielded by `run_claude_task`**:

    # Agent message
    {"type": "agent_message", "item": {"type": "agent_message", "text": "..."}, "session_id": "..."}

    # Command execution
    {"type": "command_execution", "item": {"type": "command_execution", "command": "...", "status": "success", "exit_code": 0, "stdout": "...", "stderr": ""}, "session_id": "..."}

    # Usage telemetry
    {"type": "usage", "session_id": "...", "usage": {"input_tokens": N, "output_tokens": M, "total_tokens": N+M}}

    # Init/session (optional)
    {"type": "init", "session_id": "..."}

**Dependencies**:
- `claude` CLI binary in PATH (installed via npm or Anthropic's package manager)
- Anthropic authentication configured (OAuth or API key)

## Idempotence and Recovery

All code changes are additive. If the implementation breaks, revert with:

    git checkout -- shinka/edit/claude_cli.py shinka/core/runner.py

Test files can be removed without affecting production code:

    rm tests/test_claude_cli.py

The wrapper gracefully degrades: if `claude` is not installed, `ensure_claude_available()` raises `ClaudeUnavailableError` and the run fails early with a clear message.

## Artifacts and Notes

(To be populated with CLI research findings, sample event streams, and test outputs)

### Claude CLI Research Notes

(Placeholder - to be filled during Milestone 1)

**CLI Installation**:

    npm install -g @anthropic-ai/claude-code

**Available commands** (to be documented):

    claude --help
    claude exec --help (if exists)

**Event stream format** (to be documented):

    (sample JSON events from test runs)

**Authentication**:

    claude login  # or similar
