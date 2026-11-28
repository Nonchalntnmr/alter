"""eval_v8's open gap (P01-t04): G's third goal, a follow-up after two corrections, changed nothing, ended
with `Outcome: reached`, and was closed `done` through the nothing-changed path. A correction is now a
follow-up goal that records the goal it corrects (`alter say <goal> "…"` to a finished goal), and one that
changes nothing waits for the person, whatever its outcome line says. `alter goals accept` closes a goal
that waits for the person as done, on their word, and it is reported as theirs.

No model is called: a scripted mock agent, and a stand-in `claude` binary for the command line."""

import re
import time

import pytest

from pcim import alter
from pcim import goals as G

from test_alter_cli import FAKE, PY, check_cmd, home  # noqa: F401  (home is a fixture)
from test_alter_goals import RUN, W, _texts, env, runner, start  # noqa: F401  (env is a fixture)

LOOKED_AGAIN = "I looked again: calc.py already returns the sum, so I changed nothing.\n\nOutcome: reached"


def test_a_follow_up_to_a_correction_that_changes_nothing_waits_for_the_person(env):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [W(), RUN(), {"text": "Changed calc.py; 1 passed.\n\nOutcome: reached"},
                                {"text": LOOKED_AGAIN}])
    first = r.run(start(r, "fix add() so it returns the sum").id)
    assert first.status == "done"
    g = r.correct(first, "it must also accept strings of digits")
    assert g.payload["corrects"] == first.id and g.payload["provider"] == "mock"
    assert g.objective.endswith("That is not what I wanted yet: it must also accept strings of digits")
    g = r.run(g.id)
    assert "That is not what I wanted yet: it must also accept strings of digits" in _texts(mock)[-1]
    assert g.payload["verification"]["verdict"] == "unchanged" and g.payload["outcome_said"] == "reached"
    assert g.status == "waiting_answer" and "nothing changed after your correction" in g.payload["reason"]
    g = r.accept(g.id)
    assert g.status == "done" and g.payload["done_by"] == "person" and g.payload["reason"] == "you accepted it"


def test_a_plain_goal_that_changes_nothing_is_done_as_before_and_a_follow_up_that_fixes_it_is_done(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [{"text": "add() looks right to me.\n\nOutcome: reached"},
                             W(), RUN(), {"text": "Fixed add().\n\nOutcome: reached"}])
    first = r.run(start(r).id)
    assert first.status == "done" and first.payload["verification"]["verdict"] == "unchanged"
    g = r.run(r.correct(first, "add() still subtracts").id)
    assert g.status == "done" and g.payload["verification"]["verdict"] == "verified" and g.payload["corrects"] == first.id
    assert "done_by" not in g.payload


def test_only_a_finished_goal_is_corrected_and_only_a_waiting_goal_is_accepted(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [W(), RUN(), {"text": "Done.\n\nOutcome: reached"}])
    g = start(r)
    with pytest.raises(G.GoalError, match="adds to it while it runs"):
        r.correct(g, "and also this")
    g = r.run(g.id)
    with pytest.raises(G.GoalError, match="say what is still wrong"):
        r.correct(g, "   ")
    with pytest.raises(G.GoalError, match="only a goal that waits for your answer"):
        r.accept(g.id)


# The stand-in model answers a correction by changing nothing and claiming the request is reached.
FOLLOW_UP_FAKE = FAKE.replace(
    'if "<ci_tools>" in prompt:',
    'if "<ci_tools>" in prompt and "That is not what I wanted yet" in prompt:\n'
    '    reply({{"text": "I looked again: calc.py already returns the sum.\\n\\nOutcome: reached", "tool_calls": []}})\n'
    'if "<ci_tools>" in prompt:', 1)


def test_alter_say_to_a_finished_goal_is_a_follow_up_that_waits_and_the_report_counts_it(home, capsys):
    tmp, repo, vault = home
    assert FOLLOW_UP_FAKE != FAKE
    (tmp / "bin" / "claude").write_text(FOLLOW_UP_FAKE.format(py=PY))
    assert alter.main(["init", "--tests", check_cmd()]) == 0
    assert alter.main(["permissions", "autonomy", "grant", "--actions", "10", "--minutes", "10"]) == 0
    assert alter.main(["fix", "add()", "so", "it", "returns", "the", "sum"]) == 0
    first = re.search(r"goal (goal_[0-9a-f]+)", capsys.readouterr().out).group(1)

    assert alter.main(["say", "and make it accept strings"]) == 2   # no active goal here: it says how to correct
    assert f'to correct the last one ({first}, done): alter say {first} "…"' in capsys.readouterr().err

    rc = alter.main(["say", first, "it must also accept strings of digits"])
    out = capsys.readouterr().out
    assert rc == 6 and f"follows up goal {first} (done) with your correction" in out
    assert "nothing changed after your correction" in out
    follow = re.search(r"ALTER: goal (goal_[0-9a-f]+) follows up", out).group(1)
    assert alter.main(["goals", "show", follow]) == 0 and f"corrects: {first}" in capsys.readouterr().out

    assert alter.main(["goals", "accept", follow]) == 0 and "you accepted it" in capsys.readouterr().out
    assert alter.main(["goals", "accept", follow]) == 2   # it is done now: nothing waits
    assert alter.main(["report"]) == 0
    assert "Follow-ups to your corrections: 1 · done on your word (alter goals accept): 1" in capsys.readouterr().out


def _settled(ci, uid, gid, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        g = G.GoalStore(ci).get(uid, gid)
        if g.status in G.FINAL or g.status == "waiting_answer":
            return g
        time.sleep(0.3)
    raise AssertionError(G.report(ci, uid, g))


def test_the_conversation_corrects_with_fix_and_closes_with_accept(home, capsys):
    """In the `alter` conversation a new message after a goal finishes is a new goal, so a correction
    needs its own word: /fix. The hint says so when a goal finishes."""
    tmp, repo, vault = home
    (tmp / "bin" / "claude").write_text(FOLLOW_UP_FAKE.format(py=PY))
    alter.main(["init", "--tests", check_cmd()])
    alter.main(["permissions", "autonomy", "grant"])
    capsys.readouterr()
    from pcim import terminal as T
    ci, uid = T._open()
    said = []
    conv = alter.Conversation(ci, uid, T._here(ci, uid), out=said.append, poll_s=0.2)
    assert conv.handle("/fix it is wrong") and "ALTER: there is no goal here to correct yet." in said
    conv.handle("fix add() so it returns the sum")
    first = _settled(ci, uid, conv.watching)
    conv.poll()
    assert first.status == "done" and any("/fix what is wrong" in s for s in said)
    conv.handle("/fix it must also accept strings of digits")
    follow = _settled(ci, uid, conv.watching)
    conv.poll()
    assert follow.status == "waiting_answer" and follow.payload["corrects"] == first.id
    assert any("right after all? /accept" in s for s in said)
    conv.handle("/accept")
    assert G.GoalStore(ci).get(uid, follow.id).status == "done" and f"ALTER: goal {follow.id} is done: you accepted it." in said
    assert conv.handle("/accept") and said[-1] == "ALTER: no goal here waits for your answer."
