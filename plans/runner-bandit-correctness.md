# Runner & Bandit Correctness Fixes

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This document must be maintained in accordance with `PLANS.md` at the repository root.

## Purpose / Big Picture

This plan fixes critical correctness issues in the evolution runner and backend bandit that cause:

- Duplicate job processing leading to inflated API costs and corrupted genealogy
- Evolution crashes due to probability normalization failures in backend selection
- Aggregator data corruption when parallel evaluators modify shared score objects

After this work:

- Each edit job is processed exactly once, with correct cost attribution
- Backend bandit never crashes with "probabilities must sum to 1" errors
- Parallel ensemble evaluations produce consistent, deterministic results

Observable behavior: Run a multi-backend evolution with ensemble evaluation for 50+ generations without crashes, cost inflation, or inconsistent scores.

## Progress

- [ ] Milestone 1: Fix duplicate job processing in `_check_completed_jobs()`
- [ ] Milestone 2: Fix probability normalization in BackendBandit
- [ ] Milestone 3: Make EvaluatorScore immutable and fix aggregator mutation
- [ ] Run full test suite and validate fixes

## Surprises & Discoveries

(To be filled during implementation)

## Decision Log

(To be filled during implementation)

## Outcomes & Retrospective

(To be filled after completion)

## Context and Orientation

Key concepts:

- RunningJob: A dataclass tracking an in-flight edit job (backend, futures, status)
- BackendBandit: Multi-armed bandit selecting between agentic backends (Codex, Gemini, Claude, etc.)
- EvaluatorScore: Dataclass holding evaluation results from each evaluator
- Ensemble Aggregator: Combines scores from multiple evaluators into final result

Key files:

- `shinka/core/runner.py`: Main evolution runner with job processing loop (>4000 lines)
- `shinka/llm/backend_bandit.py`: Backend selection with Thompson sampling
- `shinka/eval/aggregator.py`: Ensemble score aggregation
- `shinka/eval/ensemble.py`: Parallel evaluator orchestration

Problem 1 - Duplicate Job Processing (runner.py lines 2154-2196):
Jobs can be added to `completed_edits` list twice: once when edit_future completes and again when checking status. This causes `_process_completed_edit_job()` to run twice per job.

Problem 2 - Probability Normalization (backend_bandit.py lines 138, 218):
When posteriors sum to a very small number (near zero), normalization produces NaN/Inf values. The subsequent `np.random.choice()` fails with "probabilities must sum to 1".

Problem 3 - Aggregator Mutation (aggregator.py lines 134-140):
When `failure_mode == "zero"`, the aggregator mutates `EvaluatorScore.score` in place. Since these objects are shared references, parallel threads can corrupt each other's scores.

## Plan of Work

### Milestone 1: Fix Duplicate Job Processing

In `shinka/core/runner.py`, the `_check_completed_jobs()` method has redundant appends to `completed_edits`. The fix:

1. Change `completed_edits` from a list to a set (using job IDs)
2. Add deduplication check before appending
3. Verify no job is processed more than once

Current buggy pattern:
    completed_edits: List[RunningJob] = []
    for job in self.running_jobs:
        if job.edit_future.done():
            completed_edits.append(job)  # First append
        ...
        if job.status == "awaiting_novelty":
            completed_edits.append(job)  # Second append - DUPLICATE!

Fixed pattern:
    completed_edit_ids: Set[str] = set()
    completed_edits: List[RunningJob] = []
    for job in self.running_jobs:
        if job.edit_future.done() and job.job_id not in completed_edit_ids:
            completed_edits.append(job)
            completed_edit_ids.add(job.job_id)

### Milestone 2: Fix Probability Normalization

In `shinka/llm/backend_bandit.py`, add robust normalization that handles edge cases:

Current buggy code (lines 137-138):
    posteriors = posteriors / posteriors.sum() if posteriors.sum() > 0 else np.ones(len(available)) / len(available)

Fixed code with multiple safeguards:
    total = posteriors.sum()
    if total <= 0 or not np.isfinite(total):
        posteriors = np.ones(len(available)) / len(available)
    else:
        posteriors = posteriors / total
        posteriors = np.clip(posteriors, 0, 1)
        posteriors = posteriors / posteriors.sum()  # Re-normalize after clip

Apply the same fix to `sample_with_fallback()` method (line 218).

### Milestone 3: Make EvaluatorScore Immutable

In `shinka/eval/aggregator.py`, prevent mutation by creating copies:

Current buggy code (lines 134-140):
    if self.config.failure_mode == "zero":
        for name, s in failed.items():
            s.score = 0.0          # MUTATES original!
            s.correct = False      # MUTATES original!
        successful.update(failed)

Fixed code using dataclass replace:
    from dataclasses import replace

    if self.config.failure_mode == "zero":
        for name, s in failed.items():
            modified = replace(s, score=0.0, correct=False)
            successful[name] = modified
        failed = {}

This preserves the original score objects unchanged.

## Concrete Steps

All commands run from repository root: `/Users/juno/workspace/shrinkaevolve-codexevolve`

Milestone 1:
    # Read current _check_completed_jobs implementation
    Read shinka/core/runner.py lines 2150-2220

    # Apply deduplication fix
    Edit shinka/core/runner.py

    # Run runner tests
    uv run pytest tests/test_runner.py -v

Milestone 2:
    # Read current sample() implementation
    Read shinka/llm/backend_bandit.py lines 130-145

    # Apply robust normalization
    Edit shinka/llm/backend_bandit.py

    # Also fix sample_with_fallback
    Read shinka/llm/backend_bandit.py lines 210-225
    Edit shinka/llm/backend_bandit.py

    # Run bandit tests
    uv run pytest tests/test_backend_bandit.py -v

Milestone 3:
    # Read current aggregate() implementation
    Read shinka/eval/aggregator.py lines 125-160

    # Apply immutable copy pattern
    Edit shinka/eval/aggregator.py

    # Run evaluator tests
    uv run pytest tests/test_evaluator.py tests/test_ensemble.py -v

Final validation:
    uv run pytest tests/ -q
    uv run ruff check shinka/core/runner.py shinka/llm/backend_bandit.py shinka/eval/aggregator.py

## Success Criteria & Validation

SC-01: No duplicate job processing
  Evidence: Add logging in `_process_completed_edit_job()` and verify each job_id appears exactly once in logs during a test run

SC-02: Probability normalization is robust
  Evidence: Unit test with posteriors summing to 1e-10 and 0 does not crash

SC-03: Aggregator doesn't mutate scores
  Evidence: Unit test verifying original EvaluatorScore objects unchanged after aggregate()

SC-04: All tests pass
  Evidence: `uv run pytest tests/ -q` shows no failures

## Idempotence and Recovery

All changes are edits to existing methods. If a test fails, read the error, re-read the file, and adjust the fix. Test databases are ephemeral.

## Artifacts and Notes

(To be filled with test output during implementation)

## Interfaces and Dependencies

Existing interfaces (no changes to signatures):

- `BackendBandit.sample() -> str`
- `BackendBandit.sample_with_fallback(exclude: Optional[List[str]] = None) -> str`
- `Aggregator.aggregate(scores: Dict[str, EvaluatorScore], raw_results: Dict) -> AggregatedResult`

No new functions or types required. Changes are internal to existing methods.
