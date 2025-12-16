"""Credential tester for LLM provider API keys."""

import os
import requests
import json
from typing import Dict, Optional

class CredentialTester:
    """Test validity of LLM provider credentials via live API calls."""

    def test_credential(self, provider: str, api_key: str) -> Dict:
        """Test a specific provider's API key.

        Args:
            provider: Provider ID (e.g., 'openai', 'anthropic')
            api_key: The API key to test

        Returns:
            Dict with 'ok', 'message'
        """
        provider = provider.lower()
        
        if not api_key:
            return {"ok": False, "message": "API key is empty"}

        if provider == "openai" or provider == "codex":
            return self._test_openai(api_key)
        elif provider == "anthropic" or provider == "claude":
            return self._test_anthropic(api_key)
        elif provider == "google" or provider == "gemini":
            return self._test_google(api_key)
        elif provider == "deepseek":
            return self._test_deepseek(api_key)
        elif provider == "openrouter":
            return self._test_openrouter(api_key)
        elif provider == "jules":
            # Jules requires complex auth (GitHub token + Jules key), 
            # simple key test might not be sufficient or standardized yet.
            # For now, just check format.
            if api_key.startswith("jules-"):
                 return {"ok": True, "message": "Jules key format valid (Live test not implemented)"}
            return {"ok": False, "message": "Invalid Jules key format"}
        elif provider == "github":
             return self._test_github(api_key)
             
        return {"ok": False, "message": f"Live testing not supported for {provider}"}

    def _test_openai(self, api_key: str) -> Dict:
        try:
            # List models is a cheap, standard auth check
            headers = {"Authorization": f"Bearer {api_key}"}
            response = requests.get("https://api.openai.com/v1/models", headers=headers, timeout=10)
            
            if response.status_code == 200:
                return {"ok": True, "message": "Connection successful (OpenAI)"}
            else:
                try:
                    err = response.json().get("error", {}).get("message", response.text)
                except (json.JSONDecodeError, ValueError, KeyError, AttributeError):
                    err = response.text
                return {"ok": False, "message": f"OpenAI Error: {err}"}
        except Exception as e:
            return {"ok": False, "message": f"Connection failed: {str(e)}"}

    def _test_anthropic(self, api_key: str) -> Dict:
        try:
            # Anthropic requires 'x-api-key' and 'anthropic-version'
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            # Create a dummy message with max_tokens=1 to test auth
            data = {
                "model": "claude-3-haiku-20240307",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "Hi"}]
            }
            response = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=data, timeout=10)
            
            if response.status_code == 200:
                return {"ok": True, "message": "Connection successful (Anthropic)"}
            else:
                try:
                    err = response.json().get("error", {}).get("message", response.text)
                except (json.JSONDecodeError, ValueError, KeyError, AttributeError):
                    err = response.text
                return {"ok": False, "message": f"Anthropic Error: {err}"}
        except Exception as e:
            return {"ok": False, "message": f"Connection failed: {str(e)}"}

    def _test_google(self, api_key: str) -> Dict:
        try:
            # Gemini list models
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                return {"ok": True, "message": "Connection successful (Gemini)"}
            else:
                try:
                    err = response.json().get("error", {}).get("message", response.text)
                except (json.JSONDecodeError, ValueError, KeyError, AttributeError):
                    err = response.text
                return {"ok": False, "message": f"Gemini Error: {err}"}
        except Exception as e:
            return {"ok": False, "message": f"Connection failed: {str(e)}"}

    def _test_deepseek(self, api_key: str) -> Dict:
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            # DeepSeek is OpenAI compatible usually
            response = requests.get("https://api.deepseek.com/models", headers=headers, timeout=10)
            if response.status_code == 200:
                 return {"ok": True, "message": "Connection successful (DeepSeek)"}
            else:
                 return {"ok": False, "message": f"DeepSeek Error: {response.status_code}"}
        except Exception as e:
            return {"ok": False, "message": f"Connection failed: {str(e)}"}

    def _test_openrouter(self, api_key: str) -> Dict:
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            response = requests.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=10)
            if response.status_code == 200:
                 return {"ok": True, "message": "Connection successful (OpenRouter)"}
            else:
                 return {"ok": False, "message": f"OpenRouter Error: {response.status_code}"}
        except Exception as e:
             return {"ok": False, "message": f"Connection failed: {str(e)}"}

    def _test_github(self, token: str) -> Dict:
        try:
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json"
            }
            response = requests.get("https://api.github.com/user", headers=headers, timeout=10)
            
            if response.status_code == 200:
                user = response.json().get("login", "Unknown")
                return {"ok": True, "message": f"Connected as {user}"}
            else:
                return {"ok": False, "message": f"GitHub Error: {response.status_code}"}
        except Exception as e:
            return {"ok": False, "message": f"Connection failed: {str(e)}"}
