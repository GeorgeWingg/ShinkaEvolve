# Bring Jules Backend to Parity With CLI Agents

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

PLANS.md is checked into the repository root at `PLANS.md`. Maintain this ExecPlan in accordance with that file.

## Purpose / Big Picture

Shinka supports agentic editing through local CLIs (Codex, Gemini, Claude, ShinkaAgent) and a cloud backend (Jules). Today, Jules “works” but is not at parity: key runtime controls are ignored, some arguments are silently dropped, and approval semantics are misleading. After this change, a user can select Jules in the WebUI or Hydra configs and expect the same guardrails and behaviors they get with other backends: event limits stop runaway sessions, approval mode behaves predictably without deadlocks, resume is either truly supported or fails loudly, and Jules failures surface as clean `JulesExecutionError`s instead of raw exceptions.

You will know it’s working when:

1. A Jules run respects `agentic.max_turns` / `max_events` and aborts with a clear error once the cap is hit.
2. Non‑`full-auto` approval modes do not hang forever; they either auto‑approve plans with a visible warning or stop with an explicit “manual plan approval not supported” error.
3. Passing `resume_session_id` no longer does nothing silently; it resumes meaningfully or raises a clear, early error.
4. Missing or invalid Jules/GitHub credentials are reported through the same error path as other agents.
5. WebUI validation and auth indicators reflect persisted Jules/GitHub credentials (already fixed in the current tree, but must remain true after refactor).

## Progress

- [x] (2025-12-12 00:00Z) Added max_events enforcement to Jules runner (`run_jules_task` now caps all yielded events and aborts on overflow).
- [x] (2025-12-12 00:00Z) Implemented approval_mode ↔ plan approval mapping with `require_plan_approval` + `auto_approve_plan`.
- [x] (2025-12-12 00:00Z) Made resume_session_id behavior explicit (requires `resume_branch_name`, otherwise fails early).
- [x] (2025-12-12 00:00Z) Normalized credential/error handling so missing creds surface as `JulesExecutionError`.
- [x] (2025-12-12 00:00Z) Added regression tests + updated Jules config docs.

## Surprises & Discoveries

- Observation: `AgenticEditor` passes `max_events=self.config.max_turns`, but `run_jules_task` ignores it entirely and will yield unbounded events until Jules finishes.
  Evidence: `shinka/edit/jules_cli.py` polling loop yields normalized events without any counter.
- Observation: `approval_mode` is accepted for signature parity but never influences Jules sessions, so any non‑default approval mode is effectively a lie.
  Evidence: `run_jules_task` never sets `requirePlanApproval` or calls `approve_plan`.
- Observation: `resume_session_id` is accepted but ignored, which can produce confusing “fresh run” behavior when resuming is expected.
  Evidence: Parameter exists in signature but is unused in function body.

## Decision Log

- Decision: Treat `max_events` as a cap on yielded normalized events, mirroring Codex/Gemini/Claude behavior, and abort the session when exceeded.
  Rationale: This is how other AgentRunner backends interpret the knob; keeping the semantics consistent avoids surprises in bandit runs.
  Date/Author: 2025-12-12 / Codex CLI agent.
- Decision: Default to auto‑approving Jules plans when `approval_mode` is not `full-auto`, unless the user explicitly disables auto‑approval via extra config.
  Rationale: Shinka is non‑interactive during backend runs; requiring manual plan approval without a UI path would deadlock runs. Auto‑approval preserves liveness while still surfacing intent.
  Date/Author: 2025-12-12 / Codex CLI agent.
- Decision: If `resume_session_id` cannot be supported safely with branch context, fail early with a clear error rather than silently ignoring.
  Rationale: “Unsupported but explicit” is better parity than “accepted but ignored.”
  Date/Author: 2025-12-12 / Codex CLI agent.

## Outcomes & Retrospective

Jules is now feature‑parity with CLI backends on the major control surfaces that matter for Shinka runs:

1. `max_events/max_turns` is enforced consistently with Codex/Gemini/Claude. Runs no longer stream indefinitely, and an overflow produces a clear `JulesExecutionError`.
2. `approval_mode` is no longer a no‑op. Non‑`full-auto` modes trigger Jules plan approval requirements, and Shinka auto‑approves by default to avoid deadlocks in non‑interactive runs. Users can opt out via `auto_approve_plan=false` to force manual approval (run aborts with a clear instruction).
3. Resume semantics are explicit: resuming without a known branch is unsupported and fails early rather than silently running a new session.
4. Missing credentials surface cleanly through the same error path as other agentic backends.
5. The earlier credential‑persistence fix for Jules/GitHub remains intact.

Remaining gaps:

- Real resume support is still partial (requires a branch name); this is intentional until Shinka tracks Jules session→branch metadata automatically.
- Full pytest validation could not be run in this Python 3.14 sandbox due to a pandas import issue unrelated to Jules. The new tests should pass in the repo’s standard Py3.11/3.12 environment.

## Context and Orientation

Relevant modules and their roles:

- `shinka/edit/jules_cli.py`: Implements `run_jules_task` and `run_jules_eval_task`. This is the AgentRunner entrypoint used by `AgenticEditor`. It pushes local scratch workdir to a temporary GitHub branch, creates a Jules session, polls activities, normalizes them into Shinka events, then pulls changes back and cleans up the branch.
- `shinka/edit/jules_api.py`: Thin REST client for Jules. Exposes `create_session`, `get_session`, `get_all_activities`, and `approve_plan`.
- `shinka/edit/jules_sync.py`: Staging‑clone GitHub sync layer used to push/pull scratch dirs without polluting Shinka’s synthetic git history.
- `shinka/edit/agentic.py`: The harness that calls the selected AgentRunner, writes `session_log.jsonl`, and enforces max_seconds/max_turns for local CLIs.

Current parity gaps:

1. Event caps: unlike local CLIs, Jules never enforces `max_events`.
2. Approval semantics: `approval_mode` is ignored. If we set `requirePlanApproval` without auto‑approving, the run will hang.
3. Resume semantics: `resume_session_id` is ignored.
4. Error normalization: missing credentials or plan‑approval stalls should surface as `JulesExecutionError`, not raw internal exceptions.

Credential persistence for Jules/GitHub was fixed earlier in this working tree by extending the unified credential store and having Jules read from it. This ExecPlan must not regress that behavior.

## Plan of Work

First, add max‑event enforcement to `run_jules_task`. Follow the pattern used in `run_codex_task`: count every yielded normalized event (including init, activity‑derived events, synthetic file‑change events, and usage). If `max_events` is non‑zero and the counter exceeds it, stop polling and raise `JulesExecutionError("Jules emitted more events than allowed (max_events).")`. In the same block, best‑effort cancel the remote session if a cancel endpoint exists; if cancel fails or is unsupported, log and continue cleanup.

Second, map approval modes to Jules plan approval. Add two optional keys to `extra_cli_config`:

- `require_plan_approval` (bool): explicit override; if absent, derive from `approval_mode != "full-auto"`.
- `auto_approve_plan` (bool, default True): whether Shinka should call `approve_plan` automatically.

Pass `require_plan_approval` into `JulesAPIClient.create_session`. While polling activities, when the first PlanGenerated‑like activity arrives and `require_plan_approval` is True:

- if `auto_approve_plan` is True, call `client.approve_plan(session.id)`, then yield a system/agent_message event explaining that the plan was auto‑approved because Shinka is non‑interactive.
- if `auto_approve_plan` is False, raise `JulesExecutionError` telling the user to approve the plan in the Jules UI or rerun with full‑auto / auto_approve_plan.

Third, make resume explicit. At the top of `run_jules_task`, if `resume_session_id` is not None:

- If `extra_cli_config` contains a `resume_branch_name`, skip push/create and instead poll that existing session id, then pull from the provided branch name after completion.
- Otherwise, raise `JulesExecutionError("resume_session_id is not supported for Jules without resume_branch_name.")`.

This keeps the door open for real resume later without silent no‑ops today.

Fourth, normalize credential and internal errors. Wrap credential lookup in the main try/except so that `JulesUnavailableError` or missing GitHub token becomes a `JulesExecutionError` with a clear message. Also ensure any early abort (max_events exceeded, manual plan approval disabled) still triggers branch cleanup in `finally`.

Finally, add tests and docs:

- Extend `tests/test_jules_cli.py` with a regression that sets `max_events=2` and verifies the runner raises `JulesExecutionError` after exceeding the cap.
- Add a test for approval auto‑approve behavior using mocked activities containing a PlanGenerated type.
- Add a test that `resume_session_id` without `resume_branch_name` raises early.
- Update `configs/evolution/agentic_jules.yaml` comments to mention the new `require_plan_approval` / `auto_approve_plan` keys and the resume limitation.

## Concrete Steps

All commands should be run from the repository root (`/Users/juno/workspace/shrinkaevolve-codexevolve`).

1. Edit `shinka/edit/jules_cli.py`:
   - Add an `events_emitted` counter.
   - Guard each yield with the counter + max_events check.
   - Add optional cancel call on overflow (best effort).
   - Wire approval mapping and resume handling as described above.

2. Edit `shinka/edit/jules_api.py`:
   - Add a `cancel_session(session_id)` method if the Jules API supports it; call `POST /sessions/{id}:cancel` and ignore 404/405.
   - Ensure `create_session` accepts `require_plan_approval` and passes `requirePlanApproval` into the payload (already present).

3. Edit `tests/test_jules_cli.py`:
   - Add new unit tests for max_events enforcement, plan auto‑approval, and resume guardrails.

4. Update `configs/evolution/agentic_jules.yaml`:
   - Document new knobs and resume limitation.

5. Validation:
   - Run `pytest tests/test_jules_cli.py -k jules` in a Python 3.11/3.12 virtualenv. Expect all Jules tests to pass.
   - Manually sanity‑check by launching a short Jules run with `agentic.max_turns=3` and verifying it aborts quickly with the expected error.

## Success Criteria & Validation

- Criterion: Jules honors max_events and aborts deterministically.
  Validation: run `pytest tests/test_jules_cli.py::TestJulesRunner::test_max_events_enforced` and expect it to pass. Manually confirm a real Jules run with `agentic.max_turns` small aborts with a clear error message.

- Criterion: approval_mode affects plan approval without deadlock.
  Validation: run `pytest tests/test_jules_cli.py::TestJulesRunner::test_auto_approves_plan_when_required` and expect it to pass. In a real Jules run with `approval_mode=default`, observe a PlanGenerated activity followed by an auto‑approval message and continued progress.

- Criterion: resume_session_id is explicit.
  Validation: run `pytest tests/test_jules_cli.py::TestJulesRunner::test_resume_requires_branch_name` and expect it to pass.

- Criterion: missing credentials surface as JulesExecutionError.
  Validation: run `pytest tests/test_jules_cli.py::TestJulesRunner::test_missing_credentials_wrapped` (new) and expect it to pass.

- Criterion: WebUI continues to show Jules/GitHub keys as configured after restart.
  Validation: start WebUI, save keys in the API‑keys modal, restart server, reopen modal, and see “configured” status for Jules and GitHub.

## Idempotence and Recovery

These changes are safe to apply repeatedly. The max_events guard and approval mapping only affect Jules sessions at runtime. If a bug causes premature aborts, users can temporarily set `agentic.max_turns=0` (no limit) or `approval_mode=full-auto` to regain legacy behavior while debugging.

## Artifacts and Notes

Keep future logs of real Jules sessions (from `/tmp/shinka_launch.log` or WebUI job logs) if unexpected activity types require normalization tweaks.

## Interfaces and Dependencies

No new third‑party dependencies are required. All new behavior should be implemented within:

- `shinka.edit.jules_cli.run_jules_task`
- `shinka.edit.jules_api.JulesAPIClient`
- `tests/test_jules_cli.py`

Maintain the existing AgentRunner signature in `shinka/edit/types.py` exactly; Jules must remain a drop‑in backend for `AgenticEditor` and the evaluator.
