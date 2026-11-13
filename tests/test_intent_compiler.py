"""`ci plan`: the intent compiler. The gate and the provenance check are code, so they are tested
without a model; the end-to-end path uses canned replies through the real routing and engine."""

import json

import pytest
from test_amplify import Recorder, env, make  # noqa: F401  (env is a fixture)

from pcim import intent_compiler as ic
from pcim import modes, terminal
from pcim.adapters.base import ModelResponse, Usage
from pcim.assist import DIRECTIVES


def spec(**kw):
    base = {"goal": "Summarise last week's backtest results", "interpretations": [{"goal": "summary", "p": 0.9}],
            "deliverables": ["a one-page summary"], "constraints": [], "success_criteria": ["covers every run"],
            "dependencies": [], "steps": [{"id": "s1", "action": "read the run logs", "kind": "read", "depends_on": []},
                                          {"id": "s2", "action": "write the summary", "kind": "think", "depends_on": ["s1"]}],
            "assumptions": [{"text": "last week means Mon-Sun", "confidence": 0.6, "consequential": False}],
            "questions": []}
    base.update(kw)
    return base


# ---------------------------------------------------------------- the gate
def test_thinking_and_reading_with_a_clear_goal_is_ready_and_low_stakes_assumptions_are_not_asked():
    g = ic.gate(ic.validate(spec(), []))
    assert g.decision == "ready" and g.questions == []
    assert g.auto_resolved == ["last week means Mon-Sun"]


@pytest.mark.parametrize("change,needle", [
    ({"interpretations": [{"goal": "summary", "p": 0.45}, {"goal": "chart", "p": 0.3}]}, "most likely reading"),
    ({"interpretations": [{"goal": "summary", "p": 0.5}, {"goal": "rerun", "p": 0.45}]}, "most likely reading"),
    ({"interpretations": [{"goal": "summary", "p": 0.62}, {"goal": "rerun", "p": 0.48}]}, "close"),
    ({"assumptions": [{"text": "delete the old runs", "confidence": 0.7, "consequential": True}]}, "consequential"),
    ({"assumptions": [{"text": "use the paid data feed", "confidence": None, "consequential": True}]}, "unknown"),
    ({"steps": []}, "no steps"),
])
def test_consequential_ambiguity_triggers_clarification(change, needle):
    g = ic.gate(ic.validate(spec(**change), []))
    assert g.decision == "clarify" and any(needle in r for r in g.reasons)
    assert g.questions, "a clarification always comes with at least one question"


def test_running_or_writing_needs_approval_and_external_actions_are_never_ready():
    g = ic.gate(ic.validate(spec(steps=[{"id": "s1", "action": "run the test suite", "kind": "run", "depends_on": []},
                                        {"id": "s2", "action": "email the results to Raj", "kind": "external",
                                         "depends_on": ["s1"]}]), []))
    assert g.decision == "approval"
    assert g.needs_approval == ["s1: run the test suite"]
    assert g.yours_to_do == ["s2: email the results to Raj"]


def test_a_confident_consequential_assumption_does_not_block():
    s = spec(assumptions=[{"text": "use the repo's own test command", "confidence": 0.95, "consequential": True}])
    assert ic.gate(ic.validate(s, [])).decision == "ready"


# ---------------------------------------------------------------- validation
def test_a_note_citation_must_point_at_a_note_that_was_sent():
    s = spec(constraints=[{"text": "costs are 25bps", "source": "notes", "notes": [2]},
                          {"text": "use weekly bars", "source": "notes", "notes": [7]},
                          {"text": "be brief", "source": "notes"}])
    v = ic.validate(s, ["mem_a", "mem_b"])
    by = {c["text"]: c for c in v["constraints"]}
    assert by["costs are 25bps"]["source"] == "notes" and by["costs are 25bps"]["item_ids"] == ["mem_b"]
    assert by["use weekly bars"]["source"] == "assumed" and by["be brief"]["source"] == "assumed"
    assert sum("citing none that were sent" in f for f in v["flags"]) == 2


def test_unknown_step_kinds_are_treated_as_the_most_restricted():
    v = ic.validate(spec(steps=[{"id": "s1", "action": "push to production", "kind": "deploy", "depends_on": []}]), [])
    assert v["steps"][0]["kind"] == "external"
    assert ic.gate(v).yours_to_do == ["s1: push to production"]


def test_levels_cycles_and_unknown_dependencies():
    steps = [{"id": "a", "action": "x", "kind": "read", "depends_on": []},
             {"id": "b", "action": "x", "kind": "read", "depends_on": []},
             {"id": "c", "action": "x", "kind": "think", "depends_on": ["a", "b"]}]
    assert ic.validate(spec(steps=steps), [])["levels"] == [["a", "b"], ["c"]]
    with pytest.raises(ic.SpecError, match="cycle"):
        ic.validate(spec(steps=[{"id": "a", "action": "x", "kind": "read", "depends_on": ["b"]},
                                {"id": "b", "action": "x", "kind": "read", "depends_on": ["a"]}]), [])
    with pytest.raises(ic.SpecError, match="unknown"):
        ic.validate(spec(steps=[{"id": "a", "action": "x", "kind": "read", "depends_on": ["zzz"]}]), [])


def test_parse_finds_the_object_in_fenced_or_chatty_replies():
    assert ic.parse("Sure!\n```json\n{\"goal\": \"g\"}\n```")["goal"] == "g"
    assert ic.parse("Here you go {\"goal\": \"g\"} hope that helps")["goal"] == "g"
    assert ic.parse("no json here") is None


# ---------------------------------------------------------------- end to end
class Canned(Recorder):
    def __init__(self, provider, model, text):
        super().__init__(provider, model)
        self.text = text

    def generate(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        return ModelResponse(text=self.text, usage=Usage(10, 5), cost_usd=0.002, latency_ms=5.0,
                             stop_reason="end_turn", model=self.model, provider=self.provider)


def canned(ci, obj):
    reps = {}
    for m in ("haiku", "sonnet", "opus"):
        reps[m] = Canned("claude-cli", m, obj if isinstance(obj, str) else json.dumps(obj))
        ci.set_adapter("claude-cli", m, reps[m])
    return reps


def test_compile_routes_in_plan_mode_checks_provenance_and_stores_a_sealed_record(env, tmp_path):
    ci, _ = make(tmp_path)
    ci.remember("u", "Backtests use 25bps round-trip costs", "constraint")
    reps = canned(ci, spec(constraints=[{"text": "25bps costs", "source": "notes", "notes": [1]}]))
    rec = ic.compile_intent(ci, "u", "backtest summary")
    served = rec["served_by"].split(":")[1]
    system = reps[served].prompts[0].system
    assert modes.PLAN_FORMAT in system and DIRECTIVES["plan"] in system
    route = ci.receipt("u", rec["interaction"])["route"]
    assert route["plan"]["requirement"]["tier"] in ("standard", "deep")
    assert rec["status"] == "ready" and rec["spec"]["constraints"][0]["source"] == "notes"
    assert rec["spec"]["constraints"][0]["item_ids"] == rec["context_items"][:1]
    assert ic.records(ci, "u")[-1]["id"] == rec["id"]
    raw = b"".join(f.read_bytes() for f in (tmp_path / "data").iterdir() if f.name.startswith("pcim.db"))
    assert b"backtest summary" not in raw  # sealed at rest
    assert any(p["id"] == rec["id"] for p in ci.export("u")["growth"]["plans"])


def test_answers_recompile_with_a_parent_and_approval_respects_the_gate(env, tmp_path):
    ci, _ = make(tmp_path)
    canned(ci, spec(assumptions=[{"text": "overwrite results.csv", "confidence": 0.5, "consequential": True}]))
    first = ic.compile_intent(ci, "u", "redo the results")
    assert first["status"] == "needs_answers"
    with pytest.raises(ValueError, match="open questions"):
        ic.approve(ci, "u", first["id"])
    canned(ci, spec(steps=[{"id": "s1", "action": "write results_v2.csv", "kind": "write", "depends_on": []}]))
    second = ic.answer(ci, "u", first["id"], "no, write a new file")
    assert second["parent"] == first["id"] and second["status"] == "awaiting_approval"
    assert "Answers to your earlier questions: no, write a new file" in ci.receipt("u", second["interaction"])["task"]
    assert ic.approve(ci, "u", second["id"])["status"] == "approved"


def test_a_reply_that_is_not_a_plan_is_recorded_as_invalid_not_guessed(env, tmp_path):
    ci, _ = make(tmp_path)
    canned(ci, "I'd be happy to help with that!")
    rec = ic.compile_intent(ci, "u", "hmm")
    assert rec["status"] == "invalid" and rec["spec"] is None and "did not return" in rec["error"]


def test_plan_is_a_command_not_a_default_mode(env, tmp_path):
    ci, _ = make(tmp_path)
    with pytest.raises(ValueError, match="command"):
        ci.set_intelligence_mode("u", "plan")


def test_terminal_plan_round_trip(env, tmp_path, monkeypatch, capsys):
    ci, _ = make(tmp_path)
    canned(ci, spec())
    monkeypatch.setattr(terminal, "_open", lambda: (ci, "u"))
    assert terminal.main(["plan", "summarise", "the", "backtests"]) == 0
    out = capsys.readouterr().out
    assert "Decision: ready" in out and "L1 s1 [read]" in out and "L2 s2 [think]" in out
    pid = ic.records(ci, "u")[-1]["id"]
    assert terminal.main(["plan", "approve", pid]) == 0
    assert terminal.main(["plan"]) == 0
    assert "approved" in capsys.readouterr().out
