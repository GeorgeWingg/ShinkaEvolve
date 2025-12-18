"""Integration tests for git-backed evolution storage.

These tests verify the full integration between runner.py and EvolutionGitManager,
ensuring that git commits are created during evolution runs and can be accessed
through the WebUI endpoints.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import pytest

from shinka.database.dbase import Program, ProgramDatabase, DatabaseConfig
from shinka.webui.git_worktree import EvolutionGitManager


class TestGitCommitGenerationHelper:
    """Tests for _git_commit_generation helper method."""

    @pytest.fixture
    def seed_workspace(self, tmp_path):
        """Create a seed workspace with test files."""
        workspace = tmp_path / "seed"
        workspace.mkdir()
        (workspace / "main.py").write_text("print('hello')\n")
        (workspace / "helper.py").write_text("def help(): pass\n")
        return workspace

    @pytest.fixture
    def git_manager(self, tmp_path):
        """Create a git manager for testing."""
        repo_path = tmp_path / "evolution.git"
        return EvolutionGitManager(repo_path, create=True)

    @pytest.fixture
    def mock_runner(self, tmp_path, git_manager, seed_workspace):
        """Create a mock runner with git_manager set up."""
        from shinka.core.runner import EvolutionRunner

        # Initialize git with seed
        initial_sha = git_manager.init_from_workspace(seed_workspace, "Initial", "seed-node")

        # Create a minimal mock runner
        runner = Mock(spec=EvolutionRunner)
        runner.git_manager = git_manager
        runner._initial_git_sha = initial_sha
        runner.verbose = True

        # Attach the real method from EvolutionRunner
        import types

        # Get the method and bind it to our mock
        runner._git_commit_generation = types.MethodType(
            EvolutionRunner._git_commit_generation,
            runner
        )

        return runner

    def test_git_commit_generation_creates_commit(
        self, mock_runner, git_manager, seed_workspace, tmp_path
    ):
        """Test that _git_commit_generation creates a commit in evolution.git."""
        # Create a generation directory with modified content
        gen_dir = tmp_path / "generation_1"
        gen_dir.mkdir()
        (gen_dir / "main.py").write_text("print('modified')\n")
        (gen_dir / "helper.py").write_text("def help(): return 'helped'\n")

        # Get parent SHA
        parent_sha = mock_runner._initial_git_sha

        # Call the method
        commit_sha = mock_runner._git_commit_generation(
            generation_dir=gen_dir,
            generation=1,
            node_uuid="test-node-001",
            parent_sha=parent_sha,
            message="Generation 1",
        )

        # Verify commit was created
        assert commit_sha is not None
        assert len(commit_sha) == 40
        assert all(c in "0123456789abcdef" for c in commit_sha)

        # Verify ref was created
        result = subprocess.run(
            ["git", "show-ref", "refs/shinka/nodes/test-node-001"],
            cwd=str(git_manager.repo_path),
            capture_output=True,
            text=True,
        )
        assert commit_sha in result.stdout

    def test_git_commit_generation_skips_results_dir(
        self, mock_runner, git_manager, seed_workspace, tmp_path
    ):
        """Test that _git_commit_generation skips results directories."""
        gen_dir = tmp_path / "generation_1"
        gen_dir.mkdir()
        (gen_dir / "main.py").write_text("print('modified')\n")

        # Create results directory that should be skipped
        results_dir = gen_dir / "results"
        results_dir.mkdir()
        (results_dir / "output.json").write_text('{"score": 0.5}')

        parent_sha = mock_runner._initial_git_sha

        commit_sha = mock_runner._git_commit_generation(
            generation_dir=gen_dir,
            generation=1,
            node_uuid="test-node-002",
            parent_sha=parent_sha,
        )

        # Verify commit was created
        assert commit_sha is not None

        # Verify results directory is NOT in the commit
        content = git_manager.get_file_contents(commit_sha)
        assert "output.json" not in content

    def test_git_commit_generation_returns_none_without_manager(self, tmp_path):
        """Test that _git_commit_generation returns None when git_manager is None."""
        from shinka.core.runner import EvolutionRunner
        import types

        runner = Mock(spec=EvolutionRunner)
        runner.git_manager = None  # No git manager
        runner.verbose = False

        runner._git_commit_generation = types.MethodType(
            EvolutionRunner._git_commit_generation,
            runner
        )

        gen_dir = tmp_path / "gen"
        gen_dir.mkdir()
        (gen_dir / "main.py").write_text("code")

        result = runner._git_commit_generation(
            generation_dir=gen_dir,
            generation=1,
            node_uuid="test",
            parent_sha="abc123",
        )

        assert result is None

    def test_git_commit_generation_handles_exception_gracefully(
        self, git_manager, tmp_path
    ):
        """Test that _git_commit_generation handles exceptions gracefully."""
        from shinka.core.runner import EvolutionRunner
        import types

        runner = Mock(spec=EvolutionRunner)
        runner.git_manager = git_manager
        runner.verbose = False

        runner._git_commit_generation = types.MethodType(
            EvolutionRunner._git_commit_generation,
            runner
        )

        gen_dir = tmp_path / "gen"
        gen_dir.mkdir()
        (gen_dir / "main.py").write_text("code")

        # Pass invalid parent SHA to trigger exception
        result = runner._git_commit_generation(
            generation_dir=gen_dir,
            generation=1,
            node_uuid="test",
            parent_sha="invalid_sha_that_does_not_exist",
        )

        # Should return None instead of raising
        assert result is None


class TestRefsProtection:
    """Test that refs protect commits from garbage collection."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create a git manager for testing."""
        repo_path = tmp_path / "evolution.git"
        return EvolutionGitManager(repo_path, create=True)

    @pytest.fixture
    def seed_workspace(self, tmp_path):
        """Create a seed workspace."""
        workspace = tmp_path / "seed"
        workspace.mkdir()
        (workspace / "main.py").write_text("print('hello')\n")
        return workspace

    def test_refs_survive_garbage_collection(self, manager, seed_workspace):
        """Test that refs/shinka/nodes/* refs survive git gc."""
        # Create initial commit with ref
        sha1 = manager.init_from_workspace(seed_workspace, "Init", "node-001")

        # Create child commit with ref
        with manager.mutation_context(sha1, "node-002") as worktree:
            (worktree.path / "main.py").write_text("print('v2')\n")
            sha2 = manager.commit_mutation(worktree, "v2", "node-002")

        # Run garbage collection
        subprocess.run(
            ["git", "gc", "--prune=now"],
            cwd=str(manager.repo_path),
            capture_output=True,
            check=True,
        )

        # Verify both refs still exist
        result = subprocess.run(
            ["git", "show-ref", "--verify", "refs/shinka/nodes/node-001"],
            cwd=str(manager.repo_path),
            capture_output=True,
        )
        assert result.returncode == 0

        result = subprocess.run(
            ["git", "show-ref", "--verify", "refs/shinka/nodes/node-002"],
            cwd=str(manager.repo_path),
            capture_output=True,
        )
        assert result.returncode == 0

        # Verify we can still read the content
        content = manager.get_file_contents(sha1, "main.py")
        assert "hello" in content

        content = manager.get_file_contents(sha2, "main.py")
        assert "v2" in content


class TestProgramGitIntegration:
    """Test Program metadata integration with git commits."""

    def test_program_stores_git_commit_sha(self, tmp_path):
        """Test that Program can store and retrieve git_commit_sha from metadata."""
        sha = "a" * 40  # Valid 40-char SHA

        program = Program(
            id="test-001",
            code="print('hello')",
            language="python",
            metadata={"git_commit_sha": sha},
        )

        assert program.get_commit_sha() == sha

    def test_program_retrieves_code_from_git(self, tmp_path):
        """Test that Program.get_code_content() can retrieve from git."""
        # Create git manager and workspace
        repo_path = tmp_path / "evolution.git"
        manager = EvolutionGitManager(repo_path, create=True)

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "main.py").write_text("print('from git')\n")

        sha = manager.init_from_workspace(workspace, "Init", "node-001")

        # Create program with empty code but SHA
        program = Program(
            id="test-002",
            code="",  # Empty!
            language="python",
            metadata={"git_commit_sha": sha},
        )

        # Should retrieve from git
        content = program.get_code_content(git_manager=manager)
        assert "from git" in content

    def test_database_stores_git_commit_sha(self, tmp_path):
        """Test that ProgramDatabase correctly stores and retrieves git_commit_sha."""
        config = DatabaseConfig(
            db_path=str(tmp_path / "test.sqlite"),
            num_islands=2,
            archive_size=50,
        )
        db = ProgramDatabase(config, embedding_model="")

        sha = "b" * 40

        program = Program(
            id="test-003",
            code="print('test')",
            language="python",
            generation=0,
            metadata={"git_commit_sha": sha},
        )

        db.add(program)
        db.save()

        # Retrieve and verify
        retrieved = db.get(program.id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("git_commit_sha") == sha
        assert retrieved.get_commit_sha() == sha

        db.close()


class TestWebUIGitEndpoints:
    """Test WebUI endpoint integration with git-backed storage."""

    @pytest.fixture
    def evolution_repo(self, tmp_path):
        """Create an evolution.git repo with some commits."""
        repo_path = tmp_path / "evolution.git"
        manager = EvolutionGitManager(repo_path, create=True)

        # Create seed workspace
        workspace = tmp_path / "seed"
        workspace.mkdir()
        (workspace / "main.py").write_text("print('v1')\n")
        sha1 = manager.init_from_workspace(workspace, "Initial seed", "node-001")

        # Create child commit
        with manager.mutation_context(sha1, "node-002") as worktree:
            (worktree.path / "main.py").write_text("print('v2')\n")
            sha2 = manager.commit_mutation(worktree, "Generation 1", "node-002")

        return {
            "manager": manager,
            "repo_path": repo_path,
            "node_001_sha": sha1,
            "node_002_sha": sha2,
        }

    def test_provision_worktree_from_evolution_git(self, evolution_repo, tmp_path):
        """Test that provision_worktree can create worktrees from evolution.git."""
        manager = evolution_repo["manager"]
        sha = evolution_repo["node_002_sha"]

        # Simulate provision_worktree behavior
        worktree = manager.checkout_for_mutation(sha)
        try:
            assert worktree.path.exists()
            assert (worktree.path / "main.py").exists()

            content = (worktree.path / "main.py").read_text()
            assert "v2" in content
        finally:
            manager.cleanup_mutation_worktree(worktree)

    def test_export_git_repo_includes_all_refs(self, evolution_repo, tmp_path):
        """Test that export_as_repo includes all node refs."""
        manager = evolution_repo["manager"]

        # Export
        export_path = tmp_path / "exported_repo"
        manager.export_as_repo(export_path)

        # Verify it's a valid git repo
        assert (export_path / ".git").exists()

        # Verify history
        result = subprocess.run(
            ["git", "log", "--all", "--oneline"],
            cwd=str(export_path),
            capture_output=True,
            text=True,
        )
        assert "Initial seed" in result.stdout or "Generation 1" in result.stdout

    def test_list_all_nodes_returns_all_commits(self, evolution_repo):
        """Test that list_all_nodes returns all committed nodes."""
        manager = evolution_repo["manager"]

        nodes = manager.list_all_nodes()
        uuids = [n["node_uuid"] for n in nodes]

        assert "node-001" in uuids
        assert "node-002" in uuids
        assert len(nodes) >= 2


class TestNoveltyJudgeGitIntegration:
    """Test NoveltyJudge can load code via git."""

    @pytest.fixture
    def git_backed_program(self, tmp_path):
        """Create a program with code stored in git."""
        repo_path = tmp_path / "evolution.git"
        manager = EvolutionGitManager(repo_path, create=True)

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "main.py").write_text(
            "def novel_function():\n    return 'unique_implementation'\n"
        )
        sha = manager.init_from_workspace(workspace, "Init", "novel-node")

        program = Program(
            id="novel-001",
            code="",  # Empty - stored in git
            language="python",
            metadata={"git_commit_sha": sha},
        )

        return {"manager": manager, "program": program, "sha": sha}

    def test_program_code_retrieval_for_novelty(self, git_backed_program):
        """Test that code can be retrieved from git for novelty scoring."""
        manager = git_backed_program["manager"]
        program = git_backed_program["program"]

        # Simulate what NoveltyJudge would do
        code = program.get_code_content(git_manager=manager)

        assert "novel_function" in code
        assert "unique_implementation" in code

    def test_get_file_contents_specific_file(self, git_backed_program):
        """Test retrieving a specific file from git commit."""
        manager = git_backed_program["manager"]
        sha = git_backed_program["sha"]

        content = manager.get_file_contents(sha, "main.py")
        assert "novel_function" in content


class TestGitBackedStorageE2E:
    """End-to-end tests for git-backed storage workflow."""

    def test_full_mutation_chain(self, tmp_path):
        """Test a full chain of mutations with git storage."""
        repo_path = tmp_path / "evolution.git"
        manager = EvolutionGitManager(repo_path, create=True)

        # Generation 0: Initial seed
        workspace = tmp_path / "gen0"
        workspace.mkdir()
        (workspace / "main.py").write_text("def main(): print('gen0')\n")
        sha0 = manager.init_from_workspace(workspace, "Gen 0", "node-gen0")

        # Generation 1: First mutation
        with manager.mutation_context(sha0, "node-gen1") as worktree:
            (worktree.path / "main.py").write_text("def main(): print('gen1')\n")
            sha1 = manager.commit_mutation(worktree, "Gen 1", "node-gen1")

        # Generation 2: Second mutation (from gen1)
        with manager.mutation_context(sha1, "node-gen2") as worktree:
            (worktree.path / "main.py").write_text("def main(): print('gen2')\n")
            sha2 = manager.commit_mutation(worktree, "Gen 2", "node-gen2")

        # Generation 2b: Branch from gen1
        with manager.mutation_context(sha1, "node-gen2b") as worktree:
            (worktree.path / "main.py").write_text("def main(): print('gen2b')\n")
            sha2b = manager.commit_mutation(worktree, "Gen 2b (branch)", "node-gen2b")

        # Verify all commits exist and have correct content
        assert "gen0" in manager.get_file_contents(sha0, "main.py")
        assert "gen1" in manager.get_file_contents(sha1, "main.py")
        assert "gen2" in manager.get_file_contents(sha2, "main.py")
        assert "gen2b" in manager.get_file_contents(sha2b, "main.py")

        # Verify all refs exist
        nodes = manager.list_all_nodes()
        uuids = [n["node_uuid"] for n in nodes]
        assert "node-gen0" in uuids
        assert "node-gen1" in uuids
        assert "node-gen2" in uuids
        assert "node-gen2b" in uuids

    def test_parallel_mutations_from_same_parent(self, tmp_path):
        """Test that parallel mutations from the same parent work correctly."""
        repo_path = tmp_path / "evolution.git"
        manager = EvolutionGitManager(repo_path, create=True)

        # Create seed
        workspace = tmp_path / "seed"
        workspace.mkdir()
        (workspace / "main.py").write_text("BASE\n")
        parent_sha = manager.init_from_workspace(workspace, "Seed", "parent")

        # Create two mutations in "parallel" (sequentially in test, but simulating parallel)
        shas = []
        for i in range(3):
            with manager.mutation_context(parent_sha, f"child-{i}") as worktree:
                (worktree.path / "main.py").write_text(f"CHILD {i}\n")
                sha = manager.commit_mutation(worktree, f"Child {i}", f"child-{i}")
                shas.append(sha)

        # All should have the same parent
        for i, sha in enumerate(shas):
            # Verify content
            content = manager.get_file_contents(sha, "main.py")
            assert f"CHILD {i}" in content

            # Verify parent relationship
            result = subprocess.run(
                ["git", "log", "--pretty=%P", "-n1", sha],
                cwd=str(manager.repo_path),
                capture_output=True,
                text=True,
            )
            assert parent_sha in result.stdout
