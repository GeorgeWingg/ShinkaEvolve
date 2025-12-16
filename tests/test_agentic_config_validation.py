"""Tests for AgenticConfig validation and new features."""

import pytest
import warnings

from shinka.core.runner import AgenticConfig, AgenticEvaluatorConfig, JulesConfig
from shinka.edit.types import SandboxMode, get_backend_sandbox_args


class TestAgenticConfigValidation:
    """Tests for AgenticConfig.__post_init__ validation."""

    def test_valid_codex_backend(self):
        """Codex backend should be valid without additional config."""
        cfg = AgenticConfig(backend="codex")
        assert cfg.backend == "codex"

    def test_valid_gemini_backend(self):
        """Gemini backend should be valid without additional config."""
        cfg = AgenticConfig(backend="gemini")
        assert cfg.backend == "gemini"

    def test_valid_claude_backend(self):
        """Claude backend should be valid without additional config."""
        cfg = AgenticConfig(backend="claude")
        assert cfg.backend == "claude"

    def test_valid_shinka_backend(self):
        """ShinkaAgent backend should be valid without additional config."""
        cfg = AgenticConfig(backend="shinka")
        assert cfg.backend == "shinka"

    def test_invalid_backend_raises(self):
        """Unknown backend should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown backend"):
            AgenticConfig(backend="invalid_backend")

    def test_jules_requires_github_repo(self):
        """Jules backend without github_repo should raise ValueError."""
        with pytest.raises(ValueError, match="Jules backend requires github_repo"):
            AgenticConfig(backend="jules")

    def test_jules_with_extra_cli_config_valid(self):
        """Jules backend with github_repo in extra_cli_config should be valid."""
        cfg = AgenticConfig(
            backend="jules",
            extra_cli_config={"github_repo": "owner/repo"},
        )
        assert cfg.backend == "jules"
        assert cfg.extra_cli_config["github_repo"] == "owner/repo"

    def test_jules_with_jules_config_valid(self):
        """Jules backend with JulesConfig should be valid."""
        jules_cfg = JulesConfig(github_repo="owner/repo")
        cfg = AgenticConfig(backend="jules", jules=jules_cfg)
        assert cfg.backend == "jules"
        assert cfg.jules.github_repo == "owner/repo"


class TestJulesConfigBridging:
    """Tests for JulesConfig bridging to extra_cli_config."""

    def test_jules_config_bridges_to_extra_cli_config(self):
        """JulesConfig values should be bridged to extra_cli_config."""
        jules_cfg = JulesConfig(
            github_repo="owner/repo",
            base_branch="develop",
            automation_mode="MANUAL",
            poll_interval=30,
            cleanup_branch=False,
            auto_approve_plan=False,
        )
        cfg = AgenticConfig(backend="jules", jules=jules_cfg)

        # Check bridged values
        assert cfg.extra_cli_config["github_repo"] == "owner/repo"
        assert cfg.extra_cli_config["base_branch"] == "develop"
        assert cfg.extra_cli_config["automation_mode"] == "MANUAL"
        assert cfg.extra_cli_config["poll_interval"] == 30
        assert cfg.extra_cli_config["cleanup_branch"] is False
        assert cfg.extra_cli_config["auto_approve_plan"] is False

    def test_extra_cli_config_takes_precedence_over_jules_config(self):
        """Values in extra_cli_config should take precedence via setdefault."""
        jules_cfg = JulesConfig(github_repo="owner/repo", base_branch="develop")
        cfg = AgenticConfig(
            backend="jules",
            jules=jules_cfg,
            extra_cli_config={"github_repo": "other/repo"},  # Override
        )

        # extra_cli_config value should take precedence (setdefault doesn't overwrite)
        assert cfg.extra_cli_config["github_repo"] == "other/repo"
        # But base_branch should be bridged since it wasn't in extra_cli_config
        assert cfg.extra_cli_config["base_branch"] == "develop"


class TestMaxTurnsDeprecation:
    """Tests for max_turns → max_events deprecation."""

    def test_max_events_is_canonical(self):
        """max_events should be the canonical field."""
        cfg = AgenticConfig()
        assert cfg.max_events == 50  # Default
        assert cfg.max_turns is None  # Deprecated field should be None

    def test_max_turns_maps_to_max_events_with_warning(self):
        """Using max_turns should map to max_events and emit warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = AgenticConfig(max_turns=100)

            # Should have emitted deprecation warning
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "max_turns is deprecated" in str(w[0].message)

            # Value should be mapped to max_events
            assert cfg.max_events == 100

    def test_evaluator_max_turns_deprecation(self):
        """AgenticEvaluatorConfig should also support max_turns deprecation."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = AgenticEvaluatorConfig(max_turns=120)

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert cfg.max_events == 120


class TestModelField:
    """Tests for the new explicit model field."""

    def test_model_field_exists(self):
        """AgenticConfig should have model field."""
        cfg = AgenticConfig()
        assert hasattr(cfg, "model")
        assert cfg.model is None  # Default is None

    def test_model_field_can_be_set(self):
        """model field should accept a string value."""
        cfg = AgenticConfig(model="gpt-5.1-codex-mini")
        assert cfg.model == "gpt-5.1-codex-mini"

    def test_evaluator_model_field_exists(self):
        """AgenticEvaluatorConfig should also have model field."""
        cfg = AgenticEvaluatorConfig()
        assert hasattr(cfg, "model")
        assert cfg.model is None


class TestSandboxMode:
    """Tests for SandboxMode enum and helpers."""

    def test_sandbox_mode_values(self):
        """SandboxMode should have SECURE, PERMISSIVE, OFF values."""
        assert SandboxMode.SECURE == "secure"
        assert SandboxMode.PERMISSIVE == "permissive"
        assert SandboxMode.OFF == "off"

    def test_get_backend_sandbox_args_codex_secure(self):
        """Codex secure mode should map to workspace-write."""
        args = get_backend_sandbox_args("secure", "codex")
        assert args == {"sandbox": "workspace-write"}

    def test_get_backend_sandbox_args_codex_permissive(self):
        """Codex permissive mode should map to none."""
        args = get_backend_sandbox_args("permissive", "codex")
        assert args == {"sandbox": "none"}

    def test_get_backend_sandbox_args_claude_secure(self):
        """Claude secure mode should not skip permissions."""
        args = get_backend_sandbox_args("secure", "claude")
        assert args == {"skip_permissions": False}

    def test_get_backend_sandbox_args_claude_permissive(self):
        """Claude permissive mode should skip permissions."""
        args = get_backend_sandbox_args("permissive", "claude")
        assert args == {"skip_permissions": True}

    def test_get_backend_sandbox_args_gemini_secure(self):
        """Gemini secure mode should enable sandbox flag."""
        args = get_backend_sandbox_args("secure", "gemini")
        assert args == {"sandbox_flag": True}

    def test_get_backend_sandbox_args_gemini_permissive(self):
        """Gemini permissive mode should disable sandbox flag."""
        args = get_backend_sandbox_args("permissive", "gemini")
        assert args == {"sandbox_flag": False}

    def test_get_backend_sandbox_args_jules_ignored(self):
        """Jules should ignore sandbox settings."""
        args = get_backend_sandbox_args("secure", "jules")
        assert args == {}

    def test_get_backend_sandbox_args_shinka_ignored(self):
        """ShinkaAgent should ignore sandbox settings."""
        args = get_backend_sandbox_args("secure", "shinka")
        assert args == {}


class TestJulesConfig:
    """Tests for JulesConfig dataclass."""

    def test_jules_config_defaults(self):
        """JulesConfig should have sensible defaults."""
        cfg = JulesConfig()
        assert cfg.github_repo == ""
        assert cfg.base_branch == "main"
        assert cfg.automation_mode == "AUTO_CREATE_PR"
        assert cfg.poll_interval == 15
        assert cfg.cleanup_branch is True
        assert cfg.require_plan_approval is None
        assert cfg.auto_approve_plan is True

    def test_jules_config_custom_values(self):
        """JulesConfig should accept custom values."""
        cfg = JulesConfig(
            github_repo="my-org/my-repo",
            base_branch="develop",
            automation_mode="MANUAL",
            poll_interval=30,
            cleanup_branch=False,
            require_plan_approval=True,
            auto_approve_plan=False,
        )
        assert cfg.github_repo == "my-org/my-repo"
        assert cfg.base_branch == "develop"
        assert cfg.automation_mode == "MANUAL"
        assert cfg.poll_interval == 30
        assert cfg.cleanup_branch is False
        assert cfg.require_plan_approval is True
        assert cfg.auto_approve_plan is False
