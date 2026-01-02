"""Fetch Claude subscription usage and plan details.

This module provides parity with codex_usage.py for Claude CLI users.
It queries the Anthropic OAuth API to retrieve rate limit information.

Supports:
- OAuth mode (Claude CLI personal login via Keychain or file)
- API key mode (ANTHROPIC_API_KEY fallback) - note: API key cannot access usage endpoint

Credential Loading Priority (matching CodexBar):
1. macOS Keychain (service: "Claude Code-credentials") - PRIMARY on macOS
2. File fallback: ~/.claude/.credentials.json
3. ANTHROPIC_API_KEY environment variable
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import getpass
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

# Reuse shared data classes from codex_usage
from shinka.tools.codex_usage import (
    RateLimitWindowData,
    UsageSnapshot,
    format_reset_timestamp,
)


# API Configuration
CLAUDE_API_BASE = "https://api.anthropic.com"
CLAUDE_USAGE_ENDPOINT = "/api/oauth/usage"
CLAUDE_BETA_HEADER = "oauth-2025-04-20"  # REQUIRED - API fails without this
TIMEOUT_SECONDS = 30

# Keychain Configuration (matches Claude Code CLI)
KEYCHAIN_SERVICE = "Claude Code-credentials"


class ClaudeUsageError(RuntimeError):
    """Raised when the Claude usage helper cannot proceed."""


@dataclasses.dataclass
class ClaudeAuthInfo:
    """Authentication info for Claude API."""

    mode: str  # "oauth" or "api_key"
    access_token: Optional[str]
    api_key: Optional[str]
    expires_at: Optional[int]  # Unix timestamp (seconds)
    scopes: List[str]
    rate_limit_tier: Optional[str]
    email: Optional[str] = None


@dataclasses.dataclass
class ExtraUsageData:
    """Extra usage credits information (Claude Extra)."""

    is_enabled: bool
    monthly_limit: Optional[float]
    used_credits: Optional[float]
    utilization: Optional[float]
    currency: str


def get_claude_home() -> Path:
    """Get the Claude CLI home directory."""
    env = os.environ.get("CLAUDE_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude"


def load_from_keychain() -> Optional[Dict[str, Any]]:
    """Load OAuth credentials from OS credential store.

    On macOS: Uses Keychain with service "Claude Code-credentials"
    On Windows: Uses Windows Credential Manager with target "Claude Code-credentials"
    On Linux: Falls back to file-based credentials

    Returns:
        Dict containing the claudeAiOauth data, or None if not available
    """
    system = platform.system()

    if system == "Darwin":
        return _load_from_macos_keychain()
    elif system == "Windows":
        return _load_from_windows_credential_manager()
    else:
        # Linux and other platforms use file-based credentials
        return None


def _load_from_macos_keychain() -> Optional[Dict[str, Any]]:
    """Load credentials from macOS Keychain."""
    try:
        username = getpass.getuser()
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s", KEYCHAIN_SERVICE,
                "-a", username,
                "-w",  # Output password only
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            return None

        password_data = result.stdout.strip()
        if not password_data:
            return None

        return _parse_credential_data(password_data)

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _load_from_windows_credential_manager() -> Optional[Dict[str, Any]]:
    """Load credentials from Windows Credential Manager.

    Uses PowerShell to access the Windows Credential Manager since
    there's no built-in CLI tool like macOS 'security' command.
    """
    try:
        # PowerShell script to read from Windows Credential Manager
        # Uses the CredRead API via .NET
        ps_script = f'''
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$cred = Get-StoredCredential -Target "{KEYCHAIN_SERVICE}" -ErrorAction SilentlyContinue
if ($cred) {{
    $cred.Password | ConvertFrom-SecureString -AsPlainText
}} else {{
    # Try cmdkey approach as fallback
    $null
}}
'''
        # Simpler approach using cmdkey (more reliable across Windows versions)
        # cmdkey can't retrieve passwords directly, so we use PowerShell with CredentialManager
        ps_script_simple = f'''
try {{
    # Try using CredentialManager module if available
    $cred = Get-StoredCredential -Target "{KEYCHAIN_SERVICE}" -ErrorAction Stop
    if ($cred) {{
        [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($cred.Password)
        )
    }}
}} catch {{
    # CredentialManager module not available, try native approach
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public class CredentialManager {{
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool CredRead(string target, int type, int flags, out IntPtr credential);

    [DllImport("advapi32.dll", SetLastError = true)]
    public static extern bool CredFree(IntPtr credential);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct CREDENTIAL {{
        public int Flags;
        public int Type;
        public string TargetName;
        public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public int CredentialBlobSize;
        public IntPtr CredentialBlob;
        public int Persist;
        public int AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }}

    public static string GetCredential(string target) {{
        IntPtr credPtr;
        if (CredRead(target, 1, 0, out credPtr)) {{
            try {{
                CREDENTIAL cred = (CREDENTIAL)Marshal.PtrToStructure(credPtr, typeof(CREDENTIAL));
                if (cred.CredentialBlob != IntPtr.Zero && cred.CredentialBlobSize > 0) {{
                    return Marshal.PtrToStringUni(cred.CredentialBlob, cred.CredentialBlobSize / 2);
                }}
            }} finally {{
                CredFree(credPtr);
            }}
        }}
        return null;
    }}
}}
"@
    [CredentialManager]::GetCredential("{KEYCHAIN_SERVICE}")
}}
'''
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script_simple],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )

        if result.returncode != 0:
            return None

        password_data = result.stdout.strip()
        if not password_data:
            return None

        return _parse_credential_data(password_data)

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _parse_credential_data(password_data: str) -> Optional[Dict[str, Any]]:
    """Parse credential data from OS credential store."""
    try:
        payload = json.loads(password_data)
    except json.JSONDecodeError:
        return None

    # Extract claudeAiOauth data
    oauth_data = payload.get("claudeAiOauth")
    if oauth_data:
        return oauth_data

    # Some versions may store it at root level
    if "accessToken" in payload:
        return payload

    return None


def load_oauth_credentials(claude_home: Path) -> Dict[str, Any]:
    """Load OAuth credentials from the Claude credentials file.

    Args:
        claude_home: Path to the .claude directory

    Returns:
        Dict containing the claudeAiOauth data

    Raises:
        ClaudeUsageError: If credentials file not found or invalid
    """
    creds_path = claude_home / ".credentials.json"

    if not creds_path.exists():
        raise ClaudeUsageError(
            f"Claude credentials file not found at {creds_path}. "
            "Run `claude` to authenticate first."
        )

    try:
        payload = json.loads(creds_path.read_text())
    except json.JSONDecodeError as exc:
        raise ClaudeUsageError(f"Failed to parse {creds_path}: {exc}") from exc

    oauth_data = payload.get("claudeAiOauth")
    if not oauth_data:
        raise ClaudeUsageError(
            f"No claudeAiOauth data found in {creds_path}. "
            "Credentials may be from an older version. Run `claude` to re-authenticate."
        )

    return oauth_data


def load_auth_info(claude_home: Optional[Path] = None) -> ClaudeAuthInfo:
    """Load Claude authentication info from available sources.

    Priority (matching CodexBar):
    1. macOS Keychain (service: "Claude Code-credentials") - PRIMARY on macOS
    2. File fallback: ~/.claude/.credentials.json
    3. ANTHROPIC_API_KEY environment variable
    4. Unified credential store (shinka/tools/credentials.py)

    Args:
        claude_home: Optional path to Claude home directory

    Returns:
        ClaudeAuthInfo with authentication details

    Raises:
        ClaudeUsageError: If no valid authentication found
    """
    home = claude_home or get_claude_home()
    oauth_data = None

    # Try macOS Keychain first (primary source on macOS)
    keychain_data = load_from_keychain()
    if keychain_data:
        oauth_data = keychain_data

    # Fall back to file-based credentials
    if not oauth_data:
        try:
            oauth_data = load_oauth_credentials(home)
        except ClaudeUsageError:
            pass  # Continue to API key fallback

    # Process OAuth data if found
    if oauth_data:
        access_token = oauth_data.get("accessToken", "").strip()

        if access_token:
            # Convert expiresAt from milliseconds to seconds
            expires_at_ms = oauth_data.get("expiresAt")
            expires_at = None
            if expires_at_ms:
                expires_at = int(expires_at_ms / 1000)

            scopes = oauth_data.get("scopes", [])
            rate_limit_tier = oauth_data.get("rateLimitTier")

            # Check if token is expired
            if expires_at and expires_at < int(dt.datetime.now(dt.timezone.utc).timestamp()):
                raise ClaudeUsageError(
                    "OAuth token has expired. Run `claude` to re-authenticate."
                )

            return ClaudeAuthInfo(
                mode="oauth",
                access_token=access_token,
                api_key=None,
                expires_at=expires_at,
                scopes=scopes,
                rate_limit_tier=rate_limit_tier,
            )

    # Try ANTHROPIC_API_KEY environment variable
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not api_key:
        # Try unified credential store
        try:
            from shinka.tools.credentials import get_api_key

            api_key = get_api_key("claude")
        except Exception:
            pass

    if api_key:
        return ClaudeAuthInfo(
            mode="api_key",
            access_token=None,
            api_key=api_key,
            expires_at=None,
            scopes=[],
            rate_limit_tier=None,
        )

    raise ClaudeUsageError(
        "No Claude authentication found. "
        "Run `claude` to authenticate with OAuth, or set ANTHROPIC_API_KEY."
    )


def build_headers(auth: ClaudeAuthInfo) -> Dict[str, str]:
    """Build HTTP headers for the Claude API request.

    Args:
        auth: Authentication info

    Returns:
        Dict of HTTP headers

    Raises:
        ClaudeUsageError: If auth mode doesn't support usage endpoint
    """
    if auth.mode != "oauth":
        raise ClaudeUsageError(
            "Claude usage endpoint requires OAuth authentication. "
            "API key mode does not have access to usage data. "
            "Run `claude` to authenticate with OAuth."
        )

    return {
        "Authorization": f"Bearer {auth.access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "anthropic-beta": CLAUDE_BETA_HEADER,  # REQUIRED
        "User-Agent": "shinka-claude-usage/1.0",
    }


def parse_iso8601_date(date_string: Optional[str]) -> Optional[int]:
    """Parse ISO8601 date string to Unix timestamp.

    Args:
        date_string: ISO8601 formatted date string (e.g., "2024-01-15T12:30:00Z")

    Returns:
        Unix timestamp as int, or None if parsing fails
    """
    if not date_string:
        return None

    try:
        # Handle both with and without timezone
        if date_string.endswith("Z"):
            date_string = date_string[:-1] + "+00:00"
        dt_obj = dt.datetime.fromisoformat(date_string)
        return int(dt_obj.timestamp())
    except (ValueError, TypeError):
        return None


def normalize_extra_amounts(
    used: Optional[float], limit: Optional[float]
) -> tuple[Optional[float], Optional[float]]:
    """Normalize extra usage amounts from cents to dollars if needed.

    Claude's API may return amounts in cents instead of dollars.
    Heuristic: if both are whole numbers AND limit >= 1000, assume cents and divide by 100.
    This catches cases like limit=10000 (which would be $100) but avoids
    normalizing values that are already in dollars like limit=100.0.

    Args:
        used: Used credits amount
        limit: Monthly limit amount

    Returns:
        Tuple of (normalized_used, normalized_limit)
    """
    if used is None or limit is None:
        return (used, limit)

    def is_whole(value: float) -> bool:
        return abs(round(value) - value) < 0.000001

    # If values look like cents (whole numbers, limit >= 1000 suggesting cents)
    # $10 = 1000 cents, so anything >= 1000 that's a whole number is likely cents
    if limit >= 1000 and used >= 0 and is_whole(limit) and is_whole(used):
        return (used / 100.0, limit / 100.0)

    return (used, limit)


def parse_usage_windows(payload: Dict[str, Any]) -> List[RateLimitWindowData]:
    """Parse rate limit windows from API response.

    Args:
        payload: JSON response from Claude usage API

    Returns:
        List of RateLimitWindowData objects

    Raises:
        ClaudeUsageError: If required five_hour window is missing
    """
    windows: List[RateLimitWindowData] = []

    # Window definitions: (key, label, window_minutes, is_mandatory)
    window_defs = [
        ("five_hour", "5h limit", 300, True),
        ("seven_day", "7d limit", 10080, False),
        ("seven_day_opus", "7d Opus limit", 10080, False),
        ("seven_day_sonnet", "7d Sonnet limit", 10080, False),
    ]

    for key, label, window_minutes, mandatory in window_defs:
        window = payload.get(key)
        if window is None:
            if mandatory:
                raise ClaudeUsageError(
                    f"Missing required rate limit window: {key}. "
                    "The Claude usage API may have changed format."
                )
            continue

        utilization = window.get("utilization")
        if utilization is None and mandatory:
            raise ClaudeUsageError(
                f"Missing utilization in {key} window. "
                "Cannot determine usage percentage."
            )

        if utilization is None:
            continue

        resets_at_str = window.get("resets_at")
        reset_at = parse_iso8601_date(resets_at_str)
        reset_local = format_reset_timestamp(reset_at)

        windows.append(
            RateLimitWindowData(
                label=label,
                percent_used=float(utilization),
                window_minutes=window_minutes,
                reset_at=reset_at,
                reset_at_local=reset_local,
            )
        )

    return windows


def parse_extra_usage(payload: Dict[str, Any]) -> Optional[ExtraUsageData]:
    """Parse extra usage (Claude Extra) from API response.

    Args:
        payload: JSON response from Claude usage API

    Returns:
        ExtraUsageData if present, None otherwise
    """
    extra = payload.get("extra_usage")
    if not extra:
        return None

    is_enabled = extra.get("is_enabled", False)
    if not is_enabled:
        return None

    monthly_limit = extra.get("monthly_limit")
    used_credits = extra.get("used_credits")
    utilization = extra.get("utilization")
    currency = extra.get("currency", "USD")

    # Normalize amounts if they appear to be in cents
    used_credits, monthly_limit = normalize_extra_amounts(used_credits, monthly_limit)

    return ExtraUsageData(
        is_enabled=is_enabled,
        monthly_limit=monthly_limit,
        used_credits=used_credits,
        utilization=utilization,
        currency=currency,
    )


def infer_plan_name(rate_limit_tier: Optional[str]) -> Optional[str]:
    """Convert rate_limit_tier to user-friendly plan name.

    Args:
        rate_limit_tier: Raw tier string from credentials

    Returns:
        Human-readable plan name, or None
    """
    if not rate_limit_tier:
        return None

    tier_lower = rate_limit_tier.lower()

    if "max" in tier_lower:
        return "Claude Max"
    if "pro" in tier_lower:
        return "Claude Pro"
    if "team" in tier_lower:
        return "Claude Team"
    if "enterprise" in tier_lower:
        return "Claude Enterprise"
    if "free" in tier_lower:
        return "Claude Free"

    # Return titlecased version as fallback
    return rate_limit_tier.replace("_", " ").replace("-", " ").title()


def fetch_usage(auth: ClaudeAuthInfo) -> Dict[str, Any]:
    """Fetch usage data from Claude OAuth API.

    Args:
        auth: Authentication info (must be OAuth mode)

    Returns:
        Raw JSON response from the API

    Raises:
        ClaudeUsageError: On API errors or invalid responses
    """
    headers = build_headers(auth)
    url = f"{CLAUDE_API_BASE}{CLAUDE_USAGE_ENDPOINT}"

    try:
        response = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise ClaudeUsageError(f"Network error fetching usage: {exc}") from exc

    if response.status_code == 401:
        raise ClaudeUsageError(
            "Unauthorized (401). OAuth token may have expired. "
            "Run `claude` to re-authenticate."
        )
    elif response.status_code == 403:
        raise ClaudeUsageError(
            "Forbidden (403). Your OAuth token may not have the required scope. "
            "Ensure 'user:profile' scope is granted. Run `claude` to re-authenticate."
        )
    elif response.status_code == 429:
        raise ClaudeUsageError(
            "Rate limited (429). Too many requests to the usage endpoint. "
            "Try again in a few minutes."
        )
    elif response.status_code >= 400:
        raise ClaudeUsageError(
            f"API error ({response.status_code}): {response.text.strip()}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ClaudeUsageError(f"Failed to decode usage response: {exc}") from exc


def collect_usage(claude_home: Optional[Path] = None) -> UsageSnapshot:
    """Collect Claude usage information.

    Args:
        claude_home: Optional path to Claude home directory

    Returns:
        UsageSnapshot with rate limit and plan information

    Raises:
        ClaudeUsageError: If unable to fetch or parse usage
    """
    home = claude_home or get_claude_home()
    auth = load_auth_info(home)

    # Fetch usage data
    payload = fetch_usage(auth)

    # Parse rate limit windows
    windows = parse_usage_windows(payload)

    # Parse extra usage if present
    extra = parse_extra_usage(payload)

    # Determine plan name
    plan = infer_plan_name(auth.rate_limit_tier)
    if not plan and extra and extra.is_enabled:
        plan = "Claude (with Extra)"

    return UsageSnapshot(
        plan=plan,
        email=auth.email,
        windows=windows,
        raw=payload,
    )


def format_snapshot(snapshot: UsageSnapshot) -> str:
    """Format usage snapshot as human-readable text.

    Args:
        snapshot: Usage snapshot to format

    Returns:
        Formatted string representation
    """
    plan = snapshot.plan or "<unknown plan>"
    lines = [f"Plan: {plan}"]

    if snapshot.email:
        lines.append(f"Email: {snapshot.email}")

    if not snapshot.windows:
        lines.append("Limits: data not available yet.")
    else:
        lines.append("Limits:")
        for window in snapshot.windows:
            remaining = 100.0 - window.percent_used
            reset = f", resets at {window.reset_at_local}" if window.reset_at_local else ""
            lines.append(
                f"  - {window.label}: {window.percent_used:.1f}% used "
                f"({remaining:.1f}% remaining){reset}"
            )

    # Add extra usage info if present
    extra = snapshot.raw.get("extra_usage")
    if extra and extra.get("is_enabled"):
        used = extra.get("used_credits", 0)
        limit = extra.get("monthly_limit")
        currency = extra.get("currency", "USD")
        used, limit = normalize_extra_amounts(used, limit)
        if limit:
            lines.append(f"Extra Usage: ${used:.2f} / ${limit:.2f} {currency}")
        else:
            lines.append(f"Extra Usage: ${used:.2f} {currency} used")

    return "\n".join(lines)


def main(argv: Optional[Iterable[str]] = None) -> None:
    """CLI entry point for Claude usage tool."""
    parser = argparse.ArgumentParser(
        description="Fetch Claude usage and subscription info."
    )
    parser.add_argument(
        "--human",
        action="store_true",
        help="Print a friendly summary instead of JSON.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Include the raw API payload in the JSON output.",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        snapshot = collect_usage()
    except ClaudeUsageError as exc:
        parser.exit(1, f"{exc}\n")

    if args.human:
        print(format_snapshot(snapshot))
        return

    indent = 2 if args.pretty else None
    print(json.dumps(snapshot.to_dict(include_raw=args.include_raw), indent=indent))


if __name__ == "__main__":  # pragma: no cover
    main()
