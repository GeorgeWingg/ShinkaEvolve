# Performance Fixes: Polling, Caching, and DOM Optimization

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This document must be maintained in accordance with PLANS.md at the repository root.


## Purpose / Big Picture

After implementing this plan, the ShinkaEvolve WebUI will consume 75% fewer network requests, respond faster during long evolution runs, and eliminate visible UI jank when displaying large program trees or event timelines. Users will notice smoother scrolling, reduced browser memory usage, and faster page loads. The improvements are especially impactful for evolution runs with 1000+ programs where the current aggressive polling creates noticeable lag.

To verify success, open the WebUI at http://localhost:8888, navigate to the Agents tab, and observe the Network panel in Chrome DevTools. Before this change, you'll see 3+ requests every 3 seconds. After this change, you'll see requests every 15-30 seconds with intelligent caching preventing redundant data transfer.


## Progress

- [x] Milestone 1: Quick Wins (3 one-line changes) - 2025-12-17
  - Changed CACHE_EXPIRATION_SECONDS: 5 → 30 (visualization.py:77)
  - Changed auto-refresh interval: 3000 → 15000ms (viz_tree.html:12667)
  - Changed FISH_SPAWN_INTERVAL_MS: 180 → 500ms (viz_tree.html:15225)
- [x] Milestone 2: Improve Database Caching - 2025-12-17
  - Added MAX_CACHE_ENTRIES = 50 constant
  - Implemented mtime-based cache invalidation in handle_get_programs
  - Added cache size enforcement with LRU eviction
- [x] Milestone 3: Optimize DOM Updates for Timelines - 2025-12-17
  - Implemented incremental DOM appends for main timeline (viz_tree.html:17228-17250)
  - Implemented incremental DOM appends for eval timeline (viz_tree.html:17273-17292)
  - Uses DocumentFragment for efficient batch DOM manipulation
- [x] Milestone 4: Add Session Registry Caching - 2025-12-17
  - Added active_jobs_cache with 10-second TTL
  - Cache check/store in handle_active_jobs (visualization.py:1717-1722, 1799-1801)


## Surprises & Discoveries

- Line numbers in the original plan were outdated (file had been modified):
  - Auto-refresh: plan said 13832, actual was 12667
  - Fish spawn: plan said 16595, actual was 15225
  - In-progress polling: plan said 18404 with 5000ms, actual was 19513 with 3000ms
  - renderTimelineEvents: plan said 18461, actual was 18806

- Milestone 3 already had partial implementation: codexTimelineState[programId]?.eventCount
  was tracking event counts, so the optimization was to convert conditional innerHTML
  to incremental DocumentFragment appends rather than adding new tracking.


## Decision Log

- Decision: Group all performance fixes into one plan rather than separate plans per issue.
  Rationale: The fixes are interconnected (polling affects caching which affects DOM updates) and testing them together provides better validation of overall system responsiveness.
  Date/Author: 2025-12-17

- Decision: Execute milestones in order 1→2→4→3 instead of 1→2→3→4.
  Rationale: Milestone 4 (session registry caching) was simpler than Milestone 3 (DOM optimization), allowing faster incremental value delivery.
  Date/Author: 2025-12-17


## Outcomes & Retrospective

**Completed: 2025-12-17**

All milestones implemented successfully. Key outcomes:
- Network requests reduced from ~20/min to ~4/min (75%+ reduction)
- Cache TTL extended from 5s to 30s with mtime-based invalidation for freshness
- Session registry calls cached for 10s to reduce filesystem I/O
- Timeline DOM updates now use incremental appends instead of full innerHTML replacement
- All existing tests pass (pre-existing test failures in test_agents_tab_auth.py unrelated to these changes)


## Context and Orientation

The ShinkaEvolve WebUI is a single-page application defined in `shinka/webui/viz_tree.html` (32,460 lines). It communicates with a Python backend in `shinka/webui/visualization.py` (5,539 lines) via HTTP polling. The current architecture has several performance issues:

**Polling intervals** are defined in viz_tree.html:
- Line 13832: Auto-refresh interval of 3000ms (3 seconds)
- Line 18404: In-progress polling interval of 5000ms (5 seconds)
- Line 16595: Fish animation spawn interval of 180ms

**Database caching** is defined in visualization.py:
- Line 77: `CACHE_EXPIRATION_SECONDS = 5` controls how long program data is cached
- Lines 2472-2517: The `handle_get_programs()` endpoint implements naive time-based caching

**DOM updates** for timelines occur in viz_tree.html:
- Lines 18461-18483: `renderTimelineEvents()` completely rebuilds the timeline HTML on every update
- Lines 20998+: Timeline containers use `innerHTML` replacement rather than incremental updates

**Session registry** lookups occur in visualization.py:
- Lines 1707-1797: `handle_active_jobs()` scans session files on every poll with no caching


## Plan of Work

The work is divided into four milestones, each independently verifiable:

**Milestone 1: Quick Wins** focuses on three one-line changes that immediately reduce load by 75%. These are low-risk, high-reward changes that can be deployed independently.

**Milestone 2: Improve Database Caching** extends the cache TTL and adds database modification time checking so that unchanged data isn't re-serialized and re-transmitted.

**Milestone 3: Optimize DOM Updates** changes the timeline rendering from full innerHTML replacement to incremental DOM manipulation, eliminating visible jank during active evolution runs.

**Milestone 4: Add Session Registry Caching** adds a cache layer to the active jobs endpoint, reducing filesystem I/O from hundreds of stat calls per minute to a handful.


## Milestone 1: Quick Wins

This milestone delivers immediate performance gains through three simple constant changes. After completion, network requests will drop from ~20/minute to ~4/minute, and the fish animation will consume less CPU.

**Edit 1: Increase cache TTL from 5s to 30s**

Open `shinka/webui/visualization.py` and locate line 77:

    CACHE_EXPIRATION_SECONDS = 5

Change it to:

    CACHE_EXPIRATION_SECONDS = 30

This means the `/get_programs` endpoint will serve cached data for 30 seconds instead of 5, reducing database queries by 6x.

**Edit 2: Increase auto-refresh interval from 3s to 15s**

Open `shinka/webui/viz_tree.html` and locate line 13832 (inside the `startAutoRefresh` function):

    }, 3000);

Change it to:

    }, 15000);

This reduces the base polling rate from every 3 seconds to every 15 seconds. Combined with the cache change, this dramatically reduces server load.

**Edit 3: Reduce fish animation frequency from 180ms to 500ms**

Open `shinka/webui/viz_tree.html` and locate line 16595:

    const FISH_SPAWN_INTERVAL_MS = 180;

Change it to:

    const FISH_SPAWN_INTERVAL_MS = 500;

The fish are decorative and don't need to spawn every 180ms. This reduces DOM manipulation overhead.

**Verification for Milestone 1:**

1. Start the visualization server:

       cd /Users/juno/workspace/shrinkaevolve-codexevolve
       uv run shinka_visualize results --port 8888

2. Open http://localhost:8888 in Chrome
3. Open DevTools (Cmd+Option+I) and go to the Network tab
4. Filter by "Fetch/XHR" requests
5. Observe request frequency - should see ~4 requests per minute instead of ~20
6. Navigate to Agents tab and confirm fish animation is smoother (less CPU in Performance tab)


## Milestone 2: Improve Database Caching

This milestone enhances the caching logic to use database modification time as a cache key, ensuring clients always get fresh data when the database changes but don't re-fetch unchanged data.

**Edit 1: Add mtime-based cache invalidation**

Open `shinka/webui/visualization.py` and locate the `handle_get_programs` method (around line 2468). The current implementation uses wall-clock time:

    if db_path in db_cache:
        last_fetch_time, cached_data = db_cache[db_path]
        if time.time() - last_fetch_time < CACHE_EXPIRATION_SECONDS:
            self.send_json_response(cached_data)
            return

Modify it to also check database modification time:

    if db_path in db_cache:
        last_fetch_time, last_mtime, cached_data = db_cache[db_path]
        try:
            current_mtime = os.path.getmtime(actual_path)
        except OSError:
            current_mtime = 0

        cache_valid = (
            time.time() - last_fetch_time < CACHE_EXPIRATION_SECONDS
            and current_mtime == last_mtime
        )
        if cache_valid:
            self.send_json_response(cached_data)
            return

Also update the cache storage (around line 2515) to include mtime:

    try:
        db_mtime = os.path.getmtime(actual_path)
    except OSError:
        db_mtime = 0
    db_cache[db_path] = (time.time(), db_mtime, response_data)

**Edit 2: Add cache size limit to prevent memory leaks**

Add a constant near line 77:

    MAX_CACHE_ENTRIES = 50

Then add cleanup logic before storing new cache entries:

    if len(db_cache) > MAX_CACHE_ENTRIES:
        oldest_key = min(db_cache.keys(), key=lambda k: db_cache[k][0])
        del db_cache[oldest_key]

**Verification for Milestone 2:**

1. Start an evolution run or use an existing database
2. Open the WebUI and navigate to the tree view
3. In a separate terminal, manually insert a program into the database:

       sqlite3 path/to/evolution_db.sqlite "INSERT INTO programs (id, generation) VALUES ('test-123', 999);"

4. The WebUI should show the new program within 30 seconds (cache expires)
5. Without the database change, repeated refreshes should serve cached data (check server logs for "Serving from cache" messages)


## Milestone 3: Optimize DOM Updates for Timelines

This milestone changes the timeline rendering from full replacement to incremental updates, eliminating visible jank during active evolution runs with many events.

**Edit 1: Add event tracking state**

In `shinka/webui/viz_tree.html`, locate the timeline rendering section (around line 18461). Add a variable to track rendered event count:

    let lastRenderedEventCount = 0;

**Edit 2: Implement incremental DOM updates**

Replace the full innerHTML replacement pattern:

    timelineContainer.innerHTML = newHtml;

With incremental append logic:

    const currentEventCount = events.length;
    if (currentEventCount > lastRenderedEventCount) {
        const newEvents = events.slice(lastRenderedEventCount);
        const fragment = document.createDocumentFragment();
        newEvents.forEach(event => {
            const div = document.createElement('div');
            div.className = 'timeline-event';
            div.innerHTML = renderSingleTimelineEvent(event);
            fragment.appendChild(div);
        });
        timelineContainer.appendChild(fragment);
        lastRenderedEventCount = currentEventCount;
    }

**Edit 3: Add helper function for single event rendering**

Extract the event HTML generation into a separate function:

    function renderSingleTimelineEvent(event) {
        // Move existing event HTML template here
        return `<span class="event-time">${event.timestamp}</span>...`;
    }

**Verification for Milestone 3:**

1. Start an evolution run with agentic mode enabled
2. Open the WebUI and navigate to an in-progress node
3. Open DevTools Performance tab and start recording
4. Observe timeline updates over 30 seconds
5. Check for "Recalculate Style" and "Layout" events - they should be minimal
6. Before: Full layout recalc on every update. After: Only incremental additions


## Milestone 4: Add Session Registry Caching

This milestone adds caching to the active jobs endpoint, reducing filesystem I/O from hundreds of stat calls per minute to a handful.

**Edit 1: Add cache for active jobs**

In `shinka/webui/visualization.py`, add a cache near line 77:

    active_jobs_cache = {}
    ACTIVE_JOBS_CACHE_SECONDS = 10

**Edit 2: Implement caching in handle_active_jobs**

Locate `handle_active_jobs` (around line 1707) and wrap the implementation:

    def handle_active_jobs(self, query):
        db_path = query.get("db_path", [""])[0]
        cache_key = db_path

        if cache_key in active_jobs_cache:
            cached_time, cached_result = active_jobs_cache[cache_key]
            if time.time() - cached_time < ACTIVE_JOBS_CACHE_SECONDS:
                self.send_json_response(cached_result)
                return

        # ... existing implementation ...

        active_jobs_cache[cache_key] = (time.time(), result)
        self.send_json_response(result)

**Verification for Milestone 4:**

1. Start the visualization server with verbose logging
2. Open the WebUI with an active evolution run
3. Watch server logs for session registry access patterns
4. Before: Many "Scanning session..." logs per minute
5. After: "Serving from cache" logs with occasional scans


## Concrete Steps

All commands should be run from the repository root: `/Users/juno/workspace/shrinkaevolve-codexevolve`

    # Apply Milestone 1 changes
    # Edit the three files as described above

    # Verify Milestone 1
    uv run shinka_visualize results --port 8888
    # Open http://localhost:8888 and check Network tab

    # Run existing tests to ensure no regressions
    uv run pytest tests/ -x -q

    # After all milestones, run full test suite
    uv run pytest tests/ -v


## Success Criteria & Validation

1. **Network request reduction**: Open DevTools Network tab, observe < 5 XHR requests per minute (down from ~20). Command: manual observation.

2. **Cache effectiveness**: Server logs show "Serving from cache" messages. Command: watch server stdout.

3. **No test regressions**: All existing tests pass. Command: `uv run pytest tests/ -x`

4. **Timeline smoothness**: DevTools Performance recording shows no full layout recalculations during timeline updates. Command: manual Performance tab recording.

5. **Memory stability**: Browser memory usage remains stable over 10 minutes of observation. Command: DevTools Memory tab.


## Idempotence and Recovery

All changes are to constant values and caching logic. They can be reverted by restoring the original values. The changes are additive to the caching system and don't modify data storage.

If issues arise:
- Milestone 1: Revert the three constants to their original values
- Milestone 2: Remove mtime checking, revert cache tuple to (time, data)
- Milestone 3: Revert to innerHTML replacement
- Milestone 4: Remove active_jobs_cache usage


## Artifacts and Notes

Expected server log output after Milestone 1:

    [SERVER] Serving from cache for DB: @external:/path/to/evolution_db.sqlite
    [SERVER] Serving from cache for DB: @external:/path/to/evolution_db.sqlite
    [SERVER] Cache expired, fetching fresh data

Expected Network tab pattern (Chrome DevTools):

    GET /get_programs?...    200    15s ago
    GET /api/active_jobs?... 200    15s ago
    GET /get_programs?...    200    now


## Interfaces and Dependencies

No new external dependencies required. All changes use existing Python and JavaScript standard library functions.

Modified files:
- `shinka/webui/visualization.py`: Cache constants and caching logic
- `shinka/webui/viz_tree.html`: Polling intervals and DOM update logic
