"""SSE connection management with event buffering and replay support.

This module manages Server-Sent Events connections for the hybrid architecture
where SSE runs on a separate port alongside the existing REST server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

from aiohttp import web

logger = logging.getLogger(__name__)

# Maximum concurrent SSE connections (security limit)
MAX_SSE_CONNECTIONS = 100

# Maximum events to buffer per topic for replay (Last-Event-ID support)
MAX_EVENT_BUFFER = 100

# Heartbeat interval in seconds
HEARTBEAT_INTERVAL = 15

# Rate limiting settings
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_CONNECTIONS = 10  # max connections per IP per window


@dataclass
class SSEClient:
    """Represents a connected SSE client."""

    client_id: str
    response: web.StreamResponse
    topics: Set[str]
    connected_at: float = field(default_factory=time.time)
    last_event_id: Optional[str] = None


@dataclass
class BufferedEvent:
    """An event stored for replay."""

    event_id: str
    event_type: str
    data: Dict[str, Any]
    timestamp: float


class SSEManager:
    """Manages SSE connections, broadcasting, and event buffering.

    Features:
    - Topic-based subscriptions (session:{id}, jobs:{db_path}, tree:{db_path})
    - Connection limiting (MAX_SSE_CONNECTIONS)
    - Event buffering for Last-Event-ID replay
    - Heartbeat to detect dead connections
    """

    def __init__(self):
        self._clients: Dict[str, SSEClient] = {}
        self._topic_subscribers: Dict[str, Set[str]] = {}  # topic -> client_ids
        self._event_buffer: Dict[str, deque] = {}  # topic -> events
        self._event_counter = 0
        self._lock = asyncio.Lock()
        # Rate limiting: IP -> list of connection timestamps
        self._connection_attempts: Dict[str, list] = {}

    @property
    def connection_count(self) -> int:
        """Current number of connected clients."""
        return len(self._clients)

    def check_rate_limit(self, client_ip: str) -> bool:
        """Check if a client IP is within rate limits.

        Args:
            client_ip: The client's IP address.

        Returns:
            True if connection is allowed, False if rate limited.
        """
        now = time.time()

        # Clean up old entries and check current count
        attempts = self._connection_attempts.get(client_ip, [])
        # Keep only attempts within the window
        attempts = [t for t in attempts if now - t < RATE_LIMIT_WINDOW]

        if len(attempts) >= RATE_LIMIT_MAX_CONNECTIONS:
            logger.warning(
                f"Rate limit exceeded for {client_ip}: "
                f"{len(attempts)} connections in {RATE_LIMIT_WINDOW}s"
            )
            return False

        # Record this attempt
        attempts.append(now)
        self._connection_attempts[client_ip] = attempts
        return True

    def _cleanup_old_rate_limits(self) -> None:
        """Clean up stale rate limit entries (call periodically)."""
        now = time.time()
        stale_ips = []
        for ip, attempts in self._connection_attempts.items():
            # Remove entries older than window
            fresh = [t for t in attempts if now - t < RATE_LIMIT_WINDOW]
            if not fresh:
                stale_ips.append(ip)
            else:
                self._connection_attempts[ip] = fresh
        for ip in stale_ips:
            del self._connection_attempts[ip]

    async def register(
        self,
        response: web.StreamResponse,
        topics: Set[str],
        last_event_id: Optional[str] = None,
    ) -> Optional[str]:
        """Register a new SSE client.

        Args:
            response: The aiohttp StreamResponse to write events to.
            topics: Set of topics to subscribe to.
            last_event_id: Optional Last-Event-ID header for replay.

        Returns:
            Client ID if registered successfully, None if connection limit reached.
        """
        events_to_replay = []

        async with self._lock:
            if len(self._clients) >= MAX_SSE_CONNECTIONS:
                logger.warning(
                    f"SSE connection limit reached ({MAX_SSE_CONNECTIONS}), rejecting new client"
                )
                return None

            client_id = str(uuid.uuid4())
            client = SSEClient(
                client_id=client_id,
                response=response,
                topics=topics,
                last_event_id=last_event_id,
            )
            self._clients[client_id] = client

            # Subscribe to topics
            for topic in topics:
                if topic not in self._topic_subscribers:
                    self._topic_subscribers[topic] = set()
                self._topic_subscribers[topic].add(client_id)

            logger.info(
                f"SSE client {client_id[:8]} registered for topics: {topics} "
                f"(total: {len(self._clients)})"
            )

            # FIX: Collect events to replay while holding lock (to avoid race)
            if last_event_id:
                events_to_replay = self._collect_replay_events(client.topics, last_event_id)

        # FIX: Replay events OUTSIDE the lock (network I/O can be slow)
        if events_to_replay:
            await self._send_replay_events(client, events_to_replay)

        return client_id

    async def unregister(self, client_id: str) -> None:
        """Unregister a client and clean up subscriptions."""
        async with self._lock:
            client = self._clients.pop(client_id, None)
            if client is None:
                return

            # Remove from topic subscriptions
            for topic in client.topics:
                if topic in self._topic_subscribers:
                    self._topic_subscribers[topic].discard(client_id)
                    if not self._topic_subscribers[topic]:
                        del self._topic_subscribers[topic]
                        # FIX: Clean up event buffer when no subscribers remain
                        if topic in self._event_buffer:
                            del self._event_buffer[topic]
                            logger.debug(f"Cleaned up event buffer for topic: {topic}")

            logger.info(
                f"SSE client {client_id[:8]} unregistered (remaining: {len(self._clients)})"
            )

    async def broadcast(
        self,
        topic: str,
        event_type: str,
        data: Dict[str, Any],
    ) -> int:
        """Broadcast an event to all subscribers of a topic.

        Args:
            topic: The topic to broadcast to (e.g., "session:abc123").
            event_type: SSE event type (e.g., "session", "jobs", "tree").
            data: JSON-serializable event data.

        Returns:
            Number of clients that received the event.
        """
        failed_clients = []
        sent_count = 0

        async with self._lock:
            # Generate event ID and buffer
            self._event_counter += 1
            event_id = str(self._event_counter)

            buffered = BufferedEvent(
                event_id=event_id,
                event_type=event_type,
                data=data,
                timestamp=time.time(),
            )

            if topic not in self._event_buffer:
                self._event_buffer[topic] = deque(maxlen=MAX_EVENT_BUFFER)
            self._event_buffer[topic].append(buffered)

            # Get subscribers snapshot
            subscriber_ids = list(self._topic_subscribers.get(topic, set()))
            if not subscriber_ids:
                return 0

            # Format SSE message
            message = self._format_sse_message(event_id, event_type, data)

            # Send to all subscribers
            for client_id in subscriber_ids:
                client = self._clients.get(client_id)
                if client is None:
                    continue

                try:
                    await client.response.write(message.encode("utf-8"))
                    sent_count += 1
                except Exception as e:
                    logger.warning(f"Failed to send to client {client_id[:8]}: {e}")
                    failed_clients.append(client_id)

        # FIX: Clean up failed clients OUTSIDE the lock to prevent deadlock
        for client_id in failed_clients:
            await self.unregister(client_id)

        return sent_count

    async def send_heartbeat(self) -> int:
        """Send heartbeat to all connected clients.

        Returns:
            Number of clients that received the heartbeat.
        """
        message = self._format_sse_message(
            event_id=None,
            event_type="heartbeat",
            data={"ts": time.time()},
        )

        sent_count = 0
        failed_clients = []

        async with self._lock:
            for client_id, client in list(self._clients.items()):
                try:
                    await client.response.write(message.encode("utf-8"))
                    sent_count += 1
                except Exception as e:
                    logger.debug(f"Heartbeat failed for {client_id[:8]}: {e}")
                    failed_clients.append(client_id)

        # Clean up failed clients
        for client_id in failed_clients:
            await self.unregister(client_id)

        return sent_count

    def _collect_replay_events(
        self, topics: Set[str], last_event_id: str
    ) -> list:
        """Collect events to replay (called while holding lock).

        Returns a list of BufferedEvent objects that should be replayed.
        """
        try:
            last_id = int(last_event_id)
        except ValueError:
            return []

        events = []
        for topic in topics:
            buffer = self._event_buffer.get(topic, deque())
            for event in buffer:
                try:
                    event_id_int = int(event.event_id)
                    if event_id_int > last_id:
                        events.append(event)
                except ValueError:
                    pass
        return events

    async def _send_replay_events(
        self, client: SSEClient, events: list
    ) -> None:
        """Send replay events to client (called without lock)."""
        for event in events:
            try:
                message = self._format_sse_message(
                    event.event_id, event.event_type, event.data
                )
                await client.response.write(message.encode("utf-8"))
            except Exception as e:
                logger.debug(f"Replay error: {e}")
                break  # Stop replaying if connection fails

    def _format_sse_message(
        self,
        event_id: Optional[str],
        event_type: str,
        data: Dict[str, Any],
    ) -> str:
        """Format data as an SSE message."""
        lines = []
        if event_id:
            lines.append(f"id: {event_id}")
        lines.append(f"event: {event_type}")
        lines.append(f"data: {json.dumps(data)}")
        lines.append("")  # Empty line to end message
        lines.append("")
        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """Get current SSE manager statistics."""
        return {
            "total_clients": len(self._clients),
            "max_clients": MAX_SSE_CONNECTIONS,
            "topics": list(self._topic_subscribers.keys()),
            "event_counter": self._event_counter,
            "buffer_sizes": {
                topic: len(buffer) for topic, buffer in self._event_buffer.items()
            },
        }


# Global SSE manager instance
_sse_manager: Optional[SSEManager] = None


def get_sse_manager() -> SSEManager:
    """Get or create the global SSE manager instance."""
    global _sse_manager
    if _sse_manager is None:
        _sse_manager = SSEManager()
    return _sse_manager
