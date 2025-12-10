"""Prompt fragments specialized for agentic editing sessions.

In agentic mode, the CLI harness (Codex/Gemini/Claude) owns the system prompt.
We provide minimal context in the user prompt - just the task and score.
The agent can explore the workspace itself.
"""

# Empty system prompt - let the CLI use its own system prompt
AGENTIC_SYS_FORMAT = ""

# Minimal user prompt - just task, score context, and optional feedback
AGENTIC_ITER_MSG = """{task_context}
# Score

{score_context}
{text_feedback_section}
Explore the workspace and make improvements. When done, explain what you changed and why.
"""
