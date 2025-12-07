# UI Polish ExecPlan (TODO-202)

This ExecPlan is a living document. It addresses **TODO-202** from `TODO_EXECPLAN.md`: "Evaluation and LLM Result tabs UI needs polish".

## Purpose / Big Picture

The Evaluation (`#log-output`) and LLM Result (`#llm-result`) tabs in the WebUI have inconsistent styling, and the new agentic timeline UI looks rough. This plan aims to unify the visual design of these two tabs, improve the readability of the timeline, command outputs, and session logs, and fix specific visual bugs like spacing, alignment, and contrast. The goal is a polished, professional look that matches the rest of the Shinka WebUI.

## Scope & Constraints

- **Target File**: `shinka/webui/viz_tree.html` (HTML/CSS/JS).
- **Tabs**: "Evaluation" (right panel) and "LLM Result" (right panel).
- **Components**:
    - Agentic Timeline (cards, events, command pills).
    - Evaluation Metrics (JSON block).
    - Session Log Preview.
    - Legacy LLM Result Table.
- **Style**: Consistent with existing "dark mode" aesthetic (cards, rounded corners, subtle borders).
- **No Frameworks**: Pure CSS/JS only, reusing existing styles where possible.

## Progress

- [x] (2025-11-27) Plan created.
- [x] Analyze current CSS/JS structure in `viz_tree.html`.
- [x] Unify timeline styling (CSS).
- [x] Polish Evaluation tab (Metrics, Log Preview).
- [x] Polish LLM Result tab (Timeline, Legacy Table).
- [x] Fix specific visual bugs (bullet positioning, header contrast, overflow).
- [x] Verify consistency between tabs.

## Specific Issues & Fixes (from TODO-202)

1.  **Timeline Cards**: Fix confusing spacing/indentation.
2.  **Command Pills**: Make them larger/more readable.
3.  **Badges**: consistent "Agentic" badges.
4.  **Legacy Table**: Modernize the look of the legacy LLM result table.
5.  **Log Download**: Consistent placement of download buttons.
6.  **Bullets**: Fix `.codex-timeline-card::before` positioning.
7.  **Headers**: Fix `.evaluation-section h5` background blending.
8.  **Hover**: Add hover states to `.collapsible-header`.
9.  **Overflow**: Handle `.codex-card-body` content overflow.
10. **Output**: Better visual separation for stdout/stderr.
11. **Log Preview**: Improve styling of `#session-log-preview`.

## Implementation Plan

1.  **CSS Refactoring**:
    - Locate `.codex-timeline*`, `.evaluation-section*`, `.llm-result-*` styles.
    - Consolidate shared timeline styles.
    - update CSS to use flexbox/grid for better alignment.
    - Increase padding, use lighter borders/backgrounds for contrast.

2.  **JS Update**:
    - Update `renderAgenticTimeline` and `renderTimelineEvents` to generate cleaner HTML structure if needed.
    - Ensure `renderEvaluationPanel` uses the improved classes.
    - Update `renderLlmResultTable` to use a card-based layout or better table styling.

## Success Criteria & Validation

1.  **Visual Consistency**: Both tabs use the same card/timeline visual language.
2.  **Readability**: Command outputs and logs are easy to read (monospace, proper contrast).
3.  **Polish**: No misalignment, overlapping elements, or broken layouts.
4.  **Responsiveness**: Layout handles resizing gracefully.
5.  **Legacy Support**: Legacy nodes still display correctly with improved styling.

## Notes

- `shinka/webui/viz_tree.html` is large. Modifying it requires careful search and replace.
- Use `TODO_EXECPLAN.md` context for specific line numbers (approximate).
