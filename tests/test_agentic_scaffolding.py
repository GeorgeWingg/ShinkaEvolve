from pathlib import Path
from typing import Optional

import pytest
import yaml

import shinka.core.runner as runner_module
from shinka.core.runner import AgenticConfig, EvolutionConfig, EvolutionRunner
from shinka.database.dbase import Program, DatabaseConfig
from shinka.edit.agentic import (
    AgentContext,
    AgenticEditor,
    AgentResult,
    CommandResult,
)
from shinka.edit.codex_cli import CodexUnavailableError, ensure_codex_available
from shinka.launch.scheduler import LocalJobConfig


def test_agentic_config_defaults():
    cfg = EvolutionConfig()

    assert cfg.agentic_mode is False
    assert isinstance(cfg.agentic, AgenticConfig)
    assert cfg.agentic.sandbox == "workspace-write"
    assert cfg.agentic.approval_mode == "full-auto"
    assert cfg.agentic.max_events == 50  # Canonical field (max_turns is deprecated)
    assert cfg.agentic.max_seconds == 0
    assert cfg.agentic.resume_parent_session is False


def test_agentic_hydra_config_enables_mode():
    config_path = "configs/evolution/agentic.yaml"
    with open(config_path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    assert data["evo_config"]["agentic_mode"] is True
    assert data["evo_config"]["agentic"]["sandbox"] == "workspace-write"
    assert data["evo_config"]["results_dir"] == "${output_dir}"
    assert data["evo_config"]["_target_"] == "shinka.core.EvolutionConfig"


def test_agentic_editor_collects_changes(tmp_path):
    config = AgenticConfig(max_turns=5, max_seconds=10)

    def fake_runner(**kwargs):
        workdir = kwargs["workdir"]
        assert kwargs.get("resume_session_id") is None
        file_path = Path(workdir) / "main.py"
        file_path.write_text(
            "# EVOLVE-BLOCK-START\nprint('hello')\n# EVOLVE-BLOCK-END\n",
            encoding="utf-8",
        )
        yield {"type": "thread.started", "thread_id": "session-test"}
        yield {"item": {"type": "agent_message", "text": "done"}}

    editor = AgenticEditor(tmp_path / "scratch", config, runner=fake_runner)

    context = AgentContext(
        user_prompt="Do something",
        language="python",
        base_files={
            Path("main.py"): "# EVOLVE-BLOCK-START\npass\n# EVOLVE-BLOCK-END\n"
        },
        primary_file=Path("main.py"),
    )

    result = editor.run_session(context)

    assert Path("main.py") in result.changed_files
    assert "print('hello')" in result.changed_files[Path("main.py")]
    assert result.metrics["messages_logged"] == 1.0
    assert result.metrics["events_logged"] == 2.0
    assert result.session_log_path is not None
    assert result.session_log_path.exists()
    assert len(result.session_events) == 2
    assert result.session_events[0]["type"] == "thread.started"
    assert result.binary_changed_files == {}
    assert result.session_id == "session-test"


def test_agentic_editor_usage_metrics(tmp_path):
    config = AgenticConfig(max_turns=3, max_seconds=5)

    def fake_runner(**kwargs):
        yield {"type": "usage", "usage": {"input_tokens": 120, "output_tokens": 80, "total_tokens": 200}}
        yield {"item": {"type": "agent_message", "text": "done"}}

    editor = AgenticEditor(tmp_path / "scratch", config, codex_runner=fake_runner)

    context = AgentContext(
        user_prompt="Track usage",
        language="python",
        base_files={Path("main.py"): "# EVOLVE-BLOCK-START\npass\n# EVOLVE-BLOCK-END\n"},
        primary_file=Path("main.py"),
    )

    result = editor.run_session(context)

    assert result.metrics["estimated_input_tokens"] == 120
    assert result.metrics["estimated_output_tokens"] == 80
    assert result.metrics["estimated_total_tokens"] == 200
    assert result.metrics["estimated_total_cost"] > 0


def test_agentic_editor_failure_produces_retry(monkeypatch, tmp_path):
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("", encoding="utf-8")

    monkeypatch.setattr(runner_module, "ensure_codex_available", lambda _: fake_codex)

    class EmptyEditor:
        def __init__(self, scratch_dir, config, *, runner=None, codex_runner=None):
            self.scratch_dir = scratch_dir

        def run_session(self, context):
            return AgentResult(
                changed_files={},
                session_log=["no change"],
                commands_run=[],
                final_message="no change",
                metrics={"elapsed_seconds": 0.1},
            )

    monkeypatch.setattr(runner_module, "AgenticEditor", EmptyEditor)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    evo_config = EvolutionConfig(
        language="python",
        results_dir=str(tmp_path / "results"),
        agentic_mode=True,
        llm_models=["gpt-4.1"],
    )
    job_config = LocalJobConfig(eval_program_path=str(tmp_path / "evaluate.py"))
    db_config = DatabaseConfig()

    runner = EvolutionRunner(
        evo_config=evo_config,
        job_config=job_config,
        db_config=db_config,
        verbose=False,
    )

    parent_program = Program(
        id="parent",
        code="# EVOLVE-BLOCK-START\nreturn 1\n# EVOLVE-BLOCK-END\n",
        language="python",
    )

    code_diff, meta, num_applied = runner.run_patch(
        parent_program=parent_program,
        archive_programs=[],
        top_k_programs=[],
        generation=1,
    )

    assert code_diff is None
    assert num_applied == 0
    assert meta["error_attempt"] is not None


def test_ensure_codex_available_failure(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(CodexUnavailableError):
        ensure_codex_available()


def test_run_patch_agentic_integration(monkeypatch, tmp_path):
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("", encoding="utf-8")

    monkeypatch.setattr(runner_module, "ensure_codex_available", lambda _: fake_codex)

    def dummy_editor_factory(scratch_dir, config, runner=None, codex_runner=None):  # pragma: no cover - simple stub
        class DummyEditor:
            def __init__(self, scratch_dir):
                self.scratch_dir = scratch_dir

            def run_session(self, context):
                # Agentic mode no longer requires a primary file; return a concrete change.
                rel_path = Path("main.py")
                new_content = "# EVOLVE-BLOCK-START\nreturn 2\n# EVOLVE-BLOCK-END\n"
                return AgentResult(
                    changed_files={rel_path: new_content},
                    session_log=["done"],
                    commands_run=[
                        CommandResult(
                            command="pytest",
                            status="success",
                            exit_code=0,
                        )
                    ],
                    final_message="done",
                    metrics={"elapsed_seconds": 0.1},
                )

        return DummyEditor(scratch_dir)

    monkeypatch.setattr(runner_module, "AgenticEditor", dummy_editor_factory)

    eval_script = tmp_path / "evaluate.py"
    eval_script.write_text(
        "def main():\n    return True\n",
        encoding="utf-8",
    )

    evo_config = EvolutionConfig(
        language="python",
        results_dir=str(tmp_path / "results"),
        agentic_mode=True,
        llm_models=["gpt-4.1"],
    )
    job_config = LocalJobConfig(eval_program_path=str(eval_script))
    db_config = DatabaseConfig()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    runner = EvolutionRunner(
        evo_config=evo_config,
        job_config=job_config,
        db_config=db_config,
        verbose=False,
    )

    parent_program = Program(
        id="parent",
        code="# EVOLVE-BLOCK-START\nreturn 1\n# EVOLVE-BLOCK-END\n",
        language="python",
    )

    code_diff, meta, num_applied = runner.run_patch(
        parent_program=parent_program,
        archive_programs=[],
        top_k_programs=[],
        generation=1,
    )

    assert num_applied == 1
    assert code_diff is None
    assert meta["patch_type"] == "agentic"
    assert meta["agent_metrics"]["elapsed_seconds"] == 0.1

    mutated_file = Path(runner.results_dir) / "gen_1" / "main.py"
    assert mutated_file.exists()
    assert "return 2" in mutated_file.read_text(encoding="utf-8")


def test_agentic_runner_copies_parent_workspace(monkeypatch, tmp_path):
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("", encoding="utf-8")

    monkeypatch.setattr(runner_module, "ensure_codex_available", lambda _: fake_codex)

    results_root = tmp_path / "results"
    parent_dir = results_root / "gen_0"
    helper_dir = parent_dir / "helpers"
    helper_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = parent_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "main.py").write_text(
        "# EVOLVE-BLOCK-START\nreturn 1\n# EVOLVE-BLOCK-END\n",
        encoding="utf-8",
    )
    helper_path = helper_dir / "palette.py"
    helper_path.write_text("ORIG = 'red'\n", encoding="utf-8")
    untouched_asset = assets_dir / "template.txt"
    untouched_asset.write_text("template", encoding="utf-8")
    (parent_dir / "results").mkdir(parents=True, exist_ok=True)

    captured_contexts: list[AgentContext] = []

    def dummy_editor_factory(scratch_dir, config, runner=None, codex_runner=None):
        class DummyEditor:
            def __init__(self, scratch_dir):
                self.scratch_dir = scratch_dir

            def run_session(self, context):
                captured_contexts.append(context)
                helper_rel = Path("helpers/palette.py")
                new_helper = context.base_files[helper_rel].replace("ORIG", "NEW")
                return AgentResult(
                    changed_files={helper_rel: new_helper},
                    session_log=["updated helper"],
                    commands_run=[],
                    final_message="done",
                    metrics={"elapsed_seconds": 0.2},
                )

        return DummyEditor(scratch_dir)

    monkeypatch.setattr(runner_module, "AgenticEditor", dummy_editor_factory)

    eval_script = tmp_path / "evaluate.py"
    eval_script.write_text("def main():\n    return True\n", encoding="utf-8")

    evo_config = EvolutionConfig(
        language="python",
        results_dir=str(results_root),
        agentic_mode=True,
        llm_models=["gpt-4.1"],
    )
    job_config = LocalJobConfig(eval_program_path=str(eval_script))
    db_config = DatabaseConfig()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    runner = EvolutionRunner(
        evo_config=evo_config,
        job_config=job_config,
        db_config=db_config,
        verbose=False,
    )

    parent_program = Program(
        id="parent",
        code="# EVOLVE-BLOCK-START\nreturn 1\n# EVOLVE-BLOCK-END\n",
        language="python",
        generation=0,
    )

    _, meta, _ = runner.run_patch(
        parent_program=parent_program,
        archive_programs=[],
        top_k_programs=[],
        generation=1,
    )

    assert meta["agent_changed_files"] != {}
    assert captured_contexts, "Agentic editor should have been invoked"
    context = captured_contexts[0]
    assert Path("helpers/palette.py") in context.base_files
    assert context.base_files[Path("helpers/palette.py")] == "ORIG = 'red'\n"
    assert context.resume_session_id is None

    child_helper = results_root / "gen_1" / "helpers" / "palette.py"
    assert child_helper.exists()
    assert "NEW" in child_helper.read_text(encoding="utf-8")

    preserved_asset = results_root / "gen_1" / "assets" / "template.txt"
    assert preserved_asset.exists()
    assert preserved_asset.read_text(encoding="utf-8") == "template"

    # Ensure evaluation artifacts weren't copied forward
    assert not (results_root / "gen_1" / "results").exists()


def test_agentic_patch_does_not_force_main_file(monkeypatch, tmp_path):
    """Agentic patching should not auto-create main.py when untouched."""
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("", encoding="utf-8")
    monkeypatch.setattr(runner_module, "ensure_codex_available", lambda _: fake_codex)

    def dummy_editor_factory(scratch_dir, config, runner=None, codex_runner=None):
        class DummyEditor:
            def __init__(self, scratch_dir):
                self.scratch_dir = scratch_dir

            def run_session(self, context):
                rel_path = Path("helpers/only_helper.py")
                return AgentResult(
                    changed_files={rel_path: "VALUE = 1\n"},
                    session_log=["helper only"],
                    commands_run=[],
                    final_message="done",
                    metrics={"elapsed_seconds": 0.1},
                )

        return DummyEditor(scratch_dir)

    monkeypatch.setattr(runner_module, "AgenticEditor", dummy_editor_factory)

    eval_script = tmp_path / "evaluate.py"
    eval_script.write_text("def main():\n    return True\n", encoding="utf-8")

    results_root = tmp_path / "results"
    # Parent workspace exists but has no main.py
    (results_root / "gen_0").mkdir(parents=True, exist_ok=True)

    evo_config = EvolutionConfig(
        language="python",
        results_dir=str(results_root),
        agentic_mode=True,
        llm_models=["gpt-4.1"],
    )
    job_config = LocalJobConfig(eval_program_path=str(eval_script))
    db_config = DatabaseConfig()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    runner = EvolutionRunner(
        evo_config=evo_config,
        job_config=job_config,
        db_config=db_config,
        verbose=False,
    )

    parent_program = Program(
        id="parent",
        code="pass",
        language="python",
        generation=0,
    )

    _, meta, num_applied = runner.run_patch(
        parent_program=parent_program,
        archive_programs=[],
        top_k_programs=[],
        generation=1,
    )

    assert num_applied == 1
    helper_file = results_root / "gen_1" / "helpers" / "only_helper.py"
    assert helper_file.exists()
    assert not (results_root / "gen_1" / "main.py").exists()


def test_agentic_runner_resumes_parent_session(monkeypatch, tmp_path):
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("", encoding="utf-8")

    monkeypatch.setattr(runner_module, "ensure_codex_available", lambda _: fake_codex)

    captured_resume_ids: list[Optional[str]] = []

    def dummy_editor_factory(scratch_dir, config, runner=None, codex_runner=None):
        class DummyEditor:
            def __init__(self, scratch_dir):
                self.scratch_dir = scratch_dir

            def run_session(self, context):
                captured_resume_ids.append(context.resume_session_id)
                rel_path = Path("main.py")
                new_code = "# EVOLVE-BLOCK-START\nreturn 3\n# EVOLVE-BLOCK-END\n"
                return AgentResult(
                    changed_files={rel_path: new_code},
                    session_log=["resumed"],
                    commands_run=[],
                    final_message="done",
                    metrics={"elapsed_seconds": 0.3},
                    session_id="child-session",
                )

        return DummyEditor(scratch_dir)

    monkeypatch.setattr(runner_module, "AgenticEditor", dummy_editor_factory)

    eval_script = tmp_path / "evaluate.py"
    eval_script.write_text("def main():\n    return True\n", encoding="utf-8")

    evo_config = EvolutionConfig(
        language="python",
        results_dir=str(tmp_path / "results"),
        agentic_mode=True,
        llm_models=["gpt-4.1"],
        agentic=AgenticConfig(resume_parent_session=True),
    )
    job_config = LocalJobConfig(eval_program_path=str(eval_script))
    db_config = DatabaseConfig()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    runner = EvolutionRunner(
        evo_config=evo_config,
        job_config=job_config,
        db_config=db_config,
        verbose=False,
    )

    parent_program = Program(
        id="parent",
        code="# EVOLVE-BLOCK-START\nreturn 1\n# EVOLVE-BLOCK-END\n",
        language="python",
        metadata={"agent_session_id": "parent-session"},
    )

    _, meta, _ = runner.run_patch(
        parent_program=parent_program,
        archive_programs=[],
        top_k_programs=[],
        generation=1,
    )

    assert captured_resume_ids == ["parent-session"]
    assert meta["agent_session_id"] == "child-session"
    assert meta["agent_resumed_from_parent"] is True
    assert meta["agent_resume_source_session_id"] == "parent-session"


def test_agentic_editor_captures_model_from_init_event(tmp_path):
    """Test that AgentResult.model is captured from init events."""
    config = AgenticConfig(max_turns=5, max_seconds=10)

    def fake_runner_with_model(**kwargs):
        # Emit init event with model (like Claude CLI and ShinkaAgent do)
        yield {"type": "init", "session_id": "test-session", "model": "claude-sonnet-4-20250514"}
        yield {"item": {"type": "agent_message", "text": "done"}}

    editor = AgenticEditor(tmp_path / "scratch", config, runner=fake_runner_with_model)

    context = AgentContext(
        user_prompt="Test model capture",
        language="python",
        base_files={Path("main.py"): "# EVOLVE-BLOCK-START\npass\n# EVOLVE-BLOCK-END\n"},
        primary_file=Path("main.py"),
    )

    result = editor.run_session(context)

    assert result.model == "claude-sonnet-4-20250514"


def test_agentic_editor_model_none_when_not_in_init(tmp_path):
    """Test that AgentResult.model is None when init event has no model."""
    config = AgenticConfig(max_turns=5, max_seconds=10)

    def fake_runner_no_model(**kwargs):
        # Emit init event WITHOUT model (like Codex/Gemini do)
        yield {"type": "init", "session_id": "test-session"}
        yield {"item": {"type": "agent_message", "text": "done"}}

    editor = AgenticEditor(tmp_path / "scratch", config, runner=fake_runner_no_model)

    context = AgentContext(
        user_prompt="Test no model",
        language="python",
        base_files={Path("main.py"): "# EVOLVE-BLOCK-START\npass\n# EVOLVE-BLOCK-END\n"},
        primary_file=Path("main.py"),
    )

    result = editor.run_session(context)

    assert result.model is None


def test_model_name_metadata_uses_backend_fallback(monkeypatch, tmp_path):
    """Test that model_name uses backend-specific fallback instead of 'codex-cli'."""
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("", encoding="utf-8")

    # Mock all backend availability checks
    monkeypatch.setattr(runner_module, "ensure_codex_available", lambda _: fake_codex)
    monkeypatch.setattr(runner_module, "ensure_gemini_available", lambda _: fake_codex)
    monkeypatch.setattr(runner_module, "ensure_claude_available", lambda _: fake_codex)

    def dummy_editor_factory(scratch_dir, config, runner=None, codex_runner=None):
        class DummyEditor:
            def __init__(self, scratch_dir):
                self.scratch_dir = scratch_dir

            def run_session(self, context):
                rel_path = Path("main.py")
                new_content = "# EVOLVE-BLOCK-START\nreturn 2\n# EVOLVE-BLOCK-END\n"
                return AgentResult(
                    changed_files={rel_path: new_content},
                    session_log=["done"],
                    commands_run=[],
                    final_message="done",
                    metrics={"elapsed_seconds": 0.1},
                    model=None,  # No model from events
                )

        return DummyEditor(scratch_dir)

    monkeypatch.setattr(runner_module, "AgenticEditor", dummy_editor_factory)

    eval_script = tmp_path / "evaluate.py"
    eval_script.write_text("def main():\n    return True\n", encoding="utf-8")

    # Test with gemini backend - should NOT show "codex-cli"
    evo_config = EvolutionConfig(
        language="python",
        results_dir=str(tmp_path / "results"),
        agentic_mode=True,
        llm_models=["gpt-4.1"],
        agentic=AgenticConfig(backend="gemini"),  # Gemini backend, no cli_profile
    )
    job_config = LocalJobConfig(eval_program_path=str(eval_script))
    db_config = DatabaseConfig()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    runner = EvolutionRunner(
        evo_config=evo_config,
        job_config=job_config,
        db_config=db_config,
        verbose=False,
    )

    parent_program = Program(
        id="parent",
        code="# EVOLVE-BLOCK-START\nreturn 1\n# EVOLVE-BLOCK-END\n",
        language="python",
    )

    _, meta, _ = runner.run_patch(
        parent_program=parent_program,
        archive_programs=[],
        top_k_programs=[],
        generation=1,
    )

    # Key assertion: model_name should NOT be "codex-cli" for gemini backend
    assert meta["model_name"] == "gemini-default"
    assert meta["agent_backend"] == "gemini"
    assert meta["agent_backend_type"] == "cli"


def test_model_name_prefers_actual_model_from_events(monkeypatch, tmp_path):
    """Test that actual model from CLI events takes priority."""
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("", encoding="utf-8")

    monkeypatch.setattr(runner_module, "ensure_claude_available", lambda _: fake_codex)

    def dummy_editor_factory(scratch_dir, config, runner=None, codex_runner=None):
        class DummyEditor:
            def __init__(self, scratch_dir):
                self.scratch_dir = scratch_dir

            def run_session(self, context):
                rel_path = Path("main.py")
                new_content = "# EVOLVE-BLOCK-START\nreturn 2\n# EVOLVE-BLOCK-END\n"
                return AgentResult(
                    changed_files={rel_path: new_content},
                    session_log=["done"],
                    commands_run=[],
                    final_message="done",
                    metrics={"elapsed_seconds": 0.1},
                    model="claude-sonnet-4-20250514",  # Actual model from init event
                )

        return DummyEditor(scratch_dir)

    monkeypatch.setattr(runner_module, "AgenticEditor", dummy_editor_factory)

    eval_script = tmp_path / "evaluate.py"
    eval_script.write_text("def main():\n    return True\n", encoding="utf-8")

    evo_config = EvolutionConfig(
        language="python",
        results_dir=str(tmp_path / "results2"),
        agentic_mode=True,
        llm_models=["gpt-4.1"],
        agentic=AgenticConfig(backend="claude"),
    )
    job_config = LocalJobConfig(eval_program_path=str(eval_script))
    db_config = DatabaseConfig()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    runner = EvolutionRunner(
        evo_config=evo_config,
        job_config=job_config,
        db_config=db_config,
        verbose=False,
    )

    parent_program = Program(
        id="parent",
        code="# EVOLVE-BLOCK-START\nreturn 1\n# EVOLVE-BLOCK-END\n",
        language="python",
    )

    _, meta, _ = runner.run_patch(
        parent_program=parent_program,
        archive_programs=[],
        top_k_programs=[],
        generation=1,
    )

    # Key assertion: actual model from events takes priority
    assert meta["model_name"] == "claude-sonnet-4-20250514"
    assert meta["agent_backend"] == "claude"
    assert meta["agent_backend_type"] == "cli"
