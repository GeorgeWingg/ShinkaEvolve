from pathlib import Path


def test_get_agent_session_log_serves_agentic_edit_log(tmp_path):
    from shinka.database import DatabaseConfig, ProgramDatabase, Program
    from shinka.webui.visualization import DatabaseRequestHandler

    run_root = tmp_path / "run"
    run_root.mkdir()
    db_path = run_root / "evolution_db.sqlite"

    log_path = run_root / "agent_sessions" / "s1" / "session_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text('{"type":"init"}\n{"item":{"type":"agent_message","text":"hi"}}\n', encoding="utf-8")

    config = DatabaseConfig(db_path=str(db_path))
    db = ProgramDatabase(config, embedding_model="", read_only=False)
    program = Program(
        id="prog-1",
        code="print('hi')",
        language="python",
        generation=0,
        combined_score=1.0,
        correct=True,
        metadata={"agent_session_log_path": str(log_path)},
    )
    db.add(program)
    db.save()
    db.conn.close()

    class DummyHandler(DatabaseRequestHandler):
        def __init__(self, search_root):
            self.search_root = search_root
            self.response_data = None
            self.error = None

        def send_json_response(self, data):
            self.response_data = data

        def send_error(self, code, message=None):
            self.error = (code, message)

    handler = DummyHandler(search_root=str(tmp_path))
    handler.handle_get_agent_session_log(
        {
            "db_path": [str(db_path)],
            "program_id": ["prog-1"],
            "tail_bytes": ["2000"],
        }
    )

    assert handler.error is None
    assert handler.response_data is not None
    assert "agent_message" in handler.response_data["content"]


def test_get_agent_session_log_serves_agentic_evaluator_log(tmp_path):
    from shinka.database import DatabaseConfig, ProgramDatabase, Program
    from shinka.webui.visualization import DatabaseRequestHandler

    run_root = tmp_path / "run"
    run_root.mkdir()
    db_path = run_root / "evolution_db.sqlite"

    rel_log = Path("agentic_eval_sessions") / "sess-1" / "session_log.jsonl"
    abs_log = run_root / rel_log
    abs_log.parent.mkdir(parents=True, exist_ok=True)
    abs_log.write_text("evaluator log\n", encoding="utf-8")

    config = DatabaseConfig(db_path=str(db_path))
    db = ProgramDatabase(config, embedding_model="", read_only=False)
    program = Program(
        id="prog-2",
        code="print('hi')",
        language="python",
        generation=0,
        combined_score=1.0,
        correct=True,
        metadata={"agentic_evaluator": {"session_log_path": str(rel_log)}},
    )
    db.add(program)
    db.save()
    db.conn.close()

    class DummyHandler(DatabaseRequestHandler):
        def __init__(self, search_root):
            self.search_root = search_root
            self.response_data = None
            self.error = None

        def send_json_response(self, data):
            self.response_data = data

        def send_error(self, code, message=None):
            self.error = (code, message)

    handler = DummyHandler(search_root=str(tmp_path))
    handler.handle_get_agent_session_log(
        {
            "db_path": [str(db_path)],
            "program_id": ["prog-2"],
            "log_type": ["agentic_evaluator"],
        }
    )

    assert handler.error is None
    assert handler.response_data is not None
    assert handler.response_data["content"].strip() == "evaluator log"


def test_get_agent_session_log_serves_ensemble_evaluator_log(tmp_path):
    from shinka.database import DatabaseConfig, ProgramDatabase, Program
    from shinka.webui.visualization import DatabaseRequestHandler

    run_root = tmp_path / "run"
    run_root.mkdir()
    db_path = run_root / "evolution_db.sqlite"

    rel_log = Path("agentic_eval_sessions") / "ensemble-1" / "session_log.jsonl"
    abs_log = run_root / rel_log
    abs_log.parent.mkdir(parents=True, exist_ok=True)
    abs_log.write_text("ensemble evaluator log\n", encoding="utf-8")

    config = DatabaseConfig(db_path=str(db_path))
    db = ProgramDatabase(config, embedding_model="", read_only=False)
    program = Program(
        id="prog-3",
        code="print('hi')",
        language="python",
        generation=0,
        combined_score=1.0,
        correct=True,
        metadata={
            "ensemble_evaluation": {
                "enabled": True,
                "evaluators": [
                    {
                        "evaluator_id": "ev-1",
                        "session_log_path": str(rel_log),
                    }
                ],
            }
        },
    )
    db.add(program)
    db.save()
    db.conn.close()

    class DummyHandler(DatabaseRequestHandler):
        def __init__(self, search_root):
            self.search_root = search_root
            self.response_data = None
            self.error = None

        def send_json_response(self, data):
            self.response_data = data

        def send_error(self, code, message=None):
            self.error = (code, message)

    handler = DummyHandler(search_root=str(tmp_path))
    handler.handle_get_agent_session_log(
        {
            "db_path": [str(db_path)],
            "program_id": ["prog-3"],
            "log_type": ["ensemble_evaluator"],
            "evaluator_id": ["ev-1"],
        }
    )

    assert handler.error is None
    assert handler.response_data is not None
    assert handler.response_data["content"].strip() == "ensemble evaluator log"

