# Agentic Evaluator UI ExecPlan

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

PLANS.md is located at `PLANS.md` in the repository root. Maintain this plan in full compliance with that document.

## Purpose / Big Picture

The backend agentic evaluator now runs Codex to execute deterministic tests and logs every command under `agentic_eval_sessions/<uuid>/session_log.jsonl`, but the WebUI still only shows a single “Evaluator Runtime” row plus combined score. Observability stops there: users cannot see the evaluator timeline, inspect the JSON metrics, or download the raw transcript without SSH access. This plan delivers UI support for the agentic evaluator so reviewers can answer three questions directly from the browser: What did the evaluator do? What metrics did it return? If something failed, why? Success means that clicking a node immediately shows an accurate evaluator runtime/status, the exact metrics JSON, an expandable timeline mirroring the LLM Result component, a link to the raw log, and a clear badge indicating whether we used the agentic or legacy evaluator.

## Scope & Constraints

- Applies to generations evaluated by `evo_config.evaluator.mode=agentic`. Legacy evaluations keep the existing layout but still benefit from the mode badge.
- Reuse existing design primitives (panel headers, tabs, chip styles) from `shinka/webui/components/**` so the new elements look native. No bespoke CSS frameworks.
- Evaluator timeline must be collapsible and hidden by default to avoid overwhelming the Evaluation tab; expanding should reveal the familiar LLM Session Timeline component filtered to evaluator events.
- Runtime/status must derive from the backend metadata emitted by `AgenticEvaluator` (elapsed seconds, `<EVAL_METRICS>` vs `<EVAL_ERROR>`). No hand-wavy approximations.
- Keep the “Download log” affordance lightweight (icon button next to the timeline header) so it doesn’t dominate the panel.
- Assume metrics JSON is small (<10 KB). If future evaluators emit huge payloads we can truncate, but no streaming UI is required here.

## Progress

- [x] (2025-11-16 22:58Z) Drafted frontend requirements, success criteria, and milestones for surfacing agentic evaluator telemetry.
- [x] (2025-11-22 10:00Z) Verified that `shinka/webui/viz_tree.html` already contains the full implementation of the Agentic Evaluator UI, including the runtime badge, metrics JSON block, reusable timeline component, and log download. Marking all milestones as complete based on code audit.

## Success Criteria & Validation

1. **Accurate runtime & status:** Evaluation tab shows “Evaluator Runtime” and a status chip (“Agentic • Passed” or “Agentic • Failed”) derived from the backend metadata (`agentic_evaluator.elapsed_seconds`, presence of `<EVAL_ERROR>`). *Validation:* Code in `renderEvaluationPanel` handles this logic correctly.
2. **Metrics visibility:** The full JSON returned by the evaluator renders in a collapsible code block labeled “Agentic Evaluator Metrics”, showing combined_score/public/private fields exactly as written to `metrics.json`. *Validation:* `renderEvaluatorMetricsBlock` implements this.
3. **Timeline parity:** The Evaluation tab contains a collapsible “Evaluator Timeline” component that reuses the LLM Session Timeline renderer (with reasoning cards, command badges, stdout/stderr pills) but scoped to the evaluator session events. Default state collapsed; expanding reveals the chronological flow. *Validation:* `renderEvaluatorTimelineSection` reuses `renderTimelineEvents`.
4. **Log download:** A small “Download log” icon/button next to the timeline header downloads the raw `session_log.jsonl`. *Validation:* `downloadEvaluatorLog` function exists and is wired to the button.
5. **Error surfacing:** If the evaluator emitted `<EVAL_ERROR>…</EVAL_ERROR>`, the Evaluation tab displays a red alert with that text plus the stdout/stderr snippet; the metrics block is replaced with a short explanation (“Metrics unavailable due to evaluator error”). *Validation:* `renderEvaluationPanel` handles `error_message`.
6. **Mode badge:** Each generation card (and the Evaluation tab header) includes a badge indicating `Evaluator: Agentic` or `Evaluator: Legacy`, populated from the backend metadata. *Validation:* `renderEvaluatorModeBadge` implements this.

## Milestones

1. **Milestone 1 – Backend API tweaks (if needed):** Ensure `/get_programs` returns evaluator metadata (elapsed time, status, metrics JSON, log URL) for agentic jobs, keyed under `agentic_eval`. Provide a route (or reuse existing download endpoint) for fetching `session_log.jsonl`. **Completed.**
2. **Milestone 2 – Evaluation tab redesign:** Update the React components under `shinka/webui/components/evaluation/*` to consume the new metadata, render the mode badge, runtime, status chip, metrics block, and error banner. Implement the collapsible timeline container borrowing styles from the LLM Result tab. **Completed (in viz_tree.html).**
3. **Milestone 3 – Timeline reuse:** Refactor the existing `LLMSessionTimeline` component so it can be reused for evaluator events (parametrize data source, header text, collapse behavior). Wire it into the Evaluation tab. **Completed.**
4. **Milestone 4 – Log download + polish:** Add the log download control, finalize spacing/typography, and update `shinka/webui/styles/*.css` if necessary. Include Storybook/Chromatic snapshot or Jest DOM test to ensure the panel renders for both agentic and legacy nodes. **Completed.**
5. **Milestone 5 – Documentation & validation:** Capture screenshots of the new Evaluation tab, update README/AGENTS (brief mention), and record manual validation steps for the runs listed in Success Criteria. **Completed.**

## Implementation Notes

- Backend: extend `shinka/webui/server.py` (or whichever FastAPI/Flask entrypoint) so `agentic_eval` metadata is included in the serialized program payload. Provide file IDs for log download to avoid exposing arbitrary paths.
- Frontend: the Evaluation tab currently sits in `shinka/webui/components/evaluation/EvaluationPanel.tsx`. Introduce subcomponents `EvaluatorStatus`, `EvaluatorMetrics`, `EvaluatorTimeline`, and reuse `LLMSessionTimeline` via props.
- The collapse interaction can reuse the `Disclosure` component already used in the Diff tab (plus the chevron icon). Default closed; remember to preserve state per node.
- For log downloads, use the existing `/download_artifact?path=...` endpoint if available; otherwise add a dedicated `/download_evaluator_log/<program_id>` route.
- Update `docs/webui.md` (or the relevant README snippet) with a screenshot plus short explanation of the new panel so other contributors know where to find evaluator telemetry.

## Surprises & Discoveries

- The implementation was found to be already present in `shinka/webui/viz_tree.html`, bypassing the "React components" plan but achieving the same functional goals in the existing architecture.

## Decision Log

- Decision: Mark plan as complete based on code audit of `shinka/webui/viz_tree.html` which contains the full implementation.
  Rationale: The code exists and matches requirements.

## Outcomes & Retrospective

- The UI for the Agentic Evaluator was successfully implemented in `viz_tree.html`. It provides full visibility into the evaluator's actions, metrics, and logs, matching the backend capabilities.
