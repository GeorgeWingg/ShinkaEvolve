import json
from pathlib import Path


def _make_runner(tmp_path, monkeypatch):
    from shinka.core.runner import EvolutionConfig, EvolutionRunner
    from shinka.database.dbase import DatabaseConfig
    from shinka.launch.scheduler import LocalJobConfig

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    results_root = tmp_path / "results"
    evo_config = EvolutionConfig(
        results_dir=str(results_root),
        language="python",
        agentic_mode=True,
        embedding_model=None,
        llm_models=["gpt-4.1-mini"],
    )
    job_config = LocalJobConfig(eval_program_path="")
    db_config = DatabaseConfig(db_path="evolution_db.sqlite")

    return EvolutionRunner(
        evo_config=evo_config,
        job_config=job_config,
        db_config=db_config,
        verbose=False,
    )


def test_agentic_evaluator_integrity_violation_for_modified_existing_file(tmp_path, monkeypatch):
    """If the evaluator edits a pre-existing candidate file, force correct=false."""
    from shinka.eval.agentic import AgenticEvaluatorResult

    runner = _make_runner(tmp_path, monkeypatch)

    gen_dir = Path(runner.results_dir) / "gen_1"
    gen_dir.mkdir(parents=True, exist_ok=True)
    (gen_dir / "main.py").write_text("print('original')\n", encoding="utf-8")

    results_dir = gen_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    class DummyEvaluator:
        def evaluate(self, **kwargs):
            repo_root = Path(kwargs["repo_root"])
            metrics_path = Path(kwargs["metrics_path"])

            # Illegitimate: modify an existing file.
            (repo_root / "main.py").write_text("print('modified')\n", encoding="utf-8")

            metrics_path.write_text(
                json.dumps(
                    {
                        "combined_score": 1.0,
                        "correct": True,
                        "details": "evaluator says ok",
                    }
                ),
                encoding="utf-8",
            )

            return AgenticEvaluatorResult(
                metrics={"combined_score": 1.0, "correct": True, "details": "evaluator says ok"},
                correct=True,
                error_message=None,
                stdout_log="",
                stderr_log="",
                session_log=[],
                commands_run=[],
                session_log_path=metrics_path.parent / "session_log.jsonl",
                session_events=[],
                session_id=None,
                session_dir=repo_root,
                elapsed_seconds=0.01,
                system_prompt="",
                user_prompt="",
            )

    runner.agentic_evaluator = DummyEvaluator()

    payload, _ = runner._run_agentic_evaluation(
        exec_fname=str(gen_dir),
        results_dir=str(results_dir),
        generation_dir=gen_dir,
        generation=1,
        parent_id="parent",
    )

    assert payload["correct"]["correct"] is False
    assert "integrity violation" in (payload["correct"]["error"] or "").lower()

    integrity = payload["agentic_eval"]["integrity"]
    assert integrity["status"] == "violation"
    assert "main.py" in integrity["modified_existing_files"]
    assert payload["metrics"].get("integrity_violation") is True


def test_agentic_evaluator_integrity_allows_new_files(tmp_path, monkeypatch):
    """Evaluator may create new files (e.g. generated tests) but must record them."""
    from shinka.eval.agentic import AgenticEvaluatorResult

    runner = _make_runner(tmp_path, monkeypatch)

    gen_dir = Path(runner.results_dir) / "gen_1"
    gen_dir.mkdir(parents=True, exist_ok=True)
    (gen_dir / "main.py").write_text("print('original')\n", encoding="utf-8")

    results_dir = gen_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    class DummyEvaluator:
        def evaluate(self, **kwargs):
            repo_root = Path(kwargs["repo_root"])
            metrics_path = Path(kwargs["metrics_path"])

            # Allowed: create a new file for probing/testing.
            (repo_root / "generated_test.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

            metrics_path.write_text(
                json.dumps(
                    {
                        "combined_score": 1.0,
                        "correct": True,
                        "details": "ok",
                    }
                ),
                encoding="utf-8",
            )

            return AgenticEvaluatorResult(
                metrics={"combined_score": 1.0, "correct": True, "details": "ok"},
                correct=True,
                error_message=None,
                stdout_log="",
                stderr_log="",
                session_log=[],
                commands_run=[],
                session_log_path=metrics_path.parent / "session_log.jsonl",
                session_events=[],
                session_id=None,
                session_dir=repo_root,
                elapsed_seconds=0.01,
                system_prompt="",
                user_prompt="",
            )

    runner.agentic_evaluator = DummyEvaluator()

    payload, _ = runner._run_agentic_evaluation(
        exec_fname=str(gen_dir),
        results_dir=str(results_dir),
        generation_dir=gen_dir,
        generation=1,
        parent_id="parent",
    )

    assert payload["correct"]["correct"] is True

    integrity = payload["agentic_eval"]["integrity"]
    assert integrity["status"] == "artifacts_only"
    assert integrity["new_files_created_count"] >= 1
    assert "generated_test.py" in integrity["new_files_created"]


def test_agentic_evaluator_module_defines_logger():
    import shinka.eval.agentic as agentic_eval

    assert hasattr(agentic_eval, "logger")
