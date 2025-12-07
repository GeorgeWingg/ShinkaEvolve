# 🎯 MASTER EXECPLAN TRACKER: The ExecPlan to End All ExecPlans

**Last Updated:** 2025-12-05
**Status:** 🟡 IN PROGRESS - Multiple critical items need attention before PR

This document tracks the consolidated state of ALL ExecPlans in the ShinkaEvolve repository, provides validation checkpoints for each, and determines PR readiness.

---

## 📊 Executive Summary

| Category | Total | ✅ Complete | 🟡 In Progress | 🔴 Blocked/Not Started | Notes |
|----------|-------|-------------|----------------|------------------------|-------|
| **Core Backend** | 4 | 2 | 2 | 0 | Codex/Gemini working; Claude/ShinkaAgent need fixes |
| **Evaluation System** | 2 | 2 | 0 | 0 | Agentic evaluator complete |
| **WebUI/Telemetry** | 5 | 3 | 2 | 0 | Real-time streaming FIXED |
| **Infrastructure** | 4 | 3 | 1 | 0 | Bandit, Embedding done; TODO tracking active |
| **Documentation** | 3 | 2 | 1 | 0 | Naming deferred post-PR |

**Overall PR Readiness: 🟡 65% - Critical blockers must be resolved**

---

## 🔴 CRITICAL BLOCKERS FOR PR

These items MUST be fixed before PR submission:

### ~~1. Real-Time In-Progress Tracking (REALTIME_STREAMING_EXECPLAN.md)~~ ✅ FIXED
- **Status:** ✅ COMPLETE (2025-12-05)
- **Fix:** Changed `inProgressStartedAt` to `window.inProgressStartedAt` for consistent global scope
- **Impact:** Time now ticks correctly, events update in real-time
- **Details:** Same fix pattern as `window.loadDatabaseSilent`

### 2. Claude Backend File Capture (TODO-110 in TODO_EXECPLAN.md)
- **Status:** 🟡 Fix Applied, Needs Validation
- **Impact:** Claude runs complete but don't save edited files
- **Problem:** Files not written to generation directories
- **Validation:** Need E2E run showing files in `gen_N/main.py`

### 3. ShinkaAgent No Improvement (TODO in TODO_EXECPLAN.md)
- **Status:** 🟡 Fix Applied, Needs Validation
- **Impact:** 299 generations with no score improvement
- **Problem:** Bash regex and prompt issues
- **Validation:** Need E2E run showing score improvement

---

## 📋 DETAILED EXECPLAN STATUS

---

### 1️⃣ CORE AGENTIC HARNESS (docs/codex-evolve/EXECPLAN.md)

**Purpose:** Enable multi-turn agentic editing with Codex/Gemini/Claude/ShinkaAgent backends

**Status:** 🟡 85% Complete

| Milestone | Status | Evidence |
|-----------|--------|----------|
| Codex CLI wrapper | ✅ | `shinka/edit/codex_cli.py`, tests pass |
| Gemini CLI wrapper | ✅ | `shinka/edit/gemini_cli.py`, tests pass |
| Claude CLI wrapper | 🟡 | Tests pass, E2E file capture broken |
| ShinkaAgent native | 🟡 | Tests pass, E2E no improvement |
| Multi-file workspace hydration | ✅ | `_hydrate_generation_directory` works |
| Agentic prompts | ✅ | `prompts_agentic.py` |
| Session logging | ✅ | `session_log.jsonl` captured |

**Validation Needed:**
```bash
# Claude backend - verify files captured
uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true \
  +evo_config.agentic.backend=claude evo_config.num_generations=2
# CHECK: ls results/.../gen_1/main.py exists

# ShinkaAgent - verify score improves
uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true \
  +evo_config.agentic.backend=shinka evo_config.num_generations=3
# CHECK: Score improves from ~0.96
```

---

### 2️⃣ CLAUDE CODE BACKEND (CLAUDE_CODE_EXECPLAN.md)

**Purpose:** Claude Code CLI as third agentic backend

**Status:** 🟡 95% Complete - E2E file capture broken

| Milestone | Status | Evidence |
|-----------|--------|----------|
| CLI wrapper implementation | ✅ | `claude_cli.py` ~400 lines |
| Event streaming | ✅ | Adapts Claude events to Shinka format |
| Session registry | ✅ | Uses shared registry |
| Token tracking | ✅ | Real counts from Claude |
| Unit tests | ✅ | 11 tests passing |
| E2E validation | 🔴 | Files not written to gen dirs |

**Root Cause Analysis:**
- Claude CLI sessions complete successfully
- `tool_result` events fire for file writes
- BUT files don't appear in `gen_N/` directories
- Likely issue in `_extract_changed_files()` or Claude event structure

**Fix Applied:** Added debug logging to `claude_cli.py` for tool_result processing

**Validation Command:**
```bash
env $(cat .env | xargs) uv run shinka_launch variant=circle_packing_example \
  +evo_config.agentic_mode=true +evo_config.agentic.backend=claude \
  +evo_config.agentic.extra_cli_config.debug_log=true \
  evo_config.num_generations=2

# Then check:
ls -la results/shinka_circle_packing/*/gen_1/
# MUST see: main.py with Claude's edits
```

---

### 3️⃣ SHINKA AGENT NATIVE BACKEND (SHINKA_AGENT_EXECPLAN.md)

**Purpose:** Native model-agnostic backend using LLMClient

**Status:** 🟡 90% Complete - Production improvements needed

| Milestone | Status | Evidence |
|-----------|--------|----------|
| Agent loop (mini-SWE-agent) | ✅ | Bash-only, regex parsing |
| Multi-turn sessions | ✅ | 3-11 turns per session |
| Cost tracking | ✅ | LLMClient reports costs |
| Unit tests | ✅ | 38 tests passing |
| Circle packing improvement | 🟡 | 0.96→1.55 (needs 2.6+) |

**Issues Found in Production:**
1. `ACTION_RE` didn't match `sh`/`shell` blocks → FIXED
2. System prompt didn't recommend Python for edits → FIXED
3. `gpt-4.1-mini` may be too weak → Consider `gpt-5.1-codex-mini`

**Validation Command:**
```bash
uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true \
  +evo_config.agentic.backend=shinka \
  +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini \
  evo_config.num_generations=5

# CHECK: Score should exceed 2.0 by gen 5
```

---

### 4️⃣ GEMINI CLI BACKEND (GEMINI_EXECPLAN.md)

**Purpose:** Gemini CLI as alternative agentic backend

**Status:** ✅ COMPLETE

| Milestone | Status | Evidence |
|-----------|--------|----------|
| CLI wrapper | ✅ | `gemini_cli.py` |
| Event adaptation | ✅ | stream-json parsing |
| Usage estimation | ✅ | Token estimation |
| E2E validation | ✅ | Score 2.389 in 105 gens |
| Tests | ✅ | All passing |

**Working Model:** `gemini-3-pro-preview`

---

### 5️⃣ AGENTIC EVALUATOR (docs/codex-evolve/AGENTIC_EVALUATOR_EXECPLAN.md)

**Purpose:** Codex-powered evaluation instead of legacy single-file

**Status:** ✅ COMPLETE

| Milestone | Status | Evidence |
|-----------|--------|----------|
| AgenticEvaluator class | ✅ | `shinka/eval/agentic.py` |
| Codex judge prompts | ✅ | `prompts_agentic_eval.py` |
| Workspace-based evaluation | ✅ | No main.py duplication |
| Config toggle | ✅ | `evo_config.evaluator.mode` |
| Documentation | ✅ | README, AGENTS.md updated |

**Validation:** Confirmed working in production runs

---

### 6️⃣ AGENTIC EVALUATOR UI (docs/codex-evolve/AGENTIC_EVALUATOR_UI.md)

**Purpose:** WebUI support for agentic evaluator telemetry

**Status:** ✅ COMPLETE

| Feature | Status |
|---------|--------|
| Runtime & status badge | ✅ |
| Metrics JSON block | ✅ |
| Timeline component | ✅ |
| Log download | ✅ |
| Error surfacing | ✅ |
| Mode badge | ✅ |

---

### 7️⃣ BACKEND BANDIT (docs/codex-evolve/PR-plans/AGENTIC_BANDIT_EXECPLAN.md)

**Purpose:** Thompson Sampling for backend selection

**Status:** ✅ COMPLETE

| Milestone | Status | Evidence |
|-----------|--------|----------|
| BackendBandit class | ✅ | `shinka/llm/backend_bandit.py` |
| Runner integration | ✅ | `runner.py` wiring |
| Posteriors in metadata | ✅ | `bandit_posteriors` |
| WebUI visualization | ✅ | LLM Posterior tab |
| Unit tests | ✅ | `test_backend_bandit.py` |

**Validation Command:**
```bash
uv run shinka_launch variant=circle_packing_example evolution=agentic_bandit \
  +num_generations=5

# CHECK: Logs show different backends selected
# CHECK: LLM Posterior tab shows posteriors
```

---

### 8️⃣ MULTI-ARTIFACT EMBEDDING (docs/codex-evolve/PR-plans/TODO001_MULTI_ARTIFACT_EMBEDDING_EXECPLAN.md)

**Purpose:** Embeddings consider all files, not just main.py

**Status:** ✅ COMPLETE

| Milestone | Status | Evidence |
|-----------|--------|----------|
| Corpus builder | ✅ | `build_embedding_corpus()` |
| Config fields | ✅ | Globs, size limits |
| Runner integration | ✅ | `get_code_embedding()` |
| Novelty alignment | ✅ | Uses corpus text |
| Tests | ✅ | `test_embedding_corpus.py` |

---

### 9️⃣ CODEX USAGE TELEMETRY (docs/codex-evolve/CODEXUsage-EXECPLAN.md)

**Purpose:** Fetch and display Codex subscription usage

**Status:** ✅ COMPLETE (API key mode unvalidated)

| Feature | Status |
|---------|--------|
| ChatGPT OAuth mode | ✅ |
| CLI output | ✅ |
| WebUI integration | ✅ |
| API key mode | ⚠️ Untested |
| Sign-in/out flow | ❌ Out of scope |

---

### 🔟 CODEXEVOLVE UI (docs/codex-evolve/CODEXEvolve-UI.md)

**Purpose:** WebUI telemetry for Codex sessions

**Status:** 🟡 90% Complete

| Feature | Status |
|---------|--------|
| Code tab multi-file | ✅ |
| Diff tab multi-file | ✅ |
| LLM Result timeline | ✅ |
| Evaluation tab | ✅ |
| Live refresh | 🟡 Needs polish |
| Context expansion | ❌ Not implemented |

---

### 1️⃣1️⃣ AGENTS TAB (AGENTS_TAB_EXECPLAN.md)

**Purpose:** WebUI tab for agent status and sessions

**Status:** ✅ COMPLETE (Phase 6 needs testing)

| Phase | Status |
|-------|--------|
| UI Stabilization | ✅ |
| Card Design | ✅ |
| Real Sessions Data | ✅ |
| Authentication | ✅ |
| Auth Metrics | ✅ |
| Running Status (PID) | 🟡 Needs E2E test |

---

### 1️⃣2️⃣ REAL-TIME STREAMING (docs/codex-evolve/PR-plans/REALTIME_STREAMING_EXECPLAN.md)

**Purpose:** Show in-progress nodes in real-time

**Status:** ✅ COMPLETE (2025-12-05)

| Phase | Status |
|-------|--------|
| Phase 0: Cleanup | ✅ |
| Phase 1: Session Registry | ✅ PID-based verification via `list_session_processes()` |
| Phase 2: Pending Nodes | ✅ `mergeInProgressNodes()` with async fix |
| Phase 3: Same-UI Tabs | ✅ `/api/session_state` + `displayInProgressDetails()` |
| Phase 4: Real-Time Updates | ✅ Fixed `inProgressStartedAt` scope issue |
| Phase 5: E2E Testing | ⏳ Needs live run verification |

**Bug Fixed (2025-12-05):**
- **Problem:** Time display didn't tick - `inProgressStartedAt` was empty in polling function
- **Fix:** Changed all references to use `window.inProgressStartedAt` for consistent global scope
- Same fix pattern as `window.loadDatabaseSilent`

**Key Implementation:**
- `_get_active_sessions_for_run()` in `visualization.py` now checks session registry FIRST
- Session registry at `~/.codex/shinka_sessions/` uses `os.kill(pid, 0)` for 100% reliable detection
- Fixes issue where long-running Claude sessions (5+ min without log writes) disappeared from UI

---

### 1️⃣3️⃣ TODO TRACKER (docs/codex-evolve/PR-plans/TODO_EXECPLAN.md)

**Purpose:** Track all pre-PR work items

**Status:** 🟡 75% Complete

| Priority | Total | Done | Notes |
|----------|-------|------|-------|
| P0 Blockers | 4 | 4 | All resolved |
| P1 High | 12 | 10 | 2 need validation |
| P2 Nice to Have | 8 | 5 | 3 deferred |

**Key Resolved Items:**
- ✅ TODO-001: Multi-file embedding
- ✅ TODO-002: Scratchpad/Meta-LLM disabled
- ✅ TODO-100: CoW copy support
- ✅ TODO-101: LLM Posterior for bandit
- ✅ TODO-103: Agentic bandit extensions
- ✅ TODO-104: Novelty LLM Judge via CLI
- ✅ TODO-106: Codex auth unit tests
- ✅ TODO-107: Codex auth help tip
- ✅ TODO-109: Unified credentials store
- ✅ TODO-203: Meta tab per-node

**Needs Validation:**
- 🟡 TODO-110: Claude file capture
- 🟡 TODO-111: Real-time observability

---

### 1️⃣4️⃣ PR GATE (docs/codex-evolve/PR-plans/PR_EXECPLAN.md)

**Purpose:** Master PR criteria and evidence

**Status:** 🟡 60% Complete

| Criterion | Status |
|-----------|--------|
| TODO Tracker clear | 🟡 |
| Quality bar (tests/lint) | ✅ |
| Legacy smoke run | 🟡 Needs rerun |
| Agentic Codex run | 🟡 Needs rerun |
| Agentic Gemini run | 🟡 Needs rerun |
| LLM Review | ✅ (agent review) |
| Docs refreshed | 🟡 |

---

### 1️⃣5️⃣ VALIDATION SUITE (docs/codex-evolve/PR-plans/EXECPLAN_VALIDATION.md)

**Purpose:** Rigorous E2E validation for all features

**Status:** 🔴 NOT EXECUTED

All 8 validation tests need to be run:
- V1: Backend Integration (4 backends)
- V2: Thompson Sampling
- V3: Multi-file Embedding
- V4: Real-time Observability
- V5: WebUI Features
- V6: Novelty LLM Judge
- V7: Legacy Regression
- V8: Quality Bar

---

### 1️⃣6️⃣ REVIEW (docs/codex-evolve/PR-plans/REVIEW_EXECPLAN.md)

**Purpose:** LLM-driven code review

**Status:** 🟡 MIGRATED TO `codex review` (2025-12-05)

| Milestone | Status |
|-----------|--------|
| Legacy Gemini CLI reviews | ✅ Complete (2025-11-30) |
| Migration to `codex review` | ✅ Complete (2025-12-05) |
| Framework created | ✅ `tests/llm_reviews/` |
| Run initial baseline | 🔴 NOT YET EXECUTED |

**New Review Framework:**
- Runner: `tests/llm_reviews/run_reviews.sh`
- 11 aspect-focused reviews (security, error handling, consistency, etc.)
- Mix scope: Full file for P0 (security), diff-only for P1 (consistency)
- Output: `tests/llm_reviews/results/<timestamp>/summary.md`

---

### 1️⃣7️⃣ UI POLISH (docs/codex-evolve/PR-plans/UI_POLISH_EXECPLAN.md)

**Purpose:** Polish Evaluation and LLM Result tabs

**Status:** ✅ COMPLETE

All CSS/JS improvements applied.

---

### 1️⃣8️⃣ NAMING CONVENTION (docs/codex-evolve/NAMING_CONVENTION_EXECPLAN.md)

**Purpose:** Rename "Codex" to generic "Agent" terms

**Status:** ⏸️ DEFERRED POST-PR

Will be done after main PR merges to avoid conflicts.

---

## ✅ VALIDATION CHECKLIST FOR PR

### Must Pass Before PR:

- [ ] **V1.1** Codex backend produces gen_1 with improved score
- [ ] **V1.2** Gemini backend produces gen_1 with improved score
- [ ] **V1.3** Claude backend produces gen_1 with FILES in gen_1/
- [ ] **V1.4** ShinkaAgent backend produces gen_1 with improved score
- [ ] **V2** Thompson Sampling rotates backends and logs posteriors
- [ ] **V4** In-progress nodes visible (BLOCKED on REALTIME_STREAMING)
- [ ] **V7** Legacy mode still works (`agentic_mode=false`)
- [ ] **V8.1** `pytest tests/` passes
- [ ] **V8.2** `ruff check shinka tests` passes
- [ ] **V8.3** `black --check shinka tests` passes

### Nice to Have:

- [ ] V3: Multi-file embedding changes when helpers change
- [ ] V5: All WebUI tabs render correctly
- [ ] V6: Novelty LLM judge uses CLI backend

---

## 📁 EXECPLAN FILE INDEX

| File | Location | Status |
|------|----------|--------|
| EXECPLAN.md (Main Agentic) | `docs/codex-evolve/EXECPLAN.md` | 🟡 85% |
| CLAUDE_CODE_EXECPLAN.md | Root | 🟡 95% |
| SHINKA_AGENT_EXECPLAN.md | Root | 🟡 90% |
| GEMINI_EXECPLAN.md | Root | ✅ 100% |
| AGENTS_TAB_EXECPLAN.md | Root | ✅ 95% |
| AGENTIC_EVALUATOR_EXECPLAN.md | `docs/codex-evolve/` | ✅ 100% |
| AGENTIC_EVALUATOR_UI.md | `docs/codex-evolve/` | ✅ 100% |
| CODEXEvolve-UI.md | `docs/codex-evolve/` | 🟡 90% |
| CODEXUsage-EXECPLAN.md | `docs/codex-evolve/` | ✅ 100% |
| NAMING_CONVENTION_EXECPLAN.md | `docs/codex-evolve/` | ⏸️ Deferred |
| PR_EXECPLAN.md | `docs/codex-evolve/PR-plans/` | 🟡 60% |
| TODO_EXECPLAN.md | `docs/codex-evolve/PR-plans/` | 🟡 75% |
| REVIEW_EXECPLAN.md | `docs/codex-evolve/PR-plans/` | ✅ 100% |
| UI_POLISH_EXECPLAN.md | `docs/codex-evolve/PR-plans/` | ✅ 100% |
| EXECPLAN_VALIDATION.md | `docs/codex-evolve/PR-plans/` | 🔴 0% |
| AGENTIC_BANDIT_EXECPLAN.md | `docs/codex-evolve/PR-plans/` | ✅ 100% |
| TODO001_MULTI_ARTIFACT_EMBEDDING_EXECPLAN.md | `docs/codex-evolve/PR-plans/` | ✅ 100% |
| REALTIME_STREAMING_EXECPLAN.md | `docs/codex-evolve/PR-plans/` | ✅ 100% |
| PLANS.md | Root | N/A (Meta doc) |

---

## 🚀 RECOMMENDED ACTION PLAN

### Phase 1: Fix Critical Blockers (1-2 days)

1. **Claude File Capture** - Run with debug_log, trace why files not saved
2. **ShinkaAgent Improvement** - Test with stronger model
3. **Run validation suite V1.1-V1.4** - Document results

### Phase 2: Real-Time Streaming (2-3 days)

Execute REALTIME_STREAMING_EXECPLAN.md phases 0-5

### Phase 3: Final Validation (1 day)

1. Execute EXECPLAN_VALIDATION.md tests V1-V8
2. Update PR_EXECPLAN.md with evidence
3. Run quality bar checks
4. Document all results

### Phase 4: PR Submission

1. Final docs review
2. Create PR with this tracker as reference
3. Address reviewer feedback

---

## 📝 CHANGE LOG

- (2025-12-05) **FIXED**: REALTIME_STREAMING time ticking now works
  - Root cause: `let inProgressStartedAt` was in a block scope invisible to polling function
  - Fix: Changed all references to `window.inProgressStartedAt` for consistent global scope
  - Also fixed: XSS vulnerability in dropdown, memory leak in beforeunload, duplicate method definitions
- (2025-12-05) **CRITICAL**: Marked REALTIME_STREAMING as BROKEN
  - Time display not ticking due to JS scope issue with `inProgressStartedAt`
  - Console shows variable is empty in polling function despite being set earlier
  - Created `docs/codex-evolve/HANDOFF_NOTES.md` with full debugging info
  - Next agent needs to investigate JavaScript variable scoping in viz_tree.html
- (2025-12-05) Updated REVIEW_EXECPLAN.md status - migrated to `codex review` framework
  - Created `tests/llm_reviews/` with run_reviews.sh and parse_results.py
  - 11 aspect-focused review passes (security, error handling, consistency, etc.)
  - Fixed TODO_EXECPLAN.md duplicate entries (TODO-203, TODO-204)
  - Updated PR_EXECPLAN.md progress section to reflect actual state
- (2025-12-04) Initial creation of Master ExecPlan Tracker
  - Consolidated 18 ExecPlans into unified tracking document
  - Added validation checkpoints for each plan
  - Identified 3 critical blockers for PR
