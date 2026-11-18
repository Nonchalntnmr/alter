"""Deterministic regressions for every planning failure observed in eval_v5 (experiments/eval_v5).

Each test replays the real request text and the shape of the plan the model produced, with the
model scripted out. They were written before the fixes, and each one failed on the code that ran
eval_v5. eval_v5 itself is frozen and untouched.

| eval_v5 run | what went wrong | test |
|---|---|---|
| P06-t08 PA, P10-t07 PA | the planner invented a destructive outcome for a code change; planning-assisted routing trusted it and planned | 1, 2 |
| P12-t17 P | "names and emails" was read as the action "email", and a phantom outside action was restored | 3 |
| P12-t17 P, P02-t03 P | the plan demanded backup copies; the writes were refused, and the session then stopped | 4, 5 |
| P06-t23 P | after the person answered, the plan re-asked about the conflict they had just resolved, until nothing was done | 6 |
"""
import json
from pathlib import Path

import pytest

from pcim import intent_compiler as ic
from pcim import plan_exec as px
from pcim import router

V5 = Path(__file__).resolve().parents[1] / "experiments" / "eval_v5" / "requests.jsonl"


def _request(pair: str) -> str:
    for line in V5.read_text().splitlines():
        r = json.loads(line)
        if r["pair"] == pair:
            return r["request"]
    raise KeyError(pair)


def _plan(request, outcomes, steps=None, **kw):
    d = {"goal": "g", "interpretations": [{"goal": "g", "p": 0.9}], "outcomes": outcomes,
         "steps": steps or [{"id": "s1", "action": "change the code", "kind": "write", "serves": [o["id"] for o in outcomes]},
                            {"id": "s2", "action": "run the tests", "kind": "run", "serves": [o["id"] for o in outcomes]}]}
    d.update(kw)
    spec = ic.validate(d, [], request=request)
    return {"id": "plan_x", "status": "awaiting_approval", "spec": spec, "gate": ic.gate(spec, request).as_dict(),
            "error": None}


# 1. P06-t08 PA: "add a reset command" is code. The planner added "running reset wipes the entries".
@pytest.mark.xfail(strict=True, reason="eval_v5 failure, not yet fixed")
def test_an_invented_destructive_outcome_does_not_make_a_code_change_a_plan():
    req = _request("P06-t08")
    rec = _plan(req, [{"id": "o1", "kind": "deliverable", "text": "A working reset command that keeps goal and unit"},
                      {"id": "o2", "kind": "destructive_change",
                       "text": "Running reset wipes the user's existing test entries (destructive when invoked)"}])
    assert not [o for o in rec["spec"]["outcomes"] if o["kind"] == "destructive_change"]
    assert any("not asked" in f for f in rec["spec"]["flags"])
    assert router.route_from_plan(rec).decision == router.DIRECT


# 2. P10-t07 PA: "change it so each person only shows up once" was planned as deleting rows from a file.
@pytest.mark.xfail(strict=True, reason="eval_v5 failure, not yet fixed")
def test_a_code_change_described_as_removing_rows_is_not_a_destructive_plan():
    req = _request("P10-t07")
    rec = _plan(req, [{"id": "o1", "kind": "destructive_change",
                       "text": "Customer export where each person appears only once, keeping the most recent sign-up"}])
    assert router.route_from_plan(rec).decision == router.DIRECT


# 2b. A destructive change the person really asks for stays one, and is still planned.
def test_a_requested_removal_stays_destructive_and_is_still_planned():
    req = "remove the old export formats from the code, we use the new ones now"
    rec = _plan(req, [{"id": "o1", "kind": "destructive_change", "text": "old export formats removed"}])
    assert [o["kind"] for o in rec["spec"]["outcomes"]] == ["destructive_change"]
    assert router.route_from_plan(rec).decision == router.PLAN


# 3. P12-t17 P: "normal-looking names and emails" is not a request to email anyone.
@pytest.mark.xfail(strict=True, reason="eval_v5 failure, not yet fixed")
def test_a_plural_noun_is_not_read_as_an_outside_action():
    req = _request("P12-t17")
    assert [a for a in ic.requested_actions(req) if a["kind"] == "external_action"] == []
    assert ic.requested_actions("email the report to Raj")[0]["lemma"] == "email"  # the real request still counts
    assert ic.requested_actions("send emails to the team")[0]["lemma"] == "send"


# 4. P12-t17 P, P02-t03 P: nobody asked for backups. The plan's backup steps and criteria are not CI's work.
@pytest.mark.xfail(strict=True, reason="eval_v5 failure, not yet fixed")
def test_an_unrequested_backup_step_and_criterion_are_dropped_and_flagged():
    req = _request("P12-t17")
    rec = _plan(req, [{"id": "o1", "kind": "destructive_change", "text": "pre-March test accounts removed"}],
                steps=[{"id": "s1", "action": "write a backup copy of customers.csv to customers_backup.csv",
                        "kind": "write", "serves": ["o1"]},
                       {"id": "s2", "action": "remove the rows that joined before 1 March 2025", "kind": "write",
                        "serves": ["o1"]}],
                success_criteria=["Original file is preserved or the change is otherwise reversible",
                                  "Zero rows remain with join date earlier than 2025-03-01"])
    s = rec["spec"]
    assert [st["id"] for st in s["steps"]] == ["s2"]
    assert s["success_criteria"] == ["Zero rows remain with join date earlier than 2025-03-01"]
    assert any("backup" in f for f in s["flags"])


def test_a_backup_the_person_asks_for_is_kept():
    req = "back up customers.csv first, then remove the test accounts from it"
    rec = _plan(req, [{"id": "o1", "kind": "deliverable", "text": "test accounts removed, with a backup"}],
                steps=[{"id": "s1", "action": "write a backup copy of customers.csv", "kind": "write", "serves": ["o1"]}])
    assert [st["id"] for st in rec["spec"]["steps"]] == ["s1"]


# 5. The contract execution is told, in words, not to add backups, copies or unrequested destructive runs.
@pytest.mark.xfail(strict=True, reason="eval_v5 failure, not yet fixed")
def test_the_contract_message_forbids_unrequested_backups_and_destructive_runs():
    rec = _plan("fix add() in calc.py", [{"id": "o1", "kind": "deliverable", "text": "add() adds"}])
    rec["id"] = "plan_x"
    msg = px._contract_message(rec)
    assert "backup" in msg and "copy" in msg and "only what the person asked" in msg


# 6. P06-t23 P: after the person answered, the same conflict blocked the plan again, three times.
@pytest.mark.xfail(strict=True, reason="eval_v5 failure, not yet fixed")
def test_after_an_answer_a_conflict_the_person_was_asked_about_does_not_block_again():
    req = _request("P06-t23")
    d = {"goal": "g", "interpretations": [{"goal": "g", "p": 0.9}],
         "outcomes": [{"id": "o1", "kind": "deliverable", "text": "slugs transliterate accented letters"}],
         "steps": [{"id": "s1", "action": "change slugify", "kind": "write", "serves": ["o1"]}],
         "conflicts": ["'handle every character from any language' vs 'slugs stay exactly as they are': the person "
                       "resolved this by keeping non-Latin scripts out of scope"]}
    spec = ic.validate(d, [], request=req, answers="Keep the slugs exactly as they are otherwise.")
    g = ic.gate(spec, req, after_answer=True)
    assert g.decision != "clarify", g.reasons
    assert ic.gate(ic.validate(d, [], request=req), req).decision == "clarify"  # the first time, it still asks
