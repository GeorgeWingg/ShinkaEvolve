import json


def test_agentic_evaluator_passes_parent_and_generation(tmp_path):
    from shinka.core.runner import AgenticEvaluatorConfig
    from shinka.eval.agentic import AgenticEvaluator

    captured = {}

    def fake_runner(**kwargs):
        captured.update(kwargs)
        yield {"type": "assistant", "item": {"type": "agent_message", "text": "ok"}}

    config = AgenticEvaluatorConfig()
    evaluator = AgenticEvaluator(config, codex_runner=fake_runner)

    results_path = tmp_path / "results"
    results_path.mkdir()
    metrics_path = results_path / "metrics.json"
    metrics_path.write_text(
        json.dumps({"combined_score": 0.5, "correct": True}),
        encoding="utf-8",
    )

    eval_sessions_root = tmp_path / "eval_sessions"
    eval_sessions_root.mkdir()

    program_path = tmp_path / "main.py"
    program_path.write_text("print('hi')", encoding="utf-8")

    evaluator.evaluate(
        repo_root=tmp_path,
        eval_command=[],
        program_path=program_path,
        results_path=results_path,
        metrics_path=metrics_path,
        eval_sessions_root=eval_sessions_root,
        task_name="test_task",
        results_dir="/tmp/run",
        parent_id="parent123",
        generation=7,
        patch_type="eval",
    )

    assert captured.get("parent_id") == "parent123"
    assert captured.get("generation") == 7
    assert captured.get("patch_type") == "eval"
    assert captured.get("session_kind") == "eval"

