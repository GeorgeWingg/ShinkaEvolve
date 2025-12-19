# Error Handling & Reliability: CLI Validation, Failure Recovery, and DB Contention

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This document must be maintained in accordance with PLANS.md at the repository root.


## Purpose / Big Picture

After implementing this plan, ShinkaEvolve evolution runs will be significantly more robust. Users will see clear error messages when CLI backends fail (instead of silent failures), evolution runs will automatically retry failed jobs instead of aborting, and high-parallelism runs (8+ concurrent jobs) will no longer suffer database contention failures.

The key user-visible improvements are:
1. When Claude or Gemini CLI crashes, users see an explicit error message with the exit code and stderr
2. When a single agentic edit fails, the evolution continues with automatic retry instead of aborting
3. Runs with `max_parallel_jobs=8` or higher complete successfully without "database is locked" errors

To verify success, run an evolution with intentionally failing prompts and observe that the run continues gracefully, logging errors but not crashing.


## Progress

- [ ] Milestone 1: Add Exit Code Validation to Claude and Gemini CLIs
- [ ] Milestone 2: Implement Job Retry Logic in Evolution Runner
- [ ] Milestone 3: Fix Database Write Contention with Batched Transactions
- [ ] Milestone 4: Improve Error Context and User Feedback


## Surprises & Discoveries

(To be populated during implementation)


## Decision Log

- Decision: Prioritize CLI exit code validation before retry logic.
  Rationale: Without proper error detection, retry logic has nothing to retry. Exit code validation is the foundation for reliable error handling.
  Date/Author: 2025-12-17


## Outcomes & Retrospective

(To be populated at completion)


## Context and Orientation

ShinkaEvolve uses three external CLI tools for agentic code editing: Codex CLI, Claude CLI, and Gemini CLI. These are wrapped in Python modules:

- `shinka/edit/codex_cli.py` (574 lines): Wraps OpenAI's Codex CLI
- `shinka/edit/claude_cli.py` (628 lines): Wraps Anthropic's Claude CLI
- `shinka/edit/gemini_cli.py` (589 lines): Wraps Google's Gemini CLI

The evolution engine is in `shinka/core/runner.py` (3800+ lines). It spawns parallel jobs via `ThreadPoolExecutor` and writes results to a SQLite database via `shinka/database/dbase.py`.

**Current error handling gaps:**

1. **Exit code validation**: Codex validates exit codes (lines 383-388), but Claude and Gemini do not. If Claude/Gemini crash, the error is silently swallowed.

2. **No retry logic**: When `edit_future.result()` raises an exception (line 2129 in runner.py), a failure result is synthesized but the job is never retried.

3. **Database contention**: Multiple completed jobs write to SQLite simultaneously without batching. At 8+ parallel jobs, SQLite's serialized mode causes "database is locked" errors that abort the run.


## Plan of Work

**Milestone 1** adds exit code validation to Claude and Gemini CLI wrappers, matching the pattern already used by Codex. This ensures failures are detected and reported.

**Milestone 2** implements retry logic in the evolution runner, allowing failed jobs to be retried with exponential backoff before being marked as failures.

**Milestone 3** fixes database write contention by implementing write batching and transaction coordination for parallel job completion.

**Milestone 4** improves error messages throughout the system to give users actionable feedback when things go wrong.


## Milestone 1: Add Exit Code Validation to Claude and Gemini CLIs

After this milestone, Claude and Gemini CLI crashes will raise explicit exceptions with exit codes and stderr content, matching the Codex behavior.

**Understanding the current pattern (Codex):**

In `shinka/edit/codex_cli.py` lines 383-388, Codex validates the exit code:

    process.wait(timeout=1)
    if process.returncode != 0:
        raise CodexExecutionError(
            f"Codex process exited with code {process.returncode}"
        )

**Edit 1: Add exit code validation to Claude CLI**

Open `shinka/edit/claude_cli.py` and locate the event loop exit (around line 528). After the loop ends, add:

    # After the event processing loop
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

    if process.returncode is not None and process.returncode != 0:
        stderr_content = ""
        if process.stderr:
            try:
                stderr_content = process.stderr.read()
            except Exception:
                pass
        raise ClaudeExecutionError(
            f"Claude process exited with code {process.returncode}. "
            f"Stderr: {stderr_content[:500] if stderr_content else 'N/A'}"
        )

Also add the exception class near the top of the file (after the imports):

    class ClaudeExecutionError(Exception):
        """Raised when Claude CLI process fails."""
        pass

**Edit 2: Add exit code validation to Gemini CLI**

Open `shinka/edit/gemini_cli.py` and apply the same pattern around line 494:

    # After the event processing loop
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

    if process.returncode is not None and process.returncode != 0:
        stderr_content = ""
        if process.stderr:
            try:
                stderr_content = process.stderr.read()
            except Exception:
                pass
        raise GeminiExecutionError(
            f"Gemini process exited with code {process.returncode}. "
            f"Stderr: {stderr_content[:500] if stderr_content else 'N/A'}"
        )

Add the exception class near the top:

    class GeminiExecutionError(Exception):
        """Raised when Gemini CLI process fails."""
        pass

**Edit 3: Export the new exception classes**

Ensure both exception classes are exported in the module's `__all__` list if one exists, or are importable directly.

**Verification for Milestone 1:**

1. Create a test that intentionally triggers a Claude failure:

       # In a Python shell or test file
       from shinka.edit.claude_cli import run_claude_task, ClaudeExecutionError

       try:
           # Use an invalid model or malformed request
           result = run_claude_task(
               prompt="test",
               workspace_dir="/tmp",
               extra_cli_args=["--model", "nonexistent-model-xyz"]
           )
       except ClaudeExecutionError as e:
           print(f"Caught expected error: {e}")

2. Verify the exception includes exit code and stderr content.


## Milestone 2: Implement Job Retry Logic in Evolution Runner

After this milestone, failed agentic jobs will be retried up to 2 times with exponential backoff before being marked as permanent failures.

**Edit 1: Add retry configuration to EvolutionConfig**

Open `shinka/core/runner.py` and locate the `EvolutionConfig` dataclass (around line 100). Add:

    # Retry configuration for failed jobs
    job_max_retries: int = 2
    job_retry_base_delay: float = 5.0  # seconds

**Edit 2: Track retry state in job metadata**

In the `_submit_agentic_job` method (around line 2050), add retry tracking to the job context:

    job_context = {
        "program_id": program_id,
        "generation": generation,
        "retry_count": 0,
        "max_retries": self.config.job_max_retries,
        # ... existing fields
    }

**Edit 3: Implement retry logic in _check_completed_jobs**

Locate `_check_completed_jobs` (around line 2120). When a job fails, check if retries are available:

    try:
        result = future.result()
    except Exception as e:
        job_ctx = self._pending_jobs.get(future)
        retry_count = job_ctx.get("retry_count", 0)
        max_retries = job_ctx.get("max_retries", 2)

        if retry_count < max_retries:
            # Schedule retry with exponential backoff
            delay = self.config.job_retry_base_delay * (2 ** retry_count)
            logger.warning(
                f"Job {job_ctx['program_id']} failed (attempt {retry_count + 1}), "
                f"retrying in {delay}s: {e}"
            )
            job_ctx["retry_count"] = retry_count + 1
            self._schedule_retry(job_ctx, delay)
            continue

        # Max retries exceeded, mark as permanent failure
        logger.error(f"Job {job_ctx['program_id']} failed after {max_retries} retries: {e}")
        result = self._synthesize_failure_result(job_ctx, str(e))

**Edit 4: Add retry scheduling helper**

Add a method to schedule retries:

    def _schedule_retry(self, job_context: dict, delay: float):
        """Schedule a job for retry after a delay."""
        def delayed_submit():
            time.sleep(delay)
            self._submit_agentic_job(**job_context)

        self._executor.submit(delayed_submit)

**Verification for Milestone 2:**

1. Run an evolution with a configuration known to occasionally fail (e.g., rate limits)
2. Check logs for "retrying in Xs" messages
3. Verify that transient failures are recovered while permanent failures are logged
4. Confirm evolution completes even when some jobs fail initially


## Milestone 3: Fix Database Write Contention with Batched Transactions

After this milestone, runs with 8+ parallel jobs will complete without "database is locked" errors.

**Edit 1: Add write queue for job finalization**

Open `shinka/core/runner.py` and add a write queue near the class initialization:

    from queue import Queue
    from threading import Thread

    class EvolutionRunner:
        def __init__(self, ...):
            # ... existing init
            self._db_write_queue = Queue()
            self._db_writer_thread = None

**Edit 2: Implement batched database writer**

Add a dedicated writer thread that batches database writes:

    def _start_db_writer(self):
        """Start background thread for batched DB writes."""
        def writer_loop():
            batch = []
            while True:
                try:
                    item = self._db_write_queue.get(timeout=1.0)
                    if item is None:  # Shutdown signal
                        break
                    batch.append(item)

                    # Batch up to 5 items or flush after timeout
                    while len(batch) < 5:
                        try:
                            item = self._db_write_queue.get(timeout=0.1)
                            if item is None:
                                break
                            batch.append(item)
                        except:
                            break

                    # Write batch in single transaction
                    if batch:
                        self._write_batch_to_db(batch)
                        batch = []

                except:
                    if batch:
                        self._write_batch_to_db(batch)
                        batch = []

        self._db_writer_thread = Thread(target=writer_loop, daemon=True)
        self._db_writer_thread.start()

    def _write_batch_to_db(self, batch):
        """Write a batch of programs in a single transaction."""
        with self.db._get_connection() as conn:
            for program_data in batch:
                # Insert program using existing db.add() logic but in batch
                self.db._add_program_internal(conn, program_data)
            conn.commit()

**Edit 3: Route finalization through write queue**

In `_finalize_job`, instead of writing directly, queue the write:

    # Instead of: self.db.add(program)
    self._db_write_queue.put(program_data)

**Edit 4: Ensure graceful shutdown**

Add shutdown logic to flush the queue:

    def _stop_db_writer(self):
        """Stop the DB writer thread and flush remaining writes."""
        if self._db_writer_thread:
            self._db_write_queue.put(None)  # Shutdown signal
            self._db_writer_thread.join(timeout=30)

**Verification for Milestone 3:**

1. Run an evolution with high parallelism:

       uv run shinka_launch variant=agentic +evo_config.max_parallel_jobs=10

2. Monitor for "database is locked" errors (should not occur)
3. Verify all programs are written to the database after completion
4. Check that program count matches expected generations


## Milestone 4: Improve Error Context and User Feedback

After this milestone, users will see clear, actionable error messages instead of generic failures.

**Edit 1: Add error context to CLI configuration failures**

Open `shinka/edit/codex_cli.py` and locate the exception handling around line 77:

    except Exception:
        pass  # Silent failure

Change to:

    except Exception as e:
        logger.warning(f"Failed to load Codex profile configuration: {e}")
        # Continue with defaults, but user is informed

Apply the same pattern to similar silent exception handlers in all three CLI files.

**Edit 2: Add detailed error messages for temp file failures**

In the temp file creation sections (around lines 242-249 in codex_cli.py), enhance the error message:

    except Exception as e:
        import errno
        err_detail = ""
        if hasattr(e, 'errno'):
            err_detail = f" (errno={e.errno}: {os.strerror(e.errno)})"
        raise CodexExecutionError(
            f"Failed to create prompt temp file{err_detail}. "
            f"Check disk space and permissions in {temp_dir}: {e}"
        )

**Edit 3: Log backend availability at startup**

In `shinka/eval/ensemble.py`, add informative logging when backends are unavailable:

    try:
        from shinka.edit.codex_cli import run_codex_task
        runners["codex"] = run_codex_task
        logger.info("Codex backend available")
    except ImportError as e:
        logger.warning(f"Codex backend unavailable: {e}")

**Verification for Milestone 4:**

1. Intentionally misconfigure a CLI (e.g., invalid profile name)
2. Run an evolution and check logs for descriptive warning messages
3. Verify the evolution continues with remaining working backends
4. Check that error messages include actionable information (file paths, error codes)


## Concrete Steps

All commands should be run from the repository root: `/Users/juno/workspace/shrinkaevolve-codexevolve`

    # After each milestone, run tests
    uv run pytest tests/test_edit_base.py tests/test_e2e_backends.py -v

    # Test CLI error handling specifically
    uv run pytest tests/ -k "cli" -v

    # Run a high-parallelism test to verify DB contention fix
    uv run pytest tests/test_git_evolution.py -v


## Success Criteria & Validation

1. **Exit code detection**: Claude/Gemini failures raise exceptions with exit codes. Command: run test with invalid model.

2. **Retry functionality**: Failed jobs are retried up to 2 times. Command: check logs for "retrying" messages.

3. **No DB contention**: Runs with max_parallel_jobs=10 complete without "database is locked". Command: run high-parallelism evolution.

4. **Clear error messages**: Configuration failures produce actionable log messages. Command: intentionally misconfigure and check logs.

5. **Test suite passes**: All existing tests continue to pass. Command: `uv run pytest tests/ -x`


## Idempotence and Recovery

All changes are additive (new exception classes, new retry logic, new write batching). They don't modify existing data or remove functionality.

Rollback:
- Milestone 1: Remove exception classes and exit code checks (harmless to leave)
- Milestone 2: Remove retry logic, jobs fail immediately as before
- Milestone 3: Remove write queue, revert to direct DB writes
- Milestone 4: Remove enhanced logging (harmless to leave)


## Artifacts and Notes

Expected log output after Milestone 1 (CLI failure):

    ERROR shinka.edit.claude_cli: Claude process exited with code 1. Stderr: Error: Model 'nonexistent-model' not found

Expected log output after Milestone 2 (retry):

    WARNING shinka.core.runner: Job prog_123 failed (attempt 1), retrying in 5s: Connection timeout
    WARNING shinka.core.runner: Job prog_123 failed (attempt 2), retrying in 10s: Connection timeout
    ERROR shinka.core.runner: Job prog_123 failed after 2 retries: Connection timeout

Expected log output after Milestone 4 (backend availability):

    INFO shinka.eval.ensemble: Codex backend available
    WARNING shinka.eval.ensemble: Gemini backend unavailable: No module named 'google.generativeai'
    INFO shinka.eval.ensemble: Claude backend available


## Interfaces and Dependencies

New exception classes to add:

    # In shinka/edit/claude_cli.py
    class ClaudeExecutionError(Exception):
        """Raised when Claude CLI process fails."""
        pass

    # In shinka/edit/gemini_cli.py
    class GeminiExecutionError(Exception):
        """Raised when Gemini CLI process fails."""
        pass

New configuration fields in EvolutionConfig:

    job_max_retries: int = 2
    job_retry_base_delay: float = 5.0

Modified files:
- `shinka/edit/claude_cli.py`: Exit code validation, exception class
- `shinka/edit/gemini_cli.py`: Exit code validation, exception class
- `shinka/edit/codex_cli.py`: Enhanced error messages
- `shinka/core/runner.py`: Retry logic, DB write batching
- `shinka/eval/ensemble.py`: Backend availability logging
