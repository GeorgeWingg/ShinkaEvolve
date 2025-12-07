"""Main entry point for the multifile optimization demo.

Goal: Optimize parameters to maximize the score.
The score function is defined in math_ops.py.
You can modify any file to improve the optimization.
"""
from math_ops import compute_score, optimize_params
from utils import format_result, validate_input


def run_optimization() -> float:
    """Run the optimization and return the best score.

    This is the main entry point called by the evaluator.
    """
    # Find optimal parameters
    best_x, best_y = optimize_params(iterations=100)

    # Validate inputs
    if not validate_input(best_x) or not validate_input(best_y):
        return 0.0

    # Compute final score
    score = compute_score(best_x, best_y)

    # Display result
    print(f"Best parameters: x={format_result(best_x)}, y={format_result(best_y)}")
    print(f"Score: {format_result(score)}")

    return score


def main() -> float:
    """Legacy main function."""
    return run_optimization()


if __name__ == "__main__":
    result = main()
    print(f"Final score: {result}")
