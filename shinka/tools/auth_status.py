"""Consolidated authentication status detection for all agentic backends.

This module provides unified auth checking for Codex, Gemini, Claude, and ShinkaAgent
backends. It's used by the BackendBandit to determine which backends are available
for selection.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Import the ensure_*_available functions from each backend
from shinka.edit.codex_cli import CodexUnavailableError, ensure_codex_available
from shinka.edit.gemini_cli import GeminiUnavailableError, ensure_gemini_available
from shinka.edit.claude_cli import ClaudeUnavailableError, ensure_claude_available
from shinka.edit.shinka_agent import ShinkaUnavailableError, ensure_shinka_available


# All supported backends
ALL_BACKENDS = ["codex", "gemini", "claude", "shinka"]


@dataclass
class BackendAuthStatus:
    """Authentication status for a single backend."""
    
    backend: str
    available: bool
    cli_path: Optional[str] = None
    plan: Optional[str] = None
    email: Optional[str] = None
    error: Optional[str] = None


def check_codex_auth() -> BackendAuthStatus:
    """Check if Codex CLI is installed and authenticated.
    
    Checks:
    1. CLI binary exists (ensure_codex_available)
    2. Auth file exists (~/.codex/auth.json)
    
    Returns:
        BackendAuthStatus with availability info
    """
    try:
        cli_path = ensure_codex_available()
        
        # Check for auth file
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        auth_file = codex_home / "auth.json"
        
        if not auth_file.exists():
            return BackendAuthStatus(
                backend="codex",
                available=False,
                cli_path=str(cli_path),
                error="Not authenticated. Run `codex login` first.",
            )
        
        # Try to extract plan info if available
        plan = None
        email = None
        try:
            import json
            with open(auth_file) as f:
                auth_data = json.load(f)
            
            # Check for tokens (ChatGPT OAuth mode)
            tokens = auth_data.get("tokens", {})
            if tokens.get("access_token"):
                # Extract plan from id_token claims
                id_token = tokens.get("id_token", {})
                if isinstance(id_token, dict):
                    plan = id_token.get("plan_type")
                    email = id_token.get("email")
                # Also check top-level id_token_claims
                if not plan:
                    claims = auth_data.get("id_token_claims", {})
                    plan = claims.get("plan_type")
                    email = email or claims.get("email")
            # Check for API key mode
            elif auth_data.get("openai_api_key"):
                plan = "API Key"
        except Exception:
            pass  # Plan extraction is best-effort
        
        return BackendAuthStatus(
            backend="codex",
            available=True,
            cli_path=str(cli_path),
            plan=plan,
            email=email,
        )
        
    except CodexUnavailableError as e:
        return BackendAuthStatus(
            backend="codex",
            available=False,
            error=str(e),
        )


def check_gemini_auth() -> BackendAuthStatus:
    """Check if Gemini CLI is installed and authenticated.

    Checks:
    1. CLI binary exists (ensure_gemini_available)
    2. Credentials exist (~/.gemini/oauth_creds.json or similar)
    3. Extracts tier and email from Gemini account files

    Returns:
        BackendAuthStatus with availability info
    """
    try:
        cli_path = ensure_gemini_available()

        # Check for Gemini auth - multiple possible locations
        home = Path.home()
        gemini_home = home / ".gemini"
        oauth_creds_path = gemini_home / "oauth_creds.json"
        possible_auth_paths = [
            oauth_creds_path,  # Primary auth location
            gemini_home / "credentials.json",
            home / ".config" / "gemini" / "credentials",
            home / ".config" / "gemini-cli" / "credentials.json",
        ]

        # Also check environment variable
        has_api_key = bool(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"))

        auth_found = has_api_key or any(p.exists() for p in possible_auth_paths)

        if not auth_found:
            return BackendAuthStatus(
                backend="gemini",
                available=False,
                cli_path=str(cli_path),
                error="Not authenticated. Run `gemini` to start interactive login, or set GEMINI_API_KEY.",
            )

        # Try to extract tier and email info
        plan = None
        email = None

        if oauth_creds_path.exists() and not has_api_key:
            try:
                import json

                # Try to get email from google_accounts.json
                accounts_path = gemini_home / "google_accounts.json"
                if accounts_path.exists():
                    with open(accounts_path) as f:
                        accounts_data = json.load(f)
                    if isinstance(accounts_data, dict):
                        email = accounts_data.get("email")
                        # Also check for accounts list
                        accounts_list = accounts_data.get("accounts", [])
                        if accounts_list and isinstance(accounts_list, list):
                            first_account = accounts_list[0]
                            if isinstance(first_account, dict):
                                email = email or first_account.get("email")

                # Try to get tier from gemini_usage module (if available)
                try:
                    from shinka.tools.gemini_usage import load_auth_info, load_code_assist
                    auth = load_auth_info(gemini_home)
                    code_assist = load_code_assist(auth)
                    current_tier = code_assist.get("currentTier", {})
                    if isinstance(current_tier, dict):
                        # Use display name from API (e.g., "Gemini Code Assist")
                        plan = current_tier.get("name") or current_tier.get("id")
                except Exception:
                    # Fallback to generic plan name if API call fails
                    plan = "OAuth"
            except Exception:
                pass  # Tier/email extraction is best-effort
        elif has_api_key:
            plan = "API Key"

        return BackendAuthStatus(
            backend="gemini",
            available=True,
            cli_path=str(cli_path),
            plan=plan or "Subscription",
            email=email,
        )

    except GeminiUnavailableError as e:
        return BackendAuthStatus(
            backend="gemini",
            available=False,
            error=str(e),
        )


def check_claude_auth() -> BackendAuthStatus:
    """Check if Claude CLI is installed and authenticated.
    
    Checks:
    1. CLI binary exists (ensure_claude_available)
    2. Auth file exists (~/.claude.json or similar)
    
    Returns:
        BackendAuthStatus with availability info
    """
    try:
        cli_path = ensure_claude_available()
        
        # Check for Claude auth - multiple possible locations
        home = Path.home()
        possible_auth_paths = [
            home / ".claude.json",  # Primary auth location for claude-code
            home / ".claude" / "auth.json",
            home / ".claude" / "credentials.json",
            home / ".config" / "claude" / "credentials.json",
            home / ".claude-code" / "auth.json",
        ]
        
        # Also check environment variable
        has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        
        auth_found = has_api_key or any(p.exists() for p in possible_auth_paths)
        
        if not auth_found:
            return BackendAuthStatus(
                backend="claude",
                available=False,
                cli_path=str(cli_path),
                error="Not authenticated. Run `claude` to log in, or set ANTHROPIC_API_KEY.",
            )
        
        return BackendAuthStatus(
            backend="claude",
            available=True,
            cli_path=str(cli_path),
            plan="Subscription" if not has_api_key else "API Key",
        )
        
    except ClaudeUnavailableError as e:
        return BackendAuthStatus(
            backend="claude",
            available=False,
            error=str(e),
        )


def check_shinka_auth() -> BackendAuthStatus:
    """Check if ShinkaAgent has API keys configured.
    
    ShinkaAgent is available if at least one LLM provider API key is set:
    - OPENAI_API_KEY
    - ANTHROPIC_API_KEY
    - DEEPSEEK_API_KEY
    - GOOGLE_API_KEY
    - AWS_ACCESS_KEY_ID (for Bedrock)
    
    Returns:
        BackendAuthStatus with availability info
    """
    try:
        ensure_shinka_available()
        
        # Determine which provider(s) are available
        providers = []
        if os.environ.get("OPENAI_API_KEY"):
            providers.append("OpenAI")
        if os.environ.get("ANTHROPIC_API_KEY"):
            providers.append("Anthropic")
        if os.environ.get("DEEPSEEK_API_KEY"):
            providers.append("DeepSeek")
        if os.environ.get("GOOGLE_API_KEY"):
            providers.append("Google")
        if os.environ.get("AWS_ACCESS_KEY_ID"):
            providers.append("AWS/Bedrock")
        
        return BackendAuthStatus(
            backend="shinka",
            available=True,
            plan=", ".join(providers) if providers else "Unknown",
        )
        
    except ShinkaUnavailableError as e:
        return BackendAuthStatus(
            backend="shinka",
            available=False,
            error=str(e),
        )


def check_backend_auth(backend: str) -> BackendAuthStatus:
    """Check auth status for a specific backend.
    
    Args:
        backend: One of 'codex', 'gemini', 'claude', 'shinka'
        
    Returns:
        BackendAuthStatus for the requested backend
        
    Raises:
        ValueError: If backend is not recognized
    """
    checkers = {
        "codex": check_codex_auth,
        "gemini": check_gemini_auth,
        "claude": check_claude_auth,
        "shinka": check_shinka_auth,
    }
    
    if backend not in checkers:
        raise ValueError(f"Unknown backend: {backend}. Must be one of: {list(checkers.keys())}")
    
    return checkers[backend]()


def get_all_backend_statuses() -> List[BackendAuthStatus]:
    """Get auth status for all backends.
    
    Returns:
        List of BackendAuthStatus, one per backend
    """
    return [
        check_codex_auth(),
        check_gemini_auth(),
        check_claude_auth(),
        check_shinka_auth(),
    ]


def get_authenticated_backends() -> List[str]:
    """Return list of backend names that are currently authenticated.
    
    This is the main entry point for BackendBandit to determine which
    backends to include in the selection pool.
    
    Returns:
        List of backend names (e.g., ['codex', 'shinka'])
    """
    available = []
    for status in get_all_backend_statuses():
        if status.available:
            available.append(status.backend)
    return available


def get_authenticated_backends_summary() -> dict:
    """Return detailed summary of all backend auth statuses.
    
    Useful for WebUI display and debugging.
    
    Returns:
        Dict with 'available', 'unavailable', and 'details' keys
    """
    statuses = get_all_backend_statuses()
    
    available = [s.backend for s in statuses if s.available]
    unavailable = [s.backend for s in statuses if not s.available]
    
    details = {}
    for s in statuses:
        details[s.backend] = {
            "available": s.available,
            "cli_path": s.cli_path,
            "plan": s.plan,
            "email": s.email,
            "error": s.error,
        }
    
    return {
        "available": available,
        "unavailable": unavailable,
        "details": details,
    }
