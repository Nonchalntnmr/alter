"""The briefing says where a project's tests run, survives a reopen, and claims nothing it can't back."""
import pytest
from fastapi.testclient import TestClient

from pcim.api import create_app
from pcim.engine import PersonalCI
from pcim.workspace import Workspace, render_briefing

EXPECTED = {
    "none": "Test runs: on the host after approval, unconfined; a host result is never confined evidence.",
    "required": "Test runs: only in a verified Linux sandbox, refused where none is available.",
}


def authorized(tmp_path, **auth):
    data, root = tmp_path / "data", tmp_path / "project"
    root.mkdir()
    ci = PersonalCI(data, seed=0)
    token = ci.onboard("alice", "", ("mock",))["api_token"]
    ws = Workspace(ci)
    pid = ws.create_project("alice", "Preview").id
    ws.authorize_workspace("alice", pid, str(root), **auth)
    # A fresh engine over the same data: the setting is read back from SQLite, not memory.
    client = TestClient(create_app(PersonalCI(data, seed=0)))
    return client, {"Authorization": "Bearer " + token}, pid, root


@pytest.mark.parametrize("policy", ["none", "required"])
def test_briefing_states_the_saved_test_policy_without_running_anything(tmp_path, policy):
    client, h, pid, root = authorized(tmp_path, run_argv=["python3", "-c", "raise SystemExit(99)"],
                                      test_confinement=policy)
    r = client.get(f"/v1/projects/{pid}", headers=h)
    assert r.status_code == 200
    b = r.json()["briefing"]
    assert b["workspace"]["test_confinement"] == policy
    lines = render_briefing(b).splitlines()
    assert EXPECTED[policy] in lines
    assert not [line for line in lines if line.startswith("Test runs:") and line != EXPECTED[policy]]
    # Reading the briefing starts no task and touches no project file.
    assert client.get("/v1/tasks", headers=h).json() == []
    assert list(root.iterdir()) == []
    assert client.get(f"/v1/projects/{pid}").status_code == 401


def test_no_test_command_means_no_test_policy_line(tmp_path):
    client, h, pid, _ = authorized(tmp_path, run_argv=None)
    b = client.get(f"/v1/projects/{pid}", headers=h).json()["briefing"]
    assert b["workspace"]["can_run_tests"] is False and b["workspace"]["test_confinement"] == "none"
    assert not [line for line in render_briefing(b).splitlines() if line.startswith("Test runs:")]


@pytest.mark.parametrize("flags, expected", [
    ([], None),
    (["--tests", "python3 -c pass"], "tests run: on this machine after approval, unconfined (receipt: verified false)"),
    (["--tests", "python3 -c pass", "--confine-tests"],
     "tests run: only in a verified Linux sandbox; refused where none is available"),
])
def test_alter_status_says_where_tests_run(tmp_path, monkeypatch, capsys, flags, expected):
    from pcim import alter, cli
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PCIM_EMBEDDER", "hashing-512")
    assert cli.main(["init", "--user", "u", "--providers", "mock"]) == 0
    repo = tmp_path / "proj"
    repo.mkdir()
    monkeypatch.chdir(repo)
    assert alter.main(["init", "--no-memory", *flags]) == 0
    capsys.readouterr()
    assert alter.main(["status"]) == 0
    lines = [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip().startswith("tests run:")]
    assert lines == ([expected] if expected else [])
