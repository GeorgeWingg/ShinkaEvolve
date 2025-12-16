"""Configuration builder for converting UI form data to EvolutionRunner configs."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from shinka.core.runner import (
    AgenticConfig,
    AgenticEvaluatorConfig,
    AggregationConfig,
    EnsembleEvaluatorConfig,
    EvaluatorConfig,
    EvaluatorInstanceConfig,
    EvolutionConfig,
    JulesConfig,
)
from shinka.database import DatabaseConfig
from shinka.launch import LocalJobConfig, SlurmCondaJobConfig


@dataclass
class UIRunConfig:
    """Flattened configuration from UI modal.

    This dataclass represents the form data from the New Run modal,
    flattened into a single structure for easy manipulation.
    """

    # Codebase configuration
    source_type: str = "local"  # "local" | "git"
    local_path: str = ""
    git_url: str = ""
    git_branch: str = "main"
    git_workspace_path: str = ""  # Where to clone git repos (empty = auto)
    use_worktree: bool = True
    # For local source_type only: how to isolate when path is a git repo.
    # "auto" (default) picks worktree for clean repos and snapshot copy for dirty repos.
    # "worktree" forces a cached worktree (falls back to snapshot copy if dirty).
    # "snapshot_copy" always copies (preserves uncommitted changes).
    local_isolation_strategy: str = "auto"
    init_program_path: str = ""
    init_support_dir: Optional[str] = None
    include_patterns: List[str] = field(default_factory=lambda: ["**/*.py"])
    exclude_patterns: List[str] = field(
        default_factory=lambda: ["results/**", "__pycache__/**", ".git/**"]
    )
    language: str = "python"
    
    # Embedding scalability settings (for large codebases)
    embedding_max_files: int = 500
    embedding_max_total_bytes: int = 2_000_000  # 2MB
    embedding_max_bytes_per_file: int = 500_000  # 500KB
    cleanup_old_generations: bool = False
    cleanup_keep_last_n: int = 50

    # Agent configuration
    agentic_mode: bool = True
    agent_backend: str = "shinka"  # "shinka" | "codex" | "gemini" | "claude" | "jules"
    # Explicit model selection (takes precedence over cli_profile for Claude/Gemini/ShinkaAgent)
    agent_model: Optional[str] = None
    agent_max_turns: int = 50
    agent_max_seconds: int = 0
    agent_sandbox: str = "workspace-write"
    agent_approval_mode: str = "full-auto"
    resume_parent_session: bool = False
    llm_models: List[str] = field(default_factory=lambda: ["gpt-4.1"])
    llm_dynamic_selection: Optional[str] = None
    embedding_model: str = "text-embedding-3-small"
    task_sys_msg: str = ""  # Evolution prompt
    patch_types: List[str] = field(default_factory=lambda: ["diff", "full"])

    # Evaluator configuration
    evaluator_mode: str = "agentic"  # "legacy" | "agentic" | "auto" | "ensemble"
    eval_program_path: str = ""
    eval_backend: str = "codex"  # Recommended for evaluation
    eval_max_turns: int = 80
    eval_sandbox: str = "workspace-write"
    eval_prompt: str = ""  # Agentic evaluator prompt

    # Ensemble evaluator configuration
    evaluator_ensemble_enabled: bool = False
    evaluator_ensemble: List[Dict[str, Any]] = field(default_factory=list)
    # Structure: [{"name": "primary", "backend": "codex", "model": null, "eval_prompt": "...", "weight": 1.0, "max_events": 80, "enabled": true}, ...]
    evaluator_aggregation_strategy: str = "average"  # best_score | worst_case | average | weighted_average | majority_vote | median
    evaluator_aggregation_vote_threshold: float = 0.5
    evaluator_aggregation_min_successful: int = 1
    evaluator_max_parallel_evaluators: int = 0  # 0 = no limit

    # Run configuration
    run_name: str = ""
    results_dir: str = "results"
    num_generations: int = 20
    max_parallel_jobs: int = 1
    max_patch_attempts: int = 10
    max_patch_resamples: int = 3
    max_novelty_attempts: int = 3

    # Island configuration
    num_islands: int = 2
    archive_size: int = 20
    migration_interval: int = 10
    migration_rate: float = 0.1
    island_elitism: bool = True

    # Score configuration
    max_score: float = 1.0  # Maximum possible score (defines the scale)
    max_runtime_hours: int = 0
    max_api_cost: float = 0.0

    # Meta-learning / Scratchpad configuration
    meta_rec_interval: Optional[int] = None
    meta_llm_models: Optional[List[str]] = None
    scratchpad_enabled: bool = False
    scratchpad_backend: str = "codex"  # "codex" | "gemini" | "claude" | "shinka" | "jules"
    scratchpad_interval: int = 5
    scratchpad_max_recommendations: int = 5

    # Jules-specific configuration (for cloud agent)
    jules_github_repo: str = ""
    jules_base_branch: str = "main"
    jules_automation_mode: str = "AUTO_CREATE_PR"
    jules_poll_interval: int = 15
    jules_cleanup_branch: bool = True
    jules_auto_approve_plan: bool = True

    # Backend Bandit configuration (multi-backend selection)
    bandit_enabled: bool = False
    bandit_backends: List[str] = field(
        default_factory=lambda: ["codex", "gemini", "claude", "shinka", "jules"]
    )
    bandit_epsilon: float = 0.1
    bandit_exploration_coef: float = 1.0
    bandit_auto_decay: float = 0.95
    bandit_use_global_history: bool = False  # Load priors from ~/.shinka/bandit_history.json
    bandit_record_to_history: bool = True    # Save interactions to global history

    # Job configuration
    job_type: str = "local"  # "local" | "slurm_conda" | "slurm_docker"
    slurm_time: str = "00:10:00"
    slurm_cpus: int = 1
    slurm_gpus: int = 0
    slurm_mem: str = "8G"
    slurm_conda_env: str = "shinka"
    slurm_modules: List[str] = field(default_factory=list)
    slurm_partition: str = "gpu"


class RunConfigBuilder:
    """Converts UI config to EvolutionRunner-compatible configs."""

    def __init__(self, ui_config: UIRunConfig, workspace_path: Optional[Path] = None):
        """Initialize the config builder.

        Args:
            ui_config: The UI configuration from the modal form
            workspace_path: The resolved workspace path (for git sources, this is
                          the worktree/clone path; for local, it's the local_path)
        """
        self.ui = ui_config
        if workspace_path:
            self.workspace_path = Path(workspace_path)
        elif ui_config.local_path:
            self.workspace_path = Path(ui_config.local_path)
        else:
            self.workspace_path = Path.cwd()

    def build_evolution_config(self) -> EvolutionConfig:
        """Build EvolutionConfig from UI settings.

        Returns:
            Configured EvolutionConfig instance
        """
        # Build AgenticConfig with bandit settings
        bandit_kwargs = {}
        if self.ui.bandit_enabled:
            bandit_kwargs = {
                "epsilon": self.ui.bandit_epsilon,
                "exploration_coef": self.ui.bandit_exploration_coef,
                "auto_decay": self.ui.bandit_auto_decay,
                "allowed_backends": self.ui.bandit_backends,
            }

        # Build JulesConfig if Jules backend is selected
        jules_cfg = None
        if self.ui.agent_backend == "jules":
            jules_cfg = JulesConfig(
                github_repo=self.ui.jules_github_repo,
                base_branch=self.ui.jules_base_branch,
                automation_mode=self.ui.jules_automation_mode,
                poll_interval=self.ui.jules_poll_interval,
                cleanup_branch=self.ui.jules_cleanup_branch,
                auto_approve_plan=self.ui.jules_auto_approve_plan,
            )

        agentic_cfg = AgenticConfig(
            backend=self.ui.agent_backend,
            model=self.ui.agent_model,  # NEW: Explicit model field
            sandbox=self.ui.agent_sandbox,
            approval_mode=self.ui.agent_approval_mode,
            max_events=self.ui.agent_max_turns,  # Use canonical max_events field
            max_seconds=self.ui.agent_max_seconds,
            resume_parent_session=self.ui.resume_parent_session,
            bandit_selection=self.ui.bandit_enabled,
            bandit_kwargs=bandit_kwargs,
            use_global_bandit_history=self.ui.bandit_use_global_history,
            record_to_global_history=self.ui.bandit_record_to_history,
            jules=jules_cfg,  # NEW: Typed Jules config (bridged to extra_cli_config in __post_init__)
        )

        # Build EvaluatorConfig
        agentic_eval = AgenticEvaluatorConfig(
            sandbox=self.ui.eval_sandbox,
            max_events=self.ui.eval_max_turns,  # Use canonical max_events field
            eval_prompt=self.ui.eval_prompt or None,
        )

        # Build ensemble config if enabled
        ensemble_cfg = EnsembleEvaluatorConfig(enabled=False)
        if self.ui.evaluator_ensemble_enabled and self.ui.evaluator_ensemble:
            # Convert UI evaluator list to EvaluatorInstanceConfig dict
            evaluators: Dict[str, EvaluatorInstanceConfig] = {}
            for eval_data in self.ui.evaluator_ensemble:
                name = eval_data.get("name", f"evaluator_{len(evaluators)}")
                evaluators[name] = EvaluatorInstanceConfig(
                    name=name,
                    backend=eval_data.get("backend"),
                    model=eval_data.get("model"),
                    eval_prompt=eval_data.get("eval_prompt"),
                    weight=eval_data.get("weight", 1.0),
                    max_events=eval_data.get("max_events", self.ui.eval_max_turns),
                    enabled=eval_data.get("enabled", True),
                    extra_cli_config=eval_data.get("extra_cli_config", {}),
                )

            ensemble_cfg = EnsembleEvaluatorConfig(
                enabled=True,
                defaults=EvaluatorInstanceConfig(
                    backend=self.ui.eval_backend,
                    sandbox=self.ui.eval_sandbox,
                    max_events=self.ui.eval_max_turns,
                ),
                evaluators=evaluators,
                aggregation=AggregationConfig(
                    strategy=self.ui.evaluator_aggregation_strategy,
                    vote_threshold=self.ui.evaluator_aggregation_vote_threshold,
                    min_successful_evals=self.ui.evaluator_aggregation_min_successful,
                ),
                max_parallel_evaluators=self.ui.evaluator_max_parallel_evaluators,
            )

        # Determine evaluator mode
        eval_mode = self.ui.evaluator_mode
        if self.ui.evaluator_ensemble_enabled and self.ui.evaluator_ensemble:
            eval_mode = "ensemble"

        evaluator_cfg = EvaluatorConfig(
            mode=eval_mode,
            agentic=agentic_eval,
            ensemble=ensemble_cfg,
        )

        # Determine patch types and probabilities
        if self.ui.agentic_mode:
            # Agentic mode uses its own editing, but we still track patch type
            patch_types = ["diff"]
            patch_type_probs = [1.0]
        else:
            patch_types = self.ui.patch_types
            patch_type_probs = [1.0 / len(patch_types)] * len(patch_types)

        # Resolve file paths
        init_program_path = None
        if self.ui.init_program_path:
            resolved_init = self.workspace_path / self.ui.init_program_path
            # In agentic mode, if file is missing, we treat it as no initial program (empty seed)
            # In non-agentic mode, we keep the path so runner can fail/warn or validation catches it
            if resolved_init.exists() or not self.ui.agentic_mode:
                init_program_path = str(resolved_init)
        elif self.workspace_path:
            # Default to workspace root - use the whole codebase for multi-file evolution
            init_program_path = str(self.workspace_path)
        init_support_dir = None
        if self.ui.init_support_dir:
            init_support_dir = str(self.workspace_path / self.ui.init_support_dir)

        return EvolutionConfig(
            task_sys_msg=self.ui.task_sys_msg or None,
            patch_types=patch_types,
            patch_type_probs=patch_type_probs,
            num_generations=self.ui.num_generations,
            max_parallel_jobs=self.ui.max_parallel_jobs,
            max_patch_resamples=self.ui.max_patch_resamples,
            max_patch_attempts=self.ui.max_patch_attempts,
            max_novelty_attempts=self.ui.max_novelty_attempts,
            job_type=self.ui.job_type,
            language=self.ui.language,
            llm_models=self.ui.llm_models,
            llm_dynamic_selection=self.ui.llm_dynamic_selection,
            embedding_model=self.ui.embedding_model or None,
            init_program_path=init_program_path,
            init_support_dir=init_support_dir,
            agentic_mode=self.ui.agentic_mode,
            agentic=agentic_cfg,
            evaluator=evaluator_cfg,
            # Scratchpad/Meta-learning: if enabled, use scratchpad settings
            meta_rec_interval=(
                self.ui.scratchpad_interval
                if self.ui.scratchpad_enabled
                else self.ui.meta_rec_interval
            ),
            meta_llm_models=self.ui.meta_llm_models,
            meta_max_recommendations=self.ui.scratchpad_max_recommendations,
            meta_backend=self.ui.scratchpad_backend if self.ui.scratchpad_enabled else None,
            embedding_include_globs=self.ui.include_patterns,
            embedding_exclude_globs=self.ui.exclude_patterns,
            embedding_max_files=self.ui.embedding_max_files,
            embedding_max_total_bytes=self.ui.embedding_max_total_bytes,
            embedding_max_bytes_per_file=self.ui.embedding_max_bytes_per_file,
            cleanup_old_generations=self.ui.cleanup_old_generations,
            cleanup_keep_last_n=self.ui.cleanup_keep_last_n,
            max_score=self.ui.max_score,
        )

    def build_database_config(self) -> DatabaseConfig:
        """Build DatabaseConfig from UI settings.

        Returns:
            Configured DatabaseConfig instance
        """
        return DatabaseConfig(
            num_islands=self.ui.num_islands,
            archive_size=self.ui.archive_size,
            migration_interval=self.ui.migration_interval,
            migration_rate=self.ui.migration_rate,
            island_elitism=self.ui.island_elitism,
        )

    def build_job_config(self) -> Union[LocalJobConfig, SlurmCondaJobConfig]:
        """Build job config based on job type.

        Returns:
            LocalJobConfig or SlurmCondaJobConfig based on ui.job_type
        """
        if self.ui.job_type == "local":
            return LocalJobConfig(eval_program_path=self.ui.eval_program_path or "")
        elif self.ui.job_type in ("slurm_conda", "slurm_docker"):
            return SlurmCondaJobConfig(
                eval_program_path=self.ui.eval_program_path or "",
                conda_env=self.ui.slurm_conda_env,
                time=self.ui.slurm_time,
                cpus=self.ui.slurm_cpus,
                gpus=self.ui.slurm_gpus,
                mem=self.ui.slurm_mem,
                modules=self.ui.slurm_modules if self.ui.slurm_modules else None,
                partition=self.ui.slurm_partition,
            )
        else:
            raise ValueError(f"Unknown job type: {self.ui.job_type}")

    def get_results_dir(self, exp_name: Optional[str] = None) -> Path:
        """Calculate the full results directory path.

        Args:
            exp_name: Optional experiment name. Defaults to workspace directory name.

        Returns:
            Path to the results directory
        """
        if self.ui.run_name:
            run_name = self.ui.run_name
        else:
            run_name = datetime.now().strftime("%Y.%m.%d%H%M%S")

        # Derive experiment name from workspace if not provided
        if not exp_name:
            exp_name = f"shinka_{self.workspace_path.name}"

        return Path(self.ui.results_dir) / exp_name / run_name


def flatten_nested_config(nested: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten nested config from UI into UIRunConfig fields.

    The UI sends a nested structure like:
    {
        "codebase": {"source_type": "local", ...},
        "agent": {"backend": "shinka", ...},
        "evaluator": {"mode": "agentic", ...},
        "run": {"num_generations": 20, ...},
        "job": {"job_type": "local", ...}
    }

    This function flattens it to match UIRunConfig fields.

    Args:
        nested: The nested configuration dictionary from UI

    Returns:
        Flattened dictionary suitable for UIRunConfig(**flat)
    """
    flat: Dict[str, Any] = {}

    # Codebase
    cb = nested.get("codebase", {})
    flat["source_type"] = cb.get("source_type", "local")
    flat["local_path"] = cb.get("local_path", "")
    flat["git_url"] = cb.get("git_url", "")
    flat["git_branch"] = cb.get("git_branch", "main")
    flat["git_workspace_path"] = cb.get("git_workspace_path", "")
    # Support both new "isolate_workspace" and old "use_worktree" for backwards compatibility
    flat["use_worktree"] = cb.get("isolate_workspace", cb.get("use_worktree", True))
    flat["local_isolation_strategy"] = cb.get("local_isolation_strategy", "auto")
    flat["init_program_path"] = cb.get("init_program_path", "")
    flat["init_support_dir"] = cb.get("init_support_dir")
    flat["include_patterns"] = cb.get("include_patterns", ["**/*.py"])
    flat["exclude_patterns"] = cb.get("exclude_patterns", ["results/**"])
    flat["language"] = cb.get("language", "python")
    flat["embedding_max_files"] = cb.get("embedding_max_files", 500)
    flat["embedding_max_total_bytes"] = cb.get("embedding_max_total_bytes", 2_000_000)
    flat["embedding_max_bytes_per_file"] = cb.get("embedding_max_bytes_per_file", 500_000)
    flat["cleanup_old_generations"] = cb.get("cleanup_old_generations", False)
    flat["cleanup_keep_last_n"] = cb.get("cleanup_keep_last_n", 50)

    # Agent
    ag = nested.get("agent", {})
    flat["agentic_mode"] = ag.get("agentic_mode", True)
    flat["agent_backend"] = ag.get("backend", "shinka")
    flat["agent_model"] = ag.get("model")  # NEW: Explicit model field
    flat["agent_max_turns"] = ag.get("max_turns", 50)
    flat["agent_max_seconds"] = ag.get("max_seconds", 0)
    flat["agent_sandbox"] = ag.get("sandbox", "workspace-write")
    flat["agent_approval_mode"] = ag.get("approval_mode", "full-auto")
    flat["resume_parent_session"] = ag.get("resume_parent_session", False)
    flat["llm_models"] = ag.get("llm_models", ["gpt-4.1"])
    flat["llm_dynamic_selection"] = ag.get("llm_dynamic_selection")
    flat["embedding_model"] = ag.get("embedding_model", "text-embedding-3-small")
    flat["task_sys_msg"] = ag.get("task_sys_msg", "")

    # Jules-specific agent config
    jules_cfg = ag.get("jules_config", {})
    flat["jules_github_repo"] = jules_cfg.get("github_repo", "")
    flat["jules_base_branch"] = jules_cfg.get("base_branch", "main")
    flat["jules_automation_mode"] = jules_cfg.get("automation_mode", "AUTO_CREATE_PR")
    flat["jules_poll_interval"] = jules_cfg.get("poll_interval", 15)
    flat["jules_cleanup_branch"] = jules_cfg.get("cleanup_branch", True)
    flat["jules_auto_approve_plan"] = jules_cfg.get("auto_approve_plan", True)

    # Evaluator
    ev = nested.get("evaluator", {})
    flat["evaluator_mode"] = ev.get("mode", "agentic")
    flat["eval_program_path"] = ev.get("eval_program_path", "shinka/eval_hydra.py")
    ac = ev.get("agentic_config", {})
    flat["eval_backend"] = ac.get("backend", "codex")
    flat["eval_max_turns"] = ac.get("max_turns", 80)
    flat["eval_sandbox"] = ac.get("sandbox", "workspace-write")
    flat["eval_prompt"] = ac.get("eval_prompt", "")

    # Ensemble evaluator config
    ensemble = ev.get("ensemble", {})
    flat["evaluator_ensemble_enabled"] = ensemble.get("enabled", False)
    flat["evaluator_ensemble"] = ensemble.get("evaluators", [])
    # Evaluators can be list or dict - convert dict to list format for UI
    if isinstance(flat["evaluator_ensemble"], dict):
        flat["evaluator_ensemble"] = [
            {"name": name, **config}
            for name, config in flat["evaluator_ensemble"].items()
        ]
    agg = ensemble.get("aggregation", {})
    flat["evaluator_aggregation_strategy"] = agg.get("strategy", "average")
    flat["evaluator_aggregation_vote_threshold"] = agg.get("vote_threshold", 0.5)
    flat["evaluator_aggregation_min_successful"] = agg.get("min_successful_evals", 1)
    flat["evaluator_max_parallel_evaluators"] = ensemble.get("max_parallel_evaluators", 0)

    # Run
    run = nested.get("run", {})
    flat["run_name"] = run.get("run_name", "")
    flat["results_dir"] = run.get("results_dir", "results")
    flat["num_generations"] = run.get("num_generations", 20)
    flat["max_parallel_jobs"] = run.get("max_parallel_jobs", 1)
    flat["max_patch_attempts"] = run.get("max_patch_attempts", 10)
    flat["max_patch_resamples"] = run.get("max_patch_resamples", 3)
    flat["max_novelty_attempts"] = run.get("max_novelty_attempts", 3)
    flat["num_islands"] = run.get("num_islands", 2)
    flat["archive_size"] = run.get("archive_size", 20)
    flat["migration_interval"] = run.get("migration_interval", 10)
    flat["migration_rate"] = run.get("migration_rate", 0.1)
    flat["island_elitism"] = run.get("island_elitism", True)
    flat["max_score"] = run.get("max_score", 1.0)
    flat["max_runtime_hours"] = run.get("max_runtime_hours", 0)
    flat["max_api_cost"] = run.get("max_api_cost", 0)
    flat["meta_rec_interval"] = run.get("meta_rec_interval")
    flat["meta_llm_models"] = run.get("meta_llm_models")

    # Scratchpad
    sp = nested.get("scratchpad", {})
    flat["scratchpad_enabled"] = sp.get("enabled", False)
    flat["scratchpad_backend"] = sp.get("backend", "codex")
    flat["scratchpad_interval"] = sp.get("interval", 5)
    flat["scratchpad_max_recommendations"] = sp.get("max_recommendations", 5)

    # Backend Bandit
    bandit = nested.get("bandit", {})
    flat["bandit_enabled"] = bandit.get("enabled", False)
    flat["bandit_backends"] = bandit.get(
        "backends", ["codex", "gemini", "claude", "shinka", "jules"]
    )
    flat["bandit_epsilon"] = bandit.get("epsilon", 0.1)
    flat["bandit_exploration_coef"] = bandit.get("exploration_coef", 1.0)
    flat["bandit_auto_decay"] = bandit.get("auto_decay", 0.95)
    flat["bandit_use_global_history"] = bandit.get("use_global_history", False)
    flat["bandit_record_to_history"] = bandit.get("record_to_history", True)

    # Job
    job = nested.get("job", {})
    flat["job_type"] = job.get("job_type", "local")
    sc = job.get("slurm_config", {})
    flat["slurm_time"] = sc.get("time", "00:10:00")
    flat["slurm_cpus"] = sc.get("cpus", 1)
    flat["slurm_gpus"] = sc.get("gpus", 0)
    flat["slurm_mem"] = sc.get("mem", "8G")
    flat["slurm_conda_env"] = sc.get("conda_env", "shinka")
    flat["slurm_modules"] = sc.get("modules", [])
    flat["slurm_partition"] = sc.get("partition", "gpu")

    return flat
