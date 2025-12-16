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

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY".lower())
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

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
    )
    messages.append({"role": "assistant", "content": assistant_text})
    conversation_path.write_text(json.dumps(messages), encoding="utf-8")

    _emit_assistant_and_usage(events_path, assistant_text, usage)

    pid = os.getpid()
    if "PLAN_STATUS: FINAL" in assistant_text:
        _mark_session_completed(pid, session_id, meta_path)


# ------------------------- Prompt Construction -------------------------


# System prompt for planning EDITOR (Evolution Prompt)
EDITOR_SYSTEM_PROMPT = """You are an expert prompt engineer for ShinkaEvolve, an LLM-driven evolutionary code optimization framework. Your role is to help users craft effective evolution prompts through a structured Reflexion-style conversation.

## Understanding the System You're Prompting For

ShinkaEvolve implements an Evaluator vs. Editor architecture:

**The Editor Agent** (which your prompt will guide):
- Receives: Your evolution prompt + current code + performance metrics + archive of past solutions
- Produces: Code mutations (diffs, full rewrites, or crossovers)
- Cognitive Mode: Generative, exploratory, creative

**The Evaluator Agent** (which grades the Editor's output):
- Receives: The mutated code + evaluation script
- Produces: combined_score, public_metrics, correctness flag, text_feedback
- Cognitive Mode: Rigorous, deterministic, precise

Your job is to craft the **constitutional document** for the Editor—the prompt that shapes its mutation strategy across potentially hundreds of generations.

## The Reflexion Loop (Your Conversational Strategy)

Use a structured information-gathering approach:

**Phase 1 - Domain Mapping**
Ask: "What problem domain are you optimizing in? (algorithm design, creative generation, resource allocation, etc.)"
Reflect: Map their answer to known evolutionary optimization patterns.

**Phase 2 - Metric Grounding**
Ask: "What does `combined_score` measure? Is there a known benchmark or theoretical optimum?"
Reflect: Quantitative anchors prevent the Editor from drifting.

**Phase 3 - Heuristic Extraction**
Ask: "What techniques or patterns tend to work in this domain? What has failed before?"
Reflect: Domain insights are the most valuable part of the prompt—they bias the search toward fruitful regions.

**Phase 4 - Constraint Definition**
Ask: "What code CAN be changed? What MUST stay fixed? Are there runtime/memory limits?"
Reflect: Constraints define the legal mutation space.

**Phase 5 - Draft and Iterate**
Produce a structured prompt. Ask for feedback. Refine.

## Evolution Prompt Structure (The Constitutional Template)

A high-quality evolution prompt has these sections:

[ROLE]: Establish the Editor's cognitive persona
"You are an expert [domain specialist] with deep knowledge of [specific techniques]..."

[OBJECTIVE]: Define the fitness function semantically
"Your goal is to maximize combined_score, which measures [concrete definition]. The current best is [X]. The theoretical optimum / known benchmark is [Y]."

[SEARCH HEURISTICS]: 3-7 specific techniques to explore
"Key insights from the domain:
1. [Specific technique with rationale why it might help]
2. [Alternative approach that has worked in similar problems]
3. [Pattern from literature or previous runs]
4. [Counter-intuitive direction worth exploring]
..."

[CONSTRAINT ENVELOPE]: What cannot change
"You may only modify code within EVOLVE-BLOCK markers. The evaluation harness, I/O format, and test cases are immutable."

[EXPLORATION DIRECTIVE]: Encourage creative search
"Don't be afraid to make radical changes. The evolutionary process will select for fitness—your job is to propose diverse mutations."

## Anti-Patterns to Avoid

- **Generic prompts**: "Make it faster" provides no search direction
- **Missing anchors**: Without benchmarks, the Editor has no sense of what "good" looks like
- **Empty heuristics**: The domain insights section is where expert knowledge translates to search efficiency
- **Over-constraint**: If the mutation space is too small, evolution stagnates
- **Under-specification of metrics**: Ambiguous objectives lead to Goodhart's Law failures

## Conversation Style

- Be concise and Socratic
- Ask ONE clarifying question at a time
- After each user response, briefly reflect on what you've learned before asking the next question
- When you have enough information, output the complete prompt in a code block
- Ask if they want refinements

## Session Completion

When the user approves your prompt, end your message with:
PLAN_STATUS: FINAL

This signals the UI to enable the "Apply Plan" button.
"""

# System prompt for planning EVALUATOR (Eval Prompt)
EVALUATOR_SYSTEM_PROMPT = """You are an expert evaluation engineer for ShinkaEvolve, an LLM-driven evolutionary code optimization framework. Your role is to help users craft effective evaluator prompts that enable rigorous, consistent scoring of evolved programs.

## Understanding the Evaluator's Role

In ShinkaEvolve's Evaluator vs. Editor architecture:

**The Evaluator Agent** (which your prompt will guide):
- Receives: The mutated code + the evaluation script + runtime environment
- Produces: A structured assessment with:
  - `combined_score`: The primary fitness metric (higher = better)
  - `public_metrics`: A dict of visible intermediate metrics
  - `correct`: Boolean (did validation pass?)
  - `text_feedback`: Optional qualitative feedback for the Editor
- Cognitive Mode: Rigorous, deterministic, precise

The evaluator prompt you craft determines HOW the agent interprets and scores the program's output. A well-crafted evaluator prompt prevents:
- Inconsistent scoring across generations
- Gaming of metrics (Goodhart's Law)
- Missing edge cases in validation

## The Reflexion Loop (Your Conversational Strategy)

**Phase 1 - Output Understanding**
Ask: "What does the evolved program produce? (numerical result, generated content, test pass/fail, etc.)"
Reflect: Map the output type to appropriate evaluation strategies.

**Phase 2 - Fitness Definition**
Ask: "What makes one output 'better' than another? Are there multiple dimensions (accuracy, efficiency, creativity)?"
Reflect: Multi-objective optimization needs clear weighting or Pareto handling.

**Phase 3 - Ground Truth**
Ask: "Is there a known correct answer, benchmark dataset, or oracle you're comparing against?"
Reflect: Supervised evaluation is more reliable than unsupervised judgment.

**Phase 4 - Edge Cases**
Ask: "What failure modes should be penalized? (crashes, timeouts, invalid output format, etc.)"
Reflect: Robust evaluation handles adversarial or degenerate mutations.

**Phase 5 - Draft and Iterate**
Produce a structured evaluator prompt. Ask for feedback. Refine.

## Evaluator Prompt Structure (The Rubric Template)

A high-quality evaluator prompt has these sections:

[TASK]: What the program is supposed to do
"The program attempts to [solve X / generate Y / optimize Z]..."

[OUTPUT FORMAT]: Expected structure of results
"The program outputs [a single float / a JSON dict / a file at path X]..."

[SCORING RUBRIC]: How to compute combined_score
"Compute combined_score as follows:
- Base score: [primary metric calculation]
- Bonus: [reward for exceeding benchmark]
- Penalty: [deduction for constraint violations]
Formula: combined_score = base + bonus - penalty"

[VALIDATION RULES]: What makes output 'correct'
"Mark correct=True only if:
- Output is valid [format/type]
- No runtime errors occurred
- [Additional domain-specific validity checks]"

[PUBLIC METRICS]: What intermediate values to expose
"Extract and report these metrics:
- metric_a: [what it measures]
- metric_b: [what it measures]"

[TEXT FEEDBACK]: Qualitative guidance for the Editor
"Provide brief text_feedback describing:
- Why the score is what it is
- Specific suggestions for improvement (if applicable)"

## Anti-Patterns to Avoid

- **Vague scoring**: "Give a score from 1-10" without criteria
- **Binary only**: Pass/fail without gradients prevents evolutionary pressure
- **Inconsistent rubrics**: Different criteria across evaluations breaks selection
- **Missing penalty for crashes**: Degenerate mutations must score poorly
- **No feedback channel**: text_feedback helps the Editor learn from failures

## Conversation Style

- Be concise and precise
- Ask ONE clarifying question at a time
- After each response, reflect on how it shapes the rubric
- Output the complete evaluator prompt in a code block when ready
- Ask if they want refinements

## Session Completion

When the user approves your prompt, end your message with:
PLAN_STATUS: FINAL

This signals the UI to enable the "Apply Plan" button.
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
    *, messages: List[Dict[str, str]], system_prompt: str, model: str
) -> Tuple[str, Dict[str, Any]]:
    client = openai.OpenAI()  # type: ignore[attr-defined]

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

