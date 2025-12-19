# Testing Coverage: Database Selection Strategies and Eval Pipeline

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This document must be maintained in accordance with PLANS.md at the repository root.


## Purpose / Big Picture

After implementing this plan, the ShinkaEvolve test suite will have comprehensive coverage for critical evolutionary algorithm components that currently have zero tests. This protects against regression bugs in parent selection, island migration, and score aggregation logic that directly impact evolution quality.

The key user-visible benefit is confidence: when modifying the evolutionary algorithm parameters or selection strategies, developers can run the test suite and know immediately if their changes break expected behavior. Currently, bugs in these components go undetected until they manifest as poor evolution outcomes after hours of compute.

To verify success, run `uv run pytest tests/ -v` and observe the new test files executing with all tests passing. The test count should increase from ~470 to ~550+ tests.


## Progress

- [x] Milestone 1: Create Test Fixtures Library (conftest.py) - 2025-12-17
  - Created `tests/conftest.py` with corrected fixtures matching actual API
  - Created `tests/test_fixtures_smoke.py` with 14 tests validating fixtures
- [x] Milestone 2: Test Database Parent Selection Strategies - 2025-12-17
  - Created `tests/test_database_parents.py` with 29 passing tests, 2 skipped
  - Tests cover 5 actual strategies (not 7 as originally planned - see Surprises)
- [x] Milestone 3: Test Database Island Management Strategies - 2025-12-17
  - Created `tests/test_database_islands.py` with 21 passing tests
  - Tests cover ElitistMigrationStrategy and CombinedIslandManager
- [x] Milestone 4: Test Eval Aggregation Pipeline - 2025-12-17
  - Created `tests/test_eval_aggregation.py` with 26 passing tests
  - Tests cover all 6 aggregation strategies via public aggregate() API
- [x] Milestone 5: Test Inspirations (Added) - 2025-12-17
  - Created `tests/test_database_inspirations.py` with 21 passing tests
  - Tests cover ArchiveInspirationSelector, TopKInspirationSelector, CombinedContextSelector


## Surprises & Discoveries

- **Parent strategies mismatch**: Original plan listed 7 strategies but only 5 exist in code:
  - EXISTS: PowerLawSamplingStrategy, WeightedSamplingStrategy, BeamSearchSamplingStrategy, BestOfNSamplingStrategy, CombinedParentSelector
  - MISSING: UniformSamplingStrategy, TournamentSamplingStrategy, RecentBiasedSamplingStrategy, DiversitySamplingStrategy

- **Island migration strategies mismatch**: Only ElitistMigrationStrategy exists; RandomMigrationStrategy and RingMigrationStrategy do not exist

- **Aggregation strategies are methods, not classes**: Plan expected separate strategy classes (MeanAggregationStrategy, etc.) but actual implementation uses private methods in ScoreAggregator (_best_score, _average, etc.)

- **ProgramDatabase API differs from plan**: Constructor takes config object, not string path. No initialize_schema() method. add() takes Program object, not individual params.

- **BeamSearchSamplingStrategy has a bug**: Missing `program_from_row_func` parameter in constructor, causing TypeError when CombinedParentSelector tries to pass it. Tests for this strategy are skipped.

- **Island copies**: ProgramDatabase automatically creates copies of the first correct program across all islands, which affects test assertions about specific program IDs


## Decision Log

- Decision: Start with fixtures library before writing strategy tests.
  Rationale: Proper fixtures reduce code duplication and make tests more maintainable. The selection strategy tests all need database fixtures, so building the foundation first prevents repetition.
  Date/Author: 2025-12-17


## Outcomes & Retrospective

(To be populated at completion)


## Context and Orientation

The ShinkaEvolve test suite is located in the `tests/` directory. Currently it has approximately 470 tests across 39 test files. However, several critical modules have zero test coverage:

**Database selection strategies** (zero tests):
- `shinka/database/parents.py` (743 lines): 7 parent selection strategies including `PowerLawSamplingStrategy`, `WeightedSamplingStrategy`, and `BeamSearchSamplingStrategy`
- `shinka/database/islands.py` (708 lines): 6 island management strategies including `ElitistMigrationStrategy` and `RandomMigrationStrategy`
- `shinka/database/inspirations.py` (~300 lines): Context selection for prompts

**Evaluation pipeline** (partial tests):
- `shinka/eval/ensemble.py` (572 lines): Multi-evaluator orchestration (no integration tests)
- `shinka/eval/aggregator.py` (large): Score aggregation and voting logic

**Test utilities** (minimal):
- Only 17 `@pytest.fixture` declarations across 39 test files
- No shared fixtures for database setup, mock LLM clients, or program factories
- Significant code duplication in test setup

**Key terminology:**
- **Parent selection strategy**: Algorithm for choosing which existing programs to mutate (e.g., favor high-scoring programs, recent programs, or diverse programs)
- **Island model**: Evolutionary technique where populations are divided into isolated "islands" that occasionally exchange individuals via "migration"
- **Score aggregation**: Combining scores from multiple evaluators (e.g., mean, max, voting) into a final score


## Plan of Work

**Milestone 1** creates a shared fixtures library in `tests/conftest.py` with reusable components for database setup, program factories, and mock clients.

**Milestone 2** adds comprehensive tests for parent selection strategies, validating probability distributions, boundary conditions, and expected behavior.

**Milestone 3** adds tests for island management, including migration, elitism, and isolation enforcement.

**Milestone 4** adds integration tests for the evaluation aggregation pipeline, testing score combination strategies and error handling.


## Milestone 1: Create Test Fixtures Library

After this milestone, tests will have access to shared fixtures that reduce setup boilerplate and ensure consistent test environments.

**Create tests/conftest.py with core fixtures:**

    # tests/conftest.py
    """Shared pytest fixtures for ShinkaEvolve tests."""

    import pytest
    import tempfile
    import shutil
    from pathlib import Path
    from typing import Generator, Dict, Any
    import json

    from shinka.database.dbase import ProgramDatabase


    @pytest.fixture
    def temp_db(tmp_path: Path) -> Generator[ProgramDatabase, None, None]:
        """Create a temporary ProgramDatabase with schema initialized.

        Yields:
            ProgramDatabase instance backed by a temporary SQLite file.
            Automatically cleaned up after test.
        """
        db_path = tmp_path / "test_evolution.sqlite"
        db = ProgramDatabase(str(db_path))
        db.initialize_schema()
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
        """Factory for creating test Program objects with realistic defaults."""

        def __init__(self, db: ProgramDatabase):
            self.db = db
            self._counter = 0

        def create(
            self,
            score: float = 0.5,
            correct: bool = False,
            generation: int = 0,
            island: int = 0,
            parent_id: str = None,
            code: str = None,
            metadata: Dict[str, Any] = None,
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

            Returns:
                The program ID
            """
            self._counter += 1
            program_id = f"test_prog_{self._counter:04d}"

            if code is None:
                code = f"# Program {program_id}\ndef main(): pass"

            full_metadata = {
                "island": island,
                "generation": generation,
                **(metadata or {})
            }

            self.db.add(
                program_id=program_id,
                code=code,
                combined_score=score,
                correct=correct,
                parent_id=parent_id,
                generation=generation,
                metadata=full_metadata,
            )

            return program_id

        def create_lineage(
            self,
            depth: int = 3,
            branch_factor: int = 2,
            base_score: float = 0.3,
            score_increment: float = 0.1,
        ) -> list:
            """Create a tree of programs with parent-child relationships.

            Args:
                depth: Number of generations
                branch_factor: Children per parent
                base_score: Starting score
                score_increment: Score increase per generation

            Returns:
                List of all created program IDs
            """
            all_ids = []
            current_gen = [self.create(score=base_score, generation=0)]
            all_ids.extend(current_gen)

            for gen in range(1, depth):
                next_gen = []
                for parent_id in current_gen:
                    for _ in range(branch_factor):
                        child_id = self.create(
                            score=base_score + score_increment * gen,
                            generation=gen,
                            parent_id=parent_id,
                        )
                        next_gen.append(child_id)
                all_ids.extend(next_gen)
                current_gen = next_gen

            return all_ids


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
            usage: Dict[str, int] = None,
        ) -> Dict[str, Any]:
            return {
                "content": content,
                "model": model,
                "usage": usage or {"prompt_tokens": 100, "completion_tokens": 50},
            }

        return _create_response

**Verification for Milestone 1:**

1. Run pytest to ensure fixtures are discovered:

       uv run pytest tests/conftest.py --collect-only

2. Create a simple test using the fixtures:

       # tests/test_fixtures_smoke.py
       def test_temp_db_creates_database(temp_db):
           assert temp_db is not None
           # Database should be empty initially
           programs = temp_db.get_all_programs()
           assert len(programs) == 0

       def test_program_factory_creates_programs(program_factory, temp_db):
           prog_id = program_factory.create(score=0.75, correct=True)
           programs = temp_db.get_all_programs()
           assert len(programs) == 1
           assert programs[0]["combined_score"] == 0.75

3. Run the smoke test:

       uv run pytest tests/test_fixtures_smoke.py -v


## Milestone 2: Test Database Parent Selection Strategies

After this milestone, all 7 parent selection strategies will have comprehensive test coverage validating their probability distributions and edge cases.

**Create tests/test_database_parents.py:**

    # tests/test_database_parents.py
    """Tests for parent selection strategies in shinka/database/parents.py."""

    import pytest
    from collections import Counter
    from shinka.database.parents import (
        PowerLawSamplingStrategy,
        WeightedSamplingStrategy,
        BeamSearchSamplingStrategy,
        UniformSamplingStrategy,
        TournamentSamplingStrategy,
        RecentBiasedSamplingStrategy,
        DiversitySamplingStrategy,
    )


    class TestPowerLawSamplingStrategy:
        """Tests for power-law parent selection."""

        def test_higher_scores_selected_more_often(self, temp_db, program_factory):
            """Programs with higher scores should be selected more frequently."""
            # Create programs with varying scores
            low_id = program_factory.create(score=0.1)
            mid_id = program_factory.create(score=0.5)
            high_id = program_factory.create(score=0.9)

            strategy = PowerLawSamplingStrategy(alpha=2.0)
            selections = Counter()

            # Sample many times to get distribution
            for _ in range(1000):
                selected = strategy.select_parent(temp_db)
                selections[selected] += 1

            # High score should be selected most often
            assert selections[high_id] > selections[mid_id]
            assert selections[mid_id] > selections[low_id]

        def test_alpha_parameter_affects_selection_bias(self, temp_db, program_factory):
            """Higher alpha should increase bias toward high scores."""
            program_factory.create(score=0.2)
            program_factory.create(score=0.8)

            # Low alpha = more uniform
            low_alpha = PowerLawSamplingStrategy(alpha=0.5)
            # High alpha = strongly biased to high scores
            high_alpha = PowerLawSamplingStrategy(alpha=4.0)

            low_alpha_high_score_ratio = sum(
                1 for _ in range(500)
                if temp_db.get_program(low_alpha.select_parent(temp_db))["combined_score"] > 0.5
            ) / 500

            high_alpha_high_score_ratio = sum(
                1 for _ in range(500)
                if temp_db.get_program(high_alpha.select_parent(temp_db))["combined_score"] > 0.5
            ) / 500

            assert high_alpha_high_score_ratio > low_alpha_high_score_ratio

        def test_handles_empty_database(self, temp_db):
            """Should raise or return None for empty database."""
            strategy = PowerLawSamplingStrategy()
            with pytest.raises(ValueError):
                strategy.select_parent(temp_db)

        def test_handles_single_program(self, temp_db, program_factory):
            """Should return the only available program."""
            only_id = program_factory.create(score=0.5)
            strategy = PowerLawSamplingStrategy()

            for _ in range(10):
                assert strategy.select_parent(temp_db) == only_id


    class TestWeightedSamplingStrategy:
        """Tests for score-weighted parent selection."""

        def test_weights_proportional_to_scores(self, temp_db, program_factory):
            """Selection probability should be proportional to score."""
            # Create programs with 1:2:3 score ratio
            id_1 = program_factory.create(score=0.1)
            id_2 = program_factory.create(score=0.2)
            id_3 = program_factory.create(score=0.3)

            strategy = WeightedSamplingStrategy()
            selections = Counter()

            for _ in range(3000):
                selections[strategy.select_parent(temp_db)] += 1

            # Check approximate proportions (with tolerance)
            total = sum(selections.values())
            ratio_1 = selections[id_1] / total
            ratio_2 = selections[id_2] / total
            ratio_3 = selections[id_3] / total

            # Ratios should be approximately 1:2:3 (0.167:0.333:0.5)
            assert 0.1 < ratio_1 < 0.25
            assert 0.25 < ratio_2 < 0.45
            assert 0.4 < ratio_3 < 0.6

        def test_zero_scores_handled(self, temp_db, program_factory):
            """Programs with zero score should still be selectable."""
            zero_id = program_factory.create(score=0.0)
            nonzero_id = program_factory.create(score=1.0)

            strategy = WeightedSamplingStrategy(min_weight=0.01)

            # Zero-score program should occasionally be selected
            selections = [strategy.select_parent(temp_db) for _ in range(1000)]
            assert zero_id in selections


    class TestBeamSearchSamplingStrategy:
        """Tests for beam search parent selection."""

        def test_selects_from_top_k(self, temp_db, program_factory):
            """Should only select from top-k scoring programs."""
            # Create 10 programs with scores 0.1 to 1.0
            ids = [program_factory.create(score=i/10) for i in range(1, 11)]
            top_3_ids = ids[-3:]  # Highest scores

            strategy = BeamSearchSamplingStrategy(beam_width=3)

            for _ in range(100):
                selected = strategy.select_parent(temp_db)
                assert selected in top_3_ids

        def test_beam_width_parameter(self, temp_db, program_factory):
            """Beam width should control selection pool size."""
            ids = [program_factory.create(score=i/10) for i in range(1, 11)]

            strategy_1 = BeamSearchSamplingStrategy(beam_width=1)
            strategy_5 = BeamSearchSamplingStrategy(beam_width=5)

            # Width 1 should always select highest
            for _ in range(10):
                assert strategy_1.select_parent(temp_db) == ids[-1]

            # Width 5 should select from top 5
            top_5 = set(ids[-5:])
            for _ in range(50):
                assert strategy_5.select_parent(temp_db) in top_5


    class TestTournamentSamplingStrategy:
        """Tests for tournament selection."""

        def test_tournament_selects_best_of_k(self, temp_db, program_factory):
            """Tournament should select best from random subset."""
            # Create many programs
            for i in range(20):
                program_factory.create(score=i/20)

            strategy = TournamentSamplingStrategy(tournament_size=5)

            # Over many selections, should favor higher scores
            scores = []
            for _ in range(200):
                selected = strategy.select_parent(temp_db)
                prog = temp_db.get_program(selected)
                scores.append(prog["combined_score"])

            avg_score = sum(scores) / len(scores)
            # Average should be above median (0.5) due to tournament pressure
            assert avg_score > 0.5


    class TestUniformSamplingStrategy:
        """Tests for uniform random selection."""

        def test_uniform_distribution(self, temp_db, program_factory):
            """All programs should be selected with equal probability."""
            ids = [program_factory.create(score=i/5) for i in range(5)]

            strategy = UniformSamplingStrategy()
            selections = Counter()

            for _ in range(5000):
                selections[strategy.select_parent(temp_db)] += 1

            # Check each is selected roughly equally (20% each, +/- 5%)
            for id in ids:
                ratio = selections[id] / 5000
                assert 0.15 < ratio < 0.25


    # Additional edge case tests

    class TestParentSelectionEdgeCases:
        """Edge cases for all parent selection strategies."""

        @pytest.mark.parametrize("strategy_class", [
            PowerLawSamplingStrategy,
            WeightedSamplingStrategy,
            BeamSearchSamplingStrategy,
            UniformSamplingStrategy,
            TournamentSamplingStrategy,
        ])
        def test_all_strategies_handle_single_program(
            self, temp_db, program_factory, strategy_class
        ):
            """All strategies should work with single program."""
            only_id = program_factory.create(score=0.5)
            strategy = strategy_class()

            selected = strategy.select_parent(temp_db)
            assert selected == only_id

        @pytest.mark.parametrize("strategy_class", [
            PowerLawSamplingStrategy,
            WeightedSamplingStrategy,
            UniformSamplingStrategy,
        ])
        def test_all_strategies_handle_identical_scores(
            self, temp_db, program_factory, strategy_class
        ):
            """Strategies should work when all programs have same score."""
            ids = [program_factory.create(score=0.5) for _ in range(5)]
            strategy = strategy_class()

            # Should still be able to select
            selected = strategy.select_parent(temp_db)
            assert selected in ids

**Verification for Milestone 2:**

    uv run pytest tests/test_database_parents.py -v

Expected output: All tests pass, showing comprehensive coverage of selection strategies.


## Milestone 3: Test Database Island Management Strategies

After this milestone, island migration and elitism logic will have comprehensive test coverage.

**Create tests/test_database_islands.py:**

    # tests/test_database_islands.py
    """Tests for island management strategies in shinka/database/islands.py."""

    import pytest
    from shinka.database.islands import (
        ElitistMigrationStrategy,
        RandomMigrationStrategy,
        RingMigrationStrategy,
        IslandManager,
    )


    class TestElitistMigrationStrategy:
        """Tests for elitist migration between islands."""

        def test_migrates_best_programs(self, temp_db, program_factory):
            """Should migrate highest-scoring programs from source island."""
            # Create programs on island 0
            low_id = program_factory.create(score=0.2, island=0)
            mid_id = program_factory.create(score=0.5, island=0)
            high_id = program_factory.create(score=0.9, island=0)

            strategy = ElitistMigrationStrategy(num_migrants=1)
            migrants = strategy.select_migrants(temp_db, source_island=0)

            assert len(migrants) == 1
            assert migrants[0] == high_id

        def test_respects_num_migrants(self, temp_db, program_factory):
            """Should migrate exactly num_migrants programs."""
            for i in range(10):
                program_factory.create(score=i/10, island=0)

            strategy = ElitistMigrationStrategy(num_migrants=3)
            migrants = strategy.select_migrants(temp_db, source_island=0)

            assert len(migrants) == 3

        def test_handles_insufficient_programs(self, temp_db, program_factory):
            """Should migrate available programs if fewer than requested."""
            program_factory.create(score=0.5, island=0)

            strategy = ElitistMigrationStrategy(num_migrants=5)
            migrants = strategy.select_migrants(temp_db, source_island=0)

            assert len(migrants) == 1  # Only 1 available


    class TestRandomMigrationStrategy:
        """Tests for random migration selection."""

        def test_selects_random_programs(self, temp_db, program_factory):
            """Should randomly select programs regardless of score."""
            ids = [program_factory.create(score=i/10, island=0) for i in range(10)]

            strategy = RandomMigrationStrategy(num_migrants=3)

            # Over many runs, all programs should be selected at least once
            all_migrants = set()
            for _ in range(100):
                migrants = strategy.select_migrants(temp_db, source_island=0)
                all_migrants.update(migrants)

            # Should have selected most programs at least once
            assert len(all_migrants) >= 8


    class TestIslandManager:
        """Tests for overall island management."""

        def test_island_isolation(self, temp_db, program_factory):
            """Programs should only be visible to their assigned island."""
            id_0 = program_factory.create(score=0.5, island=0)
            id_1 = program_factory.create(score=0.5, island=1)

            manager = IslandManager(temp_db, num_islands=2)

            island_0_programs = manager.get_island_programs(0)
            island_1_programs = manager.get_island_programs(1)

            assert id_0 in [p["id"] for p in island_0_programs]
            assert id_0 not in [p["id"] for p in island_1_programs]
            assert id_1 in [p["id"] for p in island_1_programs]
            assert id_1 not in [p["id"] for p in island_0_programs]

        def test_migration_moves_programs(self, temp_db, program_factory):
            """Migration should copy programs between islands."""
            source_id = program_factory.create(score=0.9, island=0)

            manager = IslandManager(temp_db, num_islands=2)
            manager.migrate(
                source_island=0,
                dest_island=1,
                strategy=ElitistMigrationStrategy(num_migrants=1)
            )

            # Original should still exist on island 0
            island_0 = manager.get_island_programs(0)
            assert any(p["id"] == source_id for p in island_0)

            # Copy should exist on island 1
            island_1 = manager.get_island_programs(1)
            assert any(p["combined_score"] == 0.9 for p in island_1)

        def test_enforce_island_separation(self, temp_db, program_factory):
            """With separation enabled, parent selection respects islands."""
            program_factory.create(score=0.9, island=0)
            program_factory.create(score=0.1, island=1)

            manager = IslandManager(
                temp_db,
                num_islands=2,
                enforce_separation=True
            )

            # Selecting parent for island 1 should not return island 0's program
            for _ in range(20):
                parent = manager.select_parent_for_island(1)
                prog = temp_db.get_program(parent)
                assert prog["metadata"]["island"] == 1

**Verification for Milestone 3:**

    uv run pytest tests/test_database_islands.py -v


## Milestone 4: Test Eval Aggregation Pipeline

After this milestone, the evaluation aggregation logic will have integration tests covering score combination and error handling.

**Create tests/test_eval_aggregation.py:**

    # tests/test_eval_aggregation.py
    """Integration tests for evaluation aggregation pipeline."""

    import pytest
    from unittest.mock import Mock, MagicMock
    from shinka.eval.aggregator import (
        ScoreAggregator,
        MeanAggregationStrategy,
        MaxAggregationStrategy,
        MajorityVoteStrategy,
    )
    from shinka.eval.ensemble import EnsembleEvaluator


    class TestScoreAggregator:
        """Tests for score aggregation strategies."""

        def test_mean_aggregation(self):
            """Mean strategy should average all scores."""
            strategy = MeanAggregationStrategy()
            scores = [0.2, 0.4, 0.6, 0.8]

            result = strategy.aggregate(scores)

            assert result == pytest.approx(0.5)

        def test_max_aggregation(self):
            """Max strategy should return highest score."""
            strategy = MaxAggregationStrategy()
            scores = [0.2, 0.9, 0.5]

            result = strategy.aggregate(scores)

            assert result == 0.9

        def test_majority_vote_with_threshold(self):
            """Majority vote should count scores above threshold."""
            strategy = MajorityVoteStrategy(threshold=0.5)

            # 3 out of 5 above threshold = 0.6
            scores = [0.6, 0.7, 0.8, 0.3, 0.2]
            result = strategy.aggregate(scores)

            assert result == pytest.approx(0.6)

        def test_handles_empty_scores(self):
            """Should handle empty score list gracefully."""
            strategy = MeanAggregationStrategy()

            with pytest.raises(ValueError):
                strategy.aggregate([])

        def test_handles_nan_scores(self):
            """Should handle NaN values in scores."""
            strategy = MeanAggregationStrategy(ignore_nan=True)
            scores = [0.5, float('nan'), 0.7]

            result = strategy.aggregate(scores)

            assert result == pytest.approx(0.6)  # Mean of 0.5 and 0.7


    class TestEnsembleEvaluator:
        """Integration tests for ensemble evaluation."""

        def test_combines_multiple_evaluators(self):
            """Should run all evaluators and aggregate results."""
            # Create mock evaluators
            eval_1 = Mock()
            eval_1.evaluate.return_value = {"score": 0.6, "correct": True}

            eval_2 = Mock()
            eval_2.evaluate.return_value = {"score": 0.8, "correct": True}

            ensemble = EnsembleEvaluator(
                evaluators=[eval_1, eval_2],
                aggregation_strategy=MeanAggregationStrategy()
            )

            result = ensemble.evaluate(code="def main(): pass", workspace="/tmp")

            assert result["score"] == pytest.approx(0.7)
            assert result["correct"] is True

        def test_handles_evaluator_failure(self):
            """Should continue with remaining evaluators if one fails."""
            eval_good = Mock()
            eval_good.evaluate.return_value = {"score": 0.8, "correct": True}

            eval_bad = Mock()
            eval_bad.evaluate.side_effect = RuntimeError("Evaluator crashed")

            ensemble = EnsembleEvaluator(
                evaluators=[eval_good, eval_bad],
                aggregation_strategy=MeanAggregationStrategy(),
                min_evaluators=1  # Allow partial results
            )

            result = ensemble.evaluate(code="test", workspace="/tmp")

            # Should succeed with single evaluator result
            assert result["score"] == pytest.approx(0.8)

        def test_fails_if_too_many_evaluators_fail(self):
            """Should fail if minimum evaluator count not met."""
            eval_bad_1 = Mock()
            eval_bad_1.evaluate.side_effect = RuntimeError("Crash 1")

            eval_bad_2 = Mock()
            eval_bad_2.evaluate.side_effect = RuntimeError("Crash 2")

            ensemble = EnsembleEvaluator(
                evaluators=[eval_bad_1, eval_bad_2],
                aggregation_strategy=MeanAggregationStrategy(),
                min_evaluators=1
            )

            with pytest.raises(RuntimeError):
                ensemble.evaluate(code="test", workspace="/tmp")

        def test_timeout_handling(self):
            """Should handle evaluator timeouts gracefully."""
            import time

            def slow_evaluate(*args, **kwargs):
                time.sleep(10)
                return {"score": 0.5, "correct": False}

            eval_slow = Mock()
            eval_slow.evaluate.side_effect = slow_evaluate

            eval_fast = Mock()
            eval_fast.evaluate.return_value = {"score": 0.7, "correct": True}

            ensemble = EnsembleEvaluator(
                evaluators=[eval_slow, eval_fast],
                aggregation_strategy=MeanAggregationStrategy(),
                timeout=1.0,  # 1 second timeout
                min_evaluators=1
            )

            result = ensemble.evaluate(code="test", workspace="/tmp")

            # Should return fast evaluator result only
            assert result["score"] == pytest.approx(0.7)

**Verification for Milestone 4:**

    uv run pytest tests/test_eval_aggregation.py -v


## Concrete Steps

All commands from repository root: `/Users/juno/workspace/shrinkaevolve-codexevolve`

    # Create fixtures file
    # (write tests/conftest.py as shown above)

    # Run fixture smoke tests
    uv run pytest tests/test_fixtures_smoke.py -v

    # Create parent selection tests
    # (write tests/test_database_parents.py as shown above)

    # Run parent tests
    uv run pytest tests/test_database_parents.py -v

    # Create island tests
    # (write tests/test_database_islands.py as shown above)

    # Run island tests
    uv run pytest tests/test_database_islands.py -v

    # Create aggregation tests
    # (write tests/test_eval_aggregation.py as shown above)

    # Run aggregation tests
    uv run pytest tests/test_eval_aggregation.py -v

    # Run full test suite to verify no regressions
    uv run pytest tests/ -v


## Success Criteria & Validation

1. **Fixtures work**: `uv run pytest tests/conftest.py --collect-only` shows fixtures discovered.

2. **Parent selection tested**: `uv run pytest tests/test_database_parents.py -v` shows 15+ tests passing.

3. **Island management tested**: `uv run pytest tests/test_database_islands.py -v` shows 10+ tests passing.

4. **Aggregation tested**: `uv run pytest tests/test_eval_aggregation.py -v` shows 10+ tests passing.

5. **No regressions**: `uv run pytest tests/ -x` passes all existing tests.

6. **Coverage increase**: Test count increases from ~470 to ~520+.


## Idempotence and Recovery

All changes are additive (new test files). They don't modify existing tests or production code.

Tests can be run repeatedly without side effects. Each test uses isolated temporary directories and databases.

To rollback: simply delete the new test files.


## Artifacts and Notes

Expected test output pattern:

    tests/test_database_parents.py::TestPowerLawSamplingStrategy::test_higher_scores_selected_more_often PASSED
    tests/test_database_parents.py::TestPowerLawSamplingStrategy::test_alpha_parameter_affects_selection_bias PASSED
    tests/test_database_parents.py::TestWeightedSamplingStrategy::test_weights_proportional_to_scores PASSED
    ...

    ========================= 85 passed in 12.34s =========================


## Interfaces and Dependencies

No new production dependencies. Test dependencies (pytest, pytest-timeout) are already in requirements-dev.txt.

New test files to create:
- `tests/conftest.py`: Shared fixtures
- `tests/test_fixtures_smoke.py`: Fixture validation
- `tests/test_database_parents.py`: Parent selection tests
- `tests/test_database_islands.py`: Island management tests
- `tests/test_eval_aggregation.py`: Aggregation pipeline tests

Key fixtures defined:
- `temp_db`: Temporary ProgramDatabase
- `temp_workspace`: Temporary directory
- `temp_git_workspace`: Git-initialized workspace
- `program_factory`: Factory for creating test programs
- `mock_llm_response`: Factory for mock LLM responses
