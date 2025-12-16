"""Tests for the BanditHistory cross-session persistence module."""

import json
import pytest
import tempfile
from pathlib import Path
from datetime import datetime

from shinka.llm.bandit_history import (
    BanditHistory,
    BanditHistoryData,
    BanditInteraction,
    BackendGlobalStats,
    RunSummary,
)


@pytest.fixture
def temp_history_path():
    """Create a temporary directory for history file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test_bandit_history.json"


@pytest.fixture
def history(temp_history_path):
    """Create a fresh BanditHistory instance for testing."""
    # Reset singleton
    BanditHistory.reset_instance()
    return BanditHistory.get_instance(temp_history_path)


class TestBanditInteraction:
    def test_to_dict_roundtrip(self):
        interaction = BanditInteraction(
            timestamp="2024-01-01T00:00:00",
            run_id="test-run",
            run_name="Test Run",
            backend="codex",
            reward=0.85,
            baseline=0.80,
            improvement=0.05,
            generation=1,
            task_name="test_task",
        )
        d = interaction.to_dict()
        restored = BanditInteraction.from_dict(d)
        assert restored.backend == interaction.backend
        assert restored.reward == interaction.reward
        assert restored.improvement == interaction.improvement


class TestBackendGlobalStats:
    def test_update_with_reward(self):
        stats = BackendGlobalStats()
        stats.update(reward=0.85, baseline=0.80)
        
        assert stats.total_selections == 1
        assert stats.total_completions == 1
        assert stats.total_successes == 1  # reward > baseline
        assert stats.total_failures == 0
        assert stats.avg_reward == 0.85
        assert stats.win_rate == 1.0
    
    def test_update_with_failure(self):
        stats = BackendGlobalStats()
        stats.update(reward=0.70, baseline=0.80)
        
        assert stats.total_selections == 1
        assert stats.total_failures == 1
        assert stats.win_rate == 0.0
    
    def test_update_with_none_reward(self):
        stats = BackendGlobalStats()
        stats.update(reward=None, baseline=0.80)
        
        assert stats.total_selections == 1
        assert stats.total_completions == 0


class TestBanditHistory:
    def test_singleton_pattern(self, temp_history_path):
        BanditHistory.reset_instance()
        h1 = BanditHistory.get_instance(temp_history_path)
        h2 = BanditHistory.get_instance()
        assert h1 is h2
    
    def test_record_interaction(self, history):
        history.record_interaction(
            run_id="test-run-1",
            run_name="Test Run",
            backend="codex",
            reward=0.85,
            baseline=0.80,
            generation=1,
            task_name="test",
        )
        
        summary = history.get_summary()
        assert summary["total_interactions"] == 1
        assert summary["total_runs"] == 1
        assert "codex" in summary["global_stats"]
    
    def test_get_priors(self, history):
        # Record some interactions
        history.record_interaction(
            run_id="test-run",
            run_name="Test",
            backend="codex",
            reward=0.85,
            baseline=0.80,
            generation=1,
        )
        history.record_interaction(
            run_id="test-run",
            run_name="Test",
            backend="codex",
            reward=0.90,
            baseline=0.85,
            generation=2,
        )
        
        priors = history.get_priors()
        assert "codex" in priors
        assert priors["codex"]["alpha"] > 1  # 2 successes + 1 smoothing
        assert priors["codex"]["beta"] == 1  # 0 failures + 1 smoothing
    
    def test_clear_history(self, history):
        history.record_interaction(
            run_id="test-run",
            run_name="Test",
            backend="gemini",
            reward=0.75,
            baseline=0.70,
            generation=1,
        )
        
        assert history.get_summary()["total_interactions"] == 1
        
        history.clear_history()
        
        assert history.get_summary()["total_interactions"] == 0
    
    def test_persistence(self, temp_history_path):
        # Create and populate history
        BanditHistory.reset_instance()
        h1 = BanditHistory.get_instance(temp_history_path)
        h1.record_interaction(
            run_id="persist-test",
            run_name="Persistence Test",
            backend="claude",
            reward=0.88,
            baseline=0.82,
            generation=1,
        )
        
        # Verify file exists
        assert temp_history_path.exists()
        
        # Create new instance (simulates server restart)
        BanditHistory.reset_instance()
        h2 = BanditHistory.get_instance(temp_history_path)
        
        # Should load from disk
        summary = h2.get_summary()
        assert summary["total_interactions"] == 1
        assert "claude" in summary["global_stats"]
    
    def test_finalize_run(self, history):
        history.record_interaction(
            run_id="final-test",
            run_name="Finalize Test",
            backend="shinka",
            reward=0.92,
            baseline=0.88,
            generation=1,
        )
        
        # Finalize should set end_time
        history.finalize_run("final-test")
        
        summary = history.get_summary()
        assert summary["runs"]["final-test"]["end_time"] is not None
    
    def test_daily_stats(self, history):
        # Record interactions
        for i in range(5):
            history.record_interaction(
                run_id="daily-test",
                run_name="Daily Test",
                backend="codex" if i % 2 == 0 else "gemini",
                reward=0.8 + i * 0.02,
                baseline=0.75 + i * 0.02,
                generation=i + 1,
            )
        
        summary = history.get_summary()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        assert today in summary["daily_stats"]
        assert sum(summary["daily_stats"][today].values()) == 5
    
    def test_export_import(self, history, temp_history_path):
        # Record some data
        history.record_interaction(
            run_id="export-test",
            run_name="Export Test",
            backend="jules",
            reward=0.77,
            baseline=0.72,
            generation=1,
        )
        
        # Export
        export_path = temp_history_path.parent / "exported.json"
        history.export_to_file(export_path)
        
        # Clear and import
        history.clear_history()
        assert history.get_summary()["total_interactions"] == 0
        
        history.import_from_file(export_path, merge=False)
        assert history.get_summary()["total_interactions"] == 1


class TestBanditHistoryData:
    def test_from_dict_handles_infinity(self):
        data_dict = {
            "version": 1,
            "last_updated": "2024-01-01T00:00:00",
            "total_interactions": 0,
            "total_runs": 0,
            "global_stats": {
                "codex": {
                    "total_selections": 0,
                    "total_completions": 0,
                    "total_successes": 0,
                    "total_failures": 0,
                    "cumulative_reward": 0.0,
                    "cumulative_improvement": 0.0,
                    "best_reward": 0.0,
                    "worst_reward": "Infinity",
                    "avg_reward": 0.0,
                    "win_rate": 0.0,
                    "runs_participated": 0,
                    "last_used": None,
                }
            },
            "runs": {},
            "interactions": [],
        }
        
        data = BanditHistoryData.from_dict(data_dict)
        assert data.global_stats["codex"].worst_reward == float('inf')
