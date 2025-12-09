"""Credential checker for LLM provider API keys."""

import os
from typing import Dict, List, Optional


class CredentialChecker:
    """Check availability of LLM provider credentials."""

    PROVIDER_ENV_VARS = {
        "openai": ["OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"],
        "anthropic": ["ANTHROPIC_API_KEY"],
        "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        "deepseek": ["DEEPSEEK_API_KEY"],
        "openrouter": ["OPENROUTER_API_KEY"],
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
        "deepseek": "DeepSeek",
        "openrouter": "OpenRouter",
    }

    def check_provider(self, provider: str) -> Dict:
        """Check if a specific provider is configured.

        Args:
            provider: Provider ID (e.g., 'openai', 'anthropic')

        Returns:
            Dict with 'available', 'models_accessible', 'error', 'env_var_used'
        """
        env_vars = self.PROVIDER_ENV_VARS.get(provider, [])
        env_var_used = None

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
        return list(providers)

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
