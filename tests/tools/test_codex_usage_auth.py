
import pytest
import json
from unittest.mock import MagicMock, patch
from pathlib import Path
from shinka.tools.codex_usage import load_auth_info, CodexAuthInfo, CodexUsageError

class TestCodexUsageAuth:
    @pytest.fixture
    def mock_codex_home(self, tmp_path):
        home = tmp_path / ".codex"
        home.mkdir()
        return home

    def test_load_auth_info_chatgpt_mode(self, mock_codex_home):
        """Test auth info loading when tokens are present (ChatGPT mode)."""
        auth_file = mock_codex_home / "auth.json"
        auth_data = {
            "tokens": {
                "access_token": "test-access-token",
                "account_id": "test-account-id",
                "id_token_claims": {
                    "email": "user@example.com",
                    "plan_type": "pro"
                }
            }
        }
        auth_file.write_text(json.dumps(auth_data), encoding="utf-8")

        auth = load_auth_info(mock_codex_home)
        
        assert auth.mode == "chatgpt"
        assert auth.access_token == "test-access-token"
        assert auth.api_key is None
        assert auth.account_id == "test-account-id"
        assert auth.email == "user@example.com"
        assert auth.plan_type == "pro"

    def test_load_auth_info_api_key_mode(self, mock_codex_home):
        """Test auth info loading when only openai_api_key is present (API Key mode)."""
        auth_file = mock_codex_home / "auth.json"
        auth_data = {
            "openai_api_key": "sk-test-api-key",
            # tokens might be missing or empty/null
            "tokens": None
        }
        auth_file.write_text(json.dumps(auth_data), encoding="utf-8")

        auth = load_auth_info(mock_codex_home)
        
        assert auth.mode == "api_key"
        assert auth.access_token is None
        assert auth.api_key == "sk-test-api-key"
        assert auth.account_id is None
        # Email/Plan not available in API key mode usually
        assert auth.email is None 
        assert auth.plan_type is None

    def test_load_auth_info_priority(self, mock_codex_home):
        """Test that tokens take priority over API key if both are present."""
        auth_file = mock_codex_home / "auth.json"
        auth_data = {
            "openai_api_key": "sk-test-api-key",
            "tokens": {
                "access_token": "test-access-token"
            }
        }
        auth_file.write_text(json.dumps(auth_data), encoding="utf-8")

        auth = load_auth_info(mock_codex_home)
        
        assert auth.mode == "chatgpt"
        assert auth.access_token == "test-access-token"

    def test_load_auth_info_missing_file(self, mock_codex_home):
        """Test error when auth file is missing."""
        with pytest.raises(CodexUsageError, match="not found"):
            load_auth_info(mock_codex_home)

    def test_load_auth_info_invalid_json(self, mock_codex_home):
        """Test error when auth file is invalid JSON."""
        auth_file = mock_codex_home / "auth.json"
        auth_file.write_text("{invalid json", encoding="utf-8")
        
        with pytest.raises(CodexUsageError, match="Failed to parse"):
            load_auth_info(mock_codex_home)

    def test_load_auth_info_no_credentials(self, mock_codex_home):
        """Test error when JSON is valid but has no tokens or key."""
        auth_file = mock_codex_home / "auth.json"
        auth_file.write_text("{}", encoding="utf-8")
        
        with pytest.raises(CodexUsageError, match="No usable tokens"):
            load_auth_info(mock_codex_home)

if __name__ == "__main__":
    pytest.main([__file__])
