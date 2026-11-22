"""eval_v7 failed its gate on item 3a (false done): P06-t20's goal added a helper that nothing calls, its
own tests passed, and it ended "done" while its report said the preview was unchanged. A secondary result
was that the goal asked 2 of the 14 questions its tasks needed, against direct's 9.

- Passing tests are necessary, not sufficient: if the agent's outcome line says "not reached", the goal
  waits for the person and is never done.
- The goal text carries `ci do`'s asking rule and no longer tells the agent to settle for a part.

Five of the six tests failed on the code before this change (feat/orphan-test-runs, 684ea50). The sixth,
that the words never grant "done" without the records, is a guard and passed before as well. Mock agent;
no model is called."""

from pcim import goals as G

from test_alter_goals import RUN, W, _texts, env, runner, start  # noqa: F401  (env is a fixture)

P06_T20_TAIL = """**What remains**
- The preview order has not changed. You or I still need to find where the preview is built.

Outcome: not reached: nothing calls sort_for_preview, so the preview still uses the old order"""


def test_tests_passing_with_the_outcome_not_reached_waits_for_the_person_never_done(env):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [W(), RUN(), {"text": P06_T20_TAIL}, RUN(2), {"text": "Wired it in.\n\nOutcome: reached"}])
    g = r.run(start(r, "fix add() in the preview").id)
    assert g.payload["verification"]["verdict"] == "verified"  # the records alone would have said done
    assert g.status == "waiting_answer" and g.payload["outcome_said"] == "not reached"
    assert "not finished: nothing calls sort_for_preview" in g.payload["reason"]
    r.gs.add_event("u", g.id, "steer", "the preview is built in calc.py; use it there")
    g = r.run(g.id)
    assert g.status == "done" and any("the preview is built in calc.py" in t for t in _texts(mock))


def test_reached_with_passing_tests_is_done_and_no_line_keeps_the_records_rule(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [W(), RUN(), {"text": "Changed calc.py; 1 passed.\n\n**Outcome:** reached"}])
    g = r.run(start(r).id)
    assert g.status == "done" and g.payload["outcome_said"] == "reached"
    r2, _ = runner(ci, here, [W(), RUN(), {"text": "Changed calc.py; 1 passed."}])
    assert r2.run(start(r2).id).status == "done"


def test_the_words_never_grant_done_without_the_records(env):
    ci, ws, p, repo, here = env
    r, _ = runner(ci, here, [W(content="def add(a, b):\n    return a * b\n"), RUN(), {"text": "Outcome: reached"},
                             RUN(2), {"text": "Outcome: reached"}, RUN(3), {"text": "Outcome: reached"}])
    g = r.run(start(r).id)
    assert g.status == "unverified"


def test_nothing_changed_and_not_reached_waits_even_without_a_question_mark(env):
    ci, ws, p, repo, here = env
    reply = ("The repo has no key names for the exhibit title or the banner colour, so I changed nothing.\n\n"
             "Outcome: not reached: I need the two key names.")
    r, _ = runner(ci, here, [{"text": reply}])
    g = r.run(start(r, "reset the old exhibit settings").id)
    assert g.status == "waiting_answer" and "I need the two key names" in g.payload["reason"]


def test_the_goal_text_asks_before_guessing_and_never_settles_for_a_part(env):
    ci, ws, p, repo, here = env
    r, mock = runner(ci, here, [W(), RUN(), {"text": "Done.\nOutcome: reached"}])
    r.run(start(r).id)
    first = _texts(mock)[0]
    assert "Ask the person one short question, and stop" in first and "Ask before changing anything" in first
    assert "never offer a partial change as the whole" in first and "Outcome: not reached: <what is missing>" in first
    assert "smallest scope" not in first


def test_the_outcome_line_is_read_strictly_and_the_last_one_counts():
    assert G.outcome("Intended outcome: the preview lists newest first.") == (None, "")
    assert G.outcome("Outcome: the preview lists newest first.") == (None, "")
    assert G.outcome("**Outcome:** not reached — the preview lives elsewhere") == ("not reached", "the preview lives elsewhere")
    assert G.outcome("`Outcome: reached`") == ("reached", "")
    assert G.outcome("Outcome: not reached: x\n...\nOutcome: reached") == ("reached", "")
    assert G.outcome("OUTCOME: Not Reached: needs the key names") == ("not reached", "needs the key names")
    assert G.outcome(None) == (None, "")
