from pathlib import Path

from shinka.core.embedding_corpus import build_embedding_corpus
from shinka.core.novelty_judge import NoveltyJudge
from shinka.database.dbase import Program


def test_build_embedding_corpus_handles_binary(tmp_path):
    root = tmp_path
    (root / "text.txt").write_text("hello", encoding="utf-8")
    (root / "bin.bin").write_bytes(b"\x00\x01abc")

    corpus = build_embedding_corpus(
        root,
        include_globs=["**/*"],
        exclude_globs=[],
        max_files=10,
        max_total_bytes=10_000,
        max_bytes_per_file=1024,
    )

    assert "text.txt" in corpus.included_files
    assert "bin.bin" in corpus.included_files
    assert corpus.binary_files == ["bin.bin"]
    assert "[BINARY FILE] bin.bin" in corpus.text


def test_build_embedding_corpus_prioritizes_changed_files(tmp_path):
    root = tmp_path
    (root / "main.txt").write_text("main", encoding="utf-8")
    (root / "helper.txt").write_text("helper", encoding="utf-8")

    corpus = build_embedding_corpus(
        root,
        include_globs=["**/*"],
        exclude_globs=[],
        max_files=1,
        max_total_bytes=10_000,
        max_bytes_per_file=1024,
        changed_first=[Path("helper.txt")],
    )

    assert corpus.included_files == ["helper.txt"]
    assert "helper" in corpus.text
    assert "main" not in corpus.text


def test_corpus_changes_when_only_helper_changes(tmp_path):
    base = tmp_path / "base"
    changed = tmp_path / "changed"
    base.mkdir()
    changed.mkdir()

    for path in [base, changed]:
        (path / "main.txt").write_text("SAME MAIN", encoding="utf-8")
    (base / "helper.txt").write_text("old helper", encoding="utf-8")
    (changed / "helper.txt").write_text("new helper", encoding="utf-8")

    corpus_a = build_embedding_corpus(
        base,
        include_globs=["**/*"],
        exclude_globs=[],
        max_files=10,
        max_total_bytes=10_000,
        max_bytes_per_file=1024,
    )
    corpus_b = build_embedding_corpus(
        changed,
        include_globs=["**/*"],
        exclude_globs=[],
        max_files=10,
        max_total_bytes=10_000,
        max_bytes_per_file=1024,
    )

    assert corpus_a.text != corpus_b.text


def test_novelty_llm_receives_corpus_text(monkeypatch):
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
                cost = 0.1

            return Resp()

    class FakeIslandManager:
        @staticmethod
        def are_all_islands_initialized():
            return True

    class FakeDB:
        def __init__(self):
            self.island_manager = FakeIslandManager()

        @staticmethod
        def compute_similarity(_, __):
            return [1.5]  # force LLM path

        @staticmethod
        def get_most_similar_program(_, __):
            return Program(id="p", code="OLD", language="python")

    judge = NoveltyJudge(novelty_llm_client=DummyLLM(), language="python", similarity_threshold=0.5)
    parent_program = Program(id="parent", code="PARENT", language="python", island_idx=0)

    should_accept, _ = judge.assess_novelty_with_rejection_sampling(
        "NEW_CORPUS_TEXT", [0.1], parent_program, FakeDB()
    )

    assert should_accept is True
    assert "NEW_CORPUS_TEXT" in judge.llm.last_user_msg
