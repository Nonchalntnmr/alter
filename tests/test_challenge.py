"""`ci challenge`: adversarial reasoning under the same controls as every request, tracked to an
outcome. No network: fake adapters and the stand-in `claude` binary from test_amplify."""

import json

import pytest
from test_amplify import LIGHT, Recorder, env, make  # noqa: F401  (env is a fixture)

from pcim import challenge as ch
from pcim import cli, modes, terminal
from pcim.adapters.base import ModelResponse, Usage
from pcim.assist import DIRECTIVES
from pcim.engine import RoutingError
from pcim.evaluation import scorecard
from pcim.types import utcnow

CLAIM = "We should rewrite the backend in Rust because it will fix our latency problems."


def reply(p=0.3, experiment="Profile one slow endpoint and see whether CPU time in Python dominates its latency."):
    return json.dumps({
        "claim": "A Rust rewrite will fix the latency problems.",
        "assumptions": [{"text": "Latency is CPU-bound in application code", "load_bearing": "high", "evidence_given": "none"},
                        {"text": "The team can ship a rewrite without a long freeze", "load_bearing": "medium",
                         "evidence_given": "none"}],
        "counterexamples": ["If p99 is dominated by database round trips, a faster language changes little."],
        "strongest_objection": "No profile shows where the time goes.",
        "uncertainty": {"p_claim_holds": p, "drivers": ["where latency is spent"]},
        "falsification": {"experiment": experiment, "if_true": "most time is in Python CPU",
                          "if_false": "most time is in I/O waits", "cost": "half a day"},
        "what_would_change_my_mind": "A profile showing CPU-bound handlers.",
        "verdict": "Unsupported until profiled."})


class Canned(Recorder):
    def __init__(self, provider, model, text):
        super().__init__(provider, model)
        self.text = text

    def generate(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        return ModelResponse(text=self.text, usage=Usage(10, 5), cost_usd=0.002, latency_ms=5.0,
                             stop_reason="end_turn", model=self.model, provider=self.provider)


def challenger(ci, text):
    c = Canned("claude-cli", "opus", text)
    ci.set_adapter("claude-cli", "opus", c)
    return c


def test_a_challenge_routes_deep_and_asks_for_assumptions_counterexamples_and_a_falsifying_test(env, tmp_path):
    ci, _ = make(tmp_path)
    c = challenger(ci, "```json\n" + reply() + "\n```")
    rec = ch.challenge(ci, "u", CLAIM)
    assert rec["served_by"] == "claude-cli:opus" and rec["receipt"]["route"]["plan"]["requirement"]["tier"] == "deep"
    assert any("challenge mode" in s for s in rec["receipt"]["route"]["plan"]["requirement"]["signals"])
    system = c.prompts[0].system
    assert DIRECTIVES["challenge"] in system and modes.CHALLENGE_FORMAT in system
    assert rec["parsed"] and rec["challenge"]["complete"] and rec["status"] == "open"
    assert rec["challenge"]["uncertainty"]["label"] == "the model's estimate, not a measurement"
    text = ch.render(rec)
    for heading in ("ASSUMPTIONS", "COUNTEREXAMPLES", "UNCERTAINTY", "CHEAPEST EXPERIMENT", "VERDICT"):
        assert heading in text
    assert "30% that the claim holds: the model's estimate" in text
    assert ch.records(ci, "u")[0]["id"] == rec["id"]


def test_parsing_is_strict_about_structure_and_honest_about_the_probability():
    assert ch.parse("I think it's a great idea!") is None
    d = json.loads(reply())
    d["falsification"]["experiment"] = ""
    assert ch.parse(json.dumps(d)) is None  # no experiment: not a challenge
    d = json.loads(reply(p=1.7))
    parsed = ch.parse("Sure. " + json.dumps(d) + " Hope that helps.")
    assert parsed["uncertainty"]["p_claim_holds"] is None and parsed["complete"] is False
    d = json.loads(reply())
    d["assumptions"] = d["assumptions"][:1]
    assert ch.parse(json.dumps(d))["complete"] is False  # one assumption: kept, flagged incomplete


def test_an_unstructured_reply_is_shown_but_not_tracked_as_an_experiment(env, tmp_path):
    ci, _ = make(tmp_path)
    challenger(ci, "Rust is fast, go for it.")
    rec = ch.challenge(ci, "u", CLAIM)
    assert rec["parsed"] is False and "did not have the challenge structure" in ch.render(rec)
    assert ch.metrics(ch.records(ci, "u"))["structured"] == 0


def test_challenge_mode_is_bound_by_the_same_controls_as_every_request(env, tmp_path):
    ci, _ = make(tmp_path)
    c = challenger(ci, reply())
    with pytest.raises(RoutingError):
        ch.challenge(ci, "u", "Our deploy key AKIAABCDEFGHIJKLMNOP is fine to keep in the repo, right?")
    assert not c.prompts
    for g in ci.ledger.grants("u"):
        ci.revoke("u", g.id)
    with pytest.raises(RoutingError):
        ch.challenge(ci, "u", CLAIM)
    assert not c.prompts and ch.records(ci, "u") == []


def test_outcomes_give_follow_through_and_the_challengers_calibration_only_with_enough_evidence(env, tmp_path):
    ci, _ = make(tmp_path)
    recs = []
    for i in range(12):
        challenger(ci, reply(p=0.8 if i % 2 else 0.2))
        recs.append(ch.challenge(ci, "u", f"{CLAIM} ({i})"))
    for i, r in enumerate(recs[:9]):
        ch.resolve(ci, "u", r["id"], "held" if i % 2 else "failed", note="profiled it")
    m = ch.metrics(ch.records(ci, "u"))
    assert (m["challenges"], m["experiments_resolved"], m["held"], m["failed"]) == (12, 9, 4, 5)
    assert m["follow_through_rate"] == 0.75 and m["challenger_brier"] is None  # 9 decided < 10
    ch.resolve(ci, "u", recs[9]["id"], "held")
    m = ch.metrics(ch.records(ci, "u"))
    assert m["challenger_brier_n"] == 10 and m["challenger_brier"] == pytest.approx(0.04)
    ch.resolve(ci, "u", recs[10]["id"], "inconclusive")
    assert ch.metrics(ch.records(ci, "u"))["challenger_brier_n"] == 10  # inconclusive is not scored
    with pytest.raises(ValueError):
        ch.resolve(ci, "u", recs[11]["id"], "probably")
    with pytest.raises(KeyError):
        ch.resolve(ci, "u", "ch_missing", "held")
    now = utcnow()
    import datetime as dt
    sc = scorecard(ci, "u", now - dt.timedelta(days=1), now + dt.timedelta(minutes=1))
    assert sc["decision"]["challenges"] == 12 and sc["decision"]["experiments_resolved"] == 11
    assert len(ci.export("u")["growth"]["challenges"]) == 12
    assert ch.forget(ci, "u") == 12 and ch.records(ci, "u") == []


def test_ci_challenge_in_the_terminal(env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "home"))
    cli.main(["init", "--user", "nishanth", "--providers", "claude-cli"])
    capsys.readouterr()
    assert terminal.main(["challenge", CLAIM]) == 0  # the stand-in CLI answers in prose
    out = capsys.readouterr().out
    assert "did not have the challenge structure" in out and "claude-cli:opus" in out
    assert terminal.main(["challenge", "--list"]) == 0 and "open" in capsys.readouterr().out
    cid = ch.records(*terminal._open())[0]["id"]
    assert terminal.main(["challenge", "--resolve", cid, "failed", "--note", "profile showed I/O"]) == 0
    assert "the claim failed" in capsys.readouterr().out
    assert terminal.main(["challenge", "--resolve", cid, "maybe"]) == 2
    assert terminal.main(["challenge", "my token ghp_" + "a" * 36 + " leaked, is that bad?"]) == 3
