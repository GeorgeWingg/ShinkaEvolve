"""Codex-powered evaluator that runs deterministic scripts inside the repo."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TYPE_CHECKING

from shinka.edit.agentic import CommandResult
from shinka.edit.codex_cli import CodexExecutionError, run_codex_task
from shinka.edit.types import AgentRunner
from shinka.prompts import AGENTIC_EVAL_SYS, AGENTIC_EVAL_USER

if TYPE_CHECKING:  # pragma: no cover
    from shinka.core.runner import AgenticEvaluatorConfig


@dataclass
class AgenticEvaluatorResult:
    """Structured output from a Codex evaluation session."""

    metrics: Dict[str, Any]
    correct: bool
    error_message: Optional[str]
    stdout_log: str
    stderr_log: str
    session_log: List[str]
    commands_run: List[CommandResult]
    session_log_path: Path
    session_events: List[Dict[str, Any]]
    session_id: Optional[str]
    session_dir: Path
    elapsed_seconds: float


class AgenticEvaluator:
    """Drive the Codex-based evaluator from the repository root."""

    def __init__(
        self,
        config: "AgenticEvaluatorConfig",
        *,
        codex_runner: AgentRunner = None,
        agent_runner: AgentRunner = None,  # Alias for codex_runner
    ) -> None:
        self.config = config
        # Accept either codex_runner or agent_runner for backward compatibility
        self.codex_runner = codex_runner or agent_runner or run_codex_task

    def evaluate(
        self,
        *,
        repo_root: Path,
        eval_command: Sequence[str],
        program_path: Path,
        results_path: Path,
        metrics_path: Path,
        eval_sessions_root: Path,
        task_name: str,
        results_dir: Optional[str] = None,
        eval_prompt: Optional[str] = None,
        max_score: float = 1.0,
    ) -> AgenticEvaluatorResult:
        session_uuid = uuid.uuid4().hex
        session_dir = eval_sessions_root / session_uuid
        session_dir.mkdir(parents=True, exist_ok=True)
        session_log_path = session_dir / "session_log.jsonl"

        user_prompt, system_prompt = self._build_prompt(
            task_name=task_name,
            eval_command=eval_command,
            program_path=program_path,
            results_path=results_path,
            metrics_path=metrics_path,
            eval_prompt=eval_prompt,
            max_score=max_score,
        )

        session_log: List[str] = []
        commands: List[CommandResult] = []
        session_events: List[Dict[str, Any]] = []
        resolved_session_id: Optional[str] = None

        start_time = time.monotonic()
        with session_log_path.open("w", encoding="utf-8") as handle:
            for event in self.codex_runner(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                workdir=repo_root,
                profile=self.config.codex_profile,
                sandbox=self.config.sandbox,
                approval_mode=self.config.approval_mode,
                max_seconds=self.config.max_seconds,
                max_events=self.config.max_turns,
                extra_cli_config=self.config.extra_cli_config,
                codex_path=self.config.codex_path,
                session_kind="eval",
                results_dir=results_dir,
            ):
                if isinstance(event, dict):
                    json.dump(event, handle)
                    handle.write("\n")
                    session_events.append(event)
                    if resolved_session_id is None:
                        resolved_session_id = _extract_session_id(event)

                item = event.get("item") if isinstance(event, dict) else None
                if not item:
                    continue
                if item.get("type") == "agent_message":
                    text = item.get("text")
                    if text:
                        session_log.append(text)
                elif item.get("type") == "command_execution":
                    commands.append(
                        CommandResult(
                            command=item.get("command"),
                            status=item.get("status"),
                            exit_code=item.get("exit_code"),
                            stdout=item.get("stdout"),
                            stderr=item.get("stderr"),
                        )
                    )
        elapsed = time.monotonic() - start_time

        if not metrics_path.exists():
            raise CodexExecutionError(
                f"Agentic evaluator did not produce metrics at {metrics_path}"
            )

        # Parse metrics with error handling for malformed JSON
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse metrics.json: {e}")
            metrics = {"error": f"Invalid JSON in metrics: {e}"}

        correct_payload: Dict[str, Any] = {}
        correct_file = results_path / "correct.json"
        if correct_file.exists():
            try:
                correct_payload = json.loads(correct_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse correct.json: {e}")
                correct_payload = {"correct": False, "error": f"Invalid JSON: {e}"}
        correct_flag = bool(correct_payload.get("correct", False))
        error_msg = correct_payload.get("error")

        stdout_log = "\n".join((cmd.stdout or "") for cmd in commands if cmd.stdout)
        stderr_log = "\n".join((cmd.stderr or "") for cmd in commands if cmd.stderr)

        metrics.setdefault("evaluation_time_seconds", elapsed)

        return AgenticEvaluatorResult(
            metrics=metrics,
            correct=correct_flag,
            error_message=error_msg,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            session_log=session_log,
            commands_run=commands,
            session_log_path=session_log_path,
            session_events=session_events,
            session_id=resolved_session_id,
            session_dir=session_dir,
            elapsed_seconds=elapsed,
        )

    def _build_prompt(
        self,
        *,
        task_name: str,
        eval_command: Sequence[str],
        program_path: Path,
        results_path: Path,
        metrics_path: Path,
        eval_prompt: Optional[str],
        max_score: float = 1.0,
    ) -> tuple[str, str]:
        # Build a user prompt that always demands metrics.json, even when no eval command is provided.
        eval_criteria = eval_prompt.strip() if eval_prompt else ""
        if eval_command:
            command_str = " ".join(eval_command)
            user = AGENTIC_EVAL_USER.format(
                task_name=task_name,
                eval_command=command_str,
                program_path=program_path,
                results_path=results_path,
                metrics_path=metrics_path,
                max_score=max_score,
            )
            if eval_criteria:
                user += f"\n\nEvaluation criteria:\n{eval_criteria}\n"
        else:
            user = (
                f"# Evaluation Task (no script provided)\n\n"
                f"- Task: {task_name}\n"
                f"- Working directory: repository root\n"
                f"- Program path: {program_path}\n"
                f"- Results path: {results_path}\n"
                f"- Metrics JSON: {metrics_path}\n"
                f"- Max score: {max_score}\n\n"
                "No evaluation command was supplied.\n"
                "1) Inspect the workspace/program as needed.\n"
                "2) Judge the submission against the evaluation criteria below.\n"
                "3) Write a JSON file at the metrics path with at least these keys:\n"
                f'   {{"combined_score": <float 0-{max_score}>, "details": <short reason>}}.\n'
                "   You may add more fields if useful.\n"
                f"4) Write `{results_path}/correct.json` with:\n"
                '   {"correct": <true if code works>, "error": <null or error message>}.\n'
                "   Be generous for open-ended tasks - if the code runs and does something meaningful, mark correct=true.\n"
                "5) If you cannot score, still create both files with fallback values (score=0, correct=false).\n"
            )
            if eval_criteria:
                user += f"\nEvaluation criteria:\n{eval_criteria}\n"
            user += "\nFinish after both metrics.json and correct.json are written.\n"

        return user.strip(), AGENTIC_EVAL_SYS.format(max_score=max_score).strip()


def _extract_session_id(event: Dict[str, Any]) -> Optional[str]:
    if not isinstance(event, dict):
        return None

    event_type = event.get("type")
    if isinstance(event_type, str) and event_type.startswith("thread."):
        thread_id = event.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id

    session_id = event.get("session_id")
    if isinstance(session_id, str) and session_id:
        return session_id

    session_obj = event.get("session")
    if isinstance(session_obj, dict):
        candidate = session_obj.get("id") or session_obj.get("session_id")
        if isinstance(candidate, str) and candidate:
            return candidate

    return None
