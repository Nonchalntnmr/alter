"""Adaptive routing for work in a workspace: direct, clarify or plan. Code, not a model call.

    request → signals (read from the words, deterministic) → direct | clarify | plan

What the evidence says, and so what this does by default:
- **direct**: hand the request to the governed agent.
  - Paired v1: direct execution verified 16/16 against step-by-step planning's 9/16.
  - Paired v2: 16/16 against V3 contract planning's 14/16, with less of the person's time on 15
    of 16 tasks, less cost and fewer calls.
  Planning has not beaten direct on any measured task, so direct is the default.
- **clarify**: ask one question first, when the request rests on something CI cannot see or has
  no real object:
  - a conversation or decision it was not part of ("the analysis we talked about");
  - a placeholder for the object ("grant stuff");
  - nothing to do ("help", "fix it").
  Acting would mean guessing. It asks at most once: after an answer, it never asks again.
- **plan**: show the list of changes for approval before anything is touched, only when the
  request deletes, overwrites or resets things whose scope is broad ("the old flags", "all the
  unused handlers"). The plan's own gate may then ask about the scope.

An action outside the computer (send, post, book, pay…) is never a reason to ask or to plan by
itself. CI cannot take such an action, so it is the person's; CI does its own part and says what
is left. Asking how the person should do their own part is what made V3 over-ask (held-out v2,
H3).

The router decides only the first move. It grants nothing:
- every tool call is still decided by the governor;
- writes and test runs still need approval in every mode but autonomous;
- no tool acts outside the computer.

The signals, and the costs and successes measured so far, are in `Route.signals`, which
`ci do --explain` prints.
"""

from __future__ import annotations

import dataclasses
import re

from .intent_compiler import requested_actions

DIRECT, CLARIFY, PLAN = "direct", "clarify", "plan"
ROUTES = (DIRECT, CLARIFY, PLAN)
ASSISTED = "assisted"  # a routing mode: triage with one plan compile (route_from_plan), not a route
# What `ci do` does when the person does not choose. "adaptive" acts on route(); "direct" always
# goes direct, and prints route()'s choice as advice. experiments/eval_v4 sets it, by the rule
# registered before its sets were read.
ROUTING_DEFAULT = "adaptive"

# What the paired runs measured per path, for --explain. Not used to decide: the rules below are.
MEASURED = {
    "direct": "paired v1 16/16, v2 16/16 verified; v2: $2.62 for 16 tasks, 102 calls",
    "plan": "paired v1 9/16 (V2.1), v2 14/16 (V3) verified; v2: $3.56, 126 calls, more person time on 15/16",
    "clarify": "not measured as a separate path before rc2",
}

# A file, path, identifier or quoted name: something the agent can look at to settle a reference.
_CONCRETE = re.compile(r"(`[^`]+`|\"[^\"]+\"|'[^']{2,}'|\b[\w.-]+/[\w./-]+|\b\w+\.(?:py|js|ts|md|txt|csv|json|"
                       r"ya?ml|toml|ini|cfg|html|css|sql|ipynb|xlsx?|docx?|pptx?|pdf)\b|\b[a-z]+_[a-z0-9_]+\b|"
                       r"\b[a-z]+[A-Z]\w*\b)")
# Shared context the assistant was not part of. Each pattern names the phrase to quote back.
_DANGLING = [
    re.compile(r"\b(?:(?:the|that|this|those|these)\s+(?:\w+\s+){0,3})?(?:we|you and i|u and i|you|u)\s+"
               r"(?:talked|spoke|discussed|chatted|agreed|decided|went over|mentioned|planned|said|suggested)"
               r"(?:\s+(?:about|on|before|earlier|yesterday|last \w+))?\b", re.I),
    re.compile(r"\b(?:like|as|same as)\s+(?:last time|before|usual|always|i said|we said|i mentioned|"
               r"discussed|agreed|planned|the other (?:day|time|week))\b", re.I),
    re.compile(r"\bthe usual(?:\s+\w+)?\b", re.I),
    re.compile(r"\byou know (?:the|which|what|who|where)\b(?:\s+\w+)?", re.I),
]
# A placeholder where the object should be.
_PLACEHOLDER = re.compile(r"\b(?:\w+\s+)?(?:stuff|thingy|thingies|whatever)\b|"
                          r"\b(?:the|that|this|those|these)\s+things?\b(?!\s+(?:where|that|which|with|about|in|"
                          r"on|for|from|like|we|you|i)\b)", re.I)
_FUNCTION = frozenset("""
a an the and or but so to of in on at for with from by about into onto up out it its it's this that these those
there here i i'm im me my mine you u your ur we our us they them their he she him her is are was were be been
being am do does did done doing can could would will shall should may might must just please pls plz kindly
hey hi ok okay yo thanks thx quick quickly asap now soon today tomorrow tmrw really very so too also then
help make get go fix sort handle deal work working finish thing things stuff something anything everything
""".split())
# Destructive verbs the request-action reader treats as everyday words ("clean the csv"); for the
# router they still mean that something goes away.
_DESTRUCTIVE_PHRASE = re.compile(r"\b(?:clean(?:\s+(?:up|out))?|prune|tidy(?:\s+up)?|get rid of|strip(?:\s+out)?|"
                                 r"scrap|throw (?:away|out)|clear(?:\s+out)?|cut|rip out|gut)\b", re.I)
_BROAD = re.compile(r"\b(?:all|every|everything|any|anything|old|older|oldest|stale|unused|obsolete|outdated|legacy|"
                    r"dead|extra|leftover|redundant|duplicate|duplicates|junk|clutter|cruft|the rest|whatever|"
                    r"no longer|not used|isn't used|aren't used|deprecated|temp|tmp|scratch)\b", re.I)


@dataclasses.dataclass
class Route:
    decision: str            # direct | clarify | plan
    reasons: list[str]
    question: str | None     # only for clarify
    yours: list[str]         # actions outside the computer the person keeps
    signals: dict

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z'-]+", text.lower()) if w not in _FUNCTION and len(w) > 2]


def signals(text: str) -> dict:
    """What the words of a request say about how to start. Deterministic, no model call."""
    text = " ".join(str(text).split())
    concrete = bool(_CONCRETE.search(text))
    dangling = None
    for p in _DANGLING:
        m = p.search(text)
        if m:
            dangling = m.group(0).strip()
            break
    if dangling and concrete:
        dangling = None        # "as discussed in docs/plan.md": the agent can read it
    ph = _PLACEHOLDER.search(text)
    placeholder = ph.group(0).strip() if ph and not concrete else None
    if placeholder and " " in placeholder and placeholder.split()[0].lower() in _FUNCTION - {"the", "that", "this",
                                                                                              "those", "these"}:
        placeholder = placeholder.split(None, 1)[1]
    words = _content_words(text)
    actions = requested_actions(text)
    external = [a for a in actions if a["kind"] == "external_action"]
    destructive = [a for a in actions if a["kind"] == "destructive_change"]
    m = _DESTRUCTIVE_PHRASE.search(text)
    destructive_clauses = [a["clause"] for a in destructive] + ([text[m.start():]] if m else [])
    broad = next((b.group(0) for c in destructive_clauses for b in [_BROAD.search(c)] if b), None)
    return {
        "dangling_reference": dangling,
        "placeholder": placeholder,
        "no_task": len(words) < 2 and not concrete,
        "concrete_reference": concrete,
        "external_actions": [f"{a['verb']} {a['object']}".strip() for a in external],
        "destructive": bool(destructive_clauses),
        "broad_scope": broad,
        "content_words": len(words),
    }


def route(text: str, *, answered: bool = False, answers: str | None = None) -> Route:
    """The first move for a request. After an answer (`answered`), never clarify again."""
    s = signals(text if not answers else f"{text} {answers}")
    first = signals(text)
    yours = s["external_actions"]
    if not answered:
        if first["dangling_reference"]:
            q = (f"You mentioned “{first['dangling_reference']}”, but I wasn't part of that and have no notes on "
                 "it. What is it, or which file has it?")
            return Route(CLARIFY, [f"refers to something CI was not given: “{first['dangling_reference']}”"], q, yours, first)
        if first["no_task"]:
            return Route(CLARIFY, ["there is nothing specific to do yet"],
                         "What would you like done? Name the file, the change, or the result you want.", yours, first)
        if first["placeholder"]:
            q = f"What do you mean by “{first['placeholder']}”? Tell me which files, or what result you want."
            return Route(CLARIFY, [f"a placeholder stands where the object should be: “{first['placeholder']}”"], q,
                         yours, first)
    # after an answer, the scope is judged on what the answer settled
    scope = s if answered else first
    if scope["destructive"] and scope["broad_scope"] and not (answered and signals(answers or "")["concrete_reference"]):
        return Route(PLAN, [f"it removes or overwrites things with a broad scope (“{scope['broad_scope']}”): "
                            "you see the list and approve it before anything is touched"], None, yours, s)
    why = ["clear enough to start; each write and test run still needs your approval"]
    if yours:
        why.append(f"{len(yours)} action(s) outside the computer stay yours; CI does its part and says what is left")
    return Route(DIRECT, why, None, yours, s)


def route_from_plan(rec: dict | None, *, answered: bool = False) -> Route:
    """Planning-assisted routing: the first move, read from one compiled V3.3 plan.

    The keyword rules above miss most ambiguity: in held-out v3 they asked on 4 of the 21
    requests that needed a question, while V3.3's compiler asked on 14 of the 17 they missed.
    So this mode spends one plan compile (about $0.035 and 25 s on CLI 2.1.316) to triage:
    - the plan's gate asks (`clarify`) → ask its questions, once;
    - an outcome removes, overwrites or resets something, or the plan is blocked → plan: show
      it for approval;
    - otherwise → direct: the agent gets the request. The plan's outside actions are the
      person's, and are listed.
    A compile that fails (an invalid plan) goes direct. The agent is governed either way.
    """
    if not rec or rec.get("status") == "invalid" or not rec.get("spec") or not rec.get("gate"):
        why = (rec or {}).get("error") or "no plan"
        return Route(DIRECT, [f"the planning triage failed ({why}); going direct, and every write still waits "
                              "for your approval"], None, [], {"plan": (rec or {}).get("id"), "gate": None})
    g, s = rec["gate"], rec["spec"]
    sig = {"plan": rec["id"], "gate": g["decision"], "assisted": True}
    yours = [y.split(": ", 1)[-1] for y in g.get("yours_to_do") or []]
    destructive = [o for o in s.get("outcomes", []) if o.get("kind") == "destructive_change"]
    if g["decision"] == "clarify" and not answered:
        q = "\n".join(g.get("questions") or []) or "What should the result be?"
        return Route(CLARIFY, ["the plan has something only you can settle: " + "; ".join(g.get("reasons") or [])],
                     q, yours, sig)
    if destructive or g["decision"] == "blocked":
        what = destructive[0]["text"] if destructive else "; ".join(g.get("reasons") or [])
        return Route(PLAN, [f"it removes, overwrites or resets something ({what}): you see the plan and approve it "
                            "before anything is touched" if destructive else f"the plan is blocked: {what}"],
                     None, yours, sig)
    why = ["the plan has nothing to ask and nothing destructive; each write and test run still needs your approval"]
    if yours:
        why.append(f"{len(yours)} action(s) outside the computer stay yours; CI does its part and says what is left")
    return Route(DIRECT, why, None, yours, sig)
