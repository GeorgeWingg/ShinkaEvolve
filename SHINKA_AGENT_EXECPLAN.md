# ShinkaAgent: Native Model-Agnostic Agentic Backend

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

## Purpose / Big Picture

ShinkaAgent is a **native, model-agnostic** agentic editing backend that uses Shinka's existing LLM infrastructure (`shinka/llm/LLMClient`) rather than external CLI tools. This addresses Rob's core concern: CLI wrappers (Codex, Gemini, Claude) are "black boxes" that limit control, hackability, and LLM ensembling.

Key differentiators from CLI backends:
1. **Model Agnostic**: Works with any provider (OpenAI, Anthropic, DeepSeek, Google, AWS Bedrock)
2. **Full Control**: No subprocess overhead, in-process agent loop
3. **Hackable**: Mini-SWE-agent pattern (bash-only, regex parsing) is easy to extend
4. **LLM Ensembling**: Leverages existing bandit-based model selection
5. **Cost Transparency**: Direct API cost tracking via LLMClient

Reference implementation: [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)

After completing this work, a user can run:
```bash
env $(cat .env | xargs) uv run shinka_launch variant=shinka_agent_example
```

and see ShinkaAgent drive agentic editing using their API keys directly.

## Scope & Constraints

* **In-Process Execution**: Unlike CLI wrappers, ShinkaAgent runs entirely within the Python process
* **Bash-Only Actions**: Agent can only interact via ````bash...``` ` blocks (enforced via regex)
* **Stateless Commands**: Each bash command is executed via `subprocess.run()` (no persistent shell)
* **No External Dependencies**: Uses existing `shinka/llm/LLMClient` - no npm packages required
* **API Keys Required**: Needs at least one LLM provider API key in environment

## Feature Parity Checklist

### Core Functionality
- [x] **API key detection**: `ensure_shinka_available()` checks for OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.
- [x] **Exception classes**: `ShinkaUnavailableError`, `ShinkaExecutionError`
- [x] **AgentRunner protocol compliance**: Exact signature match with `types.py`

### Agent Loop
- [x] **System prompt**: Bash-only instructions with EVOLVE-BLOCK rules
- [x] **Multi-turn conversation**: Message history maintained across turns
- [x] **Action parsing**: Regex extraction of ````bash...``` ` blocks
- [x] **Command execution**: `subprocess.run()` with timeout, cwd, capture
- [x] **Observation formatting**: Exit code + stdout/stderr returned to agent
- [x] **Termination signal**: `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` keyword
- [x] **Output truncation**: Long outputs truncated to avoid context overflow

### Event Streaming
- [x] **Init event**: Session start with session_id, model, timestamp
- [x] **Agent message events**: LLM response text
- [x] **Command execution events**: Bash command results with exit_code, stdout, stderr
- [x] **Usage event**: Token counts and cost at session end

### Integration Points
- [x] **runner.py imports**: `from shinka.edit.shinka_agent import ...`
- [x] **runner.py evaluator selection**: `backend == "shinka"` handling
- [x] **runner.py editor selection**: `backend == "shinka"` handling
- [x] **edit/__init__.py exports**: Public symbols exported

### Limits & Safety
- [x] **Turn limit**: `max_events` parameter enforced
- [x] **Time limit**: `max_seconds` parameter enforced
- [x] **Command timeout**: Per-command 120s default timeout

## Progress

- [x] Milestone 1: Design agent loop following mini-SWE-agent pattern
- [x] Milestone 2: Implement `shinka/edit/shinka_agent.py` (~360 lines)
- [x] Milestone 3: Configuration wiring (`configs/evolution/shinka.yaml`)
- [x] Milestone 4: Unit tests (`tests/test_shinka_agent.py` - 38 tests)
- [x] Milestone 5: Bug fixes discovered during E2E testing
  - [x] Fix: Bash commands not executed when termination in same response
  - [x] Fix: `temperature` ’ `temperatures` parameter mapping
- [x] Milestone 6: E2E validation with circle_packing example
- [x] Milestone 7: Robustness improvements (2025-11-30)
  - [x] Fix: `ACTION_RE` now supports `bash`, `sh`, `shell`
  - [x] Fix: System prompt recommends Python for file editing
- [x] Milestone 8: WebUI card update to show "Ready" status (Added `/api/shinka_status` endpoint)

## Surprises & Discoveries

1. **Termination Before Execution Bug** (2025-11-25): The agent loop checked for `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` **before** parsing and executing bash commands. When the LLM included both a bash block and termination signal in the same response (a common pattern), the code would exit without running the edit.
   - **Impact**: Agent made 1-turn no-op sessions instead of actual edits
   - **Fix**: Move termination check to AFTER bash execution (lines 302-344)

2. **Parameter Name Mismatch** (2025-11-25): Config uses `temperature` (singular) but `LLMClient` expects `temperatures` (plural).
   - **Fix**: Added parameter mapping in `run_shinka_task()` (lines 189-195)

3. **gpt-4o-mini vs gpt-4.1-mini**: Some configs had typo `gpt-4.1-mini` instead of `gpt-4o-mini`.

## Decision Log

- **Decision**: Follow mini-SWE-agent pattern (bash-only, regex parsing, stateless subprocess)
  **Rationale**: Simple, debuggable, proven pattern. Avoids complexity of persistent shell or structured tool calling.
  **Date**: 2025-11-24

- **Decision**: Use existing `LLMClient` rather than raw API calls
  **Rationale**: Leverages existing bandit-based model selection, cost tracking, and retry logic.
  **Date**: 2025-11-24

- **Decision**: Execute bash commands even when termination signal is in same response
  **Rationale**: Common LLM pattern is "I'll do X [bash block] Done." - should execute the command.
  **Date**: 2025-11-25

## Outcomes & Retrospective

### Key Deliverables
1. **`shinka/edit/shinka_agent.py`** (~360 lines): Full in-process agent implementation
2. **`tests/test_shinka_agent.py`** (38 tests): Comprehensive unit test coverage
3. **`configs/evolution/shinka.yaml`**: Hydra config for ShinkaAgent backend
4. **`configs/variant/shinka_agent_example.yaml`**: Example variant config

### E2E Validation Results (Circle Packing)

| Metric | Before Bug Fixes | After Bug Fixes |
|--------|------------------|-----------------|
| Turns per session | 1-3 (immediate exit) | 3-11 (actual work) |
| Edits made | 0 bytes | 3.6KB+ diffs |
| Score improvement | Stuck at 0.9598 | 0.9598 ’ 1.5523 (62% gain) |
| Cost per session | ~$0.003 | ~$0.011 |

### Cost Tracking Verification
-  `LLMClient` correctly reports `response.cost`, `response.input_tokens`, `response.output_tokens`
-  Usage events emitted with actual token counts from API
-  Logs show per-session cost: `ShinkaAgent completed task in 11 turns, 224.5s, cost=$0.0110`

## Remaining Work

1. **Picbreeder E2E Test**: Validate multi-file editing works (uses `init_support_dir`)
2. **WebUI Card**: Update ShinkaAgent card to show "Ready" status like other backends
3. **Benchmark vs Codex**: Side-by-side comparison on same task

## Success Criteria & Validation

1. **Unit Tests Pass**: `uv run pytest tests/test_shinka_agent.py -v` ’ 38 passed
   -  Validated 2025-11-25

2. **E2E Circle Packing**: Score improves from baseline
   -  Score: 0.9598 ’ 1.5523

3. **Cost Tracking**: Non-zero costs in session logs
   -  Logs show `cost=$0.0110` per session

4. **Multi-Turn Sessions**: Agent takes >1 turn before completing
   -  Sessions now take 3-11 turns

## File Locations

- **Implementation**: `shinka/edit/shinka_agent.py`
- **Tests**: `tests/test_shinka_agent.py`
- **Config**: `configs/evolution/shinka.yaml`
- **Variant**: `configs/variant/shinka_agent_example.yaml`
- **Results**: `results/shinka_circle_packing/*/` (runs with `_shinka` suffix)
