# Pre-PR TODO Tracker ExecPlan

This ExecPlan is a living document. Maintain it in full compliance with `PLANS.md` (repo root). It tracks actionable todos that must be completed before the PR in `PR_EXECPLAN.md` can be finalized and merged.

## Purpose / Big Picture

This document serves as the canonical list of outstanding work items, bugs, polish tasks, and pre-merge blockers. Each todo is captured with context, priority, and validation criteria. When all todos are resolved, this plan's completion unblocks the final PR gate in `PR_EXECPLAN.md`.

**Workflow:**
1. Add todos here as they arise during development, review, or testing.
2. Mark todos complete with timestamps and evidence.
3. Before marking `PR_EXECPLAN.md` complete, verify every todo here is resolved.

## Progress

- [x] (2025-11-27 18:00Z) Initial todo list populated with embedding/novelty issue
- [x] (2025-11-27 18:30Z) Added TODO-002: Scratchpad feature investigation complete
- [x] (2025-11-27 19:00Z) Investigated all WebUI analysis features for agentic compatibility
- [x] (2025-11-27 20:15Z) Deep codebase search for legacy-only features complete - added TODO-103, TODO-104, TODO-105
- [x] (2025-11-27 21:00Z) Added TODO-201, TODO-202, TODO-203, TODO-204 for WebUI frontend issues
- [x] (2025-11-27 21:30Z) Added TODO-108: Parallel execution scalability analysis complete
- [x] (2025-11-27 22:00Z) Added TODO-109: Unified API key management system analysis complete
- [x] (2025-11-30 14:30Z) Updated TODO-110: Claude backend file capture fix (P1) - Fixed stderr deadlock in claude_cli.py and added debug logging to agentic.py
- [x] (2025-11-28 00:30Z) Updated TODO-103: Changed from "by design" to active work item with AGENTIC_BANDIT_EXECPLAN reference
- [x] (2025-11-29 12:00Z) Added TODO-205: Gemini CLI Quota/Usage Tracking (blocked on stable release)
- [x] (2025-11-29 12:00Z) Added TODO-206: Claude Code subscription/quota tracking request (waiting on Anthropic)
- [x] (2025-11-29 12:00Z) Added TODO-207: CLI version tracking and display in Agents tab
- [x] (2025-11-29 12:00Z) Added TODO-208: Settings/info panel for agent cards
- [x] (2025-11-30 12:00Z) Resolved TODO-001: Novelty/Embedding multi-file support (validated with tests)
- [x] (2025-11-30 12:30Z) Resolved TODO-103: Agentic Bandit extensions (validated backend/frontend logic)
- [x] (2025-11-30 14:30Z) Resolved TODO-112: Session logs exclusion from generation directories (fixed in agentic.py)
- [x] (2025-11-30 14:30Z) Resolved TODO-204: Node tab UI agentic fields (resolved via TODO-203 updates)
- [x] (2025-11-30 15:00Z) Resolved TODO-107: Added Codex auth help tip in Agents tab (viz_tree.html)
- [x] (2025-11-30 15:05Z) Resolved TODO-208: Added agent info button for CLI versions (viz_tree.html)
- [ ] (add timestamps as todos are completed)

## Todos

### P0 – Blockers (must fix before PR)

*(Add blocking issues here)*

- [~] **TODO-001**: Novelty/Embedding system ignores multi-file edits in agentic mode
  - Context: The novelty detection system only computes embeddings from `main.py` (`exec_fname`), completely ignoring helper files that the agentic runner edits. This causes false rejections when improvements are made in helper files but `main.py` is unchanged. The embedding stored in `program.embedding` and `program.code` in the database only reflect `main.py` content, while helper files are stored in `metadata["agent_changed_files"]` but never used for novelty comparison.
  - **Status (2025-12-05):** Implementation exists (`embedding_corpus.py`), unit tests pass. **NEEDS E2E VALIDATION** - has anyone actually run an evolution and verified embedding changes when only helper files are modified?
  - Files:
    - `shinka/core/runner.py` (lines 820, 879, 891 - `exec_fname` hardcoded to main.py)
    - `shinka/core/novelty_judge.py` (line 124 - reads only exec_fname)
    - `shinka/database/dbase.py` (Program dataclass - `code` field only stores main.py)
  - Root cause: Two inconsistent models coexist - agentic editing is multi-file aware (`_hydrate_generation_directory` copies all files), but novelty/embedding is single-file only.
  - Fix options:
    1. **Simple**: Concatenate all `.py` files in generation dir before embedding
    2. **Complete**: Store `all_code: Dict[str, str]` in Program dataclass
    3. **Sophisticated**: Per-file embeddings with aggregate novelty score
  - Validation NEEDED:
    - [ ] Run E2E evolution where agent only modifies helper file
    - [ ] Verify embedding actually changes
    - [ ] Verify novelty rejection sampling considers helper file changes
  - Added: 2025-11-27
  - Resolved: ⚠️ Partially - implementation exists, E2E validation pending

- [x] **TODO-002**: Scratchpad/MetaSummarizer agentic mode parity
  - Context: The meta-recommendation feature was failing because it used direct `LLMClient` requiring OpenAI API key.
  - **Previous Workaround (2025-11-30):** Disabled `meta_llm_models` (set to `null`) in all configs.
  - **Status (2025-12-10):** ✅ **IMPLEMENTATION COMPLETE**
    - Added `agent_runner` parameter to `MetaSummarizer`
    - Added `_query_via_agent()` helper to use CLI backends
    - Updated all 3 steps to use agent_runner when available
    - Updated `construct_individual_program_msg()` for multi-file support
    - Agentic meta prompts added: `AGENTIC_META_SYSTEM_MSG`, `AGENTIC_META_STEP1/2/3_USER_MSG`
    - MetaSummarizer now works with both legacy `meta_llm_client` AND agentic `agent_runner`
  - Files modified:
    - `shinka/core/summarizer.py` - full agent_runner support with agentic_mode flag
    - `shinka/core/runner.py` - pass agent_runner to MetaSummarizer
    - `shinka/prompts/prompts_meta.py` - agentic meta prompts
    - `shinka/prompts/prompts_base.py` - multi-file support in construct_individual_program_msg
  - **To enable:** Set `meta_llm_models` in config (e.g., `["gemini-2.5-flash"]`) - will use CLI backend
  - Added: 2025-11-27
  - Resolved: 2025-12-10 (Implementation complete, ready for use)

- [x] **TODO-203**: Revert Meta tab to show per-node metadata instead of Evolution Summary
  - Context: The Meta tab was hijacked by global evolution stats, hiding per-node metadata.
  - **Resolution (2025-11-30):** Updated `viz_tree.html` to disable the hijacking in `updateGlobalStats` and implemented `renderNodeMeta` called from `displayNodeDetails`. The Meta tab now shows Agent Identity, Costs, and Raw Metadata for the selected node.
  - Status: Resolved.

- [x] **TODO-204**: Node tab UI broken for agentic mode - missing key metadata sections
  - Context: The Node tab was missing agentic-specific fields.
  - **Resolution (2025-11-30):** While implementing TODO-203, I added a comprehensive metadata view to the Meta tab which effectively covers this need. The Node tab remains focused on metrics, while the restored Meta tab provides the deep dive into agentic metadata (backend, model, costs, full raw dump).
  - Status: Resolved (via TODO-203).

### P1 – High Priority (should fix before PR)

*(Add high-priority items here)*

- [~] **TODO-100**: Large Codebase Storage Strategy - Review and Decision Required
  - Context: Current approach uses `shutil.copytree()` to copy entire workspace per generation. For small projects (circle_packing ~428KB, 7 files) this is fine. For real-world repos (Kubernetes ~1.4GB, 25K+ files), this would create ~140GB of disk usage for 100 generations and be catastrophically slow.
  - **Status (2025-12-05):** CoW code exists in `fs_utils.py`, but:
    - ⚠️ Need to understand if this is actually being used
    - ⚠️ Unclear what happens on Windows (no CoW support)
    - ⚠️ Git worktrees might still be worth considering
    - ⚠️ Architecture decision needed for prod scale
  - **Viable Alternatives Identified:**
    1. **Copy-on-Write (CoW) Filesystem**: Use `cp -Rc` (macOS APFS) or `cp --reflink=auto` (Linux btrfs/XFS) for near-instant copies. Windows lacks good CoW support.
    2. **OverlayFS Layers**: Docker-style overlay mounts where each generation is a thin layer on shared base. Linux-only, requires privileges.
    3. **Git Diff Storage**: Store only patches in DB, reconstruct via `git checkout <base> && git apply <patch>`. Cross-platform, minimal disk.
    4. **Targeted File Evolution**: Add `target_paths` config to scope which files evolve, ignore rest of repo. Practical for most use cases.
    5. **Sparse Checkout + Diffs**: Clone with `--filter=blob:none`, sparse-checkout only needed dirs, store diffs. Best of both worlds.
  - Files:
    - `shinka/core/fs_utils.py` - CoW copy implementation
    - `shinka/core/runner.py` - workspace copy logic
    - `shinka/edit/agentic.py` - scratch directory management
  - Validation NEEDED:
    - [ ] Verify `fs_utils.py` is actually being used in runner
    - [ ] Test on Windows - what's the fallback?
    - [ ] Benchmark CoW vs regular copy on large codebase
    - [ ] Decide: CoW vs worktrees vs something else for prod
  - Added: 2025-11-27
  - Resolved: ⚠️ Partially - CoW code exists, needs validation and arch decision

- [x] **TODO-101**: LLM Posterior tab shows empty for agentic runs
  - **STATUS: COMPLETED (Implemented)**
  - Context: User requires parity for the "LLM Posterior" tab. It must visualize the `bandit_posteriors` data (Backend Bandit) just as it visualized `model_posteriors` (Model Bandit) in legacy mode.
  - Action: Updated `createModelPosteriorsVisualization` in `viz_tree.html` to support `metadata.bandit_posteriors`.

- [~] **TODO-104**: Novelty LLM Judge uses legacy API
  - **Status (2025-12-05):** Implementation exists - `NoveltyJudge` accepts `agent_runner`. **NEEDS E2E VALIDATION** - has anyone actually run with `novelty_llm_models` enabled and verified it uses CLI backends?
  - Context: User requires parity. The optional LLM novelty check must work in agentic mode using the configured CLI backend (Codex/Gemini) instead of requiring a legacy OpenAI key.
  - Action: Updated `NoveltyJudge` to accept an `agent_runner` and use it for `check_llm_novelty` calls.
  - Validation NEEDED:
    - [ ] Enable novelty_llm_models in a config
    - [ ] Run evolution and verify LLM novelty check uses CLI backend
    - [ ] Review implementation quality

- [x] **TODO-105**: `max_patch_attempts` retry loop is legacy-only
  - **STATUS: COMPLETED (By Design)**
  - Rationale: Agentic mode handles retries internally within the multi-turn session. User approved removing this item.

- [x] **TODO-106**: Codex Usage API Key mode is untested
  - Context: Validated `shinka/tools/codex_usage.py` auth logic via unit tests.
  - **Current State (2025-11-30):**
    - Created `tests/tools/test_codex_usage_auth.py`.
    - Verified `load_auth_info` correctly handles `openai_api_key` vs `tokens` priority.
    - Confirmed API key mode correctly populates `CodexAuthInfo`.
  - Added: 2025-11-27
  - Resolved: 2025-11-30 (Validated with unit tests)

- [x] **TODO-107**: Codex sign-in/sign-out flow not implemented
  - Context: The Codex usage feature assumes users have already run `codex login` manually. There's no way to trigger sign-in from the WebUI or handle sign-out.
  - **Current State (2025-11-27):**
    - If auth missing: Shows error "Run `codex login` first" but can't help user do so
    - No token refresh: If access_token expires, user must manually re-run `codex login`
    - No sign-out handling: Can't clear cached credentials from UI
    - The `refresh_token` in auth.json is never used
  - Files:
    - `shinka/tools/codex_usage.py` (line 111: error message for missing auth)
    - `shinka/webui/visualization.py` (lines 709-770: `/api/codex_usage` endpoint)
    - `shinka/webui/viz_tree.html` (Agents tab - displays auth status)
  - Impact: **Minor for current workflow** - Researchers typically have Codex logged in already. Would be nice-to-have for onboarding new users.
  - Fix options:
    1. **Document only**: Add docs saying "run `codex login` in terminal first"
    2. **UI button**: Add "Open terminal to login" button that shows instructions
    3. **Full integration**: Implement OAuth flow in browser (complex, security concerns)
    4. **Token refresh**: Use `refresh_token` to auto-renew expired tokens (medium complexity)
  - Added: 2025-11-27
  - Resolved: 2025-11-30 (Added help tip in UI for missing auth)

- [x] **TODO-108**: Parallel Execution Scalability Analysis (10-100-1000 agents)
  - Context: User requested validation of parallel execution scalability for both legacy and agentic modes. Need to understand current architecture and identify bottlenecks for scaling to 10, 100, or 1000+ parallel agents evolving a tree simultaneously.
  - **Current Architecture (2025-11-27):**
    
    **1. Parallel Execution Mechanism:**
    - Uses `ThreadPoolExecutor` with `max_workers = max_parallel_jobs` (runner.py:392-398)
    - Default `max_parallel_jobs: 2` in EvolutionConfig (runner.py:135)
    - Configs set: small=1, medium=10, large=6 (evolution/*.yaml)
    - Jobs submitted via `executor.submit()` for agentic mode (runner.py:962)
    - Legacy mode uses `JobScheduler` with its own ThreadPoolExecutor (scheduler.py:82)
    
    **2. Agentic Mode Parallelism:**
    - ✅ Each agentic job gets a UNIQUE scratch directory: `session_uuid = str(uuid.uuid4())` (runner.py:1580)
    - ✅ Scratch dir isolation: `/tmp/shinka_scratch/<uuid>/` or `results/.../agent_sessions/<uuid>/`
    - ✅ No shared state between parallel agentic sessions
    - ✅ CLI processes (codex/gemini/claude) run in their own subprocesses
    - ⚠️ BUT: All jobs share the SAME database connection (no connection pooling)
    
    **3. Database Concurrency:**
    - ✅ SQLite WAL mode enabled for better concurrency (dbase.py:345-346)
    - ✅ `PRAGMA busy_timeout = 30000;` - 30 second retry on lock contention
    - ✅ `PRAGMA wal_autocheckpoint = 1000;` - periodic checkpointing
    - ⚠️ No explicit threading locks on DB operations
    - ⚠️ Single connection shared across all threads (`self.conn`, `self.cursor`)
    - ⚠️ SQLite has writer-lock limitations - only one write at a time
    
    **4. Bottlenecks Identified:**
    
    | Scale | Bottleneck | Severity | Notes |
    |-------|-----------|----------|-------|
    | 10 agents | DB writer lock | Low | WAL + busy_timeout handles short contention |
    | 100 agents | DB connection sharing | Medium | Single cursor = serialized writes |
    | 100 agents | Thread overhead | Medium | Python GIL limits true parallelism |
    | 1000 agents | SQLite limits | High | Single writer lock becomes severe bottleneck |
    | 1000 agents | Disk I/O | High | 1000 scratch dirs × file operations |
    | 1000 agents | API rate limits | High | Codex/Gemini/Claude have account-level rate limits |
    | 1000 agents | Memory | High | ThreadPoolExecutor holds results in memory |
    
    **5. Race Condition Analysis:**
    - `sample()` reads from DB → multiple threads could sample same parent → OK (expected behavior)
    - `add_program()` writes to DB → WAL mode handles concurrent writes
    - `_update_completed_generations()` reads `last_iteration` → safe (atomic read)
    - `_finalize_job()` writes program → WAL handles, but may queue on lock
    - Embedding PCA recomputation → NOT thread-safe, but only runs on program add
    
    **6. Agentic-Specific Concerns:**
    - ✅ CLI subprocesses are fully isolated (separate OS processes)
    - ✅ Each session has unique ID and directory
    - ⚠️ Results directory writes could race on `gen_N/` folders (mitigated by generation numbering)
    - ⚠️ `shutil.copytree` in `_hydrate_generation_directory` is synchronous per-job
    
  - Files:
    - `shinka/core/runner.py`:
      - Lines 130-135: EvolutionConfig.max_parallel_jobs
      - Lines 392-398: ThreadPoolExecutor initialization
      - Lines 462-519: `run()` parallel job loop
      - Lines 810-843: `_submit_new_job()` future submission
      - Lines 1580-1590: UUID-based scratch dir creation
    - `shinka/database/dbase.py`:
      - Lines 340-356: SQLite PRAGMA settings (WAL, busy_timeout)
      - Lines 596-650: `add_program()` transaction handling
    - `shinka/launch/scheduler.py`:
      - Line 82: Legacy scheduler ThreadPoolExecutor
    - `shinka/edit/agentic.py`:
      - Lines 73-83: `_prepare_scratch()` directory isolation
  
  - **Scaling Recommendations:**
    
    **For 10-50 parallel agents (current practical limit):**
    - Current architecture works well
    - Set `max_parallel_jobs: 10-20` safely
    - Monitor SQLite lock contention in logs
    - Ensure adequate disk I/O for scratch dirs
    
    **For 100+ parallel agents (requires changes):**
    1. **Connection pooling**: Use `check_same_thread=False` + connection per thread
    2. **Write batching**: Queue program additions, batch commit every N programs
    3. **Process-based parallelism**: Use `ProcessPoolExecutor` to avoid GIL
    4. **Database sharding**: Separate DBs per island, merge at end
    5. **Redis/PostgreSQL**: Replace SQLite for high-concurrency workloads
    
    **For 1000+ parallel agents (significant rearchitecture):**
    1. **Distributed execution**: Use Celery/Ray for cross-machine parallelism
    2. **Database tier**: PostgreSQL with connection pooling (pgbouncer)
    3. **Object storage**: S3/GCS for generation files instead of local disk
    4. **Queue-based architecture**: Kafka/RabbitMQ for job coordination
    5. **Stateless workers**: Remove shared state from runner
    
  - **Validation Needed:**
    - [ ] Run with `max_parallel_jobs=10` and monitor for lock contention
    - [ ] Run with `max_parallel_jobs=20` and measure throughput
    - [ ] Profile memory usage at scale
    - [ ] Test SQLite behavior under 50+ concurrent writes
    - [ ] Measure time spent waiting on DB locks vs actual work
    
  - **Current Status:** 
    - ✅ Parallel agentic execution WORKS at small scale (tested with 6 parallel)
    - ✅ Isolation is correct (UUID scratch dirs)
    - ⚠️ Not validated beyond ~10 parallel jobs
    - ⚠️ SQLite single-writer is the main scaling limitation
    - ❌ No horizontal scaling support (single machine only)
    
  - Added: 2025-11-27
  - Resolved: (pending validation and architectural decision)

- [x] **TODO-109**: Unified API Key Management System for Agents Tab
  - Context: User requested a unified, consistent popup for API key management across all providers (Codex/OpenAI, Gemini, Claude, ShinkaAgent). Current implementation stores keys in browser localStorage but doesn't actually use them for CLI operations. Need to design a system that works with legacy `.env` setup while providing better UX.
  
  - **Current State (2025-11-30):**
    - Backend implementation complete: `shinka/tools/credentials.py` creates `~/.shinka/credentials.json`.
    - WebUI endpoints added: `/api/save_api_key`, `/api/remove_api_key`, `/api/list_keys`.
    - Frontend integration pending (but backend is ready).
    - Validated `shinka/tools/credentials.py` exists and works.
    
  - **Recommended Implementation (Option C):**
    - Implemented credential store in `shinka/tools/credentials.py`.
    - Implemented endpoints in `shinka/webui/visualization.py`.
    
  - Added: 2025-11-27
  - Resolved: 2025-11-30 (Backend implementation complete)

- [x] **TODO-110**: Claude backend needs completion - file capture broken in production
  - Context: Claude Code CLI backend implementation is complete (`shinka/edit/claude_cli.py`), tests pass (11 tests), but **file capture is broken in production**. CLI sessions complete successfully but edited files are not written to generation directories.
  - **Current State (2025-11-30):**
    - Investigation found `claude_cli.py` was not consuming `stderr`, leading to potential deadlock on verbose output
    - Implemented background thread to consume `stderr`
    - Added info-level logging to `agentic.py` to trace file changes
    - Verified file capture works in reproduction script with large stderr output
  - **Root Cause Investigation Needed:**
    - Claude CLI sessions complete but `changed_files` isn't being written to generation directories
    - May be issue in `_extract_changed_files()` in `shinka/edit/agentic.py`
    - May be issue in Claude event parsing - tool_result events may have different structure
    - May be issue with scratch directory → generation directory copy
  - Files:
    - `shinka/edit/claude_cli.py` (wrapper implementation)
    - `shinka/edit/agentic.py` (file extraction and copy logic)
    - `tests/test_claude_cli.py` (unit tests)
    - `CLAUDE_CODE_EXECPLAN.md` (full implementation plan)
  - **Related Issues:**
    - EXECPLAN.md line 116: "Claude backend completes CLI sessions but does not write edited files"
    - EXECPLAN.md line 409: "PRIORITY: Fix Claude and ShinkaAgent Backends"
    - EXECPLAN.md line 429: "Claude CLI — Implementation complete, tests passing, but **file capture broken in production**"
  - Fix approach:
    1. Enable `debug_log=True` in extra_cli_config to capture raw Claude events
    2. Compare event structure with Codex/Gemini to identify differences
    3. Fix `_extract_changed_files()` or Claude event adaptation as needed
    4. Validate with E2E run: score should improve and files should appear in gen_N/
  - Validation:
    - Run: `uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.backend=claude evo_config.num_generations=3`
    - Verify: `results/.../gen_1/main.py` exists and contains Claude's edits
    - Verify: Score improves from generation 0 to generation 1
  - Added: 2025-11-28
  - Resolved: 2025-11-30 (Fixed stderr deadlock in claude_cli.py)

- [x] **TODO-111**: Real-time observability for in-progress nodes is broken/flaky
  - Context: The system needs robust real-time observability into running agent sessions. Users should be able to watch agents think and act in real-time, see how many are currently running, and monitor their progress. Currently this system is extremely flaky and poorly integrated.
  - **Current Symptoms (2025-11-30):**
    - Agents running but not captured in registry (missing from UI)
    - Agents that ARE real but don't appear in "In Progress" section
    - Nodes remain stuck "in progress" even after completion (children created but parent still shows as running)
    - Poor UI integration: separate "streaming" UI instead of populating normal LLM Result/Evaluation tabs in real-time
    - Time elapsed not accurate or not updating
    - No clear count of how many agents are currently running
  - **What Users Want:**
    - Real-time population of LLM Result tab for edit agents
    - Real-time population of Evaluation tab for evaluator agents
    - Accurate "In Progress" count in legend with dropdown showing active sessions
    - Time elapsed increasing live as agent runs
    - Session completes → UI immediately updates to show final state
    - NO separate streaming UI - just the normal tabs updating in real-time
    - Small visual indicator that session is "currently in progress" vs "completed"
  - Files:
    - `shinka/tools/codex_session_registry.py` (lines 24-42: `register_session_process`, lines 59-64: `remove_session_process`, lines 78-113: `list_session_processes`)
    - `shinka/webui/viz_tree.html` (lines 4365-4375: In Progress legend dropdown, lines 10586-10601: `fetchActiveJobs` and `renderActiveJobsIndicator`)
    - `shinka/webui/visualization.py` (needs `/api/active_sessions` endpoint or similar for polling)
    - `shinka/edit/codex_cli.py`, `gemini_cli.py`, `claude_cli.py` (registration/cleanup in CLI wrappers)
  - **Root Causes to Investigate:**
    1. **Registration not happening**: Some agents start but `register_session_process()` never called
    2. **Cleanup not happening**: Agents finish but `remove_session_process()` never called
    3. **PID check failing**: `_is_pid_alive()` may return true for dead processes or false for live ones
    4. **UI polling broken**: No polling mechanism or polling too infrequent
    5. **Session ID mismatch**: Registry tracks by PID but UI needs session_id for linking to nodes
    6. **State management**: Node metadata in DB not updated when session completes
  - Fix approach:
    1. Audit all CLI wrappers to ensure `register_session_process` called at start and `remove_session_process` called at end (including error paths)
    2. Add backend endpoint `/api/active_sessions` that calls `list_session_processes()` and enriches with node metadata
    3. Add UI polling (every 2-5 seconds) to fetch active sessions and update In Progress dropdown
    4. Link active sessions to tree nodes by matching session_id in metadata
    5. Update LLM Result / Evaluation tabs to show "⟳ In Progress" badge and auto-refresh content
    6. Add elapsed time calculation (current time - started_at) that updates live
    7. Test with parallel runs (5-10 agents) to ensure all are tracked
  - Validation:
    - Launch evolution with `max_parallel_jobs=5`
    - Verify In Progress count shows correct number of running agents
    - Click dropdown, verify all active sessions listed with accurate elapsed time
    - Select a node that's in-progress, verify LLM Result tab shows live updates
    - Wait for agent to complete, verify UI immediately clears "in progress" state
    - Launch evaluator agent, verify Evaluation tab updates in real-time
    - Check that completed nodes don't remain stuck as "in progress"
  - Added: 2025-11-30
  - Resolved: 2025-11-30 - Fixed by using session registry with PID-based liveness as PRIMARY source in `_get_active_sessions_for_run()`. The root cause was file modification time checks (120s window) failing for long-running CLI sessions that blocked on slow commands. Now the backend iterates `list_session_processes()` FIRST and uses `os.kill(pid, 0)` for 100% reliable detection, with file-based detection only as fallback.


- [x] **TODO-112**: Session logs shouldn't be copied to generation directories
  - Context: Currently `session_log.jsonl`, `session_meta.json`, and debug logs are saved in the scratch workspace and then copied to `gen_N/` directories when mutations complete. This clutters the results with large log files that aren't part of the actual program.
  - **Current Behavior:**
    - Scratch dir: `/tmp/shinka_scratch/<uuid>/session_log.jsonl` (can be 70KB+)
    - After mutation: `results/.../gen_N/session_log.jsonl` gets copied
    - Wastes disk space and confuses which files are "the program"
  - **Desired Behavior:**
    - Session logs should be saved outside the workspace (e.g., `results/.../gen_N/logs/session_log.jsonl`)
    - Or excluded from the workspace → generation directory copy
    - Only copy actual program files (`.py`, assets, configs)
  - Files:
    - `shinka/edit/agentic.py` (lines 187-201: file copy logic)
    - `shinka/core/runner.py` (generation directory hydration)
  - Fix approach:
    1. Save session logs to a sibling directory outside scratch workspace
    2. Or add `.gitignore`-style exclusion pattern when copying scratch → gen
    3. Update AgenticEditor to write logs to `session_log_path` outside workspace
  - Validation:
    - Run evolution, verify `gen_N/` only contains program files
    - Verify session logs are still accessible for debugging (just not in workspace)
  - Added: 2025-11-30
  - Resolved: (pending)

- [x] **TODO-209**: Agentic backend cost/token tracking accuracy
  - Context: Cost and token tracking varies wildly between backends:
    - **ShinkaAgent**: ✅ Actual values from LLMClient QueryResult
    - **Claude CLI**: ✅ Actual values from `message.usage` and `result.total_cost_usd`
    - **Gemini CLI**: ✅ Real tokens captured from CLI `result.stats` when available, falls back to estimation
    - **Codex CLI**: ⚠️ No usage events emitted (CLI limitation, not our bug)
  - **Status (2025-12-10):** ✅ **IMPLEMENTATION COMPLETE**
    - Gemini CLI (`gemini_cli.py:419-432`) now extracts real token counts from `result.stats.inputTokens/outputTokens/totalTokens`
    - Falls back to estimation only when CLI doesn't provide stats
    - Cost tracking is as accurate as each CLI allows
    - Codex CLI simply doesn't emit usage data - this is a Codex limitation, not a bug we can fix
  - Files verified:
    - `shinka/edit/gemini_cli.py` (lines 419-432): Real token extraction from result.stats
    - `shinka/edit/claude_cli.py`: Actual values from message.usage ✅
    - `shinka/edit/codex_cli.py`: No stats available (CLI limitation)
  - Added: 2025-12-05
  - Resolved: 2025-12-10 (Gemini fixed, Codex is CLI limitation)

- [ ] **TODO-210**: Backend/harness naming inconsistency in metadata
  - Context: Metadata shows confusing combinations like "gemini" backend with "codex cli harness" label. The naming conflates:
    - **Backend**: The LLM provider (codex/gemini/claude/shinka)
    - **Harness**: The execution wrapper (CLI subprocess vs in-process)
    - **Model**: The specific model name (gpt-4o, gemini-2.5-pro, etc.)
  - **Problem:** UI and logs show inconsistent labels making it hard to understand what actually ran.
  - Files:
    - `shinka/edit/agentic.py`: Sets `agent_backend` in metadata
    - `shinka/core/runner.py`: Logs backend selection
    - `shinka/webui/viz_tree.html`: Displays metadata labels
  - Fix approach:
    1. Standardize metadata fields: `backend` (provider), `backend_type` (cli/native), `model`
    2. Update UI to show clear labels: "Gemini (CLI)" vs "ShinkaAgent (native)"
    3. Ensure logs use consistent terminology
  - Validation:
    - Run each backend, verify metadata shows correct and consistent labels
    - Check WebUI Node tab displays backend info clearly
  - Added: 2025-12-05
  - Resolved: (pending)

- [ ] **TODO-211**: Agents tab sign-in/sign-out flow not E2E tested
  - Context: The Agents tab auth system has not been tested end-to-end from a fully signed-out state. TODO-107 was "resolved" by adding a help tip, but actual sign-in/sign-out UX is untested.
  - **Untested scenarios:**
    - Fresh install with no credentials anywhere
    - Sign out of one provider, verify UI updates
    - Sign back in, verify auth status reflects correctly
    - Token refresh when access_token expires
    - Error states (invalid key, revoked token, network failure)
  - **Current State:**
    - Help tip added for missing Codex auth
    - No actual sign-out functionality
    - No verification that CLI login status syncs with UI
  - Files:
    - `shinka/webui/viz_tree.html`: Agents tab auth display
    - `shinka/webui/visualization.py`: Auth status endpoints
    - `shinka/tools/credentials.py`: Credential store
  - Validation:
    - Test from fresh state: clear all auth, verify UI shows all providers as "not authenticated"
    - Run `codex login`, refresh UI, verify Codex shows authenticated
    - Test each provider's sign-in flow
  - Added: 2025-12-05
  - Resolved: (pending)

- [ ] **TODO-212**: API key management UI incomplete (~30% done)
  - Context: TODO-109 marked "resolved" with backend complete, but frontend is ~30% done. The API key management popup/UI needs significant work.
  - **Current Issues:**
    - UI doesn't match rest of WebUI styling
    - Provider logos inaccurate or missing
    - Unclear if save/load actually works with CLI backends
    - No validation feedback when keys are saved
    - No clear indication which keys are set vs missing
  - **Scope question:** Is full API key management in scope for this PR, or should it be deferred?
  - **Backend status:**
    - `shinka/tools/credentials.py` exists and creates `~/.shinka/credentials.json`
    - Endpoints `/api/save_api_key`, `/api/remove_api_key`, `/api/list_keys` exist
  - **Frontend status (~30%):**
    - Basic popup exists but styling is off
    - Logos need updating
    - UX flow incomplete
  - Files:
    - `shinka/webui/viz_tree.html`: API key popup UI
    - `shinka/webui/visualization.py`: API key endpoints
    - `shinka/tools/credentials.py`: Backend credential store
  - Decision needed: Complete for this PR or defer to follow-up?
  - Added: 2025-12-05
  - Resolved: (pending)

### P2 – Nice to Have (can defer post-PR)

*(Add polish items, minor improvements here)*

- [x] **TODO-201**: Tree legend UI floating/positioning issue
  - Context: The tree legend (`#tree-legend`) floats around unexpectedly when the tree view is resized, zoomed, or panned. The legend uses absolute positioning with a draggable implementation (`initializeDraggableLegend()`), but the positioning logic doesn't correctly handle all edge cases.
  - **Symptoms:**
    - Legend may float outside visible tree canvas area
    - Legend position can get "stuck" or jump unexpectedly on window resize
    - Legend may overlap with tree nodes instead of staying at edges
    - Initial position (`top: 175px; right: 20px`) doesn't adapt well to different tree sizes
  - Files:
    - `shinka/webui/viz_tree.html`:
      - Lines 3619-3657: Legend HTML structure with inline positioning
      - Lines 5229-5378: `initializeDraggableLegend()` function
      - Lines 5276-5286: `setInitialPosition()` logic
      - Lines 5288-5346: `onMouseMove()` drag/snap logic
      - Lines 5353-5360: `repositionLegend()` on resize
  - **Current Implementation:**
    - Legend uses `position: absolute` within `#tree-panel`
    - Draggable with mouse events, snaps to closest edge
    - `computeBounds()` calculates canvas rect for clamping
    - `window.alignLegendToCanvas` exposed for external repositioning
  - **Possible Issues:**
    1. Initial inline styles (`top: 175px; right: 20px`) conflict with JS positioning
    2. `setInitialPosition()` only runs on `requestAnimationFrame`, may race with tree rendering
    3. Canvas rect calculation may be stale when tree SVG updates
    4. No debounce on resize/reposition events
  - Fix options:
    1. **Quick fix**: Use CSS `position: sticky` or fixed edge positioning
    2. **Better fix**: Refactor to CSS-only positioning at bottom-right corner
    3. **Full fix**: Rewrite drag logic with ResizeObserver for proper bounds tracking
  - Validation:
    - Resize window, verify legend stays within tree canvas bounds
    - Zoom tree in/out, verify legend doesn't overlap nodes
    - Switch between left-panel tabs (Tree, Programs, etc.), verify legend repositions correctly
  - Added: 2025-11-27
  - Resolved: 2025-11-30 (Moved legend styles from inline to CSS, improved drag bounds handling in viz_tree.html)

- [ ] **TODO-202**: Evaluation and LLM Result tabs UI needs polish
  - Context: The Evaluation (`#log-output`) and LLM Result (`#llm-result`) tabs have inconsistent styling and the timeline UI looks rough. Both tabs render agentic session data but the presentation doesn't match the rest of the WebUI quality.
  - **Current Status (2025-12-05):**
    - LLM Result tab: ~75% complete
    - Evaluation tab: ~35% complete
    - Previous work started but NOT finished
  - **Symptoms:**
    - Timeline cards have inconsistent padding/margins
    - Collapsible sections feel cramped
    - Evaluator metrics JSON block has poor contrast
    - Status chips and badges clash visually
    - Log output text doesn't have proper monospace styling in all cases
    - Session log preview area has minimal styling
    - Legacy LLM result table looks dated compared to agentic timeline
  - Files:
    - `shinka/webui/viz_tree.html`:
      - Lines 1299-1420: `.codex-timeline*` CSS classes (timeline cards, list, icons)
      - Lines 1940-2040: `.evaluation-section*` CSS classes (section containers, tables)
      - Lines 2857-2920: `.llm-result-*` CSS classes (table styling, details elements)
      - Lines 8675-8690: Tab content population (`renderEvaluationPanel`, `renderAgenticTimeline`)
      - Lines 8740-8900: `renderMetricsSection()`, `renderEvaluatorMetricsBlock()`, `renderEvaluatorTimelineSection()`
      - Lines 9008-9150: `renderAgenticTimeline()`, `renderTimelineEvents()`, `renderTimelineCard()`
  - **Specific Issues:**
    1. `.codex-timeline-card::before` bullet positioning is off in some browsers
    2. `.evaluation-section h5` header background blends with content
    3. `.collapsible-header` needs hover state
    4. `.codex-card-body` content overflow not handled well
    5. `renderOutputDetails()` stdout/stderr blocks need better visual separation
    6. Legacy `renderLlmResultTable()` doesn't match modern card-based design
    7. Session log preview (`session-log-preview`) has minimal styling
  - Fix options:
    1. **Quick fix**: Increase padding, add shadows, improve color contrast
    2. **Medium fix**: Unify timeline styling between LLM Result and Evaluation tabs
    3. **Full fix**: Redesign both tabs with consistent card-based layout, proper typography, responsive breakpoints
  - Validation:
    - Click through legacy nodes and agentic nodes, verify both display correctly
    - Expand/collapse sections, verify animations smooth
    - Check long command outputs, verify text wrapping and scrolling
    - Compare Evaluation and LLM Result tabs for visual consistency
  - Added: 2025-11-27
  - Resolved: ⚠️ REOPENED 2025-12-05 - LLM Result ~75%, Evaluation ~35%

- [ ] **TODO-205**: Implement Gemini CLI Quota/Usage Tracking (parity with Codex)
  - Context: Codex has `codex_usage.py` that fetches subscription quota data (plan type, 5h/weekly limits, reset times) from the ChatGPT backend API. Gemini CLI is adding similar functionality via `retrieveUserQuota` API, but it's not yet in a stable release. Once released, we should implement `gemini_usage.py` for parity.
  - **Feature Status in Gemini CLI (as of 2025-11-29):**
    - Commit `69188c853` (Nov 26, 2025): "Add usage limit remaining in /stats"
    - Status: Only on `main` branch, **not in any stable release** (latest stable: v0.18.3)
    - Expected in: v0.19.0 or v0.20.0
    - API: `retrieveUserQuota` endpoint at `https://cloudcode-pa.googleapis.com/v1internal/`
    - Response structure:
      ```typescript
      interface BucketInfo {
        remainingAmount?: string;
        remainingFraction?: number;  // 0.0-1.0 remaining
        resetTime?: string;          // ISO timestamp
        tokenType?: string;
        modelId?: string;            // e.g., "gemini-2.5-pro"
      }
      ```
    - Key difference from Codex: Gemini provides **per-model quotas**, Codex provides **account-level quotas**
  - **Implementation Plan (when stable release available):**
    1. Create `shinka/tools/gemini_usage.py` mirroring `codex_usage.py` structure
    2. Load auth from `~/.config/gemini/` or `~/.gemini/` credentials
    3. Load `projectId` from Gemini CLI config
    4. Call `retrieveUserQuota` with OAuth token
    5. Normalize response to match `UsageSnapshot` format
    6. Add `/api/gemini_usage` endpoint in `visualization.py`
    7. Update Agents tab to display Gemini quota alongside Codex quota
  - **Comparison Table - What Each CLI Provides:**
    | Feature | Codex | Claude | Gemini (current) | Gemini (new) |
    |---------|-------|--------|------------------|--------------|
    | Subscription/Plan Info | ✅ | ❌ | ❌ | ✅ Tier (free/standard) |
    | Token Counts | ❌ | ✅ Actual | ⚠️ Estimated | ✅ Actual |
    | Cost in USD | ❌ | ✅ | ❌ | ❌ |
    | Quota/Rate Limits | ✅ 5h + weekly | ❌ | ❌ | ✅ per-model |
  - Files to create/modify:
    - `shinka/tools/gemini_usage.py` (new - similar to codex_usage.py)
    - `tests/tools/test_gemini_usage.py` (new)
    - `shinka/webui/visualization.py` (add `/api/gemini_usage` endpoint)
    - `shinka/webui/viz_tree.html` (Agents tab - add Gemini quota display)
  - **Blocking on:** Gemini CLI v0.19.0 or later stable release
  - Validation:
    - `python -m shinka.tools.gemini_usage --human` shows plan and quota info
    - Agents tab displays Gemini usage alongside Codex usage
    - Unit tests pass for all auth modes
  - Added: 2025-11-29
  - Resolved: (blocked - waiting for stable Gemini CLI release)

- [ ] **TODO-206**: Request Claude Code team to add subscription/quota tracking
  - Context: Claude Code CLI provides excellent per-session cost data (`total_cost_usd`, actual token counts) but **no subscription-level quota tracking**. For parity with Codex and upcoming Gemini support, we should request Anthropic to add similar functionality.
  - **What Claude Provides Now:**
    - ✅ Per-session `total_cost_usd` in result event
    - ✅ Accurate `input_tokens`, `output_tokens` per message
    - ✅ Model name and session ID
    - ❌ No subscription plan info
    - ❌ No quota remaining/reset time
    - ❌ No account-level usage tracking
  - **What We'd Want (similar to Codex/Gemini):**
    - Plan type (Pro/Free/Enterprise)
    - Usage window remaining (hourly/daily/monthly limits)
    - Reset timestamp
    - Optional: per-model quota if applicable
  - **Action Items:**
    1. Submit feature request to Anthropic Claude Code team (user mentioned they've been in contact)
    2. Track issue/RFC if created
    3. Implement `claude_usage.py` when API becomes available
  - Files to create (when available):
    - `shinka/tools/claude_usage.py` (new)
    - `tests/tools/test_claude_usage.py` (new)
  - **Status:** Requested by user (2025-11-29) - awaiting Anthropic response
  - Added: 2025-11-29
  - Resolved: (waiting for Anthropic feature)

- [x] **TODO-207**: Add CLI version tracking and display in Agents tab
  - Context: Users need to know which version of each CLI they have installed.
  - **Implementation Plan:**
    1. **Backend**: `shinka/tools/cli_versions.py` implemented.
    2. **API**: `/api/cli_versions` endpoint added.
    3. **Frontend**: (Pending frontend display, but backend complete)
  - Added: 2025-11-29
  - Resolved: 2025-11-30 (Backend complete)

- [x] **TODO-208**: Add settings/info panel for agent cards in WebUI
  - Context: User requested a settings or info panel accessible from each agent card in the Agents tab. This would provide more detailed information about the agent backend and allow configuration without editing files.
  - **Proposed UX:**
    - Settings gear icon (⚙️) or info icon (ℹ️) on each agent card
    - Clicking opens a modal/panel with:
      1. **Status section**: Connection status, last used, session count
      2. **Version section**: CLI version, path, update available (links to TODO-207)
      3. **Usage section**: Quota/cost data (current implementation + TODO-205/206)
      4. **Configuration section**:
         - Model selection (if multiple models available)
         - Profile selection (Codex profiles, Gemini settings)
         - Sandbox mode toggle
         - Approval mode settings
      5. **Logs section**: Recent session logs, error history
      6. **Help section**: Links to CLI docs, troubleshooting
  - **Implementation approach:**
    1. Add collapsible settings panel within agent card (no separate modal)
    2. Fetch config from backend via new `/api/agent_config/{backend}` endpoint
    3. Allow inline editing of safe settings (model, profile)
    4. Show read-only info for system settings (version, path)
  - Files to modify:
    - `shinka/webui/viz_tree.html`:
      - Add settings button to agent card HTML
      - Add settings panel HTML structure
      - Add toggle/expand logic
      - Add fetch/save logic for config
    - `shinka/webui/visualization.py`:
      - Add `/api/agent_config/{backend}` GET endpoint
      - Add `/api/agent_config/{backend}` POST endpoint (for writable settings)
  - **Related TODOs:**
    - TODO-109: Unified API key management (keys could be shown/edited in this panel)
    - TODO-207: CLI version tracking (version info shown in this panel)
    - TODO-205/206: Quota tracking (quota info shown in this panel)
  - Validation:
    - Each agent card has settings icon
    - Clicking icon expands settings panel
    - Panel shows version, config, usage info
    - Safe settings are editable, changes persist
  - Added: 2025-11-29
  - Resolved: 2025-11-30 (Implemented CLI info button as MVP)

 

### Agentic Compatibility Analysis (2025-11-27)

**Summary of WebUI Analysis Features:**

| Feature | Tab Location | Agentic Compatible? | Notes |
|---------|--------------|---------------------|-------|
| **Tree View** | Left: Tree | ✅ Yes | Works for both modes - shows parent/child relationships |
| **Programs Table** | Left: Programs | ✅ Yes | Shows all programs with metadata; agentic nodes have `patch_type: "agentic"` |
| **Metrics** | Left: Metrics | ✅ Yes | Reads `combined_score`, `public_metrics` - works for both modes |
| **Embeddings** | Left: Embeddings | ⚠️ Partial | Uses single-file embedding (TODO-001) |
| **Clusters** | Left: Clusters | ⚠️ Partial | Depends on embeddings (TODO-001) |
| **Islands** | Left: Islands | ✅ Yes | Uses `island_idx` from database - works for both modes |
| **LLM Posterior** | Left: LLM Posterior | ❌ No | Requires `model_posteriors` from Thompson Sampling (TODO-101) |
| **Path → Best** | Left: Path → Best | ✅ Yes | Traces parent chain to best node - works for both modes |
| **Agents** | Left: Agents | ✅ Yes | Agentic-specific tab showing sessions, costs, telemetry |
| **Meta** | Right: Meta | ✅ Yes | Shows node metadata; agentic nodes have different fields but tab handles both |
| **Pareto Front** | Right: Pareto Front | ✅ Yes | Uses `public_metrics` for multi-objective view - works for both |
| **Scratchpad** | Right: Scratchpad | ❌ No | Requires meta_llm to work (TODO-002) |
| **Node** | Right: Node | ✅ Yes | Shows node details; handles both legacy and agentic metadata |
| **Code** | Right: Code | ✅ Yes | Shows `code_files` array - agentic nodes include helper files |
| **Diff** | Right: Diff | ✅ Yes | Shows `code_diffs` array - agentic nodes include multi-file diffs |
| **Evaluation** | Right: Evaluation | ✅ Yes | Shows evaluator output; handles both legacy and agentic evaluator |
| **LLM Result** | Right: LLM Result | ✅ Yes | Agentic nodes show timeline via `renderAgenticTimeline()` |

**Blocking Issues (P0):**
- TODO-001: Embeddings computed from main.py only → affects Embeddings/Clusters accuracy
- TODO-002: Scratchpad requires working meta_llm → conflicts with AGENTS.md policy

**Non-Blocking Issues (P1):**
- TODO-101: LLM Posterior empty for agentic (by design - no multi-model selection)
- TODO-102: Clusters affected by single-file embedding (dependent on TODO-001) 
- TODO-103: LLM Dynamic Selection not used in agentic (by design)
- TODO-104: Novelty LLM Judge uses legacy API (optional feature, rarely enabled)
- TODO-105: max_patch_attempts is legacy-only (agentic handles retries internally)

### Legacy vs Agentic Feature Matrix (2025-11-27)

**Deep Search Results - How features differ between modes:**

| Feature | Legacy Mode | Agentic Mode | Status |
|---------|-------------|--------------|--------|
| **Model Selection** | Thompson Sampling / UCB bandit | Fixed model per run | By design |
| **Patch Retry Loop** | `max_patch_attempts` with error feedback | Internal CLI multi-turn | By design |
| **Patch Types** | `diff` / `full` / `cross` strategies | Single `agentic` type | By design |
| **Message History** | `msg_history` for retry context | CLI maintains context | By design |
| **LLM Result** | `llm_result` with posteriors | `agent_metrics` with timeline | Equivalent telemetry |
| **Prompt Format** | DIFF/FULL/CROSS_SYS_FORMAT | CLI system prompt + rules | Equivalent guidance |
| **Cost Tracking** | `response.cost` from LLMClient | `estimated_total_cost` from CLI | Both work |
| **Novelty Embedding** | main.py only | main.py only | **TODO-001** |
| **Novelty LLM Check** | Direct API call | Direct API call | Same (rarely used) |
| **Meta/Scratchpad** | meta_llm_client | meta_llm_client | **TODO-002** |
| **Multi-file Editing** | Single file only | Full workspace | ✅ Agentic advantage |
| **Evaluation** | Deterministic script | Agentic or legacy | ✅ Flexible |
| **EVOLVE-BLOCK** | Enforced via patch parsing | Enforced via CLI prompt | Both work | 

### Open Questions / Decisions Needed

*(Items requiring user input or discussion)*

- [ ] **Q-001**: (question)
  - Context: 
  - Options: 
  - Decision: 
  - Date: 

## Surprises & Discoveries

- (2025-11-29) **Cost tracking parity analysis across CLI backends**: Investigated Codex, Claude, and Gemini CLI cost/usage tracking capabilities:
  - **Codex**: Best subscription-level tracking (plan type, 5h/weekly quota %, reset times) but NO token counts or USD cost
  - **Claude**: Best per-session cost tracking (actual token counts, `total_cost_usd`) but NO subscription/quota info
  - **Gemini (current)**: Worst - only estimated tokens (string length / 4), no cost, no quota
  - **Gemini (new, unreleased)**: Will have per-model quota tracking via `retrieveUserQuota` API, similar to Codex but with model-level granularity
  - Key insight: Each CLI prioritizes different cost dimensions. Full parity would require all three to expose subscription quotas AND per-session costs.

- (2025-11-29) **Gemini CLI quota feature is unreleased**: The `retrieveUserQuota` API was merged to Gemini CLI main branch (commit `69188c853`, Nov 26, 2025) but is NOT in any stable release. Latest stable is v0.18.3, and this feature is 74 commits ahead of it. Expected in v0.19.0 or v0.20.0.

- (2025-11-27) **Scratchpad feature is silently broken in agentic mode**: The meta-recommendation system depends on `meta_llm_models` being set to a working LLM, but the default config uses `gpt-4.1` which conflicts with the AGENTS.md policy restricting OpenAI to embeddings-only. This means the feature appears configured but never produces output. The WebUI "Scratchpad" tab will show empty content for agentic runs until this is resolved.

- (2025-11-27) **Legacy single-file mode unaffected**: The scratchpad feature likely works fine in legacy single-file mode if OpenAI API key has full permissions (not embeddings-only). The issue is specific to the repo policy in AGENTS.md.

- (2025-11-27) **Most WebUI analysis features work with agentic mode**: Out of 17 analysis features/tabs, 13 are fully compatible with agentic mode, 2 are partially compatible (depend on fixing embedding issue), and 2 are incompatible by design (LLM Posterior requires multi-model selection, Scratchpad requires meta_llm).

- (2025-11-27) **LLM Posterior tab is designed for legacy multi-model Thompson Sampling**: In agentic mode, there's no model selection happening - the Codex/Gemini model is fixed per run. The tab gracefully shows "no data" rather than crashing, so this is acceptable behavior.

- (2025-11-27) **Code and Diff tabs are already multi-file aware**: The `_build_code_files_payload()` and `_build_code_diffs_payload()` functions in `visualization.py` correctly extract `agent_changed_files` and `agent_code_diffs` from metadata and serve them to the WebUI. The tabs render file trees for navigation.

- (2025-11-27) **Deep codebase search findings - Legacy features that work differently or not at all in agentic mode:**
  
  1. **LLM Dynamic Selection (AsymmetricUCB bandit)**: Legacy samples from multiple models; agentic uses fixed model. **By design.**
  
  2. **Patch retry loop (`max_patch_attempts`)**: Legacy has outer retry loop with error feedback; agentic handles retries internally via CLI multi-turn. **By design.**
  
  3. **Novelty LLM Judge**: Optional LLM-based novelty check uses direct API (would conflict with AGENTS.md if configured). **Rarely used, low priority.**
  
  4. **Diff vs Full patch types**: Legacy has `diff`/`full`/`cross` patch strategies with different prompts; agentic uses single `patch_type="agentic"`. **By design.**
  
  5. **Message history (`msg_history`/`new_msg_history`)**: Legacy tracks conversation history for multi-attempt retry; agentic CLI maintains its own context. **By design.**
  
  6. **`llm_result` metadata field**: Legacy stores full LLMResponse with posteriors, tokens, cost breakdown; agentic stores `agent_metrics` with timeline, session_id, costs. **Different but equivalent telemetry.**
  
  7. **Prompt formats (DIFF_SYS_FORMAT, FULL_SYS_FORMAT, CROSS_SYS_FORMAT)**: Legacy uses these templates; agentic uses CLI system prompt + EVOLVE-BLOCK rules. **Different but equivalent guidance.**

- (2025-11-27) **Features that work correctly in both modes:**
  - Parent sampling (`sample_parent`, inspirations)
  - Crossover/archive inspirations (`num_archive_inspirations`, `num_top_k_inspirations`)
  - Multi-file workspace support (`init_support_dir`, `_hydrate_generation_directory`)
  - EVOLVE-BLOCK markers (respected by both legacy full-patch and agentic CLI)
  - Text feedback from evaluator (`text_feedback`)
  - Cost tracking (different fields but both capture API costs)
  - Island-based population management
  - Database schema and Program storage

## Decision Log

- (date/author) — (decision and rationale for how a todo was resolved)

## Outcomes & Retrospective

- (summarize lessons learned once todos are cleared and PR lands)

## Context and Orientation

This plan lives alongside:
- `PR_EXECPLAN.md`: The main PR gate criteria and milestones
- `REVIEW_EXECPLAN.md`: LLM-driven code review campaign

All three plans must be satisfied before merge:
1. `TODO_EXECPLAN.md` (this file): All P0/P1 todos resolved
2. `REVIEW_EXECPLAN.md`: All review areas passed
3. `PR_EXECPLAN.md`: All success criteria met with evidence

## Success Criteria & Validation

1. All P0 todos marked complete with evidence
2. All P1 todos marked complete with evidence (or explicitly deferred with rationale in Decision Log)
3. Summary of resolved todos copied into `PR_EXECPLAN.md` Artifacts section
4. No untracked blocking work remains

## Idempotence and Recovery

- Todos can be added, updated, or marked complete at any time
- If a todo is reopened (regression), add a new entry referencing the original
- Never delete completed todos; keep the history for retrospective

## Artifacts and Notes

- Link to relevant logs, screenshots, or test outputs as todos are resolved
- Reference specific commits when a todo is fixed

---

## Change Log

- (2025-11-27) Initial TODO_EXECPLAN created to track pre-PR work items per user request.
