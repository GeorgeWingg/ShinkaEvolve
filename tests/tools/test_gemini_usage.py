"""Tests for Gemini usage tracking module."""

import json
from pathlib import Path

import pytest

from shinka.tools import gemini_usage


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestParseQuotaBuckets:
    """Tests for parse_quota_buckets function."""

    def test_parses_remaining_fraction(self):
        buckets = [
            {
                "remainingFraction": 0.75,
                "tokenType": "input",
                "modelId": "gemini-2.5-flash",
            }
        ]
        windows = gemini_usage.parse_quota_buckets(buckets)
        assert len(windows) == 1
        assert windows[0].percent_used == 25.0
        assert windows[0].label == "input (gemini-2.5-flash)"

    def test_parses_reset_time(self):
        buckets = [
            {
                "remainingFraction": 0.5,
                "tokenType": "output",
                "resetTime": "2025-01-15T10:30:00Z",
            }
        ]
        windows = gemini_usage.parse_quota_buckets(buckets)
        assert len(windows) == 1
        assert windows[0].percent_used == 50.0
        assert windows[0].reset_at is not None

    def test_handles_missing_fields(self):
        buckets = [{"tokenType": "unknown"}]
        windows = gemini_usage.parse_quota_buckets(buckets)
        assert len(windows) == 1
        assert windows[0].percent_used == 0.0
        assert windows[0].label == "unknown tokens"

    def test_handles_empty_buckets(self):
        windows = gemini_usage.parse_quota_buckets([])
        assert windows == []


class TestTierDisplayName:
    """Tests for tier_display_name function."""

    def test_free_tier(self):
        assert gemini_usage.tier_display_name("free-tier") == "Free"

    def test_standard_tier(self):
        assert gemini_usage.tier_display_name("standard-tier") == "Standard"

    def test_legacy_tier(self):
        assert gemini_usage.tier_display_name("legacy-tier") == "Legacy"

    def test_unknown_tier(self):
        assert gemini_usage.tier_display_name("some-other-tier") == "Some Other Tier"

    def test_none_tier(self):
        assert gemini_usage.tier_display_name(None) is None


class TestLoadOAuthCredentials:
    """Tests for load_oauth_credentials function."""

    def test_loads_valid_credentials(self, tmp_path):
        gemini_home = tmp_path / ".gemini"
        write_file(
            gemini_home / "oauth_creds.json",
            json.dumps({
                "access_token": "token-123",
                "refresh_token": "refresh-456",
                "client_id": "client.apps.googleusercontent.com",
                "client_secret": "secret",
            }),
        )
        creds = gemini_usage.load_oauth_credentials(gemini_home)
        assert creds["access_token"] == "token-123"
        assert creds["refresh_token"] == "refresh-456"

    def test_raises_on_missing_file(self, tmp_path):
        gemini_home = tmp_path / ".gemini"
        gemini_home.mkdir(parents=True)
        with pytest.raises(gemini_usage.GeminiUsageError, match="not found"):
            gemini_usage.load_oauth_credentials(gemini_home)

    def test_raises_on_invalid_json(self, tmp_path):
        gemini_home = tmp_path / ".gemini"
        write_file(gemini_home / "oauth_creds.json", "not valid json")
        with pytest.raises(gemini_usage.GeminiUsageError, match="Failed to parse"):
            gemini_usage.load_oauth_credentials(gemini_home)

    def test_raises_on_missing_access_token(self, tmp_path):
        gemini_home = tmp_path / ".gemini"
        write_file(
            gemini_home / "oauth_creds.json",
            json.dumps({"refresh_token": "refresh-456"}),
        )
        with pytest.raises(gemini_usage.GeminiUsageError, match="No access_token"):
            gemini_usage.load_oauth_credentials(gemini_home)


class TestLoadGoogleAccountInfo:
    """Tests for load_google_account_info function."""

    def test_loads_email_from_dict(self, tmp_path):
        gemini_home = tmp_path / ".gemini"
        write_file(
            gemini_home / "google_accounts.json",
            json.dumps({"email": "user@example.com", "id": "123"}),
        )
        email, account_id = gemini_usage.load_google_account_info(gemini_home)
        assert email == "user@example.com"
        assert account_id == "123"

    def test_loads_from_accounts_list(self, tmp_path):
        gemini_home = tmp_path / ".gemini"
        write_file(
            gemini_home / "google_accounts.json",
            json.dumps({
                "accounts": [
                    {"email": "first@example.com", "id": "acc1"},
                    {"email": "second@example.com", "id": "acc2"},
                ]
            }),
        )
        email, account_id = gemini_usage.load_google_account_info(gemini_home)
        assert email == "first@example.com"
        assert account_id == "acc1"

    def test_returns_none_on_missing_file(self, tmp_path):
        gemini_home = tmp_path / ".gemini"
        gemini_home.mkdir(parents=True)
        email, account_id = gemini_usage.load_google_account_info(gemini_home)
        assert email is None
        assert account_id is None


class TestCollectUsage:
    """Tests for collect_usage function."""

    def test_collect_usage_success(self, monkeypatch, tmp_path):
        gemini_home = tmp_path / ".gemini"
        write_file(
            gemini_home / "oauth_creds.json",
            json.dumps({
                "access_token": "token-123",
                "refresh_token": "refresh-456",
            }),
        )
        write_file(
            gemini_home / "google_accounts.json",
            json.dumps({"email": "user@example.com"}),
        )

        captured = {"calls": []}

        class DummyResponse:
            status_code = 200
            text = "{}"

            def __init__(self, data):
                self._data = data

            def json(self):
                return self._data

        def fake_post(url, headers, json, timeout):
            captured["calls"].append({"url": url, "headers": headers, "json": json})
            if "loadCodeAssist" in url:
                return DummyResponse({
                    "currentTier": {"id": "standard-tier", "name": "Standard"},
                    "cloudaicompanionProject": "project-123",
                })
            elif "retrieveUserQuota" in url:
                return DummyResponse({
                    "buckets": [
                        {
                            "remainingFraction": 0.80,
                            "tokenType": "input",
                            "resetTime": "2025-01-15T15:00:00Z",
                        },
                        {
                            "remainingFraction": 0.60,
                            "tokenType": "output",
                            "resetTime": "2025-01-15T15:00:00Z",
                        },
                    ]
                })
            return DummyResponse({})

        monkeypatch.setattr(gemini_usage.requests, "post", fake_post)

        snapshot = gemini_usage.collect_usage(gemini_home=gemini_home)

        assert len(captured["calls"]) == 2
        assert "loadCodeAssist" in captured["calls"][0]["url"]
        assert "retrieveUserQuota" in captured["calls"][1]["url"]
        assert snapshot.plan == "Standard"
        assert snapshot.email == "user@example.com"
        assert len(snapshot.windows) == 2
        assert snapshot.windows[0].percent_used == 20.0  # 1 - 0.80
        assert snapshot.windows[1].percent_used == 40.0  # 1 - 0.60

    def test_collect_usage_missing_auth(self, tmp_path):
        gemini_home = tmp_path / ".gemini"
        gemini_home.mkdir(parents=True, exist_ok=True)

        with pytest.raises(gemini_usage.GeminiUsageError, match="not found"):
            gemini_usage.collect_usage(gemini_home=gemini_home)

    def test_collect_usage_handles_auth_failure(self, monkeypatch, tmp_path):
        gemini_home = tmp_path / ".gemini"
        write_file(
            gemini_home / "oauth_creds.json",
            json.dumps({"access_token": "expired-token"}),
        )

        class DummyResponse:
            status_code = 401
            text = "Unauthorized"

            def json(self):
                return {"error": "invalid_token"}

        def fake_post(url, headers, json, timeout):
            return DummyResponse()

        monkeypatch.setattr(gemini_usage.requests, "post", fake_post)

        with pytest.raises(gemini_usage.GeminiUsageError, match="Authentication failed"):
            gemini_usage.collect_usage(gemini_home=gemini_home)

    def test_collect_usage_handles_quota_unavailable(self, monkeypatch, tmp_path):
        """Test that we gracefully handle quota endpoint failures."""
        gemini_home = tmp_path / ".gemini"
        write_file(
            gemini_home / "oauth_creds.json",
            json.dumps({"access_token": "token-123"}),
        )

        call_count = {"n": 0}

        class LoadCodeAssistResponse:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "currentTier": {"id": "free-tier"},
                    "cloudaicompanionProject": "project-123",
                }

        class QuotaErrorResponse:
            status_code = 403
            text = "Forbidden"

            def json(self):
                return {"error": "quota_not_available"}

        def fake_post(url, headers, json, timeout):
            call_count["n"] += 1
            if "loadCodeAssist" in url:
                return LoadCodeAssistResponse()
            elif "retrieveUserQuota" in url:
                return QuotaErrorResponse()

        monkeypatch.setattr(gemini_usage.requests, "post", fake_post)

        # Should succeed but with empty windows
        snapshot = gemini_usage.collect_usage(gemini_home=gemini_home)
        assert snapshot.plan == "Free"
        assert snapshot.windows == []


class TestFormatSnapshot:
    """Tests for format_snapshot function."""

    def test_formats_with_windows(self):
        from shinka.tools.codex_usage import RateLimitWindowData, UsageSnapshot

        snapshot = UsageSnapshot(
            plan="Standard",
            email="user@example.com",
            windows=[
                RateLimitWindowData(
                    label="input tokens",
                    percent_used=25.0,
                    window_minutes=None,
                    reset_at=None,
                    reset_at_local="15:30",
                ),
            ],
            raw={},
        )
        output = gemini_usage.format_snapshot(snapshot)
        assert "Tier: Standard" in output
        assert "Email: user@example.com" in output
        assert "Auth: OAuth (Personal Login)" in output
        assert "input tokens: 25.0% used" in output
        assert "resets at 15:30" in output

    def test_formats_without_windows(self):
        from shinka.tools.codex_usage import UsageSnapshot

        snapshot = UsageSnapshot(
            plan="Free",
            email=None,
            windows=[],
            raw={},
        )
        output = gemini_usage.format_snapshot(snapshot)
        assert "Tier: Free" in output
        assert "quota data not available" in output


class TestMain:
    """Tests for CLI main function."""

    def test_human_output(self, monkeypatch, tmp_path, capsys):
        gemini_home = tmp_path / ".gemini"
        write_file(
            gemini_home / "oauth_creds.json",
            json.dumps({"access_token": "token-123"}),
        )

        class DummyResponse:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "currentTier": {"id": "standard-tier"},
                    "cloudaicompanionProject": "proj",
                    "buckets": [],
                }

        def fake_post(url, headers, json, timeout):
            return DummyResponse()

        monkeypatch.setattr(gemini_usage.requests, "post", fake_post)
        monkeypatch.setattr(gemini_usage, "get_gemini_home", lambda: gemini_home)

        gemini_usage.main(["--human"])
        captured = capsys.readouterr()
        assert "Tier:" in captured.out

    def test_json_output(self, monkeypatch, tmp_path, capsys):
        gemini_home = tmp_path / ".gemini"
        write_file(
            gemini_home / "oauth_creds.json",
            json.dumps({"access_token": "token-123"}),
        )

        class DummyResponse:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "currentTier": {"id": "free-tier"},
                    "cloudaicompanionProject": "proj",
                    "buckets": [],
                }

        def fake_post(url, headers, json, timeout):
            return DummyResponse()

        monkeypatch.setattr(gemini_usage.requests, "post", fake_post)
        monkeypatch.setattr(gemini_usage, "get_gemini_home", lambda: gemini_home)

        gemini_usage.main([])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "plan" in data
        assert "windows" in data
