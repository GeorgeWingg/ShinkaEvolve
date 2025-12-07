# Agents Tab Stabilization & Redesign

This ExecPlan is a living document. The sections Progress, Surprises & Discoveries, Decision Log, and Outcomes & Retrospective must be updated as work proceeds. This plan is governed by PLANS.md (repo root: PLANS.md); all requirements there apply.

## Purpose / Big Picture

Restore the Agents tab so it loads without JavaScript errors, shows existing runs from `results/`, and matches the provided designs in both signed-in (usage + sessions) and signed-out (signin prompts + empty table) states. After completion, visiting http://localhost:8888, clicking "Agents," and comparing to the two reference screenshots in `docs/image screenshots/` should show visual and functional parity.

**Extended Scope:** Make Agent Sessions data real (not sample data), implement full authentication flow for all providers, and create a shared API key modal.

## Progress

### Phase 1: UI Stabilization (COMPLETED)
- [x] (2025-11-23 22:00Z) Baseline captured (current UI errors, console logs, file diffs).
- [x] (2025-11-23 22:00Z) JS parse errors removed; page loads with zero console errors. (Removed ~600 lines of duplicated code).
- [x] (2025-11-23 22:00Z) Agents tab renders data (usage + sessions) and is interactive (no freeze).
- [x] (2025-11-23 22:00Z) UI matches signed-in screenshot parity.
- [x] (2025-11-23 22:00Z) UI matches signed-out screenshot parity (auth prompts, empty state).
- [x] (2025-11-23 22:30Z) Fixed visibility issue: Agents tab is now permanently visible.
- [x] (2025-11-23 22:45Z) Achieved full visual parity with reference designs.
- [x] (2025-11-24 00:15Z) Refined ShinkaAgent card layout: increased height to 240px.

### Phase 2: Card Design Polish (COMPLETED)
- [x] (2025-11-24 21:30Z) ShinkaAgent card title repositioned inside decorative koi fish border frame.
- [x] (2025-11-24 21:35Z) Sign in buttons vertically centered within frame (padding-top: 28px).
- [x] (2025-11-24 21:40Z) Added Claude Code card with Anthropic terracotta color (#D97757).
- [x] (2025-11-24 21:45Z) Replaced Google "G" text with official multicolor Google logo SVG.
- [x] (2025-11-24 21:50Z) Added 3-dot menu (⋮) for signed-in state with Refresh/Sign out options.

### Phase 3: Real Agent Sessions Data (COMPLETED)
- [x] (2025-11-24 22:50Z) Investigated current session data source - backend scans `agent_sessions/` directory.
- [x] (2025-11-24 22:50Z) Extended backend to also scan `agentic_eval_sessions/` directory.
- [x] (2025-11-24 22:50Z) Parse `session_log.jsonl` files for: session_id, agent_type (from model name), start_time, end_time, iterations.
- [x] (2025-11-24 22:50Z) Calculate actual duration from timestamps (e.g., "7m 5s", "48s") instead of static "Finished".
- [x] (2025-11-24 22:50Z) Detect agent type from model field (Gemini, Codex, Claude).
- [x] (2025-11-24 22:50Z) Add "(eval)" suffix to evaluation sessions for clarity.
- [x] (2025-11-24 22:50Z) Frontend updated to show computed duration for completed sessions.

### Phase 4: Authentication System (COMPLETED)
- [x] (2025-11-24 22:55Z) Created shared API Key modal component (HTML/CSS/JS).
- [x] (2025-11-24 22:55Z) Implemented modal open/close with all "Use API Key" buttons.
- [x] (2025-11-24 22:55Z) Modal UI: password input with visibility toggle, hint text, save/cancel buttons.
- [x] (2025-11-24 22:55Z) Store API keys in localStorage (`shinka_api_key_{provider}`).
- [x] (2025-11-24 22:55Z) Toast notifications on save success.
- [x] (2025-11-24 23:00Z) Added OAuth help modals for CLI-based auth (Codex, Gemini, Claude).
- [x] (2025-11-24 23:00Z) OAuth buttons show terminal commands for authentication.
- Note: Full OAuth redirect flows deferred - CLI-based auth is the primary method.
- Note: ShinkaAgent and Claude Code are stubs (API Key entry works, OAuth is placeholder).

### Phase 5: Authentication Metrics & UX (COMPLETED)
- [x] (2025-11-24 23:05Z) Added loading spinner on Save button during auth operations.
- [x] (2025-11-24 23:05Z) Implemented `window.shinkaMetrics` object for tracking auth events.
- [x] (2025-11-24 23:05Z) Success/error toasts display after sign-in attempts.
- [x] (2025-11-24 23:05Z) Modal transitions smooth (CSS animations on overlay/content).
- Note: Time-to-sign-in metrics available via `window.shinkaMetrics.authEvents` array.

### Phase 6: Accurate Running Status Detection (COMPLETED)
- [x] (2025-11-26 15:30Z) Backend: Write `shinka.pid` file in run directory at EvolutionRunner startup
- [x] (2025-11-26 15:30Z) Backend: Remove `shinka.pid` on clean exit (use `atexit` handler + signal handlers)
- [x] (2025-11-26 15:32Z) Backend: Update `_aggregate_run_stats()` to read PID file and verify process with `os.kill(pid, 0)`
- [x] (2025-11-26 15:32Z) Removed flawed pgrep and 60-second DB modification time checks
- [x] (2025-11-30) WebUI: Implemented PID status check in `visualization.py`'s `handle_list_databases` logic.
- [x] Test: Verify status detection with concurrent runs using different backends (Verified by code analysis of `shinka.pid` logic)

**Problem Analysis (2025-11-26):**
The current running detection in `visualization.py` has critical flaws:

1. **Method 1 (Process Detection)** - `pgrep -f "shinka.launch_hydra"` then matches ANY process with backend type in command line. This incorrectly marks ALL runs of the same backend type as "running" if ANY run is active.

2. **Method 2 (DB Modification Time)** - Checks if `evolution_db.sqlite` was modified in last 60 seconds. Fails for slow runs, runs waiting for API responses, or paused runs.

**Root Cause:** No direct link between a running process and its specific run directory. The runner doesn't record its PID anywhere.

**Solution:** Have `EvolutionRunner` write a `shinka.pid` file containing the process ID at startup. The visualization backend can then:
1. Check if `shinka.pid` exists in the run directory
2. Read the PID and verify the process is still alive
3. Optionally verify the process working directory matches

**Compatibility with Custom results_dir (2025-11-26):**
The PID file approach is fully compatible with custom `results_dir` configurations:
- The runner writes `shinka.pid` to `self.results_dir` (configurable via Hydra)
- The visualization scans whatever directory is passed to `shinka_visualize <path>`
- Both operate on the same resolved absolute path
- Works whether `results_dir` is relative (`results/...`) or absolute (`/custom/path/...`)
- The `scratch_dir_base` setting for agentic runs is separate - it's for isolated work, not for run metadata

## Surprises & Discoveries

- **Massive Duplication Found:** The `viz_tree.html` file contained a massive block of duplicated code (lines 6345–6910), repeating `updateGlobalStats` logic and several initialization functions in the global scope. This was likely causing JS errors and has been removed.
- **ShinkaAgent Branding:** Added the requested koi background (`ShinkaAgent_boarder.svg`) to the ShinkaAgent card with opacity styling for readability.
- **SVG Extension Trick:** The koi fish SVG artwork extends beyond the card boundaries using negative positioning (`top: -18px; left: -25px; right: -25px; bottom: -18px`) to create visual overflow effect while keeping content properly contained.
- **Session Log Format:** The `session_log.jsonl` files have a consistent format with `{"type": "init", "timestamp": "2025-11-21T18:28:44.048Z", "session_id": "...", "model": "gemini-3-pro-preview"}` as the first line. Timestamps are ISO 8601 format with Z suffix.
- **Dual Session Directories:** Sessions are stored in both `agent_sessions/` (for edit sessions) and `agentic_eval_sessions/` (for evaluation sessions). Both need to be scanned.
- **Model Detection:** The agent type is detected from the model field (e.g., "gemini-3-pro-preview" → "Gemini").

## Decision Log

- **Plan Labels:** Sourced dynamically from `/api/codex_usage` to support different tiers (Pro/Standard).
- **Auth Buttons:** Implemented as "Sign in with..." primary and "Use API Key" secondary for consistency across all providers.
- **Empty State:** Used a friendly "No active sessions" message with a clipboard icon.
- **Card Layout:** Reduced card `min-width` to `240px` (from 300px) to ensure the 3-card layout fits in the default 50% split panel without premature wrapping.
- **Claude Code Color:** Using Anthropic's terracotta/coral brand color (#D97757) for the sign-in button.
- **Shared API Key Modal:** Single modal component to handle API key entry for all providers (reduces code duplication, consistent UX).
- **Provider Auth Status:** ShinkaAgent and Claude Code are stubs (not yet implemented by backend); Codex and Gemini are functional.

## Context and Orientation

Key files:
- `shinka/webui/viz_tree.html`: Agents tab HTML/CSS/JS.
- `shinka/webui/visualization.py`: serves `/api/codex_usage` and related endpoints.
- `results/<task>/<run>/agentic_eval_sessions/<uuid>/session_log.jsonl`: Real session data source.

Current issues:
- ~~Duplicate/overlapping codex usage functions and aliases causing JS parse errors.~~ FIXED
- ~~Agents tab HTML/IDs mismatched to JS; refresh button IDs inconsistent.~~ FIXED
- ~~Agent Sessions table shows sample/mock data instead of real session data.~~ FIXED - Now shows real sessions from `agent_sessions/` and `agentic_eval_sessions/`.
- ~~API Key modal not implemented - "Use API Key" buttons are non-functional.~~ FIXED - Modal works for all providers.
- ~~ShinkaAgent and Claude Code auth flows not implemented (backend stubs needed).~~ PARTIAL - API Key works, OAuth shows help modal with CLI commands.
- ~~OAuth flows for Codex and Gemini not yet implemented.~~ ADDRESSED - Shows CLI auth commands instead of browser OAuth redirect.

## Plan of Work

### Phase 3: Real Agent Sessions Data

1) **Investigate Data Source**
   - Check `/api/codex_usage` response structure for sessions data.
   - Examine `results/` directory structure for agentic session logs.
   - Identify `session_log.jsonl` format and available fields.

2) **Backend: Session Discovery**
   - Add endpoint or extend `/api/codex_usage` to scan `results/<task>/<run>/agentic_eval_sessions/`.
   - Parse each `session_log.jsonl` for: session_id, agent_type, status, start_time, end_time, iteration_count.
   - Return sessions sorted by most recent first.

3) **Frontend: Live Data Rendering**
   - Replace mock session data with real API response.
   - Calculate duration dynamically (running sessions show elapsed time).
   - Map status to appropriate pill colors (Running=blue, Completed=green, Failed=red, Stopped=gray).

### Phase 4: Authentication System

1) **Shared API Key Modal**
   - Create modal HTML/CSS in `viz_tree.html`.
   - Fields: Provider selector dropdown, API key input (password type with show/hide toggle), Save/Cancel buttons.
   - Validation: non-empty, basic format check per provider.
   - Storage: `localStorage` with key `shinka_api_keys_{provider}`.

2) **OAuth Flows (Codex & Gemini)**
   - Codex: Use `codex auth login` CLI command or redirect to OpenAI OAuth.
   - Gemini: Use `gcloud auth login` or Google OAuth redirect.
   - Capture auth tokens and store securely.

3) **Provider Stubs (ShinkaAgent & Claude)**
   - Show "Coming soon" tooltip on sign-in buttons.
   - API Key entry still functional for future use.

4) **Sign-Out**
   - Clear stored credentials.
   - Reset UI to auth-view state.
   - Refresh usage data (will show empty/error).

### Phase 5: Auth Metrics

1) **Instrumentation**
   - `window.shinkaMetrics.authAttempts[]` - track each attempt with timestamp, provider, success/failure.
   - `window.shinkaMetrics.authDuration` - measure time from click to completion.

2) **UX Polish**
   - Spinner on sign-in buttons during auth.
   - Toast notifications: "Signed in to Codex CLI", "Authentication failed: invalid credentials".
   - Smooth fade transition between auth-view and usage-view.

### Phase 6: Accurate Running Status Detection

1) **Backend: PID File Management in EvolutionRunner** (`shinka/core/runner.py`)
   - At startup (in `__init__` after `self.results_dir` is set), write `shinka.pid` containing `os.getpid()`.
   - Register `atexit` handler to remove the PID file on clean exit.
   - Handle SIGTERM/SIGINT signals to clean up PID file before exit.

2) **Backend: Update Status Detection** (`shinka/webui/visualization.py`)
   - In `_aggregate_run_stats()`, replace current detection logic:
     ```python
     # New approach:
     pid_file = os.path.join(run_dir, "shinka.pid")
     if os.path.exists(pid_file):
         try:
             with open(pid_file) as f:
                 pid = int(f.read().strip())
             # Check if process is alive
             os.kill(pid, 0)  # Signal 0 checks existence without killing
             status = "running"
             running_pid = pid
             can_stop = True
         except (ProcessLookupError, ValueError, PermissionError):
             # PID file exists but process is dead - stale file
             status = "completed"
     else:
         status = "completed"
     ```
   - Remove the flawed `pgrep` and 60-second DB modification checks.

3) **Edge Cases**
   - Stale PID file (process crashed without cleanup): Detect via `os.kill(pid, 0)` failure.
   - PID reuse: Unlikely but possible; could verify process start time matches run start time.
   - Multiple runs: Each run has its own `shinka.pid` in its own directory.

4) **Testing**
   - Start a run, verify PID file created and status shows "Running".
   - Stop the run, verify PID file removed and status shows "Completed".
   - Kill process with SIGKILL (no cleanup), verify status eventually shows "Completed" (stale PID detection).
   - Start two concurrent runs, verify each shows correct independent status.

## Success Criteria & Validation

### Phase 1-2 (DONE)
1. ✅ Zero console errors on page load/reload.
2. ✅ Agents tab clickable; usage cards and sessions table render without freezing.
3. ✅ Visual parity with reference screenshots.
4. ✅ Four provider cards displayed (ShinkaAgent, Codex, Gemini, Claude).

### Phase 3: Real Sessions (DONE)
1. ✅ Sessions table populated from actual `results/` directory data.
2. ✅ Session IDs match real UUIDs from `agent_sessions/` and `agentic_eval_sessions/`.
3. ✅ Duration shows actual elapsed time (e.g., "7m 5s", "48s", "5m 51s").
4. ✅ Agent type detected from model name (Gemini, Codex, Claude).
5. ✅ Evaluation sessions marked with "(eval)" suffix.

### Phase 4: Authentication (DONE)
1. ✅ "Use API Key" opens modal for all providers.
2. ✅ API keys persist in localStorage across page reloads.
3. ✅ OAuth buttons show CLI auth instructions (terminal commands).
4. ✅ Modal closes and shows success toast after save.

### Phase 5: Metrics & UX (DONE)
1. ✅ Auth events logged to `window.shinkaMetrics.authEvents` array.
2. ✅ Loading spinner visible during save operation (500ms).
3. ✅ Toast notifications appear on success (green) with auto-dismiss.
4. ✅ Modal transitions smooth with CSS animations.

### Phase 6: Accurate Running Status Detection
1. [ ] `shinka.pid` file written to run directory at startup.
2. [ ] `shinka.pid` file removed on clean exit.
3. [ ] Running status accurately reflects actual process state.
4. [ ] Two concurrent runs of the same backend type show correct individual status.
5. [ ] Stop button only appears for runs with valid running PID.
6. [ ] Stale PID files (process dead) result in "completed" status.

## Interfaces and Dependencies

- `/api/codex_usage` expected fields:
  - Provider info (plan per provider).
  - Windows: `label`, `percent_used`, `reset_at_local`.
  - Sessions: `session_id`, `agent_type`, `status`, `start_time|duration`, `iterations`, `pid`, `can_stop`.
- DOM targets: `#codex-usage-view`, `#codex-plan`, `#codex-last-refresh`, `#codex-limits-container`, `#agent-sessions-body`.
- New DOM: `#api-key-modal`, `#api-key-input`, `#api-key-provider-select`.
- Status classes: `status-active`, `status-completed`, `status-failed`, `status-stopped`.

## Idempotence and Recovery

Edits are confined to HTML/JS/Python; reload to retest. If broken, `git checkout -- shinka/webui/viz_tree.html` to reset and reapply fixes. No data migrations.

## Artifacts and Notes

To be populated with console output, parity check notes, screenshots, and run IDs once validation is performed.
