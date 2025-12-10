# Credential Management

This document explains how Shinka securely stores and manages API keys for various LLM providers.

## How API Keys Are Stored

Shinka uses a two-layer security approach for storing credentials:

1. **Encryption Key in OS Keyring** - A Fernet encryption key is stored in your operating system's secure credential storage:
   - **macOS**: Keychain
   - **Windows**: Credential Manager
   - **Linux**: Secret Service (GNOME Keyring, KWallet, or similar)

2. **Encrypted Credential File** - The actual API keys are encrypted using that key and stored in `~/.shinka/credentials.json`

This means even if someone gains access to the credential file, they cannot read the API keys without also having access to your OS keyring.

## Adding API Keys

You can add API keys in two ways:

### Via Web UI

1. Open the Shinka visualization UI (`shinka_visualize`)
2. Click on any provider card or the "API Keys" button
3. Enter your API key and click "Save"
4. The key is securely encrypted and stored on the backend

### Via Environment Variables

Set environment variables in your shell or `.env` file:

```bash
# OpenAI (for embeddings and Codex CLI)
export OPENAI_API_KEY="sk-..."

# Anthropic (for Claude CLI)
export ANTHROPIC_API_KEY="sk-ant-..."

# Google (for Gemini CLI)
export GEMINI_API_KEY="AIza..."

# DeepSeek
export DEEPSEEK_API_KEY="sk-..."

# OpenRouter
export OPENROUTER_API_KEY="sk-or-..."
```

Environment variables take precedence over stored credentials.

## Security Notes

- **No Browser Storage**: API keys are never stored in browser localStorage or sessionStorage. They are only stored on the backend.
- **Encrypted at Rest**: All credentials in `~/.shinka/credentials.json` are encrypted using Fernet (AES-128-CBC).
- **Restrictive Permissions**: The credential file has `0o600` permissions (readable only by owner).
- **Automatic Migration**: Existing plaintext credentials are automatically encrypted on first access after updating to the secure version.

## Troubleshooting

### "Keyring backend not available" error

On headless Linux systems or containers without a desktop environment, the keyring may not be available. You have two options:

**Option 1: Use environment variables instead**
```bash
export OPENAI_API_KEY="your-key"
export GEMINI_API_KEY="your-key"
# etc.
```

**Option 2: Install a file-based keyring backend**
```bash
pip install keyrings.alt
export PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring
```

> **Warning**: The PlaintextKeyring stores the encryption key in an unencrypted file. Only use this for development or in environments where you trust all users.

### Resetting credentials

To start fresh with credentials:

1. Delete the credential file:
   ```bash
   rm ~/.shinka/credentials.json
   ```

2. Optionally, remove the encryption key from your keyring:
   - **macOS**: Open Keychain Access, search for "shinka", delete the entry
   - **Linux**: Use `secret-tool` or your keyring manager
   - **Windows**: Use Credential Manager

3. Re-add your API keys via the Web UI or environment variables.

### Cannot decrypt existing credentials

If you see "Decryption failed" errors, the encryption key in your keyring may have been lost or changed. To resolve:

1. Delete the old credential file: `rm ~/.shinka/credentials.json`
2. Re-add your API keys

## Credential Priority

When retrieving an API key, Shinka checks these sources in order:

1. **Encrypted credential store** (`~/.shinka/credentials.json`)
2. **Environment variables** (including loaded `.env` file)
3. **Legacy CLI auth files** (e.g., `~/.codex/auth.json` for Codex)

The first source that has a key for the requested provider wins.

## Supported Providers

| Provider | Environment Variable | Description |
|----------|---------------------|-------------|
| OpenAI | `OPENAI_API_KEY` | GPT models, embeddings, Codex CLI |
| Anthropic | `ANTHROPIC_API_KEY` | Claude models, Claude CLI |
| Google | `GEMINI_API_KEY` | Gemini models, Gemini CLI |
| DeepSeek | `DEEPSEEK_API_KEY` | DeepSeek models |
| OpenRouter | `OPENROUTER_API_KEY` | Multi-provider access |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` | Azure-hosted OpenAI models |

## Programmatic Access

You can also access credentials programmatically:

```python
from shinka.tools.credentials import get_api_key, set_api_key, is_encryption_available

# Check if encryption is available
if is_encryption_available():
    print("Credentials will be encrypted")

# Get an API key (checks store, then env vars, then legacy files)
key = get_api_key("codex")  # Returns OpenAI key
key = get_api_key("gemini")  # Returns Gemini key

# Set an API key (encrypts and saves to store)
set_api_key("codex", "sk-...")
```
