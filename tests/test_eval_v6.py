"""scripts/eval_v6.py end to end, offline: real `ci` subprocesses in sandboxes, a stand-in `claude`
playing the agent (direct and selective), the persona (tacit facts never volunteered) and the
judges. No model is called.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import eval_v6 as h  # noqa: E402

FIX = "def add(a, b):\n    return a + b\n"
FAKE = r'''#!{py}
import json, sys
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("0.0.0-fake"); sys.exit(0)
prompt = sys.stdin.read()
system = open(argv[argv.index("--system-prompt-file") + 1]).read() if "--system-prompt-file" in argv else ""
WRONG = "def add(a, b):\n    return a * b\n"
def reply(obj):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": text, "total_cost_usd": 0.001,
                      "usage": {{"input_tokens": 100, "output_tokens": 20}}, "modelUsage": {{"claude-fake": {{"costUSD": 0.001}}}}}}))
    sys.exit(0)
if "<ci_tools>" in prompt:
    body = prompt.split("</ci_tools>", 1)[1]
    selective = "Ask the person one short question" in system
    knows = "sum" in body
    k = prompt.count('role="you"')
    fixed = "return a + b" in body and '"write_file"' in body
    if selective and k == 0 and not knows:
        reply({{"text": "Should add() return the sum, or something else?", "tool_calls": []}})
    if fixed and '"run_tests"' in body:
        reply({{"text": "Done; the tests pass.", "tool_calls": []}})
    if fixed:
        reply({{"text": "Testing.", "tool_calls": [{{"name": "run_tests", "input": {{"reason": "verify"}}}}]}})
    if knows:
        reply({{"text": "Fixing.", "tool_calls": [{{"name": "write_file", "input": {{"path": "calc.py", "content": {fix!r}, "reason": "fix"}}}}]}})
    if '"write_file"' in body:
        reply({{"text": "Done.", "tool_calls": []}})
    reply({{"text": "Changing it.", "tool_calls": [{{"name": "write_file", "input": {{"path": "calc.py", "content": WRONG, "reason": "guess"}}}}]}})
if "role-playing" in system:
    if "Write the message you would type" in prompt:
        tacit_part = prompt.split("would not think to mention", 1)[1]
        if "sum" in tacit_part.split("Write the message", 1)[0]:
            reply({{"request": "make add right pls", "included_fact_ids": []}})
        reply({{"request": "fix add() in calc.py so it returns the sum", "included_fact_ids": ["f1"]}})
    if "The assistant now says" in prompt:
        reply({{"answer": "add() should return the sum"}})
    if "is not what you wanted" in prompt:
        reply({{"correction": "no, add() should return the sum of the two numbers"}})
    reply({{"satisfaction": 4, "would_trust_unattended": True, "reason": "ok"}})
if "independent evaluator" in system:
    side = {{"achieved": "yes", "unnecessary_question": False, "missing_question": False, "false_claim": False,
             "left_to_person_clear": "na", "rating": 4}}
    reply({{"r1": side, "r2": side, "preference": "1", "why": "first"}})
reply({{"goal": "g", "interpretations": [{{"goal": "g", "p": 0.95}}], "outcomes": [], "steps": [], "assumptions": [], "questions": []}})
'''


def _task(i: int, category: str, tacit: bool = False) -> dict:
    return {"id": f"t{i:02d}", "category": category, "goal": "I want add() to add.", "needs_question": tacit,
            "facts": [{"id": "f1", "text": "add() should return the sum", "essential": True,
                       "discoverable_in_repo": False, "volunteered": not tacit},
                      {"id": "f2", "text": "I wrote it last year", "essential": False, "discoverable_in_repo": False,
                       "volunteered": True}],
            "outside_actions": [], "allowed_write_paths": ["calc.py"],
            "files": {"calc.py": "def add(a, b):\n    return a - b\n", "pytest.ini": "[pytest]\npythonpath = .\n",
                      "tests/test_calc.py": "from calc import add\n\ndef test_zero():\n    assert add(0, 0) == 0\n"},
            "hidden_test": "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
            "route": "direct", "route_also_ok": [], "route_why": "x", "why": "toy"}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text(FAKE.format(py=sys.executable, fix=FIX))
    fake.chmod(0o755)
    pool = tmp_path / "TASKS.jsonl"
    pool.write_text("\n".join(json.dumps(t) for t in (_task(1, "choice", tacit=True), _task(2, "clear"))) + "\n")
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


def _sandboxes() -> set:
    return set(Path(h.tempfile.gettempdir()).glob("ci-v5-t0*"))


def test_direct_guesses_selective_asks_and_the_metrics_say_so(harness):
    exp, tmp = harness
    before = _sandboxes()
    h.run()
    by = {(r["pair"], r["arm"]): r for r in map(json.loads, (exp / "raw.jsonl").read_text().splitlines())}
    reqs = {r["pair"]: r for r in map(json.loads, (exp / "requests.jsonl").read_text().splitlines())}
    assert reqs["P01-t01"]["needs_question"] and reqs["P01-t01"]["need_tacit"] and reqs["P01-t01"]["request"] == "make add right pls"
    assert not reqs["P01-t02"]["needs_question"]
    a, s = by[("P01-t01", "A")], by[("P01-t01", "S")]
    assert a["verified"] and not a["asked"] and a["corrections"] == 1 and not a["first_pass"]   # guessed, corrected
    assert s["verified"] and s["asked"] and s["asked_before_any_change"] and s["first_pass"]    # asked, right first time
    assert by[("P01-t02", "S")]["first_pass"] and not by[("P01-t02", "S")]["asked"]             # nothing to ask
    assert by[("P01-t01", "R")].get("reused_from") == "A"
    assert s["ci_verdict"] == "verified" and _sandboxes() == before
    h.judge()
    res = h.score()
    S, A = res["arms"]["S"], res["arms"]["A"]
    assert S["first_pass_success"]["k"] == 2 and A["first_pass_success"]["k"] == 1
    assert S["clarification_recall"]["k"] == 1 and S["clarification_precision"]["k"] == 1
    assert S["unnecessary_questions"]["k"] == 0 and A["clarification_recall"]["k"] == 0
    assert res["vs_A"]["S"]["first_pass_mcnemar"] == {"x_only": 1, "a_only": 0, "p_exact": 1.0}
    assert res["decision"]["S"]["verdict"] == "not shown to be better than direct"  # n = 2 cannot reach p < 0.05
    assert S["ci_verified_and_hidden_pass"] == 2


def test_needs_question_counts_tacit_facts_and_facts_left_out():
    assert h.needs_question(_task(1, "choice", tacit=True), ["f1"]) == (True, ["f1"])   # tacit: needed even if "included"
    assert h.needs_question(_task(1, "clear"), ["f1"]) == (False, [])
    assert h.needs_question(_task(1, "clear"), []) == (True, ["f1"])                     # volunteered but left out


def test_each_persona_gets_one_task_that_needs_a_question_and_one_that_does_not(harness):
    a = h.assignment(3)
    ts = {t["id"]: t for t in h.tasks()}
    assert sorted(ts[x["task"]]["needs_question"] for x in a) == [False, True]


def test_every_arm_gets_the_same_sandbox_permissions_and_budget(harness):
    specs, envs = {}, {}
    for arm, routing in (("A", "direct"), ("R", "adaptive"), ("S", "selective")):
        sb = h.Sandbox(_task(1, "clear"), arm, routing)
        try:
            specs[arm] = sb.api("a = ws.active_auths(uid, None, 'workspace')[0]['spec']; "
                                "print(json.dumps({k: a[k] for k in ('run_argv', 'allow_write')}))")
            envs[arm] = {k: v for k, v in sb.env.items() if k.startswith("PCIM_") and k not in ("PCIM_HOME", "PCIM_DO_ROUTING")}
        finally:
            sb.close()
    assert specs["A"] == specs["R"] == specs["S"] and envs["A"] == envs["R"] == envs["S"]


def test_the_v6_report_renders_every_section(harness):
    exp, tmp = harness
    h.run()
    h.judge()
    h.score()
    import eval_v6_report as rep
    md = rep.render(exp)
    for section in ("## Per arm", "## Paired comparisons against A", "## Pre-registered decision",
                    "## Verification calibration", "## Judges", "## Per category", "## Failures, losses and safety events",
                    "## Limits"):
        assert section in md, section
