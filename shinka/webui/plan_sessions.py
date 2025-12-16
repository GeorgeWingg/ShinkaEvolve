"""Planning-only session harness for New Evolution Run UI.

Plan sessions are separate from evolution edit/eval sessions. They are used to
help users craft high-quality Evolution Prompt (edit plan) and Evaluator Prompt
without executing code. Sessions are backed by the OpenAI API (GPT-5.2 by default)
and stream Shinka-style JSON events to `session_log.jsonl` so the WebUI can reuse
the existing `/api/session_state` polling/parsing.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

try:  # pragma: no cover
    import openai
except Exception:  # pragma: no cover
    openai = None  # type: ignore

from shinka.tools.codex_session_registry import (
    register_session_process,
    update_session_process,
)
from shinka.tools.credentials import get_api_key
from shinka.llm.models.pricing import OPENAI_MODELS

logger = logging.getLogger(__name__)

PlanKind = Literal["edit", "eval"]

PLAN_SESSIONS_ROOT = Path("/tmp/shinka_plan_sessions")
DEFAULT_MODEL = "gpt-5.2"


@dataclass
class PlanSessionMeta:
    session_id: str
    kind: PlanKind
    model: str
    created_at: float
    status: str = "running"
    goal: str = ""
    edit_prompt_background: str = ""  # For eval plans


def start_plan_session(
    *,
    kind: PlanKind,
    goal: str,
    context: Dict[str, Any],
    resume_session_id: Optional[str] = None,
    model: Optional[str] = None,
) -> Tuple[str, Path]:
    """Start or resume a planning-only session.

    Returns (session_id, session_dir). For new sessions this also emits an
    initial assistant response based on the goal + context.
    """
    if openai is None:
        raise RuntimeError("openai package not installed")

    # Check credential store first, then fall back to env vars and legacy files
    api_key = get_api_key("codex")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY not set. Save your key via the API Credentials dialog "
            "or set OPENAI_API_KEY environment variable."
        )

    session_id = resume_session_id or uuid.uuid4().hex
    session_dir = PLAN_SESSIONS_ROOT / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    conversation_path = session_dir / "conversation.json"
    events_path = session_dir / "session_log.jsonl"
    meta_path = session_dir / "session_meta.json"

    is_new = not resume_session_id or not conversation_path.exists()

    # Load or initialize conversation
    if conversation_path.exists():
        try:
            messages: List[Dict[str, str]] = json.loads(
                conversation_path.read_text(encoding="utf-8")
            )
        except Exception:
            messages = []
    else:
        messages = []

    selected_model = model or DEFAULT_MODEL

    edit_background = ""
    if kind == "eval":
        edit_background = str(context.get("edit_prompt_background") or "")

    meta = PlanSessionMeta(
        session_id=session_id,
        kind=kind,
        model=selected_model,
        created_at=time.time(),
        status="running",
        goal=goal,
        edit_prompt_background=edit_background,
    )
    meta_path.write_text(json.dumps(meta.__dict__), encoding="utf-8")

    # Register in session registry so /api/session_state can find it.
    pid = os.getpid()
    register_session_process(
        pid,
        prompt_preview=goal[:120],
        workdir=session_dir,
        session_kind=f"plan_{kind}",
        filename_key=session_id,
    )
    update_session_process(
        pid, filename_key=session_id, session_id=session_id, status="running"
    )

    # Emit init event for new sessions
    if is_new:
        _append_event(
            events_path,
            {
                "type": "init",
                "timestamp": time.time(),
                "session_id": session_id,
                "model": selected_model,
            },
        )

        initial_user_prompt = _build_initial_user_prompt(
            kind=kind, goal=goal, context=context
        )
        messages.append({"role": "user", "content": initial_user_prompt})
        assistant_text, usage = _call_openai(
            messages=messages,
            system_prompt=_build_system_prompt(kind=kind, edit_background=edit_background),
            model=selected_model,
            api_key=api_key,
        )
        messages.append({"role": "assistant", "content": assistant_text})
        conversation_path.write_text(json.dumps(messages), encoding="utf-8")
        _emit_assistant_and_usage(events_path, assistant_text, usage)

        if "PLAN_STATUS: FINAL" in assistant_text:
            _mark_session_completed(pid, session_id, meta_path)

    return session_id, session_dir


def append_plan_message(
    *,
    session_id: str,
    user_message: str,
    model: Optional[str] = None,
) -> None:
    """Append a user message to an existing plan session and emit assistant reply."""
    if openai is None:
        raise RuntimeError("openai package not installed")

    # Get API key
    api_key = get_api_key("codex")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    session_dir = PLAN_SESSIONS_ROOT / session_id
    conversation_path = session_dir / "conversation.json"
    events_path = session_dir / "session_log.jsonl"
    meta_path = session_dir / "session_meta.json"

    if not conversation_path.exists() or not meta_path.exists():
        raise FileNotFoundError(f"Plan session not found: {session_id}")

    meta_payload = json.loads(meta_path.read_text(encoding="utf-8"))
    kind: PlanKind = meta_payload.get("kind", "edit")
    edit_background = meta_payload.get("edit_prompt_background", "")
    selected_model = model or meta_payload.get("model") or DEFAULT_MODEL

    messages: List[Dict[str, str]] = json.loads(
        conversation_path.read_text(encoding="utf-8")
    )
    messages.append({"role": "user", "content": user_message})

    assistant_text, usage = _call_openai(
        messages=messages,
        system_prompt=_build_system_prompt(kind=kind, edit_background=edit_background),
        model=selected_model,
        api_key=api_key,
    )
    messages.append({"role": "assistant", "content": assistant_text})
    conversation_path.write_text(json.dumps(messages), encoding="utf-8")

    _emit_assistant_and_usage(events_path, assistant_text, usage)

    pid = os.getpid()
    if "PLAN_STATUS: FINAL" in assistant_text:
        _mark_session_completed(pid, session_id, meta_path)


# ------------------------- Prompt Construction -------------------------


# System prompt for planning EDITOR (Evolution Prompt)
EDITOR_SYSTEM_PROMPT = """You help users create evolution prompts for ShinkaEvolve.

## CRITICAL: Be Brief

Your responses must be SHORT. 2-3 sentences max before any draft. No walls of text. No long explanations. No multiple questions.

Bad: "Totally can do — but 'hi' is a bit too open-ended for me to aim ShinkaEvolve in the right direction. Quick clarifiers so I don't accidentally optimize the wrong thing: what are you evolving..."

Good: "Hey! What kind of code are you working on?"

## How to Respond

- **"hi" or greeting** → Ask ONE simple question: "Hey! What are you working on?" (10 words max)
- **Any hint of a goal** → Draft immediately, no questions
- **Vague/nonsense** → Draft a generic prompt, no questions

Never ask multiple questions. Never list options. Never explain what you need. Just be friendly and either ask ONE thing or draft something.

## When Drafting

Keep intro to 1 sentence, then the prompt:

**Role:** [1 line]
**Goal:** [1 line]
**Hints:** [2-3 bullet points max]
**Constraints:** [1-2 lines]
**Encouragement:** [1 line]

## Finishing

When user approves ("ok", "sure", "looks good", etc.):
PLAN_STATUS: FINAL

[final prompt text]
"""

# System prompt for planning EVALUATOR (Eval Prompt)
EVALUATOR_SYSTEM_PROMPT = """You help users create evaluation prompts for ShinkaEvolve.

## CRITICAL: Be Brief

Your responses must be SHORT. 2-3 sentences max before any draft. No walls of text. No long explanations. No multiple questions.

Good: "Hey! What does your code output?"
Bad: Long paragraph with multiple questions.

## How to Respond

- **"hi" or greeting** → Ask ONE simple question: "Hey! What does your code produce?" (10 words max)
- **Any hint of a metric** → Draft immediately, no questions
- **Vague/nonsense** → Draft a generic scoring prompt, no questions

Never ask multiple questions. Never list options. Just be friendly and either ask ONE thing or draft something.

## When Drafting

Keep intro to 1 sentence, then the prompt:

**Task:** [1 line]
**Expected Output:** [1 line]
**Scoring:** [2-3 lines]
**Validity:** [1 line]
**Feedback:** [1 line]

## Finishing

When user approves ("ok", "sure", "looks good", etc.):
PLAN_STATUS: FINAL

[final prompt text]
"""


def _build_system_prompt(*, kind: PlanKind, edit_background: str = "") -> str:
    """Build the system prompt for the planning session based on kind."""
    if kind == "edit":
        return EDITOR_SYSTEM_PROMPT
    else:
        # Evaluator mode
        prompt = EVALUATOR_SYSTEM_PROMPT
        if edit_background.strip():
            prompt += (
                "\n\n## Background Context\n"
                "The following Evolution Prompt is already configured. "
                "Use this to align your evaluation criteria with the mutation goals:\n\n"
                f"```\n{edit_background.strip()}\n```\n"
            )
        return prompt


def _build_initial_user_prompt(
    *, kind: PlanKind, goal: str, context: Dict[str, Any]
) -> str:
    parts = [f"High-level goal:\n{goal.strip()}\n"]

    if kind == "edit":
        language = context.get("language")
        if language:
            parts.append(f"Language: {language}")
    else:
        parts.append(
            "Design an evaluation prompt that scores the evolved code. "
            "Be deterministic and explicit about metrics/logging."
        )

    existing_task = str(context.get("existing_task_sys_msg") or "").strip()
    existing_eval = str(context.get("existing_eval_prompt") or "").strip()

    if existing_task:
        parts.append(f"\nExisting Evolution Prompt (for continuity):\n{existing_task}")
    if existing_eval:
        parts.append(f"\nExisting Evaluator Prompt (for continuity):\n{existing_eval}")

    parts.append(
        "\nStart by asking clarifying questions if anything is missing."
    )
    return "\n".join(parts).strip()


# ------------------------- OpenAI Call + Events -------------------------


def _call_openai(
    *, messages: List[Dict[str, str]], system_prompt: str, model: str, api_key: str
) -> Tuple[str, Dict[str, Any]]:
    client = openai.OpenAI(api_key=api_key)  # type: ignore[attr-defined]

    response = client.responses.create(
        model=model,
        input=[{"role": "system", "content": system_prompt}, *messages],
        temperature=0.2,
    )

    text = ""
    try:
        text = response.output[0].content[0].text
    except Exception:
        try:
            text = response.output[1].content[0].text
        except Exception:
            # Last-resort scan
            for out in getattr(response, "output", []) or []:
                for c in getattr(out, "content", []) or []:
                    candidate = getattr(c, "text", None)
                    if candidate:
                        text = candidate
                        break
                if text:
                    break
    if not text:
        text = "(empty response)"

    usage = getattr(response, "usage", None)
    usage_dict = {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "total_tokens": getattr(usage, "total_tokens", 0),
    }

    # Cost calculation if pricing known
    if model in OPENAI_MODELS and usage:
        input_cost = OPENAI_MODELS[model]["input_price"] * usage.input_tokens
        output_cost = OPENAI_MODELS[model]["output_price"] * usage.output_tokens
        usage_dict["total_cost_usd"] = float(input_cost + output_cost)
    else:
        usage_dict["total_cost_usd"] = 0.0

    return text.strip(), usage_dict


def _emit_assistant_and_usage(
    events_path: Path, assistant_text: str, usage: Dict[str, Any]
) -> None:
    _append_event(
        events_path,
        {
            "type": "agent_message",
            "timestamp": time.time(),
            "item": {"type": "agent_message", "text": assistant_text},
        },
    )
    _append_event(
        events_path,
        {"type": "usage", "timestamp": time.time(), "usage": usage},
    )


def _append_event(path: Path, event: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        json.dump(event, handle)
        handle.write("\n")


def _mark_session_completed(pid: int, session_id: str, meta_path: Path) -> None:
    try:
        meta_payload = json.loads(meta_path.read_text(encoding="utf-8"))
        meta_payload["status"] = "completed"
        meta_path.write_text(json.dumps(meta_payload), encoding="utf-8")
    except Exception:
        pass
    update_session_process(pid, filename_key=session_id, status="completed")

