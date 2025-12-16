"""
Jules Backend Runner for Shinka Agentic Mode.

Implements the AgentRunner protocol for Google Jules, enabling Jules
to be used as a cloud-based code editing agent alongside local CLI
backends (Codex, Gemini, Claude, Shinka).

Key Differences from CLI Backends:
- Jules operates on GitHub repos, not local directories
- Async/polling model instead of streaming
- Uses the Staging Directory Pattern to sync files

Flow:
1. Push workdir contents to temporary GitHub branch
2. Create Jules session targeting that branch
3. Poll for completion, yielding normalized events
4. Pull changes back to workdir
5. Cleanup temporary branch
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from shinka.edit.jules_api import (
    JulesActivity,
    JulesAPIClient,
    JulesAPIError,
    JulesQuotaExhaustedError,
    JulesRepoNotConnectedError,
    JulesSession,
    JulesSessionError,
    JulesTimeoutError,
    JulesUnavailableError,
    ensure_jules_api_key,
    ensure_jules_available,
)
from shinka.edit.jules_sync import (
    JulesGitHubSync,
    JulesSyncError,
    ensure_github_token,
    generate_jules_branch_name,
)

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------


class JulesExecutionError(RuntimeError):
    """Raised when a Jules task execution fails."""


# -----------------------------------------------------------------------------
# Event Normalization
# -----------------------------------------------------------------------------


def _normalize_activity_to_events(
    activity: JulesActivity,
    session: JulesSession,
) -> List[Dict[str, Any]]:
    """Convert a Jules activity to Shinka-compatible timeline events.

    Maps Jules activity types to Shinka event format for compatibility
    with the timeline UI. Some activities produce multiple events.

    Jules Activity Types:
    - PlanGenerated: Agent creates a detailed plan with steps
    - ProgressUpdated: Agent reports progress (title + description)
    - AgentMessaged: Agent posts a message
    - UserMessaged: User posts a message
    - PlanApproved: User approves a plan
    - SessionCompleted: Session finished with artifacts (changeSets, gitPatches)
    - SessionFailed: Session encountered an error

    Args:
        activity: Jules activity from API
        session: Current session for context

    Returns:
        List of normalized event dicts
    """
    events: List[Dict[str, Any]] = []
    activity_type = activity.type.lower()
    base_event = {
        "timestamp": activity.timestamp,
        "session_id": session.id,
    }

    # PlanGenerated → Structured plan display with numbered steps
    if "plangenerated" in activity_type or "plan" in activity_type and "approved" not in activity_type:
        plan_data = activity.content.get("planGenerated", activity.content.get("plan", activity.content))
        if isinstance(plan_data, dict):
            plan_obj = plan_data.get("plan", plan_data)
            steps = plan_obj.get("steps", [])
            if steps:
                plan_text = "**Plan Generated**\n\n"
                for step in steps:
                    idx = step.get("index", 0) + 1
                    title = step.get("title", "")
                    plan_text += f"{idx}. {title}\n"
            else:
                plan_text = f"**Plan Generated**\n\n{plan_obj}"
        else:
            plan_text = f"**Plan Generated**\n\n{plan_data}"

        events.append({
            **base_event,
            "type": "agent_message",
            "item": {
                "type": "agent_message",
                "text": plan_text,
                "role": "assistant",
            },
        })

    # ProgressUpdated → Status card with title and description
    elif "progress" in activity_type:
        progress = activity.content.get("progressUpdated", activity.content)
        title = progress.get("title", "Progress Update")
        desc = progress.get("description", "")
        events.append({
            **base_event,
            "type": "agent_message",
            "item": {
                "type": "agent_message",
                "text": f"**{title}**\n\n{desc}" if desc else f"**{title}**",
                "role": "assistant",
            },
        })

    # PlanApproved → System message
    elif "approved" in activity_type:
        events.append({
            **base_event,
            "type": "agent_message",
            "item": {
                "type": "agent_message",
                "text": "Plan approved by user",
                "role": "system",
            },
        })

    # AgentMessaged → Agent reasoning/message
    elif "agentmessaged" in activity_type or (
        "message" in activity_type and "user" not in activity_type
    ):
        msg_data = activity.content.get("agentMessaged", activity.content)
        message = msg_data.get("message", str(msg_data))
        events.append({
            **base_event,
            "type": "agent_message",
            "item": {
                "type": "agent_message",
                "text": message,
                "role": "assistant",
            },
        })

    # UserMessaged → User input
    elif "usermessaged" in activity_type or "user" in activity_type:
        msg_data = activity.content.get("userMessaged", activity.content)
        message = msg_data.get("message", str(msg_data))
        events.append({
            **base_event,
            "type": "agent_message",
            "item": {
                "type": "agent_message",
                "text": message,
                "role": "user",
            },
        })

    # SessionCompleted → Result event + tool_use with diff for code changes
    elif "completed" in activity_type:
        # Extract artifacts (changeSets with git patches)
        completed_data = activity.content.get("sessionCompleted", activity.content)
        artifacts = completed_data.get("artifacts", activity.content.get("artifacts", []))

        for artifact in artifacts:
            if isinstance(artifact, dict) and "changeSet" in artifact:
                change_set = artifact["changeSet"]
                git_patch = change_set.get("gitPatch", {})
                diff = git_patch.get("unidiffPatch", "")
                commit_msg = git_patch.get("suggestedCommitMessage", "Code changes")
                source = change_set.get("source", "")

                # Emit tool_use with diff for timeline visualization
                if diff:
                    events.append({
                        **base_event,
                        "type": "tool_use",
                        "tool_name": "jules_apply_changes",
                        "tool_id": f"jules_{session.id}",
                        "parameters": {
                            "commit_message": commit_msg,
                            "source": source,
                        },
                        "diff": diff,
                    })

        # Final result event
        result_text = "Session completed successfully"
        if session.pr_url:
            result_text += f"\n\nPR: {session.pr_url}"
        events.append({
            **base_event,
            "type": "result",
            "is_error": False,
            "subtype": "success",
            "result": result_text,
        })

    # SessionFailed → Error result
    elif "failed" in activity_type or "error" in activity_type:
        failed_data = activity.content.get("sessionFailed", activity.content)
        error_msg = (
            failed_data.get("message")
            or failed_data.get("error")
            or session.error
            or "Session failed"
        )
        events.append({
            **base_event,
            "type": "result",
            "is_error": True,
            "subtype": "error",
            "result": str(error_msg),
        })

    # Generic fallback
    else:
        events.append({
            **base_event,
            "type": "agent_message",
            "item": {
                "type": "agent_message",
                "text": f"[{activity.type}] {activity.content}",
                "role": "assistant",
            },
        })

    return events


# Legacy single-event wrapper for backward compatibility
def _normalize_activity_to_event(
    activity: JulesActivity,
    session: JulesSession,
) -> Dict[str, Any]:
    """Convert a Jules activity to a single Shinka event (legacy)."""
    events = _normalize_activity_to_events(activity, session)
    return events[0] if events else {
        "type": "agent_message",
        "timestamp": activity.timestamp,
        "session_id": session.id,
        "item": {"type": "agent_message", "text": str(activity.content), "role": "assistant"},
    }


def _create_init_event(session: JulesSession) -> Dict[str, Any]:
    """Create initialization event for session start."""
    return {
        "type": "init",
        "session_id": session.id,
        "model": "jules",  # Jules uses Gemini 2.5 Pro internally
        "timestamp": time.time(),
    }


def _create_usage_event(
    session: JulesSession,
    elapsed_seconds: float,
    files_changed: int = 0,
) -> Dict[str, Any]:
    """Create usage/telemetry event for session end."""
    return {
        "type": "usage",
        "session_id": session.id,
        "elapsed_seconds": elapsed_seconds,
        "files_changed": files_changed,
        # Jules is subscription-based, not token-based
        # Track tasks consumed instead
        "jules_tasks_consumed": 1,
        "total_cost_usd": 0.0,  # Included in subscription
    }


# -----------------------------------------------------------------------------
# Main Runner Function
# -----------------------------------------------------------------------------


def run_jules_task(
    user_prompt: str,
    workdir: Path,
    *,
    system_prompt: Optional[str] = None,
    profile: Optional[str] = None,  # Not used for Jules
    sandbox: str = "",  # Not used (cloud-based)
    approval_mode: str = "full-auto",
    max_seconds: int = 1800,
    max_events: int = 50,  # Not used (polling-based)
    extra_cli_config: Dict[str, Any],
    codex_path: Optional[str] = None,  # Not used
    cli_path: Optional[str] = None,  # Not used
    resume_session_id: Optional[str] = None,
    session_kind: str = "edit",
    registry_workdir: Optional[Path] = None,
    parent_id: Optional[str] = None,
    generation: Optional[int] = None,
    patch_type: Optional[str] = None,
    results_dir: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    """Execute a Jules task and stream normalized JSON events.

    This function bridges Jules' async/polling model to the streaming
    event iterator expected by AgenticEditor.

    Required extra_cli_config:
        github_repo: str - GitHub repo in "owner/repo" format

    Optional extra_cli_config:
        base_branch: str - Branch to start from (default: "main")
        automation_mode: str - "AUTO_CREATE_PR" or "" (default: AUTO_CREATE_PR)
        poll_interval: int - Seconds between status polls (default: 15)
        cleanup_branch: bool - Delete temp branch after (default: True)
        require_plan_approval: bool - Require Jules plan approval before execution.
                                 If not set, derived from approval_mode != "full-auto".
        auto_approve_plan: bool - If require_plan_approval is True, auto-approve
                                 the plan on first PlanGenerated activity (default: True).
        resume_branch_name: str - Required when resume_session_id is provided; branch
                                 Jules is working on so we can pull changes.
        jules_api_key: str - Override API key (default: from env)
        github_token: str - Override GitHub token (default: from env)

    Args:
        user_prompt: Task description for Jules
        workdir: Local directory with files to sync
        system_prompt: Optional system context (prepended to prompt)
        extra_cli_config: Configuration dict with github_repo etc.
        max_seconds: Maximum wait time (default: 1800 = 30 min)
        session_kind: "edit" or "eval"
        Other args: Metadata for tracking

    Yields:
        Normalized JSON events matching Shinka format

    Raises:
        JulesExecutionError: If task fails
    """
    start_time = time.time()
    events_emitted = 0

    # Extract configuration
    github_repo = extra_cli_config.get("github_repo")
    if not github_repo:
        raise JulesExecutionError(
            "github_repo is required in extra_cli_config for Jules backend"
        )

    base_branch = extra_cli_config.get("base_branch", "main")
    automation_mode = extra_cli_config.get("automation_mode", "AUTO_CREATE_PR")
    poll_interval = extra_cli_config.get("poll_interval", 15)
    cleanup_branch = extra_cli_config.get("cleanup_branch", True)

    # Derive plan approval behavior from approval_mode unless explicitly overridden.
    require_plan_approval_raw = extra_cli_config.get("require_plan_approval")
    require_plan_approval = (
        bool(require_plan_approval_raw)
        if require_plan_approval_raw is not None
        else approval_mode != "full-auto"
    )
    auto_approve_plan = bool(extra_cli_config.get("auto_approve_plan", True))

    # Resume handling: require explicit branch name when resuming.
    is_resuming = resume_session_id is not None
    resume_branch_name = extra_cli_config.get("resume_branch_name") if is_resuming else None
    if is_resuming and not resume_branch_name:
        raise JulesExecutionError(
            "resume_session_id is not supported for Jules without resume_branch_name "
            "in extra_cli_config."
        )

    # Combine system prompt with user prompt if provided
    full_prompt = user_prompt
    if system_prompt:
        full_prompt = f"{system_prompt}\n\n{user_prompt}"

    session: Optional[JulesSession] = None
    files_changed = 0

    def _yield_with_cap(ev: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
        """Yield an event while enforcing max_events parity with CLI backends."""
        nonlocal events_emitted
        events_emitted += 1
        if max_events and events_emitted > max_events:
            # Best-effort cancel to avoid burning Jules quota.
            if session is not None:
                try:
                    client.cancel_session(session.id)
                except Exception:
                    pass
            raise JulesExecutionError(
                "Jules emitted more events than allowed (max_events)."
            )
        yield ev

    # Initialize branch_name before try block to avoid UnboundLocalError in finally
    branch_name = None

    try:
        # Get credentials (inside try so missing creds normalize to JulesExecutionError)
        api_key = extra_cli_config.get("jules_api_key") or ensure_jules_api_key()
        github_token = extra_cli_config.get("github_token") or ensure_github_token()

        # Initialize clients
        client = JulesAPIClient(api_key)
        sync = JulesGitHubSync(github_token)

        # Determine branch name
        branch_name = resume_branch_name if is_resuming else generate_jules_branch_name()

        # 1. Verify repo is connected in Jules
        logger.info(f"Verifying {github_repo} is connected in Jules...")
        client.verify_repo_connected(github_repo)

        if not is_resuming:
            # 2. Push workdir to temporary branch
            logger.info(f"Pushing workdir to {github_repo}:{branch_name}...")
            sync_result = sync.push_to_jules_branch(
                workdir=workdir,
                github_repo=github_repo,
                branch_name=branch_name,
                base_branch=base_branch,
                commit_message=f"Shinka evolution snapshot (gen {generation})"
                if generation is not None
                else "Shinka evolution snapshot",
            )

            if not sync_result.success:
                raise JulesExecutionError(
                    f"Failed to push to GitHub: {sync_result.error}"
                )

            # 3. Create Jules session
            logger.info(f"Creating Jules session for {github_repo}:{branch_name}...")
            session = client.create_session(
                prompt=full_prompt,
                github_repo=github_repo,
                branch=branch_name,
                automation_mode=automation_mode,
                title=f"Shinka Evolution - Gen {generation}" if generation else None,
                require_plan_approval=require_plan_approval,
            )
        else:
            # Resume an existing session on an existing branch.
            logger.info(
                f"Resuming Jules session {resume_session_id} for {github_repo}:{branch_name}..."
            )
            session = client.get_session(resume_session_id)  # type: ignore[arg-type]

        # Yield init event
        yield from _yield_with_cap(_create_init_event(session))

        # 4. Poll for completion, yielding activities as events
        logger.info(f"Polling Jules session {session.id}...")
        seen_activities: set[str] = set()
        plan_auto_approved = False

        while True:
            elapsed = time.time() - start_time
            if elapsed > max_seconds:
                raise JulesTimeoutError(
                    f"Jules session {session.id} did not complete within "
                    f"{max_seconds} seconds"
                )

            # Get current session status
            session = client.get_session(session.id)

            # Get and yield new activities
            activities = client.get_all_activities(session.id)
            for activity in activities:
                activity_key = f"{activity.timestamp}:{activity.type}"
                if activity_key in seen_activities:
                    continue
                seen_activities.add(activity_key)

                # If plan approval is required, handle first PlanGenerated activity.
                activity_type = activity.type.lower()
                is_plan_generated = (
                    "plangenerated" in activity_type
                    or ("plan" in activity_type and "approved" not in activity_type)
                )
                if require_plan_approval and is_plan_generated and not plan_auto_approved:
                    # Always yield the plan itself.
                    for event in _normalize_activity_to_events(activity, session):
                        yield from _yield_with_cap(event)

                    if auto_approve_plan:
                        try:
                            client.approve_plan(session.id)
                        except Exception as e:
                            raise JulesExecutionError(
                                f"Failed to auto-approve Jules plan: {e}"
                            ) from e
                        plan_auto_approved = True
                        auto_event = {
                            "timestamp": activity.timestamp,
                            "session_id": session.id,
                            "type": "agent_message",
                            "item": {
                                "type": "agent_message",
                                "text": (
                                    "Plan auto‑approved by Shinka because approval_mode "
                                    "was not full-auto. Set extra_cli_config.auto_approve_plan=false "
                                    "to require manual approval."
                                ),
                                "role": "system",
                            },
                        }
                        yield from _yield_with_cap(auto_event)
                    else:
                        raise JulesExecutionError(
                            "Jules plan approval required but auto_approve_plan=false. "
                            "Approve the plan in the Jules UI or rerun with approval_mode=full-auto."
                        )
                    continue

                # Use multi-event normalization for rich timeline events
                for event in _normalize_activity_to_events(activity, session):
                    yield from _yield_with_cap(event)

            # Check if complete
            if session.is_complete:
                if not session.is_success:
                    error_msg = session.error or f"Session failed: {session.status}"
                    logger.error(f"Jules session {session.id} failed: {error_msg}")
                    raise JulesSessionError(error_msg)
                break

            logger.debug(
                f"Jules session {session.id}: {session.status} "
                f"(elapsed: {elapsed:.0f}s)"
            )
            time.sleep(poll_interval)

        # 5. Pull changes back to workdir
        logger.info(f"Pulling changes from {github_repo}:{branch_name}...")
        changed_files = sync.pull_from_jules_branch(
            workdir=workdir,
            github_repo=github_repo,
            branch_name=branch_name,
        )
        files_changed = len(changed_files)

        # Yield synthetic events for file changes
        for rel_path, content in changed_files.items():
            yield from _yield_with_cap({
                "type": "command_execution",
                "item": {
                    "type": "command_execution",
                    "command": f"jules_file_change: {rel_path}",
                    "exit_code": 0,
                    "stdout": f"Changed: {rel_path}" if content != "[deleted]" else f"Deleted: {rel_path}",
                    "stderr": "",
                },
                "session_id": session.id,
            })

        logger.info(
            f"Jules session {session.id} completed. "
            f"Changed {files_changed} files."
        )

    except JulesUnavailableError as e:
        logger.error(f"Jules unavailable: {e}")
        raise JulesExecutionError(str(e)) from e

    except JulesRepoNotConnectedError as e:
        logger.error(f"Jules repo not connected: {e}")
        raise JulesExecutionError(str(e)) from e

    except JulesQuotaExhaustedError as e:
        logger.error(f"Jules quota exhausted: {e}")
        raise JulesExecutionError(str(e)) from e

    except JulesAPIError as e:
        logger.error(f"Jules API error: {e}")
        raise JulesExecutionError(str(e)) from e

    except JulesSyncError as e:
        logger.error(f"Jules sync error: {e}")
        raise JulesExecutionError(str(e)) from e

    finally:
        # 6. Cleanup temporary branch
        if cleanup_branch and branch_name:
            logger.info(f"Cleaning up branch {branch_name}...")
            try:
                sync.cleanup_branch(github_repo, branch_name)
            except Exception as e:
                logger.warning(f"Failed to cleanup branch {branch_name}: {e}")

    # Yield final usage event
    elapsed = time.time() - start_time
    yield from _yield_with_cap(_create_usage_event(
        session=session,
        elapsed_seconds=elapsed,
        files_changed=files_changed,
    ))


# -----------------------------------------------------------------------------
# Evaluation Mode (Using Jules Critic)
# -----------------------------------------------------------------------------

JULES_EVAL_PROMPT_TEMPLATE = """
You are evaluating code changes made by an AI coding agent.

**Task Context:**
{task_context}

**Evaluation Criteria:**
{eval_criteria}

**Code to Review:**
{code_diff}

**Instructions:**
1. Review the code changes carefully
2. Identify any issues: bugs, logic errors, edge cases, inefficiencies
3. Assess whether the changes correctly address the task
4. Provide a score from 0 to 100 where:
   - 0-30: Fundamentally broken or incorrect
   - 31-50: Has significant issues
   - 51-70: Works but has notable problems
   - 71-85: Good with minor issues
   - 86-100: Excellent, production-ready

**Response Format:**
```json
{{
  "score": <number 0-100>,
  "correct": <true if score >= 50 else false>,
  "feedback": "<detailed feedback>",
  "issues": ["<issue 1>", "<issue 2>", ...]
}}
```
"""


def run_jules_eval_task(
    code_to_review: str,
    eval_criteria: str,
    task_context: str,
    workdir: Path,
    *,
    extra_cli_config: Dict[str, Any],
    max_seconds: int = 600,
    **kwargs,
) -> Iterator[Dict[str, Any]]:
    """Run Jules as an evaluator using its built-in critic capabilities.

    Creates a Jules session with a critique-focused prompt to leverage
    the internal critic agent for code review.

    Args:
        code_to_review: Code diff or content to evaluate
        eval_criteria: Specific evaluation criteria
        task_context: Original task description
        workdir: Working directory
        extra_cli_config: Config with github_repo etc.
        max_seconds: Max wait time

    Yields:
        Events from evaluation session
    """
    eval_prompt = JULES_EVAL_PROMPT_TEMPLATE.format(
        task_context=task_context,
        eval_criteria=eval_criteria,
        code_diff=code_to_review,
    )

    yield from run_jules_task(
        user_prompt=eval_prompt,
        workdir=workdir,
        extra_cli_config=extra_cli_config,
        max_seconds=max_seconds,
        session_kind="eval",
        **kwargs,
    )


# -----------------------------------------------------------------------------
# Re-exports for convenience
# -----------------------------------------------------------------------------

__all__ = [
    "JulesExecutionError",
    "JulesUnavailableError",
    "ensure_jules_available",
    "run_jules_task",
    "run_jules_eval_task",
]
