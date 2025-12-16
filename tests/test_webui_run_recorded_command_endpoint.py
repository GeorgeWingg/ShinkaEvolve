from pathlib import Path

import pytest


def test_run_recorded_command_endpoint_executes(monkeypatch, tmp_path):
    from shinka.database import DatabaseConfig, ProgramDatabase, Program
    from shinka.webui.visualization import DatabaseRequestHandler

    run_root = tmp_path / "run"
    run_root.mkdir()
    db_path = run_root / "evolution_db.sqlite"

    # Create a real schema + program row via ProgramDatabase (write mode)
    config = DatabaseConfig(db_path=str(db_path))
    db = ProgramDatabase(config, embedding_model="", read_only=False)
    program = Program(
        id="prog-1",
        code="print('hi')",
        language="python",
        generation=0,
        combined_score=1.0,
        correct=True,
        private_metrics={"run_command": "echo hello", "run_workdir": "best"},
    )
    db.add(program)
    db.save()
    db.conn.close()

    # Ensure best/ exists so workdir resolution succeeds
    (run_root / "best").mkdir()

    class DummyHandler(DatabaseRequestHandler):
        def __init__(self, search_root, payload):
            self.search_root = search_root
            self._payload = payload
            self.response_data = None

        def _read_json_body(self):
            return self._payload

        def send_json_response(self, data):
            self.response_data = data

    class DummyPopen:
        def __init__(self, *args, **kwargs):
            self.pid = 12345

    monkeypatch.setattr("shinka.webui.visualization.subprocess.Popen", DummyPopen)

    payload = {"db_path": str(db_path), "best": True}
    handler = DummyHandler(search_root=str(tmp_path), payload=payload)
    handler.handle_run_recorded_command()

    assert handler.response_data is not None
    assert handler.response_data["ok"] is True
    assert handler.response_data["pid"] == 12345
    assert handler.response_data["command"] == "echo hello"
    assert Path(handler.response_data["log_dir"]).exists()


def test_run_recorded_command_endpoint_dry_run(monkeypatch, tmp_path):
    from shinka.database import DatabaseConfig, ProgramDatabase, Program
    from shinka.webui.visualization import DatabaseRequestHandler

    run_root = tmp_path / "run"
    run_root.mkdir()
    db_path = run_root / "evolution_db.sqlite"

    config = DatabaseConfig(db_path=str(db_path))
    db = ProgramDatabase(config, embedding_model="", read_only=False)
    program = Program(
        id="prog-2",
        code="print('hi')",
        language="python",
        generation=0,
        combined_score=1.0,
        correct=True,
        private_metrics={"run_command": "echo dry"},
    )
    db.add(program)
    db.save()
    db.conn.close()

    class DummyHandler(DatabaseRequestHandler):
        def __init__(self, search_root, payload):
            self.search_root = search_root
            self._payload = payload
            self.response_data = None

        def _read_json_body(self):
            return self._payload

        def send_json_response(self, data):
            self.response_data = data

    payload = {"db_path": str(db_path), "best": True, "dry_run": True}
    handler = DummyHandler(search_root=str(tmp_path), payload=payload)
    handler.handle_run_recorded_command()

    assert handler.response_data["ok"] is True
    assert handler.response_data["command"] == "echo dry"
    assert "pid" not in handler.response_data

