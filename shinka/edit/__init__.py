from .agentic import AgentContext, AgenticEditor, AgentResult, CommandResult
from .apply_diff import apply_diff_patch, redact_immutable
from .apply_full import apply_full_patch
from .summary import summarize_diff
from .claude_cli import (
    run_claude_task,
    ensure_claude_available,
    ClaudeUnavailableError,
    ClaudeExecutionError,
)
from .shinka_agent import (
    run_shinka_task,
    ensure_shinka_available,
    ShinkaUnavailableError,
    ShinkaExecutionError,
)

__all__ = [
    "redact_immutable",
    "apply_diff_patch",
    "apply_full_patch",
    "summarize_diff",
    "AgenticEditor",
    "AgentContext",
    "AgentResult",
    "CommandResult",
    "run_claude_task",
    "ensure_claude_available",
    "ClaudeUnavailableError",
    "ClaudeExecutionError",
    "run_shinka_task",
    "ensure_shinka_available",
    "ShinkaUnavailableError",
    "ShinkaExecutionError",
]
