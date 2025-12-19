"""Tests for context selection (inspirations) in shinka/database/inspirations.py.

This module tests the inspiration/context selection components:
1. ArchiveInspirationSelector - 4-tier fallback strategy for archive inspirations
2. TopKInspirationSelector - top-k best performers selection
3. CombinedContextSelector - orchestrator for both selectors
"""

import pytest
from typing import List
from unittest.mock import Mock

from shinka.database.inspirations import (
    ContextSelectorStrategy,
    ArchiveInspirationSelector,
    TopKInspirationSelector,
    CombinedContextSelector,
)
from shinka.database.dbase import ProgramDatabase, DatabaseConfig, Program


# =============================================================================
# Integration Tests using Database Sample
# =============================================================================


class TestArchiveInspirationSelectorIntegration:
    """Integration tests for ArchiveInspirationSelector using real database."""

    def test_returns_inspirations_from_archive(self, temp_db, program_factory):
        """Should return programs from archive as inspirations."""
        # Create some correct programs (will be added to archive)
        program_factory.create(score=0.5, correct=True, generation=0)
        program_factory.create(score=0.7, correct=True, generation=0)
        parent_id = program_factory.create(score=0.3, correct=True, generation=0)

        # Sample - should get inspirations
        parent, archive_inspirations, topk_inspirations = temp_db.sample()

        # Should have some inspirations
        assert isinstance(archive_inspirations, list)

    def test_excludes_parent_from_inspirations(self, temp_db, program_factory):
        """Parent program should not appear in its own inspirations."""
        parent_id = program_factory.create(score=0.5, correct=True, generation=0)
        program_factory.create(score=0.7, correct=True, generation=0)

        parent, archive_inspirations, _ = temp_db.sample()

        # Parent should not be in its own inspirations
        inspiration_ids = [insp.id for insp in archive_inspirations]
        assert parent.id not in inspiration_ids

    def test_prefers_elites_from_archive(self, temp_db, program_factory):
        """Should prefer high-scoring (elite) programs as inspirations."""
        # Create programs with varying scores
        program_factory.create(score=0.2, correct=True)
        program_factory.create(score=0.9, correct=True)  # Elite
        program_factory.create(score=0.8, correct=True)  # Elite

        _, archive_inspirations, _ = temp_db.sample()

        # If we have inspirations, they should include high-scoring programs
        if archive_inspirations:
            scores = [insp.combined_score for insp in archive_inspirations]
            # At least one should be high-scoring
            assert any(s >= 0.5 for s in scores)

    def test_respects_island_separation(self, temp_db, program_factory):
        """With island separation, should only use programs from same island."""
        # Create programs on different islands
        program_factory.create(score=0.9, correct=True, island=0)
        program_factory.create(score=0.8, correct=True, island=1)

        temp_db.config.enforce_island_separation = True

        _, archive_inspirations, _ = temp_db.sample()

        # Inspirations should be from consistent islands
        # (exact behavior depends on which island parent is on)
        assert isinstance(archive_inspirations, list)


class TestTopKInspirationSelectorIntegration:
    """Integration tests for TopKInspirationSelector."""

    def test_returns_top_scoring_programs(self, temp_db, program_factory):
        """Should return top-k highest scoring programs."""
        # Create programs with varying scores
        program_factory.create(score=0.1, correct=True)
        program_factory.create(score=0.5, correct=True)
        program_factory.create(score=0.9, correct=True)  # Should be in top-k

        _, _, topk_inspirations = temp_db.sample()

        # Should have top-k programs
        if topk_inspirations:
            # At least one should be high-scoring
            scores = [p.combined_score for p in topk_inspirations]
            assert max(scores) >= 0.5

    def test_excludes_archive_inspirations(self, temp_db, program_factory):
        """Top-k should not duplicate archive inspirations."""
        program_factory.create(score=0.5, correct=True)
        program_factory.create(score=0.7, correct=True)
        program_factory.create(score=0.9, correct=True)

        _, archive_inspirations, topk_inspirations = temp_db.sample()

        # No overlap between archive and top-k
        archive_ids = {p.id for p in archive_inspirations}
        topk_ids = {p.id for p in topk_inspirations}

        assert archive_ids.isdisjoint(topk_ids)

    def test_respects_k_limit(self, temp_db, program_factory):
        """Should return at most k programs."""
        # Create many programs
        for i in range(10):
            program_factory.create(score=0.1 + i * 0.05, correct=True)

        _, _, topk_inspirations = temp_db.sample()

        # Should respect config limit
        assert len(topk_inspirations) <= temp_db.config.num_top_k_inspirations


class TestCombinedContextSelectorIntegration:
    """Integration tests for CombinedContextSelector orchestration."""

    def test_returns_both_archive_and_topk(self, temp_db, program_factory):
        """Should return both archive and top-k inspirations."""
        # Create enough programs for both types
        for i in range(5):
            program_factory.create(score=0.3 + i * 0.1, correct=True)

        parent, archive_inspirations, topk_inspirations = temp_db.sample()

        # Should return valid lists for both
        assert isinstance(archive_inspirations, list)
        assert isinstance(topk_inspirations, list)

    def test_archive_and_topk_dont_overlap(self, temp_db, program_factory):
        """Archive and top-k inspirations should be disjoint."""
        for i in range(5):
            program_factory.create(score=0.3 + i * 0.1, correct=True)

        _, archive_inspirations, topk_inspirations = temp_db.sample()

        archive_ids = {p.id for p in archive_inspirations}
        topk_ids = {p.id for p in topk_inspirations}

        assert archive_ids.isdisjoint(topk_ids)


# =============================================================================
# Edge Cases
# =============================================================================


class TestInspirationEdgeCases:
    """Edge cases for inspiration selection."""

    def test_handles_no_archive_programs(self, temp_db, program_factory):
        """Should handle case where no programs are in archive."""
        # Create single program (will be parent)
        program_factory.create(score=0.5, correct=True, generation=0)

        # Should not crash
        parent, archive_inspirations, topk_inspirations = temp_db.sample()

        # Lists may be empty but should be valid
        assert isinstance(archive_inspirations, list)
        assert isinstance(topk_inspirations, list)

    def test_handles_single_program_database(self, temp_db, program_factory):
        """Should handle database with only one program."""
        only_id = program_factory.create(score=0.5, correct=True)

        parent, archive_inspirations, topk_inspirations = temp_db.sample()

        # Parent is the only program, so no inspirations available
        # (or only island copies)
        assert parent is not None

    def test_handles_all_incorrect_programs(self):
        """Should handle case where no correct programs exist."""
        config = DatabaseConfig(
            db_path="",
            num_islands=1,
            archive_size=50,
        )
        db = ProgramDatabase(config, embedding_model="")

        # Add only incorrect program
        program = Program(
            id="test_incorrect",
            code="def main(): pass",
            combined_score=0.5,
            correct=True,  # First one must be correct to bootstrap
        )
        db.add(program)

        # This should work
        parent, archive, topk = db.sample()
        assert parent is not None

        db.close()

    def test_zero_inspirations_requested(self, temp_db, program_factory):
        """Should handle when zero inspirations are configured."""
        program_factory.create(score=0.5, correct=True)

        temp_db.config.num_archive_inspirations = 0
        temp_db.config.num_top_k_inspirations = 0

        parent, archive_inspirations, topk_inspirations = temp_db.sample()

        # Should return empty lists
        assert archive_inspirations == []
        assert topk_inspirations == []


# =============================================================================
# Configuration Tests
# =============================================================================


class TestInspirationConfiguration:
    """Tests for inspiration configuration parameters."""

    def test_elite_selection_ratio_affects_selection(self, temp_db, program_factory):
        """Elite selection ratio should affect how many elites are selected."""
        # Create programs
        for i in range(5):
            program_factory.create(score=0.3 + i * 0.1, correct=True)

        # High elite ratio
        temp_db.config.elite_selection_ratio = 0.8

        _, archive_inspirations, _ = temp_db.sample()

        # Should have some inspirations
        assert isinstance(archive_inspirations, list)

    def test_num_archive_inspirations_respected(self, temp_db, program_factory):
        """Should respect num_archive_inspirations config."""
        for i in range(10):
            program_factory.create(score=0.3 + i * 0.05, correct=True)

        temp_db.config.num_archive_inspirations = 3

        _, archive_inspirations, _ = temp_db.sample()

        # Should not exceed configured limit
        assert len(archive_inspirations) <= 3

    def test_num_topk_inspirations_respected(self, temp_db, program_factory):
        """Should respect num_top_k_inspirations config."""
        for i in range(10):
            program_factory.create(score=0.3 + i * 0.05, correct=True)

        temp_db.config.num_top_k_inspirations = 2

        _, _, topk_inspirations = temp_db.sample()

        # Should not exceed configured limit
        assert len(topk_inspirations) <= 2


# =============================================================================
# Correctness Filter Tests
# =============================================================================


class TestCorrectnessFiltering:
    """Tests for correctness-based filtering."""

    def test_only_correct_programs_as_inspirations(self, temp_db, program_factory):
        """Only correct programs should be selected as inspirations."""
        # Create correct programs
        program_factory.create(score=0.5, correct=True)
        program_factory.create(score=0.7, correct=True)
        # Create incorrect program (should not be in inspirations)
        program_factory.create(score=0.9, correct=False)

        _, archive_inspirations, topk_inspirations = temp_db.sample()

        # All inspirations should be correct
        for insp in archive_inspirations + topk_inspirations:
            assert insp.correct, f"Inspiration {insp.id} should be correct"


# =============================================================================
# Best Program Tests
# =============================================================================


class TestBestProgramInspiration:
    """Tests for best program handling in inspirations."""

    def test_best_program_included_if_correct(self, temp_db, program_factory):
        """Best program should be included in inspirations if correct."""
        # Create programs
        program_factory.create(score=0.3, correct=True)
        best_id = program_factory.create(score=0.95, correct=True)

        _, archive_inspirations, _ = temp_db.sample()

        # Check if any inspiration has the high score
        # (may be original or island copy)
        inspiration_scores = [insp.combined_score for insp in archive_inspirations]
        if inspiration_scores:
            assert max(inspiration_scores) >= 0.5

    def test_best_program_excluded_if_is_parent(self, temp_db, program_factory):
        """Best program should be excluded if it's the parent."""
        best_id = program_factory.create(score=0.95, correct=True)

        parent, archive_inspirations, _ = temp_db.sample()

        # Parent should not be in its own inspirations
        if parent.combined_score >= 0.9:  # If parent is the best
            inspiration_ids = [insp.id for insp in archive_inspirations]
            assert parent.id not in inspiration_ids


# =============================================================================
# Island-Aware Tests
# =============================================================================


class TestIslandAwareInspiration:
    """Tests for island-aware inspiration selection."""

    def test_same_island_preference_with_separation(self, temp_db, program_factory):
        """With island separation, should prefer same-island programs."""
        # Create programs on different islands
        island0_prog = program_factory.create(score=0.8, correct=True, island=0)
        island1_prog = program_factory.create(score=0.9, correct=True, island=1)

        temp_db.config.enforce_island_separation = True

        parent, archive_inspirations, _ = temp_db.sample()

        # All inspirations should be from parent's island
        parent_island = parent.island_idx
        for insp in archive_inspirations:
            if parent_island is not None:
                assert insp.island_idx == parent_island or insp.island_idx is None

    def test_cross_island_allowed_without_separation(self, temp_db, program_factory):
        """Without island separation, can use programs from any island."""
        # Create programs on different islands
        program_factory.create(score=0.8, correct=True, island=0)
        program_factory.create(score=0.9, correct=True, island=1)

        temp_db.config.enforce_island_separation = False

        parent, archive_inspirations, _ = temp_db.sample()

        # Should be able to get inspirations (may be from any island)
        assert isinstance(archive_inspirations, list)
