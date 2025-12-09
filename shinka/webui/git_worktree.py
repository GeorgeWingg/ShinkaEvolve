"""Git worktree manager for isolated evolution workspaces."""

import hashlib
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class WorktreeInfo:
    """Information about a created worktree or clone."""

    path: Path
    repo_url: str
    branch: str
    commit_sha: str
    is_worktree: bool  # True if worktree, False if full clone
    created_at: float


class GitWorktreeManager:
    """Manages Git worktrees for isolated evolution workspaces.

    Git worktrees allow creating multiple working directories from a single
    repository clone, avoiding the overhead of full clones for large repos.
    This is particularly useful for:
    - Large monorepos where full clones are expensive
    - Running multiple evolution experiments on different branches
    - Isolating experiment modifications from the main repo
    """

    def __init__(
        self,
        base_dir: str = "/tmp/shinka_workspaces",
        cache_dir: str = "/tmp/shinka_git_cache",
        max_cache_age_hours: int = 24,
    ):
        """Initialize the worktree manager.

        Args:
            base_dir: Directory where worktrees will be created
            cache_dir: Directory for cached bare clones
            max_cache_age_hours: Max age before refreshing cache
        """
        self.base_dir = Path(base_dir)
        self.cache_dir = Path(cache_dir)
        self.max_cache_age_hours = max_cache_age_hours
        self._worktrees: Dict[str, WorktreeInfo] = {}

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_repo_hash(self, git_url: str) -> str:
        """Generate a short hash for the repo URL."""
        return hashlib.sha256(git_url.encode()).hexdigest()[:12]

    def _get_repo_name(self, git_url: str) -> str:
        """Extract repo name from URL."""
        return git_url.rstrip("/").split("/")[-1].replace(".git", "")

    def _get_cached_repo_path(self, git_url: str) -> Path:
        """Get the path to the cached bare clone."""
        repo_hash = self._get_repo_hash(git_url)
        return self.cache_dir / f"repo_{repo_hash}.git"

    def _ensure_cached_clone(self, git_url: str, branch: str = "main") -> Path:
        """Ensure a bare clone exists in the cache.

        Args:
            git_url: The git repository URL
            branch: Branch to fetch

        Returns:
            Path to the cached bare repository
        """
        cache_path = self._get_cached_repo_path(git_url)

        if cache_path.exists():
            # Update the cached repo
            try:
                logger.info(f"Updating cached repo: {cache_path}")
                subprocess.run(
                    ["git", "fetch", "--all", "--prune"],
                    cwd=cache_path,
                    capture_output=True,
                    check=True,
                    timeout=300,
                )
            except subprocess.SubprocessError as e:
                logger.warning(f"Failed to update cache, recreating: {e}")
                shutil.rmtree(cache_path)

        if not cache_path.exists():
            # Create new bare clone with single branch
            logger.info(f"Creating bare clone of {git_url}")
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--bare",
                    "--single-branch",
                    "--branch",
                    branch,
                    git_url,
                    str(cache_path),
                ],
                capture_output=True,
                check=True,
                timeout=600,
            )

        return cache_path

    def _get_commit_sha(self, repo_path: Path) -> str:
        """Get the current HEAD commit SHA."""
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def create_worktree(
        self,
        git_url: str,
        branch: str = "main",
        workspace_name: Optional[str] = None,
    ) -> WorktreeInfo:
        """Create a new worktree from a Git repository.

        Uses a cached bare clone to avoid re-downloading the entire repo.

        Args:
            git_url: URL of the Git repository
            branch: Branch to checkout
            workspace_name: Optional name for the workspace directory

        Returns:
            WorktreeInfo with the created worktree details
        """
        # Ensure we have a cached clone
        cache_path = self._ensure_cached_clone(git_url, branch)

        # Generate workspace path
        if workspace_name:
            # Support both absolute paths and relative names
            workspace_path = Path(workspace_name)
            if workspace_path.is_absolute():
                worktree_path = workspace_path
            else:
                worktree_path = self.base_dir / workspace_name
        else:
            timestamp = int(time.time())
            repo_name = self._get_repo_name(git_url)
            worktree_path = self.base_dir / f"{repo_name}_{timestamp}"

        # Create parent directory if needed (for absolute paths)
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove existing if present
        if worktree_path.exists():
            self._remove_worktree(worktree_path)

        # Fetch the branch first to ensure it exists
        subprocess.run(
            ["git", "fetch", "origin", branch],
            cwd=cache_path,
            capture_output=True,
            timeout=120,
        )

        # Create the worktree
        subprocess.run(
            ["git", "worktree", "add", str(worktree_path), branch],
            cwd=cache_path,
            capture_output=True,
            check=True,
            timeout=120,
        )

        # Get the commit SHA
        commit_sha = self._get_commit_sha(worktree_path)

        info = WorktreeInfo(
            path=worktree_path,
            repo_url=git_url,
            branch=branch,
            commit_sha=commit_sha,
            is_worktree=True,
            created_at=time.time(),
        )

        self._worktrees[str(worktree_path)] = info
        logger.info(f"Created worktree at {worktree_path}")

        return info

    def create_full_clone(
        self,
        git_url: str,
        branch: str = "main",
        workspace_name: Optional[str] = None,
        depth: int = 1,
    ) -> WorktreeInfo:
        """Create a full (shallow) clone instead of worktree.

        Use this when worktrees are not supported or when complete
        isolation from the cache is needed.

        Args:
            git_url: URL of the Git repository
            branch: Branch to checkout
            workspace_name: Optional name for the workspace directory
            depth: Clone depth (1 for shallow, 0 for full)

        Returns:
            WorktreeInfo with the cloned repo details
        """
        if workspace_name:
            # Support both absolute paths and relative names
            workspace_path = Path(workspace_name)
            if workspace_path.is_absolute():
                clone_path = workspace_path
            else:
                clone_path = self.base_dir / workspace_name
        else:
            timestamp = int(time.time())
            repo_name = self._get_repo_name(git_url)
            clone_path = self.base_dir / f"{repo_name}_{timestamp}"

        # Create parent directory if needed (for absolute paths)
        clone_path.parent.mkdir(parents=True, exist_ok=True)

        if clone_path.exists():
            shutil.rmtree(clone_path)

        # Build clone command
        cmd = ["git", "clone", "--branch", branch]
        if depth > 0:
            cmd.extend(["--depth", str(depth)])
        cmd.extend([git_url, str(clone_path)])

        subprocess.run(cmd, capture_output=True, check=True, timeout=600)

        # Get commit SHA
        commit_sha = self._get_commit_sha(clone_path)

        info = WorktreeInfo(
            path=clone_path,
            repo_url=git_url,
            branch=branch,
            commit_sha=commit_sha,
            is_worktree=False,
            created_at=time.time(),
        )

        self._worktrees[str(clone_path)] = info
        logger.info(f"Created clone at {clone_path}")

        return info

    def _remove_worktree(self, path: Path) -> None:
        """Remove a worktree and clean up."""
        path_str = str(path)
        if path_str in self._worktrees:
            info = self._worktrees[path_str]
            if info.is_worktree:
                cache_path = self._get_cached_repo_path(info.repo_url)
                try:
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", str(path)],
                        cwd=cache_path,
                        capture_output=True,
                        timeout=60,
                    )
                except subprocess.SubprocessError:
                    pass
            del self._worktrees[path_str]

        if path.exists():
            shutil.rmtree(path)

    def cleanup(self, max_age_hours: Optional[int] = None) -> int:
        """Remove old worktrees.

        Args:
            max_age_hours: Max age in hours (uses default if not specified)

        Returns:
            Number of worktrees removed
        """
        max_age = max_age_hours or self.max_cache_age_hours
        cutoff = time.time() - (max_age * 3600)
        removed = 0

        for path_str, info in list(self._worktrees.items()):
            if info.created_at < cutoff:
                self._remove_worktree(Path(path_str))
                removed += 1

        return removed

    def cleanup_all(self) -> int:
        """Remove all managed worktrees.

        Returns:
            Number of worktrees removed
        """
        removed = 0
        for path_str in list(self._worktrees.keys()):
            self._remove_worktree(Path(path_str))
            removed += 1
        return removed

    def get_worktree_info(self, path: str) -> Optional[WorktreeInfo]:
        """Get info about a specific worktree.

        Args:
            path: Path to the worktree

        Returns:
            WorktreeInfo or None if not found
        """
        return self._worktrees.get(path)

    def list_worktrees(self) -> Dict[str, WorktreeInfo]:
        """List all managed worktrees.

        Returns:
            Dict mapping path to WorktreeInfo
        """
        return dict(self._worktrees)

    def prepare_workspace(
        self,
        git_url: str,
        branch: str = "main",
        use_worktree: bool = True,
        workspace_name: Optional[str] = None,
    ) -> WorktreeInfo:
        """Prepare a workspace for evolution.

        This is the main entry point that decides whether to use
        worktree or full clone based on the use_worktree flag.

        Args:
            git_url: URL of the Git repository
            branch: Branch to checkout
            use_worktree: Whether to use worktree (True) or full clone (False)
            workspace_name: Optional name for the workspace

        Returns:
            WorktreeInfo with the workspace details
        """
        if use_worktree:
            return self.create_worktree(git_url, branch, workspace_name)
        else:
            return self.create_full_clone(git_url, branch, workspace_name)

    def create_local_copy(
        self,
        source_path: Path,
        target_path: Path,
        exclude_patterns: Optional[list] = None,
    ) -> WorktreeInfo:
        """Create an isolated copy of a local directory for evolution.

        Copies the directory (excluding common non-essential files),
        initializes a fresh git repo, and commits the initial state.

        Args:
            source_path: Path to the source directory to copy
            target_path: Path where the isolated workspace will be created
            exclude_patterns: Optional list of patterns to exclude (uses defaults if None)

        Returns:
            WorktreeInfo with the workspace details
        """
        source_path = Path(source_path).resolve()
        target_path = Path(target_path).resolve()

        if not source_path.exists():
            raise ValueError(f"Source path does not exist: {source_path}")

        if not source_path.is_dir():
            raise ValueError(f"Source path is not a directory: {source_path}")

        # Default exclusion patterns
        if exclude_patterns is None:
            exclude_patterns = [
                '.git',
                'node_modules',
                '__pycache__',
                '.venv',
                'venv',
                '.env',
                '.env.*',
                '*.pyc',
                '*.pyo',
                '.DS_Store',
                '.idea',
                '.vscode',
                '*.egg-info',
                'dist',
                'build',
                '.pytest_cache',
                '.mypy_cache',
                '.ruff_cache',
                '*.so',
                '*.dylib',
                'results',  # Exclude results folder to avoid recursive copying
            ]

        def ignore_func(directory, files):
            """Custom ignore function for shutil.copytree."""
            ignored = set()
            for pattern in exclude_patterns:
                for f in files:
                    file_path = Path(directory) / f
                    # Check exact match or glob pattern
                    if f == pattern:
                        ignored.add(f)
                    elif pattern.startswith('*') and f.endswith(pattern[1:]):
                        ignored.add(f)
                    elif pattern.endswith('*') and f.startswith(pattern[:-1]):
                        ignored.add(f)
            return ignored

        # Remove target if it exists
        if target_path.exists():
            logger.info(f"Removing existing target: {target_path}")
            shutil.rmtree(target_path)

        # Copy the directory
        logger.info(f"Copying {source_path} to {target_path}")
        shutil.copytree(source_path, target_path, ignore=ignore_func)

        # Initialize git in the workspace
        logger.info(f"Initializing git in {target_path}")
        subprocess.run(
            ["git", "init"],
            cwd=target_path,
            capture_output=True,
            check=True,
        )

        # Configure git user for commits (use generic evolution user)
        subprocess.run(
            ["git", "config", "user.email", "shinka@evolution.local"],
            cwd=target_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Shinka Evolution"],
            cwd=target_path,
            capture_output=True,
        )

        # Add all files
        subprocess.run(
            ["git", "add", "-A"],
            cwd=target_path,
            capture_output=True,
            check=True,
        )

        # Check if there are files to commit
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=target_path,
            capture_output=True,
            text=True,
        )

        if status_result.stdout.strip():
            # There are files to commit
            commit_result = subprocess.run(
                ["git", "commit", "-m", "Initial state (copy from source)"],
                cwd=target_path,
                capture_output=True,
                text=True,
            )
            if commit_result.returncode != 0:
                logger.warning(f"Git commit failed: {commit_result.stderr}")
                # Try with --allow-empty as fallback
                subprocess.run(
                    ["git", "commit", "--allow-empty", "-m", "Initial state (empty workspace)"],
                    cwd=target_path,
                    capture_output=True,
                    check=True,
                )
        else:
            # No files to commit - create an empty initial commit
            logger.info(f"No files to commit in {target_path}, creating empty initial commit")
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "Initial state (empty workspace)"],
                cwd=target_path,
                capture_output=True,
                check=True,
            )

        # Get the commit SHA
        commit_sha = self._get_commit_sha(target_path)

        info = WorktreeInfo(
            path=target_path,
            repo_url=str(source_path),  # Store original source path
            branch="main",
            commit_sha=commit_sha,
            is_worktree=False,
            created_at=time.time(),
        )

        self._worktrees[str(target_path)] = info
        logger.info(f"Created isolated workspace at {target_path} (commit: {commit_sha[:8]})")

        return info

    def commit_workspace_changes(
        self,
        workspace_path: Path,
        message: str,
    ) -> Optional[str]:
        """Commit any changes in the workspace and return the commit SHA.

        Args:
            workspace_path: Path to the workspace
            message: Commit message

        Returns:
            Commit SHA if changes were committed, None if no changes
        """
        workspace_path = Path(workspace_path).resolve()

        # Check if there are any changes
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
        )

        if not result.stdout.strip():
            logger.info(f"No changes to commit in {workspace_path}")
            return None

        # Add all changes
        subprocess.run(
            ["git", "add", "-A"],
            cwd=workspace_path,
            capture_output=True,
            check=True,
        )

        # Commit
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=workspace_path,
            capture_output=True,
            check=True,
        )

        # Get commit SHA
        commit_sha = self._get_commit_sha(workspace_path)
        logger.info(f"Committed changes in {workspace_path}: {commit_sha[:8]}")

        return commit_sha

    def get_diff_from_original(
        self,
        workspace_path: Path,
        commit_sha: Optional[str] = None,
    ) -> str:
        """Get the diff between the original state and current/specified commit.

        Args:
            workspace_path: Path to the workspace
            commit_sha: Optional specific commit to diff (defaults to HEAD)

        Returns:
            Git diff output as string
        """
        workspace_path = Path(workspace_path).resolve()

        # Get the first commit (original state)
        result = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=True,
        )
        first_commit = result.stdout.strip()

        # Get target commit
        target = commit_sha or "HEAD"

        # Get the diff
        result = subprocess.run(
            ["git", "diff", first_commit, target],
            cwd=workspace_path,
            capture_output=True,
            text=True,
        )

        return result.stdout

    def export_patch(
        self,
        workspace_path: Path,
        commit_sha: str,
        output_path: Path,
    ) -> Path:
        """Export a commit as a patch file.

        Args:
            workspace_path: Path to the workspace
            commit_sha: Commit SHA to export
            output_path: Directory to save the patch file

        Returns:
            Path to the generated patch file
        """
        workspace_path = Path(workspace_path).resolve()
        output_path = Path(output_path).resolve()
        output_path.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            ["git", "format-patch", "-1", commit_sha, "-o", str(output_path)],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=True,
        )

        # format-patch outputs the filename
        patch_filename = result.stdout.strip()
        return Path(patch_filename)
