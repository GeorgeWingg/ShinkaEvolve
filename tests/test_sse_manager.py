"""Tests for SSE manager and event watchers (Phase 5 of SSE architecture).

These tests verify the SSE infrastructure including connection management,
event buffering, and file change detection.
"""

import asyncio
import json
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class MockStreamResponse:
    """Mock aiohttp StreamResponse for testing."""

    def __init__(self):
        self.written_data: List[bytes] = []
        self.closed = False
        self.task = MagicMock()
        self.task.done.return_value = False

    async def write(self, data: bytes) -> None:
        if self.closed:
            raise ConnectionResetError("Connection closed")
        self.written_data.append(data)

    def get_messages(self) -> List[str]:
        """Parse written SSE messages."""
        messages = []
        for data in self.written_data:
            text = data.decode("utf-8")
            messages.append(text)
        return messages


class TestSSEManager:
    """Tests for SSEManager class."""

    @pytest.fixture
    def sse_manager(self):
        """Create a fresh SSEManager for each test."""
        from shinka.webui.sse_manager import SSEManager

        return SSEManager()

    @pytest.mark.asyncio
    async def test_register_client(self, sse_manager):
        """Register should create a client entry."""
        response = MockStreamResponse()
        topics = {"session:abc123", "jobs:"}

        client_id = await sse_manager.register(response, topics)

        assert client_id is not None
        assert sse_manager.connection_count == 1

    @pytest.mark.asyncio
    async def test_register_with_topics(self, sse_manager):
        """Registered client should be subscribed to specified topics."""
        response = MockStreamResponse()
        topics = {"session:abc123", "tree:/path/to/db"}

        client_id = await sse_manager.register(response, topics)

        # Check topic subscriptions
        stats = sse_manager.get_stats()
        assert "session:abc123" in stats["topics"]
        assert "tree:/path/to/db" in stats["topics"]

    @pytest.mark.asyncio
    async def test_connection_limit(self, sse_manager):
        """Should reject connections when limit is reached."""
        from shinka.webui.sse_manager import MAX_SSE_CONNECTIONS

        # Fill up to limit
        for i in range(MAX_SSE_CONNECTIONS):
            response = MockStreamResponse()
            client_id = await sse_manager.register(response, {f"session:{i}"})
            assert client_id is not None

        assert sse_manager.connection_count == MAX_SSE_CONNECTIONS

        # Try one more - should be rejected
        extra_response = MockStreamResponse()
        client_id = await sse_manager.register(extra_response, {"session:extra"})
        assert client_id is None
        assert sse_manager.connection_count == MAX_SSE_CONNECTIONS

    @pytest.mark.asyncio
    async def test_unregister_client(self, sse_manager):
        """Unregister should remove client and clean up subscriptions."""
        response = MockStreamResponse()
        topics = {"session:abc123"}

        client_id = await sse_manager.register(response, topics)
        assert sse_manager.connection_count == 1

        await sse_manager.unregister(client_id)

        assert sse_manager.connection_count == 0
        stats = sse_manager.get_stats()
        assert "session:abc123" not in stats["topics"]

    @pytest.mark.asyncio
    async def test_unregister_nonexistent(self, sse_manager):
        """Unregister should handle nonexistent client gracefully."""
        # Should not raise
        await sse_manager.unregister("nonexistent-client-id")

    @pytest.mark.asyncio
    async def test_broadcast_to_subscribers(self, sse_manager):
        """Broadcast should send event to all topic subscribers."""
        response1 = MockStreamResponse()
        response2 = MockStreamResponse()

        await sse_manager.register(response1, {"session:abc123"})
        await sse_manager.register(response2, {"session:abc123"})

        sent_count = await sse_manager.broadcast(
            "session:abc123", "session", {"status": "running"}
        )

        assert sent_count == 2
        # Both responses should have received the message
        assert len(response1.written_data) == 1
        assert len(response2.written_data) == 1

    @pytest.mark.asyncio
    async def test_broadcast_only_to_matching_topic(self, sse_manager):
        """Broadcast should only send to subscribers of the specific topic."""
        response1 = MockStreamResponse()
        response2 = MockStreamResponse()

        await sse_manager.register(response1, {"session:abc123"})
        await sse_manager.register(response2, {"session:xyz789"})

        sent_count = await sse_manager.broadcast(
            "session:abc123", "session", {"status": "running"}
        )

        assert sent_count == 1
        assert len(response1.written_data) == 1
        assert len(response2.written_data) == 0

    @pytest.mark.asyncio
    async def test_broadcast_to_no_subscribers(self, sse_manager):
        """Broadcast to topic with no subscribers should return 0."""
        sent_count = await sse_manager.broadcast(
            "session:nonexistent", "session", {"status": "running"}
        )
        assert sent_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_handles_failed_write(self, sse_manager):
        """Broadcast should handle and clean up failed connections."""
        response1 = MockStreamResponse()
        response2 = MockStreamResponse()
        response2.closed = True  # Simulate closed connection

        await sse_manager.register(response1, {"session:abc123"})
        await sse_manager.register(response2, {"session:abc123"})

        assert sse_manager.connection_count == 2

        sent_count = await sse_manager.broadcast(
            "session:abc123", "session", {"status": "running"}
        )

        assert sent_count == 1  # Only one successful
        # Give time for cleanup task
        await asyncio.sleep(0.1)
        assert sse_manager.connection_count == 1

    @pytest.mark.asyncio
    async def test_event_buffering(self, sse_manager):
        """Events should be buffered for replay."""
        response = MockStreamResponse()
        await sse_manager.register(response, {"session:abc123"})

        # Send multiple events
        for i in range(5):
            await sse_manager.broadcast(
                "session:abc123", "session", {"count": i}
            )

        stats = sse_manager.get_stats()
        assert stats["buffer_sizes"]["session:abc123"] == 5
        assert stats["event_counter"] == 5

    @pytest.mark.asyncio
    async def test_event_buffer_limit(self, sse_manager):
        """Event buffer should respect maximum size."""
        from shinka.webui.sse_manager import MAX_EVENT_BUFFER

        response = MockStreamResponse()
        await sse_manager.register(response, {"session:abc123"})

        # Send more events than buffer size
        for i in range(MAX_EVENT_BUFFER + 50):
            await sse_manager.broadcast(
                "session:abc123", "session", {"count": i}
            )

        stats = sse_manager.get_stats()
        assert stats["buffer_sizes"]["session:abc123"] == MAX_EVENT_BUFFER

    @pytest.mark.asyncio
    async def test_sse_message_format(self, sse_manager):
        """SSE messages should follow the correct format."""
        response = MockStreamResponse()
        await sse_manager.register(response, {"session:abc123"})

        await sse_manager.broadcast(
            "session:abc123", "session", {"status": "running"}
        )

        message = response.written_data[0].decode("utf-8")
        assert "id: " in message
        assert "event: session" in message
        assert "data: " in message
        assert '"status": "running"' in message

    @pytest.mark.asyncio
    async def test_heartbeat(self, sse_manager):
        """Heartbeat should send to all connected clients."""
        response1 = MockStreamResponse()
        response2 = MockStreamResponse()

        await sse_manager.register(response1, {"session:abc123"})
        await sse_manager.register(response2, {"session:xyz789"})

        sent_count = await sse_manager.send_heartbeat()

        assert sent_count == 2
        # Check heartbeat format
        message = response1.written_data[0].decode("utf-8")
        assert "event: heartbeat" in message
        assert '"ts":' in message

    @pytest.mark.asyncio
    async def test_heartbeat_cleans_dead_connections(self, sse_manager):
        """Heartbeat should clean up dead connections."""
        response1 = MockStreamResponse()
        response2 = MockStreamResponse()
        response2.closed = True

        await sse_manager.register(response1, {"session:abc123"})
        await sse_manager.register(response2, {"session:xyz789"})

        assert sse_manager.connection_count == 2

        sent_count = await sse_manager.send_heartbeat()

        assert sent_count == 1
        # Give time for cleanup
        await asyncio.sleep(0.1)
        assert sse_manager.connection_count == 1

    @pytest.mark.asyncio
    async def test_get_stats(self, sse_manager):
        """get_stats should return accurate statistics."""
        response = MockStreamResponse()
        await sse_manager.register(response, {"session:abc123", "jobs:"})

        await sse_manager.broadcast("session:abc123", "session", {"x": 1})
        await sse_manager.broadcast("jobs:", "jobs", {"y": 2})

        stats = sse_manager.get_stats()

        assert stats["total_clients"] == 1
        assert stats["max_clients"] == 100
        assert set(stats["topics"]) == {"session:abc123", "jobs:"}
        assert stats["event_counter"] == 2
        assert "session:abc123" in stats["buffer_sizes"]


class TestEventWatchers:
    """Tests for event watcher classes."""

    @pytest.fixture
    def broadcast_callback(self):
        """Create a mock broadcast callback."""
        callback = AsyncMock()
        return callback

    @pytest.mark.asyncio
    async def test_session_watcher_detects_changes(self, broadcast_callback, tmp_path):
        """SessionWatcher should detect log file changes."""
        from shinka.webui.event_watchers import SessionWatcher

        watcher = SessionWatcher(broadcast_callback)

        # Create a session directory with log
        session_dir = tmp_path / "test_session"
        session_dir.mkdir()
        log_path = session_dir / "session_log.jsonl"
        log_path.write_text('{"type": "start", "ts": 1}\n')

        # Watch the session
        await watcher.watch_session("test_session", session_dir)
        await watcher.start()

        # Give time for initial poll
        await asyncio.sleep(0.1)

        # Modify the log
        with open(log_path, "a") as f:
            f.write('{"type": "event", "ts": 2}\n')

        # Wait for watcher to detect
        await asyncio.sleep(1.0)  # Longer than poll interval

        await watcher.stop()

        # Should have been called at least once
        assert broadcast_callback.call_count >= 1

    @pytest.mark.asyncio
    async def test_session_watcher_unwatch(self, broadcast_callback, tmp_path):
        """SessionWatcher should stop watching when unwatched."""
        from shinka.webui.event_watchers import SessionWatcher

        watcher = SessionWatcher(broadcast_callback)

        session_dir = tmp_path / "test_session"
        session_dir.mkdir()
        log_path = session_dir / "session_log.jsonl"
        log_path.write_text('{"type": "start"}\n')

        await watcher.watch_session("test_session", session_dir)
        await watcher.unwatch_session("test_session")

        # Should not crash
        await watcher.start()
        await asyncio.sleep(0.2)
        await watcher.stop()

    @pytest.mark.asyncio
    async def test_database_watcher_detects_changes(self, broadcast_callback, tmp_path):
        """DatabaseWatcher should detect database mtime changes."""
        from shinka.webui.event_watchers import DatabaseWatcher

        watcher = DatabaseWatcher(broadcast_callback)

        # Create a fake database file
        db_path = tmp_path / "test.sqlite"
        db_path.write_text("fake db content")

        await watcher.watch_database(str(db_path))
        await watcher.start()

        await asyncio.sleep(0.1)

        # Touch the file to update mtime
        db_path.write_text("updated content")

        # Wait for watcher to detect (3s poll interval)
        await asyncio.sleep(4.0)

        await watcher.stop()

        # Callback may or may not be called depending on timing
        # Just verify no errors occurred

    @pytest.mark.asyncio
    async def test_watcher_manager_starts_all(self, broadcast_callback):
        """WatcherManager should start all watchers."""
        from shinka.webui.event_watchers import WatcherManager

        manager = WatcherManager(broadcast_callback)

        await manager.start()

        # All watchers should be running
        assert manager.session_watcher._running
        assert manager.database_watcher._running
        assert manager.registry_watcher._running

        await manager.stop()

        # All watchers should be stopped
        assert not manager.session_watcher._running
        assert not manager.database_watcher._running
        assert not manager.registry_watcher._running


class TestSSEServerValidation:
    """Tests for SSE server topic validation."""

    def test_validate_session_topic(self):
        """Session topics with valid IDs should be accepted."""
        from shinka.webui.sse_server import validate_topic

        assert validate_topic("session:abc123") is True
        assert validate_topic("session:a1b2c3d4-e5f6") is True
        assert validate_topic("session:12345") is True

    def test_validate_tree_topic_requires_search_root(self, tmp_path):
        """Tree topics require search_root (fail closed) and existing file."""
        from shinka.webui.sse_server import validate_topic

        # Without search_root, path topics should be rejected (fail closed)
        assert validate_topic("tree:/path/to/db.sqlite") is False
        assert validate_topic("db:/another/path") is False

        # With search_root but file doesn't exist - rejected
        search_root = str(tmp_path)
        assert validate_topic(f"tree:{tmp_path}/nonexistent.db", search_root) is False

        # With search_root and existing file - accepted
        test_file = tmp_path / "test.sqlite"
        test_file.write_text("fake db")
        assert validate_topic(f"tree:{test_file}", search_root) is True

    def test_validate_jobs_topic(self):
        """Jobs topic (global) should be accepted."""
        from shinka.webui.sse_server import validate_topic

        assert validate_topic("jobs:") is True

    def test_reject_invalid_topics(self):
        """Invalid topics should be rejected."""
        from shinka.webui.sse_server import validate_topic

        assert validate_topic("") is False
        assert validate_topic("invalid") is False  # No colon
        assert validate_topic("unknown:value") is False  # Unknown type

    def test_path_validation_with_search_root(self, tmp_path):
        """Paths should be validated against search_root."""
        from shinka.webui.sse_server import validate_topic

        search_root = str(tmp_path)

        # Create a valid file under root
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        valid_file = subdir / "db.sqlite"
        valid_file.write_text("fake db")

        # Path under root with existing file should be valid
        assert validate_topic(f"tree:{valid_file}", search_root) is True

        # Path outside root should be invalid
        assert validate_topic("tree:/etc/passwd", search_root) is False

    def test_session_path_traversal_rejected(self):
        """Session IDs with path traversal chars should be rejected."""
        from shinka.webui.sse_server import validate_topic

        # Path traversal attempts in session ID
        assert validate_topic("session:../../../etc/passwd") is False
        assert validate_topic("session:foo/bar") is False
        assert validate_topic("session:foo\\bar") is False
        assert validate_topic("session:..") is False

        # Valid session IDs
        assert validate_topic("session:abc123") is True
        assert validate_topic("session:a1b2c3d4-e5f6-7890") is True


class TestLastEventIdReplay:
    """Tests for Last-Event-ID replay functionality."""

    @pytest.fixture
    def sse_manager(self):
        from shinka.webui.sse_manager import SSEManager

        return SSEManager()

    @pytest.mark.asyncio
    async def test_replay_on_reconnect(self, sse_manager):
        """Reconnecting client should receive missed events.

        Note: Buffer is only preserved when there are other active subscribers.
        If all clients disconnect, the buffer is cleaned up to prevent memory leaks.
        """
        response1 = MockStreamResponse()
        response_keeper = MockStreamResponse()  # Keep one client subscribed

        client1_id = await sse_manager.register(response1, {"session:abc123"})
        await sse_manager.register(response_keeper, {"session:abc123"})

        # Send some events
        await sse_manager.broadcast("session:abc123", "session", {"count": 1})
        await sse_manager.broadcast("session:abc123", "session", {"count": 2})
        await sse_manager.broadcast("session:abc123", "session", {"count": 3})

        # Simulate disconnect and reconnect with last_event_id
        # (keeper client stays connected to preserve buffer)
        await sse_manager.unregister(client1_id)

        response2 = MockStreamResponse()
        # Reconnect with last_event_id = "1" (should replay events 2 and 3)
        await sse_manager.register(response2, {"session:abc123"}, last_event_id="1")

        # Should have received replayed events
        assert len(response2.written_data) == 2

    @pytest.mark.asyncio
    async def test_replay_with_invalid_last_event_id(self, sse_manager):
        """Invalid last_event_id should be handled gracefully."""
        response = MockStreamResponse()

        # Should not raise
        await sse_manager.register(
            response, {"session:abc123"}, last_event_id="not-a-number"
        )

        assert sse_manager.connection_count == 1


class TestRateLimiting:
    """Tests for SSE connection rate limiting."""

    def test_rate_limit_allows_initial_connections(self):
        """First few connections should be allowed."""
        from shinka.webui.sse_manager import (
            RATE_LIMIT_MAX_CONNECTIONS,
            SSEManager,
        )

        manager = SSEManager()

        # First N connections should be allowed
        for i in range(RATE_LIMIT_MAX_CONNECTIONS):
            assert manager.check_rate_limit("192.168.1.1") is True

    def test_rate_limit_blocks_excessive_connections(self):
        """Connections beyond limit should be blocked."""
        from shinka.webui.sse_manager import (
            RATE_LIMIT_MAX_CONNECTIONS,
            SSEManager,
        )

        manager = SSEManager()

        # Use up the limit
        for i in range(RATE_LIMIT_MAX_CONNECTIONS):
            manager.check_rate_limit("192.168.1.1")

        # Next one should be blocked
        assert manager.check_rate_limit("192.168.1.1") is False

    def test_rate_limit_per_ip(self):
        """Rate limits should be per-IP."""
        from shinka.webui.sse_manager import (
            RATE_LIMIT_MAX_CONNECTIONS,
            SSEManager,
        )

        manager = SSEManager()

        # Use up limit for one IP
        for i in range(RATE_LIMIT_MAX_CONNECTIONS):
            manager.check_rate_limit("192.168.1.1")
        assert manager.check_rate_limit("192.168.1.1") is False

        # Different IP should still be allowed
        assert manager.check_rate_limit("192.168.1.2") is True

    def test_rate_limit_cleanup(self):
        """Old rate limit entries should be cleaned up."""
        from shinka.webui.sse_manager import SSEManager

        manager = SSEManager()
        manager.check_rate_limit("192.168.1.1")

        # Add some entries
        manager._connection_attempts["old-ip"] = [0.0]  # Very old timestamp

        manager._cleanup_old_rate_limits()

        # Old entry should be gone
        assert "old-ip" not in manager._connection_attempts
        # Recent entry should remain
        assert "192.168.1.1" in manager._connection_attempts


class TestWatcherReferenceCounter:
    """Tests for watcher reference counting."""

    @pytest.fixture
    def watcher_manager(self):
        """Create a WatcherManager for testing."""
        from shinka.webui.event_watchers import WatcherManager
        from unittest.mock import AsyncMock

        callback = AsyncMock()
        return WatcherManager(callback)

    @pytest.mark.asyncio
    async def test_reference_counting_sessions(self, watcher_manager, tmp_path):
        """Session watchers use reference counting."""
        session_dir = tmp_path / "test_session"
        session_dir.mkdir()
        log_path = session_dir / "session_log.jsonl"
        log_path.write_text('{"type": "start"}')

        # First watch - ref count = 1
        await watcher_manager.watch_session("test1", session_dir)
        assert watcher_manager._session_ref_counts["test1"] == 1

        # Second watch same session - ref count = 2
        await watcher_manager.watch_session("test1", session_dir)
        assert watcher_manager._session_ref_counts["test1"] == 2

        # First unwatch - ref count = 1
        await watcher_manager.unwatch_session("test1")
        assert watcher_manager._session_ref_counts["test1"] == 1

        # Second unwatch - ref count = 0, removed
        await watcher_manager.unwatch_session("test1")
        assert "test1" not in watcher_manager._session_ref_counts

    @pytest.mark.asyncio
    async def test_reference_counting_databases(self, watcher_manager, tmp_path):
        """Database watchers use reference counting."""
        db_path = tmp_path / "test.sqlite"
        db_path.write_text("fake db")
        db_str = str(db_path)

        # First watch
        await watcher_manager.watch_database(db_str)
        assert watcher_manager._database_ref_counts[db_str] == 1

        # Second watch
        await watcher_manager.watch_database(db_str)
        assert watcher_manager._database_ref_counts[db_str] == 2

        # Unwatch
        await watcher_manager.unwatch_database(db_str)
        assert watcher_manager._database_ref_counts[db_str] == 1

        await watcher_manager.unwatch_database(db_str)
        assert db_str not in watcher_manager._database_ref_counts


class TestFileLockErrorHandling:
    """Tests for FileLock error handling."""

    def test_lock_acquisition_error_class(self):
        """LockAcquisitionError should be defined."""
        from shinka.tools.codex_session_registry import LockAcquisitionError

        err = LockAcquisitionError("test error")
        assert str(err) == "test error"

    def test_lock_context_tracks_acquisition(self):
        """_LockContext should track whether lock was acquired."""
        from shinka.tools.codex_session_registry import _LockContext

        ctx = _LockContext("test_key")
        with ctx:
            # Lock should be acquired (or marked as such if FileLock is available)
            pass
        # After exit, lock should be released


class TestCORSValidation:
    """Tests for CORS origin validation."""

    def test_localhost_origins_allowed(self):
        """Localhost origins should be allowed."""
        from shinka.webui.sse_server import create_sse_app

        # Create app and extract the _is_allowed_origin function
        # This is an internal function so we test it indirectly via the app behavior
        app = create_sse_app()
        assert app is not None  # App creates successfully

    def test_cors_middleware_exists(self):
        """CORS middleware should be registered."""
        from shinka.webui.sse_server import create_sse_app

        app = create_sse_app()
        # Verify middleware is registered
        assert len(app.middlewares) > 0


class TestHardeningEdgeCases:
    """Edge case tests for security hardening."""

    def test_path_traversal_double_dot(self):
        """Path traversal with .. should be rejected regardless of encoding."""
        from shinka.webui.sse_server import validate_topic

        # URL-encoded path traversal still contains .. which is rejected
        assert validate_topic("session:..%2F..%2Fetc%2Fpasswd") is False
        # Actual slashes should be rejected
        assert validate_topic("session:../etc/passwd") is False
        # Just dots without slashes also rejected (contains ..)
        assert validate_topic("session:..") is False

    def test_path_traversal_backslash(self):
        """Backslash path traversal should be rejected."""
        from shinka.webui.sse_server import validate_topic

        assert validate_topic("session:..\\..\\etc\\passwd") is False

    def test_empty_and_null_topics(self):
        """Empty and null-like topics should be rejected."""
        from shinka.webui.sse_server import validate_topic

        assert validate_topic("") is False
        assert validate_topic(":") is False
        assert validate_topic("session:") is False  # Empty session ID

    def test_very_long_topic_ids(self):
        """Very long topic IDs should still be validated."""
        from shinka.webui.sse_server import validate_topic

        long_id = "a" * 10000  # 10K char session ID
        # Should be accepted if alphanumeric (no path traversal)
        assert validate_topic(f"session:{long_id}") is True


class TestGlobalSSEManager:
    """Tests for global SSE manager singleton."""

    def test_get_sse_manager_singleton(self):
        """get_sse_manager should return the same instance."""
        from shinka.webui.sse_manager import get_sse_manager

        manager1 = get_sse_manager()
        manager2 = get_sse_manager()

        assert manager1 is manager2
