# ShinkaEvolve Automated Review ExecPlan

This ExecPlan is a living document. Maintain it in full compliance with `PLANS.md` (repo root). It governs the LLM-driven code reviews required to unblock the PR in `PR_EXECPLAN.md`.

## Purpose / Big Picture

Run high-fidelity, non-interactive code reviews across the current branch using **`codex review`** (Codex CLI exec mode). Reviews are structured as **aspect-focused passes** rather than general code review - each pass targets one specific concern (security, error handling, consistency, etc.) across relevant files.

The goal is to produce immutable, actionable findings for every changed area before the PR can be marked complete. This approach treats LLM review as a **test framework** - reviewable, repeatable, and CI-integratable.

## Progress

### Legacy Gemini CLI Reviews (2025-11-30)
- [x] (2025-11-30 16:00Z) Engine/scheduler review run saved.
- [x] (2025-11-30 16:00Z) Agentic editor/backends review run saved.
- [x] (2025-11-30 16:00Z) Evaluator + prompts review run saved.
- [x] (2025-11-30 16:00Z) WebUI review run saved.
- [x] (2025-11-30 16:00Z) Configs/defaults review run saved.
- [x] (2025-11-30 16:00Z) Docs/plans review run saved.
- [x] (2025-11-30 16:00Z) Tests/tooling review run saved.

### Codex Review Migration (2025-12-05)
- [x] (2025-12-05) Created `tests/llm_reviews/` framework with aspect-focused reviews
- [x] (2025-12-05) Created `run_reviews.sh` script with 11 review passes
- [x] (2025-12-05) Created `parse_results.py` for JSON aggregation
- [ ] Run initial baseline with `codex review`
- [ ] Document findings in PR_EXECPLAN.md

## Surprises & Discoveries

- (2025-12-05) `codex review` now supports non-interactive exec mode (PR #7444 by @jif-oai)
- Mix scope strategy: Full file reviews for security/error handling, diff-only for consistency checks

## Decision Log

- (2025-12-05/juno) — Migrated from Gemini CLI to `codex review` for native integration with Codex review prompts
- (2025-12-05/juno) — Changed artifact storage from `results/reviews/` to `tests/llm_reviews/results/` to emphasize test framework nature
- (2025-12-05/juno) — Adopted "mix scope" strategy: security/error reviews on full files, others on `--base main` diff

## Outcomes & Retrospective

- **Legacy Review Completion**: Initial review campaign completed 2025-11-30 via Gemini CLI
- **Verdict**: Codebase was ready for PR submission; all P0/P1 blockers resolved
- **Migration**: Now using `codex review` for ongoing/future reviews

## Context and Orientation

- **Review tool**: `codex review` (Codex CLI non-interactive mode)
- **Evidence storage**: `tests/llm_reviews/results/<timestamp>/`
- **Runner script**: `tests/llm_reviews/run_reviews.sh`
- **Summary generator**: `tests/llm_reviews/parse_results.py`

## Plan of Work

1. Run `tests/llm_reviews/run_reviews.sh` to execute all aspect-focused reviews
2. Review generated `summary.md` for P0/P1/P2 findings
3. Address any P0 findings immediately
4. Track P1 findings in `TODO_EXECPLAN.md`
5. Rerun affected reviews after fixes land

## Review Categories

Reviews are organized into **aspect-focused passes**. Each uses either:
- **Full file scope**: Thorough analysis of entire files (security, error handling)
- **Diff scope** (`--base main`): Focused on changes in this PR (consistency, resources)

### Full File Reviews (P0 Priority)

| ID | Focus | Target Files |
|----|-------|--------------|
| S1 | Subprocess injection | `shinka/edit/{codex,claude,gemini}_cli.py` |
| S2 | API key exposure | `shinka/tools/credentials.py`, `shinka/webui/visualization.py` |
| S3 | Path traversal | `shinka/edit/agentic.py` |
| E1 | Subprocess error handling | `shinka/edit/{codex,claude,gemini}_cli.py` |
| E2 | Database transaction safety | `shinka/database/dbase.py` |
| E3 | WebUI API error responses | `shinka/webui/visualization.py` |

### Diff-Only Reviews (P1 Priority)

| ID | Focus | Scope |
|----|-------|-------|
| C1 | Backend interface parity | All `shinka/edit/*.py` backends |
| C2 | Config schema consistency | `configs/evolution/*.yaml` |
| C3 | Event/telemetry format | `shinka/edit/*.py` telemetry |
| R1 | Process cleanup | `shinka/edit/agentic.py`, `shinka/tools/codex_session_registry.py` |
| W1 | XSS vulnerabilities | `shinka/webui/viz_tree.html` |
| L1 | Novelty judge correctness | `shinka/core/novelty_judge.py` |

## Concrete Steps

Run from repo root `/Users/juno/workspace/shrinkaevolve`:

```bash
# Run all reviews
./tests/llm_reviews/run_reviews.sh

# Run only security reviews
./tests/llm_reviews/run_reviews.sh --security

# Run only error handling reviews
./tests/llm_reviews/run_reviews.sh --error

# Run only diff-based reviews
./tests/llm_reviews/run_reviews.sh --diff-only
```

Individual review example:
```bash
# Full file review (security)
codex review "Review shinka/edit/codex_cli.py for command injection vulnerabilities. Check shell=True usage, unsanitized inputs." --json

# Diff-only review (consistency)
codex review --base main "Review for backend interface consistency across shinka/edit/*.py" --json
```

## Success Criteria & Validation

1. All 11 review passes complete with artifacts in `tests/llm_reviews/results/<timestamp>/`
2. `summary.md` generated with P0/P1/P2 counts per review
3. **P0 findings = 0** (critical issues must be fixed)
4. **P1 findings tracked** in `TODO_EXECPLAN.md` or resolved
5. Summary linked in `PR_EXECPLAN.md` Artifacts section

## Idempotence and Recovery

- Each run creates a new timestamped directory; never overwrite previous outputs
- Rerun specific reviews by editing `run_reviews.sh` or running `codex review` directly
- Failed reviews (non-zero exit, empty output) are logged but don't block other reviews

## Artifacts and Notes

Results structure:
```
tests/llm_reviews/results/<timestamp>/
├── S1_subprocess_injection.json
├── S2_api_key_exposure.json
├── S3_path_traversal.json
├── E1_subprocess_errors.json
├── E2_database_safety.json
├── E3_webui_errors.json
├── C1_backend_parity.json
├── C2_config_schema.json
├── C3_telemetry_format.json
├── R1_process_cleanup.json
├── W1_xss.json
├── L1_novelty_judge.json
├── summary.md
└── summary.json
```

## Interfaces and Dependencies

- **Codex CLI**: `codex` binary in PATH (from openai/codex repo)
- **Python 3.10+**: For `parse_results.py` summary generation
- **Repository**: `/Users/juno/workspace/shrinkaevolve` (branch `codex-evolve`)
- **Git remote**: `main` branch for diff comparisons

## Legacy Artifacts

Previous Gemini CLI review artifacts (2025-11-30) stored in:
- `results/reviews/<area>/<timestamp>/` (deprecated location)
- System prompt: `/Users/juno/workspace/codex/codex-rs/core/review_prompt.md`
