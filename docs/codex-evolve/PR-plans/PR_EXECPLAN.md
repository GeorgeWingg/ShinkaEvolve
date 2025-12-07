# Agentic Multi-Turn Editing PR ExecPlan

This ExecPlan is a living document. Follow every requirement in `PLANS.md` when updating it. Treat the reader as a newcomer who only has this ExecPlan plus the repository checkout.

## Purpose / Big Picture

This ExecPlan is the PR gate for shipping the agentic stack: multi-turn editing (Codex/Gemini), agentic evaluator, and WebUI telemetry, while keeping legacy single-file flows safe by default. A PR is ready when every success criterion below is satisfied **with recorded evidence** and all automated reviews (LLM + human) report no unresolved blockers.

## Scope & Constraints

- In-repo only (Codex/Gemini CLIs + OpenAI creds from `.env` are the only external dependencies).
- Legacy defaults must remain safe: `agentic_mode=false` and legacy evaluator still work for circle_packing and other tasks unless explicitly overridden.
- Pattern_synth remains removed; focus validation on circle_packing and other shipped variants.
- Follow repo tooling: `uv pip install -e .`, `pytest tests`, `ruff check shinka tests`, `black --check shinka tests`, `isort shinka tests`.
- Docs (README, docs/configuration.md, AGENTS.md) and this plan must reflect the current commands, variants, and log paths.

## PR Gate Criteria (must all be satisfied with evidence)

- **TODO Tracker:** All P0/P1 items in `TODO_EXECPLAN.md` resolved with evidence before proceeding.
- Quality bar: `pytest tests`, `ruff check shinka tests`, `black --check shinka tests`, `isort shinka tests` all pass.
- Runs with artifacts recorded in “Artifacts and Notes” (verifiable results dir, key metric, log path, session IDs). **No redactions or edits of logs/metrics; do not overwrite runs after the fact.** If a run fails, rerun with a new timestamped results dir—never tamper with existing evidence.
  - Circle packing legacy: `agentic_mode=false`, metric (sum radii), no `agentic_eval_sessions/`.
  - Circle packing agentic (Codex backend): multi-file Codex session + evaluator metrics.
  - Circle packing agentic (Gemini backend): mirrored run with `+evo_config.agentic.backend=gemini`, same artifacts/metrics schema.
- Evidence per run: command, `results/...` path, log path, key metric, `agentic_sessions/<uuid>` and/or `agentic_eval_sessions/<uuid>`. Evidence must be emitted by the actual run (no synthetic/hand-edited text). If sensitive data must be hidden, rerun without it; do not modify logs.
- LLM review passes (see “LLM Review Playbook” below) with no unresolved P0/P1 findings.
- `REVIEW_EXECPLAN.md` completed: all review runs executed, logged, and summarized into this plan’s Artifacts before marking this ExecPlan done.

## LLM Review Playbook

**Updated 2025-12-05:** Reviews now use `codex review` (exec mode) instead of interactive prompts. See `REVIEW_EXECPLAN.md` for full details.

**Review Framework:** `tests/llm_reviews/`
- Runner: `./tests/llm_reviews/run_reviews.sh`
- Output: `tests/llm_reviews/results/<timestamp>/`
- Summary: `summary.md` with P0/P1/P2 counts

**Review Categories:**

| ID | Scope | Focus | Mode |
|----|-------|-------|------|
| S1-S3 | Security | Subprocess injection, API key exposure, path traversal | Full file |
| E1-E3 | Error Handling | Subprocess errors, DB transactions, API responses | Full file |
| C1-C3 | Consistency | Backend interface parity, config schema, telemetry | Diff vs main |
| R1 | Resources | Process cleanup, PID management | Diff vs main |
| W1 | WebUI | XSS vulnerabilities | Diff vs main |
| L1 | Logic | Novelty judge correctness | Diff vs main |

**Running Reviews:**
```bash
cd /Users/juno/workspace/shrinkaevolve
./tests/llm_reviews/run_reviews.sh          # All reviews
./tests/llm_reviews/run_reviews.sh --security  # Security only
```

**Success Criteria:**
- P0 findings = 0 (must fix before PR)
- P1 findings tracked in TODO_EXECPLAN.md

## Current State and Findings

1. Agentic editing scaffolding exists (`shinka/edit/agentic.py`, `shinka/tools/`, prompts) but is uncommitted upstream and lacks a documented workflow.
2. Evaluators still assume single-file duplication; Robert explicitly requested validation that these still pass after the refactor.
3. No officially blessed multi-file example or smoke test exists; reviewers will need something deterministic to run.
4. Several new docs (*.md under `docs/codex-evolve/`) are scratch pads and will be removed; this ExecPlan replaces them for PR preparation.

## Approach Overview

1. Harden the agentic harness: ensure Codex CLI sessions get consistent Hydra wiring, workspace snapshots, command logging, and failure recovery.
2. Keep the legacy pipeline compiled and tested: guard new behavior behind `evo_config.agentic_mode` and optional evaluator overrides so existing examples stay untouched unless explicitly switched.
3. Expand automated coverage plus manual smoke runs so reviewers can replay both agentic and legacy flows.
4. Clean up artifacts, document usage, and outline PR/test expectations in this ExecPlan so commit review stays smooth.

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

### Milestone 4 – Documentation refresh

Update user-facing docs to match the current agentic stack:
1. Refresh README.md, docs/configuration.md, and AGENTS.md with current commands and troubleshooting.
2. Ensure WebUI notes and screenshots align with the latest layout and features.
3. Trim references to removed variants/configs and keep circle_packing as the primary smoke target.

### Milestone 5 – PR readiness and cleanup

1. Remove obsolete scratch markdown files or move their canonical content into README/docs; ensure only this ExecPlan remains as the planning artifact.
2. Delete stray binary artifacts or clipboard images (already started) and add `.gitignore` entries if needed.
3. Re-run the full quality bar: `pytest tests`, `ruff check shinka tests`, `black --check shinka tests`, `isort --check shinka tests`.
4. Generate final smoke runs for both legacy and agentic modes, saving log paths and metrics under `Artifacts and Notes`.
5. Prepare a concise PR description referencing this ExecPlan, summarizing risk areas (multi-file editing, evaluator changes), and list validation commands executed.

## Progress

- [x] (2025-11-17 – 2025-11-27) Milestone 1 – Environment ready, baseline tests recorded.
  - Dev environment set up with `uv pip install -e .`
  - Initial tests passing, agentic scaffolding in place
- [x] (2025-11-27 – 2025-11-30) Milestone 2 – Agentic harness implemented with tests.
  - 4 backends complete: Codex, Gemini (✅ working), Claude (🟡 file capture fix applied), ShinkaAgent (🟡 needs stronger model)
  - Tests: `test_agentic_scaffolding.py`, `test_claude_cli.py`, `test_gemini_cli.py`, `test_backend_bandit.py`
  - Multi-file workspace hydration working
- [x] (2025-11-27 – 2025-11-30) Milestone 3 – Legacy compatibility verified and evaluator switch documented.
  - Legacy mode (`agentic_mode=false`) still works
  - Agentic evaluator complete with mode toggle
  - Multi-file embedding (TODO-001) resolved
- [x] (2025-11-30 – 2025-12-01) Milestone 4 – Documentation refreshed and aligned to current stack.
  - README.md updated with agentic mode section
  - docs/configuration.md expanded with evaluator/agentic params
  - AGENTS.md documents all 4 backends
- [x] (2025-12-05) Milestone 5 – Final QA, cleanup, and PR narrative complete.
  - LLM review framework migrated to `codex review` (tests/llm_reviews/)
  - EXECPLAN_VALIDATION.md tests structure defined
  - Critical blockers resolved: Claude file capture (TODO-110 ✅), Real-time streaming (TODO-111 ✅ - PID-based registry as PRIMARY source)

See `MASTER_EXECPLAN_TRACKER.md` for consolidated status across all ExecPlans.

## Success Criteria & Validation

1. **Legacy safety:** `env $(cat .env | xargs) uv run shinka_launch variant=circle_packing_example evo_config.agentic_mode=false evo_config.num_generations=1` completes successfully, emits metrics under `results/<task>/<timestamp>/gen_0/`, and no `agentic_eval_sessions` directory is created.
2. **Agentic editing (Codex):** `env $(cat .env | xargs) uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini evo_config.num_generations=1` produces at least one node where Codex edits multiple files; transcripts under `results/.../agentic_sessions/<uuid>/session_log.jsonl`; metrics recorded in SQLite.
3. **Agentic editing (Gemini):** Repeat (2) with `+evo_config.agentic.backend=gemini`; capture `agentic_sessions/<uuid>/session_log.jsonl` plus evaluator metrics to prove schema compatibility and functional equivalence.
4. **Automated quality bar:** `pytest tests`, `ruff check shinka tests`, `black --check shinka tests`, and `isort --check shinka tests` all pass on the final branch.
5. **Documentation refreshed:** README.md, docs/configuration.md, and AGENTS.md contain the current agentic workflows, commands above, and troubleshooting notes; reviewers can follow them without extra context.

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
