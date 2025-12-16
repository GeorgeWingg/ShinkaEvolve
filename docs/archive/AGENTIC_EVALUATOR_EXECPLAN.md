# Agentic Evaluator ExecPlan

This ExecPlan is a living document. Follow every requirement in `PLANS.md` when updating it. Treat the reader as a newcomer who only has this ExecPlan plus the repository checkout.

## Purpose / Big Picture

Agentic editing already copies the entire parent workspace into `gen_<n>/workspace_snapshot/` and lets the Codex agent mutate any files it needs. However, evaluation still forces everything through the legacy single-file pipeline (`main.py`/`original.py` and deterministic scripts that expect one entry point). That mismatch adds brittle “duplicate this file” plumbing, blocks multi-file metrics, and prevents us from capturing richer qualitative signals. This plan delivers an Agentic Evaluator: a Codex-powered judge that executes the deterministic evaluation scripts inside the same sandbox, inspects their outputs, and reports metrics back to Shinka without relying on duplicated files. When agentic mode is enabled, this evaluator becomes the default; legacy deterministic evaluators remain available as an override for non-agentic runs or regression tests.

The intention here is that if you were working inside of a large production codebase, and you wanted to run shinkaevolve on that codebase, you would not want the evolved code to be in initial.py / main.py artifacts, but would like each node and change to be a modification to the codebase, and then for each node to be evaluated. and in order to do this, we must have the produced code be evaluated in an agentic, multi file style which allows codex to read the files, run commands like unit tests which might already exist, end to end tests or maybe write test files itself to evaluate the code as accuratley as possible. 

## Scope & Constraints

- Applies to all variants launched with `+evo_config.agentic_mode=true`. Legacy/CLI-triggered runs without agentic mode must keep the deterministic evaluator flow untouched.
- The evaluator must run deterministic scripts (e.g., `examples/circle_packing/evaluate.py`) inside the same hydrated workspace so numeric metrics remain reproducible and comparable with historical results.
- Agentic judgements must emit structured metrics compatible with the existing SQLite schema (`combined_score`, `public`, `private`, `text_feedback`). Additional qualitative notes can be stored under metadata.
- Codex judge sessions must be sandboxed (workspace-write) and logged in `results/<task>/<ts>/agentic_eval_sessions/<uuid>/session_log.jsonl`, mirroring the mutation logs.
- No duplicate `main.py`/`original.py` artifacts once the agentic evaluator is active; evaluators consume the workspace snapshot directly.
- Hydra configs must expose a single switch (e.g., `evo_config.evaluator.mode`) with values `legacy` or `agentic`. Default to `agentic` whenever `agentic_mode=true`, but allow explicit overrides for debugging.

## Progress

- [x] (2025-11-16 15:35Z) Drafted the Agentic Evaluator ExecPlan outlining scope, milestones, and success criteria.
- [x] (2025-11-16 16:20Z) Extended `EvolutionConfig`/Hydra presets with `evaluator.mode` + `AgenticEvaluatorConfig`, added judge prompt templates, and landed the `shinka.eval.agentic.AgenticEvaluator` scaffolding so runs can instantiate the new judge.
- [x] (2025-11-16 17:45Z) Integrated the agentic evaluator into `EvolutionRunner`: auto-selects the judge when `agentic_mode=true`, bypasses the old scheduler path, stores session metadata, and records the Codex-evaluated metrics while leaving legacy evaluation available via override.
- [x] (2025-11-16 19:05Z) Documented evaluator toggles/log locations in README, AGENTS.md, and `docs/configuration.md` so operators know how to switch between agentic and legacy evaluators.
- [x] (2025-11-17 00:32Z) Milestone 1 – Baseline research and configuration scaffolding. Finalized `EvaluatorConfig`/Hydra wiring plus prompt scaffolding in `shinka/prompts/prompts_agentic_eval.py`, documented usage in README + docs/configuration.md, and updated AGENTS.md with evaluator expectations.
- [x] (2025-11-17 00:36Z) Milestone 2 – Agentic evaluator implementation (Codex judge, workspace orchestration, logging). Landed `shinka/eval/agentic.py` with JSON streaming, metrics parsing, and command capture plus regression tests under `tests/test_agentic_scaffolding.py`.
- [x] (2025-11-17 00:40Z) Milestone 3 – Integration with `EvolutionRunner` (auto-select evaluator, remove `main.py` duplication for agentic runs, database wiring). Runner now hydrates a single workspace snapshot, records evaluator metadata (elapsed_seconds, stdout/stderr, session log path) per program, and exposes it through `/get_programs`.

## Success Criteria & Validation

1. **Agentic default:** Running `shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true` without overrides uses the agentic evaluator, skips the legacy `main.py/original.py` duplication, and records a Codex transcript under `agentic_eval_sessions/<uuid>/session_log.jsonl`. *Validation:*  
   `env $(cat .env | xargs) uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true evo_config.num_generations=1 evo_config.max_parallel_jobs=1 evo_config.evaluator.mode=agentic +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini evo_config.results_dir=results/shinka_circle_packing/2025.11.17095500_agentic_eval_ui`  
   produced `results/shinka_circle_packing/2025.11.17095500_agentic_eval_ui/gen_0/results/metrics.json` (sum of radii 2.62629) and `agentic_eval_sessions/c3176180d080483aba3b34f45980080e/session_log.jsonl`. `/get_programs` now returns `agentic_evaluator.elapsed_seconds=12.62s` plus the previewed timeline, confirming the UI can link directly to the log.
2. **Configurable overrides:** Passing `evo_config.evaluator.mode=legacy` falls back to the deterministic evaluator even while the editor stays agentic. *Validation:*  
   `env $(cat .env | xargs) uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true evo_config.evaluator.mode=legacy evo_config.num_generations=1 +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini evo_config.results_dir=results/shinka_circle_packing/2025.11.17104000_legacy_eval_ui`  
   completed with the traditional scheduler, emitted metrics under `gen_0/results/metrics.json`, and—critically—no `agentic_eval_sessions/` directory or `agentic_evaluator` metadata shows up in `evolution_db.sqlite`, proving the override works.
3. **Multi-file task proof:** Running the agentic evaluator on a multi-module workload preserves helper edits and executes deterministic scripts inside the workspace snapshot. *Validation:*  
   generated `combined_score=0.5682`, left the helper edits in `gen_0/workspace_snapshot`, and saved the evaluator log at `agentic_eval_sessions/07b04f5149134c54b376ac4d3fe57d11/session_log.jsonl`, with `/get_programs` showing the commands that set `PYTHONPATH` so helper module updates were honored.
4. **Documentation:** README, AGENTS.md, and `docs/configuration.md` now describe the agentic evaluator, log paths, overrides, and the “default to `gpt-5.1-codex-mini`” guidance (see AGENTS.md “Long-Running Codex Experiments” + new ExecPlan discipline bullet). These files cite the exact commands above so operators can reproduce the runs.

## Architecture Overview

- **Evaluator selection:** Extend `EvolutionConfig` with `evaluator: EvaluatorConfig` containing `mode` (`legacy` | `agentic`) and `agentic` options (timeouts, tool limits, commands to run). When `agentic_mode=true` and the user does not override, set `mode=agentic` automatically.
- **AgenticEvaluator:** New component under `shinka/eval/agentic.py` that shells out to `codex exec --json` with judge prompts. The evaluator receives the workspace path, deterministic script path, and expected outputs, runs commands (e.g., `python examples/circle_packing/evaluate.py --program_path workspace_snapshot/main.py ...`), parses logs, and emits JSON metrics.
- **Session logging:** Mirror the mutation logs: each evaluation run creates `results/<task>/<ts>/agentic_eval_sessions/<uuid>/session_log.jsonl`, `workspace_snapshot/`, and optional artifacts (`stdout.log`, `metrics.json`). Link the evaluator session ID into the `metadata` column of the evaluated `Program` row.
- **Pipeline integration:** `EvolutionRunner` hydrates one workspace snapshot, hands that path to both the mutator and evaluator, and never copies out `main.py` unless running in legacy mode. The scheduler still tracks runtime per evaluation job for resource accounting.

## Milestones

1. **Milestone 1 – Configuration & Research:** Map current evaluation flow, design the `EvaluatorConfig`, and document required prompts/commands for deterministic scripts. Deliverable: config schema, prompt drafts, and a doc snippet describing how deterministic scripts will be invoked.
2. **Milestone 2 – Agentic Evaluator Core:** Implement `AgenticEvaluator` (Codex CLI wrapper, prompt templates, log handling) plus unit tests that mock Codex responses and verify command execution + JSON parsing.
3. **Milestone 3 – Runner Integration:** Update `EvolutionRunner` to hydrate one workspace snapshot, invoke the agentic evaluator when configured, drop the `main.py/original.py` duplication for agentic runs, and persist evaluator session data. Add regression tests covering evaluator selection and DB writes.

## Concrete Steps

1. **Understand the current evaluation pipeline.** Document how `Scheduler.run()` invokes task evaluators, what artifacts they expect, and how results flow into `Program`. Identify every code path that assumes `main.py` exists (runner, scheduler, WebUI) to know what to gate behind `legacy` mode.
2. **Design configuration knobs.** Extend `EvolutionConfig` with `evaluator.mode` and `evaluator.agentic` (timeout, max Codex events, verbs). Ensure Hydra defaults choose `agentic` when `agentic_mode=true`, yet allow explicit overrides in configs/CLI. Update `configs/evolution/*.yaml` accordingly.
3. **Author judge prompts.** Under `shinka/prompts`, add `AGENTIC_EVAL_SYS` and `AGENTIC_EVAL_USER` templates that instruct Codex how to inspect the workspace snapshot, run deterministic scripts, parse their numeric outputs, and emit a structured JSON result.
4. **Build `AgenticEvaluator`.** Create `shinka/eval/agentic.py` housing a class that manages scratch dirs, launches `codex exec --json` with the judge prompts, streams events to `agentic_eval_sessions/<uuid>/session_log.jsonl`, enforces time/tool limits, and returns `(metrics, stdout, stderr, api_costs)`.
5. **Integrate with the runner.** When submitting a job, hydrate the workspace once, pass that path to the agentic editor, and after mutation call either the agentic evaluator or legacy evaluator based on `evaluator.mode`. Remove the `main.py/original.py` copy step for agentic mode, ensuring evaluators read from `gen_<n>/workspace_snapshot`.
6. **Persist outputs.** Store evaluator metrics in the existing DB schema, embed the judge session ID/log path under `Program.metadata`, and copy any `metrics.json` into `gen_<n>/best/results/` for WebUI consumption.
7. **Add tests.** Unit-test `AgenticEvaluator` with mocked Codex responses, add integration tests covering evaluator selection, and extend WebUI snapshots to verify they handle the new artifact structure.
8. **Document and migrate tooling.** Update README, AGENTS.md, and docs/configuration.md with instructions on evaluator modes, logging locations, and override commands. Provide migration notes for existing results and update scripts that previously assumed `main.py` is the sole artifact.

## Remaining Work & Risks

- **Command safety:** Codex judges will run deterministic scripts; we must ensure prompts keep them within the sandbox and constrain commands to the evaluation entrypoints to avoid destructive operations. (Ongoing monitoring)
- **Performance:** Evaluations now involve two Codex sessions (mutator + judge). Track per-generation runtime/API usage and consider caching deterministic script outputs if the judge only needs to summarize logs.
- **WebUI updates:** **Completed.** The visualization now displays agentic evaluator runtime, status, metrics JSON, and timelines. See `docs/codex-evolve/AGENTIC_EVALUATOR_UI.md`.
- **Fallback behavior:** If the agentic evaluator fails (timeout, Codex error), define retry/backoff rules and automatic fallback to legacy evaluation to avoid stalling long runs.

## Surprises & Discoveries

- The implementation of the UI components was found to be already integrated into `viz_tree.html`, simplifying the UI rollout.

## Decision Log

- Decision: Delegate UI work to `AGENTIC_EVALUATOR_UI.md`.
  Rationale: Separation of concerns.
  Date/Author: 2025-11-13 / Codex (agent)

- Decision: Mark WebUI updates as complete.
  Rationale: Code audit confirmed `shinka/webui/viz_tree.html` contains the full agentic evaluator UI logic.
  Date/Author: 2025-11-22 / Gemini (agent)

## Notes

- Place all related prototypes, metrics screenshots, and log excerpts in `results/<task>/<timestamp>/agentic_eval_sessions/` and reference them here once available.
- Keep this ExecPlan synchronized with any changes in the CodexEvolve UI plan to avoid duplicated effort.
