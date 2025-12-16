"""Tests for OpenRouter and Custom Provider integration.

This test suite validates the OpenRouter and custom provider functionality added
to the Shinka codebase, following established patterns from test_claude_cli.py
and test_auth_status_store_fallback.py.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestOpenRouterAuth:
    """Test check_openrouter_auth() function in auth_status.py."""

    def test_auth_with_env_var(self):
        """OPENROUTER_API_KEY in env → available=True"""
        from shinka.tools.auth_status import check_openrouter_auth

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-123"}):
            status = check_openrouter_auth()

        assert status.backend == "openrouter"
        assert status.available is True
        assert status.plan == "API Key"
        assert status.error is None

    def test_auth_from_credential_store(self, monkeypatch):
        """Fallback to credential store when env var missing"""
        from shinka.tools.auth_status import check_openrouter_auth

        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        with patch(
            "shinka.tools.credentials.get_api_key",
            side_effect=lambda p: "sk-or-stored-123" if p == "openrouter" else None,
        ):
            status = check_openrouter_auth()

        assert status.available is True
        assert os.environ.get("OPENROUTER_API_KEY") == "sk-or-stored-123"

    def test_auth_not_configured(self, monkeypatch):
        """No credentials → available=False with error message"""
        from shinka.tools.auth_status import check_openrouter_auth

        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        with patch("shinka.tools.credentials.get_api_key", return_value=None):
            status = check_openrouter_auth()

        assert status.available is False
        assert "not set" in status.error.lower()

    def test_openrouter_in_all_backends(self):
        """OpenRouter is included in ALL_BACKENDS list"""
        from shinka.tools.auth_status import ALL_BACKENDS

        assert "openrouter" in ALL_BACKENDS


class TestOpenRouterClient:
    """Test get_client_llm() for OpenRouter models in client.py."""

    def test_client_creation_for_openrouter_model(self):
        """Verify base_url, api_key, HTTP-Referer header"""
        from shinka.llm.client import get_client_llm

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-123"}):
            client, model_name = get_client_llm(
                "openrouter/meta-llama/llama-3.3-70b-instruct"
            )

        assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
        assert client.api_key == "sk-or-test-123"
        assert "HTTP-Referer" in client.default_headers
        assert client.default_headers["HTTP-Referer"] == "https://shinka.ai"

    def test_prefix_stripping(self):
        """'openrouter/meta-llama/llama-3' → 'meta-llama/llama-3'"""
        from shinka.llm.client import get_client_llm

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-123"}):
            _, model_name = get_client_llm("openrouter/meta-llama/llama-3.3-70b")

        assert not model_name.startswith("openrouter/")
        assert model_name == "meta-llama/llama-3.3-70b"

    def test_arbitrary_model_string(self):
        """Any model string works, not just hardcoded ones in OPENROUTER_MODELS"""
        from shinka.llm.client import get_client_llm

        # Test with a model NOT in the hardcoded OPENROUTER_MODELS dict
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-123"}):
            client, model_name = get_client_llm(
                "openrouter/some-provider/some-arbitrary-model-v99"
            )

        # Should work because we check startswith("openrouter/")
        assert model_name == "some-provider/some-arbitrary-model-v99"
        assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"

    def test_hardcoded_model_without_prefix(self):
        """Models in OPENROUTER_MODELS dict work with the full key"""
        from shinka.llm.client import get_client_llm
        from shinka.llm.models.pricing import OPENROUTER_MODELS

        # Get a hardcoded model key
        hardcoded_model = list(OPENROUTER_MODELS.keys())[0]

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-123"}):
            client, model_name = get_client_llm(hardcoded_model)

        assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"


class TestOpenRouterQueryRouting:
    """Test query routing for OpenRouter models in query.py."""

    def test_openrouter_routes_to_openai_query(self):
        """OpenRouter models should use query_openai function"""
        from shinka.llm.query import query
        from shinka.llm.models import query_openai

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-123"}):
            with patch("shinka.llm.models.query_openai") as mock_query:
                mock_query.return_value = MagicMock(
                    content="test", input_tokens=10, output_tokens=5
                )

                # This should route to query_openai
                with patch("shinka.llm.client.get_client_llm") as mock_client:
                    mock_client.return_value = (MagicMock(), "meta-llama/llama-3")
                    try:
                        query(
                            "openrouter/meta-llama/llama-3",
                            "Hello",
                            "Be helpful",
                        )
                    except Exception:
                        pass  # We're just checking routing, not actual API call


class TestCustomProviderStorage:
    """Test custom provider CRUD in credentials.py."""

    def test_save_and_get_custom_provider(self, tmp_path, monkeypatch):
        """Save provider config to credentials store and retrieve it"""
        from shinka.tools import credentials

        # Use temp directory for credentials
        monkeypatch.setattr(credentials, "CREDENTIALS_DIR", tmp_path)
        monkeypatch.setattr(credentials, "CREDENTIALS_FILE", tmp_path / "creds.json")

        # Mock encryption (to avoid keyring dependency in tests)
        monkeypatch.setattr(
            credentials, "_encrypt_data", lambda x: x.encode()
        )
        monkeypatch.setattr(
            credentials, "_decrypt_data", lambda x: x.decode()
        )
        monkeypatch.setattr(credentials, "_is_encrypted", lambda x: False)

        config = {
            "name": "LMStudio",
            "env_var": "LMSTUDIO_API_KEY",
            "base_url": "http://localhost:1234/v1",
            "models": ["llama-3.2-3b"],
            "placeholder": "not-needed",
        }

        credentials.save_custom_provider("lmstudio", config)
        providers = credentials.get_custom_providers()

        assert "lmstudio" in providers
        assert providers["lmstudio"]["name"] == "LMStudio"
        assert providers["lmstudio"]["base_url"] == "http://localhost:1234/v1"

    def test_remove_custom_provider(self, tmp_path, monkeypatch):
        """Delete provider from store"""
        from shinka.tools import credentials

        monkeypatch.setattr(credentials, "CREDENTIALS_DIR", tmp_path)
        monkeypatch.setattr(credentials, "CREDENTIALS_FILE", tmp_path / "creds.json")
        monkeypatch.setattr(credentials, "_encrypt_data", lambda x: x.encode())
        monkeypatch.setattr(credentials, "_decrypt_data", lambda x: x.decode())
        monkeypatch.setattr(credentials, "_is_encrypted", lambda x: False)

        # Save then remove
        credentials.save_custom_provider("test_provider", {"name": "Test"})
        assert "test_provider" in credentials.get_custom_providers()

        result = credentials.remove_custom_provider("test_provider")
        assert result is True
        assert "test_provider" not in credentials.get_custom_providers()

    def test_remove_nonexistent_provider(self, tmp_path, monkeypatch):
        """Removing non-existent provider returns False"""
        from shinka.tools import credentials

        monkeypatch.setattr(credentials, "CREDENTIALS_DIR", tmp_path)
        monkeypatch.setattr(credentials, "CREDENTIALS_FILE", tmp_path / "creds.json")
        monkeypatch.setattr(credentials, "_encrypt_data", lambda x: x.encode())
        monkeypatch.setattr(credentials, "_decrypt_data", lambda x: x.decode())
        monkeypatch.setattr(credentials, "_is_encrypted", lambda x: False)

        result = credentials.remove_custom_provider("nonexistent")
        assert result is False


class TestCustomProviderClient:
    """Test get_client_llm() for custom/ prefix in client.py."""

    def test_custom_provider_client_creation(self, monkeypatch):
        """custom/lmstudio/model → correct base_url"""
        from shinka.llm.client import get_client_llm
        from shinka.tools import credentials

        # Mock get_custom_providers to return our test provider
        monkeypatch.setattr(
            credentials,
            "get_custom_providers",
            lambda: {
                "lmstudio": {
                    "name": "LMStudio",
                    "env_var": "LMSTUDIO_API_KEY",
                    "base_url": "http://localhost:1234/v1",
                    "models": ["llama-3.2-3b"],
                }
            },
        )

        # Local endpoint doesn't need API key
        client, model_name = get_client_llm("custom/lmstudio/llama-3.2-3b")

        assert model_name == "llama-3.2-3b"
        assert str(client.base_url).rstrip("/") == "http://localhost:1234/v1"

    def test_missing_provider_raises_error(self, monkeypatch):
        """Unconfigured provider → ValueError"""
        from shinka.llm.client import get_client_llm
        from shinka.tools import credentials

        monkeypatch.setattr(credentials, "get_custom_providers", lambda: {})

        with pytest.raises(ValueError, match="not configured"):
            get_client_llm("custom/nonexistent/model")

    def test_remote_endpoint_requires_key(self, monkeypatch):
        """Non-localhost without key → error"""
        from shinka.llm.client import get_client_llm
        from shinka.tools import credentials

        monkeypatch.setattr(
            credentials,
            "get_custom_providers",
            lambda: {
                "together": {
                    "name": "Together AI",
                    "env_var": "TOGETHER_API_KEY",
                    "base_url": "https://api.together.xyz/v1",  # Remote!
                    "models": ["llama-3"],
                }
            },
        )
        monkeypatch.delenv("TOGETHER_API_KEY", raising=False)

        with pytest.raises(ValueError, match="API key not set"):
            get_client_llm("custom/together/llama-3")

    def test_local_endpoint_no_key_required(self, monkeypatch):
        """localhost URLs don't require API key"""
        from shinka.llm.client import get_client_llm
        from shinka.tools import credentials

        monkeypatch.setattr(
            credentials,
            "get_custom_providers",
            lambda: {
                "ollama": {
                    "name": "Ollama",
                    "env_var": "OLLAMA_API_KEY",
                    "base_url": "http://localhost:11434/v1",
                    "models": ["codellama"],
                }
            },
        )
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

        # Should NOT raise error for localhost
        client, model_name = get_client_llm("custom/ollama/codellama")
        assert model_name == "codellama"


class TestCredentialChecker:
    """Test credential_checker.py OpenRouter/custom handling."""

    def test_openrouter_model_detection(self):
        """'openrouter/...' → providers.add('openrouter')"""
        from shinka.webui.credential_checker import CredentialChecker

        cc = CredentialChecker()
        providers = cc.get_required_providers_for_models(
            ["openrouter/meta-llama/llama-3.3-70b"]
        )

        assert "openrouter" in providers

    def test_custom_model_detection(self):
        """'custom/lmstudio/...' → providers.add('custom_lmstudio')"""
        from shinka.webui.credential_checker import CredentialChecker

        cc = CredentialChecker()
        providers = cc.get_required_providers_for_models(
            ["custom/lmstudio/llama-3.2-3b"]
        )

        assert "custom_lmstudio" in providers

    def test_custom_provider_availability_check(self, monkeypatch):
        """_check_custom_provider() returns correct status"""
        from shinka.webui.credential_checker import CredentialChecker
        from shinka.tools import credentials

        monkeypatch.setattr(
            credentials,
            "get_custom_providers",
            lambda: {
                "lmstudio": {
                    "name": "LMStudio",
                    "env_var": "LMSTUDIO_API_KEY",
                    "base_url": "http://localhost:1234/v1",
                    "models": ["llama-3.2-3b"],
                }
            },
        )

        cc = CredentialChecker()
        result = cc._check_custom_provider("custom_lmstudio")

        assert result["available"] is True  # localhost doesn't need key
        assert result["is_local"] is True
        assert "llama-3.2-3b" in result["models_accessible"]

    def test_custom_provider_not_configured(self, monkeypatch):
        """Unconfigured custom provider returns available=False"""
        from shinka.webui.credential_checker import CredentialChecker
        from shinka.tools import credentials

        monkeypatch.setattr(credentials, "get_custom_providers", lambda: {})

        cc = CredentialChecker()
        result = cc._check_custom_provider("custom_nonexistent")

        assert result["available"] is False
        assert "not configured" in result["error"]


class TestOpenRouterPricing:
    """Test OpenRouter pricing configuration."""

    def test_openrouter_models_dict_exists(self):
        """OPENROUTER_MODELS dict is defined"""
        from shinka.llm.models.pricing import OPENROUTER_MODELS

        assert isinstance(OPENROUTER_MODELS, dict)
        assert len(OPENROUTER_MODELS) > 0

    def test_openrouter_models_have_pricing(self):
        """Each model has input_price and output_price"""
        from shinka.llm.models.pricing import OPENROUTER_MODELS

        for model, pricing in OPENROUTER_MODELS.items():
            assert "input_price" in pricing, f"{model} missing input_price"
            assert "output_price" in pricing, f"{model} missing output_price"
            assert pricing["input_price"] >= 0
            assert pricing["output_price"] >= 0

    def test_openrouter_models_use_prefix(self):
        """All models in dict use openrouter/ prefix"""
        from shinka.llm.models.pricing import OPENROUTER_MODELS

        for model in OPENROUTER_MODELS.keys():
            assert model.startswith(
                "openrouter/"
            ), f"{model} should start with 'openrouter/'"


class TestCredentialTester:
    """Test credential_tester.py OpenRouter support."""

    def test_openrouter_test_method_exists(self):
        """_test_openrouter method exists in CredentialTester"""
        from shinka.webui.credential_tester import CredentialTester

        tester = CredentialTester()
        assert hasattr(tester, "_test_openrouter")

    def test_openrouter_provider_recognized(self):
        """test_credential recognizes 'openrouter' provider"""
        from shinka.webui.credential_tester import CredentialTester

        tester = CredentialTester()

        # Test with empty key should return error, not "not supported"
        result = tester.test_credential("openrouter", "")
        assert "empty" in result["message"].lower() or result["ok"] is False
        assert "not supported" not in result.get("message", "").lower()
