"""Regression tests for the independent review of ALTER 0.3.0a1 (findings 1-17), one per finding.
Run against the reviewed code (4f8a716), 17 of these 18 failed. The exception is finding 14's
non-retryable case: the old code also parked after one call, though only because it caught every
ProviderError as a RuntimeError. Mock agent or the stand-in `claude`: no model is called."""

import json
import os
import shlex
import signal
import subprocess
import sys
import time

import pytest
from test_alter_cli import check_cmd, git, home  # noqa: F401  (fixture)
from test_alter_goals import BAD_OK, GOOD, RUN, W, _texts, env, runner, start  # noqa: F401  (fixture)

from pcim import alter, thirdeye, tools
from pcim import goals as G
from pcim.adapters.base import ProviderError
from pcim.adapters.mock_agent import MockAgentAdapter
from pcim.workspace import Workspace


# 1 ------------------------------------------------------------------------------------------------
def test_1_the_worker_never_imports_code_from_the_project(home, capsys):
    tmp, repo, vault = home
    marker = tmp / "pwned"
    (repo / "argparse.py").write_text(f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(99)\n")
    alter.main(["init", "--tests", check_cmd()])
    alter.main(["permissions", "autonomy", "grant"])
    assert alter.main(["goals", "start", "fix add() please", "--background"]) == 0
    from pcim import terminal as T
    ci, uid = T._open()
    gs = G.GoalStore(ci)
    gid = gs.list(uid)[0].id
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and gs.get(uid, gid).status not in G.FINAL:
        time.sleep(0.3)
    assert gs.get(uid, gid).status == "done" and not marker.exists()
    log = (gs.dir / f"{gid}.log").read_text()  # 11: ids and statuses only, never model text
    assert "Intended outcome" not in log and "calc.py" not in log and json.loads(log.strip().splitlines()[-1])["status"] == "done"
    assert alter.main(["goals", "forget", gid]) == 0
    assert not (gs.dir / f"{gid}.log").exists() and not (gs.dir / f"{gid}.lock").exists()


def test_resume_reaps_an_interrupted_test_before_starting_a_new_one(home, capsys):
    from pcim import terminal as T
    from pcim.governor import Governor
    from pcim.tasks import TaskQueue

    _, repo, _ = home
    test_command = """
import fcntl
import time
from pathlib import Path
import calc

with Path('test-process.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    first = Path('first-test-started')
    if not first.exists():
        first.touch()
        end = time.monotonic() + 20
        while not Path('release-first-test').exists() and time.monotonic() < end:
            time.sleep(0.05)
    assert calc.add(2, 3) == 5
"""
    assert alter.main(["init", "--tests", shlex.join([sys.executable, "-c", test_command])]) == 0
    assert alter.main(["permissions", "autonomy", "grant"]) == 0
    ci, uid = T._open()
    here = T._here(ci, uid)
    ws = Workspace(ci)
    queue = TaskQueue(ws, Governor(ws))
    gs = G.GoalStore(ci)
    assert alter.main(["goals", "start", "fix add()", "--background"]) == 0
    try:
        deadline = time.monotonic() + 10
        while not (repo / "first-test-started").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (repo / "first-test-started").exists()
        goal = gs.list(uid, here.project_id)[0]
        [running] = queue.list(uid, here.project_id, status="running")
        assert running.tool == "run_tests"
        os.kill(goal.worker_pid, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while gs.is_live(goal.id) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert gs.shown_status(gs.get(uid, goal.id)) == "interrupted"
        while not queue.unreconciled_test_count(uid) and time.monotonic() < deadline:
            time.sleep(0.05)
        capsys.readouterr()
        assert alter.main(["doctor", "--json"]) == 0
        checks = {c["name"]: c["status"] for c in json.loads(capsys.readouterr().out)["checks"]}
        assert checks["test_runs"] == "warn" and queue.get(uid, running.id).status == "running"
        assert alter.main(["goals", "resume", goal.id]) == 0
        out = capsys.readouterr().out
        assert "verified: the project's tests passed" in out
        assert queue.get(uid, running.id).status == "interrupted"
        attempts = [t for t in queue.list(uid, here.project_id) if t.tool == "run_tests"]
        assert [t.status for t in attempts] == ["succeeded", "interrupted"]
    finally:
        (repo / "release-first-test").touch()


def test_1b_the_search_child_does_not_import_from_its_working_directory(tmp_path, monkeypatch):
    marker = tmp_path / "pwned"
    (tmp_path / "json.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n")
    (tmp_path / "a.txt").write_text("needle\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(p for p in sys.path if p))
    r = tools._search_in_child(tmp_path, "needle", "**/*", 20)
    assert r["matches"] and not marker.exists()


# 2 ------------------------------------------------------------------------------------------------
def test_2_a_readme_symlinked_outside_the_workspace_is_not_read(tmp_path):
    secret = tmp_path / "id_ed25519"
    secret.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nSECRETKEY\n")
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README.md").symlink_to(secret)
    (repo / "a.py").write_text("x = 1\n")
    git(repo, "add", "a.py")
    git(repo, "commit", "-qm", "init")
    ev, _ = thirdeye._repo(repo)
    assert not any("SECRETKEY" in e.text for e in ev) and not any(e.cite == "file:README.md" for e in ev)


# 3 ------------------------------------------------------------------------------------------------
def test_3_git_log_never_runs_a_signature_program_named_in_the_repository(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    marker = tmp_path / "ran"
    prog = tmp_path / "fakegpg"
    prog.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    prog.chmod(0o755)
    git(repo, "init", "-q")
    (repo / "a.py").write_text("x = 1\n")
    git(repo, "add", "a.py")
    tree = subprocess.run(["git", "-C", str(repo), "write-tree"], capture_output=True, text=True, check=True).stdout.strip()
    body = (f"tree {tree}\nauthor t <t@example.invalid> 0 +0000\ncommitter t <t@example.invalid> 0 +0000\n"
            "gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQ==\n -----END PGP SIGNATURE-----\n\nsigned\n")
    sha = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"], input=body,
                         capture_output=True, text=True, check=True).stdout.strip()
    git(repo, "update-ref", "HEAD", sha)
    git(repo, "config", "log.showSignature", "true")
    git(repo, "config", "gpg.program", str(prog))
    subprocess.run(["git", "-C", str(repo), "log", "-1"], capture_output=True)
    assert marker.exists(), "the vector is real: a plain git log runs the program"
    marker.unlink()
    ev, _ = thirdeye._repo(repo)
    assert any(e.cite.startswith("git:") and e.text == "signed" for e in ev) and not marker.exists()


def test_3b_status_ignores_dirty_submodules(monkeypatch, tmp_path):
    calls = []
    real = tools._git

    def spy(root, *a):
        calls.append(a)
        return real(root, *a)
    monkeypatch.setattr(tools, "_git", spy)
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q")
    thirdeye._repo(repo)
    assert any(a[0] == "status" and "--ignore-submodules=dirty" in a for a in calls)
    assert all("--no-show-signature" in a for a in calls if a[0] == "log")


# 4 ------------------------------------------------------------------------------------------------
TWO = {"text": "Two changes.", "tool_uses": [
    {"id": "w1", "name": "write_file", "input": {"path": "a.txt", "content": "A", "reason": "a"}},
    {"id": "w2", "name": "write_file", "input": {"path": "b.txt", "content": "B", "reason": "b"}}]}


def test_4_approving_one_task_approves_only_that_task(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [TWO, {"text": "ok"}], autonomous=False)
    g = r.run(start(r).id)
    pend = G.pending_tasks(ci, "u", g)
    assert [t["what"] for t in pend] == ["a.txt", "b.txt"]
    g = r.resolve_pending(g.id, True, task_ids=[pend[0]["id"]])
    assert (repo / "a.txt").exists() and not (repo / "b.txt").exists()
    assert g.status == "waiting_approval" and [t["what"] for t in G.pending_tasks(ci, "u", g)] == ["b.txt"]
    with pytest.raises(G.GoalError, match="not waiting on"):
        r.resolve_pending(g.id, True, task_ids=["task_nope"])


# 5 ------------------------------------------------------------------------------------------------
def test_5_a_turn_resumed_by_an_approval_still_obeys_a_pause(env):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [W(), RUN(), {"text": "done"}], autonomous=False)
    g = r.run(start(r).id)
    assert g.status == "waiting_approval"
    calls = len(mock.requests)
    r.gs.add_event("u", g.id, "pause")  # from another terminal, while the approval is being resolved
    g = r.resolve_pending(g.id, True)
    assert g.status == "paused" and len(mock.requests) == calls  # the write ran; no model step after the pause
    assert repo.joinpath("calc.py").read_text() == GOOD and r.gs.pending_events("u", g.id) == []


# 6 ------------------------------------------------------------------------------------------------
def test_6_a_pause_applied_before_the_worker_starts_is_kept(env):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [W(), RUN(), {"text": "done"}])
    g = start(r)
    r.gs.add_event("u", g.id, "pause")
    assert r.apply_events(g.id).status == "paused" and mock.requests == []
    assert r.run(g.id).status == "paused" and mock.requests == []  # a late worker does not undo it
    assert r.run(g.id, resume=True).status == "done"


# 7 ------------------------------------------------------------------------------------------------
def test_7_after_a_memory_change_the_new_session_is_given_the_goal_again(env):
    ci, ws, p, repo, here = env
    item = ci.remember("u", "I like small diffs", "preference").item.id
    r, mock = runner(ci, here, [{"text": "Should add() return the sum?"}, W(), RUN(), {"text": "done"}])
    g = r.run(start(r, "make add right").id)
    assert g.status == "waiting_answer"
    ci.memory.delete("u", item)
    r.gs.add_event("u", g.id, "steer", "yes, the sum")
    g = r.run(g.id)
    resumed = _texts(mock)[1]
    assert g.status == "done" and "Goal: make add right" in resumed and "<alter_context" in resumed
    assert "yes, the sum" in resumed and len(set(g.payload["sessions"])) == 2


# 8 ------------------------------------------------------------------------------------------------
def test_8_a_test_run_before_a_later_approved_write_does_not_verify_it(env):
    ci, ws, p, repo, here = env
    repo.joinpath("calc.py").write_text(GOOD)
    ws.grant_autonomy("u", here.project_id, ["run_tests"], max_actions=10)
    ws.set_mode("u", "autonomous")
    both = {"text": "Change and test.", "tool_uses": [
        {"id": "w1", "name": "write_file", "input": {"path": "calc.py", "content": BAD_OK, "reason": "r"}},
        {"id": "t1", "name": "run_tests", "input": {"reason": "check"}}]}
    r, _ = runner(ci, here, [both, {"text": "Done."}, {"text": "Done."}, {"text": "Done."}], autonomous=False)
    g = r.run(start(r).id)
    assert g.status == "waiting_approval"  # the run already ran (and passed, on the old code); the write waits
    g = r.resolve_pending(g.id, True)
    assert g.payload["verification"]["verdict"] == "not_verified" and g.status != "done"


# 9 ------------------------------------------------------------------------------------------------
def test_9_notes_derived_from_memory_are_left_out_without_a_vendor_and_a_resumed_brief_is_fresh(env, tmp_path):
    ci, ws, p, repo, here = env
    ws.add_note("u", p.id, "decision", "derived from memory", source="model", lineage=["mem_x"])
    ws.add_note("u", p.id, "decision", "plain decision", source="user")
    texts = [e.text for e in thirdeye._remembered(ci, "u", p.id)]
    assert any("plain decision" in t for t in texts) and not any("derived from memory" in t for t in texts)
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "Calc.md").write_text("# Calc\nThe calculator must add numbers before the demo.\n")
    thirdeye.set_source(ws, "u", "vault", True, str(vault))
    r, _ = runner(ci, here, [{"text": "Should add() return the sum?"}])
    g = r.run(start(r, "calc add numbers").id)
    assert "[Calc]" in g.payload["context"]["text"]
    thirdeye.set_source(ws, "u", "vault", False)
    resumed = r._resume_message(r.gs.get("u", g.id))
    assert "[Calc]" not in resumed and "vault: off" in resumed


# 10 -----------------------------------------------------------------------------------------------
def test_10_a_refused_change_is_not_done_and_a_question_is_not_answered_by_continue(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [W(), {"text": "Understood, I will leave it."}], autonomous=False)
    g = r.run(start(r).id)
    g = r.resolve_pending(g.id, False, "not now")
    assert g.status == "stopped" and "refused" in g.payload["reason"]
    r2, mock = runner(ci, here, [{"text": "Which one should I change?"}, {"text": "never sent"}])
    g2 = r2.run(start(r2, "change it").id)
    assert g2.status == "waiting_answer"
    assert r2.run(g2.id, resume=True).status == "waiting_answer" and len(mock.requests) == 1


# 12, 13 -------------------------------------------------------------------------------------------
def test_12_13_a_deleted_project_leaves_no_goals_and_a_live_goal_cannot_be_forgotten(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [W()])
    g = start(r)
    with r.gs.worker_lock(g.id):
        with pytest.raises(G.GoalError, match="running"):
            r.gs.delete("u", g.id)
    ws.delete_project("u", p.id)
    assert G.GoalStore(ci).list("u") == []
    assert ci.store.query("SELECT count(*) FROM goals")[0][0] == 0


# 14 -----------------------------------------------------------------------------------------------
class Failing(MockAgentAdapter):
    def __init__(self, retryable):
        super().__init__(script=[])
        self.retryable, self.calls = retryable, 0

    def agent_step(self, *a, **k):
        self.calls += 1
        raise ProviderError("429 overloaded" if self.retryable else "401 bad key", retryable=self.retryable)


@pytest.mark.parametrize("retryable,calls", [(True, 2), (False, 1)])
def test_14_provider_errors_follow_their_retryable_flag(env, monkeypatch, retryable, calls):
    ci, ws, p, repo, here = env
    monkeypatch.setattr("time.sleep", lambda s: None)
    r, _ = runner(ci, here, [])
    f = Failing(retryable)
    r.agent.set_adapter("mock", None, f)
    g = r.run(start(r).id)
    assert f.calls == calls and g.status == "paused"


# 15 -----------------------------------------------------------------------------------------------
def test_15_cli_edges(home, capsys):
    tmp, repo, vault = home
    alter.main(["init", "--tests", check_cmd()])
    from pcim import terminal as T
    ci, uid = T._open()
    assert alter.main(["stop"]) == 2 and G.GoalStore(ci).list(uid) == []  # a typo is not a goal
    assert alter.main(["permissions", "vault", "on", "--path"]) == 2
    ws = Workspace(ci)
    ws.set_halted(uid, True)
    alter.main(["goals", "start", "fix add()"])  # waits for approval
    gid = G.GoalStore(ci).list(uid)[0].id
    alter.main(["resume", gid])  # the goal, never Stop-all
    assert Workspace(T._open()[0]).is_halted(uid)


# 16, 17 -------------------------------------------------------------------------------------------
def test_16_model_text_is_made_safe_for_the_terminal(env, capsys):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [{"text": "plain \x1b]0;owned\x07 text \x1b[31mred"}])
    r.out = G.GoalRunner(ci, "u", here, agent=r.agent).out  # the default printer
    r.run(start(r, "look").id)
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and "plain" in out


def test_17_one_running_goal_per_project(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [W()])
    a = start(r)
    a.status = "running"
    r.gs.save(a)
    with r.gs.worker_lock(a.id):
        with pytest.raises(G.GoalError, match="running in this project"):
            start(r, "another")


# second pass ---------------------------------------------------------------------------------------
def test_s1_cancelling_a_goal_never_calls_the_model_or_runs_what_it_waited_on(env):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [W(), {"text": "must never be asked"}], autonomous=False)
    g = r.run(start(r).id)
    assert g.status == "waiting_approval"
    pend = G.pending_tasks(ci, "u", g)
    r.gs.add_event("u", g.id, "cancel")
    g = r.apply_events(g.id)
    assert g.status == "cancelled" and len(mock.requests) == 1
    assert r.agent.queue.get("u", pend[0]["id"]).status == "cancelled"
    assert repo.joinpath("calc.py").read_text().endswith("a - b\n")


def test_cancel_during_a_model_call_discards_its_proposed_write(env, monkeypatch):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [W()])
    g = start(r)
    step = mock.agent_step

    def cancel_before_reply(*args, **kwargs):
        reply = step(*args, **kwargs)
        r.gs.add_event("u", g.id, "cancel")
        return reply

    monkeypatch.setattr(mock, "agent_step", cancel_before_reply)

    cancelled = r.run(g.id)

    assert cancelled.status == "cancelled" and len(mock.requests) == 1
    assert (repo / "calc.py").read_text().endswith("a - b\n")
    assert cancelled.payload["verification"]["writes"] == 0


def test_s2_a_goal_does_not_open_a_session_over_another_goals_approval(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [{"text": "Should add() return the sum?"}], autonomous=False)
    a = r.run(start(r, "make add right").id)
    assert a.status == "waiting_answer"
    rb, _ = runner(ci, here, [W(), RUN(), {"text": "done"}], autonomous=False)
    b = rb.run(start(rb, "fix add").id)
    assert b.status == "waiting_approval"
    r.gs.add_event("u", a.id, "steer", "yes")
    a = r.run(a.id)
    assert a.status == "paused" and b.id in a.payload["reason"]
    assert rb.agent.open_session("u", p.id) == b.payload["sessions"][-1]  # B's session was not closed
    b = rb.resolve_pending(b.id, True)
    assert b.status == "waiting_approval" and G.pending_tasks(ci, "u", b)[0]["tool"] == "run_tests"


def test_s3_approving_part_of_a_step_is_not_a_turn_and_the_limit_stops_cleanly(env):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [TWO, {"text": "ok"}], autonomous=False)
    g = r.run(start(r).id)
    first, second = G.pending_tasks(ci, "u", g)
    g = r.resolve_pending(g.id, True, task_ids=[first["id"]])
    assert g.payload["turns"] == 1 and g.status == "waiting_approval" and g.payload["reason"].startswith("1 action")
    g.payload["max_turns"] = 1
    r.gs.save(g)
    g = r.resolve_pending(g.id, True)
    assert g.status == "stopped" and "cancelled, not run" in g.payload["reason"]
    assert not (repo / "b.txt").exists() and len(mock.requests) == 1


def test_s4_a_pause_given_after_a_resume_request_wins(env, monkeypatch):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [W(), RUN(), {"text": "done"}])
    g = start(r)
    r.gs.add_event("u", g.id, "pause")
    r.apply_events(g.id)
    token = r.gs.last_event_id(g.id)  # "keep going": a worker is spawned with this token
    r.gs.add_event("u", g.id, "pause")  # then "pause", before that worker starts
    assert r.apply_events(g.id).status == "paused"
    assert r.run(g.id, resume=True, resume_after=token).status == "paused" and mock.requests == []
    seen = {}
    monkeypatch.setattr(G.subprocess, "Popen", lambda argv, **kw: seen.setdefault("argv", argv) and type("P", (), {"pid": 1})())
    G.spawn_worker(ci, g.id, str(repo), 60, resume=True)
    assert "-P" in seen["argv"] and seen["argv"][seen["argv"].index("--resume-after") + 1] == str(r.gs.last_event_id(g.id))


def test_s5_a_context_source_that_fails_is_listed_and_the_goal_still_starts(env, monkeypatch):
    ci, ws, p, repo, here = env
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("git", 15)
    monkeypatch.setattr(thirdeye, "_repo", boom)
    r, _ = runner(ci, here, [W(), RUN(), {"text": "done"}])
    g = start(r)
    assert "git: not read (TimeoutExpired)" in g.payload["context"]["withheld"]
    assert r.run(g.id).status == "done"


def test_s6_the_twins_do_counts_are_shown_but_never_saved(env):
    ci, ws, p, repo, here = env
    from pcim import twin_do
    from pcim.twin import CognitiveTwin
    twin_do.include_do(ci, "u")
    rv = CognitiveTwin(ci).review("u")
    assert rv.do is not None and "do" in rv.as_dict() and "do" not in rv.snapshot()


def test_s7_short_command_like_phrases_are_not_goals(home, capsys):
    tmp, repo, vault = home
    alter.main(["init", "--tests", check_cmd()])
    from pcim import terminal as T
    ci, uid = T._open()
    for argv in (["Stop."], ["stop", "now"], ["list"], ["show", "goal_x"]):
        assert alter.main(argv) == 2, argv
    assert G.GoalStore(ci).list(uid) == []


def test_s8_pausing_a_goal_that_waits_for_an_answer_keeps_it_waiting(env):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [{"text": "Which file?"}, {"text": "never"}])
    g = r.run(start(r, "change it").id)
    r.gs.add_event("u", g.id, "pause")
    assert r.apply_events(g.id).status == "waiting_answer"
    assert r.run(g.id, resume=True).status == "waiting_answer" and len(mock.requests) == 1
