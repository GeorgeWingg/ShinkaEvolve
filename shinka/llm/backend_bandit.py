"""Backend bandit for dynamic agentic backend selection.

This module provides a multi-armed bandit for selecting between agentic backends
(Codex, Gemini, Claude, ShinkaAgent) based on their performance during evolution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from shinka.llm.dynamic_sampling import AsymmetricUCB
from shinka.tools.auth_status import get_authenticated_backends, ALL_BACKENDS

# Alias for backward compatibility with runner.py imports
BACKEND_ARMS = ALL_BACKENDS


def _safe_normalize_probabilities(probs: np.ndarray) -> np.ndarray:
    """Safely normalize probabilities, handling edge cases.

    Handles:
    - Zero sum
    - NaN values
    - Inf values
    - Negative values
    - Very small sums that would cause overflow

    Returns uniform distribution if normalization fails.

    Args:
        probs: Array of probability values (may be unnormalized)

    Returns:
        Normalized probability array that sums to 1.0
    """
    n = len(probs)
    if n == 0:
        return probs  # Empty array case

    uniform = np.ones(n, dtype=np.float64) / n

    # Check for NaN or Inf - fall back to uniform
    if not np.all(np.isfinite(probs)):
        return uniform

    # Clip negative values to 0
    probs = np.clip(probs, 0.0, None)

    total = probs.sum()

    # Check if total is valid for division
    if total <= 0.0 or not np.isfinite(total):
        return uniform

    # Normalize
    normalized = probs / total

    # Post-normalization safety: ensure result is valid
    # This catches edge cases like total being very small (producing Inf)
    if not np.all(np.isfinite(normalized)):
        return uniform

    # Ensure exactly sums to 1.0 (floating point can drift slightly)
    # Re-normalize after any adjustments
    final_sum = normalized.sum()
    if final_sum > 0 and np.isfinite(final_sum):
        normalized = normalized / final_sum
    else:
        return uniform

    return normalized


class NoAuthenticatedBackendsError(Exception):
    """Raised when no backends are authenticated."""
    pass


@dataclass
class BackendBanditConfig:
    """Configuration for backend bandit selection."""
    
    exploration_coef: float = 1.0
    epsilon: float = 0.1  # Higher epsilon for more exploration with few arms
    auto_decay: float = 0.95
    shift_by_baseline: bool = True
    asymmetric_scaling: bool = True
    exponential_base: float = 2.718  # e
    allowed_backends: Optional[List[str]] = None  # If set, only use these backends


class BackendBandit:
    """Multi-armed bandit for agentic backend selection.
    
    Wraps AsymmetricUCB with auth-aware arm filtering. Only samples from
    backends that are currently authenticated.
    
    Usage:
        bandit = BackendBandit()
        backend = bandit.sample()  # Returns 'codex', 'gemini', 'claude', or 'shinka'
        # ... run mutation with backend ...
        bandit.update(backend, reward=score, baseline=parent_score)
    """
    
    def __init__(
        self,
        config: Optional[BackendBanditConfig] = None,
        **kwargs: Any,
    ):
        """Initialize the backend bandit.
        
        Args:
            config: BackendBanditConfig with hyperparameters
            **kwargs: Override config values
        """
        if config is None:
            config = BackendBanditConfig()
        
        # Allow kwargs to override config
        exploration_coef = kwargs.get("exploration_coef", config.exploration_coef)
        epsilon = kwargs.get("epsilon", config.epsilon)
        auto_decay = kwargs.get("auto_decay", config.auto_decay)
        shift_by_baseline = kwargs.get("shift_by_baseline", config.shift_by_baseline)
        asymmetric_scaling = kwargs.get("asymmetric_scaling", config.asymmetric_scaling)
        exponential_base = kwargs.get("exponential_base", config.exponential_base)
        allowed_backends = kwargs.get("allowed_backends", config.allowed_backends)
        
        self._bandit = AsymmetricUCB(
            arm_names=ALL_BACKENDS,
            exploration_coef=exploration_coef,
            epsilon=epsilon,
            auto_decay=auto_decay,
            shift_by_baseline=shift_by_baseline,
            asymmetric_scaling=asymmetric_scaling,
            exponential_base=exponential_base,
        )
        self._cached_auth: Optional[List[str]] = None
        self._allowed_backends: Optional[List[str]] = allowed_backends
        self._n_arms = len(ALL_BACKENDS)
    
    def refresh_auth(self, *, skip_cache: bool = False) -> List[str]:
        """Refresh cached auth status.

        Args:
            skip_cache: If True, bypass auth status cache and check fresh.

        Returns:
            List of authenticated backend names (filtered by allowed_backends if set)
        """
        all_authenticated = get_authenticated_backends(skip_cache=skip_cache)
        # Filter by allowed_backends if configured
        if self._allowed_backends:
            self._cached_auth = [b for b in all_authenticated if b in self._allowed_backends]
        else:
            self._cached_auth = all_authenticated
        return self._cached_auth
    
    @property
    def available_backends(self) -> List[str]:
        """Return backends that are currently authenticated and allowed."""
        if self._cached_auth is None:
            self.refresh_auth()
        return self._cached_auth or []
    
    def sample(self) -> str:
        """Sample a backend from the bandit, filtering by auth.
        
        Returns:
            Backend name (one of 'codex', 'gemini', 'claude', 'shinka')
            
        Raises:
            NoAuthenticatedBackendsError: If no backends are authenticated
        """
        available = self.available_backends
        if not available:
            raise NoAuthenticatedBackendsError(
                "No authenticated backends available. "
                "Run `codex login`, `gemini auth login`, `claude login`, "
                "or set API keys for ShinkaAgent."
            )
        
        # Get posteriors for all arms
        full_posteriors = self._bandit.posterior(subset=available)
        
        # Extract only the posteriors for available backends
        # posterior() returns array of size n_arms with zeros for non-subset arms
        available_indices = [ALL_BACKENDS.index(b) for b in available]
        posteriors = np.array([full_posteriors[i] for i in available_indices])
        
        # Safely normalize probabilities (handles NaN, Inf, zero sum, etc.)
        posteriors = _safe_normalize_probabilities(posteriors)

        # Sample according to posteriors
        idx = np.random.choice(len(available), p=posteriors)
        return available[idx]
    
    def update(
        self,
        backend: str,
        reward: Optional[float],
        baseline: Optional[float] = None,
    ) -> Optional[Tuple[float, float]]:
        """Update bandit after evaluation.

        Args:
            backend: The backend that was used
            reward: The score achieved (None if evaluation failed)
            baseline: The parent's score for baseline shift

        Returns:
            Tuple of (normalized_score, baseline) if update succeeded, None otherwise
        """
        result = self._bandit.update(arm=backend, reward=reward, baseline=baseline)
        return result
    
    def posterior(self, subset: Optional[List[str]] = None) -> np.ndarray:
        """Get selection probabilities for backends.
        
        Args:
            subset: List of backends to include (default: authenticated backends)
            
        Returns:
            Array of probabilities for each backend in subset
        """
        if subset is None:
            subset = self.available_backends
        
        full_posteriors = self._bandit.posterior(subset=subset)
        
        # Extract only the posteriors for the subset
        subset_indices = [ALL_BACKENDS.index(b) for b in subset]
        posteriors = np.array([full_posteriors[i] for i in subset_indices])
        
        return posteriors
    
    def sample_with_fallback(self, exclude: Optional[List[str]] = None) -> str:
        """Sample a backend, excluding specified ones (for fallback after failures).

        This is used when a backend was initially selected but became unavailable.
        It samples from remaining authenticated backends.

        Args:
            exclude: List of backend names to exclude from selection

        Returns:
            Backend name from available backends not in exclude list

        Raises:
            NoAuthenticatedBackendsError: If no alternative backends are available
        """
        # Refresh auth to get latest available backends
        available = self.refresh_auth(skip_cache=True)

        # Filter out excluded backends
        if exclude:
            available = [b for b in available if b not in exclude]

        if not available:
            excluded_str = ", ".join(exclude) if exclude else "none"
            raise NoAuthenticatedBackendsError(
                f"No alternative backends available after excluding: {excluded_str}. "
                "All authenticated backends have been tried."
            )

        # Get posteriors for available backends and sample
        full_posteriors = self._bandit.posterior(subset=available)
        available_indices = [ALL_BACKENDS.index(b) for b in available]
        posteriors = np.array([full_posteriors[i] for i in available_indices])

        # Safely normalize probabilities (handles NaN, Inf, zero sum, etc.)
        posteriors = _safe_normalize_probabilities(posteriors)

        # Sample
        idx = np.random.choice(len(available), p=posteriors)
        return available[idx]

    def decay(self) -> None:
        """Apply decay to exploration parameters."""
        if hasattr(self._bandit, 'auto_decay') and self._bandit.auto_decay:
            self._bandit.decay(self._bandit.auto_decay)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get per-backend statistics.
        
        Returns:
            Dict with per-backend stats (pulls, rewards, etc.)
        """
        stats = {}
        for i, backend in enumerate(ALL_BACKENDS):
            stats[backend] = {
                "n_submitted": self._bandit.n_submitted[i],
                "n_completed": self._bandit.n_completed[i],
                "s": float(self._bandit.s[i]),
                "divs": float(self._bandit.divs[i]),
            }
        return stats
    
    def get_summary(self) -> Dict[str, Any]:
        """Return summary for WebUI display.
        
        Returns:
            Dict with available backends, posteriors, and stats
        """
        available = self.available_backends
        posteriors = self.posterior(subset=available) if available else []
        
        return {
            "available_backends": available,
            "posteriors": dict(zip(available, posteriors.tolist())) if len(posteriors) > 0 else {},
            "total_pulls": int(sum(self._bandit.n_completed)),
            "per_backend_stats": self.get_stats(),
        }
