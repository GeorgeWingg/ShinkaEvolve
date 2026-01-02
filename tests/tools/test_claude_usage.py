"""Tests for Claude usage tracking module."""

import json
from pathlib import Path

import pytest

from shinka.tools import claude_usage


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestParseUsageWindows:
    """Tests for parse_usage_windows function."""

    def test_parses_five_hour_window(self):
        payload = {
            "five_hour": {
                "utilization": 45.5,
                "resets_at": "2025-01-15T10:30:00Z",
            }
        }
        windows = claude_usage.parse_usage_windows(payload)
        assert len(windows) == 1
        assert windows[0].percent_used == 45.5
        assert windows[0].label == "5h limit"
        assert windows[0].window_minutes == 300
        assert windows[0].reset_at is not None

    def test_parses_all_windows(self):
        payload = {
            "five_hour": {"utilization": 45.5, "resets_at": "2025-01-15T10:30:00Z"},
            "seven_day": {"utilization": 62.3, "resets_at": "2025-01-20T00:00:00Z"},
            "seven_day_opus": {"utilization": 55.2, "resets_at": "2025-01-20T00:00:00Z"},
            "seven_day_sonnet": {"utilization": 70.0, "resets_at": "2025-01-20T00:00:00Z"},
        }
        windows = claude_usage.parse_usage_windows(payload)
        assert len(windows) == 4
        labels = [w.label for w in windows]
        assert "5h limit" in labels
        assert "7d limit" in labels
        assert "7d Opus limit" in labels
        assert "7d Sonnet limit" in labels

    def test_raises_on_missing_five_hour(self):
        payload = {"seven_day": {"utilization": 50.0}}
        with pytest.raises(claude_usage.ClaudeUsageError, match="Missing required"):
            claude_usage.parse_usage_windows(payload)

    def test_raises_on_missing_utilization_in_five_hour(self):
        payload = {"five_hour": {"resets_at": "2025-01-15T10:30:00Z"}}
        with pytest.raises(claude_usage.ClaudeUsageError, match="Missing utilization"):
            claude_usage.parse_usage_windows(payload)

    def test_skips_optional_windows_without_utilization(self):
        payload = {
            "five_hour": {"utilization": 45.5, "resets_at": "2025-01-15T10:30:00Z"},
            "seven_day": {"resets_at": "2025-01-20T00:00:00Z"},  # No utilization
        }
        windows = claude_usage.parse_usage_windows(payload)
        assert len(windows) == 1
        assert windows[0].label == "5h limit"


class TestParseIso8601Date:
    """Tests for ISO8601 date parsing."""

    def test_parses_z_suffix(self):
        result = claude_usage.parse_iso8601_date("2025-01-15T10:30:00Z")
        assert result is not None
        assert isinstance(result, int)

    def test_parses_timezone_offset(self):
        result = claude_usage.parse_iso8601_date("2025-01-15T10:30:00+00:00")
        assert result is not None

    def test_returns_none_for_none_input(self):
        result = claude_usage.parse_iso8601_date(None)
        assert result is None

    def test_returns_none_for_invalid_format(self):
        result = claude_usage.parse_iso8601_date("invalid-date")
        assert result is None


class TestLoadFromKeychain:
    """Tests for load_from_keychain function."""

    def test_returns_none_on_non_darwin(self, monkeypatch):
        """Test that Keychain loading is skipped on non-macOS."""
        monkeypatch.setattr(claude_usage.platform, "system", lambda: "Linux")
        result = claude_usage.load_from_keychain()
        assert result is None

    def test_returns_none_on_keychain_error(self, monkeypatch):
        """Test graceful handling of Keychain errors."""
        monkeypatch.setattr(claude_usage.platform, "system", lambda: "Darwin")

        def mock_run(*args, **kwargs):
            result = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "error"})()
            return result

        monkeypatch.setattr(claude_usage.subprocess, "run", mock_run)
        result = claude_usage.load_from_keychain()
        assert result is None

    def test_parses_keychain_oauth_data(self, monkeypatch):
        """Test parsing OAuth data from Keychain."""
        monkeypatch.setattr(claude_usage.platform, "system", lambda: "Darwin")

        keychain_data = {
            "claudeAiOauth": {
                "accessToken": "keychain-token-123",
                "expiresAt": 4102444800000,
                "scopes": ["user:profile"],
                "rateLimitTier": "max",
            }
        }

        def mock_run(*args, **kwargs):
            import json
            result = type("Result", (), {
                "returncode": 0,
                "stdout": json.dumps(keychain_data),
                "stderr": ""
            })()
            return result

        monkeypatch.setattr(claude_usage.subprocess, "run", mock_run)
        result = claude_usage.load_from_keychain()

        assert result is not None
        assert result["accessToken"] == "keychain-token-123"
        assert result["rateLimitTier"] == "max"

    def test_windows_credential_manager(self, monkeypatch):
        """Test loading from Windows Credential Manager."""
        monkeypatch.setattr(claude_usage.platform, "system", lambda: "Windows")

        cred_data = {
            "claudeAiOauth": {
                "accessToken": "windows-token-456",
                "expiresAt": 4102444800000,
                "scopes": ["user:profile"],
                "rateLimitTier": "pro",
            }
        }

        def mock_run(*args, **kwargs):
            import json
            result = type("Result", (), {
                "returncode": 0,
                "stdout": json.dumps(cred_data),
                "stderr": ""
            })()
            return result

        monkeypatch.setattr(claude_usage.subprocess, "run", mock_run)
        result = claude_usage.load_from_keychain()

        assert result is not None
        assert result["accessToken"] == "windows-token-456"
        assert result["rateLimitTier"] == "pro"

    def test_linux_returns_none(self, monkeypatch):
        """Test that Linux falls back to file-based credentials."""
        monkeypatch.setattr(claude_usage.platform, "system", lambda: "Linux")
        result = claude_usage.load_from_keychain()
        assert result is None


class TestLoadOAuthCredentials:
    """Tests for load_oauth_credentials function."""

    def test_loads_valid_credentials(self, tmp_path):
        claude_home = tmp_path / ".claude"
        write_file(
            claude_home / ".credentials.json",
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "token-123",
                        "expiresAt": 1737000000000,  # milliseconds
                        "scopes": ["user:profile"],
                        "rateLimitTier": "pro",
                    }
                }
            ),
        )
        creds = claude_usage.load_oauth_credentials(claude_home)
        assert creds["accessToken"] == "token-123"
        assert creds["expiresAt"] == 1737000000000

    def test_raises_on_missing_file(self, tmp_path):
        claude_home = tmp_path / ".claude"
        claude_home.mkdir(parents=True)
        with pytest.raises(claude_usage.ClaudeUsageError, match="not found"):
            claude_usage.load_oauth_credentials(claude_home)

    def test_raises_on_invalid_json(self, tmp_path):
        claude_home = tmp_path / ".claude"
        write_file(claude_home / ".credentials.json", "not valid json")
        with pytest.raises(claude_usage.ClaudeUsageError, match="Failed to parse"):
            claude_usage.load_oauth_credentials(claude_home)

    def test_raises_on_missing_oauth_key(self, tmp_path):
        claude_home = tmp_path / ".claude"
        write_file(claude_home / ".credentials.json", json.dumps({"other": "data"}))
        with pytest.raises(claude_usage.ClaudeUsageError, match="No claudeAiOauth"):
            claude_usage.load_oauth_credentials(claude_home)


class TestLoadAuthInfo:
    """Tests for load_auth_info function."""

    def test_loads_oauth_mode(self, tmp_path, monkeypatch):
        # Disable Keychain to test file-based loading
        monkeypatch.setattr(claude_usage, "load_from_keychain", lambda: None)

        claude_home = tmp_path / ".claude"
        # Use a far future expiration (year 2100)
        expires_ms = 4102444800000  # Jan 1, 2100
        write_file(
            claude_home / ".credentials.json",
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "test-access-token",
                        "expiresAt": expires_ms,
                        "scopes": ["user:profile", "user:inference"],
                        "rateLimitTier": "pro",
                    }
                }
            ),
        )

        auth = claude_usage.load_auth_info(claude_home)

        assert auth.mode == "oauth"
        assert auth.access_token == "test-access-token"
        assert auth.api_key is None
        assert auth.expires_at == expires_ms // 1000  # Converted to seconds
        assert "user:profile" in auth.scopes
        assert auth.rate_limit_tier == "pro"

    def test_api_key_fallback(self, tmp_path, monkeypatch):
        """Test fallback to ANTHROPIC_API_KEY when OAuth not available."""
        # Disable Keychain to test fallback path
        monkeypatch.setattr(claude_usage, "load_from_keychain", lambda: None)

        claude_home = tmp_path / ".claude"
        claude_home.mkdir(parents=True)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

        auth = claude_usage.load_auth_info(claude_home)

        assert auth.mode == "api_key"
        assert auth.access_token is None
        assert auth.api_key == "sk-ant-test-key"

    def test_raises_when_no_auth(self, tmp_path, monkeypatch):
        """Test error when no auth is available."""
        # Disable Keychain to test error path
        monkeypatch.setattr(claude_usage, "load_from_keychain", lambda: None)

        claude_home = tmp_path / ".claude"
        claude_home.mkdir(parents=True)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with pytest.raises(claude_usage.ClaudeUsageError, match="No Claude authentication"):
            claude_usage.load_auth_info(claude_home)


class TestBuildHeaders:
    """Tests for build_headers function."""

    def test_includes_beta_header(self):
        auth = claude_usage.ClaudeAuthInfo(
            mode="oauth",
            access_token="test-token",
            api_key=None,
            expires_at=None,
            scopes=["user:profile"],
            rate_limit_tier=None,
        )
        headers = claude_usage.build_headers(auth)

        assert "anthropic-beta" in headers
        assert headers["anthropic-beta"] == "oauth-2025-04-20"
        assert headers["Authorization"] == "Bearer test-token"

    def test_raises_for_api_key_mode(self):
        auth = claude_usage.ClaudeAuthInfo(
            mode="api_key",
            access_token=None,
            api_key="sk-ant-test",
            expires_at=None,
            scopes=[],
            rate_limit_tier=None,
        )
        with pytest.raises(claude_usage.ClaudeUsageError, match="OAuth authentication"):
            claude_usage.build_headers(auth)


class TestNormalizeExtraAmounts:
    """Tests for extra_usage amount normalization (cents to dollars)."""

    def test_normalizes_cents_to_dollars(self):
        """Test that whole number amounts >= 1000 are divided by 100 (cents to dollars)."""
        used, limit = claude_usage.normalize_extra_amounts(2300, 10000)
        assert used == 23.0
        assert limit == 100.0

    def test_keeps_dollar_amounts(self):
        """Test that amounts < 1000 are not normalized."""
        used, limit = claude_usage.normalize_extra_amounts(23.0, 100.0)
        assert used == 23.0
        assert limit == 100.0

    def test_keeps_decimal_amounts(self):
        """Test that decimal amounts are not normalized."""
        used, limit = claude_usage.normalize_extra_amounts(23.45, 500.0)
        assert used == 23.45
        assert limit == 500.0

    def test_handles_none_values(self):
        used, limit = claude_usage.normalize_extra_amounts(None, None)
        assert used is None
        assert limit is None


class TestParseExtraUsage:
    """Tests for parse_extra_usage function."""

    def test_parses_extra_usage(self):
        payload = {
            "extra_usage": {
                "is_enabled": True,
                "monthly_limit": 100.0,
                "used_credits": 23.0,
                "utilization": 23.0,
                "currency": "USD",
            }
        }
        extra = claude_usage.parse_extra_usage(payload)
        assert extra is not None
        assert extra.is_enabled is True
        assert extra.monthly_limit == 100.0
        assert extra.used_credits == 23.0

    def test_returns_none_when_disabled(self):
        payload = {
            "extra_usage": {
                "is_enabled": False,
                "monthly_limit": 100.0,
            }
        }
        extra = claude_usage.parse_extra_usage(payload)
        assert extra is None

    def test_returns_none_when_missing(self):
        payload = {"five_hour": {"utilization": 10.0}}
        extra = claude_usage.parse_extra_usage(payload)
        assert extra is None


class TestInferPlanName:
    """Tests for infer_plan_name function."""

    def test_infers_pro(self):
        assert claude_usage.infer_plan_name("pro") == "Claude Pro"
        assert claude_usage.infer_plan_name("claude-pro") == "Claude Pro"

    def test_infers_max(self):
        assert claude_usage.infer_plan_name("max") == "Claude Max"
        assert claude_usage.infer_plan_name("claude_max") == "Claude Max"

    def test_infers_team(self):
        assert claude_usage.infer_plan_name("team") == "Claude Team"

    def test_returns_none_for_none(self):
        assert claude_usage.infer_plan_name(None) is None

    def test_titlecases_unknown_tier(self):
        assert claude_usage.infer_plan_name("custom_tier") == "Custom Tier"


class TestCollectUsage:
    """Tests for collect_usage function."""

    def test_collect_usage_success(self, monkeypatch, tmp_path):
        # Disable Keychain to test file-based loading
        monkeypatch.setattr(claude_usage, "load_from_keychain", lambda: None)

        claude_home = tmp_path / ".claude"
        # Far future expiration
        expires_ms = 4102444800000
        write_file(
            claude_home / ".credentials.json",
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "token-123",
                        "expiresAt": expires_ms,
                        "scopes": ["user:profile"],
                        "rateLimitTier": "pro",
                    }
                }
            ),
        )

        captured = {}

        class DummyResponse:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "five_hour": {"utilization": 10.0, "resets_at": "2025-01-15T15:00:00Z"},
                    "seven_day": {"utilization": 25.0, "resets_at": "2025-01-20T00:00:00Z"},
                }

        def fake_get(url, headers, timeout):
            captured["url"] = url
            captured["headers"] = headers
            return DummyResponse()

        monkeypatch.setattr(claude_usage.requests, "get", fake_get)

        snapshot = claude_usage.collect_usage(claude_home=claude_home)

        assert captured["url"] == "https://api.anthropic.com/api/oauth/usage"
        assert captured["headers"]["Authorization"] == "Bearer token-123"
        assert captured["headers"]["anthropic-beta"] == "oauth-2025-04-20"
        assert len(snapshot.windows) == 2
        assert snapshot.windows[0].percent_used == 10.0
        assert snapshot.plan == "Claude Pro"

    def test_collect_usage_requires_beta_header(self, monkeypatch, tmp_path):
        """Verify the required beta header is included."""
        # Disable Keychain to test file-based loading
        monkeypatch.setattr(claude_usage, "load_from_keychain", lambda: None)

        claude_home = tmp_path / ".claude"
        expires_ms = 4102444800000
        write_file(
            claude_home / ".credentials.json",
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "token-123",
                        "expiresAt": expires_ms,
                        "scopes": ["user:profile"],
                    }
                }
            ),
        )

        captured_headers = {}

        class DummyResponse:
            status_code = 200

            def json(self):
                return {"five_hour": {"utilization": 0.0, "resets_at": "2025-01-15T00:00:00Z"}}

        def fake_get(url, headers, timeout):
            captured_headers.update(headers)
            return DummyResponse()

        monkeypatch.setattr(claude_usage.requests, "get", fake_get)
        claude_usage.collect_usage(claude_home=claude_home)

        # CRITICAL: Beta header must be present
        assert "anthropic-beta" in captured_headers
        assert captured_headers["anthropic-beta"] == "oauth-2025-04-20"

    def test_collect_usage_missing_auth(self, tmp_path, monkeypatch):
        # Disable Keychain to test error path
        monkeypatch.setattr(claude_usage, "load_from_keychain", lambda: None)

        claude_home = tmp_path / ".claude"
        claude_home.mkdir(parents=True, exist_ok=True)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with pytest.raises(claude_usage.ClaudeUsageError, match="No Claude authentication"):
            claude_usage.collect_usage(claude_home=claude_home)

    def test_collect_usage_handles_401(self, monkeypatch, tmp_path):
        # Disable Keychain to test file-based loading
        monkeypatch.setattr(claude_usage, "load_from_keychain", lambda: None)

        claude_home = tmp_path / ".claude"
        expires_ms = 4102444800000
        write_file(
            claude_home / ".credentials.json",
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "expired-token",
                        "expiresAt": expires_ms,
                        "scopes": ["user:profile"],
                    }
                }
            ),
        )

        class DummyResponse:
            status_code = 401
            text = "Unauthorized"

        def fake_get(url, headers, timeout):
            return DummyResponse()

        monkeypatch.setattr(claude_usage.requests, "get", fake_get)

        with pytest.raises(claude_usage.ClaudeUsageError, match="Unauthorized"):
            claude_usage.collect_usage(claude_home=claude_home)

    def test_collect_usage_handles_403(self, monkeypatch, tmp_path):
        # Disable Keychain to test file-based loading
        monkeypatch.setattr(claude_usage, "load_from_keychain", lambda: None)

        claude_home = tmp_path / ".claude"
        expires_ms = 4102444800000
        write_file(
            claude_home / ".credentials.json",
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "token-no-scope",
                        "expiresAt": expires_ms,
                        "scopes": [],
                    }
                }
            ),
        )

        class DummyResponse:
            status_code = 403
            text = "Forbidden"

        def fake_get(url, headers, timeout):
            return DummyResponse()

        monkeypatch.setattr(claude_usage.requests, "get", fake_get)

        with pytest.raises(claude_usage.ClaudeUsageError, match="Forbidden"):
            claude_usage.collect_usage(claude_home=claude_home)


class TestFormatSnapshot:
    """Tests for format_snapshot function."""

    def test_formats_with_windows(self):
        from shinka.tools.codex_usage import RateLimitWindowData, UsageSnapshot

        snapshot = UsageSnapshot(
            plan="Claude Pro",
            email="test@example.com",
            windows=[
                RateLimitWindowData(
                    label="5h limit",
                    percent_used=25.0,
                    window_minutes=300,
                    reset_at=None,
                    reset_at_local="15:00",
                )
            ],
            raw={},
        )

        output = claude_usage.format_snapshot(snapshot)
        assert "Claude Pro" in output
        assert "test@example.com" in output
        assert "25.0% used" in output
        assert "75.0% remaining" in output

    def test_formats_without_windows(self):
        from shinka.tools.codex_usage import UsageSnapshot

        snapshot = UsageSnapshot(
            plan="Claude Pro",
            email=None,
            windows=[],
            raw={},
        )

        output = claude_usage.format_snapshot(snapshot)
        assert "not available" in output
