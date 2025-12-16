"""Tests for Codex device authentication module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shinka.tools.codex_device_auth import (
    CodexAuthError,
    _is_interactive,
    _login_device_auth,
    _login_with_api_key,
    _parse_device_auth_output,
    _status_looks_authenticated,
    ensure_codex_authenticated,
    is_codex_authenticated,
)


class TestIsInteractive:
    """Tests for _is_interactive() TTY detection."""

    def test_returns_true_when_both_stdin_stdout_are_tty(self, monkeypatch):
        """Should return True when both stdin and stdout are TTYs."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        assert _is_interactive() is True

    def test_returns_false_when_stdin_not_tty(self, monkeypatch):
        """Should return False when stdin is not a TTY (e.g., CI)."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        assert _is_interactive() is False

    def test_returns_false_when_stdout_not_tty(self, monkeypatch):
        """Should return False when stdout is not a TTY (e.g., piped)."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)
        assert _is_interactive() is False

    def test_returns_false_when_neither_are_tty(self, monkeypatch):
        """Should return False when neither stdin nor stdout are TTYs."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)
        assert _is_interactive() is False


class TestStatusLooksAuthenticated:
    """Tests for _status_looks_authenticated() output parsing."""

    # Positive cases (authenticated)
    @pytest.mark.parametrize(
        "stdout,stderr",
        [
            ("Logged in as user@example.com", ""),
            ("Authenticated\nPlan: Pro", ""),
            ("", "Logged in"),
            ("User: test\nOrg: default", ""),
            ("Login successful", ""),
            ("", ""),  # Empty output = assume authenticated
        ],
    )
    def test_authenticated_outputs(self, stdout, stderr):
        """Should recognize authenticated status strings."""
        assert _status_looks_authenticated(stdout, stderr) is True

    # Negative cases (not authenticated)
    @pytest.mark.parametrize(
        "stdout,stderr",
        [
            ("Not logged in", ""),
            ("not logged in", ""),  # Case insensitive
            ("", "Not logged in. Run `codex login`"),
            ("Unauthorized access", ""),
            ("unauthorized", ""),
            ("Please login first", ""),
            ("Please log in to continue", ""),
            ("You are not logged in", ""),
        ],
    )
    def test_unauthenticated_outputs(self, stdout, stderr):
        """Should recognize unauthenticated status strings."""
        assert _status_looks_authenticated(stdout, stderr) is False

    def test_case_insensitive_matching(self):
        """Should match regardless of case."""
        assert _status_looks_authenticated("NOT LOGGED IN", "") is False
        assert _status_looks_authenticated("Please Login", "") is False

    def test_substring_in_longer_message(self):
        """Should detect keywords within longer messages."""
        assert (
            _status_looks_authenticated(
                "Error: You are not logged in. Please run codex login --device-auth",
                "",
            )
            is False
        )


class TestParseDeviceAuthOutput:
    """Tests for _parse_device_auth_output() URL and code extraction."""

    def test_parses_standard_output(self):
        """Should parse the standard Codex CLI device auth output."""
        output = """Welcome to Codex [v0.72.0]
OpenAI's command-line coding agent

Follow these steps to sign in with ChatGPT using device code authorization:

1. Open this link in your browser and sign in to your account
   https://auth.openai.com/codex/device

2. Enter this one-time code (expires in 15 minutes)
   D2CR-A7XYL
"""
        url, code = _parse_device_auth_output(output)
        assert url == "https://auth.openai.com/codex/device"
        assert code == "D2CR-A7XYL"

    def test_parses_url_only(self):
        """Should parse URL even without code."""
        output = "Visit https://auth.openai.com/codex/device to authenticate"
        url, code = _parse_device_auth_output(output)
        assert url == "https://auth.openai.com/codex/device"
        assert code is None

    def test_parses_code_only(self):
        """Should parse code even without URL."""
        output = "Enter this code: ABCD-12345"
        url, code = _parse_device_auth_output(output)
        assert url is None
        assert code == "ABCD-12345"

    def test_returns_none_when_no_match(self):
        """Should return None for both when nothing matches."""
        output = "Some random output with no auth info"
        url, code = _parse_device_auth_output(output)
        assert url is None
        assert code is None

    def test_handles_platform_openai_url(self):
        """Should also match platform.openai.com URLs."""
        output = "Go to https://platform.openai.com/device"
        url, code = _parse_device_auth_output(output)
        assert url == "https://platform.openai.com/device"

    @pytest.mark.parametrize(
        "code_str,expected",
        [
            ("ABCD-12345", "ABCD-12345"),
            ("1234-ABCDE", "1234-ABCDE"),
            ("A1B2-C3D4E", "A1B2-C3D4E"),
        ],
    )
    def test_various_code_formats(self, code_str, expected):
        """Should match various valid device code formats."""
        output = f"Code: {code_str}"
        _, code = _parse_device_auth_output(output)
        assert code == expected


class TestIsCodexAuthenticated:
    """Tests for is_codex_authenticated() CLI invocation."""

    def test_returns_true_on_successful_status(self, monkeypatch):
        """Should return True when CLI returns success."""

        def fake_run(args, **kwargs):
            assert args[1:] == ["login", "status"]
            return subprocess.CompletedProcess(args, 0, stdout="Logged in", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert is_codex_authenticated(Path("/bin/codex")) is True

    def test_returns_false_on_nonzero_exit_code(self, monkeypatch):
        """Should return False when CLI returns non-zero."""

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="Not logged in")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert is_codex_authenticated(Path("/bin/codex")) is False

    def test_returns_false_on_oserror(self, monkeypatch):
        """Should return False when CLI binary is not executable."""

        def fake_run(args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert is_codex_authenticated(Path("/bin/codex")) is False

    def test_returns_false_when_status_output_indicates_not_logged_in(self, monkeypatch):
        """Should return False when exit code is 0 but output says not logged in."""

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout="Not logged in", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert is_codex_authenticated(Path("/bin/codex")) is False

    def test_returns_false_on_timeout(self, monkeypatch):
        """Should return False when status check times out."""

        def fake_run(args, **kwargs):
            raise subprocess.TimeoutExpired(args, 30)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert is_codex_authenticated(Path("/bin/codex")) is False


class TestLoginWithApiKey:
    """Tests for _login_with_api_key() flow."""

    def test_success_returns_true(self, monkeypatch):
        """Should return True on successful API key login."""
        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _login_with_api_key(Path("/bin/codex"), "sk-test", timeout_seconds=30)

        assert result is True
        assert calls[0][1]["input"] == "sk-test\n"

    def test_nonzero_exit_returns_false(self, monkeypatch):
        """Should return False when login fails."""

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="Invalid key")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _login_with_api_key(Path("/bin/codex"), "bad-key", timeout_seconds=30) is False

    def test_timeout_returns_false(self, monkeypatch):
        """Should return False on timeout."""

        def fake_run(args, **kwargs):
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 30))

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _login_with_api_key(Path("/bin/codex"), "sk-test", timeout_seconds=5) is False

    def test_oserror_returns_false(self, monkeypatch):
        """Should return False on OSError."""

        def fake_run(args, **kwargs):
            raise OSError("Binary not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _login_with_api_key(Path("/bin/codex"), "sk-test", timeout_seconds=30) is False


class TestLoginDeviceAuth:
    """Tests for _login_device_auth() interactive flow."""

    def test_success_returns_true(self, monkeypatch):
        """Should return True on successful device auth."""
        mock_proc = MagicMock()
        mock_proc.stdout = iter(["Line 1\n", "https://auth.openai.com/device\n", "Code: ABCD-12345\n"])
        mock_proc.returncode = 0
        mock_proc.wait = MagicMock()

        def fake_popen(args, **kwargs):
            return mock_proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr("webbrowser.open", lambda url: True)

        assert _login_device_auth(Path("/bin/codex"), timeout_seconds=900) is True

    def test_nonzero_exit_returns_false(self, monkeypatch):
        """Should return False when device auth fails."""
        mock_proc = MagicMock()
        mock_proc.stdout = iter(["Error\n"])
        mock_proc.returncode = 1
        mock_proc.wait = MagicMock()

        def fake_popen(args, **kwargs):
            return mock_proc

        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        assert _login_device_auth(Path("/bin/codex"), timeout_seconds=900) is False

    def test_oserror_returns_false(self, monkeypatch):
        """Should return False when Popen fails."""

        def fake_popen(args, **kwargs):
            raise OSError("Binary not found")

        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        assert _login_device_auth(Path("/bin/codex"), timeout_seconds=900) is False

    def test_browser_fallback_on_error(self, monkeypatch, capsys):
        """Should show URL when browser fails to open."""
        mock_proc = MagicMock()
        mock_proc.stdout = iter(
            ["https://auth.openai.com/codex/device\n", "Code: ABCD-12345\n"]
        )
        mock_proc.returncode = 0
        mock_proc.wait = MagicMock()

        def fake_popen(args, **kwargs):
            return mock_proc

        def fake_browser_open(url):
            raise Exception("No browser available")

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr("webbrowser.open", fake_browser_open)

        result = _login_device_auth(Path("/bin/codex"), timeout_seconds=900)
        assert result is True

        captured = capsys.readouterr()
        assert "Open this URL" in captured.out
        assert "ABCD-12345" in captured.out


class TestEnsureCodexAuthenticated:
    """Tests for the main ensure_codex_authenticated() function."""

    def test_returns_status_when_already_authenticated(self, monkeypatch):
        """Fast path: returns 'status' when already logged in."""

        def fake_run(args, **kwargs):
            if args[1:] == ["login", "status"]:
                return subprocess.CompletedProcess(args, 0, stdout="Logged in", stderr="")
            raise AssertionError(f"Unexpected call: {args}")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ensure_codex_authenticated(Path("/bin/codex"), allow_interactive=False)
        assert result == "status"

    def test_api_key_fallback_when_not_interactive(self, monkeypatch):
        """Should use API key when not interactive and not logged in."""
        status_count = {"n": 0}

        def fake_run(args, **kwargs):
            if args[1:] == ["login", "status"]:
                status_count["n"] += 1
                if status_count["n"] <= 1:
                    return subprocess.CompletedProcess(args, 1, stdout="", stderr="Not logged")
                return subprocess.CompletedProcess(args, 0, stdout="Logged in", stderr="")
            if args[1:] == ["login", "--with-api-key"]:
                return subprocess.CompletedProcess(args, 0)
            raise AssertionError(f"Unexpected: {args}")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ensure_codex_authenticated(
            Path("/bin/codex"),
            api_key="sk-test",
            allow_interactive=False,
        )
        assert result == "api_key"

    def test_raises_codex_auth_error_when_all_methods_fail(self, monkeypatch):
        """Should raise CodexAuthError when no auth method succeeds."""

        def fake_run(args, **kwargs):
            if args[1:] == ["login", "status"]:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="Not logged")
            if args[1:] == ["login", "--with-api-key"]:
                return subprocess.CompletedProcess(args, 1)
            raise AssertionError(f"Unexpected: {args}")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(CodexAuthError) as exc_info:
            ensure_codex_authenticated(
                Path("/bin/codex"),
                api_key="bad-key",
                allow_interactive=False,
            )

        error_msg = str(exc_info.value)
        assert "codex login --device-auth" in error_msg
        assert "ChatGPT Security Settings" in error_msg

    def test_skips_device_auth_when_allow_interactive_false(self, monkeypatch):
        """Should not attempt device auth when non-interactive."""
        calls = []
        status_count = {"n": 0}

        def fake_run(args, **kwargs):
            calls.append(args[1:])
            if args[1:] == ["login", "status"]:
                status_count["n"] += 1
                if status_count["n"] <= 1:
                    return subprocess.CompletedProcess(args, 1, stdout="", stderr="Not logged")
                return subprocess.CompletedProcess(args, 0, stdout="Logged in", stderr="")
            if args[1:] == ["login", "--with-api-key"]:
                return subprocess.CompletedProcess(args, 0)
            raise AssertionError(f"Unexpected: {args}")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ensure_codex_authenticated(
            Path("/bin/codex"),
            api_key="sk-test",
            allow_interactive=False,
        )

        assert result == "api_key"
        assert ["login", "--device-auth"] not in calls

    def test_raises_when_no_api_key_and_not_interactive(self, monkeypatch):
        """Should raise when non-interactive and no API key provided."""

        def fake_run(args, **kwargs):
            if args[1:] == ["login", "status"]:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="Not logged")
            raise AssertionError(f"Unexpected: {args}")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(CodexAuthError):
            ensure_codex_authenticated(
                Path("/bin/codex"),
                api_key=None,
                allow_interactive=False,
            )


class TestErrorScenarios:
    """Tests for various error conditions."""

    def test_error_message_includes_security_settings(self, monkeypatch):
        """Error message should include ChatGPT Security Settings URL."""

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="Not logged")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(CodexAuthError) as exc_info:
            ensure_codex_authenticated(
                Path("/bin/codex"),
                api_key=None,
                allow_interactive=False,
            )

        error_msg = str(exc_info.value)
        assert "chatgpt.com/settings/security" in error_msg

    def test_error_message_includes_api_key_option(self, monkeypatch):
        """Error message should include API key authentication option."""

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="Not logged")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(CodexAuthError) as exc_info:
            ensure_codex_authenticated(
                Path("/bin/codex"),
                api_key=None,
                allow_interactive=False,
            )

        error_msg = str(exc_info.value)
        assert "OPENAI_API_KEY" in error_msg
        assert "codex login --with-api-key" in error_msg
