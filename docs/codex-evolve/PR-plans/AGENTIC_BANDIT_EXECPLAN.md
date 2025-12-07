# Agentic Bandit ExecPlan

This plan tracks the implementation of Multi-Model/Multi-Backend Bandit selection for Agentic Mode (TODO-103).

## Purpose
Enable dynamic selection between different agent backends (Codex, Gemini, Claude, ShinkaAgent) and models (e.g., gpt-5.1 vs gpt-4.1) using a multi-armed bandit algorithm. This allows the system to learn which backend/model performs best for the current task and shift resources accordingly.

## Goals
1. **Multi-Harness Support**: Dynamically switch backends.
2. **Multi-Model Support**: Dynamically switch models within backends.
3. **Telemetry**: Store bandit probabilities (posteriors) in node metadata.
4. **Visualization**: Display agentic bandit probabilities in the WebUI "LLM Posterior" tab.

## Current State (2025-11-30)
- `BackendBandit` class exists in `shinka/llm/backend_bandit.py`.
- `runner.py` has initial integration in `__init__`, `_run_agentic_patch`, and `_finalize_job`.
- WebUI does not currently display bandit data for agentic nodes (it looks for legacy `llm_result.model_posteriors`).

## Plan

### Phase 1: Verification & Backend Fixes
- [x] Verify `BackendBandit` correctly handles `arms` config (e.g., `["codex:gpt-5", "gemini:flash"]`) (Verified in `backend_bandit.py`).
- [x] Ensure `runner.py` passes the correct reward signal (combined_score) to `backend_bandit.update()` (Verified in `runner.py`).
- [x] Ensure `runner.py` stores bandit summary in a consistent metadata field (currently `bandit_posteriors`) (Verified in `runner.py`).

### Phase 2: WebUI Integration
- [x] Modify `viz_tree.html` to read `metadata.bandit_posteriors` if present.
- [x] Added `renderBanditChart` helper to visualize posteriors in the LLM Result tab.
- [x] Ensure the chart correctly labels arms (e.g., "codex:gpt-5").

### Phase 3: Testing
- [ ] Create unit test for `BackendBandit` configuration and sampling.
- [ ] Create integration test ensuring `runner.py` rotates backends when bandit is enabled.

## Implementation Details

### Metadata Schema
Agentic nodes will store:
```json
"metadata": {
  "agent_backend": "codex",
  "agent_model": "gpt-5.1-codex-mini",
  "bandit_posteriors": {
    "available_backends": ["codex:gpt-5.1", "gemini:flash"],
    "posteriors": {
      "codex:gpt-5.1": 0.8,
      "gemini:flash": 0.2
    },
    "per_backend_stats": { ... }
  }
}
```

### WebUI Changes
- The "LLM Posterior" tab currently expects `data.metadata.llm_result.model_posteriors`.
- We need to adapt it to look for `data.metadata.bandit_posteriors`.

## Verification
- [ ] Run a short evolution with `+evo_config.agentic.bandit_selection=true` (e.g., `uv run shinka_launch variant=circle_packing_example +evo_config.agentic_mode=true +evo_config.agentic.bandit_selection=true`).
- [ ] Verify different backends are used.
- [ ] Verify "LLM Posterior" tab shows data.
