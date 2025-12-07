#!/usr/bin/env python3
from pathlib import Path
from dotenv import load_dotenv
import hydra
from omegaconf import DictConfig, OmegaConf
from shinka.core import EvolutionRunner


def _snapshot_hydra_config(cfg: DictConfig, results_dir: Path) -> None:
    hydra_dir = results_dir / ".hydra"
    hydra_dir.mkdir(parents=True, exist_ok=True)
    OmegaConf.save(cfg, hydra_dir / "config.yaml")
    hydra_cfg = cfg.get("hydra", {})
    OmegaConf.save(OmegaConf.create(hydra_cfg), hydra_dir / "hydra.yaml")


@hydra.main(config_path="../configs", config_name="config", version_base=None)
def main(cfg: DictConfig):
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)

    print("Experiment configurations:")
    print(OmegaConf.to_yaml(cfg, resolve=True))

    job_cfg = hydra.utils.instantiate(cfg.job_config)
    db_cfg = hydra.utils.instantiate(cfg.db_config)
    evo_cfg = hydra.utils.instantiate(cfg.evo_config)

    # Persist Hydra metadata alongside the actual results directory (which may
    # differ from Hydra's run dir when `evo_config.results_dir` is overridden).
    snapshot_dir = Path(evo_cfg.results_dir) if evo_cfg.results_dir else Path(cfg.output_dir)
    _snapshot_hydra_config(cfg, snapshot_dir)

    evo_runner = EvolutionRunner(
        evo_config=evo_cfg,
        job_config=job_cfg,
        db_config=db_cfg,
        verbose=cfg.verbose,
    )
    evo_runner.run()


if __name__ == "__main__":
    main()
