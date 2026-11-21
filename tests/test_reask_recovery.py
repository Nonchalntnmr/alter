"""The invalid-JSON recovery path of the intent compiler (paired v2 r10), deterministically.

A planner reply that is not a JSON object gets exactly one re-ask:
- if the re-ask parses, the plan is used, and the failed first call stays on the record
  (`reask_of`), so its cost is counted;
- if it does not, the plan is `invalid`: it is never ready, never approvable and never run.

These tests script the model's replies through the real routing and engine. No live model
produced a non-JSON plan in 157 compiles (experiments/eval_v4), so this is the path's only
measurement.
"""

import json

import pytest
from test_amplify import env, make  # noqa: F401  (env is a fixture)

from pcim import intent_compiler as ic
from pcim import terminal
from pcim.adapters.base import ModelResponse, Usage
from test_intent_compiler import authorized, spec


class Scripted:
    """Every routed model shares one script; each call takes the next reply and records its prompt."""

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def install(self, ci):
        for m in ("haiku", "sonnet", "opus"):
            ci.set_adapter("claude-cli", m, _Model(self, m))
        return self


class _Model:
    provider = "claude-cli"

    def __init__(self, script, model):
        self.script, self.model = script, model

    def generate(self, prompt, max_tokens=None):
        self.script.prompts.append(prompt.user)
        if not self.script.replies:
            raise AssertionError("a third model call: the re-ask must be bounded to one")
        text = self.script.replies.pop(0)
        return ModelResponse(text=text, usage=Usage(10, 5), cost_usd=0.002, latency_ms=1.0, stop_reason="end_turn",
                             model=self.model, provider="claude-cli")


NOT_JSON = "Sure! Here's how I'd approach it: first read the logs, then write a summary."


def test_a_non_json_reply_is_recovered_by_exactly_one_reask(env, tmp_path):
    ci, _ = make(tmp_path)
    s = Scripted([NOT_JSON, json.dumps(spec())]).install(ci)
    rec = ic.compile_intent(ci, "u", "summarise the backtests")
    assert len(s.prompts) == 2 and s.replies == []
    assert "not a valid JSON object" in s.prompts[1] and "summarise the backtests" in s.prompts[1]
    assert rec["status"] != "invalid" and rec["spec"] is not None and rec["error"] is None
    assert rec["reask_of"] and rec["reask_of"] != rec["interaction"]
    # both calls are real interactions with receipts, so the failed one's cost is countable
    assert ci.receipt("u", rec["reask_of"]) is not None and ci.receipt("u", rec["interaction"]) is not None


def test_two_non_json_replies_end_invalid_after_exactly_two_calls_and_can_never_run(env, tmp_path, monkeypatch, capsys):
    ci, _ = make(tmp_path)
    s = Scripted([NOT_JSON, "Still not JSON, sorry."]).install(ci)
    rec = ic.compile_intent(ci, "u", "summarise the backtests")
    assert len(s.prompts) == 2                          # bounded: never a third call
    assert rec["status"] == "invalid" and rec["spec"] is None and rec["gate"] is None
    assert rec["error"] == "the model did not return a JSON plan"
    with pytest.raises(ValueError, match="invalid"):
        ic.approve(ci, "u", rec["id"])                  # an invalid plan is never approvable
    assert ic.get(ci, "u", rec["id"])["status"] == "invalid"
    # and the terminal says so instead of offering approval
    monkeypatch.setattr(terminal, "_open", lambda: (ci, "u"))
    terminal.main(["plan", "show", rec["id"]])
    out = capsys.readouterr().out
    assert "Approve with" not in out


def test_a_fenced_json_reply_parses_without_a_reask(env, tmp_path):
    ci, _ = make(tmp_path)
    s = Scripted(["Here is the plan:\n```json\n" + json.dumps(spec()) + "\n```"]).install(ci)
    rec = ic.compile_intent(ci, "u", "summarise the backtests")
    assert len(s.prompts) == 1 and rec["reask_of"] is None and rec["spec"] is not None


def test_json_that_is_not_a_usable_plan_is_invalid_without_a_reask(env, tmp_path):
    ci, _ = make(tmp_path)
    s = Scripted([json.dumps({"steps": []})]).install(ci)  # parses, but has no goal
    rec = ic.compile_intent(ci, "u", "summarise the backtests")
    assert len(s.prompts) == 1 and rec["status"] == "invalid" and rec["error"].startswith("unusable plan")


def test_the_reask_also_guards_the_compile_after_an_answer(env, tmp_path):
    ci, _ = make(tmp_path)
    p, vendors, _ = authorized(ci, tmp_path)
    ask = spec(interpretations=[{"goal": "summary", "p": 0.45}, {"goal": "chart", "p": 0.4}])
    s = Scripted([json.dumps(ask), NOT_JSON, json.dumps(spec())]).install(ci)
    first = ic.compile_intent(ci, "u", "summarise the backtests", project_id=p.id, project_vendors=vendors)
    assert first["status"] == "needs_answers"
    second = ic.answer(ci, "u", first["id"], "a one-page summary", project_vendors=vendors)
    assert len(s.prompts) == 3 and second["reask_of"] and second["parent"] == first["id"]
    assert second["status"] != "invalid"
