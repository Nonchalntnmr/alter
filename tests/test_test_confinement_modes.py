"""Where approved tests run: on the host by default (as in V1), confined only when required."""
import sys

import pytest

from pcim.engine import PersonalCI
from pcim.orchestration import verified_test
from pcim.sandbox import SandboxUnavailable, _snapshot, confined_command, snapshot_digest, unavailable_reason
from pcim.tools import HOST_TEST_RECEIPT, Executor, ToolError
from pcim.workspace import Workspace, WorkspaceError

NO_SANDBOX = unavailable_reason()


def spec(root, **extra):
    return {"root": str(root), "run_argv": [sys.executable, "-c", "print('3 passed')"], "run_timeout_s": 60, **extra}


def test_default_runs_on_the_host_and_the_receipt_says_so(tmp_path):
    result = Executor().run("t-host", "run_tests", {}, spec(tmp_path))
    assert result["exit_code"] == 0 and "3 passed" in result["stdout_tail"]
    assert result["confinement"] == HOST_TEST_RECEIPT
    assert result["confinement"]["verified"] is False and result["confinement"]["backend"] == "none"
    # A host run is honest evidence for a V1 goal, and never confined evidence for a specialist gate.
    assert not verified_test({"tool": "run_tests", "status": "succeeded", "result": result})


def test_a_saved_workspace_without_the_setting_keeps_the_host_path(tmp_path):
    # Workspaces authorized before the setting existed have no "test_confinement" key.
    result = Executor().run("t-old", "run_tests", {}, {k: v for k, v in spec(tmp_path).items()})
    assert result["confinement"] == HOST_TEST_RECEIPT


@pytest.mark.skipif(NO_SANDBOX is None, reason="a verified sandbox exists here; the refusal case needs none")
def test_required_confinement_refuses_where_no_sandbox_exists(tmp_path):
    required = spec(tmp_path, test_confinement="required", run_argv=["python3", "-c", "print('3 passed')"])
    with pytest.raises(ToolError, match="confinement|bubblewrap|probe"):
        Executor().run("t-req", "run_tests", {}, required)


def test_confinement_refuses_an_absolute_virtualenv_runner_with_the_reason(tmp_path):
    with pytest.raises(SandboxUnavailable, match="virtualenv is not visible"):
        with confined_command(tmp_path, ["/Users/someone/project/.venv/bin/python", "-m", "pytest"]):
            pass


def test_workspace_records_and_validates_the_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "home"))
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", "U", providers=("mock",))
    ws = Workspace(ci)
    p = ws.create_project("u", "modes")
    root = tmp_path / "project"
    root.mkdir()
    ws.authorize_workspace("u", p.id, str(root), run_argv=["python3", "-c", "pass"])
    assert ws.workspace_auth("u", p.id)["spec"]["test_confinement"] == "none"
    ws.authorize_workspace("u", p.id, str(root), run_argv=["python3", "-c", "pass"], test_confinement="required")
    assert ws.workspace_auth("u", p.id)["spec"]["test_confinement"] == "required"
    with pytest.raises(WorkspaceError, match="test_confinement"):
        ws.authorize_workspace("u", p.id, str(root), run_argv=["python3", "-c", "pass"], test_confinement="host")


def project(root):
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "calc.py").write_text("def add(a, b): return a + b\n")
    (root / ".git").write_text("gitdir: /Users/someone/main/.git/worktrees/x\n")  # a linked worktree's pointer
    (root / ".env").write_text("TOKEN=not-for-tests\n")
    (root / "node_modules").write_text("a file named like a skipped directory\n")
    (root / "empty").mkdir()
    return root


def test_snapshot_leaves_out_git_pointer_files_and_secrets(tmp_path):
    root = project(tmp_path / "project")
    dest = tmp_path / "copy"
    dest.mkdir()
    snap = _snapshot(root, dest)
    copied = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*"))
    assert copied == ["empty", "pkg", "pkg/calc.py"]
    assert snap["snapshot_files"] == 1 and snap["snapshot_sha256"] == snapshot_digest(root)


def test_snapshot_digest_tracks_tested_files_only(tmp_path):
    root = project(tmp_path / "project")
    before = snapshot_digest(root)
    (root / ".git").write_text("gitdir: /elsewhere\n")
    (root / ".env").write_text("TOKEN=changed\n")
    assert snapshot_digest(root) == before  # excluded files are not part of what was tested
    (root / "pkg" / "calc.py").write_text("def add(a, b): return a - b\n")
    assert snapshot_digest(root) != before
    (root / "pkg" / "calc.py").write_text("def add(a, b): return a + b\n")
    assert snapshot_digest(root) == before
    (root / "pkg" / "new.py").write_text("x = 1\n")
    assert snapshot_digest(root) != before


def test_snapshot_digest_fails_closed_on_a_symlink(tmp_path):
    root = project(tmp_path / "project")
    (root / "link.py").symlink_to(root / "pkg" / "calc.py")
    assert snapshot_digest(root) is None
