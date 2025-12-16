#!/usr/bin/env python3
"""Create a small, local mock run for previewing the WebUI "Evaluation Integrity" panel.

This script writes a synthetic Shinka "run directory" under:

  results/mock_integrity_demo/<run_id>/

It creates:
  - evolution_db.sqlite with a tiny parent->child chain (gen_0..gen_3)
  - gen_<n>/results/metrics.json files
  - agentic_eval_sessions/<uuid>/session_log.jsonl files

The generated programs include `metadata.agentic_evaluator.integrity` with:
  - clean
  - artifacts_only (new file created)
  - violation (pre-existing file modified)

Usage:
  uv run python scripts/create_mock_integrity_run.py
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _make_eval_session(eval_sessions_root: Path, *, status: str) -> Tuple[str, str, str, List[Dict[str, Any]]]:
    session_uuid = uuid.uuid4().hex
    session_dir = eval_sessions_root / session_uuid
    session_dir.mkdir(parents=True, exist_ok=True)
    session_log_path = session_dir / "session_log.jsonl"

    events: List[Dict[str, Any]] = [
        {
            "type": "assistant",
            "item": {"type": "agent_message", "text": f"Starting evaluator ({status})"},
        },
        {
            "type": "assistant",
            "item": {
                "type": "command_execution",
                "command": "python -c \"print('running tests')\"",
                "status": "success",
                "exit_code": 0,
                "stdout": "running tests\n",
                "stderr": "",
            },
        },
        {
            "type": "assistant",
            "item": {
                "type": "agent_message",
                "text": "Wrote metrics.json and returning result.",
            },
        },
    ]
    with session_log_path.open("w", encoding="utf-8") as handle:
        for ev in events:
            handle.write(json.dumps(ev))
            handle.write("\n")

    # The UI expects these paths relative to the run root, matching runner behavior.
    return (
        session_uuid,
        f"agentic_eval_sessions/{session_uuid}",
        f"agentic_eval_sessions/{session_uuid}/session_log.jsonl",
        events,
    )


def _build_agentic_evaluator_meta(
    *,
    run_root: Path,
    generation: int,
    combined_score: float,
    correct: bool,
    status: str,
    integrity: Dict[str, Any],
    details: str,
) -> Dict[str, Any]:
    eval_sessions_root = run_root / "agentic_eval_sessions"
    eval_sessions_root.mkdir(parents=True, exist_ok=True)
    session_id, session_dir_rel, session_log_rel, events = _make_eval_session(
        eval_sessions_root, status=status
    )

    results_rel = f"gen_{generation}/results"
    metrics_rel = f"{results_rel}/metrics.json"

    # Mirror the shape produced by EvolutionRunner._run_agentic_evaluation.
    return {
        "session_dir": session_dir_rel,
        "session_log_path": session_log_rel,
        "session_id": session_id,
        "commands_run": [
            {
                "command": "python -c \"print('running tests')\"",
                "status": "success",
                "exit_code": 0,
                "stdout": "running tests\n",
                "stderr": "",
            }
        ],
        "generation": generation,
        "elapsed_seconds": 1.23,
        "status": status,
        "correct": bool(correct),
        "metrics_path": metrics_rel,
        "results_relpath": results_rel,
        "metrics": {
            "combined_score": combined_score,
            "correct": bool(correct),
            "details": details,
            "integrity_violation": bool(integrity.get("status") == "violation"),
        },
        "error_message": None if correct else details,
        "stdout_log": "running tests\n",
        "stderr_log": "",
        "events_preview": events,
        "system_prompt": "You are an agentic evaluator.",
        "user_prompt": "Evaluate the candidate and write metrics.json.",
        "integrity": integrity,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument(
        "--task",
        default="mock_integrity_demo",
        help="Task name directory under results/ (default: mock_integrity_demo)",
    )
    parser.add_argument(
        "--run",
        default=None,
        help="Run directory name (default: timestamp-based)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    results_root = repo_root / "results"
    task_dir = results_root / str(args.task)
    run_id = (
        str(args.run)
        if args.run
        else f"{time.strftime('%Y.%m.%d_%H%M%S')}_integrity_demo"
    )
    run_dir = task_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Create some workspace directories/files so the UI has something realistic.
    for gen in range(4):
        gen_dir = run_dir / f"gen_{gen}"
        (gen_dir / "results").mkdir(parents=True, exist_ok=True)
        (gen_dir / "main.py").write_text(
            f"print('hello from gen_{gen}')\n", encoding="utf-8"
        )

    # Write per-generation metrics.json as an extra breadcrumb.
    _write_json(
        run_dir / "gen_0" / "results" / "metrics.json",
        {"combined_score": 0.1, "correct": True, "details": "Seed program"},
    )

    # Import here so the script can be read without importing Shinka.
    from shinka.database.dbase import DatabaseConfig, Program, ProgramDatabase

    db_path = run_dir / "evolution_db.sqlite"
    db = ProgramDatabase(
        DatabaseConfig(db_path=str(db_path), num_islands=1, archive_size=0),
        embedding_model="",  # Disable embedding client to keep this offline.
        read_only=False,
    )

    # Parent seed (legacy-ish node).
    p0 = Program(
        id=uuid.uuid4().hex,
        code="print('seed')\n",
        language="python",
        generation=0,
        correct=True,
        combined_score=0.1,
        public_metrics={},
        private_metrics={},
        metadata={"patch_type": "init", "evaluator_mode": "legacy"},
        text_feedback="Seed",
    )
    db.add(p0, verbose=False)

    # gen_1: clean integrity
    integrity_clean = {
        "policy": "no_modify_preexisting_files",
        "status": "clean",
        "modified_existing_count": 0,
        "deleted_existing_count": 0,
        "new_files_created_count": 0,
        "modified_existing_files": [],
        "deleted_existing_files": [],
        "new_files_created": [],
        "truncated": False,
    }
    agentic_eval_clean = _build_agentic_evaluator_meta(
        run_root=run_dir,
        generation=1,
        combined_score=0.8,
        correct=True,
        status="success",
        integrity=integrity_clean,
        details="All checks passed.",
    )
    _write_json(
        run_dir / "gen_1" / "results" / "metrics.json",
        agentic_eval_clean["metrics"],
    )
    p1 = Program(
        id=uuid.uuid4().hex,
        code=(run_dir / "gen_1" / "main.py").read_text(encoding="utf-8"),
        language="python",
        parent_id=p0.id,
        generation=1,
        correct=True,
        combined_score=0.8,
        public_metrics={},
        private_metrics={},
        metadata={
            "patch_type": "agentic",
            "evaluator_mode": "agentic",
            "agent_backend": "codex",
            "agent_backend_type": "cli",
            "agentic_evaluator": agentic_eval_clean,
        },
        text_feedback="Clean integrity run",
    )
    db.add(p1, verbose=False)

    # gen_2: artifacts_only (new test file created)
    (run_dir / "gen_2" / "generated_test.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    integrity_artifacts = {
        "policy": "no_modify_preexisting_files",
        "status": "artifacts_only",
        "modified_existing_count": 0,
        "deleted_existing_count": 0,
        "new_files_created_count": 1,
        "modified_existing_files": [],
        "deleted_existing_files": [],
        "new_files_created": ["generated_test.py"],
        "truncated": False,
    }
    agentic_eval_artifacts = _build_agentic_evaluator_meta(
        run_root=run_dir,
        generation=2,
        combined_score=0.75,
        correct=True,
        status="success",
        integrity=integrity_artifacts,
        details="Generated an extra test file to probe behavior.",
    )
    _write_json(
        run_dir / "gen_2" / "results" / "metrics.json",
        agentic_eval_artifacts["metrics"],
    )
    p2 = Program(
        id=uuid.uuid4().hex,
        code=(run_dir / "gen_2" / "main.py").read_text(encoding="utf-8"),
        language="python",
        parent_id=p1.id,
        generation=2,
        correct=True,
        combined_score=0.75,
        public_metrics={},
        private_metrics={},
        metadata={
            "patch_type": "agentic",
            "evaluator_mode": "agentic",
            "agent_backend": "codex",
            "agent_backend_type": "cli",
            "agentic_evaluator": agentic_eval_artifacts,
        },
        text_feedback="Artifacts-only integrity run",
    )
    db.add(p2, verbose=False)

    # gen_3: violation (modified existing file)
    integrity_violation = {
        "policy": "no_modify_preexisting_files",
        "status": "violation",
        "modified_existing_count": 1,
        "deleted_existing_count": 0,
        "new_files_created_count": 0,
        "modified_existing_files": ["main.py"],
        "deleted_existing_files": [],
        "new_files_created": [],
        "truncated": False,
    }
    agentic_eval_violation = _build_agentic_evaluator_meta(
        run_root=run_dir,
        generation=3,
        combined_score=1.0,
        correct=False,  # matches integrity guard behavior
        status="error",
        integrity=integrity_violation,
        details="Evaluation integrity violation: evaluator modified main.py.",
    )
    _write_json(
        run_dir / "gen_3" / "results" / "metrics.json",
        agentic_eval_violation["metrics"],
    )
    p3 = Program(
        id=uuid.uuid4().hex,
        code=(run_dir / "gen_3" / "main.py").read_text(encoding="utf-8"),
        language="python",
        parent_id=p2.id,
        generation=3,
        correct=False,
        combined_score=0.0,
        public_metrics={},
        private_metrics={},
        metadata={
            "patch_type": "agentic",
            "evaluator_mode": "agentic",
            "agent_backend": "codex",
            "agent_backend_type": "cli",
            "agentic_evaluator": agentic_eval_violation,
        },
        text_feedback="Violation run (forced incorrect)",
    )
    db.add(p3, verbose=False)

    db.save()
    db.close()

    rel_db_path = db_path.relative_to(repo_root)
    print(f"Created mock integrity demo run at: {run_dir}")
    print(f"DB: {rel_db_path}")
    print()
    print("To view it:")
    print(f"  uv run shinka_visualize results --port 8888 --db {rel_db_path} --open")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

