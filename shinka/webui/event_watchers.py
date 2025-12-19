"""File and database change detection for SSE event broadcasting.

This module provides watchers that poll for changes and trigger SSE broadcasts
when files or databases are modified.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional, Set

logger = logging.getLogger(__name__)

# Polling intervals (in seconds)
SESSION_POLL_INTERVAL = 0.5  # 500ms for session logs
DATABASE_POLL_INTERVAL = 3.0  # 3s for SQLite databases
REGISTRY_POLL_INTERVAL = 2.0  # 2s for session registry


class BaseWatcher:
    """Base class for file/directory watchers."""

    def __init__(
        self,
        broadcast_callback: Callable[[str, str, Dict[str, Any]], Coroutine],
        poll_interval: float = 1.0,
    ):
        """Initialize watcher.

        Args:
            broadcast_callback: Async function to call when changes detected.
                               Signature: (topic, event_type, data) -> None
            poll_interval: Seconds between polls.
        """
        self._callback = broadcast_callback
        self._poll_interval = poll_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the watcher loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info(f"{self.__class__.__name__} started")

    async def stop(self) -> None:
        """Stop the watcher loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info(f"{self.__class__.__name__} stopped")

    async def _watch_loop(self) -> None:
        """Main watch loop - override in subclasses."""
        while self._running:
            try:
                await self._poll()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Log and continue - don't crash the watcher
                logger.error(f"{self.__class__.__name__} error: {e}")
            await asyncio.sleep(self._poll_interval)

    async def _poll(self) -> None:
        """Override in subclasses to implement polling logic."""
        raise NotImplementedError


class SessionWatcher(BaseWatcher):
    """Watch session log files for changes and broadcast updates.

    Monitors session_log.jsonl files and broadcasts new events to subscribers.
    """

    def __init__(
        self,
        broadcast_callback: Callable[[str, str, Dict[str, Any]], Coroutine],
    ):
        super().__init__(broadcast_callback, SESSION_POLL_INTERVAL)
        # Track watched sessions: session_id -> (path, last_mtime, last_size)
        self._watched: Dict[str, tuple] = {}
        self._lock = asyncio.Lock()

    async def watch_session(self, session_id: str, session_dir: Path) -> None:
        """Start watching a session directory for log changes."""
        log_path = session_dir / "session_log.jsonl"
        async with self._lock:
            if session_id in self._watched:
                return
            mtime = log_path.stat().st_mtime if log_path.exists() else 0
            size = log_path.stat().st_size if log_path.exists() else 0
            self._watched[session_id] = (log_path, mtime, size)
            logger.debug(f"SessionWatcher: watching {session_id}")

    async def unwatch_session(self, session_id: str) -> None:
        """Stop watching a session."""
        async with self._lock:
            self._watched.pop(session_id, None)
            logger.debug(f"SessionWatcher: unwatched {session_id}")

    async def _poll(self) -> None:
        """Check all watched sessions for changes."""
        async with self._lock:
            sessions_to_check = list(self._watched.items())

        for session_id, (log_path, last_mtime, last_size) in sessions_to_check:
            try:
                if not log_path.exists():
                    continue

                stat = log_path.stat()
                current_mtime = stat.st_mtime
                current_size = stat.st_size

                # Check if file changed
                if current_mtime > last_mtime or current_size > last_size:
                    # Read new events (from last_size to end)
                    events = await self._read_new_events(log_path, last_size)
                    if events:
                        # Detect status
                        status = await self._detect_status(log_path, events)

                        # Broadcast update
                        await self._callback(
                            f"session:{session_id}",
                            "session",
                            {
                                "session_id": session_id,
                                "new_events": events,
                                "total_size": current_size,
                                "status": status,
                            },
                        )

                    # FIX: Atomic compare-and-swap to prevent TOCTOU race
                    # Only update if our snapshot values still match current state
                    async with self._lock:
                        if session_id in self._watched:
                            stored = self._watched[session_id]
                            stored_mtime = stored[1]
                            stored_size = stored[2]
                            # Only update if no one else modified it
                            if stored_mtime == last_mtime and stored_size == last_size:
                                self._watched[session_id] = (
                                    log_path,
                                    current_mtime,
                                    current_size,
                                )

            except Exception as e:
                logger.debug(f"SessionWatcher error for {session_id}: {e}")

    async def _read_new_events(self, log_path: Path, from_pos: int) -> list:
        """Read new events from log file starting at position."""
        events = []
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                f.seek(from_pos)
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            logger.debug(f"Error reading {log_path}: {e}")
        return events

    async def _detect_status(self, log_path: Path, recent_events: list) -> str:
        """Detect session status from events and metadata."""
        # Check recent events for completion indicators
        for event in reversed(recent_events):
            event_type = event.get("type", "")
            if event_type in ("result", "error", "completed", "done"):
                return "completed"

        # Check session_meta.json
        meta_path = log_path.parent / "session_meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("status") == "completed":
                    return "completed"
            except Exception:
                pass

        return "running"


class DatabaseWatcher(BaseWatcher):
    """Watch SQLite databases for changes and broadcast updates.

    Monitors database mtime and broadcasts when changes detected.
    """

    def __init__(
        self,
        broadcast_callback: Callable[[str, str, Dict[str, Any]], Coroutine],
    ):
        super().__init__(broadcast_callback, DATABASE_POLL_INTERVAL)
        # Track watched databases: db_path -> last_mtime
        self._watched: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def watch_database(self, db_path: str) -> None:
        """Start watching a database file for changes."""
        path = Path(db_path)
        async with self._lock:
            if db_path in self._watched:
                return
            mtime = path.stat().st_mtime if path.exists() else 0
            self._watched[db_path] = mtime
            logger.debug(f"DatabaseWatcher: watching {db_path}")

    async def unwatch_database(self, db_path: str) -> None:
        """Stop watching a database."""
        async with self._lock:
            self._watched.pop(db_path, None)

    async def _poll(self) -> None:
        """Check all watched databases for changes."""
        async with self._lock:
            dbs_to_check = list(self._watched.items())

        for db_path, last_mtime in dbs_to_check:
            try:
                path = Path(db_path)
                if not path.exists():
                    continue

                current_mtime = path.stat().st_mtime
                if current_mtime > last_mtime:
                    # Database changed - get basic stats
                    program_count = await self._get_program_count(db_path)

                    # Broadcast tree update
                    await self._callback(
                        f"tree:{db_path}",
                        "tree",
                        {
                            "db_path": db_path,
                            "program_count": program_count,
                            "mtime": current_mtime,
                        },
                    )

                    # FIX: Atomic compare-and-swap to prevent TOCTOU race
                    # Only update if our snapshot value still matches current state
                    async with self._lock:
                        if db_path in self._watched:
                            stored_mtime = self._watched[db_path]
                            # Only update if no one else modified it
                            if stored_mtime == last_mtime:
                                self._watched[db_path] = current_mtime

            except Exception as e:
                logger.debug(f"DatabaseWatcher error for {db_path}: {e}")

    async def _get_program_count(self, db_path: str) -> int:
        """Get program count from database."""
        try:
            # FIX: Use context manager to ensure connection is always closed
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM programs")
                return cursor.fetchone()[0]
        except Exception:
            return 0


class RegistryWatcher(BaseWatcher):
    """Watch session registry for changes and broadcast job updates.

    Monitors ~/.codex/shinka_sessions/ for new/removed session files.
    """

    def __init__(
        self,
        broadcast_callback: Callable[[str, str, Dict[str, Any]], Coroutine],
    ):
        super().__init__(broadcast_callback, REGISTRY_POLL_INTERVAL)
        self._registry_dir = Path.home() / ".codex" / "shinka_sessions"
        self._last_files: Set[str] = set()
        self._last_mtime: float = 0

    async def _poll(self) -> None:
        """Check registry directory for changes."""
        if not self._registry_dir.exists():
            return

        try:
            # Get current state
            current_files = set()
            dir_mtime = self._registry_dir.stat().st_mtime

            for json_file in self._registry_dir.glob("*.json"):
                current_files.add(json_file.name)

            # Check if anything changed
            if current_files != self._last_files or dir_mtime > self._last_mtime:
                # Load all active jobs
                jobs = await self._load_jobs()

                # Broadcast jobs update (use empty string as global topic)
                await self._callback(
                    "jobs:",
                    "jobs",
                    {
                        "jobs": jobs,
                        "count": len(jobs),
                    },
                )

                self._last_files = current_files
                self._last_mtime = dir_mtime

        except Exception as e:
            logger.debug(f"RegistryWatcher error: {e}")

    async def _load_jobs(self) -> list:
        """Load all active jobs from registry."""
        from shinka.tools.codex_session_registry import list_session_processes

        try:
            return list_session_processes()
        except Exception as e:
            logger.debug(f"Error loading jobs: {e}")
            return []


class WatcherManager:
    """Manages all watchers and provides a unified interface.

    Uses reference counting so multiple clients can watch the same session/database.
    Watchers are only stopped when the last client unwatches.
    """

    def __init__(
        self,
        broadcast_callback: Callable[[str, str, Dict[str, Any]], Coroutine],
    ):
        self.session_watcher = SessionWatcher(broadcast_callback)
        self.database_watcher = DatabaseWatcher(broadcast_callback)
        self.registry_watcher = RegistryWatcher(broadcast_callback)
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False
        # Reference counting for shared watchers
        self._session_ref_counts: Dict[str, int] = {}
        self._database_ref_counts: Dict[str, int] = {}
        self._ref_lock = asyncio.Lock()

    async def start(self) -> None:
        """Start all watchers."""
        self._running = True
        await self.session_watcher.start()
        await self.database_watcher.start()
        await self.registry_watcher.start()
        logger.info("WatcherManager: all watchers started")

    async def stop(self) -> None:
        """Stop all watchers."""
        self._running = False
        await self.session_watcher.stop()
        await self.database_watcher.stop()
        await self.registry_watcher.stop()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        logger.info("WatcherManager: all watchers stopped")

    async def watch_session(self, session_id: str, session_dir: Path) -> None:
        """Start watching a session (reference counted)."""
        async with self._ref_lock:
            if session_id in self._session_ref_counts:
                self._session_ref_counts[session_id] += 1
                logger.debug(
                    f"Session {session_id} ref count: {self._session_ref_counts[session_id]}"
                )
                return
            self._session_ref_counts[session_id] = 1
        await self.session_watcher.watch_session(session_id, session_dir)

    async def unwatch_session(self, session_id: str) -> None:
        """Stop watching a session (reference counted)."""
        async with self._ref_lock:
            if session_id not in self._session_ref_counts:
                return
            self._session_ref_counts[session_id] -= 1
            if self._session_ref_counts[session_id] <= 0:
                del self._session_ref_counts[session_id]
                await self.session_watcher.unwatch_session(session_id)
            else:
                logger.debug(
                    f"Session {session_id} ref count: {self._session_ref_counts[session_id]}"
                )

    async def watch_database(self, db_path: str) -> None:
        """Start watching a database (reference counted)."""
        async with self._ref_lock:
            if db_path in self._database_ref_counts:
                self._database_ref_counts[db_path] += 1
                logger.debug(
                    f"Database {db_path} ref count: {self._database_ref_counts[db_path]}"
                )
                return
            self._database_ref_counts[db_path] = 1
        await self.database_watcher.watch_database(db_path)

    async def unwatch_database(self, db_path: str) -> None:
        """Stop watching a database (reference counted)."""
        async with self._ref_lock:
            if db_path not in self._database_ref_counts:
                return
            self._database_ref_counts[db_path] -= 1
            if self._database_ref_counts[db_path] <= 0:
                del self._database_ref_counts[db_path]
                await self.database_watcher.unwatch_database(db_path)
            else:
                logger.debug(
                    f"Database {db_path} ref count: {self._database_ref_counts[db_path]}"
                )

    def get_stats(self) -> Dict[str, Any]:
        """Get watcher statistics for monitoring."""
        return {
            "session_watcher": {
                "watched_sessions": list(self.session_watcher._watched.keys()),
                "ref_counts": dict(self._session_ref_counts),
            },
            "database_watcher": {
                "watched_databases": list(self.database_watcher._watched.keys()),
                "ref_counts": dict(self._database_ref_counts),
            },
            "registry_watcher_running": self.registry_watcher._running,
        }
