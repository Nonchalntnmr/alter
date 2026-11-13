"""The amplification slice: task-aware routing, intelligence modes, independent-performance
tracking, the scorecards, the Cognitive Twin (`ci weekly`) and the `ci` terminal command.

No test makes a network call. The Claude CLI is a stand-in binary (it prints a canned result),
and every other model is an in-process adapter that records exactly what it was sent.
"""

import datetime as dt
import io
import json
import re
import sys

import pytest

from pcim import cli, modes, routing, terminal
from pcim.adapters.base import ModelResponse, ProviderError, Usage
from pcim.assist import DIRECTIVES
from pcim.engine import PersonalCI, RoutingError
from pcim.sovereignty.credentials import find_credentials
from pcim.twin import CognitiveTwin, render
from pcim.types import utcnow
from pcim.workspace import Workspace

PY = sys.executable
STUB = """#!{py}
import json, sys
argv = sys.argv[1:]
model = argv[argv.index("--model") + 1]
canon = {{"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}}.get(model, model)
prompt = sys.stdin.read()
open({log!r}, "a").write(model + "\\n")
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn",
                  "result": "[stub " + canon + "] ok", "usage": {{"input_tokens": 10, "output_tokens": 5}},
                  "total_cost_usd": 0.001, "modelUsage": {{canon: {{"costUSD": 0.001}}}}}}))
"""
_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "PCIM_OPENAI_MODEL", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "PCIM_GEMINI_MODEL", "PCIM_LOCAL_MODEL", "PCIM_LOCAL_BASE_URL", "PCIM_CAPABILITIES")


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A machine with only the Claude CLI: the stand-in binary is on PCIM_CLAUDE_BIN, no API keys."""
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "bin").mkdir()
    binary, log = tmp_path / "bin" / "claude", tmp_path / "bin" / "calls.log"
    binary.write_text(STUB.format(py=PY, log=str(log)))
    binary.chmod(0o755)
    monkeypatch.setenv("PCIM_CLAUDE_BIN", str(binary))
    return log


class Recorder:
    """An adapter that records every prompt; optionally fails the way a provider does."""

    can_plan = False

    def __init__(self, provider, model, fail=None):
        self.provider, self.model, self.fail, self.prompts = provider, model, fail, []

    def generate(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        if self.fail:
            raise self.fail
        return ModelResponse(text=f"answer from {self.model}", usage=Usage(10, 5), cost_usd=0.001, latency_ms=5.0,
                             stop_reason="end_turn", model=self.model, provider=self.provider)


def make(tmp_path, providers=("claude-cli",), name="Nishanth"):
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", name, providers)
    recs = {m: Recorder("claude-cli", m) for m in ("haiku", "sonnet", "opus")}
    for m, r in recs.items():
        ci.set_adapter("claude-cli", m, r)
    return ci, recs


LIGHT = "What does HTTP status 429 mean?"
DEEP = "Design the architecture for a multi-tenant SaaS with per-tenant keys, and explain the trade-offs."


# ---------------------------------------------------------------- routing
def test_routing_picks_the_cheapest_model_that_meets_the_requirement_and_says_why(env):
    allowed = {"anthropic"}
    balanced = routing.plan(LIGHT, allowed_vendors=allowed)
    assert balanced.chosen.key == "claude-cli:sonnet"  # the default never routes below standard
    assert any("balanced routing starts at standard" in s for s in balanced.requirement.signals)
    light = routing.plan(LIGHT, allowed_vendors=allowed, preference="economy")
    std = routing.plan("Write unit tests for a function that validates email addresses.", allowed_vendors=allowed)
    deep = routing.plan(DEEP, allowed_vendors=allowed)
    assert (light.chosen.key, std.chosen.key, deep.chosen.key) == ("claude-cli:haiku", "claude-cli:sonnet",
                                                                   "claude-cli:opus")
    assert [c.key for c in light.candidates] == ["claude-cli:haiku", "claude-cli:sonnet", "claude-cli:opus"]
    assert deep.candidates[1].meets is False  # a fallback below the requirement is marked as such
    assert deep.reason == "cheapest authorized model that meets the deep requirement"
    text = "\n".join(deep.explain())
    assert "'trade-offs'" in text and "ANTHROPIC_API_KEY is not set" in text and "notional" in text


def test_routing_never_offers_a_vendor_the_person_has_not_authorized(env, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("PCIM_OPENAI_MODEL", "gpt-test")
    p = routing.plan(LIGHT, allowed_vendors={"openai"})
    assert {c.entry.vendor for c in p.candidates} == {"openai"}
    assert any(c["model"] == "claude-cli:haiku" and c["outcome"].startswith("not authorized") for c in p.considered)
    assert routing.plan(LIGHT, allowed_vendors=set()).refused == "no available model is authorized"

    ci, recs = make(tmp_path)
    assert ci.authorized_vendors("u") == {"anthropic"}
    for g in ci.ledger.grants("u"):
        ci.revoke("u", g.id)
    with pytest.raises(RoutingError):
        ci.ask_routed("u", LIGHT)
    assert not any(r.prompts for r in recs.values())


def test_a_request_containing_a_credential_never_reaches_an_external_vendor(env, monkeypatch, tmp_path):
    ci, recs = make(tmp_path)
    task = "deploys fail with key AKIAABCDEFGHIJKLMNOP, why?"
    assert find_credentials(task) == ["AWS access key id"]
    assert "password assignment" in find_credentials("password: hunter2hunter2")
    assert find_credentials("the password rotates on Fridays") == []
    with pytest.raises(RoutingError) as e:
        ci.ask_routed("u", task)
    assert "credential" in e.value.plan.refused and not any(r.prompts for r in recs.values())
    assert ci.audit.entries("u")[-1]["payload"]["credentials_found"] == 1
    assert "AKIA" not in json.dumps(ci.audit.entries("u"))  # the kind is recorded, never the value
    monkeypatch.setenv("PCIM_LOCAL_MODEL", "llama-test")
    monkeypatch.setenv("PCIM_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1")
    p = routing.plan(task, allowed_vendors={"anthropic", "local"})
    assert p.chosen.key == "local:llama-test" and p.chosen.est_cost_usd == 0.0


def test_a_pinned_model_is_used_as_chosen_and_never_swapped(env, tmp_path):
    ci, recs = make(tmp_path)
    recs["haiku"].fail = ProviderError("down")
    with pytest.raises(RoutingError) as e:
        ci.ask_routed("u", LIGHT, pinned="claude-cli:haiku")
    assert [a["model"] for a in e.value.attempts] == ["claude-cli:haiku"] and not recs["sonnet"].prompts
    with pytest.raises(RoutingError) as e:
        ci.ask_routed("u", LIGHT, pinned="openai:gpt-x")
    assert "not a model CI knows" in str(e.value)
    recs["haiku"].fail = None
    r = ci.ask_routed("u", DEEP, pinned="claude-cli:haiku")  # explicit choice wins over the estimate
    assert r.receipt["route"]["served_by"] == "claude-cli:haiku" and "below the estimate" in r.receipt["route"]["plan"]["reason"]


def test_a_provider_failure_falls_back_and_the_receipt_and_audit_say_so(env, tmp_path):
    ci, recs = make(tmp_path)
    recs["sonnet"].fail = ProviderError("overloaded")
    r = ci.ask_routed("u", LIGHT)
    rt = r.receipt["route"]
    assert rt["served_by"] == "claude-cli:opus" and rt["failed_before"][0]["model"] == "claude-cli:sonnet"
    assert "overloaded" in rt["failed_before"][0]["error"]
    assert ci.store.get_interaction(r.interaction_id)["receipt"]["route"]["served_by"] == "claude-cli:opus"
    assert any(a["event"] == "route.fallback" for a in ci.audit.entries("u"))
    recs["sonnet"].fail = KeyError("a bug, not an outage")  # never hidden behind a fallback
    with pytest.raises(KeyError):
        ci.ask_routed("u", LIGHT)


def test_a_fallback_to_another_vendor_never_carries_what_that_vendor_may_not_see(env, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("PCIM_OPENAI_MODEL", "gpt-test")
    caps = tmp_path / "caps.json"
    caps.write_text(json.dumps({"openai:gpt-test": {"tier": 1}}))
    monkeypatch.setenv("PCIM_CAPABILITIES", str(caps))
    ci, recs = make(tmp_path, providers=("claude-cli", "openai"))
    ci.consent("u", "ingest:notes", "store")
    ci.consent("u", "ingest:notes", "egress", "anthropic")  # Anthropic may see these notes; OpenAI may not
    ci.ingest_text("u", "The staging database password rotates every Friday at noon.", "ingest:notes")
    for r in recs.values():
        r.fail = ProviderError("down")
    oa = Recorder("openai", "gpt-test")
    ci.set_adapter("openai", "gpt-test", oa)
    r = ci.ask_routed("u", "When does the staging database password rotate?")
    assert r.receipt["route"]["served_by"] == "openai:gpt-test"
    assert r.receipt["sent"] == [] and r.receipt["withheld_by_egress"]
    assert "Friday" not in oa.prompts[0].system + oa.prompts[0].user
    first = recs["sonnet"].prompts[0]
    assert "Friday" in first.system + first.user  # the vendor allowed to see it was offered it


def test_a_capability_file_rates_new_models_and_known_context_limits_bind(env, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("PCIM_OPENAI_MODEL", "gpt-test")
    allowed = {"anthropic", "openai"}
    task = "What is the expected value of rolling two dice and taking the maximum?"
    p = routing.plan(task, allowed_vendors=allowed, estimator="lexical")
    assert p.requirement.domains == ["math"] and p.chosen.key == "claude-cli:sonnet"  # unrated models go last
    caps = tmp_path / "caps.json"
    caps.write_text(json.dumps({"openai:gpt-test": {"tier": 2, "strengths": ["math"]},
                                "claude-haiku-5-5": {"context_window": 1000}}))
    monkeypatch.setenv("PCIM_CAPABILITIES", str(caps))
    p = routing.plan(task, allowed_vendors=allowed, estimator="lexical")
    assert p.chosen.key == "openai:gpt-test" and "strong at math" in p.reason
    light = routing.plan("Convert 72 Fahrenheit to Celsius.", allowed_vendors={"anthropic"}, estimator="lexical")
    assert light.chosen.key == "claude-cli:sonnet"
    assert any(c["model"] == "claude-cli:haiku" and c["outcome"].startswith("too small") for c in light.considered)
    caps.write_text(json.dumps({"x": {"tier": 9}}))
    with pytest.raises(routing.CapabilityFileError):
        routing.plan("hi", allowed_vendors=allowed, estimator="lexical")


def test_modes_and_preferences_move_the_requirement_and_say_why():
    """Rules of the lexical estimator and of preference/floor/mode, which apply to both."""
    assert routing.estimate("Convert 72 Fahrenheit to Celsius.", apply_floor=False, estimator="lexical").tier == 1
    assert routing.estimate("Convert 72 Fahrenheit to Celsius.", estimator="lexical").tier == 2  # balanced floor
    assert routing.estimate("Convert 72 Fahrenheit to Celsius.", preference="economy", estimator="lexical").tier == 1
    r = routing.estimate("Convert 72 Fahrenheit to Celsius.", mode="learn", apply_floor=False, estimator="lexical")
    assert r.tier == 2 and any("learn mode" in s for s in r.signals)
    assert routing.estimate("Write a threat model and weigh the trade-offs", preference="economy", estimator="lexical").tier == 2
    assert routing.estimate("Convert 72 Fahrenheit to Celsius.", preference="quality", estimator="lexical").tier == 2
    with pytest.raises(ValueError):
        routing.estimate("x", preference="cheap", estimator="lexical")


# ---------------------------------------------------------------- modes
def test_each_mode_sends_its_own_instructions(env, tmp_path):
    ci, recs = make(tmp_path)

    def run(mode):
        r = ci.ask_routed("u", "Explain how gradient descent works", mode=mode)
        return recs[r.receipt["route"]["served_by"].split(":")[1]].prompts[-1].system, r.receipt

    s, rec = run("execute")
    assert DIRECTIVES["direct"] in s and modes.EXECUTE_DIRECTIVE in s and rec["assist_mode"] == "direct"
    s, rec = run("amplify")
    assert DIRECTIVES["amplify"] in s and rec["intelligence_mode"]["mode"] == "amplify"
    s, rec = run("learn")
    assert modes.LEARN_DIRECTIVE in s and rec["assist_mode"] in ("hint_first", "socratic", "explain")


def test_mode_precedence_is_request_then_growth_goal_then_default(env, tmp_path):
    ci, _ = make(tmp_path)
    task = "Explain how gradient descent works, I want to understand it"
    assert ci.resolve_mode("u", task).as_dict() == {"mode": "execute", "source": "default", "why": "the built-in default"}
    ci.set_intelligence_mode("u", "amplify")
    assert ci.resolve_mode("u", task).mode == "amplify"
    ci.set_growth_goals("u", ["learning"])
    assert (ci.resolve_mode("u", task).mode, ci.resolve_mode("u", task).source) == ("learn", "growth_goal")
    d = ci.resolve_mode("u", task, "e")
    assert (d.mode, d.source) == ("execute", "request")
    with pytest.raises(ValueError):
        ci.set_intelligence_mode("u", "turbo")


def test_modes_never_change_autonomy_permissions_or_routing_filters(env, tmp_path):
    ci, _ = make(tmp_path)
    ws = Workspace(ci)
    p = ws.create_project("u", "P", objective="o")
    before = (ws.get_mode("u"), ws.all_auths("u"), ws.provider_vendors("u", p.id))
    for m in modes.MODES:
        ci.set_intelligence_mode("u", m)
        ci.ask_routed("u", LIGHT, mode=m)
    assert (ws.get_mode("u"), ws.all_auths("u"), ws.provider_vendors("u", p.id)) == before
    for g in ci.ledger.grants("u"):
        ci.revoke("u", g.id)
    for m in modes.MODES:
        with pytest.raises(RoutingError):
            ci.ask_routed("u", LIGHT, mode=m)


def test_requests_without_a_mode_behave_as_they_did_before(tmp_path):
    ci = PersonalCI(tmp_path, seed=0)
    ci.onboard("u")
    for _ in range(30):
        r = ci.ask("u", "help me plan my week")
        assert r.receipt["intelligence_mode"] is None and r.receipt["assist_mode"] != "amplify"


# ---------------------------------------------------------------- skills and independent performance
def test_independent_performance_is_measured_apart_from_assisted_success(tmp_path):
    ci = PersonalCI(tmp_path, seed=0)
    ci.onboard("u")
    for _ in range(4):
        ci.probe("u", "bayes rule", True, assisted=True)
    for ok in (True, False, False, False):
        ci.probe("u", "bayes rule", ok)
    s = ci.skill_report("u")["skills"]["bayes rule"]
    assert (s["unassisted"]["n"], s["assisted"]["rate"], s["dependency_gap"]) == (4, 1.0, 0.75)
    assert s["evidence"]["dependency_gap"] == "with help 4/4, alone 1/4"
    assert {"dependency_gap", "recurring_difficulty"} <= set(s["flags"])
    assert s["mastery"]["unassisted_probes"] == 4  # assisted attempts never moved mastery
    assert s["trend"]["direction"] == "not enough evidence"
    later = ci.skill_report("u", now=utcnow() + dt.timedelta(days=8))["skills"]["bayes rule"]
    assert "due_for_review" in later["flags"]
    assert later["evidence"]["due_for_review"].endswith("(4 unassisted attempts, 1 correct)")
    assert len(ci.export("u")["growth"]["attempts"]) == 8
    assert ci.forget_skill("u", "bayes rule") == {"skill": "bayes rule", "attempts_deleted": 8, "state_deleted": True}
    assert ci.skill_report("u")["skills"] == {} and ci.store.list_skills("u") == {}
    with pytest.raises(ValueError):
        ci.probe("u", "   ", True)


def test_review_dates_follow_the_evidence_not_a_fixed_week(tmp_path, clock):
    ci = PersonalCI(tmp_path, seed=0)
    ci.onboard("u")
    t0 = clock[0]
    for d in (0, 4, 18):  # three spaced unassisted successes
        clock[0] = t0 + dt.timedelta(days=d)
        ci.probe("u", "sql joins", True)
    ci.probe("u", "regex", False)  # one unassisted failure, same day
    for _ in range(3):
        ci.probe("u", "css grid", True, assisted=True)
    rep = ci.skill_report("u", now=t0 + dt.timedelta(days=26))["skills"]
    assert "due_for_review" not in rep["sql joins"]["flags"]  # idle 8 days: the old 7-day rule flagged it
    assert rep["sql joins"]["review"]["interval_days"] > 8
    assert "due_for_review" in rep["regex"]["flags"]
    assert rep["regex"]["evidence"]["due_for_review"].endswith("(1 unassisted attempt, 0 correct)")
    assert "due_for_review" not in rep["css grid"]["flags"] and rep["css grid"]["review"]["due"] is None


def test_an_outcome_is_verified_feedback_and_optionally_an_assisted_attempt(env, tmp_path):
    ci, _ = make(tmp_path)
    r = ci.ask_routed("u", "Explain how gradient descent works", mode="learn")
    out = ci.record_outcome("u", r.interaction_id, worked=True, skill="gradient descent")
    assert out["reward"] == 1.0 and out["attempt"]["counted"] is False
    assert ci.store.get_interaction(r.interaction_id)["feedback"]["task_success"] is True
    last = ci.attempts("u")[-1]
    assert (last["assisted"], last["interaction"], last["source"]) == (True, r.interaction_id, "outcome")
    assert ci.store.list_skills("u") == {}


# ---------------------------------------------------------------- the Cognitive Twin
@pytest.fixture
def clock(monkeypatch):
    """Engine time under test control, so two weeks can be simulated."""
    t = [utcnow()]
    monkeypatch.setattr("pcim.engine.utcnow", lambda: t[0])
    return t


def busy_week(ci):
    for task in ("Debug this: TypeError in parse_rows", "Write a Python function that parses CSV rows",
                 "Fix the failing test in utils.py", "Compare two papers on retrieval evaluation"):
        ci.ask_routed("u", task)
    for _ in range(3):
        ci.probe("u", "sql joins", True, assisted=True)
    for ok in (True, False, False):
        ci.probe("u", "sql joins", ok)


def test_the_weekly_review_is_evidence_based_and_makes_no_model_call(env, tmp_path):
    ci, recs = make(tmp_path)
    busy_week(ci)
    calls = sum(len(r.prompts) for r in recs.values())
    rv = CognitiveTwin(ci).review("u")
    assert sum(len(r.prompts) for r in recs.values()) == calls
    assert {"unverified_answers", "dependency_gap"} <= {c.rule for c in rv.challenges}
    assert [p["check"]["type"] for p in rv.priorities] == ["practice", "verified_ratio"]
    assert rv.priorities[0]["check"]["skill"] == "sql joins" and rv.name == "Nishanth 2.0"
    text = render(rv, 1)
    for heading in ("WHAT YOU DID", "WHERE YOU GOT STUCK", "WHAT'S NEXT", "CHALLENGE", "PRIORITY ADJUSTMENTS",
                    "SCORECARD"):
        assert heading in text
    assert "with help 3/3, alone 1/3" in text and "smarter" not in text.lower()
    sc = rv.scorecard
    assert sc["orchestration"]["routed"] == 4 and sc["cognitive"]["unassisted_attempts"] == 3
    assert sc["productivity"]["verified_success_rate"] is None  # no outcomes: no rate, not 0 or 100%


def test_next_weeks_review_holds_this_weeks_priorities_to_account(env, tmp_path, clock):
    ci, _ = make(tmp_path)
    busy_week(ci)
    twin = CognitiveTwin(ci)
    t0 = clock[0]
    v1 = twin.save("u", twin.review("u", now=t0 + dt.timedelta(hours=1)))
    assert twin.save("u", twin.review("u", now=t0 + dt.timedelta(hours=2))) == v1  # same week: replaced, not added
    clock[0] = t0 + dt.timedelta(days=7)
    for ok in (True, True, False):
        ci.probe("u", "sql joins", ok)
    rv2 = twin.review("u", now=t0 + dt.timedelta(days=7, hours=1))
    practice = next(a for a in rv2.accountability if "sql joins" in a["priority"])
    assert (practice["status"], practice["evidence"]) == ("kept", "3 unassisted attempts at sql joins, 2 correct")
    verify = next(a for a in rv2.accountability if "outcome" in a["priority"])
    assert verify["status"] == "n/a"  # no code or research requests this week to check
    v2 = twin.save("u", rv2)
    assert v2 == v1 + 1 and rv2.previous_version == v1
    assert "LAST WEEK'S PRIORITIES" in render(rv2) and "✓" in render(rv2)
    ci.activate_weekly("u", v1)
    assert ci.active_weekly("u")[0] == v1 and [s["version"] for s in ci.weekly_snapshots("u")] == [v1, v2]
    assert ci.forget_weekly("u") == 2 and ci.weekly_snapshots("u") == []


def test_a_week_with_no_records_says_so_instead_of_inventing_findings(tmp_path):
    ci = PersonalCI(tmp_path, seed=0)
    ci.onboard("u", "Ada")
    rv = CognitiveTwin(ci).review("u")
    assert rv.thin and rv.priorities == [] and [c.rule for c in rv.challenges] == ["no_evidence"]
    assert rv.name == "Ada 2.0"


def test_stale_next_steps_and_decisions_on_open_questions_are_challenged(env, tmp_path, monkeypatch):
    ci, _ = make(tmp_path)
    ws = Workspace(ci)
    p = ws.create_project("u", "Thesis", objective="finish chapter 3")
    step = ws.add_note("u", p.id, "next_step", "email the advisor about the dataset")
    ws.add_note("u", p.id, "open_question", "is the sample size enough?")
    later = utcnow() + dt.timedelta(days=10)
    monkeypatch.setattr("pcim.workspace.utcnow", lambda: later - dt.timedelta(days=1))
    ws.add_note("u", p.id, "decision", "use the 2024 cohort only")
    twin = CognitiveTwin(ci, ws)
    rv = twin.review("u", now=later)
    rules = {c.rule for c in rv.challenges}
    assert {"stale_next_steps", "decided_on_open_questions"} <= rules
    pr = next(x for x in rv.priorities if x["check"]["type"] == "note_resolved")
    assert pr["check"]["note_id"] == step["id"]
    assert twin.check(pr["check"], rv.facts, rv.since, rv.until) == ("missed", "still open")
    ws.resolve_note("u", p.id, step["id"])
    rv = twin.review("u", now=later)
    assert twin.check(pr["check"], rv.facts, rv.since, rv.until) == ("kept", "closed")


def test_a_proposed_growth_goal_changes_nothing_until_it_is_applied(env, tmp_path):
    ci, _ = make(tmp_path)
    ids = [ci.ask_routed("u", f"Fix the failing test in parser_{i}.py").interaction_id for i in range(4)]
    for i in ids[:3]:
        ci.record_outcome("u", i, worked=False)
    ci.record_outcome("u", ids[3], worked=True, corrected=True)
    twin = CognitiveTwin(ci)
    rv = twin.review("u")
    adj = [a for a in rv.adjustments if a["kind"] == "growth_goal"]
    assert adj and adj[0]["task_type"] == "code" and "4/4" in adj[0]["why"]
    assert ci._assist("u").growth_tasks == set()
    assert ci.resolve_mode("u", "Fix the failing test in x.py").mode == "execute"
    assert [a["task_type"] for a in twin.apply("u", rv)] == ["code"]
    assert ci.resolve_mode("u", "Fix the failing test in x.py").mode == "learn"


def test_the_deep_review_sends_only_what_the_chosen_vendor_may_see_and_changes_no_evidence(env, tmp_path):
    ci, recs = make(tmp_path, providers=("claude-cli", "mock"))
    ci.ask("u", "private question about my landlord dispute", provider="mock")  # answered by vendor mock
    ci.ask_routed("u", LIGHT)  # answered by vendor anthropic
    ws = Workspace(ci)
    ws.create_project("u", "Side project", objective="acquire the competitor quietly", providers=["mock"])
    twin = CognitiveTwin(ci, ws)
    rv = twin.review("u")
    text, withheld = twin.digest("u", rv, "anthropic")
    assert "429" in text and "landlord" not in text and "competitor" not in text
    assert withheld["requests_answered_by_other_vendors"] == 1 and withheld["projects_not_allowing_this_vendor"] == 1
    before = json.dumps({k: v for k, v in rv.snapshot().items() if k != "generated_at"}, sort_keys=True)
    out = twin.deep("u", rv)
    assert out["model"].startswith("Claude Opus") and out["text"] == "answer from opus"
    sent = recs["opus"].prompts[-1]
    assert "landlord" not in sent.system + sent.user and "competitor" not in sent.system + sent.user
    assert json.dumps({k: v for k, v in rv.snapshot().items() if k != "generated_at"}, sort_keys=True) == before
    assert "MODEL-WRITTEN CHALLENGE" in render(rv, deep=out) and "not evidence" in render(rv, deep=out)


# ---------------------------------------------------------------- the terminal
def test_ci_end_to_end_in_the_terminal(env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "home"))
    assert cli.main(["init", "--user", "nishanth", "--name", "Nishanth", "--providers", "claude-cli"]) == 0
    capsys.readouterr()
    assert terminal.main([LIGHT]) == 0
    out = capsys.readouterr().out
    assert "[stub claude-sonnet-5-5] ok" in out and "Claude Sonnet 5.5" in out and "execute" in out
    iid = re.search(r"ci outcome (int_[0-9a-f]+)", out).group(1)
    assert terminal.main(["outcome", iid, "worked"]) == 0
    assert terminal.main(["route", DEEP]) == 0
    assert "Claude Opus 5.5" in capsys.readouterr().out
    assert terminal.main(["practice", "sql", "joins", "right"]) == 0
    assert "sql joins: right, alone" in capsys.readouterr().out
    assert terminal.main(["mode", "learn"]) == 0
    assert terminal.main(["my key is sk-ant-" + "a" * 30 + " and it fails"]) == 3
    assert "credential" in capsys.readouterr().err
    assert terminal.main(["weekly", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["name"] == "Nishanth 2.0" and data["saved_version"] == 1
    assert terminal.main(["weekly", "--history"]) == 0 and "v1 (active)" in capsys.readouterr().out
    assert terminal.main(["status"]) == 0 and "mode learn" in capsys.readouterr().out
    calls = env.read_text().count("\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # not a terminal: --deep must not send unconfirmed
    assert terminal.main(["weekly", "--deep", "--dry-run"]) == 0
    assert "Not sent" in capsys.readouterr().err and env.read_text().count("\n") == calls


def test_ci_inside_an_authorized_workspace_uses_that_project_and_only_its_vendors(env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "home"))
    cli.main(["init", "--user", "nishanth", "--providers", "claude-cli"])
    ci, uid = terminal._open()
    ws = Workspace(ci)
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    p = ws.create_project(uid, "Repo", objective="ship it", providers=["claude-cli"])
    ws.authorize_workspace(uid, p.id, str(root))
    here = terminal._here(ci, uid, cwd=root / "src")
    assert (here.project_id, here.vendors) == (p.id, {"anthropic"})
    assert terminal._here(ci, uid, cwd=tmp_path) is None
    ws.revoke_provider(uid, p.id, "anthropic")
    monkeypatch.chdir(root / "src")
    capsys.readouterr()
    assert terminal.main(["route", LIGHT]) == 3
    assert "not authorized" in capsys.readouterr().out


def test_a_server_that_names_its_model_after_a_claude_model_does_not_inherit_its_tier(env, monkeypatch):
    monkeypatch.setenv("PCIM_LOCAL_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("PCIM_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1")
    local = next(e for e in routing.entries() if e.provider == "local")
    assert routing.capability(local).tier is None  # unrated until the person rates it
    cli_opus = next(e for e in routing.entries() if e.key == "claude-cli:opus")
    assert routing.capability(cli_opus).tier == 3
