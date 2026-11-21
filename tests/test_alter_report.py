"""`alter report`: the pilot's metrics, computed from ALTER's own records with no model call. The goals
here are synthetic, run through the governed agent with the deterministic mock (as in
test_alter_goals); a few records are then edited by hand to prove each detector fires."""

import dataclasses
import datetime as dt
import json

import pytest
from test_alter_cli import check_cmd, home  # noqa: F401  (fixture)
from test_alter_goals import BAD_OK, CHECK, RUN, W, env, runner, start  # noqa: F401  (fixture)

from pcim import alter, pilot
from pcim import goals as G
from pcim.adapters.mock_agent import MockAgentAdapter
from pcim.agent import Agent
from pcim.engine import PersonalCI
from pcim.growth import wilson
from pcim.terminal import Here
from pcim.workspace import Workspace


def manual(ci, here):
    """Every write and test run asks the person again: no grant, collaborative mode."""
    ws = Workspace(ci)
    for x in ws.active_auths("u", here.project_id, "autonomous"):
        ws.revoke("u", x["id"])
    ws.set_mode("u", "collaborative")


def run_goal(ci, here, script, text="fix add() so it returns the sum", autonomous=True, **kw):
    r, mock = runner(ci, here, script, autonomous=autonomous)
    return r, r.run(start(r, text, **kw).id)


def report(ci, **kw):
    return pilot.compute(ci, "u", **kw)


class Priced(MockAgentAdapter):
    """The mock, at $0.10 a model step."""

    def _step(self, text="", tool_uses=None):
        return dataclasses.replace(super()._step(text, tool_uses), cost_usd=0.10)


def test_statuses_and_the_verified_done_rate_with_its_wilson_interval(env):
    ci, ws, p, repo, here = env
    run_goal(ci, here, [W(), RUN(), {"text": "Changed calc.py; the tests pass."}])
    run_goal(ci, here, [W(content=BAD_OK), RUN(1), {"text": "Fixed."}, RUN(2), {"text": "Fixed."}, RUN(3),
                        {"text": "Fixed."}])
    r, _ = runner(ci, here, [W()])
    g = start(r, "something to cancel")
    r.gs.add_event("u", g.id, "cancel")
    assert r.run(g.id).status == "cancelled"
    run_goal(ci, here, [{"text": "add() already returns the sum. Nothing to change."}], text="check add")
    rep = report(ci)
    assert rep["goals"] == {"n": 4, "finished": 4, "active": 0,
                            "by_status": {"cancelled": 1, "done": 2, "unverified": 1}}
    assert rep["verified_done"] == {"k": 1, "n": 4, "rate": 0.25, "ci95": list(wilson(1, 4))}
    assert rep["done_nothing_changed"] == 1 and rep["false_done"] == {"n": 0, "goals": []}
    assert rep["scope"]["model_calls"] == 0


def test_a_done_goal_without_a_verified_or_unchanged_verdict_is_a_false_done(env):
    ci, ws, p, repo, here = env
    r, g = run_goal(ci, here, [W(), RUN(), {"text": "Done."}])
    assert report(ci)["false_done"]["n"] == 0
    g.payload["verification"]["verdict"] = "not_verified"  # what the rule must never let through
    r.gs.save(g)
    assert report(ci)["false_done"] == {"n": 1, "goals": [g.id]}
    g.payload["verification"] = None
    r.gs.save(g)
    assert report(ci)["false_done"]["n"] == 1


def test_identifier_report_counts_hits_and_scan_coverage_without_file_names(env, monkeypatch):
    from pcim import identity_guard

    ci, ws, p, repo, here = env
    monkeypatch.setattr(identity_guard, "markers", lambda: {"name": "synthetic identity"})
    script = [W(content="def add(a, b):\n    return a + b  # synthetic identity\n"), RUN(), {"text": "Done."}]
    _, hit = run_goal(ci, here, script)
    assert hit.payload["identifiers"] == [{"file": "calc.py", "labels": ["name"]}]
    run_goal(ci, here, [W(), RUN(), {"text": "Done."}])
    r, old = run_goal(ci, here, [W(), RUN(), {"text": "Done."}])
    old.payload.pop("identifiers")
    r.gs.save(old)
    r, failed = run_goal(ci, here, [W(), RUN(), {"text": "Done."}])
    failed.payload["identifiers"] = [{"file": "(check failed)", "labels": ["OSError"]}]
    r.gs.save(failed)

    rep = pilot.export(report(ci))

    assert rep["identifiers"] == {"goals_with_hits": 1, "files": 1, "goals": [hit.id],
                                  "scanned_goals": 2, "unscanned_goals": 1, "scan_failures": 1}
    assert "calc.py" not in json.dumps(rep) and "synthetic identity" not in json.dumps(rep)


def test_approvals_answers_and_the_autonomy_share(env):
    ci, ws, p, repo, here = env
    manual(ci, here)
    r, mock = runner(ci, here, [{"text": "add() subtracts. Should it return the sum?"}, W(), RUN(),
                                {"text": "Done; tests pass."}], autonomous=False)
    g = r.run(start(r, "make add right").id)
    assert g.status == "waiting_answer"
    r.gs.add_event("u", g.id, "steer", "yes, the sum")
    assert r.run(g.id).status == "waiting_approval"
    assert r.resolve_pending(g.id, approve=True).status == "waiting_approval"
    assert r.resolve_pending(g.id, approve=True).status == "done"
    run_goal(ci, here, [W(), RUN(), {"text": "Tests pass."}])  # under a grant: nothing asked of the person
    rep = report(ci)
    assert rep["autonomy"] == {"ran_under_grant": 2, "ran_approved_by_person": 2, "share": 0.5}
    assert rep["effort"]["approvals"] == 2 and rep["effort"]["answers"] == 1
    assert rep["effort"]["per_finished_goal_median"] == 1.5 and rep["effort"]["per_finished_goal_mean"] == 1.5
    mine = {x["goal"]: x for x in rep["per_goal"]}[g.id]
    assert (mine["approvals"], mine["answers"], mine["ran_approved_by_person"]) == (2, 1, 2)


def test_policy_denials_are_unauthorized_attempts_and_refusals_are_the_persons(env):
    ci, ws, p, repo, here = env
    # the governor refuses a secret file; the agent goes on and finishes
    run_goal(ci, here, [W(path=".env", content="TOKEN=1\n"), W(), RUN(), {"text": "Tests pass."}])
    manual(ci, here)
    r, _ = runner(ci, here, [W(), {"text": "Understood; I left calc.py as it was."}], autonomous=False)
    g = r.run(start(r, "fix add").id)
    g = r.resolve_pending(g.id, approve=False, reason="not now")
    assert g.status == "stopped"
    u = report(ci)["unauthorized"]
    assert [x["tool"] for x in u["denied_by_policy"]] == ["write_file"]
    assert u["refused_by_person"] == 1 and u["ran_without_permission"] == []


def test_an_action_that_ran_with_neither_a_grant_nor_an_approval_is_counted(env):
    ci, ws, p, repo, here = env
    run_goal(ci, here, [W(), RUN(), {"text": "Tests pass."}])
    assert report(ci)["unauthorized"]["ran_without_permission"] == []
    tid = ci.store.query("SELECT id FROM tasks WHERE tool='write_file'")[0]["id"]
    ci.store.execute("UPDATE tasks SET authorization_id=NULL WHERE id=?", (tid,))  # forge the record
    assert report(ci)["unauthorized"]["ran_without_permission"] == [{"task": tid, "tool": "write_file"}]


def test_cost_in_all_per_finished_goal_and_per_verified_goal(env):
    ci, ws, p, repo, here = env
    run_goal(ci, here, Priced(script=[W(), RUN(), {"text": "Tests pass."}]))  # 3 steps
    run_goal(ci, here, Priced(script=[W(content=BAD_OK), RUN(1), {"text": "Fixed."}, RUN(2), {"text": "Fixed."},
                                      RUN(3), {"text": "Fixed."}]))  # 7 steps, never verified
    c = report(ci)["cost"]
    assert c["total_usd"] == pytest.approx(1.0) and c["median_per_finished_goal_usd"] == pytest.approx(0.5)
    assert c["per_verified_goal_usd"] == pytest.approx(1.0) and c["goals_with_unknown_cost"] == 0


def test_time_to_first_response_and_wall_time_come_from_the_checkpoints(env):
    ci, ws, p, repo, here = env
    r, g = run_goal(ci, here, [W(), {"text": "Done, it works."}, RUN(), {"text": "Now verified."}])  # two turns
    assert len(g.payload["checkpoints"]) == 2 and g.payload["requested_at"] <= g.created_at
    t0 = dt.datetime.fromisoformat(g.created_at)
    g.payload["requested_at"] = (t0 - dt.timedelta(seconds=5)).isoformat()
    g.payload["checkpoints"][0]["at"] = (t0 + dt.timedelta(seconds=30)).isoformat()
    g.payload["checkpoints"][-1]["at"] = (t0 + dt.timedelta(seconds=600)).isoformat()
    r.gs.save(g)
    t = report(ci)["time"]
    assert t == {"first_response_median_s": 35.0, "first_response_n": 1, "wall_median_s": 600.0, "wall_n": 1}


def test_recovery_counts_goals_that_were_paused_or_interrupted_and_later_finished(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [W(), RUN(), {"text": "Tests pass."}])
    a = start(r)  # paused by the person, then resumed to done
    r.gs.add_event("u", a.id, "pause")
    assert r.run(a.id).status == "paused"
    assert r.run(a.id, resume=True).status == "done"
    r, _ = runner(ci, here, [W(), RUN(), {"text": "Tests pass."}])
    b = start(r)  # its worker died mid-run; resumed to done
    b.status = "running"
    r.gs.save(b)
    b = r.run(b.id)
    assert b.status == "done" and len(b.payload["interruptions"]) == 1
    r, _ = runner(ci, here, [W()])
    c = start(r)  # paused, then cancelled
    r.gs.add_event("u", c.id, "pause")
    assert r.run(c.id).status == "paused"
    r.gs.add_event("u", c.id, "cancel")
    assert r.apply_events(c.id).status == "cancelled"
    d = start(r)  # interrupted now, not yet resumed
    d.status = "running"
    r.gs.save(d)
    rc = report(ci)["recovery"]
    assert (rc["paused_or_interrupted"], rc["later_done"], rc["ended_otherwise"], rc["not_finished"]) == (4, 2, 1, 1)
    assert rc["rate"]["k"] == 2 and rc["rate"]["n"] == 3
    assert report(ci)["goals"]["by_status"]["interrupted"] == 1


def test_the_pilot_log_is_sealed_links_to_the_next_goal_and_the_export_holds_no_words(env):
    ci, ws, p, repo, here = env
    with pytest.raises(ValueError):
        pilot.log_task(ci, "u", "   ", 10)
    for bad in (0, -5, float("nan"), float("inf"), "soon"):
        with pytest.raises(ValueError):
            pilot.log_task(ci, "u", "a task", bad)
    e = pilot.log_task(ci, "u", "refactor the zebra parser", 45, p.id)
    assert all(b"zebra" not in bytes(r["state_ct"]) for r in ci.store.query("SELECT state_ct FROM policy_states"))
    assert not any("zebra" in str(x) for x in ci.audit.entries("u"))
    later = pilot.log_task(ci, "u", "a second task, not started yet", 20, p.id)
    _, g = run_goal(ci, here, [W(), RUN(), {"text": "Tests pass."}])
    rep = report(ci)
    first, second = rep["pilot_log"]["entries"]
    assert (first["entry"], first["goal"], first["estimate_minutes"], first["status"]) == (e["id"], g.id, 45.0, "done")
    assert first["verdict"] == "verified" and first["approvals_and_answers"] == 0
    assert second["entry"] == later["id"] and second["goal"] is None
    assert rep["pilot_log"]["logged"] == 2 and rep["pilot_log"]["linked"] == 1
    shared = json.dumps(pilot.export(rep))
    assert "zebra" not in shared and "second task" not in shared and "returns the sum" not in shared
    assert "calc.py" not in shared
    assert "zebra" in pilot.render(rep, "project Calc")  # the person's own terminal shows their words
    assert pilot.forget_log(ci, "u") == 2  # withdrawal: every entry and every version is gone
    assert pilot.log_entries(ci, "u") == [] and report(ci)["pilot_log"]["logged"] == 0
    assert ci.store.query("SELECT COUNT(*) FROM policy_states WHERE name=?", (pilot.LOG_STATE,))[0][0] == 0


def test_days_and_project_scope(env, tmp_path):
    ci, ws, p, repo, here = env
    _, old = run_goal(ci, here, [W(), RUN(), {"text": "Tests pass."}])
    ci.store.execute("UPDATE goals SET created_at=? WHERE id=?", ("2020-01-01T00:00:00+00:00", old.id))
    run_goal(ci, here, [W(), RUN(), {"text": "Tests pass."}])
    repo2 = tmp_path / "repo2"
    repo2.mkdir()
    (repo2 / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    p2 = ws.create_project("u", "Other")
    ws.authorize_workspace("u", p2.id, str(repo2), run_argv=CHECK, allow_write=True)
    here2 = Here(p2.id, "Other", {"mock"}, str(repo2))
    run_goal(ci, here2, [W(), RUN(), {"text": "Tests pass."}])
    assert report(ci)["goals"]["n"] == 2
    assert report(ci, days=None)["goals"]["n"] == 3
    assert report(ci, project_id=p.id)["goals"]["n"] == 1
    assert report(ci, project_id=p2.id)["per_goal"][0]["project"] == p2.id


def test_alter_report_through_the_command(home, capsys, monkeypatch, tmp_path):
    tmp, repo, vault = home
    assert alter.main(["init", "--tests", check_cmd()]) == 0
    assert alter.main(["permissions", "autonomy", "grant", "--actions", "10", "--minutes", "10"]) == 0
    capsys.readouterr()
    assert alter.main(["report", "--log", "the zebra task", "--estimate-minutes", "25"]) == 0
    assert "your estimate 25 min" in capsys.readouterr().out
    assert alter.main(["fix", "add()", "so", "it", "returns", "the", "sum"]) == 0
    capsys.readouterr()

    def no_model(*a, **k):
        raise AssertionError("alter report must not call a model")
    monkeypatch.setattr(Agent, "send", no_model)
    monkeypatch.setattr(PersonalCI, "ask_routed", no_model)
    assert alter.main(["report"]) == 0
    out = capsys.readouterr().out
    assert "Verified done: 1 of 1 finished goals" in out and "project proj" in out and "zebra" in out
    assert alter.main(["report", "--json", "--days", "7"]) == 0
    raw = capsys.readouterr().out
    rep = json.loads(raw)
    assert rep["verified_done"]["k"] == 1 and rep["pilot_log"]["linked"] == 1 and rep["cost"]["total_usd"] > 0
    assert rep["unauthorized"]["ran_without_permission"] == [] and rep["false_done"]["n"] == 0
    assert "zebra" not in raw and "returns the sum" not in raw and "calc.py" not in raw
    # wrong use is refused with a usage line, not a traceback
    assert alter.main(["report", "--log", "no estimate"]) == 2
    assert alter.main(["report", "--log", "x", "--estimate-minutes", "0"]) == 2
    assert alter.main(["report", "--days", "0"]) == 2
    monkeypatch.chdir(tmp_path)
    assert alter.main(["report", "--project", "here"]) == 2
    assert alter.main(["report", "--project", "all", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["goals"]["n"] == 1
    assert alter.main(["report", "--forget-log"]) == 0 and "Deleted the pilot log (1 entry)" in capsys.readouterr().out
