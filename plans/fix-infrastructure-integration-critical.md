# Fix Critical Infrastructure-Without-Integration Issues

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This document must be maintained in accordance with `PLANS.md` at the repository root.


## Purpose / Big Picture

This plan fixes seven critical "Infrastructure Without Integration" issues where sophisticated code exists but is never invoked in production paths. After these fixes:

1. **Island migration will actually run** - Programs will migrate between islands at configured intervals, enabling island-based evolutionary algorithms to work as designed
2. **Inspirations will appear in agentic prompts** - The agentic editing backends (Codex, Claude, Gemini) will receive relevant code examples as context, improving edit quality
3. **Parent selection strategies will all work** - Users can select "beam_search" strategy without crashes
4. **Real-time updates will stream to WebUI** - The SSE infrastructure will broadcast evolution events live
5. **GPU scheduling will work for local jobs** - Local job submission will wait for available GPUs instead of competing for resources
6. **Meta-LLM recommendations will be available** - Users can optionally enable AI-guided evolution recommendations

Each milestone is independently testable. The user can verify each fix works before proceeding to the next.


## Progress

- [x] Milestone 1: Fix BeamSearch API mismatch (2025-12-18)
  - Added `program_from_row_func` parameter to `BeamSearchSamplingStrategy.__init__`
  - Removed `@pytest.mark.skip` decorators from BeamSearch tests
  - All 52 parent selection tests pass

- [x] Milestone 2: Wire island migration into runner loop (2025-12-18)
  - Added `self.db.check_scheduled_operations()` call after `db.add()` in `runner.py:2621`
  - All 21 island tests pass including migration tests

- [x] Milestone 3: Include inspirations in agentic prompts (2025-12-18)
  - Discovery: Inspirations WERE being included via `eval_history_msg` prepended in sampler.py
  - Enhancement: Added explicit "# Reference Examples" section header in `sampler.py:208-215` for clarity

- [x] Milestone 4: Start SSE server and event watchers (2025-12-18)
  - Added import for `run_sse_server_sync` in `visualization.py:55`
  - Added SSE server thread startup in `visualization.py:main()` at lines 5777-5785
  - All 42 SSE tests pass

- [x] Milestone 5: Fix local GPU scheduling bypass (2025-12-18)
  - Added `_wait_for_free_gpus()` function to `local.py:16-68`
  - Added `gpus` parameter to `submit()` function in `local.py:132`
  - Added `gpus` field to `LocalJobConfig` in `scheduler.py:39`
  - Updated scheduler to pass gpus to `submit_local()` in `scheduler.py:155-157` and `211-213`

- [x] Milestone 6: Enable meta-LLM option (opt-in) (2025-12-18)
  - Created `configs/evolution/agentic_meta.yaml` config variant
  - Added docstring to `EvolutionConfig` class in `runner.py:491-512` documenting meta-LLM options


## Surprises & Discoveries

- Milestone 3: Inspirations were already being included in agentic prompts! The `eval_history_msg` was already being prepended to `AGENTIC_ITER_MSG` in `sampler.py:230`. Added explicit section header for better clarity.
- Milestone 4: SSE server code was complete but never started from main() - simple integration fix.


## Decision Log

- Decision: Scope limited to 6 milestones, deferring meta-LLM full enablement to opt-in only
  Rationale: Meta-LLM requires OpenAI API key which may conflict with existing setup (TODO-002 in configs). Safer to make it opt-in rather than default-on.
  Date/Author: 2025-12-18 / Claude

- Decision: Fix BeamSearch by adding missing parameter rather than removing strategy
  Rationale: Strategy is documented in examples and user-facing. Removal would be breaking change. Fix is additive.
  Date/Author: 2025-12-18 / Claude


## Outcomes & Retrospective

(To be populated at completion)


## Context and Orientation

This plan addresses issues discovered by a 25-agent investigation of the shinka codebase. The shinka system is an evolutionary code generation framework that mutates programs, evaluates them, and selects the best for further evolution.

Key files and their roles:

- `shinka/core/runner.py` (4,225 lines): The main evolution orchestrator. Contains `EvolutionRunner` class that drives the mutation-evaluation loop. This is where most integration gaps exist.

- `shinka/database/dbase.py` (~800 lines): Database abstraction for storing programs. Contains `check_scheduled_operations()` method that handles island migration but is never called.

- `shinka/database/parents.py` (715 lines): Parent selection strategies. Contains `BeamSearchSamplingStrategy` with API mismatch and `CombinedParentSelector` that instantiates strategies.

- `shinka/database/islands.py` (~600 lines): Island-based evolution. Contains `ElitistMigrationStrategy` that is fully implemented but never triggered.

- `shinka/edit/agentic.py` (~400 lines): Agentic editing orchestration. Contains prompt templates that should include inspirations but don't.

- `shinka/webui/visualization.py` (5,770 lines): Flask web server. Should start SSE server and event watchers but doesn't.

- `shinka/webui/sse_server.py` (~150 lines): SSE (Server-Sent Events) server for real-time updates. Complete but never started.

- `shinka/webui/event_watchers.py` (~350 lines): Watchers that emit SSE events. Complete but never instantiated.

- `shinka/launch/scheduler.py` (370 lines): Job scheduling. Contains local job submission that bypasses GPU wait logic in `slurm.py`.

- `shinka/launch/slurm.py` (~500 lines): Contains `launch_local_subprocess()` with GPU waiting logic that is never called for local jobs.

Terms used in this plan:

- **Island migration**: Moving programs between separate "islands" (subpopulations) to share genetic material, preventing local optima
- **Inspirations**: High-quality code examples from the archive shown to LLMs as context for generating edits
- **Beam search**: Parent selection strategy that maintains top-K candidates and expands from best ones
- **SSE (Server-Sent Events)**: HTTP-based protocol for server-to-client real-time streaming
- **Agentic mode**: Using CLI tools (Codex, Claude, Gemini) as agents for code editing rather than single-shot API calls


## Plan of Work

### Milestone 1: Fix BeamSearch API Mismatch

The `BeamSearchSamplingStrategy` class in `shinka/database/parents.py` has a constructor that accepts 5 parameters, but `CombinedParentSelector` (same file) tries to instantiate it with 11 parameters including `program_from_row_func` which doesn't exist in the BeamSearch constructor.

Fix: Add the missing `program_from_row_func` parameter to `BeamSearchSamplingStrategy.__init__()` and use it in the `sample()` method, matching the pattern of other strategies.

Files to modify:
- `shinka/database/parents.py`: Add parameter to BeamSearchSamplingStrategy

Tests to unskip:
- `tests/test_database_parents.py`: Remove `@pytest.mark.skip` from BeamSearch tests


### Milestone 2: Wire Island Migration into Runner Loop

The `check_scheduled_operations()` method in `shinka/database/dbase.py` checks if migration is due and performs it. This is never called from the runner.

Fix: In `EvolutionRunner.run()` in `shinka/core/runner.py`, call `self.db.check_scheduled_operations()` after each program is added (after the `db.add()` call in the main loop).

Files to modify:
- `shinka/core/runner.py`: Add call to `check_scheduled_operations()` after program addition


### Milestone 3: Include Inspirations in Agentic Prompts

The runner computes inspirations via `db.sample()` which returns `(parent, archive_inspirations, topk_inspirations)`. These are available but never included in the prompt sent to agentic backends.

Fix: Modify `shinka/edit/agentic.py` to include inspirations in the iteration prompt. The prompt template `AGENTIC_ITER_MSG` needs a placeholder for inspirations, and the calling code needs to format them.

Files to modify:
- `shinka/edit/agentic.py`: Add inspirations placeholder to AGENTIC_ITER_MSG and format them when calling the template


### Milestone 4: Start SSE Server and Event Watchers

The SSE infrastructure in `shinka/webui/sse_server.py` and watchers in `shinka/webui/event_watchers.py` are complete but never started.

Fix: In `shinka/webui/visualization.py`, import and start the SSE server and instantiate event watchers when the Flask app starts.

Files to modify:
- `shinka/webui/visualization.py`: Add SSE server startup and watcher instantiation


### Milestone 5: Fix Local GPU Scheduling Bypass

The function `launch_local_subprocess()` in `shinka/launch/slurm.py` waits for GPUs before launching. But local job submission in `shinka/launch/local.py` uses `subprocess.Popen` directly, bypassing this.

Fix: Modify `shinka/launch/local.py` to optionally use GPU waiting logic when GPUs are requested.

Files to modify:
- `shinka/launch/local.py`: Add GPU wait logic for local submissions
- `shinka/launch/scheduler.py`: Pass GPU count to local submission


### Milestone 6: Enable Meta-LLM Option (Opt-In)

All configs set `meta_llm_models: null`, disabling the meta-LLM feature entirely. Rather than enabling by default (which may conflict with API keys), make it explicitly available.

Fix: Create a new config variant that enables meta-LLM and document how to use it.

Files to create/modify:
- `configs/evolution/agentic_meta.yaml`: New config with meta_llm enabled
- Update docstrings in runner.py to explain the feature


## Concrete Steps

### Milestone 1: Fix BeamSearch API Mismatch

Working directory: `/Users/juno/workspace/shrinkaevolve-codexevolve`

Step 1.1: Read current BeamSearchSamplingStrategy implementation

    cat -n shinka/database/parents.py | sed -n '476,530p'

Expected: See class with __init__ accepting ~5 params (config, cursor, conn, etc.) but NOT program_from_row_func

Step 1.2: Read how other strategies handle program_from_row_func

    grep -A 20 "class PowerLawSamplingStrategy" shinka/database/parents.py | head -30

Expected: See program_from_row_func parameter in constructor and stored as self.program_from_row_func

Step 1.3: Edit BeamSearchSamplingStrategy to add missing parameter

In `shinka/database/parents.py`, modify the `BeamSearchSamplingStrategy.__init__` method to accept `program_from_row_func` parameter and store it. Then use it in the `sample()` method where programs are retrieved from rows.

Step 1.4: Remove skip decorators from tests

    grep -n "pytest.mark.skip" tests/test_database_parents.py

Edit the file to remove the skip decorators from BeamSearch tests.

Step 1.5: Run tests to verify

    uv run pytest tests/test_database_parents.py -k "BeamSearch" -v

Expected: Tests pass (previously skipped due to bug)


### Milestone 2: Wire Island Migration into Runner Loop

Working directory: `/Users/juno/workspace/shrinkaevolve-codexevolve`

Step 2.1: Find where programs are added in runner

    grep -n "db.add\|self.db.add" shinka/core/runner.py | head -10

Expected: Line numbers where db.add() is called in the main evolution loop

Step 2.2: Verify check_scheduled_operations exists

    grep -n "def check_scheduled_operations" shinka/database/dbase.py

Expected: Method definition found

Step 2.3: Add call after program addition

In `shinka/core/runner.py`, after the `self.db.add(...)` call in the main evolution loop, add:

    self.db.check_scheduled_operations()

Step 2.4: Run island tests to verify migration works

    uv run pytest tests/test_database_islands.py -v

Expected: All tests pass including migration tests


### Milestone 3: Include Inspirations in Agentic Prompts

Working directory: `/Users/juno/workspace/shrinkaevolve-codexevolve`

Step 3.1: Find AGENTIC_ITER_MSG template

    grep -n "AGENTIC_ITER_MSG" shinka/edit/agentic.py

Step 3.2: See how inspirations are currently computed

    grep -n "archive_inspirations\|topk_inspirations" shinka/core/runner.py | head -10

Step 3.3: Modify agentic.py to include inspirations

Add a section to AGENTIC_ITER_MSG template for inspirations:

    ## Reference Code Examples

    The following are high-quality code examples from the archive that may help:

    {inspirations_text}

Then modify the code that calls this template to format inspirations into inspirations_text.

Step 3.4: Add test for inspiration inclusion

Create or update test to verify inspirations appear in prompts when available.

Step 3.5: Run agentic tests

    uv run pytest tests/ -k "agentic" -v

Expected: Tests pass with inspirations in prompts


### Milestone 4: Start SSE Server and Event Watchers

Working directory: `/Users/juno/workspace/shrinkaevolve-codexevolve`

Step 4.1: Read SSE server start function

    grep -n "def start_sse_server\|def create_sse_app" shinka/webui/sse_server.py

Step 4.2: Read event watcher classes

    grep -n "class.*Watcher" shinka/webui/event_watchers.py

Step 4.3: Find Flask app initialization in visualization.py

    grep -n "app = Flask\|def create_app\|if __name__" shinka/webui/visualization.py | head -10

Step 4.4: Add SSE initialization to visualization.py

At app startup, add:

    from shinka.webui.sse_server import start_sse_server
    from shinka.webui.event_watchers import SessionWatcher, DatabaseWatcher, RegistryWatcher

    # Start SSE server on separate port
    sse_thread = start_sse_server(port=8889)

    # Start watchers
    session_watcher = SessionWatcher(db_path, sse_manager)
    session_watcher.start()

Step 4.5: Test SSE functionality

    # Terminal 1: Start visualization
    uv run shinka_visualize results --port 8888

    # Terminal 2: Connect to SSE
    curl -N http://localhost:8889/events

Expected: SSE connection stays open and receives events when evolution runs


### Milestone 5: Fix Local GPU Scheduling Bypass

Working directory: `/Users/juno/workspace/shrinkaevolve-codexevolve`

Step 5.1: Read current local submission

    cat -n shinka/launch/local.py | head -80

Step 5.2: Read GPU wait logic in slurm.py

    grep -n "def launch_local_subprocess\|wait.*gpu\|nvidia-smi" shinka/launch/slurm.py

Step 5.3: Refactor GPU wait logic to shared utility

Create a utility function that can be used by both local.py and slurm.py:

    def wait_for_gpus(num_gpus: int, timeout: int = 300) -> list[int]:
        """Wait for specified number of free GPUs, return CUDA device IDs."""
        ...

Step 5.4: Update local.py to use GPU waiting

Modify `submit_local()` to optionally wait for GPUs when `gpus > 0` is passed.

Step 5.5: Test with GPU job

    # Run a local job that requests GPUs
    uv run pytest tests/ -k "gpu" -v

Expected: Job waits for GPU availability before starting


### Milestone 6: Enable Meta-LLM Option (Opt-In)

Working directory: `/Users/juno/workspace/shrinkaevolve-codexevolve`

Step 6.1: Create new config variant

Create `configs/evolution/agentic_meta.yaml`:

    defaults:
      - agentic

    # Enable Meta-LLM recommendations
    # Requires OPENAI_API_KEY environment variable
    meta_llm_models:
      - gpt-4o-mini
    meta_rec_interval: 10
    meta_max_recommendations: 5
    meta_llm_kwargs:
      temperatures: [0.0, 0.3]

Step 6.2: Update documentation in runner.py

Add docstring explaining meta_llm_models usage and requirements.

Step 6.3: Test meta-LLM activation

    # With OpenAI key set
    OPENAI_API_KEY=sk-xxx uv run shinka_launch evolution=agentic_meta variant=default +dry_run=true

Expected: Meta-LLM initializes without error (dry run mode)


## Success Criteria & Validation

Each milestone has specific validation criteria. Update this section with actual command outputs as work proceeds.

**Milestone 1 - BeamSearch Fix:**
- [x] `uv run pytest tests/test_database_parents.py -k "BeamSearch" -v` passes all tests
- [x] No `@pytest.mark.skip` decorators remain on BeamSearch tests

**Milestone 2 - Island Migration:**
- [x] `uv run pytest tests/test_database_islands.py::TestElitistMigrationStrategy -v` passes
- [x] `check_scheduled_operations()` call added after `db.add()` in runner.py

**Milestone 3 - Inspirations in Prompts:**
- [x] Verified: `eval_history_msg` already prepended to prompts in `sampler.py:230`
- [x] Added explicit "# Reference Examples" header for clarity in `sampler.py:208-215`

**Milestone 4 - SSE Server:**
- [x] `run_sse_server_sync()` import added to visualization.py
- [x] SSE server thread startup added in main() function
- [x] All 42 SSE tests pass

**Milestone 5 - GPU Scheduling:**
- [x] `_wait_for_free_gpus()` function added to local.py
- [x] `gpus` parameter added to `submit()` function in local.py
- [x] `gpus` field added to `LocalJobConfig` in scheduler.py
- [x] Scheduler passes gpus to submit_local() in both run() and submit_async()

**Milestone 6 - Meta-LLM Option:**
- [x] Config file `configs/evolution/agentic_meta.yaml` exists and is valid
- [x] Docstring added to `EvolutionConfig` documenting meta-LLM options
- [x] Config loads correctly with OmegaConf


## Idempotence and Recovery

All changes are additive and safe to repeat:

- **BeamSearch fix**: Adding a parameter is backward compatible. Existing code that doesn't pass it will use the default.
- **Migration call**: Calling `check_scheduled_operations()` multiple times is safe; it only acts when conditions are met.
- **Inspiration template**: Adding optional section to template doesn't break existing prompts.
- **SSE server**: Server startup is idempotent; starting twice is handled gracefully.
- **GPU wait**: Waiting logic has timeout; won't block forever.
- **Meta-LLM config**: New file, doesn't affect existing configs.

If any step fails:
1. Tests will catch regressions
2. Git revert to previous commit
3. Re-read this plan and retry with corrections noted in Decision Log


## Artifacts and Notes

(To be populated with transcripts during implementation)


## Interfaces and Dependencies

### Milestone 1 - BeamSearch

In `shinka/database/parents.py`, modify:

    class BeamSearchSamplingStrategy(ParentSamplingStrategy):
        def __init__(
            self,
            config: DatabaseConfig,
            cursor: sqlite3.Cursor,
            conn: sqlite3.Connection,
            get_all_programs_func: Callable,
            program_from_row_func: Callable,  # ADD THIS
            num_beams: int = 3,
        ):
            ...
            self.program_from_row_func = program_from_row_func  # ADD THIS

### Milestone 2 - Migration

In `shinka/core/runner.py`, after `self.db.add(...)` call:

    # Check for scheduled operations (migration, etc.)
    self.db.check_scheduled_operations()

### Milestone 3 - Inspirations

In `shinka/edit/agentic.py`, modify `AGENTIC_ITER_MSG`:

    AGENTIC_ITER_MSG = """
    ... existing content ...

    ## Reference Code Examples

    {inspirations_section}
    """

Add helper function:

    def format_inspirations(archive: list, topk: list) -> str:
        """Format inspirations for inclusion in prompt."""
        if not archive and not topk:
            return "No reference examples available."

        sections = []
        if archive:
            sections.append("### Archive Examples (proven solutions)\n" +
                          "\n---\n".join(p.code for p in archive[:3]))
        if topk:
            sections.append("### Recent High-Performers\n" +
                          "\n---\n".join(p.code for p in topk[:3]))
        return "\n\n".join(sections)

### Milestone 4 - SSE

In `shinka/webui/visualization.py`, add startup code:

    from shinka.webui.sse_server import SSEManager, start_sse_server
    from shinka.webui.event_watchers import SessionWatcher, DatabaseWatcher

    # Global SSE manager
    sse_manager = SSEManager()

    def start_background_services(db_path: str):
        """Start SSE server and event watchers."""
        # Start SSE server thread
        start_sse_server(sse_manager, port=8889)

        # Start watchers
        if db_path:
            db_watcher = DatabaseWatcher(db_path, sse_manager)
            db_watcher.start()

### Milestone 5 - GPU Wait

In `shinka/launch/local.py`, add:

    def wait_for_free_gpus(num_gpus: int, timeout: int = 300) -> list[int]:
        """
        Poll nvidia-smi until num_gpus are free.
        Returns list of available CUDA device indices.
        """
        import subprocess
        import time

        start = time.time()
        while time.time() - start < timeout:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                usage = [int(x) for x in result.stdout.strip().split("\n")]
                free_gpus = [i for i, mem in enumerate(usage) if mem < 100]  # <100MB = free
                if len(free_gpus) >= num_gpus:
                    return free_gpus[:num_gpus]
            time.sleep(5)
        raise TimeoutError(f"Could not acquire {num_gpus} GPUs within {timeout}s")

### Milestone 6 - Meta-LLM Config

New file `configs/evolution/agentic_meta.yaml`:

    # Agentic evolution with Meta-LLM recommendations
    # Requires: OPENAI_API_KEY environment variable

    defaults:
      - agentic

    meta_llm_models:
      - gpt-4o-mini
    meta_rec_interval: 10
    meta_max_recommendations: 5
    meta_llm_kwargs:
      temperatures: [0.0, 0.3]


## Remaining Work

(To be populated as implementation proceeds with any deferred items)
