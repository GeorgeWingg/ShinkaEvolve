"""
Unified API Key/Credential Store for Shinka.

This module provides a secure and consistent way to manage API keys for
various backends (Codex, Gemini, Claude, ShinkaAgent). It prioritizes
credentials from a dedicated JSON store but falls back to environment variables
and legacy CLI auth files.
"""

import json
import os
from pathlib import Path
from typing import Dict, Optional, Tuple, List

from dotenv import load_dotenv

# Define where credentials are stored
CREDENTIALS_DIR = Path.home() / ".shinka"
CREDENTIALS_FILE = CREDENTIALS_DIR / "credentials.json"

# Providers supported by the unified system
PROVIDERS = ["codex", "gemini", "claude", "shinka", "deepseek", "openrouter", "azure"]

# Map provider names to their environment variable equivalents
ENV_VAR_MAP = {
    "codex": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "shinka": "SHINKA_API_KEY",  # Or specific shinka vars
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
}


def _ensure_store_exists():
    """Ensure the credential store directory exists."""
    if not CREDENTIALS_DIR.exists():
        CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
        # Set restrictive permissions (user read/write only)
        os.chmod(CREDENTIALS_DIR, 0o700)


def load_credentials_store() -> Dict[str, str]:
    """Load credentials strictly from the JSON store."""
    if not CREDENTIALS_FILE.exists():
        return {}
    try:
        content = CREDENTIALS_FILE.read_text(encoding="utf-8")
        return json.loads(content)
    except Exception:
        return {}


def save_credentials_store(creds: Dict[str, str]) -> None:
    """Save credentials to the JSON store."""
    _ensure_store_exists()
    CREDENTIALS_FILE.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    # Set restrictive permissions on the file
    os.chmod(CREDENTIALS_FILE, 0o600)


def get_api_key(provider: str) -> Optional[str]:
    """
    Get the API key for a provider, checking sources in priority order:
    1. Unified credential store (~/.shinka/credentials.json)
    2. Environment variables (including loaded .env)
    3. CLI-specific auth files (legacy fallback)
    
    Args:
        provider: One of "codex", "gemini", "claude", "shinka"
        
    Returns:
        The API key string if found, else None.
    """
    provider = provider.lower()
    
    # 1. Check credential store
    store = load_credentials_store()
    if provider in store:
        return store[provider]
        
    # 2. Check environment variables
    # Ensure .env is loaded
    load_dotenv()
    env_var = ENV_VAR_MAP.get(provider)
    if env_var and os.environ.get(env_var):
        return os.environ.get(env_var)
        
    # 3. Check legacy CLI auth files (best effort)
    # Note: This is specific to how each CLI stores its auth.
    # Codex uses ~/.codex/auth.json
    if provider == "codex":
        try:
            codex_auth = Path.home() / ".codex" / "auth.json"
            if codex_auth.exists():
                data = json.loads(codex_auth.read_text())
                # Codex might store 'openai_api_key' or access tokens
                return data.get("openai_api_key")
        except Exception:
            pass
            
    # Gemini uses ~/.config/gemini/credentials usually, but format varies
    # Claude uses ~/.claude/auth.json or similar
    
    return None


def set_api_key(provider: str, key: str) -> None:
    """
    Save an API key to the unified credential store.
    
    Args:
        provider: One of "codex", "gemini", "claude", "shinka"
        key: The API key string
    """
    provider = provider.lower()
    store = load_credentials_store()
    store[provider] = key
    save_credentials_store(store)


def remove_api_key(provider: str) -> None:
    """
    Remove an API key from the unified credential store.
    Does NOT affect environment variables or legacy CLI files.
    
    Args:
        provider: One of "codex", "gemini", "claude", "shinka"
    """
    provider = provider.lower()
    store = load_credentials_store()
    if provider in store:
        del store[provider]
        save_credentials_store(store)


def list_configured_providers() -> List[str]:
    """Return a list of providers that have keys configured in the store."""
    store = load_credentials_store()
    return list(store.keys())


def validate_credential(provider: str, key: str) -> Tuple[bool, str]:
    """
    Validate an API key by making a minimal request to the provider.
    
    Args:
        provider: The provider to test
        key: The API key to test
        
    Returns:
        (is_valid, message)
    """
    # This is a placeholder for now. Real implementation would need 
    # to instantiate the specific client and make a test call (e.g. list models).
    # For TODO-109 completion, we implement the storage mechanism first.
    if not key or len(key.strip()) < 5:
        return False, "Key is too short or empty"
        
    return True, "Validation not yet implemented (stored successfully)"
