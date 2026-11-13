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


def approved_plan(ci, steps, project_id, request=None, outcomes=None):
    spec = ic.validate({"goal": "ship add() with a passing test", "interpretations": [{"goal": "g", "p": 0.9}],
                        "steps": steps, "success_criteria": ["tests pass"], "outcomes": outcomes or []}, [],
                       request=request)
    rec = {"id": new_id("plan"), "ts": utcnow().isoformat(), "text": request or "ship it", "answers": None,
           "parent": None, "interaction": None, "served_by": "test", "project_id": project_id, "context_items": [],
           "spec": spec, "gate": ic.gate(spec, request).as_dict(), "error": None, "status": "approved",
           "steps_state": {}, "outcomes_state": {}}
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
    assert r["outcome"] == "awaiting_you" and "yours to do" in px.render(r)
    assert r["plan"]["outcomes_state"]["oSs2"]["status"] == "yours_pending"  # sending is not done by drafting
    with pytest.raises(px.ExecError, match="neither"):
        px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent, done=["s1"])
    r2 = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent, done=["s2"])
    assert r2["outcome"] == "done" and r2["plan"]["steps_state"]["s3"]["status"] == "done"


def test_stop_all_stops_the_plan_before_any_model_call_and_leaves_the_step_untouched(setup):
    ci, ws, agent, p, repo = setup
    ws.set_halted("u", True)
    mock = MockAgentAdapter(script=[{"text": "Read it; verified."}])
    agent.set_adapter("mock", None, mock)
    rec = approved_plan(ci, [{"id": "s1", "action": "read calc.py", "kind": "read", "depends_on": []}], p.id)
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    assert r["outcome"] == "halted" and r["plan"]["steps_state"]["s1"]["status"] == "not_started"
    assert mock.requests == [] and "Not done: halted" in px.render(r)
    ws.set_halted("u", False)  # ci resume
    assert px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)["outcome"] == "done"


def test_a_governor_denial_makes_the_step_incomplete_never_done(setup):
    ci, ws, agent, p, repo = setup
    (repo / ".env").write_text("API_KEY=secret\n")
    agent.set_adapter("mock", None, MockAgentAdapter(script=[
        {"tool_uses": [{"id": "t1", "name": "read_file", "input": {"path": ".env"}}]},
        {"text": "I was not allowed to read .env."}]))
    rec = approved_plan(ci, [{"id": "s1", "action": "read the settings", "kind": "read", "depends_on": []}], p.id)
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    st = r["plan"]["steps_state"]["s1"]
    assert st["status"] == "incomplete" and list(st["task_status"].values()) == ["denied"]
    assert r["outcome"] == "incomplete" and any("read_file denied" in pr for pr in st["problems"])
    assert "denied" in px.render(r) and "--retry s1" in px.render(r)


# ---------------------------------------------------------------- Mission 4: execution correctness
def statuses(r):
    return {k: v["status"] for k, v in r["plan"]["steps_state"].items()}


def three_steps():
    return [{"id": "s1", "action": "read calc.py", "kind": "read", "depends_on": []},
            {"id": "s2", "action": "run the tests", "kind": "run", "depends_on": ["s1"]},
            {"id": "s3", "action": "summarise", "kind": "think", "depends_on": ["s2"]}]


def test_every_step_has_an_observable_state_after_every_run(setup):
    ci, ws, agent, p, repo = setup
    agent.set_adapter("mock", None, MockAgentAdapter(script=[
        {"text": "Read; verified.", "tool_uses": [{"id": "t1", "name": "read_file", "input": {"path": "calc.py"}}]},
        {"text": "ok"},
        {"text": "Running.", "tool_uses": [{"id": "t2", "name": "run_tests", "input": {"reason": "s2"}}]}]))
    rec = approved_plan(ci, three_steps(), p.id)
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    assert statuses(r) == {"s1": "done", "s2": "waiting", "s3": "not_started"}
    assert all(s in px.STEP_STATES for s in statuses(r).values())
    assert set(ic.get(ci, "u", rec["id"])["steps_state"]) == {"s1", "s2", "s3"}  # stored, not only returned


def test_a_step_found_in_progress_is_interrupted_and_never_rerun_without_retry(setup):
    """A process that died mid-step may have acted. The next run must not repeat it."""
    ci, ws, agent, p, repo = setup
    mock = MockAgentAdapter(script=[{"text": "Read; verified.",
                                     "tool_uses": [{"id": "t1", "name": "read_file", "input": {"path": "calc.py"}}]},
                                    {"text": "done"}])
    agent.set_adapter("mock", None, mock)
    rec = approved_plan(ci, [{"id": "s1", "action": "read calc.py", "kind": "read", "depends_on": []}], p.id)
    rec["steps_state"] = {"s1": {"status": "in_progress", "started": utcnow().isoformat()}}
    rec["status"] = "executing"
    ic._save(ci, "u", rec)
    for _ in range(2):
        r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
        assert r["outcome"] == "interrupted" and statuses(r)["s1"] == "interrupted" and mock.requests == []
    assert "--retry s1" in px.render(r)
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent, retry=["s1"])
    assert r["outcome"] == "done" and r["plan"]["steps_state"]["s1"]["previous"]["status"] == "interrupted"


@pytest.mark.parametrize("state", [None, "done", "waiting", "yours"])
def test_only_failed_interrupted_incomplete_or_halted_steps_can_be_retried(setup, state):
    ci, ws, agent, p, repo = setup
    rec = approved_plan(ci, [{"id": "s1", "action": "read calc.py", "kind": "read", "depends_on": []}], p.id)
    if state:
        rec["steps_state"] = {"s1": {"status": state}}
        ic._save(ci, "u", rec)
    with pytest.raises(px.ExecError, match="can be retried"):
        px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent, retry=["s1"])


def waiting_on_a_write(ci, agent, p):
    mock = MockAgentAdapter(script=[
        {"text": "Writing.", "tool_uses": [{"id": "t1", "name": "write_file",
                                            "input": {"path": "new.py", "content": "x = 1\n", "reason": "s1"}}]},
        {"text": "Written; verified."}])
    agent.set_adapter("mock", None, mock)
    rec = approved_plan(ci, [{"id": "s1", "action": "create new.py", "kind": "write", "depends_on": []},
                             {"id": "s2", "action": "summarise", "kind": "think", "depends_on": ["s1"]}], p.id)
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    assert r["outcome"] == "waiting"
    return rec, r["plan"]["steps_state"]["s1"]["pending"][0], mock


def test_cancel_closes_the_session_then_cancels_pending_actions_so_nothing_resumes_or_runs(setup):
    ci, ws, agent, p, repo = setup
    rec, task, mock = waiting_on_a_write(ci, agent, p)
    calls = len(mock.requests)
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent, cancel=True)
    assert r["outcome"] == "cancelled" and statuses(r) == {"s1": "cancelled", "s2": "cancelled"}
    assert agent.queue.get("u", task).status == "cancelled"
    assert len(mock.requests) == calls  # the model was not called again
    assert not (repo / "new.py").exists()  # the write never happened
    with pytest.raises(Exception, match="cancelled"):
        agent.resolve("u", task, approve=True)  # a late `ci approve` cannot revive it
    with pytest.raises(px.ExecError, match="cancelled"):
        px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    assert not (repo / "new.py").exists()


def test_a_denied_action_makes_the_step_incomplete_and_its_dependents_do_not_run(setup):
    ci, ws, agent, p, repo = setup
    rec, task, mock = waiting_on_a_write(ci, agent, p)
    agent.resolve("u", task, approve=False, reason="not now")  # ci deny; the session resumes and ends
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    assert r["outcome"] == "incomplete" and statuses(r) == {"s1": "incomplete", "s2": "not_started"}
    assert any("write_file denied" in pr for pr in r["plan"]["steps_state"]["s1"]["problems"])
    assert not (repo / "new.py").exists()


def test_approving_records_done_only_from_the_task_record(setup):
    ci, ws, agent, p, repo = setup
    rec, task, mock = waiting_on_a_write(ci, agent, p)
    mock.script.append({"text": "Summarised."})
    agent.resolve("u", task, approve=True)
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    assert (repo / "new.py").read_text() == "x = 1\n"
    assert r["outcome"] == "done" and r["plan"]["steps_state"]["s1"]["task_status"] == {task: "succeeded"}


def test_failing_tests_make_the_step_incomplete_even_when_the_tool_call_succeeded(setup):
    ci, ws, agent, p, repo = setup
    ws.authorize_workspace("u", p.id, str(repo), run_argv=[PY, "-c", "import sys; sys.exit(1)"], allow_write=True)
    agent.set_adapter("mock", None, MockAgentAdapter(script=[
        {"text": "Running.", "tool_uses": [{"id": "t1", "name": "run_tests", "input": {"reason": "s1"}}]},
        {"text": "All good!"}]))  # the model's claim is not the evidence
    rec = approved_plan(ci, [{"id": "s1", "action": "run the tests", "kind": "run", "depends_on": []}], p.id)
    r1 = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    agent.resolve("u", r1["plan"]["steps_state"]["s1"]["pending"][0], approve=True)
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    assert r["outcome"] == "incomplete" and statuses(r)["s1"] == "incomplete"
    assert r["plan"]["steps_state"]["s1"]["problems"] == ["run_tests: tests exited 1"]


def test_stop_all_while_a_step_waits_cancels_the_action_and_the_step_reads_halted(setup):
    ci, ws, agent, p, repo = setup
    rec, task, mock = waiting_on_a_write(ci, agent, p)
    agent.queue.halt("u")  # ci halt
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    assert r["outcome"] == "halted" and statuses(r)["s1"] == "halted"
    assert agent.queue.get("u", task).status == "cancelled" and not (repo / "new.py").exists()
    ws.set_halted("u", False)  # ci resume
    mock.script[:] = [{"text": "Writing.", "tool_uses": [{"id": "t9", "name": "write_file", "input": {
        "path": "new.py", "content": "x = 1\n", "reason": "retry"}}]}, {"text": "Written; verified."}]
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent, retry=["s1"])
    assert r["outcome"] == "waiting"  # the retry asks again: the earlier approval window is gone
    st = r["plan"]["steps_state"]["s1"]
    assert st["closed_stale_session"] and st["previous"]["status"] == "halted"
    assert any(e["event"] == "plan.close_stale_session" and e["payload"]["session"] == st["closed_stale_session"]
               for e in ci.audit.entries("u"))


def test_the_workspace_agent_recovers_by_itself_after_stop_all(setup):
    ci, ws, agent, p, repo = setup
    rec, task, mock = waiting_on_a_write(ci, agent, p)
    agent.queue.halt("u")
    ws.set_halted("u", False)
    agent.send("u", p.id, "carry on", "mock")


def test_a_plan_is_done_only_when_every_requested_outcome_is_met(setup):
    """All steps done is not enough: the request asked for a send, and CI cannot send."""
    ci, ws, agent, p, repo = setup
    agent.set_adapter("mock", None, MockAgentAdapter(script=[{"text": "Summary drafted."}]))
    rec = approved_plan(ci, [{"id": "s1", "action": "draft the summary", "kind": "think", "depends_on": [],
                              "serves": ["o1"]}], p.id, request="summarise the results and send them to priya",
                        outcomes=[{"id": "o1", "text": "a summary of the results", "kind": "deliverable"}])
    r = px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    assert statuses(r) == {"s1": "done"} and r["outcome"] == "awaiting_you"
    assert r["plan"]["outcomes_state"] == {"o1": {"status": "met", "steps": {"s1": "done"}, "judged_by": "linked steps"},
                                           "oR1": {"status": "yours_pending"}}
    assert "Not done: awaiting_you" in px.render(r) and "--done oR1" in px.render(r)
    with pytest.raises(px.ExecError, match="CI's to complete"):
        px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent, done=["o1"])
    assert px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent, done=["oR1"])["outcome"] == "done"


def test_one_run_at_a_time(setup, tmp_path):
    ci, ws, agent, p, repo = setup
    rec = approved_plan(ci, [{"id": "s1", "action": "read calc.py", "kind": "read", "depends_on": []}], p.id)
    with px.plan_lock(ci, rec["id"]):  # another `ci execute` or `ci approve` holds it
        with pytest.raises(px.ExecError, match="another process"):
            px.execute(ci, "u", rec["id"], p.id, {"mock"}, provider="mock", agent=agent)
    assert ic.get(ci, "u", rec["id"])["steps_state"] == {}  # nothing was touched


def test_terminal_has_stop_all_and_approve_takes_the_plan_lock(setup, monkeypatch, capsys):
    from pcim import terminal
    ci, ws, agent, p, repo = setup
    rec, task, mock = waiting_on_a_write(ci, agent, p)
    monkeypatch.setattr(terminal, "_open", lambda: (ci, "u"))
    assert px.plan_for_task(ci, "u", task) == rec["id"]
    with px.plan_lock(ci, rec["id"]):
        assert terminal.main(["approve", task]) == 2
    assert "another process" in capsys.readouterr().err and agent.queue.get("u", task).status == "awaiting_approval"
    assert terminal.main(["halt"]) == 0 and ws.is_halted("u")
    assert agent.queue.get("u", task).status == "cancelled"
    assert terminal.main(["resume"]) == 0 and not ws.is_halted("u")
