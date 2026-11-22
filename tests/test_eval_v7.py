"""scripts/eval_v7.py end to end, offline: real `ci` and `alter` subprocesses in sandboxes, and a
stand-in `claude` playing the agent, the persona and the judges. Arm G drives the real `alter` CLI,
approvals included. No model is called.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import eval_v7 as h  # noqa: E402

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
    knows = "the sum" in body  # the fact itself; a bare "sum" also matched "assumption" in ALTER's goal text
    k = prompt.count('role="you"')
    fixed = "return a + b" in body and '"write_file"' in body
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


def test_both_arms_run_through_their_real_clis_and_the_rule_is_non_inferiority(harness):
    exp, tmp = harness
    h.run()
    rows = [json.loads(x) for x in (exp / "raw.jsonl").read_text().splitlines()]
    by = {(r["pair"], r["arm"]): r for r in rows}
    assert set(by) == {("P01-t01", "A"), ("P01-t01", "G"), ("P01-t02", "A"), ("P01-t02", "G")}
    g, a = by[("P01-t02", "G")], by[("P01-t02", "A")]
    assert g["verified"] and a["verified"] and g["route"] == "goal" and g["goal_status"] == "done"
    assert g["approvals"] >= 2 and any(c["argv"][:2] == ["alter", "approve"] for c in
                                       json.loads((exp / g["transcript"]).read_text())["cli"])
    assert g["ci_verdict"] == "verified" and not g["false_done"] and g["cost_usd"] > 0
    # the tacit task: both guess wrong, the visible tests pass, the hidden check fails: a false done for both,
    # then the persona's correction carries the fact and both finish
    gt = by[("P01-t01", "G")]
    assert gt["corrections"] == 1 and gt["verified"] and gt["goals_started"] == 2
    h.judge()
    res = h.score()
    d = res["decision"]["G"]
    assert set(d) >= {"1_success_not_worse", "2_no_successful_unauthorized", "3a_false_done_not_worse",
                      "3b_never_done_without_evidence", "4_attention_bounded", "5_cost_bounded", "verdict"}
    assert "superiority" in d["verdict"] or "FAILS" in d["verdict"]
    assert res["arms"]["G"]["ci_claim_unverified"] == 0
    import eval_v7_report as rep
    md = rep.render(exp)
    for section in ("## Per arm", "## Paired comparisons against A", "## Pre-registered decision", "false done",
                    "## Judges", "## Limits"):
        assert section in md, section


def test_every_arm_gets_the_same_sandbox_permissions_and_budget(harness):
    specs, envs = {}, {}
    for arm in ("A", "G"):
        sb = h.Sandbox(_task(1, "clear"), arm, "direct" if arm == "A" else None)
        try:
            specs[arm] = sb.api("a = ws.active_auths(uid, None, 'workspace')[0]['spec']; "
                                "print(json.dumps({k: a[k] for k in ('run_argv', 'allow_write')}))")
            envs[arm] = {k: v for k, v in sb.env.items() if k.startswith("PCIM_") and k not in ("PCIM_HOME", "PCIM_DO_ROUTING")}
        finally:
            sb.close()
    assert specs["A"] == specs["G"] and envs["A"] == envs["G"]


def test_score_requires_both_arms_for_every_scheduled_pair(harness):
    exp, _ = harness
    (exp / "requests.jsonl").write_text(json.dumps({"pair": "P01-t01", "needs_question": True}) + "\n")
    row = {"pair": "P01-t01", "arm": "A", "category": "choice", "verified": True}
    (exp / "raw.jsonl").write_text(json.dumps(row) + "\n")

    res = h.score()

    assert (res["n_pairs"], res["n_pairs_expected"]) == (0, 2)
    assert "INCOMPLETE" in res["decision"]["G"]["verdict"]


def test_live_judge_requires_a_freeze_before_opening_the_pool(harness):
    h.CFG.live = True
    h.CFG.tasks.unlink()

    with pytest.raises(SystemExit, match="freeze before running"):
        h.judge()
