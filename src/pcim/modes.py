"""The three intelligence modes: EXECUTE, AMPLIFY, LEARN — how CI balances doing and teaching.

They sit on top of the assistance styles in assist.py rather than beside them:

    execute → the `direct` style. Finish the work; no teaching the person did not ask for.
    amplify → the `amplify` style: recommendation, the strongest alternative, the trade-off,
              and any assumption the evidence does not support. For decisions, not chores.
    learn   → a growth style (hint first, Socratic, explain) chosen by the existing bandit
              among those only, plus one active-recall question at the end.
    challenge → adversarial reasoning (`ci challenge`): assumptions, counterexamples, the model's
              uncertainty estimate, and the cheapest experiment that could falsify the idea, in a
              structured form challenge.py parses and tracks to its outcome.

Which mode applies, in order:
  1. the mode named on this request (`ci -l ...`): an explicit choice always wins;
  2. learn, when the task type is one the person marked as a growth goal. A direct answer
     would win on speed and lose on the thing they said they wanted (assist.py);
  3. the person's configured default (`ci mode amplify`), else execute.

What a mode never touches: autonomy, approvals and tool permissions. Those belong to the
workspace's autonomy mode and the governor (workspace.py, governor.py). A mode changes how CI
answers, never what it is allowed to do or send; routing still applies every egress and
authorization filter (routing.py), and a mode only raises the capability floor.
"""

from __future__ import annotations

import dataclasses

from .assist import GROWTH_MODES

MODES = ("execute", "amplify", "learn", "challenge", "plan")
# Operations, not ways of helping: chosen by a command (`ci plan`), never as a default.
INTERNAL_MODES = frozenset({"plan"})
DEFAULT_MODE = "execute"
ALIASES = {"e": "execute", "x": "execute", "exec": "execute", "a": "amplify", "amp": "amplify",
           "l": "learn", "teach": "learn", "c": "challenge", "adversarial": "challenge"}

LEARN_DIRECTIVE = ("End with one short question the person can answer without help, so they can check their "
                   "own understanding before relying on it.")
EXECUTE_DIRECTIVE = "Do not add explanations or lessons the person did not ask for."
# The output shape challenge.py parses. The probability is the model's estimate and is shown as
# such; nothing downstream treats it as a measurement.
CHALLENGE_FORMAT = (
    "Reply with ONE JSON object and nothing else: {\"claim\": the idea restated in one sentence as strongly as the "
    "person would put it, \"assumptions\": [{\"text\", \"load_bearing\": \"high\"|\"medium\"|\"low\", "
    "\"evidence_given\": what in the request supports it, or \"none\"}], \"counterexamples\": [concrete cases where "
    "the claim fails or the plan backfires], \"strongest_objection\": string, \"uncertainty\": {\"p_claim_holds\": "
    "a number from 0 to 1, \"drivers\": [what that probability depends on most]}, \"falsification\": "
    "{\"experiment\": the cheapest test that could show the claim is wrong, \"if_true\": what you would expect to "
    "see if it holds, \"if_false\": what you would expect if it does not, \"cost\": rough time or money}, "
    "\"what_would_change_my_mind\": string, \"verdict\": one or two sentences}. List at least two assumptions and "
    "one counterexample. Mark an assumption high only if the claim fails without it. Never invent facts about the "
    "person; work only from what the request and the provided context say.")


# The task specification intent_compiler.py parses and gates. Confidences are the model's
# estimates; the gate that decides what happens next is code, not the model.
PLAN_FORMAT = (
    "The request may be one word, shorthand or a rough idea. Do not do the work: reconstruct what the person most "
    "likely wants and plan it. Reply with ONE JSON object and nothing else: {\"interpretations\": [{\"goal\": "
    "string, \"p\": probability 0 to 1}] (the readings you considered, most likely first, p summing to at most 1), "
    "\"goal\": the most likely goal in one sentence, \"outcomes\": [{\"id\": \"o1\", \"text\": what the person "
    "asked to end up with or to have happened, \"kind\": \"deliverable\"|\"external_action\"|"
    "\"destructive_change\", \"quote\": the words of the request it comes from, \"done_when\": a checkable "
    "condition}] (an external_action is anything that sends, posts, publishes, books, buys, pays, schedules, "
    "submits, deploys, shares or contacts someone; a destructive_change deletes, overwrites or resets something; "
    "keep every action the person asked for as an outcome even if you can only prepare it), \"deliverables\": "
    "[what the person ends up with], \"constraints\": [{\"text\", \"source\": \"request\"|\"notes\"|"
    "\"profile\"|\"assumed\", \"notes\": [the numbers of the notes it comes from, only for source notes]}], "
    "\"conflicts\": [constraints in the request that cannot both hold], \"success_criteria\": [checkable "
    "statements], \"dependencies\": [what must exist or be true first], \"steps\": [{\"id\": \"s1\", "
    "\"action\": string, \"kind\": \"think\"|\"read\"|\"run\"|\"write\"|\"external\", \"serves\": "
    "[outcome ids], \"role\": \"preparatory\"|\"fulfilling\", \"depends_on\": [step ids]}], \"assumptions\": "
    "[{\"text\", \"confidence\": 0 to 1, \"consequential\": true if acting on it wrongly would cause an "
    "irreversible/destructive change, spend money, or contact/act externally (never mark routine code, "
    "file-location, or implementation-detail questions as consequential inside a project: plan a read step to "
    "inspect the code instead), \"about\": [the ids of the outcomes it affects]}] (CI cannot send, post, book, "
    "pay or contact anyone, so an external_action is the person's to do; an assumption that only matters to how "
    "the person does their own part, such as which channel, which address or which payment method, goes in "
    "assumptions with its about, and is not a question), \"questions\": [only the fewest questions that settle truly consequential "
    "ambiguity; omit questions when reading the code can answer them]}. Kinds: think = reasoning or writing an "
    "answer; read = reading the person's files; run = running code or tests; write = creating or changing files; "
    "external = doing an external_action. Keep steps minimal and direct: do only what the request asks. Do not add "
    "backup or copy steps, steps that run deletions or resets on existing data, temporary scratch scripts, or outcomes "
    "and prerequisites the request does not ask for; a feature that deletes when used is a deliverable, not a "
    "destructive_change. Drafting something is preparatory, never a replacement for sending or "
    "posting it. You cannot see the person's files while planning: CI's agent reads them in the person's "
    "authorized project folder when it executes, so plan read steps instead of asking where files are. Any "
    "working directory you are told about is your own process's and has nothing to do with the project. "
    "Never invent facts about the person; cite a note only if it says what you claim.")


def normalize(mode: str | None) -> str | None:
    if mode is None:
        return None
    m = ALIASES.get(mode.strip().lower(), mode.strip().lower())
    if m not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    return m


@dataclasses.dataclass(frozen=True)
class ModeDecision:
    mode: str
    source: str  # request | growth_goal | default
    why: str
    assist_mode: str | None  # a fixed assistance style, or None: the bandit picks among `among`
    among: frozenset[str] | None
    directives: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"mode": self.mode, "source": self.source, "why": self.why}


def resolve(requested: str | None, default: str | None, task_type: str, growth_tasks: set[str]) -> ModeDecision:
    req = normalize(requested)
    if req:
        mode, source, why = req, "request", "you asked for it on this request"
    elif task_type in growth_tasks:
        mode, source, why = "learn", "growth_goal", f"you marked {task_type} as something you want to get better at"
    else:
        mode = normalize(default) or DEFAULT_MODE
        source, why = "default", "your default mode" if default else "the built-in default"
    if mode == "execute":
        return ModeDecision(mode, source, why, "direct", None, (EXECUTE_DIRECTIVE,))
    if mode == "amplify":
        return ModeDecision(mode, source, why, "amplify", None, ())
    if mode == "challenge":
        return ModeDecision(mode, source, why, "challenge", None, (CHALLENGE_FORMAT,))
    if mode == "plan":
        return ModeDecision(mode, source, why, "plan", None, (PLAN_FORMAT,))
    return ModeDecision(mode, source, why, None, GROWTH_MODES, (LEARN_DIRECTIVE,))
