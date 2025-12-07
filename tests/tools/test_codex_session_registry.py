import os
import signal
from pathlib import Path

import pytest

from shinka.tools import codex_session_registry as registry


@pytest.fixture(autouse=True)
def registry_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "sessions")
    return registry.REGISTRY_DIR


def test_register_and_list(monkeypatch, registry_dir):
    registry.register_session_process(
        pid=os.getpid(),
        prompt_preview="demo prompt",
        workdir=Path("/tmp"),
        session_kind="edit",
    )
    sessions = registry.list_session_processes()
    assert sessions
    assert sessions[0]["prompt_preview"] == "demo prompt"
    assert sessions[0]["session_kind"] == "edit"


def test_update_and_remove(monkeypatch, registry_dir):
    pid = os.getpid()
    registry.register_session_process(
        pid=pid,
        prompt_preview="demo",
        workdir=Path("/tmp"),
    )
    registry.update_session_process(pid, session_id="abc123")
    entry_path = registry_dir / f"{pid}.json"
    assert "abc123" in entry_path.read_text()
    registry.remove_session_process(pid)
    assert not entry_path.exists()


def test_list_ignores_dead_process(monkeypatch, registry_dir):
    # use fake PID unlikely to exist
    pid = 999999
    registry.register_session_process(
        pid=pid,
        prompt_preview="ghost",
        workdir=Path("/tmp"),
    )
    sessions = registry.list_session_processes()
    assert sessions == []
