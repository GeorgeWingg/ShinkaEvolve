# Pre-PR Validation ExecPlan

This ExecPlan provides rigorous end-to-end validation for ALL features marked complete in TODO_EXECPLAN. **Nothing gets checked off until proven working in a real evolution run.**

## Purpose / Big Picture

Before opening the PR, we must validate that:
1. All 4 backends (Codex, Gemini, Claude, ShinkaAgent) actually run mutations end-to-end
2. Thompson Sampling backend selection works and logs posteriors
3. Multi-file embedding detects novelty correctly
4. Real-time observability shows running agents
5. WebUI displays all new features correctly
6. No regressions in legacy mode

**Standard:** Rigour turned up to 11. Every validation requires:
- Running actual code (not just importing)
- Checking logs for evidence
- Verifying database state
- Screenshot/log proof when possible

## Progress

- [ ] (add timestamps as validations complete)

## Validation Tests

### V1 - Backend Integration (Codex, Gemini, Claude, ShinkaAgent)

**What we're testing:** All 4 agentic backends can run mutations and produce valid programs.

**Prerequisites:**
- Codex: `codex login` completed (may be out of compute)
- Gemini: `gemini --version` shows v0.18.4+, authenticated
- Claude: `claude --version` works, authenticated
- ShinkaAgent: Just needs LLM API key in `.env`

**Test V1.1: Codex Backend**
- [ ] Run: `uv run shinka_launch variant=circle_packing_example evolution=agentic +evo_config.agentic.backend=codex +num_generations=2 evo_config.max_parallel_jobs=1`
- [ ] **Validation:**
  - [ ] Gen 0 completes (score ~0.96)
  - [ ] Gen 1 mutation runs (Codex CLI launches)
  - [ ] Gen 1 program appears in database: `sqlite3 results/.../evolution_db.sqlite "SELECT generation, combined_score FROM programs"`
  - [ ] Files appear in `gen_1/main.py`
  - [ ] Session log exists: `ls results/.../gen_1/` should NOT contain `session_log.jsonl` (TODO-112)
  - [ ] Score changes (improvement OR regression is fine, just not stuck at 0.96)
- [ ] **Evidence:** Screenshot of `sqlite3` output showing gen 1 program

**Test V1.2: Gemini Backend**
- [ ] Run: `uv run shinka_launch variant=circle_packing_example evolution=agentic +evo_config.agentic.backend=gemini +num_generations=2 evo_config.max_parallel_jobs=1`
- [ ] **Validation:**
  - [ ] Gen 0 completes
  - [ ] Gen 1 mutation runs (Gemini CLI launches, visible in `ps aux | grep gemini`)
  - [ ] Gen 1 program appears in database
  - [ ] Files appear in `gen_1/main.py`
  - [ ] Session log NOT in gen_1 directory
  - [ ] Score changes
- [ ] **Evidence:** Log snippet showing Gemini CLI launched

**Test V1.3: Claude Backend**
- [ ] Run: `uv run shinka_launch variant=circle_packing_example evolution=agentic +evo_config.agentic.backend=claude +num_generations=2 evo_config.max_parallel_jobs=1`
- [ ] **Validation:**
  - [ ] Gen 0 completes
  - [ ] Gen 1 mutation runs (Claude CLI launches)
  - [ ] Gen 1 program appears in database
  - [ ] **CRITICAL:** Files appear in `gen_1/main.py` (tests TODO-110 file capture fix)
  - [ ] Session log NOT in gen_1 directory
  - [ ] Score changes
- [ ] **Evidence:** `ls -la results/.../gen_1/` showing main.py exists

**Test V1.4: ShinkaAgent Backend**
- [ ] Run: `uv run shinka_launch variant=circle_packing_example evolution=agentic +evo_config.agentic.backend=shinka +num_generations=2 evo_config.max_parallel_jobs=1`
- [ ] **Validation:**
  - [ ] Gen 0 completes
  - [ ] Gen 1 mutation runs (native agent, no external CLI)
  - [ ] Gen 1 program appears in database
  - [ ] Files appear in `gen_1/main.py`
  - [ ] Session log NOT in gen_1 directory
  - [ ] Score changes (should improve if using strong model like `gpt-4.1`)
- [ ] **Evidence:** Log showing ShinkaAgent ran (not Codex/Gemini/Claude)

---

### V2 - Thompson Sampling (Backend Bandit)

**What we're testing:** Backend selection via Thompson Sampling actually works and logs decisions.

**Test V2.1: Bandit Selection with Multiple Backends**
- [ ] Run: `uv run shinka_launch variant=circle_packing_example evolution=agentic_bandit +num_generations=5 evo_config.max_parallel_jobs=1`
- [ ] **Validation:**
  - [ ] Logs show "Backend bandit selected 'X'" messages for each generation
  - [ ] At least 2 different backends were selected across 5 generations
  - [ ] Logs show posteriors: `(posteriors: {'gemini': 0.X, 'claude': 0.Y})`
  - [ ] Database metadata contains `bandit_posteriors`:
    ```bash
    sqlite3 results/.../evolution_db.sqlite "SELECT metadata FROM programs WHERE generation=1" | jq '.bandit_posteriors'
    ```
  - [ ] Posteriors change over generations (bandit is learning)
- [ ] **Evidence:**
  - [ ] Log excerpt showing 2+ different backends selected
  - [ ] Screenshot of bandit posteriors evolving

**Test V2.2: Bandit Respects Auth Filtering**
- [ ] Temporarily sign out of Gemini: `rm ~/.gemini/auth.json` (or equivalent)
- [ ] Run bandit with `allowed_backends: [gemini, claude]`
- [ ] **Validation:**
  - [ ] Bandit only selects Claude (Gemini filtered out due to no auth)
  - [ ] Log shows "Backend bandit selected 'claude'" every time
  - [ ] No errors about missing Gemini auth
- [ ] **Restore:** Re-authenticate Gemini
- [ ] **Evidence:** Logs showing only Claude selected

---

### V3 - Multi-File Embedding & Novelty Detection

**What we're testing:** Novelty judge considers ALL files, not just main.py (TODO-001).

**Test V3.1: Single-File Baseline**
- [ ] Run: `uv run shinka_launch variant=circle_packing_example evolution=agentic +num_generations=3 evo_config.max_parallel_jobs=1`
- [ ] **Validation:**
  - [ ] Check database for embedding corpus metadata:
    ```bash
    sqlite3 results/.../evolution_db.sqlite "SELECT metadata FROM programs WHERE generation=1" | jq '.embedding_corpus_meta'
    ```
  - [ ] `included_files` should list `["main.py"]` for circle packing
  - [ ] `total_bytes` matches main.py size

**Test V3.2: Multi-File Novelty (if multi-file example exists)**
- [ ] If you have a multi-file example variant, run it
- [ ] **Validation:**
  - [ ] `included_files` lists multiple `.py` files
  - [ ] Embeddings change when helper files change (not just main.py)
  - [ ] Novelty rejection based on full corpus, not just main.py
- [ ] **Evidence:** Database query showing multi-file corpus

---

### V4 - Real-Time Observability (In-Progress Nodes)

**What we're testing:** UI shows live agent sessions while they're running (TODO-111).

**Test V4.1: Session Registry Tracking**
- [ ] Start: `uv run shinka_launch variant=circle_packing_example evolution=agentic +num_generations=2 evo_config.max_parallel_jobs=1 > /tmp/run.log 2>&1 &`
- [ ] While gen 1 is running (use `tail -f /tmp/run.log` to watch):
  - [ ] Check registry: `ls ~/.codex/shinka_sessions/` shows active PID files
  - [ ] Check: `cat ~/.codex/shinka_sessions/<PID>.json` shows session metadata
  - [ ] Verify PID matches running process: `ps aux | grep <PID>`
- [ ] After gen 1 completes:
  - [ ] Registry file removed: `ls ~/.codex/shinka_sessions/` should not have that PID
- [ ] **Evidence:** Session file contents during run

**Test V4.2: WebUI Active Jobs Display**
- [ ] Start visualizer: `uv run shinka_visualize results --port 8888 &`
- [ ] Start evolution with slow task: `uv run shinka_launch ...`
- [ ] Open `http://localhost:8888/viz_tree.html`
- [ ] **Validation:**
  - [ ] "In Progress" count in legend updates (shows 1 when agent running)
  - [ ] Click "In Progress" dropdown shows active session details
  - [ ] Time elapsed increases in real-time
  - [ ] When agent finishes, count drops to 0
- [ ] **Evidence:** Screenshot of In Progress section with active agent

---

### V5 - WebUI Features (LLM Posterior, Agents Tab, Meta Tab)

**What we're testing:** All new UI features render correctly.

**Test V5.1: LLM Posterior Tab for Bandit**
- [ ] Run bandit evolution (from V2.1)
- [ ] Open WebUI, select a gen ≥1 node
- [ ] Click "LLM Posterior" tab
- [ ] **Validation:**
  - [ ] Tab shows "Backend Bandit Status" section (not "No data")
  - [ ] Shows posteriors like `gemini: 45%`, `claude: 55%`
  - [ ] Posteriors sum to ~100%
- [ ] **Evidence:** Screenshot of LLM Posterior tab

**Test V5.2: Agents Tab - Backend Cards**
- [ ] Open WebUI Agents tab
- [ ] **Validation:**
  - [ ] Shows 4 backend cards: Codex, Gemini, Claude, ShinkaAgent
  - [ ] Each card shows:
    - [ ] Auth status (green checkmark or red X)
    - [ ] CLI version (if applicable)
    - [ ] "Info" button exists (TODO-208)
  - [ ] Click info button opens modal with version/config details
- [ ] **Evidence:** Screenshot of Agents tab

**Test V5.3: Meta Tab - Per-Node Metadata**
- [ ] Open WebUI, select a gen ≥1 agentic node
- [ ] Click "Meta" tab
- [ ] **Validation:**
  - [ ] Shows metadata for THAT NODE (not global stats)
  - [ ] Shows: backend, model, api_costs, patch_name, etc.
  - [ ] Select different node → Meta tab updates
- [ ] **Evidence:** Screenshot showing Meta tab updates per node

---

### V6 - Novelty LLM Judge (Agentic Mode)

**What we're testing:** Novelty judge uses CLI backends, not direct OpenAI API (TODO-104).

**Test V6.1: LLM Novelty Check via CLI**
- [ ] Enable novelty LLM judge in config:
  ```yaml
  evo_config:
    novelty_judge:
      use_llm_judge: true
      similarity_threshold: 0.85  # Trigger borderline cases
  ```
- [ ] Run evolution until novelty check triggers
- [ ] **Validation:**
  - [ ] Logs show "LLM novelty check" or similar
  - [ ] Check that it used agent_runner (Codex/Gemini), NOT legacy LLMClient
  - [ ] No errors about missing OpenAI API key
- [ ] **Evidence:** Log showing LLM judge ran via CLI

---

### V7 - Regression Testing (Legacy Mode Still Works)

**What we're testing:** None of the agentic changes broke legacy single-file mode.

**Test V7.1: Legacy Mode - Diff Patch**
- [ ] Run: `uv run shinka_launch variant=circle_packing_example +num_generations=2`
- [ ] **Validation:**
  - [ ] Gen 0 completes
  - [ ] Gen 1 uses legacy LLM query (not agentic)
  - [ ] Patch type is `diff` or `full`
  - [ ] Score changes
  - [ ] No references to Codex/Gemini CLI in logs
- [ ] **Evidence:** Log showing legacy mode ran

---

### V8 - Quality Bar (Code Health)

**What we're testing:** Code passes linting, tests, and formatting checks.

**Test V8.1: Pytest**
- [ ] Run: `uv run pytest tests/ -v`
- [ ] **Validation:**
  - [ ] All tests pass (0 failures)
  - [ ] New tests exist for:
    - [ ] `test_backend_bandit.py` (backend selection)
    - [ ] `test_embedding_corpus.py` (multi-file embedding)
    - [ ] `test_novelty_integration.py` (novelty with multi-file)
    - [ ] `test_agentic_bandit_extensions.py` (Thompson Sampling)
    - [ ] `test_e2e_backends.py` (Claude/Gemini/Codex integration)
- [ ] **Evidence:** Pytest summary showing X passed

**Test V8.2: Ruff (Linting)**
- [ ] Run: `uv run ruff check shinka tests`
- [ ] **Validation:**
  - [ ] 0 errors in new files:
    - [ ] `shinka/llm/backend_bandit.py`
    - [ ] `shinka/tools/credentials.py`
    - [ ] `shinka/core/embedding_corpus.py`
    - [ ] `shinka/edit/claude_cli.py`
    - [ ] `shinka/edit/gemini_cli.py`
    - [ ] `shinka/edit/shinka_agent.py`
  - [ ] Existing files have no NEW ruff errors

**Test V8.3: Black & Isort (Formatting)**
- [ ] Run: `uv run black --check shinka tests && uv run isort --check shinka tests`
- [ ] **Validation:**
  - [ ] All files formatted correctly (0 would reformat)

---

## Failure Criteria

If ANY of the following occur, **STOP and fix before PR**:
- Claude backend doesn't create files in gen directories (TODO-110 not actually fixed)
- Bandit always selects same backend (Thompson Sampling not working)
- Session logs appear in gen_N/ directories (TODO-112 not actually fixed)
- "In Progress" count stuck or wrong (TODO-111 not working)
- Tests fail (code broken)
- Legacy mode broken (regression)

## Success Criteria

**ALL validation checkboxes must be checked** before opening PR. No exceptions.

When complete, this document proves:
1. Every backend works end-to-end
2. Thompson Sampling selects and learns
3. Multi-file embedding detects novelty
4. Real-time observability shows agents
5. WebUI renders all features
6. No regressions
7. Code is clean (tests pass, linted, formatted)

---

## Notes

- Run validations on a clean results directory each time to avoid polluted state
- Save logs and screenshots in `docs/codex-evolve/validation-evidence/`
- If a validation fails, document the failure and link to fix in TODO_EXECPLAN
- Estimated time: 4-6 hours of rigorous testing
