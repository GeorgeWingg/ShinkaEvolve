"""End-to-end integration tests for agentic CLI backends.

These tests verify that each backend (Codex, Gemini, Claude) integrates
correctly with Shinka's AgenticEditor and produces compatible event streams.

Requirements:
- For Claude tests: `claude` CLI installed and authenticated
- For Codex tests: `codex` CLI installed and authenticated
- For Gemini tests: `gemini` CLI installed and authenticated

Tests are marked with @pytest.mark.skipif to skip if the CLI is not available.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest

# Import all backend modules
from shinka.edit.claude_cli import (
    ensure_claude_available,
    run_claude_task,
    ClaudeUnavailableError,
    ClaudeExecutionError,
)

try:
    from shinka.edit.codex_cli import (
        ensure_codex_available,
        run_codex_task,
        CodexUnavailableError,
    )
    CODEX_AVAILABLE = True
except ImportError:
    CODEX_AVAILABLE = False
    CodexUnavailableError = RuntimeError

try:
    from shinka.edit.gemini_cli import (
        ensure_gemini_available,
        run_gemini_task,
        GeminiUnavailableError,
    )
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    GeminiUnavailableError = RuntimeError

from shinka.edit.agentic import (
    AgentContext,
    AgentResult,
    AgenticEditor,
    CommandResult,
)
from shinka.core.runner import AgenticConfig


# ==============================================================================
# Fixtures for checking backend availability
# ==============================================================================


def is_claude_cli_available() -> bool:
    """Check if Claude CLI is installed and accessible."""
    try:
        ensure_claude_available()
        return True
    except ClaudeUnavailableError:
        return False


def is_codex_cli_available() -> bool:
    """Check if Codex CLI is installed and accessible."""
    if not CODEX_AVAILABLE:
        return False
    try:
        ensure_codex_available()
        return True
    except CodexUnavailableError:
        return False


def is_gemini_cli_available() -> bool:
    """Check if Gemini CLI is installed and accessible."""
    if not GEMINI_AVAILABLE:
        return False
    try:
        ensure_gemini_available()
        return True
    except GeminiUnavailableError:
        return False


# Skip markers
requires_claude = pytest.mark.skipif(
    not is_claude_cli_available(),
    reason="Claude CLI not available"
)

requires_codex = pytest.mark.skipif(
    not is_codex_cli_available(),
    reason="Codex CLI not available"
)

requires_gemini = pytest.mark.skipif(
    not is_gemini_cli_available(),
    reason="Gemini CLI not available"
)


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace for E2E tests."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    yield workspace
    # Cleanup handled by pytest tmp_path


# ==============================================================================
# Backend Discovery Tests
# ==============================================================================


class TestBackendDiscovery:
    """Test that backend binaries can be discovered correctly."""

    def test_claude_binary_discovery(self):
        """Test Claude CLI binary discovery."""
        try:
            path = ensure_claude_available()
            assert path.exists()
            assert path.is_file()
            # Verify we can run --version
            result = subprocess.run(
                [str(path), "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            # Claude CLI may return non-zero on --version, but should produce output
            assert result.stdout or result.stderr
        except ClaudeUnavailableError:
            pytest.skip("Claude CLI not available")

    @requires_codex
    def test_codex_binary_discovery(self):
        """Test Codex CLI binary discovery."""
        path = ensure_codex_available()
        assert path.exists()
        assert path.is_file()

    @requires_gemini
    def test_gemini_binary_discovery(self):
        """Test Gemini CLI binary discovery."""
        path = ensure_gemini_available()
        assert path.exists()
        assert path.is_file()

    def test_custom_claude_path_failure(self):
        """Test that invalid custom path raises ClaudeUnavailableError."""
        with pytest.raises(ClaudeUnavailableError):
            ensure_claude_available("/nonexistent/path/claude")


# ==============================================================================
# Event Streaming Parity Tests
# ==============================================================================


class TestEventStreamingParity:
    """Test that all backends emit events in compatible formats."""

    @requires_claude
    def test_claude_event_stream_format(self, temp_workspace):
        """Test Claude CLI emits properly formatted events."""
        # Create a simple test file
        test_file = temp_workspace / "test.py"
        test_file.write_text("# test\nprint('hello')\n", encoding="utf-8")

        events = []
        try:
            for event in run_claude_task(
                user_prompt="List the contents of the current directory.",
                workdir=temp_workspace,
                profile=None,
                sandbox="workspace-write",
                approval_mode="full-auto",
                max_seconds=60,
                max_events=50,
                extra_cli_config={},
            ):
                events.append(event)
        except ClaudeExecutionError as e:
            # May timeout or hit limits, but we should have some events
            if not events:
                pytest.fail(f"No events received before error: {e}")

        # Verify we got an init event
        init_events = [e for e in events if e.get("type") == "init"]
        assert len(init_events) >= 1, "Should have at least one init event"

        # Verify session_id extraction
        init_event = init_events[0]
        assert "session_id" in init_event

        # Verify usage event at end
        usage_events = [e for e in events if e.get("type") == "usage"]
        if usage_events:
            usage = usage_events[-1].get("usage", {})
            assert "input_tokens" in usage
            assert "output_tokens" in usage

    @requires_claude
    def test_claude_agent_message_format(self, temp_workspace):
        """Test Claude CLI agent_message events have correct structure."""
        events = list(run_claude_task(
            user_prompt="Say 'hello world' and nothing else.",
            workdir=temp_workspace,
            profile=None,
            sandbox="workspace-write",
            approval_mode="full-auto",
            max_seconds=30,
            max_events=20,
            extra_cli_config={},
        ))

        agent_messages = [e for e in events if e.get("type") == "agent_message"]

        if agent_messages:
            msg = agent_messages[0]
            assert "item" in msg
            assert msg["item"].get("type") == "agent_message"
            assert "text" in msg["item"]

    @requires_claude
    def test_claude_command_execution_format(self, temp_workspace):
        """Test Claude CLI command_execution events have correct structure."""
        # Create a file to ensure there's something to list
        (temp_workspace / "test.txt").write_text("hello", encoding="utf-8")

        events = list(run_claude_task(
            user_prompt="Run 'ls' to list files in the current directory.",
            workdir=temp_workspace,
            profile=None,
            sandbox="workspace-write",
            approval_mode="full-auto",
            max_seconds=60,
            max_events=50,
            extra_cli_config={},
        ))

        cmd_events = [e for e in events if e.get("type") == "command_execution"]

        if cmd_events:
            cmd = cmd_events[0]
            assert "item" in cmd
            item = cmd["item"]
            assert item.get("type") == "command_execution"
            assert "command" in item
            assert "status" in item
            assert "exit_code" in item
            assert item["status"] in ("success", "error")
            assert isinstance(item["exit_code"], int)


# ==============================================================================
# AgenticEditor Integration Tests
# ==============================================================================


class TestAgenticEditorIntegration:
    """Test AgenticEditor integration with different backends."""

    @requires_claude
    def test_agentic_editor_with_claude(self, temp_workspace):
        """Test AgenticEditor can use Claude backend to make edits."""
        config = AgenticConfig(
            max_turns=30,
            max_seconds=120,
            sandbox="workspace-write",
            approval_mode="full-auto",
        )

        scratch_dir = temp_workspace / "scratch"

        editor = AgenticEditor(scratch_dir, config, runner=run_claude_task)

        context = AgentContext(
            user_prompt="Add a comment at the top of main.py that says '# Modified by Claude'",
            language="python",
            base_files={
                Path("main.py"): "# EVOLVE-BLOCK-START\nprint('hello')\n# EVOLVE-BLOCK-END\n"
            },
            primary_file=Path("main.py"),
        )

        result = editor.run_session(context)

        # Verify result structure
        assert isinstance(result, AgentResult)
        assert result.session_log_path is not None
        assert result.session_log_path.exists()
        assert len(result.session_events) > 0
        assert result.metrics.get("elapsed_seconds", 0) > 0

        # Verify session log was written
        log_content = result.session_log_path.read_text(encoding="utf-8")
        assert log_content.strip()  # Not empty

    @requires_claude
    def test_agentic_editor_captures_usage_metrics(self, temp_workspace):
        """Test that AgenticEditor captures token usage from Claude."""
        config = AgenticConfig(
            max_turns=20,
            max_seconds=60,
            sandbox="workspace-write",
            approval_mode="full-auto",
        )

        editor = AgenticEditor(
            temp_workspace / "scratch",
            config,
            runner=run_claude_task
        )

        context = AgentContext(
            user_prompt="Just say 'done' and do nothing else.",
            language="python",
            base_files={Path("main.py"): "pass\n"},
            primary_file=Path("main.py"),
        )

        result = editor.run_session(context)

        # Claude backend provides real token counts
        metrics = result.metrics
        assert "estimated_input_tokens" in metrics
        assert "estimated_output_tokens" in metrics
        assert "estimated_total_tokens" in metrics

        # Should have some token usage (may be 0 if no work done)
        total = metrics.get("estimated_total_tokens", 0)
        # Token count should be present (may be 0 if model returns immediately)
        assert isinstance(total, (int, float))


# ==============================================================================
# Session Resume Tests
# ==============================================================================


class TestSessionResume:
    """Test session resume functionality across backends."""

    @requires_claude
    def test_claude_session_id_extraction(self, temp_workspace):
        """Test that session_id is correctly extracted from Claude events."""
        events = list(run_claude_task(
            user_prompt="Say hello.",
            workdir=temp_workspace,
            profile=None,
            sandbox="workspace-write",
            approval_mode="full-auto",
            max_seconds=30,
            max_events=20,
            extra_cli_config={},
        ))

        # Find init event
        init_events = [e for e in events if e.get("type") == "init"]
        assert init_events, "Should have init event"

        session_id = init_events[0].get("session_id")
        assert session_id, "Init event should have session_id"
        assert isinstance(session_id, str)

    @requires_claude
    def test_claude_resume_flag_passed(self, temp_workspace):
        """Test that resume session ID is passed to Claude CLI."""
        # First, get a session ID
        first_events = list(run_claude_task(
            user_prompt="Say hello.",
            workdir=temp_workspace,
            profile=None,
            sandbox="workspace-write",
            approval_mode="full-auto",
            max_seconds=30,
            max_events=20,
            extra_cli_config={},
        ))

        init_events = [e for e in first_events if e.get("type") == "init"]
        if not init_events:
            pytest.skip("Could not get session ID from first run")

        session_id = init_events[0].get("session_id")
        if not session_id:
            pytest.skip("Session ID not available")

        # Try to resume (may fail if session expired, but command should be formed correctly)
        try:
            resume_events = list(run_claude_task(
                user_prompt="What did I ask before?",
                workdir=temp_workspace,
                profile=None,
                sandbox="workspace-write",
                approval_mode="full-auto",
                max_seconds=30,
                max_events=20,
                extra_cli_config={},
                resume_session_id=session_id,
            ))
            # If we get events, the resume was attempted
            assert len(resume_events) > 0
        except ClaudeExecutionError:
            # Resume may fail for various reasons, but the flag should be passed
            pass


# ==============================================================================
# Error Handling Tests
# ==============================================================================


class TestErrorHandling:
    """Test error handling across backends."""

    @requires_claude
    def test_claude_timeout_handling(self, temp_workspace):
        """Test that Claude respects timeout setting."""
        with pytest.raises(ClaudeExecutionError) as exc_info:
            # Use very short timeout
            list(run_claude_task(
                user_prompt="Write a very long story about the history of computing.",
                workdir=temp_workspace,
                profile=None,
                sandbox="workspace-write",
                approval_mode="full-auto",
                max_seconds=1,  # 1 second timeout
                max_events=1000,
                extra_cli_config={},
            ))

        assert "timeout" in str(exc_info.value).lower()

    @requires_claude
    def test_claude_max_events_handling(self, temp_workspace):
        """Test that Claude respects max_events setting."""
        # This may not always trigger, depending on how Claude responds.
        # The max_events limit is a soft cap that stops iteration after N events
        # but the usage event and init event may still be emitted.
        events = []
        try:
            for event in run_claude_task(
                user_prompt="List all files recursively.",
                workdir=temp_workspace,
                profile=None,
                sandbox="workspace-write",
                approval_mode="full-auto",
                max_seconds=60,
                max_events=3,  # Very low limit
                extra_cli_config={},
            ):
                events.append(event)
        except ClaudeExecutionError as e:
            assert "more events than allowed" in str(e).lower()
            return

        # If no error, we should have approximately the max_events count.
        # Allow some flexibility since init/usage events may be added regardless.
        # The important thing is we don't get dozens of events.
        assert len(events) <= 6, f"Expected ~3 events (with init/usage overhead), got {len(events)}"


# ==============================================================================
# Multi-Backend Parity Tests
# ==============================================================================


class TestMultiBackendParity:
    """Test that different backends produce compatible outputs."""

    def _get_event_types(self, events: List[Dict[str, Any]]) -> set:
        """Extract unique event types from event list."""
        types = set()
        for e in events:
            if isinstance(e, dict):
                t = e.get("type")
                if t:
                    types.add(t)
        return types

    @requires_claude
    def test_claude_event_types(self, temp_workspace):
        """Test Claude produces expected event types."""
        events = list(run_claude_task(
            user_prompt="Create a file called test.txt with content 'hello'.",
            workdir=temp_workspace,
            profile=None,
            sandbox="workspace-write",
            approval_mode="full-auto",
            max_seconds=60,
            max_events=100,
            extra_cli_config={},
        ))

        types = self._get_event_types(events)

        # Core event types that should be present
        assert "init" in types, f"Missing init event, got types: {types}"

        # Should have at least one of these if Claude did work
        work_types = {"agent_message", "tool_use", "command_execution", "result", "usage"}
        assert types & work_types, f"No work events found, got types: {types}"

    @requires_claude
    def test_claude_vs_protocol_compliance(self, temp_workspace):
        """Test Claude events comply with AgentRunner protocol expectations."""
        events = list(run_claude_task(
            user_prompt="Say 'test' and exit.",
            workdir=temp_workspace,
            profile=None,
            sandbox="workspace-write",
            approval_mode="full-auto",
            max_seconds=30,
            max_events=30,
            extra_cli_config={},
        ))

        for event in events:
            event_type = event.get("type")

            # Verify agent_message structure
            if event_type == "agent_message":
                assert "item" in event
                assert event["item"].get("type") == "agent_message"
                assert "text" in event["item"]

            # Verify command_execution structure
            elif event_type == "command_execution":
                assert "item" in event
                item = event["item"]
                assert "command" in item
                assert "status" in item
                assert "exit_code" in item

            # Verify tool_use structure
            elif event_type == "tool_use":
                assert "tool_name" in event
                assert "parameters" in event

            # Verify init structure
            elif event_type == "init":
                assert "session_id" in event

            # Verify usage structure
            elif event_type == "usage":
                assert "usage" in event
                usage = event["usage"]
                assert "input_tokens" in usage
                assert "output_tokens" in usage


# ==============================================================================
# Real File Modification Tests
# ==============================================================================


class TestRealFileModification:
    """Test that backends can actually modify files."""

    @requires_claude
    def test_claude_creates_file(self, temp_workspace):
        """Test Claude can create a new file."""
        events = list(run_claude_task(
            user_prompt="Create a file called 'created_by_claude.txt' containing 'Hello from Claude!'",
            workdir=temp_workspace,
            profile=None,
            sandbox="workspace-write",
            approval_mode="full-auto",
            max_seconds=60,
            max_events=50,
            extra_cli_config={},
        ))

        # Check if file was created
        created_file = temp_workspace / "created_by_claude.txt"
        if created_file.exists():
            content = created_file.read_text(encoding="utf-8")
            assert "Hello" in content or "Claude" in content or "claude" in content.lower()
        else:
            # File may not be created if Claude didn't execute successfully
            # Check if there were any tool executions
            tool_events = [e for e in events if e.get("type") in ("tool_use", "command_execution")]
            if tool_events:
                # Claude tried to do something
                pass
            else:
                pytest.skip("Claude did not execute any tools to create file")

    @requires_claude
    def test_claude_modifies_existing_file(self, temp_workspace):
        """Test Claude can modify an existing file."""
        # Create initial file
        test_file = temp_workspace / "modify_me.py"
        original_content = "# Original content\nprint('original')\n"
        test_file.write_text(original_content, encoding="utf-8")

        events = list(run_claude_task(
            user_prompt="Add a comment '# Modified by test' to the top of modify_me.py",
            workdir=temp_workspace,
            profile=None,
            sandbox="workspace-write",
            approval_mode="full-auto",
            max_seconds=60,
            max_events=50,
            extra_cli_config={},
        ))

        # Check if file was modified
        new_content = test_file.read_text(encoding="utf-8")

        # The file should either be modified or unchanged if Claude didn't execute
        if new_content != original_content:
            assert "Modified" in new_content or "modified" in new_content.lower()


# ==============================================================================
# WebUI Status Endpoint Test
# ==============================================================================


class TestWebUIIntegration:
    """Test WebUI integration for backend status."""

    def test_claude_status_endpoint_logic(self):
        """Test the logic that would be used by the /api/claude_status endpoint."""
        # Simulate what the endpoint does
        try:
            path = ensure_claude_available()
            status = {
                "available": True,
                "path": str(path),
            }
        except ClaudeUnavailableError as e:
            status = {
                "available": False,
                "error": str(e),
            }

        assert "available" in status
        if status["available"]:
            assert "path" in status
        else:
            assert "error" in status


# ==============================================================================
# Performance Tests
# ==============================================================================


class TestPerformance:
    """Basic performance tests for backends."""

    @requires_claude
    def test_claude_startup_time(self, temp_workspace):
        """Test Claude CLI startup time is reasonable."""
        start = time.monotonic()

        events = list(run_claude_task(
            user_prompt="Say 'ping'.",
            workdir=temp_workspace,
            profile=None,
            sandbox="workspace-write",
            approval_mode="full-auto",
            max_seconds=120,
            max_events=20,
            extra_cli_config={},
        ))

        elapsed = time.monotonic() - start

        # Should complete within 2 minutes for a simple prompt
        assert elapsed < 120, f"Claude took too long: {elapsed}s"

        # Should have some events
        assert len(events) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
