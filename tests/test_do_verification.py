"""`ci do` says what the session's own records verify, apart from what the agent says.

It is read-only. The verdict comes from plan_exec's evidence rule: a change, and the project's
tests passing after the last change. It changes no outcome and no exit code.
"""
from test_router import Scripted, term  # noqa: F401  (term is a fixture)

FIX = "def add(a, b):\n    return a + b\n"
WRITE = {"id": "t1", "name": "write_file", "input": {"path": "calc.py", "content": FIX, "reason": "fix"}}
TEST = {"id": "t2", "name": "run_tests", "input": {"reason": "verify"}}


def _drive(terminal, capsys, args):
    rc = terminal.main(args)
    out = capsys.readouterr().out
    while "waiting for you:" in out:
        tid = next(ln.split()[3] for ln in out.splitlines() if "waiting for you:" in ln)
        rc = terminal.main(["approve", tid])
        out = capsys.readouterr().out
    return rc, out


def test_a_change_that_was_never_tested_is_reported_not_verified(term, capsys):
    terminal, ci, ws, agent, p, repo = term
    agent.set_adapter("mock", None, Scripted([{"text": "Fixing.", "tool_uses": [WRITE]},
                                              {"text": "Fixed add(); it works now."}]))
    rc, out = _drive(terminal, capsys, ["do", "--model", "mock", "fix add() in calc.py"])
    assert "a + b" in (repo / "calc.py").read_text() and "it works now" in out
    assert "not verified: the tests were not run after the last change" in out
    rec = terminal._do_log(ci, "u")[1][-1]
    assert rec["verification"]["verdict"] == "not_verified" and rc == 0  # the claim is reported, not overridden


def test_a_change_followed_by_a_passing_test_run_is_reported_verified(term, capsys):
    terminal, ci, ws, agent, p, repo = term
    agent.set_adapter("mock", None, Scripted([{"text": "Fixing.", "tool_uses": [WRITE]},
                                              {"text": "Testing.", "tool_uses": [TEST]}, {"text": "Done."}]))
    rc, out = _drive(terminal, capsys, ["do", "--model", "mock", "fix add() in calc.py"])
    assert "verified: the project's tests passed after the last change" in out
    assert terminal._do_log(ci, "u")[1][-1]["verification"]["verdict"] == "verified"


def test_a_refused_action_is_listed_and_nothing_changed_is_said_plainly(term, capsys):
    terminal, ci, ws, agent, p, repo = term
    agent.set_adapter("mock", None, Scripted([{"text": "Writing notes.", "tool_uses": [
        {"id": "t9", "name": "write_file", "input": {"path": "NOTES.md", "content": "x", "reason": "notes"}}]},
        {"text": "I could not write the notes."}]))
    rc = terminal.main(["do", "--model", "mock", "write some notes"])
    out = capsys.readouterr().out
    tid = next(ln.split()[3] for ln in out.splitlines() if "waiting for you:" in ln)
    terminal.main(["deny", tid, "not that file"])
    out = capsys.readouterr().out
    assert "no file was changed" in out and "refused or cancelled: 1 action(s): write_file NOTES.md" in out
    assert not (repo / "NOTES.md").exists()


def test_a_question_only_turn_reports_no_change(term, capsys):
    terminal, ci, ws, agent, p, repo = term
    agent.set_adapter("mock", None, Scripted([{"text": "Which helper do you mean?"}]))
    rc, out = _drive(terminal, capsys, ["do", "--model", "mock", "fix the helper in calc.py"])
    assert "no file was changed" in out and rc == 0


def test_ci_do_forget_removes_the_request_and_its_conversation_and_keeps_the_action_record(term, capsys):
    terminal, ci, ws, agent, p, repo = term
    agent.set_adapter("mock", None, Scripted([{"text": "Fixing.", "tool_uses": [WRITE]}, {"text": "Done."},
                                              {"text": "Listing."}]))
    _drive(terminal, capsys, ["do", "--model", "mock", "fix add() in calc.py, my secret project"])
    terminal.main(["do", "--model", "mock", "list the files"])
    capsys.readouterr()
    first, second = terminal._do_log(ci, "u")[1]
    tasks_before = ci.store.query("SELECT COUNT(*) AS n FROM tasks WHERE user_id='u'")[0]["n"]
    assert terminal.main(["do", "forget", first["id"]]) == 0
    assert "Forgot 1 request(s) and 1 agent conversation(s)" in capsys.readouterr().out
    log = terminal._do_log(ci, "u")[1]
    assert [r["id"] for r in log] == [second["id"]]
    assert not ci.store.query("SELECT id FROM sessions WHERE id=?", (first["session_id"],))
    assert ci.store.query("SELECT COUNT(*) AS n FROM tasks WHERE user_id='u'")[0]["n"] == tasks_before  # the action record stays
    assert ci.audit.verify()[0]
    assert terminal.main(["do", "forget", "--all"]) == 0
    assert terminal._do_log(ci, "u")[1] == []


def test_a_failed_run_then_a_fix_then_a_passing_run_is_verified(term, capsys):
    """The common sequence: change, tests fail, fix, tests pass. Only the runs after the last change count."""
    import sys
    terminal, ci, ws, agent, p, repo = term
    ws.authorize_workspace("u", p.id, str(repo), allow_write=True, run_argv=[
        sys.executable, "-c", "import sys; sys.path.insert(0, '.'); import calc; sys.exit(0 if calc.add(2, 3) == 5 else 1)"])
    wrong = {"id": "w1", "name": "write_file", "input": {"path": "calc.py", "content": "def add(a, b):\n    return a * b\n",
                                                          "reason": "first try"}}
    agent.set_adapter("mock", None, Scripted([{"text": "Trying.", "tool_uses": [wrong]},
                                              {"text": "Testing.", "tool_uses": [{"id": "r1", "name": "run_tests", "input": {"reason": "check"}}]},
                                              {"text": "Fixing.", "tool_uses": [WRITE]},
                                              {"text": "Testing again.", "tool_uses": [{"id": "r2", "name": "run_tests", "input": {"reason": "check"}}]},
                                              {"text": "Done: add() fixed and the tests pass."}]))
    rc, out = _drive(terminal, capsys, ["do", "--model", "mock", "fix add() in calc.py"])
    assert "verified: the project's tests passed after the last change" in out, out[-600:]


def test_a_passing_run_followed_by_another_change_is_not_verified(term, capsys):
    terminal, ci, ws, agent, p, repo = term
    agent.set_adapter("mock", None, Scripted([{"text": "Fixing.", "tool_uses": [WRITE]},
                                              {"text": "Testing.", "tool_uses": [TEST]},
                                              {"text": "One more tweak.", "tool_uses": [{**WRITE, "id": "t3"}]},
                                              {"text": "All done."}]))
    rc, out = _drive(terminal, capsys, ["do", "--model", "mock", "fix add() in calc.py"])
    assert "not verified: the tests were not run after the last change" in out


def test_a_same_size_fix_written_within_a_second_is_tested_as_the_new_code(tmp_path):
    """CPython reuses a .pyc whose source has the same size and whole-second mtime. A test run
    straight after a same-size rewrite must still see the new code."""
    import sys
    from pcim.engine import PersonalCI
    from pcim.governor import Governor
    from pcim.tasks import TaskQueue
    from pcim.workspace import Workspace
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", "U", providers=("mock",))
    repo = tmp_path / "repo"
    repo.mkdir()
    ws = Workspace(ci)
    p = ws.create_project("u", "Calc")
    ws.authorize_workspace("u", p.id, str(repo), allow_write=True, run_argv=[
        sys.executable, "-c", "import sys; sys.path.insert(0, '.'); import calc; sys.exit(0 if calc.add(2, 3) == 5 else 1)"])
    q = TaskQueue(ws, Governor(ws))
    for content, expect_pass in (("def add(a, b):\n    return a * b\n", False), ("def add(a, b):\n    return a + b\n", True)):
        (repo / "calc.py").write_text(content)  # same size, same second
        t = q.propose("u", p.id, "run_tests", {"reason": "check"})
        q.approve("u", t.id)
        t = q.get("u", t.id)
        assert (not t.reason) is expect_pass, (content, t.reason)
