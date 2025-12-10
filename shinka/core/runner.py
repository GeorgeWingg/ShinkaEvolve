import atexit
import base64
import difflib
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
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union, cast, Literal
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
from shinka.eval import AgenticEvaluator
from shinka.eval.agentic import AgenticEvaluatorResult
from shinka.core.sampler import PromptSampler
from shinka.core.summarizer import MetaSummarizer
from shinka.core.novelty_judge import NoveltyJudge
from shinka.core.embedding_corpus import (
    build_embedding_corpus,
    extract_file_content,
    EmbeddingCorpus,
)
from shinka.logo import print_gradient_logo

FOLDER_PREFIX = "gen"

WORKSPACE_EXCLUDE_DIRS = {
    "results",
    "workspace_snapshot",
    "agent_sessions",
    ".hydra",
    "__pycache__",
}
WORKSPACE_EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
WORKSPACE_EXCLUDE_FILES = {
    "rewrite.txt",
    "edit.diff",
    "session_log.jsonl",
}

AGENTIC_EVAL_PREVIEW_LIMIT = 200


@dataclass
class AgenticConfig:
    """Configuration options for agentic editing sessions.
    
    This config is backend-agnostic: it works with Codex, Gemini, Claude,
    or ShinkaAgent backends. The `backend` field selects which one to use.
    
    When `bandit_selection` is True, the backend is dynamically selected
    using a multi-armed bandit (UCB) algorithm that learns which backend
    performs best over time.
    """

    backend: str = "codex"
    cli_profile: Optional[str] = None
    sandbox: str = "workspace-write"
    approval_mode: str = "full-auto"
    max_turns: int = 50
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
    
    This config is backend-agnostic: the evaluator uses whatever backend
    is configured in the parent AgenticConfig.
    """

    cli_profile: Optional[str] = None
    sandbox: str = "workspace-write"
    approval_mode: str = "full-auto"
    max_turns: int = 80
    max_seconds: int = 0
    cli_path: Optional[str] = None
    extra_cli_config: Dict[str, Any] = field(default_factory=dict)
    eval_prompt: Optional[str] = None

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
class EvaluatorConfig:
    """Evaluator selection and configuration."""

    mode: Literal["auto", "legacy", "agentic"] = "auto"
    agentic: AgenticEvaluatorConfig = field(default_factory=AgenticEvaluatorConfig)


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
    results_dir: Optional[str] = None
    max_novelty_attempts: int = 3
    code_embed_sim_threshold: float = 1.0
    novelty_llm_models: Optional[List[str]] = None
    novelty_llm_kwargs: dict = field(default_factory=lambda: {})
    use_text_feedback: bool = False
    agentic_mode: bool = False
    agentic: AgenticConfig = field(default_factory=AgenticConfig)
    evaluator: EvaluatorConfig = field(default_factory=EvaluatorConfig)
    max_score: float = 1.0  # Maximum possible score (defines the scale)


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
    # For agentic parallel execution - stores the future and its result
    agentic_future: Optional[Future] = None
    agentic_result: Optional[Tuple[Dict[str, Any], float]] = None


# Set up logging
logger = logging.getLogger(__name__)


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
        
        # Register cleanup handlers to remove PID file on exit
        atexit.register(self._cleanup_pid_file)
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
        else:
            self.backend_bandit = None

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
        if self.evaluator_mode == "agentic":
            if self.evo_config.agentic.backend == "gemini":
                runner_fn = run_gemini_task
            elif self.evo_config.agentic.backend == "claude":
                runner_fn = run_claude_task
            elif self.evo_config.agentic.backend == "shinka":
                runner_fn = run_shinka_task
            else:
                runner_fn = run_codex_task
            self.agentic_evaluator: Optional[AgenticEvaluator] = AgenticEvaluator(
                self.evo_config.evaluator.agentic,
                agent_runner=runner_fn,
            )
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
        self.novelty_judge = NoveltyJudge(
            novelty_llm_client=self.novelty_llm,
            language=evo_config.language,
            similarity_threshold=evo_config.code_embed_sim_threshold,
            max_novelty_attempts=evo_config.max_novelty_attempts,
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
        
        # ThreadPoolExecutor for parallel agentic jobs
        self._agentic_executor: Optional[ThreadPoolExecutor] = None
        if self.evo_config.agentic_mode:
            max_workers = max(1, self.evo_config.max_parallel_jobs)
            self._agentic_executor = ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix="agentic_worker",
            )
            logger.info(f"Initialized agentic executor with {max_workers} workers")

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

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle SIGTERM/SIGINT by cleaning up PID file and exiting."""
        self._cleanup_pid_file()
        # Re-raise the signal with default handler to ensure proper exit
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

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

    def run(self):
        """Run evolution with parallel job queue."""
        max_jobs = self.evo_config.max_parallel_jobs
        target_gens = self.evo_config.num_generations
        logger.info(
            f"Starting evolution with {max_jobs} parallel jobs, "
            f"target: {target_gens} generations"
        )

        # First, run generation 0 sequentially to populate the database
        if self.completed_generations == 0 and target_gens > 0:
            logger.info("Running generation 0 sequentially to initialize database...")
            self._run_generation_0()
            self.completed_generations = 1
            self.next_generation_to_submit = 1
            logger.info(f"Completed generation 0, total: 1/{target_gens}")

        # Now start parallel execution for remaining generations
        if self.completed_generations < target_gens:
            logger.info("Starting parallel execution for remaining generations...")

            # Main loop: monitor jobs and submit new ones
            while (
                self.completed_generations < target_gens or len(self.running_jobs) > 0
            ):
                # Check for completed jobs
                completed_jobs = self._check_completed_jobs()

                # Process completed jobs
                if completed_jobs:
                    for job in completed_jobs:
                        self._process_completed_job(job)

                    # Update completed generations count
                    self._update_completed_generations()

                    if self.verbose:
                        logger.info(
                            f"Processed {len(completed_jobs)} jobs. "
                            f"Total completed generations: "
                            f"{self.completed_generations}/{target_gens}"
                        )

                # Check if we've completed all generations
                if self.completed_generations >= target_gens:
                    logger.info("All generations completed, exiting...")
                    break

                # Submit new jobs to fill the queue (only if we have capacity)
                if (
                    len(self.running_jobs) < max_jobs
                    and self.next_generation_to_submit < target_gens
                ):
                    self._submit_new_job()

                # Wait a bit before checking again
                time.sleep(2)

            # All jobs are now handled by the main loop above

        # Shutdown the agentic executor if it was created
        if self._agentic_executor is not None:
            logger.info("Shutting down agentic executor...")
            self._agentic_executor.shutdown(wait=True)
            self._agentic_executor = None

        # Perform final meta summary for any remaining unprocessed programs
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
        Path(initial_dir).mkdir(parents=True, exist_ok=True)
        exec_fname = f"{initial_dir}/main.{self.lang_ext}"
        results_dir = f"{self.results_dir}/{FOLDER_PREFIX}_0/results"
        Path(results_dir).mkdir(parents=True, exist_ok=True)

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

                def ignore_patterns(directory, files):
                    """Ignore results directory and common non-source files."""
                    ignored = []
                    for f in files:
                        # Ignore results directory to prevent recursive copying
                        if f == 'results':
                            ignored.append(f)
                        # Also ignore common non-source directories
                        elif f in ('__pycache__', '.git', '.venv', 'node_modules', '.pytest_cache'):
                            ignored.append(f)
                    return ignored

                shutil.copytree(init_path, Path(initial_dir), dirs_exist_ok=True, ignore=ignore_patterns)
                # Create a placeholder exec_fname if it doesn't exist
                if not Path(exec_fname).exists():
                    # Write a minimal placeholder for agentic mode
                    with open(exec_fname, 'w') as f:
                        f.write("# Initial placeholder - agent will create the implementation\n")
                        f.write("# Task: " + (self.evo_config.task_sys_msg or "Evolve this code") + "\n")
            else:
                # For single files, copy as before
                shutil.copy(init_path, exec_fname)
                if self.evo_config.init_support_dir:
                    support_root = Path(self.evo_config.init_support_dir)
                    self._copy_support_tree(
                        support_root,
                        Path(initial_dir),
                        exclude_file=init_path,
                    )
        else:
            if self.evo_config.agentic_mode:
                # Agentic open-ended: create minimal empty file, skip evaluation.
                # The agentic editor in gen_1 will create real code from scratch.
                if self.verbose:
                    logger.info("`init_program_path` not provided; creating empty seed for agentic run (no gen_0 eval).")
                Path(exec_fname).parent.mkdir(parents=True, exist_ok=True)
                with open(exec_fname, "w", encoding="utf-8") as f:
                    f.write("")  # Empty file - agent creates everything in gen_1
                api_costs = 0.0
                patch_name = "empty_seed"
                patch_description = "Empty seed for agentic open-ended evolution."

                # Skip evaluation for empty seed - insert directly into DB
                initial_corpus = self._build_embedding_corpus(Path(initial_dir), {})
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

                db_program = Program(
                    id=str(uuid.uuid4()),
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
                with open(exec_fname, "w", encoding="utf-8") as f:
                    f.write(initial_code)

                if self.verbose:
                    logger.info(f"Initial program generated and saved to {exec_fname}")

        if self.evaluator_mode == "agentic":
            results, rtime = self._run_agentic_evaluation(
                exec_fname=exec_fname,
                results_dir=results_dir,
                generation_dir=Path(initial_dir),
                generation=0,
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

        db_program = Program(
            id=str(uuid.uuid4()),
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
                **corpus_meta,
            },
        )
        if agentic_eval_meta:
            if db_program.metadata is None:
                db_program.metadata = {}
            db_program.metadata["agentic_evaluator"] = agentic_eval_meta

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

    def _submit_new_job(self):
        """Submit a new job to the queue."""
        current_gen = self.next_generation_to_submit

        if current_gen >= self.evo_config.num_generations:
            return

        self.next_generation_to_submit += 1

        exec_fname = (
            f"{self.results_dir}/{FOLDER_PREFIX}_{current_gen}/main.{self.lang_ext}"
        )
        results_dir = f"{self.results_dir}/{FOLDER_PREFIX}_{current_gen}/results"
        Path(results_dir).mkdir(parents=True, exist_ok=True)
        generation_dir = Path(self.results_dir) / f"{FOLDER_PREFIX}_{current_gen}"
        generation_dir.mkdir(parents=True, exist_ok=True)

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
            archive_insp_ids = []
            top_k_insp_ids = []
            code_diff = None
            meta_patch_data = {}
            # Initial program already copied in setup_initial_program
        else:
            api_costs = 0
            embed_cost = 0
            novelty_cost = 0.0
            novelty_checks_performed = 0
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
                    api_costs += meta_patch_data["api_costs"]
                    if (
                        meta_patch_data["error_attempt"] is None
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
                if self.novelty_judge.should_check_novelty(
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

    def _check_completed_jobs(self) -> List[RunningJob]:
        """Check for completed jobs and return them."""
        completed = []
        still_running = []

        # Take a thread-safe snapshot of jobs to check
        with self._jobs_lock:
            jobs_snapshot = list(self.running_jobs)

        for job in jobs_snapshot:
            # Check agentic futures first
            if job.agentic_future is not None:
                if job.agentic_future.done():
                    # Agentic job completed
                    try:
                        job.agentic_result = job.agentic_future.result()
                        if self.verbose:
                            logger.info(f"Agentic job {job.job_id} completed!")
                        completed.append(job)
                    except Exception as e:
                        logger.error(f"Agentic job {job.job_id} failed: {e}")
                        # Store error result so we can handle it in _process_completed_job
                        job.agentic_result = (
                            {"correct": {"correct": False, "error": str(e)}, "metrics": {}},
                            time.time() - job.start_time,
                        )
                        completed.append(job)
                else:
                    # Still running
                    still_running.append(job)
            else:
                # Legacy scheduler-based job
                is_running = self.scheduler.check_job_status(job)
                if not is_running:
                    # Job completed
                    if self.verbose:
                        logger.info(f"Job {job.job_id} completed!")
                    completed.append(job)
                else:
                    # Job still running
                    still_running.append(job)

        # Update jobs list atomically
        with self._jobs_lock:
            self.running_jobs = still_running
        
        # Update active_jobs.json after jobs complete
        if completed:
            if self.running_jobs:
                self._write_active_jobs_file()
            else:
                self._clear_active_jobs_file()
        
        return completed

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

        # Generate program ID first so we can use it in commit message
        program_id = str(uuid.uuid4())

        # Commit workspace changes and get the commit SHA (for isolated workspaces)
        commit_sha = self._commit_workspace_changes(program_id, job.generation)

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
            },
        )
        if agentic_eval_meta:
            if db_program.metadata is None:
                db_program.metadata = {}
            db_program.metadata["agentic_evaluator"] = agentic_eval_meta
        self.db.add(db_program, verbose=True)

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

        shutil.copytree(source_dir, best_dir)

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
    ) -> tuple[Optional[str], dict, int]:
        """Execute an agentic editing session via the Codex CLI."""

        primary_filename = Path(f"main.{self.lang_ext}")
        
        # Extract content from corpus; fallback to raw code if not a corpus
        primary_content = extract_file_content(parent_program.code, str(primary_filename))
        if primary_content is None:
            # If no headers found, assume it's legacy raw code
            if "=== FILE:" not in parent_program.code:
                primary_content = parent_program.code
            else:
                # Try generic 'main.py' if language-specific path failed
                primary_content = extract_file_content(parent_program.code, "main.py")
                if primary_content is None:
                    # Fallback to using the whole thing (likely broken but consistent)
                    primary_content = parent_program.code

        base_files: Dict[Path, str] = {primary_filename: primary_content}
        base_files.update(self._collect_parent_workspace_files(parent_program))
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
                if rel_path == primary_filename:
                    # Primary file already stored in program.code
                    continue
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

        # Initialize selected_backend before inner functions so closures can capture it.
        # This will be updated by bandit sampling if enabled.
        selected_backend = self.evo_config.agentic.backend

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
                "agent_primary_file": str(primary_filename),
                "diff_summary": {},
                "model_name": _agent_model_name(selected_backend),
                "agent_backend": selected_backend,
                "agent_session_id": session_id,
                "agent_resumed_from_parent": resumed_from_parent,
                "agent_resume_source_session_id": resume_session_id,
                "bandit_posteriors": (
                    self.backend_bandit.get_summary() if self.backend_bandit else None
                ),
            }
            return None, meta_edit_data, 0

        # Determine which backend to use: either sample from bandit or use config
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

        try:
            if selected_backend == "gemini":
                ensure_gemini_available(self.evo_config.agentic.cli_path)
            elif selected_backend == "claude":
                ensure_claude_available(self.evo_config.agentic.cli_path)
            elif selected_backend == "shinka":
                ensure_shinka_available()
            else:
                ensure_codex_available(self.evo_config.agentic.cli_path)
        except (CodexUnavailableError, GeminiUnavailableError, ClaudeUnavailableError, ShinkaUnavailableError) as exc:  # pragma: no cover - system dep
            return failure_meta(str(exc))

        # Create scratch directory outside any git repo to prevent Codex CLI from
        # discovering parent AGENTS.md files. If scratch_dir_base is None, fall
        # back to the results directory (legacy behavior).
        session_uuid = str(uuid.uuid4())
        if self.evo_config.agentic.scratch_dir_base:
            scratch_base = Path(self.evo_config.agentic.scratch_dir_base)
            scratch_base.mkdir(parents=True, exist_ok=True)
            session_root = scratch_base / session_uuid
        else:
            session_root = (
                Path(self.results_dir)
                / "agent_sessions"
                / session_uuid
            )

        # Write session metadata for visualization to track in-progress jobs
        session_root.mkdir(parents=True, exist_ok=True)
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

        helper_files = [
            rel_path
            for rel_path in base_files.keys()
            if rel_path != primary_filename
        ]
        
        system_prompt = patch_sys.strip()
        if helper_files:
            helper_listing = "\n".join(
                f"- {path.as_posix()}" for path in sorted(helper_files)
            )
            system_prompt += (
                "\n\n# Workspace Files\n"
                "The following helper files were copied from the parent program; "
                "edit any that your improvement touches so the evaluator can run without manual fixes:\n"
                f"{helper_listing}"
                "\n\nWhen helper files are available you are expected to update at least one of them whenever your idea relies on shared utilities. "
                "If you believe no helper change is required, record that decision with a short comment inside the most relevant helper file so the run is not discarded."
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
                else run_codex_task
            ),
        )

        try:
            agent_result = editor.run_session(context)
        except (CodexExecutionError, GeminiExecutionError, ClaudeExecutionError, ShinkaExecutionError) as exc:
            return failure_meta(str(exc))

        generation_dir = Path(self.results_dir) / f"{FOLDER_PREFIX}_{generation}"
        if generation_dir.exists():
            shutil.rmtree(generation_dir)
        generation_dir.mkdir(parents=True, exist_ok=True)
        self._hydrate_generation_directory(parent_program, generation_dir)

        patch_dir = str(generation_dir)

        primary_content = agent_result.changed_files.get(
            context.primary_file, base_files[context.primary_file]
        )

        # Use the extracted primary file content (without corpus headers) as the
        # original for patch application, not the full corpus text.
        original_for_patch = base_files[context.primary_file]

        # Special case: if parent is empty (open-ended agentic), skip patch system
        # and write content directly - there's nothing to diff against
        if not original_for_patch.strip():
            # Parent was empty, just write the new content directly
            output_path = Path(patch_dir) / f"main.{self.lang_ext}"
            output_path.write_text(primary_content, encoding="utf-8")
            num_applied = 1
            error_msg = None
            patch_txt = None
            patch_path = None
        else:
            # Bypass legacy apply_full_patch for agentic mode to avoid EVOLVE-BLOCK enforcement.
            # We trust the agent's full file output.
            output_path = Path(patch_dir) / f"main.{self.lang_ext}"
            output_path.write_text(primary_content, encoding="utf-8")
            num_applied = 1
            error_msg = None
            
            # Generate a diff for logging/legacy compatibility
            patch_txt = None
            patch_path = None
            try:
                 # Simple unified diff
                 diff_lines = difflib.unified_diff(
                    original_for_patch.splitlines(keepends=True),
                    primary_content.splitlines(keepends=True),
                    fromfile=f"original.{self.lang_ext}",
                    tofile=f"main.{self.lang_ext}",
                 )
                 patch_txt = "".join(diff_lines)
                 if patch_txt:
                     patch_path = Path(patch_dir) / "edit.diff"
                     patch_path.write_text(patch_txt, encoding="utf-8")
                     # Also write backup of original for completeness
                     (Path(patch_dir) / f"original.{self.lang_ext}").write_text(original_for_patch, encoding="utf-8")
            except Exception as e:
                 logger.warning(f"Failed to generate diff for agentic edit: {e}")

        if error_msg is not None or num_applied == 0:
            return failure_meta(
                f"Agentic edit violated immutable regions: {error_msg or 'no changes applied'}",
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

        # Persist any additional files produced during the session.
        for rel_path, content in agent_result.changed_files.items():
            if rel_path == context.primary_file:
                continue
            target = generation_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        for rel_path, b64_content in agent_result.binary_changed_files.items():
            target = generation_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(b64_content))

        # Snapshot entire agent workspace for auditing/debugging.
        snapshot_dir: Optional[Path] = None
        if session_root.exists():
            snapshot_dir = generation_dir / "workspace_snapshot"
            if snapshot_dir.exists():
                shutil.rmtree(snapshot_dir)
            shutil.copytree(session_root, snapshot_dir, dirs_exist_ok=True)
            # Clean up temp scratch directory if it was created outside results_dir
            if self.evo_config.agentic.scratch_dir_base:
                shutil.rmtree(session_root, ignore_errors=True)

        if self.evaluator_mode == "agentic":
            backup_file = generation_dir / f"original.{self.lang_ext}"
            if backup_file.exists():
                backup_file.unlink()

        diff_summary = {}
        if patch_path is not None:
            diff_summary = summarize_diff(str(patch_path))
            # Only keep the diff for the edited primary file to match legacy behavior
            original_filename = f"original.{self.lang_ext}"
            if original_filename in diff_summary:
                diff_summary = diff_summary[original_filename]

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
            "agent_primary_file": str(primary_filename),
            "diff_summary": diff_summary,
            "model_name": _agent_model_name(selected_backend, agent_result.model),
            "agent_backend": selected_backend,
            "agent_session_id": agent_result.session_id,
            "agent_resumed_from_parent": resumed_from_parent,
            "agent_resume_source_session_id": resume_session_id,
            "bandit_posteriors": (
                self.backend_bandit.get_summary() if self.backend_bandit else None
            ),
        }

        return patch_txt, meta_edit_data, num_applied

    def _build_embedding_corpus(
        self, generation_dir: Path, meta_patch_data: Optional[dict]
    ) -> EmbeddingCorpus:
        """Construct the artifact corpus for embeddings and novelty checks."""
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
        if mode == "auto":
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
            if rel_path == Path(f"main.{self.lang_ext}"):
                continue
            try:
                collected[rel_path] = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
        return collected

    def _build_eval_command(self, exec_fname: str, results_dir: str) -> List[str]:
        program_dir = str(Path(exec_fname).parent.resolve())
        env_prefix = f"PYTHONPATH={program_dir}:${{PYTHONPATH:-}}"
        if not self.job_config.eval_program_path:
            # No external evaluator script; agentic evaluator will score directly.
            return []
        cmd = [
            env_prefix,
            "python",
            self.job_config.eval_program_path or "evaluate.py",
            "--program_path",
            exec_fname,
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

    def _run_agentic_evaluation(
        self,
        *,
        exec_fname: str,
        results_dir: str,
        generation_dir: Path,
        generation: int,
    ) -> tuple[Dict[str, Any], float]:
        if self.agentic_evaluator is None:
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

        start = time.time()
        result = None
        try:
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

        events_preview = result.session_events[-AGENTIC_EVAL_PREVIEW_LIMIT:]
        agentic_meta = {
            "session_dir": _rel_to_run_path(result.session_dir),
            "session_log_path": _rel_to_run_path(result.session_log_path),
            "session_id": result.session_id,
            "commands_run": [asdict(cmd) for cmd in result.commands_run],
            "generation": generation,
            "elapsed_seconds": result.elapsed_seconds,
            "status": "error" if result.error_message else "success",
            "correct": result.correct,
            "metrics_path": _rel_to_run_path(metrics_path),
            "results_relpath": _rel_to_run_path(Path(results_dir)),
            "metrics": result.metrics,
            "error_message": result.error_message,
            "stdout_log": result.stdout_log,
            "stderr_log": result.stderr_log,
            "events_preview": events_preview,
        }

        results_payload = {
            "metrics": result.metrics,
            "correct": {
                "correct": result.correct,
                "error": result.error_message,
            },
            "stdout_log": result.stdout_log,
            "stderr_log": result.stderr_log,
            "agentic_eval": agentic_meta,
        }

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

        # Use CoW (Copy-on-Write) when available for ~100x disk savings on large codebases
        # Pass exclusions as sets instead of callback to preserve CoW benefits
        from shinka.core.fs_utils import fast_copy
        fast_copy(
            source_root,
            target_dir,
            dirs_exist_ok=True,
            exclude_dirs=WORKSPACE_EXCLUDE_DIRS,
            exclude_suffixes=WORKSPACE_EXCLUDE_SUFFIXES,
            exclude_files=WORKSPACE_EXCLUDE_FILES,
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
