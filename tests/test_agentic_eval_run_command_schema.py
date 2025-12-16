import pytest


def test_agentic_eval_prompts_require_run_command():
    from shinka.prompts.prompts_agentic_eval import AGENTIC_EVAL_SYS, AGENTIC_EVAL_USER

    assert "private.run_command" in AGENTIC_EVAL_SYS
    assert "run_command" in AGENTIC_EVAL_USER
    assert '"private"' in AGENTIC_EVAL_USER or "private" in AGENTIC_EVAL_USER

