# Agentic Multi-Turn Editing PR ExecPlan

This ExecPlan is a living document. Follow every requirement in `PLANS.md` when updating it. Treat the reader as a newcomer who only has this ExecPlan plus the repository checkout.

## Purpose / Big Picture

This ExecPlan is the PR gate for shipping the agentic stack: multi-turn editing (Codex/Gemini), agentic evaluator, WebUI telemetry, and the picbreeder multi-file example, while keeping legacy single-file flows safe by default. A PR is ready when every success criterion below is satisfied **with recorded evidence** and all automated reviews (LLM + human) report no unresolved blockers.

## Scope & Constraints

- In-repo only (Codex/Gemini CLIs + OpenAI creds from `.env` are the only external dependencies).
- Legacy defaults must remain safe: `agentic_mode=false` and legacy evaluator still work for circle_packing and other tasks unless explicitly overridden.
- One curated multi-file example **picbreeder** is the canonical agentic showcase; pattern_synth is removed.
- Follow repo tooling: `uv pip install -e .`, `pytest tests`, `ruff check shinka tests`, `black --check shinka tests`, `isort shinka tests`.
- Docs (README, docs/configuration.md, AGENTS.md) and this plan must reflect the current commands, variants, and log paths.

## PR Gate Criteria (must all be satisfied with evidence)

- Quality bar: `pytest tests`, `ruff check shinka tests`, `black --check shinka tests`, `isort shinka tests` all pass.
- Runs with artifacts recorded in “Artifacts and Notes” (verifiable results dir, key metric, log path, session IDs). **No redactions or edits of logs/metrics; do not overwrite runs after the fact.** If a run fails, rerun with a new timestamped results dir—never tamper with existing evidence.
  - Circle packing legacy: `agentic_mode=false`, metric (sum radii), no `agentic_eval_sessions/`.
  - Circle packing agentic (Codex backend): multi-file Codex session + evaluator metrics.
  - Circle packing agentic (Gemini backend): mirrored run with `+evo_config.agentic.backend=gemini`, same artifacts/metrics schema.
  - Picbreeder agentic (Codex backend): multi-file edits preserved; evaluator metrics logged.
  - Picbreeder agentic (Gemini backend): mirrored run with `+evo_config.agentic.backend=gemini`, same artifacts/metrics schema.
  - Human Feedback UI manual checklist (from TEST_HUMAN_FEEDBACK_PLAN): iframe visibility, star widget, save works.
- Evidence per run: command, `results/...` path, log path, key metric, `agentic_sessions/<uuid>` and/or `agentic_eval_sessions/<uuid>`. Evidence must be emitted by the actual run (no synthetic/hand-edited text). If sensitive data must be hidden, rerun without it; do not modify logs.
- LLM review passes (see “LLM Review Playbook” below) with no unresolved P0/P1 findings.

## LLM Review Playbook (run after staging logical chunks)

Run reviews per area, paste findings + fixes into “Artifacts and Notes” with: scope, prompt used, issues (P0/P1/P2), resolution/PR link, files touched. Do not sanitize or rewrite review output; if there’s sensitive info, rerun with safer inputs rather than editing the transcript.

1) **Core engine & scheduler** — files: `shinka/core/runner.py`, `shinka/core/sampler.py`, `shinka/launch/scheduler.py`, `shinka/core/*bandit*`, `shinka/database/islands.py`
   - Prompt: `/review`, then `/prompts:pr-blockers`
   - Checks: sandbox + approval mode honored; resume/parent-session correctness; bandit/model-arm safety with Codex/Gemini; struct-mode override handling; DB writes (metadata, metrics) not breaking legacy.

2) **Agentic editor/backends** — files: `shinka/edit/agentic.py`, `shinka/edit/codex_cli.py`, `shinka/edit/gemini_cli.py`, `shinka/edit/types.py`, `shinka/tools/*`
   - Prompt: `/prompts:agentic-safety`
   - Checks: workspace hydration/init_support_dir; binary/artifact skip; timeout/max events; resume semantics; backend parity (event schema, sandbox flags, model mapping); error surfacing.

3) **Evaluator (agentic/legacy) + prompts** — files: `shinka/eval/*`, `shinka/prompts/prompts_agentic_eval.py`, `shinka/prompts/prompts_agentic.py`
   - Prompt: `/prompts:eval-parity`
   - Checks: evaluator mode switch, no `main.py` duplication when agentic; metrics/log paths; Codex/Gemini judge parity; prompt safety/instructions; structured metrics JSON.

4) **WebUI & visualization** — files: `shinka/webui/viz_tree.html`, `shinka/webui/visualization.py`, assets
   - Prompt: `/prompts:ui-regression`
   - Checks: tab visibility (iframe only on HF tab); agentic evaluator timeline/log links; agent sessions/downloads; performance (auto-refresh); no inline JS errors; accessibility of new controls.

5) **Human Feedback UI** — files: `shinka/webui/viz_tree.html`, `shinka/webui/visualization.py`, HF CSS/JS
   - Prompt: `/prompts:ui-regression` with HF focus or a dedicated HF prompt
   - Checks: iframe visibility toggling, star widget, save POST paths, DB fields, no bleed into other tabs.

6) **Configs/defaults** — files: `configs/evolution/*budget.yaml`, `configs/evolution/agentic.yaml`, `configs/task/picbreeder.yaml`, `configs/variant/picbreeder_example.yaml`, cluster/local configs
   - Prompt: `/prompts:config-struct`
   - Checks: defaults keep legacy safe; struct-mode keys exist; agentic flags grouped; model defaults (gpt-5.1-codex-mini); backend override wiring; results_dir/output_dir correctness.

7) **Docs & plans** — files: README.md, docs/configuration.md, AGENTS.md, ExecPlans in docs/codex-evolve/*, this PR_EXECPLAN.md
   - Prompt: `/prompts:docs-consistency`
   - Checks: commands match configs; pattern_synth removed; picbreeder commands present; log paths accurate; HF plan referenced; Gemini parity mentioned.

8) **Tests & tooling** — files: `tests/test_agentic_scaffolding.py`, `tests/test_gemini_cli.py`, `tests/tools/*`
   - Prompt: `/prompts:pr-blockers` scoped to tests
   - Checks: coverage of new code paths, mocking correctness for Codex/Gemini, flaky risks, fixture isolation, deterministic seeds.

## Current State and Findings

1. Agentic editing scaffolding exists (`shinka/edit/agentic.py`, `shinka/tools/`, prompts) but is uncommitted upstream and lacks a documented workflow.
2. Evaluators still assume single-file duplication; Robert explicitly requested validation that these still pass after the refactor.
3. No officially blessed multi-file example or smoke test exists; reviewers will need something deterministic to run.
4. Several new docs (*.md under `docs/codex-evolve/`) are scratch pads and will be removed; this ExecPlan replaces them for PR preparation.

## Approach Overview

1. Harden the agentic harness: ensure Codex CLI sessions get consistent Hydra wiring, workspace snapshots, command logging, and failure recovery.
2. Keep the legacy pipeline compiled and tested: guard new behavior behind `evo_config.agentic_mode` and optional evaluator overrides so existing examples stay untouched unless explicitly switched.
3. Produce a canonical multi-file example (picbreeder) that modifies multiple modules, carries deterministic evaluation, and is documented. (Pattern_synth removed.)
4. Expand automated coverage plus manual smoke runs so reviewers can replay both agentic and legacy flows.
5. Clean up artifacts, document usage, and outline PR/test expectations in this ExecPlan so commit review stays smooth.

## Milestones

### Milestone 1 – Baseline inventory and environment readiness

Establish a clean working environment, capture the current diff, and ensure dev dependencies are installed. Steps:
1. Run `uv pip install -e .` inside an activated `uv` environment so editable installs include new packages.
2. Run `uv pip install -r requirements-dev.txt` if `ruff/black/isort` are not available.
3. Capture `git status -sb` and `git diff --stat` to understand outstanding work; store the output under `Artifacts and Notes`.
4. Verify existing tests pass before modifications: `pytest tests -k "not agentic"` to isolate legacy suites and record results.

### Milestone 2 – Agentic editing harness completion

Implement the multi-turn editing workflow so each evolution node can spawn Codex CLI sessions:
1. Finalize `shinka/edit/agentic.py` and `shinka/edit/codex_cli.py` to orchestrate session lifecycle (workspace snapshot creation, temp dirs, cleanup, metadata persistence).
2. Flesh out `shinka/tools/` utilities (command execution sandboxes, file staging, logging helpers) needed by Codex CLI.
3. Wire Hydra configs (`configs/evolution/agentic.yaml`, variants) so `evo_config.agentic_mode=true` enables the new harness with sensible defaults (e.g., `+evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini`).
4. Update prompts under `shinka/prompts/prompts_agentic.py` to specify expectations around multi-file edits, testing, and tool usage.
5. Ensure `shinka/launch_hydra.py`/`shinka/core/runner.py` instantiate the correct edit strategy based on the config flag.
6. Add or update targeted tests (`tests/test_agentic_scaffolding.py`, `tests/tools/`) that mock Codex CLI responses and assert multi-file edits get staged properly.

### Milestone 3 – Legacy compatibility & evaluator wiring

Guarantee single-file workflows remain intact and evaluators can switch modes:
1. Audit `shinka/core/sampler.py`, `shinka/database/*`, and `configs/evolution/*budget.yaml` to confirm defaults keep `agentic_mode=false` unless explicitly set.
2. Ensure `shinka/eval/` supports both legacy and agentic evaluators. Provide a config switch (`evo_config.evaluator.mode`) with documented defaults and add regression coverage for both paths.
3. Run `pytest tests` end-to-end plus `ruff check shinka tests`, `black --check shinka tests`, `isort --check shinka tests` to confirm no regressions.
4. Capture before/after metrics for at least one legacy task (`uv run shinka_launch variant=circle_packing_example evo_config.agentic_mode=false`) and document results under `Success Criteria`.

### Milestone 4 – Multi-file example + documentation

Deliver a reproducible multi-file showcase per Robert's request:
1. Finish the example under `examples/picbreeder/` so it modifies multiple modules (canvas helpers, assets) and has deterministic evaluation scripts.
2. Provide a Hydra config (`configs/variant/picbreeder_example.yaml` + `configs/task/picbreeder.yaml`) describing required overrides.
3. Run `env $(cat .env | xargs) uv run shinka_launch variant=picbreeder_example +evo_config.agentic_mode=true +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini evo_config.num_generations=1` and record the results directory, metrics, and `agentic_eval_sessions` log path.
4. Update README.md, docs/configuration.md, and AGENTS.md with a “How to run the multi-file agentic example” section including the exact command, expected output location, and tips for tailing logs.
5. Add screenshots or CLI snippets only if they prove value; otherwise rely on textual instructions.

### Milestone 5 – PR readiness and cleanup

1. Remove obsolete scratch markdown files or move their canonical content into README/docs; ensure only this ExecPlan remains as the planning artifact.
2. Delete stray binary artifacts or clipboard images (already started) and add `.gitignore` entries if needed.
3. Re-run the full quality bar: `pytest tests`, `ruff check shinka tests`, `black --check shinka tests`, `isort --check shinka tests`.
4. Generate final smoke runs for both legacy and agentic modes, saving log paths and metrics under `Artifacts and Notes`.
5. Prepare a concise PR description referencing this ExecPlan, summarizing risk areas (multi-file editing, evaluator changes), and list validation commands executed.

## Progress

- [ ] (YYYY-MM-DD HH:MMZ) Milestone 1 – Environment ready, baseline tests recorded.
- [ ] (YYYY-MM-DD HH:MMZ) Milestone 2 – Agentic harness implemented with tests.
- [ ] (YYYY-MM-DD HH:MMZ) Milestone 3 – Legacy compatibility verified and evaluator switch documented.
- [ ] (YYYY-MM-DD HH:MMZ) Milestone 4 – Multi-file example + docs in place with recorded run.
- [ ] (YYYY-MM-DD HH:MMZ) Milestone 5 – Final QA, cleanup, and PR narrative completed.

Update each entry immediately after completing the work, replacing the timestamp with the actual UTC time and noting any surprises.

## Success Criteria & Validation

1. **Legacy safety:** `env $(cat .env | xargs) uv run shinka_launch variant=circle_packing_example evo_config.agentic_mode=false evo_config.num_generations=1` completes successfully, emits metrics under `results/<task>/<timestamp>/gen_0/`, and no `agentic_eval_sessions` directory is created.
2. **Agentic editing (Codex):** `env $(cat .env | xargs) uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini evo_config.num_generations=1` produces at least one node where Codex edits multiple files; transcripts under `results/.../agentic_sessions/<uuid>/session_log.jsonl`; metrics recorded in SQLite.
3. **Multi-file showcase (picbreeder, Codex):** `env $(cat .env | xargs) uv run shinka_launch variant=picbreeder_example +evo_config.agentic_mode=true evo_config.num_generations=1` modifies multiple picbreeder assets/modules and yields deterministic metrics saved in `results/.../metrics.json` plus evaluator logs.
4. **Gemini backend parity:** Repeat (2) and (3) with `+evo_config.agentic.backend=gemini`; capture `session_log.jsonl` plus evaluator metrics to prove schema compatibility and functional equivalence.
5. **Human Feedback UI:** Run `TEST_HUMAN_FEEDBACK_PLAN.md` steps; confirm iframe visibility bug is fixed, star widget/save works, and logs are recorded.
6. **Automated quality bar:** `pytest tests`, `ruff check shinka tests`, `black --check shinka tests`, and `isort --check shinka tests` all pass on the final branch.
7. **Documentation refreshed:** README.md, docs/configuration.md, and AGENTS.md contain the new agentic workflows, commands above, and troubleshooting notes; reviewers can follow them without extra context.

For each item, paste the command output or summarize the relevant log path under `Artifacts and Notes` when you mark it complete.

## Idempotence and Recovery

- Agentic runs write to `results/<task>/<timestamp>/`; rerun commands with a new timestamp directory if a run fails. Delete partial `results/...` dirs to keep disk usage manageable.
- Codex CLI sessions use workspace snapshots under `gen_<n>/workspace_snapshot`; it is safe to re-run a failed generation because snapshots are per-node.
- Tests and linters are safe to re-run; if they leave caches, clean with `rm -rf .pytest_cache .ruff_cache` before retrying.
- If a Hydra launch fails mid-run, kill the background process (`kill <PID>`) and tail `/tmp/shinka_launch.log` to diagnose before restarting.

## Artifacts and Notes

Maintain a short log of important outputs referenced elsewhere:
- Baseline `git status -sb` / `git diff --stat` from Milestone 1.
- Command transcripts (or summarized exit statuses) for each success criterion.
- Paths to `results/...` directories for both legacy and agentic smoke runs.
- Any evaluator transcripts or UI screenshots used in documentation.
- LLM review findings for each scope (file list, prompt used, summary of issues and resolutions).

## Interfaces and Dependencies

- `shinka/edit/agentic.py`: orchestrates Codex CLI editing sessions. Must expose an interface compatible with `EditStrategy` used by `shinka/core/runner.py`.
- `shinka/edit/codex_cli.py`: wraps the Codex CLI binary; ensure configurable model, temperature, and tool permissions via Hydra config.
- `shinka/tools/*`: reusable helpers (file staging, env loading, SQLite hooks) shared across agentic edit and eval paths.
- `shinka/eval/agentic.py` plus legacy evaluators: both must implement a shared interface so `EvolutionRunner` can switch via config.
- Hydra configs (`configs/evolution/*.yaml`, `configs/variant/*.yaml`, `configs/task/*.yaml`): provide overrides to toggle agentic mode and evaluator selection.
- Documentation files (`README.md`, `docs/configuration.md`, `AGENTS.md`, this `PR_EXECPLAN.md`) must cross-reference the same commands and log locations.

## Change Log

- (2025-11-17 15:10Z) Initial ExecPlan drafted from conversation with Robert & George; establishes milestones for multi-turn agentic PR.
