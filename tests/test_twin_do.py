"""The twin's view of `ci do` work: off by default, counts that match the records, gone after forget."""

import datetime as dt
import json

from test_amplify import env, make  # noqa: F401

from pcim import terminal, twin_do
from pcim.types import utcnow

SECRET = "rename the landlord-dispute folder"


def _rec(rid, ts, status="done", answers=None, verdict=None, claim_unverified=False):
    rec = {"id": rid, "ts": ts.isoformat(), "text": f"{SECRET} {rid}", "project_id": "p1",
           "answers": answers, "status": status, "session_id": None, "plan_id": None, "model": None}
    if verdict:
        rec["verification"] = {"verdict": verdict, "line": "", "refused": [], "writes": 1, "test_runs": 0,
                               "claims_done": claim_unverified, "claim_unverified": claim_unverified}
    return rec


def test_the_twin_counts_ci_do_work_only_after_opt_in_and_forgets_what_ci_do_forgets(env, tmp_path, capsys):
    assert twin_do.DO_STATE == terminal.DO_STATE
    ci, _ = make(tmp_path)
    now = utcnow()
    for r in (_rec("do_aaaa0001", now - dt.timedelta(hours=3), verdict="verified"),
              _rec("do_aaaa0002", now - dt.timedelta(hours=2), answers="use pytest", verdict="not_verified",
                   claim_unverified=True),
              _rec("do_aaaa0003", now - dt.timedelta(hours=1), status="needs_answer"),
              _rec("do_aaaa0004", now - dt.timedelta(minutes=30), verdict="unchanged"),
              _rec("do_aaaa0005", now - dt.timedelta(days=30), verdict="verified")):  # outside the window
        terminal._do_save(ci, "u", r)
    since, until = now - dt.timedelta(days=7), now + dt.timedelta(minutes=1)

    # default off: nothing is read until the person opts in
    assert not twin_do.do_included(ci, "u")
    assert twin_do.do_summary(ci, "u", since, until) is None

    twin_do.include_do(ci, "u")
    s = twin_do.do_summary(ci, "u", since, until)
    assert s == {"requests": 4, "answered_a_question": 1, "waiting_for_answer": 1, "no_verdict": 1,
                 "claimed_done_unverified": 1, "verified": 1, "not_verified": 1, "unchanged": 1}
    assert all(isinstance(v, int) for v in s.values())
    assert SECRET not in json.dumps(s) and "pytest" not in json.dumps(s)

    # a request removed with `ci do forget` is not counted in the next summary
    terminal._do_forget(ci, "u", ["do_aaaa0002"])
    capsys.readouterr()
    s2 = twin_do.do_summary(ci, "u", since, until)
    assert s2["requests"] == 3 and s2["not_verified"] == 0
    assert s2["answered_a_question"] == 0 and s2["claimed_done_unverified"] == 0

    # forgetting everything leaves nothing to count
    terminal._do_forget(ci, "u", ["--all"])
    assert twin_do.do_summary(ci, "u", since, until)["requests"] == 0

    # opting out stops the twin reading again
    twin_do.exclude_do(ci, "u")
    assert twin_do.do_summary(ci, "u", since, until) is None


def test_the_weekly_review_and_snapshot_hold_ci_do_counts_only_after_opt_in(env, tmp_path):
    from pcim.twin import CognitiveTwin, render
    ci, _ = make(tmp_path)
    now = utcnow()
    terminal._do_save(ci, "u", _rec("do_bbbb0001", now - dt.timedelta(hours=2), verdict="verified"))
    terminal._do_save(ci, "u", _rec("do_bbbb0002", now - dt.timedelta(hours=1), answers="use pytest",
                                    verdict="not_verified", claim_unverified=True))
    twin = CognitiveTwin(ci)

    # default off: the review, snapshot and render say nothing about ci do
    rv = twin.review("u")
    assert rv.do is None and "do" not in rv.snapshot()
    assert "CI DO" not in render(rv)

    twin_do.include_do(ci, "u")
    rv = twin.review("u")
    # shown in the review, never saved: a saved count would outlive `ci do forget` (second review, item 6)
    assert "do" not in rv.snapshot()
    d = rv.as_dict()["do"]
    assert d["requests"] == 2 and d["verified"] == 1 and d["not_verified"] == 1
    assert d["answered_a_question"] == 1 and d["claimed_done_unverified"] == 1
    assert all(isinstance(v, int) for v in d.values())
    dumped = json.dumps(rv.as_dict(), default=str)  # what is shown, saved or not, quotes no request
    assert SECRET not in dumped and "pytest" not in dumped and "do_bbbb" not in dumped
    text = render(rv)
    assert "CI DO" in text and SECRET not in text


def test_ci_twin_include_and_exclude_do_from_the_terminal(env, tmp_path, monkeypatch, capsys):
    from pcim import cli
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "home"))
    assert cli.main(["init", "--user", "u", "--name", "U", "--providers", "claude-cli"]) == 0
    ci, uid = terminal._open()
    assert not twin_do.do_included(ci, uid)
    assert terminal.cmd_twin(["include", "do"]) == 0
    ci, uid = terminal._open()
    assert twin_do.do_included(ci, uid)
    capsys.readouterr()
    assert terminal.cmd_twin([]) == 0
    assert "ci do records: included" in capsys.readouterr().out
    assert terminal.cmd_twin(["exclude", "do"]) == 0
    ci, uid = terminal._open()
    assert not twin_do.do_included(ci, uid)
    assert terminal.cmd_twin(["include", "everything"]) == 2
    capsys.readouterr()
