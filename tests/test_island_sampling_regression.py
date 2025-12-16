import random

from shinka.database.dbase import DatabaseConfig, Program, ProgramDatabase


def test_sample_uses_initialized_islands_when_partial_init(tmp_path):
    """
    Regression test for the "flat evolution tree" bug.

    When at least one island has a correct program but not all islands are
    initialized yet, `ProgramDatabase.sample()` should still select parents
    via the normal parent-selection logic (i.e., from correct programs),
    rather than falling back to the very first program in the DB.
    """
    # In-memory DB (empty db_path => sqlite :memory:)
    cfg = DatabaseConfig(db_path="", num_islands=3)
    db = ProgramDatabase(cfg, embedding_model="")

    # First program is incorrect and should never be sampled as a parent once
    # any correct programs exist.
    p0 = Program(
        id="p0",
        code="print('bad')",
        language="python",
        correct=False,
        combined_score=0.0,
        complexity=1.0,
        timestamp=1.0,
    )
    db.add(p0)

    # Add two correct programs so that islands 0 and 1 are initialized, but
    # island 2 remains uninitialized.
    p1 = Program(
        id="p1",
        code="print('good1')",
        language="python",
        correct=True,
        combined_score=1.0,
        complexity=1.0,
        timestamp=2.0,
    )
    db.add(p1)

    p2 = Program(
        id="p2",
        code="print('good2')",
        language="python",
        correct=True,
        combined_score=0.9,
        complexity=1.0,
        timestamp=3.0,
    )
    db.add(p2)

    # Deterministic island choice for stability.
    random.seed(0)

    parent, _, _ = db.sample()

    assert bool(parent.correct) is True
    assert parent.id != "p0"


def test_bootstrap_samples_uninitialized_islands(tmp_path):
    """
    When no islands have correct programs yet (e.g., empty agentic seed or
    incorrect init), sampling should still pick an island and return a parent
    from that island without requiring correctness.
    """
    cfg = DatabaseConfig(db_path="", num_islands=3)
    db = ProgramDatabase(cfg, embedding_model="")

    p0 = Program(
        id="p0",
        code="print('bad')",
        language="python",
        correct=False,
        combined_score=0.0,
        complexity=1.0,
        timestamp=1.0,
    )
    db.add(p0)

    random.seed(0)  # randint(0,2) -> 1
    parent, _, _ = db.sample()

    assert bool(parent.correct) is False
    assert parent.island_idx == 1
