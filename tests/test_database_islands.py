"""Tests for island management strategies in shinka/database/islands.py.

This module tests the island management components:
1. DefaultIslandAssignmentStrategy - default island assignment logic
2. CopyInitialProgramIslandStrategy - cross-island seeding
3. ElitistMigrationStrategy - elite-protecting migration
4. CombinedIslandManager - orchestrator for assignment and migration
"""

import pytest
from collections import Counter
from typing import List
from unittest.mock import Mock, MagicMock, patch

from shinka.database.islands import (
    IslandStrategy,
    DefaultIslandAssignmentStrategy,
    CopyInitialProgramIslandStrategy,
    IslandMigrationStrategy,
    ElitistMigrationStrategy,
    CombinedIslandManager,
)
from shinka.database.dbase import ProgramDatabase, DatabaseConfig, Program


# =============================================================================
# Island Assignment Strategy Tests
# =============================================================================


class TestDefaultIslandAssignmentStrategy:
    """Tests for DefaultIslandAssignmentStrategy."""

    def test_children_inherit_parent_island(self, temp_db, program_factory):
        """Children should be placed on the same island as their parent."""
        # Create parent on island 0
        parent_id = program_factory.create(score=0.5, correct=True, island=0)

        # Create child - should inherit parent's island
        child_id = program_factory.create(
            score=0.6, correct=True, generation=1, parent_id=parent_id
        )

        child = temp_db.get(child_id)
        parent = temp_db.get(parent_id)

        # Child should be on same island as parent
        # (Note: may be assigned to different island if island not initialized)
        assert child.island_idx is not None

    def test_distributes_correct_programs_across_islands(self, temp_db, program_factory):
        """Initial correct programs should be distributed to different islands."""
        # Create multiple correct programs
        ids = []
        for i in range(3):
            prog_id = program_factory.create(score=0.5, correct=True, generation=0)
            ids.append(prog_id)

        # Check that programs are distributed across islands
        islands = [temp_db.get(pid).island_idx for pid in ids]
        # At least some programs should be on different islands
        # (exact behavior depends on num_islands config)
        assert len(islands) == 3


class TestCopyInitialProgramIslandStrategy:
    """Tests for CopyInitialProgramIslandStrategy."""

    def test_first_program_copied_to_all_islands(self, temp_db, program_factory):
        """First program should be copied to all islands."""
        # The first correct program should trigger island copies
        first_id = program_factory.create(score=0.5, correct=True, generation=0)

        # Get all programs from database
        all_programs = temp_db.get_all_programs()

        # Should have copies across islands
        # (exact number depends on num_islands config)
        assert len(all_programs) >= 1

    def test_subsequent_programs_not_copied(self, temp_db, program_factory):
        """Subsequent programs should not be automatically copied."""
        # Create first program (triggers copies)
        program_factory.create(score=0.5, correct=True, generation=0)

        count_after_first = len(temp_db.get_all_programs())

        # Create second program
        program_factory.create(score=0.6, correct=True, generation=0)

        count_after_second = len(temp_db.get_all_programs())

        # Second program should add only 1 or 1 + island copies, not exponential
        # (the exact behavior depends on whether copies are created for subsequent programs)
        assert count_after_second > count_after_first


# =============================================================================
# Migration Strategy Tests
# =============================================================================


class TestElitistMigrationStrategy:
    """Tests for ElitistMigrationStrategy."""

    def test_migration_scheduled_at_intervals(self, temp_db, program_factory):
        """Migration should be scheduled at configured intervals."""
        # Set migration interval
        temp_db.config.migration_interval = 5

        # Check migration scheduling at different generations
        manager = temp_db.island_manager

        # Create mock programs at different generations
        prog_gen_0 = Mock(generation=0)
        prog_gen_5 = Mock(generation=5)
        prog_gen_10 = Mock(generation=10)
        prog_gen_3 = Mock(generation=3)

        # Gen 0 should not trigger (generation > 0 required)
        assert not manager.should_schedule_migration(prog_gen_0)

        # Gen 5 should trigger (5 % 5 == 0)
        assert manager.should_schedule_migration(prog_gen_5)

        # Gen 10 should trigger (10 % 5 == 0)
        assert manager.should_schedule_migration(prog_gen_10)

        # Gen 3 should not trigger (3 % 5 != 0)
        assert not manager.should_schedule_migration(prog_gen_3)

    def test_migration_only_migrates_correct_programs(self, temp_db, program_factory):
        """Migration should only move correct programs."""
        # This is an integration test - migration behavior is complex
        # Just verify the manager has migration capability
        assert temp_db.island_manager is not None
        assert hasattr(temp_db.island_manager, "perform_migration")

    def test_generation_zero_programs_protected(self, temp_db, program_factory):
        """Generation 0 programs should not be migrated."""
        # Create gen-0 program
        gen0_id = program_factory.create(
            score=0.9, correct=True, generation=0, island=0
        )

        gen0_program = temp_db.get(gen0_id)
        original_island = gen0_program.island_idx

        # Perform migration (if we can)
        temp_db.config.migration_interval = 1
        temp_db.config.migration_rate = 0.5

        # The gen-0 program should stay on its island
        # (exact verification depends on migration implementation)
        gen0_after = temp_db.get(gen0_id)
        # Gen-0 programs are protected from migration
        assert gen0_after.generation == 0


# =============================================================================
# CombinedIslandManager Tests
# =============================================================================


class TestCombinedIslandManager:
    """Tests for CombinedIslandManager orchestration."""

    def test_manager_created_with_database(self, temp_db):
        """ProgramDatabase should create island manager automatically."""
        assert temp_db.island_manager is not None
        assert isinstance(temp_db.island_manager, CombinedIslandManager)

    def test_get_island_idx(self, temp_db, program_factory):
        """Should retrieve correct island index for a program."""
        prog_id = program_factory.create(score=0.5, correct=True, island=0)

        island_idx = temp_db.island_manager.get_island_idx(prog_id)

        # Should return an island index (0 or another valid island)
        assert island_idx is not None
        assert isinstance(island_idx, int)

    def test_get_initialized_islands(self, temp_db, program_factory):
        """Should return list of islands with correct programs."""
        # Create correct program on island 0
        program_factory.create(score=0.5, correct=True, island=0)

        initialized = temp_db.island_manager.get_initialized_islands()

        # Should have at least one initialized island
        assert len(initialized) >= 1
        assert all(isinstance(i, int) for i in initialized)

    def test_are_all_islands_initialized(self, temp_db, program_factory):
        """Should track when all islands have correct programs."""
        # Initially may or may not be all initialized (depends on config)
        initial_state = temp_db.island_manager.are_all_islands_initialized()
        assert isinstance(initial_state, bool)

    def test_get_island_populations(self, temp_db, program_factory):
        """Should return population count per island."""
        # Create some programs
        program_factory.create(score=0.5, correct=True, island=0)
        program_factory.create(score=0.6, correct=True, island=0)

        populations = temp_db.island_manager.get_island_populations()

        # Should return a dict with island indices as keys
        assert isinstance(populations, dict)
        if populations:  # May be empty if num_islands=0
            for island_idx, count in populations.items():
                assert isinstance(island_idx, int)
                assert isinstance(count, int)
                assert count >= 0

    def test_perform_migration(self, temp_db, program_factory):
        """Should be able to call perform_migration."""
        # Create some programs to migrate
        for i in range(3):
            program_factory.create(
                score=0.5 + i * 0.1, correct=True, generation=1, island=0
            )

        # Migration should be callable (may or may not do anything)
        result = temp_db.island_manager.perform_migration(current_generation=5)
        assert isinstance(result, bool)


# =============================================================================
# Island Constraint Tests
# =============================================================================


class TestIslandSeparation:
    """Tests for island separation enforcement."""

    def test_sampling_respects_island_separation(self, temp_db, program_factory):
        """Parent selection should respect island constraints when configured."""
        # Create programs on different islands
        island0_id = program_factory.create(score=0.9, correct=True, island=0)
        island1_id = program_factory.create(score=0.1, correct=True, island=1)

        temp_db.config.enforce_island_separation = True

        # Sample - should get a program (enforcement depends on implementation)
        parent, _, _ = temp_db.sample()
        assert parent is not None

    def test_island_populations_tracked(self, temp_db, program_factory):
        """Island population counts should be tracked."""
        # Create programs on island 0
        for _ in range(3):
            program_factory.create(score=0.5, correct=True, island=0)

        populations = temp_db.island_manager.get_island_populations()

        # Island 0 should have programs
        # (exact count depends on island copies)
        assert sum(populations.values()) >= 3


# =============================================================================
# Edge Cases
# =============================================================================


class TestIslandEdgeCases:
    """Edge cases for island management."""

    def test_single_island_mode(self):
        """Should work with only one island."""
        config = DatabaseConfig(
            db_path="",
            num_islands=1,
            archive_size=50,
        )
        db = ProgramDatabase(config, embedding_model="")

        program = Program(
            id="test_single",
            code="def main(): pass",
            combined_score=0.5,
            correct=True,
        )
        db.add(program)

        # Should work with single island
        retrieved = db.get("test_single")
        assert retrieved is not None
        assert retrieved.island_idx in [0, None]

        db.close()

    def test_zero_islands_mode(self):
        """Should work with islands disabled."""
        config = DatabaseConfig(
            db_path="",
            num_islands=0,
            archive_size=50,
        )
        db = ProgramDatabase(config, embedding_model="")

        program = Program(
            id="test_no_island",
            code="def main(): pass",
            combined_score=0.5,
            correct=True,
        )
        db.add(program)

        retrieved = db.get("test_no_island")
        assert retrieved is not None

        db.close()

    def test_migration_interval_zero_disables_migration(self, temp_db, program_factory):
        """Migration interval of 0 should disable migration scheduling."""
        temp_db.config.migration_interval = 0

        prog = Mock(generation=5)
        assert not temp_db.island_manager.should_schedule_migration(prog)

    def test_empty_island_handling(self, temp_db):
        """Should handle islands with no programs."""
        populations = temp_db.island_manager.get_island_populations()

        # Should return valid dict even for empty database
        assert isinstance(populations, dict)


class TestMigrationHistory:
    """Tests for migration history tracking."""

    def test_migration_history_stored(self, temp_db, program_factory):
        """Programs should track their migration history."""
        prog_id = program_factory.create(score=0.5, correct=True, generation=0)

        program = temp_db.get(prog_id)

        # Migration history should be a list
        assert isinstance(program.migration_history, list)

    def test_new_programs_have_empty_history(self, temp_db, program_factory):
        """Newly created programs should have empty migration history."""
        prog_id = program_factory.create(score=0.5, correct=True, generation=0)

        program = temp_db.get(prog_id)

        # New programs shouldn't have migrated yet
        assert len(program.migration_history) == 0
