"""Tests for auth recovery (cache bypass) and backend fallback selection."""

from __future__ import annotations

from pathlib import Path

from shinka.database.dbase import DatabaseConfig, Program
from shinka.core.runner import EvolutionConfig, EvolutionRunner, AgenticConfig
from shinka.edit.agentic import AgentResult
from shinka.edit.codex_cli import CodexUnavailableError
from shinka.launch.scheduler import LocalJobConfig


def test_auth_status_cache_respects_skip_cache(monkeypatch):
    from shinka.tools.auth_status import check_openrouter_auth, clear_auth_cache

    clear_auth_cache("openrouter")

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-1")
    status = check_openrouter_auth(skip_cache=True)
    assert status.available is True

    # Now remove the env var; without skip_cache we should still see the cached status.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cached = check_openrouter_auth()
    assert cached.available is True

    # With skip_cache we should see the fresh (missing) status.
    fresh = check_openrouter_auth(skip_cache=True)
    assert fresh.available is False


def test_runner_falls_back_to_other_backend_on_unavailable(monkeypatch, tmp_path):
    """If the initially selected backend is unavailable, fall back via bandit."""
    eval_script = tmp_path / "evaluate.py"
    eval_script.write_text("def main():\n    return True\n", encoding="utf-8")

    evo_config = EvolutionConfig(
        language="python",
        results_dir=str(tmp_path / "results"),
        agentic_mode=True,
        llm_models=["gpt-4.1"],
        agentic=AgenticConfig(backend="codex"),
    )
    job_config = LocalJobConfig(eval_program_path=str(eval_script))
    db_config = DatabaseConfig()

    runner = EvolutionRunner(
        evo_config=evo_config,
        job_config=job_config,
        db_config=db_config,
        verbose=False,
    )

    class DummyBandit:
        def sample(self):
            return "codex"

        def refresh_auth(self, *, skip_cache: bool = False):  # noqa: ARG002
            return ["codex", "gemini"]

        def sample_with_fallback(self, exclude=None):
            assert exclude and "codex" in exclude
            return "gemini"

        def get_summary(self):
            return {"available_backends": ["codex", "gemini"], "posteriors": {"codex": 0.5, "gemini": 0.5}}

    runner.backend_bandit = DummyBandit()

    # Force codex to be unavailable, gemini to be available.
    monkeypatch.setattr(
        "shinka.core.runner.ensure_codex_available",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CodexUnavailableError("codex unavailable")),
    )
    monkeypatch.setattr(
        "shinka.core.runner.ensure_gemini_available",
        lambda *_args, **_kwargs: Path("/tmp/gemini"),
    )

    class DummyEditor:
        def __init__(self, scratch_dir, config, *, registry_dir=None, prepare_workspace=True, runner=None, **_kwargs):  # noqa: ARG002
            self.scratch_dir = scratch_dir

        def run_session(self, context):  # noqa: ARG002
            return AgentResult(
                changed_files={Path("main.py"): "# EVOLVE-BLOCK-START\nreturn 2\n# EVOLVE-BLOCK-END\n"},
                session_log=["ok"],
                commands_run=[],
                final_message="done",
                metrics={"elapsed_seconds": 0.1},
            )

    monkeypatch.setattr("shinka.core.runner.AgenticEditor", DummyEditor)

    parent_program = Program(
        id="parent",
        code="# EVOLVE-BLOCK-START\nreturn 1\n# EVOLVE-BLOCK-END\n",
        language="python",
    )

    _, meta, num_applied = runner.run_patch(
        parent_program=parent_program,
        archive_programs=[],
        top_k_programs=[],
        generation=1,
    )

    assert num_applied == 1
    assert meta["agent_backend"] == "gemini"
