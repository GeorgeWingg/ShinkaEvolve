"""Tests for GitWorktreeManager and git source cloning."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from shinka.webui.git_worktree import GitWorktreeManager, WorktreeInfo
from shinka.webui.run_config import UIRunConfig, flatten_nested_config


class TestGitWorktreeManager:
    """Tests for GitWorktreeManager functionality."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create a manager with temp directories."""
        base_dir = tmp_path / "workspaces"
        cache_dir = tmp_path / "cache"
        return GitWorktreeManager(
            base_dir=str(base_dir),
            cache_dir=str(cache_dir),
        )

    @pytest.fixture
    def local_git_repo(self, tmp_path):
        """Create a local git repository for testing."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_path,
            capture_output=True,
        )

        # Create initial file
        (repo_path / "main.py").write_text("print('hello')\n")
        subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        )

        return repo_path

    def test_manager_creates_directories(self, manager):
        """Test that manager creates base and cache directories."""
        assert Path(manager.base_dir).exists()
        assert Path(manager.cache_dir).exists()

    def test_create_local_copy(self, manager, local_git_repo):
        """Test creating an isolated copy of a local directory."""
        target = Path(manager.base_dir) / "isolated_copy"

        info = manager.create_local_copy(
            source_path=local_git_repo,
            target_path=target,
        )

        assert info.path == target
        assert info.path.exists()
        assert (info.path / "main.py").exists()
        assert (info.path / ".git").exists()
        assert info.is_worktree is False
        assert info.commit_sha  # Should have a commit

    def test_create_local_copy_excludes_patterns(self, manager, local_git_repo):
        """Test that local copy excludes specified patterns."""
        # Create files that should be excluded
        (local_git_repo / "__pycache__").mkdir()
        (local_git_repo / "__pycache__" / "cache.pyc").write_text("cached")
        (local_git_repo / "node_modules").mkdir()
        (local_git_repo / "node_modules" / "pkg").write_text("package")
        (local_git_repo / ".env").write_text("SECRET=123")

        target = Path(manager.base_dir) / "isolated_copy"
        info = manager.create_local_copy(
            source_path=local_git_repo,
            target_path=target,
        )

        # Original files should exist
        assert (info.path / "main.py").exists()

        # Excluded patterns should not exist
        assert not (info.path / "__pycache__").exists()
        assert not (info.path / "node_modules").exists()
        assert not (info.path / ".env").exists()

    def test_commit_workspace_changes(self, manager, local_git_repo):
        """Test committing changes to a workspace."""
        target = Path(manager.base_dir) / "workspace"
        info = manager.create_local_copy(local_git_repo, target)

        # Make a change
        (target / "new_file.py").write_text("# new file\n")

        # Commit it
        new_sha = manager.commit_workspace_changes(target, "Add new file")

        assert new_sha is not None
        assert new_sha != info.commit_sha

    def test_commit_workspace_no_changes(self, manager, local_git_repo):
        """Test that committing with no changes returns None."""
        target = Path(manager.base_dir) / "workspace"
        manager.create_local_copy(local_git_repo, target)

        # No changes made
        result = manager.commit_workspace_changes(target, "No changes")
        assert result is None

    def test_get_diff_from_original(self, manager, local_git_repo):
        """Test getting diff from original state."""
        target = Path(manager.base_dir) / "workspace"
        manager.create_local_copy(local_git_repo, target)

        # Make a change
        (target / "main.py").write_text("print('modified')\n")
        subprocess.run(["git", "add", "."], cwd=target, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Modify main.py"],
            cwd=target,
            capture_output=True,
        )

        diff = manager.get_diff_from_original(target)

        assert "modified" in diff
        assert "-print('hello')" in diff
        assert "+print('modified')" in diff

    def test_cleanup_removes_worktrees(self, manager, local_git_repo):
        """Test that cleanup removes old worktrees."""
        target = Path(manager.base_dir) / "workspace"
        manager.create_local_copy(local_git_repo, target)

        assert target.exists()

        # Cleanup all
        removed = manager.cleanup_all()
        assert removed == 1
        assert not target.exists()


class TestUIRunConfigGitSource:
    """Tests for UIRunConfig with git source type."""

    def test_git_source_config_fields(self):
        """Test that git source fields are properly configured."""
        config = UIRunConfig(
            source_type="git",
            git_url="https://github.com/user/repo.git",
            git_branch="develop",
            git_workspace_path="/home/user/workspace",
        )

        assert config.source_type == "git"
        assert config.git_url == "https://github.com/user/repo.git"
        assert config.git_branch == "develop"
        assert config.git_workspace_path == "/home/user/workspace"

    def test_git_source_defaults(self):
        """Test default values for git source."""
        config = UIRunConfig(source_type="git")

        assert config.git_url == ""
        assert config.git_branch == "main"
        assert config.git_workspace_path == ""
        assert config.use_worktree is True

    def test_flatten_nested_config_git_source(self):
        """Test flattening nested config with git source."""
        nested = {
            "codebase": {
                "source_type": "git",
                "git_url": "https://github.com/test/repo.git",
                "git_branch": "feature",
                "git_workspace_path": "/tmp/workspace",
                "isolate_workspace": True,
            },
            "agent": {
                "agentic_mode": True,
                "backend": "codex",
            },
            "run": {
                "run_name": "test_run",
            },
        }

        flat = flatten_nested_config(nested)

        assert flat["source_type"] == "git"
        assert flat["git_url"] == "https://github.com/test/repo.git"
        assert flat["git_branch"] == "feature"
        assert flat["git_workspace_path"] == "/tmp/workspace"
        assert flat["use_worktree"] is True  # isolate_workspace maps to use_worktree


class TestGitCloneIntegration:
    """Integration tests for git clone functionality."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create a manager with temp directories."""
        return GitWorktreeManager(
            base_dir=str(tmp_path / "workspaces"),
            cache_dir=str(tmp_path / "cache"),
        )

    @pytest.mark.skipif(
        not shutil.which("git"),
        reason="Git not available",
    )
    def test_full_clone_from_local_bare_repo(self, manager, tmp_path):
        """Test full clone workflow with a local bare repo."""
        # Create a bare repo to simulate remote
        bare_repo = tmp_path / "bare.git"
        subprocess.run(["git", "init", "--bare", str(bare_repo)], capture_output=True, check=True)

        # Create a working repo and push to bare
        work_repo = tmp_path / "work"
        work_repo.mkdir()
        subprocess.run(["git", "init"], cwd=work_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=work_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=work_repo,
            capture_output=True,
        )
        (work_repo / "code.py").write_text("def main(): pass\n")
        subprocess.run(["git", "add", "."], cwd=work_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Init"],
            cwd=work_repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(bare_repo)],
            cwd=work_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "master"],
            cwd=work_repo,
            capture_output=True,
            check=True,
        )

        # Now clone from bare repo using manager
        info = manager.create_full_clone(
            git_url=str(bare_repo),
            branch="master",
            workspace_name="cloned_workspace",
        )

        assert info.path.exists()
        assert (info.path / "code.py").exists()
        assert info.repo_url == str(bare_repo)
        assert info.branch == "master"
        assert info.is_worktree is False
