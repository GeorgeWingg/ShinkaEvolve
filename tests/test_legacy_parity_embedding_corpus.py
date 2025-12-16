from pathlib import Path


def test_legacy_mode_embedding_corpus_is_primary_file_only(tmp_path, monkeypatch):
    """Legacy mode should embed/store the executable source, not a multi-file corpus.

    Regression: agentic multi-file corpus formatting ("=== FILE: ... ===") must
    never be written into legacy `Program.code` / main.py via the legacy patcher.
    """
    from shinka.core.runner import EvolutionConfig, EvolutionRunner
    from shinka.database.dbase import DatabaseConfig
    from shinka.launch.scheduler import LocalJobConfig

    results_root = tmp_path / "results"
    gen_dir = results_root / "gen_1"
    gen_dir.mkdir(parents=True, exist_ok=True)

    main_path = gen_dir / "main.py"
    helper_path = gen_dir / "helper.py"
    main_path.write_text("print('main')\n", encoding="utf-8")
    helper_path.write_text("print('helper')\n", encoding="utf-8")

    # Avoid any accidental API usage during init.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    evo_config = EvolutionConfig(
        results_dir=str(results_root),
        language="python",
        agentic_mode=False,
        embedding_model=None,
        llm_models=["gpt-4.1-mini"],
    )
    job_config = LocalJobConfig(eval_program_path="")
    db_config = DatabaseConfig(db_path="evolution_db.sqlite")

    runner = EvolutionRunner(
        evo_config=evo_config,
        job_config=job_config,
        db_config=db_config,
        verbose=False,
    )

    corpus = runner._build_embedding_corpus(gen_dir, meta_patch_data={})

    assert corpus.included_files == ["main.py"]
    assert corpus.text == "print('main')\n"
    assert "helper" not in corpus.text
    assert "=== FILE:" not in corpus.text

