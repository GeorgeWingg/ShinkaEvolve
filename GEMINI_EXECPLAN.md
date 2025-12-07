# Gemini CLI Backend for Agentic Evolution

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

PLANS.md is located at `PLANS.md` in the repository root. Maintain this plan in full compliance with that document.

## Purpose / Big Picture

The goal is to enable Shinka to use the `gemini` CLI tool as an alternative backend for agentic editing and evaluation, mirroring the existing integration with the `codex` CLI. By wrapping `gemini-cli` (specifically its headless/exec mode), users can leverage their Google Gemini subscriptions directly. This implementation will be a drop-in replacement for the Codex backend, providing feature parity (multi-file editing, shell execution, streaming JSONL events) without implementing a custom Python agent loop.

## Scope & Constraints

*   **Backend Agnosticism**: The existing Codex harness is model-agnostic, but we need to generalize the *runner* layer to support different CLI tools (`codex` vs `gemini`).
*   **Gemini CLI Integration**: Instead of building a Python driver, we will wrap the external `gemini` CLI tool.
    *   We assume `gemini-cli` exposes a command (e.g., `gemini exec`) that accepts a prompt and working directory, and emits JSON/JSONL events.
    *   We will leverage `gemini-cli`'s native system prompt support if available.
*   **No Vendoring**: We will not vendor the `gemini-cli` source code. We assume it is installed in the environment (like `codex`).
*   **Parity**: The integration must produce the same event structure (`command_execution`, `agent_message`) so that Shinka's logging, database, and WebUI work seamlessly.

## Progress

- [x] Milestone 1: Research `gemini-cli` interface and capabilities.
- [x] Milestone 2: Refactor `AgenticEditor`/`Evaluator` to support pluggable CLI wrappers.
- [x] Milestone 3: Implement `shinka/edit/gemini_cli.py` wrapper (Initial functional pass).
- [x] Milestone 4: Configuration wiring and Parity Testing (Functional equivalence achieved).
- [x] Milestone 5: Strict Parity Gap Closure (Sandbox, Resume, Telemetry).
- [x] Milestone 6: Verification & Regression Testing.
- [x] Milestone 7: Telemetry Estimation (Enhancement).
- [x] (2025-11-20 19:40Z) Initial Gemini-backed evolution attempt blocked by missing login/API key. Documented the failure and paused until credentials were available.
- [x] (2025-11-20 15:12Z) Confirmed `gemini-1.5-pro|flash` return `ModelNotFoundError` (404) even with cached OAuth creds; recorded as non-working slugs.
- [x] (2025-11-20 23:40Z) Tried `gemini-3-pro` and `gemini-3-pro-preview`; gen0 eval worked but agentic gen1 failed with 404 during tool stream. Logs: `/tmp/shinka_gemini3_run2.log`, `/tmp/shinka_g3preview_run2.log`, artifacts `results/shinka_circle_packing_g3preview_smoke`.
- [x] (2025-11-21 14:23Z) Completed first end-to-end Gemini agentic run using `gemini-2.5-pro` (2 generations, backend=gemini). Gen1 produced `session_log.jsonl` under `results/shinka_circle_packing/2025.11.21142051_example/agent_sessions/b6d1723d-2185-4469-89ea-8d13229eb3b7`; launch log `/tmp/shinka_gemini25_run2.log`.
- [x] Model access log (2025-11-21): **working** `gemini-2.5-pro`; **failing** `gemini-3-pro` (404), `gemini-3-pro-preview` (hang/404 during tool stream), `gemini-1.5-pro|flash` (404). Prefer 3-pro when Google exposes it; keep the log current.

## Milestones

### Milestone 1: Research Gemini CLI

Investigate the `gemini-cli` tool (source available at `../gemini-api/gemini-cli` or similar) to define the exact contract.
*   **Goal**: Determine the command-line arguments for headless execution.
*   **Questions to Answer**:
    *   Does `gemini exec` exist? What are its flags?
    *   Does it support `--json` or similar for structured output?
    *   How do we pass the system prompt vs user prompt?
    *   How does it handle authentication (does it have a `login` command or use env vars)?
    *   What does the event stream look like? (Do we need an adapter to match Codex events?)

### Milestone 2: Pluggable Runner Interface

Refactor `shinka/edit/agentic.py` and `shinka/eval/agentic.py` to stop importing `run_codex_task` directly.
*   **Goal**: Define a common signature for "CLI Task Runners".
*   **Work**:
    *   Define the protocol (inputs: user_prompt, workdir, config, system_prompt; output: iterator of dicts).
    *   Update `AgenticEditor` to take a `runner` callable in `__init__`.
    *   Ensure `EvolutionRunner` selects the correct callable based on config.

### Milestone 3: Gemini CLI Wrapper

Create `shinka/edit/gemini_cli.py`.
*   **Goal**: A Python module that wraps the `gemini` subprocess.
*   **Work**:
    *   Implement `ensure_gemini_available()` (check PATH).
    *   Implement `run_gemini_task(...)` generator.
    *   Map Shinka's config (sandbox, max_turns) to Gemini CLI flags.
    *   **Prompt Handling**: Combine `system_prompt` and `user_prompt` and pass via stdin, matching Codex parity.
    *   **Event Adaptation**: If `gemini` emits different JSON events than `codex`, write an adapter layer inside `run_gemini_task` to normalize them into the format Shinka expects (`command_execution`, `agent_message`, etc.).

### Milestone 4: Integration & Validation

*   **Goal**: End-to-end parity.
*   **Work**:
    *   Update `configs/evolution/agentic.yaml` with `backend: gemini`.
    *   Run `shinka_launch variant=circle_packing_example +evo_config.agentic.backend=gemini`.
    *   Verify logs and artifacts match the Codex baseline.

### Milestone 5: Strict Parity Gap Closure

Address specific gaps identified during initial implementation to ensure `gemini` backend is a 1:1 replacement for `codex`.

*   **Goal**: Eliminate functional differences in arguments and session management.
*   **Work**:
    1.  **Sandbox Config**: `gemini` CLI supports `--sandbox`, but `gemini_cli.py` currently ignores the `sandbox` argument.
        *   *Task*: Map `sandbox="workspace-write"` (or boolean true) to `gemini --sandbox`. (Completed)
    2.  **Resume by UUID**: `codex` resumes via `codex exec resume <UUID>`. `gemini` supports `--resume <index|latest>`, but capturing and using the UUID (e.g., `cb8c80cb-b3f7...`) is critical for robust state management in the database.
        *   *Investigation*: Verify if `gemini --resume <UUID>` works. `gemini --list-sessions` shows UUIDs in brackets.
        *   *Task*: Ensure `run_gemini_task` correctly passes the resume ID if supported, or implement a lookup mechanism if it only accepts indices. (Completed - Logic verified, CLI supports UUIDs)
    3.  **Telemetry**: `codex` provides detailed usage. `gemini` currently does not.
        *   *Task*: If possible, mock or approximate token counts from event data to prevent UI regressions (e.g. "N/A" instead of crash). (Verified - Current implementation calculates basic metrics; token usage is not available in CLI output).

## Milestone 6: Verification & Regression Testing

Create and run a dedicated test suite to rigorously verify that the Gemini wrapper behaves exactly like the Codex wrapper in terms of argument passing and event adaptation.

*   **Goal**: Validate 100% parity in CLI invocation logic.
*   **Work**:
    *   Update `tests/test_gemini_cli.py` to include "Parity Verification Tests":
        1.  **Command Construction**: Verify `--sandbox`, `--resume`, `--model` flags are correctly appended to the subprocess call based on inputs.
        2.  **Prompt Combination**: Verify `system_prompt` and `user_prompt` are concatenated correctly and written to `stdin`.
        3.  **Event Adaptation**: Verify intricate `tool_result` scenarios (e.g., errors, empty output) map correctly to `command_execution` events.
    *   Run the tests and ensure all pass.

## Milestone 7: Telemetry Estimation (Enhancement)

Improve telemetry parity by estimating token usage for Gemini sessions, allowing the UI to display non-zero costs/counts where applicable.

*   **Goal**: Populate token usage metrics.
*   **Work**:
    1.  Update `shinka/edit/gemini_cli.py` to estimate input/output tokens (char count / 4).
    2.  Emit a synthetic `usage` event in the stream.
    3.  Update `shinka/edit/agentic.py` to capture `usage` events and aggregate them into `metrics`.
    4.  Update `shinka/core/runner.py` to use `metrics["total_cost"]` (if available) instead of hardcoded `0.0`.
*   **Success Criteria**:
    *   `AgentResult.metrics` contains `estimated_input_tokens`, `estimated_output_tokens`.
    *   Database records show non-zero cost for Gemini runs (if runner is updated).

## Success Criteria & Validation

1.  **CLI Invocation**: Shinka successfully launches the `gemini` binary in headless mode.
    *   *Validation*: Logs show `gemini exec ...` call.
2.  **Event Parity**: The `gemini` wrapper yields events that `AgenticEditor` understands without modification.
    *   *Validation*: `session_log.jsonl` contains standard Shinka event types.
3.  **System Prompting**: The implementation correctly separates system/user prompts in the interface, but combines them for the CLI invocation to maintain strict parity with Codex behavior.
4.  **End-to-End Run**: Circle Packing example completes a generation using Gemini.
    *   *Validation*: `env $(cat .env | xargs) GEMINI_NO_EXTENSIONS=1 PATH=.venv/bin:$PATH uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.backend=gemini +evo_config.agentic.extra_cli_config.model=gemini-2.5-pro evo_config.num_generations=2` → artifacts in `results/shinka_circle_packing/2025.11.21142051_example/agent_sessions/b6d1723d-2185-4469-89ea-8d13229eb3b7/session_log.jsonl`, log `/tmp/shinka_gemini25_run2.log`.
5.  **Test Coverage**: `tests/test_gemini_cli.py` covers Sandbox flag mapping and Resume logic explicitly. (Verified - 4/4 tests passed)
6.  **Telemetry Estimation**: `AgenticEditor` aggregates Gemini usage events into `estimated_*_tokens` and `estimated_total_cost`, and meta records carry non-zero `api_costs`.
    *   *Validation*: `.venv/bin/pytest tests/test_gemini_cli.py tests/test_agentic_scaffolding.py` (2025-11-21) passed after usage-metrics assertion.

## Surprises & Discoveries

- `gemini-cli` supports streaming JSON output natively via `--output-format stream-json`.
- The event structure (`TOOL_RESULT` with `output` string) required adaptation to match Shinka's expected `command_execution` (stdout/stderr/exit_code) format. Approximated exit code based on status.
- `gemini-cli` has no dedicated system-prompt flag in headless mode; we combine system+user prompts into the positional prompt to avoid touching `GEMINI.md`.
- `gemini-cli` lacks a `usage` command for fetching account/quota info, making full telemetry parity impossible without direct API usage. The UI was updated to handle this missing data gracefully.
- Token-cost telemetry still has to be estimated; a small per-1k-token placeholder keeps `api_costs` non-zero so database/UI paths don’t crash even when the CLI omits billing data.
- Model access (2025-11-21 update): `gemini-2.5-pro` is confirmed working end-to-end; `gemini-3-pro` and `gemini-3-pro-preview` still return 404/not found during tool streams; `gemini-1.5-*` variants remain unsupported. Keep this log current and prefer 3-pro once Google exposes it to this account.
- Passing unsupported flags to the CLI silently suppresses output. Forwarding `--no_extensions` / `--debug_log` (we only need `GEMINI_NO_EXTENSIONS=1` via env) caused the agent to emit zero JSON events; removing those args and teeing stdout/stderr restored normal multi-generation runs on `gemini-3-pro-preview`.

## Decision Log

- **Decision**: Wrap `gemini-cli` instead of using SDK.
  **Rationale**: User request; enables reuse of existing CLI features (auth, subscription) and maintains architectural symmetry with Codex integration.
  **Date**: 2025-11-18
- **Decision**: Split `prompt` into `user_prompt` and `system_prompt` in the `AgentRunner` protocol.
  **Rationale**: Allows backend-specific handling (combining for Codex, file-writing for Gemini) to maximize feature usage.
  **Date**: 2025-11-18
- **Decision**: Combine prompts for Gemini CLI instead of writing to `GEMINI.md`.
  **Rationale**: User directive to maintain strict parity with Codex and avoid modifying `GEMINI.md` which is used for other purposes in user repositories.
  **Date**: 2025-11-18
- **Decision**: Do not implement `gemini_usage.py` and update UI to show "Usage unavailable" for Gemini.
  **Rationale**: `gemini-cli` does not expose this data. Replicating Codex's `codex usage` feature would require out-of-band SDK authentication, violating the "CLI wrapper" design constraint.
  **Date**: 2025-11-18

## Outcomes & Retrospective

- **Backend Agnostic Runner**: Implemented `AgentRunner` protocol to decouple runner logic and support separate system prompts.
- **Gemini Wrapper**: Added `shinka/edit/gemini_cli.py` to wrap `gemini-cli`, handling `stream-json` parsing and prompt combination.
- **Integration**: Refactored `EvolutionRunner` and `AgenticEvaluator` to select backend via `evo_config`.
- **UI Updates**: Redesigned "Agents" tab in `viz_tree.html` to be backend-agnostic and gracefully handle missing usage telemetry for Gemini.
- **Verified**: Logic verified with unit tests (`tests/test_gemini_cli.py`) mocking the CLI interaction.
- **Telemetry estimation added (2025-11-20)**: Gemini runner now emits a `usage` event; AgenticEditor aggregates estimated tokens/cost and meta records carry non-zero `api_costs`, validated via `.venv/bin/pytest tests/test_gemini_cli.py tests/test_agentic_scaffolding.py`.
- **End-to-end validation (2025-11-21)**: Ran `variant=circle_packing_example +evo_config.agentic.backend=gemini +evo_config.agentic.extra_cli_config.model=gemini-2.5-pro evo_config.num_generations=2`; agentic gen1 completed, `session_log.jsonl` captured under `results/shinka_circle_packing/2025.11.21142051_example/agent_sessions/b6d1723d-2185-4469-89ea-8d13229eb3b7`.
- **Model access stance**: `gemini-2.5-pro` is the current working slug; `gemini-3-pro` and `gemini-3-pro-preview` remain 404/not-found for tool streaming. Keep using 2.5-pro until 3-pro access lands.
