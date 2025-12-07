
import platform
import subprocess
import shutil
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

def fast_copy(src: Path, dst: Path, dirs_exist_ok: bool = False, ignore=None) -> None:
    """
    Copy directory tree using platform-specific fast copy methods (cp -Rc / --reflink)
    if available, falling back to shutil.copytree.
    
    Args:
        src: Source directory path
        dst: Destination directory path
        dirs_exist_ok: Passed to shutil.copytree fallback (not supported by cp directly without pre-cleaning)
        ignore: Callable for shutil.copytree ignore (not supported by cp optimization)
    """
    # If ignore pattern is used, we must use shutil.copytree as cp doesn't support complex ignoring
    if ignore is not None:
        shutil.copytree(src, dst, dirs_exist_ok=dirs_exist_ok, ignore=ignore)
        return

    system = platform.system()
    
    # Ensure parent exists
    dst.parent.mkdir(parents=True, exist_ok=True)
    
    # cp generally expects destination directory to NOT exist for full recursive copy behavior similar to copytree
    # or if it exists, it might copy INTO it. We want exact copytree semantics (dst becomes copy of src).
    if dst.exists() and not dirs_exist_ok:
        raise FileExistsError(f"Destination {dst} already exists")
        
    try:
        if system == "Darwin":  # macOS
            # cp -R (recursive) -c (clone using APFS copy-on-write)
            # If dst exists and we want to merge/overwrite, standard cp behavior applies.
            # To match copytree(dirs_exist_ok=True), we copy contents.
            # For simplicity/robustness in this initial implementation:
            # If dst doesn't exist, we use `cp -Rc src dst`
            if not dst.exists():
                subprocess.run(["cp", "-Rc", str(src), str(dst)], check=True, capture_output=True)
                return
                
        elif system == "Linux":
            # cp --reflink=auto --recursive --archive
            if not dst.exists():
                subprocess.run(["cp", "--reflink=auto", "--recursive", "--archive", str(src), str(dst)], check=True, capture_output=True)
                return
                
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"Fast copy failed (sys={system}): {e}. Falling back to shutil.copytree.")
    
    # Fallback
    shutil.copytree(src, dst, dirs_exist_ok=dirs_exist_ok, ignore=ignore)
