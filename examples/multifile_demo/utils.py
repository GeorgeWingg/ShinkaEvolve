"""Utility functions for the multifile demo."""


def format_result(value: float, precision: int = 4) -> str:
    """Format a numeric result for display."""
    return f"{value:.{precision}f}"


def validate_input(x: float) -> bool:
    """Validate that input is within acceptable range."""
    return -100 <= x <= 100
