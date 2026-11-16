"""Adaptive routing (router.py, `ci do`) and the V3.3 gate changes.

The router decides only the first move: direct, clarify or plan. It grants nothing. Writes and
test runs still wait for approval, and no tool acts outside the computer.
"""

import sys
from pathlib import Path

import pytest

from pcim import intent_compiler as ic
from pcim import router
from pcim.adapters.mock_agent import MockAgentAdapter
from pcim.agent import Agent
from pcim.engine import PersonalCI
from pcim.governor import Governor
from pcim.tasks import TaskQueue
from pcim.types import new_id, utcnow
from pcim.workspace import Workspace

PY = sys.executable


# ---------------------------------------------------------------------- the router
@pytest.mark.parametrize("text", [
    "fix the failing date test in utils/ and bump the version in pyproject.toml",
    "add a --verbose flag to the cli and update the readme",
    "delete parse_legacy() from io/reader.py",
    "as discussed in docs/plan.md, split the loader into two modules",
])
def test_clear_requests_go_direct(text):
    assert router.route(text).decision == router.DIRECT


@pytest.mark.parametrize("text,why", [
    ("run the analysis we talked about and write it up", "not given"),
    ("same as last time but for the march data", "not given"),
    ("help", "nothing specific"),
    ("fix it", "nothing specific"),
    ("grant stuff is due soon and im so behind", "placeholder"),
])
def test_requests_resting_on_what_ci_cannot_see_are_asked_about_once(text, why):
    r = router.route(text)
    assert r.decision == router.CLARIFY and why in r.reasons[0] and r.question
    # after one answer it never asks again
    assert router.route(text, answered=True, answers="it's in analysis/q3.py").decision != router.CLARIFY


def test_broad_destructive_scope_is_planned_and_a_specific_one_is_not():
    assert router.route("clean out the old feature flags in config/flags.json").decision == router.PLAN
    assert router.route("remove all the unused helper functions").decision == router.PLAN
    assert router.route("remove parse_v1 from loader.py").decision == router.DIRECT
    # once the answer names what goes, the scope is no longer broad
    r = router.route("clean out the old feature flags", answered=True, answers="just `beta_search` and `legacy_ui`")
    assert r.decision == router.DIRECT


def test_an_action_outside_the_computer_is_the_persons_and_never_a_reason_to_ask():
    r = router.route("fix the date test, then email the report to the team and deploy it to staging")
    assert r.decision == router.DIRECT
    assert len(r.yours) >= 2
    assert "outside the computer" in " ".join(r.reasons)


def test_the_router_reads_words_and_grants_nothing():
    src = Path(router.__file__).read_text()
    assert "governor" not in src.split('"""', 2)[2].lower().replace("decided by the governor", "")
    assert "import" in src and "workspace" not in [ln.split()[-1] for ln in src.splitlines() if ln.startswith("from")]
    r = router.route("deploy the site")
    assert set(r.as_dict()) == {"decision", "reasons", "question", "yours", "signals"}


# ---------------------------------------------------------------------- V3.3 gate
def _spec(outcomes, assumptions, request, p=0.9):
    return ic.validate({"goal": "g", "interpretations": [{"goal": "g", "p": p}],
                        "outcomes": outcomes, "assumptions": assumptions,
                        "steps": [{"id": "s1", "action": "draft it", "kind": "think", "serves": [o["id"] for o in outcomes]}]},
                       [], request=request)


def test_an_open_detail_of_the_persons_own_action_does_not_make_ci_ask():
    req = "let the team know standup moved to 10:30 tomorrow"
    spec = _spec([{"id": "o1", "text": "team told", "kind": "external_action"}],
                 [{"text": "post in #general", "confidence": 0.4, "consequential": True, "about": ["o1"]}], req)
    g = ic.gate(spec, req)
    assert g.decision == "approval"
    assert g.for_your_part == ["post in #general"]
    assert "Check when you do your part" in ic.render({"id": "plan_x", "spec": spec, "gate": g.as_dict(),
                                                      "status": "awaiting_approval"})


def test_an_assumption_that_names_no_outcome_or_names_cis_own_work_still_asks():
    req = "let the team know standup moved, and update the schedule file"
    outs = [{"id": "o1", "text": "team told", "kind": "external_action"},
            {"id": "o2", "text": "schedule.md updated", "kind": "deliverable"}]
    for about in ([], ["o2"], ["o1", "o2"]):
        spec = _spec(outs, [{"text": "which file", "confidence": 0.4, "consequential": True, "about": about}], req)
        assert ic.gate(spec, req).decision == "clarify", about


def test_a_first_compile_asks_about_context_ci_was_not_given_however_sure_the_model_is():
    req = "clean up the survey data and run the analysis we talked about"
    spec = _spec([{"id": "o1", "text": "analysis", "kind": "deliverable"}], [], req, p=0.95)
    g = ic.gate(spec, req)
    assert g.decision == "clarify" and "not given" in g.reasons[0] and g.questions
    assert ic.gate(spec, req, after_answer=True).decision != "clarify"


# ---------------------------------------------------------------------- ci do
@pytest.fixture
def term(tmp_path, monkeypatch):
    from pcim import terminal
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", "U", providers=("mock",))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    ws = Workspace(ci)
    p = ws.create_project("u", "Calc")
    ws.authorize_workspace("u", p.id, str(repo), run_argv=[PY, "-c", "print('1 passed')"], allow_write=True)
    agent = Agent(ci, ws, TaskQueue(ws, Governor(ws)))
    monkeypatch.setattr(terminal, "_open", lambda: (ci, "u"))
    monkeypatch.setattr(terminal, "_here", lambda c, u, cwd=None: terminal.Here(p.id, "Calc", {"mock"}, str(repo)))
    monkeypatch.setattr(terminal, "_workspace_agent", lambda c: agent)
    return terminal, ci, ws, agent, p, repo


class Scripted(MockAgentAdapter):
    def __init__(self, script):
        super().__init__(script=script)
        self.calls = 0

    def agent_step(self, system, messages, tools, max_tokens=None):
        self.calls += 1
        return super().agent_step(system, messages, tools, max_tokens=max_tokens)


def test_ci_do_hands_a_clear_request_to_the_governed_agent_and_the_write_still_waits(term, capsys):
    terminal, ci, ws, agent, p, repo = term
    mock = Scripted([
        {"text": "Fixing add.", "tool_uses": [{"id": "t1", "name": "write_file", "input": {
            "path": "calc.py", "content": "def add(a, b):\n    return a + b\n", "reason": "fix"}}]},
        {"text": "Fixed add() in calc.py."},
    ])
    agent.set_adapter("mock", None, mock)
    assert terminal.main(["do", "--model", "mock", "fix add() in calc.py, it subtracts"]) == 6
    out = capsys.readouterr().out
    assert "─ direct:" in out and "waiting for you:" in out and "write_file calc.py" in out
    assert "a - b" in (repo / "calc.py").read_text()          # nothing written before approval
    task = next(ln.split()[3] for ln in out.splitlines() if "waiting for you:" in ln)
    assert terminal.main(["approve", task]) == 0
    out = capsys.readouterr().out
    assert "a + b" in (repo / "calc.py").read_text() and "Fixed add() in calc.py." in out
    rec = terminal._do_log(ci, "u")[1][-1]
    assert rec["route"]["decision"] == "direct" and rec["session_id"]
    assert any(e["event"] == "do.route" for e in ci.audit.entries("u"))


def test_ci_do_asks_first_without_a_model_call_then_acts_on_the_answer(term, capsys):
    terminal, ci, ws, agent, p, repo = term
    mock = Scripted([{"text": "Read calc.py; nothing to change."}])
    agent.set_adapter("mock", None, mock)
    assert terminal.main(["do", "--model", "mock", "redo the cleanup we talked about"]) == 6
    out = capsys.readouterr().out
    assert "wasn't part of that" in out and "ci do answer" in out
    assert mock.calls == 0
    rid = terminal._do_log(ci, "u")[1][-1]["id"]
    assert terminal.main(["do", "answer", rid, "the one in calc.py: make add() add"]) in (0, 6)
    assert mock.calls == 1                                  # the pinned model is kept for the answer
    rec = terminal._do_log(ci, "u")[1][-1]
    assert rec["answers"] and rec["route"]["decision"] == "direct"


def test_ci_do_says_plainly_when_no_model_may_run_the_project(term, capsys):
    terminal = term[0]
    assert terminal.main(["do", "fix add() in calc.py"]) == 2   # no --model, and no routable model for "mock"
    assert "Not run:" in capsys.readouterr().err


def test_ci_do_plans_a_broad_removal_and_runs_nothing(term, capsys, monkeypatch):
    terminal, ci, ws, agent, p, repo = term
    mock = Scripted([])
    agent.set_adapter("mock", None, mock)
    seen = {}

    def fake_compile(ci_, uid, text, **kw):
        spec = ic.validate({"goal": "remove unused helpers", "interpretations": [{"goal": "g", "p": 0.9}],
                            "outcomes": [{"id": "o1", "text": "helpers removed", "kind": "destructive_change"}],
                            "steps": [{"id": "s1", "action": "edit calc.py", "kind": "write", "serves": ["o1"]}]},
                           [], request=text)
        g = ic.gate(spec, text)
        seen["text"] = text
        rec = {"id": new_id("plan"), "ts": utcnow().isoformat(), "text": text, "answers": None, "parent": None,
               "interaction": None, "served_by": "test", "project_id": kw.get("project_id"), "context_items": [],
               "spec": spec, "gate": g.as_dict(), "error": None, "status": "awaiting_approval",
               "steps_state": {}, "outcomes_state": {}}
        ic._save(ci_, uid, rec)
        return rec

    monkeypatch.setattr(ic, "compile_intent", fake_compile)
    before = (repo / "calc.py").read_text()
    assert terminal.main(["do", "remove all the unused helpers from calc.py"]) == 0
    out = capsys.readouterr().out
    assert "─ plan:" in out and "Approve with: ci plan approve" in out
    assert seen["text"] and mock.calls == 0 and (repo / "calc.py").read_text() == before


def test_ci_do_with_a_question_subject_is_an_ordinary_question(term, monkeypatch):
    terminal = term[0]
    got = {}
    monkeypatch.setattr(terminal, "cmd_ask", lambda argv: got.setdefault("argv", argv) and 0)
    terminal.main(["do", "you", "think", "this", "needs", "a", "rewrite?"])
    assert got["argv"][:2] == ["do", "you"]


def test_ci_do_outside_an_authorized_folder_refuses(term, monkeypatch, capsys):
    terminal = term[0]
    monkeypatch.setattr(terminal, "_here", lambda c, u, cwd=None: None)
    assert terminal.main(["do", "fix add()"]) == 2
    assert "authorized" in capsys.readouterr().err


def test_with_direct_as_the_default_the_router_only_advises(term, capsys, monkeypatch):
    terminal, ci, ws, agent, p, repo = term
    monkeypatch.setattr(router, "ROUTING_DEFAULT", "direct")
    mock = Scripted([{"text": "Which cleanup? I see no notes on it."}])
    agent.set_adapter("mock", None, mock)
    assert terminal.main(["do", "--model", "mock", "redo the cleanup we talked about"]) == 0
    out = capsys.readouterr().out
    assert "direct is the default" in out and "suggests clarify" in out and mock.calls == 1


def test_pcim_do_routing_overrides_the_built_in_default(term, capsys, monkeypatch):
    terminal, ci, ws, agent, p, repo = term
    monkeypatch.setenv("PCIM_DO_ROUTING", "direct")
    mock = Scripted([{"text": "Which cleanup?"}])
    agent.set_adapter("mock", None, mock)
    assert terminal.main(["do", "--model", "mock", "redo the cleanup we talked about"]) == 0
    assert "direct is the default" in capsys.readouterr().out and mock.calls == 1
    monkeypatch.setenv("PCIM_DO_ROUTING", "adaptive")
    assert terminal.main(["do", "--model", "mock", "redo the cleanup we talked about"]) == 6


def test_an_answer_never_lands_in_another_requests_conversation(term, capsys):
    terminal, ci, ws, agent, p, repo = term
    mock = Scripted([{"text": "Which helper do you mean?"}, {"text": "Listing files."}, {"text": "Done."}])
    agent.set_adapter("mock", None, mock)
    assert terminal.main(["do", "--model", "mock", "rename the helper in calc.py"]) == 0
    first = terminal._do_log(ci, "u")[1][-1]
    assert terminal.main(["do", "--model", "mock", "list the files"]) == 0      # closes the first conversation
    second = terminal._do_log(ci, "u")[1][-1]
    assert second["session_id"] != first["session_id"]
    assert terminal.main(["do", "answer", first["id"], "the add helper"]) == 0
    after = next(r for r in terminal._do_log(ci, "u")[1] if r["id"] == first["id"])
    assert after["session_id"] not in (first["session_id"], second["session_id"])  # a new conversation
    st = agent._load("u", p.id, after["session_id"])
    user_text = " ".join(m["content"] if isinstance(m["content"], str) else "" for m in st["messages"] if m["role"] == "user")
    assert "rename the helper in calc.py" in user_text and "the add helper" in user_text
