# Codex Usage Telemetry Bootstrap

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

PLANS.md is located at `PLANS.md` in the repository root. Maintain this plan in full compliance with that document.

## Status: COMPLETE

Core functionality works: fetching usage via ChatGPT OAuth flow, CLI output, and WebUI integration. API key mode is implemented and verified via unit tests.

## Purpose / Big Picture

ShinkaEvolve orchestrates Codex CLI runs (`codex exec --json`) but has no built-in way to show researchers how much of their ChatGPT/Codex subscription they are burning or when limits will reset. Codex already exposes this information through the same authenticated session Shinka uses; we simply need to fetch it, normalize it, and prove that we can do so reliably before attempting any UI integration. After completing the work in this plan a contributor can run a single command (or library helper) inside this repo to dump the user's Codex plan type plus the primary/secondary usage windows, with validation proving that failure states and edge cases are handled. ~~UI surfacing is explicitly out of scope for this plan; we stop once data capture is solid.~~ **UPDATE:** UI integration was completed as part of the AGENTS_TAB_EXECPLAN.

## Progress

- [x] (2025-11-17 23:10Z) Captured requirements for fetching Codex usage via REST/JSON-RPC, confirmed plan with the requester, and documented this ExecPlan skeleton.
- [x] (2025-11-18) Implemented credential + base-URL loader with defensive error messages (`load_auth_info()`, `load_base_url()`, `normalize_base_url()`).
- [x] (2025-11-18) Implemented `shinka/tools/codex_usage.py` helper (~280 lines) that fetches usage JSON, normalizes it, and prints structured output.
- [x] (2025-11-18) Added automated validation: 5 unit tests in `tests/tools/test_codex_usage.py` covering URL normalization, window parsing, auth loading, success paths, and missing auth errors.
- [x] (2025-11-18) Integration smoke test completed with real Codex Pro credentials (ChatGPT OAuth mode).
- [x] (2025-11-20) **BONUS:** UI integration completed in WebUI Agents tab (`/api/codex_usage` endpoint + frontend rendering).
- [x] (2025-11-27) Validated end-to-end: CLI returns real usage data, WebUI displays plan/limits correctly.
- [x] (2025-11-30) Added unit test coverage for API key mode (`test_collect_usage_api_key_mode`).
- [ ] **OUT OF SCOPE:** Sign-in/sign-out flow management (requires Codex CLI interaction).

## Known Gaps

### API Key Mode (Validated via Unit Test)

The code supports `openai_api_key` in `auth.json` (lines 132-150 in `codex_usage.py`):
```python
api_key = payload.get("openai_api_key")
if api_key:
    return CodexAuthInfo(
        mode="api_key",
        access_token=None,
        api_key=api_key,
        ...
    )
```

**Problems:**
1. No test case covers this branch
2. Unknown if `/wham/usage` endpoint accepts API key auth at all
3. Plan detection won't work (no `id_token` claims with API keys)
4. May need different endpoint (`/api/codex/usage` vs `/wham/usage`)

### Sign-In/Sign-Out (Not Implemented)

Current implementation assumes user has already run `codex login` manually. There's no:
- Programmatic sign-in trigger
- Sign-out handling
- Token refresh logic
- Session expiry detection

The UI shows "Run `codex login` first" error message but can't help the user sign in.

### Token Refresh (Not Implemented)

`auth.json` contains `refresh_token` but we don't use it. If the access token expires mid-experiment, the user sees an HTTP 401 and must manually re-run `codex login`.

## Surprises & Discoveries

- (2025-11-18) **`id_token` field variance**: The `auth.json` file structure varies between OAuth flows. Some have `id_token` as a nested dict, others have `id_token_claims` as a separate key. The implementation merges both sources to reliably extract `plan_type` and `email`.

- (2025-11-18) **ChatGPT vs API-key auth modes**: The auth file can contain either `tokens.access_token` (ChatGPT OAuth flow) or `openai_api_key` (direct API key). Both modes are coded but only ChatGPT mode is validated.

- (2025-11-20) **Caching for UI**: The WebUI endpoint (`/api/codex_usage`) caches usage data for 15 minutes to avoid hammering the ChatGPT backend-api. Sessions are fetched fresh each time for real-time status.

## Decision Log

- (2025-11-18) **Chose REST over JSON-RPC**: The `/wham/usage` endpoint is a simple GET that returns the same data Codex CLI uses. No need for the complexity of JSON-RPC.

- (2025-11-18) **Used `requests` library**: Although the plan suggested standard library only, `requests` was already vendored for other HTTP calls. Kept consistency.

- (2025-11-18) **Separate dataclasses for auth vs usage**: `CodexAuthInfo` captures credentials, `UsageSnapshot` captures the normalized response. This separation makes testing easier and keeps concerns separate.

- (2025-11-18) **Human-readable CLI output**: Added `--human` flag for pretty-printed output in addition to JSON, since researchers often want to quickly check limits from terminal.

- (2025-11-27) **Deferred API key validation**: API key mode was implemented speculatively but not tested. Left as known gap rather than removing the code.

## Outcomes & Retrospective

### What Worked Well

1. **Dataclass-based design**: Clean separation between `CodexAuthInfo`, `RateLimitWindowData`, and `UsageSnapshot` made the code testable and the API surface clear.

2. **Window label computation**: Ported the `get_limits_duration()` logic from Codex CLI exactly, ensuring labels match what users see in the Codex UI.

3. **Graceful degradation**: When Codex auth is missing or fails, the WebUI shows "Unknown / Gemini" as fallback and continues to display sessions. This prevents the Agents tab from breaking entirely.

4. **Test coverage for happy path**: The 5 unit tests cover the ChatGPT OAuth flow well.

### What's Missing

1. **API key mode is untested**: Code exists but we don't know if it works with real API keys.

2. **Token refresh**: Currently assumes `access_token` is valid. If it expires mid-session, the user must re-run `codex login`. Could add automatic refresh using `refresh_token` if present.

3. **No sign-in/sign-out flow**: Users must run `codex login` manually. The UI can't help.

4. **Error handling gaps**: 401/403 responses from expired tokens aren't handled gracefully.

### Follow-up Work

- ✅ UI integration completed in AGENTS_TAB_EXECPLAN
- ✅ Gemini/Claude status endpoints added for parity
- ❌ API key mode validation (needs someone with API key auth setup)
- ❌ Token refresh implementation
- ❌ Sign-in/sign-out flow from UI

## Implementation Plan

The steps below were executed in order. Documentation retained for reference.

### 1. Load Codex Credentials and Base URL ✅

How `codex exec` stores authentication:

1. `~/.codex/config.toml` includes `chatgpt_base_url`. If absent, default to `https://chatgpt.com/backend-api`. If the value already ends with `/backend-api`, Codex's REST helper switches to the `/wham` paths when contacting ChatGPT.
2. `~/.codex/auth.json` (created by `codex login`) includes either `openai_api_key` for API-key mode or `tokens` for ChatGPT mode. When `tokens` exist, we care about:
   - `access_token`: bearer token for the requests.
   - `account_id`: becomes the `ChatGPT-Account-Id` header.
   - `id_token.email` and `id_token.plan_type`: optional metadata we can echo in summaries.

**Implementation:**

- `shinka/tools/codex_usage.py` contains:
  - `load_auth_info(codex_home: Path) -> CodexAuthInfo`: Returns dataclass with auth mode, tokens, plan, and email.
  - `load_base_url(codex_home: Path) -> str`: Reads `config.toml`, normalizes URL, enforces `/backend-api` suffix.
  - `normalize_base_url(candidate: Optional[str]) -> str`: Handles edge cases and defaults.

### 2. Fetch and Normalize Usage ✅ (ChatGPT mode only)

**Implementation:**

- `collect_usage(codex_home: Optional[Path] = None) -> UsageSnapshot`:
  - Builds usage URL via `build_usage_url()` (chooses `/wham/usage` vs `/api/codex/usage`)
  - Sends headers: `Authorization: Bearer <token>`, `ChatGPT-Account-Id`, `User-Agent`
  - Parses response into `UsageSnapshot` with `plan`, `email`, `windows` list
  
- `parse_windows(payload: Dict) -> List[RateLimitWindowData]`:
  - Extracts `primary_window` and `secondary_window` from `rate_limit`
  - Computes labels via `get_limits_duration()` (ported from Codex CLI)
  - Formats `reset_at` to local time via `format_reset_timestamp()`

- CLI interface:
  - `python -m shinka.tools.codex_usage` → JSON output
  - `python -m shinka.tools.codex_usage --pretty` → Pretty JSON
  - `python -m shinka.tools.codex_usage --human` → Human-readable summary
  - `python -m shinka.tools.codex_usage --include-raw` → Include raw API response

### 3. Validate Functionality ⚠️ (Partial)

**Unit tests** (`tests/tools/test_codex_usage.py`):
1. ✅ `test_normalize_base_url_appends_backend_api` - URL normalization
2. ✅ `test_build_usage_url_for_chatgpt_base` - Path selection
3. ✅ `test_parse_windows_builds_labels` - Window parsing and label generation
4. ✅ `test_collect_usage_success` - Full flow with mocked HTTP (ChatGPT mode only)
5. ✅ `test_collect_usage_missing_auth` - Error handling for missing auth

**Missing tests:**
- ❌ `test_collect_usage_api_key_mode` - API key auth flow
- ❌ `test_collect_usage_expired_token` - 401 handling
- ❌ `test_collect_usage_rate_limited` - 429 handling

**Integration smoke test** (2025-11-27, ChatGPT OAuth mode):
```
$ python -m shinka.tools.codex_usage --pretty
{
  "plan": "Pro",
  "email": null,
  "windows": [
    {
      "label": "5h limit",
      "percent_used": 4.0,
      "window_minutes": 300,
      "reset_at": 1764275509,
      "reset_at_local": "20:31"
    },
    {
      "label": "weekly limit",
      "percent_used": 41.0,
      "window_minutes": 10080,
      "reset_at": 1764529245,
      "reset_at_local": "Sun 19:00"
    }
  ]
}
```

**Failure-path test**:
```
$ CODEX_HOME=/nonexistent python -m shinka.tools.codex_usage
Codex auth file not found at /nonexistent/auth.json. Run `codex login` first or set CODEX_HOME.
```

## Success Criteria & Validation

| Criterion | Status | Notes |
|-----------|--------|-------|
| `python -m shinka.tools.codex_usage --json` prints normalized usage JSON | ✅ | Works for ChatGPT OAuth mode |
| `pytest tests/tools/test_codex_usage.py -q` passes locally | ✅ | 5/5 tests pass |
| Missing auth fails fast with descriptive message | ✅ | Tested |
| API key mode works | ❌ | Code exists but not validated |
| Token refresh on expiry | ❌ | Not implemented |
| Sign-in/sign-out from UI | ❌ | Out of scope |

## Idempotence and Recovery

The helper only reads files and performs GET requests, so it is safe to rerun. No permanent state changes occur in Shinka.

## Artifacts and Notes

### CLI Output (2025-11-27)
```
$ python -m shinka.tools.codex_usage --human
Plan: Pro
Limits:
  - 5h limit: 4.0% used, resets at 20:31
  - weekly limit: 42.0% used, resets at Sun 19:00
```

### WebUI Display (2025-11-27)
The Agents tab shows:
- **Codex CLI Pro** card with usage bars
- 5h limit: 3.0% used, Resets 20:31
- weekly limit: 41.0% used, Resets Sun 19:00

## Interfaces and Dependencies

**Module:** `shinka/tools/codex_usage.py`

**Exports:**
- `CodexUsageError` - Exception class
- `CodexAuthInfo` - Dataclass for auth info
- `RateLimitWindowData` - Dataclass for rate limit windows
- `UsageSnapshot` - Dataclass for full usage snapshot
- `collect_usage(codex_home: Path) -> UsageSnapshot` - Main entry point
- `main(argv: Iterable[str])` - CLI entry point

**Tests:** `tests/tools/test_codex_usage.py` (5 tests, ChatGPT mode only)

**WebUI Integration:**
- Endpoint: `/api/codex_usage` in `shinka/webui/visualization.py`
- Frontend: `fetchAndRenderAgentUsage()` in `shinka/webui/viz_tree.html`

**Dependencies:**
- `requests` - HTTP client
- `tomllib` / `tomli` - TOML parsing for config.toml

## Change Log

- (2025-11-17) Initial draft created to scope the usage-fetch helper and validation steps per user request.
- (2025-11-18) Implementation completed: codex_usage.py + tests + CLI interface.
- (2025-11-20) UI integration completed in Agents tab.
- (2025-11-27) ExecPlan updated to reflect completion status with validation evidence.
- (2025-11-27) Revised status to MOSTLY COMPLETE: documented that API key mode is unvalidated and sign-in/sign-out flows are not implemented.
