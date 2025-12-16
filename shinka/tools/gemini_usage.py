"""Fetch Gemini subscription usage and quota details.

This module provides parity with codex_usage.py for Gemini CLI users.
It queries the CloudCode Private API to retrieve quota bucket information.

Only OAuth mode is supported (API key mode cannot access quota endpoints).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

# Reuse dataclasses from codex_usage for consistency
from shinka.tools.codex_usage import RateLimitWindowData, UsageSnapshot, format_reset_timestamp


# API Configuration
CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com"
API_VERSION = "v1internal"
TIMEOUT_SECONDS = 20

# Client metadata for API requests
CLIENT_METADATA = {
    "ideType": "GEMINI_CLI",
    "pluginType": "GEMINI",
}


class GeminiUsageError(RuntimeError):
    """Raised when the Gemini usage helper cannot proceed."""


@dataclasses.dataclass
class GeminiAuthInfo:
    """Authentication info for Gemini CLI."""

    mode: str  # "oauth"
    access_token: str
    refresh_token: Optional[str]
    project_id: Optional[str]
    email: Optional[str]
    tier: Optional[str]  # "free-tier", "standard-tier", "legacy-tier"


def get_gemini_home() -> Path:
    """Get the Gemini CLI home directory."""
    env = os.environ.get("GEMINI_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".gemini"


def load_oauth_credentials(gemini_home: Path) -> Dict[str, Any]:
    """Load OAuth credentials from oauth_creds.json.

    Returns:
        Dict with access_token, refresh_token, client_id, client_secret, etc.

    Raises:
        GeminiUsageError: If credentials file not found or invalid.
    """
    creds_path = gemini_home / "oauth_creds.json"
    if not creds_path.exists():
        raise GeminiUsageError(
            f"Gemini OAuth credentials not found at {creds_path}. "
            "Run `gemini` and authenticate first."
        )

    try:
        data = json.loads(creds_path.read_text())
    except json.JSONDecodeError as exc:
        raise GeminiUsageError(f"Failed to parse {creds_path}: {exc}") from exc

    # The file should have access_token and refresh_token
    if not data.get("access_token"):
        raise GeminiUsageError(
            f"No access_token found in {creds_path}. Re-authenticate with `gemini`."
        )

    return data


def load_google_account_info(gemini_home: Path) -> Tuple[Optional[str], Optional[str]]:
    """Load user email and account ID from google_accounts.json.

    Returns:
        Tuple of (email, account_id), both may be None if not found.
    """
    accounts_path = gemini_home / "google_accounts.json"
    if not accounts_path.exists():
        return None, None

    try:
        data = json.loads(accounts_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None, None

    # The file may have different structures, try common patterns
    if isinstance(data, dict):
        # Try to find email in the data
        email = data.get("email")
        account_id = data.get("account_id") or data.get("id")

        # If it's a list of accounts, get the first one
        accounts = data.get("accounts", [])
        if accounts and isinstance(accounts, list):
            first = accounts[0]
            if isinstance(first, dict):
                email = email or first.get("email")
                account_id = account_id or first.get("id")

        return email, account_id

    return None, None


def _trigger_gemini_cli_refresh() -> bool:
    """Run Gemini CLI to trigger its internal token refresh.

    The Gemini CLI handles token refresh internally when it runs.
    We run a minimal command to trigger this refresh.

    Returns:
        True if refresh succeeded, False otherwise
    """
    import shutil
    import subprocess

    gemini_bin = shutil.which("gemini")
    if not gemini_bin:
        return False

    try:
        # Run a minimal command with JSON output and short timeout
        # The CLI will refresh the token as part of startup
        # Using "hi" as the shortest possible prompt
        result = subprocess.run(
            [gemini_bin, "--output-format", "json", "hi"],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "NO_COLOR": "1"},
        )
        # Success if CLI ran (even errors mean the CLI ran and refreshed)
        return result.returncode == 0 or len(result.stdout) > 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def _is_token_expired(creds: Dict[str, Any]) -> bool:
    """Check if the OAuth token is expired.

    Args:
        creds: OAuth credentials dict

    Returns:
        True if token is expired or expiry unknown, False if still valid
    """
    expiry = creds.get("expiry") or creds.get("token_expiry") or creds.get("expiry_date")
    if not expiry:
        return True  # Unknown expiry, assume expired to be safe

    try:
        if isinstance(expiry, str):
            expiry_dt = dt.datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        elif expiry > 1e12:
            # Timestamp is in milliseconds (Gemini CLI format)
            expiry_dt = dt.datetime.fromtimestamp(expiry / 1000, tz=dt.timezone.utc)
        else:
            expiry_dt = dt.datetime.fromtimestamp(expiry, tz=dt.timezone.utc)

        # Add 60 second buffer to avoid edge cases
        return expiry_dt <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)
    except (ValueError, TypeError):
        return True  # Can't parse, assume expired


def refresh_oauth_token(creds: Dict[str, Any], gemini_home: Optional[Path] = None) -> str:
    """Refresh the OAuth access token if needed.

    Tries multiple refresh strategies:
    1. Check if token is still valid
    2. Use google-auth library if available
    3. Run Gemini CLI to trigger its internal refresh

    Args:
        creds: OAuth credentials dict with refresh_token, client_id, client_secret
        gemini_home: Path to reload credentials from after CLI refresh

    Returns:
        Valid access token (refreshed if necessary)

    Raises:
        GeminiUsageError: If refresh fails
    """
    access_token = creds.get("access_token")
    refresh_token = creds.get("refresh_token")

    # Check if token is still valid
    if not _is_token_expired(creds):
        return access_token

    # Strategy 1: Try google-auth library refresh
    if refresh_token and creds.get("client_id") and creds.get("client_secret"):
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request

            google_creds = Credentials(
                token=access_token,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds.get("client_id"),
                client_secret=creds.get("client_secret"),
            )

            if google_creds.expired or not google_creds.valid:
                google_creds.refresh(Request())
                return google_creds.token

        except ImportError:
            pass  # google-auth not installed
        except Exception:
            pass  # Refresh failed

    # Strategy 2: Run Gemini CLI to trigger its internal token refresh
    # This is the most reliable method since the CLI handles its own auth
    if _trigger_gemini_cli_refresh():
        # Reload credentials from disk after CLI refresh
        home = gemini_home or get_gemini_home()
        try:
            new_creds = load_oauth_credentials(home)
            new_token = new_creds.get("access_token")
            if new_token and new_token != access_token:
                return new_token
        except Exception:
            pass  # Couldn't reload, fall through

    # Return current token and hope it works
    if not access_token:
        raise GeminiUsageError("No valid access token available")

    return access_token


def load_auth_info(gemini_home: Optional[Path] = None) -> GeminiAuthInfo:
    """Load and prepare authentication info for Gemini API calls.

    Args:
        gemini_home: Path to Gemini CLI home directory (default: ~/.gemini)

    Returns:
        GeminiAuthInfo with valid access token

    Raises:
        GeminiUsageError: If authentication not available
    """
    home = gemini_home or get_gemini_home()

    # Load OAuth credentials
    creds = load_oauth_credentials(home)

    # Get valid access token (refresh if needed)
    access_token = refresh_oauth_token(creds, gemini_home=home)

    # Load account info
    email, _ = load_google_account_info(home)

    return GeminiAuthInfo(
        mode="oauth",
        access_token=access_token,
        refresh_token=creds.get("refresh_token"),
        project_id=None,  # Will be populated by loadCodeAssist
        email=email,
        tier=None,  # Will be populated by loadCodeAssist
    )


def build_headers(auth: GeminiAuthInfo) -> Dict[str, str]:
    """Build HTTP headers for API requests."""
    return {
        "Authorization": f"Bearer {auth.access_token}",
        "Content-Type": "application/json",
        "User-Agent": "gemini-usage-helper",
    }


def build_api_url(method: str) -> str:
    """Build the API endpoint URL."""
    return f"{CODE_ASSIST_ENDPOINT}/{API_VERSION}:{method}"


def load_code_assist(auth: GeminiAuthInfo) -> Dict[str, Any]:
    """Call loadCodeAssist to get project ID and user tier.

    Returns:
        Response dict with currentTier, cloudaicompanionProject, etc.

    Raises:
        GeminiUsageError: If API call fails
    """
    url = build_api_url("loadCodeAssist")
    headers = build_headers(auth)

    payload = {
        "metadata": CLIENT_METADATA,
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise GeminiUsageError(f"Failed to connect to Gemini API: {exc}") from exc

    if response.status_code == 401:
        raise GeminiUsageError(
            "Authentication failed. Your token may have expired. "
            "Re-authenticate with `gemini`."
        )

    if response.status_code >= 400:
        raise GeminiUsageError(
            f"loadCodeAssist failed with status {response.status_code}: "
            f"{response.text.strip()}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise GeminiUsageError(f"Failed to decode API response: {exc}") from exc


def retrieve_user_quota(auth: GeminiAuthInfo, project_id: str) -> Dict[str, Any]:
    """Call retrieveUserQuota to get quota bucket information.

    Args:
        auth: Authentication info
        project_id: The cloudaicompanionProject ID from loadCodeAssist

    Returns:
        Response dict with buckets array

    Raises:
        GeminiUsageError: If API call fails
    """
    url = build_api_url("retrieveUserQuota")
    headers = build_headers(auth)

    payload = {
        "project": project_id,
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise GeminiUsageError(f"Failed to connect to Gemini API: {exc}") from exc

    if response.status_code == 401:
        raise GeminiUsageError(
            "Authentication failed. Your token may have expired. "
            "Re-authenticate with `gemini`."
        )

    if response.status_code >= 400:
        raise GeminiUsageError(
            f"retrieveUserQuota failed with status {response.status_code}: "
            f"{response.text.strip()}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise GeminiUsageError(f"Failed to decode API response: {exc}") from exc


def parse_quota_buckets(buckets: List[Dict[str, Any]]) -> List[RateLimitWindowData]:
    """Parse quota bucket info into RateLimitWindowData objects.

    Args:
        buckets: List of BucketInfo dicts from retrieveUserQuota

    Returns:
        List of RateLimitWindowData with usage info
    """
    windows: List[RateLimitWindowData] = []

    for bucket in buckets:
        # Convert remainingFraction to percent used
        remaining_fraction = bucket.get("remainingFraction")
        if remaining_fraction is not None:
            percent_used = (1.0 - remaining_fraction) * 100
        else:
            # Try to parse from remainingAmount if fraction not available
            percent_used = 0.0

        # Parse reset time
        reset_time_str = bucket.get("resetTime")
        reset_at: Optional[int] = None
        if reset_time_str:
            try:
                # Parse ISO format timestamp
                reset_dt = dt.datetime.fromisoformat(
                    reset_time_str.replace("Z", "+00:00")
                )
                reset_at = int(reset_dt.timestamp())
            except (ValueError, TypeError):
                pass

        # Build label from token type and model
        token_type = bucket.get("tokenType", "unknown")
        model_id = bucket.get("modelId")
        if model_id:
            label = f"{token_type} ({model_id})"
        else:
            label = f"{token_type} tokens"

        windows.append(
            RateLimitWindowData(
                label=label,
                percent_used=round(percent_used, 1),
                window_minutes=None,  # Not provided by Gemini API
                reset_at=reset_at,
                reset_at_local=format_reset_timestamp(reset_at),
            )
        )

    return windows


def tier_display_name(tier_id: Optional[str]) -> Optional[str]:
    """Convert tier ID to display name."""
    if not tier_id:
        return None

    tier_map = {
        "free-tier": "Free",
        "standard-tier": "Standard",
        "legacy-tier": "Legacy",
    }
    return tier_map.get(tier_id, tier_id.replace("-", " ").title())


def collect_usage(gemini_home: Optional[Path] = None) -> UsageSnapshot:
    """Collect Gemini usage and quota information.

    Args:
        gemini_home: Path to Gemini CLI home directory (default: ~/.gemini)

    Returns:
        UsageSnapshot with plan, email, and quota windows

    Raises:
        GeminiUsageError: If unable to collect usage data
    """
    home = gemini_home or get_gemini_home()

    # Load auth info
    auth = load_auth_info(home)

    # Step 1: Call loadCodeAssist to get project ID and tier
    code_assist_response = load_code_assist(auth)

    project_id = code_assist_response.get("cloudaicompanionProject")
    current_tier = code_assist_response.get("currentTier") or {}
    # Use the display name from API, fallback to id-based name
    tier_name = current_tier.get("name") if isinstance(current_tier, dict) else None
    tier_id = current_tier.get("id") if isinstance(current_tier, dict) else None

    # Update auth with project and tier info
    auth.project_id = project_id
    auth.tier = tier_id

    # Step 2: Call retrieveUserQuota if we have a project ID
    windows: List[RateLimitWindowData] = []
    quota_response: Dict[str, Any] = {}

    if project_id:
        try:
            quota_response = retrieve_user_quota(auth, project_id)
            buckets = quota_response.get("buckets") or []
            windows = parse_quota_buckets(buckets)
        except GeminiUsageError:
            # Quota endpoint may not be available for all tiers
            pass

    # Combine raw responses
    raw = {
        "loadCodeAssist": code_assist_response,
        "retrieveUserQuota": quota_response,
    }

    return UsageSnapshot(
        plan=tier_name or tier_display_name(tier_id),
        email=auth.email,
        windows=windows,
        raw=raw,
    )


def format_snapshot(snapshot: UsageSnapshot) -> str:
    """Format usage snapshot as human-readable text."""
    plan = snapshot.plan or "<unknown tier>"
    lines = [f"Tier: {plan}"]

    if snapshot.email:
        lines.append(f"Email: {snapshot.email}")

    lines.append("Auth: OAuth (Personal Login)")

    if not snapshot.windows:
        lines.append("Limits: quota data not available")
    else:
        lines.append("Limits:")
        for window in snapshot.windows:
            reset = f", resets at {window.reset_at_local}" if window.reset_at_local else ""
            lines.append(f"  - {window.label}: {window.percent_used:.1f}% used{reset}")

    return "\n".join(lines)


def main(argv: Optional[Iterable[str]] = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Fetch Gemini usage and subscription info."
    )
    parser.add_argument(
        "--human",
        action="store_true",
        help="Print a friendly summary instead of JSON."
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output."
    )
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Include the raw API payload in the JSON output.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        snapshot = collect_usage()
    except GeminiUsageError as exc:
        parser.exit(1, f"{exc}\n")

    if args.human:
        print(format_snapshot(snapshot))
        return

    indent = 2 if args.pretty else None
    print(json.dumps(snapshot.to_dict(include_raw=args.include_raw), indent=indent))


if __name__ == "__main__":  # pragma: no cover
    main()
