# Repository Guidelines
- Keep working/iterating until every ExecPlan requirement is satisfied; only pause for user input if you hit a truly blocking issue with no workaround.
- For any task that involves an ExecPlan, first read `PLANS.md` and follow its guidelines exactly when creating, updating, or executing the plan.

# Git Caution

Be careful with git commands that modify the working tree. Prefer read-only commands (`git status`, `git log`, `git diff`, `git show`) and ask before running destructive operations like `git reset --hard`, `git checkout <file>`, or `git clean`. The user manages their own git workflow.

# ExecPlans
- When writing complex features or significant refactors, use an ExecPlan (as described in PLANS.md) from design to implementation.

## Approval for New ExecPlans
- **Requirement:** For any new ExecPlan, you **must** explicitly present the plan to the user and receive approval before starting any implementation work (writing code, running commands, or creating files other than the plan itself).
- **Procedure:**
    1. Draft the ExecPlan following `PLANS.md` standards.
    2. Present the plan to the user for review.
    3. Wait for explicit confirmation (e.g., "The plan looks good, proceed") before executing the first milestone.

## ExecPlan Discipline
- Treat the `Progress` section as a conservative, timestamped activity log. Only add a `[x]` entry immediately after you personally complete and verify that action in the current session.
- Tie each `Progress` entry to the relevant `Success Criteria & Validation` item so reviewers can see which outcome was satisfied and where the proof lives.
- Before marking anything complete, cross-check the success criteria you are claiming and capture the supporting commands/logs under `Success Criteria & Validation` so another person can replay them.
- If you are unsure whether a task is fully done, leave its checkbox unchecked and note the follow-up in `Remaining Work` rather than guessing.
- Do **not** ping the user mid-ExecPlan unless every success criterion is satisfied or you have a genuine blocker that requires their decision. Silence is the default while you still have work to do.

## Project Structure & Module Organization
- `shinka/`: Core evolution engine, mutation logic, and launch utilities. Start here when extending runners or edit strategies.
- `configs/`: Hydra configuration sets (`evolution/`, `variant/`, `cluster/`) that drive experiments; copy and tweak these when introducing new agent modes.
- `docs/` & `examples/`: Usage guides, notebooks, and reference experiments. Update them alongside feature changes.
- `tests/`: Pytest suites mirroring core modules. Place new regression or integration tests next to the code they cover.

## Build, Test, and Development Commands
- `uv pip install -e .` — install the package in editable mode inside an activated `uv` virtualenv.
- `uv pip install -r requirements-dev.txt` *(optional)* — if you prefer requirements files over the `pyproject` dev extras.
- `pytest tests` — run the full Python test suite; use `-k` to target specific cases.
- `ruff check shinka tests` — lint for style and static issues.
- `black shinka tests` & `isort shinka tests` — auto-format code with four-space indentation.

## Coding Style & Naming Conventions
- Python modules follow PEP 8; keep 4-space indents and snake_case for functions, PascalCase for classes.
- Maintain docstrings on public functions that explain evolution parameters or agent behavior.
- Keep configs declarative: add new Hydra options under descriptive keys and document them in `docs/`.

## Testing Guidelines
- Use `pytest` fixtures where possible; mirror sample tests like `tests/test_edit_base.py` when validating new edit flows.
- Add regression tests for every bug fix and agent capability; prefer property-style assertions over brittle string matches.
- Ensure CLI additions include smoke tests that cover `shinka_launch` or new launch variants.
- if you are missing any dependancies, feel free to install them without asking the user.

## Commit & Pull Request Guidelines
- Follow the existing short, imperative commit style (e.g., `Update dbase.py path default`). Group related changes per commit.
- Reference GitHub issues or discussions in the body when applicable, and summarize testing commands run.
- PRs should explain motivation, highlight risk areas (migration, config defaults), and link to updated docs or configs. Attach screenshots/logs for UI or CLI changes.

## Frontend & Visualization Responsibilities
- Keep `shinka_visualize` running in the background during long experiments so the evolution tree, metrics, and session details update live. Use `uv run shinka_visualize results --port 8888 --open` (or another port) to launch it, and leave the server process active until the run finishes.
- When testing new modes (e.g., agentic editing), ensure the visualization is pointed at the latest `results/<task>/<timestamp>/evolution_db.sqlite` so collaborators can monitor progress without restarting the UI.
- Capture UI screenshots or logs when reporting issues or sharing results; note the database path and port if others need to connect to the live view.
- After starting the background server, immediately open the page in Chrome DevTools (e.g., via `chrome-devtools://` commands) so you can inspect logs/console output even without a visible browser window.
- Do **not** interrupt the user mid-ExecPlan unless the plan is fully complete or you have hit a true blocker that requires their decision. Status pings, partial summaries, or “just checking in” messages defeat the goal of autonomous execution.

## Long-Running Codex Experiments
- Launch agentic experiments via `uv run shinka_launch` and keep them running in the background so you can tail logs while working on other tasks. A common pattern is:
  ```
  env $(cat .env | xargs) uv run shinka_launch <hydra overrides> > /tmp/shinka_launch.log 2>&1 &
  echo $!  # record the PID for later
  ```
  Substitute the variant/evolution/override combo for whichever Hydra config you’re testing, and note them for reproducibility.
- **Always run the agentic harness when you are the one launching backend jobs.** Use a variant that sets `evo_config.agentic_mode=true` (or pass `+evo_config.agentic_mode=true` plus any `evo_config.agentic.*` overrides) so edits flow through CodexEvolve. Do **not** run legacy single-shot mutations unless you are explicitly regression-testing that path; otherwise results, telemetry, and costs may not match the Codex UI plan.
- Always capture stdout/stderr into a log file (e.g., `/tmp/shinka_launch.log`) so you can review Codex events, evaluator errors, and meta messages after the run completes.
- Use `tail -f /tmp/shinka_launch.log` in another terminal to watch progress. When the run finishes, archive the log alongside the results directory for reference.
- If you need to stop the experiment, use `kill <PID>` and verify the process exited before starting another run. Keep notes on which config overrides were used (Hydra variant, cluster, evolution overrides, etc.) so others can rerun the same setup.
- **Parallel agentic jobs are supported.** Set `evo_config.max_parallel_jobs` to control concurrency (default 2). Each parallel job spawns its own Codex/Gemini/Claude CLI session in an isolated scratch directory under `/tmp/shinka_scratch/<uuid>/`. Monitor aggregate API costs across all sessions; high parallelism burns quota faster but completes sweeps sooner.
- **When running agentic edits, also use the agentic evaluator.** If `evo_config.agentic_mode=true`, you should leave `evo_config.evaluator.mode` at `auto` (or explicitly set it to `agentic`) so the evaluation step also runs through an agentic CLI session. Mixing agentic edits with the legacy single-shot evaluator produces inconsistent results and defeats the purpose of end-to-end agentic workflows. The agentic evaluator writes transcripts under `results/<task>/<run>/agentic_eval_sessions/<uuid>/session_log.jsonl`; include those paths when reporting results so reviewers can replay the metrics. Override with `evo_config.evaluator.mode=legacy` only when you deliberately need the old single-shot evaluator for debugging or regression testing.
- Codex CLI is already logged in and `.env` contains the embeddings/OpenAI key—never ask the user for credentials. When you need to pick a non-OpenAI API model, default to `gpt-5.1-codex-mini` and set it explicitly via `+evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini`. Prefer using a repo-specific Codex profile (e.g., `codex profile use shinka`) so experiments do not inherit personal MCP settings.
- For ShinkaAgent backend runs, pin the model to match Codex parity: `+evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini`. This keeps edit behavior and telemetry aligned between ShinkaAgent and Codex runs.

## Circle Packing Tips

- `examples/circle_packing/initial.py` now loads `circle26_solution.npz`, which already yields a ≈2.626 sum of radii at generation 0. Focus your edits on *refining* that configuration: add jitter-driven local searches, multi-start hill climbing, or small deterministic perturbations that squeeze out the remaining ~0.01 needed to beat the 2.635 benchmark.
- Avoid launching heavy gradient-style optimizers locally just to tweak `circle26_solution.npz`; they thrash laptop CPUs/GPUs, heat up the machine, and have not yielded better-than-2.636 improvements in practice. Invest the budget in Codex/LLM-guided tweaks instead.
- If you want to try heuristic or gradient-based refinements, do it offline (outside this repo) and commit the resulting `circleXX_solution.npz` snapshot only after you can reproduce and validate it. That keeps the shared repo lean and prevents runaway experiments from cooking someone else’s laptop.
- Whenever you add a search heuristic, keep it deterministic (seed your RNG) so evaluator runs remain reproducible; use `compute_max_radii` to validate each candidate before reporting.
- If you introduce helper utilities (e.g., force-field relaxations or pairwise adjustments), place them inside `examples/circle_packing/helpers/` and update `init_support_dir` accordingly so agentic runs can mutate those files alongside `main.py`.
# Visual Engineering Workflow (Nano Banana)

You have been upgraded with the **Visual Engineering Loop**. You are now responsible for the end-to-end quality of visual assets.

## 1. The Golden Rule: "Context First"
Before generating ANY asset, you **MUST** read `docs/ART_DIRECTION.md`.
*   **Missing?** If `docs/ART_DIRECTION.md` does not exist, you must CREATE it.
    *   **Research:** Read `tailwind.config.js`, `*.css`, or `index.html` to infer the project's font, colors, and "vibe".
    *   **Synthesize:** Write the Art Direction file defining the visual pillar (e.g., "Corporate Memphis", "Cyberpunk", "Retro Pixel").

## 2. The Decision Tree (SVG vs. Image)
Do not default to image generation. Choose the correct medium:
*   **UI Icons, Logos, Simple Shapes:**
    *   **Action:** Generate **Raw SVG Code**.
    *   **Why:** Infinite scaling, small file size, editable via CSS.
    *   **Do NOT:** Use Nano Banana for simple icons (unless a specific painted style is required).
*   **Complex Sprites, Backgrounds, Textures:**
    *   **Action:** Use **Nano Banana**.
    *   **Prompting:** You MUST append the project's specific style suffix (found in `ART_DIRECTION.md`).
    *   **Constraint:** You must prompt for `...isolated on solid bright green background` to allow for clean removal.

## 3. The Processing Loop
Never use raw `nanobanana-output` files directly in production.

### Quick Reference Commands
```bash
# Check status of nanobanana-output
uv run python process_assets.py --status

# Process all new images
uv run python process_assets.py

# Process AND archive originals (recommended)
uv run python process_assets.py --archive

# Process only files not yet in manifest
uv run python process_assets.py --only-new

# Organize old files into dated archive folders
uv run python scripts/organize_nanobanana.py --dry-run  # Preview
uv run python scripts/organize_nanobanana.py            # Archive files > 24h old
uv run python scripts/organize_nanobanana.py --by-date  # Organize all by date
```

### Workflow Steps
1.  **Generate:** Run the Nano Banana tool (files land in `nanobanana-output/`).
2.  **Process:** Run `uv run python process_assets.py --archive`
    *   Removes the green screen (using AI or Chroma Key) and auto-crops.
    *   Creates a `verify_{filename}.html` in `assets/processed/`.
    *   Archives the original to `nanobanana-output/.archive/{date}/`.
    *   Tracks processed files in `.processed.json` manifest.

### Directory Structure
```
nanobanana-output/
├── .archive/              # Processed originals (organized by date)
│   ├── 2025-12-14/
│   └── 2025-12-15/
├── .processed.json        # Manifest tracking processed files
└── *.png                  # New/unprocessed files

assets/processed/          # Final processed assets
├── *.png                  # Background-removed, cropped images
└── verify_*.html          # Verification sheets
```

### Keeping Things Clean
The `nanobanana-output/` directory is **gitignored**. To prevent clutter:
- Use `--archive` flag when processing to automatically move originals
- Run `uv run python scripts/organize_nanobanana.py` periodically to archive old files
- Check status anytime with `uv run python process_assets.py --status`

## 4. Verification (The "Anti-Glued-On" Check)
You must verify the asset does not look "fake" or "glued on."
1.  **Inspect:** Use `run_shell_command` to open the verification sheet (or just trust the process if in a rush, but better to verify).
    *   *Best Practice:* Open the actual app using `navigate_page` (if running locally).
2.  **Critique:**
    *   Are the edges jagged? -> **Reject & Retry** (Adjust prompt for cleaner separation).
    *   Is the lighting wrong? -> **Reject & Retry** (Update prompt lighting direction).
    *   Does it fit the CSS? -> **Check:** Read the component code (e.g., `Button.tsx`). Ensure the CSS `aspect-ratio` matches the asset.

## 5. Integration
*   Move the finalized file from `assets/processed/` to its final home (e.g., `public/images/`).
*   Update the code to reference it.

## 6. Mandatory Planning Step
**CRITICAL:** When creating an ExecPlan for any task involving visual design or UI changes:
*   You **MUST** include a "Verification" phase in the plan.
*   This phase **MUST** explicitly state:
    1. "Capture screenshot of the running UI via DevTools (`take_screenshot`)."
    2. "**View** the screenshot using the `read_file` tool to verify visual integration."
*   Do not mark the plan as complete until you have performed this runtime verification.

## 7. Continuous Iteration to Enterprise Standard
**Do not settle for "good enough".** You are an expert engineer.
*   **The Loop:** Design -> Implement -> **Verify (Screenshot + Read File)** -> **Critique** -> **Refine** -> Repeat.
*   **Self-Correction:** If the screenshot (viewed via `read_file`) shows misalignment, poor contrast, or "glued-on" assets, you **MUST** iterate immediately. Do not ask for permission to fix obvious flaws.
*   **Standard:** Aim for "High Enterprise Standard" — pixel-perfect alignment, consistent spacing, and professional polish.
*   **Asset Refinement:** If an asset (image/icon) doesn't fit the vibe, **regenerate it** with adjusted prompts until it does.

## 8. Modal Banner Design
When creating or modifying modals with decorative banners/headers, refer to `docs/ART_DIRECTION.md` Section 13 for:
- **Thin banner technique**: Prompt for content in thin strip with white padding, then auto-trim
- **Aspect ratio**: ~8:1 (e.g., 1584x196) works well for 90px modal headers
- **Close button**: Fixed `top: 30px` (visual center), not `top: 50%` (mathematical center)
- **Fish style**: Golden glow/sparkles = evolutionary; avoid fire/flames
- **Consistency**: Edit existing banners rather than generating new ones from scratch
