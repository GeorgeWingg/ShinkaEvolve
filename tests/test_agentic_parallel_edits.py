import threading
import time
from pathlib import Path

import pytest

from shinka.core.runner import (
    EvolutionConfig,
    EvolutionRunner,
    PreparedEditJob,
    EditWorkerResult,
)
from shinka.database import DatabaseConfig, Program
from shinka.launch import LocalJobConfig


def test_agentic_edits_run_in_parallel(monkeypatch, tmp_path):
    """Increasing max_parallel_jobs should produce overlapping agentic edits."""

    evo_config = EvolutionConfig(
        language="python",
        results_dir=str(tmp_path / "results"),
        agentic_mode=True,
        max_parallel_jobs=3,
        num_generations=4,
        embedding_model=None,  # avoid real embedding calls
    )
    job_config = LocalJobConfig(eval_program_path=str(tmp_path / "evaluate.py"))
    db_config = DatabaseConfig()

    runner = EvolutionRunner(
        evo_config=evo_config,
        job_config=job_config,
        db_config=db_config,
        verbose=False,
    )

    # Pretend generation 0 already ran.
    runner.next_generation_to_submit = 1

    def fake_prepare_agentic_edit_job(self, *, generation, novelty_attempt, resample_attempt):
        results_dir = f"{runner.results_dir}/gen_{generation}/results"
        Path(results_dir).mkdir(parents=True, exist_ok=True)
        generation_dir = Path(runner.results_dir) / f"gen_{generation}"
        generation_dir.mkdir(parents=True, exist_ok=True)

        parent_program = Program(
            id=f"parent_{generation}",
            code="=== FILE: main.py ===\nprint('hi')\n",
            language="python",
        )
        return PreparedEditJob(
            generation=generation,
            parent_program=parent_program,
            archive_programs=[],
            top_k_programs=[],
            patch_sys="",
            patch_msg="",
            patch_type="agentic",
            generation_dir=generation_dir,
            results_dir=results_dir,
            novelty_attempt=novelty_attempt,
            resample_attempt=resample_attempt,
            selected_backend="codex",
            bandit_summary=None,
            meta_recs=None,
            meta_summary=None,
            meta_scratch=None,
        )

    monkeypatch.setattr(
        EvolutionRunner, "_prepare_agentic_edit_job", fake_prepare_agentic_edit_job
    )

    lock = threading.Lock()
    active = 0
    max_active = 0
    barrier = threading.Barrier(3, timeout=5)

    def fake_worker(self, prepared_job):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            barrier.wait()
        except Exception:
            pass
        time.sleep(0.1)
        with lock:
            active -= 1
        return EditWorkerResult(
            generation=prepared_job.generation,
            generation_dir=prepared_job.generation_dir,
            results_dir=prepared_job.results_dir,
            corpus_text="",
            embedding=[],
            embed_cost=0.0,
            corpus_meta={},
            meta_edit_data={"api_costs": 0.0, "error_attempt": None},
            code_diff=None,
            num_applied=1,
        )

    monkeypatch.setattr(EvolutionRunner, "_run_agentic_patch_worker", fake_worker)

    # Submit three edit jobs; they should overlap due to max_parallel_jobs=3.
    runner._submit_new_job()
    runner._submit_new_job()
    runner._submit_new_job()

    with runner._jobs_lock:
        futures = [j.edit_future for j in runner.running_jobs if j.edit_future]

    for fut in futures:
        fut.result(timeout=5)

    assert max_active >= 3

    # Cleanup executors to avoid thread leaks in test suite.
    if runner._agentic_edit_executor is not None:
        runner._agentic_edit_executor.shutdown(wait=True)
        runner._agentic_edit_executor = None
    if runner._agentic_executor is not None:
        runner._agentic_executor.shutdown(wait=True)
        runner._agentic_executor = None
