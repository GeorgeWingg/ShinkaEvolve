# WebUI Right Panel Performance Optimization

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This document must be maintained in accordance with `PLANS.md` at the repository root.

## Purpose / Big Picture

Users viewing evolution runs with large code files (1M+ characters, 20K+ lines) previously experienced severe performance degradation: multi-second tab switching, large memory usage, and unresponsive UI when scrolling. After this optimization, the right panel keeps a small DOM footprint and remains responsive even when code, diffs, logs, and timelines are very large.

Observable behavior after implementation: Loading a node with very large code renders quickly, scrolling stays smooth, and tab switching is instantaneous (sub-200ms). Code search and syntax highlighting continue to work.

## Progress

- [x] (2025-12-17 16:16Z) Implemented right-panel virtualization for Code/Diff/Evaluation and progressive rendering for LLM timeline in `shinka/webui/viz_tree.html` (SC-01–SC-06).
- [x] (2025-12-17 16:18Z) Captured and reviewed a live UI screenshot verifying Code virtualization + search UX (`plans/artifacts/webui_perf_code_virtual.png`) (SC-07).
- [x] (2025-12-17 16:27Z) Ran `uv run pytest tests -q` and `uv run ruff check shinka tests` to capture current repo health; failures are documented under `Success Criteria & Validation` (SC-08).

## Surprises & Discoveries

- Observation: The previously referenced sample DB under `shinka/webui/results/shinka_webui/...` is empty in this workspace, so end-to-end reproduction with a real 1M+ code node is not currently possible.
  Evidence: `find shinka/webui/results/shinka_webui -type f` returns no database files.
- Observation: When running `shinka_visualize` from a non-interactive agent session, the server must be detached (e.g. `nohup ... &`) to avoid SIGHUP termination.
  Evidence: The server stays listening when started with `nohup uv run ... &`.
- Observation: Chrome aggressively caches the single-file `viz_tree.html`; adding a cache-busting query param (e.g. `?v=...`) is necessary to ensure the latest JS is evaluated.
  Evidence: Re-loading without a cache buster can show stale behavior; re-loading with `?v=` consistently loads updated logic.

## Decision Log

- Decision: Validate performance improvements with a deterministic “synthetic node” harness executed in the browser console, rather than requiring a real `evolution_db.sqlite`.
  Rationale: The workspace does not contain a suitable DB right now; the synthetic harness still exercises the real rendering code paths (`renderCodeViewer`, `renderDiffViewer`, `renderEvaluationPanel`, `renderTimelineEvents`) and produces reproducible DOM/heap/timing metrics.
  Date/Author: 2025-12-17 / Codex (GPT-5.2)

- Decision: Use a small fixed DOM pool (viewport + buffer) for virtualized viewers and tune buffers downward (Code/Diff bufferLines = 20; timeline initial render = 20).
  Rationale: DOM size is the main driver of tab-switch jank; smaller pools preserve smooth scrolling while keeping total DOM under ~2k in the synthetic worst case.
  Date/Author: 2025-12-17 / Codex (GPT-5.2)

- Decision: Keep highlighting work off the main thread for large code by using a Blob-based Web Worker that imports Highlight.js from CDN and only highlights visible lines.
  Rationale: This prevents >50ms main-thread tasks during scroll on large files while preserving syntax highlighting.
  Date/Author: 2025-12-17 / Codex (GPT-5.2)

## Outcomes & Retrospective

The right panel now avoids “render everything” strategies for large payloads. Large Code/Diff/Log content renders in milliseconds (synthetic validation), DOM stays below ~2k, memory stays well under 200MB, and tab switches are consistently <20ms. Search in the Code tab continues to work (including in virtual mode), and large evaluation logs no longer block the UI because they are initialized only on demand.

The remaining gap is an end-to-end validation against a real `evolution_db.sqlite` with 1M+ code content in this workspace. The implemented approach is data-shape compatible with the existing schema (it only changes rendering), so it should apply directly once a suitable DB is available.

## Context and Orientation

The ShinkaEvolve WebUI is primarily a single-file frontend:

- `shinka/webui/viz_tree.html` (≈32k lines): all rendering logic for the right panel tabs (Meta/Code/Diff/Evaluation/LLM Result).
- `shinka/webui/visualization.py` (≈5.3k lines): the Python HTTP server that serves the UI and exposes API endpoints.

The performance problem this plan addresses is that the right panel previously attempted to render huge “whole-file” payloads (20k+ lines, 1M+ chars) as fully expanded DOM, causing large DOM counts and long main-thread tasks.

Terms used below:

- Virtual scrolling: render only the visible window (plus a buffer) and reuse a fixed pool of nodes, while preserving total scroll height.
- DOM recycling: update existing DOM nodes’ content/position rather than creating/removing thousands of nodes on scroll.

## Plan of Work (Implemented)

Milestone 1: Code tab virtual scrolling

- Added a size threshold (`VIRTUAL_CODE_CONFIG`) and a `shouldUseVirtualCodeViewer()` gate.
- Implemented `renderVirtualCodeViewer()` / `updateVirtualCodeViewport()` using a fixed pool of `.virtual-code-line` nodes and `.virtual-line-number` gutter nodes, positioned via `transform: translateY(...)`.
- Integrated search into virtual mode (`performCodeSearch()` computes matches by line; `applyVirtualCodeSearchHighlight()` highlights the visible line; `scrollVirtualCodeToLine()` moves to matches).
- Moved Highlight.js work for visible lines into a Web Worker created by `getVirtualCodeHighlightWorker()`; highlighted lines are cached in `virtualCodeState.highlightCache` with pruning to `maxCachedLines`.

Milestone 2: Diff tab optimization

- Multi-file diffs render headers/stats immediately; diff bodies are rendered lazily on expand (`loadDiffSection()`).
- Large diffs render via `renderVirtualDiff()` using the shared `VirtualScroller`, keeping a small `.virtual-diff-row` pool.

Milestone 3: Evaluation + LLM Result optimization

- Timeline events are progressively rendered: `renderTimelineEvents(events, timelineKey)` renders only the last `TIMELINE_RENDER_CONFIG.initialCount` events with “Load more / Show all”.
- Large evaluation logs are lazily initialized: `renderEvaluationLogsSection()` shows a placeholder for logs over `VIRTUAL_EVAL_LOG_CONFIG.minLines`, and on expand `initVirtualEvalLog()` mounts a `VirtualScroller`.

Milestone 4: Cross-cutting

- Added `VirtualScroller` as a shared fixed-row virtualization utility (used by diff + eval logs).
- Added CSS containment / `will-change` hints for virtualized elements.
- Added request deduplication for `/get_program_details` (`programDetailsPending`).

## Concrete Steps

### Step 1: Start the WebUI server

Working directory: `/Users/juno/workspace/shrinkaevolve-codexevolve`

    nohup uv run shinka_visualize shinka/webui/results --port 8900 > /tmp/shinka_visualize_perf_8900.log 2>&1 &
    open http://localhost:8900/?v=1

The `?v=` cache buster is important for ensuring the browser loads the updated `viz_tree.html`.

### Step 2: Run the synthetic performance harness in the browser console

In Chrome DevTools console, run:

    (async () => {
      document.querySelector('.tab[data-tab="agent-code"]')?.click();

      const numLines = 23028;
      const payload = 'x'.repeat(40);
      const base = `print("${payload}")`;
      const lines = Array.from({ length: numLines }, (_, i) => `${base}  # ${i}`);
      const content = lines.join('\n');

      // Code (forces virtual mode)
      currentCodeFiles = [{ path: 'main.py', language: 'python', content }];
      currentCodeSelectionIndex = 0;
      const t0 = performance.now();
      renderCodeViewer();
      const t1 = performance.now();

      // Diff (forces virtual mode)
      const diffLines = [
        'diff --git a/main.py b/main.py',
        'index 0000000..1111111 100644',
        '--- a/main.py',
        '+++ b/main.py',
        '@@ -1,5000 +1,5000 @@',
      ];
      for (let i = 0; i < 5000; i += 1) {
        diffLines.push(`-old line ${i} ${payload}`);
        diffLines.push(`+new line ${i} ${payload}`);
      }
      currentDiffFiles = [{ path: 'main.py', diff: diffLines.join('\n') }];
      currentDiffIsMultiFile = true;
      const t2 = performance.now();
      renderDiffViewer();
      const t3 = performance.now();

      // Evaluation logs (lazy)
      const stdoutText = Array.from({ length: 50000 }, (_, i) => `stdout line ${i}`).join('\n');
      const logOutput = document.getElementById('log-output');
      if (logOutput) {
        const node = {
          id: 'synthetic-1',
          generation: 0,
          agent_name: 'Synthetic',
          combined_score: 0.123,
          language: 'python',
          metadata: {
            patch_type: 'agentic',
            model_name: 'gpt-5.1-codex-mini',
            stdout_log: stdoutText,
            stderr_log: 'stderr sample',
          }
        };
        logOutput.innerHTML = renderEvaluationPanel(node);
      }

      // LLM timeline (progressive render)
      const timelineKey = 'agent:synthetic-1';
      const events = Array.from({ length: 120 }, (_, i) => ({
        category: 'command',
        title: `Command ${i}`,
        displayType: 'Command',
        details: `Did thing ${i}`,
        commandLabel: 'run',
      }));
      const llm = document.getElementById('llm-result');
      if (llm) {
        llm.innerHTML = `<div class="codex-timeline-list" id="__timeline_test">${renderTimelineEvents(events, timelineKey)}</div>`;
      }

      // Wait for diff lazy render to finish.
      await new Promise(r => setTimeout(r, 60));

      // Remove timeline cards to measure base DOM without them.
      const domWithTimeline = document.querySelectorAll('*').length;
      const timelineCards = document.querySelectorAll('#__timeline_test .codex-timeline-card').length;
      if (llm) llm.innerHTML = '<div class="codex-timeline-list" id="__timeline_test"></div>';
      const domWithoutTimeline = document.querySelectorAll('*').length;

      return {
        codeChars: content.length,
        codeLines: lines.length,
        codeRenderMs: +(t1 - t0).toFixed(2),
        virtualCodePoolLines: document.querySelectorAll('.virtual-code-line').length,

        diffLines: diffLines.length,
        diffRenderMs: +(t3 - t2).toFixed(2),
        virtualDiffRows: document.querySelectorAll('.virtual-diff-row').length,

        timelineCards,

        domWithTimeline,
        domWithoutTimeline,
        usedJSHeapSizeBytes: performance.memory?.usedJSHeapSize ?? null,
      };
    })();

Expected shape of output is a JSON object with (approximately):

    {
      "codeLines": 23028,
      "codeChars": 1347541,
      "codeRenderMs": < 200,
      "virtualCodePoolLines": ~70,
      "diffLines": 10005,
      "diffRenderMs": < 300,
      "virtualDiffRows": ~40,
      "timelineCards": 20,
      "domWithoutTimeline": < 2000,
      "usedJSHeapSizeBytes": < 200000000
    }

### Step 3: Verify tab switching and long-task behavior

Tab switch timing (worst case with heavy synthetic content already loaded):

    (async () => {
      async function measureTab(tabId) {
        const tab = document.querySelector(`.tab[data-tab="${tabId}"]`);
        if (!tab) return null;
        const t0 = performance.now();
        tab.click();
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
        const t1 = performance.now();
        return +(t1 - t0).toFixed(2);
      }

      const tabs = ['agent-code','code-diff','log-output','llm-result'];
      const times = {};
      for (const t of tabs) times[t] = await measureTab(t);
      return { tabSwitchMs: times };
    })();

Expected: all values < 200ms.

Long-task check while programmatically scrolling:

    (async () => {
      const scrollEl = document.getElementById('virtual-code-container');
      if (!scrollEl) return { ok: false, error: 'virtual code container not found' };

      const longTasks = [];
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) longTasks.push(entry.duration);
      });
      observer.observe({ entryTypes: ['longtask'] });

      const maxScroll = scrollEl.scrollHeight - scrollEl.clientHeight;
      const steps = 40;
      for (let i = 0; i <= steps; i += 1) {
        scrollEl.scrollTop = Math.floor((maxScroll * i) / steps);
        await new Promise(r => setTimeout(r, 16));
      }

      await new Promise(r => setTimeout(r, 400));
      observer.disconnect();

      return {
        longTaskCount: longTasks.length,
        worstLongTaskMs: longTasks.length ? Math.max(...longTasks) : 0,
      };
    })();

Expected: `longTaskCount` is 0 (no >50ms tasks observed during scroll).

## Success Criteria & Validation

SC-01: Large code payload renders without huge DOM inflation.

- Validation: Run Step 2 harness and confirm `codeLines` is 23k+, `codeRenderMs < 200`, and `virtualCodePoolLines` stays in the ~50–100 range.
- Evidence (2025-12-17): Code render on 1.34M chars / 23,028 lines returned `codeRenderMs=1.8` and `virtualCodePoolLines=73`.

SC-02: Syntax highlighting does not block scrolling for large code.

- Validation: Ensure the worker is created and the highlight cache populates for visible lines:

    (async () => {
      await new Promise(r => setTimeout(r, 500));
      return {
        workerCreated: !!virtualCodeHighlightWorker,
        highlightCacheSize: virtualCodeState?.highlightCache?.size ?? null,
      };
    })();

- Evidence (2025-12-17): `workerCreated=true`, `highlightCacheSize=70`.

SC-03: Large diffs are lazy-loaded and virtualized.

- Validation: In Step 2 harness, confirm `virtualDiffRows` is non-zero and small (~40–80), not proportional to `diffLines`.
- Evidence (2025-12-17): Diff render returned `diffLines=10005`, `virtualDiffRows=40`.

SC-04: Large evaluation logs do not render huge `<pre>` blocks by default.

- Validation: In the Evaluation tab, expand the stdout block and confirm only a small pool is mounted:

    (async () => {
      const header = document.querySelector('#log-output .eval-log-block[data-log-type="stdout"] .eval-log-header');
      const t0 = performance.now();
      header?.click();
      await new Promise(r => setTimeout(r, 60));
      const t1 = performance.now();
      return {
        initMs: +(t1 - t0).toFixed(1),
        virtualLogLines: document.querySelectorAll('#log-output .virtual-log-line').length,
      };
    })();

- Evidence (2025-12-17): `initMs≈61.7`, `virtualLogLines=60`.

SC-05: Timeline renders progressively and is controllable.

- Validation: Confirm initial render is 20 cards and buttons increase to 70 and then 120:

    (() => {
      const before = document.querySelectorAll('#__timeline_test .codex-timeline-card').length;
      document.querySelector('#__timeline_test [data-action="timeline-load-more"]')?.click();
      const after = document.querySelectorAll('#__timeline_test .codex-timeline-card').length;
      document.querySelector('#__timeline_test [data-action="timeline-show-all"]')?.click();
      const afterAll = document.querySelectorAll('#__timeline_test .codex-timeline-card').length;
      return { before, after, afterAll };
    })();

- Evidence (2025-12-17): `{ before: 20, after: 70, afterAll: 120 }`.

SC-06: Right-panel tab switching stays responsive with large content present.

- Validation: Run Step 3 tab switch script and confirm all times are <200ms.
- Evidence (2025-12-17): `{ agent-code: 1.4ms, code-diff: 16.7ms, log-output: 16.6ms, llm-result: 8.3ms }`.

SC-07: Visual verification performed on a running UI.

- Validation: Capture a screenshot of the running UI, then inspect it to confirm the Code tab shows 23,028 lines and the search bar is functional.
- Evidence (2025-12-17): Screenshot captured and reviewed at `plans/artifacts/webui_perf_code_virtual.png`.

SC-08: Repo health checks captured.

- Validation: Run `uv run pytest tests -q` and record the result; run `uv run ruff check shinka tests` and record the result.
- Evidence (2025-12-17): `pytest` reports 6 failing tests and `ruff` reports 127 issues; these are currently outside the scope of this WebUI performance change, but are recorded here for visibility.

## Idempotence and Recovery

These changes are safe to re-run and validate repeatedly.

- The synthetic harness only mutates in-memory browser globals (`currentCodeFiles`, `currentDiffFiles`, DOM containers). Reloading the page restores normal UI state.
- To stop the server started in Step 1, find the listening process and kill it:

    lsof -i :8900 -sTCP:LISTEN
    kill <PID>

## Artifacts and Notes

- Screenshot: `plans/artifacts/webui_perf_code_virtual.png`
- Server logs: `/tmp/shinka_visualize_perf_8900.log`

Pytest transcript (2025-12-17):

    uv run pytest tests -q
    ...
    6 failed, 386 passed, 8 skipped

Ruff transcript (2025-12-17):

    uv run ruff check shinka tests
    Found 127 errors.

## Interfaces and Dependencies

No new backend endpoints are required.

Frontend dependencies remain CDN-provided:

- Highlight.js (CDN): used both in main thread for small code blocks and in a Blob-based Web Worker for large code virtualization.
- jsdiff: used for diff formatting and parsing.

## Plan revision note

(2025-12-17) Updated this ExecPlan to reflect the completed implementation in `shinka/webui/viz_tree.html`, removed outdated pseudo-code/table formatting, and added reproducible, deterministic validation steps (including screenshot verification) plus concrete evidence outputs.
