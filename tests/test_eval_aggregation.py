"""Tests for evaluation aggregation pipeline in shinka/eval/aggregator.py.

This module tests the ScoreAggregator class and its aggregation strategies:
1. best_score - returns maximum score
2. worst_case - returns minimum score (conservative)
3. average - arithmetic mean with majority vote
4. weighted_average - weighted mean based on evaluator weights
5. majority_vote - threshold-based voting for correctness
6. median - median score with majority vote
"""

import pytest
from dataclasses import dataclass
from typing import Dict, Any, Optional
from unittest.mock import Mock

from shinka.eval.aggregator import ScoreAggregator, EvaluatorScore, AggregatedResult


# =============================================================================
# Test Fixtures
# =============================================================================


@dataclass
class MockAggregationConfig:
    """Mock AggregationConfig for testing."""

    strategy: str = "average"
    vote_threshold: float = 0.5
    min_successful_evals: int = 1
    failure_mode: str = "ignore"


def create_score(
    evaluator_id: str,
    score: float,
    correct: bool,
    weight: float = 1.0,
    error: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> EvaluatorScore:
    """Helper to create EvaluatorScore instances."""
    return EvaluatorScore(
        evaluator_id=evaluator_id,
        evaluator_name=evaluator_id,
        score=score,
        correct=correct,
        weight=weight,
        error=error,
        metrics=metrics or {},
    )


# =============================================================================
# Best Score Strategy Tests
# =============================================================================


class TestBestScoreStrategy:
    """Tests for the best_score aggregation strategy."""

    def test_returns_maximum_score(self):
        """Should return the highest score among evaluators."""
        config = MockAggregationConfig(strategy="best_score")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.3, True),
            "eval2": create_score("eval2", 0.9, True),
            "eval3": create_score("eval3", 0.6, True),
        }

        result = aggregator.aggregate(scores)

        assert result.combined_score == 0.9
        assert result.strategy_used == "best_score"

    def test_uses_best_evaluator_correctness(self):
        """Should use the correct status from the best-scoring evaluator."""
        config = MockAggregationConfig(strategy="best_score")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.3, True),
            "eval2": create_score("eval2", 0.9, False),  # Best but incorrect
        }

        result = aggregator.aggregate(scores)

        assert result.combined_score == 0.9
        assert result.correct is False


# =============================================================================
# Worst Case Strategy Tests
# =============================================================================


class TestWorstCaseStrategy:
    """Tests for the worst_case aggregation strategy."""

    def test_returns_minimum_score(self):
        """Should return the lowest score among evaluators."""
        config = MockAggregationConfig(strategy="worst_case")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.3, True),
            "eval2": create_score("eval2", 0.9, True),
            "eval3": create_score("eval3", 0.6, True),
        }

        result = aggregator.aggregate(scores)

        assert result.combined_score == 0.3
        assert result.strategy_used == "worst_case"

    def test_requires_all_correct_for_correct(self):
        """Should require ALL evaluators to agree for correct=True."""
        config = MockAggregationConfig(strategy="worst_case")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.3, True),
            "eval2": create_score("eval2", 0.9, False),  # One incorrect
        }

        result = aggregator.aggregate(scores)

        assert result.correct is False

    def test_all_correct_gives_correct_true(self):
        """Should return correct=True when all evaluators agree."""
        config = MockAggregationConfig(strategy="worst_case")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.3, True),
            "eval2": create_score("eval2", 0.9, True),
        }

        result = aggregator.aggregate(scores)

        assert result.correct is True


# =============================================================================
# Average Strategy Tests
# =============================================================================


class TestAverageStrategy:
    """Tests for the average aggregation strategy."""

    def test_returns_mean_score(self):
        """Should return arithmetic mean of scores."""
        config = MockAggregationConfig(strategy="average")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.2, True),
            "eval2": create_score("eval2", 0.4, True),
            "eval3": create_score("eval3", 0.6, True),
            "eval4": create_score("eval4", 0.8, True),
        }

        result = aggregator.aggregate(scores)

        assert result.combined_score == pytest.approx(0.5)
        assert result.strategy_used == "average"

    def test_majority_vote_for_correctness(self):
        """Should use majority vote to determine correctness."""
        config = MockAggregationConfig(strategy="average")
        aggregator = ScoreAggregator(config)

        # 3 correct, 2 incorrect = majority correct
        scores = {
            "eval1": create_score("eval1", 0.5, True),
            "eval2": create_score("eval2", 0.5, True),
            "eval3": create_score("eval3", 0.5, True),
            "eval4": create_score("eval4", 0.5, False),
            "eval5": create_score("eval5", 0.5, False),
        }

        result = aggregator.aggregate(scores)

        assert result.correct is True

    def test_single_evaluator(self):
        """Should handle single evaluator case."""
        config = MockAggregationConfig(strategy="average")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.75, True),
        }

        result = aggregator.aggregate(scores)

        assert result.combined_score == 0.75
        assert result.correct is True


# =============================================================================
# Weighted Average Strategy Tests
# =============================================================================


class TestWeightedAverageStrategy:
    """Tests for the weighted_average aggregation strategy."""

    def test_respects_weights(self):
        """Should weight scores by evaluator weights."""
        config = MockAggregationConfig(strategy="weighted_average")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.0, True, weight=1.0),
            "eval2": create_score("eval2", 1.0, True, weight=3.0),  # Higher weight
        }

        result = aggregator.aggregate(scores)

        # Weighted average: (0*1 + 1*3) / (1+3) = 0.75
        assert result.combined_score == pytest.approx(0.75)
        assert result.strategy_used == "weighted_average"

    def test_zero_weights_handled(self):
        """Should handle zero weights gracefully."""
        config = MockAggregationConfig(strategy="weighted_average")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.5, True, weight=0.0),
            "eval2": create_score("eval2", 0.8, True, weight=1.0),
        }

        result = aggregator.aggregate(scores)

        # Only eval2 should contribute
        assert result.combined_score == pytest.approx(0.8)


# =============================================================================
# Majority Vote Strategy Tests
# =============================================================================


class TestMajorityVoteStrategy:
    """Tests for the majority_vote aggregation strategy."""

    def test_majority_correct(self):
        """Should return correct=True when majority agrees."""
        config = MockAggregationConfig(strategy="majority_vote", vote_threshold=0.5)
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.6, True),
            "eval2": create_score("eval2", 0.7, True),
            "eval3": create_score("eval3", 0.8, False),
        }

        result = aggregator.aggregate(scores)

        # 2/3 = 0.67 >= 0.5 threshold
        assert result.correct is True
        assert result.strategy_used == "majority_vote"

    def test_threshold_effect(self):
        """Higher threshold should require more agreement."""
        config = MockAggregationConfig(strategy="majority_vote", vote_threshold=0.8)
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.6, True),
            "eval2": create_score("eval2", 0.7, True),
            "eval3": create_score("eval3", 0.8, False),
        }

        result = aggregator.aggregate(scores)

        # 2/3 = 0.67 < 0.8 threshold
        assert result.correct is False

    def test_averages_majority_camp_scores(self):
        """Should average scores from the majority camp."""
        config = MockAggregationConfig(strategy="majority_vote", vote_threshold=0.5)
        aggregator = ScoreAggregator(config)

        # 3 correct with varying scores
        scores = {
            "eval1": create_score("eval1", 0.5, True),
            "eval2": create_score("eval2", 0.7, True),
            "eval3": create_score("eval3", 0.8, True),
            "eval4": create_score("eval4", 0.1, False),
        }

        result = aggregator.aggregate(scores)

        # Average of correct camp: (0.5 + 0.7 + 0.8) / 3 = 0.667
        assert result.combined_score == pytest.approx((0.5 + 0.7 + 0.8) / 3, abs=0.01)


# =============================================================================
# Median Strategy Tests
# =============================================================================


class TestMedianStrategy:
    """Tests for the median aggregation strategy."""

    def test_returns_median_score(self):
        """Should return median score."""
        config = MockAggregationConfig(strategy="median")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.1, True),
            "eval2": create_score("eval2", 0.5, True),  # Median
            "eval3": create_score("eval3", 0.9, True),
        }

        result = aggregator.aggregate(scores)

        assert result.combined_score == 0.5
        assert result.strategy_used == "median"

    def test_even_number_of_scores(self):
        """Should handle even number of scores (average of middle two)."""
        config = MockAggregationConfig(strategy="median")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.2, True),
            "eval2": create_score("eval2", 0.4, True),
            "eval3": create_score("eval3", 0.6, True),
            "eval4": create_score("eval4", 0.8, True),
        }

        result = aggregator.aggregate(scores)

        # Median of [0.2, 0.4, 0.6, 0.8] = (0.4 + 0.6) / 2 = 0.5
        assert result.combined_score == pytest.approx(0.5)


# =============================================================================
# Failure Mode Tests
# =============================================================================


class TestFailureModes:
    """Tests for evaluator failure handling."""

    def test_ignore_mode_skips_failed(self):
        """Ignore mode should skip failed evaluators."""
        config = MockAggregationConfig(strategy="average", failure_mode="ignore")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.8, True),
            "eval2": create_score("eval2", 0.0, False, error="Crashed"),
        }

        result = aggregator.aggregate(scores)

        # Should only use eval1
        assert result.combined_score == 0.8
        assert result.num_successful == 1

    def test_zero_mode_treats_as_zero(self):
        """Zero mode should treat failed evaluators as score=0."""
        config = MockAggregationConfig(strategy="average", failure_mode="zero")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.8, True),
            "eval2": create_score("eval2", 0.6, True, error="Crashed"),
        }

        result = aggregator.aggregate(scores)

        # Average of 0.8 and 0.0 = 0.4
        assert result.combined_score == pytest.approx(0.4)

    def test_fail_mode_fails_on_error(self):
        """Fail mode should fail entire result if any evaluator fails."""
        config = MockAggregationConfig(strategy="average", failure_mode="fail")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.8, True),
            "eval2": create_score("eval2", 0.0, False, error="Crashed"),
        }

        result = aggregator.aggregate(scores)

        assert result.combined_score == 0.0
        assert result.correct is False


# =============================================================================
# Statistics Tests
# =============================================================================


class TestAggregationStatistics:
    """Tests for statistics computation."""

    def test_computes_statistics(self):
        """Should compute mean, std, min, max statistics."""
        config = MockAggregationConfig(strategy="average")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.2, True),
            "eval2": create_score("eval2", 0.4, True),
            "eval3": create_score("eval3", 0.8, True),
        }

        result = aggregator.aggregate(scores)

        assert result.score_min == 0.2
        assert result.score_max == 0.8
        assert result.score_mean == pytest.approx((0.2 + 0.4 + 0.8) / 3)
        assert result.score_std > 0  # Should have some variance

    def test_voting_statistics(self):
        """Should track voting breakdown."""
        config = MockAggregationConfig(strategy="average")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.5, True),
            "eval2": create_score("eval2", 0.5, True),
            "eval3": create_score("eval3", 0.5, False),
        }

        result = aggregator.aggregate(scores)

        assert result.votes_correct == 2
        assert result.votes_incorrect == 1
        assert result.agreement_ratio == pytest.approx(2 / 3)

    def test_counts_evaluators(self):
        """Should count total, successful, and failed evaluators."""
        config = MockAggregationConfig(strategy="average", failure_mode="ignore")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.8, True),
            "eval2": create_score("eval2", 0.6, True),
            "eval3": create_score("eval3", 0.0, False, error="Failed"),
        }

        result = aggregator.aggregate(scores)

        assert result.num_evaluators == 3
        assert result.num_successful == 2
        assert result.num_failed == 1


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Edge cases for aggregation."""

    def test_single_evaluator_average(self):
        """Single evaluator should work with average strategy."""
        config = MockAggregationConfig(strategy="average")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.75, True),
        }

        result = aggregator.aggregate(scores)

        assert result.combined_score == 0.75
        assert result.score_std == 0.0  # No variance with single value

    def test_all_evaluators_fail_with_min_evals(self):
        """Should fail if all evaluators fail and min_successful > 0."""
        config = MockAggregationConfig(
            strategy="average", failure_mode="ignore", min_successful_evals=1
        )
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.0, False, error="Crashed"),
            "eval2": create_score("eval2", 0.0, False, error="Timeout"),
        }

        result = aggregator.aggregate(scores)

        assert result.combined_score == 0.0
        assert result.correct is False

    def test_unknown_strategy_falls_back_to_average(self):
        """Unknown strategy should fall back to average."""
        config = MockAggregationConfig(strategy="unknown_strategy")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.4, True),
            "eval2": create_score("eval2", 0.6, True),
        }

        result = aggregator.aggregate(scores)

        # Should use average as fallback
        assert result.combined_score == pytest.approx(0.5)

    def test_identical_scores(self):
        """Should handle identical scores."""
        config = MockAggregationConfig(strategy="average")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.5, True),
            "eval2": create_score("eval2", 0.5, True),
            "eval3": create_score("eval3", 0.5, True),
        }

        result = aggregator.aggregate(scores)

        assert result.combined_score == 0.5
        assert result.score_std == 0.0


# =============================================================================
# Result Metadata Tests
# =============================================================================


class TestResultMetadata:
    """Tests for AggregatedResult metadata conversion."""

    def test_to_metadata_dict(self):
        """Should convert result to metadata dictionary."""
        config = MockAggregationConfig(strategy="average")
        aggregator = ScoreAggregator(config)

        scores = {
            "eval1": create_score("eval1", 0.7, True),
        }

        result = aggregator.aggregate(scores)
        metadata = result.to_metadata_dict()

        assert metadata["enabled"] is True
        assert metadata["aggregation_strategy"] == "average"
        assert metadata["aggregated"]["combined_score"] == 0.7
        assert len(metadata["evaluators"]) == 1
