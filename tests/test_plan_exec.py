"""`ci execute`: an approved plan runs step by step through the governed agent, pauses on approvals,
records evidence per step, never performs external steps, and resumes after interruption."""

import sys

import pytest

from pcim import intent_compiler as ic
from pcim import plan_exec as px
from pcim.adapters.mock_agent import MockAgentAdapter
from pcim.agent import Agent
from pcim.engine import PersonalCI
from pcim.governor import Governor
from pcim.tasks import TaskQueue
from pcim.types import new_id, utcnow
from pcim.workspace import Workspace

PY = sys.executable


@pytest.fixture
def setup(tmp_path):
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", "U", providers=("mock",))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    ws = Workspace(ci)
    p = ws.create_project("u", "Calc", objective="ship add()")
    ws.authorize_workspace("u", p.id, str(repo), run_argv=[PY, "-c", "print('1 passed')"], allow_write=True)
    agent = Agent(ci, ws, TaskQueue(ws, Governor(ws)))
    return ci, ws, agent, p, repo


def approved_plan(ci, steps, project_id):
    spec = ic.validate({"goal": "ship add() with a passing test", "interpretations": [{"goal": "g", "p": 0.9}],
                        "steps": steps, "success_criteria": ["tests pass"]}, [])
    rec = {"id": new_id("plan"), "ts": utcnow().isoformat(), "text": "ship it", "answers": None, "parent": None,
           "interaction": None, "served_by": "test", "project_id": project_id, "context_items": [], "spec": spec,
           "gate": ic.gate(spec).as_dict(), "error": None, "status": "approved", "steps_state": {}}
    ic._save(ci, "u", rec)
    return rec


def test_only_approved_plans_run_and_only_in_their_own_project(setup):
    ci, ws, agent, p, repo = setup
    rec = approved_plan(ci, [{"id": "s1", "action": "read calc.py", "kind": "read", "depends_on": []}], p.id)
    rec["status"] = "awaiting_approval"
    ic._save(ci, "u", rec)
    with pytest.raises(px.ExecError, match="approved"):
        px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    rec["status"] = "approved"
    ic._save(ci, "u", rec)
    other = ws.create_project("u", "Other")
    with pytest.raises(px.ExecError, match="another project"):
        px.execute(ci, "u", rec["id"], other.id, {"mock"}, provider="mock", agent=agent)


def test_steps_run_in_dependency_order_pause_on_approval_and_resume(setup):
    ci, ws, agent, p, repo = setup
    agent.set_adapter("mock", None, MockAgentAdapter(script=[
        {"text": "Reading.", "tool_uses": [{"id": "t1", "name": "read_file", "input": {"path": "calc.py"}}]},
        {"text": "add() returns a + b; verified by reading it."},
        {"text": "Running.", "tool_uses": [{"id": "t2", "name": "run_tests", "input": {"reason": "step s2"}}]},
        {"text": "1 passed; verified by the test run."},
    ]))
    rec = approved_plan(ci, [{"id": "s2", "action": "run the tests", "kind": "run", "depends_on": ["s1"]},
                             {"id": "s1", "action": "read calc.py", "kind": "read", "depends_on": []}], p.id)
    r1 = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    st = r1["plan"]["steps_state"]
    assert r1["outcome"] == "waiting" and st["s1"]["status"] == "done" and st["s2"]["status"] == "waiting"
    assert list(st["s1"]["task_status"].values()) == ["succeeded"]  # evidence: the read actually ran
    pending = st["s2"]["pending"]
    assert len(pending) == 1 and r1["plan"]["status"] == "waiting"
    assert px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)["outcome"] == "waiting"
    agent.resolve("u", pending[0], approve=True)  # what `ci approve` does
    r2 = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    assert r2["outcome"] == "done" and r2["plan"]["status"] == "done"
    assert ic.get(ci, "u", rec["id"])["status"] == "done"  # saved, so a restart sees it
    assert "approve: ci approve" in px.render(r1) and "done" in px.render(r2)


def test_external_steps_are_never_performed_and_block_only_their_dependents(setup):
    ci, ws, agent, p, repo = setup
    agent.set_adapter("mock", None, MockAgentAdapter(script=[{"text": "Drafted."}, {"text": "Logged."}]))
    rec = approved_plan(ci, [{"id": "s1", "action": "draft the release note", "kind": "think", "depends_on": []},
                             {"id": "s2", "action": "email it to the list", "kind": "external", "depends_on": ["s1"]},
                             {"id": "s3", "action": "log that it went out", "kind": "think", "depends_on": ["s2"]}], p.id)
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    st = r["plan"]["steps_state"]
    assert st["s1"]["status"] == "done" and st["s2"]["status"] == "yours" and st["s3"]["status"] == "blocked"
    assert r["outcome"] == "blocked" and "yours to do" in px.render(r)
    with pytest.raises(px.ExecError, match="external"):
        px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent, done=["s1"])
    r2 = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent, done=["s2"])
    assert r2["outcome"] == "done" and r2["plan"]["steps_state"]["s3"]["status"] == "done"


def test_a_halt_stops_the_plan_before_any_model_call_and_says_why(setup):
    ci, ws, agent, p, repo = setup
    ws.set_halted("u", True)
    mock = MockAgentAdapter(script=[{"text": "should never be asked"}])
    agent.set_adapter("mock", None, mock)
    rec = approved_plan(ci, [{"id": "s1", "action": "read calc.py", "kind": "read", "depends_on": []}], p.id)
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    assert r["outcome"] == "failed" and "halted" in r["plan"]["steps_state"]["s1"]["why"] and mock.requests == []


def test_a_governor_denial_is_recorded_as_evidence_not_hidden(setup):
    ci, ws, agent, p, repo = setup
    (repo / ".env").write_text("API_KEY=secret\n")
    agent.set_adapter("mock", None, MockAgentAdapter(script=[
        {"tool_uses": [{"id": "t1", "name": "read_file", "input": {"path": ".env"}}]},
        {"text": "I was not allowed to read .env."}]))
    rec = approved_plan(ci, [{"id": "s1", "action": "read the settings", "kind": "read", "depends_on": []}], p.id)
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    st = r["plan"]["steps_state"]["s1"]
    assert st["status"] == "done" and list(st["task_status"].values()) == ["denied"]
    assert "denied" in px.render(r)
