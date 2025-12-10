"""Tests for git-backed evolution storage (GIT_WORKTREE_EXECPLAN.md).

Milestone 1 & 2 tests covering:
- EvolutionGitManager operations
- refs/shinka/nodes/* creation and gc protection
- MutationContext cleanup on exception
- get_code_content() abstraction for both legacy and git-backed nodes
- Cross-platform temp directory usage
- Windows symlink fallback (mocked)
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shinka.database.dbase import Program
from shinka.webui.git_worktree import (
    EvolutionGitManager,
    MutationWorktree,
    _get_cross_platform_temp_base,
)


class TestCrossPlatformTempBase:
    """Test cross-platform temp directory handling."""

    def test_returns_path_object(self):
        """Should return a Path object."""
        result = _get_cross_platform_temp_base()
        assert isinstance(result, Path)

    def test_directory_exists(self):
        """Should create the directory if it doesn't exist."""
        result = _get_cross_platform_temp_base()
        assert result.exists()
        assert result.is_dir()

    def test_uses_system_temp(self):
        """Should be under the system temp directory."""
        result = _get_cross_platform_temp_base()
        system_temp = Path(tempfile.gettempdir())
        # The result should start with or be within the system temp
        assert str(result).startswith(str(system_temp))

    def test_has_shinka_prefix(self):
        """Should have shinka_mutation_worktrees in the path."""
        result = _get_cross_platform_temp_base()
        assert "shinka_mutation_worktrees" in str(result)


class TestEvolutionGitManagerInit:
    """Test EvolutionGitManager initialization."""

    def test_creates_bare_repo(self, tmp_path):
        """Should create a bare git repository."""
        repo_path = tmp_path / "test_evolution.git"
        manager = EvolutionGitManager(repo_path, create=True)

        assert repo_path.exists()
        # Bare repos have HEAD file directly in the repo
        assert (repo_path / "HEAD").exists()

    def test_configures_gc_auto_zero(self, tmp_path):
        """Should configure gc.auto=0 to prevent garbage collection."""
        repo_path = tmp_path / "test_evolution.git"
        EvolutionGitManager(repo_path, create=True)

        result = subprocess.run(
            ["git", "config", "gc.auto"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "0"

    def test_configures_autocrlf_false(self, tmp_path):
        """Should configure core.autocrlf=false for line ending consistency."""
        repo_path = tmp_path / "test_evolution.git"
        EvolutionGitManager(repo_path, create=True)

        result = subprocess.run(
            ["git", "config", "core.autocrlf"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "false"

    def test_opens_existing_repo(self, tmp_path):
        """Should open an existing repo without recreating it."""
        repo_path = tmp_path / "test_evolution.git"
        
        # Create first
        EvolutionGitManager(repo_path, create=True)
        
        # Add a marker file
        marker = repo_path / "test_marker"
        marker.write_text("exists")
        
        # Open again
        EvolutionGitManager(repo_path, create=True)
        
        # Marker should still exist
        assert marker.exists()


class TestEvolutionGitManagerWorkflow:
    """Test the full mutation workflow."""

    @pytest.fixture
    def seed_workspace(self, tmp_path):
        """Create a seed workspace with test files."""
        workspace = tmp_path / "seed"
        workspace.mkdir()
        (workspace / "main.py").write_text("print('hello')\n")
        (workspace / "helper.py").write_text("def help(): pass\n")
        return workspace

    @pytest.fixture
    def manager(self, tmp_path):
        """Create a git manager for testing."""
        repo_path = tmp_path / "evolution.git"
        return EvolutionGitManager(repo_path, create=True)

    def test_init_from_workspace(self, manager, seed_workspace):
        """Should create initial commit from workspace."""
        sha = manager.init_from_workspace(seed_workspace, "Initial seed", "node-001")

        # SHA should be 40 hex characters
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

        # Node ref should exist
        result = subprocess.run(
            ["git", "show-ref", "refs/shinka/nodes/node-001"],
            cwd=str(manager.repo_path),
            capture_output=True,
            text=True,
        )
        assert sha in result.stdout

    def test_checkout_for_mutation(self, manager, seed_workspace):
        """Should create a worktree for mutation."""
        initial_sha = manager.init_from_workspace(seed_workspace)
        
        worktree = manager.checkout_for_mutation(initial_sha)
        
        try:
            assert worktree.path.exists()
            assert worktree.parent_sha == initial_sha
            assert (worktree.path / "main.py").exists()
        finally:
            manager.cleanup_mutation_worktree(worktree)

    def test_commit_mutation_creates_ref(self, manager, seed_workspace):
        """Should create ref for the new commit."""
        initial_sha = manager.init_from_workspace(seed_workspace)
        
        worktree = manager.checkout_for_mutation(initial_sha)
        try:
            # Make a change
            (worktree.path / "main.py").write_text("print('modified')\n")
            
            # Commit
            new_sha = manager.commit_mutation(worktree, "Test mutation", "node-002")
            
            # Verify ref exists
            result = subprocess.run(
                ["git", "show-ref", "refs/shinka/nodes/node-002"],
                cwd=str(manager.repo_path),
                capture_output=True,
                text=True,
            )
            assert new_sha in result.stdout
        finally:
            manager.cleanup_mutation_worktree(worktree)

    def test_mutation_context_cleanup_on_success(self, manager, seed_workspace):
        """Context manager should clean up worktree on normal exit."""
        initial_sha = manager.init_from_workspace(seed_workspace)
        worktree_path = None
        
        with manager.mutation_context(initial_sha, "node-003") as worktree:
            worktree_path = worktree.path
            assert worktree_path.exists()
        
        # Should be cleaned up after context exits
        assert not worktree_path.exists()

    def test_mutation_context_cleanup_on_exception(self, manager, seed_workspace):
        """Context manager should clean up worktree even on exception."""
        initial_sha = manager.init_from_workspace(seed_workspace)
        worktree_path = None
        
        with pytest.raises(RuntimeError):
            with manager.mutation_context(initial_sha, "node-004") as worktree:
                worktree_path = worktree.path
                assert worktree_path.exists()
                raise RuntimeError("Simulated agent crash")
        
        # Should still be cleaned up
        assert not worktree_path.exists()

    def test_get_file_contents_single_file(self, manager, seed_workspace):
        """Should retrieve a single file's contents."""
        initial_sha = manager.init_from_workspace(seed_workspace)
        
        content = manager.get_file_contents(initial_sha, "main.py")
        assert content == "print('hello')\n"

    def test_get_file_contents_all_files(self, manager, seed_workspace):
        """Should retrieve all files as corpus."""
        initial_sha = manager.init_from_workspace(seed_workspace)
        
        content = manager.get_file_contents(initial_sha)
        assert "=== FILE: main.py ===" in content
        assert "=== FILE: helper.py ===" in content
        assert "print('hello')" in content
        assert "def help():" in content

    def test_export_as_repo(self, manager, seed_workspace, tmp_path):
        """Should export as a complete repository."""
        initial_sha = manager.init_from_workspace(seed_workspace, node_uuid="node-001")
        
        # Add another commit
        with manager.mutation_context(initial_sha, "node-002") as worktree:
            (worktree.path / "main.py").write_text("print('v2')\n")
            manager.commit_mutation(worktree, "Version 2", "node-002")
        
        # Export
        export_path = tmp_path / "export"
        manager.export_as_repo(export_path)
        
        # Verify it's a valid git repo
        assert (export_path / ".git").exists()
        
        # Verify we can see the history
        result = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=str(export_path),
            capture_output=True,
            text=True,
        )
        assert "Initial seed" in result.stdout or "Version 2" in result.stdout

    def test_list_all_nodes(self, manager, seed_workspace):
        """Should list all node refs."""
        sha1 = manager.init_from_workspace(seed_workspace, node_uuid="node-aaa")
        
        with manager.mutation_context(sha1, "node-bbb") as worktree:
            (worktree.path / "main.py").write_text("v2\n")
            manager.commit_mutation(worktree, "v2", "node-bbb")
        
        nodes = manager.list_all_nodes()
        uuids = [n["node_uuid"] for n in nodes]
        
        assert "node-aaa" in uuids
        assert "node-bbb" in uuids


class TestProgramGetCodeContent:
    """Test Program.get_code_content() data access abstraction."""

    def test_returns_code_blob_when_present(self):
        """Should return code field if it has content (legacy mode)."""
        program = Program(
            id="test-001",
            code="print('hello')",
            language="python",
        )
        
        result = program.get_code_content()
        assert result == "print('hello')"

    def test_uses_git_manager_when_code_empty(self, tmp_path):
        """Should use git manager when code is empty but SHA exists."""
        # Create a real git repo for this test
        repo_path = tmp_path / "test.git"
        manager = EvolutionGitManager(repo_path, create=True)
        
        # Initialize with some content
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "main.py").write_text("git content\n")
        sha = manager.init_from_workspace(workspace)
        
        # Create program with empty code but SHA in metadata
        program = Program(
            id="test-002",
            code="",  # Empty!
            language="python",
            metadata={"git_commit_sha": sha},
        )
        
        result = program.get_code_content(git_manager=manager)
        assert "git content" in result

    def test_raises_when_no_code_available(self):
        """Should raise ValueError when neither blob nor SHA available."""
        program = Program(
            id="test-003",
            code="",
            language="python",
            metadata={},  # No SHA
        )
        
        with pytest.raises(ValueError, match="No code available"):
            program.get_code_content()

    def test_prefers_blob_over_git(self, tmp_path):
        """Should prefer code blob even if SHA is also present."""
        repo_path = tmp_path / "test.git"
        manager = EvolutionGitManager(repo_path, create=True)
        
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "main.py").write_text("git version\n")
        sha = manager.init_from_workspace(workspace)
        
        program = Program(
            id="test-004",
            code="blob version",  # Has content
            language="python",
            metadata={"git_commit_sha": sha},
        )
        
        result = program.get_code_content(git_manager=manager)
        assert result == "blob version"  # Should use blob, not git


class TestProgramCommitShaHelpers:
    """Test Program commit SHA helper methods."""

    def test_get_commit_sha_returns_none_when_missing(self):
        """Should return None when no SHA in metadata."""
        program = Program(id="test", code="x", metadata={})
        assert program.get_commit_sha() is None

    def test_get_commit_sha_returns_value(self):
        """Should return SHA when present."""
        sha = "abc123" * 6 + "abcd"  # 40 chars
        program = Program(id="test", code="x", metadata={"git_commit_sha": sha})
        assert program.get_commit_sha() == sha

    def test_set_commit_sha(self):
        """Should set SHA in metadata."""
        program = Program(id="test", code="x", metadata={})
        sha = "def456" * 6 + "defg"
        program.set_commit_sha(sha)
        assert program.metadata["git_commit_sha"] == sha


class TestWindowsSymlinkFallback:
    """Test Windows symlink fallback behavior."""

    def test_fallback_to_shared_clone_on_symlink_error(self, tmp_path):
        """Should fall back to --shared clone when worktree fails with symlink error."""
        repo_path = tmp_path / "test.git"
        manager = EvolutionGitManager(repo_path, create=True)
        
        # Initialize repo
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "main.py").write_text("test\n")
        sha = manager.init_from_workspace(workspace)
        
        # Mock subprocess.run to simulate symlink failure on worktree add
        original_run = subprocess.run
        worktree_add_called = [False]
        clone_shared_called = [False]
        
        def mock_run(cmd, *args, **kwargs):
            if isinstance(cmd, list):
                cmd_str = " ".join(cmd)
                # Fail the worktree add with symlink error
                if "worktree" in cmd_str and "add" in cmd_str:
                    worktree_add_called[0] = True
                    error = subprocess.CalledProcessError(1, cmd)
                    error.stderr = "error: unable to create symlink"
                    raise error
                # Track if clone --shared was called
                if "clone" in cmd_str and "--shared" in cmd_str:
                    clone_shared_called[0] = True
            
            return original_run(cmd, *args, **kwargs)
        
        with patch("shinka.webui.git_worktree.subprocess.run", side_effect=mock_run):
            # This should trigger fallback to --shared clone
            worktree = manager.checkout_for_mutation(sha)
            
            try:
                # Verify the fallback was used
                assert worktree_add_called[0], "git worktree add should have been called"
                assert clone_shared_called[0], "git clone --shared should have been called as fallback"
                assert worktree.is_shared_clone, "Worktree should be marked as shared clone"
                assert worktree.path.exists(), "Worktree path should exist"
            finally:
                manager.cleanup_mutation_worktree(worktree)


class TestGarbageCollectionProtection:
    """Test that refs prevent garbage collection."""

    def test_refs_survive_gc(self, tmp_path):
        """Commits with refs should survive garbage collection."""
        repo_path = tmp_path / "test.git"
        manager = EvolutionGitManager(repo_path, create=True)
        
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "main.py").write_text("test\n")
        sha = manager.init_from_workspace(workspace, node_uuid="protected-node")
        
        # Run garbage collection
        subprocess.run(
            ["git", "gc", "--prune=now"],
            cwd=str(repo_path),
            capture_output=True,
        )
        
        # Verify the commit still exists via ref
        result = subprocess.run(
            ["git", "rev-parse", "refs/shinka/nodes/protected-node"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert sha in result.stdout
