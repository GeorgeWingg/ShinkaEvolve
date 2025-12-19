"""Security tests for path traversal, SHA injection, and TOCTOU vulnerabilities."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestPathTraversalBlocked:
    """Tests for path traversal vulnerability in session log endpoint."""

    def test_path_traversal_with_dotdot_returns_403(self, tmp_path):
        """Verify that ../../../etc/passwd style paths return 403 Forbidden."""
        from shinka.database import DatabaseConfig, ProgramDatabase, Program
        from shinka.webui.visualization import DatabaseRequestHandler

        run_root = tmp_path / "run"
        run_root.mkdir()
        db_path = run_root / "evolution_db.sqlite"

        # Create a legitimate log file
        log_path = run_root / "agent_sessions" / "s1" / "session_log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text('{"type":"init"}\n', encoding="utf-8")

        # Store a malicious path in metadata
        config = DatabaseConfig(db_path=str(db_path))
        db = ProgramDatabase(config, embedding_model="", read_only=False)
        program = Program(
            id="prog-malicious",
            code="print('hi')",
            language="python",
            generation=0,
            combined_score=1.0,
            correct=True,
            metadata={"agent_session_log_path": "../../../etc/passwd"},
        )
        db.add(program)
        db.save()
        db.conn.close()

        class DummyHandler(DatabaseRequestHandler):
            def __init__(self, search_root):
                self.search_root = search_root
                self.response_data = None
                self.error = None

            def send_json_response(self, data):
                self.response_data = data

            def send_error(self, code, message=None):
                self.error = (code, message)

        handler = DummyHandler(search_root=str(tmp_path))
        handler.handle_get_agent_session_log(
            {
                "db_path": [str(db_path)],
                "program_id": ["prog-malicious"],
            }
        )

        # Should return 403 Forbidden, not the file contents
        assert handler.error is not None
        assert handler.error[0] == 403
        assert "outside run directory" in handler.error[1]

    def test_path_traversal_with_absolute_path_returns_403(self, tmp_path):
        """Verify that absolute paths outside run_dir return 403."""
        from shinka.database import DatabaseConfig, ProgramDatabase, Program
        from shinka.webui.visualization import DatabaseRequestHandler

        run_root = tmp_path / "run"
        run_root.mkdir()
        db_path = run_root / "evolution_db.sqlite"

        config = DatabaseConfig(db_path=str(db_path))
        db = ProgramDatabase(config, embedding_model="", read_only=False)
        program = Program(
            id="prog-absolute",
            code="print('hi')",
            language="python",
            generation=0,
            combined_score=1.0,
            correct=True,
            metadata={"agent_session_log_path": "/etc/passwd"},
        )
        db.add(program)
        db.save()
        db.conn.close()

        class DummyHandler(DatabaseRequestHandler):
            def __init__(self, search_root):
                self.search_root = search_root
                self.response_data = None
                self.error = None

            def send_json_response(self, data):
                self.response_data = data

            def send_error(self, code, message=None):
                self.error = (code, message)

        handler = DummyHandler(search_root=str(tmp_path))
        handler.handle_get_agent_session_log(
            {
                "db_path": [str(db_path)],
                "program_id": ["prog-absolute"],
            }
        )

        # Should return 403 or 404, not file contents
        assert handler.error is not None
        # Could be 403 (blocked) or 404 (not found after resolution)
        assert handler.error[0] in (403, 404)


class TestShaValidation:
    """Tests for git SHA validation."""

    def test_valid_sha_accepted(self):
        """Verify that valid 40-char hex SHAs are accepted."""
        from shinka.webui.git_worktree import _validate_git_sha

        valid_shas = [
            "a" * 40,
            "0123456789abcdef0123456789abcdef01234567",
            "ABCDEF0123456789abcdef0123456789abcdef01",  # Mixed case
        ]
        for sha in valid_shas:
            assert _validate_git_sha(sha), f"Valid SHA rejected: {sha}"

    def test_invalid_sha_rejected(self):
        """Verify that invalid SHAs are rejected."""
        from shinka.webui.git_worktree import _validate_git_sha

        invalid_shas = [
            "INVALID",
            "HEAD",
            "main",
            "a" * 39,  # Too short
            "a" * 41,  # Too long
            "g" * 40,  # Non-hex characters
            "$(rm -rf /)",
            "refs/heads/main",
            "",
        ]
        for sha in invalid_shas:
            assert not _validate_git_sha(sha), f"Invalid SHA accepted: {sha}"

    def test_get_file_contents_rejects_invalid_sha(self, tmp_path):
        """Verify that get_file_contents raises ValueError for invalid SHA."""
        from shinka.webui.git_worktree import EvolutionGitManager

        # Initialize a bare git repo for EvolutionGitManager
        repo_path = tmp_path / "evolution.git"

        manager = EvolutionGitManager(repo_path, create=True)

        with pytest.raises(ValueError) as exc_info:
            manager.get_file_contents("INVALID_SHA")

        assert "Invalid git SHA format" in str(exc_info.value)

    def test_checkout_for_mutation_rejects_invalid_sha(self, tmp_path):
        """Verify that checkout_for_mutation raises ValueError for invalid SHA."""
        from shinka.webui.git_worktree import EvolutionGitManager

        repo_path = tmp_path / "evolution.git"
        manager = EvolutionGitManager(repo_path, create=True)

        with pytest.raises(ValueError) as exc_info:
            manager.checkout_for_mutation("HEAD~1")  # Not a valid 40-char SHA

        assert "Invalid git SHA format" in str(exc_info.value)


class TestNodeIdValidation:
    """Tests for node ID validation (accepts UUIDs and simple IDs like 'node-001')."""

    def test_valid_node_ids_accepted(self):
        """Verify that valid node IDs are accepted."""
        from shinka.webui.git_worktree import _validate_node_id

        valid_ids = [
            "node-001",  # Simple alphanumeric with dash
            "node_002",  # Simple alphanumeric with underscore
            "12345678-1234-1234-1234-123456789abc",  # Full UUID
            "ABCDEF12-3456-7890-ABCD-EF1234567890",  # Uppercase UUID
            "gen-1-mutation-5",  # Complex node ID
            "a",  # Single character
            "A" * 64,  # Max length
        ]
        for node_id in valid_ids:
            assert _validate_node_id(node_id), f"Valid node ID rejected: {node_id}"

    def test_invalid_node_ids_rejected(self):
        """Verify that invalid node IDs are rejected."""
        from shinka.webui.git_worktree import _validate_node_id

        invalid_ids = [
            "",  # Empty
            "A" * 65,  # Too long
            "node.001",  # Contains dot
            "node/001",  # Contains slash
            "$(rm -rf /)",  # Command injection
            "node 001",  # Contains space
            "../../../etc/passwd",  # Path traversal
        ]
        for node_id in invalid_ids:
            assert not _validate_node_id(node_id), f"Invalid node ID accepted: {node_id}"


class TestBranchValidation:
    """Tests for git branch name validation."""

    def test_valid_branches_accepted(self):
        """Verify that valid branch names are accepted."""
        from shinka.webui.git_worktree import _validate_branch

        valid_branches = [
            "main",
            "master",
            "feature/new-thing",
            "release-1.0",
            "fix_bug_123",
            "refs/heads/main",
        ]
        for branch in valid_branches:
            assert _validate_branch(branch), f"Valid branch rejected: {branch}"

    def test_invalid_branches_rejected(self):
        """Verify that invalid/malicious branch names are rejected."""
        from shinka.webui.git_worktree import _validate_branch

        invalid_branches = [
            "",  # Empty
            "--malicious",  # Option injection
            "-n",  # Short option injection
            "../../../etc",  # Path traversal
            "branch..name",  # Double dot
            "A" * 257,  # Too long
        ]
        for branch in invalid_branches:
            assert not _validate_branch(branch), f"Invalid branch accepted: {branch}"


class TestWorktreeSymlinkProtection:
    """Tests for TOCTOU protection in worktree cleanup."""

    def test_symlink_not_followed_in_cleanup(self, tmp_path):
        """Verify that symlinks are not followed during worktree cleanup."""
        from shinka.webui.git_worktree import EvolutionGitManager, MutationWorktree

        # Create a bare git repo
        repo_path = tmp_path / "evolution.git"
        manager = EvolutionGitManager(repo_path, create=True)

        # Create a target directory that should NOT be deleted
        target_dir = tmp_path / "important_data"
        target_dir.mkdir()
        important_file = target_dir / "important.txt"
        important_file.write_text("DO NOT DELETE")

        # Create a worktree path that is a symlink to the target
        worktree_path = tmp_path / "worktree"
        worktree_path.symlink_to(target_dir)

        # Create a mock worktree object pointing to the symlink
        worktree = MutationWorktree(
            path=worktree_path,
            worktree_id="test-worktree",
            parent_sha="a" * 40,
            is_shared_clone=True,  # Skip git worktree remove
        )

        # Cleanup should skip the symlink
        manager.cleanup_mutation_worktree(worktree)

        # Verify the target directory was NOT deleted
        assert target_dir.exists(), "Target directory was deleted through symlink!"
        assert important_file.exists(), "Important file was deleted through symlink!"
        assert important_file.read_text() == "DO NOT DELETE"

    def test_regular_directory_deleted_in_cleanup(self, tmp_path):
        """Verify that regular directories ARE deleted during cleanup."""
        from shinka.webui.git_worktree import EvolutionGitManager, MutationWorktree

        # Create a bare git repo
        repo_path = tmp_path / "evolution.git"
        manager = EvolutionGitManager(repo_path, create=True)

        # Create a regular worktree directory
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        (worktree_path / "file.txt").write_text("test")

        worktree = MutationWorktree(
            path=worktree_path,
            worktree_id="test-worktree",
            parent_sha="a" * 40,
            is_shared_clone=True,
        )

        manager.cleanup_mutation_worktree(worktree)

        # Regular directory should be deleted
        assert not worktree_path.exists(), "Regular worktree directory was not deleted"
