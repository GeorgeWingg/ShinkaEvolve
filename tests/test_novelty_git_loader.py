"""Regression tests for NoveltyJudge git-backed code loading.

When git-backed storage is enabled, Program.code may be intentionally empty and
the program text must be hydrated from evolution.git for novelty comparisons.
"""

from __future__ import annotations

from pathlib import Path

from shinka.core.novelty_judge import NoveltyJudge
from shinka.database.dbase import Program
from shinka.webui.git_worktree import EvolutionGitManager


def test_novelty_judge_uses_code_loader_for_empty_program_code(tmp_path):
    class DummyLLM:
        def __init__(self):
            self.last_user_msg = None

        @staticmethod
        def get_kwargs():
            return {}

        def query(self, msg, system_msg=None, llm_kwargs=None):
            self.last_user_msg = msg

            class Resp:
                content = "NOVEL: ok"
                cost = 0.0

            return Resp()

    repo_path = tmp_path / "evolution.git"
    manager = EvolutionGitManager(repo_path, create=True)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("git content\n", encoding="utf-8")
    sha = manager.init_from_workspace(workspace)

    most_similar = Program(
        id="p1",
        code="",
        language="python",
        metadata={"git_commit_sha": sha},
    )

    llm = DummyLLM()
    judge = NoveltyJudge(
        novelty_llm_client=llm,
        language="python",
        code_loader=lambda program: program.get_code_content(git_manager=manager),
    )

    judge.check_llm_novelty("PROPOSED", most_similar)

    assert llm.last_user_msg is not None
    assert "git content" in llm.last_user_msg

