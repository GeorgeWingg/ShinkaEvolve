# Backend Parity Cleanup (Bandit Jules + Store Fallbacks)

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

PLANS.md is checked into the repository root at `PLANS.md`. Maintain this ExecPlan in accordance with that file.

## Purpose / Big Picture

Shinka’s agentic backends are intended to be interchangeable under `AgenticEditor` and the backend bandit. Two parity defects remain:

1. The WebUI exposes a Jules checkbox in Backend Bandit configuration, but the value is silently ignored. Users think Jules is in the selection pool when it is not.
2. After the WebUI saves Gemini/Claude API keys into the unified encrypted store, `auth_status` (used by the backend bandit and status endpoints) does not consult that store. This creates confusing disagreement between WebUI validation (which checks the store) and bandit availability (which does not). Even if OAuth/subscription is the preferred path, store-backed keys should still behave consistently as a fallback.

After this work, bandit selections made in the UI truly include Jules when checked, and `auth_status` recognizes Gemini/Claude keys stored in `~/.shinka/credentials.json` as a best‑effort fallback. Codex remains OAuth‑only per repo policy.

You will know it’s working when:

1. Enabling bandit and leaving Jules checked results in `bandit.backends` containing `"jules"` in the posted run config.
2. Restarting the WebUI server with only Gemini/Claude keys in the unified store still marks those backends “available” in `/api/*_status` and for bandit selection.
3. No changes encourage OpenAI API‑key inference; Codex availability still requires CLI OAuth auth files.

## Progress

- [x] (2025-12-12 00:00Z) Drafted parity ExecPlan.
- [x] (2025-12-12 00:00Z) Fixed bandit UI to include Jules in backend pool and preset restore.
- [x] (2025-12-12 00:00Z) Added unified-store fallback to Gemini/Claude auth_status (OAuth remains primary).
- [x] (2025-12-12 00:00Z) Added regression tests for auth_status store fallback and Codex OAuth-only behavior.

## Surprises & Discoveries

- Observation: `getBanditConfig()` ignores the Jules checkbox even though it is rendered and default-checked.
  Evidence: `shinka/webui/viz_tree.html` only iterates `['codex','gemini','claude','shinka']`.
- Observation: `auth_status.check_gemini_auth` and `check_claude_auth` only look at env vars or CLI OAuth files, not the unified credential store.
  Evidence: `shinka/tools/auth_status.py`.

## Decision Log

- Decision: Keep Codex availability OAuth‑only; do not add unified-store fallback for Codex.
  Rationale: Repo policy states OpenAI API key is embeddings‑only; Codex CLI is the supported inference path.
  Date/Author: 2025-12-12 / Codex CLI agent.
- Decision: Add unified-store fallback only for Gemini/Claude (and only as fallback after OAuth detection).
  Rationale: Provides parity with WebUI credential persistence without changing the recommended OAuth/subscription flow.
  Date/Author: 2025-12-12 / Codex CLI agent.

## Outcomes & Retrospective

Outcome: The two highest-impact parity defects are resolved.

- Bandit Jules checkbox is now honored. The UI backend enumeration includes `"jules"` when collecting bandit backends and when restoring presets, so runs actually include Jules in the selection pool when you check it.
- Gemini and Claude availability checks now consult the unified credential store as a best‑effort fallback after OAuth/env checks fail. This keeps WebUI validation and bandit availability consistent while preserving your preferred OAuth/subscription path.
- Codex auth is unchanged and remains OAuth-only, aligned with the embeddings‑only OpenAI key policy.

Gaps left intentionally:

- We did not add store fallback to Codex, by design.
- Scratchpad backend UI still omits Jules; this is a separate product choice.

## Context and Orientation

Relevant files:

- `shinka/webui/viz_tree.html`: Single-page WebUI. The Backend Bandit checkboxes live here, and `getBanditConfig()` serializes them into the posted run config.
- `shinka/tools/auth_status.py`: Backend availability checks used by bandit selection and `/api/*_status` endpoints. Today it detects Gemini/Claude auth via OAuth files or env vars only.
- `shinka/tools/credentials.py`: Unified encrypted credential store. WebUI writes provider keys here via `/api/credentials`.

## Plan of Work

1. Fix bandit backend collection and restore:
   - In `getBanditConfig()`, include `"jules"` in the backend enumeration so its checkbox is honored.
   - In `setFormFromConfig()` when restoring presets, include `"jules"` in the list of checkboxes to set.

2. Add unified-store fallback to Gemini/Claude auth detection:
   - In `check_gemini_auth()`, after OAuth/env checks fail, read `get_api_key("gemini")` from the store. If present, treat Gemini as available and set `GEMINI_API_KEY` in `os.environ` for immediate use.
   - In `check_claude_auth()`, similarly fall back to `get_api_key("claude")` and set `ANTHROPIC_API_KEY` in env if found.
   - Do **not** add this fallback to Codex.

3. Tests/docs:
   - Add or extend unit tests (if present) to assert:
     - bandit config includes Jules when checked.
     - auth_status returns available for Gemini/Claude when store contains keys and OAuth files are absent.
   - Update any relevant docs/comments if needed.

## Concrete Steps

Run from repo root.

1. Edit `shinka/webui/viz_tree.html`:
   - Update `getBanditConfig()` list to include `jules`.
   - Update bandit preset restore loop to include `jules`.

2. Edit `shinka/tools/auth_status.py`:
   - Add store fallback blocks to `check_gemini_auth` and `check_claude_auth`.

3. Add/modify tests:
   - Locate existing WebUI/bandit tests; add minimal pytest cases if adjacent patterns exist.

4. Validate:
   - Run targeted pytest in a Py3.11/3.12 venv if available.
   - Manually open WebUI, enable bandit, leave Jules checked, click “Validate Run” and confirm errors mention Jules availability as expected.

## Success Criteria & Validation

- Criterion: Jules checkbox affects bandit backend pool.
  Validation: In WebUI, enable bandit, validate config; inspect request payload (DevTools) or server log to see `"jules"` in `bandit.backends`.

- Criterion: Gemini/Claude store keys are recognized by auth_status.
  Validation: With OAuth files removed/absent and no env vars set, place keys in `~/.shinka/credentials.json`, restart WebUI server, and call `/api/gemini_status` and `/api/claude_status`. Expect `authenticated: true`.

- Criterion: Codex remains OAuth-only.
  Validation: With only an OpenAI key in the store and no Codex OAuth file, `/api/codex_usage` or auth_status should still report not authenticated.

## Idempotence and Recovery

All changes are safe to apply repeatedly. If store fallback mis-detects availability in edge cases, users can clear the relevant store entry or set env vars explicitly to override.

## Artifacts and Notes

Keep any DevTools payload captures or pytest transcripts here as indented blocks when validating.
