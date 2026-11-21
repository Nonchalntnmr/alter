"""ALTER goals: durable work through the governed agent. Verified from task records, steerable while
running, pausable at a safe point, resumable after an interruption, bounded, and remembered with
provenance. Driven by the deterministic mock agent: no model is called."""

import sys

import pytest

from pcim import goals as G
from pcim import thirdeye
from pcim.adapters.mock_agent import MockAgentAdapter
from pcim.agent import Agent
from pcim.engine import PersonalCI
from pcim.governor import Governor
from pcim.tasks import TaskQueue
from pcim.terminal import Here
from pcim.workspace import Workspace

PY = sys.executable
GOOD = "def add(a, b):\n    return a + b\n"
CHECK = [PY, "-c", "import sys; sys.path.insert(0, '.'); import calc; assert calc.add(2, 3) == 5; print('1 passed')"]


def W(path="calc.py", content=GOOD):
    return {"text": "Changing it.", "tool_uses": [{"id": f"tw{id(content) % 997}", "name": "write_file",
                                                    "input": {"path": path, "content": content, "reason": "fix"}}]}


def RUN(i=1):
    return {"text": "Testing.", "tool_uses": [{"id": f"tr{i}", "name": "run_tests", "input": {"reason": "verify"}}]}


@pytest.fixture
def env(tmp_path):
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", "U", providers=("mock",))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    ws = Workspace(ci)
    p = ws.create_project("u", "Calc", objective="ship add()")
    ws.authorize_workspace("u", p.id, str(repo), run_argv=CHECK, allow_write=True)
    here = Here(p.id, "Calc", {"mock"}, str(repo))
    return ci, ws, p, repo, here


def runner(ci, here, script, autonomous=True, out=None):
    ws = Workspace(ci)
    if autonomous:
        ws.grant_autonomy("u", here.project_id, ["write_file", "run_tests"], max_actions=20)
        ws.set_mode("u", "autonomous")
    agent = Agent(ci, ws, TaskQueue(ws, Governor(ws)))
    mock = script if not isinstance(script, list) else MockAgentAdapter(script=script)
    agent.set_adapter("mock", None, mock)
    return G.GoalRunner(ci, "u", here, agent=agent, out=(out.append if out is not None else lambda s: None)), mock


def start(r, text="fix add() so it returns the sum", **kw):
    return r.start(text, provider="mock", model=None, **kw)


def _texts(mock):
    out = []
    for req in mock.requests:
        c = req["messages"][-1]["content"]
        out.append(c if isinstance(c, str) else " ".join(b.get("text", "") for b in c if isinstance(b, dict)))
    return out


def test_a_goal_is_done_only_when_the_records_show_tests_passing_after_the_last_change(env):
    ci, ws, p, repo, here = env
    G.grant_outcome_memory(ci, "u")
    r, mock = runner(ci, here, [W(), RUN(), {"text": "Changed calc.py; the tests pass."}])
    g = r.run(start(r).id)
    assert g.status == "done" and g.payload["verification"]["verdict"] == "verified"
    assert g.payload["verification"]["files"] == ["calc.py"] and repo.joinpath("calc.py").read_text() == GOOD
    first = _texts(mock)[0]
    assert "<alter_context" in first and "Goal: fix add() so it returns the sum" in first and "smallest scope" in first
    # learning: an episode computed from the records, with provenance, under the person's grant
    item = next(i for i in ci.memory.current("u") if i.id == g.payload["memory_item"])
    assert item.kind == "episode" and item.source == G.MEMORY_SOURCE and "Ended done" in item.content
    assert item.provenance["goal"] == g.id and item.provenance["verdict"] == "verified"
    assert item.provenance["computed_from"] == "task records"


def test_without_the_grant_nothing_is_written_to_memory(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [W(), RUN(), {"text": "Done."}])
    g = r.run(start(r).id)
    assert g.status == "done" and g.payload["memory_item"] is None and ci.memory.current("u") == []


def test_a_claim_without_a_test_run_is_sent_back_to_verify_then_counts_only_when_it_passes(env):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [W(), {"text": "Done, it works."}, RUN(), {"text": "Now verified."}])
    g = r.run(start(r).id)
    assert g.status == "done" and g.payload["nudges"] == 1 and g.payload["turns"] == 2
    assert any("tests were not run after the last change" in t for t in _texts(mock))


def test_changes_that_never_pass_end_unverified_not_done(env):
    ci, ws, p, repo, here = env
    bad = "def add(a, b):\n    return a * b\n"
    r, _ = runner(ci, here, [W(content=bad), RUN(1), {"text": "Fixed."}, RUN(2), {"text": "Fixed."}, RUN(3),
                              {"text": "Fixed."}])
    g = r.run(start(r).id)
    assert g.status == "unverified" and g.payload["verification"]["verdict"] == "not_verified"
    assert g.payload["nudges"] == G.MAX_NUDGES


def test_approvals_park_the_goal_and_resolving_them_continues_it(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [W(), RUN(), {"text": "Tests pass."}], autonomous=False)
    g = r.run(start(r).id)
    assert g.status == "waiting_approval" and [t["tool"] for t in G.pending_tasks(ci, "u", g)] == ["write_file"]
    g = r.resolve_pending(g.id, approve=True)
    assert g.status == "waiting_approval" and [t["tool"] for t in G.pending_tasks(ci, "u", g)] == ["run_tests"]
    g = r.resolve_pending(g.id, approve=True)
    assert g.status == "done" and g.payload["verification"]["verdict"] == "verified"


class SteeredAdapter(MockAgentAdapter):
    """The person types while the agent works: an event arrives after the first model step."""

    def __init__(self, gs, gid_box, script):
        super().__init__(script=script)
        self.gs, self.gid_box = gs, gid_box

    def agent_step(self, system, messages, tools, max_tokens=None):
        step = super().agent_step(system, messages, tools, max_tokens)
        if len(self.requests) == 1:
            self.gs.add_event("u", self.gid_box[0], "steer", "make it minimalist")
        return step


def test_steering_mid_turn_stops_at_the_next_step_and_keeps_the_work_done(env):
    ci, ws, p, repo, here = env
    box = []
    gs = G.GoalStore(ci)
    r, _ = runner(ci, here, [])
    mock = SteeredAdapter(gs, box, [W(), RUN(), {"text": "Kept it minimal; tests pass."}])
    r.agent.set_adapter("mock", None, mock)
    g = start(r)
    box.append(g.id)
    g = r.run(g.id)
    texts = _texts(mock)
    assert "make it minimalist" in texts[1] and "Keep what is already done" in texts[1]
    assert g.status == "done" and g.payload["steers"][0]["text"] == "make it minimalist"
    assert g.payload["checkpoints"][0]["stop_reason"] == "paused"  # the safe point, with the write recorded
    assert len(set(g.payload["sessions"])) == 1 and repo.joinpath("calc.py").read_text() == GOOD
    assert gs.pending_events("u", g.id) == []


def test_pause_then_resume_and_an_interrupted_goal_is_shown_as_such(env):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [W(), RUN(), {"text": "Tests pass."}])
    g = start(r)
    r.gs.add_event("u", g.id, "pause")
    g = r.run(g.id)
    assert g.status == "paused" and g.payload["reason"] == "paused by you" and mock.requests == []
    g.status = "running"  # as if its worker died mid-turn: no process holds the goal's lock
    r.gs.save(g)
    assert r.gs.shown_status(r.gs.get("u", g.id)) == "interrupted"
    g = r.run(g.id)
    assert g.status == "done"


def test_cancel_ends_the_goal_and_a_finished_goal_takes_no_more_events(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [W()])
    g = start(r)
    r.gs.add_event("u", g.id, "cancel")
    g = r.run(g.id)
    assert g.status == "cancelled"
    with pytest.raises(G.GoalError, match="cancelled"):
        r.gs.add_event("u", g.id, "steer", "more")


def test_limits_stop_before_any_model_call_and_the_window_pauses_instead_of_running_on(env):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [W()])
    assert r.run(start(r, max_turns=0).id).payload["reason"].startswith("turn limit")
    assert r.run(start(r, max_cost_usd=0.0).id).status == "stopped"
    g = r.run(start(r).id, window_s=-1)
    assert g.status == "paused" and "time window" in g.payload["reason"] and mock.requests == []


class Flaky:
    provider, model = "mock", "mock-agent"

    def __init__(self):
        self.calls = 0

    def agent_step(self, *a, **k):
        self.calls += 1
        raise ConnectionError("network down")


def test_a_failing_provider_is_retried_once_then_the_goal_parks_resumable(env, monkeypatch):
    ci, ws, p, repo, here = env
    monkeypatch.setattr("time.sleep", lambda s: None)
    r, _ = runner(ci, here, [])
    flaky = Flaky()
    r.agent.set_adapter("mock", None, flaky)
    g = r.run(start(r).id)
    assert flaky.calls == 2 and g.status == "paused" and "failed twice" in g.payload["reason"]


def test_one_worker_per_goal(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [W()])
    g = start(r)
    with r.gs.worker_lock(g.id):
        assert r.gs.is_live(g.id)
        with pytest.raises(G.GoalBusy):
            r.run(g.id)
    assert not r.gs.is_live(g.id)


def test_a_question_back_waits_for_the_answer_which_continues_the_same_goal(env):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [{"text": "add() subtracts. Should it return the sum?"}, W(), RUN(),
                                {"text": "Done; tests pass."}])
    g = r.run(start(r, "make add right").id)
    assert g.status == "waiting_answer"
    r.gs.add_event("u", g.id, "steer", "yes, the sum")
    g = r.run(g.id)
    assert g.status == "done" and "yes, the sum" in _texts(mock)[1]


def test_report_states_what_the_records_show(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [W(), RUN(), {"text": "Changed calc.py."}])
    g = r.run(start(r).id)
    rep = G.report(ci, "u", g)
    assert "files changed: calc.py" in rep and "1 passed" in rep and "(model-written)" in rep


def test_goal_contents_are_sealed_at_rest(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [W()])
    g = start(r, "a secret objective about zebras")
    raw = ci.store.query("SELECT payload_ct FROM goals WHERE id=?", (g.id,))[0][0]
    assert b"zebras" not in raw
    r.gs.add_event("u", g.id, "steer", "more zebras")
    assert b"zebras" not in ci.store.query("SELECT payload_ct FROM goal_events")[0][0]


def test_the_context_brief_reports_what_it_left_out(env):
    ci, ws, p, repo, here = env
    b = thirdeye.gather(ci, "u", here, "add")
    assert any("not a git repository" in w for w in b.withheld)
    assert any(w.startswith("vault: off") for w in b.withheld) and any(w.startswith("github: off") for w in b.withheld)
