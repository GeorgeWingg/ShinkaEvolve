"""Codex authentication helpers (headless-friendly).

This module provides a wrapper around the Codex CLI login flows:
- OAuth device auth (`codex login --device-auth`) for headless environments
- API key auth (`codex login --with-api-key`) for non-interactive setups

Key enhancement: Automatically opens the auth URL in the user's browser
and displays the device code prominently.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Literal, Optional, Tuple

logger = logging.getLogger(__name__)


class CodexAuthError(RuntimeError):
    """Raised when Codex authentication cannot be established."""


def _is_interactive() -> bool:
    """Check if we're in an interactive terminal context.

    Avoid hanging in non-interactive contexts (CI, background jobs).
    """
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _status_looks_authenticated(stdout: str, stderr: str) -> bool:
    """Parse CLI status output to detect authentication state.

    Be conservative: treat explicit "not logged in"/"unauthorized" as failure.
    """
    combined = f"{stdout}\n{stderr}".lower()
    if "not logged" in combined:
        return False
    if "unauthorized" in combined:
        return False
    if "please login" in combined or "please log in" in combined:
        return False
    return True


def is_codex_authenticated(codex_bin: Path) -> bool:
    """Return True if Codex CLI reports an authenticated session."""
    try:
        result = subprocess.run(
            [str(codex_bin), "login", "status"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    if result.returncode != 0:
        return False
    return _status_looks_authenticated(result.stdout or "", result.stderr or "")


def _strip_ansi_codes(text: str) -> str:
    """Remove ANSI escape codes from text."""
    ansi_pattern = re.compile(r'\x1b\[[0-9;]*m|\[(?:\d+;)*\d*m')
    return ansi_pattern.sub('', text)


def _parse_device_auth_output(output: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract auth URL and device code from Codex CLI output.

    Expected output format:
        Follow these steps to sign in with ChatGPT using device code authorization:
        1. Open this link in your browser: https://auth.openai.com/codex/device
        2. Enter this one-time code (expires in 15 minutes): D2CR-A7XYL

    Returns:
        Tuple of (url, code) where either may be None if not found.
    """
    # Strip ANSI color codes first
    clean_output = _strip_ansi_codes(output)

    # Match auth URL (could be auth.openai.com or platform.openai.com)
    url_match = re.search(r'https://[a-z.]*openai\.com/\S+', clean_output)

    # Match device code pattern: XXXX-XXXXX (4 chars, dash, 5 chars)
    code_match = re.search(r'\b([A-Z0-9]{4}-[A-Z0-9]{5})\b', clean_output)

    url = url_match.group(0) if url_match else None
    code = code_match.group(1) if code_match else None

    return url, code


def _login_with_api_key(codex_bin: Path, api_key: str, *, timeout_seconds: int) -> bool:
    """Attempt a non-interactive login using an API key via stdin."""
    try:
        result = subprocess.run(
            [str(codex_bin), "login", "--with-api-key"],
            input=f"{api_key}\n",
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    return result.returncode == 0


# The device auth URL is always the same - we can open it immediately
CODEX_DEVICE_AUTH_URL = "https://auth.openai.com/codex/device"


def _login_device_auth(codex_bin: Path, *, timeout_seconds: int) -> bool:
    """Attempt device auth login with immediate browser opening.

    Flow:
    1. Open browser to auth URL immediately (URL is always the same)
    2. Start CLI process to get the device code
    3. Display code prominently as soon as we parse it
    """
    # Open browser IMMEDIATELY - don't wait for CLI
    try:
        webbrowser.open(CODEX_DEVICE_AUTH_URL)
        logger.info(f"Opened browser to {CODEX_DEVICE_AUTH_URL}")
    except Exception as e:
        logger.warning(f"Could not open browser: {e}")
        print(f"\n>>> Open this URL: {CODEX_DEVICE_AUTH_URL}\n", flush=True)

    # Start CLI process to get the device code
    try:
        proc = subprocess.Popen(
            [str(codex_bin), "login", "--device-auth"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as e:
        logger.error(f"Failed to start Codex device auth: {e}")
        return False

    output_lines = []
    code_shown = False

    try:
        # Read output line by line, parsing for the device code
        for line in proc.stdout:  # type: ignore[union-attr]
            output_lines.append(line)

            # Parse for device code as soon as possible
            if not code_shown:
                _, code = _parse_device_auth_output(''.join(output_lines))
                if code:
                    code_shown = True
                    print(f"\n>>> Enter this code: {code}\n", flush=True)

        # Wait for process to complete
        proc.wait(timeout=timeout_seconds)

    except subprocess.TimeoutExpired:
        proc.kill()
        logger.warning(f"Device auth timed out after {timeout_seconds}s")
        print(f"\n>>> Device auth timed out. Please try again.\n", flush=True)
        return False
    except Exception as e:
        proc.kill()
        logger.error(f"Device auth failed: {e}")
        return False

    return proc.returncode == 0


def ensure_codex_authenticated(
    codex_bin: Path,
    *,
    api_key: Optional[str] = None,
    timeout_seconds: int = 900,
    allow_interactive: Optional[bool] = None,
) -> Literal["status", "device_auth", "api_key"]:
    """Ensure Codex is authenticated, attempting login flows if needed.

    Order of operations (subscription-first approach):
    1. `codex login status` (fast path - check if already authenticated)
    2. If not logged in and interactive, attempt `codex login --device-auth`
    3. If still not logged in and api_key provided, attempt `codex login --with-api-key`

    Args:
        codex_bin: Path to the Codex CLI binary.
        api_key: Optional OpenAI API key for fallback authentication.
        timeout_seconds: Maximum time to wait for auth flows (default 15 min).
        allow_interactive: Override interactive detection (None = auto-detect).

    Returns:
        The authentication method that succeeded: "status", "device_auth", or "api_key".

    Raises:
        CodexAuthError: If authentication is not available after all attempts.
    """
    # Fast path: already authenticated
    if is_codex_authenticated(codex_bin):
        logger.debug("Codex already authenticated (status check passed)")
        return "status"

    # Determine if we can try interactive auth
    interactive = _is_interactive() if allow_interactive is None else allow_interactive

    # Try device auth first (preferred for subscription users)
    if interactive:
        logger.info("Attempting Codex device authentication...")
        if _login_device_auth(codex_bin, timeout_seconds=timeout_seconds):
            if is_codex_authenticated(codex_bin):
                logger.info("Device authentication successful")
                return "device_auth"
            logger.warning("Device auth completed but status check failed")

    # Fall back to API key auth
    if api_key:
        logger.info("Attempting Codex API key authentication...")
        if _login_with_api_key(codex_bin, api_key, timeout_seconds=timeout_seconds):
            if is_codex_authenticated(codex_bin):
                logger.info("API key authentication successful")
                return "api_key"
            logger.warning("API key auth completed but status check failed")

    # All methods failed
    raise CodexAuthError(
        "Codex authentication required. Options:\n"
        "  1. Device auth (recommended for ChatGPT subscribers):\n"
        "     - Enable in ChatGPT Security Settings: chatgpt.com/settings/security\n"
        "     - Run: codex login --device-auth\n"
        "  2. API key:\n"
        "     - Set OPENAI_API_KEY environment variable\n"
        "     - Or run: echo $OPENAI_API_KEY | codex login --with-api-key"
    )
