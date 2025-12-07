"""Tests for BackendBandit with allowed_backends filtering.

The BackendBandit currently supports backend-level selection (codex, gemini, claude, shinka)
with allowed_backends filtering. Multi-model support (backend:model) is a future feature.
"""
import pytest
from unittest.mock import patch
from shinka.llm.backend_bandit import BackendBandit, BackendBanditConfig, NoAuthenticatedBackendsError


class TestBackendBandit:
    def test_legacy_sampling(self):
        """Test bandit with simple backend names (legacy mode)."""
        config = BackendBanditConfig(allowed_backends=["codex", "gemini"])
        
        with patch("shinka.llm.backend_bandit.get_authenticated_backends", return_value=["codex", "gemini"]):
            bandit = BackendBandit(config=config)
            
            # Verify available arms
            assert set(bandit.available_backends) == {"codex", "gemini"}
            
            # Sample
            sampled = bandit.sample()
            assert isinstance(sampled, str)
            assert sampled in ["codex", "gemini"]
            
            # Update
            bandit.update(sampled, reward=1.0, baseline=0.5)
            
            # Check stats
            stats = bandit.get_stats()
            assert stats[sampled]["n_completed"] == 1.0

    def test_allowed_backends_filtering(self):
        """Test that allowed_backends restricts which backends can be sampled."""
        # Only allow codex and gemini, even if claude is also authenticated
        config = BackendBanditConfig(allowed_backends=["codex", "gemini"])
        
        with patch("shinka.llm.backend_bandit.get_authenticated_backends", return_value=["codex", "gemini", "claude"]):
            bandit = BackendBandit(config=config)
            
            # Should only have codex and gemini, not claude
            assert set(bandit.available_backends) == {"codex", "gemini"}
            
            # Sample multiple times to verify filtering
            for _ in range(10):
                sampled = bandit.sample()
                assert sampled in ["codex", "gemini"]
                assert sampled != "claude"

    def test_auth_filtering(self):
        """Test that unauthenticated backends are filtered out."""
        # Allow all backends in config
        config = BackendBanditConfig(allowed_backends=None)
        
        # Only codex is authenticated
        with patch("shinka.llm.backend_bandit.get_authenticated_backends", return_value=["codex"]):
            bandit = BackendBandit(config=config)
            
            assert bandit.available_backends == ["codex"]
            
            # Should only sample codex
            sampled = bandit.sample()
            assert sampled == "codex"

    def test_runner_integration_logic(self):
        """Simulate the logic used in runner.py to ensure it works with current API."""
        # Setup bandit with allowed backends
        config = BackendBanditConfig(allowed_backends=["codex", "gemini"])
        
        with patch("shinka.llm.backend_bandit.get_authenticated_backends", return_value=["codex", "gemini"]):
            bandit = BackendBandit(config=config)
            
            # 1. Sampling (Runner _run_agentic_patch logic)
            selected_backend = bandit.sample()
            
            # Current API returns string, not tuple
            assert isinstance(selected_backend, str)
            assert selected_backend in ["codex", "gemini"]
            
            # 2. Metadata storage simulation
            metadata = {
                "agent_backend": selected_backend,
            }
            
            # 3. Update (Runner _finalize_job logic)
            agent_backend = metadata.get("agent_backend")
            
            bandit.update(agent_backend, reward=1.0, baseline=0.0)
            assert bandit.get_stats()[agent_backend]["n_completed"] == 1.0
            
    def test_no_authenticated_backends_raises(self):
        """Test that sampling with no auth raises NoAuthenticatedBackendsError."""
        config = BackendBanditConfig()
        
        with patch("shinka.llm.backend_bandit.get_authenticated_backends", return_value=[]):
            bandit = BackendBandit(config=config)
            
            with pytest.raises(NoAuthenticatedBackendsError):
                bandit.sample()


if __name__ == "__main__":
    pytest.main([__file__])