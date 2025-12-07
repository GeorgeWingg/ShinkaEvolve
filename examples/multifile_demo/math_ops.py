"""Math operations for optimization."""
import math


def compute_score(x: float, y: float) -> float:
    """Compute a score based on x and y parameters.

    Goal: Maximize this score. The optimal solution achieves score > 0.99.
    """
    # Simple quadratic with a peak at (3, 7)
    score = 1.0 - 0.01 * ((x - 3) ** 2 + (y - 7) ** 2)
    return max(0.0, min(1.0, score))


def optimize_params(iterations: int = 100) -> tuple[float, float]:
    """Find optimal x, y parameters through search.

    This is a naive implementation - improve it!
    """
    best_x, best_y = 0.0, 0.0
    best_score = 0.0

    for i in range(iterations):
        # Random search in [-10, 10] range
        x = (i % 20) - 10
        y = (i // 20) - 10
        score = compute_score(x, y)
        if score > best_score:
            best_score = score
            best_x, best_y = x, y

    return best_x, best_y
