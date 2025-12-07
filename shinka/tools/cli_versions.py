"""
CLI Version Detection Tool.

Detects installed versions of Codex, Gemini, and Claude CLIs.
"""

import shutil
import subprocess
import re
from typing import Dict, Optional

def get_cli_version(cli_name: str) -> Optional[str]:
    """
    Get the version of a CLI tool by running `{cli_name} --version`.
    
    Args:
        cli_name: Name of the CLI executable (codex, gemini, claude)
        
    Returns:
        Version string if found, else None.
    """
    if not shutil.which(cli_name):
        return None
        
    try:
        # Run with timeout to avoid hanging
        result = subprocess.run(
            [cli_name, "--version"], 
            capture_output=True, 
            text=True, 
            timeout=2
        )
        if result.returncode == 0:
            # Parse version from output (e.g., "2.0.55 (Claude Code)" -> "2.0.55")
            output = result.stdout.strip()
            # Common pattern: match first sequence of digits and dots
            match = re.search(r"(\d+\.\d+\.\d+)", output)
            if match:
                return match.group(1)
            return output  # Return full string if pattern fails
    except Exception:
        pass
        
    return None

def get_all_cli_versions() -> Dict[str, Optional[str]]:
    """Get versions for all supported CLIs."""
    return {
        "codex": get_cli_version("codex"),
        "gemini": get_cli_version("gemini"),
        "claude": get_cli_version("claude"),
    }
