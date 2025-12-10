
import platform
import subprocess
import shutil
from pathlib import Path
from typing import Callable, List, Optional, Set
import logging

logger = logging.getLogger(__name__)


def fast_copy(
    src: Path,
    dst: Path,
    dirs_exist_ok: bool = False,
    ignore: Optional[Callable[[str, List[str]], List[str]]] = None,
    exclude_dirs: Optional[Set[str]] = None,
    exclude_suffixes: Optional[Set[str]] = None,
    exclude_files: Optional[Set[str]] = None,
) -> None:
    """
    Copy directory tree using platform-specific fast copy methods (cp -Rc / --reflink)
    if available, falling back to shutil.copytree.
    
    For CoW to work efficiently, avoid using the `ignore` callback parameter.
    Instead, use exclude_dirs/exclude_suffixes/exclude_files which can be
    converted to rsync-compatible exclusions that preserve CoW benefits.
    
    Cross-platform behavior:
    - macOS (APFS): Uses `cp -Rc` for Copy-on-Write cloning
    - Linux (Btrfs/XFS): Uses `cp --reflink=auto` or rsync with reflink
    - Windows/Other: Falls back to shutil.copytree (no CoW, but fully functional)
    
    Args:
        src: Source directory path
        dst: Destination directory path
        dirs_exist_ok: If True, merge into existing directory
        ignore: Callable for shutil.copytree ignore (disables CoW optimization)
        exclude_dirs: Set of directory names to exclude (e.g., {"__pycache__", "results"})
        exclude_suffixes: Set of file suffixes to exclude (e.g., {".pyc", ".pyo"})
        exclude_files: Set of file names to exclude (e.g., {"session_log.jsonl"})
    """
    # Build ignore function from exclusion sets for fallback path
    built_ignore = None
    if exclude_dirs or exclude_suffixes or exclude_files:
        def _make_ignore(d, s, f):
            def _ignore_fn(dir_path: str, names: List[str]) -> List[str]:
                ignored = []
                for name in names:
                    if d and name in d:
                        ignored.append(name)
                    elif s and any(name.endswith(suf) for suf in s):
                        ignored.append(name)
                    elif f and name in f:
                        ignored.append(name)
                return ignored
            return _ignore_fn
        built_ignore = _make_ignore(exclude_dirs, exclude_suffixes, exclude_files)
    
    # Use provided ignore callback if given, otherwise use built one
    effective_ignore = ignore if ignore is not None else built_ignore
    
    # If old-style ignore callback is used, fall back to shutil.copytree
    if ignore is not None:
        shutil.copytree(src, dst, dirs_exist_ok=dirs_exist_ok, ignore=ignore)
        return

    system = platform.system()
    
    # Windows and other platforms: direct fallback to shutil (no CoW support)
    if system not in ("Darwin", "Linux"):
        shutil.copytree(src, dst, dirs_exist_ok=dirs_exist_ok, ignore=effective_ignore)
        return
    
    # Ensure parent exists
    dst.parent.mkdir(parents=True, exist_ok=True)
    
    if dst.exists() and not dirs_exist_ok:
        raise FileExistsError(f"Destination {dst} already exists")
    
    # For dirs_exist_ok=True with existing directory, clean it first to allow CoW
    if dst.exists() and dirs_exist_ok:
        try:
            shutil.rmtree(dst)
        except Exception as e:
            logger.warning(f"Could not remove existing dir {dst}: {e}, falling back to shutil.copytree")
            shutil.copytree(src, dst, dirs_exist_ok=dirs_exist_ok, ignore=effective_ignore)
            return
    
    # Build rsync exclusion args if we have exclusions
    rsync_excludes: List[str] = []
    if exclude_dirs:
        for d in exclude_dirs:
            rsync_excludes.extend(["--exclude", f"{d}/"])
    if exclude_suffixes:
        for s in exclude_suffixes:
            rsync_excludes.extend(["--exclude", f"*{s}"])
    if exclude_files:
        for f in exclude_files:
            rsync_excludes.extend(["--exclude", f])
    
    # Try rsync first on Linux (supports exclusions + reflink together)
    if rsync_excludes and system == "Linux":
        try:
            cmd = ["rsync", "-a", "--reflink=auto"] + rsync_excludes + [f"{src}/", str(dst)]
            subprocess.run(cmd, check=True, capture_output=True)
            logger.debug(f"Fast copy (rsync reflink) completed: {src} -> {dst}")
            return
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.debug(f"rsync reflink failed: {e}, trying cp")
    
    # Try platform-specific fast copy (full copy, then delete exclusions)
    try:
        if system == "Darwin":  # macOS
            # cp -R (recursive) -c (clone using APFS copy-on-write)
            subprocess.run(["cp", "-Rc", str(src), str(dst)], check=True, capture_output=True)
            # Apply exclusions after copy (still faster than no-CoW full copy)
            if exclude_dirs or exclude_suffixes or exclude_files:
                _apply_exclusions_post_copy(dst, exclude_dirs, exclude_suffixes, exclude_files)
            logger.debug(f"Fast copy (CoW) completed: {src} -> {dst}")
            return
                
        elif system == "Linux":
            # cp --reflink=auto --recursive --archive
            subprocess.run(["cp", "--reflink=auto", "--recursive", "--archive", str(src), str(dst)], check=True, capture_output=True)
            # Apply exclusions after copy
            if exclude_dirs or exclude_suffixes or exclude_files:
                _apply_exclusions_post_copy(dst, exclude_dirs, exclude_suffixes, exclude_files)
            logger.debug(f"Fast copy (reflink) completed: {src} -> {dst}")
            return
                
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"Fast copy failed (sys={system}): {e}. Falling back to shutil.copytree.")
    
    # Final fallback: shutil.copytree with built ignore function
    shutil.copytree(src, dst, dirs_exist_ok=dirs_exist_ok, ignore=effective_ignore)


def _apply_exclusions_post_copy(
    dst: Path,
    exclude_dirs: Optional[Set[str]],
    exclude_suffixes: Optional[Set[str]],
    exclude_files: Optional[Set[str]],
) -> None:
    """Remove excluded files/dirs after a CoW copy.
    
    Note: We iterate in reverse sorted order by path length to handle
    nested exclusions correctly (delete children before parents).
    """
    if not dst.exists():
        return
    
    # Collect items to delete, sorted by path depth (deepest first)
    items_to_delete: List[Path] = []
    
    for item in dst.rglob("*"):
        should_delete = False
        if exclude_dirs and item.is_dir() and item.name in exclude_dirs:
            should_delete = True
        elif exclude_suffixes and item.is_file() and item.suffix in exclude_suffixes:
            should_delete = True
        elif exclude_files and item.is_file() and item.name in exclude_files:
            should_delete = True
        
        if should_delete:
            items_to_delete.append(item)
    
    # Sort by path length descending (delete deepest items first)
    items_to_delete.sort(key=lambda p: len(p.parts), reverse=True)
    
    for item in items_to_delete:
        try:
            if item.is_dir():
                shutil.rmtree(item)
            elif item.is_file():
                item.unlink()
        except FileNotFoundError:
            pass  # Already deleted (parent dir was removed)
        except Exception as e:
            logger.debug(f"Could not delete {item}: {e}")
