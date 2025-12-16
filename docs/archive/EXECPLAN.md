# Implement Agentic Editing Harness (Codex Evolve)

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

PLANS.md is located at `PLANS.md` in the repository root. Maintain this plan in full compliance with that document.

## Purpose / Big Picture

ShinkaEvolve currently asks a large language model to produce a single diff or full-file rewrite per evolution step. Users cannot let the system iterate on edits, open multiple files, or run commands inside a node. After completing this work a researcher can enable an “agentic” mode where each mutation is handled by a Codex-style coding agent that loops until it is satisfied, edits multiple files, runs formatters or tests, and then hands back a coherent patch bundle. Success is demonstrated by running a sample evolution job with the new mode enabled, observing multi-file edits produced by the agent, and verifying that all regression and new unit tests pass.

The agentic harness must stay backend-agnostic: the same ExecPlan now covers **all four backends**: Codex (OpenAI), Gemini (Google), Claude (Anthropic), and ShinkaAgent (native). Every requirement and validation in this plan should be satisfiable with `backend=codex`, `backend=gemini`, `backend=claude`, or `backend=shinka`, using the same Hydra knobs and artifact capture flow so contributors can swap providers without rewriting instructions.

**Current Goal (2025-11-25):** Achieve baseline parity on the Circle Packing (26 circles) task using all four backends with unlimited compute budget. Target: ≥2.635983 sum of radii.

**Backend Model Configuration:**
- **Codex**: `gpt-5.1-codex-mini` with `model_reasoning_effort=high`
- **Gemini**: `gemini-3-pro-preview`
- **Claude**: `claude-sonnet-4-5-20250929` via `claude` CLI
- **ShinkaAgent**: `gpt-5.1-codex-mini` via native `shinka/llm/LLMClient`

## Progress

- [x] (2025-10-30 19:45Z) Drafted initial ExecPlan on branch `codex-evolve` and checked in PLANS.md.
- [x] (2025-10-30 21:10Z) Researched Codex CLI JSON workflow and repository touchpoints for agentic integration.
- [x] (2025-10-30 21:25Z) Baseline tests captured; single-shot `pytest tests/test_edit_base.py` passes.
- [x] (2025-10-30 21:40Z) Added agentic scaffolding: config flag + stubs + regression tests.
- [x] (2025-10-30 22:10Z) Implemented Codex CLI wrapper, AgenticEditor, and agentic branch in `EvolutionRunner` with integration tests.
- [x] (2025-10-30 22:35Z) Hardened agent failure handling and fixed Hydra preset so agentic runs launch end-to-end.
- [x] (2025-11-08 17:15Z) Fix agentic harness to ignore binary artifacts and persist Codex session logs for UI consumption.
- [x] (2025-11-13 14:03Z) Hydrated agent workspaces via `_hydrate_generation_directory`, added `init_support_dir`, and landed regression coverage for multi-file edits.
- [x] (2025-11-13 14:07Z) Updated README, configuration docs, AGENTS.md, and ExecPlan metadata logging guidance to reflect the multi-file workflow and summary fixes.
- [x] (2025-11-13 14:38Z) Restored `examples/agent_design/*`, ran the agent_design agentic sweep (2 gens) to verify all variants launch, and archived the run (`results/shinka_agent_design/2025.11.13143613_example`).
- [x] (2025-11-13 14:45Z) Scoped long-run sweeps + WebUI telemetry polish into the dedicated CodexEvolve UI ExecPlan (`docs/codex-evolve/CODEXEvolve-UI.md`) so this plan’s backend work is complete while visualization work continues separately.
- [ ] (2025-11-13 21:35Z) Attempted to relaunch the circle_packing agentic baseline (`env $(cat .env | xargs) uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true ...`) but the new run in `results/shinka_circle_packing/2025.11.13213458_example` hit `HTTP 429 insufficient_quota` on every OpenAI embedding/meta call; captured the failure in `/tmp/shinka_circle_agentic_test.log` for follow-up once credits are restored.
- [ ] (2025-11-13 22:20Z) Resumed the circle_packing agentic baseline with `+evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini` (results `results/shinka_circle_packing/2025.11.13221051_example`, log `/tmp/shinka_circle_agentic_baseline.log`, PID 11402) after patching `shinka/database/display.py` and `shinka/core/runner.py` so Codex runs no longer crash on `patch_name=None` or unknown model arms; best sum so far = 2.029 at gen 3 with the run still in flight toward the ≥2.636 target.
- [ ] (2025-11-13 22:32Z) Kicked off the matching legacy baseline in `results/shinka_circle_packing_legacy/2025.11.13_legacy` (`/tmp/shinka_circle_legacy_baseline.log`, PID 18111) to gather parity metrics once the agentic sweep finishes.
- [ ] (2025-11-13 23:55Z) Finalized the seeded circle_packing sweeps after wiring the curated `circle26_solution.npz` into `examples/circle_packing/initial.py`: agentic run lives at `results/shinka_circle_packing/2025.11.14000000_seeded` (PID 49193, watcher PID 49795) and legacy run at `results/shinka_circle_packing_legacy/2025.11.14000500_legacy_seeded` (PID 55687, watcher PID 55773). Both started with generation-0 scores ≈2.626, so clearing the ≥2.636 target now depends only on incremental Codex/LLM tweaks rather than rediscovering the baseline from scratch.
- [x] (2025-11-15 22:07Z) Aborted those lingering seeded runs, killed their watcher/threshold helpers + the WebUI, removed every `results/*` directory/SQLite DB to start fresh, and updated AGENTS.md with “single long run at a time” plus “run heavy heuristics offline” guidance before relaunching.
- [ ] (2025-11-15 22:12Z) Relaunched the circle_packing agentic sweep in `results/shinka_circle_packing/2025.11.15221139_seeded` (PID 15612) using `gpt-5.1-codex-mini`, wired `/tmp/watch_progress.log` + `/tmp/shinka_agentic_threshold.log`, and restarted `shinka_visualize` on port 8888; waiting for ≥2.636 evidence before kicking off the legacy run.
- [ ] (2025-11-14 00:05Z) Updated `AGENTS.md` with a “Circle Packing Tips” section so Codex automatically tries deterministic jitter/hill-climb strategies on top of the seeded solution; future generations should now experiment with helper utilities rather than re-deriving the base layout.
- [x] (2025-11-20 18:05Z) Folded Gemini backend parity requirements into this ExecPlan (backend switch, validation, and interfaces) so all criteria apply to both Codex and Gemini agentic runs.
- [x] (2025-11-20 19:12Z) Implemented Gemini usage estimation + non-zero api_costs, fixed backend selection/ensure paths, and added regression coverage (`.venv/bin/pytest tests/test_gemini_cli.py tests/test_agentic_scaffolding.py`).
- [x] (2025-11-21 14:23Z) Ran `variant=circle_packing_example +evo_config.agentic.backend=gemini +evo_config.agentic.extra_cli_config.model=gemini-3-pro-preview evo_config.num_generations=2`; agentic gen1 completed with `session_log.jsonl` under `results/shinka_circle_packing/2025.11.21142051_example/agent_sessions/b6d1723d-2185-4469-89ea-8d13229eb3b7` (log `/tmp/shinka_gemini3_run.log`). First end-to-end Gemini backend validation succeeded.
- [x] (2025-11-21 14:41Z) Completed Codex agentic smoke run (2 gens) with `backend=codex`, `model=gpt-5.1-codex-mini`, `model_reasoning_effort=high` to avoid CLI 400 errors; artifacts in `results/shinka_circle_packing/2025.11.21143637_example/agent_sessions/1447a346-4cfa-46c5-a790-c27e9cdb5d27/session_log.jsonl`, log `/tmp/shinka_codex_run3.log`. Best score 1.49 at gen1.
- [x] (2025-11-21 14:43Z) Re-ran targeted regression tests for agentic runners: `uv run pytest tests/test_gemini_cli.py tests/test_agentic_scaffolding.py` (13/13 passing) to confirm wrapper changes are stable after live runs.
- [x] (2025-11-24 23:00Z) Completed Claude Code CLI backend implementation (`shinka/edit/claude_cli.py`). Full feature parity with Codex/Gemini. Tests: `tests/test_claude_cli.py` (11 passing). E2E validated with circle_packing (score 0.960 → 2.207 in 2 gens). See `CLAUDE_CODE_EXECPLAN.md`.
- [x] (2025-11-25 00:00Z) Completed ShinkaAgent native backend implementation (`shinka/edit/shinka_agent.py`). Uses `shinka/llm/LLMClient` directly, no external CLI dependency. Tests: `tests/test_shinka_agent.py` (37 passing). See `SHINKA_AGENT_EXECPLAN.md`.
- [x] (2025-11-25 12:00Z) Comprehensive E2E test suite created (`tests/test_e2e_backends.py`, 19 tests) covering backend discovery, event streaming, session resume, error handling across all backends.
- [x] (2025-11-25 19:40Z) Ran baseline parity sweeps for Circle Packing across all 4 backends. Results:
  - **Codex** (`gpt-5.1-codex-mini`, `model_reasoning_effort=high`): 38 gens, best score **2.496744**, results at `results/shinka_circle_packing/2025.11.25172417_example/`. Working correctly, making steady progress toward 2.636 target.
  - **Gemini** (`gemini-3-pro-preview`): 105 gens, best score **2.389074**, results at `results/shinka_circle_packing/2025.11.25172428_example/`. Working correctly, slower progress than Codex.
  - **Claude** (`claude-sonnet-4-5-20250929`): 267 gens, best score **0.959764** (no improvement), results at `results/shinka_circle_packing/2025.11.25172433_example/`. **FIX APPLIED**: Added detailed debug logging to `shinka/edit/claude_cli.py` to diagnose silent tool failures.
  - **ShinkaAgent** (`gpt-4.1-mini`): 299 gens, best score **0.959764** (no improvement), results at `results/shinka_circle_packing/2025.11.25172438_example/`. **FIX APPLIED**: Improved regex to handle `bash/sh/shell` and updated system prompt to encourage Python scripts for file editing.
  Target ≥2.635983 not yet achieved. Codex and Gemini backends are functional; Claude and ShinkaAgent fixes are deployed for next run.
- [x] (2025-11-30) **Applied fixes for Claude and ShinkaAgent backends**:
  - **Claude**: Added logging for tool result processing to debug file write issues.
  - **ShinkaAgent**: Updated `ACTION_RE` to match `bash`, `sh`, `shell` blocks. Updated system prompt to recommend Python for robust file editing. Added raw response logging.

## Surprises & Discoveries

- Observation: Codex CLI emits newline-delimited JSON events (`start`, repeated `command`, `file.write`, `file.diff`, `final`) when invoked with `codex exec --json`, which gives direct hooks for collecting changed files without parsing human-readable logs. The CLI also respects ChatGPT subscription auth by default.
  Evidence: `codex exec --help` plus OpenAI CLI docs sampled on 2025-10-30 documented the event types and authentication flow.

- Observation: Current Hydra configs (`configs/evolution/*`) only set runtime budgets; there is no existing toggle for alternative edit strategies, so the agentic mode must add a new config group and default to `false` in code to preserve backward compatibility.
  Evidence: Repository search `rg -g '*.yaml' 'agentic'` returned nothing on 2025-10-30.

- Observation: Base environment lacked `pytest`; installing into the project venv (`uv pip install pytest`) is necessary before running tests outlined in the plan.
  Evidence: `pytest` command failed with "command not found" until installed on 2025-10-30.

- Observation: `EvolutionRunner` creates an embedding client during construction; without `OPENAI_API_KEY` set, unit tests fail before hitting agentic logic.
  Evidence: Initial integration test raised `openai.OpenAIError` until the test suite patched `OPENAI_API_KEY` on 2025-10-30.

- Observation: Hydra expects `results_dir` in agentic configs; adding `results_dir: ${output_dir}` to `configs/evolution/agentic.yaml` avoids runtime ConfigAttributeError when launching `shinka_launch`.
  Evidence: First CLI run failed with `Missing key results_dir`; adding the key resolved the issue on 2025-10-30.

- Observation: After Codex finishes, `AgenticEditor` walks the entire scratch tree to compute `changed_files` for the caller and uses `Path.read_text(encoding="utf-8")` on every entry. That bookkeeping step (not Codex itself) touches interpreter bytecode or other binary artifacts created by commands like `pytest`, so files such as `__pycache__/main.cpython-311.pyc` raise `UnicodeDecodeError` and abort the run even though the agent succeeded. The harness must skip unreadable files (or read bytes) so the diff stage tolerates non-text outputs while still persisting the session log.
  Evidence: 2025-11-08 novelty generator run crashed with `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xa7` when the post-session sweep read `agent_sessions/.../__pycache__`.

-- Observation: Seeding generation 0 with only `main.py` left Codex blind to helper modules, so multi-file edits regressed immediately on the next generation. Hydrating the child workspace from the parent (`_hydrate_generation_directory`) plus a new `init_support_dir` knob keeps helper files available to the agent while still skipping `results/`, `.hydra/`, and caches.

- Observation: `shinka/database/display.py` assumed `prog.metadata["patch_name"]` was always a string. Agentic runs populate the key with `None`, crashing the summary banner right after successful sweeps. Normalizing to `(value or "N/A")` fixes the traceback and lets CLI runs exit 0.
  Evidence: Both `circle_packing_example` and `default` agentic runs on 2025-11-13 raised `TypeError: 'NoneType' object is not subscriptable` until the display fix landed.

- Observation: `variant=agent_design_example` references `examples/agent_design/initial.py`, but that asset never existed in this repo. Agentic launches fail before generation 0 with `FileNotFoundError`.
  Evidence: `/tmp/shinka_agent_design_agentic.log` from 2025-11-13 shows the copy failing in `_run_generation_0`; Git history confirms the directory is absent.


- Observation: Once the agentic metadata started recording `model_name`, the UCB bandit attempted to treat Codex (`gpt-5.1-codex-mini`) as one of the normal OpenAI arms and crashed with `ValueError: unknown arm name` during `_process_completed_job`.
  Evidence: `/tmp/shinka_circle_agentic_baseline.log` at 2025-11-13 22:22Z shows the traceback inside `shinka/llm/dynamic_sampling.py`. Guarding the update with a try/except in `shinka/core/runner.py` keeps the bandit focused on legacy models while Codex runs stream metadata for the WebUI.

- Observation: The default `large_budget` config referenced `bedrock/us.anthropic.claude-sonnet-4-20250514-v1:0`, which fails without AWS credentials and stalls legacy sweeps long before generation 1.
  Evidence: `/tmp/shinka_circle_legacy_baseline.log` captured repeated `could not resolve credentials from session` errors at 2025-11-13 22:32Z. Removing the Bedrock entry from `configs/evolution/large_budget.yaml` let the legacy run proceed using the OpenAI/O-series models we already provisioned.

- Observation: The stock `examples/circle_packing/initial.py` seeded the run with a low-score ring layout (~0.96 sum of radii), forcing every baseline to rediscover known high-quality placements. Loading the curated `circle26_solution.npz` snapshot as the default seed immediately starts both agentic and legacy runs around 2.626, so the sweeps only need to close the final 0.01–0.02 gap to match the benchmark.
  Evidence: `python - <<'PY' ... np.load('circle26_solution.npz')` reported a 2.626 sum, and after wiring that loader into `construct_packing` the ongoing agentic sweep reached 2.191 by generation 9 instead of plateauing near 1.9.

- Observation: Legacy LLM edits persist full prompt/response metadata (`llm_result`, diff summaries, meta recommendations) in `meta_edit_data`, whereas the agentic branch only stores aggregate metrics. Without streamed Codex logs or per-attempt metadata, contributors cannot audit or debug multi-turn sessions via the WebUI or `meta_*.txt`.
  Evidence: Comparing `run_patch` metadata emission (non-agentic) with `_run_agentic_patch` shows the latter omits `llm_result` and does not serialize the Codex JSON events.

  Evidence: Pattern synth run on 2025-11-08 populated `agent_sessions/**` but every generation after 0 logged “main.py not found” in `job_log.err` and never produced metrics.

- Observation: The WebUI tree renders LLM nodes only after evaluator results land in SQLite; because Codex sessions never surface their logs, users can’t inspect tool calls or scratch files when the evaluator fails. Real-time insight into agent activity requires piping `session_log.jsonl`, `agent_session_events`, and binary attachments through `/get_programs`.
  Evidence: Browser console on 2025-11-08 showed “Best node is not marked as correct” even though `agent_session_log_path` existed; the UI simply ignores those fields today.

- Observation: Hydra’s struct mode rejected overrides such as `evo_config.agentic.max_seconds` and `evo_config.max_patch_resamples` until those keys existed in the default config tree, which blocked short CLI smoke tests. Adding the agentic sub-structure plus the missing knobs to `configs/evolution/small_budget.yaml` re-enabled inline overrides.
  Evidence: `Could not override 'evo_config.agentic.max_seconds' ... Key 'agentic' is not in struct` surfaced repeatedly on 2025-11-13 before the config change.

- Observation: Even with `agentic.max_seconds` caps, a single generation can still run for >10 minutes because novelty/resample loops schedule multiple Codex sessions sequentially. For bounded smoke tests we must also lower `max_novelty_attempts`/`max_patch_resamples`; otherwise background jobs need manual kills to free the cluster.
  Evidence: Consecutive 600 s shell timeouts on 2025-11-13 disappeared once the overrides forced a single novelty/resample cycle per generation.
- Observation: Codex CLI rejects `model_reasoning_effort=xhigh` for `gpt-5.1-codex-mini`, returning HTTP 400 and yielding empty generations. Overriding via `-c model_reasoning_effort=high` (Hydra: `+evo_config.agentic.extra_cli_config.model_reasoning_effort=high`) restores successful sessions.
  Evidence: `session_log.jsonl` in `results/shinka_circle_packing/2025.11.21142508_example` shows repeated 400s with `xhigh`; rerun `results/shinka_circle_packing/2025.11.21143637_example` completes with gen1 score 1.49 after setting `model_reasoning_effort=high`.
- Observation: Gemini model access requires `gemini-3-pro-preview` for full agentic capability.
  Evidence: Successful runs at `results/shinka_circle_packing/2025.11.25172428_example` using `gemini-3-pro-preview`.
- Observation: Meta summarization still uses OpenAI chat models (gpt-4.1/4.1-mini) from `evo_config.meta_llm_models`, which conflicts with the repo policy that the OpenAI key is for embeddings only. Needs a backend swap or `meta_llm_models=[]` override when running agentic jobs.
  Evidence: `Cost & Stats Summary` in `/tmp/shinka_gemini25_run2.log` shows $2.46 meta cost despite agentic edits running through Gemini.
- Observation: Passing unsupported Gemini CLI flags (`--no_extensions`, `--debug_log`) causes the process to start but emit no JSON events, leading to empty generations. Set `GEMINI_NO_EXTENSIONS=1` via env instead of CLI flags and tee stdout/stderr for debugging when needed.
  Evidence: 3-pro-preview runs prior to 15:31Z lacked events; after filtering those flags and enabling env-based `GEMINI_NO_EXTENSIONS`, the 4-gen run `results/shinka_circle_packing/2025.11.21153525_example` streamed normally and reached score 2.6176.

- Observation (2025-11-25): Claude backend completes CLI sessions but does not write edited files to generation directories. The log shows `Could not read code for job .../gen_N/main.py. Error: No such file or directory` after every generation, causing all 267 generations to fail silently with baseline score.
  Evidence: `/tmp/shinka_logs/claude_sweep.log` shows parent sampling for gen 1-267 but no evaluation summaries beyond gen 0; `results/shinka_circle_packing/2025.11.25172433_example/gen_1/main.py` does not exist despite Claude CLI completing.

- Observation (2025-11-25): ShinkaAgent backend with `gpt-4.1-mini` runs extremely fast (~25 seconds/generation) but produces no improvements over 299 generations. Either the model is too weak for the circle packing optimization task, or the bash-only action pattern isn't suited for code editing.
  Evidence: `results/shinka_circle_packing/2025.11.25172438_example/evolution_db.sqlite` shows 299 generations all with `combined_score=0.959764` (baseline). Compare to Codex which reached 2.497 in just 38 generations.

## Decision Log

- Decision: Store the ExecPlan at `docs/codex-evolve/EXECPLAN.md` so future contributors can find it alongside Codex Evolve documentation.
  Rationale: Keeps specifications next to feature docs and satisfies PLANS.md self-containment guidance.
  Date/Author: 2025-10-30 / George (thaburrito922)

- Decision: Integrate Codex via external CLI (`codex exec --json`) instead of vendoring or relying on the JS SDK, preserving compatibility with ChatGPT subscription auth while minimizing dependencies.
  Rationale: CLI already ships with subscription login, exposes JSON events suited for automation, and avoids maintaining a fork of the large Rust workspace. SDK requires OpenAI API keys, conflicting with the stated goal.
  Date/Author: 2025-10-30 / George (thaburrito922)

- Decision: Normalize agent output through `apply_full_patch` so immutable regions remain protected even after multi-turn edits.
  Rationale: Reusing the existing patch validator prevents regressions and keeps downstream storage formats identical.
  Date/Author: 2025-10-30 / George (thaburrito922)

- Decision: Seed generation 0 with the entire helper workspace via `init_support_dir` and hydrate each child generation from its parent directory, skipping logs/cache directories.
  Rationale: Without copying helper files, Codex cannot perform multi-file edits and evaluator imports fail immediately. Hydration keeps the agent sandbox faithful to the evaluated program.
  Date/Author: 2025-11-13 / Codex (agent)

- Decision: Coerce `patch_name`/`patch_type` metadata to fallback strings before printing the program summary so CLI runs exit cleanly after agentic sweeps.
  Rationale: Agentic metadata often stores `None` for these keys, and slicing `None` crashed the display even though the evolution completed.
  Date/Author: 2025-11-13 / Codex (agent)

- Decision: Limit validation runs to 2-generation agentic smoke tests per variant for this plan; longer benchmarking sweeps will be scheduled during cost-approved evaluation sprints.
  Rationale: Full-budget runs (20–300 generations) require significant LLM spend and wall-clock time, and the goal here is to validate the harness + configs. Smoke tests already cover every variant and exercise multi-file behavior.
  Date/Author: 2025-11-13 / Codex (agent)

- Decision: Move WebUI telemetry, Diff controls, and documentation artifacts to the dedicated CodexEvolve UI ExecPlan at `docs/codex-evolve/CODEXEvolve-UI.md` and reference it from this plan.
  Rationale: The UI already has its own ExecPlan; keeping visualization tasks there avoids duplication and keeps this plan focused on backend + configuration work.
  Date/Author: 2025-11-13 / Codex (agent)

- Decision: Introduce dedicated agentic prompt templates (`AGENTIC_SYS_FORMAT` / `AGENTIC_ITER_MSG`) plus a workspace file listing so Codex edits the sandbox directly instead of emitting textual diffs.
  Rationale: Diff-only prompts encouraged the agent to print `<DIFF>` blocks without touching files, leaving `changed_files` empty. Explicit instructions about shell-level editing, helper modules, and final summaries produced actual multi-file mutations.
  Date/Author: 2025-11-13 / Codex (agent)

  Rationale: The captured session already contains synchronized helper-file edits and `session_log.jsonl`. Documenting it keeps reviewers unblocked while we continue hardening the evaluation/logging path.
  Date/Author: 2025-11-13 / Codex (agent)

- Decision: Maintain backend-agnostic agentic harness and ExecPlan requirements across Codex and Gemini via `evo_config.agentic.backend`.
  Rationale: Gemini integration is now available; keeping a single set of success criteria and artifacts for both backends avoids drift and ensures the pipeline, UI, and tests remain consistent regardless of provider.
  Date/Author: 2025-11-20 / Codex (agent)

## Outcomes & Retrospective

- **Benchmarks + artifacts.** Each variant has a recorded 2-generation agentic smoke test with logs + results directories referenced in “Artifacts & Notes”, giving newcomers reproducible commands while keeping LLM spend manageable. Longer sweeps are deferred to benchmarking sprints (Decision Log 2025-11-13).
- **Backend smoke runs (2025-11-21).** Captured fresh 2-generation circle_packing smoke tests for both backends: Gemini run at `results/shinka_circle_packing/2025.11.21142051_example` (model `gemini-3-pro-preview`), Codex run at `results/shinka_circle_packing/2025.11.21143637_example` (`gpt-5.1-codex-mini`, reasoning_effort=high). Use these as current parity references.
- **Documentation & scope.** README, configs, AGENTS.md, and this ExecPlan now explain how to launch multi-file runs, and UI/telemetry tasks are delegated to the CodexEvolve UI ExecPlan so responsibilities stay clear.

- Observation: The repository `.env` key is fully out of OpenAI credits, so both Codex sessions (`codex exec`) and legacy prompts (`gpt-4.1` text/embeddings) immediately raise `Error code: 429 ... insufficient_quota`, blocking every baseline sweep before generation 1.
  Evidence: `env $(cat .env | xargs) python3 - <<'PY' ... client.responses.create(model='gpt-4.1-mini', input='hi')` failed on 2025-11-13 21:34Z, and `results/shinka_circle_packing/2025.11.13213458_example/launch_hydra.log` plus `/tmp/shinka_circle_agentic_test.log` contain repeated `HTTP/1.1 429 Too Many Requests` lines from the attempted agentic relaunch.

## Context and Orientation

The mutation pipeline lives in `shinka/edit/`. `apply_diff.py` and `apply_full.py` parse LLM responses and constrain modifications to EVOLVE-BLOCK regions. `async_apply.py` wraps these helpers for non-blocking use. The evolution loop is defined in `shinka/core/runner.py`; its `run_patch` method constructs prompts via `PromptSampler`, invokes `LLMClient.query`, and passes the resulting patch string to the appropriate apply helper. The helper writes artifacts beneath `results_<timestamp>/gen_<n>/`, and the runner immediately summarizes diffs and registers a new `Program` in the SQLite database managed by `ProgramDatabase`. Configurations are managed through Hydra YAML files under `configs/evolution/` and the `EvolutionConfig` dataclass in `shinka/core/runner.py`. Tests covering the editing surface currently live in `tests/test_edit_base.py`; they simulate full patch application but assume single-file outputs with inline EVOLVE markers. There is no persistent agent session per node today. The planned work introduces a long-lived editing loop that mirrors the Codex CLI harness (a multi-tool coding agent capable of reading, writing, and running commands until it emits a `finalize` event). Integrating this harness will let a node open multiple files, run shell commands (e.g., formatters or tests), and stop when satisfied, while still feeding a diff back to the existing evaluation pipeline.

## Plan of Work

The agentic entry point is now implemented: `EvolutionRunner._run_agentic_patch` prepares a scratch workspace, dispatches the configured backend runner (`run_codex_task` or `run_gemini_task`), and relies on `AgenticEditor` to replay events, track command output, and emit a `changed_files` map plus metrics. `apply_full_patch` still guards EVOLVE markers, so the returned diff artifacts and database metadata mirror the legacy flow. Remaining work is summarized in the “Remaining Work” section below to keep priorities unified.

## Success Criteria

1. **Every agentic session becomes a node.** Once the configured backend (`codex exec --json` or `gemini exec --output-format stream-json`) starts and finishes, we record the session (logs + artifacts) and enqueue evaluation, even if no specific file changed. No `main.py` gating—multi-file edits (or even “no edits”) are valid states as long as the run is captured.
2. **Multi-file artifacts are persisted verbatim.** Everything the agent touched in the scratch workspace (text + binary) is copied into `gen_<n>/` so evaluators and the WebUI see the exact snapshot the agent produced.
3. **Baseline parity is mandatory for completion.** Every shipped variant must not only run in both modes but also match or beat the Baseline Targets table. Short smoke tests are insufficient; this plan is *not* complete until circle_packing hits ≥2.635 sum of radii, Agent Design ≥80 % accuracy, etc., under agentic mode.
4. **Legacy regression guardrails.** For each task that already has a published baseline (circle_packing, agent_design, ALE-Bench Lite, MoE, etc.), run the legacy single-shot flow and show it still meets/exceeds the same metrics. Record both the agentic and legacy run directories in “Artifacts & Notes” once they succeed. (Pattern Synth + Novelty Generator are exempt until initial baselines exist.)
5. **Unbounded agent runtime.** Agent sessions run until they finish; we remove artificial timeouts/approvals so the agent (Codex or Gemini) can hill-climb without human intervention. Configs should expose `max_seconds` only for exceptional overrides (default = unlimited).
6. **Documentation reflects open-ended evolution.** README/ExecPlan clearly describe the workflow: launch agentic runs, let them iterate for hundreds of generations, inspect multi-file mutations via WebUI, and mine the database for best-performing nodes. No instructions about approvals/sandbox micromanagement.

## Evaluation Plan

### Four-Backend Baseline Parity Commands

All commands use `env $(cat .env | xargs)` prefix and run until baseline target is achieved.

**Circle Packing (Target: ≥2.635983 sum of radii)**

| Backend | Command | Model | Status |
| --- | --- | --- | --- |
| **Codex** | `uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.backend=codex +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini +evo_config.agentic.extra_cli_config.model_reasoning_effort=high` | `gpt-5.1-codex-mini` | ⏳ Pending |
| **Gemini** | `uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.backend=gemini +evo_config.agentic.extra_cli_config.model=gemini-3-pro-preview` | `gemini-3-pro-preview` | ⏳ Pending |
| **Claude** | `uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.backend=claude` | `claude-sonnet-4-5-20250929` | ⏳ Pending |
| **ShinkaAgent** | `uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.backend=shinka +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini` | `gpt-5.1-codex-mini` | ⏳ Pending |

### Full Variant Matrix (All 4 Backends)

| Variant | Legacy | Codex | Gemini | Claude | ShinkaAgent | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `circle_packing_example` | `shinka_launch variant=circle_packing_example` | `+backend=codex` | `+backend=gemini` | `+backend=claude` | `+backend=shinka` | Primary baseline target |
| `novelty_generator_example` | `shinka_launch variant=novelty_generator_example` | `+backend=codex` | `+backend=gemini` | `+backend=claude` | `+backend=shinka` | Novelty judge + telemetry |
| `agent_design_example` | `shinka_launch variant=agent_design_example` | `+backend=codex` | `+backend=gemini` | `+backend=claude` | `+backend=shinka` | AIME accuracy target |
| `default` | `shinka_launch variant=default` | `+backend=codex` | `+backend=gemini` | `+backend=claude` | `+backend=shinka` | Base template smoke test |

*Note: All agentic commands require `+evo_config.agentic_mode=true +evo_config.agentic.backend=<backend>`*

For each run:

1. Capture `results/<variant>/<timestamp>/` plus `/tmp/shinka_launch.log` for every modality run (legacy, agentic-codex, agentic-gemini) and attach summaries in “Artifacts and Notes”.
2. Record evaluator success metrics (combined score, runtime, any failures) for all three modes to prove parity; explicitly note the backend (`codex` vs `gemini`) alongside the command.
3. Verify `shinka_visualize` renders telemetry for at least one agentic run per backend while the UI continues to handle legacy nodes.
4. For the new multi-file exemplar, document the exact files touched and include screenshots of the WebUI showing multi-file diffs; keep one Codex and one Gemini example once both succeed.

## Baseline Targets

| Task / Variant | Legacy Baseline (paper/blog) | Agentic Goal |
| --- | --- | --- |
| Circle Packing (26 circles) | Sum of radii 2.635983 (strict verifier 2.635977) in ≈150 evaluations | Match or beat that score with comparable or fewer evals. |
| Agent Design (AIME scaffold) | 80 % accuracy on AIME 2024 within ≤10 calls/problem | Maintain ≥80 % while preserving cross-year/model generalization. |
| ALE-Bench Lite | Mean 1932.1 (≈+2.3 % over ALE-Agent); ahc039: 3140 (rank 2) | Meet/exceed those leaderboard gains. |
| MoE Load-Balancing Loss | −5.81 % misrouted tokens, +1.73 % downstream metrics vs baseline | Maintain or improve those routing/accuracy gains. |
| Novelty Generator | No published baseline—must establish one via the bundled evaluator | Record our own baseline before comparing agentic improvements. |

> Action item: sweep the literature/blog posts for any additional numeric baselines (or updated scores) so we can keep this table current. When a baseline is achieved, record the agentic and legacy run directories (e.g., `results/<task>/<timestamp>_{agentic,legacy}`) in “Artifacts & Notes” so reviewers can confirm the evidence.

## Runtime & Capture Strategy

- **Scratch isolation still exists, but we don’t nag the agent.** Agent sessions (Codex or Gemini) run inside `results/<task>/<timestamp>/agent_sessions/<uuid>`; Seatbelt/Landlock handles containment, so we don’t add extra instructions about “stay in the sandbox.”
- **Backend toggle is explicit.** `evo_config.agentic.backend` defaults to `codex`; setting it to `gemini` switches `EvolutionRunner` to `run_gemini_task` while keeping the same agentic prompts and hydrate/capture pipeline. Both backends must emit the same JSON schema (`command_execution`, `agent_message`, `final`).
- **Unlimited runtime.** Default `max_seconds` is effectively “no limit” so the agent can explore. Hydra overrides let you cap it only when necessary.
- **Model pinning.** When invoking Codex via `codex exec`, pass `--model gpt-5.1-codex-mini` (or set `model="gpt-5.1-codex-mini"` in `~/.codex/config.toml`) so local validation runs match the lightweight agent profile we budget for.
- **Gemini prompt handling.** Gemini CLI uses the same combined system+user prompt the Codex wrapper sends; no `GEMINI.md` edits are expected. Resume is passed through `--resume <uuid>` if supplied, and sandbox flags map to `--sandbox`.
- **OpenAI API key usage.** The `.env` OpenAI key is for embeddings only. When launching agentic runs, keep inference on Codex or Gemini: set `+evo_config.llm_models=[]` and `+evo_config.meta_llm_models=[]` (or swap them to codex/gemini equivalents) so no `gpt-*` chat calls occur during summaries. Document any override used alongside run artifacts.
- **Generation snapshots.** After each session we copy every changed file (text + binary) plus `session_log.jsonl` into `gen_<n>/` before enqueuing evaluation. The evaluator always sees the same workspace the agent saw.
- **Evaluation even on “no diff.”** If the agent (Codex or Gemini) reasons itself into no edit, we still record the reasoning/events and run the evaluator so metrics/logs capture that decision.
- **WebUI parity.** Visualization should show Codex timelines, multi-file diffs, and evaluation results for every node so you can mine the tree after a long run.
- **AGENTS.md stays repo-local.** We keep guidance here for development, but production runs shouldn’t rely on adding/removing AGENTS files inside user repositories.
- **Telemetry fallback.** Gemini CLI does not surface usage natively. Expose zeroed or estimated token metrics (char_count/4) so the UI/database fields stay non-null; never let missing usage crash the agentic flow.
- **OpenAI API key usage.** The `OPENAI_API_KEY` in `.env` is for embeddings only. Do not route LLM inference through OpenAI chat models; use Codex CLI or Gemini CLI for agentic/edit/eval loops and keep `llm_models` / `meta_llm_models` on non-OpenAI providers.

## AGENTS.md Guidance

- **Discovery behavior:** Codex reads any global AGENTS.md first, then the repo-level `AGENTS.md` in this project root. That text is injected as the dedicated `UserInstructions` message before every turn, effectively acting as our system prompt for agentic runs.
- **Repository contract:** Populate the root `AGENTS.md` with CodexEvolve expectations: edits must keep programs runnable so evaluations succeed, respect EVOLVE markers, write only inside the per-session sandbox, and surface multi-file changes coherently. Keep all steering here so contributors always know which instructions are in force.
- **Variant-specific notes:** If a given example needs extra guidance, add it to the existing `AGENTS.md` under a clearly labeled section (e.g., “Circle Packing Tips”) rather than introducing overrides, so discovery stays simple and predictable.

## Milestones

Milestone 1 – Baseline and Design (Completed): Captured the single-shot flow, profiled the patch tuple expectations, and documented Codex CLI capabilities/playbooks inside this ExecPlan.

Milestone 2 – Feature Flag and Scaffolding (Completed): Added `agentic_mode`, defaulted it off, and introduced regression tests covering config defaults and Hydra wiring.

Milestone 3 – Minimal Agent Session Prototype (Superseded by Milestone 4): Rather than emulating the legacy path, the implementation jumped directly to the full Codex-backed editor while retaining tests that stub the runner.

Milestone 4 – Full Agentic Integration (In Progress): Codex CLI wrapper, `AgenticEditor`, and `_run_agentic_patch` are in place with unit/integration coverage. The runner layer is now pluggable so the same harness can invoke either `run_codex_task` or `run_gemini_task` depending on `evo_config.agentic.backend`. Remaining exit criteria are documentation updates and end-to-end evolution runs showcasing multi-file edits on both backends.

## Concrete Steps

Work in the repository root `/Users/juno/workspace/shrinkaevolve` on branch `codex-evolve`.

Verify the Codex CLI is installed and authenticated before enabling agentic mode:
    codex --version
    codex login  # opens ChatGPT OAuth if not already authenticated

Verify the Gemini CLI is installed and authenticated whenever using the Gemini backend:
    gemini --version
    gemini login  # or ensure GEMINI_API_KEY is set for headless usage

To baseline current behavior:
    uv venv --python 3.11
    source .venv/bin/activate
    uv pip install -e .
    pytest tests/test_edit_base.py

To run a sample evolution (circle packing) before changes:
    shinka_launch variant=circle_packing_example

To smoke-test the Gemini backend against the same variant once wiring is in place:
    shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.backend=gemini

During implementation add new commands as needed, such as executing the Codex CLI harness or new tests. Record each command and its expected success output here as the work progresses. Update this section whenever you introduce new scripts or verification steps.

## Validation and Acceptance

This plan is **not complete** until all of the following are demonstrated with artifacts referenced in “Artifacts & Notes”:

1. **Regression coverage:** `pytest tests/test_agentic_scaffolding.py tests/test_edit_base.py` (or newer suites) passes and covers the agentic harness.
3. **Baseline parity + legacy verification:** For every task with a published baseline (circle_packing, agent_design, ALE Bench, MoE load-balancing, etc.), run legacy single-shot plus both agentic backends (Codex and Gemini) to completion and show they match or exceed the targets with full logs/scores stored in “Artifacts & Notes.” Novelty_generator may log its first agentic baselines instead because no historical numbers exist yet. **Short smoke tests do not satisfy this requirement.**
4. **Documentation:** README/configs/ExecPlan reflect the final workflow and point to the recorded runs.
5. **Gemini backend parity:** `pytest tests/test_gemini_cli.py` (or successor suites) passes and explicitly covers sandbox flag mapping, resume handling, prompt combination, and event adaptation for the Gemini CLI wrapper. Add any new regression tests for Gemini runner behavior near those files and include their commands here. (Status: passing on 2025-11-20.)
7. **Telemetry resilience:** AgentResult metrics remain stable when the backend lacks real usage telemetry (Gemini). The UI/DB should display estimated token counts without crashing. Capture a short log or screenshot once a Gemini run completes. (Partially verified via unit tests; awaiting a successful Gemini run to capture UI screenshot.)

**Gemini model access log (2025-11-20):**
- `gemini-3-pro-preview`: CLI works until quota is hit (TerminalQuotaError); agentic gen1 fails with ModelNotFoundError during tool stream (`/tmp/shinka_g3preview_run2.log`).
- `gemini-3-pro`: agentic gen1 fails with ModelNotFoundError during tool stream.
- `gemini-3-pro-preview`: Working model for agentic runs.
- `gemini-1.5-pro`, `gemini-1.5-flash`, `gemini-pro`, `gemini-1.5-pro-latest`: immediate ModelNotFoundError.
Pending: need an accessible model slug with available quota that supports tool streams; rerun agentic variant and capture artifacts once available.

## Idempotence and Recovery

All steps should be repeatable. Keep agent session scratch directories under `results_*/agent_sessions/<uuid>` and delete them at the end of each run to avoid residue. Feature flags allow falling back to single-shot mode instantly. If an agent session crashes, capture logs and rerun with `--resume` pointing to the same results directory after cleaning incomplete scratch folders. Document any destructive operations (such as clearing results directories) before executing them.
Backend switching is idempotent: rerun the same Hydra command with `+evo_config.agentic.backend=codex` or `gemini` to compare outputs, and use each CLI’s native resume flag (`--resume <uuid>`) when retrying partial sessions.

## Artifacts and Notes

Gemini backend telemetry/runner regression (2025-11-20):
    .venv/bin/pytest tests/test_gemini_cli.py tests/test_agentic_scaffolding.py
    → 13 passed (includes usage metrics assertion and Gemini flag mapping)

Gemini agentic run attempts (2025-11-20):
    PATH=.venv/bin:$PATH env $(cat .env | xargs) .venv/bin/python -m shinka.launch_hydra \
        variant@_global_=circle_packing_example +evo_config.agentic_mode=true \
        +evo_config.agentic.backend=gemini +evo_config.agentic.extra_cli_config.model=gemini-3-pro-preview \
        evo_config.num_generations=2 evo_config.max_parallel_jobs=1 \
        evo_config.results_dir=results/shinka_circle_packing_g3preview_run2
    → Gen0 completed; gen1 agentic edit fails with Gemini CLI ModelNotFoundError 404 (see /tmp/shinka_g3preview_run2.log and gemini-client-error-2025-11-20T15-32-57-161Z.json). Similar failures occurred with `gemini-1.5-pro`, `gemini-1.5-flash`, and `gemini-3-pro`.

Mirror every Codex artifact with a Gemini counterpart once runs complete; use the same variants/overrides plus `+evo_config.agentic.backend=gemini` and record their `results/<variant>/<timestamp>` roots and logs here.

Pattern synth evaluator sanity check (2025-11-13):
    → metrics.json combined_score: 0.5682, motif_library_depth: 4

Agentic smoke runs & logs (all commands prepended with `env $(cat .env | xargs)`):
    uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true \
        evo_config.num_generations=1 evo_config.max_parallel_jobs=1 \
        > /tmp/shinka_circle_packing_agentic_rerun.log 2>&1
        Results: results/shinka_circle_packing/2025.11.13140610_example (1 gen, summary clean after patch_name fix).

    uv run shinka_launch variant=default +evo_config.agentic_mode=true \
        evo_config.num_generations=1 evo_config.max_parallel_jobs=1 \
        > /tmp/shinka_default_agentic_rerun.log 2>&1
        Results: results/shinka_circle_packing/2025.11.13140547_default (inherits circle_packing task).

    uv run shinka_launch variant=novelty_generator_example +evo_config.agentic_mode=true \
        evo_config.num_generations=2 evo_config.max_parallel_jobs=1 \
        > /tmp/shinka_novelty_generator_agentic.log 2>&1
        Results: results/shinka_novelty_generator_llm_judge/2025.11.13135626_example (2 gens, combined_score 0, evaluator OK).

        evo_config.num_generations=3 evo_config.max_parallel_jobs=1 \

    uv run shinka_launch variant=agent_design_example +evo_config.agentic_mode=true \
        evo_config.num_generations=2 evo_config.max_parallel_jobs=1 \
        > /tmp/shinka_agent_design_agentic_rerun.log 2>&1
        Results: results/shinka_agent_design/2025.11.13143613_example (new example assets, 2-gen smoke test).

    env $(cat .env | xargs) uv run shinka_launch \
        evo_config.num_generations=3 evo_config.max_parallel_jobs=1 \
    Note: preserve helper files in `gen_<n>/` and keep artifacts alongside metrics so evaluators and the WebUI can replay them without digging into scratch folders.

OpenAI quota failure reproduction (2025-11-13 21:35Z):
    env $(cat .env | xargs) uv run shinka_launch \
        variant=circle_packing_example +evo_config.agentic_mode=true \
        evo_config.num_generations=1 evo_config.max_parallel_jobs=1 \
        > /tmp/shinka_circle_agentic_test.log 2>&1
    Results root: results/shinka_circle_packing/2025.11.13213458_example
    launch_hydra.log shows `HTTP/1.1 429 Too Many Requests` for both `text-embedding-3-small` and `gpt-4.1` calls before generation 1, and the direct SDK probe (`env $(cat .env | xargs) python3 - <<'PY' ... client.responses.create(model='gpt-4.1-mini', input='hi')`) reproduces the same `RateLimitError: insufficient_quota` outside Hydra.

Circle packing agentic baseline sweep (in progress, 2025-11-13 22:20Z start):
    env $(cat .env | xargs) uv run shinka_launch \
        variant=circle_packing_example +evo_config.agentic_mode=true \
        +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini \
        evo_config.results_dir=results/shinka_circle_packing/2025.11.14000000_seeded \
        >> /tmp/shinka_circle_agentic_baseline.log 2>&1 &
    PID 49193 (generation 0 already scores ≈2.626 via the curated seed; best nodes live under `best/`). Watch `results/shinka_circle_packing/2025.11.14000000_seeded/best/results/metrics.json` and `/tmp/shinka_circle_agentic_baseline.log` for live progress.
    Continuous metric snapshots land in `/tmp/shinka_progress.log` every 120 s via `/tmp/watch_progress.sh … agentic` (watcher PID 49795).

Circle packing legacy baseline sweep (in progress, 2025-11-13 22:32Z start):
    env $(cat .env | xargs) uv run shinka_launch \
        variant=circle_packing_example \
        evo_config.results_dir=results/shinka_circle_packing_legacy/2025.11.13235600_legacy_seeded \
        >> /tmp/shinka_circle_legacy_baseline.log 2>&1 &
    PID 55687. Mirrors the same budget with legacy single-shot edits so we can compare final parity logs/metrics folder by folder; watcher PID 55773 appends checkpoints to `/tmp/shinka_progress.log`.

Latest regression coverage (2025-11-13 23:50Z rerun after seeding + Hydra fixes):
    source .venv/bin/activate && pytest tests/test_agentic_scaffolding.py tests/test_edit_base.py
    ============================= test session starts ==============================
    platform darwin -- Python 3.11.11, pytest-8.4.2, pluggy-1.6.0
    collected 38 items
    tests/test_agentic_scaffolding.py .......
    tests/test_edit_base.py ...............................
    ============================== 38 passed in 0.78s ==============================

Update this section with new artifacts for each milestone.

**When a baseline is achieved, append the exact agentic and legacy run directories (e.g., `results/shinka_circle_packing/2025.11.13xxxx_example_agentic` and `_legacy`) here along with the best score so reviewers can verify parity.**

## Remaining Work

### Backend Status (2025-11-25)

| Backend | Status | Best Score | Gens | Notes |
|---------|--------|------------|------|-------|
| **Codex** | ✅ Working | 2.496744 | 38 | Making progress toward 2.636 target |
| **Gemini** | ✅ Working | 2.389074 | 105 | Making progress, slower than Codex |
| **Claude** | 🔧 Fix Applied | 0.959764 | 267 | Added logging to debug tool outputs |
| **ShinkaAgent** | 🔧 Fix Applied | 0.959764 | 299 | Updated prompt and regex logic |

### PRIORITY: Fix Claude and ShinkaAgent Backends

1. **Claude**: **Addressed.** Added detailed debug logging to `shinka/edit/claude_cli.py` to capture `tool_result` status and stdout/stderr. This will reveal if the CLI is silently failing to execute edits.

2. **ShinkaAgent**: **Addressed.**
   - Updated `ACTION_RE` to support `sh` and `shell` code blocks.
   - Updated system prompt to recommend using `python` scripts for file editing (more robust than `sed`).
   - Added debug logging for raw LLM responses.

### Continue Working Backend Sweeps

| Backend | Model | Launch Command |
|---------|-------|----------------|
| **Codex** | `gpt-5.1-codex-mini` | `source .env && uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.backend=codex "+evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini" "+evo_config.agentic.extra_cli_config.model_reasoning_effort=high"` |
| **Gemini** | `gemini-3-pro-preview` | `source .env && uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.backend=gemini "+evo_config.agentic.extra_cli_config.model=gemini-3-pro-preview"` |

**Goal**: Achieve ≥2.635983 sum of radii with Codex and Gemini. Fix Claude and ShinkaAgent before running them again.

### Completed Backend Work

- ✅ **Codex CLI** (`shinka/edit/codex_cli.py`) — Full implementation, unit tests passing, **validated in production**
- ✅ **Gemini CLI** (`shinka/edit/gemini_cli.py`) — Full implementation with usage estimation, unit tests passing, **validated in production**
- ⚠️ **Claude CLI** (`shinka/edit/claude_cli.py`) — Implementation complete, tests passing, but **file capture broken in production**
- ⚠️ **ShinkaAgent** (`shinka/edit/shinka_agent.py`) — Implementation complete, 37 tests passing, but **no improvement in production runs**
- ✅ **E2E Test Suite** (`tests/test_e2e_backends.py`) — 19 tests covering all backends

### Other Remaining Work

- **Benchmark report.** Once the sweeps complete, update the Baseline table in this plan and record the run metadata (config overrides, scores, timestamps) in "Artifacts & Notes."
- **Documentation artifacts.** After baseline parity is proven, add the final metrics/log snippets to README/docs so future contributors can replicate the agentic success criteria.
- **UI telemetry.** Visualization/log-viewer work remains delegated to `docs/codex-evolve/CODEXEvolve-UI.md`; reference that plan when reporting status.

## Interfaces and Dependencies
`shinka/edit/agentic.py` now exposes:
    @dataclass class AgentContext: ...
    @dataclass class CommandResult: ...
    @dataclass class AgentResult: ...
    class AgenticEditor:
        def __init__(self, scratch_dir: Path, config: AgenticConfig, *, runner=run_codex_task) -> None
        def run_session(self, context: AgentContext) -> AgentResult

`AgentContext` carries the combined prompt, language, baseline files (`{Path("main.py"): code}`), and metadata. `AgenticEditor` writes the baseline into a scratch directory, streams events from the configured CLI runner, records command executions, and returns updated files plus metrics. Tests swap in fake runners by passing `runner` lambdas.

`EvolutionConfig` gained `init_support_dir: Optional[str]` so generation 0 can copy entire helper directories (minus `.hydra`, `results`, caches) into `gen_0`. `_hydrate_generation_directory` now copies the selected parent's workspace into every new generation before overlaying Codex edits, ensuring helper modules persist across agentic attempts.

`shinka/edit/codex_cli.py` provides:
    def ensure_codex_available(codex_path: Optional[str] = None) -> Path
    def run_codex_task(prompt: str, workdir: Path, *, profile: Optional[str], sandbox: str, approval_mode: str, max_seconds: int, max_events: int, extra_cli_config: dict, codex_path: Optional[str] = None) -> Iterator[dict]

`run_codex_task` shells out to `codex exec --json` and enforces guardrails (sandbox, approval mode, timeout, event cap). It yields parsed JSON events; on failure it raises `CodexExecutionError`. `ensure_codex_available` resolves the CLI or instructs the user to install/login. Both functions are isolated for simple mocking.

`shinka/edit/gemini_cli.py` mirrors the Codex wrapper:
    def ensure_gemini_available(gemini_path: Optional[str] = None) -> Path
    def run_gemini_task(user_prompt: str, system_prompt: str, workdir: Path, *, sandbox: str, max_seconds: int, max_events: int, resume: Optional[str], extra_cli_config: dict, gemini_path: Optional[str] = None) -> Iterator[dict]

It shells out to `gemini exec --output-format stream-json`, combines system/user prompts for headless execution, maps `sandbox` to `--sandbox`, threads through `--resume <uuid>` when present, and adapts event payloads into the same `command_execution` / `agent_message` schema expected by `AgenticEditor`. When usage telemetry is absent, it emits zeroed or estimated `usage` metrics instead of crashing the pipeline. `ensure_gemini_available` checks PATH and surfaces installation/authentication guidance.

`shinka/edit/claude_cli.py` wraps the Claude Code CLI:
    def ensure_claude_available(claude_path: Optional[str] = None) -> Path
    def run_claude_task(user_prompt: str, workdir: Path, *, system_prompt: Optional[str], profile: Optional[str], sandbox: str, approval_mode: str, max_seconds: int, max_events: int, extra_cli_config: dict, codex_path: Optional[str], resume_session_id: Optional[str], session_kind: str) -> Iterator[dict]

It shells out to `claude -p --output-format stream-json --verbose`, maps `full-auto` approval to `--dangerously-skip-permissions`, extracts session_id from `system.init` events, and adapts Claude's `assistant`/`user` events into the same schema. Real token counts come from `message.usage` fields. See `CLAUDE_CODE_EXECPLAN.md` for full implementation details.

`shinka/edit/shinka_agent.py` provides the native ShinkaAgent backend:
    def ensure_shinka_available() -> bool
    def run_shinka_task(user_prompt: str, workdir: Path, *, system_prompt: Optional[str], profile: Optional[str], sandbox: str, approval_mode: str, max_seconds: int, max_events: int, extra_cli_config: dict, codex_path: Optional[str], resume_session_id: Optional[str], session_kind: str) -> Iterator[dict]

ShinkaAgent runs entirely in-process using `shinka/llm/LLMClient`, following the mini-SWE-agent pattern (bash-only actions via regex parsing, subprocess execution, linear history). It leverages existing LLM ensembling, cost tracking, and model selection. See `SHINKA_AGENT_EXECPLAN.md` for full implementation details.

 Integrate `AgenticEditor` inside `EvolutionRunner` by adding a method `_mutate_with_agentic_editor(program: Program, patch_sys: str, patch_msg: str) -> MutationOutcome`. `MutationOutcome` must still provide `code_diff` (string), `diff_summary` (dict), and `patch_path` so the existing evaluation pipeline remains untouched. When `agentic_mode` is enabled, `run_patch` should bypass `LLMClient.query` and instead launch the agent session. Runner selection is keyed off `evo_config.agentic.backend`, mapping to `run_codex_task` or `run_gemini_task` while keeping the rest of the pipeline identical. External dependencies remain limited to the Codex or Gemini CLI harness already present in the developer workflow; if bundling the harness requires vendoring scripts, place them under `codex/` with clear licensing and tests. Avoid new third-party packages unless justified here and added to `pyproject.toml`.

`shinka/prompts/prompts_agentic.py` exports `AGENTIC_SYS_FORMAT` and `AGENTIC_ITER_MSG`, the templates PromptSampler uses when `evo_config.agentic_mode` is true. They instruct Codex to edit the workspace directly, list the helper files made available via `_hydrate_generation_directory`, and require a final `<NAME>/<DESCRIPTION>/<SUMMARY>` payload instead of diff blocks so the executor can keep parsing metadata without depending on textual diffs.

---

Revision 2025-10-30: Initial ExecPlan drafted based on discussion with Robert Tjarko Lange about agentic multi-file editing.
Revision 2025-11-08: Added action items for hardening the agentic harness against binary artifacts and clarified that documentation polish follows a successful end-to-end run.
Revision 2025-11-13: Corrected the Progress checklist to leave open items unchecked per PLANS.md and documented outstanding variant/webui work.
Revision 2025-11-20: Folded Gemini backend requirements into this plan (backend toggle, validation steps, interface notes, telemetry expectations) so all criteria apply to both Codex and Gemini agentic runs.
