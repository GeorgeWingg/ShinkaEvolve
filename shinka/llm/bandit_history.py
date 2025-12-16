"""Persistent bandit history for cross-session learning.

This module provides global persistence for backend bandit statistics,
allowing the system to learn preferences across multiple experiment runs.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default location for global bandit history
DEFAULT_HISTORY_PATH = Path.home() / ".shinka" / "bandit_history.json"

# Current schema version for migrations
SCHEMA_VERSION = 1


@dataclass
class BanditInteraction:
    """A single bandit interaction record."""
    
    timestamp: str
    run_id: str
    run_name: str
    backend: str
    reward: Optional[float]
    baseline: Optional[float]
    improvement: Optional[float]  # reward - baseline if both present
    generation: int
    task_name: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BanditInteraction":
        return cls(**d)


@dataclass
class BackendGlobalStats:
    """Aggregated statistics for a single backend across all runs."""
    
    total_selections: int = 0
    total_completions: int = 0
    total_successes: int = 0  # reward > baseline
    total_failures: int = 0   # reward <= baseline or error
    cumulative_reward: float = 0.0
    cumulative_improvement: float = 0.0
    best_reward: float = 0.0
    worst_reward: float = float('inf')
    avg_reward: float = 0.0
    win_rate: float = 0.0
    runs_participated: int = 0
    last_used: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BackendGlobalStats":
        # Handle inf values from JSON
        if d.get('worst_reward') == 'Infinity' or d.get('worst_reward') is None:
            d['worst_reward'] = float('inf')
        return cls(**d)
    
    def update(self, reward: Optional[float], baseline: Optional[float]) -> None:
        """Update stats with a new interaction."""
        self.total_selections += 1
        self.last_used = datetime.now(timezone.utc).isoformat()
        
        if reward is not None:
            self.total_completions += 1
            self.cumulative_reward += reward
            self.best_reward = max(self.best_reward, reward)
            if self.worst_reward == float('inf'):
                self.worst_reward = reward
            else:
                self.worst_reward = min(self.worst_reward, reward)
            self.avg_reward = self.cumulative_reward / self.total_completions
            
            if baseline is not None:
                improvement = reward - baseline
                self.cumulative_improvement += improvement
                if improvement > 0:
                    self.total_successes += 1
                else:
                    self.total_failures += 1
                self.win_rate = self.total_successes / self.total_completions if self.total_completions > 0 else 0.0
            else:
                # No baseline, count as success if reward > 0
                if reward > 0:
                    self.total_successes += 1
                else:
                    self.total_failures += 1
                self.win_rate = self.total_successes / self.total_completions if self.total_completions > 0 else 0.0


@dataclass  
class RunSummary:
    """Summary of bandit usage in a single run."""
    
    run_id: str
    run_name: str
    task_name: Optional[str]
    start_time: str
    end_time: Optional[str]
    total_generations: int
    backends_used: Dict[str, int]  # backend -> count
    best_backend: Optional[str]
    best_score: float
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RunSummary":
        return cls(**d)


@dataclass
class BanditHistoryData:
    """Complete bandit history data structure."""
    
    version: int = SCHEMA_VERSION
    last_updated: str = ""
    global_stats: Dict[str, BackendGlobalStats] = field(default_factory=dict)
    runs: Dict[str, RunSummary] = field(default_factory=dict)
    interactions: List[BanditInteraction] = field(default_factory=list)
    
    # Metadata
    total_interactions: int = 0
    total_runs: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "last_updated": self.last_updated,
            "global_stats": {k: v.to_dict() for k, v in self.global_stats.items()},
            "runs": {k: v.to_dict() for k, v in self.runs.items()},
            "interactions": [i.to_dict() for i in self.interactions],
            "total_interactions": self.total_interactions,
            "total_runs": self.total_runs,
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BanditHistoryData":
        return cls(
            version=d.get("version", SCHEMA_VERSION),
            last_updated=d.get("last_updated", ""),
            global_stats={k: BackendGlobalStats.from_dict(v) for k, v in d.get("global_stats", {}).items()},
            runs={k: RunSummary.from_dict(v) for k, v in d.get("runs", {}).items()},
            interactions=[BanditInteraction.from_dict(i) for i in d.get("interactions", [])],
            total_interactions=d.get("total_interactions", 0),
            total_runs=d.get("total_runs", 0),
        )


class BanditHistory:
    """Manages persistent bandit history across sessions.
    
    Thread-safe singleton that handles loading, saving, and querying
    historical bandit data.
    
    Usage:
        history = BanditHistory.get_instance()
        history.record_interaction(run_id, backend, reward, baseline, ...)
        priors = history.get_priors()  # Use as initial bandit state
    """
    
    _instance: Optional["BanditHistory"] = None
    _lock = threading.Lock()
    
    def __init__(self, history_path: Optional[Path] = None):
        """Initialize bandit history.
        
        Args:
            history_path: Path to history file. Defaults to ~/.shinka/bandit_history.json
        """
        self.history_path = history_path or DEFAULT_HISTORY_PATH
        self._data: Optional[BanditHistoryData] = None
        self._file_lock = threading.Lock()
        self._dirty = False
        
    @classmethod
    def get_instance(cls, history_path: Optional[Path] = None) -> "BanditHistory":
        """Get or create the singleton instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(history_path)
            return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (for testing)."""
        with cls._lock:
            cls._instance = None
    
    def _ensure_loaded(self) -> BanditHistoryData:
        """Ensure data is loaded from disk."""
        if self._data is None:
            self._load()
        return self._data
    
    def _load(self) -> None:
        """Load history from disk."""
        with self._file_lock:
            if self.history_path.exists():
                try:
                    with open(self.history_path, 'r') as f:
                        raw = json.load(f)
                    self._data = BanditHistoryData.from_dict(raw)
                    # Migrate if needed
                    if self._data.version < SCHEMA_VERSION:
                        self._migrate(self._data.version)
                except Exception as e:
                    print(f"[BanditHistory] Warning: Failed to load history: {e}")
                    self._data = BanditHistoryData()
            else:
                self._data = BanditHistoryData()
    
    def _save(self) -> None:
        """Save history to disk."""
        if self._data is None:
            return
            
        with self._file_lock:
            try:
                self.history_path.parent.mkdir(parents=True, exist_ok=True)
                self._data.last_updated = datetime.now(timezone.utc).isoformat()
                
                # Custom JSON encoder for inf values
                def encode_value(obj):
                    if isinstance(obj, float):
                        if obj == float('inf'):
                            return "Infinity"
                        if obj == float('-inf'):
                            return "-Infinity"
                    return obj
                
                data_dict = self._data.to_dict()
                
                with open(self.history_path, 'w') as f:
                    json.dump(data_dict, f, indent=2, default=encode_value)
                self._dirty = False
            except Exception as e:
                print(f"[BanditHistory] Warning: Failed to save history: {e}")
    
    def _migrate(self, from_version: int) -> None:
        """Migrate data from older schema versions."""
        # Future migration logic here
        if self._data:
            self._data.version = SCHEMA_VERSION
    
    def record_interaction(
        self,
        run_id: str,
        run_name: str,
        backend: str,
        reward: Optional[float],
        baseline: Optional[float],
        generation: int,
        task_name: Optional[str] = None,
    ) -> None:
        """Record a bandit interaction.
        
        Args:
            run_id: Unique identifier for the run
            run_name: Human-readable run name
            backend: Backend that was selected
            reward: Score achieved (None if failed)
            baseline: Parent's score for comparison
            generation: Current generation number
            task_name: Optional task/experiment name
        """
        data = self._ensure_loaded()
        
        improvement = None
        if reward is not None and baseline is not None:
            improvement = reward - baseline
        
        interaction = BanditInteraction(
            timestamp=datetime.now(timezone.utc).isoformat(),
            run_id=run_id,
            run_name=run_name,
            backend=backend,
            reward=reward,
            baseline=baseline,
            improvement=improvement,
            generation=generation,
            task_name=task_name,
        )
        
        # Update global stats
        if backend not in data.global_stats:
            data.global_stats[backend] = BackendGlobalStats()
        data.global_stats[backend].update(reward, baseline)
        
        # Update run tracking
        if run_id not in data.runs:
            data.runs[run_id] = RunSummary(
                run_id=run_id,
                run_name=run_name,
                task_name=task_name,
                start_time=datetime.now(timezone.utc).isoformat(),
                end_time=None,
                total_generations=0,
                backends_used={},
                best_backend=None,
                best_score=0.0,
            )
            data.total_runs += 1
            data.global_stats[backend].runs_participated += 1
        
        run = data.runs[run_id]
        run.backends_used[backend] = run.backends_used.get(backend, 0) + 1
        run.total_generations = max(run.total_generations, generation)
        if reward is not None and reward > run.best_score:
            run.best_score = reward
            run.best_backend = backend
        
        # Add interaction (keep last 10000 for memory)
        data.interactions.append(interaction)
        if len(data.interactions) > 10000:
            data.interactions = data.interactions[-10000:]
        data.total_interactions += 1
        
        self._dirty = True
        self._save()
    
    def finalize_run(self, run_id: str) -> None:
        """Mark a run as complete."""
        data = self._ensure_loaded()
        if run_id in data.runs:
            data.runs[run_id].end_time = datetime.now(timezone.utc).isoformat()
            self._save()
    
    def get_priors(self) -> Dict[str, Dict[str, float]]:
        """Get prior statistics for initializing a new bandit.
        
        Returns:
            Dict mapping backend -> {alpha, beta} for Beta distribution priors
        """
        data = self._ensure_loaded()
        priors = {}
        
        for backend, stats in data.global_stats.items():
            # Convert successes/failures to Beta distribution parameters
            # Add 1 to both for Laplace smoothing
            alpha = stats.total_successes + 1
            beta = stats.total_failures + 1
            
            priors[backend] = {
                "alpha": alpha,
                "beta": beta,
                "win_rate": stats.win_rate,
                "total_selections": stats.total_selections,
                "avg_reward": stats.avg_reward,
            }
        
        return priors
    
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of all historical data for visualization."""
        data = self._ensure_loaded()
        
        # Calculate time series data for charts
        daily_stats: Dict[str, Dict[str, int]] = {}  # date -> backend -> count
        for interaction in data.interactions:
            date = interaction.timestamp[:10]  # YYYY-MM-DD
            if date not in daily_stats:
                daily_stats[date] = {}
            backend = interaction.backend
            daily_stats[date][backend] = daily_stats[date].get(backend, 0) + 1
        
        # Recent performance (last 100 interactions per backend)
        recent_by_backend: Dict[str, List[float]] = {}
        for interaction in reversed(data.interactions):
            backend = interaction.backend
            if backend not in recent_by_backend:
                recent_by_backend[backend] = []
            if interaction.improvement is not None and len(recent_by_backend[backend]) < 100:
                recent_by_backend[backend].append(interaction.improvement)
        
        return {
            "version": data.version,
            "last_updated": data.last_updated,
            "total_interactions": data.total_interactions,
            "total_runs": data.total_runs,
            "global_stats": {k: v.to_dict() for k, v in data.global_stats.items()},
            "runs": {k: v.to_dict() for k, v in data.runs.items()},
            "daily_stats": daily_stats,
            "recent_performance": {k: v for k, v in recent_by_backend.items()},
            "history_path": str(self.history_path),
        }
    
    def get_recent_interactions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent interactions for display."""
        data = self._ensure_loaded()
        return [i.to_dict() for i in data.interactions[-limit:]]
    
    def clear_history(self) -> None:
        """Clear all historical data."""
        self._data = BanditHistoryData()
        self._save()
    
    def export_to_file(self, path: Path) -> None:
        """Export history to a specified file."""
        data = self._ensure_loaded()
        with open(path, 'w') as f:
            json.dump(data.to_dict(), f, indent=2)
    
    def import_from_file(self, path: Path, merge: bool = True) -> None:
        """Import history from a file.
        
        Args:
            path: Path to import from
            merge: If True, merge with existing data. If False, replace.
        """
        with open(path, 'r') as f:
            imported = BanditHistoryData.from_dict(json.load(f))
        
        if not merge:
            self._data = imported
        else:
            data = self._ensure_loaded()
            # Merge global stats
            for backend, stats in imported.global_stats.items():
                if backend in data.global_stats:
                    existing = data.global_stats[backend]
                    existing.total_selections += stats.total_selections
                    existing.total_completions += stats.total_completions
                    existing.total_successes += stats.total_successes
                    existing.total_failures += stats.total_failures
                    existing.cumulative_reward += stats.cumulative_reward
                    existing.cumulative_improvement += stats.cumulative_improvement
                    existing.best_reward = max(existing.best_reward, stats.best_reward)
                    if existing.worst_reward == float('inf'):
                        existing.worst_reward = stats.worst_reward
                    elif stats.worst_reward != float('inf'):
                        existing.worst_reward = min(existing.worst_reward, stats.worst_reward)
                    if existing.total_completions > 0:
                        existing.avg_reward = existing.cumulative_reward / existing.total_completions
                        existing.win_rate = existing.total_successes / existing.total_completions
                else:
                    data.global_stats[backend] = stats
            
            # Merge runs (don't duplicate)
            for run_id, run in imported.runs.items():
                if run_id not in data.runs:
                    data.runs[run_id] = run
            
            # Merge interactions (dedupe by timestamp+run_id+backend)
            existing_keys = {(i.timestamp, i.run_id, i.backend) for i in data.interactions}
            for interaction in imported.interactions:
                key = (interaction.timestamp, interaction.run_id, interaction.backend)
                if key not in existing_keys:
                    data.interactions.append(interaction)
                    existing_keys.add(key)
            
            data.interactions.sort(key=lambda x: x.timestamp)
            data.total_interactions = len(data.interactions)
            data.total_runs = len(data.runs)
        
        self._save()
