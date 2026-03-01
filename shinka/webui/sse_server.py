"""Standalone aiohttp SSE server for the hybrid architecture.

This server runs on a separate port (default 8001) alongside the existing
REST server (port 8000). It handles Server-Sent Events for realtime updates.

Usage:
    from shinka.webui.sse_server import start_sse_server
    await start_sse_server(port=8001)

Or run directly:
    python -m shinka.webui.sse_server --port 8001
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import signal
import sys
from pathlib import Path
from typing import Optional, Set

from aiohttp import web

from .event_watchers import WatcherManager
from .sse_manager import (
    HEARTBEAT_INTERVAL,
    MAX_SSE_CONNECTIONS,
    SSEManager,
    get_sse_manager,
)

logger = logging.getLogger(__name__)

# Default SSE server port (separate from REST on 8000)
DEFAULT_SSE_PORT = 8001

# Validation patterns
UUID_PATTERN = re.compile(r"^[a-f0-9\-]{8,}$", re.IGNORECASE)


def validate_topic(topic: str, search_root: Optional[str] = None) -> bool:
    """Validate a topic subscription for security.

    Args:
        topic: Topic string like "session:abc123" or "tree:/path/to/db.sqlite"
        search_root: If provided, validate paths are under this root.

    Returns:
        True if valid, False otherwise.
    """
    if not topic or ":" not in topic:
        return False

    topic_type, topic_id = topic.split(":", 1)

    if topic_type == "session":
        # FIX: Reject path traversal characters in session IDs
        if "/" in topic_id or "\\" in topic_id or ".." in topic_id:
            logger.warning(f"Rejected session ID with path chars: {topic_id[:50]}")
            return False
        # Session IDs should be UUID-like or alphanumeric
        return bool(UUID_PATTERN.match(topic_id)) or topic_id.isalnum()

    elif topic_type in ("tree", "jobs", "db"):
        if not topic_id:
            # Empty topic_id for global jobs subscription
            return topic_type == "jobs"

        # FIX: Require search_root for path topics (fail closed)
        if not search_root:
            logger.warning(f"Rejected path topic without search_root: {topic_type}")
            return False

        # Database paths - validate path is under search_root
        try:
            path = Path(topic_id).resolve()
            root = Path(search_root).resolve()
            # Check path is under search_root (raises ValueError if not)
            path.relative_to(root)
            # FIX: Verify file exists and is a file (not directory)
            if not path.exists() or not path.is_file():
                logger.warning(f"Rejected non-existent or non-file path: {topic_id}")
                return False
        except (ValueError, RuntimeError) as e:
            logger.warning(f"Rejected path outside search_root: {topic_id}")
            return False

        return True

    return False


async def handle_sse_events(request: web.Request) -> web.StreamResponse:
    """Handle SSE connection requests.

    Query params:
        topics: Comma-separated list of topics to subscribe to
        last_event_id: Optional Last-Event-ID for replay

    Example:
        GET /api/events?topics=session:abc123,jobs:,tree:/path/db.sqlite
    """
    sse_manager = get_sse_manager()

    # Check connection limit
    if sse_manager.connection_count >= MAX_SSE_CONNECTIONS:
        return web.json_response(
            {"error": "Connection limit reached", "max": MAX_SSE_CONNECTIONS},
            status=503,
        )

    # Check rate limit
    client_ip = request.remote or "unknown"
    if not sse_manager.check_rate_limit(client_ip):
        return web.json_response(
            {"error": "Rate limit exceeded", "retry_after": 60},
            status=429,
        )

    # Parse topics
    topics_param = request.query.get("topics", "")
    if not topics_param:
        return web.json_response(
            {"error": "Missing 'topics' parameter"},
            status=400,
        )

    topics: Set[str] = set()
    search_root = request.app.get("search_root")

    for topic in topics_param.split(","):
        topic = topic.strip()
        if topic and validate_topic(topic, search_root):
            topics.add(topic)

    if not topics:
        return web.json_response(
            {"error": "No valid topics provided"},
            status=400,
        )

    # Get Last-Event-ID header
    last_event_id = request.headers.get("Last-Event-ID")

    # Create SSE response
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
    await response.prepare(request)

    # Register client
    client_id = await sse_manager.register(response, topics, last_event_id)
    if client_id is None:
        return web.json_response(
            {"error": "Failed to register client"},
            status=503,
        )

    # Track watchers registered for this client (for cleanup on disconnect)
    registered_sessions: list = []
    registered_databases: list = []

    # Register watchers for topics
    watcher_manager: WatcherManager = request.app.get("watcher_manager")
    if watcher_manager:
        for topic in topics:
            topic_type, topic_id = topic.split(":", 1)
            if topic_type == "session" and topic_id:
                # Try to find session directory
                # Sessions can be in /tmp/shinka_scratch/{session_id}/
                # or in a results directory
                session_dirs = [
                    Path(f"/tmp/shinka_scratch/{topic_id}"),
                    Path(f"/tmp/shinka_plan_sessions/{topic_id}"),
                ]
                for session_dir in session_dirs:
                    if session_dir.exists():
                        await watcher_manager.watch_session(topic_id, session_dir)
                        registered_sessions.append(topic_id)
                        break
            elif topic_type in ("tree", "db") and topic_id:
                await watcher_manager.watch_database(topic_id)
                registered_databases.append(topic_id)

    # Send initial connection event
    init_message = (
        f"event: connected\n"
        f"data: {{\"client_id\": \"{client_id}\", \"topics\": {list(topics)}}}\n\n"
    )
    try:
        await response.write(init_message.encode("utf-8"))
    except Exception:
        await sse_manager.unregister(client_id)
        # FIX: Clean up watchers on early disconnect
        if watcher_manager:
            for session_id in registered_sessions:
                await watcher_manager.unwatch_session(session_id)
            for db_path in registered_databases:
                await watcher_manager.unwatch_database(db_path)
        return response

    # Keep connection open until client disconnects
    try:
        while True:
            # Check if client is still connected
            if response.task is None or response.task.done():
                break
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await sse_manager.unregister(client_id)
        # FIX: Clean up watchers on disconnect
        if watcher_manager:
            for session_id in registered_sessions:
                await watcher_manager.unwatch_session(session_id)
            for db_path in registered_databases:
                await watcher_manager.unwatch_database(db_path)

    return response


async def handle_stats(request: web.Request) -> web.Response:
    """Return SSE server statistics."""
    sse_manager = get_sse_manager()
    return web.json_response(sse_manager.get_stats())


async def handle_health(request: web.Request) -> web.Response:
    """Health check endpoint."""
    return web.json_response({"status": "ok", "service": "sse"})


async def handle_publish_event(request: web.Request) -> web.Response:
    """Handle HTTP POST event publishing from EventPublisher.

    This endpoint allows the evolution runner (running in a separate process or
    thread) to push events directly to connected SSE clients without going
    through file-based polling.

    POST /api/events/publish
    Body: {"topic": "tree:...", "event_type": "node_created", "data": {...}}

    Returns:
        JSON response with count of clients notified.
    """
    sse_manager = get_sse_manager()

    try:
        payload = await request.json()
        topic = payload.get("topic", "")
        event_type = payload.get("event_type", "")
        data = payload.get("data", {})

        if not topic or not event_type:
            return web.json_response(
                {"error": "Missing topic or event_type"},
                status=400,
            )

        # Validate topic format (security check)
        search_root = request.app.get("search_root")
        if not validate_topic(topic, search_root):
            return web.json_response(
                {"error": "Invalid topic format"},
                status=400,
            )

        # Broadcast to all subscribers
        count = await sse_manager.broadcast(topic, event_type, data)
        logger.debug(
            f"Published event via HTTP: topic={topic}, type={event_type}, "
            f"clients={count}"
        )
        return web.json_response({"clients_notified": count})

    except json.JSONDecodeError:
        return web.json_response(
            {"error": "Invalid JSON body"},
            status=400,
        )
    except Exception as e:
        logger.error(f"Publish event error: {e}")
        return web.json_response(
            {"error": str(e)},
            status=500,
        )


async def heartbeat_loop(sse_manager: SSEManager) -> None:
    """Send periodic heartbeats to all connected clients."""
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            count = await sse_manager.send_heartbeat()
            if count > 0:
                logger.debug(f"Sent heartbeat to {count} clients")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")


async def on_startup(app: web.Application) -> None:
    """Initialize components on server startup."""
    sse_manager = get_sse_manager()

    # Create watcher manager with broadcast callback
    async def broadcast(topic: str, event_type: str, data: dict) -> None:
        await sse_manager.broadcast(topic, event_type, data)

    watcher_manager = WatcherManager(broadcast)
    app["watcher_manager"] = watcher_manager

    # Start watchers
    await watcher_manager.start()

    # Start heartbeat loop
    app["heartbeat_task"] = asyncio.create_task(heartbeat_loop(sse_manager))

    logger.info("SSE server startup complete")


async def on_shutdown(app: web.Application) -> None:
    """Clean up on server shutdown."""
    # Stop heartbeat
    heartbeat_task = app.get("heartbeat_task")
    if heartbeat_task:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

    # Stop watchers
    watcher_manager = app.get("watcher_manager")
    if watcher_manager:
        await watcher_manager.stop()

    logger.info("SSE server shutdown complete")


def create_sse_app(search_root: Optional[str] = None) -> web.Application:
    """Create the aiohttp application for SSE.

    Args:
        search_root: Root directory for database search validation.

    Returns:
        Configured aiohttp Application.
    """
    app = web.Application()

    # Store search root for topic validation
    app["search_root"] = search_root

    # Register routes
    app.router.add_get("/api/events", handle_sse_events)
    app.router.add_get("/api/sse/stats", handle_stats)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/api/events/publish", handle_publish_event)

    # Add CORS middleware - only allow localhost origins (security hardening)
    def _is_allowed_origin(origin: str) -> bool:
        """Check if origin is from localhost."""
        if not origin:
            return True  # Same-origin requests have no Origin header
        return (
            origin.startswith("http://localhost:") or
            origin.startswith("http://127.0.0.1:") or
            origin == "http://localhost" or
            origin == "http://127.0.0.1"
        )

    @web.middleware
    async def cors_middleware(request: web.Request, handler):
        origin = request.headers.get("Origin", "")
        is_allowed = _is_allowed_origin(origin)

        if request.method == "OPTIONS":
            if not is_allowed:
                return web.Response(status=403, text="CORS origin not allowed")
            return web.Response(
                headers={
                    "Access-Control-Allow-Origin": origin or "http://localhost:8000",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, Last-Event-ID",
                }
            )

        response = await handler(request)
        if is_allowed:
            # Use the actual origin for proper CORS (not wildcard)
            response.headers["Access-Control-Allow-Origin"] = origin or "http://localhost:8000"
        return response

    app.middlewares.append(cors_middleware)

    # Register startup/shutdown handlers
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    return app


async def start_sse_server(
    port: int = DEFAULT_SSE_PORT,
    search_root: Optional[str] = None,
    bind_addr: str = "127.0.0.1",
) -> None:
    """Start the SSE server.

    Args:
        port: Port to listen on (default: 8001).
        search_root: Root directory for database validation.
        bind_addr: Address to bind to (default: localhost only).
    """
    app = create_sse_app(search_root)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, bind_addr, port)
    await site.start()

    display_addr = "localhost" if bind_addr == "127.0.0.1" else bind_addr
    logger.info(f"SSE server running at http://{display_addr}:{port}")

    # Keep running until cancelled
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()


def run_sse_server_sync(
    port: int = DEFAULT_SSE_PORT,
    search_root: Optional[str] = None,
    bind_addr: str = "127.0.0.1",
) -> None:
    """Synchronous wrapper to run the SSE server.

    This is useful for running in a thread from the main visualization server.
    """
    asyncio.run(start_sse_server(port, search_root, bind_addr))


def main() -> None:
    """Main entry point for standalone SSE server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Shinka SSE Server")
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=DEFAULT_SSE_PORT,
        help=f"Port to listen on (default: {DEFAULT_SSE_PORT})",
    )
    parser.add_argument(
        "--search-root",
        type=str,
        default=None,
        help="Root directory for database path validation",
    )
    parser.add_argument(
        "--bind",
        type=str,
        default="127.0.0.1",
        help="Address to bind to (default: 127.0.0.1)",
    )

    args = parser.parse_args()

    print(f"\n[*] Starting Shinka SSE Server on port {args.port}")
    print(f"[*] Bind address: {args.bind}")
    if args.search_root:
        print(f"[*] Search root: {args.search_root}")
    print("[*] Press Ctrl+C to stop\n")

    # Handle graceful shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def shutdown_handler():
        print("\n[*] Shutting down...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_handler)

    try:
        loop.run_until_complete(
            start_sse_server(args.port, args.search_root, args.bind)
        )
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()
        print("[*] SSE server stopped")


if __name__ == "__main__":
    main()
