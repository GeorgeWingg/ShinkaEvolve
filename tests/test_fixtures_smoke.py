"""Smoke tests for shared pytest fixtures.

These tests validate that the fixtures defined in conftest.py work correctly
and can be used as building blocks for other test files.
"""

import pytest
from pathlib import Path


class TestTempDbFixture:
    """Tests for the temp_db fixture."""

    def test_temp_db_creates_database(self, temp_db):
        """temp_db fixture should create a working database."""
        assert temp_db is not None
        assert temp_db.conn is not None
        assert temp_db.cursor is not None

    def test_temp_db_has_config(self, temp_db, temp_db_config):
        """temp_db should have the test config."""
        assert temp_db.config == temp_db_config
        assert temp_db.config.num_islands == 2

    def test_temp_db_allows_adding_programs(self, temp_db):
        """temp_db should allow adding programs."""
        from shinka.database.dbase import Program

        program = Program(
            id="test_001",
            code="def main(): pass",
            combined_score=0.5,
            correct=True,
            generation=0,
        )
        prog_id = temp_db.add(program)
        assert prog_id == "test_001"

        # Verify retrieval
        retrieved = temp_db.get("test_001")
        assert retrieved is not None
        assert retrieved.combined_score == 0.5
        assert retrieved.correct  # SQLite stores bool as int


class TestInMemoryDbFixture:
    """Tests for the in_memory_db fixture."""

    def test_in_memory_db_works(self, in_memory_db):
        """in_memory_db fixture should work without file backing."""
        assert in_memory_db is not None
        assert in_memory_db.config.db_path == ""


class TestProgramFactoryFixture:
    """Tests for the program_factory fixture."""

    def test_program_factory_creates_programs(self, program_factory, temp_db):
        """program_factory should create programs in the database."""
        prog_id = program_factory.create(score=0.75, correct=True)

        assert prog_id.startswith("test_prog_")
        retrieved = temp_db.get(prog_id)
        assert retrieved is not None
        assert retrieved.combined_score == 0.75
        assert retrieved.correct  # SQLite stores bool as int

    def test_program_factory_increments_counter(self, program_factory):
        """program_factory should create unique IDs."""
        id1 = program_factory.create()
        id2 = program_factory.create()
        id3 = program_factory.create()

        assert id1 != id2 != id3
        assert "0001" in id1
        assert "0002" in id2
        assert "0003" in id3

    def test_program_factory_respects_custom_params(self, program_factory, temp_db):
        """program_factory should allow customization."""
        prog_id = program_factory.create(
            score=0.9,
            correct=True,
            generation=5,
            island=1,
            code="print('custom')",
        )

        retrieved = temp_db.get(prog_id)
        assert retrieved.combined_score == 0.9
        assert retrieved.correct  # SQLite stores bool as int
        assert retrieved.generation == 5
        assert "print('custom')" in retrieved.code

    def test_program_factory_creates_lineage(self, program_factory, temp_db):
        """program_factory.create_lineage should create parent-child trees."""
        all_ids = program_factory.create_lineage(
            depth=3,
            branch_factor=2,
            base_score=0.3,
            score_increment=0.1,
        )

        # depth=3, branch_factor=2 means: 1 + 2 + 4 = 7 programs
        assert len(all_ids) == 7

        # Check generations
        gen0 = temp_db.get(all_ids[0])
        assert gen0.generation == 0
        assert gen0.parent_id is None

        gen1 = temp_db.get(all_ids[1])
        assert gen1.generation == 1
        assert gen1.parent_id == all_ids[0]

    def test_program_factory_creates_population(self, program_factory, temp_db):
        """program_factory.create_population should create many programs."""
        ids = program_factory.create_population(
            count=5,
            island=0,
            correct=True,
            score_range=(0.1, 0.9),
        )

        assert len(ids) == 5

        # Check scores are distributed
        scores = [temp_db.get(pid).combined_score for pid in ids]
        assert min(scores) >= 0.1
        assert max(scores) <= 0.9


class TestTempWorkspaceFixture:
    """Tests for workspace fixtures."""

    def test_temp_workspace_creates_directory(self, temp_workspace):
        """temp_workspace should create an empty directory."""
        assert temp_workspace.exists()
        assert temp_workspace.is_dir()

    def test_temp_git_workspace_has_git(self, temp_git_workspace):
        """temp_git_workspace should have git initialized."""
        import subprocess

        assert temp_git_workspace.exists()
        assert (temp_git_workspace / ".git").exists()

        # Should have at least one commit
        result = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=temp_git_workspace,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Initial commit" in result.stdout


class TestMockFixtures:
    """Tests for mock fixtures."""

    def test_mock_llm_response_factory(self, mock_llm_response):
        """mock_llm_response should create response dicts."""
        response = mock_llm_response(content="Hello", model="test-model")

        assert response["content"] == "Hello"
        assert response["model"] == "test-model"
        assert "usage" in response

    def test_mock_config_has_attributes(self, mock_config):
        """mock_config should have common config attributes."""
        assert mock_config.exploitation_ratio == 0.2
        assert mock_config.exploitation_alpha == 1.0
        assert mock_config.num_islands == 2
        assert mock_config.parent_selection_strategy == "power_law"

    def test_mock_cursor_works(self, mock_cursor):
        """mock_cursor should be usable for testing."""
        mock_cursor.fetchall.return_value = [("id1", 0.5), ("id2", 0.7)]

        result = mock_cursor.fetchall()
        assert len(result) == 2
        assert result[0] == ("id1", 0.5)
