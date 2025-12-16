from __future__ import annotations

import json
from pathlib import Path

import pytest


class _FakeUsage:
    def __init__(self, input_tokens=10, output_tokens=5):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = input_tokens + output_tokens


class _FakeContent:
    def __init__(self, text: str):
        self.text = text


class _FakeOutput:
    def __init__(self, text: str):
        self.content = [_FakeContent(text)]


class _FakeResponse:
    def __init__(self, text: str):
        self.output = [_FakeOutput(text)]
        self.usage = _FakeUsage()


class _FakeOpenAIClient:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)

        class _Responses:
            def __init__(self, outer):
                self._outer = outer

            def create(self, **kwargs):
                text = self._outer._responses.pop(0)
                return _FakeResponse(text)

        self.responses = _Responses(self)


def test_start_plan_session_writes_files(monkeypatch, tmp_path):
    import shinka.webui.plan_sessions as ps

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(ps, "PLAN_SESSIONS_ROOT", tmp_path)
    monkeypatch.setattr(ps, "register_session_process", lambda *a, **k: None)
    monkeypatch.setattr(ps, "update_session_process", lambda *a, **k: None)

    fake_client = _FakeOpenAIClient(
        ["PLAN_STATUS: NEEDS_INFO\n## Goal\nDo a thing\n## Assumptions\nNone"]
    )
    monkeypatch.setattr(ps.openai, "OpenAI", lambda: fake_client)

    session_id, session_dir = ps.start_plan_session(
        kind="edit",
        goal="Do a thing",
        context={},
        model="gpt-5.2",
    )

    assert session_id
    assert session_dir.exists()

    convo = json.loads((session_dir / "conversation.json").read_text())
    assert convo[0]["role"] == "user"
    assert convo[-1]["role"] == "assistant"

    events = (session_dir / "session_log.jsonl").read_text().splitlines()
    assert any('"type": "init"' in e for e in events)
    assert any('"type": "agent_message"' in e for e in events)


def test_append_plan_message_marks_final(monkeypatch, tmp_path):
    import shinka.webui.plan_sessions as ps

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(ps, "PLAN_SESSIONS_ROOT", tmp_path)
    monkeypatch.setattr(ps, "register_session_process", lambda *a, **k: None)
    monkeypatch.setattr(ps, "update_session_process", lambda *a, **k: None)

    fake_client = _FakeOpenAIClient(
        [
            "PLAN_STATUS: DRAFT\n## Goal\nDraft\n## Step Plan\n1. X",
            "PLAN_STATUS: FINAL\n## Goal\nFinal\n## Step Plan\n1. X\n2. Y",
        ]
    )
    monkeypatch.setattr(ps.openai, "OpenAI", lambda: fake_client)

    session_id, session_dir = ps.start_plan_session(
        kind="eval",
        goal="Evaluate it",
        context={"edit_prompt_background": "edit context"},
        model="gpt-5.2",
    )

    ps.append_plan_message(session_id=session_id, user_message="Looks good")

    meta = json.loads((session_dir / "session_meta.json").read_text())
    assert meta["status"] == "completed"

    convo = json.loads((session_dir / "conversation.json").read_text())
    assert any(m["role"] == "assistant" and "PLAN_STATUS: FINAL" in m["content"] for m in convo)

