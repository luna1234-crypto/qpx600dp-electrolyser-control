"""Launch real Qt subprocesses, including failure paths, without lab hardware."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from qpx_electrolyser.app.paths import default_paths


@pytest.mark.parametrize("entrypoint", ["script", "module"])
def test_application_launches_processes_events_and_exits(tmp_path: Path, entrypoint: str) -> None:
    root = default_paths().project_root
    log_dir = tmp_path / "logs with spaces"
    arguments = [str(root / "main.py")] if entrypoint == "script" else ["-m", "qpx_electrolyser"]
    process = subprocess.run(
        [sys.executable, *arguments, "--smoke-test", "--log-dir", str(log_dir)],
        cwd=tmp_path if entrypoint == "script" else root,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    contents = next(log_dir.glob("application_*.log")).read_text(encoding="utf-8")
    assert "Application started" in contents
    assert "window visible=True; Qt event loop processed events" in contents
    assert "Application stopped: exit_code=0" in contents


def test_application_reports_unwritable_log_target(tmp_path: Path) -> None:
    blocked = tmp_path / "occupied"
    blocked.write_text("keep me", encoding="utf-8")
    process = subprocess.run(
        [sys.executable, str(default_paths().project_root / "main.py"),
         "--smoke-test", "--log-dir", str(blocked)],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert process.returncode == 1
    assert "Cannot open the application log" in process.stderr
    assert "Traceback" not in process.stderr
    assert blocked.read_text(encoding="utf-8") == "keep me"


def test_default_paths_do_not_follow_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expected_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    paths = default_paths()
    assert paths.project_root == expected_root
    assert paths.log_directory == expected_root / "logs"
