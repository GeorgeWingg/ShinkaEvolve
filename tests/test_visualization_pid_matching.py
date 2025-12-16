from types import SimpleNamespace


def _fake_ps(stdout: str):
    return SimpleNamespace(stdout=stdout, returncode=0)


def test_pid_matches_run_detects_absolute_path(monkeypatch):
    from shinka.webui.visualization import pid_matches_run

    run_dir = "/tmp/shinka_run/task1/runA"

    def fake_run(*args, **kwargs):
        return _fake_ps(f"/usr/bin/python3 -m shinka_launch --results_dir {run_dir}")

    monkeypatch.setattr("shinka.webui.visualization.subprocess.run", fake_run)

    assert pid_matches_run(1234, run_dir) is True


def test_pid_matches_run_rejects_unrelated_pid(monkeypatch):
    from shinka.webui.visualization import pid_matches_run

    run_dir = "/tmp/shinka_run/task1/runA"

    def fake_run(*args, **kwargs):
        return _fake_ps("/usr/bin/python3 -m some_other_process --foo bar")

    monkeypatch.setattr("shinka.webui.visualization.subprocess.run", fake_run)

    assert pid_matches_run(1234, run_dir) is False


def test_pid_matches_run_allows_basename_with_shinka(monkeypatch):
    from shinka.webui.visualization import pid_matches_run

    run_dir = "/tmp/shinka_run/task1/runA"
    basename = "runA"

    def fake_run(*args, **kwargs):
        return _fake_ps(f"/usr/bin/python3 -m shinka_launch --run_id {basename}")

    monkeypatch.setattr("shinka.webui.visualization.subprocess.run", fake_run)

    assert pid_matches_run(1234, run_dir) is True

