"""`ci plan`: the intent compiler. The gate and the provenance check are code, so they are tested
without a model; the end-to-end path uses canned replies through the real routing and engine."""

import json
import sys

import pytest
from test_amplify import Recorder, env, make  # noqa: F401  (env is a fixture)

from pcim import intent_compiler as ic
from pcim import modes, terminal
from pcim.adapters.base import ModelResponse, Usage
from pcim.assist import DIRECTIVES
from pcim.workspace import Workspace


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
    # the external step serves no outcome the model named, so one is made for it, and it is yours
    assert g.yours_to_do == ["oSs2: email the results to Raj"]


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
    assert ic.gate(v).yours_to_do == ["oSs1: push to production"]


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


def authorized(ci, tmp_path, write=False):
    """A project whose folder CI may read (and run tests in, and write to if `write`)."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    ws = Workspace(ci)
    p = ws.create_project("u", "Backtests", objective="weekly summaries")
    ws.authorize_workspace("u", p.id, str(repo), run_argv=[sys.executable, "-c", "print('1 passed')"],
                           allow_write=write)
    return p, set(ws.provider_vendors("u", p.id)), repo


def test_compile_routes_in_plan_mode_checks_provenance_and_stores_a_sealed_record(env, tmp_path):
    ci, _ = make(tmp_path)
    ci.remember("u", "Backtests use 25bps round-trip costs", "constraint")
    reps = canned(ci, spec(constraints=[{"text": "25bps costs", "source": "notes", "notes": [1]}]))
    p, vendors, _ = authorized(ci, tmp_path)
    rec = ic.compile_intent(ci, "u", "backtest summary", project_id=p.id, project_vendors=vendors)
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
    p, vendors, _ = authorized(ci, tmp_path, write=True)
    canned(ci, spec(assumptions=[{"text": "overwrite results.csv", "confidence": 0.5, "consequential": True}]))
    first = ic.compile_intent(ci, "u", "redo the results", project_id=p.id, project_vendors=vendors)
    assert first["status"] == "needs_answers"
    with pytest.raises(ValueError, match="open questions"):
        ic.approve(ci, "u", first["id"])
    canned(ci, spec(steps=[{"id": "s1", "action": "write results_v2.csv", "kind": "write", "depends_on": []}]))
    second = ic.answer(ci, "u", first["id"], "no, write a new file", project_vendors=vendors)
    assert second["parent"] == first["id"] and second["status"] == "awaiting_approval"
    assert "Answers to your earlier questions: no, write a new file" in ci.receipt("u", second["interaction"])["task"]
    assert ic.approve(ci, "u", second["id"])["status"] == "approved"


def test_a_reply_that_is_not_a_plan_is_recorded_as_invalid_not_guessed(env, tmp_path):
    ci, _ = make(tmp_path)
    canned(ci, "I'd be happy to help with that!")
    rec = ic.compile_intent(ci, "u", "hmm")
    assert rec["status"] == "invalid" and rec["spec"] is None and "did not return" in rec["error"]


def test_invalid_json_plan_gets_one_bounded_reask(env, tmp_path):
    ci, _ = make(tmp_path)
    replies = ["I'd be happy to help!", json.dumps(spec())]

    class TwoStep:
        provider, model = "claude-cli", "sonnet"
        def generate(self, prompt, max_tokens=None):
            return ModelResponse(text=replies.pop(0), usage=Usage(1, 1), cost_usd=0.0, latency_ms=1.0,
                                 stop_reason="end_turn", model="sonnet", provider="claude-cli")

    for m in ("haiku", "sonnet", "opus"):
        ci.set_adapter("claude-cli", m, TwoStep())
    rec = ic.compile_intent(ci, "u", "summarise the backtests")
    assert rec["status"] == "blocked" and rec["spec"] is not None and replies == []
    assert rec["reask_of"] and rec["reask_of"] != rec["interaction"]  # the failed first call stays on record, so its cost is not lost


def test_plan_is_a_command_not_a_default_mode(env, tmp_path):
    ci, _ = make(tmp_path)
    with pytest.raises(ValueError, match="command"):
        ci.set_intelligence_mode("u", "plan")


def test_terminal_plan_round_trip(env, tmp_path, monkeypatch, capsys):
    ci, _ = make(tmp_path)
    canned(ci, spec())
    _, _, repo = authorized(ci, tmp_path)
    monkeypatch.chdir(repo)  # `ci plan` plans for the project whose folder you are in
    monkeypatch.setattr(terminal, "_open", lambda: (ci, "u"))
    assert terminal.main(["plan", "summarise", "the", "backtests"]) == 0
    out = capsys.readouterr().out
    assert "Decision: ready" in out and "L1 s1 [read]" in out and "L2 s2 [think]" in out
    pid = ic.records(ci, "u")[-1]["id"]
    assert terminal.main(["plan", "approve", pid]) == 0
    assert terminal.main(["plan"]) == 0
    assert "approved" in capsys.readouterr().out


def test_a_requested_external_action_the_plan_dropped_cannot_be_ready():
    """Post hoc, from the 2026-11-13 development run: "tweet about the launch" was planned as
    drafting only and gated ready. The request's own words now stop that."""
    drafting = ic.validate(spec(steps=[{"id": "s1", "action": "draft a tweet", "kind": "think", "depends_on": []}]), [])
    g = ic.gate(drafting, "tweet about the launch")
    assert g.decision == "approval"
    assert g.yours_to_do == ["oR1: tweet about the launch (restored from your request)"]
    assert any("intent preserved" in f for f in drafting["flags"])
    assert ic.gate(ic.validate(spec(steps=[{"id": "s1", "action": "draft a tweet", "kind": "think", "depends_on": []}]), []),
                   "explain how FSRS spacing works").decision == "ready"
    planned = ic.validate(spec(steps=[{"id": "s1", "action": "post the tweet", "kind": "external", "depends_on": []}]), [])
    ic.gate(planned, "tweet about it")
    assert not any(o.get("restored") for o in planned["outcomes"])  # the plan already has it: nothing to restore


def test_outside_an_authorized_folder_a_plan_that_reads_files_is_blocked_and_cannot_be_approved(env, tmp_path):
    ci, _ = make(tmp_path)
    canned(ci, spec())  # s1 reads the run logs
    rec = ic.compile_intent(ci, "u", "summarise the backtests")
    assert rec["status"] == "blocked" and "no folder is authorized" in rec["gate"]["reasons"][0]
    with pytest.raises(ValueError, match="permissions"):
        ic.approve(ci, "u", rec["id"])


def test_d1_after_one_answer_round_low_stakes_ambiguity_becomes_assumed():
    s = ic.validate({
        "goal": "Fix the parser",
        "interpretations": [{"goal": "Fix A", "p": 0.5}, {"goal": "Fix B", "p": 0.5}],
        "outcomes": [{"id": "o1", "text": "fixed parser", "kind": "deliverable"}],
        "steps": [{"id": "s1", "action": "edit parser", "kind": "write", "serves": ["o1"]}],
        "assumptions": [{"text": "target is v2", "confidence": 0.5, "consequential": True}],
        "questions": ["Which parser?"],
    }, [])
    assert ic.gate(s, after_answer=False).decision == "clarify"
    g2 = ic.gate(s, after_answer=True)
    assert g2.decision == "approval" and "target is v2" in g2.auto_resolved


def test_d1_consequential_external_ambiguity_still_asks_after_answer():
    s = ic.validate({
        "goal": "Send report",
        "interpretations": [{"goal": "Email Alice", "p": 0.5}, {"goal": "Email Bob", "p": 0.5}],
        "outcomes": [{"id": "o1", "text": "send report", "kind": "external_action"}],
        "steps": [{"id": "s1", "action": "send", "kind": "external", "serves": ["o1"]}],
        "assumptions": [{"text": "recipient is Alice", "confidence": 0.4, "consequential": True}],
    }, [])
    assert ic.gate(s, after_answer=True).decision == "clarify"


@pytest.mark.parametrize("prose", [
    "Calls that don't pass holidays raise ValueError",
    "reserve raises KeyError when empty",
    "release raises RuntimeError",
    "remove doesn't mutate the list",
    "`reserve` returns None on miss",
    "reserve(x) should return False",
    "Emails from unknown senders are ignored",
    "Buying a house is expensive",
])
def test_d2_extractor_ignores_identifiers_subjects_and_inflections(prose):
    assert ic.requested_actions(prose) == []


@pytest.mark.parametrize("req,expected_lemma", [
    ("please send it to priya", "send"),
    ("delete old_logs.txt", "delete"),
    ("call Alice and reserve a table", "contact"),
    ("emial the team", "email"),
])
def test_d2_extractor_preserves_real_requested_actions(req, expected_lemma):
    hits = ic.requested_actions(req)
    assert any(h["lemma"] == expected_lemma for h in hits)


def test_d5_answer_preserves_parent_external_outcome_unless_withdrawn(env, tmp_path):
    ci, _ = make(tmp_path)
    p1 = {
        "goal": "Fix bug and create a Jira ticket",
        "interpretations": [{"goal": "Fix bug and create Jira ticket", "p": 0.55},
                            {"goal": "Fix bug only", "p": 0.45}],
        "outcomes": [
            {"id": "o1", "text": "bug fixed", "kind": "deliverable"},
            {"id": "o2", "text": "create a Jira ticket", "kind": "external_action", "quote": "create a Jira ticket"},
        ],
        "steps": [
            {"id": "s1", "action": "fix bug", "kind": "think", "serves": ["o1"]},
            {"id": "s2", "action": "create Jira ticket", "kind": "external", "serves": ["o2"]},
        ],
        "questions": ["Which module?"],
    }
    p2 = {
        "goal": "Fix bug in parser.py",
        "interpretations": [{"goal": "Fix bug in parser.py", "p": 0.95}],
        "outcomes": [{"id": "o1", "text": "bug fixed", "kind": "deliverable"}],
        "steps": [{"id": "s1", "action": "fix parser.py", "kind": "think", "serves": ["o1"]}],
    }
    canned(ci, p1)
    parent = ic.compile_intent(ci, "u", "fix the bug and create a Jira ticket")
    assert parent["status"] == "needs_answers"

    canned(ci, p2)
    child1 = ic.answer(ci, "u", parent["id"], "in parser.py")
    assert any(o["kind"] == "external_action" and "Jira" in o["text"] for o in child1["spec"]["outcomes"])
    assert child1["gate"]["yours_to_do"]

    canned(ci, p2)
    child2 = ic.answer(ci, "u", parent["id"], "in parser.py, and don't create the Jira ticket")
    assert not any(o["kind"] == "external_action" for o in child2["spec"]["outcomes"])


def test_a_parent_action_is_not_hidden_by_a_different_action_that_shares_a_word(env, tmp_path):
    ci, _ = make(tmp_path)
    parent_spec = {"goal": "share the report", "interpretations": [{"goal": "a", "p": 0.5}, {"goal": "b", "p": 0.5}],
                   "outcomes": [{"id": "o1", "text": "email the report to raj", "kind": "external_action"}],
                   "steps": [{"id": "s1", "action": "draft", "kind": "think", "serves": ["o1"]}], "questions": ["which report?"]}
    child_spec = {"goal": "share the march report", "interpretations": [{"goal": "a", "p": 0.95}],
                  "outcomes": [{"id": "o1", "text": "post the report in #eng", "kind": "external_action"}],
                  "steps": [{"id": "s1", "action": "draft", "kind": "think", "serves": ["o1"]}]}
    canned(ci, parent_spec)
    parent = ic.compile_intent(ci, "u", "get the report out")
    canned(ci, child_spec)
    child = ic.answer(ci, "u", parent["id"], "the march report, don't include the appendix")
    texts = [o["text"] for o in child["spec"]["outcomes"] if o["kind"] == "external_action"]
    assert "email the report to raj" in texts and not child["spec"].get("withdrawn")


def test_after_an_answer_the_persons_own_external_action_does_not_keep_low_stakes_questions_open():
    spec = ic.validate({"goal": "fix paginate and email raj the diff",
                        "interpretations": [{"goal": "fix and email", "p": 0.5}, {"goal": "fix, draft only", "p": 0.4}],
                        "outcomes": [{"id": "o1", "text": "paginate fixed", "kind": "deliverable"},
                                     {"id": "o2", "text": "email raj the diff", "kind": "external_action"}],
                        "steps": [{"id": "s1", "action": "fix paginate", "kind": "write", "serves": ["o1"]}],
                        "assumptions": [{"text": "pager.py is the file", "confidence": 0.5, "consequential": True}]},
                       [], request="fix paginate() and email raj the diff")
    assert ic.gate(dict(spec), "fix paginate() and email raj the diff").decision == "clarify"  # first time: ask
    g = ic.gate(dict(spec), "fix paginate() and email raj the diff", after_answer=True)
    assert g.decision == "approval" and any("email raj" in y for y in g.yours_to_do)  # never ready, never dropped
