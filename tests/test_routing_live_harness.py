"""experiments/routing/live/harness.py, exercised through --dry-run and without --live. No test makes a model call:
the harness's own fake adapters answer and grade, and its live factory is replaced by one that fails the test if
it is ever reached."""

import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("routing_live_harness", ROOT / "experiments/routing/live/harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

TASKS = [
    {"id": "q1", "tier": 1, "text": "What does HTTP status 429 mean?", "criteria": ["Says too many requests."],
     "check": {"type": "regex", "pattern": "too many requests", "flags": "i"}},
    {"id": "q2", "tier": 1, "text": "Convert 72 Fahrenheit to Celsius.", "criteria": ["About 22.2 C."],
     "reference": "22.2 °C", "check": {"type": "number", "value": 22.22, "tolerance": 0.05}},
    {"id": "q3", "tier": 2, "text": "Write a Python function that checks whether a string is a palindrome.",
     "criteria": ["A correct function."]},
    {"id": "q4", "tier": 2, "text": "Compare SQLite and PostgreSQL for a small internal tool with five users.",
     "criteria": ["Recommends one, with reasons."]},
    {"id": "q5", "tier": 3, "text": "Design the architecture for a multi-tenant SaaS with per-tenant encryption keys, "
     "and explain the trade-offs.", "criteria": ["Per-tenant key management."]},
    {"id": "q6", "tier": 3, "text": "Find the race condition in a counter incremented from two threads without a lock.",
     "criteria": ["Names the lost update."], "check": {"type": "elements", "required": ["lock|atomic"]}},
    {"id": "x1", "text": "deploys fail with key AKIAABCDEFGHIJKLMNOP, why?", "criteria": ["n/a"]},
    {"id": "x2", "text": "Is Claude Opus 5.5 better than the other one for this?", "criteria": ["n/a"]},
]
N_ROWS = 6 * 3 * 2  # included requests x arms x repetitions
LABELS = ("always_opus", "always_sonnet", "always_haiku", "claude-cli:", "claude-opus-5-5", "claude-sonnet-5-5",
          "claude-haiku-5-5", "Opus 5.5", "Sonnet 5.5", "Haiku 5.5", "Claude", "Anthropic", "routed")


@pytest.fixture
def ts(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "PCIM_OPENAI_MODEL", "GEMINI_API_KEY",
              "GOOGLE_API_KEY", "PCIM_GEMINI_MODEL", "PCIM_LOCAL_MODEL", "PCIM_LOCAL_BASE_URL", "PCIM_CAPABILITIES",
              "PCIM_PRICES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "must-stay-unused"))

    def no_live(*a, **k):
        raise AssertionError("a live adapter was constructed")
    monkeypatch.setattr(harness, "live_adapter", no_live)
    path = tmp_path / "test_set.json"
    path.write_text(json.dumps({"format": harness.FORMAT, "name": "unit", "tasks": TASKS}))
    return path


class Counting:
    """Wraps the harness's fake factories: counts generate calls and notes whether the manifest existed by then."""

    def __init__(self, monkeypatch, out: Path, answer_text=None):
        self.answers, self.grades, self.manifest_before_first_call, self.grader_prompts = 0, 0, None, []
        real_answerer, real_grader = harness.make_fake_answerer, harness.make_fake_grader
        outer = self

        def answerer(model):
            ad = real_answerer(model)
            gen = ad.generate

            def generate(prompt, max_tokens=None):
                if outer.manifest_before_first_call is None:
                    outer.manifest_before_first_call = (out / "manifest.json").exists()
                outer.answers += 1
                r = gen(prompt)
                return dataclasses.replace(r, text=answer_text) if answer_text else r
            ad.generate = generate
            return ad

        def grader(provider, model):
            g = real_grader(provider, model)
            gen = g.generate

            def generate(prompt, max_tokens=None):
                outer.grades += 1
                outer.grader_prompts.append(prompt)
                return gen(prompt)
            g.generate = generate
            return g
        monkeypatch.setattr(harness, "make_fake_answerer", answerer)
        monkeypatch.setattr(harness, "make_fake_grader", grader)


def run(ts, out, *extra):
    return harness.main([str(ts), "--out", str(out), "--workers", "2", *extra])


def test_without_live_it_prints_the_estimate_and_refuses_before_any_call(ts, tmp_path, monkeypatch, capsys):
    def never(*a, **k):
        raise AssertionError("an adapter was constructed")
    monkeypatch.setattr(harness, "make_fake_answerer", never)
    monkeypatch.setattr(harness, "make_fake_grader", never)
    before = dict(os.environ)
    assert run(ts, tmp_path / "out") == 2
    text = capsys.readouterr().out
    assert "Estimated cost before any call" in text and "routed" in text and "always_opus" in text
    assert "refused: no model was called" in text
    assert not (tmp_path / "out").exists()  # nothing written, nothing recorded as a run
    assert dict(os.environ) == before  # the scratch $PCIM_HOME and the stub CLI were scoped to the run
    assert "AKIA" not in text and "Claude Opus" not in text  # excluded requests are counted, never printed


def test_dry_run_end_to_end_records_the_test_set_hash_before_the_first_call(ts, tmp_path, monkeypatch):
    out = tmp_path / "out"
    c = Counting(monkeypatch, out)
    assert run(ts, out, "--dry-run") == 0
    m = json.loads((out / "manifest.json").read_text())
    assert m["test_set_sha256"] == hashlib.sha256(ts.read_bytes()).hexdigest()
    assert c.manifest_before_first_call is True
    assert m["mode"] == "dry-run" and m["confirmatory"] is False and "not a live run" in m["not_confirmatory_because"]
    assert m["arms"] == ["routed", "always_sonnet", "always_opus"]  # haiku is off unless asked for
    assert {e["id"]: e["reason"].split(" (")[0] for e in m["excluded"]} == {
        "x1": "credential-shaped text", "x2": "names an AI model or vendor"}
    assert c.answers == N_ROWS and c.grades == N_ROWS + 2  # + the two-call grader check
    res = json.loads((out / "results.json").read_text())
    a = res["analysis"]
    assert {r["arm"] for r in res["rows"]} == set(m["arms"]) and len(res["rows"]) == N_ROWS
    assert a["arms"]["routed"]["served_by"] == {"sonnet": 8, "opus": 4}  # the standard floor: never haiku
    assert a["integrity"]["context_identical"] == a["integrity"]["context_pairs"] == 12
    assert a["arms"]["always_opus"]["verified_n"] == 6  # q1, q2, q6 carry checks; 3 tasks x 2 repetitions
    cost = {arm: s["cost_usd"] for arm, s in a["arms"].items()}
    assert cost["always_opus"] > cost["routed"] > cost["always_sonnet"]
    assert {"kill_rule", "R1_quality_vs_opus", "R2_saving_vs_opus", "routing_claim"} <= set(a["decision"])
    report = (out / "report.md").read_text()
    assert report.startswith("# Routing live evaluation") and "DRY RUN" in report and "## Decision" in report


def test_blinding_hides_arm_identity_from_every_grader_input(ts, tmp_path, monkeypatch):
    out = tmp_path / "out"
    c = Counting(monkeypatch, out)  # the fake answerers name themselves: "As Claude Opus 5.5 (claude-opus-5-5)"
    assert run(ts, out, "--dry-run") == 0
    assert len(c.grader_prompts) == N_ROWS + 2
    for p in c.grader_prompts:
        text = p.system + "\n" + p.user
        assert not [label for label in LABELS if label in text], text[-400:]
        assert harness.blinding_violations(p) == []
    assert sum(harness.WITHHELD in p.user.rsplit("<answer nonce=", 1)[-1] for p in c.grader_prompts) == N_ROWS
    blinded = [json.loads(line) for line in (out / "blinded.jsonl").read_text().splitlines()]
    assert all(set(b) == {"blind_id", "task", "answer", "grader_input_sha256"} for b in blinded)
    key = json.loads((out / "blinding_key.json").read_text())
    arms = [key[b["blind_id"]]["arm"] for b in blinded]  # grading order
    assert sum(x != y for x, y in zip(arms, arms[1:])) > 10  # interleaved, not arm by arm
    # the guard refuses what blinding should have removed
    assert harness.blinding_violations(harness.RenderedPrompt("s", "always_opus wrote this")) == ["arm label"]
    assert harness.blinding_violations(harness.RenderedPrompt("s", "I am Claude Sonnet 5.5")) == [
        "model or vendor name"]


def test_identical_answers_reach_the_grader_as_identical_input_whatever_the_arm(ts, tmp_path, monkeypatch):
    out = tmp_path / "out"
    c = Counting(monkeypatch, out, answer_text="[[complete]] the same answer")
    assert run(ts, out, "--dry-run") == 0
    by_request: dict = {}
    for p in c.grader_prompts[2:]:  # after the grader check
        by_request.setdefault(p.user.split("\n")[1], set()).add((p.system, p.user))
    assert len(by_request) == 6 and all(len(v) == 1 for v in by_request.values())  # 3 arms x 2 reps, one input


@pytest.mark.parametrize("grader", ["claude-cli:opus", "claude-cli:sonnet", "anthropic:claude-sonnet-5-5"])
def test_the_grader_is_never_a_model_an_arm_uses(ts, tmp_path, grader, capsys):
    assert run(ts, tmp_path / "out", "--dry-run", "--grader", grader) == 2
    assert "is a model an arm uses" in capsys.readouterr().out and not (tmp_path / "out").exists()


def test_a_test_set_that_is_not_the_sealed_one_is_refused_before_anything(ts, tmp_path, capsys):
    assert run(ts, tmp_path / "out", "--dry-run", "--expect-sha256", "0" * 64) == 2
    assert "sha256 is not the expected" in capsys.readouterr().out and not (tmp_path / "out").exists()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": harness.FORMAT, "tasks": [{"id": "a", "text": "t", "criteria": ["c"],
                                                                     "check": {"type": "regex", "pattern": "("}}]}))
    assert harness.main([str(bad), "--out", str(tmp_path / "o2"), "--dry-run"]) == 2


def test_a_pilot_withholds_quality_and_a_resumed_run_never_rebills_finished_items(ts, tmp_path, monkeypatch):
    out = tmp_path / "out"
    c = Counting(monkeypatch, out)
    assert run(ts, out, "--dry-run", "--limit", "2") == 0
    pilot = (out / "report.md").read_text()
    assert "Quality results are withheld" in pilot and "## Decision" not in pilot and "success" not in pilot
    assert c.answers == 2 * 3 * 2
    first = c.answers
    assert run(ts, out, "--dry-run") == 0  # the full run grows from the pilot
    assert c.answers - first == N_ROWS - 12
    full = json.loads((out / "results.json").read_text())["analysis"]["arms"]

    # a crash mid-run: answers after the 20th and half a line lost, grades after the 10th lost
    lines = (out / "answers.jsonl").read_text().splitlines()
    (out / "answers.jsonl").write_text("\n".join(lines[:20]) + '\n{"task": "q1", "ar')
    kept = (out / "grades.jsonl").read_text().splitlines()[:10]
    (out / "grades.jsonl").write_text("\n".join(kept) + "\n")
    answers, grades = c.answers, c.grades
    assert run(ts, out, "--dry-run") == 0
    assert c.answers - answers == N_ROWS - 20
    kept_inputs = {json.loads(line)["grader_input_sha256"] for line in kept}
    blinded = [json.loads(line) for line in (out / "blinded.jsonl").read_text().splitlines()]
    assert c.grades - grades == sum(b["grader_input_sha256"] not in kept_inputs for b in blinded)  # check cached
    again = json.loads((out / "results.json").read_text())["analysis"]["arms"]
    assert {a: s["successes"] for a, s in again.items()} == {a: s["successes"] for a, s in full.items()}


def test_objective_checks_match_text_and_never_execute_it(tmp_path):
    num = {"type": "number", "value": 22.22, "tolerance": 0.05}
    assert harness.run_check(num, "It is about 22.2 °C.") and not harness.run_check(num, "About 23 °C.")
    assert harness.run_check({"type": "number", "value": 1234.5}, "1,234.5 units")
    exact = {"type": "exact", "value": "Lisbon"}
    assert harness.run_check(exact, "**Lisbon**.") and not harness.run_check(exact, "Lisbon, Portugal")
    el = {"type": "elements", "required": ["lock|atomic"], "forbidden": [r"time\.sleep"]}
    assert harness.run_check(el, "Use a Lock.") and not harness.run_check(el, "Add a lock and time.sleep(1).")
    marker = tmp_path / "executed"
    code = f"```python\nimport pathlib; pathlib.Path({str(marker)!r}).write_text('x')\n```"
    harness.run_check({"type": "regex", "pattern": "pathlib"}, code)
    harness.run_check(el, code)
    assert not marker.exists()
    assert harness.check_problems({"type": "regex", "pattern": "("}) and harness.check_problems({"type": "eval"})


def test_redaction_removes_self_names_and_keeps_poems():
    text, n = harness.redact("I'm Claude Opus 5.5 (claude-sonnet-5-5), made by Anthropic. "
                             "Here is a haiku and a sonnet.")
    assert n == 3 and "Claude" not in text and "Anthropic" not in text and "a haiku and a sonnet." in text


def test_the_registered_decision_rules():
    sel = sep = {"pairs": 100, "diff_ci95": [-0.01, 0.02]}
    d = harness.decide({"pairs": 100, "diff": -0.05, "diff_ci95": [-0.10, -0.01]},
                       {"ratio": 0.7, "saving": 0.3, "saving_ci95": [0.25, 0.35]}, sel, sep, {})
    assert d["kill_rule"]["fires"] and d["kill_rule"]["decision"] == "kill"
    assert (d["R1_quality_vs_opus"], d["R2_saving_vs_opus"], d["routing_claim"]) == ("inconclusive", "falsified",
                                                                                     "falsified")
    assert d["R3_selection_vs_sonnet"] == "falsified"  # any gain over always-Sonnet is under 3 pp
    d = harness.decide({"pairs": 100, "diff": -0.05, "diff_ci95": [-0.09, -0.01]},
                       {"ratio": 0.55, "saving": 0.45, "saving_ci95": [0.41, 0.49]}, sel, sep, {})
    assert d["kill_rule"]["decision"] == "trade-off" and not d["kill_rule"]["fires"]
    ok = {"pairs": 100, "diff": -0.005, "diff_ci95": [-0.02, 0.01]}
    good_cost = {"ratio": 0.5, "saving": 0.5, "saving_ci95": [0.45, 0.55]}
    d = harness.decide(ok, good_cost, sel, {"pairs": 100, "diff_ci95": [0.02, 0.09]}, {})
    assert d["kill_rule"]["decision"] == "keep" and d["routing_claim"] == "supported" and "note" not in d
    d = harness.decide(ok, good_cost, sel, sep, {"pairs": 30, "diff": -0.1})
    assert d["R1_quality_vs_opus"].startswith("inconclusive: the objective checks")
    assert harness.decide({"pairs": 100, "diff": -0.06, "diff_ci95": [-0.09, -0.04]}, good_cost, sel, sep,
                          {})["R1_quality_vs_opus"] == "falsified"


def test_the_statistics_are_the_textbook_ones():
    assert harness.mcnemar_exact(0, 0) == 1.0 and harness.mcnemar_exact(6, 0) == pytest.approx(2 / 2 ** 6)
    lo, hi = harness.wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    h = harness.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert h["a"]["p_holm"] == pytest.approx(0.03) and h["c"]["p_holm"] == pytest.approx(0.06)
    assert h["b"]["p_holm"] == pytest.approx(0.06) and h["a"]["significant"] and not h["b"]["significant"]
