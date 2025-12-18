import atexit
import base64
import difflib
import hashlib
import json
import os
import signal
import shutil
import uuid
import time
import logging
import yaml
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from rich.logging import RichHandler
from rich.table import Table
from rich.console import Console
import rich.box
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union, cast, Literal, Set
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from subprocess import Popen
from shinka.launch import JobScheduler, JobConfig, ProcessWithLogging
from shinka.database import ProgramDatabase, DatabaseConfig, Program
from shinka.llm import (
    LLMClient,
    extract_between,
    EmbeddingClient,
    BanditBase,
    AsymmetricUCB,
)
from shinka.llm.backend_bandit import (
    BackendBandit,
    BackendBanditConfig,
    NoAuthenticatedBackendsError,
    BACKEND_ARMS,
)
from shinka.edit import (
    AgentContext,
    AgenticEditor,
    CommandResult,
    apply_diff_patch,
    apply_full_patch,
    summarize_diff,
    redact_immutable,
)
from shinka.edit.codex_cli import (
    CodexExecutionError,
    CodexUnavailableError,
    ensure_codex_available,
    run_codex_task,
)
from shinka.edit.gemini_cli import (
    ensure_gemini_available,
    run_gemini_task,
    GeminiUnavailableError,
    GeminiExecutionError,
)
from shinka.edit.claude_cli import (
    ensure_claude_available,
    run_claude_task,
    ClaudeUnavailableError,
    ClaudeExecutionError,
)
from shinka.edit.shinka_agent import (
    ensure_shinka_available,
    run_shinka_task,
    ShinkaUnavailableError,
    ShinkaExecutionError,
)
from shinka.edit.jules_cli import (
    JulesExecutionError,
    JulesUnavailableError,
    run_jules_task,
)
from shinka.edit.jules_api import ensure_jules_available
from shinka.eval import AgenticEvaluator, EnsembleEvaluator
from shinka.eval.agentic import AgenticEvaluatorResult
from shinka.eval.ensemble import EnsembleEvaluationResult
from shinka.core.sampler import PromptSampler
from shinka.core.summarizer import MetaSummarizer
from shinka.core.novelty_judge import NoveltyJudge
from shinka.core.embedding_corpus import (
    build_embedding_corpus,
    extract_file_content,
    EmbeddingCorpus,
)
from shinka.logo import print_gradient_logo
from shinka.webui.git_worktree import EvolutionGitManager

# -----------------------------------------------------------------------------
# Thread-Safety Model
# -----------------------------------------------------------------------------
#
# EvolutionRunner uses ThreadPoolExecutors for parallel agentic edits and
# evaluations. The threading model is as follows:
#
# THREAD-SAFE STATE (protected by locks):
#   - self.running_jobs: List[RunningJob]
#       Protected by: self._jobs_lock (threading.Lock)
#       Used in: _check_completed_jobs(), _submit_new_job(), _process_completed_job()
#
#   - self._active_scratch_dirs: Set[Path]
#       Protected by: self._scratch_dir_lock (threading.Lock)
#       Used in: _register_scratch_dir(), _unregister_scratch_dir(), _cleanup_scratch_dirs()
#
# SINGLE-THREADED STATE (main thread only):
#   - self.best_program_id: Only updated in main loop after jobs complete
#   - self.completed_generations: Counter updated in main loop
#   - self.next_generation_to_submit: Counter advanced in main loop
#   - self.db: ProgramDatabase - all writes happen in main thread via _process_completed_job()
#   - self._stagnation_counter, self._best_score_seen: Stagnation tracking in main loop
#
# WORKER THREADS (ThreadPoolExecutor):
#   - _run_agentic_patch_worker(): Executes agentic edit sessions
#   - _run_agentic_eval_worker(): Executes agentic evaluations
#   - Workers return results via Future objects; main thread processes results
#
# IMPORTANT:
#   - All database writes (self.db.add(), etc.) are done on the main thread
#   - Workers only prepare data and return it; they don't write to shared state
#   - The main loop polls for completed jobs every 2 seconds
# -----------------------------------------------------------------------------

FOLDER_PREFIX = "gen"

WORKSPACE_EXCLUDE_DIRS = {
    "results",
    "workspace_snapshot",
    "agent_sessions",
    ".hydra",
    "__pycache__",
    ".git",
    ".venv",
    "node_modules",
    ".pytest_cache",
}
WORKSPACE_EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
WORKSPACE_EXCLUDE_FILES = {
    "rewrite.txt",
    "edit.diff",
    "session_log.jsonl",
}

AGENTIC_EVAL_PREVIEW_LIMIT = 200


@dataclass
class JulesConfig:
    """Jules-specific configuration fields.

    These are bridged to extra_cli_config in AgenticConfig.__post_init__
    for backward compatibility with jules_cli.py.
    """
    github_repo: str = ""  # Required for Jules backend
    base_branch: str = "main"
    automation_mode: str = "AUTO_CREATE_PR"
    poll_interval: int = 15
    cleanup_branch: bool = True
    require_plan_approval: Optional[bool] = None
    auto_approve_plan: bool = True


@dataclass
class AgenticConfig:
    """Configuration options for agentic editing sessions.

    This config is backend-agnostic: it works with Codex, Gemini, Claude,
    ShinkaAgent, or Jules backends. The `backend` field selects which one to use.

    When `bandit_selection` is True, the backend is dynamically selected
    using a multi-armed bandit (UCB) algorithm that learns which backend
    performs best over time.

    Sandbox semantics vary per backend:
    - Codex: Policy string passed to --sandbox (e.g., "workspace-write", "none")
    - Claude: Truthy triggers --dangerously-skip-permissions
    - Gemini: Truthy enables --sandbox flag
    - Jules: Ignored (cloud-based)
    - ShinkaAgent: Ignored (local process)
    """

    backend: str = "codex"
    # Explicit model selection (takes precedence over cli_profile for Claude/Gemini/ShinkaAgent)
    model: Optional[str] = None
    cli_profile: Optional[str] = None  # CLI profile (Codex) or legacy model selector
    sandbox: str = "workspace-write"
    approval_mode: str = "full-auto"
    # Canonical event limit field
    max_events: int = 50
    # Deprecated: use max_events instead (kept for Hydra backward compatibility)
    max_turns: Optional[int] = None
    max_seconds: int = 0
    cli_path: Optional[str] = None
    extra_cli_config: Dict[str, Any] = field(default_factory=dict)
    resume_parent_session: bool = False
    # Base directory for scratch workspaces. Using /tmp ensures scratch dirs are
    # outside any git repo, preventing Codex CLI from discovering parent AGENTS.md
    # files. Set to None to use the results directory (legacy behavior).
    scratch_dir_base: Optional[str] = "/tmp/shinka_scratch"

    # Backend bandit selection: dynamically choose backend via UCB
    bandit_selection: bool = False
    bandit_kwargs: Dict[str, Any] = field(default_factory=dict)

    # Global bandit history: persist learning across runs
    use_global_bandit_history: bool = False  # Load priors from global history
    record_to_global_history: bool = True    # Save interactions to global history

    # Backend-specific typed configs (bridged to extra_cli_config in __post_init__)
    jules: Optional[JulesConfig] = None

    def __post_init__(self):
        """Handle deprecated fields, bridging, and validation."""
        import warnings

        # 1. Handle deprecated max_turns → max_events
        # Handle Hydra passing "None" as a string instead of Python None
        if isinstance(self.max_turns, str) and self.max_turns.lower() == "none":
            self.max_turns = None
        if self.max_turns is not None:
            warnings.warn(
                "AgenticConfig.max_turns is deprecated, use max_events instead",
                DeprecationWarning,
                stacklevel=2,
            )
            # Ensure it's an integer
            self.max_events = int(self.max_turns)

        # 2. Bridge: Merge typed JulesConfig into extra_cli_config for legacy backend
        if self.backend == "jules" and self.jules:
            # Ensure extra_cli_config is a mutable dict (OmegaConf may give DictConfig)
            try:
                from omegaconf import OmegaConf
                if OmegaConf.is_config(self.extra_cli_config):
                    self.extra_cli_config = dict(OmegaConf.to_container(self.extra_cli_config, resolve=True))
            except ImportError:
                pass
            if not isinstance(self.extra_cli_config, dict):
                self.extra_cli_config = dict(self.extra_cli_config)

            self.extra_cli_config.setdefault("github_repo", self.jules.github_repo)
            self.extra_cli_config.setdefault("base_branch", self.jules.base_branch)
            self.extra_cli_config.setdefault("automation_mode", self.jules.automation_mode)
            self.extra_cli_config.setdefault("poll_interval", self.jules.poll_interval)
            self.extra_cli_config.setdefault("cleanup_branch", self.jules.cleanup_branch)
            if self.jules.require_plan_approval is not None:
                self.extra_cli_config.setdefault("require_plan_approval", self.jules.require_plan_approval)
            self.extra_cli_config.setdefault("auto_approve_plan", self.jules.auto_approve_plan)

        # 3. Validation (fail fast)
        errors = []

        # Validate backend is known
        valid_backends = ("codex", "gemini", "claude", "shinka", "jules")
        if self.backend not in valid_backends:
            errors.append(f"Unknown backend: {self.backend}. Valid: {valid_backends}")

        # Jules requires github_repo
        if self.backend == "jules":
            repo = self.extra_cli_config.get("github_repo") or (
                self.jules.github_repo if self.jules else ""
            )
            if not repo:
                errors.append(
                    "Jules backend requires github_repo. Set via "
                    "agentic.jules.github_repo or agentic.extra_cli_config.github_repo"
                )

        if errors:
            raise ValueError(f"AgenticConfig validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    # Deprecated aliases for backward compatibility
    @property
    def codex_profile(self) -> Optional[str]:
        """Deprecated: use cli_profile instead."""
        return self.cli_profile

    @property
    def codex_path(self) -> Optional[str]:
        """Deprecated: use cli_path instead."""
        return self.cli_path


@dataclass
class AgenticEvaluatorConfig:
    """Configuration for agentic evaluation sessions.

    The evaluator can use a different backend than the editor.
    If backend is None, inherits from parent AgenticConfig.backend.
    """

    backend: Optional[str] = None  # If None, use agentic.backend
    # Explicit model selection (takes precedence over cli_profile for Claude/Gemini/ShinkaAgent)
    model: Optional[str] = None
    cli_profile: Optional[str] = None  # CLI profile (Codex) or legacy model selector
    sandbox: str = "workspace-write"
    approval_mode: str = "full-auto"
    # Canonical event limit field
    max_events: int = 80
    # Deprecated: use max_events instead (kept for Hydra backward compatibility)
    max_turns: Optional[int] = None
    max_seconds: int = 0
    cli_path: Optional[str] = None
    extra_cli_config: Dict[str, Any] = field(default_factory=dict)
    eval_prompt: Optional[str] = None

    def __post_init__(self):
        """Handle deprecated fields."""
        import warnings

        # Handle deprecated max_turns → max_events
        # Handle Hydra passing "None" as a string instead of Python None
        if isinstance(self.max_turns, str) and self.max_turns.lower() == "none":
            self.max_turns = None
        if self.max_turns is not None:
            warnings.warn(
                "AgenticEvaluatorConfig.max_turns is deprecated, use max_events instead",
                DeprecationWarning,
                stacklevel=2,
            )
            # Ensure it's an integer
            self.max_events = int(self.max_turns)

    # Deprecated aliases for backward compatibility
    @property
    def codex_profile(self) -> Optional[str]:
        """Deprecated: use cli_profile instead."""
        return self.cli_profile

    @property
    def codex_path(self) -> Optional[str]:
        """Deprecated: use cli_path instead."""
        return self.cli_path


@dataclass
class EvaluatorInstanceConfig:
    """Configuration for a single evaluator in an ensemble.

    All fields are Optional to support inheritance from defaults.
    When an evaluator is resolved, None values inherit from the ensemble defaults.
    """

    name: str = "primary"
    backend: Optional[str] = None  # If None, inherits from defaults
    model: Optional[str] = None
    cli_profile: Optional[str] = None
    sandbox: Optional[str] = None
    approval_mode: Optional[str] = None
    max_events: Optional[int] = None
    max_seconds: Optional[int] = None
    cli_path: Optional[str] = None
    extra_cli_config: Dict[str, Any] = field(default_factory=dict)
    eval_prompt: Optional[str] = None

    # Ensemble-specific settings
    weight: float = 1.0  # Weight for weighted aggregation strategies
    enabled: bool = True  # Can disable evaluators without removing them

    def merge_with_defaults(self, defaults: "EvaluatorInstanceConfig") -> "EvaluatorInstanceConfig":
        """Create new instance with defaults filled in for None values."""
        return EvaluatorInstanceConfig(
            name=self.name,
            backend=self.backend if self.backend is not None else defaults.backend,
            model=self.model if self.model is not None else defaults.model,
            cli_profile=self.cli_profile if self.cli_profile is not None else defaults.cli_profile,
            sandbox=self.sandbox if self.sandbox is not None else defaults.sandbox,
            approval_mode=self.approval_mode if self.approval_mode is not None else defaults.approval_mode,
            max_events=self.max_events if self.max_events is not None else defaults.max_events,
            max_seconds=self.max_seconds if self.max_seconds is not None else defaults.max_seconds,
            cli_path=self.cli_path if self.cli_path is not None else defaults.cli_path,
            extra_cli_config={
                **(defaults.extra_cli_config or {}),
                **(self.extra_cli_config or {}),
            },
            eval_prompt=self.eval_prompt if self.eval_prompt is not None else defaults.eval_prompt,
            weight=self.weight,
            enabled=self.enabled,
        )

    def to_agentic_evaluator_config(self) -> "AgenticEvaluatorConfig":
        """Convert to AgenticEvaluatorConfig for runner compatibility."""
        return AgenticEvaluatorConfig(
            backend=self.backend,
            model=self.model,
            cli_profile=self.cli_profile,
            sandbox=self.sandbox or "workspace-write",
            approval_mode=self.approval_mode or "full-auto",
            max_events=self.max_events or 80,
            max_seconds=self.max_seconds or 0,
            cli_path=self.cli_path,
            extra_cli_config=self.extra_cli_config or {},
            eval_prompt=self.eval_prompt,
        )


@dataclass
class AggregationConfig:
    """Configuration for aggregating scores from multiple evaluators."""

    strategy: Literal[
        "best_score", "worst_case", "average",
        "weighted_average", "majority_vote", "median"
    ] = "average"

    # For majority_vote: fraction of evaluators that must agree for "correct"
    vote_threshold: float = 0.5

    # Minimum number of successful evaluations required for valid result
    min_successful_evals: int = 1

    # How to handle evaluator failures
    # "ignore" - skip failed evaluators in aggregation
    # "zero" - treat failed as score=0, correct=False
    # "fail" - fail entire evaluation if any required evaluator fails
    failure_mode: Literal["ignore", "zero", "fail"] = "ignore"

    def __post_init__(self):
        if self.vote_threshold < 0 or self.vote_threshold > 1:
            raise ValueError(f"vote_threshold must be in [0, 1], got {self.vote_threshold}")
        if self.min_successful_evals < 1:
            raise ValueError(f"min_successful_evals must be >= 1, got {self.min_successful_evals}")


@dataclass
class EnsembleEvaluatorConfig:
    """Configuration for multi-evaluator ensemble.

    Enables running multiple evaluators with different configurations
    in parallel, aggregating their results into a single score.
    """

    enabled: bool = False

    # Default settings inherited by all evaluators
    defaults: EvaluatorInstanceConfig = field(default_factory=lambda: EvaluatorInstanceConfig(
        backend="codex",
        sandbox="workspace-write",
        approval_mode="full-auto",
        max_events=80,
    ))

    # Named evaluators: {name: config}
    evaluators: Dict[str, EvaluatorInstanceConfig] = field(default_factory=dict)

    # Aggregation configuration
    aggregation: AggregationConfig = field(default_factory=AggregationConfig)

    # Maximum parallel evaluators per program (0 = no limit, use all)
    max_parallel_evaluators: int = 0

    def __post_init__(self):
        """Validate configuration."""
        if self.enabled and not self.evaluators:
            # Auto-create a default "primary" evaluator if none specified
            self.evaluators = {"primary": EvaluatorInstanceConfig(name="primary")}

        # Ensure all evaluators have names matching their keys
        for name, config in self.evaluators.items():
            if config.name != name:
                config.name = name

    def get_resolved_evaluators(self) -> Dict[str, EvaluatorInstanceConfig]:
        """Return evaluators with defaults merged in, filtering disabled ones."""
        return {
            name: config.merge_with_defaults(self.defaults)
            for name, config in self.evaluators.items()
            if config.enabled
        }


@dataclass
class EvaluatorConfig:
    """Evaluator selection and configuration.

    Modes:
    - auto: Agentic if agentic_mode=true, else legacy
    - legacy: Single-shot deterministic evaluator
    - agentic: Single agentic evaluator session
    - ensemble: Multiple evaluators with aggregation
    """

    mode: Literal["auto", "legacy", "agentic", "ensemble"] = "auto"
    agentic: AgenticEvaluatorConfig = field(default_factory=AgenticEvaluatorConfig)
    ensemble: EnsembleEvaluatorConfig = field(default_factory=EnsembleEvaluatorConfig)


@dataclass
class EvolutionConfig:
    task_sys_msg: Optional[str] = None
    patch_types: List[str] = field(default_factory=lambda: ["diff"])
    patch_type_probs: List[float] = field(default_factory=lambda: [1.0])
    num_generations: int = 10
    max_parallel_jobs: int = 2
    max_patch_resamples: int = 3
    max_patch_attempts: int = 5
    job_type: str = "local"
    language: str = "python"
    llm_models: List[str] = field(default_factory=lambda: ["azure-gpt-4.1-mini"])
    llm_dynamic_selection: Optional[Union[str, BanditBase]] = None
    llm_dynamic_selection_kwargs: dict = field(default_factory=lambda: {})
    llm_kwargs: dict = field(default_factory=lambda: {})
    meta_rec_interval: Optional[int] = None
    meta_llm_models: Optional[List[str]] = None
    meta_llm_kwargs: dict = field(default_factory=lambda: {})
    meta_max_recommendations: int = 5
    meta_backend: Optional[str] = None  # "same" | "codex" | "gemini" | "claude" | "shinka" | None
    embedding_model: Optional[str] = None
    embedding_include_globs: List[str] = field(default_factory=lambda: ["**/*"])
    embedding_exclude_globs: List[str] = field(
        default_factory=lambda: [
            "results/**",
            "workspace_snapshot/**",
            "agent_sessions/**",
            ".hydra/**",
            "__pycache__/**",
            "*.pyc",
            "*.pyo",
        ]
    )
    embedding_max_files: int = 500  # Increased default for larger codebases
    embedding_max_total_bytes: int = 2_000_000  # 2MB default
    embedding_max_bytes_per_file: int = 500_000  # 500KB per file
    cleanup_old_generations: bool = False  # Auto-cleanup old gen dirs to save disk
    cleanup_keep_last_n: int = 50  # Keep last N generations when cleanup enabled
    embedding_use_changed_files_first: bool = True
    init_program_path: Optional[str] = "initial.py"
    init_support_dir: Optional[str] = None
    # Agentic-only: explicit entrypoint file for deterministic evaluators.
    # If set, treated as relative to each generation directory unless absolute.
    agentic_entrypoint_path: Optional[str] = None
    results_dir: Optional[str] = None
    max_novelty_attempts: int = 3
    code_embed_sim_threshold: float = 0.85
    novelty_error_accepts: bool = False
    novelty_llm_models: Optional[List[str]] = None
    novelty_llm_kwargs: dict = field(default_factory=lambda: {})
    novelty_exclude_parent: bool = False  # If True, exclude parent from similarity comparison
    use_text_feedback: bool = False
    agentic_mode: bool = False
    agentic: AgenticConfig = field(default_factory=AgenticConfig)
    evaluator: EvaluatorConfig = field(default_factory=EvaluatorConfig)
    # Git-backed storage: Store each mutation as a real git commit
    # See docs/git_backed_evolution.md for details
    git_backed_storage: bool = False  # Opt-in, default off until fully validated
    git_repo_path: Optional[str] = None  # Override default location (results/<task>/<run>/evolution.git)
    max_score: float = 1.0  # Maximum possible score (defines the scale)
    # Stagnation detection: stop early if best score hasn't improved for N generations
    stagnation_generations: int = 20  # 0 = disabled


@dataclass
class PreparedEditJob:
    """Immutable inputs for a single agentic edit attempt.

    This is created on the main thread (after sampling parent/inspirations and
    prompts) and executed in a parallel worker. A job may be retried with
    different novelty/resample attempt counters.
    """

    generation: int
    parent_program: Program
    archive_programs: List[Program]
    top_k_programs: List[Program]
    patch_sys: str
    patch_msg: str
    patch_type: str
    generation_dir: Path
    results_dir: str
    novelty_attempt: int
    resample_attempt: int
    selected_backend: str
    bandit_summary: Optional[Dict[str, Any]] = None
    meta_recs: Optional[str] = None
    meta_summary: Optional[str] = None
    meta_scratch: Optional[str] = None


@dataclass
class EditWorkerResult:
    """Result of a parallel agentic edit worker.

    The worker is responsible for running the agentic edit session, materializing
    the new generation workspace on disk, building an embedding corpus, and
    computing the embedding vector. Novelty checks and DB writes remain on the
    main thread.
    """

    generation: int
    generation_dir: Path
    results_dir: str
    corpus_text: str
    embedding: List[float]
    embed_cost: float
    corpus_meta: dict
    meta_edit_data: dict
    code_diff: Optional[str]
    num_applied: int


@dataclass
class RunningJob:
    """Represents a running job in the queue."""

    job_id: Union[str, Popen, ProcessWithLogging, Future]
    exec_fname: str
    results_dir: str
    generation_dir: Path
    start_time: float
    generation: int
    parent_id: Optional[str]
    archive_insp_ids: List[str]
    top_k_insp_ids: List[str]
    code_diff: Optional[str]
    meta_patch_data: Optional[dict]
    code_embedding: List[float] = field(default_factory=list)
    embed_cost: float = 0.0
    novelty_cost: float = 0.0
    corpus_text: str = ""
    corpus_meta: dict = field(default_factory=dict)
    # Parallel agentic editing stage
    edit_future: Optional[Future] = None
    edit_result: Optional[EditWorkerResult] = None
    status: str = "evaluating"  # editing | awaiting_novelty | evaluating
    novelty_attempt: int = 1
    resample_attempt: int = 1
    api_costs_accumulated: float = 0.0
    embed_cost_accumulated: float = 0.0
    novelty_cost_accumulated: float = 0.0
    novelty_checks_performed: int = 0
    novelty_explanation: str = ""
    parent_program: Optional[Program] = None
    # For agentic parallel execution - stores the future and its result
    agentic_future: Optional[Future] = None
    agentic_result: Optional[Tuple[Dict[str, Any], float]] = None


# Set up logging
logger = logging.getLogger(__name__)


def cleanup_stale_scratch_dirs(scratch_base: Path, max_age_hours: float = 24.0) -> List[Path]:
    """Clean up scratch directories older than max_age_hours.

    Args:
        scratch_base: Base directory for scratch directories
        max_age_hours: Maximum age in hours before a directory is considered stale

    Returns:
        List of paths that were cleaned up
    """
    if not scratch_base.exists():
        return []

    import time
    current_time = time.time()
    max_age_seconds = max_age_hours * 3600
    cleaned: List[Path] = []

    for child in scratch_base.iterdir():
        if not child.is_dir():
            continue
        try:
            # Check modification time
            mtime = child.stat().st_mtime
            age_seconds = current_time - mtime
            if age_seconds > max_age_seconds:
                shutil.rmtree(child, ignore_errors=True)
                cleaned.append(child)
        except Exception:
            pass

    return cleaned


class EvolutionRunner:
    def __init__(
        self,
        evo_config: EvolutionConfig,
        job_config: JobConfig,
        db_config: DatabaseConfig,
        verbose: bool = True,
    ):
        self.evo_config = evo_config
        self.job_config = job_config
        self.db_config = db_config
        self.verbose = verbose

        print_gradient_logo((255, 0, 0), (255, 255, 255))
        if evo_config.results_dir is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.results_dir = f"results_{timestamp}"
        else:
            self.results_dir = Path(evo_config.results_dir)

        # Hydra writes its metadata to the CWD; copy it into the custom results
        # directory so evaluators (which only know about results_dir) can locate
        # `config.yaml` even when we override the run output path.
        hydra_run_dir = Path.cwd()
        hydra_dir = hydra_run_dir / ".hydra"
        target_hydra_dir = self.results_dir / ".hydra"
        if hydra_dir.exists() and hydra_run_dir != self.results_dir:
            target_hydra_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(hydra_dir, target_hydra_dir, dirs_exist_ok=True)

        # Write PID file for running status detection by visualization
        Path(self.results_dir).mkdir(parents=True, exist_ok=True)
        self._pid_file_path = Path(self.results_dir) / "shinka.pid"
        self._pid_file_path.write_text(str(os.getpid()))

        # Track active scratch directories for cleanup on exit/crash
        self._active_scratch_dirs: Set[Path] = set()
        self._scratch_dir_lock = threading.Lock()

        # Clean up stale scratch directories from previous crashed runs
        scratch_base = Path("/tmp/shinka_scratch")
        cleaned = cleanup_stale_scratch_dirs(scratch_base, max_age_hours=24.0)
        if cleaned:
            logger.info(f"Cleaned {len(cleaned)} stale scratch directories from previous runs")

        # Register cleanup handlers to remove PID file on exit
        atexit.register(self._cleanup_pid_file)
        atexit.register(self._shutdown_executors)
        atexit.register(self._cleanup_scratch_dirs)
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        if self.verbose:
            # Create log file path in results directory
            log_filename = f"{self.results_dir}/evolution_run.log"
            Path(self.results_dir).mkdir(parents=True, exist_ok=True)

            # Set up logging with both console and file handlers
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
                handlers=[
                    RichHandler(
                        show_time=False, show_level=False, show_path=False
                    ),  # Console output (clean)
                    logging.FileHandler(
                        log_filename, mode="a", encoding="utf-8"
                    ),  # File output (detailed)
                ],
            )

            # Also log the initial setup information
            logger.info("=" * 80)
            start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"Evolution run started at {start_time}")
            logger.info(f"Results directory: {self.results_dir}")
            logger.info(f"Log file: {log_filename}")
            logger.info("=" * 80)

        # Check if we are resuming a run
        resuming_run = False
        db_path = Path(f"{self.results_dir}/{db_config.db_path}")
        if self.evo_config.results_dir is not None and db_path.exists():
            resuming_run = True

        # Initialize LLM selection strategy
        if evo_config.llm_dynamic_selection is None:
            self.llm_selection = None
        elif isinstance(evo_config.llm_dynamic_selection, BanditBase):
            self.llm_selection = evo_config.llm_dynamic_selection
        elif (evo_config.llm_dynamic_selection.lower() == "ucb") or (
            evo_config.llm_dynamic_selection.lower() == "ucb1"
        ):
            self.llm_selection = AsymmetricUCB(
                arm_names=evo_config.llm_models,
                **evo_config.llm_dynamic_selection_kwargs,
            )
        else:
            raise ValueError("Invalid llm_dynamic_selection")

        # Initialize backend bandit for agentic mode (if enabled)
        if evo_config.agentic_mode and evo_config.agentic.bandit_selection:
            bandit_config = BackendBanditConfig(**evo_config.agentic.bandit_kwargs)
            self.backend_bandit: Optional[BackendBandit] = BackendBandit(
                config=bandit_config
            )
            # Pre-populate auth cache and log available backends
            available = self.backend_bandit.refresh_auth()
            if self.verbose:
                logger.info(
                    f"Backend bandit enabled with {len(available)} authenticated "
                    f"backends: {available}"
                )
            
            # Initialize global history if enabled
            self._bandit_history = None
            if evo_config.agentic.use_global_bandit_history or evo_config.agentic.record_to_global_history:
                try:
                    from shinka.llm.bandit_history import BanditHistory
                    self._bandit_history = BanditHistory.get_instance()
                    
                    # Load priors from global history if enabled
                    if evo_config.agentic.use_global_bandit_history:
                        priors = self._bandit_history.get_priors()
                        if priors:
                            # Apply priors to the bandit
                            for backend, prior_data in priors.items():
                                if backend in available:
                                    # Use successes/failures as warm start
                                    successes = prior_data.get("alpha", 1) - 1
                                    failures = prior_data.get("beta", 1) - 1
                                    if successes > 0 or failures > 0:
                                        # Warm start the UCB with historical data
                                        # Scale down to not overwhelm new data
                                        scale = min(1.0, 10.0 / (successes + failures + 1))
                                        arm_idx = self.backend_bandit._bandit.arm_names.index(backend)
                                        total_trials = int((successes + failures) * scale)
                                        # Set n_submitted to match n_completed for proper UCB exploration bonus
                                        self.backend_bandit._bandit.n_submitted[arm_idx] = total_trials
                                        self.backend_bandit._bandit.n_completed[arm_idx] = total_trials
                                        self.backend_bandit._bandit.s[arm_idx] = successes * scale
                            if self.verbose:
                                logger.info(
                                    f"Loaded global bandit priors for backends: {list(priors.keys())}"
                                )
                except Exception as e:
                    if self.verbose:
                        logger.warning(f"Failed to initialize bandit history: {e}")
                    self._bandit_history = None
        else:
            self.backend_bandit = None
            self._bandit_history = None

        # Initialize git manager for git-backed storage mode
        self.git_manager: Optional[EvolutionGitManager] = None
        self._initial_git_sha: Optional[str] = None  # SHA of generation 0 seed
        if evo_config.git_backed_storage:
            git_repo_path = (
                Path(evo_config.git_repo_path)
                if evo_config.git_repo_path
                else Path(self.results_dir) / "evolution.git"
            )
            self.git_manager = EvolutionGitManager(git_repo_path)
            if self.verbose:
                logger.info(f"Git-backed storage enabled at: {git_repo_path}")

        # Initialize database and scheduler
        db_config.db_path = str(db_path)
        # Use embedding model from config - None disables novelty-based selection
        embedding_model_to_use = evo_config.embedding_model
        self.db = ProgramDatabase(
            config=db_config, embedding_model=embedding_model_to_use
        )
        self.scheduler = JobScheduler(
            job_type=evo_config.job_type,
            config=job_config,  # type: ignore
            verbose=verbose,
        )

        self.evaluator_mode = self._resolve_evaluator_mode()

        # Initialize ensemble evaluator if in ensemble mode
        self.ensemble_evaluator: Optional[EnsembleEvaluator] = None
        if self.evaluator_mode == "ensemble":
            # Build agent runners for ensemble
            ensemble_runners = {
                "codex": run_codex_task,
                "gemini": run_gemini_task,
                "claude": run_claude_task,
                "shinka": run_shinka_task,
                "jules": run_jules_task,
            }
            self.ensemble_evaluator = EnsembleEvaluator(
                self.evo_config.evaluator.ensemble,
                agent_runners=ensemble_runners,
            )
            # Also initialize agentic_evaluator as fallback
            self.agentic_evaluator = None
            if self.verbose:
                num_evals = len(self.evo_config.evaluator.ensemble.get_resolved_evaluators())
                logger.info(
                    f"Ensemble evaluator initialized with {num_evals} evaluators, "
                    f"aggregation strategy: {self.evo_config.evaluator.ensemble.aggregation.strategy}"
                )
        elif self.evaluator_mode == "agentic":
            # Use evaluator-specific backend if set, else fall back to agentic backend
            eval_backend = (
                self.evo_config.evaluator.agentic.backend
                or self.evo_config.agentic.backend
            )
            if eval_backend == "gemini":
                runner_fn = run_gemini_task
            elif eval_backend == "claude":
                runner_fn = run_claude_task
            elif eval_backend == "shinka":
                runner_fn = run_shinka_task
            elif eval_backend == "jules":
                runner_fn = run_jules_task
            else:
                runner_fn = run_codex_task
            self.agentic_evaluator: Optional[AgenticEvaluator] = AgenticEvaluator(
                self.evo_config.evaluator.agentic,
                agent_runner=runner_fn,
            )
            if self.verbose:
                logger.info(f"Agentic evaluator using backend: {eval_backend}")
        else:
            self.agentic_evaluator = None

        self.agentic_eval_sessions_dir = (
            Path(self.results_dir) / "agentic_eval_sessions"
        )

        self.llm = LLMClient(
            model_names=evo_config.llm_models,
            model_selection=self.llm_selection,
            **evo_config.llm_kwargs,
            verbose=verbose,
        )
        if evo_config.embedding_model is not None:
            self.embedding = EmbeddingClient(
                model_name=evo_config.embedding_model,
                verbose=verbose,
            )
        else:
            self.embedding = None

        if evo_config.meta_llm_models is not None:
            self.meta_llm = LLMClient(
                model_names=evo_config.meta_llm_models,
                **evo_config.meta_llm_kwargs,
                verbose=verbose,
            )
        else:
            self.meta_llm = None

        if evo_config.novelty_llm_models is not None:
            self.novelty_llm = LLMClient(
                model_names=evo_config.novelty_llm_models,
                **evo_config.novelty_llm_kwargs,
                verbose=verbose,
            )
        else:
            self.novelty_llm = None

        # Initialize PromptSampler for handling LLM code prompts
        self.prompt_sampler = PromptSampler(
            task_sys_msg=evo_config.task_sys_msg,
            language=evo_config.language,
            patch_types=evo_config.patch_types,
            patch_type_probs=evo_config.patch_type_probs,
            use_text_feedback=evo_config.use_text_feedback,
            agentic_mode=evo_config.agentic_mode,
            max_score=evo_config.max_score,
        )

        # Initialize MetaSummarizer for meta-recommendations
        # In agentic mode, use CLI backend for meta queries instead of direct LLMClient
        meta_agent_runner = None
        if evo_config.agentic_mode:
            # Determine which backend to use for meta/scratchpad
            # If meta_backend is None, use the same as agentic editing
            meta_backend = evo_config.meta_backend or evo_config.agentic.backend

            if meta_backend == "gemini":
                meta_agent_runner = run_gemini_task
            elif meta_backend == "claude":
                meta_agent_runner = run_claude_task
            elif meta_backend == "shinka":
                meta_agent_runner = run_shinka_task
            elif meta_backend == "jules":
                meta_agent_runner = run_jules_task
            else:
                meta_agent_runner = run_codex_task
            logger.info(
                f"MetaSummarizer using agent_runner ({meta_backend}) for agentic mode"
            )

        self.meta_summarizer = MetaSummarizer(
            meta_llm_client=self.meta_llm,
            agent_runner=meta_agent_runner,
            results_dir=Path(self.results_dir) if self.results_dir else None,
            language=evo_config.language,
            use_text_feedback=evo_config.use_text_feedback,
            max_recommendations=evo_config.meta_max_recommendations,
            agentic_mode=evo_config.agentic_mode,
        )

        # Initialize NoveltyJudge for novelty assessment
        # In agentic mode, pass agent_runner so LLM novelty check uses CLI backend
        novelty_agent_config = None
        if evo_config.agentic_mode and meta_agent_runner:
            # Create a minimal config object for agent_runner calls
            novelty_agent_config = evo_config.agentic

        self.novelty_judge = NoveltyJudge(
            novelty_llm_client=self.novelty_llm,
            language=evo_config.language,
            similarity_threshold=evo_config.code_embed_sim_threshold,
            max_novelty_attempts=evo_config.max_novelty_attempts,
            agentic_mode=evo_config.agentic_mode,
            agent_runner=meta_agent_runner,
            agent_config=novelty_agent_config,
            code_loader=(
                (lambda program: program.get_code_content(self.git_manager))
                if self.git_manager is not None
                else None
            ),
            error_accepts=evo_config.novelty_error_accepts,
            exclude_parent=evo_config.novelty_exclude_parent,
        )

        # Initialize rich console for formatted output
        self.console = Console()

        if self.evo_config.language == "cuda":
            self.lang_ext = "cu"
        elif self.evo_config.language == "cpp":
            self.lang_ext = "cpp"
        elif self.evo_config.language == "python":
            self.lang_ext = "py"
        elif self.evo_config.language == "rust":
            self.lang_ext = "rs"
        elif self.evo_config.language == "html":
            self.lang_ext = "html"
        else:
            msg = f"Language {self.evo_config.language} not supported"
            raise ValueError(msg)

        # Queue for managing parallel jobs (protected by _jobs_lock for thread safety)
        self.running_jobs: List[RunningJob] = []
        self._jobs_lock = threading.Lock()
        self.best_program_id: Optional[str] = None
        self.next_generation_to_submit = 0

        # Stagnation tracking: stop early if score doesn't improve
        self._stagnation_counter: int = 0
        self._best_score_seen: float = float("-inf")
        
        # ThreadPoolExecutors for parallel agentic editing and evaluation.
        # Edits are now executed in parallel workers; evaluation parallelism
        # remains bounded by the same max_parallel_jobs knob.
        self._agentic_edit_executor: Optional[ThreadPoolExecutor] = None
        self._agentic_executor: Optional[ThreadPoolExecutor] = None
        if self.evo_config.agentic_mode:
            max_workers = max(1, self.evo_config.max_parallel_jobs)
            self._agentic_edit_executor = ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix="agentic_edit_worker",
            )
            self._agentic_executor = ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix="agentic_eval_worker",
            )
            logger.info(
                f"Initialized agentic edit+eval executors with {max_workers} workers each"
            )

        # Check if db exists but is empty (failed previous run)
        # Note: last_iteration defaults to 0, so we need to check actual program count
        if resuming_run and self.db._count_programs_in_db() == 0:
            logger.warning(
                "Database exists but is empty (previous run may have failed). "
                "Cleaning up and starting fresh."
            )
            # Clean up any partial generation directories from the failed run
            results_path = Path(self.results_dir)
            for gen_dir in results_path.glob("gen_*"):
                if gen_dir.is_dir():
                    logger.info(f"Removing partial generation directory: {gen_dir}")
                    shutil.rmtree(gen_dir, ignore_errors=True)
            # Also remove any partial session directories
            for sessions_dir in results_path.glob("*_sessions"):
                if sessions_dir.is_dir():
                    logger.info(f"Removing partial sessions directory: {sessions_dir}")
                    shutil.rmtree(sessions_dir, ignore_errors=True)
            resuming_run = False

        if resuming_run:
            self.completed_generations = self.db.last_iteration + 1
            self.next_generation_to_submit = self.completed_generations
            logger.info("=" * 80)
            logger.info("RESUMING PREVIOUS EVOLUTION RUN")
            logger.info("=" * 80)
            logger.info(
                f"Resuming evolution from: {self.results_dir}\n"
                f"Found {self.completed_generations} "
                "previously completed generations."
            )
            logger.info("=" * 80)
            self._update_best_solution()
            # Restore meta memory state when resuming
            self._restore_meta_memory()
        else:
            self.completed_generations = 0

        # Save experiment configuration to a YAML file
        self._save_experiment_config(evo_config, job_config, db_config)

    def _cleanup_pid_file(self) -> None:
        """Remove the PID file on clean exit."""
        if hasattr(self, '_pid_file_path') and self._pid_file_path.exists():
            try:
                self._pid_file_path.unlink()
            except OSError:
                pass  # Ignore errors during cleanup

    def _shutdown_executors(self) -> None:
        """Best-effort shutdown of thread pools.

        Pytest can hang if ThreadPoolExecutor worker threads stay alive after
        tests finish. We shutdown non-blockingly on interpreter exit.
        """
        for executor in (getattr(self, "_agentic_edit_executor", None), getattr(self, "_agentic_executor", None)):
            if executor is None:
                continue
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:  # pragma: no cover - older Python
                executor.shutdown(wait=False)
            except Exception as e:
                logger.debug(f"Error shutting down executor (ignored): {e}")

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle SIGTERM/SIGINT by cleaning up PID file and exiting."""
        self._cleanup_pid_file()
        self._shutdown_executors()
        self._cleanup_scratch_dirs()
        # Re-raise the signal with default handler to ensure proper exit
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    def _cleanup_scratch_dirs(self) -> None:
        """Clean up all tracked scratch directories."""
        with self._scratch_dir_lock:
            for d in list(self._active_scratch_dirs):
                if d.exists():
                    try:
                        shutil.rmtree(d, ignore_errors=True)
                    except Exception as e:
                        logger.debug(f"Error cleaning up scratch dir {d} (ignored): {e}")

    def _register_scratch_dir(self, path: Path) -> None:
        """Register a scratch directory for cleanup on exit."""
        with self._scratch_dir_lock:
            self._active_scratch_dirs.add(path)

    def _unregister_scratch_dir(self, path: Path) -> None:
        """Unregister a scratch directory (already cleaned up)."""
        with self._scratch_dir_lock:
            self._active_scratch_dirs.discard(path)

    def _check_stagnation(self, current_best_score: float) -> bool:
        """Check if evolution has stagnated (no improvement for N generations).

        Returns True if we should stop early due to stagnation.
        Updates internal tracking state.
        """
        # Disabled if stagnation_generations == 0
        if self.evo_config.stagnation_generations <= 0:
            return False

        if current_best_score > self._best_score_seen:
            # Improvement! Reset counter
            self._best_score_seen = current_best_score
            self._stagnation_counter = 0
            return False
        else:
            # No improvement
            self._stagnation_counter += 1
            if self._stagnation_counter >= self.evo_config.stagnation_generations:
                return True
            return False

    def _save_experiment_config(
        self,
        evo_config: EvolutionConfig,
        job_config: JobConfig,
        db_config: DatabaseConfig,
    ) -> None:
        """Save experiment configuration to a YAML file."""
        config_data = {
            "evolution_config": asdict(evo_config),
            "job_config": asdict(job_config),
            "database_config": asdict(db_config),
            "timestamp": datetime.now().isoformat(),
            "results_directory": str(self.results_dir),
        }

        config_path = Path(self.results_dir) / "experiment_config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        with config_path.open("w", encoding="utf-8") as f:
            yaml.dump(config_data, f, default_flow_style=False, indent=2)

        logger.info(f"Experiment configuration saved to {config_path}")

    def _running_jobs_count(self) -> int:
        """Thread-safe read of running jobs count."""
        with self._jobs_lock:
            return len(self.running_jobs)

    def run(self):
        """Run evolution with parallel job queue."""
        max_jobs = self.evo_config.max_parallel_jobs
        target_gens = self.evo_config.num_generations
        logger.info(
            f"Starting evolution with {max_jobs} parallel jobs, "
            f"target: {target_gens} generations"
        )

        try:
            # First, run generation 0 sequentially to populate the database
            if self.completed_generations == 0 and target_gens > 0:
                logger.info("Running generation 0 sequentially to initialize database...")
                self._run_generation_0()
                # Validate that generation 0 actually produced programs
                gen0_programs = self.db.get_programs_by_generation(0)
                if not gen0_programs:
                    raise RuntimeError(
                        "Generation 0 initialization failed: no programs were added to the database. "
                        "Check initial program path and evaluator configuration."
                    )
                self.completed_generations = 1
                self.next_generation_to_submit = 1
                logger.info(f"Completed generation 0, total: 1/{target_gens}")

            # Now start parallel execution for remaining generations
            if self.completed_generations < target_gens:
                logger.info("Starting parallel execution for remaining generations...")

                # Main loop: monitor jobs and submit new ones
                while (
                    self.completed_generations < target_gens or self._running_jobs_count() > 0
                ):
                    # Check for completed jobs
                    completed_edits, completed_jobs = self._check_completed_jobs()

                    # Process completed agentic edits first (may enqueue eval or resubmit edits)
                    if completed_edits:
                        for job in completed_edits:
                            self._process_completed_edit_job(job)

                    # Process completed evaluations
                    if completed_jobs:
                        for job in completed_jobs:
                            self._process_completed_job(job)

                        # Update completed generations count
                        self._update_completed_generations()

                        if self.verbose:
                            logger.info(
                                f"Processed {len(completed_jobs)} eval jobs. "
                                f"Total completed generations: "
                                f"{self.completed_generations}/{target_gens}"
                            )

                        # Check for stagnation after processing jobs
                        best_program = self.db.get_best_program()
                        if best_program and best_program.combined_score is not None:
                            if self._check_stagnation(best_program.combined_score):
                                logger.warning(
                                    f"Evolution stagnated after {self._stagnation_counter} "
                                    f"generations without improvement. Stopping early."
                                )
                                break

                    # Check if we've completed all generations
                    if self.completed_generations >= target_gens:
                        logger.info("All generations completed, exiting...")
                        break

                    # Submit new jobs to fill the queue (only if we have capacity)
                    if (
                        self._running_jobs_count() < max_jobs
                        and self.next_generation_to_submit < target_gens
                    ):
                        self._submit_new_job()

                    # Wait a bit before checking again
                    time.sleep(2)

                # All jobs are now handled by the main loop above

        except Exception as e:
            logger.error(f"Evolution failed with exception: {e}", exc_info=True)
            raise
        finally:
            # Always shutdown executors, even on exception
            try:
                self._shutdown_executors_safe()
            except Exception as e:
                logger.warning(f"Error during executor shutdown (ignored): {e}")

        # Post-run summary (only executes on successful completion)
        best_program = self.db.get_best_program()
        self.meta_summarizer.perform_final_summary(str(self.results_dir), best_program)

        # Save final meta memory state
        self._save_meta_memory()

        self.db.print_summary()
        logger.info(f"Evolution completed! {self.completed_generations} generations")
        logger.info("=" * 80)
        end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"Evolution run ended at {end_time}")
        logger.info("=" * 80)

    def _shutdown_executors_safe(self) -> None:
        """Shutdown all executors safely, even on exception."""
        # Use wait=False to avoid blocking on exception
        if self._agentic_edit_executor is not None:
            logger.info("Shutting down agentic edit executor...")
            self._agentic_edit_executor.shutdown(wait=False)
            self._agentic_edit_executor = None
        if self._agentic_executor is not None:
            logger.info("Shutting down agentic eval executor...")
            self._agentic_executor.shutdown(wait=False)
            self._agentic_executor = None
        if self.scheduler is not None:
            logger.info("Shutting down job scheduler...")
            self.scheduler.shutdown()

    def generate_initial_program(self):
        """Generate initial program with LLM, with retries."""
        llm_kwargs = self.llm.get_kwargs()

        sys_msg, user_msg = self.prompt_sampler.initial_program_prompt()
        msg_history = []
        total_costs = 0.0

        for attempt in range(self.evo_config.max_patch_attempts):
            response = self.llm.query(
                msg=user_msg,
                system_msg=sys_msg,
                llm_kwargs=llm_kwargs,
                msg_history=msg_history,
            )
            if response is None or response.content is None:
                if self.verbose:
                    logger.info(
                        f"  INITIAL PROGRAM ATTEMPT {attempt + 1}/"
                        f"{self.evo_config.max_patch_attempts} "
                        "FAILURE. Error: LLM response content was None."
                    )
                if attempt < self.evo_config.max_patch_attempts - 1:
                    user_msg = (
                        "The previous response was empty. Please try again "
                        "and provide the full code."
                    )
                    if response and response.new_msg_history:
                        msg_history = response.new_msg_history
                    continue
                else:
                    break

            total_costs += response.cost or 0
            initial_code = extract_between(
                response.content,
                f"```{self.evo_config.language}",
                "```",
                False,
            )

            if initial_code:
                patch_name = extract_between(
                    response.content, "<NAME>", "</NAME>", False
                )
                patch_description = extract_between(
                    response.content, "<DESCRIPTION>", "</DESCRIPTION>", False
                )
                if self.evo_config.language == "python":
                    comment_char = "#"
                else:
                    comment_char = "//"

                initial_code = (
                    f"{comment_char} EVOLVE-BLOCK-START\n"
                    f"{initial_code}\n"
                    f"{comment_char} EVOLVE-BLOCK-END\n"
                )

                if self.verbose:
                    logger.info(
                        f"  INITIAL PROGRAM ATTEMPT {attempt + 1}/"
                        f"{self.evo_config.max_patch_attempts} "
                        "SUCCESS."
                    )
                return initial_code, patch_name, patch_description, total_costs
            else:  # code extraction failed
                if self.verbose:
                    logger.info(
                        f"  INITIAL PROGRAM ATTEMPT {attempt + 1}/"
                        f"{self.evo_config.max_patch_attempts} "
                        "FAILURE. Error: Could not extract code from response."
                    )
                if attempt < self.evo_config.max_patch_attempts - 1:
                    user_msg = (
                        "Could not extract code from your last response. "
                        "Please make sure to enclose the code in "
                        "`<CODE>`...`</CODE>` tags."
                    )
                    msg_history = response.new_msg_history
                else:  # last attempt
                    break

        raise ValueError(
            "LLM failed to generate a valid initial program after "
            f"{self.evo_config.max_patch_attempts} attempts."
        )

    def _run_generation_0(self):
        """Setup and run generation 0 to initialize the database."""
        initial_dir = f"{self.results_dir}/{FOLDER_PREFIX}_0"
        initial_dir_path = Path(initial_dir)
        initial_dir_path.mkdir(parents=True, exist_ok=True)

        # In agentic mode, the "program" is the entire gen_0 workspace directory.
        # Legacy mode continues to use main.{lang_ext}.
        if self.evo_config.agentic_mode:
            exec_fname = str(initial_dir_path)
            legacy_exec_path = initial_dir_path / f"main.{self.lang_ext}"
        else:
            legacy_exec_path = initial_dir_path / f"main.{self.lang_ext}"
            exec_fname = str(legacy_exec_path)
        results_dir = f"{self.results_dir}/{FOLDER_PREFIX}_0/results"

        api_costs = 0.0
        patch_name = "initial_program"
        patch_description = "Initial program from file."
        patch_type = "init"

        # In agentic mode, if the specified init file is missing, treat it as not provided
        # (fallback to empty seed) rather than crashing.
        init_path_str = self.evo_config.init_program_path
        if init_path_str and self.evo_config.agentic_mode and not Path(init_path_str).exists():
            if self.verbose:
                logger.warning(
                    f"Initial program '{init_path_str}' not found. "
                    "Agentic mode enabled - falling back to empty seed."
                )
            init_path_str = None

        if init_path_str:
            if self.verbose:
                logger.info(
                    f"Copying initial program from {init_path_str}"
                )
            init_path = Path(init_path_str)

            if init_path.is_dir():
                # For directories (agentic mode on full codebase), copy entire tree
                # but exclude results directory to avoid recursive copying
                if self.verbose:
                    logger.info(f"init_program_path is a directory, copying tree to {initial_dir}")
                # Clean the empty gen_0 dir before CoW clone so fast_copy can preserve reflinks.
                if initial_dir_path.exists():
                    shutil.rmtree(initial_dir_path)
                self._fast_copy_workspace_tree(
                    init_path,
                    initial_dir_path,
                    dirs_exist_ok=False,
                )
            else:
                # For single files:
                # - Legacy mode: copy into main.{lang_ext}
                # - Agentic mode: preserve the original filename in the workspace
                if self.evo_config.agentic_mode:
                    target = initial_dir_path / init_path.name
                    shutil.copy(init_path, target)
                else:
                    shutil.copy(init_path, legacy_exec_path)
                if self.evo_config.init_support_dir:
                    support_root = Path(self.evo_config.init_support_dir)
                    self._copy_support_tree(
                        support_root,
                        initial_dir_path,
                        exclude_file=init_path,
                    )
        else:
            if self.evo_config.agentic_mode:
                # Agentic open-ended: create seed marker file, skip evaluation.
                # The agentic editor in gen_1 will create real code from scratch.
                # For Jules backend, the seed marker signals to preserve GitHub repo content.
                if self.verbose:
                    logger.info("`init_program_path` not provided; creating seed marker for agentic run (no gen_0 eval).")
                # Create seed marker file instead of empty main.py
                seed_marker_path = initial_dir_path / "SHINKA_SEED.txt"
                seed_marker_content = (
                    "# Shinka Evolution Seed Marker\n\n"
                    "This file indicates an empty seed for open-ended evolution.\n"
                    "The agent should work on the existing codebase as it sees fit.\n\n"
                    "This marker file can be safely ignored or deleted.\n"
                )
                with open(seed_marker_path, "w", encoding="utf-8") as f:
                    f.write(seed_marker_content)
                api_costs = 0.0
                patch_name = "empty_seed"
                patch_description = "Empty seed for agentic open-ended evolution."

                # Skip evaluation for empty seed - insert directly into DB
                initial_corpus = self._build_embedding_corpus(initial_dir_path, {})
                code_embedding, e_cost = self.get_code_embedding(initial_corpus.text)

                corpus_meta = {
                    "embedding_corpus_meta": {
                        "included_files": initial_corpus.included_files,
                        "skipped_files": initial_corpus.skipped_files,
                        "binary_files": initial_corpus.binary_files,
                        "truncated": initial_corpus.truncated,
                        "total_bytes": initial_corpus.total_bytes,
                    }
                }

                # Pre-generate program ID for git ref
                program_id = str(uuid.uuid4())

                # Initialize git-backed storage with seed files
                git_commit_sha = None
                if self.git_manager is not None:
                    try:
                        git_commit_sha = self.git_manager.init_from_workspace(
                            workspace_path=initial_dir_path,
                            message="Initial seed (empty)",
                            node_uuid=program_id,
                        )
                        self._initial_git_sha = git_commit_sha
                        if self.verbose:
                            logger.info(f"Git seed commit (empty): {git_commit_sha[:8]}")
                    except Exception as e:
                        logger.warning(f"Failed to initialize git-backed storage: {e}")

                db_program = Program(
                    id=program_id,
                    code=initial_corpus.text,
                    language=self.evo_config.language,
                    parent_id=None,
                    generation=0,
                    archive_inspiration_ids=[],
                    top_k_inspiration_ids=[],
                    code_diff=None,
                    embedding=code_embedding,
                    correct=False,  # Not evaluated
                    combined_score=0.0,  # Will be scored in gen_1
                    public_metrics={},
                    private_metrics={},
                    text_feedback="Empty seed - awaiting agentic creation in gen_1",
                    metadata={
                        "compute_time": 0.0,
                        "api_costs": api_costs,
                        "embed_cost": e_cost,
                        "novelty_cost": 0.0,
                        "patch_type": patch_type,
                        "patch_name": patch_name,
                        "patch_description": patch_description,
                        "stdout_log": "",
                        "stderr_log": "",
                        "evaluator_mode": self.evaluator_mode,
                        "skipped_gen0_eval": True,
                        **({"git_commit_sha": git_commit_sha} if git_commit_sha else {}),
                        **corpus_meta,
                    },
                )
                self.db.add(db_program, verbose=self.verbose)
                # Note: Not calling meta_summarizer.add_evaluated_program since we're skipping eval
                if self.verbose:
                    logger.info(f"Empty seed inserted into DB (id={db_program.id[:8]}...), skipping gen_0 evaluation.")
                return  # Exit early - no evaluation needed
            else:
                if self.verbose:
                    logger.info(
                        "`init_program_path` not provided, "
                        "generating initial program with LLM..."
                    )
                initial_code, patch_name, patch_description, api_costs = (
                    self.generate_initial_program()
                )
                with open(legacy_exec_path, "w", encoding="utf-8") as f:
                    f.write(initial_code)

                if self.verbose:
                    logger.info(f"Initial program generated and saved to {exec_fname}")

        # Ensure results directory exists for evaluation paths.
        Path(results_dir).mkdir(parents=True, exist_ok=True)

        if self.evaluator_mode == "agentic":
            results, rtime = self._run_agentic_evaluation(
                exec_fname=exec_fname,
                results_dir=results_dir,
                generation_dir=Path(initial_dir),
                generation=0,
                parent_id=None,
            )
        else:
            results, rtime = self.scheduler.run(exec_fname, results_dir)

        initial_corpus = self._build_embedding_corpus(Path(initial_dir), {})
        code_embedding, e_cost = self.get_code_embedding(initial_corpus.text)
        embed_cost = e_cost
        corpus = initial_corpus

        correct_val = False
        metrics_val = {}
        stdout_log = ""
        stderr_log = ""
        if results:
            correct_val = results.get("correct", {}).get("correct", False)
            metrics_val = results.get("metrics", {})
            stdout_log = results.get("stdout_log", "")
            stderr_log = results.get("stderr_log", "")

        agentic_eval_meta = results.get("agentic_eval") if results else None
        ensemble_eval_meta = results.get("ensemble_evaluation") if results else None
        combined_score = metrics_val.get("combined_score", 0.0)
        public_metrics = metrics_val.get("public", {})
        private_metrics = metrics_val.get("private", {})
        text_feedback = metrics_val.get("text_feedback", "")

        # Add the program to the database
        corpus_meta = {
            "embedding_corpus_meta": {
                "included_files": initial_corpus.included_files,
                "skipped_files": initial_corpus.skipped_files,
                "binary_files": initial_corpus.binary_files,
                "truncated": initial_corpus.truncated,
                "total_bytes": initial_corpus.total_bytes,
            }
        }

        # Pre-generate program ID for git ref
        program_id = str(uuid.uuid4())

        # Initialize git-backed storage with seed files
        git_commit_sha = None
        if self.git_manager is not None:
            try:
                git_commit_sha = self.git_manager.init_from_workspace(
                    workspace_path=Path(initial_dir),
                    message="Initial seed",
                    node_uuid=program_id,
                )
                self._initial_git_sha = git_commit_sha
                if self.verbose:
                    logger.info(f"Git seed commit: {git_commit_sha[:8]}")
            except Exception as e:
                logger.warning(f"Failed to initialize git-backed storage: {e}")

        db_program = Program(
            id=program_id,
            code=initial_corpus.text,
            language=self.evo_config.language,
            parent_id=None,
            generation=0,
            archive_inspiration_ids=[],
            top_k_inspiration_ids=[],
            code_diff=None,
            embedding=code_embedding,
            correct=correct_val,
            combined_score=combined_score,
            public_metrics=public_metrics,
            private_metrics=private_metrics,
            text_feedback=text_feedback,
            metadata={
                "compute_time": rtime,
                "api_costs": api_costs,
                "embed_cost": e_cost,
                "novelty_cost": 0.0,  # No novelty cost for generation 0
                "patch_type": patch_type,
                "patch_name": patch_name,
                "patch_description": patch_description,
                "stdout_log": stdout_log,
                "stderr_log": stderr_log,
                "evaluator_mode": self.evaluator_mode,
                **({"git_commit_sha": git_commit_sha} if git_commit_sha else {}),
                **corpus_meta,
            },
        )
        if agentic_eval_meta:
            if db_program.metadata is None:
                db_program.metadata = {}
            db_program.metadata["agentic_evaluator"] = agentic_eval_meta
        if ensemble_eval_meta:
            if db_program.metadata is None:
                db_program.metadata = {}
            db_program.metadata["ensemble_evaluation"] = ensemble_eval_meta

        self.db.add(db_program, verbose=True)
        if self.llm_selection is not None:
            self.llm_selection.set_baseline_score(
                db_program.combined_score if correct_val else 0.0,
            )
        self.db.save()
        self._update_best_solution()

        # Add the evaluated program to meta memory tracking
        self.meta_summarizer.add_evaluated_program(db_program)

        # Check if we should update meta memory after adding this program
        if self.meta_summarizer.should_update_meta(self.evo_config.meta_rec_interval):
            logger.info(
                f"Updating meta memory after processing "
                f"{len(self.meta_summarizer.evaluated_since_last_meta)} programs..."
            )
            best_program = self.db.get_best_program()
            updated_recs, meta_cost = self.meta_summarizer.update_meta_memory(
                best_program
            )
            if updated_recs:
                # Write meta output file for generation 0
                self.meta_summarizer.write_meta_output(str(self.results_dir))
                # Store meta cost for tracking
                if meta_cost > 0:
                    logger.info(
                        f"Meta recommendation generation cost: ${meta_cost:.4f}"
                    )
                    # Add meta cost to this program's metadata (the one that triggered the update)
                    if db_program.metadata is None:
                        db_program.metadata = {}
                    db_program.metadata["meta_cost"] = meta_cost
                    # Update the program in the database with the new metadata
                    self.db.update_program_metadata(db_program.id, db_program.metadata)

        # Save meta memory state after each job completion
        self._save_meta_memory()

    def _update_completed_generations(self):
        """
        Update the count of completed generations from the database.
        A generation `g` is considered complete if all generations from 0..g
        have at least one program in the database. This ensures the count
        advances sequentially without gaps.
        """
        last_gen = self.db.last_iteration
        if last_gen == -1:
            self.completed_generations = 0
            return

        # Check for contiguous generations from 0 up to last_gen
        completed_up_to = 0
        for i in range(last_gen + 1):
            if self.db.get_programs_by_generation(i):
                completed_up_to = i + 1
            else:
                # Found a gap, so contiguous sequence is broken
                self.completed_generations = completed_up_to
                return

        self.completed_generations = completed_up_to

    def _prepare_agentic_edit_job(
        self,
        *,
        generation: int,
        novelty_attempt: int,
        resample_attempt: int,
    ) -> PreparedEditJob:
        """Prepare an agentic edit attempt on the main thread.

        This samples the parent/inspirations, captures current meta context,
        builds prompts, and selects a backend (bandit or config). The resulting
        PreparedEditJob is safe to pass to a parallel worker.
        """

        results_dir = f"{self.results_dir}/{FOLDER_PREFIX}_{generation}/results"
        Path(results_dir).mkdir(parents=True, exist_ok=True)
        generation_dir = Path(self.results_dir) / f"{FOLDER_PREFIX}_{generation}"
        generation_dir.mkdir(parents=True, exist_ok=True)

        (
            parent_program,
            archive_programs,
            top_k_programs,
        ) = self.db.sample(
            target_generation=generation,
            novelty_attempt=novelty_attempt,
            max_novelty_attempts=self.evo_config.max_novelty_attempts,
            resample_attempt=resample_attempt,
            max_resample_attempts=self.evo_config.max_patch_resamples,
        )

        meta_recs, meta_summary, meta_scratch = self.meta_summarizer.get_current()

        patch_sys, patch_msg, patch_type = self.prompt_sampler.sample(
            parent=parent_program,
            archive_inspirations=archive_programs,
            top_k_inspirations=top_k_programs,
            meta_recommendations=meta_recs,
        )

        selected_backend = self.evo_config.agentic.backend
        bandit_summary = None
        if self.backend_bandit is not None:
            try:
                selected_backend = self.backend_bandit.sample()
                bandit_summary = self.backend_bandit.get_summary()
                if self.verbose:
                    posteriors = bandit_summary.get("posteriors", {}) if bandit_summary else {}
                    logger.info(
                        f"Backend bandit selected '{selected_backend}' "
                        f"(posteriors: {posteriors})"
                    )
            except NoAuthenticatedBackendsError as e:
                if self.verbose:
                    logger.warning(
                        f"Backend bandit could not sample backend: {e}. "
                        f"Falling back to configured backend '{selected_backend}'."
                    )

        return PreparedEditJob(
            generation=generation,
            parent_program=parent_program,
            archive_programs=archive_programs,
            top_k_programs=top_k_programs,
            patch_sys=patch_sys,
            patch_msg=patch_msg,
            patch_type=patch_type,
            generation_dir=generation_dir,
            results_dir=results_dir,
            novelty_attempt=novelty_attempt,
            resample_attempt=resample_attempt,
            selected_backend=selected_backend,
            bandit_summary=bandit_summary,
            meta_recs=meta_recs,
            meta_summary=meta_summary,
            meta_scratch=meta_scratch,
        )

    def _run_agentic_patch_worker(
        self,
        prepared_job: PreparedEditJob,
    ) -> EditWorkerResult:
        """Parallel worker entrypoint for agentic editing.

        Runs the agentic edit session, materializes generation workspace, builds
        embedding corpus, and computes embeddings. Returns a structured result
        for the main thread to novelty-check and enqueue evaluation.
        """

        code_diff, meta_edit_data, num_applied = self._run_agentic_patch(
            parent_program=prepared_job.parent_program,
            generation=prepared_job.generation,
            patch_sys=prepared_job.patch_sys,
            patch_msg=prepared_job.patch_msg,
            patch_type=prepared_job.patch_type,
            novelty_attempt=prepared_job.novelty_attempt,
            resample_attempt=prepared_job.resample_attempt,
            selected_backend_override=prepared_job.selected_backend,
            bandit_summary_override=prepared_job.bandit_summary,
        )

        # Persist current meta recommendations into metadata for auditing/UI.
        if prepared_job.meta_recs is not None:
            meta_edit_data["meta_recommendations"] = prepared_job.meta_recs
            meta_edit_data["meta_summary"] = prepared_job.meta_summary
            meta_edit_data["meta_scratch_pad"] = prepared_job.meta_scratch

        corpus_meta: dict = {}
        corpus_text = ""
        embedding: List[float] = []
        embed_cost = 0.0
        try:
            corpus = self._build_embedding_corpus(
                prepared_job.generation_dir, meta_edit_data
            )
            corpus_text = corpus.text
            embedding, embed_cost = self.get_code_embedding(corpus_text)
            corpus_meta = {
                "embedding_corpus_meta": {
                    "included_files": corpus.included_files,
                    "skipped_files": corpus.skipped_files,
                    "binary_files": corpus.binary_files,
                    "truncated": corpus.truncated,
                    "total_bytes": corpus.total_bytes,
                }
            }
            meta_edit_data.update(corpus_meta)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(
                f"Failed to build embedding corpus for gen {prepared_job.generation}: {e}"
            )

        return EditWorkerResult(
            generation=prepared_job.generation,
            generation_dir=prepared_job.generation_dir,
            results_dir=prepared_job.results_dir,
            corpus_text=corpus_text,
            embedding=embedding,
            embed_cost=embed_cost,
            corpus_meta=corpus_meta,
            meta_edit_data=meta_edit_data,
            code_diff=code_diff,
            num_applied=num_applied,
        )

    def _submit_new_job(self):
        """Submit a new job to the queue.

        In agentic mode, patch generation is dispatched to a parallel edit worker.
        Novelty checks and DB writes remain on the main thread, and evaluation
        runs in parallel as before.
        """

        current_gen = self.next_generation_to_submit

        if current_gen >= self.evo_config.num_generations:
            return

        self.next_generation_to_submit += 1

        results_dir = f"{self.results_dir}/{FOLDER_PREFIX}_{current_gen}/results"
        Path(results_dir).mkdir(parents=True, exist_ok=True)
        generation_dir = Path(self.results_dir) / f"{FOLDER_PREFIX}_{current_gen}"
        generation_dir.mkdir(parents=True, exist_ok=True)

        # In agentic mode, enqueue a parallel edit attempt and return.
        if self.evo_config.agentic_mode:
            exec_fname = str(generation_dir)
            prepared_job = self._prepare_agentic_edit_job(
                generation=current_gen,
                novelty_attempt=1,
                resample_attempt=1,
            )

            edit_future: Optional[Future] = None
            edit_result: Optional[EditWorkerResult] = None
            if self._agentic_edit_executor is not None:
                edit_future = self._agentic_edit_executor.submit(
                    self._run_agentic_patch_worker,
                    prepared_job,
                )
            else:
                # Fallback to synchronous edit if no executor (should be rare).
                edit_result = self._run_agentic_patch_worker(prepared_job)

            running_job = RunningJob(
                job_id=f"agentic_edit_gen_{current_gen}",
                exec_fname=exec_fname,
                results_dir=prepared_job.results_dir,
                generation_dir=prepared_job.generation_dir,
                start_time=time.time(),
                generation=current_gen,
                parent_id=prepared_job.parent_program.id,
                archive_insp_ids=[p.id for p in prepared_job.archive_programs],
                top_k_insp_ids=[p.id for p in prepared_job.top_k_programs],
                code_diff=None,
                meta_patch_data=None,
                code_embedding=[],
                embed_cost=0.0,
                novelty_cost=0.0,
                corpus_text="",
                corpus_meta={},
                edit_future=edit_future,
                edit_result=edit_result,
                status="editing" if edit_future is not None else "awaiting_novelty",
                novelty_attempt=1,
                resample_attempt=1,
                api_costs_accumulated=0.0,
                embed_cost_accumulated=0.0,
                novelty_cost_accumulated=0.0,
                parent_program=prepared_job.parent_program,
            )

            with self._jobs_lock:
                self.running_jobs.append(running_job)
                queue_size = len(self.running_jobs)
            self._write_active_jobs_file()
            if self.verbose:
                logger.info(
                    f"Submitted agentic edit job for generation {current_gen}, "
                    f"queue size: {queue_size}"
                )
            return

        # Legacy (non-agentic) path: synchronous patching + novelty, then enqueue evaluation.
        exec_fname = (
            f"{self.results_dir}/{FOLDER_PREFIX}_{current_gen}/main.{self.lang_ext}"
        )

        # Get current meta-recommendations for this job
        meta_recs, meta_summary, meta_scratch = self.meta_summarizer.get_current()

        code_embedding: List[float] = []
        embed_cost = 0.0
        novelty_cost = 0.0
        novelty_checks_performed = 0
        novelty_explanation = ""
        corpus: Optional[EmbeddingCorpus] = None

        # Sample parent and inspiration programs
        if current_gen == 0:
            parent_id = None
            archive_insp_ids: List[str] = []
            top_k_insp_ids: List[str] = []
            code_diff = None
            meta_patch_data: dict = {}
            parent_program = None
        else:
            api_costs = 0.0
            embed_cost = 0.0
            novelty_cost = 0.0
            novelty_checks_performed = 0
            parent_program = None
            # Loop over novelty attempts
            for nov_attempt in range(self.evo_config.max_novelty_attempts):
                # Loop over patch resamples - including parents
                for resample in range(self.evo_config.max_patch_resamples):
                    (
                        parent_program,
                        archive_programs,
                        top_k_programs,
                    ) = self.db.sample(
                        target_generation=current_gen,
                        novelty_attempt=nov_attempt + 1,
                        max_novelty_attempts=self.evo_config.max_novelty_attempts,
                        resample_attempt=resample + 1,
                        max_resample_attempts=self.evo_config.max_patch_resamples,
                    )
                    archive_insp_ids = [p.id for p in archive_programs]
                    top_k_insp_ids = [p.id for p in top_k_programs]
                    parent_id = parent_program.id
                    # Run patch (until success with max attempts)
                    code_diff, meta_patch_data, num_applied_attempt = self.run_patch(
                        parent_program,
                        archive_programs,
                        top_k_programs,
                        current_gen,
                        novelty_attempt=nov_attempt + 1,
                        resample_attempt=resample + 1,
                    )
                    api_costs += meta_patch_data.get("api_costs", 0.0)
                    if (
                        meta_patch_data.get("error_attempt") is None
                        and num_applied_attempt > 0
                    ):
                        meta_patch_data["api_costs"] = api_costs
                        break

                # Build corpus and compute embedding for this generation attempt
                corpus = self._build_embedding_corpus(generation_dir, meta_patch_data)
                code_embedding, e_cost = self.get_code_embedding(corpus.text)
                embed_cost += e_cost

                if not code_embedding:
                    self.novelty_judge.log_novelty_skip_message("no embedding")
                    break

                # Use NoveltyJudge for novelty assessment with rejection sampling
                if parent_program and self.novelty_judge.should_check_novelty(
                    code_embedding, current_gen, parent_program, self.db
                ):
                    should_accept, novelty_metadata = (
                        self.novelty_judge.assess_novelty_with_rejection_sampling(
                            corpus.text, code_embedding, parent_program, self.db
                        )
                    )

                    # Update costs and metadata from novelty assessment
                    novelty_cost += novelty_metadata.get("novelty_total_cost", 0.0)
                    novelty_checks_performed = novelty_metadata.get(
                        "novelty_checks_performed", 0
                    )
                    novelty_explanation = novelty_metadata.get(
                        "novelty_explanation", ""
                    )

                    if should_accept:
                        break
                    # If not accepted, continue to next attempt (rejection sampling)
                else:
                    if not self.db.island_manager or not hasattr(
                        self.db.island_manager, "are_all_islands_initialized"
                    ):
                        self.novelty_judge.log_novelty_skip_message("no island manager")
                    elif not self.db.island_manager.are_all_islands_initialized():
                        self.novelty_judge.log_novelty_skip_message(
                            "not all islands initialized yet"
                        )
                    break

        exec_path = Path(exec_fname)
        if not exec_path.exists():
            logger.warning(
                "Skipping evaluation for generation %s: main file missing.",
                current_gen,
            )
            # Rewind submission pointer so we retry this generation on the next loop
            self.next_generation_to_submit = current_gen
            return

        # Add meta-recommendations/summary/scratchpad to meta_patch_data
        if meta_recs is not None:
            meta_patch_data["meta_recommendations"] = meta_recs
            meta_patch_data["meta_summary"] = meta_summary
            meta_patch_data["meta_scratch_pad"] = meta_scratch

        # Add novelty check information to meta_patch_data if any checks were performed
        if current_gen > 0 and novelty_checks_performed > 0:
            meta_patch_data["novelty_checks_performed"] = novelty_checks_performed
            meta_patch_data["novelty_cost"] = novelty_cost
            meta_patch_data["novelty_explanation"] = novelty_explanation

        # Ensure corpus exists (could be missing if novelty loop skipped)
        if corpus is None:
            corpus = self._build_embedding_corpus(generation_dir, meta_patch_data)

        corpus_meta = {
            "embedding_corpus_meta": {
                "included_files": corpus.included_files,
                "skipped_files": corpus.skipped_files,
                "binary_files": corpus.binary_files,
                "truncated": corpus.truncated,
                "total_bytes": corpus.total_bytes,
            }
        }
        meta_patch_data.update(corpus_meta)

        running_job = RunningJob(
            job_id="agentic" if self.evaluator_mode == "agentic" else "",
            exec_fname=exec_fname,
            results_dir=results_dir,
            generation_dir=generation_dir,
            start_time=time.time(),
            generation=current_gen,
            parent_id=parent_id,
            archive_insp_ids=archive_insp_ids,
            top_k_insp_ids=top_k_insp_ids,
            code_diff=code_diff,
            meta_patch_data=meta_patch_data,
            code_embedding=code_embedding,
            embed_cost=embed_cost,
            novelty_cost=novelty_cost,
            corpus_text=corpus.text,
            corpus_meta=corpus_meta,
        )

        if self.evaluator_mode == "agentic":
            # Submit agentic evaluation to thread pool for parallel execution
            if self._agentic_executor is not None:
                future = self._agentic_executor.submit(
                    self._run_agentic_evaluation,
                    exec_fname=exec_fname,
                    results_dir=results_dir,
                    generation_dir=generation_dir,
                    generation=current_gen,
                    parent_id=parent_id,
                )
                running_job.agentic_future = future
                running_job.job_id = f"agentic_gen_{current_gen}"
                with self._jobs_lock:
                    self.running_jobs.append(running_job)
                    queue_size = len(self.running_jobs)
                self._write_active_jobs_file()
                if self.verbose:
                    logger.info(
                        f"Submitted agentic job for generation {current_gen}, "
                        f"queue size: {queue_size}"
                    )
            else:
                # Fallback to synchronous execution if no executor
                results, rtime = self._run_agentic_evaluation(
                    exec_fname=exec_fname,
                    results_dir=results_dir,
                    generation_dir=generation_dir,
                    generation=current_gen,
                    parent_id=parent_id,
                )
                self._finalize_job(running_job, results, rtime)
                self._update_completed_generations()
        else:
            job_id = self.scheduler.submit_async(exec_fname, results_dir)
            running_job.job_id = job_id
            with self._jobs_lock:
                self.running_jobs.append(running_job)
                queue_size = len(self.running_jobs)
            self._write_active_jobs_file()

            if self.verbose:
                logger.info(
                    f"Submitted job for generation {current_gen}, "
                    f"queue size: {queue_size}"
                )

    def _write_active_jobs_file(self) -> None:
        """Write active_jobs.json with metadata about currently running jobs.

        This allows the WebUI to display in-progress nodes in the tree.
        """
        try:
            # Take thread-safe snapshot of jobs
            with self._jobs_lock:
                jobs_snapshot = list(self.running_jobs)
            jobs_data = []
            for job in jobs_snapshot:
                jobs_data.append({
                    "job_id": str(job.job_id) if not hasattr(job.job_id, 'pid') else f"pid_{job.job_id.pid}",
                    "generation": job.generation,
                    "parent_id": job.parent_id,
                    "start_time": job.start_time,
                    "results_dir": str(job.results_dir),
                    "status": getattr(job, "status", "evaluating"),
                })
            
            jobs_file = Path(self.results_dir) / "active_jobs.json"
            logger.info(f"Writing active_jobs.json with {len(jobs_data)} jobs: {jobs_data}")
            with open(jobs_file, 'w') as f:
                json.dump(jobs_data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to write active_jobs.json: {e}")

    def _clear_active_jobs_file(self) -> None:
        """Clear the active_jobs.json file when no jobs are running."""
        try:
            jobs_file = Path(self.results_dir) / "active_jobs.json"
            logger.info(f"Clearing active_jobs.json (no jobs running)")
            if jobs_file.exists():
                with open(jobs_file, 'w') as f:
                    json.dump([], f)
        except Exception as e:
            logger.warning(f"Failed to clear active_jobs.json: {e}")

    def _check_completed_jobs(
        self,
    ) -> Tuple[List[RunningJob], List[RunningJob]]:
        """Check for completed edit/evaluation jobs.

        Returns:
            (completed_edits, completed_evals)
        """
        completed_edits: List[RunningJob] = []
        completed_evals: List[RunningJob] = []
        still_running: List[RunningJob] = []

        # Take a thread-safe snapshot of jobs to check
        with self._jobs_lock:
            jobs_snapshot = list(self.running_jobs)

        for job in jobs_snapshot:
            # Parallel agentic edit stage
            if job.status in {"editing", "awaiting_novelty"}:
                if job.edit_future is not None:
                    if job.edit_future.done():
                        try:
                            job.edit_result = job.edit_future.result()
                        except Exception as e:
                            logger.error(
                                f"Agentic edit job {job.job_id} failed: {e}"
                            )
                            # Synthesize a failure result so main thread can resubmit.
                            job.edit_result = EditWorkerResult(
                                generation=job.generation,
                                generation_dir=job.generation_dir,
                                results_dir=job.results_dir,
                                corpus_text="",
                                embedding=[],
                                embed_cost=0.0,
                                corpus_meta={},
                                meta_edit_data={
                                    "patch_type": "agentic",
                                    "api_costs": 0.0,
                                    "num_applied": 0,
                                    "error_attempt": str(e),
                                    "novelty_attempt": job.novelty_attempt,
                                    "resample_attempt": job.resample_attempt,
                                    "patch_attempt": 1,
                                    "agent_backend": self.evo_config.agentic.backend,
                                },
                                code_diff=None,
                                num_applied=0,
                            )
                        job.edit_future = None
                        job.status = "awaiting_novelty"
                        completed_edits.append(job)
                elif (
                    job.edit_future is None
                    and job.edit_result is not None
                    and job.status == "awaiting_novelty"
                ):
                    completed_edits.append(job)
                still_running.append(job)
                continue

            # Evaluation stage (agentic or legacy)
            if job.agentic_future is not None:
                if job.agentic_future.done():
                    try:
                        job.agentic_result = job.agentic_future.result()
                        if self.verbose:
                            logger.info(f"Agentic job {job.job_id} completed!")
                    except Exception as e:
                        logger.error(f"Agentic job {job.job_id} failed: {e}")
                        job.agentic_result = (
                            {
                                "correct": {"correct": False, "error": str(e)},
                                "metrics": {},
                            },
                            time.time() - job.start_time,
                        )
                    completed_evals.append(job)
                else:
                    still_running.append(job)
            else:
                # Legacy scheduler-based evaluation job
                is_running = self.scheduler.check_job_status(job)
                if not is_running:
                    if self.verbose:
                        logger.info(f"Job {job.job_id} completed!")
                    completed_evals.append(job)
                else:
                    still_running.append(job)

        # Update jobs list atomically (remove only completed eval jobs)
        completed_any = bool(completed_edits or completed_evals)
        with self._jobs_lock:
            self.running_jobs = still_running

        # Update active_jobs.json after jobs change
        if completed_any:
            if self.running_jobs:
                self._write_active_jobs_file()
            else:
                self._clear_active_jobs_file()

        return completed_edits, completed_evals

    def _process_completed_edit_job(self, job: RunningJob) -> None:
        """Handle a completed agentic edit attempt.

        This method runs novelty checks on the main thread and either resubmits
        another edit attempt (resample/novelty rejection) or enqueues evaluation.
        """

        if job.edit_result is None:
            return

        result = job.edit_result
        job.edit_result = None

        # Accumulate costs across attempts
        attempt_api_cost = float(result.meta_edit_data.get("api_costs", 0.0) or 0.0)
        job.api_costs_accumulated += attempt_api_cost
        job.embed_cost_accumulated += float(result.embed_cost or 0.0)

        # Update job with attempt outputs
        job.code_diff = result.code_diff
        job.meta_patch_data = result.meta_edit_data
        job.code_embedding = result.embedding
        job.corpus_text = result.corpus_text
        job.corpus_meta = result.corpus_meta or {}

        error_attempt = result.meta_edit_data.get("error_attempt")
        edit_success = error_attempt is None and result.num_applied > 0

        # If the edit failed and we have remaining resamples, try another parent/resample.
        if not edit_success and job.resample_attempt < self.evo_config.max_patch_resamples:
            next_resample = job.resample_attempt + 1
            prepared_job = self._prepare_agentic_edit_job(
                generation=job.generation,
                novelty_attempt=job.novelty_attempt,
                resample_attempt=next_resample,
            )
            job.parent_program = prepared_job.parent_program
            job.parent_id = prepared_job.parent_program.id
            job.archive_insp_ids = [p.id for p in prepared_job.archive_programs]
            job.top_k_insp_ids = [p.id for p in prepared_job.top_k_programs]
            job.resample_attempt = next_resample
            job.status = "editing"
            if self._agentic_edit_executor is not None:
                job.edit_future = self._agentic_edit_executor.submit(
                    self._run_agentic_patch_worker,
                    prepared_job,
                )
            else:  # pragma: no cover - fallback
                job.edit_result = self._run_agentic_patch_worker(prepared_job)
                job.status = "awaiting_novelty"
            return

        # Novelty check on main thread (even if edit_success=False after max resamples)
        parent_program = job.parent_program
        if parent_program is None:
            parent_program = result.meta_edit_data.get("parent_program")  # type: ignore

        should_accept = True
        novelty_metadata: Dict[str, Any] = {}
        if result.embedding and parent_program is not None:
            if self.novelty_judge.should_check_novelty(
                result.embedding, job.generation, parent_program, self.db
            ):
                should_accept, novelty_metadata = (
                    self.novelty_judge.assess_novelty_with_rejection_sampling(
                        result.corpus_text,
                        result.embedding,
                        parent_program,
                        self.db,
                    )
                )
                job.novelty_cost_accumulated += float(
                    novelty_metadata.get("novelty_total_cost", 0.0) or 0.0
                )
                job.novelty_checks_performed = int(
                    novelty_metadata.get("novelty_checks_performed", 0) or 0
                )
                job.novelty_explanation = str(
                    novelty_metadata.get("novelty_explanation", "") or ""
                )
            else:
                # Log skip reasons similar to legacy path
                if not self.db.island_manager or not hasattr(
                    self.db.island_manager, "are_all_islands_initialized"
                ):
                    self.novelty_judge.log_novelty_skip_message("no island manager")
                elif not self.db.island_manager.are_all_islands_initialized():
                    self.novelty_judge.log_novelty_skip_message(
                        "not all islands initialized yet"
                    )
        else:
            if not result.embedding:
                self.novelty_judge.log_novelty_skip_message("no embedding")

        # If novelty rejected and we still have attempts, resubmit a fresh novelty attempt.
        if not should_accept and job.novelty_attempt < self.evo_config.max_novelty_attempts:
            next_novelty = job.novelty_attempt + 1
            prepared_job = self._prepare_agentic_edit_job(
                generation=job.generation,
                novelty_attempt=next_novelty,
                resample_attempt=1,
            )
            job.parent_program = prepared_job.parent_program
            job.parent_id = prepared_job.parent_program.id
            job.archive_insp_ids = [p.id for p in prepared_job.archive_programs]
            job.top_k_insp_ids = [p.id for p in prepared_job.top_k_programs]
            job.novelty_attempt = next_novelty
            job.resample_attempt = 1
            job.status = "editing"
            if self._agentic_edit_executor is not None:
                job.edit_future = self._agentic_edit_executor.submit(
                    self._run_agentic_patch_worker,
                    prepared_job,
                )
            else:  # pragma: no cover - fallback
                job.edit_result = self._run_agentic_patch_worker(prepared_job)
                job.status = "awaiting_novelty"
            return

        # Accept this attempt (either novelty passed/skipped, or max attempts reached)
        if job.meta_patch_data is None:
            job.meta_patch_data = {}
        job.meta_patch_data["api_costs"] = job.api_costs_accumulated
        job.embed_cost = job.embed_cost_accumulated
        job.novelty_cost = job.novelty_cost_accumulated
        if job.novelty_checks_performed > 0:
            job.meta_patch_data["novelty_checks_performed"] = job.novelty_checks_performed
            job.meta_patch_data["novelty_cost"] = job.novelty_cost_accumulated
            job.meta_patch_data["novelty_explanation"] = job.novelty_explanation

        # Enqueue evaluation for this generation
        job.status = "evaluating"
        job.edit_future = None

        if self.evaluator_mode == "agentic":
            if self._agentic_executor is not None:
                future = self._agentic_executor.submit(
                    self._run_agentic_evaluation,
                    exec_fname=job.exec_fname,
                    results_dir=job.results_dir,
                    generation_dir=job.generation_dir,
                    generation=job.generation,
                    parent_id=job.parent_id,
                )
                job.agentic_future = future
                job.job_id = f"agentic_gen_{job.generation}"
            else:  # pragma: no cover - fallback
                results, rtime = self._run_agentic_evaluation(
                    exec_fname=job.exec_fname,
                    results_dir=job.results_dir,
                    generation_dir=job.generation_dir,
                    generation=job.generation,
                    parent_id=job.parent_id,
                )
                job.agentic_result = (results, rtime)
        else:
            eval_exec_fname = job.exec_fname
            entrypoint = getattr(self.evo_config, "agentic_entrypoint_path", None)
            if entrypoint:
                entry_path = Path(entrypoint)
                if not entry_path.is_absolute():
                    entry_path = job.generation_dir / entry_path
                eval_exec_fname = str(entry_path)
            job_id = self.scheduler.submit_async(eval_exec_fname, job.results_dir)
            job.job_id = job_id

        self._write_active_jobs_file()

    def _process_completed_job(self, job: RunningJob):
        """Process a completed job from the scheduler."""
        end_time = time.time()
        rtime = end_time - job.start_time
        
        # Handle agentic jobs differently
        if job.agentic_result is not None:
            results, rtime = job.agentic_result
            self._finalize_job(job, results, rtime)
        else:
            results = self.scheduler.get_job_results(job.job_id, job.results_dir)
            self._finalize_job(job, results, rtime)

    def _finalize_job(
        self,
        job: RunningJob,
        results: Optional[Dict[str, Any]],
        rtime: float,
    ) -> None:
        # Use prepared corpus text; fall back to primary file if missing
        corpus_text = job.corpus_text
        if not corpus_text:
            try:
                corpus_text = Path(job.exec_fname).read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(
                    f"Could not read code for job {job.job_id}. Error: {e}"
                )
                corpus_text = ""

        # Use pre-computed embedding and novelty costs
        code_embedding = job.code_embedding
        e_cost = job.embed_cost
        n_cost = job.novelty_cost
        if self.verbose:
            logger.debug(
                f"=> Using pre-computed embedding for job {job.job_id}, "
                f"embed cost: {e_cost:.4f}, novelty cost: {n_cost:.4f}"
            )

        correct_val = False
        metrics_val = {}
        stdout_log = ""
        stderr_log = ""
        payload = results or {"correct": {"correct": False}, "metrics": {}}
        correct_val = payload.get("correct", {}).get("correct", False)
        metrics_val = payload.get("metrics", {})
        stdout_log = payload.get("stdout_log", "")
        stderr_log = payload.get("stderr_log", "")

        combined_score = metrics_val.get("combined_score", 0.0)
        public_metrics = metrics_val.get("public", {})
        private_metrics = metrics_val.get("private", {})
        text_feedback = metrics_val.get("text_feedback", "")

        agentic_eval_meta = payload.get("agentic_eval")
        ensemble_eval_meta = payload.get("ensemble_evaluation")

        # Generate program ID first so we can use it in commit message
        program_id = str(uuid.uuid4())

        # Commit workspace changes and get the commit SHA (for isolated workspaces)
        commit_sha = self._commit_workspace_changes(program_id, job.generation)

        # Create git commit in evolution.git for git-backed storage
        git_commit_sha = None
        if self.git_manager is not None:
            # Get parent's git commit SHA
            parent_git_sha = None
            if job.parent_program is not None:
                # Try to get SHA from parent's metadata
                parent_meta = job.parent_program.metadata or {}
                parent_git_sha = parent_meta.get("git_commit_sha")
            # Fallback to initial SHA for generation 1 or if parent has no SHA
            if parent_git_sha is None:
                parent_git_sha = self._initial_git_sha

            if parent_git_sha:
                git_commit_sha = self._git_commit_generation(
                    generation_dir=job.generation_dir,
                    generation=job.generation,
                    node_uuid=program_id,
                    parent_sha=parent_git_sha,
                )

        db_program = Program(
            id=program_id,
            code=corpus_text,
            language=self.evo_config.language,
            parent_id=job.parent_id,
            generation=job.generation,
            archive_inspiration_ids=job.archive_insp_ids,
            top_k_inspiration_ids=job.top_k_insp_ids,
            code_diff=job.code_diff,
            embedding=code_embedding,
            correct=correct_val,
            combined_score=combined_score,
            public_metrics=public_metrics,
            private_metrics=private_metrics,
            text_feedback=text_feedback,
            metadata={
                "compute_time": rtime,
                **(job.meta_patch_data or {}),
                "embed_cost": e_cost,
                "novelty_cost": n_cost,
                "stdout_log": stdout_log,
                "stderr_log": stderr_log,
                "evaluator_mode": self.evaluator_mode,
                **({"commit_sha": commit_sha} if commit_sha else {}),
                **({"git_commit_sha": git_commit_sha} if git_commit_sha else {}),
            },
        )
        if agentic_eval_meta:
            if db_program.metadata is None:
                db_program.metadata = {}
            db_program.metadata["agentic_evaluator"] = agentic_eval_meta
        if ensemble_eval_meta:
            if db_program.metadata is None:
                db_program.metadata = {}
            db_program.metadata["ensemble_evaluation"] = ensemble_eval_meta
        # Pass parent's island_idx to avoid DB lookup during island assignment
        parent_island_idx = (
            job.parent_program.island_idx
            if job.parent_program is not None
            else None
        )
        self.db.add(db_program, verbose=True, parent_island_idx=parent_island_idx)

        # Add the evaluated program to meta memory tracking
        self.meta_summarizer.add_evaluated_program(db_program)

        # Check if we should update meta memory after adding this program
        if self.meta_summarizer.should_update_meta(self.evo_config.meta_rec_interval):
            logger.info(
                f"Updating meta memory after processing "
                f"{len(self.meta_summarizer.evaluated_since_last_meta)} programs..."
            )
            best_program = self.db.get_best_program()
            updated_recs, meta_cost = self.meta_summarizer.update_meta_memory(
                best_program
            )
            if updated_recs:
                # Write meta output file using accumulated program count
                self.meta_summarizer.write_meta_output(str(self.results_dir))
                # Store meta cost for tracking
                if meta_cost > 0:
                    logger.info(
                        f"Meta recommendation generation cost: ${meta_cost:.4f}"
                    )
                    # Add meta cost to this program's metadata (the one that triggered the update)
                    if db_program.metadata is None:
                        db_program.metadata = {}
                    db_program.metadata["meta_cost"] = meta_cost
                    # Update the program in the database with the new metadata
                    self.db.update_program_metadata(db_program.id, db_program.metadata)

        if self.llm_selection is not None:
            if "model_name" not in db_program.metadata:
                logger.warning(
                    "No model_name found in program metadata, "
                    "unable to update model selection algorithm."
                )
            else:
                parent = (
                    self.db.get(db_program.parent_id) if db_program.parent_id else None
                )
                baseline = parent.combined_score if parent else None
                reward = db_program.combined_score if correct_val else None
                model_name = db_program.metadata["model_name"]
                result = None
                try:
                    result = self.llm_selection.update(
                        arm=model_name,
                        reward=reward,
                        baseline=baseline,
                    )
                except ValueError as exc:
                    if "unknown arm name" in str(exc):
                        if self.verbose:
                            logger.debug(
                                "Skipping llm_selection update for %s: %s",
                                model_name,
                                exc,
                            )
                        result = None
                    else:  # pragma: no cover - defensive
                        raise
                if result and self.verbose:
                    normalized_score, baseline = result

                    def fmt(x):
                        return f"{x:.4f}" if isinstance(x, (float, int)) else "None"

                    logger.debug(
                        f"==> UPDATED LLM SELECTION: model: "
                        f"{model_name.split('/')[-1][-25:]}..., "
                        f"score: {fmt(normalized_score)}, "
                        f"raw score: {fmt(reward)}, baseline: {fmt(baseline)}"
                    )
                    self.llm_selection.print_summary()

        # Update backend bandit if enabled (agentic mode only)
        if self.backend_bandit is not None:
            agent_backend = db_program.metadata.get("agent_backend") if db_program.metadata else None
            if agent_backend is None:
                if self.verbose:
                    logger.debug(
                        "No agent_backend in program metadata, "
                        "skipping backend bandit update."
                    )
            else:
                parent = (
                    self.db.get(db_program.parent_id) if db_program.parent_id else None
                )
                baseline = parent.combined_score if parent else None
                reward = db_program.combined_score if correct_val else None
                result = self.backend_bandit.update(
                    backend=agent_backend,
                    reward=reward,
                    baseline=baseline,
                )
                if result and self.verbose:
                    normalized_score, _ = result

                    def fmt_bandit(x):
                        return f"{x:.4f}" if isinstance(x, (float, int)) else "None"

                    posteriors = self.backend_bandit.get_summary()["posteriors"]
                    logger.debug(
                        f"==> UPDATED BACKEND BANDIT: backend: {agent_backend}, "
                        f"score: {fmt_bandit(normalized_score)}, "
                        f"raw: {fmt_bandit(reward)}, baseline: {fmt_bandit(baseline)}, "
                        f"posteriors: {posteriors}"
                    )
                # Save bandit state for WebUI live posteriors
                self._save_bandit_state()
                
                # Record to global history if enabled
                if (self._bandit_history is not None and 
                    self.evo_config.agentic.record_to_global_history):
                    try:
                        # Get run identifier from results_dir
                        run_id = Path(self.results_dir).name
                        run_name = Path(self.results_dir).parent.name
                        self._bandit_history.record_interaction(
                            run_id=run_id,
                            run_name=run_name,
                            backend=agent_backend,
                            reward=reward,
                            baseline=baseline,
                            generation=db_program.generation,
                            task_name=self.evo_config.task_sys_msg[:50] if self.evo_config.task_sys_msg else None,
                        )
                    except Exception as e:
                        if self.verbose:
                            logger.debug(f"Failed to record to bandit history: {e}")

        self.db.save()
        self._update_best_solution()

        # Note: Meta summarization check is now done after completed generations
        # are updated in the main loop to ensure correct timing

        # Save meta memory state after each job completion
        self._save_meta_memory()
        
        # Cleanup old generations if enabled (saves disk space for long runs)
        self._cleanup_old_generations()

    def _update_best_solution(self):
        """Checks and updates the best program."""
        best_programs = self.db.get_top_programs(n=1, correct_only=True)
        if not best_programs:
            if self.verbose:
                logger.debug(
                    "No correct programs found yet, cannot determine best solution."
                )
            return

        best_program = best_programs[0]

        if best_program.id == self.best_program_id:
            return  # No change

        self.best_program_id = best_program.id

        source_dir = f"{self.results_dir}/{FOLDER_PREFIX}_{best_program.generation}"
        best_dir = Path(self.results_dir) / "best"

        if best_dir.exists():
            shutil.rmtree(best_dir)

        # Mirror best generation using fast CoW copy, but keep per-gen results/
        exclude_dirs = {d for d in WORKSPACE_EXCLUDE_DIRS if d != "results"}
        self._fast_copy_workspace_tree(
            Path(source_dir),
            best_dir,
            dirs_exist_ok=True,
            exclude_dirs=exclude_dirs,
        )

        if self.verbose:
            logger.info(
                f"New best program found: gen {best_program.generation}, "
                f"id {best_program.id[:6]}... "
                f"Copied to {best_dir}"
            )

    def run_patch(
        self,
        parent_program: Program,
        archive_programs: List[Program],
        top_k_programs: List[Program],
        generation: int,
        novelty_attempt: int = 1,
        resample_attempt: int = 1,
    ) -> tuple[Optional[str], dict, int]:
        """Run patch generation for a specific generation."""
        max_patch_attempts = self.evo_config.max_patch_attempts
        if self.verbose:
            logger.info(
                f"Edit Cycle {generation} -> {generation + 1}, "
                f"Max Patch Attempts: {max_patch_attempts}"
            )
        # Get current meta recommendations
        meta_recs, _, _ = self.meta_summarizer.get_current()
        # Construct edit / code change message
        patch_sys, patch_msg, patch_type = self.prompt_sampler.sample(
            parent=parent_program,
            archive_inspirations=archive_programs,
            top_k_inspirations=top_k_programs,
            meta_recommendations=meta_recs,
        )

        if self.evo_config.agentic_mode:
            return self._run_agentic_patch(
                parent_program=parent_program,
                generation=generation,
                patch_sys=patch_sys,
                patch_msg=patch_msg,
                patch_type=patch_type,
                novelty_attempt=novelty_attempt,
                resample_attempt=resample_attempt,
            )

        if patch_type in ["full", "cross"]:
            apply_patch = apply_full_patch
        elif patch_type == "diff":
            apply_patch = apply_diff_patch
        elif patch_type == "paper":
            raise NotImplementedError("Paper edit not implemented.")
            # apply_patch = apply_paper_patch
        else:
            raise ValueError(f"Invalid patch type: {patch_type}")

        total_costs = 0
        msg_history = []
        llm_kwargs = self.llm.get_kwargs()
        if self.llm_selection is not None:
            model_name = llm_kwargs["model_name"]
            self.llm_selection.update_submitted(model_name)
        code_diff = None  # Initialize code_diff
        num_applied_attempt = 0  # Initialize num_applied_attempt
        error_attempt = (
            "Max attempts reached without successful patch."  # Default error
        )
        patch_name = None
        patch_description = None
        output_path_attempt = None
        patch_txt_attempt = None
        patch_path = None
        diff_summary = {}

        for patch_attempt in range(max_patch_attempts):
            response = self.llm.query(
                msg=patch_msg,
                system_msg=patch_sys,
                msg_history=msg_history,
                llm_kwargs=llm_kwargs,
            )
            # print(response.content)
            if response is None or response.content is None:
                if self.verbose:
                    logger.info(
                        f"  PATCH ATTEMPT {patch_attempt + 1}/{max_patch_attempts} FAILURE. "
                        f"Error: LLM response content was None."
                    )
                # Prepare for next attempt or exit
                error_attempt = "LLM response content was None."
                num_applied_attempt = 0
                patch_txt_attempt = None
                if patch_attempt < max_patch_attempts - 1:
                    patch_msg = (
                        "The previous attempt to get an edit was not "
                        "successful because the LLM response was empty. "
                        "Try again."
                    )
                    if response:
                        msg_history = response.new_msg_history
                    continue
                else:  # Last attempt
                    break

            total_costs += response.cost  # Acc. cost
            patch_name = extract_between(
                response.content,
                "<NAME>",
                "</NAME>",
                False,
            )
            patch_description = extract_between(
                response.content,
                "<DESCRIPTION>",
                "</DESCRIPTION>",
                False,
            )

            # Apply the code patch (diff/full rewrite)
            (
                _,
                num_applied_attempt,
                output_path_attempt,
                error_attempt,
                patch_txt_attempt,
                patch_path,
            ) = apply_patch(
                original_str=parent_program.code,
                patch_str=response.content,
                patch_dir=f"{self.results_dir}/{FOLDER_PREFIX}_{generation}",
                language=self.evo_config.language,
                verbose=False,
            )

            if error_attempt is None and num_applied_attempt > 0:
                if patch_path:  # Ensure patch_path is not None
                    diff_summary = summarize_diff(
                        str(patch_path)
                    )  # Convert Path to str
                if self.verbose:
                    logger.info(
                        f"  PATCH ATTEMPT {patch_attempt + 1}/{max_patch_attempts} SUCCESS. "
                        f"Output: {output_path_attempt}, "
                        f"Patches Applied: {num_applied_attempt}."
                    )

                code_diff = patch_txt_attempt
                break  # Break from patch attempts
            else:
                error_str = (
                    str(error_attempt) if error_attempt else "No changes applied."
                )
                patch_msg = (
                    "The previous edit was not successful."
                    + " This was the error message: \n\n"
                    + error_str
                    + "\n\n Try again."
                )
                if self.verbose:
                    logger.info(
                        f"  PATCH ATTEMPT {patch_attempt + 1}/{max_patch_attempts} FAILURE. "
                        f"Error: '{error_str}', "
                        f"Patches Applied: {num_applied_attempt}."
                    )
                msg_history = response.new_msg_history
                code_diff = None
                if patch_attempt == max_patch_attempts - 1:  # Last attempt failed
                    # error_attempt is already set from apply_patch or default
                    pass

        # Only consider the diff summary for the original source file
        original_filename = f"original.{self.lang_ext}"
        if original_filename in diff_summary:
            diff_summary = diff_summary[original_filename]

        meta_edit_data = {
            "patch_type": patch_type,
            "api_costs": total_costs,
            "num_applied": num_applied_attempt,
            "patch_name": patch_name,
            "patch_description": patch_description,
            "error_attempt": error_attempt,
            "novelty_attempt": novelty_attempt,
            "resample_attempt": resample_attempt,
            "patch_attempt": patch_attempt + 1,
            **llm_kwargs,
            "llm_result": response.to_dict() if response else None,
            "diff_summary": diff_summary,
        }
        if self.verbose and num_applied_attempt > 0:
            self._print_metadata_table(meta_edit_data, generation)
        # Delete generation from meta_edit_data
        return code_diff, meta_edit_data, num_applied_attempt

    def _run_agentic_patch(
        self,
        *,
        parent_program: Program,
        generation: int,
        patch_sys: str,
        patch_msg: str,
        patch_type: str,
        novelty_attempt: int,
        resample_attempt: int,
        selected_backend_override: Optional[str] = None,
        bandit_summary_override: Optional[Dict[str, Any]] = None,
    ) -> tuple[Optional[str], dict, int]:
        """Execute an agentic editing session via the Codex CLI."""

        # Agentic mode treats the entire generation workspace as the program.
        # Build the scratch baseline from the parent's on-disk workspace when available.
        base_files: Dict[Path, str] = {}
        parent_root = self._workspace_root_for_program(parent_program)
        if parent_root is not None and parent_root.exists():
            for file_path in parent_root.rglob("*"):
                if not file_path.is_file():
                    continue
                rel_path = file_path.relative_to(parent_root)
                if self._should_skip_workspace_path(rel_path):
                    continue
                try:
                    base_files[rel_path] = file_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue

        # primary_file is not semantically meaningful in agentic mode, but the
        # AgentContext interface requires one. Use a sentinel path that is not
        # expected to exist in the workspace.
        primary_filename = Path("__agentic_primary_unused__")
        session_root: Optional[Path] = None
        parent_metadata = parent_program.metadata or {}
        resume_session_id: Optional[str] = None
        resumed_from_parent = False
        if self.evo_config.agentic.resume_parent_session:
            candidate = parent_metadata.get("agent_session_id")
            if isinstance(candidate, str) and candidate.strip():
                resume_session_id = candidate.strip()
                resumed_from_parent = True
            elif self.verbose:
                logger.info(
                    "Agentic resume enabled but parent %s had no session id",
                    parent_program.id,
                )

        def _serialize_changed_files(
            changed_files: Optional[Dict[Path, str]]
        ) -> Dict[str, str]:
            if not changed_files:
                return {}
            serialized: Dict[str, str] = {}
            for rel_path, content in changed_files.items():
                serialized[str(rel_path)] = content
            return serialized

        def _build_code_diffs(
            changed_files: Optional[Dict[Path, str]]
        ) -> Dict[str, str]:
            if not changed_files:
                return {}
            diffs: Dict[str, str] = {}
            for rel_path, new_content in changed_files.items():
                before = base_files.get(rel_path, "")
                before_lines = before.splitlines(keepends=True)
                after_lines = new_content.splitlines(keepends=True)
                diff_text = "".join(
                    difflib.unified_diff(
                        before_lines,
                        after_lines,
                        fromfile=f"a/{rel_path}",
                        tofile=f"b/{rel_path}",
                    )
                )
                diffs[str(rel_path)] = diff_text
            return diffs

        def _agent_model_name(
            backend: str, actual_model: Optional[str] = None
        ) -> str:
            """Determine model name with priority: actual > config > profile > fallback."""
            # Priority 1: Actual model from CLI events (Claude/ShinkaAgent emit this)
            if actual_model:
                return actual_model

            # Priority 2: Explicit config override
            extra_cli = self.evo_config.agentic.extra_cli_config
            model_override = None
            if extra_cli:
                getter = getattr(extra_cli, "get", None)
                if callable(getter):
                    model_override = getter("model")
                elif isinstance(extra_cli, dict):
                    model_override = extra_cli.get("model")
            if model_override:
                return str(model_override)

            # Priority 3: CLI profile (for gemini/claude this IS the model)
            if self.evo_config.agentic.cli_profile:
                return self.evo_config.agentic.cli_profile

            # Priority 4: Backend-specific fallback (only if nothing else set)
            return f"{backend}-default"

        def _agent_backend_type(backend: str) -> str:
            """Classify agentic backend as CLI vs native for metadata/UI display."""
            if backend == "shinka":
                return "native"
            return "cli"

        # Initialize selected_backend before inner functions so closures can capture it.
        # This may be overridden by bandit sampling (main-thread) or by an explicit
        # override when invoked from a parallel worker.
        selected_backend = (
            selected_backend_override or self.evo_config.agentic.backend
        )

        def failure_meta(
            message: str,
            *,
            session_log: Optional[List[str]] = None,
            commands: Optional[List[CommandResult]] = None,
            metrics: Optional[Dict[str, float]] = None,
            session_log_path: Optional[Path] = None,
            session_events: Optional[List[Dict[str, Any]]] = None,
            agent_prompt: Optional[str] = None,
            binary_changed_files: Optional[Dict[Path, str]] = None,
            changed_files: Optional[Dict[Path, str]] = None,
            session_id: Optional[str] = None,
        ) -> tuple[Optional[str], dict, int]:
            api_cost = 0.0
            if metrics:
                api_cost = (
                    metrics.get("total_cost")
                    or metrics.get("estimated_total_cost")
                    or metrics.get("estimated_total_tokens", 0.0)
                )
            serialized_changed = _serialize_changed_files(changed_files)
            meta_edit_data = {
                "patch_type": "agentic",
                "api_costs": api_cost,
                "num_applied": 0,
                "patch_name": None,
                "patch_description": None,
                "error_attempt": message,
                "novelty_attempt": novelty_attempt,
                "resample_attempt": resample_attempt,
                "patch_attempt": 1,
                "agent_session_path": str(session_root) if session_root else None,
                "agent_final_message": None,
                "agent_session_log": session_log or [],
                "agent_commands": [asdict(cmd) for cmd in commands or []],
                "agent_metrics": metrics or {},
                "agent_session_log_path": (
                    str(session_log_path) if session_log_path else None
                ),
                "agent_session_events": session_events or [],
                "agent_prompt": agent_prompt,
                "agent_binary_files": {
                    str(path): content
                    for path, content in (binary_changed_files or {}).items()
                },
                "agent_changed_files": serialized_changed,
                "agent_code_diffs": _build_code_diffs(changed_files),
                "agent_primary_file": None,
                "diff_summary": {},
                "model_name": _agent_model_name(selected_backend),
                "agent_backend": selected_backend,
                "agent_backend_type": _agent_backend_type(selected_backend),
                "agent_session_id": session_id,
                "agent_resumed_from_parent": resumed_from_parent,
                "agent_resume_source_session_id": resume_session_id,
                "bandit_posteriors": (
                    bandit_summary_override
                    if bandit_summary_override is not None
                    else (
                        self.backend_bandit.get_summary()
                        if self.backend_bandit
                        else None
                    )
                ),
            }
            return None, meta_edit_data, 0

        # Determine which backend to use: either sample from bandit or use config.
        # If an override was provided (e.g., from a parallel worker), respect it.
        if selected_backend_override is None:
            if self.backend_bandit is not None:
                try:
                    selected_backend = self.backend_bandit.sample()
                    if self.verbose:
                        posteriors = self.backend_bandit.get_summary()["posteriors"]
                        logger.info(
                            f"Backend bandit selected '{selected_backend}' "
                            f"(posteriors: {posteriors})"
                        )
                except NoAuthenticatedBackendsError as e:
                    return failure_meta(str(e))
            else:
                selected_backend = self.evo_config.agentic.backend

        # Try to ensure the selected backend is available, with fallback to alternatives
        excluded_backends: List[str] = []
        while True:
            try:
                if selected_backend == "gemini":
                    ensure_gemini_available(self.evo_config.agentic.cli_path)
                elif selected_backend == "claude":
                    ensure_claude_available(self.evo_config.agentic.cli_path)
                elif selected_backend == "shinka":
                    ensure_shinka_available()
                elif selected_backend == "jules":
                    # Jules needs github_repo from extra_cli_config to verify repo is connected
                    github_repo = self.evo_config.agentic.extra_cli_config.get("github_repo")
                    ensure_jules_available(github_repo)
                else:
                    ensure_codex_available(self.evo_config.agentic.cli_path)
                break  # Backend is available, proceed
            except (CodexUnavailableError, GeminiUnavailableError, ClaudeUnavailableError, ShinkaUnavailableError, JulesUnavailableError) as exc:
                # Backend unavailable - try to fall back to another if bandit is available
                if self.backend_bandit is None:
                    return failure_meta(str(exc))

                excluded_backends.append(selected_backend)
                if self.verbose:
                    logger.warning(
                        f"Backend '{selected_backend}' unavailable: {exc}. "
                        f"Attempting fallback (excluded: {excluded_backends})"
                    )

                try:
                    selected_backend = self.backend_bandit.sample_with_fallback(
                        exclude=excluded_backends
                    )
                    if self.verbose:
                        logger.info(f"Falling back to backend: {selected_backend}")
                except NoAuthenticatedBackendsError as fallback_exc:
                    return failure_meta(
                        f"All backends failed. Original: {exc}. Fallback: {fallback_exc}"
                    )

        # Create scratch directory outside any git repo to prevent Codex CLI from
        # discovering parent AGENTS.md files. If scratch_dir_base is None, fall
        # back to the results directory (legacy behavior).
        session_uuid = str(uuid.uuid4())
        if self.evo_config.agentic.scratch_dir_base:
            scratch_base = Path(self.evo_config.agentic.scratch_dir_base)
            scratch_base.mkdir(parents=True, exist_ok=True)
            session_root = scratch_base / session_uuid
            # Register for process-level cleanup on crash/exit
            self._register_scratch_dir(session_root)
        else:
            session_root = (
                Path(self.results_dir)
                / "agent_sessions"
                / session_uuid
            )

        # Write session metadata for visualization to track in-progress jobs
        session_root.mkdir(parents=True, exist_ok=True)

        # Wrap in try/finally to ensure cleanup on all exit paths
        try:
            session_meta = {
                "parent_id": parent_program.id,
                "generation": generation,
                "patch_type": patch_type,
                "novelty_attempt": novelty_attempt,
                "resample_attempt": resample_attempt,
                "start_time": time.time(),
                "results_dir": str(self.results_dir),
            }
            try:
                with open(session_root / "session_meta.json", 'w') as f:
                    json.dump(session_meta, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to write session_meta.json: {e}")

            helper_files = sorted(base_files.keys())

            system_prompt = patch_sys.strip()
            if helper_files:
                max_helpers = 50
                listed_helpers = helper_files[:max_helpers]
                helper_listing = "\n".join(
                    f"- {path.as_posix()}" for path in listed_helpers
                )
                if len(helper_files) > max_helpers:
                    helper_listing += (
                        f"\n- ... and {len(helper_files) - max_helpers} more files"
                    )
                system_prompt += (
                    "\n\n# Workspace Files\n"
                    "The following files were copied from the parent workspace; "
                    "edit any that your improvement touches so evaluation can run without manual fixes:\n"
                    f"{helper_listing}"
                    "\n\nWhen workspace files are available you are expected to update at least one of them whenever your idea relies on shared utilities. "
                    "If you believe no helper change is required, record that decision with a short comment inside the most relevant file so the run is not discarded."
                )

            context = AgentContext(
                user_prompt=patch_msg.strip(),
                system_prompt=system_prompt,
                language=self.evo_config.language,
                base_files=base_files,
                primary_file=primary_filename,
                metadata={
                    "generation": generation,
                    "novelty_attempt": novelty_attempt,
                    "resample_attempt": resample_attempt,
                    "patch_type": patch_type,
                    "results_dir": str(self.results_dir),
                },
                resume_session_id=resume_session_id,
            )

            editor = AgenticEditor(
                scratch_dir=session_root,
                config=self.evo_config.agentic,
                runner=(
                    run_gemini_task if selected_backend == "gemini"
                    else run_claude_task if selected_backend == "claude"
                    else run_shinka_task if selected_backend == "shinka"
                    else run_jules_task if selected_backend == "jules"
                    else run_codex_task
                ),
            )

            try:
                agent_result = editor.run_session(context)
            except (CodexExecutionError, GeminiExecutionError, ClaudeExecutionError, ShinkaExecutionError, JulesExecutionError) as exc:
                return failure_meta(str(exc))

            generation_dir = Path(self.results_dir) / f"{FOLDER_PREFIX}_{generation}"
            if generation_dir.exists():
                shutil.rmtree(generation_dir)
            generation_dir.mkdir(parents=True, exist_ok=True)
            self._hydrate_generation_directory(parent_program, generation_dir)

            patch_dir = str(generation_dir)
            # Apply all changes from the agentic session into the new generation workspace.
            for rel_path, content in agent_result.changed_files.items():
                target = generation_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            for rel_path, b64_content in agent_result.binary_changed_files.items():
                target = generation_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(b64_content))

            num_applied = (
                1
                if agent_result.changed_files or agent_result.binary_changed_files
                else 0
            )
            patch_txt = None
            patch_path = None

            if num_applied == 0:
                return failure_meta(
                    "Agentic edit produced no changes.",
                    session_log=agent_result.session_log,
                    commands=agent_result.commands_run,
                    metrics=agent_result.metrics,
                    session_log_path=agent_result.session_log_path,
                    session_events=agent_result.session_events,
                    agent_prompt=patch_msg,
                    binary_changed_files=agent_result.binary_changed_files,
                    changed_files=agent_result.changed_files,
                    session_id=agent_result.session_id,
                )

            # Snapshot entire agent workspace for auditing/debugging.
            snapshot_dir: Optional[Path] = None
            if session_root.exists():
                snapshot_dir = generation_dir / "workspace_snapshot"
                if snapshot_dir.exists():
                    shutil.rmtree(snapshot_dir)
                self._fast_copy_workspace_tree(
                    session_root,
                    snapshot_dir,
                    dirs_exist_ok=True,
                )

            diff_summary: Dict[str, Any] = {}

            api_cost = (
                agent_result.metrics.get("total_cost")
                or agent_result.metrics.get("estimated_total_cost")
                or agent_result.metrics.get("estimated_total_tokens", 0.0)
            )

            meta_edit_data = {
                "patch_type": "agentic",
                "api_costs": api_cost,
                "num_applied": num_applied,
                "patch_name": None,
                "patch_description": None,
                "error_attempt": None,
                "novelty_attempt": novelty_attempt,
                "resample_attempt": resample_attempt,
                "patch_attempt": 1,
                "agent_session_path": str(session_root),
                "agent_final_message": agent_result.final_message,
                "agent_session_log": agent_result.session_log,
                "agent_commands": [asdict(cmd) for cmd in agent_result.commands_run],
                "agent_metrics": agent_result.metrics,
                "agent_session_log_path": (
                    str(agent_result.session_log_path)
                    if agent_result.session_log_path
                    else None
                ),
                "agent_session_events": agent_result.session_events,
                "agent_prompt": patch_msg,
                "agent_workspace_snapshot": (
                    str(snapshot_dir) if snapshot_dir else None
                ),
                "agent_binary_files": {
                    str(path): content
                    for path, content in agent_result.binary_changed_files.items()
                },
                "agent_changed_files": _serialize_changed_files(
                    agent_result.changed_files
                ),
                "agent_code_diffs": _build_code_diffs(agent_result.changed_files),
                "agent_primary_file": None,
                "diff_summary": diff_summary,
                "model_name": _agent_model_name(selected_backend, agent_result.model),
                "agent_backend": selected_backend,
                "agent_backend_type": _agent_backend_type(selected_backend),
                "agent_session_id": agent_result.session_id,
                "agent_resumed_from_parent": resumed_from_parent,
                "agent_resume_source_session_id": resume_session_id,
                "bandit_posteriors": (
                    bandit_summary_override
                    if bandit_summary_override is not None
                    else (
                        self.backend_bandit.get_summary()
                        if self.backend_bandit
                        else None
                    )
                ),
            }

            return patch_txt, meta_edit_data, num_applied
        finally:
            # Clean up scratch directory if it was created outside results_dir
            if self.evo_config.agentic.scratch_dir_base and session_root.exists():
                try:
                    shutil.rmtree(session_root, ignore_errors=True)
                    self._unregister_scratch_dir(session_root)
                except Exception:
                    pass

    def _build_embedding_corpus(
        self, generation_dir: Path, meta_patch_data: Optional[dict]
    ) -> EmbeddingCorpus:
        """Construct the artifact corpus for embeddings and novelty checks.

        Scientific integrity note:

        - In legacy mode (`agentic_mode=False`), `Program.code` historically meant
          the executable source text (e.g., `main.py`). Embeddings/novelty should
          also operate on that same source to keep results comparable to pre-agentic
          runs.

        - In agentic mode (`agentic_mode=True`), the "program" is the full
          workspace. Embeddings/novelty operate on a deterministic multi-file
          corpus dump.
        """

        # Legacy parity: embed only the executable source file, not a multi-file corpus.
        if not self.evo_config.agentic_mode:
            primary_rel = Path(f"main.{self.lang_ext}")
            primary_path = generation_dir / primary_rel
            try:
                text = primary_path.read_text(encoding="utf-8")
                total_bytes = len(text.encode("utf-8"))
                return EmbeddingCorpus(
                    text=text,
                    included_files=[primary_rel.as_posix()],
                    skipped_files=[],
                    binary_files=[],
                    truncated=False,
                    total_bytes=total_bytes,
                )
            except Exception:
                # Mirror the previous behavior: missing/invalid code yields an
                # empty embedding input and will skip novelty/embedding safely.
                return EmbeddingCorpus(
                    text="",
                    included_files=[],
                    skipped_files=[primary_rel.as_posix()],
                    binary_files=[],
                    truncated=False,
                    total_bytes=0,
                )

        # Agentic mode: build deterministic multi-file corpus.
        changed_first: Optional[List[Path]] = None
        if self.evo_config.embedding_use_changed_files_first and meta_patch_data:
            changed_files = meta_patch_data.get("agent_changed_files") or {}
            if isinstance(changed_files, dict):
                changed_first = [Path(p) for p in changed_files.keys()]

        return build_embedding_corpus(
            generation_dir,
            include_globs=self.evo_config.embedding_include_globs,
            exclude_globs=self.evo_config.embedding_exclude_globs,
            max_files=self.evo_config.embedding_max_files,
            max_total_bytes=self.evo_config.embedding_max_total_bytes,
            max_bytes_per_file=self.evo_config.embedding_max_bytes_per_file,
            changed_first=changed_first,
            exclude_dirs=WORKSPACE_EXCLUDE_DIRS,
            exclude_suffixes=WORKSPACE_EXCLUDE_SUFFIXES,
            exclude_files=WORKSPACE_EXCLUDE_FILES,
        )

    def get_code_embedding(self, corpus_text: str) -> tuple[List[float], float]:
        """Get the embedding of the corpus text."""
        if not corpus_text:
            return [], 0.0

        try:
            if self.embedding is not None:
                # Check if this is a multi-file corpus or legacy code
                if "=== FILE:" in corpus_text:
                    redacted_text = corpus_text
                else:
                    redacted_text = redact_immutable(corpus_text, no_state=True)

                if self.verbose:
                    logger.debug(
                        "=> EMBED: Corpus length - "
                        f"Original: {len(corpus_text)} - "
                        f"Redacted: {len(redacted_text)}"
                    )

                embedding_result, e_cost = self.embedding.get_embedding(redacted_text)
            else:
                if self.verbose:
                    logger.debug("=> EMBED: No embedding model configured.")
                embedding_result = []
                e_cost = 0.0
            code_embedding = cast(List[float], embedding_result)
        except Exception as e:
            logger.warning(f"Could not embed corpus text. Error: {e}")
            code_embedding = []
            e_cost = 0.0
        return code_embedding, e_cost

    def _print_metadata_table(self, meta_data: dict, generation: int):
        """Display metadata in a formatted rich table."""
        # Create title with generation and attempt information
        title_parts = ["[bold magenta]Patch Metadata"]

        # Add generation if present
        if generation is not None:
            title_parts.append(
                f" - Gen {generation}/{self.evo_config.num_generations} - Novelty: {meta_data['novelty_attempt']}/{self.evo_config.max_novelty_attempts} - Resample: {meta_data['resample_attempt']}/{self.evo_config.max_patch_resamples} - Patch: {meta_data['patch_attempt']}/{self.evo_config.max_patch_attempts}"
            )

        # Add attempt information if present
        if all(
            key in meta_data
            for key in [
                "novelty_attempt",
                "resample_attempt",
                "patch_attempt",
                "generation",
            ]
        ):
            title_parts.append(
                f" (Novelty: {meta_data['novelty_attempt']}, "
                f"Resample: {meta_data['resample_attempt']}, "
                f"Patch: {meta_data['patch_attempt']})"
            )

        title_parts.append("[/bold magenta]")
        table = Table(
            title="".join(title_parts),
            show_header=True,
            header_style="bold cyan",
            border_style="magenta",
            box=rich.box.ROUNDED,
            width=120,  # Match display.py table width
        )
        table.add_column("Field", style="cyan bold", no_wrap=True, width=25)
        table.add_column("Value", style="green", overflow="fold", width=90)

        # Define display order and formatting for specific fields
        display_order = [
            "patch_type",
            "patch_name",
            "patch_description",
            "num_applied",
            "api_costs",
            "error_attempt",
        ]

        # Add ordered fields first
        for field_name in display_order:
            if field_name in meta_data:
                value = meta_data[field_name]
                if value is None:
                    formatted_value = "[dim]None[/dim]"
                elif field_name == "api_costs":
                    formatted_value = f"${value:.4f}"
                elif field_name == "error_attempt" and value is None:
                    formatted_value = "[green]Success[/green]"
                elif field_name == "error_attempt":
                    formatted_value = (
                        f"[red]{str(value)[:100]}...[/red]"
                        if len(str(value)) > 100
                        else f"[red]{value}[/red]"
                    )
                else:
                    formatted_value = str(value)

                table.add_row(field_name, formatted_value)

        # Add remaining fields (excluding llm_result, diff_summary, and header info)
        skip_fields = set(
            display_order
            + [
                "llm_result",
                "diff_summary",
                "generation",
                "novelty_attempt",
                "resample_attempt",
                "patch_attempt",
            ]
        )
        for field_key, field_value in meta_data.items():
            if field_key not in skip_fields:
                if field_value is None:
                    formatted_value = "[dim]None[/dim]"
                else:
                    formatted_value = (
                        str(field_value)[:100] + "..."
                        if len(str(field_value)) > 100
                        else str(field_value)
                    )
                table.add_row(field_key, formatted_value)

        # Add diff summary if available
        if "diff_summary" in meta_data and meta_data["diff_summary"]:
            diff_summary = meta_data["diff_summary"]
            if isinstance(diff_summary, dict):
                summary_text = ""
                for k, v in diff_summary.items():
                    summary_text += f"{k}: {v}; "
                table.add_row("diff_summary", summary_text.strip())
            else:
                table.add_row("diff_summary", str(diff_summary)[:200])

        self.console.print(table)

    def _save_meta_memory(self) -> None:
        """Save the meta memory state to disk."""
        meta_memory_path = Path(self.results_dir) / "meta_memory.json"
        self.meta_summarizer.save_meta_state(str(meta_memory_path))

    def _save_bandit_state(self) -> None:
        """Save backend bandit state to JSON for WebUI access."""
        if self.backend_bandit is None:
            return
        try:
            summary = self.backend_bandit.get_summary()
            state_path = Path(self.results_dir) / "bandit_state.json"
            with open(state_path, "w") as f:
                json.dump(summary, f, indent=2)
        except Exception as e:
            if self.verbose:
                logger.debug(f"Failed to save bandit state: {e}")

    def _restore_meta_memory(self) -> None:
        """Restore the meta memory state from disk."""
        meta_memory_path = Path(self.results_dir) / "meta_memory.json"

        if self.verbose:
            logger.info(f"Attempting to restore meta memory from: {meta_memory_path}")

        success = self.meta_summarizer.load_meta_state(str(meta_memory_path))
        if success:
            logger.info("Successfully restored meta memory state")
        else:
            if meta_memory_path.exists():
                logger.warning(
                    f"Meta memory file exists but failed to load: {meta_memory_path}"
                )
            else:
                logger.info("No previous meta memory state found - starting fresh")

    def _cleanup_old_generations(self) -> None:
        """Remove old generation directories to save disk space.
        
        Only runs if cleanup_old_generations is enabled in config.
        Keeps the last N generations as specified by cleanup_keep_last_n.
        Always preserves the 'best' directory.
        """
        if not self.evo_config.cleanup_old_generations:
            return
        
        keep_n = max(1, self.evo_config.cleanup_keep_last_n)
        results_path = Path(self.results_dir)
        
        # Find all generation directories
        gen_dirs = sorted(
            results_path.glob(f"{FOLDER_PREFIX}_*"),
            key=lambda p: int(p.name.split("_")[-1]) if p.name.split("_")[-1].isdigit() else -1,
            reverse=True
        )
        
        # Keep the most recent N directories
        dirs_to_remove = gen_dirs[keep_n:]
        
        for gen_dir in dirs_to_remove:
            try:
                gen_num = gen_dir.name.split("_")[-1]
                if gen_dir.is_dir() and gen_num.isdigit():
                    shutil.rmtree(gen_dir)
                    logger.debug(f"Cleaned up old generation directory: {gen_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup {gen_dir}: {e}")
        
        if dirs_to_remove:
            logger.info(
                f"Cleaned up {len(dirs_to_remove)} old generation directories, "
                f"keeping last {keep_n}"
            )

    def _workspace_root_for_program(self, program: Program) -> Optional[Path]:
        """Return the on-disk directory for the given program's workspace."""

        try:
            generation_idx = int(program.generation)
        except (TypeError, ValueError):
            return None

        workspace = Path(self.results_dir) / f"{FOLDER_PREFIX}_{generation_idx}"
        return workspace if workspace.exists() else None

    def _should_skip_workspace_path(self, rel_path: Path) -> bool:
        """Determine whether a relative workspace path should be ignored."""

        if not rel_path.parts:
            return False

        if rel_path.parts[0] in WORKSPACE_EXCLUDE_DIRS:
            return True

        if rel_path.name in WORKSPACE_EXCLUDE_FILES:
            return True

        if rel_path.suffix in WORKSPACE_EXCLUDE_SUFFIXES:
            return True

        return False

    def _resolve_evaluator_mode(self) -> str:
        """Resolve evaluator mode after considering agentic defaults."""

        mode = (self.evo_config.evaluator.mode or "auto").lower()
        if mode == "legacy":
            return "legacy"
        if mode == "agentic":
            return "agentic"
        if mode == "ensemble":
            # Ensemble mode requires agentic_mode to be enabled
            if not self.evo_config.agentic_mode:
                logger.warning(
                    "Ensemble evaluator mode requires agentic_mode=true, "
                    "falling back to legacy evaluator"
                )
                return "legacy"
            # Also require ensemble to be enabled with evaluators defined
            if not self.evo_config.evaluator.ensemble.enabled:
                logger.warning(
                    "Ensemble mode requested but ensemble.enabled=false, "
                    "falling back to agentic evaluator"
                )
                return "agentic"
            return "ensemble"
        if mode == "auto":
            # Check if ensemble is explicitly enabled
            if (
                self.evo_config.agentic_mode
                and self.evo_config.evaluator.ensemble.enabled
                and self.evo_config.evaluator.ensemble.evaluators
            ):
                return "ensemble"
            return "agentic" if self.evo_config.agentic_mode else "legacy"
        raise ValueError(f"Unknown evaluator mode: {self.evo_config.evaluator.mode}")

    def _collect_parent_workspace_files(self, parent_program: Program) -> Dict[Path, str]:
        """Load additional text files from the parent's workspace."""

        parent_root = self._workspace_root_for_program(parent_program)
        if parent_root is None:
            return {}

        collected: Dict[Path, str] = {}
        for file_path in parent_root.rglob("*"):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(parent_root)
            if self._should_skip_workspace_path(rel_path):
                continue
            # Legacy mode treats main.{lang_ext} as the primary file and excludes it here.
            # In agentic mode we want the full workspace snapshot, so include everything.
            if not self.evo_config.agentic_mode and rel_path == Path(
                f"main.{self.lang_ext}"
            ):
                continue
            try:
                collected[rel_path] = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
        return collected

    def _build_eval_command(self, exec_fname: str, results_dir: str) -> List[str]:
        exec_path = Path(exec_fname)
        workspace_root = exec_path.resolve() if exec_path.is_dir() else exec_path.parent.resolve()
        program_path_arg = exec_fname

        # If an explicit agentic entrypoint is configured, pass it to deterministic evaluators.
        entrypoint = getattr(self.evo_config, "agentic_entrypoint_path", None)
        if self.evo_config.agentic_mode and entrypoint:
            entry_path = Path(entrypoint)
            if not entry_path.is_absolute():
                entry_path = workspace_root / entry_path
            program_path_arg = str(entry_path)

        env_prefix = (
            f"PYTHONPATH={workspace_root}:${{PYTHONPATH:-}} "
            "PYTHONDONTWRITEBYTECODE=1"
        )
        if not self.job_config.eval_program_path:
            # No external evaluator script; agentic evaluator will score directly.
            return []
        cmd = [
            env_prefix,
            "python",
            self.job_config.eval_program_path or "evaluate.py",
            "--program_path",
            program_path_arg,
            "--results_dir",
            results_dir,
        ]
        extra = getattr(self.job_config, "extra_cmd_args", {}) or {}
        for key, value in extra.items():
            cmd.extend([f"--{key}", str(value)])
        return cmd

    def _commit_workspace_changes(
        self,
        program_id: str,
        generation: int,
    ) -> Optional[str]:
        """Commit any changes in the isolated workspace and return the commit SHA.

        This is used to track each program's changes as a git commit,
        enabling diff viewing and patch export in the UI.

        Args:
            program_id: The ID of the program being saved
            generation: The generation number

        Returns:
            Commit SHA if changes were committed, None if no workspace or no changes
        """
        import subprocess

        # Check if we have an isolated workspace (it's at results_dir / "workspace")
        workspace_path = Path(self.results_dir) / "workspace"
        if not workspace_path.exists():
            return None

        # Check if it's a git repo
        git_dir = workspace_path / ".git"
        if not git_dir.exists():
            return None

        try:
            # Check if there are any changes
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if not result.stdout.strip():
                logger.debug(f"No changes to commit for program {program_id[:8]}")
                return None

            # Add all changes
            subprocess.run(
                ["git", "add", "-A"],
                cwd=workspace_path,
                capture_output=True,
                check=True,
                timeout=30,
            )

            # Commit with a descriptive message
            commit_msg = f"Generation {generation}: {program_id[:8]}"
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=workspace_path,
                capture_output=True,
                check=True,
                timeout=30,
            )

            # Get commit SHA
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            commit_sha = result.stdout.strip()
            logger.info(f"Committed workspace changes for program {program_id[:8]}: {commit_sha[:8]}")
            return commit_sha

        except subprocess.SubprocessError as e:
            logger.warning(f"Failed to commit workspace changes: {e}")
            return None
        except Exception as e:
            logger.warning(f"Unexpected error committing workspace: {e}")
            return None

    def _git_commit_generation(
        self,
        generation_dir: Path,
        generation: int,
        node_uuid: str,
        parent_sha: str,
        message: Optional[str] = None,
    ) -> Optional[str]:
        """Create a git commit in evolution.git for the generation directory.

        Uses EvolutionGitManager's mutation_context to create a commit with
        proper worktree lifecycle management and file locking.

        Args:
            generation_dir: Path to the generation's workspace directory
            generation: The generation number
            node_uuid: The program ID (used as node UUID in git refs)
            parent_sha: The parent commit's SHA (from parent program)
            message: Optional commit message (defaults to "Generation {N}")

        Returns:
            Commit SHA if successful, None if git_manager is not available or on error
        """
        if self.git_manager is None:
            return None

        try:
            with self.git_manager.mutation_context(parent_sha, node_uuid) as worktree:
                # Copy generation_dir contents to worktree
                for file_path in generation_dir.rglob("*"):
                    if not file_path.is_file():
                        continue
                    rel_path = file_path.relative_to(generation_dir)
                    # Skip results directories and other non-source files
                    rel_str = str(rel_path)
                    if rel_str.startswith("results") or rel_str.startswith("."):
                        continue
                    target = worktree.path / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file_path, target)

                # Commit changes
                commit_sha = self.git_manager.commit_mutation(
                    worktree,
                    message or f"Generation {generation}",
                    node_uuid,
                )
                logger.info(f"Git commit for gen {generation}: {commit_sha[:8]}")
                return commit_sha
        except Exception as e:
            logger.warning(f"Failed to create git commit for gen {generation}: {e}")
            return None  # Fallback to blob-only storage

    def _run_agentic_evaluation(
        self,
        *,
        exec_fname: str,
        results_dir: str,
        generation_dir: Path,
        generation: int,
        parent_id: Optional[str] = None,
        patch_type: Optional[str] = None,
    ) -> tuple[Dict[str, Any], float]:
        # Check if we should use ensemble evaluator
        use_ensemble = (
            self.evaluator_mode == "ensemble"
            and self.ensemble_evaluator is not None
        )

        if not use_ensemble and self.agentic_evaluator is None:
            raise RuntimeError("Agentic evaluator not initialized")

        # Use generation_dir as workdir so the sandbox allows writes to results_dir
        # This is critical for external workspaces where results_dir is outside the server's repo
        repo_root = generation_dir.resolve()
        Path(results_dir).mkdir(parents=True, exist_ok=True)
        metrics_path = Path(results_dir) / "metrics.json"
        eval_sessions_root = self.agentic_eval_sessions_dir
        eval_sessions_root.mkdir(parents=True, exist_ok=True)
        eval_command = self._build_eval_command(exec_fname, results_dir)
        run_root = Path(self.results_dir).resolve()

        def _rel_to_run_path(raw: Union[str, Path]) -> str:
            try:
                resolved = Path(raw).resolve()
                return str(resolved.relative_to(run_root))
            except Exception:
                return str(raw)

        # --- Evaluation integrity snapshot ---------------------------------
        # Policy: evaluator may create new artifacts (e.g., generated tests),
        # but must not modify or delete any pre-existing candidate source file.
        results_path = Path(results_dir).resolve()
        try:
            results_rel = results_path.relative_to(repo_root)
        except Exception:
            results_rel = None

        ignored_dir_parts = {
            # Job/eval artifacts
            "__pycache__",
            ".pytest_cache",
            ".hydra",
            ".mypy_cache",
            ".ruff_cache",
            # Repo/venv artifacts
            ".git",
            ".venv",
            "node_modules",
        }
        ignored_suffixes = {".pyc", ".pyo"}

        def _should_ignore_integrity_path(rel_path: Path) -> bool:
            if not rel_path.parts:
                return True

            # Always ignore the evaluation results directory for this generation.
            if results_rel is not None and rel_path.parts[: len(results_rel.parts)] == results_rel.parts:
                return True

            if rel_path.suffix in ignored_suffixes:
                return True

            # Ignore transient tooling dirs anywhere in the tree.
            if any(part in ignored_dir_parts for part in rel_path.parts):
                return True

            return False

        def _snapshot_integrity(root: Path) -> Dict[str, str]:
            snapshot: Dict[str, str] = {}
            for abs_path in root.rglob("*"):
                if not abs_path.is_file():
                    continue
                rel = abs_path.relative_to(root)
                if _should_ignore_integrity_path(rel):
                    continue
                try:
                    digest = hashlib.sha256(abs_path.read_bytes()).hexdigest()
                except Exception:
                    # If we can't read a file, treat it as untracked for integrity purposes.
                    continue
                snapshot[rel.as_posix()] = digest
            return snapshot

        integrity_pre = _snapshot_integrity(repo_root)

        start = time.time()
        result = None
        ensemble_result: Optional[EnsembleEvaluationResult] = None

        try:
            if use_ensemble:
                # Use ensemble evaluator - it handles its own workspace copies
                ensemble_result = self.ensemble_evaluator.evaluate(
                    repo_root=repo_root,
                    eval_command=eval_command,
                    program_path=Path(exec_fname),
                    results_path=Path(results_dir),
                    metrics_path=metrics_path,
                    eval_sessions_root=eval_sessions_root,
                    task_name=self.job_config.eval_program_path or "ensemble_evaluator",
                    results_dir=str(self.results_dir),
                    max_score=self.evo_config.max_score,
                    parent_id=parent_id,
                    generation=generation,
                    patch_type=patch_type,
                )
                # Convert ensemble result to AgenticEvaluatorResult for downstream compatibility
                # Use the first evaluator's session info for backward compatibility
                first_eval = ensemble_result.evaluator_results[0] if ensemble_result.evaluator_results else None
                result = AgenticEvaluatorResult(
                    metrics={
                        "combined_score": ensemble_result.combined_score,
                        "score_mean": ensemble_result.score_mean,
                        "score_std": ensemble_result.score_std,
                        "num_evaluators": ensemble_result.num_evaluators,
                        "num_successful": ensemble_result.num_successful,
                        "aggregation_strategy": ensemble_result.aggregation_strategy,
                    },
                    correct=ensemble_result.correct,
                    error_message=None if ensemble_result.num_successful > 0 else ensemble_result.details,
                    stdout_log="",
                    stderr_log="",
                    session_log=[],
                    commands_run=[],
                    session_log_path=first_eval.session_log_path if first_eval and first_eval.session_log_path else metrics_path.parent / "session_log.missing",
                    session_events=[],
                    session_id=first_eval.session_id if first_eval else None,
                    session_dir=first_eval.session_dir if first_eval and first_eval.session_dir else metrics_path.parent,
                    elapsed_seconds=time.time() - start,
                )
            else:
                # Use single agentic evaluator
                result = self.agentic_evaluator.evaluate(
                    repo_root=repo_root,
                    eval_command=eval_command,
                    program_path=Path(exec_fname),
                    results_path=Path(results_dir),
                    metrics_path=metrics_path,
                    eval_sessions_root=eval_sessions_root,
                    task_name=self.job_config.eval_program_path or "agentic_evaluator",
                    results_dir=str(self.results_dir),
                    eval_prompt=getattr(self.evo_config.evaluator.agentic, "eval_prompt", None),
                    max_score=self.evo_config.max_score,
                    parent_id=parent_id,
                    generation=generation,
                    patch_type=patch_type,
                )
        except (CodexExecutionError, GeminiExecutionError, ClaudeExecutionError, ShinkaExecutionError) as exc:
            # If metrics are missing, emit a fallback so the run can proceed
            if not metrics_path.exists():
                metrics_path.parent.mkdir(parents=True, exist_ok=True)
                fallback = {
                    "combined_score": 0.0,
                    "details": f"Agentic evaluator failed: {exc}",
                }
                metrics_path.write_text(json.dumps(fallback), encoding="utf-8")
            # Build a minimal result so downstream logic continues
            metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {"combined_score": 0.0, "details": str(exc)}
            result = AgenticEvaluatorResult(
                metrics=metrics,
                correct=False,
                error_message=str(exc),
                stdout_log="",
                stderr_log="",
                session_log=[],
                commands_run=[],
                session_log_path=metrics_path.parent / "session_log.missing",
                session_events=[],
                session_id=None,
                session_dir=metrics_path.parent,
                elapsed_seconds=time.time() - start,
            )
        rtime = time.time() - start

        integrity_post = _snapshot_integrity(repo_root)
        modified_existing = sorted(
            p for p in integrity_pre.keys() if p in integrity_post and integrity_pre[p] != integrity_post[p]
        )
        deleted_existing = sorted(p for p in integrity_pre.keys() if p not in integrity_post)
        new_files_created = sorted(p for p in integrity_post.keys() if p not in integrity_pre)

        integrity_status = "clean"
        if modified_existing or deleted_existing:
            integrity_status = "violation"
        elif new_files_created:
            integrity_status = "artifacts_only"

        integrity_limit = 200
        integrity_meta = {
            "policy": "no_modify_preexisting_files",
            "status": integrity_status,
            "results_dir_rel": str(results_rel) if results_rel is not None else None,
            "modified_existing_count": len(modified_existing),
            "deleted_existing_count": len(deleted_existing),
            "new_files_created_count": len(new_files_created),
            "modified_existing_files": modified_existing[:integrity_limit],
            "deleted_existing_files": deleted_existing[:integrity_limit],
            "new_files_created": new_files_created[:integrity_limit],
            "truncated": (
                len(modified_existing) > integrity_limit
                or len(deleted_existing) > integrity_limit
                or len(new_files_created) > integrity_limit
            ),
        }

        # If integrity is violated, force the run to be incorrect regardless of evaluator score.
        effective_correct = result.correct
        effective_error = result.error_message
        effective_metrics = dict(result.metrics or {})

        if integrity_status == "violation":
            effective_correct = False
            sample_paths = (modified_existing + deleted_existing)[:10]
            sample_str = ", ".join(sample_paths) if sample_paths else "<unknown>"
            integrity_msg = (
                "Evaluation integrity violation: evaluator modified or deleted "
                f"pre-existing candidate files (sample: {sample_str})."
            )
            if effective_error:
                effective_error = f"{effective_error} | {integrity_msg}"
            else:
                effective_error = integrity_msg

            # Ensure UI-visible fields reflect the integrity failure.
            details = str(effective_metrics.get("details") or "").strip()
            if details:
                effective_metrics["details"] = f"{details}\n\n{integrity_msg}"
            else:
                effective_metrics["details"] = integrity_msg
            effective_metrics["correct"] = False
            effective_metrics["integrity_violation"] = True
        elif integrity_status == "artifacts_only":
            effective_metrics.setdefault("integrity_violation", False)

        events_preview = result.session_events[-AGENTIC_EVAL_PREVIEW_LIMIT:]
        agentic_meta = {
            "session_dir": _rel_to_run_path(result.session_dir),
            "session_log_path": _rel_to_run_path(result.session_log_path),
            "session_id": result.session_id,
            "commands_run": [asdict(cmd) for cmd in result.commands_run],
            "generation": generation,
            "elapsed_seconds": result.elapsed_seconds,
            "status": "error" if effective_error else "success",
            "correct": effective_correct,
            "metrics_path": _rel_to_run_path(metrics_path),
            "results_relpath": _rel_to_run_path(Path(results_dir)),
            "metrics": effective_metrics,
            "error_message": effective_error,
            "stdout_log": result.stdout_log,
            "stderr_log": result.stderr_log,
            "events_preview": events_preview,
            # Prompts sent to the evaluator (for UI display)
            "system_prompt": result.system_prompt,
            "user_prompt": result.user_prompt,
            "integrity": integrity_meta,
        }

        results_payload = {
            "metrics": effective_metrics,
            "correct": {
                "correct": effective_correct,
                "error": effective_error,
            },
            "stdout_log": result.stdout_log,
            "stderr_log": result.stderr_log,
            "agentic_eval": agentic_meta,
        }

        # Add ensemble evaluation metadata if available
        if ensemble_result is not None:
            results_payload["ensemble_evaluation"] = ensemble_result.to_metadata_dict()

        return results_payload, rtime

    def _hydrate_generation_directory(
        self, parent_program: Program, generation_dir: Path
    ) -> None:
        """Pre-populate the target generation directory with the parent's files."""

        parent_root = self._workspace_root_for_program(parent_program)
        if parent_root is None:
            return

        source_root = parent_root.resolve()
        target_dir = generation_dir.resolve()

        if not source_root.exists():
            return

        self._fast_copy_workspace_tree(
            source_root,
            target_dir,
            dirs_exist_ok=True,
        )

    def _fast_copy_workspace_tree(
        self,
        source_root: Path,
        target_dir: Path,
        *,
        dirs_exist_ok: bool = False,
        exclude_dirs: Optional[Set[str]] = None,
        exclude_suffixes: Optional[Set[str]] = None,
        exclude_files: Optional[Set[str]] = None,
    ) -> None:
        """Copy a workspace tree using platform CoW when possible."""
        from shinka.core.fs_utils import fast_copy

        fast_copy(
            source_root,
            target_dir,
            dirs_exist_ok=dirs_exist_ok,
            exclude_dirs=(
                exclude_dirs if exclude_dirs is not None else WORKSPACE_EXCLUDE_DIRS
            ),
            exclude_suffixes=(
                exclude_suffixes
                if exclude_suffixes is not None
                else WORKSPACE_EXCLUDE_SUFFIXES
            ),
            exclude_files=(
                exclude_files if exclude_files is not None else WORKSPACE_EXCLUDE_FILES
            ),
        )

    def _copy_support_tree(
        self,
        support_root: Path,
        target_dir: Path,
        exclude_file: Optional[Path] = None,
    ) -> None:
        """Copy auxiliary source files for generation 0."""

        if not support_root.exists():
            return

        source_root = support_root.resolve()
        excluded = exclude_file.resolve() if exclude_file else None

        for file_path in source_root.rglob("*"):
            if not file_path.is_file():
                continue
            if excluded and file_path.resolve() == excluded:
                continue
            rel_path = file_path.relative_to(source_root)
            if self._should_skip_workspace_path(rel_path):
                continue
            target_path = target_dir / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, target_path)
