from pathlib import Path
from concurrent.futures import Future


def test_agentic_submit_new_job_does_not_require_main(monkeypatch, tmp_path):
    """Agentic mode should submit generations even if no main.* exists."""
    from shinka.core.runner import EvolutionRunner, EvolutionConfig
    from shinka.database.dbase import Program, DatabaseConfig
    from shinka.launch.scheduler import LocalJobConfig

    results_root = tmp_path / "results"

    evo_config = EvolutionConfig(
        results_dir=str(results_root),
        agentic_mode=True,
        num_generations=3,
        max_novelty_attempts=1,
        max_patch_resamples=1,
        max_parallel_jobs=1,
    )
    job_config = LocalJobConfig(eval_program_path="")
    db_config = DatabaseConfig()

    # Minimal runner
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    runner = EvolutionRunner(evo_config=evo_config, job_config=job_config, db_config=db_config, verbose=False)

    # Avoid spawning a real agentic edit thread in this unit test, since it can
    # keep pytest alive even after the assertion passes.
    class DummyInlineExecutor:
        def submit(self, *args, **kwargs):
            fut: Future = Future()
            fut.set_result(None)
            return fut

        def shutdown(self, wait: bool = True, cancel_futures: bool = False):
            return None

    runner._agentic_edit_executor = DummyInlineExecutor()  # type: ignore[assignment]

    # Stub DB sampling and island manager access.
    parent_program = Program(
        id="parent",
        code="pass",
        language="python",
        generation=0,
    )

    class DummyDB:
        island_manager = None

        def sample(self, *args, **kwargs):
            return parent_program, [], []

    runner.db = DummyDB()

    # Stub run_patch to create a generation dir with no main file.
    def fake_run_patch(*args, **kwargs):
        generation = kwargs.get("generation", 1)
        gen_dir = Path(runner.results_dir) / f"gen_{generation}"
        gen_dir.mkdir(parents=True, exist_ok=True)
        (gen_dir / "foo.py").write_text("print('hi')\n", encoding="utf-8")
        return None, {"api_costs": 0.0, "error_attempt": None}, 1

    runner.run_patch = fake_run_patch  # type: ignore[assignment]

    # Provide a deterministic embedding so get_code_embedding doesn't short‑circuit.
    class DummyEmbedding:
        def get_embedding(self, text):
            return [0.1, 0.1], 0.0

    runner.embedding = DummyEmbedding()

    class DummyNovelty:
        def should_check_novelty(self, *args, **kwargs):
            return False

        def log_novelty_skip_message(self, *args, **kwargs):
            return None

    runner.novelty_judge = DummyNovelty()

    class DummyMeta:
        def get_current(self):
            return None, None, None

    runner.meta_summarizer = DummyMeta()

    # Avoid invoking the agentic evaluator in this unit test.
    class DummyScheduler:
        def submit_async(self, exec_fname, results_dir):
            return "job-1"

    runner.scheduler = DummyScheduler()
    runner.evaluator_mode = "legacy"

    # Simulate post-gen0 state.
    runner.completed_generations = 1
    runner.next_generation_to_submit = 1

    runner._submit_new_job()

    assert runner.next_generation_to_submit == 2
    assert len(runner.running_jobs) == 1
    job = runner.running_jobs[0]
    assert job.exec_fname == str(results_root / "gen_1")
    assert not (results_root / "gen_1" / "main.py").exists()
