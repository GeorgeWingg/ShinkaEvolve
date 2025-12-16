import shutil
import subprocess
from pathlib import Path

import pytest

import shinka.core.fs_utils as fs_utils


def _make_src_tree(root: Path) -> None:
    (root / "a").mkdir(parents=True)
    (root / "a" / "file.txt").write_text("hi", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.pyc").write_text("junk", encoding="utf-8")
    (root / "session_log.jsonl").write_text("log", encoding="utf-8")
    (root / "keep.py").write_text("print('ok')", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "mod.js").write_text("mod", encoding="utf-8")


def test_fast_copy_uses_cp_rc_on_darwin(monkeypatch, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "file.txt").write_text("hi", encoding="utf-8")

    recorded = {}

    def fake_run(cmd, check=False, capture_output=False, text=False):
        recorded["cmd"] = cmd
        shutil.copytree(src, dst)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(fs_utils.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(fs_utils.subprocess, "run", fake_run)

    fs_utils.fast_copy(src, dst)

    assert recorded["cmd"] == ["cp", "-Rc", str(src), str(dst)]
    assert (dst / "file.txt").read_text(encoding="utf-8") == "hi"


def test_fast_copy_uses_cp_reflink_on_linux_without_excludes(monkeypatch, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "file.txt").write_text("hi", encoding="utf-8")

    recorded = {}

    def fake_run(cmd, check=False, capture_output=False, text=False):
        recorded["cmd"] = cmd
        shutil.copytree(src, dst)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(fs_utils.platform, "system", lambda: "Linux")
    monkeypatch.setattr(fs_utils.subprocess, "run", fake_run)

    fs_utils.fast_copy(src, dst)

    assert recorded["cmd"][:2] == ["cp", "--reflink=auto"]
    assert (dst / "file.txt").exists()


def test_fast_copy_prefers_rsync_reflink_on_linux_with_excludes(monkeypatch, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_src_tree(src)

    calls = []

    def fake_run(cmd, check=False, capture_output=False, text=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(fs_utils.platform, "system", lambda: "Linux")
    monkeypatch.setattr(fs_utils.subprocess, "run", fake_run)

    fs_utils.fast_copy(
        src,
        dst,
        exclude_dirs={"results"},
        exclude_suffixes={".pyc"},
        exclude_files={"session_log.jsonl"},
    )

    assert calls, "Expected rsync or cp to be invoked"
    assert calls[0][0] == "rsync"
    assert "--reflink=auto" in calls[0]


def test_fast_copy_excludes_items_on_windows_fallback(monkeypatch, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_src_tree(src)

    monkeypatch.setattr(fs_utils.platform, "system", lambda: "Windows")

    fs_utils.fast_copy(
        src,
        dst,
        exclude_dirs={"__pycache__", "node_modules"},
        exclude_suffixes={".pyc"},
        exclude_files={"session_log.jsonl"},
    )

    assert (dst / "a" / "file.txt").exists()
    assert (dst / "keep.py").exists()
    assert not (dst / "__pycache__").exists()
    assert not (dst / "node_modules").exists()
    assert not (dst / "session_log.jsonl").exists()
    assert list(dst.rglob("*.pyc")) == []


def test_fast_copy_falls_back_when_cp_missing(monkeypatch, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "file.txt").write_text("hi", encoding="utf-8")

    def fake_run(cmd, check=False, capture_output=False, text=False):
        raise FileNotFoundError()

    monkeypatch.setattr(fs_utils.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(fs_utils.subprocess, "run", fake_run)

    fs_utils.fast_copy(src, dst)

    assert (dst / "file.txt").exists()

