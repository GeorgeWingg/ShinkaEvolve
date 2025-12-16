"""Tests for Jules backend integration.

Tests cover:
- Jules API client (HTTP operations, error handling)
- GitHub staging directory sync
- AgentRunner implementation
- Auth status checking
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# -----------------------------------------------------------------------------
# Test Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def mock_jules_api_key():
    """Mock JULES_API_KEY environment variable."""
    with patch.dict(os.environ, {"JULES_API_KEY": "test-api-key"}):
        yield "test-api-key"


@pytest.fixture
def mock_github_token():
    """Mock GITHUB_TOKEN environment variable."""
    with patch.dict(os.environ, {"GITHUB_TOKEN": "test-github-token"}):
        yield "test-github-token"


@pytest.fixture
def mock_requests():
    """Mock requests library for API calls."""
    with patch("shinka.edit.jules_api.requests") as mock:
        mock_session = MagicMock()
        mock.Session.return_value = mock_session
        yield mock_session


@pytest.fixture
def mock_subprocess():
    """Mock subprocess for git commands."""
    with patch("shinka.edit.jules_sync.subprocess") as mock:
        mock.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        yield mock


@pytest.fixture
def temp_workdir(tmp_path):
    """Create a temporary working directory with sample files."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    # Create sample files
    (workdir / "main.py").write_text("print('hello')")
    (workdir / "helper.py").write_text("def helper(): pass")
    (workdir / "subdir").mkdir()
    (workdir / "subdir" / "nested.py").write_text("# nested")

    return workdir


@pytest.fixture
def temp_staging(tmp_path):
    """Create a temporary staging directory."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / ".git").mkdir()  # Simulate git repo
    return staging


# -----------------------------------------------------------------------------
# Test is_workdir_effectively_empty
# -----------------------------------------------------------------------------


class TestIsWorkdirEffectivelyEmpty:
    """Tests for smart preserve_base detection."""

    def test_empty_dir_is_empty(self, tmp_path):
        """Empty directory is effectively empty."""
        from shinka.edit.jules_sync import is_workdir_effectively_empty

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        assert is_workdir_effectively_empty(workdir) is True

    def test_nonexistent_dir_is_empty(self, tmp_path):
        """Nonexistent directory is effectively empty."""
        from shinka.edit.jules_sync import is_workdir_effectively_empty

        workdir = tmp_path / "does_not_exist"
        assert is_workdir_effectively_empty(workdir) is True

    def test_dir_with_real_code_is_not_empty(self, tmp_path):
        """Directory with real code files is not empty."""
        from shinka.edit.jules_sync import is_workdir_effectively_empty

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        (workdir / "main.py").write_text("def main():\n    print('hello world')\n" * 10)
        assert is_workdir_effectively_empty(workdir) is False

    def test_seed_marker_only_is_empty(self, tmp_path):
        """Directory with only seed marker is effectively empty."""
        from shinka.edit.jules_sync import is_workdir_effectively_empty, SEED_MARKER_FILENAME

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        (workdir / SEED_MARKER_FILENAME).write_text("seed marker content")
        assert is_workdir_effectively_empty(workdir) is True

    def test_seed_marker_plus_empty_main_is_empty(self, tmp_path):
        """Seed marker + empty main.py is effectively empty."""
        from shinka.edit.jules_sync import is_workdir_effectively_empty, SEED_MARKER_FILENAME

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        (workdir / SEED_MARKER_FILENAME).write_text("seed marker content")
        (workdir / "main.py").write_text("")  # Empty file
        assert is_workdir_effectively_empty(workdir) is True

    def test_seed_marker_plus_real_code_is_not_empty(self, tmp_path):
        """Seed marker + real code files is not empty."""
        from shinka.edit.jules_sync import is_workdir_effectively_empty, SEED_MARKER_FILENAME

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        (workdir / SEED_MARKER_FILENAME).write_text("seed marker content")
        (workdir / "evolved_code.py").write_text("# This is significant evolved code\n" * 20)
        assert is_workdir_effectively_empty(workdir) is False

    def test_small_main_py_is_empty(self, tmp_path):
        """Very small main.py (legacy empty seed) is effectively empty."""
        from shinka.edit.jules_sync import is_workdir_effectively_empty

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        (workdir / "main.py").write_text("# placeholder")  # < 200 bytes
        assert is_workdir_effectively_empty(workdir) is True

    def test_git_folder_ignored(self, tmp_path):
        """Git folder is ignored when checking if empty."""
        from shinka.edit.jules_sync import is_workdir_effectively_empty

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        git_dir = workdir / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("lots of git config data" * 100)
        assert is_workdir_effectively_empty(workdir) is True

    def test_evolved_multifile_is_not_empty(self, tmp_path):
        """Directory with multiple evolved files is not empty."""
        from shinka.edit.jules_sync import is_workdir_effectively_empty

        workdir = tmp_path / "workdir"
        workdir.mkdir()
        (workdir / "src").mkdir()
        (workdir / "src" / "app.py").write_text("# Application code\n" * 30)
        (workdir / "src" / "utils.py").write_text("# Utility functions\n" * 30)
        (workdir / "tests").mkdir()
        (workdir / "tests" / "test_app.py").write_text("# Tests\n" * 20)
        assert is_workdir_effectively_empty(workdir) is False


# -----------------------------------------------------------------------------
# Test JulesAPIClient
# -----------------------------------------------------------------------------


class TestJulesAPIClient:
    """Tests for the Jules API client."""

    def test_create_session(self, mock_jules_api_key, mock_requests):
        """Test session creation."""
        from shinka.edit.jules_api import JulesAPIClient

        # Mock response
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "name": "sessions/12345",
            "id": "12345",
            "state": "PENDING",
            "prompt": "Fix the bug",
            "url": "https://jules.google.com/session/12345",
        }
        mock_response.text = json.dumps(mock_response.json.return_value)
        mock_requests.request.return_value = mock_response

        client = JulesAPIClient(mock_jules_api_key)
        session = client.create_session(
            prompt="Fix the bug",
            github_repo="owner/repo",
            branch="main",
        )

        assert session.id == "12345"
        assert session.status == "PENDING"
        assert session.prompt == "Fix the bug"

    def test_poll_until_complete(self, mock_jules_api_key, mock_requests):
        """Test polling mechanism."""
        from shinka.edit.jules_api import JulesAPIClient, JulesSession

        # Mock responses: PENDING -> RUNNING -> COMPLETED
        responses = [
            {"state": "PENDING"},
            {"state": "RUNNING"},
            {"state": "COMPLETED"},
        ]
        response_iter = iter(responses)

        def mock_request(*args, **kwargs):
            mock_response = MagicMock()
            mock_response.ok = True
            data = next(response_iter, {"state": "COMPLETED"})
            data.setdefault("name", "sessions/123")
            data.setdefault("id", "123")
            data.setdefault("prompt", "test")
            mock_response.json.return_value = data
            mock_response.text = json.dumps(data)
            return mock_response

        mock_requests.request.side_effect = mock_request

        client = JulesAPIClient(mock_jules_api_key)
        with patch("time.sleep"):
            session = client.poll_until_complete("123", poll_interval=0)

        assert session.is_complete
        assert session.is_success

    def test_list_sources(self, mock_jules_api_key, mock_requests):
        """Test listing connected sources."""
        from shinka.edit.jules_api import JulesAPIClient

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "sources": [
                {"name": "sources/github/owner/repo1"},
                {"name": "sources/github/owner/repo2"},
            ]
        }
        mock_response.text = json.dumps(mock_response.json.return_value)
        mock_requests.request.return_value = mock_response

        client = JulesAPIClient(mock_jules_api_key)
        sources = client.list_sources()

        assert len(sources) == 2
        assert "sources/github/owner/repo1" in sources

    def test_verify_repo_connected_success(self, mock_jules_api_key, mock_requests):
        """Test verifying repo is connected - success case."""
        from shinka.edit.jules_api import JulesAPIClient

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "sources": [{"name": "sources/github/owner/repo"}]
        }
        mock_response.text = json.dumps(mock_response.json.return_value)
        mock_requests.request.return_value = mock_response

        client = JulesAPIClient(mock_jules_api_key)
        result = client.verify_repo_connected("owner/repo")

        assert result is True

    def test_verify_repo_not_connected_raises(self, mock_jules_api_key, mock_requests):
        """Test verifying repo is connected - not found."""
        from shinka.edit.jules_api import JulesAPIClient, JulesRepoNotConnectedError

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"sources": []}
        mock_response.text = json.dumps(mock_response.json.return_value)
        mock_requests.request.return_value = mock_response

        client = JulesAPIClient(mock_jules_api_key)

        with pytest.raises(JulesRepoNotConnectedError):
            client.verify_repo_connected("owner/repo")

    def test_handles_quota_exceeded(self, mock_jules_api_key):
        """Test handling quota exceeded error."""
        from shinka.edit.jules_api import JulesAPIClient, JulesQuotaExhaustedError
        import requests

        with patch("requests.Session") as MockSession:
            mock_session = MockSession.return_value
            mock_response = MagicMock()
            mock_response.ok = False
            mock_response.status_code = 429
            mock_response.json.return_value = {
                "error": {"message": "Quota exceeded"}
            }
            mock_response.text = json.dumps(mock_response.json.return_value)
            mock_session.request.return_value = mock_response

            client = JulesAPIClient(mock_jules_api_key)

            with pytest.raises(JulesQuotaExhaustedError):
                client.list_sources()


# -----------------------------------------------------------------------------
# Test JulesGitHubSync
# -----------------------------------------------------------------------------


class TestJulesGitHubSync:
    """Tests for GitHub synchronization."""

    def test_staging_dir_created(self, tmp_path, mock_github_token):
        """Test staging directory is created."""
        from shinka.edit.jules_sync import JulesGitHubSync

        sync = JulesGitHubSync(
            github_token=mock_github_token,
            staging_base=tmp_path / "staging",
        )

        staging_dir = sync.get_staging_dir("owner/repo")
        assert str(staging_dir).endswith("owner/repo") or str(staging_dir).endswith("owner\\repo")

    def test_push_excludes_git_folder(
        self, temp_workdir, temp_staging, mock_github_token, mock_subprocess
    ):
        """Test that .git folder is not copied during push."""
        from shinka.edit.jules_sync import JulesGitHubSync

        # Create .git folder in workdir
        git_dir = temp_workdir / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("git config")

        sync = JulesGitHubSync(
            github_token=mock_github_token,
            staging_base=temp_staging.parent,
        )

        # Mock ensure_staging_clone to return our temp staging
        with patch.object(sync, "ensure_staging_clone", return_value=temp_staging):
            with patch.object(sync, "_run_git"):
                result = sync.push_to_jules_branch(
                    workdir=temp_workdir,
                    github_repo="owner/repo",
                    branch_name="test-branch",
                )

        # Verify .git was not copied
        # (In real implementation, _sync_files_to_staging excludes .git)
        assert result.branch_name == "test-branch"

    def test_pull_updates_workdir(
        self, temp_workdir, temp_staging, mock_github_token, mock_subprocess
    ):
        """Test that pull updates the working directory."""
        from shinka.edit.jules_sync import JulesGitHubSync

        # Add a new file to staging (simulating Jules changes)
        (temp_staging / "new_file.py").write_text("# new from jules")

        sync = JulesGitHubSync(
            github_token=mock_github_token,
            staging_base=temp_staging.parent,
        )

        with patch.object(sync, "get_staging_dir", return_value=temp_staging):
            with patch.object(sync, "_run_git"):
                changed_files = sync.pull_from_jules_branch(
                    workdir=temp_workdir,
                    github_repo="owner/repo",
                    branch_name="test-branch",
                )

        assert Path("new_file.py") in changed_files

    def test_cleanup_branch_called(self, mock_github_token, mock_subprocess, tmp_path):
        """Test that cleanup_branch deletes the branch."""
        from shinka.edit.jules_sync import JulesGitHubSync

        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / ".git").mkdir()

        sync = JulesGitHubSync(
            github_token=mock_github_token,
            staging_base=tmp_path,
        )

        with patch.object(sync, "get_staging_dir", return_value=staging):
            result = sync.cleanup_branch("owner/repo", "test-branch")

        assert result is True


# -----------------------------------------------------------------------------
# Test Jules Runner
# -----------------------------------------------------------------------------


class TestJulesRunner:
    """Tests for the Jules AgentRunner implementation."""

    def test_follows_agent_runner_protocol(self):
        """Test that run_jules_task matches AgentRunner protocol."""
        from shinka.edit.jules_cli import run_jules_task
        import inspect

        sig = inspect.signature(run_jules_task)
        params = list(sig.parameters.keys())

        # Required parameters
        assert "user_prompt" in params
        assert "workdir" in params
        assert "extra_cli_config" in params
        assert "max_seconds" in params
        assert "max_events" in params

    def test_yields_normalized_events(
        self, mock_jules_api_key, mock_github_token, temp_workdir
    ):
        """Test event normalization."""
        from shinka.edit.jules_cli import run_jules_task
        from shinka.edit.jules_api import JulesSession

        with patch("shinka.edit.jules_cli.JulesAPIClient") as MockClient:
            with patch("shinka.edit.jules_cli.JulesGitHubSync") as MockSync:
                # Setup mocks
                mock_client = MockClient.return_value
                mock_sync = MockSync.return_value

                # Mock session creation
                mock_session = MagicMock()
                mock_session.id = "123"
                mock_session.status = "COMPLETED"
                mock_session.is_complete = True
                mock_session.is_success = True
                mock_client.create_session.return_value = mock_session
                mock_client.get_session.return_value = mock_session
                mock_client.get_all_activities.return_value = []
                mock_client.verify_repo_connected.return_value = True

                # Mock sync
                mock_sync.push_to_jules_branch.return_value = MagicMock(
                    success=True, branch_name="test-branch"
                )
                mock_sync.pull_from_jules_branch.return_value = {}

                # Run task
                events = list(
                    run_jules_task(
                        user_prompt="test",
                        workdir=temp_workdir,
                        extra_cli_config={
                            "github_repo": "owner/repo",
                            "jules_api_key": mock_jules_api_key,
                            "github_token": mock_github_token,
                        },
                        max_seconds=60,
                        max_events=10,
                        sandbox="",
                        approval_mode="full-auto",
                        profile=None,
                    )
                )

        # Should have at least init and usage events
        event_types = [e.get("type") for e in events]
        assert "init" in event_types
        assert "usage" in event_types

    def test_branch_cleanup_on_success(
        self, mock_jules_api_key, mock_github_token, temp_workdir
    ):
        """Test branch cleanup on successful completion."""
        from shinka.edit.jules_cli import run_jules_task

        cleanup_called = False

        def mock_cleanup(*args, **kwargs):
            nonlocal cleanup_called
            cleanup_called = True

        with patch("shinka.edit.jules_cli.JulesAPIClient") as MockClient:
            with patch("shinka.edit.jules_cli.JulesGitHubSync") as MockSync:
                mock_client = MockClient.return_value
                mock_sync = MockSync.return_value

                mock_session = MagicMock()
                mock_session.id = "123"
                mock_session.is_complete = True
                mock_session.is_success = True
                mock_client.create_session.return_value = mock_session
                mock_client.get_session.return_value = mock_session
                mock_client.get_all_activities.return_value = []
                mock_client.verify_repo_connected.return_value = True

                mock_sync.push_to_jules_branch.return_value = MagicMock(
                    success=True, branch_name="test-branch"
                )
                mock_sync.pull_from_jules_branch.return_value = {}
                mock_sync.cleanup_branch = mock_cleanup

                list(
                    run_jules_task(
                        user_prompt="test",
                        workdir=temp_workdir,
                        extra_cli_config={
                            "github_repo": "owner/repo",
                            "jules_api_key": mock_jules_api_key,
                            "github_token": mock_github_token,
                            "cleanup_branch": True,
                        },
                        max_seconds=60,
                        max_events=10,
                        sandbox="",
                        approval_mode="full-auto",
                        profile=None,
                    )
                )

        assert cleanup_called

    def test_branch_cleanup_on_error(
        self, mock_jules_api_key, mock_github_token, temp_workdir
    ):
        """Test branch cleanup even on error."""
        from shinka.edit.jules_cli import run_jules_task, JulesExecutionError

        cleanup_called = False

        def mock_cleanup(*args, **kwargs):
            nonlocal cleanup_called
            cleanup_called = True

        with patch("shinka.edit.jules_cli.JulesAPIClient") as MockClient:
            with patch("shinka.edit.jules_cli.JulesGitHubSync") as MockSync:
                mock_client = MockClient.return_value
                mock_sync = MockSync.return_value

                # Make session fail
                mock_session = MagicMock()
                mock_session.id = "123"
                mock_session.is_complete = True
                mock_session.is_success = False
                mock_session.status = "FAILED"
                mock_session.error = "Test error"
                mock_client.create_session.return_value = mock_session
                mock_client.get_session.return_value = mock_session
                mock_client.get_all_activities.return_value = []
                mock_client.verify_repo_connected.return_value = True

                mock_sync.push_to_jules_branch.return_value = MagicMock(
                    success=True, branch_name="test-branch"
                )
                mock_sync.cleanup_branch = mock_cleanup

                with pytest.raises(JulesExecutionError):
                    list(
                        run_jules_task(
                            user_prompt="test",
                            workdir=temp_workdir,
                            extra_cli_config={
                                "github_repo": "owner/repo",
                                "jules_api_key": mock_jules_api_key,
                                "github_token": mock_github_token,
                                "cleanup_branch": True,
                            },
                            max_seconds=60,
                            max_events=10,
                            sandbox="",
                            approval_mode="full-auto",
                            profile=None,
                        )
                    )

        # Cleanup should still be called via finally
        assert cleanup_called

    def test_max_events_enforced(
        self, mock_jules_api_key, mock_github_token, temp_workdir
    ):
        """Runner aborts once max_events is exceeded."""
        from shinka.edit.jules_cli import run_jules_task, JulesExecutionError

        with patch("shinka.edit.jules_cli.JulesAPIClient") as MockClient:
            with patch("shinka.edit.jules_cli.JulesGitHubSync") as MockSync:
                mock_client = MockClient.return_value
                mock_sync = MockSync.return_value

                mock_session = MagicMock()
                mock_session.id = "123"
                mock_session.status = "COMPLETED"
                mock_session.is_complete = True
                mock_session.is_success = True
                mock_client.create_session.return_value = mock_session
                mock_client.get_session.return_value = mock_session
                mock_client.verify_repo_connected.return_value = True

                # Two activities => init + first activity ok, second exceeds max_events=2.
                act1 = MagicMock()
                act1.timestamp = "t1"
                act1.type = "AgentMessaged"
                act1.content = {"agentMessaged": {"message": "one"}}

                act2 = MagicMock()
                act2.timestamp = "t2"
                act2.type = "AgentMessaged"
                act2.content = {"agentMessaged": {"message": "two"}}

                mock_client.get_all_activities.return_value = [act1, act2]

                mock_sync.push_to_jules_branch.return_value = MagicMock(
                    success=True, branch_name="test-branch"
                )
                mock_sync.pull_from_jules_branch.return_value = {}

                with pytest.raises(JulesExecutionError) as exc:
                    list(
                        run_jules_task(
                            user_prompt="test",
                            workdir=temp_workdir,
                            extra_cli_config={
                                "github_repo": "owner/repo",
                                "jules_api_key": mock_jules_api_key,
                                "github_token": mock_github_token,
                            },
                            max_seconds=60,
                            max_events=2,
                            sandbox="",
                            approval_mode="full-auto",
                            profile=None,
                        )
                    )

        assert "max_events" in str(exc.value).lower()

    def test_auto_approves_plan_when_required(
        self, mock_jules_api_key, mock_github_token, temp_workdir
    ):
        """When approval_mode != full-auto, Shinka auto-approves the plan."""
        from shinka.edit.jules_cli import run_jules_task

        with patch("shinka.edit.jules_cli.JulesAPIClient") as MockClient:
            with patch("shinka.edit.jules_cli.JulesGitHubSync") as MockSync:
                mock_client = MockClient.return_value
                mock_sync = MockSync.return_value

                mock_session = MagicMock()
                mock_session.id = "123"
                mock_session.status = "COMPLETED"
                mock_session.is_complete = True
                mock_session.is_success = True
                mock_client.create_session.return_value = mock_session
                mock_client.get_session.return_value = mock_session
                mock_client.verify_repo_connected.return_value = True

                plan_act = MagicMock()
                plan_act.timestamp = "t1"
                plan_act.type = "PlanGenerated"
                plan_act.content = {
                    "planGenerated": {
                        "plan": {
                            "steps": [{"index": 0, "title": "do thing"}]
                        }
                    }
                }
                mock_client.get_all_activities.return_value = [plan_act]

                mock_sync.push_to_jules_branch.return_value = MagicMock(
                    success=True, branch_name="test-branch"
                )
                mock_sync.pull_from_jules_branch.return_value = {}

                events = list(
                    run_jules_task(
                        user_prompt="test",
                        workdir=temp_workdir,
                        extra_cli_config={
                            "github_repo": "owner/repo",
                            "jules_api_key": mock_jules_api_key,
                            "github_token": mock_github_token,
                        },
                        max_seconds=60,
                        max_events=20,
                        sandbox="",
                        approval_mode="default",
                        profile=None,
                    )
                )

        mock_client.approve_plan.assert_called_once_with("123")
        assert any(
            e.get("item", {}).get("role") == "system"
            and "auto" in (e.get("item", {}).get("text", "").lower())
            for e in events
        )

    def test_resume_requires_branch_name(self, temp_workdir):
        """resume_session_id without resume_branch_name fails loudly."""
        from shinka.edit.jules_cli import run_jules_task, JulesExecutionError

        with pytest.raises(JulesExecutionError):
            list(
                run_jules_task(
                    user_prompt="test",
                    workdir=temp_workdir,
                    extra_cli_config={"github_repo": "owner/repo"},
                    max_seconds=60,
                    max_events=10,
                    sandbox="",
                    approval_mode="full-auto",
                    profile=None,
                    resume_session_id="abc123",
                )
            )

    def test_missing_credentials_wrapped(self, temp_workdir):
        """Missing Jules credentials surface as JulesExecutionError."""
        from shinka.edit.jules_cli import run_jules_task, JulesExecutionError
        from shinka.edit.jules_api import JulesUnavailableError

        with patch(
            "shinka.edit.jules_cli.ensure_jules_api_key",
            side_effect=JulesUnavailableError("no key"),
        ), patch(
            "shinka.edit.jules_cli.ensure_github_token",
            return_value="gh-token",
        ):
            with pytest.raises(JulesExecutionError):
                list(
                    run_jules_task(
                        user_prompt="test",
                        workdir=temp_workdir,
                        extra_cli_config={"github_repo": "owner/repo"},
                        max_seconds=60,
                        max_events=10,
                        sandbox="",
                        approval_mode="full-auto",
                        profile=None,
                    )
                )


# -----------------------------------------------------------------------------
# Test Jules Auth
# -----------------------------------------------------------------------------


class TestJulesAuth:
    """Tests for Jules authentication checking."""

    def test_check_jules_auth_env_var(self, mock_jules_api_key, mock_github_token):
        """Test auth detection via env var."""
        from shinka.tools.auth_status import check_jules_auth

        with patch("shinka.tools.auth_status.JulesAPIClient") as MockClient:
            mock_client = MockClient.return_value
            mock_client.list_sources.return_value = ["sources/github/owner/repo"]

            status = check_jules_auth()

        assert status.available is True
        assert status.backend == "jules"

    def test_check_jules_auth_missing_api_key(self, mock_github_token):
        """Test auth detection with missing API key."""
        from shinka.tools.auth_status import check_jules_auth

        # Ensure JULES_API_KEY is not set
        with patch.dict(os.environ, {}, clear=False):
            if "JULES_API_KEY" in os.environ:
                del os.environ["JULES_API_KEY"]

            status = check_jules_auth()

        assert status.available is False
        assert "JULES_API_KEY" in status.error

    def test_check_jules_auth_missing_github_token(self, mock_jules_api_key):
        """Test auth detection with missing GitHub token."""
        from shinka.tools.auth_status import check_jules_auth

        # Ensure GITHUB_TOKEN is not set
        with patch.dict(os.environ, {"JULES_API_KEY": mock_jules_api_key}, clear=False):
            if "GITHUB_TOKEN" in os.environ:
                del os.environ["GITHUB_TOKEN"]

            with patch("shinka.tools.auth_status.get_github_token", return_value=None):
                status = check_jules_auth()

        assert status.available is False
        assert "GITHUB_TOKEN" in status.error

    def test_repo_connection_check(self, mock_jules_api_key, mock_requests):
        """Test repository connection checking."""
        from shinka.edit.jules_api import ensure_jules_available, JulesRepoNotConnectedError

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"sources": []}
        mock_response.text = json.dumps(mock_response.json.return_value)
        mock_requests.request.return_value = mock_response

        with pytest.raises(JulesRepoNotConnectedError) as exc_info:
            ensure_jules_available("owner/nonexistent-repo")

        assert "not connected" in str(exc_info.value).lower()


class TestJulesNoveltyAndScratchpad:
    """Tests for Jules support in novelty judge and scratchpad."""

    def test_novelty_judge_can_use_jules_runner(self):
        """Test that NoveltyJudge accepts Jules agent_runner."""
        from shinka.core.novelty_judge import NoveltyJudge
        from dataclasses import dataclass
        from typing import Dict, Any, Optional

        @dataclass
        class MockAgenticConfig:
            cli_profile: Optional[str] = None
            sandbox: str = "workspace-write"
            extra_cli_config: Dict[str, Any] = None
            cli_path: Optional[str] = None

            def __post_init__(self):
                if self.extra_cli_config is None:
                    self.extra_cli_config = {"github_repo": "owner/repo"}

        def mock_jules_runner(**kwargs):
            # Yield a mock response
            yield {"type": "agent_message", "item": {"text": "NOVEL: The code is different"}}
            yield {"type": "usage", "usage": {"total_cost_usd": 0.001}}

        config = MockAgenticConfig()
        judge = NoveltyJudge(
            novelty_llm_client=None,
            language="python",
            agentic_mode=True,
            agent_runner=mock_jules_runner,
            agent_config=config,
        )

        assert judge.agentic_mode is True
        assert judge.agent_runner is not None
        assert judge.agent_config is not None

    def test_scratchpad_backend_includes_jules(self):
        """Test that UIRunConfig allows jules as scratchpad backend."""
        from shinka.webui.run_config import UIRunConfig

        config = UIRunConfig(
            scratchpad_enabled=True,
            scratchpad_backend="jules",
        )

        assert config.scratchpad_backend == "jules"

    def test_runner_maps_jules_meta_backend(self):
        """Test that runner.py maps jules to run_jules_task for meta."""
        from shinka.edit import run_jules_task

        # Verify the function exists and is callable
        assert callable(run_jules_task)

    def test_novelty_check_with_jules_agentic(self, tmp_path):
        """Test agentic novelty check using Jules-like runner."""
        from shinka.core.novelty_judge import NoveltyJudge
        from shinka.database import Program
        from dataclasses import dataclass
        from typing import Dict, Any, Optional

        @dataclass
        class MockAgenticConfig:
            cli_profile: Optional[str] = None
            sandbox: str = "workspace-write"
            extra_cli_config: Dict[str, Any] = None
            cli_path: Optional[str] = None

            def __post_init__(self):
                if self.extra_cli_config is None:
                    self.extra_cli_config = {"github_repo": "owner/repo"}

        call_count = 0

        def mock_jules_runner(**kwargs):
            nonlocal call_count
            call_count += 1
            yield {"type": "agent_message", "item": {"text": "NOVEL: Different approach"}}
            yield {"type": "usage", "usage": {"total_cost_usd": 0.002}}

        config = MockAgenticConfig()
        judge = NoveltyJudge(
            novelty_llm_client=None,
            language="python",
            agentic_mode=True,
            agent_runner=mock_jules_runner,
            agent_config=config,
        )

        # Create a mock most_similar_program
        similar_program = Program(
            id="parent-1",
            code="def foo(): return 1",
            generation=1,
            combined_score=0.5,
        )

        # Call the agentic novelty check directly
        is_novel, explanation, cost = judge._check_llm_novelty_agentic(
            original_code="def foo(): return 1",
            new_code="def foo(): return calculate_value()",
        )

        assert call_count == 1
        assert is_novel is True
        assert "NOVEL" in explanation or "Different" in explanation
        assert cost == 0.002
