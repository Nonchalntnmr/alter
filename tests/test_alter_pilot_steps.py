"""The `alter` commands the V1 pilot's deliberate interruptions and its close rely on (PILOT.md §4, §5):
`alter pause`, `alter cancel`, and Ctrl-C during `alter approve`. What each one prints must match what it
did, because the person records the interruption from it. The mock agent only: no model is called."""

import sys

import pytest

from pcim import alter, cli
from pcim import goals as G
from pcim import terminal as T
from pcim.agent import Agent

PY = sys.executable


@pytest.fixture
def mock_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PCIM_CLI_REQUIRE_ISOLATION", raising=False)
    assert cli.main(["init", "--user", "u", "--providers", "mock"]) == 0
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    monkeypatch.chdir(repo)
    assert alter.main(["init", "--tests", f"{PY} -c \"print('1 passed')\""]) == 0
    return repo


def _waiting_goal(capsys) -> str:
    """A goal whose first step (the mock runs the tests, as the goal instructions ask) waits on approval."""
    assert alter.main(["goals", "start", "--model", "mock:mock-agent", "check the tests"]) == 6
    capsys.readouterr()
    ci, uid = T._open()
    return G.GoalStore(ci).list(uid)[0].id


def _goal(gid: str) -> G.Goal:
    ci, uid = T._open()
    return G.GoalStore(ci).get(uid, gid)


def test_pausing_a_goal_that_waits_on_approval_says_it_keeps_waiting(mock_home, capsys):
    gid = _waiting_goal(capsys)
    assert alter.main(["pause", gid]) == 0
    out = capsys.readouterr().out
    assert "keeps waiting" in out and "alter approve" in out and "pause at once" not in out
    assert _goal(gid).status == "waiting_approval"


def test_cancelling_a_goal_that_is_not_running_says_it_is_cancelled_now(mock_home, capsys):
    gid = _waiting_goal(capsys)
    assert alter.main(["cancel", gid]) == 0
    out = capsys.readouterr().out
    assert f"Cancelled goal {gid} at once" in out and "when the goal resumes" not in out
    assert _goal(gid).status == "cancelled"


def test_pausing_a_goal_that_is_not_running_pauses_it_now(mock_home, capsys):
    ci, uid = T._open()
    g = G.GoalRunner(ci, uid, T._here(ci, uid), out=lambda s: None).start("check the tests", provider="mock")
    assert alter.main(["pause", g.id]) == 0
    assert f"Paused goal {g.id} at once" in capsys.readouterr().out
    assert _goal(g.id).status == "paused"


def test_ctrl_c_during_alter_approve_is_a_message_and_the_goal_resumes(mock_home, capsys, monkeypatch):
    gid = _waiting_goal(capsys)

    def ctrl_c(self, *a, **kw):
        raise KeyboardInterrupt

    with monkeypatch.context() as m:
        m.setattr(Agent, "resolve", ctrl_c)
        try:
            rc = alter.main(["approve"])
        except KeyboardInterrupt:
            pytest.fail("Ctrl-C during `alter approve` escaped as a traceback")
    assert rc == 5
    err = capsys.readouterr().err
    assert "Ctrl-C" in err and f"alter goals resume {gid}" in err
    ci, uid = T._open()
    gs = G.GoalStore(ci)
    g = gs.get(uid, gid)
    # interrupted, and not still described as waiting on an approval it no longer waits on
    assert gs.shown_status(g) == "interrupted" and not g.payload["reason"]
    assert alter.main(["goals", "resume", gid]) == 6  # the action it waited on waits again: nothing lost
    assert alter.main(["approve"]) == 0
    assert _goal(gid).status == "done"
