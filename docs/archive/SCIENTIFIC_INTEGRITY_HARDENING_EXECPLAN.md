# Scientific Integrity Hardening (Legacy Parity + Evaluator Guardrails)

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Follow every requirement in `PLANS.md` when updating this document. Treat the reader as a newcomer who only has this ExecPlan plus the repository checkout.

## Purpose / Big Picture

This change restores the “scientific integrity” properties that existed before agentic editing landed, while keeping the agentic stack usable.

Concretely:

1) When `evo_config.agentic_mode=false` (legacy mode), the evolutionary algorithm must behave like the pre-agentic system: the database `Program.code` field must contain the actual executable program source (e.g., `main.py`), patching must apply cleanly to that source, complexity metrics must be computed on that source, and novelty checks must compare like-with-like.

2) When `evo_config.agentic_mode=true` (agentic mode), evaluation must remain auditable and must not silently “fix” or rewrite the candidate code during scoring. However, the evaluator may legitimately create temporary artifacts (for example, generated tests) to probe the candidate, as long as it does not modify any pre-existing candidate source files. The system must record evidence of what happened.

3) The agentic evaluator must be robust in failure paths (no crashes due to missing logger definitions).

The visible “it works” outcome is:

- A legacy run can evolve `main.py` without accidentally writing an embedding corpus (with `=== FILE:` headers) into `main.py`.
- Agentic evaluation returns `correct=false` with an explicit integrity error if the evaluator modifies any pre-existing candidate source file during scoring. Creating new files (e.g., temporary tests) is allowed, but should be recorded.

## Progress

- [x] (2025-12-14 13:40Z) ExecPlan drafted and approved to proceed.
- [x] (2025-12-14 14:18Z) Implemented legacy parity fix for `Program.code` and novelty/embedding inputs (legacy mode now embeds/stores only `main.{lang_ext}` text).
- [x] (2025-12-14 14:18Z) Added evaluator integrity guard (detects & invalidates evaluator-written modifications to pre-existing candidate files; allows new-file artifacts like generated tests).
- [x] (2025-12-14 14:18Z) Fixed `shinka/eval/agentic.py` logger bug (`logger = logging.getLogger(__name__)`).
- [x] (2025-12-14 14:18Z) Added WebUI surfacing for evaluator integrity results (status chip + Evaluation Integrity section in Evaluation tab).
- [x] (2025-12-14 14:18Z) Added/adjusted regression tests for legacy parity + evaluator guard; fixed a pytest hang by preventing a unit test from spawning a real agentic edit worker thread.
- [x] (2025-12-14 14:18Z) Ran targeted validation commands and recorded evidence in `Success Criteria & Validation` (attempted full suite; two unrelated Jules auth tests fail in this environment due to a local credential store).
- [x] (2025-12-14 14:18Z) Summarized outcomes and updated this ExecPlan sections for completion.

## Surprises & Discoveries

- Observation: Current legacy flow stores an “embedding corpus” text (with `=== FILE:` headers) in `Program.code`, but the legacy patcher and prompt generator still treat `Program.code` as the executable source. This can cause invalid `main.py` outputs and breaks comparability with pre-agentic results.
  Evidence: `shinka/core/runner.py` writes `Program.code=corpus_text` and legacy patching uses `original_str=parent_program.code`.

- Observation: `shinka/eval/agentic.py` references `logger` without defining it.
  Evidence: `shinka/eval/agentic.py` calls `logger.error(...)` but has no `import logging` / `logger = ...`.

- Observation: `tests/test_agentic_no_main_submission.py` can cause pytest to hang after the test passes, because agentic `_submit_new_job()` submits a real background edit future that keeps non-daemon worker threads alive.
  Evidence: `uv run pytest -q tests/test_agentic_no_main_submission.py` printed “1 passed …” but did not exit until killed.

- Observation: `uv run pytest -q tests` currently fails two Jules auth tests in this environment because the Jules auth checker consults the unified credential store (`~/.shinka/credentials.json`) in addition to env vars.
  Evidence: `tests/test_jules_cli.py::TestJulesAuth::test_check_jules_auth_missing_api_key` expects “missing API key => unavailable”, but local stored credentials make it appear available. This is not related to this ExecPlan’s runner/evaluator integrity changes.
## Decision Log

- Decision: Restore legacy semantics by making `Program.code` equal to the executable program text in legacy mode, and ensuring novelty/embedding inputs match that same text (rather than a multi-file corpus).
  Rationale: This preserves historical comparability and prevents writing non-code corpus markers into `main.py`.
  Date/Author: 2025-12-14 / GPT-5.2

- Decision: Add an evaluator “integrity guard” that detects source-file modifications during agentic evaluation and invalidates those results.
  Rationale: The evaluator prompt is a soft policy; an explicit check provides hard scientific/audit guarantees without relying on a particular CLI sandbox capability. The guard is nuanced: it forbids modifications to pre-existing candidate files, but allows creation of new artifacts (including generated tests) while recording them for audit.
  Date/Author: 2025-12-14 / GPT-5.2

## Outcomes & Retrospective

Legacy-mode runs now preserve the pre-agentic meaning of `Program.code` (executable source text), which restores comparability of novelty/embeddings and avoids accidentally writing multi-file corpus markers into `main.py`.

Agentic-mode evaluation now enforces a hard integrity invariant: the evaluator may create new artifacts (e.g., generated tests), but any modification or deletion of pre-existing candidate files is detected, recorded, and forces `correct=false` regardless of the evaluator’s claimed score.

The WebUI now surfaces this integrity metadata so violations are visible without digging through logs.

## Context and Orientation

Key files/modules involved:

- `shinka/core/runner.py`: Orchestrates evolution, patching, embedding/novelty, evaluation, and DB writes.
- `shinka/database/dbase.py`: Stores `Program` records; computes complexity from `Program.code`.
- `shinka/core/embedding_corpus.py`: Builds a deterministic multi-file “corpus” representation used for embeddings/novelty in agentic mode.
- `shinka/eval/agentic.py`: Implements the agentic evaluator wrapper and parses `metrics.json`.
- `shinka/prompts/prompts_base.py`: Renders `Program.code` into prompts for inspirations/history.

Definitions used in this plan:

- “Legacy mode”: `evo_config.agentic_mode=false`, where editing uses single-file diff/full patching and the evaluator is a deterministic script (the pre-agentic workflow).
- “Embedding corpus”: A string representation of a workspace that concatenates multiple files with `=== FILE: ... ===` headers. This is useful for multi-file tasks but is not valid program source.
- “Scientific integrity” (operational definition for this repo): Re-running the same configuration yields results derived from the exact candidate program produced by the evolution operator, with an auditable trail; evaluation does not secretly mutate the candidate; and legacy runs remain comparable to historical runs by preserving data semantics and selection logic.

## Plan of Work

1) Restore legacy parity for how code is stored and compared.

In `shinka/core/runner.py`, change the legacy (non-agentic) path so that:

- `Program.code` is always the actual executable program text (contents of `main.{lang_ext}`).
- Embedding/novelty inputs for legacy mode are derived from that same executable text (not from `EmbeddingCorpus`).
- Any multi-file “corpus” is reserved for agentic mode (or stored separately in metadata), so legacy patching (`apply_diff_patch` / `apply_full_patch`) operates on plain source code as before.

2) Add agentic evaluator integrity guard.

In `shinka/core/runner.py` `_run_agentic_evaluation(...)`:

- Compute a pre-eval fingerprint of relevant workspace “source” files (exclude `results/` and other known artifacts).
- Run the evaluator as today.
- Compute a post-eval fingerprint and compare.
- If any pre-existing candidate file changed, set `correct=false` and annotate `metrics.details` / `agentic_eval` metadata with the violation and the list of modified files (and short diffs for text files when feasible).
- If new files are created during evaluation (for example, generated tests), do not fail the run, but record them in `agentic_eval` metadata under an explicit field (for example `new_files_created`) so the behavior is auditable.

Also, update `_build_eval_command(...)` to reduce incidental workspace writes by setting `PYTHONDONTWRITEBYTECODE=1` in the evaluation environment.

3) Fix agentic evaluator logger bug.

In `shinka/eval/agentic.py`, import `logging` and define `logger = logging.getLogger(__name__)`.

4) Tests.

Add regression coverage under `tests/` for:

- Legacy mode: `Program.code` stored to DB does not contain corpus headers and matches the executable file content.
- Agentic evaluator: if a mocked evaluator modifies `main.py` during evaluation, `_run_agentic_evaluation` returns `correct=false` and records an integrity error.
- Module import: `shinka/eval/agentic.py` no longer raises `NameError` on `logger` in parse-error paths.

5) WebUI improvements (auditing ergonomics).

In `shinka/webui/viz_tree.html` (Evaluation tab rendering), add an “Evaluation Integrity” section for agentic evaluator runs that displays:

- Integrity status: `clean` / `artifacts_only` / `violation`.
- Counts and file lists for:
  - Pre-existing candidate files modified (violation).
  - New files created (allowed artifacts, e.g., generated tests).
  - Ignored/allowed changes (for example, `results/` outputs and `.pyc` if any slip through).

In the node header/status area, add a small “Integrity” status chip so violations are visible at a glance when browsing.

## Concrete Steps

All commands run from repo root.

1) Implement code changes (files listed in “Plan of Work”).

2) Run focused tests:

    uv run pytest -q tests/test_agentic_no_main_submission.py
    uv run pytest -q tests/test_embedding_corpus.py
    uv run pytest -q tests/test_edit_circle.py

3) Run the full suite if feasible:

    uv run pytest -q tests

## Success Criteria & Validation

1) Legacy parity: legacy mode embedding/storage uses only the executable `main.py` text (no multi-file corpus markers), preserving pre-agentic semantics for patching/complexity/novelty comparability.
   Validation:
     Command:
       uv run pytest -q tests/test_legacy_parity_embedding_corpus.py
     Expected:
       1 passed

2) Evaluator guard: in a unit test, a mocked agentic evaluator that edits a source file causes `correct=false` and records an integrity violation message/metadata.
   Validation:
     Command:
       uv run pytest -q tests/test_agentic_evaluator_integrity_guard.py
     Expected:
       3 passed

3) Robustness: importing and using `shinka/eval/agentic.py` no longer hits `NameError: logger is not defined`.
   Validation:
     Evidence: `tests/test_agentic_evaluator_integrity_guard.py` includes `test_agentic_evaluator_module_defines_logger`.

4) No unintended regressions in existing edit tooling.
   Validation:
     Command:
       uv run pytest -q tests/test_edit_circle.py
     Expected:
       2 passed

5) WebUI exposes evaluator integrity outcomes for agentic nodes.
   Validation:
     Evidence: `shinka/webui/viz_tree.html` contains an “Evaluation Integrity” section and an integrity status chip builder.

## Idempotence and Recovery

These changes are safe to apply repeatedly. If a mistake is made:

- Prefer reverting via manual edits to the affected files (do not use destructive git commands in this repo).
- Re-run the focused pytest subset above to confirm behavior.

## Artifacts and Notes

Focused validations (run 2025-12-14 14:18Z):

  uv run pytest -q tests/test_legacy_parity_embedding_corpus.py tests/test_agentic_evaluator_integrity_guard.py
  ....
  4 passed in 0.82s

  uv run pytest -q tests/test_embedding_corpus.py tests/test_agentic_scaffolding.py tests/test_agentic_no_main_submission.py
  ...................
  19 passed in 0.99s

  uv run pytest -q tests/test_edit_circle.py
  ..
  2 passed in 1.23s

UI check (presence of section heading):

  rg -n "Evaluation Integrity" shinka/webui/viz_tree.html | head
  14423:                            <h5>Evaluation Integrity</h5>

## Interfaces and Dependencies

No new external services are introduced. This change stays within the existing Shinka codebase and test harness.

## Plan Revision Notes

- (2025-12-14 14:18Z) Updated `Progress`, `Success Criteria & Validation`, and `Artifacts and Notes` to match implemented files/tests (`uv run pytest`), and recorded the pytest hang discovery/fix and environment-specific Jules test failures for future triage.
