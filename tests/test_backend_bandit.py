"""Tests for BackendBandit and auth-aware backend selection."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
import numpy as np

from shinka.llm.backend_bandit import (
    BackendBandit,
    BackendBanditConfig,
    NoAuthenticatedBackendsError,
)
from shinka.tools.auth_status import ALL_BACKENDS


class TestBackendBanditConfig:
    """Tests for BackendBanditConfig."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = BackendBanditConfig()
        assert config.exploration_coef == 1.0
        assert config.epsilon == 0.1
        assert config.auto_decay == 0.95
        assert config.shift_by_baseline is True
        assert config.asymmetric_scaling is True
        assert config.exponential_base == pytest.approx(2.718, rel=0.01)
    
    def test_custom_config(self):
        """Test custom configuration values."""
        config = BackendBanditConfig(
            exploration_coef=2.0,
            epsilon=0.2,
            auto_decay=0.9,
        )
        assert config.exploration_coef == 2.0
        assert config.epsilon == 0.2
        assert config.auto_decay == 0.9


class TestBackendBanditInit:
    """Tests for BackendBandit initialization."""
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_init_with_default_config(self, mock_get_auth):
        """Test initialization with default config."""
        mock_get_auth.return_value = ["codex"]
        bandit = BackendBandit()
        assert bandit._n_arms == len(ALL_BACKENDS)
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_init_with_custom_config(self, mock_get_auth):
        """Test initialization with custom config."""
        mock_get_auth.return_value = ["codex"]
        config = BackendBanditConfig(exploration_coef=2.0)
        bandit = BackendBandit(config=config)
        # AsymmetricUCB stores exploration_coef as 'c'
        assert bandit._bandit.c == 2.0
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_init_with_kwargs_override(self, mock_get_auth):
        """Test initialization with kwargs overriding config."""
        mock_get_auth.return_value = ["codex"]
        config = BackendBanditConfig(exploration_coef=1.0)
        bandit = BackendBandit(config=config, exploration_coef=3.0)
        # AsymmetricUCB stores exploration_coef as 'c'
        assert bandit._bandit.c == 3.0


class TestBackendBanditAuth:
    """Tests for auth-aware backend filtering."""
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_refresh_auth(self, mock_get_auth):
        """Test refresh_auth updates cache."""
        mock_get_auth.return_value = ["codex", "shinka"]
        bandit = BackendBandit()
        bandit._cached_auth = None
        result = bandit.refresh_auth()
        assert result == ["codex", "shinka"]
        assert bandit._cached_auth == ["codex", "shinka"]
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_available_backends_uses_cache(self, mock_get_auth):
        """Test available_backends uses cached value."""
        mock_get_auth.return_value = ["codex"]
        bandit = BackendBandit()
        bandit._cached_auth = ["gemini"]
        # Should return cache, not call get_authenticated_backends
        assert bandit.available_backends == ["gemini"]
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_available_backends_refreshes_if_none(self, mock_get_auth):
        """Test available_backends refreshes if cache is None."""
        mock_get_auth.return_value = ["claude"]
        bandit = BackendBandit()
        bandit._cached_auth = None
        result = bandit.available_backends
        assert result == ["claude"]
        mock_get_auth.assert_called()


class TestBackendBanditSampling:
    """Tests for sample() method."""
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_sample_returns_available_backend(self, mock_get_auth):
        """Test sample returns one of available backends."""
        mock_get_auth.return_value = ["codex", "shinka"]
        bandit = BackendBandit()
        
        samples = [bandit.sample() for _ in range(20)]
        for s in samples:
            assert s in ["codex", "shinka"]
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_sample_raises_if_no_auth(self, mock_get_auth):
        """Test sample raises NoAuthenticatedBackendsError if no auth."""
        mock_get_auth.return_value = []
        bandit = BackendBandit()
        
        with pytest.raises(NoAuthenticatedBackendsError):
            bandit.sample()
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_sample_single_backend(self, mock_get_auth):
        """Test sample with single available backend always returns it."""
        mock_get_auth.return_value = ["gemini"]
        bandit = BackendBandit()
        
        for _ in range(10):
            assert bandit.sample() == "gemini"


class TestBackendBanditUpdate:
    """Tests for update() method."""
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_update_increments_counts(self, mock_get_auth):
        """Test update increments pull counts."""
        mock_get_auth.return_value = ["codex"]
        bandit = BackendBandit()
        
        initial_stats = bandit.get_stats()
        assert initial_stats["codex"]["n_submitted"] == 0
        
        bandit.update("codex", reward=2.6, baseline=2.5)
        updated_stats = bandit.get_stats()
        assert updated_stats["codex"]["n_completed"] == 1
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_update_with_none_reward(self, mock_get_auth):
        """Test update with None reward (failed eval)."""
        mock_get_auth.return_value = ["codex"]
        bandit = BackendBandit()
        
        # Should not raise
        bandit.update("codex", reward=None, baseline=2.5)
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_update_affects_posteriors(self, mock_get_auth):
        """Test that updates affect posterior probabilities."""
        mock_get_auth.return_value = ["codex", "shinka"]
        bandit = BackendBandit()
        
        # Give codex high rewards
        for _ in range(10):
            bandit.update("codex", reward=3.0, baseline=2.5)
        
        # Give shinka low rewards
        for _ in range(10):
            bandit.update("shinka", reward=2.5, baseline=2.5)
        
        posteriors = bandit.posterior(subset=["codex", "shinka"])
        # codex should have higher posterior after high rewards
        assert posteriors[0] > posteriors[1]


class TestBackendBanditStats:
    """Tests for statistics methods."""
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_get_stats_all_backends(self, mock_get_auth):
        """Test get_stats returns stats for all backends."""
        mock_get_auth.return_value = ["codex"]
        bandit = BackendBandit()
        
        stats = bandit.get_stats()
        assert set(stats.keys()) == set(ALL_BACKENDS)
        for backend in ALL_BACKENDS:
            assert "n_submitted" in stats[backend]
            assert "n_completed" in stats[backend]
            assert "s" in stats[backend]
            assert "divs" in stats[backend]
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_get_summary(self, mock_get_auth):
        """Test get_summary returns WebUI-ready dict."""
        mock_get_auth.return_value = ["codex", "shinka"]
        bandit = BackendBandit()
        
        summary = bandit.get_summary()
        assert "available_backends" in summary
        assert "posteriors" in summary
        assert "total_pulls" in summary
        assert "per_backend_stats" in summary
        assert set(summary["available_backends"]) == {"codex", "shinka"}
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_get_summary_empty_when_no_auth(self, mock_get_auth):
        """Test get_summary handles no auth gracefully."""
        mock_get_auth.return_value = []
        bandit = BackendBandit()
        
        summary = bandit.get_summary()
        assert summary["available_backends"] == []
        assert summary["posteriors"] == {}


class TestBackendBanditDecay:
    """Tests for decay() method."""
    
    @patch("shinka.llm.backend_bandit.get_authenticated_backends")
    def test_decay_reduces_scores(self, mock_get_auth):
        """Test decay applies to score values."""
        mock_get_auth.return_value = ["codex"]
        bandit = BackendBandit()
        
        # Add some observations first
        bandit.update("codex", reward=3.0, baseline=2.5)
        initial_divs = bandit._bandit.divs.copy()
        
        bandit.decay()
        # divs should decrease after decay
        assert np.all(bandit._bandit.divs <= initial_divs)
