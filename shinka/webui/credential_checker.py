"""Credential checker for LLM provider API keys."""

import os
from typing import Dict, List, Optional


class CredentialChecker:
    """Check availability of LLM provider credentials."""

    PROVIDER_ENV_VARS = {
        "openai": ["OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"],
        "anthropic": ["ANTHROPIC_API_KEY"],
        "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        "jules": ["JULES_API_KEY"],
        "github": ["GITHUB_TOKEN"],
        "deepseek": ["DEEPSEEK_API_KEY"],
        "openrouter": ["OPENROUTER_API_KEY"],
    }

    # Map frontend/provider IDs to unified credential-store keys.
    STORE_PROVIDER_MAP = {
        "openai": "codex",
        "anthropic": "claude",
        "google": "gemini",
        "jules": "jules",
        "github": "github",
        "deepseek": "deepseek",
        "openrouter": "openrouter",
        "azure": "azure",
    }

    PROVIDER_MODELS = {
        "openai": [
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
            "gpt-4o",
            "gpt-4o-mini",
            "o1-preview",
            "o1-mini",
            "o3-mini",
        ],
        "anthropic": [
            "claude-opus-4-20250514",
            "claude-sonnet-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ],
        "google": [
            "gemini-pro",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-2.0-flash",
        ],
        "jules": [],  # Jules is a cloud agent, not a model
        "github": [],  # GitHub Token is for repo access, not models
        "deepseek": [
            "deepseek-chat",
            "deepseek-coder",
            "deepseek-reasoner",
        ],
        "openrouter": [],  # Dynamic based on what's available
    }

    PROVIDER_DISPLAY_NAMES = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "google": "Google (Gemini)",
        "jules": "Jules (Cloud Agent)",
        "github": "GitHub",
        "deepseek": "DeepSeek",
        "openrouter": "OpenRouter",
    }

    def check_provider(self, provider: str) -> Dict:
        """Check if a specific provider is configured.

        Args:
            provider: Provider ID (e.g., 'openai', 'anthropic', 'custom_lmstudio')

        Returns:
            Dict with 'available', 'models_accessible', 'error', 'env_var_used'
        """
        provider = provider.lower()

        # Handle custom providers
        if provider.startswith("custom_"):
            return self._check_custom_provider(provider)

        env_vars = self.PROVIDER_ENV_VARS.get(provider, [])
        env_var_used = None

        # 1) Check unified credential store first (survives server restarts)
        store_key = self.STORE_PROVIDER_MAP.get(provider, provider)
        try:
            from shinka.tools.credentials import load_credentials_store

            store = load_credentials_store()
            if store_key in store and store[store_key]:
                env_var_used = "store"
        except Exception:
            # If the store isn't readable (missing crypto/keyring), fall back to env vars.
            env_var_used = None

        # 2) Fall back to environment variables
        if env_var_used is None:
            for var in env_vars:
                if os.environ.get(var):
                    env_var_used = var
                    break

        available = env_var_used is not None

        return {
            "available": available,
            "models_accessible": self.PROVIDER_MODELS.get(provider, [])
            if available
            else [],
            "error": None
            if available
            else f"No API key found. Set one of: {', '.join(env_vars)}",
            "env_var_used": env_var_used,
            "display_name": self.PROVIDER_DISPLAY_NAMES.get(provider, provider),
        }

    def check_all(self) -> Dict[str, Dict]:
        """Check all providers.

        Returns:
            Dict mapping provider ID to check result
        """
        return {provider: self.check_provider(provider) for provider in self.PROVIDER_ENV_VARS}

    def get_available_providers(self) -> List[str]:
        """Get list of providers with configured credentials.

        Returns:
            List of provider IDs that have valid credentials
        """
        return [provider for provider, info in self.check_all().items() if info["available"]]

    def get_available_models(self) -> List[str]:
        """Get list of all models with configured credentials.

        Returns:
            List of model names that can be used
        """
        models = []
        for provider, info in self.check_all().items():
            if info["available"]:
                models.extend(info["models_accessible"])
        return models

    def get_required_providers_for_models(self, models: List[str]) -> List[str]:
        """Determine which providers are needed for a list of models.

        Args:
            models: List of model names

        Returns:
            List of provider IDs required
        """
        providers = set()
        for model in models:
            model_lower = model.lower()
            if any(x in model_lower for x in ["gpt", "o1", "o3", "o4"]):
                providers.add("openai")
            elif "claude" in model_lower:
                providers.add("anthropic")
            elif "gemini" in model_lower:
                providers.add("google")
            elif "deepseek" in model_lower:
                providers.add("deepseek")
            elif "azure" in model_lower:
                providers.add("openai")  # Azure uses OpenAI-compatible API
            elif "openrouter/" in model_lower:
                providers.add("openrouter")
            elif model_lower.startswith("custom/"):
                # Extract custom provider ID: custom/<provider_id>/<model>
                parts = model_lower.split("/")
                if len(parts) >= 2:
                    providers.add(f"custom_{parts[1]}")
        return list(providers)

    def _check_custom_provider(self, provider: str) -> Dict:
        """Check if a custom provider is configured.

        Args:
            provider: Provider ID in format 'custom_<provider_id>'

        Returns:
            Dict with availability info
        """
        from shinka.tools.credentials import get_custom_providers

        provider_id = provider.replace("custom_", "", 1)
        providers = get_custom_providers()

        if provider_id not in providers:
            return {
                "available": False,
                "models_accessible": [],
                "error": f"Custom provider '{provider_id}' not configured",
                "env_var_used": None,
                "display_name": provider_id.replace("_", " ").title(),
            }

        config = providers[provider_id]
        base_url = config.get("base_url", "")
        env_var = config.get("env_var", f"CUSTOM_{provider_id.upper()}_API_KEY")
        models = config.get("models", [])

        # Check if base_url is local (no key needed)
        is_local = "localhost" in base_url or "127.0.0.1" in base_url

        # Check for API key
        has_key = bool(os.environ.get(env_var))

        # Also check credential store
        if not has_key:
            try:
                from shinka.tools.credentials import load_credentials_store
                store = load_credentials_store()
                # Custom provider keys might be stored directly
                if provider_id in store:
                    has_key = True
            except Exception:
                pass

        available = is_local or has_key

        return {
            "available": available,
            "models_accessible": models if available else [],
            "error": None if available else f"API key not set ({env_var})",
            "env_var_used": env_var if has_key else ("local" if is_local else None),
            "display_name": config.get("name", provider_id.replace("_", " ").title()),
            "base_url": base_url,
            "is_local": is_local,
        }

    def validate_models(self, models: List[str]) -> Dict:
        """Validate that credentials exist for requested models.

        Args:
            models: List of model names to validate

        Returns:
            Dict with 'valid', 'missing_providers', 'available_providers'
        """
        required = self.get_required_providers_for_models(models)
        available = self.get_available_providers()
        missing = [p for p in required if p not in available]

        return {
            "valid": len(missing) == 0,
            "missing_providers": missing,
            "available_providers": available,
            "required_providers": required,
        }
