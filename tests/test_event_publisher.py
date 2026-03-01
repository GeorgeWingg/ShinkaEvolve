"""Tests for the EventPublisher module."""

import asyncio
import pytest
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from shinka.webui.event_publisher import (
    EventPublisher,
    EventType,
    EvolutionEvent,
    create_event_publisher,
    get_event_publisher,
    set_event_publisher,
)


class TestEvolutionEvent:
    """Tests for EvolutionEvent dataclass."""

    def test_event_default_timestamp(self):
        """Event should auto-populate timestamp."""
        before = time.time()
        event = EvolutionEvent(event_type=EventType.NODE_CREATED)
        after = time.time()
        assert before <= event.timestamp <= after

    def test_event_to_dict_removes_none(self):
        """to_dict() should remove None values."""
        event = EvolutionEvent(
            event_type=EventType.NODE_CREATED,
            generation=5,
            node_id="abc123",
            parent_id=None,  # This should be excluded
        )
        d = event.to_dict()
        assert "event_type" in d
        assert d["event_type"] == "node_created"
        assert "generation" in d
        assert d["generation"] == 5
        assert "parent_id" not in d  # None values excluded

    def test_event_metadata_included(self):
        """Metadata dict should be included in to_dict()."""
        event = EvolutionEvent(
            event_type=EventType.BEST_UPDATED,
            metadata={"extra": "data", "count": 42},
        )
        d = event.to_dict()
        assert d["metadata"] == {"extra": "data", "count": 42}


class TestEventPublisher:
    """Tests for EventPublisher class."""

    def test_publisher_not_started_by_default(self):
        """Publisher should not be running until start() is called."""
        publisher = EventPublisher()
        assert not publisher._running
        assert publisher.emit(EvolutionEvent(EventType.RUN_STARTED)) is False

    def test_publisher_start_stop(self):
        """Publisher should start and stop cleanly."""
        publisher = EventPublisher()
        publisher.start()
        assert publisher._running
        assert publisher._thread is not None
        assert publisher._thread.is_alive()

        publisher.stop()
        assert not publisher._running
        # Thread should stop
        publisher._thread.join(timeout=2.0)
        assert not publisher._thread.is_alive()

    def test_emit_queues_event(self):
        """emit() should queue events when running."""
        publisher = EventPublisher()
        publisher.start()

        event = EvolutionEvent(
            event_type=EventType.NODE_CREATED,
            generation=1,
            node_id="test-node",
        )
        result = publisher.emit(event)
        assert result is True
        assert publisher._events_emitted == 1

        publisher.stop()

    def test_emit_drops_when_not_running(self):
        """emit() should drop events when not running."""
        publisher = EventPublisher()
        event = EvolutionEvent(event_type=EventType.RUN_STARTED)
        result = publisher.emit(event)
        assert result is False

    def test_convenience_methods(self):
        """Convenience methods should create proper events."""
        publisher = EventPublisher(db_path="/test/db.sqlite")
        publisher.start()

        # Test each convenience method
        publisher.emit_run_started(max_workers=4)
        publisher.emit_job_submitted(generation=1, job_id="job-1")
        publisher.emit_mutation_started(generation=1, job_id="job-1")
        publisher.emit_mutation_completed(generation=1, job_id="job-1", success=True)
        publisher.emit_evaluation_started(generation=1, job_id="job-1")
        publisher.emit_node_created(
            generation=1, node_id="node-1", score=0.85, correct=True
        )
        publisher.emit_evaluation_completed(
            generation=1, node_id="node-1", score=0.85, correct=True
        )
        publisher.emit_best_updated(
            generation=1, node_id="node-1", score=0.9, previous_best_score=0.85
        )
        publisher.emit_run_completed(total_generations=10, best_score=0.9)

        # All 9 events should be emitted
        assert publisher._events_emitted == 9

        publisher.stop()

    def test_db_path_populated_on_events(self):
        """Events should inherit db_path from publisher."""
        publisher = EventPublisher(
            db_path="/test/evolution.sqlite",
            results_dir="/test/results",
        )
        publisher.start()

        event = EvolutionEvent(event_type=EventType.NODE_CREATED, generation=1)
        publisher.emit(event)

        # Wait a bit for the queue to process
        time.sleep(0.1)

        # The event's db_path should be populated
        assert event.db_path == "/test/evolution.sqlite"
        assert event.results_dir == "/test/results"

        publisher.stop()

    def test_queue_full_drops_events(self):
        """Events should be dropped when queue is full."""
        publisher = EventPublisher(max_queue_size=5)
        # Don't start - queue will fill but not drain
        publisher._running = True  # Fake running to allow emit

        for i in range(10):
            publisher.emit(EvolutionEvent(event_type=EventType.NODE_CREATED))

        # First 5 should succeed, rest dropped
        assert publisher._events_emitted == 5
        assert publisher._events_dropped == 5

        publisher._running = False

    def test_get_stats(self):
        """get_stats() should return accurate statistics."""
        publisher = EventPublisher()
        publisher.start()

        for _ in range(3):
            publisher.emit(EvolutionEvent(event_type=EventType.NODE_CREATED))

        stats = publisher.get_stats()
        assert stats["running"] is True
        assert stats["events_emitted"] == 3
        assert stats["events_dropped"] == 0

        publisher.stop()


class TestGlobalPublisher:
    """Tests for global publisher functions."""

    def test_get_set_global_publisher(self):
        """Should be able to get/set global publisher."""
        # Clear any existing
        set_event_publisher(None)
        assert get_event_publisher() is None

        publisher = EventPublisher()
        set_event_publisher(publisher)
        assert get_event_publisher() is publisher

        # Cleanup
        set_event_publisher(None)

    def test_create_event_publisher_sets_global(self):
        """create_event_publisher with set_global=True should set global."""
        # Clear any existing
        set_event_publisher(None)

        publisher = create_event_publisher(
            db_path="/test/db.sqlite",
            auto_start=False,
            set_global=True,
        )
        assert get_event_publisher() is publisher

        # Cleanup
        set_event_publisher(None)


class TestTopicRouting:
    """Tests for topic routing logic."""

    def test_topic_routing_tree_topic(self):
        """Events with db_path should route to tree: topic."""
        publisher = EventPublisher(db_path="/test/evolution.sqlite")
        event = EvolutionEvent(
            event_type=EventType.NODE_CREATED,
            db_path="/test/evolution.sqlite",
        )
        topic = publisher._get_topic_for_event(event)
        assert topic == "tree:/test/evolution.sqlite"

    def test_topic_routing_jobs_topic(self):
        """Job-related events should route to jobs: topic."""
        publisher = EventPublisher()
        for event_type in [
            EventType.JOB_SUBMITTED,
            EventType.MUTATION_STARTED,
            EventType.MUTATION_COMPLETED,
            EventType.EVALUATION_STARTED,
        ]:
            event = EvolutionEvent(event_type=event_type)
            topic = publisher._get_topic_for_event(event)
            assert topic == "jobs:"


@pytest.mark.asyncio
class TestAsyncBroadcast:
    """Tests for async broadcasting."""

    async def test_broadcast_to_sse_manager(self):
        """Should broadcast to SSEManager when available."""
        mock_sse_manager = AsyncMock()
        mock_sse_manager.broadcast = AsyncMock(return_value=2)

        publisher = EventPublisher(
            db_path="/test/db.sqlite",
            sse_manager=mock_sse_manager,
        )
        publisher.start()

        publisher.emit_node_created(
            generation=1,
            node_id="test-node",
            score=0.9,
            correct=True,
        )

        # Wait for async processing
        await asyncio.sleep(0.3)

        # SSEManager.broadcast should have been called
        assert mock_sse_manager.broadcast.called

        publisher.stop()
