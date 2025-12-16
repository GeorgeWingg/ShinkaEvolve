from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Protocol


class SandboxMode(str, Enum):
    """High-level sandbox intent, mapped to backend-specific flags.

    This enum abstracts the semantic differences in sandbox handling
    across different backends:

    Backend mappings:
    - SECURE: Codex "--sandbox workspace-write", Claude no skip, Gemini --sandbox
    - PERMISSIVE: Codex "--sandbox none", Claude --dangerously-skip-permissions, Gemini no sandbox
    - OFF: Same as PERMISSIVE (for clarity in naming)

    Legacy string values are normalized in AgenticConfig.__post_init__:
    - "workspace-write" → SECURE
    - "none" or "" → PERMISSIVE/OFF
    """
    SECURE = "secure"           # Maximum sandboxing, restricted permissions
    PERMISSIVE = "permissive"   # Minimal restrictions, auto-approve actions
    OFF = "off"                 # Alias for PERMISSIVE


def get_backend_sandbox_args(mode: str, backend: str) -> Dict[str, Any]:
    """Convert a SandboxMode value to backend-specific arguments.

    Args:
        mode: SandboxMode value (or legacy string after normalization)
        backend: Backend name ("codex", "claude", "gemini", "jules", "shinka")

    Returns:
        Dict with backend-specific sandbox arguments to merge into CLI config.
    """
    is_secure = mode == SandboxMode.SECURE or mode == "secure"

    if backend == "codex":
        return {"sandbox": "workspace-write" if is_secure else "none"}
    elif backend == "claude":
        # Claude: secure means don't skip permissions
        return {"skip_permissions": not is_secure}
    elif backend == "gemini":
        # Gemini: secure means enable --sandbox flag
        return {"sandbox_flag": is_secure}
    # Jules and ShinkaAgent ignore sandbox settings
    return {}


class AgentRunner(Protocol):
    """Protocol for an agent runner that executes a prompt in a workspace."""

    def __call__(
        self,
        user_prompt: str,
        workdir: Path,
        *,
        system_prompt: Optional[str] = None,
        profile: Optional[str],
        sandbox: str,
        approval_mode: str,
        max_seconds: int,
        max_events: int,
        extra_cli_config: Dict[str, Any],
        codex_path: Optional[str] = None,
        cli_path: Optional[str] = None,
        resume_session_id: Optional[str] = None,
        session_kind: str = "unknown",
        registry_workdir: Optional[Path] = None,
        parent_id: Optional[str] = None,
        generation: Optional[int] = None,
        patch_type: Optional[str] = None,
        results_dir: Optional[str] = None,
    ) -> Iterator[Dict[str, Any]]:
        ...
