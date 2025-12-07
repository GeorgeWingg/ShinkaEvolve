# Naming Convention ExecPlan

This ExecPlan tracks the renaming of "Codex" artifacts to "Agent" or generic terms to reflect the multi-backend nature of ShinkaEvolve.

## Purpose
ShinkaEvolve now supports Codex, Gemini, Claude, and ShinkaAgent. The codebase and UI still use "Codex" terminology heavily (e.g., `codex-usage-view`, `codex-session-row`, `codex_cli.py`). This plan outlines the systematic renaming to neutral terms like "Agent", "Agentic", or "Backend".

## Scope
- **Files to Rename**: `codex_cli.py` -> `openai_cli.py` (or keep as legacy reference but introduce `agent_cli.py` interface).
- **UI Elements**: Rename `codex-usage-view` to `agents-view`, `codex-card` to `agent-card`.
- **Config**: Deprecate `codex_path`, `codex_profile` in favor of `cli_path`, `cli_profile`.
- **Docs**: Update documentation to refer to "Agentic Mode" instead of "Codex Mode".

## Status
- [x] Draft Plan
- [x] Identify all "Codex" references (Completed during development)
- [ ] Rename UI IDs and Classes (Deferred to post-PR cleanup)
- [ ] Rename Python modules (Deferred to post-PR cleanup)
- [ ] Update Configs (Deferred to post-PR cleanup)
- [ ] Update Docs (Deferred to post-PR cleanup)

## Notes
This plan tracks future refactoring work. The definition phase is complete. Execution is deferred to after the main functional PR is merged to avoid massive merge conflicts.
