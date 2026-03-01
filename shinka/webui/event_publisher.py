"""Thread-safe event publisher for sync-to-async SSE bridging.

This module provides a way for the evolution runner (synchronous, threaded) to
emit events to the SSE server (asynchronous). It uses a thread-safe queue and
a dedicated background thread to process events.

Architecture:
    - Sync threads call emit() which queues events (non-blocking)
    - Internal async loop processes queue and broadcasts via SSEManager
    - HTTP POST fallback for separate-process SSE servers

Example:
    # At runner initialization
    publisher = EventPublisher(db_path=str(db_path))
    publisher.start()

    # At event points
    publisher.emit_node_created(
        generation=gen,
        node_id=program.id,
        score=program.combined_score,
    )

    # At shutdown
    publisher.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional
from urllib.parse import urljoin

if TYPE_CHECKING:
    from .sse_manager import SSEManager

logger = logging.getLogger(__name__)

# Environment variable to configure SSE server URL for HTTP fallback
SSE_SERVER_URL_ENV = "SHINKA_SSE_SERVER_URL"
DEFAULT_SSE_PORT = 8001


class EventType(str, Enum):
    """Evolution event types for SSE broadcasting."""

    # Job lifecycle events
    JOB_SUBMITTED = "job_submitted"
    MUTATION_STARTED = "mutation_started"
    MUTATION_COMPLETED = "mutation_completed"
    EVALUATION_STARTED = "evaluation_started"
    EVALUATION_COMPLETED = "evaluation_completed"

    # Node/program events
    NODE_CREATED = "node_created"
    BEST_UPDATED = "best_updated"

    # Run lifecycle events
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_ERROR = "run_error"
    GENERATION_COMPLETED = "generation_completed"

    # Island events (for island-based evolution)
    MIGRATION_OCCURRED = "migration_occurred"


@dataclass
class EvolutionEvent:
    """Structured event payload for evolution updates."""

    event_type: EventType
    timestamp: float = field(default_factory=time.time)

    # Run identification
    db_path: Optional[str] = None
    results_dir: Optional[str] = None

    # Job/node identification
    job_id: Optional[str] = None
    generation: Optional[int] = None
    node_id: Optional[str] = None
    parent_id: Optional[str] = None

    # Metrics
    score: Optional[float] = None
    correct: Optional[bool] = None
    runtime: Optional[float] = None

    # Additional metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        d["event_type"] = self.event_type.value
        # Remove None values for cleaner payloads
        return {k: v for k, v in d.items() if v is not None}


class EventPublisher:
    """Thread-safe event publisher for sync-to-async SSE bridging.

    This class provides a thread-safe interface for emitting evolution events
    from the runner (synchronous, threaded) to the SSE server (asynchronous).

    Architecture:
        - Sync threads call emit() which queues events
        - Internal async loop processes queue and broadcasts via SSEManager
        - HTTP POST fallback for separate-process SSE servers

    Attributes:
        db_path: Path to evolution database (for topic routing).
        results_dir: Results directory path.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        results_dir: Optional[str] = None,
        sse_manager: Optional["SSEManager"] = None,
        sse_server_url: Optional[str] = None,
        max_queue_size: int = 1000,
    ):
        """Initialize EventPublisher.

        Args:
            db_path: Path to evolution database (for topic routing).
            results_dir: Results directory path.
            sse_manager: Direct reference to SSEManager (in-process mode).
            sse_server_url: URL of SSE server for HTTP POST fallback.
                           Defaults to env var SHINKA_SSE_SERVER_URL or localhost:8001.
            max_queue_size: Maximum queued events before dropping oldest.
        """
        self._db_path = db_path
        self._results_dir = results_dir
        self._sse_manager = sse_manager

        # HTTP fallback configuration
        self._sse_server_url = (
            sse_server_url
            or os.environ.get(SSE_SERVER_URL_ENV)
            or f"http://localhost:{DEFAULT_SSE_PORT}"
        )

        # Thread-safe event queue
        self._event_queue: queue.Queue[Optional[EvolutionEvent]] = queue.Queue(
            maxsize=max_queue_size
        )

        # Control flags
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None

        # HTTP session for fallback (lazy init)
        self._http_session = None

        # Statistics
        self._events_emitted = 0
        self._events_dropped = 0
        self._events_delivered = 0
        self._last_error: Optional[str] = None

    @property
    def db_path(self) -> Optional[str]:
        """Get the database path."""
        return self._db_path

    @db_path.setter
    def db_path(self, value: str) -> None:
        """Set the database path."""
        self._db_path = value

    def start(self) -> None:
        """Start the event publisher background thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_event_loop,
            name="EventPublisher",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"EventPublisher started (db_path={self._db_path}, "
            f"sse_url={self._sse_server_url})"
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the event publisher gracefully.

        Args:
            timeout: Maximum seconds to wait for pending events.
        """
        if not self._running:
            return

        self._running = False

        # Signal shutdown via sentinel
        try:
            self._event_queue.put_nowait(None)
        except queue.Full:
            pass

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("EventPublisher thread did not stop cleanly")

        logger.info(
            f"EventPublisher stopped (emitted={self._events_emitted}, "
            f"delivered={self._events_delivered}, dropped={self._events_dropped})"
        )

    def emit(self, event: EvolutionEvent) -> bool:
        """Emit an event (thread-safe, non-blocking).

        Args:
            event: The evolution event to emit.

        Returns:
            True if queued successfully, False if queue full.
        """
        if not self._running:
            logger.debug("EventPublisher not running, event dropped")
            return False

        # Populate default fields
        if event.db_path is None:
            event.db_path = self._db_path
        if event.results_dir is None:
            event.results_dir = self._results_dir

        try:
            self._event_queue.put_nowait(event)
            self._events_emitted += 1
            return True
        except queue.Full:
            self._events_dropped += 1
            logger.warning(
                f"EventPublisher queue full, dropped event: {event.event_type}"
            )
            return False

    # -------------------------------------------------------------------------
    # Convenience methods for common events
    # -------------------------------------------------------------------------

    def emit_run_started(
        self,
        **metadata: Any,
    ) -> None:
        """Convenience method for run start event."""
        self.emit(
            EvolutionEvent(
                event_type=EventType.RUN_STARTED,
                metadata=metadata,
            )
        )

    def emit_run_completed(
        self,
        total_generations: int,
        best_score: Optional[float] = None,
        **metadata: Any,
    ) -> None:
        """Convenience method for run completion event."""
        self.emit(
            EvolutionEvent(
                event_type=EventType.RUN_COMPLETED,
                score=best_score,
                metadata={"total_generations": total_generations, **metadata},
            )
        )

    def emit_generation_completed(
        self,
        generation: int,
        best_score: Optional[float] = None,
        programs_count: int = 0,
        **metadata: Any,
    ) -> None:
        """Convenience method for generation completion event."""
        self.emit(
            EvolutionEvent(
                event_type=EventType.GENERATION_COMPLETED,
                generation=generation,
                score=best_score,
                metadata={"programs_count": programs_count, **metadata},
            )
        )

    def emit_job_submitted(
        self,
        generation: int,
        job_id: str,
        parent_id: Optional[str] = None,
        **metadata: Any,
    ) -> None:
        """Convenience method for job submission event."""
        self.emit(
            EvolutionEvent(
                event_type=EventType.JOB_SUBMITTED,
                generation=generation,
                job_id=job_id,
                parent_id=parent_id,
                metadata=metadata,
            )
        )

    def emit_mutation_started(
        self,
        generation: int,
        job_id: str,
        parent_id: Optional[str] = None,
        **metadata: Any,
    ) -> None:
        """Convenience method for mutation start event."""
        self.emit(
            EvolutionEvent(
                event_type=EventType.MUTATION_STARTED,
                generation=generation,
                job_id=job_id,
                parent_id=parent_id,
                metadata=metadata,
            )
        )

    def emit_mutation_completed(
        self,
        generation: int,
        job_id: str,
        success: bool = True,
        **metadata: Any,
    ) -> None:
        """Convenience method for mutation completion event."""
        self.emit(
            EvolutionEvent(
                event_type=EventType.MUTATION_COMPLETED,
                generation=generation,
                job_id=job_id,
                correct=success,
                metadata=metadata,
            )
        )

    def emit_evaluation_started(
        self,
        generation: int,
        job_id: str,
        node_id: Optional[str] = None,
        **metadata: Any,
    ) -> None:
        """Convenience method for evaluation start event."""
        self.emit(
            EvolutionEvent(
                event_type=EventType.EVALUATION_STARTED,
                generation=generation,
                job_id=job_id,
                node_id=node_id,
                metadata=metadata,
            )
        )

    def emit_evaluation_completed(
        self,
        generation: int,
        node_id: str,
        score: float,
        correct: bool,
        runtime: Optional[float] = None,
        **metadata: Any,
    ) -> None:
        """Convenience method for evaluation completion event."""
        self.emit(
            EvolutionEvent(
                event_type=EventType.EVALUATION_COMPLETED,
                generation=generation,
                node_id=node_id,
                score=score,
                correct=correct,
                runtime=runtime,
                metadata=metadata,
            )
        )

    def emit_node_created(
        self,
        generation: int,
        node_id: str,
        parent_id: Optional[str] = None,
        score: Optional[float] = None,
        correct: Optional[bool] = None,
        **metadata: Any,
    ) -> None:
        """Convenience method for node creation event."""
        self.emit(
            EvolutionEvent(
                event_type=EventType.NODE_CREATED,
                generation=generation,
                node_id=node_id,
                parent_id=parent_id,
                score=score,
                correct=correct,
                metadata=metadata,
            )
        )

    def emit_best_updated(
        self,
        generation: int,
        node_id: str,
        score: float,
        previous_best_score: Optional[float] = None,
        **metadata: Any,
    ) -> None:
        """Convenience method for best program update event."""
        self.emit(
            EvolutionEvent(
                event_type=EventType.BEST_UPDATED,
                generation=generation,
                node_id=node_id,
                score=score,
                metadata={"previous_best_score": previous_best_score, **metadata},
            )
        )

    def get_stats(self) -> Dict[str, Any]:
        """Get publisher statistics."""
        return {
            "running": self._running,
            "events_emitted": self._events_emitted,
            "events_delivered": self._events_delivered,
            "events_dropped": self._events_dropped,
            "queue_size": self._event_queue.qsize(),
            "last_error": self._last_error,
        }

    # -------------------------------------------------------------------------
    # Internal methods
    # -------------------------------------------------------------------------

    def _run_event_loop(self) -> None:
        """Internal: Run the async event processing loop."""
        # Create dedicated event loop for this thread
        self._async_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._async_loop)

        try:
            self._async_loop.run_until_complete(self._process_events())
        except Exception as e:
            logger.error(f"EventPublisher loop error: {e}")
            self._last_error = str(e)
        finally:
            # Close HTTP session if it was created
            if self._http_session is not None:
                try:
                    self._async_loop.run_until_complete(self._http_session.close())
                except Exception:
                    pass
            self._async_loop.close()
            self._async_loop = None

    async def _process_events(self) -> None:
        """Internal: Async event processing loop."""
        while self._running:
            try:
                # Block with timeout to allow checking _running flag
                try:
                    event = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: self._event_queue.get(timeout=0.5)
                    )
                except queue.Empty:
                    continue

                # Sentinel for shutdown
                if event is None:
                    break

                # Broadcast event
                await self._broadcast_event(event)
                self._events_delivered += 1

            except Exception as e:
                logger.error(f"EventPublisher error processing event: {e}")
                self._last_error = str(e)

    async def _broadcast_event(self, event: EvolutionEvent) -> None:
        """Internal: Broadcast event via SSEManager or HTTP fallback."""
        event_data = event.to_dict()

        # Determine topic based on event type
        topic = self._get_topic_for_event(event)

        # Try in-process SSEManager first
        if self._sse_manager is not None:
            try:
                await self._sse_manager.broadcast(
                    topic=topic,
                    event_type=event.event_type.value,
                    data=event_data,
                )
                logger.debug(f"Broadcast event via SSEManager: {event.event_type}")
                return
            except Exception as e:
                logger.warning(f"SSEManager broadcast failed: {e}, trying HTTP")

        # HTTP POST fallback
        await self._broadcast_via_http(topic, event.event_type.value, event_data)

    async def _broadcast_via_http(
        self,
        topic: str,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Internal: Broadcast event via HTTP POST to SSE server."""
        try:
            import aiohttp

            if self._http_session is None:
                self._http_session = aiohttp.ClientSession()

            url = urljoin(self._sse_server_url, "/api/events/publish")
            payload = {
                "topic": topic,
                "event_type": event_type,
                "data": data,
            }

            async with self._http_session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=5.0),
            ) as response:
                if response.status != 200:
                    logger.warning(f"HTTP broadcast failed: {response.status}")
                else:
                    logger.debug(f"Broadcast event via HTTP: {event_type}")

        except ImportError:
            logger.warning("aiohttp not available for HTTP fallback")
        except Exception as e:
            logger.debug(f"HTTP broadcast error: {e}")
            self._last_error = str(e)

    def _get_topic_for_event(self, event: EvolutionEvent) -> str:
        """Internal: Determine SSE topic for an event."""
        # Database-scoped events use tree:{db_path} topic
        if event.db_path:
            return f"tree:{event.db_path}"

        # Job events use jobs: topic (global)
        if event.event_type in (
            EventType.JOB_SUBMITTED,
            EventType.MUTATION_STARTED,
            EventType.MUTATION_COMPLETED,
            EventType.EVALUATION_STARTED,
        ):
            return "jobs:"

        # Default to global jobs topic
        return "jobs:"


# Singleton instance for global access
_global_publisher: Optional[EventPublisher] = None
_global_publisher_lock = threading.Lock()


def get_event_publisher() -> Optional[EventPublisher]:
    """Get the global EventPublisher instance.

    Returns:
        The global EventPublisher if initialized, None otherwise.
    """
    return _global_publisher


def set_event_publisher(publisher: Optional[EventPublisher]) -> None:
    """Set the global EventPublisher instance.

    Args:
        publisher: The EventPublisher to set as global, or None to clear.
    """
    global _global_publisher
    with _global_publisher_lock:
        _global_publisher = publisher


def create_event_publisher(
    db_path: Optional[str] = None,
    results_dir: Optional[str] = None,
    auto_start: bool = True,
    set_global: bool = True,
) -> EventPublisher:
    """Create and optionally start an EventPublisher.

    This attempts to get a reference to the global SSEManager if available.

    Args:
        db_path: Evolution database path for topic routing.
        results_dir: Results directory path.
        auto_start: Whether to start the publisher immediately.
        set_global: Whether to set this as the global publisher.

    Returns:
        Configured EventPublisher instance.
    """
    sse_manager = None

    # Try to get global SSEManager if in same process
    try:
        from .sse_manager import get_sse_manager

        sse_manager = get_sse_manager()
    except Exception:
        pass  # SSE server not running in this process

    publisher = EventPublisher(
        db_path=db_path,
        results_dir=results_dir,
        sse_manager=sse_manager,
    )

    if auto_start:
        publisher.start()

    if set_global:
        set_event_publisher(publisher)

    return publisher
