import json
from pathlib import Path

import pytest

from shinka.tools import codex_usage


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_normalize_base_url_appends_backend_api():
    result = codex_usage.normalize_base_url("https://chatgpt.com")
    assert result == "https://chatgpt.com/backend-api"


def test_build_usage_url_for_chatgpt_base():
    base = "https://chatgpt.com/backend-api"
    assert codex_usage.build_usage_url(base) == "https://chatgpt.com/backend-api/wham/usage"


def test_parse_windows_builds_labels():
    payload = {
        "plan_type": "pro",
        "rate_limit": {
            "primary_window": {
                "used_percent": 25,
                "limit_window_seconds": 60 * 60,
                "reset_at": 1,
            },
            "secondary_window": {
                "used_percent": 50,
                "limit_window_seconds": 60 * 60 * 24 * 7,
                "reset_at": 2,
            },
        },
    }
    windows = codex_usage.parse_windows(payload)
    assert [w.label for w in windows] == ["1h limit", "weekly limit"]
    assert [w.percent_used for w in windows] == [25.0, 50.0]


def test_collect_usage_success(monkeypatch, tmp_path):
    codex_home = tmp_path / ".codex"
    write_file(
        codex_home / "auth.json",
        json.dumps(
            {
                "tokens": {
                    "access_token": "token-123",
                    "account_id": "acct",
                    "id_token": {"email": "user@example.com", "plan_type": "pro"},
                }
            }
        ),
    )
    write_file(codex_home / "config.toml", 'chatgpt_base_url = "https://chatgpt.com"')

    captured = {}

    class DummyResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "plan_type": "pro",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 10,
                        "limit_window_seconds": 60 * 60 * 5,
                        "reset_at": 1700000000,
                    }
                },
            }

    def fake_get(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(codex_usage.requests, "get", fake_get)

    snapshot = codex_usage.collect_usage(codex_home=codex_home)

    assert captured["url"] == "https://chatgpt.com/backend-api/wham/usage"
    assert captured["headers"]["Authorization"] == "Bearer token-123"
    assert snapshot.plan == "Pro"
    assert snapshot.email == "user@example.com"
    assert len(snapshot.windows) == 1
    assert snapshot.windows[0].percent_used == 10.0


def test_collect_usage_missing_auth(tmp_path):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    write_file(codex_home / "config.toml", "")

    with pytest.raises(codex_usage.CodexUsageError):
        codex_usage.collect_usage(codex_home=codex_home)


def test_collect_usage_api_key_mode(monkeypatch, tmp_path):
    codex_home = tmp_path / ".codex"
    write_file(
        codex_home / "auth.json",
        json.dumps({"openai_api_key": "sk-proj-123"}),
    )
    # Default config if missing
    
    captured = {}

    class DummyResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "plan_type": "unknown", # API key mode might not return plan
                "rate_limit": {}
            }

    def fake_get(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        return DummyResponse()

    monkeypatch.setattr(codex_usage.requests, "get", fake_get)

    snapshot = codex_usage.collect_usage(codex_home=codex_home)

    # Check that we fall back to default URL if config is missing
    assert captured["url"] == "https://chatgpt.com/backend-api/wham/usage"
    # Check Authorization header format for API key
    assert captured["headers"]["Authorization"] == "Bearer sk-proj-123"
    assert snapshot.email is None

