"""
GitHub Sync Layer for Jules Integration.

Implements the "Staging Directory Pattern" to safely sync files between
Shinka worktrees and GitHub without corrupting git history.

CRITICAL: Never run git commands inside Shinka worktrees. They are linked
to evolution.git which contains synthetic evolutionary history that would
pollute GitHub PRs.

Instead, we maintain a separate clean clone in:
    ~/.shinka/cache/jules_staging/{owner}/{repo}/
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Default staging directory base
STAGING_BASE = Path.home() / ".shinka" / "cache" / "jules_staging"

# Seed marker filename for empty workdirs
SEED_MARKER_FILENAME = "SHINKA_SEED.txt"
SEED_MARKER_CONTENT = """# Shinka Evolution Seed Marker

This file indicates an empty seed for open-ended evolution.
The agent should work on the existing codebase as it sees fit.

This marker file can be safely ignored or deleted.
"""


def is_workdir_effectively_empty(workdir: Path) -> bool:
    """Check if workdir is effectively empty (just a seed marker or no files).

    Used to determine whether to preserve base branch content when syncing
    to Jules. If the workdir is empty/seed-only, we preserve the GitHub repo
    content. If it has real evolved content, we sync our state.

    Args:
        workdir: Directory to check

    Returns:
        True if workdir is empty or contains only seed marker files
    """
    if not workdir.exists():
        return True

    files = [
        f for f in workdir.rglob("*")
        if f.is_file() and ".git" not in f.parts
    ]

    if len(files) == 0:
        return True

    # Check if only seed marker files exist
    non_seed_files = [
        f for f in files
        if f.name != SEED_MARKER_FILENAME
        and f.stat().st_size > 0  # Ignore empty files
    ]

    # Also consider very small files (< 200 bytes) as potential seed markers
    # This handles legacy empty main.py files
    significant_files = [
        f for f in non_seed_files
        if f.stat().st_size >= 200 or not f.name.startswith(("main.", "SEED", "seed"))
    ]

    return len(significant_files) == 0


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------


class JulesSyncError(RuntimeError):
    """Base exception for GitHub sync errors."""


class GitOperationError(JulesSyncError):
    """Raised when a git operation fails."""


class StagingCloneError(JulesSyncError):
    """Raised when staging directory clone/setup fails."""


# -----------------------------------------------------------------------------
# Data Classes
# -----------------------------------------------------------------------------


@dataclass
class SyncResult:
    """Result of a sync operation."""

    branch_name: str
    files_synced: int
    files_deleted: int
    commit_sha: Optional[str] = None
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


# -----------------------------------------------------------------------------
# GitHub Sync Class
# -----------------------------------------------------------------------------


class JulesGitHubSync:
    """Manages synchronization between local scratch and GitHub for Jules.

    This class implements the Staging Directory Pattern:
    1. Maintain a clean clone of the target GitHub repo in a staging area
    2. Copy files from Shinka worktree to staging (excluding .git/)
    3. Commit and push from staging directory
    4. Pull changes back after Jules completes

    This ensures Jules receives clean branches with proper "Base + Changes"
    diffs, without polluting PRs with unrelated evolutionary history.
    """

    def __init__(
        self,
        github_token: str,
        staging_base: Optional[Path] = None,
    ):
        """Initialize GitHub sync.

        Args:
            github_token: GitHub personal access token with 'repo' scope
            staging_base: Base directory for staging clones
                         (default: ~/.shinka/cache/jules_staging/)
        """
        self.github_token = github_token
        self.staging_base = staging_base or STAGING_BASE
        self.staging_base.mkdir(parents=True, exist_ok=True)

    def _run_git(
        self,
        args: List[str],
        cwd: Path,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run a git command in the specified directory.

        Args:
            args: Git command arguments (without 'git' prefix)
            cwd: Working directory for the command
            check: Raise exception on non-zero exit
            capture_output: Capture stdout/stderr

        Returns:
            CompletedProcess result

        Raises:
            GitOperationError: If check=True and command fails
        """
        cmd = ["git"] + args
        env = os.environ.copy()
        # Use token for HTTPS auth
        env["GIT_ASKPASS"] = "echo"
        env["GIT_TERMINAL_PROMPT"] = "0"

        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                check=False,
                capture_output=capture_output,
                text=True,
                env=env,
            )
            if check and result.returncode != 0:
                raise GitOperationError(
                    f"Git command failed: {' '.join(cmd)}\n"
                    f"stderr: {result.stderr}\n"
                    f"stdout: {result.stdout}"
                )
            return result
        except subprocess.SubprocessError as e:
            raise GitOperationError(f"Git command error: {e}") from e

    def _get_auth_url(self, github_repo: str) -> str:
        """Get authenticated GitHub URL.

        Args:
            github_repo: Repository in "owner/repo" format

        Returns:
            HTTPS URL with embedded token for auth
        """
        return f"https://x-access-token:{self.github_token}@github.com/{github_repo}.git"

    def get_staging_dir(self, github_repo: str) -> Path:
        """Get staging directory path for a repository.

        Args:
            github_repo: Repository in "owner/repo" format

        Returns:
            Path to staging directory (may not exist yet)
        """
        # Sanitize repo path
        safe_path = github_repo.replace("/", os.sep)
        return self.staging_base / safe_path

    def ensure_staging_clone(self, github_repo: str) -> Path:
        """Ensure staging directory exists with a clean clone.

        If the staging clone doesn't exist, creates it.
        If it exists, fetches latest from origin.

        Args:
            github_repo: Repository in "owner/repo" format

        Returns:
            Path to staging directory

        Raises:
            StagingCloneError: If clone/fetch fails
        """
        staging_dir = self.get_staging_dir(github_repo)
        git_dir = staging_dir / ".git"

        try:
            if git_dir.exists():
                # Existing clone - fetch latest
                logger.debug(f"Fetching latest in staging: {staging_dir}")
                self._run_git(["fetch", "--all", "--prune"], cwd=staging_dir)
            else:
                # New clone needed
                logger.info(f"Cloning {github_repo} to staging: {staging_dir}")
                staging_dir.mkdir(parents=True, exist_ok=True)

                auth_url = self._get_auth_url(github_repo)
                self._run_git(
                    ["clone", "--no-checkout", auth_url, "."],
                    cwd=staging_dir,
                )

            return staging_dir

        except GitOperationError as e:
            raise StagingCloneError(
                f"Failed to setup staging clone for {github_repo}: {e}"
            ) from e

    def _sync_files_to_staging(
        self,
        workdir: Path,
        staging_dir: Path,
        preserve_base: bool = False,
    ) -> tuple[int, int]:
        """Sync files from workdir to staging directory.

        Implements the file overlay rules:
        - Copy all files from workdir to staging
        - Exclude .git/ folder
        - Delete files in staging that don't exist in workdir (unless preserve_base)
        - Preserve staging's .git/ directory
        - Skip copying seed marker files when preserve_base is True

        Args:
            workdir: Source directory (Shinka worktree)
            staging_dir: Destination staging directory
            preserve_base: If True, don't delete files from staging that aren't
                          in workdir. Used for open-ended evolution where we want
                          to preserve the existing GitHub repo content.

        Returns:
            Tuple of (files_synced, files_deleted)
        """
        files_synced = 0
        files_deleted = 0

        # Get all files in workdir (excluding .git and seed markers if preserving base)
        workdir_files: Set[Path] = set()
        for path in workdir.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                rel_path = path.relative_to(workdir)
                # Skip seed marker files when preserving base - don't pollute repo
                if preserve_base and path.name == SEED_MARKER_FILENAME:
                    logger.debug(f"Skipping seed marker: {rel_path}")
                    continue
                # Skip empty files when preserving base
                if preserve_base and path.stat().st_size == 0:
                    logger.debug(f"Skipping empty file: {rel_path}")
                    continue
                workdir_files.add(rel_path)

        # Get all files in staging (excluding .git)
        staging_files: Set[Path] = set()
        for path in staging_dir.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                rel_path = path.relative_to(staging_dir)
                staging_files.add(rel_path)

        # Delete files that don't exist in workdir (unless preserve_base)
        if not preserve_base:
            for rel_path in staging_files - workdir_files:
                target = staging_dir / rel_path
                target.unlink()
                files_deleted += 1
                logger.debug(f"Deleted from staging: {rel_path}")

            # Clean up empty directories (except .git)
            for dirpath, dirnames, filenames in os.walk(staging_dir, topdown=False):
                dirpath = Path(dirpath)
                if ".git" in dirpath.parts:
                    continue
                if not filenames and not dirnames:
                    try:
                        dirpath.rmdir()
                    except OSError:
                        pass
        else:
            logger.info("preserve_base=True: Keeping existing repo files")

        # Copy files from workdir to staging
        for rel_path in workdir_files:
            source = workdir / rel_path
            target = staging_dir / rel_path

            # Create parent directories
            target.parent.mkdir(parents=True, exist_ok=True)

            # Copy file
            shutil.copy2(source, target)
            files_synced += 1

        logger.info(
            f"Synced {files_synced} files to staging, "
            f"deleted {files_deleted} obsolete files"
            + (" (preserve_base=True)" if preserve_base else "")
        )
        return files_synced, files_deleted

    def _sync_files_from_staging(
        self,
        staging_dir: Path,
        workdir: Path,
    ) -> Dict[Path, str]:
        """Sync files from staging back to workdir.

        Args:
            staging_dir: Source staging directory
            workdir: Destination directory (Shinka worktree)

        Returns:
            Dict of changed files {relative_path: content}
        """
        changed_files: Dict[Path, str] = {}

        # Get workdir baseline
        workdir_files: Dict[Path, str] = {}
        for path in workdir.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                rel_path = path.relative_to(workdir)
                try:
                    workdir_files[rel_path] = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    # Binary file - read as bytes for comparison
                    workdir_files[rel_path] = path.read_bytes().hex()

        # Get staging files
        staging_files: Dict[Path, str] = {}
        for path in staging_dir.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                rel_path = path.relative_to(staging_dir)
                try:
                    staging_files[rel_path] = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    staging_files[rel_path] = path.read_bytes().hex()

        # Find changed/new files
        for rel_path, content in staging_files.items():
            if rel_path not in workdir_files or workdir_files[rel_path] != content:
                # File is new or changed
                source = staging_dir / rel_path
                target = workdir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)

                # Copy file
                shutil.copy2(source, target)

                # Record change (read actual content for text files)
                try:
                    changed_files[rel_path] = target.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    # For binary files, just note it changed
                    changed_files[rel_path] = "[binary file]"

        # Handle deleted files
        for rel_path in workdir_files.keys() - staging_files.keys():
            target = workdir / rel_path
            if target.exists():
                target.unlink()
                changed_files[rel_path] = "[deleted]"

        logger.info(f"Synced {len(changed_files)} changed files from staging")
        return changed_files

    def push_to_jules_branch(
        self,
        workdir: Path,
        github_repo: str,
        branch_name: str,
        base_branch: str = "main",
        commit_message: Optional[str] = None,
        preserve_base: Optional[bool] = None,
    ) -> SyncResult:
        """Push workdir contents to a new GitHub branch for Jules.

        This is the main entry point for pre-Jules sync:
        1. Ensures staging clone exists and is up to date
        2. Creates new branch from base_branch
        3. Overlays workdir files onto staging
        4. Commits and pushes

        Args:
            workdir: Shinka worktree (DO NOT run git commands here!)
            github_repo: Repository in "owner/repo" format
            branch_name: Target branch name (e.g., "shinka-jules-abc123")
            base_branch: Branch to create from (default: "main")
            commit_message: Optional commit message
            preserve_base: If True, don't delete files from base branch.
                          If None (default), auto-detect based on whether
                          workdir is effectively empty.

        Returns:
            SyncResult with branch info

        Raises:
            JulesSyncError: If sync fails
        """
        try:
            # 1. Ensure staging clone
            staging_dir = self.ensure_staging_clone(github_repo)

            # 2. Fetch and create new branch from base
            self._run_git(["fetch", "origin"], cwd=staging_dir)
            self._run_git(
                ["checkout", "-B", branch_name, f"origin/{base_branch}"],
                cwd=staging_dir,
            )

            # 3. Determine preserve_base mode
            # Auto-detect if not explicitly set: preserve base when workdir is empty
            if preserve_base is None:
                preserve_base = is_workdir_effectively_empty(workdir)
                if preserve_base:
                    logger.info(
                        "Workdir is effectively empty - preserving base branch content "
                        "for open-ended evolution"
                    )

            # 4. Sync files from workdir to staging
            files_synced, files_deleted = self._sync_files_to_staging(
                workdir, staging_dir, preserve_base=preserve_base
            )

            # 4. Stage all changes
            self._run_git(["add", "-A"], cwd=staging_dir)

            # Check if there are changes to commit
            status_result = self._run_git(
                ["status", "--porcelain"],
                cwd=staging_dir,
                check=False,
            )
            if not status_result.stdout.strip():
                logger.info("No changes to commit")
                return SyncResult(
                    branch_name=branch_name,
                    files_synced=files_synced,
                    files_deleted=files_deleted,
                )

            # 5. Commit
            message = commit_message or f"Shinka evolution snapshot for Jules"
            self._run_git(
                ["commit", "-m", message],
                cwd=staging_dir,
            )

            # Get commit SHA
            sha_result = self._run_git(
                ["rev-parse", "HEAD"],
                cwd=staging_dir,
            )
            commit_sha = sha_result.stdout.strip()

            # 6. Push
            self._run_git(
                ["push", "-u", "origin", branch_name, "--force"],
                cwd=staging_dir,
            )

            logger.info(
                f"Pushed branch {branch_name} to {github_repo} "
                f"(commit: {commit_sha[:8]})"
            )

            return SyncResult(
                branch_name=branch_name,
                files_synced=files_synced,
                files_deleted=files_deleted,
                commit_sha=commit_sha,
            )

        except (GitOperationError, StagingCloneError) as e:
            logger.error(f"Failed to push to Jules branch: {e}")
            return SyncResult(
                branch_name=branch_name,
                files_synced=0,
                files_deleted=0,
                error=str(e),
            )

    def pull_from_jules_branch(
        self,
        workdir: Path,
        github_repo: str,
        branch_name: str,
    ) -> Dict[Path, str]:
        """Pull changes from Jules branch back to workdir.

        After Jules completes, this syncs the changes back:
        1. Fetches and checks out the Jules branch
        2. Copies changed files to workdir

        Args:
            workdir: Shinka worktree to update
            github_repo: Repository in "owner/repo" format
            branch_name: Branch Jules worked on

        Returns:
            Dict of changed files {relative_path: content}

        Raises:
            JulesSyncError: If pull fails
        """
        try:
            staging_dir = self.get_staging_dir(github_repo)

            # Fetch latest and checkout Jules branch
            self._run_git(["fetch", "origin"], cwd=staging_dir)
            self._run_git(["checkout", branch_name], cwd=staging_dir)
            self._run_git(["pull", "origin", branch_name], cwd=staging_dir)

            # Sync files back to workdir
            changed_files = self._sync_files_from_staging(staging_dir, workdir)

            logger.info(
                f"Pulled {len(changed_files)} changed files from "
                f"{github_repo}:{branch_name}"
            )
            return changed_files

        except GitOperationError as e:
            raise JulesSyncError(
                f"Failed to pull from Jules branch {branch_name}: {e}"
            ) from e

    def cleanup_branch(
        self,
        github_repo: str,
        branch_name: str,
        delete_local: bool = True,
    ) -> bool:
        """Delete temporary Jules branch from remote (and optionally local).

        Args:
            github_repo: Repository in "owner/repo" format
            branch_name: Branch to delete
            delete_local: Also delete local branch in staging

        Returns:
            True if successful, False otherwise
        """
        staging_dir = self.get_staging_dir(github_repo)

        if not staging_dir.exists():
            logger.warning(f"Staging dir not found: {staging_dir}")
            return False

        try:
            # Delete remote branch
            result = self._run_git(
                ["push", "origin", "--delete", branch_name],
                cwd=staging_dir,
                check=False,
            )
            if result.returncode != 0:
                # Branch may already be deleted
                logger.warning(
                    f"Could not delete remote branch {branch_name}: "
                    f"{result.stderr}"
                )

            # Delete local branch
            if delete_local:
                # Switch to default branch first
                self._run_git(
                    ["checkout", "main"],
                    cwd=staging_dir,
                    check=False,
                )
                self._run_git(
                    ["branch", "-D", branch_name],
                    cwd=staging_dir,
                    check=False,
                )

            logger.info(f"Cleaned up branch {branch_name}")
            return True

        except GitOperationError as e:
            logger.warning(f"Error cleaning up branch {branch_name}: {e}")
            return False


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def get_github_token() -> Optional[str]:
    """Get GitHub token from unified store or environment.

    Priority:
      1. ~/.shinka/credentials.json (provider "github")
      2. GITHUB_TOKEN environment variable

    Returns:
        Token string or None if not set
    """
    try:
        from shinka.tools.credentials import get_api_key as _get_api_key

        token = _get_api_key("github")
        if token:
            return token
    except Exception:
        pass

    return os.environ.get("GITHUB_TOKEN")


def ensure_github_token() -> str:
    """Get GitHub token or raise error.

    Returns:
        Token string

    Raises:
        JulesSyncError: If token is not set
    """
    token = get_github_token()
    if not token:
        raise JulesSyncError(
            "GITHUB_TOKEN environment variable is not set. "
            "Required for GitHub sync with Jules."
        )
    return token


def generate_jules_branch_name(prefix: str = "shinka-jules") -> str:
    """Generate a unique branch name for Jules.

    Args:
        prefix: Branch name prefix

    Returns:
        Unique branch name like "shinka-jules-abc12345"
    """
    unique_id = uuid.uuid4().hex[:8]
    return f"{prefix}-{unique_id}"
