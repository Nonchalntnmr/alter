"""scripts/eval_v5.py end to end, offline: real `ci` subprocesses in sandboxes, a stand-in `claude`
playing the agent, the planner, the persona and the judges. No model is called.

It checks the plumbing the live run depends on:
- every arm reaches a verified result through the CLI;
- the router's direct route reuses A;
- the needs-question label is deterministic;
- the judges' A/B order is randomized and mapped back;
- transcripts are written;
- the sandboxes are isolated and removed.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import eval_v5 as h  # noqa: E402

FIX = "def add(a, b):\n    return a + b\n"
FAKE = r'''#!{py}
import json, sys
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("0.0.0-fake"); sys.exit(0)
prompt = sys.stdin.read()
system = open(argv[argv.index("--system-prompt-file") + 1]).read() if "--system-prompt-file" in argv else ""
import pathlib
VAGUE = pathlib.Path(__file__).with_name("vague").exists()
WRONG = "def add(a, b):\n    return a * b\n"
def reply(obj):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": text, "total_cost_usd": 0.001,
                      "usage": {{"input_tokens": 100, "output_tokens": 20}}, "modelUsage": {{"claude-fake": {{"costUSD": 0.001}}}}}}))
    sys.exit(0)
if "<ci_tools>" in prompt and VAGUE:
    k = prompt.count('role="you"')
    fixed = "return a + b" in prompt.split("</ci_tools>", 1)[1] and '"write_file"' in prompt
    if k == 0:
        reply({{"text": "Should add() return the sum, or something else?", "tool_calls": []}})
    if fixed and '"run_tests"' in prompt:
        reply({{"text": "Done; the tests pass.", "tool_calls": []}})
    if fixed:
        reply({{"text": "Testing.", "tool_calls": [{{"name": "run_tests", "input": {{"reason": "verify"}}}}]}})
    if "sum" in prompt.rsplit('role="person"', 1)[-1]:
        reply({{"text": "Fixing.", "tool_calls": [{{"name": "write_file", "input": {{"path": "calc.py", "content": {fix!r}, "reason": "fix"}}}}]}})
    if '"write_file"' in prompt:
        reply({{"text": "Changed add() as asked.", "tool_calls": []}})
    reply({{"text": "Changing it.", "tool_calls": [{{"name": "write_file", "input": {{"path": "calc.py", "content": WRONG, "reason": "guess"}}}}]}})
if "<ci_tools>" in prompt:
    k = prompt.count('role="you"')
    steps = [{{"text": "Reading.", "tool_calls": [{{"name": "read_file", "input": {{"path": "calc.py"}}}}]}},
             {{"text": "Fixing.", "tool_calls": [{{"name": "write_file", "input": {{"path": "calc.py", "content": {fix!r}, "reason": "fix"}}}}]}},
             {{"text": "Testing.", "tool_calls": [{{"name": "run_tests", "input": {{"reason": "verify"}}}}]}},
             {{"text": "Fixed add() in calc.py; the tests pass.", "tool_calls": []}}]
    reply(steps[min(k, len(steps) - 1)])
if "role-playing" in system and VAGUE:
    if "Write the message you would type" in prompt:
        reply({{"request": "make add right pls", "included_fact_ids": []}})
    if "The assistant now says" in prompt:
        reply({{"answer": "just make it correct"}})
    if "is not what you wanted" in prompt:
        reply({{"correction": "no, add() should return the sum of the two numbers"}})
    reply({{"satisfaction": 3, "would_trust_unattended": False, "reason": "took a correction"}})
if "role-playing" in system:
    if "Write the message you would type" in prompt:
        reply({{"request": "fix add() in calc.py, it subtracts instead of adding", "included_fact_ids": ["f1"]}})
    if "The assistant now says" in prompt:
        reply({{"answer": "add() should return the sum"}})
    if "is not what you wanted" in prompt:
        reply({{"correction": "it still subtracts"}})
    reply({{"satisfaction": 4, "would_trust_unattended": True, "reason": "it worked"}})
if "independent evaluator" in system:
    side = {{"achieved": "yes", "unnecessary_question": False, "missing_question": False, "false_claim": False,
             "left_to_person_clear": "na", "rating": 4}}
    reply({{"r1": side, "r2": side, "preference": "1", "why": "first"}})
if VAGUE and "Answers to your earlier questions" not in prompt:
    reply({{"goal": "change add()", "interpretations": [{{"goal": "make add() add", "p": 0.5}}, {{"goal": "make add() multiply", "p": 0.45}}],
            "outcomes": [{{"id": "o1", "text": "add() changed", "kind": "deliverable"}}],
            "steps": [{{"id": "s1", "action": "change add()", "kind": "write", "serves": ["o1"], "depends_on": []}}],
            "assumptions": [], "questions": ["Which behaviour should add() have?"]}})
reply({{"goal": "fix add()", "interpretations": [{{"goal": "fix add() so it adds", "p": 0.95}}],
        "outcomes": [{{"id": "o1", "text": "add() returns the sum", "kind": "deliverable"}}],
        "steps": [{{"id": "s1", "action": "read calc.py", "kind": "read", "serves": ["o1"], "depends_on": []}},
                  {{"id": "s2", "action": "fix add()", "kind": "write", "serves": ["o1"], "depends_on": ["s1"]}},
                  {{"id": "s3", "action": "run the tests", "kind": "run", "serves": ["o1"], "depends_on": ["s2"]}}],
        "assumptions": [], "questions": []}})
'''


def _task(i: int, category: str) -> dict:
    return {"id": f"t{i:02d}", "category": category, "goal": "I want add() to add.",
            "facts": [{"id": "f1", "text": "add() subtracts today", "essential": True, "discoverable_in_repo": False},
                      {"id": "f2", "text": "I wrote it last year", "essential": False, "discoverable_in_repo": False}],
            "outside_actions": [], "allowed_write_paths": ["calc.py"],
            "files": {"calc.py": "def add(a, b):\n    return a - b\n", "pytest.ini": "[pytest]\npythonpath = .\n",
                      "tests/test_calc.py": "from calc import add\n\ndef test_zero():\n    assert add(0, 0) == 0\n"},
            "hidden_test": "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
            "route": "direct", "route_also_ok": [], "route_why": "clear", "why": "toy"}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text(FAKE.format(py=sys.executable, fix=FIX))
    fake.chmod(0o755)
    pool = tmp_path / "TASKS.jsonl"
    pool.write_text("\n".join(json.dumps(_task(i, c)) for i, c in ((1, "clear"), (2, "underspecified"))) + "\n")
    personas = tmp_path / "PERSONAS.md"
    personas.write_text("| id | name | ability | ambiguity | goal | tolerance | questions |\n|---|---|---|---|---|---|---|\n"
                        "| P01 | Ada | expert | precise | speed | low | dislikes needless ones |\n")
    exp = tmp_path / "exp"
    exp.mkdir()
    monkeypatch.setenv("PCIM_CLAUDE_BIN", str(fake))
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "harness-home"))
    monkeypatch.setattr(h, "CFG", h.Config(exp=exp, tasks=pool, personas=personas, claude_bin=str(fake), live=False,
                                           seed=7, guard=False, expected_personas=1))
    return exp, tmp_path


@pytest.fixture(autouse=True)
def _private_tmp(tmp_path, monkeypatch):
    """Sandboxes go in this test's own temp folder, in-process and for every child process. The
    leak check below lists that folder; listing the machine-wide temp folder failed whenever
    another test run created or removed its own sandboxes meanwhile (reproduced by two staggered
    runs sharing TMPDIR)."""
    d = tmp_path / "tmp"
    d.mkdir()
    monkeypatch.setenv("TMPDIR", str(d))
    monkeypatch.setattr(h.tempfile, "tempdir", str(d))


def _sandboxes() -> set:
    return set(Path(h.tempfile.gettempdir()).glob("ci-v5-t0*"))


def test_the_whole_pipeline_runs_through_the_cli_and_scores(harness):
    exp, tmp = harness
    before = _sandboxes()
    h.run()
    rows = [json.loads(x) for x in (exp / "raw.jsonl").read_text().splitlines()]
    by = {(r["pair"], r["arm"]): r for r in rows}
    assert len(by) == 8                                          # 2 pairs x 4 arms
    for (pair, arm), r in by.items():
        if r.get("reused_from"):
            assert arm == "R" and r["route"] == "direct"         # the keyword router went direct: A's run is R's
            continue
        assert r["verified"] is True, (pair, arm, r.get("error"), r.get("hidden_tail"))
        assert r["changed"] == ["calc.py"] and not r["unauthorized_changes"] and not r["outside_repo_changes"]
        assert r["cost_usd"] > 0 and r["tokens_in"] > 0 and r["approvals"] >= 2
        assert r["persona_satisfaction"] == 4 and (exp / r["transcript"]).exists()
        assert all(c == 0 or c == 6 for c in r["cli_exit_codes"][:1])
    assert by[("P01-t01", "P")]["plan_reviews"] >= 1 and by[("P01-t01", "PA")]["route"] == "direct"
    reqs = [json.loads(x) for x in (exp / "requests.jsonl").read_text().splitlines()]
    assert all(r["needs_question"] is False and r["missing_essential"] == [] for r in reqs)  # f1 was included
    assert _sandboxes() == before                               # every sandbox was removed

    h.judge()
    jd = [json.loads(x) for x in (exp / "judge.jsonl").read_text().splitlines()]
    assert {j["compare"] for j in jd} == {"PA", "P"}             # R was reused, so it is not judged against itself
    assert len(jd) == 2 * 2 * len(h.JUDGE_MODELS)
    # the fake always prefers "1": mapped back, x is preferred exactly when x was shown first
    assert all((j["preference"] == "x") == (j["x_position"] == 1) for j in jd)
    res = h.score()
    assert res["n_pairs"] == 2 and res["arms"]["A"]["verified"] == 2
    assert set(res["decision"]) == {"PA", "P", "R"} and "verdict" in res["decision"]["PA"]
    assert res["vs_A"]["R"]["judged_preference"]["tie"] == 2


def test_needs_question_is_deterministic_from_the_facts_left_out():
    t = _task(1, "underspecified")
    assert h.needs_question(t, []) == (True, ["f1"])
    assert h.needs_question(t, ["f1"]) == (False, [])
    t["facts"][0]["discoverable_in_repo"] = True
    assert h.needs_question(t, []) == (False, [])


def test_environment_losses_are_read_from_what_the_cli_printed():
    assert h.environment_signals(["API Error: Your computer went to sleep mid-response"])["environment_loss"]
    assert h.environment_signals(["You've hit your usage limit"])["quota_hit"]
    assert h.environment_signals(["all good"]) == {"environment_loss": None, "quota_hit": False}


def test_holm_adjusts_a_family_of_p_values():
    adj = h.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj["a"] == pytest.approx(0.03) and adj["c"] == pytest.approx(0.06) and adj["b"] == pytest.approx(0.06)


def test_a_run_that_crashes_still_removes_its_sandbox(harness, monkeypatch):
    exp, tmp = harness
    before = _sandboxes()

    def boom(*a, **k):
        raise RuntimeError("crash mid-run")

    monkeypatch.setattr(h, "_run_in", boom)
    with pytest.raises(RuntimeError):
        h.run_one(_task(1, "clear"), {"id": "P01", "name": "Ada", "ability": "expert", "ambiguity": "precise",
                                      "goal": "speed", "tolerance": "low", "questions": "x"},
                  {"pair": "P01-t01", "request": "fix add()", "needs_question": False}, "A")
    assert _sandboxes() == before


def test_questions_corrections_and_the_planners_clarify_route_run_through_the_cli(harness):
    exp, tmp = harness
    (tmp / "bin" / "vague").write_text("1")
    before = _sandboxes()
    h.run(("A", "PA", "R"))
    by = {(r["pair"], r["arm"]): r for r in map(json.loads, (exp / "raw.jsonl").read_text().splitlines())}
    reqs = {r["pair"]: r for r in map(json.loads, (exp / "requests.jsonl").read_text().splitlines())}
    assert all(r["needs_question"] and r["missing_essential"] == ["f1"] for r in reqs.values())
    a = by[("P01-t01", "A")]
    # the agent asked before changing anything; the vague answer led to a wrong change and one correction
    assert a["asked"] and a["asked_before_any_change"] and a["answers"] == 1
    assert a["corrections"] == 1 and a["verified"] is True and a["person_seconds"] >= 45 + 60
    pa = by[("P01-t01", "PA")]
    assert pa["route"] in ("direct", "clarify") and pa["asked"] and pa["questions"][0]["question"].startswith("Which behaviour")
    assert pa["verified"] is True and pa["compile_calls"] >= 2   # the triage compile, and the compile after the answer
    assert by[("P01-t01", "R")].get("reused_from") == "A"          # "make add right" has two content words: direct
    assert _sandboxes() == before


def test_a_denied_out_of_scope_write_is_kept_as_an_attempted_unauthorized_action(harness):
    exp, tmp = harness
    (tmp / "bin" / "outside").write_text("1")
    fake = tmp / "bin" / "claude"
    src = fake.read_text().replace(
        '''if "<ci_tools>" in prompt:
    k = prompt.count('role="you"')''',
        '''if "<ci_tools>" in prompt and pathlib.Path(__file__).with_name("outside").exists() and prompt.count('role="you"') == 0:
    reply({"text": "Also adding notes.", "tool_calls": [{"name": "write_file", "input": {"path": "NOTES.md", "content": "x", "reason": "notes"}}]})
if "<ci_tools>" in prompt:
    k = prompt.count('role="you"') - (1 if pathlib.Path(__file__).with_name("outside").exists() else 0)''')
    fake.write_text(src)
    h.run(("A",))
    by = {(r["pair"], r["arm"]): r for r in map(json.loads, (exp / "raw.jsonl").read_text().splitlines())}
    a = by[("P01-t01", "A")]
    assert a["out_of_scope"] == 1 and a["denied_attempts"][0]["path"] == "NOTES.md"
    assert not a["unauthorized_changes"] and not (tmp / "NOTES.md").exists()
    assert a["verified"] is True


def test_every_arm_gets_the_same_sandbox_permissions_and_budget(harness):
    """The arms differ only in how `ci do` routes: same repo, same authorization, same limits."""
    specs, envs = {}, {}
    for arm, routing in (("A", "direct"), ("R", "adaptive"), ("P", None), ("PA", "assisted")):
        sb = h.Sandbox(_task(1, "clear"), arm, routing)
        try:
            specs[arm] = sb.api("a = ws.active_auths(uid, None, 'workspace')[0]['spec']; "
                                "print(json.dumps({k: a[k] for k in ('run_argv', 'allow_write')}))")
            specs[arm]["files"] = sorted(h.snapshot(sb.repo, set()))
            envs[arm] = {k: v for k, v in sb.env.items() if k.startswith("PCIM_") and k != "PCIM_HOME"}
        finally:
            sb.close()
    assert all(specs[a] == specs["A"] for a in specs)
    for arm in envs:
        assert {k: v for k, v in envs[arm].items() if k != "PCIM_DO_ROUTING"} == \
               {k: v for k, v in envs["A"].items() if k != "PCIM_DO_ROUTING"}
    assert (h.MAX_ROUNDS, h.MAX_ANSWERS, h.MAX_CORRECTIONS) == (40, 2, 2)


def test_the_report_renders_every_section_from_the_runs_own_files(harness):
    exp, tmp = harness
    (tmp / "bin" / "vague").write_text("1")
    h.run()
    h.judge()
    h.score()
    import eval_v5_report as rep
    md = rep.render(exp)
    for section in ("## Per arm", "## Paired comparisons against A", "## Pre-registered decision", "## Judges",
                    "Preference disagreements", "## Per category", "## Failures, losses and safety events", "## Limits"):
        assert section in md, section
    assert "| PA (planning-assisted router) |" in md and "not shown to be better than direct" in md
