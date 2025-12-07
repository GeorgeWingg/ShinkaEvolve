"""Backend bandit for dynamic agentic backend selection.

This module provides a multi-armed bandit for selecting between agentic backends
(Codex, Gemini, Claude, ShinkaAgent) based on their performance during evolution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from shinka.llm.dynamic_sampling import AsymmetricUCB
from shinka.tools.auth_status import get_authenticated_backends, ALL_BACKENDS

# Alias for backward compatibility with runner.py imports
BACKEND_ARMS = ALL_BACKENDS


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
    
    def refresh_auth(self) -> List[str]:
        """Refresh cached auth status.
        
        Returns:
            List of authenticated backend names (filtered by allowed_backends if set)
        """
        all_authenticated = get_authenticated_backends()
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
        
        # Normalize to ensure sum = 1
        posteriors = posteriors / posteriors.sum() if posteriors.sum() > 0 else np.ones(len(available)) / len(available)
        
        # Sample according to posteriors
        idx = np.random.choice(len(available), p=posteriors)
        return available[idx]
    
    def update(
        self,
        backend: str,
        reward: Optional[float],
        baseline: Optional[float] = None,
    ) -> None:
        """Update bandit after evaluation.
        
        Args:
            backend: The backend that was used
            reward: The score achieved (None if evaluation failed)
            baseline: The parent's score for baseline shift
        """
        self._bandit.update(arm=backend, reward=reward, baseline=baseline)
    
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
