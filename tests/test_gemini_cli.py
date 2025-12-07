import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from shinka.edit.gemini_cli import run_gemini_task, GeminiExecutionError

@pytest.fixture
def mock_subprocess():
    with patch("subprocess.Popen") as mock_popen:
        yield mock_popen

@pytest.fixture
def mock_ensure_gemini():
    with patch("shinka.edit.gemini_cli.ensure_gemini_available") as mock_ensure:
        mock_ensure.return_value = Path("gemini")
        yield mock_ensure

def test_run_gemini_task_streams_events(mock_subprocess, mock_ensure_gemini):
    # Mock stdout stream
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.stdin = MagicMock()
    mock_process.poll.return_value = None
    
    # Simulate Gemini JSON stream
    events = [
        json.dumps({"type": "init", "session_id": "sess-123"}),
        json.dumps({"type": "message", "role": "user", "content": "fix bug"}), # Ignored
        json.dumps({"type": "message", "role": "assistant", "content": "I will fix it."}),
        json.dumps({"type": "tool_use", "tool_id": "call-1", "tool_name": "run_shell", "parameters": {"command": "ls"}}),
        json.dumps({"type": "tool_result", "tool_id": "call-1", "status": "success", "output": "file.txt"}),
    ]
    
    # readline side effect
    def readline_side_effect():
        if events:
            return events.pop(0) + "\n"
        mock_process.poll.return_value = 0 # Signal exit
        return ""
        
    mock_process.stdout.readline.side_effect = readline_side_effect
    mock_subprocess.return_value = mock_process

    iterator = run_gemini_task(
        user_prompt="fix it",
        workdir=Path("/tmp"),
        profile=None,
        sandbox="default",
        approval_mode="full-auto",
        max_seconds=10,
        max_events=100,
        extra_cli_config={}
    )

    results = list(iterator)
    
    # INIT passed through
    assert results[0]["type"] == "init"
    assert results[0]["session_id"] == "sess-123"
    
    # Assistant message adapted
    assert results[1]["type"] == "agent_message"
    assert results[1]["item"]["text"] == "I will fix it."
    
    # Tool use passed through
    assert results[2]["type"] == "tool_use"
    
    # Tool result adapted to command_execution
    assert results[3]["type"] == "command_execution"
    assert results[3]["item"]["command"] == "ls"
    assert results[3]["item"]["status"] == "success"
    assert results[3]["item"]["stdout"] == "file.txt"
    assert results[3]["item"]["exit_code"] == 0

def test_run_gemini_task_handles_errors(mock_subprocess, mock_ensure_gemini):
    mock_process = MagicMock()
    mock_process.pid = 67890
    mock_process.poll.return_value = None
    
    # Events for second test
    events = [
        json.dumps({
            "type": "error", 
            "message": "Something exploded"
        })
    ]
    
    def readline_side_effect():
        if events:
            return events.pop(0) + "\n"
        mock_process.poll.return_value = 1
        return ""

    mock_process.stdout.readline.side_effect = readline_side_effect
    mock_subprocess.return_value = mock_process
    
    iterator = run_gemini_task(
        user_prompt="hi", 
        workdir=Path("."), 
        profile=None, 
        sandbox="d", 
        approval_mode="y", 
        max_seconds=0, 
        max_events=10, 
        extra_cli_config={}
    )
    
    res = next(iterator)
    assert res["type"] == "agent_message"
    assert "SYSTEM ERROR" in res["item"]["text"]

def test_gemini_parity_args(mock_subprocess, mock_ensure_gemini):
    """Verify correct CLI flags are constructed for parity."""
    mock_process = MagicMock()
    mock_process.pid = 111
    mock_process.stdout.readline.return_value = ""
    mock_process.poll.return_value = 0
    mock_subprocess.return_value = mock_process

    # 1. Test Sandbox flag injection
    list(run_gemini_task(
        user_prompt="test",
        workdir=Path("."),
        profile=None,
        sandbox="workspace-write",  # Should trigger --sandbox
        approval_mode="full-auto",
        max_seconds=0,
        max_events=10,
        extra_cli_config={}
    ))
    
    call_args = mock_subprocess.call_args[0][0]
    assert "--sandbox" in call_args

    # 2. Test Resume flag injection
    list(run_gemini_task(
        user_prompt="test",
        workdir=Path("."),
        profile=None,
        sandbox="",
        approval_mode="full-auto",
        max_seconds=0,
        max_events=10,
        extra_cli_config={},
        resume_session_id="uuid-123"
    ))
    call_args_resume = mock_subprocess.call_args[0][0]
    assert "--resume" in call_args_resume
    assert "uuid-123" in call_args_resume

    # 3. Test Model Profile
    list(run_gemini_task(
        user_prompt="test",
        workdir=Path("."),
        profile="gemini-pro",
        sandbox="",
        approval_mode="full-auto",
        max_seconds=0,
        max_events=10,
        extra_cli_config={}
    ))
    call_args_model = mock_subprocess.call_args[0][0]
    assert "--model" in call_args_model
    assert "gemini-pro" in call_args_model

def test_gemini_prompt_combination(mock_subprocess, mock_ensure_gemini):
    """Verify prompts are concatenated and sent to stdin."""
    mock_process = MagicMock()
    mock_process.pid = 222
    mock_process.stdin = MagicMock()
    mock_process.stdout.readline.return_value = ""
    mock_process.poll.return_value = 0
    mock_subprocess.return_value = mock_process

    system_prompt = "Be helpful."
    user_prompt = "Write code."

    list(run_gemini_task(
        user_prompt=user_prompt,
        workdir=Path("."),
        system_prompt=system_prompt,
        profile=None,
        sandbox="",
        approval_mode="full-auto",
        max_seconds=0,
        max_events=10,
        extra_cli_config={}
    ))

    # Verify prompt is appended as positional arg
    call_args = mock_subprocess.call_args[0][0]
    assert call_args[-1].startswith(system_prompt)
    assert user_prompt in call_args[-1]
