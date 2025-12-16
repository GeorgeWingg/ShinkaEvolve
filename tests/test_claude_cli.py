"""Unit tests for the Claude Code CLI wrapper."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shinka.edit.claude_cli import (
    run_claude_task,
    ensure_claude_available,
    ClaudeExecutionError,
    ClaudeUnavailableError,
)


@pytest.fixture
def mock_subprocess():
    with patch("subprocess.Popen") as mock_popen:
        yield mock_popen


@pytest.fixture
def mock_ensure_claude():
    with patch("shinka.edit.claude_cli.ensure_claude_available") as mock_ensure:
        mock_ensure.return_value = Path("claude")
        yield mock_ensure


def test_ensure_claude_available_success():
    """Test that ensure_claude_available returns path when CLI exists."""
    with patch("shutil.which") as mock_which:
        mock_which.return_value = "/usr/local/bin/claude"
        with patch("pathlib.Path.exists") as mock_exists:
            mock_exists.return_value = True
            with patch("pathlib.Path.is_file") as mock_is_file:
                mock_is_file.return_value = True
                result = ensure_claude_available()
                assert result == Path("/usr/local/bin/claude")


def test_ensure_claude_available_not_found():
    """Test that ensure_claude_available raises error when CLI not in PATH."""
    with patch("shutil.which") as mock_which:
        mock_which.return_value = None
        with patch("pathlib.Path.home") as mock_home:
            mock_home.return_value = Path("/nonexistent/home")
            with pytest.raises(ClaudeUnavailableError) as exc_info:
                ensure_claude_available()
            assert "Claude CLI not found" in str(exc_info.value)


def test_ensure_claude_available_custom_path():
    """Test that ensure_claude_available works with custom path."""
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = True
        with patch("pathlib.Path.is_file") as mock_is_file:
            mock_is_file.return_value = True
            result = ensure_claude_available("/custom/path/claude")
            assert result == Path("/custom/path/claude")


def test_run_claude_task_streams_events(mock_subprocess, mock_ensure_claude):
    """Test that run_claude_task streams and normalizes events correctly."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.stdin = MagicMock()
    mock_process.poll.return_value = None

    # Simulate Claude CLI JSON stream
    events = [
        # Init event
        json.dumps({
            "type": "system",
            "subtype": "init",
            "session_id": "sess-abc-123",
            "model": "claude-sonnet-4-5-20250929",
            "tools": ["Bash", "Edit", "Read"]
        }),
        # Assistant text message
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "I'll help you with that."}],
                "usage": {"input_tokens": 10, "output_tokens": 8}
            },
            "session_id": "sess-abc-123"
        }),
        # Assistant tool use
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu_123",
                    "name": "Bash",
                    "input": {"command": "ls -la"}
                }],
                "usage": {"input_tokens": 5, "output_tokens": 12}
            },
            "session_id": "sess-abc-123"
        }),
        # User tool result
        json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_123",
                    "content": "file1.py\nfile2.py",
                    "is_error": False
                }]
            },
            "tool_use_result": {"stdout": "file1.py\nfile2.py", "stderr": ""},
            "session_id": "sess-abc-123"
        }),
        # Final result
        json.dumps({
            "type": "result",
            "subtype": "success",
            "total_cost_usd": 0.025,
            "usage": {"input_tokens": 15, "output_tokens": 20},
            "session_id": "sess-abc-123"
        }),
    ]

    def readline_side_effect():
        if events:
            return events.pop(0) + "\n"
        mock_process.poll.return_value = 0
        return ""

    mock_process.stdout.readline.side_effect = readline_side_effect
    mock_subprocess.return_value = mock_process

    results = list(run_claude_task(
        user_prompt="list files",
        workdir=Path("/tmp"),
        profile=None,
        sandbox="",
        approval_mode="full-auto",
        max_seconds=10,
        max_events=100,
        extra_cli_config={}
    ))

    # Verify init event
    assert results[0]["type"] == "init"
    assert results[0]["session_id"] == "sess-abc-123"
    assert results[0]["model"] == "claude-sonnet-4-5-20250929"

    # Verify agent message
    assert results[1]["type"] == "agent_message"
    assert results[1]["item"]["text"] == "I'll help you with that."

    # Verify tool use
    assert results[2]["type"] == "tool_use"
    assert results[2]["tool_name"] == "Bash"
    assert results[2]["parameters"]["command"] == "ls -la"

    # Verify command execution (adapted from tool_result)
    assert results[3]["type"] == "command_execution"
    assert results[3]["item"]["command"] == "ls -la"
    assert results[3]["item"]["status"] == "success"
    assert results[3]["item"]["exit_code"] == 0
    assert results[3]["item"]["stdout"] == "file1.py\nfile2.py"

    # Verify result passthrough
    assert results[4]["type"] == "result"

    # Verify usage event at end
    assert results[-1]["type"] == "usage"
    assert results[-1]["usage"]["input_tokens"] == 15
    assert results[-1]["usage"]["output_tokens"] == 20


def test_run_claude_task_handles_errors(mock_subprocess, mock_ensure_claude):
    """Test that tool errors are properly converted to command_execution with error status."""
    mock_process = MagicMock()
    mock_process.pid = 67890
    mock_process.stdin = MagicMock()
    mock_process.poll.return_value = None

    events = [
        json.dumps({
            "type": "system",
            "subtype": "init",
            "session_id": "err-sess",
            "model": "claude-sonnet-4-5-20250929"
        }),
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "tool_use",
                    "id": "toolu_err",
                    "name": "Bash",
                    "input": {"command": "invalid_command"}
                }],
                "usage": {"input_tokens": 5, "output_tokens": 8}
            }
        }),
        json.dumps({
            "type": "user",
            "message": {
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_err",
                    "content": "bash: invalid_command: command not found",
                    "is_error": True
                }]
            },
            "tool_use_result": {"stdout": "", "stderr": "bash: invalid_command: command not found"}
        }),
    ]

    def readline_side_effect():
        if events:
            return events.pop(0) + "\n"
        mock_process.poll.return_value = 0
        return ""

    mock_process.stdout.readline.side_effect = readline_side_effect
    mock_subprocess.return_value = mock_process

    results = list(run_claude_task(
        user_prompt="run bad command",
        workdir=Path("/tmp"),
        profile=None,
        sandbox="",
        approval_mode="default",
        max_seconds=0,
        max_events=100,
        extra_cli_config={}
    ))

    # Find the command_execution event
    cmd_events = [r for r in results if r.get("type") == "command_execution"]
    assert len(cmd_events) == 1
    cmd_event = cmd_events[0]

    assert cmd_event["item"]["status"] == "error"
    assert cmd_event["item"]["exit_code"] == 1
    assert "invalid_command" in cmd_event["item"]["stderr"]


def test_claude_parity_args(mock_subprocess, mock_ensure_claude):
    """Verify correct CLI flags are constructed for parity with other backends."""
    mock_process = MagicMock()
    mock_process.pid = 111
    mock_process.stdout.readline.return_value = ""
    mock_process.poll.return_value = 0
    mock_subprocess.return_value = mock_process

    # Test 1: Model selection
    list(run_claude_task(
        user_prompt="test",
        workdir=Path("/tmp"),
        profile="opus",
        sandbox="",
        approval_mode="default",
        max_seconds=0,
        max_events=10,
        extra_cli_config={}
    ))

    call_args = mock_subprocess.call_args[0][0]
    assert "--model" in call_args
    assert "opus" in call_args

    # Test 2: Full-auto permission bypass
    list(run_claude_task(
        user_prompt="test",
        workdir=Path("/tmp"),
        profile=None,
        sandbox="workspace-write",
        approval_mode="full-auto",
        max_seconds=0,
        max_events=10,
        extra_cli_config={}
    ))

    call_args = mock_subprocess.call_args[0][0]
    assert "--dangerously-skip-permissions" in call_args

    # Test 3: Resume session
    list(run_claude_task(
        user_prompt="test",
        workdir=Path("/tmp"),
        profile=None,
        sandbox="",
        approval_mode="default",
        max_seconds=0,
        max_events=10,
        extra_cli_config={},
        resume_session_id="sess-uuid-123"
    ))

    call_args = mock_subprocess.call_args[0][0]
    assert "--resume" in call_args
    assert "sess-uuid-123" in call_args


def test_claude_prompt_handling(mock_subprocess, mock_ensure_claude):
    """Verify prompt is piped via stdin (not positional)."""
    mock_process = MagicMock()
    mock_process.pid = 222
    mock_process.stdin = MagicMock()
    mock_process.stdout.readline.return_value = ""
    mock_process.poll.return_value = 0
    mock_subprocess.return_value = mock_process

    user_prompt = "Write some code"

    list(run_claude_task(
        user_prompt=user_prompt,
        workdir=Path("/tmp"),
        profile=None,
        sandbox="",
        approval_mode="default",
        max_seconds=0,
        max_events=10,
        extra_cli_config={}
    ))

    call_args = mock_subprocess.call_args[0][0]
    call_kwargs = mock_subprocess.call_args[1]
    # Prompt should not be appended as a CLI argument.
    assert user_prompt not in call_args
    # Prompt should be provided via stdin file handle.
    assert call_kwargs.get("stdin") is not subprocess.DEVNULL


def test_claude_system_prompt(mock_subprocess, mock_ensure_claude):
    """Verify system prompt is passed via --system-prompt-file flag."""
    mock_process = MagicMock()
    mock_process.pid = 333
    mock_process.stdin = MagicMock()
    mock_process.stdout.readline.return_value = ""
    mock_process.poll.return_value = 0
    mock_subprocess.return_value = mock_process

    system_prompt = "You are a helpful assistant."
    user_prompt = "Help me"

    # Prevent temp prompt cleanup so we can inspect contents.
    with patch("shinka.edit.claude_cli.os.remove"):
        list(run_claude_task(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            workdir=Path("/tmp"),
            profile=None,
            sandbox="",
            approval_mode="default",
            max_seconds=0,
            max_events=10,
            extra_cli_config={}
        ))

    call_args = mock_subprocess.call_args[0][0]
    assert "--system-prompt-file" in call_args
    idx = call_args.index("--system-prompt-file")
    sys_path = Path(call_args[idx + 1])
    assert sys_path.exists()
    assert system_prompt in sys_path.read_text(encoding="utf-8")


def test_claude_usage_event_emitted(mock_subprocess, mock_ensure_claude):
    """Verify synthetic usage event is yielded when process exits."""
    mock_process = MagicMock()
    mock_process.pid = 444
    mock_process.stdin = MagicMock()
    mock_process.poll.return_value = None

    events = [
        json.dumps({
            "type": "system",
            "subtype": "init",
            "session_id": "usage-sess"
        }),
        json.dumps({
            "type": "result",
            "total_cost_usd": 0.05,
            "usage": {"input_tokens": 100, "output_tokens": 50}
        }),
    ]

    def readline_side_effect():
        if events:
            return events.pop(0) + "\n"
        mock_process.poll.return_value = 0
        return ""

    mock_process.stdout.readline.side_effect = readline_side_effect
    mock_subprocess.return_value = mock_process

    results = list(run_claude_task(
        user_prompt="test",
        workdir=Path("/tmp"),
        profile=None,
        sandbox="",
        approval_mode="default",
        max_seconds=0,
        max_events=100,
        extra_cli_config={}
    ))

    # Last event should be usage
    usage_event = results[-1]
    assert usage_event["type"] == "usage"
    assert usage_event["usage"]["input_tokens"] == 100
    assert usage_event["usage"]["output_tokens"] == 50
    assert usage_event["usage"]["total_tokens"] == 150


def test_claude_timeout_enforcement(mock_subprocess, mock_ensure_claude):
    """Verify max_seconds enforcement raises ClaudeExecutionError."""
    mock_process = MagicMock()
    mock_process.pid = 555
    mock_process.stdin = MagicMock()
    mock_process.poll.return_value = None  # Never exits naturally

    # readline blocks forever
    def readline_side_effect():
        import time
        time.sleep(0.1)
        return ""

    mock_process.stdout.readline.side_effect = readline_side_effect
    mock_subprocess.return_value = mock_process

    with pytest.raises(ClaudeExecutionError) as exc_info:
        # Use a very short timeout
        list(run_claude_task(
            user_prompt="test",
            workdir=Path("/tmp"),
            profile=None,
            sandbox="",
            approval_mode="default",
            max_seconds=0.05,  # 50ms timeout
            max_events=100,
            extra_cli_config={}
        ))

    assert "timeout" in str(exc_info.value).lower()
    mock_process.kill.assert_called()


def test_claude_max_events_enforcement(mock_subprocess, mock_ensure_claude):
    """Verify max_events enforcement raises ClaudeExecutionError."""
    mock_process = MagicMock()
    mock_process.pid = 666
    mock_process.stdin = MagicMock()
    mock_process.poll.return_value = None

    # Generate many events
    event_count = 0

    def readline_side_effect():
        nonlocal event_count
        event_count += 1
        return json.dumps({"type": "system", "subtype": "init", "session_id": f"sess-{event_count}"}) + "\n"

    mock_process.stdout.readline.side_effect = readline_side_effect
    mock_subprocess.return_value = mock_process

    with pytest.raises(ClaudeExecutionError) as exc_info:
        list(run_claude_task(
            user_prompt="test",
            workdir=Path("/tmp"),
            profile=None,
            sandbox="",
            approval_mode="default",
            max_seconds=0,
            max_events=5,  # Only allow 5 events
            extra_cli_config={}
        ))

    assert "more events than allowed" in str(exc_info.value).lower()
    mock_process.kill.assert_called()
