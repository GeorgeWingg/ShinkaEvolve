"""Shared pytest fixtures for ShinkaEvolve tests.

This module provides reusable fixtures for database setup, program creation,
and mock utilities used across the test suite.
"""

import pytest
import tempfile
import uuid
from pathlib import Path
from typing import Generator, Dict, Any, Optional, List
from unittest.mock import Mock, MagicMock
from dataclasses import dataclass

from shinka.database.dbase import ProgramDatabase, DatabaseConfig, Program


@pytest.fixture
def temp_db_config(tmp_path: Path) -> DatabaseConfig:
    """Create a DatabaseConfig for testing with minimal settings.

    Args:
        tmp_path: Pytest's temporary directory fixture

    Returns:
        DatabaseConfig with test-appropriate defaults
    """
    return DatabaseConfig(
        db_path=str(tmp_path / "test_evolution.sqlite"),
        num_islands=2,
        archive_size=50,
        migration_interval=5,
        migration_rate=0.2,
        island_elitism=True,
        enforce_island_separation=False,
        parent_selection_strategy="power_law",
        exploitation_alpha=1.0,
        exploitation_ratio=0.2,
        parent_selection_lambda=10.0,
        num_beams=3,
    )


@pytest.fixture
def temp_db(temp_db_config: DatabaseConfig) -> Generator[ProgramDatabase, None, None]:
    """Create a temporary ProgramDatabase with schema initialized.

    Yields:
        ProgramDatabase instance backed by a temporary SQLite file.
        Automatically cleaned up after test.
    """
    # Use empty embedding model to avoid API calls during tests
    db = ProgramDatabase(temp_db_config, embedding_model="")
    yield db
    db.close()


@pytest.fixture
def in_memory_db() -> Generator[ProgramDatabase, None, None]:
    """Create an in-memory ProgramDatabase for fast tests.

    Yields:
        ProgramDatabase instance using in-memory SQLite.
    """
    config = DatabaseConfig(
        db_path="",  # Empty path = in-memory
        num_islands=2,
        archive_size=50,
    )
    db = ProgramDatabase(config, embedding_model="")
    yield db
    db.close()


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary workspace directory.

    Yields:
        Path to temporary workspace directory.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    yield workspace


@pytest.fixture
def temp_git_workspace(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary workspace with git initialized.

    Yields:
        Path to git-initialized workspace.
    """
    import subprocess

    workspace = tmp_path / "git_workspace"
    workspace.mkdir()

    subprocess.run(
        ["git", "init"],
        cwd=workspace,
        capture_output=True,
        check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=workspace,
        capture_output=True,
        check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=workspace,
        capture_output=True,
        check=True
    )

    # Create initial commit
    (workspace / "README.md").write_text("# Test Repo")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=workspace,
        capture_output=True,
        check=True
    )

    yield workspace


class ProgramFactory:
    """Factory for creating test Program objects with realistic defaults.

    This factory simplifies test setup by providing a convenient way to
    create programs with sensible defaults while allowing customization
    of specific fields.
    """

    def __init__(self, db: ProgramDatabase):
        """Initialize factory with a database connection.

        Args:
            db: ProgramDatabase to add created programs to
        """
        self.db = db
        self._counter = 0

    def create(
        self,
        score: float = 0.5,
        correct: bool = False,
        generation: int = 0,
        island: int = 0,
        parent_id: Optional[str] = None,
        code: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        program_id: Optional[str] = None,
    ) -> str:
        """Create a program and add it to the database.

        Args:
            score: Combined score (0.0 to 1.0)
            correct: Whether program passes all tests
            generation: Generation number
            island: Island index
            parent_id: ID of parent program (or None for root)
            code: Program source code
            metadata: Additional metadata dict
            program_id: Optional specific program ID

        Returns:
            The program ID
        """
        self._counter += 1
        if program_id is None:
            program_id = f"test_prog_{self._counter:04d}"

        if code is None:
            code = f"# Program {program_id}\ndef main(): pass"

        full_metadata = {
            "island": island,
            "generation": generation,
            **(metadata or {})
        }

        program = Program(
            id=program_id,
            code=code,
            combined_score=score,
            correct=correct,
            parent_id=parent_id,
            generation=generation,
            island_idx=island,
            metadata=full_metadata,
        )

        self.db.add(program)
        return program_id

    def create_lineage(
        self,
        depth: int = 3,
        branch_factor: int = 2,
        base_score: float = 0.3,
        score_increment: float = 0.1,
        island: int = 0,
        correct: bool = True,
    ) -> List[str]:
        """Create a tree of programs with parent-child relationships.

        Args:
            depth: Number of generations
            branch_factor: Children per parent
            base_score: Starting score
            score_increment: Score increase per generation
            island: Island to place programs on
            correct: Whether programs should be marked correct

        Returns:
            List of all created program IDs
        """
        all_ids = []
        current_gen = [
            self.create(
                score=base_score,
                generation=0,
                island=island,
                correct=correct,
            )
        ]
        all_ids.extend(current_gen)

        for gen in range(1, depth):
            next_gen = []
            for parent_id in current_gen:
                for _ in range(branch_factor):
                    child_id = self.create(
                        score=base_score + score_increment * gen,
                        generation=gen,
                        parent_id=parent_id,
                        island=island,
                        correct=correct,
                    )
                    next_gen.append(child_id)
            all_ids.extend(next_gen)
            current_gen = next_gen

        return all_ids

    def create_population(
        self,
        count: int,
        island: int = 0,
        correct: bool = True,
        score_range: tuple = (0.1, 0.9),
    ) -> List[str]:
        """Create a population of programs with varying scores.

        Args:
            count: Number of programs to create
            island: Island to place programs on
            correct: Whether programs should be marked correct
            score_range: (min, max) score range

        Returns:
            List of created program IDs
        """
        import random
        ids = []
        min_score, max_score = score_range
        for i in range(count):
            # Distribute scores evenly across range
            score = min_score + (max_score - min_score) * (i / max(count - 1, 1))
            prog_id = self.create(
                score=score,
                correct=correct,
                generation=0,
                island=island,
            )
            ids.append(prog_id)
        return ids


@pytest.fixture
def program_factory(temp_db: ProgramDatabase) -> ProgramFactory:
    """Create a ProgramFactory bound to a temporary database.

    Args:
        temp_db: Temporary database fixture

    Returns:
        ProgramFactory instance
    """
    return ProgramFactory(temp_db)


@pytest.fixture
def mock_llm_response():
    """Factory for creating mock LLM responses.

    Returns:
        Function that creates mock response dicts
    """
    def _create_response(
        content: str = "Mock response",
        model: str = "mock-model",
        usage: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        return {
            "content": content,
            "model": model,
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 50},
        }

    return _create_response


@pytest.fixture
def mock_config():
    """Create a mock config object for testing strategies.

    Returns:
        Mock config with common attributes set
    """
    config = Mock()
    config.exploitation_ratio = 0.2
    config.exploitation_alpha = 1.0
    config.num_islands = 2
    config.parent_selection_lambda = 10.0
    config.num_beams = 5
    config.parent_selection_strategy = "power_law"
    config.archive_size = 100
    config.migration_interval = 10
    config.migration_rate = 0.1
    config.island_elitism = True
    config.enforce_island_separation = False
    config.elite_selection_ratio = 0.3
    return config


@pytest.fixture
def mock_cursor():
    """Create a mock SQLite cursor for testing strategies.

    Returns:
        Mock cursor with execute/fetchall/fetchone methods
    """
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    return cursor


@pytest.fixture
def mock_connection():
    """Create a mock SQLite connection.

    Returns:
        Mock connection object
    """
    conn = MagicMock()
    return conn


# Dataclass for EvaluatorScore to use in aggregation tests
@dataclass
class MockEvaluatorScore:
    """Mock EvaluatorScore for testing aggregation without importing eval module."""
    evaluator_id: str
    evaluator_name: str
    score: float
    correct: bool
    metrics: Dict[str, Any]
    weight: float = 1.0
    backend: Optional[str] = None
    model: Optional[str] = None
    error: Optional[str] = None
    elapsed_seconds: float = 0.0
    session_dir: Optional[str] = None
    session_log_path: Optional[str] = None
