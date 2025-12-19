"""Multi-evaluator ensemble for exploring evaluation space."""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, TYPE_CHECKING

from shinka.eval.agentic import AgenticEvaluator, AgenticEvaluatorResult
from shinka.eval.aggregator import ScoreAggregator, EvaluatorScore, AggregatedResult

if TYPE_CHECKING:
    from shinka.core.runner import EnsembleEvaluatorConfig, EvaluatorInstanceConfig
    from shinka.edit.types import AgentRunner

logger = logging.getLogger(__name__)


@dataclass
class SingleEvaluatorResult:
    """Result from a single evaluator in the ensemble."""

    evaluator_id: str
    evaluator_name: str
    config: "EvaluatorInstanceConfig"

    # Core results
    metrics: Dict[str, Any]
    combined_score: float
    correct: bool
    error_message: Optional[str]

    # Timing and status
    elapsed_seconds: float
    status: str  # "success" | "error" | "timeout"

    # Session metadata
    session_id: Optional[str]
    session_dir: Optional[Path]
    session_log_path: Optional[Path]

    # Prompts used (for UI display)
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None

    # Full result object
    raw_result: Optional[AgenticEvaluatorResult] = None


@dataclass
class EnsembleEvaluationResult:
    """Aggregated result from all evaluators in the ensemble."""

    # Aggregated values
    combined_score: float
    correct: bool
    aggregation_strategy: str

    # Individual results
    evaluator_results: List[SingleEvaluatorResult]

    # Statistics
    num_evaluators: int
    num_successful: int
    num_failed: int
    score_mean: float
    score_std: float
    score_min: float
    score_max: float

    # Voting breakdown
    votes_correct: int = 0
    votes_incorrect: int = 0
    agreement_ratio: float = 0.0

    # Details
    details: str = ""

    # Aggregated metadata for storage
    aggregated_result: Optional[AggregatedResult] = None

    def to_metadata_dict(self) -> Dict[str, Any]:
        """Convert to dictionary suitable for storage in Program.metadata."""
        if self.aggregated_result:
            return self.aggregated_result.to_metadata_dict()

        # Fallback if aggregated_result not set
        return {
            "enabled": True,
            "aggregation_strategy": self.aggregation_strategy,
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
                    "evaluator_id": r.evaluator_id,
                    "evaluator_name": r.evaluator_name,
                    "backend": r.config.backend,
                    "model": r.config.model,
                    "combined_score": r.combined_score,
                    "correct": r.correct,
                    "weight": r.config.weight,
                    "status": r.status,
                    "error_message": r.error_message,
                    "elapsed_seconds": r.elapsed_seconds,
                    "session_dir": str(r.session_dir) if r.session_dir else None,
                    "session_log_path": str(r.session_log_path) if r.session_log_path else None,
                    "metrics": r.metrics,
                }
                for r in self.evaluator_results
            ],
        }


class EnsembleEvaluator:
    """Orchestrates parallel execution of multiple evaluators.

    Each evaluator runs in an ephemeral workspace copy, allowing evaluators
    to run their own tests and modify files without affecting each other.
    Results are aggregated using configurable strategies.
    """

    def __init__(
        self,
        config: "EnsembleEvaluatorConfig",
        *,
        agent_runners: Optional[Dict[str, Callable]] = None,
    ) -> None:
        """Initialize the ensemble evaluator.

        Args:
            config: Ensemble configuration with evaluator specs and aggregation settings
            agent_runners: Optional dictionary mapping backend names to runner functions.
                          If not provided, imports defaults from edit modules.
        """
        self.config = config
        self._agent_runners = agent_runners or self._build_default_runners()
        self._evaluators: Dict[str, AgenticEvaluator] = {}
        self._aggregator = ScoreAggregator(config.aggregation)

    def _build_default_runners(self) -> Dict[str, Callable]:
        """Build runner functions for each backend."""
        runners: Dict[str, Callable] = {}

        try:
            from shinka.edit.codex_cli import run_codex_task
            runners["codex"] = run_codex_task
            logger.info("Codex backend available")
        except ImportError as e:
            logger.warning(f"Codex backend unavailable: {e}")

        try:
            from shinka.edit.gemini_cli import run_gemini_task
            runners["gemini"] = run_gemini_task
            logger.info("Gemini backend available")
        except ImportError as e:
            logger.warning(f"Gemini backend unavailable: {e}")

        try:
            from shinka.edit.claude_cli import run_claude_task
            runners["claude"] = run_claude_task
            logger.info("Claude backend available")
        except ImportError as e:
            logger.warning(f"Claude backend unavailable: {e}")

        try:
            from shinka.edit.shinka_agent import run_shinka_task
            runners["shinka"] = run_shinka_task
            logger.info("Shinka backend available")
        except ImportError as e:
            logger.warning(f"Shinka backend unavailable: {e}")

        try:
            from shinka.edit.jules_cli import run_jules_task
            runners["jules"] = run_jules_task
            logger.info("Jules backend available")
        except ImportError as e:
            logger.warning(f"Jules backend unavailable: {e}")

        return runners

    def _get_evaluator(
        self,
        spec: "EvaluatorInstanceConfig",
    ) -> AgenticEvaluator:
        """Get or create an AgenticEvaluator for a spec.

        Args:
            spec: Evaluator instance configuration (already resolved with defaults)

        Returns:
            AgenticEvaluator configured for the spec
        """
        evaluator_id = spec.name

        if evaluator_id not in self._evaluators:
            # Convert to AgenticEvaluatorConfig
            eval_config = spec.to_agentic_evaluator_config()

            # Get runner for this backend
            backend = spec.backend or self.config.defaults.backend or "codex"
            runner = self._agent_runners.get(backend)

            if runner is None:
                logger.warning(
                    f"No runner for backend '{backend}', falling back to codex"
                )
                runner = self._agent_runners.get("codex")

            if runner is None:
                raise RuntimeError(
                    f"No runner available for evaluator '{evaluator_id}' "
                    f"(backend: {backend})"
                )

            self._evaluators[evaluator_id] = AgenticEvaluator(
                eval_config,
                agent_runner=runner,
            )

        return self._evaluators[evaluator_id]

    def _create_ephemeral_workspace(
        self,
        source_root: Path,
        evaluator_id: str,
    ) -> Path:
        """Create an isolated workspace copy for an evaluator.

        Uses Copy-on-Write when available for efficiency.

        Args:
            source_root: Source workspace to copy
            evaluator_id: Evaluator identifier for naming

        Returns:
            Path to the ephemeral workspace
        """
        workspace = Path(tempfile.mkdtemp(
            prefix=f"shinka_eval_{evaluator_id}_",
        ))

        try:
            # Try fast copy with CoW support
            from shinka.core.fs_utils import fast_copy
            fast_copy(source_root, workspace, dirs_exist_ok=True)
        except ImportError:
            # Fallback to shutil
            shutil.copytree(source_root, workspace, dirs_exist_ok=True)

        logger.debug(f"Created ephemeral workspace for '{evaluator_id}': {workspace}")
        return workspace

    def _cleanup_ephemeral_workspace(
        self,
        workspace: Path,
        evaluator_id: str,
    ) -> None:
        """Clean up ephemeral workspace after evaluation.

        Args:
            workspace: Path to workspace to clean up
            evaluator_id: Evaluator identifier for logging
        """
        try:
            shutil.rmtree(workspace, ignore_errors=True)
            logger.debug(f"Cleaned up workspace for '{evaluator_id}': {workspace}")
        except Exception as e:
            logger.warning(f"Failed to cleanup workspace {workspace}: {e}")

    def _run_single_evaluator(
        self,
        spec: "EvaluatorInstanceConfig",
        *,
        repo_root: Path,
        eval_command: Sequence[str],
        program_path: Path,
        results_path: Path,
        metrics_path: Path,
        eval_sessions_root: Path,
        task_name: str,
        results_dir: Optional[str] = None,
        max_score: float = 1.0,
        parent_id: Optional[str] = None,
        generation: Optional[int] = None,
        patch_type: Optional[str] = None,
    ) -> SingleEvaluatorResult:
        """Run a single evaluator in its own workspace.

        Args:
            spec: Evaluator configuration
            repo_root: Source repository root
            eval_command: Command to run for evaluation
            program_path: Path to main program file
            results_path: Path to results directory
            metrics_path: Path where metrics should be written
            eval_sessions_root: Root for evaluation session logs
            task_name: Name of the task being evaluated
            results_dir: Optional results directory path
            max_score: Maximum possible score
            parent_id: Optional parent program ID
            generation: Optional generation number
            patch_type: Optional patch type used

        Returns:
            SingleEvaluatorResult with evaluation outcome
        """
        evaluator_id = spec.name
        start_time = time.monotonic()

        # Create evaluator-specific session directory
        session_uuid = uuid.uuid4().hex
        session_dir = eval_sessions_root / f"{evaluator_id}_{session_uuid}"
        session_dir.mkdir(parents=True, exist_ok=True)

        # Create ephemeral workspace
        eval_workspace = self._create_ephemeral_workspace(repo_root, evaluator_id)

        try:
            # Get evaluator instance
            evaluator = self._get_evaluator(spec)

            # Run evaluation in ephemeral workspace
            result = evaluator.evaluate(
                repo_root=eval_workspace,
                eval_command=eval_command,
                program_path=program_path,
                results_path=eval_workspace / results_path.name if results_path else eval_workspace / "results",
                metrics_path=eval_workspace / "metrics.json",
                eval_sessions_root=session_dir,
                task_name=task_name,
                results_dir=results_dir,
                eval_prompt=spec.eval_prompt,
                max_score=max_score,
                parent_id=parent_id,
                generation=generation,
                patch_type=patch_type,
            )

            elapsed = time.monotonic() - start_time

            # Extract score from metrics
            combined_score = result.metrics.get("combined_score", 0.0)
            if combined_score == 0.0:
                # Try alternative keys
                combined_score = result.metrics.get("score", 0.0)

            return SingleEvaluatorResult(
                evaluator_id=evaluator_id,
                evaluator_name=spec.name,
                config=spec,
                metrics=result.metrics,
                combined_score=combined_score,
                correct=result.correct,
                error_message=result.error_message,
                elapsed_seconds=elapsed,
                status="success" if not result.error_message else "error",
                session_id=result.session_id,
                session_dir=result.session_dir,
                session_log_path=result.session_log_path,
                system_prompt=result.system_prompt,
                user_prompt=result.user_prompt,
                raw_result=result,
            )

        except Exception as e:
            elapsed = time.monotonic() - start_time
            logger.error(f"Evaluator '{evaluator_id}' failed: {e}", exc_info=True)

            return SingleEvaluatorResult(
                evaluator_id=evaluator_id,
                evaluator_name=spec.name,
                config=spec,
                metrics={},
                combined_score=0.0,
                correct=False,
                error_message=str(e),
                elapsed_seconds=elapsed,
                status="error",
                session_id=None,
                session_dir=session_dir,
                session_log_path=session_dir / "session_log.missing",
            )

        finally:
            # Cleanup ephemeral workspace
            self._cleanup_ephemeral_workspace(eval_workspace, evaluator_id)

    def evaluate(
        self,
        *,
        repo_root: Path,
        eval_command: Sequence[str],
        program_path: Path,
        results_path: Path,
        metrics_path: Path,
        eval_sessions_root: Path,
        task_name: str,
        results_dir: Optional[str] = None,
        max_score: float = 1.0,
        parent_id: Optional[str] = None,
        generation: Optional[int] = None,
        patch_type: Optional[str] = None,
    ) -> EnsembleEvaluationResult:
        """Run all evaluators in parallel and aggregate results.

        Args:
            repo_root: Repository root directory
            eval_command: Command to run for evaluation
            program_path: Path to main program file
            results_path: Path to results directory
            metrics_path: Path where metrics should be written
            eval_sessions_root: Root for evaluation session logs
            task_name: Name of the task being evaluated
            results_dir: Optional results directory path
            max_score: Maximum possible score
            parent_id: Optional parent program ID
            generation: Optional generation number
            patch_type: Optional patch type used

        Returns:
            EnsembleEvaluationResult with aggregated scores and individual results
        """
        # Get resolved evaluator configs (with defaults merged in)
        resolved_evaluators = self.config.get_resolved_evaluators()

        if not resolved_evaluators:
            raise RuntimeError("No enabled evaluators in ensemble configuration")

        logger.info(
            f"Running ensemble evaluation with {len(resolved_evaluators)} evaluators: "
            f"{list(resolved_evaluators.keys())}"
        )

        # Determine parallelism
        max_workers = self.config.max_parallel_evaluators
        if max_workers <= 0:
            max_workers = len(resolved_evaluators)

        evaluator_results: List[SingleEvaluatorResult] = []

        # Run evaluators in parallel
        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="ensemble_eval_",
        ) as executor:
            futures = {
                executor.submit(
                    self._run_single_evaluator,
                    spec,
                    repo_root=repo_root,
                    eval_command=eval_command,
                    program_path=program_path,
                    results_path=results_path,
                    metrics_path=metrics_path,
                    eval_sessions_root=eval_sessions_root,
                    task_name=task_name,
                    results_dir=results_dir,
                    max_score=max_score,
                    parent_id=parent_id,
                    generation=generation,
                    patch_type=patch_type,
                ): spec
                for spec in resolved_evaluators.values()
            }

            for future in as_completed(futures):
                spec = futures[future]
                try:
                    result = future.result()
                    evaluator_results.append(result)
                    logger.info(
                        f"Evaluator '{spec.name}' completed: "
                        f"score={result.combined_score:.4f}, correct={result.correct}"
                    )
                except Exception as e:
                    logger.error(f"Evaluator '{spec.name}' future failed: {e}", exc_info=True)
                    # Create error result
                    evaluator_results.append(SingleEvaluatorResult(
                        evaluator_id=spec.name,
                        evaluator_name=spec.name,
                        config=spec,
                        metrics={},
                        combined_score=0.0,
                        correct=False,
                        error_message=str(e),
                        elapsed_seconds=0.0,
                        status="error",
                        session_id=None,
                        session_dir=None,
                        session_log_path=None,
                    ))

        # Convert to EvaluatorScore for aggregator
        scores: Dict[str, EvaluatorScore] = {}
        raw_results: Dict[str, AgenticEvaluatorResult] = {}

        for result in evaluator_results:
            scores[result.evaluator_id] = EvaluatorScore(
                evaluator_id=result.evaluator_id,
                evaluator_name=result.evaluator_name,
                score=result.combined_score,
                correct=result.correct,
                metrics=result.metrics,
                weight=result.config.weight,
                backend=result.config.backend,
                model=result.config.model,
                error=result.error_message,
                elapsed_seconds=result.elapsed_seconds,
                session_dir=str(result.session_dir) if result.session_dir else None,
                session_log_path=str(result.session_log_path) if result.session_log_path else None,
            )
            if result.raw_result:
                raw_results[result.evaluator_id] = result.raw_result

        # Aggregate results
        aggregated = self._aggregator.aggregate(scores, raw_results)

        logger.info(
            f"Ensemble evaluation complete: "
            f"combined_score={aggregated.combined_score:.4f}, "
            f"correct={aggregated.correct}, "
            f"strategy={aggregated.strategy_used}, "
            f"successful={aggregated.num_successful}/{aggregated.num_evaluators}"
        )

        return EnsembleEvaluationResult(
            combined_score=aggregated.combined_score,
            correct=aggregated.correct,
            aggregation_strategy=aggregated.strategy_used,
            evaluator_results=evaluator_results,
            num_evaluators=aggregated.num_evaluators,
            num_successful=aggregated.num_successful,
            num_failed=aggregated.num_failed,
            score_mean=aggregated.score_mean,
            score_std=aggregated.score_std,
            score_min=aggregated.score_min,
            score_max=aggregated.score_max,
            votes_correct=aggregated.votes_correct,
            votes_incorrect=aggregated.votes_incorrect,
            agreement_ratio=aggregated.agreement_ratio,
            details=aggregated.details,
            aggregated_result=aggregated,
        )
