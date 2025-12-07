"""Evaluator for the multifile demo using shinka.core.run_shinka_eval."""
import os
from typing import Dict, Any, List, Tuple, Optional

from shinka.core import run_shinka_eval


def validate_result(result: float) -> Tuple[bool, Optional[str]]:
    """Validate the optimization result."""
    if not isinstance(result, (int, float)):
        return False, f"Expected numeric result, got {type(result)}"
    if result < 0 or result > 1:
        return False, f"Score {result} out of valid range [0, 1]"
    return True, None


def get_run_kwargs(run_index: int) -> Dict[str, Any]:
    """No extra kwargs needed."""
    return {}


def aggregate_metrics(results: List[float], results_dir: str) -> Dict[str, Any]:
    """Aggregate metrics from runs."""
    if not results:
        return {"combined_score": 0.0, "error": "No results"}

    score = results[0]
    return {
        "combined_score": float(score),
        "score": float(score),
    }


def main(program_path: str, results_dir: str):
    """Run evaluation using shinka.core."""
    print(f"Evaluating program: {program_path}")
    print(f"Saving results to: {results_dir}")
    os.makedirs(results_dir, exist_ok=True)

    def _aggregator(results: List[float]) -> Dict[str, Any]:
        return aggregate_metrics(results, results_dir)

    metrics, correct, error_msg = run_shinka_eval(
        program_path=program_path,
        results_dir=results_dir,
        experiment_fn_name="run_optimization",
        num_runs=1,
        get_experiment_kwargs=get_run_kwargs,
        validate_fn=validate_result,
        aggregate_metrics_fn=_aggregator,
    )

    if correct:
        print("Evaluation completed successfully.")
    else:
        print(f"Evaluation failed: {error_msg}")

    print(f"Metrics: {metrics}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--program_path", type=str, default="initial.py")
    parser.add_argument("--results_dir", type=str, default="results")
    args = parser.parse_args()
    main(args.program_path, args.results_dir)
