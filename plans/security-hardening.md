# Security Hardening for WebUI and Git Operations

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This document must be maintained in accordance with `PLANS.md` at the repository root.

## Purpose / Big Picture

This plan fixes security vulnerabilities in the WebUI and git-backed storage that could allow:

- Path traversal attacks reading arbitrary files from the server
- Git command injection via malicious SHAs or UUIDs in database metadata
- TOCTOU (time-of-check-time-of-use) race conditions in worktree cleanup

After this work:

- Session log endpoint validates paths stay within run directory
- Git SHAs and UUIDs are validated before use in subprocess commands
- Worktree cleanup is atomic and immune to symlink races

Observable behavior: Attempts to access files outside the run directory via `/get_agent_session_log` return 403 Forbidden. Invalid SHAs cause graceful errors instead of command injection.

## Progress

### Phase 1 (Initial Fixes)
- [x] Milestone 1: Add path traversal protection to `/get_agent_session_log`
  - Added path resolution and containment check at visualization.py:2746-2752
- [x] Milestone 2: Validate git SHAs and UUIDs in git operations
  - Added `_validate_git_sha()` and `_validate_uuid()` functions at git_worktree.py:24-38
  - Added validation to: `get_file_contents()`, `checkout_for_mutation()`, `create_user_worktree()`, `get_commit_info()`, `_create_node_ref()`
- [x] Milestone 3: Fix TOCTOU race condition in worktree cleanup
  - Moved directory cleanup inside lock and added symlink check at git_worktree.py:1298-1318
- [x] Add security-focused unit tests
  - Created tests/test_security.py with 10 tests covering all vulnerabilities
- [x] Run full test suite
  - All 10 security tests pass
  - All 5 webui tests pass

### Phase 2 (Validation Agent Findings)
- [x] Fix UUID validation backward compatibility
  - Replaced strict UUID pattern with flexible `_validate_node_id()` accepting "node-001" format
  - All 26 test_git_evolution.py tests now pass
- [x] Add missing SHA validation to `export_patch()` and `get_diff_from_original()`
- [x] Add branch name validation `_validate_branch()`
  - Applied to `_ensure_cached_clone()`, `create_worktree()`, `create_full_clone()`
  - Rejects option injection (`--malicious`) and path traversal (`..`)
- [x] Improve path validation to use `relative_to()` instead of string prefix
- [x] Fix TOCTOU in `_remove_worktree()` with symlink check
- [x] Update security tests: 12 tests now covering node IDs and branch validation
- [x] All 45 tests pass (12 security + 26 git evolution + 5 webui + 2 others)

## Surprises & Discoveries

- The `get_file_contents`, `checkout_for_mutation`, and `cleanup_mutation_worktree` methods are on `EvolutionGitManager`, not `GitWorktreeManager`
- Pre-existing ruff warnings in visualization.py and git_worktree.py (not introduced by these changes)
- Strict UUID validation broke test_git_evolution.py which uses "node-001" format IDs
- Branch parameters were never validated before being passed to git subprocess calls

## Decision Log

- Replaced `_validate_uuid()` with `_validate_node_id()` to accept both UUID and simple alphanumeric IDs
- Used `Path.relative_to()` instead of string `startswith()` for more robust path containment checks
- Added branch validation pattern `^[a-zA-Z0-9._/-]+$` with explicit rejection of `--` and `..`

## Outcomes & Retrospective

Phase 2 successfully addressed issues found by 7 validation subagents:
- Backward compatibility restored (test_git_evolution.py passes)
- Added validation for previously unprotected git operations (export_patch, get_diff_from_original, branch names)
- Improved path validation robustness
- Extended security test coverage from 10 to 12 tests

## Context and Orientation

Key concepts:

- Path traversal: An attack where "../" sequences in file paths escape intended directories
- Git SHA: A 40-character hexadecimal string identifying a commit (e.g., `a1b2c3d4...`)
- TOCTOU: A race condition where time passes between checking a condition and acting on it
- Worktree: A git feature allowing multiple working directories from one repository

Key files:

- `shinka/webui/visualization.py`: WebUI HTTP server with session log endpoint
- `shinka/webui/git_worktree.py`: Git worktree management for mutations
- `shinka/database/dbase.py`: Database with git SHA retrieval

Problem 1 - Path Traversal (visualization.py lines 2747-2749):
The session log endpoint reads `log_path_str` from database metadata and constructs a path without validating it stays within the run directory. A malicious path like `"../../../etc/passwd"` could read arbitrary files.

Problem 2 - Git SHA Injection (git_worktree.py lines 1452, 1477):
Git SHAs from database metadata are used directly in `git show {sha}:{path}` without validation. An attacker could store malicious refs like `HEAD~1`, `main`, or refs with special characters.

Problem 3 - UUID Injection (git_worktree.py line 1075):
Node UUIDs are used in git ref names without validation: `refs/shinka/nodes/{node_uuid}`. While git has format restrictions, malicious UUIDs could exploit edge cases.

Problem 4 - TOCTOU Race (git_worktree.py lines 1269-1280):
Worktree cleanup releases the lock before deleting the directory. Another process could create a symlink in that window, causing deletion of unintended files.

## Plan of Work

### Milestone 1: Add Path Traversal Protection

In `shinka/webui/visualization.py`, the `handle_get_agent_session_log()` method must validate paths:

Current buggy code:
    log_path = Path(log_path_str)
    if not log_path.is_absolute():
        log_path = run_dir / log_path

Fixed code with validation:
    log_path = (run_dir / log_path_str).resolve()
    run_dir_resolved = run_dir.resolve()
    if not str(log_path).startswith(str(run_dir_resolved) + os.sep):
        self.send_error(403, "Access denied: path outside run directory")
        return
    if not log_path.exists():
        self.send_error(404, "Log file not found")
        return

Apply this pattern to all file path constructions from untrusted input.

### Milestone 2: Validate Git SHAs and UUIDs

Add validation functions and apply them consistently:

1. Create `_validate_git_sha(sha: str) -> bool`:
   - Must be exactly 40 characters
   - Must be hexadecimal only
   - Returns False for any other format

2. Create `_validate_uuid(uuid_str: str) -> bool`:
   - Must match UUID format (8-4-4-4-12 hex)
   - No special characters

3. Apply validation before any subprocess call using SHAs or UUIDs

In `shinka/webui/git_worktree.py`:

    import re

    SHA_PATTERN = re.compile(r'^[0-9a-f]{40}$')
    UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

    def _validate_git_sha(sha: str) -> bool:
        return bool(SHA_PATTERN.match(sha.lower()))

    def _validate_uuid(uuid_str: str) -> bool:
        return bool(UUID_PATTERN.match(uuid_str.lower()))

Then add checks before subprocess calls:
    if not _validate_git_sha(sha):
        raise ValueError(f"Invalid git SHA format: {sha}")

### Milestone 3: Fix TOCTOU Race in Worktree Cleanup

In `shinka/webui/git_worktree.py`, the `cleanup_mutation_worktree()` method must keep directory operations inside the lock:

Current buggy code:
    with self._git_lock():
        subprocess.run(["git", "worktree", "remove", ...])
    # OUTSIDE LOCK - TOCTOU window here!
    if worktree.path.exists():
        shutil.rmtree(worktree.path, ignore_errors=True)

Fixed code:
    with self._git_lock():
        subprocess.run(["git", "worktree", "remove", ...])
        # Stay inside lock for directory cleanup
        if worktree.path.exists():
            # Verify it's still a directory (not replaced with symlink)
            if worktree.path.is_dir() and not worktree.path.is_symlink():
                shutil.rmtree(worktree.path, ignore_errors=True)
            else:
                logger.warning(f"Skipping cleanup of {worktree.path}: not a directory")

### Milestone 4: Add Security Tests

Create `tests/test_security.py` with:

1. `test_path_traversal_blocked()`: Verify "../" paths return 403
2. `test_invalid_sha_rejected()`: Verify non-hex SHAs raise ValueError
3. `test_invalid_uuid_rejected()`: Verify malformed UUIDs raise ValueError
4. `test_worktree_symlink_not_followed()`: Verify symlinks don't cause unintended deletion

## Concrete Steps

All commands run from repository root: `/Users/juno/workspace/shrinkaevolve-codexevolve`

Milestone 1:
    # Read current endpoint
    Read shinka/webui/visualization.py lines 2740-2795

    # Add path validation
    Edit shinka/webui/visualization.py

    # Run webui tests
    uv run pytest tests/test_webui*.py -v

Milestone 2:
    # Add validation functions to git_worktree.py
    Read shinka/webui/git_worktree.py lines 1-50
    Edit shinka/webui/git_worktree.py

    # Add validation calls at subprocess usages
    Read shinka/webui/git_worktree.py lines 1070-1090
    Edit shinka/webui/git_worktree.py

    Read shinka/webui/git_worktree.py lines 1445-1485
    Edit shinka/webui/git_worktree.py

Milestone 3:
    # Fix TOCTOU in cleanup
    Read shinka/webui/git_worktree.py lines 1265-1290
    Edit shinka/webui/git_worktree.py

Milestone 4:
    # Create security tests
    Write tests/test_security.py

    # Run security tests
    uv run pytest tests/test_security.py -v

Final validation:
    uv run pytest tests/ -q
    uv run ruff check shinka/webui/

## Success Criteria & Validation

SC-01: Path traversal blocked
  Evidence: Test with path "../../../etc/passwd" returns 403 error

SC-02: Invalid SHAs rejected
  Evidence: Test with SHA "INVALID" raises ValueError

SC-03: Invalid UUIDs rejected
  Evidence: Test with UUID "not-a-uuid" raises ValueError

SC-04: Symlinks not followed in cleanup
  Evidence: Test creating symlink in worktree path doesn't delete target

SC-05: All tests pass
  Evidence: `uv run pytest tests/ -q` shows no failures

## Idempotence and Recovery

Validation functions are pure and can be called multiple times. Path checks are read-only. Worktree cleanup is made safer, not changed in behavior for normal cases.

## Artifacts and Notes

(To be filled with test output during implementation)

## Interfaces and Dependencies

New functions:

In `shinka/webui/git_worktree.py`:
- `_validate_git_sha(sha: str) -> bool`: Returns True iff sha is 40 hex characters
- `_validate_uuid(uuid_str: str) -> bool`: Returns True iff uuid_str matches UUID format

No external dependencies added. Uses only stdlib `re` module.
