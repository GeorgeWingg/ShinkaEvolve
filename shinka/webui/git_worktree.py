"""Git worktree manager for isolated evolution workspaces."""

import hashlib
import logging
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

try:
    from filelock import FileLock
except ImportError:
    # Fallback for environments without filelock
    FileLock = None  # type: ignore

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


# =============================================================================
# Evolution Git Manager (Milestone 1-2 of GIT_WORKTREE_EXECPLAN.md)
# =============================================================================


@dataclass
class MutationWorktree:
    """Represents an ephemeral worktree created for a single mutation.

    This worktree is temporary - it exists only while the agent is running
    and is cleaned up immediately after the mutation is committed.

    Attributes:
        path: Filesystem path to the worktree directory
        parent_sha: The commit SHA of the parent node (what we checked out from)
        worktree_id: Unique identifier for this worktree (UUID)
        is_shared_clone: True if this is a --shared clone (Windows fallback)
    """

    path: Path
    parent_sha: str
    worktree_id: str
    is_shared_clone: bool = False


def _get_cross_platform_temp_base() -> Path:
    """Get a cross-platform temporary directory for mutation worktrees.

    Uses tempfile.gettempdir() which returns:
    - Linux: /tmp (or $TMPDIR)
    - macOS: /var/folders/... or /tmp (or $TMPDIR)
    - Windows: C:\\Users\\<user>\\AppData\\Local\\Temp (or %TEMP%)

    Returns:
        Path to the base directory for mutation worktrees.
    """
    base = Path(tempfile.gettempdir()) / "shinka_mutation_worktrees"
    base.mkdir(parents=True, exist_ok=True)
    return base


class EvolutionGitManager:
    """Manages the git repository for an evolution run.

    Creates one bare git repository per evolution run. Each mutation becomes
    a commit, with refs/shinka/nodes/<node_uuid> keeping all nodes reachable
    for visualization and export.

    The repository is stored as a bare repo (no working directory) to save
    space. Working directories are created on-demand as worktrees for:
    1. Mutation operations (ephemeral, cleaned up after commit)
    2. User inspection (persistent until explicitly removed)

    Thread Safety:
        Git operations are protected by a file lock to prevent concurrent
        index.lock contention when multiple agents finish simultaneously.

    Cross-Platform:
        - Uses pathlib.Path for all paths
        - Uses tempfile.gettempdir() for temp directories
        - Falls back to --shared clone on Windows if worktrees fail

    Example:
        # Initialize for an evolution run
        repo_path = Path("results/task/run/evolution.git")
        manager = EvolutionGitManager(repo_path)

        # Initialize with seed files
        initial_sha = manager.init_from_workspace(seed_workspace)

        # Perform a mutation
        with manager.mutation_context(parent_sha, node_uuid) as worktree:
            # Agent runs in worktree.path
            # ... agent makes changes ...
            new_sha = manager.commit_mutation(worktree, "Gen 1: improve", node_uuid)
        # worktree automatically cleaned up

        # Export for user download
        manager.export_as_repo(Path("/tmp/evolution_export"))
    """

    def __init__(
        self,
        repo_path: Path,
        create: bool = True,
        worktree_base: Optional[Path] = None,
    ):
        """Initialize or open the evolution git repository.

        Args:
            repo_path: Path to the bare git repo (e.g., results/task/run/evolution.git)
            create: If True, create the repo if it doesn't exist
            worktree_base: Override base directory for mutation worktrees.
                          Uses cross-platform temp dir if None.

        On creation, configures:
        - gc.auto=0: Prevent aggressive garbage collection during runs
        - core.autocrlf=false: Prevent line ending corruption
        """
        self.repo_path = Path(repo_path).resolve()
        self.worktree_base = worktree_base or _get_cross_platform_temp_base()
        self._lock: Optional[Any] = None  # FileLock instance

        if create and not self.repo_path.exists():
            self._init_bare_repo()

        # Initialize file lock for concurrent git operations
        if FileLock is not None:
            lock_path = self.repo_path / "shinka_evolution.lock"
            self._lock = FileLock(str(lock_path), timeout=300)  # 5 min timeout

    def _init_bare_repo(self) -> None:
        """Create a new bare git repository with proper configuration."""
        self.repo_path.mkdir(parents=True, exist_ok=True)

        # Initialize bare repository
        subprocess.run(
            ["git", "init", "--bare"],
            cwd=str(self.repo_path),
            capture_output=True,
            check=True,
            text=True,
        )

        # Configure to prevent garbage collection of unreferenced commits
        subprocess.run(
            ["git", "config", "gc.auto", "0"],
            cwd=str(self.repo_path),
            capture_output=True,
            check=True,
        )

        # Prevent line ending issues on Windows
        subprocess.run(
            ["git", "config", "core.autocrlf", "false"],
            cwd=str(self.repo_path),
            capture_output=True,
            check=True,
        )

        logger.info(f"Initialized bare evolution repo at {self.repo_path}")

    def _acquire_lock(self) -> None:
        """Acquire the file lock for git operations."""
        if self._lock is not None:
            self._lock.acquire()

    def _release_lock(self) -> None:
        """Release the file lock."""
        if self._lock is not None:
            try:
                self._lock.release()
            except Exception:
                pass  # Lock may not be held

    @contextmanager
    def _git_lock(self) -> Generator[None, None, None]:
        """Context manager for locked git operations."""
        self._acquire_lock()
        try:
            yield
        finally:
            self._release_lock()

    def init_from_workspace(
        self,
        workspace_path: Path,
        message: str = "Initial seed",
        node_uuid: Optional[str] = None,
    ) -> str:
        """Initialize the repo with contents from a workspace directory.

        Creates the initial commit from the seed files. This should be called
        once when starting an evolution run.

        Args:
            workspace_path: Path to directory containing the seed files
            message: Commit message for the initial commit
            node_uuid: Optional UUID for the initial node (creates ref)

        Returns:
            The initial commit SHA (40 characters)
        """
        workspace_path = Path(workspace_path).resolve()
        if not workspace_path.exists():
            raise ValueError(f"Workspace path does not exist: {workspace_path}")

        with self._git_lock():
            # Create a temporary worktree to stage the initial files
            temp_id = str(uuid.uuid4())
            temp_worktree = self.worktree_base / f"init_{temp_id}"

            try:
                temp_worktree.mkdir(parents=True, exist_ok=True)

                # Initialize a temporary repo in the worktree
                subprocess.run(
                    ["git", "init"],
                    cwd=str(temp_worktree),
                    capture_output=True,
                    check=True,
                )

                # Configure git user
                subprocess.run(
                    ["git", "config", "user.email", "shinka@evolution.local"],
                    cwd=str(temp_worktree),
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "config", "user.name", "Shinka Evolution"],
                    cwd=str(temp_worktree),
                    capture_output=True,
                )

                # Copy workspace files to temp worktree (excluding .git)
                for item in workspace_path.iterdir():
                    if item.name == ".git":
                        continue
                    dest = temp_worktree / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest)
                    else:
                        shutil.copy2(item, dest)

                # Stage and commit
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=str(temp_worktree),
                    capture_output=True,
                    check=True,
                )

                subprocess.run(
                    ["git", "commit", "-m", message],
                    cwd=str(temp_worktree),
                    capture_output=True,
                    check=True,
                )

                # Get the commit SHA
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(temp_worktree),
                    capture_output=True,
                    text=True,
                    check=True,
                )
                commit_sha = result.stdout.strip()

                # Push objects to the bare repo using git push with file:// protocol
                # This works even for bare repos without a main branch
                subprocess.run(
                    ["git", "remote", "add", "origin", f"file://{self.repo_path}"],
                    cwd=str(temp_worktree),
                    capture_output=True,
                )

                # First push HEAD to create the main branch
                result = subprocess.run(
                    ["git", "push", "origin", "HEAD:refs/heads/main"],
                    cwd=str(temp_worktree),
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    # Fallback: use git bundle to transfer objects
                    bundle_path = temp_worktree / "transfer.bundle"
                    subprocess.run(
                        ["git", "bundle", "create", str(bundle_path), "HEAD"],
                        cwd=str(temp_worktree),
                        capture_output=True,
                        check=True,
                    )
                    subprocess.run(
                        ["git", "bundle", "unbundle", str(bundle_path)],
                        cwd=str(self.repo_path),
                        capture_output=True,
                        check=True,
                    )
                    # Create the main branch ref
                    subprocess.run(
                        ["git", "update-ref", "refs/heads/main", commit_sha],
                        cwd=str(self.repo_path),
                        capture_output=True,
                        check=True,
                    )

                # Create ref for the node if UUID provided
                if node_uuid:
                    self._create_node_ref(commit_sha, node_uuid)

                logger.info(
                    f"Initialized evolution repo with seed commit: {commit_sha[:8]}"
                )
                return commit_sha

            finally:
                # Clean up temp worktree
                if temp_worktree.exists():
                    shutil.rmtree(temp_worktree, ignore_errors=True)

    def _create_node_ref(self, commit_sha: str, node_uuid: str) -> None:
        """Create a git ref for a node to prevent garbage collection.

        Creates refs/shinka/nodes/<node_uuid> pointing to the commit.
        This ensures the commit is always reachable and visible in
        `git log --graph --all`.

        Args:
            commit_sha: The commit SHA to reference
            node_uuid: The node's UUID
        """
        ref_name = f"refs/shinka/nodes/{node_uuid}"
        subprocess.run(
            ["git", "update-ref", ref_name, commit_sha],
            cwd=str(self.repo_path),
            capture_output=True,
            check=True,
        )
        logger.debug(f"Created ref {ref_name} -> {commit_sha[:8]}")

    def checkout_for_mutation(self, parent_sha: str) -> MutationWorktree:
        """Create an ephemeral worktree for performing a mutation.

        The worktree is created in a cross-platform temp directory and
        should be cleaned up after the mutation is committed using
        cleanup_mutation_worktree() or the mutation_context() manager.

        Args:
            parent_sha: The commit SHA of the parent node to checkout

        Returns:
            MutationWorktree with the checkout path and metadata

        Raises:
            subprocess.CalledProcessError: If git operations fail
        """
        worktree_id = str(uuid.uuid4())
        worktree_path = self.worktree_base / worktree_id

        with self._git_lock():
            try:
                # Try creating a proper worktree first
                subprocess.run(
                    [
                        "git",
                        "worktree",
                        "add",
                        "--detach",
                        str(worktree_path),
                        parent_sha,
                    ],
                    cwd=str(self.repo_path),
                    capture_output=True,
                    check=True,
                    text=True,
                )
                is_shared_clone = False

            except subprocess.CalledProcessError as e:
                # Windows without Developer Mode may fail on symlinks
                stderr = e.stderr if e.stderr else ""
                if "symlink" in stderr.lower() or "permission" in stderr.lower():
                    logger.warning(
                        "Git worktree failed (likely Windows symlink issue), "
                        "falling back to --shared clone"
                    )
                    # Fall back to shared clone
                    worktree_path.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["git", "clone", "--shared", str(self.repo_path), str(worktree_path)],
                        capture_output=True,
                        check=True,
                    )
                    subprocess.run(
                        ["git", "checkout", parent_sha],
                        cwd=str(worktree_path),
                        capture_output=True,
                        check=True,
                    )
                    is_shared_clone = True
                else:
                    raise

        logger.debug(f"Created mutation worktree at {worktree_path}")
        return MutationWorktree(
            path=worktree_path,
            parent_sha=parent_sha,
            worktree_id=worktree_id,
            is_shared_clone=is_shared_clone,
        )

    def commit_mutation(
        self,
        worktree: MutationWorktree,
        message: str,
        node_uuid: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Commit changes in the mutation worktree and create ref.

        After committing, creates refs/shinka/nodes/<node_uuid> to prevent
        the commit from being garbage collected.

        Args:
            worktree: The MutationWorktree from checkout_for_mutation
            message: Commit message
            node_uuid: UUID of the node (used for refs/shinka/nodes/<uuid>)
            metadata: Optional metadata to append to commit message

        Returns:
            The new commit SHA (40 characters)

        Raises:
            ValueError: If there are no changes to commit
        """
        with self._git_lock():
            # Configure git user in worktree
            subprocess.run(
                ["git", "config", "user.email", "shinka@evolution.local"],
                cwd=str(worktree.path),
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Shinka Evolution"],
                cwd=str(worktree.path),
                capture_output=True,
            )

            # Check if there are changes
            status_result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(worktree.path),
                capture_output=True,
                text=True,
            )

            if not status_result.stdout.strip():
                # No changes - create empty commit to maintain lineage
                logger.warning(f"No changes in worktree {worktree.worktree_id}, creating empty commit")
                commit_msg = f"{message}\n\n[empty mutation - no file changes]"
                if metadata:
                    commit_msg += f"\n\nMetadata: {metadata}"

                subprocess.run(
                    ["git", "commit", "--allow-empty", "-m", commit_msg],
                    cwd=str(worktree.path),
                    capture_output=True,
                    check=True,
                )
            else:
                # Stage all changes
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=str(worktree.path),
                    capture_output=True,
                    check=True,
                )

                # Build commit message with metadata
                commit_msg = message
                if metadata:
                    commit_msg += f"\n\nMetadata: {metadata}"

                # Commit
                subprocess.run(
                    ["git", "commit", "-m", commit_msg],
                    cwd=str(worktree.path),
                    capture_output=True,
                    check=True,
                )

            # Get the new commit SHA
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(worktree.path),
                capture_output=True,
                text=True,
                check=True,
            )
            commit_sha = result.stdout.strip()

            # Push to bare repo (for worktree) or the commit is already there (shared clone)
            if not worktree.is_shared_clone:
                # Worktree commits are automatically in the bare repo
                pass
            else:
                # For shared clone, push the new commit
                subprocess.run(
                    ["git", "push", "origin", f"HEAD:refs/heads/temp_{worktree.worktree_id}"],
                    cwd=str(worktree.path),
                    capture_output=True,
                )

            # CRITICAL: Create ref to prevent garbage collection
            self._create_node_ref(commit_sha, node_uuid)

            logger.info(f"Committed mutation {commit_sha[:8]} for node {node_uuid[:8]}")
            return commit_sha

    def cleanup_mutation_worktree(self, worktree: MutationWorktree) -> None:
        """Remove an ephemeral mutation worktree after use.

        Args:
            worktree: The MutationWorktree to clean up
        """
        try:
            if not worktree.is_shared_clone:
                # Remove git worktree registration
                with self._git_lock():
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", str(worktree.path)],
                        cwd=str(self.repo_path),
                        capture_output=True,
                    )
            # Remove directory if it still exists
            if worktree.path.exists():
                shutil.rmtree(worktree.path, ignore_errors=True)
            logger.debug(f"Cleaned up mutation worktree {worktree.worktree_id}")
        except Exception as e:
            logger.warning(f"Failed to cleanup worktree {worktree.worktree_id}: {e}")

    @contextmanager
    def mutation_context(
        self, parent_sha: str, node_uuid: str
    ) -> Generator[MutationWorktree, None, None]:
        """Context manager for safe mutation worktree lifecycle.

        Ensures cleanup happens even if the agent crashes or raises an exception.

        Args:
            parent_sha: The commit SHA of the parent node
            node_uuid: The UUID for the new node

        Yields:
            MutationWorktree with the checkout path

        Example:
            with manager.mutation_context(parent_sha, node_uuid) as worktree:
                # Run agent in worktree.path
                # ... agent makes changes ...
                sha = manager.commit_mutation(worktree, "message", node_uuid)
            # worktree automatically cleaned up here, even on exception
        """
        worktree = self.checkout_for_mutation(parent_sha)
        try:
            yield worktree
        finally:
            self.cleanup_mutation_worktree(worktree)

    def create_user_worktree(self, sha: str, target_path: Path) -> Path:
        """Create a persistent worktree for user inspection.

        Unlike mutation worktrees, these are not automatically cleaned up.
        Users can work in them, make changes, etc.

        Args:
            sha: Commit SHA to checkout
            target_path: Where to create the worktree

        Returns:
            Path to the created worktree
        """
        target_path = Path(target_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        with self._git_lock():
            try:
                subprocess.run(
                    ["git", "worktree", "add", "--detach", str(target_path), sha],
                    cwd=str(self.repo_path),
                    capture_output=True,
                    check=True,
                    text=True,
                )
            except subprocess.CalledProcessError as e:
                stderr = e.stderr if e.stderr else ""
                if "symlink" in stderr.lower() or "permission" in stderr.lower():
                    # Windows fallback
                    target_path.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["git", "clone", "--shared", str(self.repo_path), str(target_path)],
                        capture_output=True,
                        check=True,
                    )
                    subprocess.run(
                        ["git", "checkout", sha],
                        cwd=str(target_path),
                        capture_output=True,
                        check=True,
                    )
                else:
                    raise

        logger.info(f"Created user worktree at {target_path} for commit {sha[:8]}")
        return target_path

    def export_as_repo(self, target_path: Path) -> Path:
        """Export the evolution as a standalone git repository.

        Clones the bare repo and includes all refs/shinka/nodes/* so the
        full evolutionary tree is visible in `git log --graph --all`.

        Args:
            target_path: Directory to export to

        Returns:
            Path to the exported repository
        """
        target_path = Path(target_path).resolve()

        if target_path.exists():
            shutil.rmtree(target_path)

        # Clone the bare repo with --no-checkout since we want to set up refs first
        subprocess.run(
            ["git", "clone", "--no-checkout", str(self.repo_path), str(target_path)],
            capture_output=True,
            check=True,
        )

        # Fetch all refs including shinka node refs
        subprocess.run(
            ["git", "fetch", "origin", "refs/shinka/nodes/*:refs/shinka/nodes/*"],
            cwd=str(target_path),
            capture_output=True,
        )

        # Checkout main branch (or the first available branch)
        result = subprocess.run(
            ["git", "branch", "-r"],
            cwd=str(target_path),
            capture_output=True,
            text=True,
        )
        branches = result.stdout.strip().split("\n")
        
        # Try to checkout main, master, or first available
        checkout_target = None
        for branch in branches:
            branch = branch.strip()
            if "origin/main" in branch:
                checkout_target = "main"
                break
            elif "origin/master" in branch:
                checkout_target = "master"
                break
        
        if checkout_target:
            subprocess.run(
                ["git", "checkout", checkout_target],
                cwd=str(target_path),
                capture_output=True,
            )
        elif branches and branches[0].strip():
            # Fallback: checkout HEAD
            subprocess.run(
                ["git", "checkout", "HEAD"],
                cwd=str(target_path),
                capture_output=True,
            )

        logger.info(f"Exported evolution repo to {target_path}")
        return target_path

    def get_file_contents(
        self,
        sha: str,
        path: Optional[str] = None,
    ) -> str:
        """Get file contents from a commit.

        This is used by the Data Access Layer (Program.get_code_content())
        to retrieve code from git-backed storage.

        Args:
            sha: Commit SHA
            path: Optional file path. If None, returns all files as a corpus
                  with === FILE: path === headers.

        Returns:
            File contents as string

        Raises:
            subprocess.CalledProcessError: If the commit or file doesn't exist
        """
        if path:
            # Get specific file
            result = subprocess.run(
                ["git", "show", f"{sha}:{path}"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout
        else:
            # Get all files as corpus
            # First, list all files in the commit
            result = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", sha],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=True,
            )
            files = result.stdout.strip().split("\n")

            # Build corpus with file headers
            corpus_parts: List[str] = []
            for file_path in files:
                if not file_path:
                    continue
                content_result = subprocess.run(
                    ["git", "show", f"{sha}:{file_path}"],
                    cwd=str(self.repo_path),
                    capture_output=True,
                    text=True,
                )
                if content_result.returncode == 0:
                    corpus_parts.append(f"=== FILE: {file_path} ===")
                    corpus_parts.append(content_result.stdout)

            return "\n".join(corpus_parts)

    def get_commit_info(self, sha: str) -> Dict[str, Any]:
        """Get information about a commit.

        Args:
            sha: Commit SHA

        Returns:
            Dict with: sha, message, author_date, parent_shas
        """
        # Get commit message
        result = subprocess.run(
            ["git", "log", "-1", "--format=%B", sha],
            cwd=str(self.repo_path),
            capture_output=True,
            text=True,
            check=True,
        )
        message = result.stdout.strip()

        # Get author date
        result = subprocess.run(
            ["git", "log", "-1", "--format=%aI", sha],
            cwd=str(self.repo_path),
            capture_output=True,
            text=True,
            check=True,
        )
        author_date = result.stdout.strip()

        # Get parent SHAs
        result = subprocess.run(
            ["git", "log", "-1", "--format=%P", sha],
            cwd=str(self.repo_path),
            capture_output=True,
            text=True,
            check=True,
        )
        parent_shas = result.stdout.strip().split() if result.stdout.strip() else []

        return {
            "sha": sha,
            "message": message,
            "author_date": author_date,
            "parent_shas": parent_shas,
        }

    def list_all_nodes(self) -> List[Dict[str, str]]:
        """List all node refs in the repository.

        Returns:
            List of dicts with 'node_uuid' and 'commit_sha' keys
        """
        result = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/shinka/nodes/"],
            cwd=str(self.repo_path),
            capture_output=True,
            text=True,
        )

        nodes: List[Dict[str, str]] = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                ref_name, sha = parts[0], parts[1]
                # Extract UUID from ref name (refs/shinka/nodes/<uuid> -> <uuid>)
                node_uuid = ref_name.replace("refs/shinka/nodes/", "").replace("shinka/nodes/", "")
                nodes.append({"node_uuid": node_uuid, "commit_sha": sha})

        return nodes