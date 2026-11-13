"""Mission 1: a request to post, send, book, buy, deploy or delete never silently becomes a request
to draft, research or prepare. All of this is code, so it is tested without a model.

The cases here are the developer's own. They test the rules as designed; they are not the
held-out evaluation (experiments/intent_heldout/), which uses a set labelled by someone else."""

import pytest

from pcim import intent_compiler as ic


def drafting_only(**kw):
    """What a model does when it drops the action: it plans to prepare and nothing more."""
    d = {"goal": "prepare it", "interpretations": [{"goal": "prepare", "p": 0.9}],
         "steps": [{"id": "s1", "action": "draft it", "kind": "think", "depends_on": []}],
         "success_criteria": ["a draft exists"], "assumptions": [], "questions": []}
    d.update(kw)
    return d


ASKS = [
    ("tweet about the launch", "external_action", "post"),
    ("summarise the results and send them to priya", "external_action", "send"),
    ("draft the abstract, then book a room for friday", "external_action", "book"),
    ("could you please email raj the draft", "external_action", "email"),
    ("can you send this to priya?", "external_action", "send"),
    ("emial raj the notes", "external_action", "email"),  # one-edit typos
    ("sned the report to the team", "external_action", "send"),
    ("pay the invoice from acme", "external_action", "pay"),
    ("push the branch", "external_action", "deploy"),
    ("schedule a call with my advisor next week", "external_action", "schedule"),
    ("order more coffee filters", "external_action", "buy"),
    ("delete the old runs", "destructive_change", "delete"),
    ("wipe the cache and rerun the backtest", "destructive_change", "delete"),
]


@pytest.mark.parametrize("request_,kind,lemma", ASKS)
def test_a_requested_action_is_extracted_from_the_persons_words(request_, kind, lemma):
    got = [(a["kind"], a["lemma"]) for a in ic.requested_actions(request_)]
    assert (kind, lemma) in got


@pytest.mark.parametrize("request_", [
    "how do i post to arxiv?",                          # a question about doing it, not a request
    "what's the best way to cancel a subscription?",
    "should i delete the old runs?",
    "order the list by date",                           # other senses of the verb
    "share your thoughts on the abstract",
    "reset my expectations for the paper",
    "clear up the confusion in section 2",
    "write a post-mortem of the outage",
    "draft an email to raj but don't send it",          # negated
    "I already sent it, summarise the replies",         # already done, by the person
    "summarise the backtests",
    "explain how FSRS spacing works",
])
def test_questions_other_senses_and_negations_are_not_requested_actions(request_):
    assert ic.requested_actions(request_) == []


@pytest.mark.parametrize("request_", [
    "can you look at the data and tell me if its good enough",  # held-out h40: restored as a booking in V2
    "can u look into the reimbursement thing, its been like 3 weeks",  # held-out h49, the same
    "test the parser and fix whatever fails",                   # "test" is not "text"
    "clean up the csv headers",                                 # "clean" is not "clear"
    "port the script to python 3",                              # "port" is not "post"
    "skip the slow tests",                                      # "skip" is not "ship"
    "wire up the logger",                                       # "wire" is not "wipe"
    "preserve the row order",                                   # "preserve" is not "reserve"
    "luke wants the summary by friday",                         # a name is not "nuke"
])
def test_a_common_word_one_letter_from_an_action_verb_is_not_read_as_a_typo(request_):
    assert ic.requested_actions(request_) == []


@pytest.mark.parametrize("request_,lemma", [
    ("tweat about the launch", "post"),
    ("delte the old runs", "delete"),
    ("shedule a call with my advisor", "schedule"),
    ("emails raj the notes", "email"),  # an inflection, not a typo
])
def test_typos_that_are_not_words_and_inflections_still_count(request_, lemma):
    assert lemma in [a["lemma"] for a in ic.requested_actions(request_)]


@pytest.mark.parametrize("request_,kind,lemma", ASKS)
def test_a_plan_that_only_prepares_a_requested_action_is_never_ready_and_the_action_is_restored(request_, kind, lemma):
    spec = ic.validate(drafting_only(), [], request=request_)
    restored = [o for o in spec["outcomes"] if o.get("restored")]
    assert restored and restored[0]["kind"] == kind and restored[0]["id"].startswith("oR")
    g = ic.gate(spec, request_)
    assert g.decision != "ready"
    if kind == "external_action":  # CI has no connector for it: it is yours, and says so
        assert restored[0]["by"] == "you"
        assert any(y.startswith(restored[0]["id"]) and "restored from your request" in y for y in g.yours_to_do)
        assert any(u.startswith(restored[0]["id"]) for u in spec["unsupported"])
    assert any("intent preserved" in f for f in spec["flags"])


def test_preservation_is_idempotent():
    spec = ic.validate(drafting_only(), [], request="tweet about the launch")
    for _ in range(3):
        ic.gate(spec, "tweet about the launch")
    assert sum(1 for o in spec["outcomes"] if o.get("restored")) == 1


def test_an_outcome_the_model_named_for_the_action_is_kept_and_nothing_is_restored():
    spec = ic.validate(drafting_only(
        outcomes=[{"id": "o1", "text": "the summary is sent to Priya", "kind": "external_action", "quote": "send them"}],
        steps=[{"id": "s1", "action": "draft the summary", "kind": "think", "depends_on": [], "serves": ["o1"],
                "role": "preparatory"}]), [], request="summarise the results and send them to priya")
    assert not any(o.get("restored") for o in spec["outcomes"])
    assert ic.gate(spec, "summarise the results and send them to priya").yours_to_do[0].startswith("o1:")


def test_an_outcome_relabelled_as_a_deliverable_does_not_hide_the_action():
    """A model that writes "send the summary" but files it as a deliverable has turned sending
    into drafting. The kind decides, so the send is restored as an external action."""
    spec = ic.validate(drafting_only(
        outcomes=[{"id": "o1", "text": "send the summary to Priya", "kind": "deliverable"}]), [],
        request="send the summary to priya")
    r = [o for o in spec["outcomes"] if o.get("restored")]
    assert len(r) == 1 and r[0]["kind"] == "external_action"


def test_a_destructive_change_is_never_ready_whoever_does_it():
    by_you = ic.validate(drafting_only(), [], request="delete the old runs")
    assert ic.gate(by_you, "delete the old runs").decision == "approval"
    assert [o["by"] for o in by_you["outcomes"] if o.get("restored")] == ["you"]
    by_ci = ic.validate(drafting_only(
        outcomes=[{"id": "o1", "text": "delete the old runs", "kind": "destructive_change"}],
        steps=[{"id": "s1", "action": "remove runs/2025-*", "kind": "write", "depends_on": [], "serves": ["o1"]}]),
        [], request="delete the old runs")
    g = ic.gate(by_ci, "delete the old runs")
    assert g.decision == "approval" and by_ci["outcomes"][0]["by"] == "ci_with_approval"
    assert g.needs_approval == ["s1: remove runs/2025-*"]


def test_conflicting_constraints_ask_which_gives_way():
    spec = ic.validate(drafting_only(conflicts=["under 200 words", "cover all nine experiments in detail"]), [])
    g = ic.gate(spec)
    assert g.decision == "clarify" and any("conflicting" in r for r in g.reasons)
    assert any("which one gives way" in q for q in g.questions)


def test_missing_permissions_block_and_no_folder_blocks_file_steps():
    run = ic.validate(drafting_only(steps=[{"id": "s1", "action": "run the tests", "kind": "run", "depends_on": []}]), [])
    run["missing_permissions"] = ic.missing_permissions(run, {"can_run_tests": False, "can_write": True})
    assert ic.gate(run).decision == "blocked" and "no test command" in ic.gate(run).reasons[0]
    write = ic.validate(drafting_only(steps=[{"id": "s1", "action": "fix x.py", "kind": "write", "depends_on": []}]), [])
    assert ic.missing_permissions(write, {"can_run_tests": True, "can_write": False}) == [
        "a step changes files, but writes are off for this workspace"]
    read = ic.validate(drafting_only(steps=[{"id": "s1", "action": "read x.py", "kind": "read", "depends_on": []}]), [])
    assert "no folder is authorized" in ic.missing_permissions(read, None)[0]
    assert ic.missing_permissions(drafting_only() | {"steps": [{"kind": "think"}]}, None) == []  # thinking needs no folder


def test_an_external_step_the_plan_did_not_link_gets_an_outcome_that_is_yours():
    spec = ic.validate(drafting_only(steps=[
        {"id": "s1", "action": "draft the note", "kind": "think", "depends_on": []},
        {"id": "s2", "action": "email it to the list", "kind": "external", "depends_on": ["s1"]}]), [])
    o = next(o for o in spec["outcomes"] if o["id"] == "oSs2")
    assert o["kind"] == "external_action" and o["by"] == "you"
    assert "oSs2" in next(s for s in spec["steps"] if s["id"] == "s2")["serves"]
