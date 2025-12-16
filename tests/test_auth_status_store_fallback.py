"""Regression tests for auth_status unified-store fallbacks.

These tests mock out CLI binaries and OAuth files so we can validate that
Gemini/Claude fall back to ~/.shinka/credentials.json, while Codex does not.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch


def test_gemini_store_fallback_marks_available(monkeypatch):
    from shinka.tools.auth_status import check_gemini_auth

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with patch(
        "shinka.tools.auth_status.ensure_gemini_available",
        return_value=Path("/tmp/gemini"),
    ), patch(
        "shinka.tools.auth_status.Path.exists",
        return_value=False,
    ), patch(
        "shinka.tools.credentials.get_api_key",
        side_effect=lambda provider: "stored-gemini-key" if provider == "gemini" else None,
    ):
        status = check_gemini_auth()

    assert status.available is True
    assert os.environ.get("GEMINI_API_KEY") == "stored-gemini-key"


def test_claude_store_fallback_marks_available(monkeypatch):
    from shinka.tools.auth_status import check_claude_auth

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch(
        "shinka.tools.auth_status.ensure_claude_available",
        return_value=Path("/tmp/claude"),
    ), patch(
        "shinka.tools.auth_status.Path.exists",
        return_value=False,
    ), patch(
        "shinka.tools.credentials.get_api_key",
        side_effect=lambda provider: "stored-claude-key" if provider == "claude" else None,
    ):
        status = check_claude_auth()

    assert status.available is True
    assert os.environ.get("ANTHROPIC_API_KEY") == "stored-claude-key"


def test_codex_does_not_use_store_fallback(monkeypatch):
    """Codex availability remains OAuth-only per repo policy."""
    from shinka.tools.auth_status import check_codex_auth

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with patch(
        "shinka.tools.auth_status.ensure_codex_available",
        return_value=Path("/tmp/codex"),
    ), patch(
        "shinka.tools.auth_status.Path.exists",
        return_value=False,
    ), patch(
        "shinka.tools.credentials.get_api_key",
        return_value="stored-openai-key",
    ):
        status = check_codex_auth()

    assert status.available is False

