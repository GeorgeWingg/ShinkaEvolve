"""
Unified API Key/Credential Store for Shinka.

This module provides a secure and consistent way to manage API keys for
various backends (Codex, Gemini, Claude, ShinkaAgent). It prioritizes
credentials from a dedicated JSON store but falls back to environment variables
and legacy CLI auth files.

Security:
- Credentials are encrypted at rest using Fernet symmetric encryption
- The encryption key is stored in the OS keyring (macOS Keychain, Windows
  Credential Manager, Linux Secret Service)
- Existing plaintext credentials are auto-migrated on first access
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple, List

from dotenv import load_dotenv

# Define where credentials are stored
CREDENTIALS_DIR = Path.home() / ".shinka"
CREDENTIALS_FILE = CREDENTIALS_DIR / "credentials.json"

# Keyring service/key names for encryption key storage
KEYRING_SERVICE = "shinka"
KEYRING_KEY_NAME = "credentials_encryption_key"

# Providers supported by the unified system
# Note: "github" is a non-LLM credential used for Jules repo sync.
PROVIDERS = [
    "codex",
    "gemini",
    "claude",
    "shinka",
    "jules",
    "github",
    "deepseek",
    "openrouter",
    "azure",
    "nanobanana",
]

# Map provider names to their environment variable equivalents
ENV_VAR_MAP = {
    "codex": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "shinka": "SHINKA_API_KEY",  # Or specific shinka vars
    "jules": "JULES_API_KEY",
    "github": "GITHUB_TOKEN",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "nanobanana": "NANOBANANA_GEMINI_API_KEY",
}

# Additional environment variables for extensions/tools (not primary providers)
EXTENSION_ENV_VARS = {
    "nanobanana_model": "NANOBANANA_MODEL",
}

# Track if we've already tried migration this session
_migration_attempted = False


def _get_or_create_encryption_key() -> bytes:
    """
    Get the encryption key from OS keyring, creating one if it doesn't exist.

    Returns:
        The Fernet encryption key as bytes.

    Raises:
        RuntimeError: If keyring is not available or fails.
    """
    try:
        import keyring
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise RuntimeError(
            f"Required security dependencies not installed: {e}. "
            "Run: pip install keyring cryptography"
        )

    # Try to get existing key from keyring
    try:
        existing_key = keyring.get_password(KEYRING_SERVICE, KEYRING_KEY_NAME)
        if existing_key:
            return existing_key.encode()
    except Exception as e:
        # Keyring might not be available (headless Linux, etc.)
        print(f"[credentials] Warning: Could not access keyring: {e}", file=sys.stderr)
        print("[credentials] Falling back to unencrypted storage", file=sys.stderr)
        raise RuntimeError(f"Keyring not available: {e}")

    # Generate new key and store it
    new_key = Fernet.generate_key()
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_KEY_NAME, new_key.decode())
        print("[credentials] Generated new encryption key in OS keyring")
    except Exception as e:
        print(f"[credentials] Warning: Could not store key in keyring: {e}", file=sys.stderr)
        raise RuntimeError(f"Cannot store encryption key: {e}")

    return new_key


def _encrypt_data(data: str) -> bytes:
    """Encrypt a string using Fernet encryption."""
    from cryptography.fernet import Fernet
    key = _get_or_create_encryption_key()
    f = Fernet(key)
    return f.encrypt(data.encode())


def _decrypt_data(encrypted: bytes) -> str:
    """Decrypt Fernet-encrypted bytes to string."""
    from cryptography.fernet import Fernet
    key = _get_or_create_encryption_key()
    f = Fernet(key)
    return f.decrypt(encrypted).decode()


def _is_encrypted(data: bytes) -> bool:
    """Check if data appears to be Fernet-encrypted (starts with 'gAAAAA')."""
    try:
        # Fernet tokens are base64-encoded and start with version byte
        # When base64-decoded, first byte is 0x80 (version)
        # The base64 representation starts with 'gAAAAA'
        return data.startswith(b'gAAAAA')
    except Exception:
        return False


def _migrate_plaintext_if_needed() -> None:
    """
    One-time migration from plaintext to encrypted credentials.
    Called automatically on first load.
    """
    global _migration_attempted
    if _migration_attempted:
        return
    _migration_attempted = True

    if not CREDENTIALS_FILE.exists():
        return

    try:
        # Try to read as plaintext JSON
        content = CREDENTIALS_FILE.read_bytes()

        # Check if already encrypted
        if _is_encrypted(content):
            return

        # Try to parse as JSON (plaintext)
        creds = json.loads(content.decode("utf-8"))

        # If we got here, it's plaintext JSON - encrypt it
        if creds:  # Only migrate if there's actually data
            save_credentials_store(creds)
            print("[credentials] Migrated plaintext credentials to encrypted format")
    except json.JSONDecodeError:
        # Not valid JSON - might be corrupted or already encrypted with different format
        pass
    except UnicodeDecodeError:
        # Binary data - probably already encrypted
        pass
    except RuntimeError:
        # Keyring not available - leave as plaintext
        pass
    except Exception as e:
        print(f"[credentials] Migration check failed: {e}", file=sys.stderr)


def _ensure_store_exists():
    """Ensure the credential store directory exists."""
    if not CREDENTIALS_DIR.exists():
        CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
        # Set restrictive permissions (user read/write only)
        os.chmod(CREDENTIALS_DIR, 0o700)


def load_credentials_store() -> Dict[str, str]:
    """
    Load and decrypt credentials from the JSON store.

    Returns:
        Dictionary mapping provider names to API keys.
    """
    # Try migration on first load
    _migrate_plaintext_if_needed()

    if not CREDENTIALS_FILE.exists():
        return {}

    try:
        content = CREDENTIALS_FILE.read_bytes()

        # Check if encrypted
        if _is_encrypted(content):
            try:
                decrypted = _decrypt_data(content)
                return json.loads(decrypted)
            except RuntimeError:
                # Keyring not available - can't decrypt
                print("[credentials] Cannot decrypt: keyring not available", file=sys.stderr)
                return {}
            except Exception as e:
                print(f"[credentials] Decryption failed: {e}", file=sys.stderr)
                return {}
        else:
            # Plaintext JSON (legacy or keyring unavailable)
            return json.loads(content.decode("utf-8"))
    except Exception as e:
        print(f"[credentials] Failed to load credentials: {e}", file=sys.stderr)
        return {}


def save_credentials_store(creds: Dict[str, str]) -> None:
    """
    Encrypt and save credentials to the JSON store.

    Args:
        creds: Dictionary mapping provider names to API keys.
    """
    _ensure_store_exists()

    try:
        # Try to encrypt
        json_data = json.dumps(creds, indent=2)
        encrypted = _encrypt_data(json_data)
        CREDENTIALS_FILE.write_bytes(encrypted)
    except RuntimeError:
        # Keyring not available - fall back to plaintext with warning
        print("[credentials] Warning: Keyring not available, storing unencrypted", file=sys.stderr)
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


def is_encryption_available() -> bool:
    """
    Check if credential encryption is available on this system.

    Returns:
        True if keyring is available and working, False otherwise.
    """
    try:
        _get_or_create_encryption_key()
        return True
    except (RuntimeError, ImportError):
        return False


# --- Custom Provider Storage ---

def get_custom_providers() -> Dict[str, dict]:
    """
    Get all custom provider configurations.

    Custom providers are stored under the "custom_providers" key in the
    credential store, as a dict of provider_id -> config.

    Returns:
        Dict mapping provider_id to config dict with keys:
        - name: Display name
        - env_var: Environment variable name for API key
        - base_url: Base URL for OpenAI-compatible API
        - models: List of model names available
        - placeholder: Optional placeholder text for UI
    """
    store = load_credentials_store()
    return store.get("custom_providers", {})


def save_custom_provider(provider_id: str, config: dict) -> None:
    """
    Save a custom provider configuration.

    Args:
        provider_id: Unique identifier (e.g., "lmstudio", "ollama")
        config: Dict with keys: name, env_var, base_url, models, placeholder
    """
    store = load_credentials_store()
    if "custom_providers" not in store:
        store["custom_providers"] = {}
    store["custom_providers"][provider_id] = config
    save_credentials_store(store)


def remove_custom_provider(provider_id: str) -> bool:
    """
    Remove a custom provider configuration.

    Args:
        provider_id: The provider ID to remove

    Returns:
        True if removed, False if not found
    """
    store = load_credentials_store()
    custom = store.get("custom_providers", {})
    if provider_id in custom:
        del custom[provider_id]
        store["custom_providers"] = custom
        save_credentials_store(store)
        return True
    return False


def get_custom_provider(provider_id: str) -> Optional[dict]:
    """
    Get a specific custom provider configuration.

    Args:
        provider_id: The provider ID

    Returns:
        Config dict or None if not found
    """
    custom = get_custom_providers()
    return custom.get(provider_id)
