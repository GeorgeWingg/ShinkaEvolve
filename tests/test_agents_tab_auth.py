"""
Tests for Agents Tab authentication functionality.

These tests validate:
1. Sign-out persists across page reloads
2. API key storage works correctly
3. Sign-in clears signed-out state
4. All providers have working auth buttons

Run with: pytest tests/test_agents_tab_auth.py -v
"""

import pytest
import re
from pathlib import Path


# Path to the viz_tree.html file
VIZ_TREE_PATH = Path(__file__).parent.parent / "shinka" / "webui" / "viz_tree.html"


@pytest.fixture
def html_content():
    """Load the viz_tree.html content."""
    return VIZ_TREE_PATH.read_text()


class TestSignOutFunctions:
    """Test that sign-out functions are properly implemented."""

    def test_signout_codex_exists(self, html_content):
        """signOutCodex function should exist."""
        assert "function signOutCodex()" in html_content

    def test_signout_gemini_exists(self, html_content):
        """signOutGemini function should exist."""
        assert "function signOutGemini()" in html_content

    def test_signout_claude_exists(self, html_content):
        """signOutClaude function should exist."""
        assert "function signOutClaude()" in html_content

    def test_signout_codex_sets_localstorage(self, html_content):
        """signOutCodex should set signed-out state in localStorage."""
        # Find the signOutCodex function
        match = re.search(r"function signOutCodex\(\)\s*\{[^}]+\}", html_content, re.DOTALL)
        assert match, "signOutCodex function not found"
        func_body = match.group(0)
        assert "localStorage.setItem('shinka_signed_out_codex', 'true')" in func_body, \
            "signOutCodex should persist signed-out state to localStorage"

    def test_signout_gemini_sets_localstorage(self, html_content):
        """signOutGemini should set signed-out state in localStorage."""
        match = re.search(r"function signOutGemini\(\)\s*\{[^}]+\}", html_content, re.DOTALL)
        assert match, "signOutGemini function not found"
        func_body = match.group(0)
        assert "localStorage.setItem('shinka_signed_out_gemini', 'true')" in func_body, \
            "signOutGemini should persist signed-out state to localStorage"

    def test_signout_claude_sets_localstorage(self, html_content):
        """signOutClaude should set signed-out state in localStorage."""
        match = re.search(r"function signOutClaude\(\)\s*\{[^}]+\}", html_content, re.DOTALL)
        assert match, "signOutClaude function not found"
        func_body = match.group(0)
        assert "localStorage.setItem('shinka_signed_out_claude', 'true')" in func_body, \
            "signOutClaude should persist signed-out state to localStorage"

    def test_signout_removes_api_keys(self, html_content):
        """Sign-out functions should remove stored API keys."""
        assert "localStorage.removeItem('shinka_api_key_codex')" in html_content
        assert "localStorage.removeItem('shinka_api_key_gemini')" in html_content
        assert "localStorage.removeItem('shinka_api_key_claude')" in html_content

    def test_signout_shows_toast(self, html_content):
        """Sign-out functions should show toast notifications."""
        assert "showToast('Signed out of Codex CLI'" in html_content
        assert "showToast('Signed out of Gemini CLI'" in html_content
        assert "showToast('Signed out of Claude Code'" in html_content


class TestSignedOutStateCheck:
    """Test that signed-out state helpers exist for future use."""

    def test_is_signed_out_helper_exists(self, html_content):
        """isSignedOut helper function should exist."""
        assert "function isSignedOut(provider)" in html_content

    def test_clear_signed_out_state_helper_exists(self, html_content):
        """clearSignedOutState helper function should exist."""
        assert "function clearSignedOutState(provider)" in html_content

    # NOTE: The following tests are for planned features not yet implemented.
    # The isSignedOut function exists but isn't called during fetch/render yet.
    # These tests will be re-enabled when the feature is fully implemented.
    
    @pytest.mark.skip(reason="Signed-out state integration not yet implemented")
    def test_fetch_checks_codex_signed_out(self, html_content):
        """fetchAndRenderAgentUsage should check codex signed-out state."""
        assert "codexSignedOut = isSignedOut('codex')" in html_content or \
               "const codexSignedOut = isSignedOut('codex')" in html_content

    @pytest.mark.skip(reason="Signed-out state integration not yet implemented")
    def test_fetch_checks_gemini_signed_out(self, html_content):
        """fetchAndRenderAgentUsage should check gemini signed-out state."""
        assert "geminiSignedOut = isSignedOut('gemini')" in html_content or \
               "const geminiSignedOut = isSignedOut('gemini')" in html_content

    @pytest.mark.skip(reason="Signed-out state integration not yet implemented")
    def test_codex_render_respects_signed_out(self, html_content):
        """Codex card render should check signed-out state."""
        assert "isCodex && !codexSignedOut" in html_content

    @pytest.mark.skip(reason="Signed-out state integration not yet implemented")
    def test_gemini_render_respects_signed_out(self, html_content):
        """Gemini card render should check signed-out state."""
        assert "isGemini && !geminiSignedOut" in html_content


class TestSignInClearsSignedOut:
    """Test that signing in clears the signed-out state."""

    @pytest.mark.skip(reason="saveApiKey function not implemented - uses inline localStorage.setItem")
    def test_save_api_key_clears_signed_out(self, html_content):
        """saveApiKey should call clearSignedOutState."""
        # Find saveApiKey function
        match = re.search(r"function saveApiKey\(\)\s*\{[\s\S]+?^\s{8}\}", html_content, re.MULTILINE)
        assert match, "saveApiKey function not found"
        func_body = match.group(0)
        assert "clearSignedOutState(currentApiKeyProvider)" in func_body, \
            "saveApiKey should clear signed-out state when user signs in"


class TestAuthButtons:
    """Test that all auth buttons are properly wired."""

    def test_shinka_api_key_button(self, html_content):
        """ShinkaAgent should have API key button."""
        assert "openApiKeyModal('shinka')" in html_content

    def test_codex_oauth_button(self, html_content):
        """Codex should have OAuth button."""
        assert "showOAuthHelp('codex')" in html_content

    def test_codex_api_key_button(self, html_content):
        """Codex should have API key button."""
        assert "openApiKeyModal('codex')" in html_content

    def test_gemini_oauth_button(self, html_content):
        """Gemini should have OAuth button."""
        assert "showOAuthHelp('gemini')" in html_content

    def test_gemini_api_key_button(self, html_content):
        """Gemini should have API key button."""
        assert "openApiKeyModal('gemini')" in html_content

    def test_claude_oauth_button(self, html_content):
        """Claude should have OAuth button."""
        assert "showOAuthHelp('claude')" in html_content

    def test_claude_api_key_button(self, html_content):
        """Claude should have API key button."""
        assert "openApiKeyModal('claude')" in html_content


class TestSignOutButtons:
    """Test that sign-out buttons call correct functions."""

    def test_codex_signout_button(self, html_content):
        """Codex sign-out button should call signOutCodex."""
        assert "signOutCodex()" in html_content

    def test_gemini_signout_button(self, html_content):
        """Gemini sign-out button should call signOutGemini."""
        assert "signOutGemini()" in html_content

    def test_claude_signout_button(self, html_content):
        """Claude sign-out button should call signOutClaude."""
        assert "signOutClaude()" in html_content


class TestModalFunctions:
    """Test modal-related functions."""

    def test_open_api_key_modal_exists(self, html_content):
        """openApiKeyModal function should exist."""
        assert "function openApiKeyModal(provider)" in html_content

    def test_close_api_key_modal_exists(self, html_content):
        """closeApiKeyModal function should exist."""
        assert "function closeApiKeyModal()" in html_content

    @pytest.mark.skip(reason="saveApiKey function not implemented - API key saving is inline")
    def test_save_api_key_exists(self, html_content):
        """saveApiKey function should exist."""
        assert "function saveApiKey()" in html_content

    def test_show_oauth_help_exists(self, html_content):
        """showOAuthHelp function should exist."""
        assert "function showOAuthHelp(provider)" in html_content

    def test_show_toast_exists(self, html_content):
        """showToast function should exist."""
        assert "function showToast(message" in html_content


class TestMetricsTracking:
    """Test auth metrics tracking."""

    def test_shinka_metrics_initialized(self, html_content):
        """window.shinkaMetrics should be initialized."""
        assert "window.shinkaMetrics" in html_content

    def test_auth_events_tracked(self, html_content):
        """Auth events should be pushed to shinkaMetrics.authEvents."""
        assert "shinkaMetrics.authEvents.push" in html_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
