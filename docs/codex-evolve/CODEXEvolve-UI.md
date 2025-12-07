```md
# CodexEvolve UI Telemetry Plan

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

PLANS.md is located at `PLANS.md` in the repository root. Maintain this plan in full compliance with that document.

## Purpose / Big Picture

Legacy Shinka runs stream single-shot LLM edits, and the WebUI already surfaces their prompts, diffs, scores, and evaluator output in near real time. CodexEvolve introduced multi-turn Codex sessions but the UI still only shows the aggregate metadata saved in SQLite; there is no way to inspect Codex tool calls, streamed messages, or the resulting scratch files while the run is in flight. The goal of this work is to make the Codex agent just as observable as the legacy flow: when a user clicks a node, the right-hand panel should reveal the agent’s session log, command history, changed files (text + binary), and evaluator status, updating live as new generations complete. Success is being able to watch a Codex run evolve in the WebUI, inspect each command/tool call, and download the session artifacts without tailing backend logs.

## Progress

- [x] (2025-11-08 19:30Z) Capture requirements for each panel, enumerated the Codex metadata surface area, and documented evaluator status needs for the Evaluation tab.
- [x] (2025-11-08 19:45Z) Backend API enriched `/get_programs` responses with `code_files`, `code_diffs`, Codex session metadata, and added `/get_agent_session_log` for large logs.
- [x] (2025-11-08 21:20Z) Frontend renders Codex-aware Code/Diff/LLM tabs plus evaluator summaries (pass/fail, combined score, error) inside the Evaluation tab with command/binary downloads.
- [x] (2025-11-09 23:15Z) Diff tab now mirrors GitHub’s stacked, multi-file experience with per-file +/- stats, collapse controls, and aggregate summary; non-agent nodes automatically fall back to the legacy single-file renderer.
- [x] (2025-11-09 23:25Z) Evaluation header restyled so combined score and runtime are always visible while status chips become minimal text labels; status is still conveyed but no longer obscures the metrics.
- [x] (2025-11-09 23:40Z) Visualization backend deduplicates `code_diffs` by path (preferring `agent_code_diffs` over legacy `code_diff`) so each file shows exactly once even when metadata sources overlap.
- [x] (2025-11-16 22:55Z) LLM Result tab now renders a reasoning-first, linear timeline with filter chips, Markdown summaries, compact command pills, and no inline JSON (users grab the JSONL only via “Download”); manually validated on node `b423f083-3521-4033-ad03-1a090267190c` by toggling the Commands filter and expanding a reasoning card per Success Criteria #1–#3.
- [ ] Live instrumentation: ensure `/get_programs` reflects new nodes within the current cache window so users can monitor runs without restarting the UI.
- [ ] Context affordances: add inline “show more” controls and/or “view file” hooks so reviewers can expand hunks when Codex diffs elide context.

### Remaining UX polish

1. Ship context-expansion controls in the Diff tab (matching GitHub’s unfold behavior) so users can reveal additional lines without leaving the stacked view.
2. Add lightweight styling to the legacy single-file diff so the per-file header (path + +/-) looks consistent regardless of agent mode.
3. Tighten the LLM Session Timeline so it reads like a single narrative flow (no Prelude/Turn dividers) with reasoning-focused cards, succinct command badges, expanded markdown summaries, and zero raw JSON exposure.
4. Document the new Diff/Evaluation/LLM behaviors with fresh screenshots and ensure contributors know multi-file mode only appears for `patch_type=agentic` nodes.
5. (Optional) Add unit/UI regression tests around `_build_code_diffs_payload`, the `renderDiffViewer` path-selection logic, and the LLM timeline renderer helpers to prevent duplicate regressions.

## LLM Result Tab Redesign

The current Session Timeline simply loops over `agent_session_events` and dumps their JSON payload inside `<pre>` tags. Every event, regardless of type, uses the same typography, so reviewers must parse braces and quotes to see whether the agent was reasoning, running a command, or adding todos. There are no icons, filters, or summaries, and stdout/stderr always render inline, creating extremely tall pages. We need this tab to feel like a proper Codex flight recorder: users should instantly see what the agent attempted, where it failed, and how commands progressed while still having access to the raw transcript when necessary.

### Goals

Explain each Codex session as chronological, human-readable cards. Instead of “Prelude/Turn” buckets, show a single timeline ordered by event index so the flow reads naturally. The primary emphasis is on reasoning: render the summary line (bold `**…**`) as the card title, show the supporting paragraphs beneath, and keep markdown formatting intact. Command executions should collapse long command text behind a short badge (e.g., `bash -lc …` with the cwd/file shown only when expanded) and aggregate stdout/stderr into lightweight previews. Todo items remain checklists. Filters (“All”, “Reasoning”, “Commands”, “Todos”, “System”, “Errors”) still apply, but the raw JSON view is gone in favor of a “Download Session Log” action only; this keeps the consumer UI focused on what the agent is thinking and doing, not the transport format.

### Implementation Outline

1. **Data shaping utilities**: expand `normalizeSessionEvents` in `shinka/webui/viz_tree.html` so each normalized item carries `role`, `event_group_id`, `duration_ms`, `token_count`, `status`, and `raw_payload`. Build helpers such as `groupEventsByTurn(events)` and `summarizeCommand(event)` to keep rendering logic declarative. Where timestamps are missing, approximate durations using deltas between adjacent events.
2. **Timeline renderer**: swap the accordion approach for a linear timeline where each event card shows (a) icon, (b) status chip, (c) timestamp, and (d) key payload. Reasoning cards parse the summary from the first Markdown heading/`**bold**` segment and tuck the detailed body behind a “Show details” toggle. Command cards display a concise badge (e.g., `bash -lc ls`) plus inline status/duration; expanding reveals stdout/stderr blocks capped at N characters with a “View full output” link that opens a modal or detail `<details>` element. Todo and system cards keep simple list/text layouts. Collapsible headers per turn are removed, and the legacy Command Executions / Agent Messages tables disappear entirely so the timeline becomes the single source of truth.
3. **Controls and affordances**: drop the filter toolbar entirely—the timeline should always show the full sequence so reviewers understand every step without toggling. The only controls that remain are “Download JSONL” (for raw data) and the collapsible reasoning/command detail sections. Consider a subtle density toggle later, but keep the base layout streamlined.
4. **Styling**: update `shinka/webui/static/style.css` (or the inline `<style>` bundle) with `.codex-turn`, `.codex-timeline-card`, `.status-chip--success|pending|error`, and responsive layouts so the cards read well even alongside the Diff tab. Reuse existing CSS variables for colors to keep the overall visual language consistent.
5. **Performance considerations**: retain the 200-event cap but lazy-render turns beyond the first five with a “Load older turns” button to prevent DOM thrash on long sessions. Batching ensures the auto-refresh loop can append new turns without re-rendering the entire timeline.
6. **Session log integration**: when `fetchAndRenderSessionLog` pulls the full `session_log.jsonl`, merge the fetched events into the linear array, refresh the filters, and rerender the cards in place. Because raw JSON is hidden, expose a single “Download timeline JSONL” button next to the log controls for anyone needing the full artifact.

### Validation

Launch `uv run shinka_visualize results --port 8891 --open`, select an agentic node, and verify:

1. The timeline shows every event in order with no filters/tabs; reasoning cards render Markdown summaries, command cards use compact pills, and status chips appear only when an error occurs.
2. Collapsible sections never expose raw JSON (aggregated command payloads that look like JSON are replaced with a “see session log” hint).
3. Loading the full session log appends additional events in place, keeps the linear order intact, and remains responsive for ~200 events; the only path to raw data is the “Download JSONL” control.

## Surprises & Discoveries

- Observation: The backend already persists Codex session logs (`session_log.jsonl`), command transcripts, and binary files per generation under `agent_sessions/<uuid>/`, but `/get_programs` only emits small metadata arrays; the full log is inaccessible to the UI today.
  Evidence: `_run_agentic_patch` stores `agent_session_log_path`, `agent_session_events`, and `agent_binary_files` in `meta_edit_data`, yet the WebUI’s `Node` panel ignores those fields (verified via console logs on 2025-11-08).

## Decision Log

- (2025-11-08) The Evaluation tab now mirrors Codex telemetry requirements by presenting evaluator status (pass/pending/fail chips), combined score, runtime, feedback, metrics, and stdout/stderr logs, so contributors no longer need to inspect raw logs for pass/fail signals.

## Outcomes & Retrospective

Pending. Summarize UI behavior, telemetry completeness, and any performance trade-offs once implementation is complete.

## Context and Orientation

The existing WebUI loads programs via `/get_programs`, caches them for five seconds, and renders a left-side tree plus right-side tabs (`Meta`, `Pareto Front`, `Scratchpad`, `Node`, `Code`, `Diff`, `Evaluation`, `LLM Result`). These tabs currently expect legacy metadata: prompt text, diff summaries, evaluator scores, etc. CodexEvolve augments `meta_edit_data` with:

- `agent_session_log_path`: filesystem path to `session_log.jsonl`.
- `agent_session_events`: in-memory copy of recent JSON events.
- `agent_commands`: structured command execution results.
- `agent_session_log`: short message history.
- `agent_metrics`: elapsed seconds, number of messages/commands.
- `agent_binary_files`: base64-encoded payloads for non-text artifacts.

To give parity with the legacy experience, the UI must hydrate these fields when a user selects a node and present them in a human-friendly way (e.g., collapsible event timeline, command output console, download links for binary files).

## Tab-Specific Updates for Codex Runs

- **Code tab parity.** Replace the single `data.code` blob with a structured `code_files` payload (`[{path, language, content}]`) so users can switch between every file touched in a generation. Surface filename chips or a dropdown, preserve syntax highlighting per file, and keep the download button scoped to the active file. Fall back to the legacy single-file view when only one entry exists.
- **Diff tab parity.** Extend the diff renderer to iterate per file (`code_diffs: [{path, diff}]`) instead of dumping one long patch. Provide quick navigation between files and reuse existing diff formatting so the experience mirrors legacy runs but can display Codex multi-file edits.
- **LLM Result → Codex Timeline.** When `meta_edit_data.patch_type == "agentic"` (or similar node-level flag), replace the static `llm_result` table with a chronological stream built from `agent_session_events`, `agent_commands`, and `agent_session_log`. Each entry should show timestamp, tool call name, brief input/output snippet, and status (running/done/error). Outside of agentic nodes, keep the current legacy table rendering so single-shot edits behave exactly as before.
- **Visibility gating.** Drive the Codex-specific UI off node metadata (e.g., `patch_type` or presence of `agent_session_events`) instead of a global Hydra flag. This lets mixed runs (agentic + legacy) coexist and keeps the UI resilient if Hydra config changes mid-run or we replay historical data.
- **Tree resiliency.** Keep the program tree visible even when no node is marked `correct` yet; highlight once evaluation confirms a best node but never block rendering or navigation while scores are pending.

## Plan of Work

1. **Backend enrichment.** Extend `/get_programs` (and any cache) so each `Program` entry includes the Codex metadata listed above. For large assets (session logs, binary files), decide between inlining small samples versus returning URLs for lazy loading. Ensure long-running runs continue to stream updates every five seconds without blocking.

2. **UI integration.** Update `viz_tree.html` to recognize the new metadata. When a node is selected:
   - Populate a “Codex Session” tab (or reuse `Node`) with the event timeline, command executions, and metrics.
   - Provide download links or embedded viewers for `session_log.jsonl` and binary files.
   - Show evaluator status (pass/fail, combined score, error) alongside the existing `Evaluation` tab.
   - Swap the Code/Diff/LLM Result tabs into their Codex-aware variants whenever the selected node reports `patch_type: agentic`; otherwise fall back to the legacy rendering.

3. **Live refresh.** Confirm the auto-refresh loop (`checkForNewData`) pulls the new metadata and handles partially written files gracefully, so the UI reflects the agent’s progress in near real time.

4. **Parity checks.** Compare the new Codex tab with the legacy `LLM Result` panel to ensure both surfaces convey similar information (prompts, outputs, costs), minimizing cognitive load when switching between edit strategies.

## Concrete Steps

1. Capture the exact JSON shape emitted by `/get_programs` today and document which new fields each node must include for Codex telemetry.
2. Implement backend helpers that expose `session_log.jsonl` (perhaps truncated for UI preview) and binary attachments via HTTP endpoints.
3. Enhance the WebUI’s node-view logic to render Codex events, commands, metrics, and evaluator outcomes, updating on each auto-refresh tick.
4. Verify with a running Codex experiment that new generations appear live, the tabs display the session data, and users can download full logs.

## Success Criteria & Validation

- Spin up a Codex agentic run (e.g., `variant=circle_packing_example evolution=agentic`) and open the WebUI. For each new node:
  - The tree updates within a few seconds of generation completion.
  - The right-hand panel shows the Codex session timeline, command outputs, binary artifacts, and evaluator result without tailing backend logs.
  - Download links for `session_log.jsonl` and binary files work.
- Switch to the refreshed LLM Result tab and confirm the card-based timeline, filters, stdout/stderr toggles, copy-json affordances, raw-view toggle, and session-log streaming behavior described earlier all function as expected.
- Exercise failure scenarios (Codex timeout, evaluator error) and confirm the UI still showcases the session data with clear error messaging.
- Capture before/after screenshots of the Codex tabs via the DevTools tooling to verify the UI changes are visible and complete.

## Idempotence and Recovery

- UI changes must be hot-reload friendly; restarting `shinka_visualize` should not be required after each run.
- If a session log is large, the backend should stream or paginate it to avoid hanging the UI; retry logic should handle missing files until the agent finishes writing them.
- Clearing caches or refreshing the page should restore the same data without manual intervention.

## Artifacts and Notes

Include representative screenshots of the new Codex tabs, sample `session_log.jsonl` excerpts, and any API schema updates once implementation lands.
```
