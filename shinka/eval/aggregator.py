"""Score aggregation engine for multi-evaluator ensemble."""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from shinka.core.runner import AggregationConfig
    from shinka.eval.agentic import AgenticEvaluatorResult

logger = logging.getLogger(__name__)


@dataclass
class EvaluatorScore:
    """Score from a single evaluator in the ensemble."""

    evaluator_id: str
    evaluator_name: str
    score: float
    correct: bool
    metrics: Dict[str, Any]
    weight: float
    backend: Optional[str] = None
    model: Optional[str] = None
    error: Optional[str] = None
    elapsed_seconds: float = 0.0
    session_dir: Optional[str] = None
    session_log_path: Optional[str] = None


@dataclass
class AggregatedResult:
    """Result of aggregating multiple evaluator scores."""

    combined_score: float
    correct: bool
    strategy_used: str

    # Individual evaluator scores
    individual_scores: Dict[str, EvaluatorScore]

    # Statistics
    num_evaluators: int
    num_successful: int
    num_failed: int
    score_mean: float
    score_std: float
    score_min: float
    score_max: float

    # Voting breakdown (for majority_vote)
    votes_correct: int = 0
    votes_incorrect: int = 0
    agreement_ratio: float = 0.0

    # Human-readable explanation
    details: str = ""

    # Full results for inspection (populated by caller)
    evaluator_results: Dict[str, "AgenticEvaluatorResult"] = field(default_factory=dict)

    def to_metadata_dict(self) -> Dict[str, Any]:
        """Convert to dictionary suitable for storage in Program.metadata."""
        return {
            "enabled": True,
            "aggregation_strategy": self.strategy_used,
            "aggregated": {
                "combined_score": self.combined_score,
                "correct": self.correct,
                "score_mean": self.score_mean,
                "score_std": self.score_std,
                "score_min": self.score_min,
                "score_max": self.score_max,
                "num_evaluators": self.num_evaluators,
                "num_successful": self.num_successful,
                "num_failed": self.num_failed,
                "votes_correct": self.votes_correct,
                "votes_incorrect": self.votes_incorrect,
                "agreement_ratio": self.agreement_ratio,
                "details": self.details,
            },
            "evaluators": [
                {
                    "evaluator_id": score.evaluator_id,
                    "evaluator_name": score.evaluator_name,
                    "backend": score.backend,
                    "model": score.model,
                    "combined_score": score.score,
                    "correct": score.correct,
                    "weight": score.weight,
                    "status": "error" if score.error else "success",
                    "error_message": score.error,
                    "elapsed_seconds": score.elapsed_seconds,
                    "session_dir": score.session_dir,
                    "session_log_path": score.session_log_path,
                    "metrics": score.metrics,
                }
                for score in self.individual_scores.values()
            ],
        }


class ScoreAggregator:
    """Aggregates scores from multiple evaluators using configurable strategies."""

    def __init__(self, config: "AggregationConfig"):
        self.config = config

    def aggregate(
        self,
        scores: Dict[str, EvaluatorScore],
        results: Optional[Dict[str, "AgenticEvaluatorResult"]] = None,
    ) -> AggregatedResult:
        """Aggregate scores based on configured strategy.

        Args:
            scores: Dictionary mapping evaluator_id to EvaluatorScore
            results: Optional dictionary of full AgenticEvaluatorResult objects

        Returns:
            AggregatedResult with combined score and statistics
        """
        results = results or {}

        # Partition scores into successful and failed
        successful = {name: s for name, s in scores.items() if s.error is None}
        failed = {name: s for name, s in scores.items() if s.error is not None}

        # Handle failure modes
        if self.config.failure_mode == "zero":
            # Treat failed evaluators as score=0, correct=False
            for name, s in failed.items():
                s.score = 0.0
                s.correct = False
            successful.update(failed)
            failed = {}
        elif self.config.failure_mode == "fail":
            # Fail if any required evaluator failed
            if failed:
                return self._create_failed_result(
                    scores,
                    results,
                    f"Evaluator(s) failed: {list(failed.keys())}",
                )

        # Check minimum successful requirement
        if len(successful) < self.config.min_successful_evals:
            return self._create_failed_result(
                scores,
                results,
                f"Insufficient successful evaluators: {len(successful)} < {self.config.min_successful_evals}",
            )

        # Dispatch to strategy implementation
        strategy = self.config.strategy
        if strategy == "best_score":
            return self._best_score(successful, failed, scores, results)
        elif strategy == "worst_case":
            return self._worst_case(successful, failed, scores, results)
        elif strategy == "average":
            return self._average(successful, failed, scores, results)
        elif strategy == "weighted_average":
            return self._weighted_average(successful, failed, scores, results)
        elif strategy == "majority_vote":
            return self._majority_vote(successful, failed, scores, results)
        elif strategy == "median":
            return self._median(successful, failed, scores, results)
        else:
            logger.warning(f"Unknown aggregation strategy '{strategy}', falling back to average")
            return self._average(successful, failed, scores, results)

    def _create_failed_result(
        self,
        scores: Dict[str, EvaluatorScore],
        results: Dict[str, "AgenticEvaluatorResult"],
        details: str,
    ) -> AggregatedResult:
        """Create a failed aggregation result."""
        return AggregatedResult(
            combined_score=0.0,
            correct=False,
            strategy_used=self.config.strategy,
            individual_scores=scores,
            num_evaluators=len(scores),
            num_successful=0,
            num_failed=len(scores),
            score_mean=0.0,
            score_std=0.0,
            score_min=0.0,
            score_max=0.0,
            votes_correct=0,
            votes_incorrect=len(scores),
            agreement_ratio=0.0,
            details=details,
            evaluator_results=results,
        )

    def _compute_statistics(
        self,
        successful: Dict[str, EvaluatorScore],
        failed: Dict[str, EvaluatorScore],
        scores: Dict[str, EvaluatorScore],
    ) -> Dict[str, float]:
        """Compute common statistics from successful scores."""
        values = [s.score for s in successful.values()]

        if not values:
            return {
                "num_evaluators": len(scores),
                "num_successful": 0,
                "num_failed": len(failed),
                "score_mean": 0.0,
                "score_std": 0.0,
                "score_min": 0.0,
                "score_max": 0.0,
            }

        return {
            "num_evaluators": len(scores),
            "num_successful": len(successful),
            "num_failed": len(failed),
            "score_mean": statistics.mean(values),
            "score_std": statistics.stdev(values) if len(values) > 1 else 0.0,
            "score_min": min(values),
            "score_max": max(values),
        }

    def _compute_voting(
        self, successful: Dict[str, EvaluatorScore]
    ) -> Dict[str, Any]:
        """Compute voting statistics."""
        votes_correct = sum(1 for s in successful.values() if s.correct)
        votes_incorrect = len(successful) - votes_correct
        agreement_ratio = max(votes_correct, votes_incorrect) / len(successful) if successful else 0.0

        return {
            "votes_correct": votes_correct,
            "votes_incorrect": votes_incorrect,
            "agreement_ratio": agreement_ratio,
        }

    def _best_score(
        self,
        successful: Dict[str, EvaluatorScore],
        failed: Dict[str, EvaluatorScore],
        scores: Dict[str, EvaluatorScore],
        results: Dict[str, "AgenticEvaluatorResult"],
    ) -> AggregatedResult:
        """Return the best (maximum) score."""
        best_id = max(successful, key=lambda n: successful[n].score)
        best = successful[best_id]

        stats = self._compute_statistics(successful, failed, scores)
        voting = self._compute_voting(successful)

        return AggregatedResult(
            combined_score=best.score,
            correct=best.correct,
            strategy_used="best_score",
            individual_scores=scores,
            details=f"Best score from '{best_id}': {best.score:.4f}",
            evaluator_results=results,
            **stats,
            **voting,
        )

    def _worst_case(
        self,
        successful: Dict[str, EvaluatorScore],
        failed: Dict[str, EvaluatorScore],
        scores: Dict[str, EvaluatorScore],
        results: Dict[str, "AgenticEvaluatorResult"],
    ) -> AggregatedResult:
        """Return the worst (minimum) score - conservative estimate."""
        worst_id = min(successful, key=lambda n: successful[n].score)
        worst = successful[worst_id]

        # Correct only if ALL evaluators agree
        all_correct = all(s.correct for s in successful.values())

        stats = self._compute_statistics(successful, failed, scores)
        voting = self._compute_voting(successful)

        return AggregatedResult(
            combined_score=worst.score,
            correct=all_correct,
            strategy_used="worst_case",
            individual_scores=scores,
            details=f"Worst-case score from '{worst_id}': {worst.score:.4f} (all_correct={all_correct})",
            evaluator_results=results,
            **stats,
            **voting,
        )

    def _average(
        self,
        successful: Dict[str, EvaluatorScore],
        failed: Dict[str, EvaluatorScore],
        scores: Dict[str, EvaluatorScore],
        results: Dict[str, "AgenticEvaluatorResult"],
    ) -> AggregatedResult:
        """Return the arithmetic mean of scores."""
        values = [s.score for s in successful.values()]
        avg_score = statistics.mean(values)

        # Correct if majority agree
        votes_correct = sum(1 for s in successful.values() if s.correct)
        majority_correct = votes_correct > len(successful) / 2

        stats = self._compute_statistics(successful, failed, scores)
        voting = self._compute_voting(successful)

        return AggregatedResult(
            combined_score=avg_score,
            correct=majority_correct,
            strategy_used="average",
            individual_scores=scores,
            details=f"Average of {len(successful)} scores: {avg_score:.4f}",
            evaluator_results=results,
            **stats,
            **voting,
        )

    def _weighted_average(
        self,
        successful: Dict[str, EvaluatorScore],
        failed: Dict[str, EvaluatorScore],
        scores: Dict[str, EvaluatorScore],
        results: Dict[str, "AgenticEvaluatorResult"],
    ) -> AggregatedResult:
        """Return weighted mean of scores."""
        total_weight = sum(s.weight for s in successful.values())
        weighted_sum = sum(s.score * s.weight for s in successful.values())
        avg_score = weighted_sum / total_weight if total_weight > 0 else 0.0

        # Weighted vote for correctness
        correct_weight = sum(s.weight for s in successful.values() if s.correct)
        majority_correct = correct_weight > total_weight / 2

        stats = self._compute_statistics(successful, failed, scores)
        voting = self._compute_voting(successful)

        return AggregatedResult(
            combined_score=avg_score,
            correct=majority_correct,
            strategy_used="weighted_average",
            individual_scores=scores,
            details=f"Weighted average: {avg_score:.4f} (total weight: {total_weight:.2f})",
            evaluator_results=results,
            **stats,
            **voting,
        )

    def _majority_vote(
        self,
        successful: Dict[str, EvaluatorScore],
        failed: Dict[str, EvaluatorScore],
        scores: Dict[str, EvaluatorScore],
        results: Dict[str, "AgenticEvaluatorResult"],
    ) -> AggregatedResult:
        """Consensus on correctness, then average score of majority camp."""
        votes_correct = sum(1 for s in successful.values() if s.correct)
        total = len(successful)

        majority_correct = votes_correct / total >= self.config.vote_threshold

        # Average score from the majority camp
        if majority_correct:
            majority_scores = [s.score for s in successful.values() if s.correct]
        else:
            majority_scores = [s.score for s in successful.values() if not s.correct]

        avg_score = statistics.mean(majority_scores) if majority_scores else 0.0

        stats = self._compute_statistics(successful, failed, scores)

        return AggregatedResult(
            combined_score=avg_score,
            correct=majority_correct,
            strategy_used="majority_vote",
            individual_scores=scores,
            votes_correct=votes_correct,
            votes_incorrect=total - votes_correct,
            agreement_ratio=votes_correct / total if total > 0 else 0.0,
            details=f"Majority vote: {votes_correct}/{total} agree on correct={majority_correct}",
            evaluator_results=results,
            **stats,
        )

    def _median(
        self,
        successful: Dict[str, EvaluatorScore],
        failed: Dict[str, EvaluatorScore],
        scores: Dict[str, EvaluatorScore],
        results: Dict[str, "AgenticEvaluatorResult"],
    ) -> AggregatedResult:
        """Return the median score - robust to outliers."""
        values = [s.score for s in successful.values()]
        median_score = statistics.median(values)

        # Correct if majority agree
        votes_correct = sum(1 for s in successful.values() if s.correct)
        majority_correct = votes_correct > len(successful) / 2

        stats = self._compute_statistics(successful, failed, scores)
        voting = self._compute_voting(successful)

        return AggregatedResult(
            combined_score=median_score,
            correct=majority_correct,
            strategy_used="median",
            individual_scores=scores,
            details=f"Median of {len(successful)} scores: {median_score:.4f}",
            evaluator_results=results,
            **stats,
            **voting,
        )
