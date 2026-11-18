"""Ids typed by the person resolve to exactly one record, or to none.

Before: `ci plan approve <suffix>` approved the *first* plan whose id ended in the suffix, and
`ci do answer <suffix>` answered the first such request. Approval is consent; it must never land
on a plan the person did not name.
"""
import pytest

from pcim import intent_compiler as ic
from pcim.types import utcnow
from test_intent_compiler import spec
from test_amplify import env, make  # noqa: F401  (env is a fixture)
from test_router import Scripted, term  # noqa: F401  (term is a fixture)


def test_resolve_id_takes_an_exact_id_or_a_unique_suffix_and_refuses_the_rest():
    ids = ["plan_aaaa1234", "plan_bbbb1234", "plan_cccc9999"]
    assert ic.resolve_id(ids, "plan_bbbb1234") == "plan_bbbb1234"
    assert ic.resolve_id(ids, "c9999") == "plan_cccc9999"
    with pytest.raises(ValueError, match="matches 2 plans"):
        ic.resolve_id(ids, "1234")
    with pytest.raises(ValueError, match="too short"):
        ic.resolve_id(ids, "99")
    with pytest.raises(KeyError):
        ic.resolve_id(ids, "zzzz")


def _plan(ci, pid):
    s = ic.validate(spec(), [])
    ic._save(ci, "u", {"id": pid, "ts": utcnow().isoformat(), "text": "t", "answers": None, "parent": None,
                       "interaction": None, "served_by": "test", "project_id": None, "context_items": [], "spec": s,
                       "gate": ic.gate(s).as_dict(), "error": None, "status": "ready", "steps_state": {},
                       "outcomes_state": {}})


def test_an_ambiguous_suffix_never_approves_a_plan(env, tmp_path, monkeypatch, capsys):
    from pcim import terminal
    ci, _ = make(tmp_path)
    _plan(ci, "plan_old00001234")
    _plan(ci, "plan_new00001234")
    monkeypatch.setattr(terminal, "_open", lambda: (ci, "u"))
    assert terminal.main(["plan", "approve", "1234"]) == 2
    assert "matches 2 plans" in capsys.readouterr().err
    assert all(r["status"] == "ready" for r in ic.records(ci, "u"))     # nothing was approved
    assert terminal.main(["plan", "approve", "new00001234"]) == 0
    assert {r["id"]: r["status"] for r in ic.records(ci, "u")} == {"plan_old00001234": "ready",
                                                                    "plan_new00001234": "approved"}


def test_an_ambiguous_suffix_never_answers_a_request(term, capsys):
    terminal, ci, ws, agent, p, repo = term
    agent.set_adapter("mock", None, Scripted([]))
    for rid in ("do_first0000abcd", "do_second000abcd"):
        terminal._do_save(ci, "u", {"id": rid, "ts": utcnow().isoformat(), "text": "the analysis we talked about",
                                    "project_id": p.id, "answers": None, "status": "needs_answer", "session_id": None,
                                    "plan_id": None, "model": "mock",
                                    "route": {"decision": "clarify", "reasons": [], "question": "q", "yours": [],
                                              "signals": {}}})
    assert terminal.main(["do", "answer", "abcd", "it is in calc.py"]) == 2
    assert "matches 2 requests" in capsys.readouterr().err
    assert all(r["status"] == "needs_answer" for r in terminal._do_log(ci, "u")[1])
