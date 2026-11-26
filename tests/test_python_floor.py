"""One Python floor everywhere: package metadata, `ci doctor`, the installer, the lint target, and the
lowest Python CI tests. A floor CI does not test is a promise nobody checks."""
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from pcim.terminal import MIN_PYTHON

REPO = Path(__file__).resolve().parents[1]
FLOOR = "%d.%d" % MIN_PYTHON
INSTALLER = REPO / "scripts" / "install_alter.sh"
WORKFLOW = REPO / ".github" / "workflows" / "tests.yml"


def test_package_metadata_states_the_floor():
    meta = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert meta["project"]["requires-python"] == ">=" + FLOOR


def test_installer_uses_the_same_floor():
    script = INSTALLER.read_text()
    assert "sys.version_info >= (%d, %d)" % MIN_PYTHON in script
    assert set(re.findall(r">= ?(3\.\d+)", script)) == {FLOOR}
    candidates = re.search(r"for c in (.*?); do", script).group(1).split()
    offered = [tuple(map(int, c.removeprefix("python").split("."))) for c in candidates if "." in c]
    assert offered and min(offered) == MIN_PYTHON


def test_lint_and_ci_target_the_floor():
    target = "--target-version py%d%d" % MIN_PYTHON
    assert target in (REPO / "scripts" / "check.sh").read_text()
    workflow = WORKFLOW.read_text()
    assert target in workflow
    tested = {tuple(map(int, v.split("."))) for v in re.findall(r'python-version: "(3\.\d+)"', workflow)}
    assert min(tested) == MIN_PYTHON


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_installer_refuses_an_older_python_before_making_a_venv(tmp_path):
    old = tmp_path / "python-old"
    old.write_text("#!/bin/sh\nexit 1\n")  # fails the version check, as a Python below the floor does
    old.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(ALTER_HOME=str(tmp_path / "alter"), ALTER_BIN=str(tmp_path / "bin"), PCIM_HOME=str(tmp_path / "pcim"))
    r = subprocess.run(["bash", str(INSTALLER), "--no-semantic", "--no-doctor", "--python", str(old)],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and f"is not a Python >= {FLOOR}" in r.stderr
    assert not (tmp_path / "alter" / "venv").exists()


def test_doctor_reports_the_floor(monkeypatch, tmp_path, capsys):
    from pcim import terminal
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "version_info", (3, 11, 9, "final", 0))
    terminal.cmd_doctor(["--json"])
    out = capsys.readouterr().out
    assert f"(need >={FLOOR})" in out
