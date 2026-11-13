"""Intent compiler: a rough instruction → a typed, inspectable task specification.

    one word / shorthand / rough idea
      → routed model call in `plan` mode (consent-filtered personal context, model fallback)
      → parse + validate (the model's JSON is never trusted as-is)
      → provenance: a constraint "from your notes" must cite notes that were really sent
      → intent preservation: every action the *request* asks for is an outcome of the plan
      → dependency graph check (unknown steps, cycles)
      → missing permissions, given the workspace the plan would run in
      → gate: ready | clarify | approval | blocked          (code, not the model)
      → sealed, versioned record you can inspect, answer, approve

Typed intent. A specification separates:
- **outcomes**: what the person asked to end up with or to have happened. Each is a
  `deliverable`, an `external_action` (send, post, book, buy, pay, schedule, submit, deploy,
  share, contact) or a `destructive_change` (delete, overwrite, reset). It quotes the
  request's own words.
- **steps**: the work. Each step serves outcomes, either as `preparatory` work (drafting the
  email) or as `fulfilling` work (the change itself).
- **assumptions**, **conflicts**, **missing permissions**, and **unsupported capabilities**:
  what CI cannot do at all. That covers every external action, because CI has no connectors
  that act in the world.
- **completion criteria**: the plan is complete only when every outcome is met. A deliverable
  counts as met through tool evidence; an external action counts only when the person marks it
  done. A model's claim never counts.

Intent preservation, the rule the 2026-11-13 development run motivated: "tweet about the
launch" was planned as drafting a tweet and marked ready, so the posting half disappeared. Now
the request is read deterministically for clauses that ask for an action (polite prefixes,
shorthand and one-letter typos tolerated; questions *about* actions are not requests). Any
requested action missing from the model's outcomes is restored as an outcome, flagged, and
leaves the plan not ready. A request to post never silently becomes a request to draft.

Gate:
- **clarify**: the goal is ambiguous (top reading below 0.6, or within 0.2 of the next), a
  consequential assumption has confidence below 0.8, constraints conflict, or there is nothing
  to do.
- **blocked**: the steps need permissions the workspace does not grant (no workspace, no test
  command, writes off).
- **approval**: there are steps that run code or change files, or outcomes that are external
  or destructive.
- **ready**: otherwise.

Nothing here executes anything. Approving a plan authorizes no tool: every tool call is decided
again by the workspace governor.
"""

from __future__ import annotations

import dataclasses
import json
import re

from .types import new_id, utcnow

PLANS_STATE = "pcim-plans"
KINDS = ("think", "read", "run", "write", "external")
OUTCOME_KINDS = ("deliverable", "external_action", "destructive_change")
CONSEQUENTIAL_KINDS = frozenset({"run", "write"})
GOAL_MIN_P = 0.6
GOAL_MIN_MARGIN = 0.2
ASSUMPTION_MIN_CONF = 0.8
MAX_STEPS = 20
MAX_OUTCOMES = 12
LOG_MAX = 200
_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)

# ---------------------------------------------------------------------- requested actions
# Verbs whose clause-initial use asks for something to be done outside CI. Lemmas only; inflections
# and one-edit typos are matched below. "push" counts: a git push publishes to a remote.
ACTION_VERBS = {
    "send": "send", "email": "email", "e-mail": "email", "mail": "email", "message": "message", "msg": "message",
    "dm": "message", "text": "message", "reply": "reply", "respond": "reply", "forward": "forward", "post": "post",
    "tweet": "post", "publish": "publish", "announce": "post", "book": "book", "reserve": "book", "buy": "buy",
    "purchase": "buy", "order": "buy", "pay": "pay", "venmo": "pay", "transfer": "pay", "refund": "pay",
    "schedule": "schedule", "invite": "invite", "submit": "submit", "upload": "submit", "deploy": "deploy",
    "ship": "deploy", "release": "deploy", "push": "deploy", "share": "share", "call": "contact", "ping": "contact",
    "notify": "contact", "cancel": "cancel", "unsubscribe": "cancel", "subscribe": "buy", "sign": "submit",
    "register": "submit", "rsvp": "reply", "apply": "submit",
}
DESTRUCTIVE_VERBS = {
    "delete": "delete", "remove": "delete", "rm": "delete", "wipe": "delete", "erase": "delete", "purge": "delete",
    "nuke": "delete", "trash": "delete", "drop": "delete", "truncate": "delete", "reset": "reset",
    "overwrite": "overwrite", "clear": "delete", "uninstall": "delete",
}
# Verbs with a common non-action sense. The action reading is kept unless the object shows the
# other sense ("order the list by date", "share your thoughts", "reset my expectations"). A miss
# here fails safe: it costs one approval, never a dropped action.
_OTHER_SENSE = {
    "order": re.compile(r"\b(by|alphabetical\w*|chronological\w*|ascending|descending)\b", re.I),
    "share": re.compile(r"^(your|my|some|any)\s+(thoughts|opinion|opinions|take|views|feedback|ideas)\b", re.I),
    "reset": re.compile(r"\b(expectations|mind|mindset|mood|focus)\b", re.I),
    "clear": re.compile(r"^(up\b|my head|the air)", re.I),
    "post": re.compile(r"^-"),
}
_POLITE = re.compile(
    r"^(?:(?:pls|plz|please|kindly|hey|hi|ok|okay|yo|so|also|now|then|next|finally|after that|afterwards|and then|"
    r"and|ci|can you|can u|could you|could u|would you|will you|i need you to|i want you to|i'd like you to|"
    r"go ahead and|quickly|just|actually|lets|let's|let us)[\s,:]+)+", re.I)
_CLAUSES = re.compile(r"(?:[.;!?\n]+|,\s*(?:and\s+)?then\s+|\s+and\s+then\s+|,\s+and\s+|\s+then\s+|\s+and\s+|,\s+)", re.I)
_INFORMATIONAL = re.compile(
    r"^(how|what|why|when|where|which|who|whats|what's|is|are|was|were|do|does|did|should|shall|would it|could it|"
    r"can i|could i|may i|explain|describe|summari[sz]e|tell me|define|compare|list|outline|teach|walk me|show me|"
    r"help me understand|what's the|whats the|is it|are there)\b", re.I)


def _osa(a: str, b: str) -> int:
    """Optimal string alignment distance (one transposition counts as one edit)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def _verb(word: str) -> tuple[str, str] | None:
    """(kind, lemma) for a clause-initial word, tolerating inflection and one-edit typos."""
    w = word.lower().strip("'\"`*_-")
    for suffix in ("ing", "ed", "es", "s"):
        if len(w) > len(suffix) + 2 and w.endswith(suffix) and w[: -len(suffix)] in ACTION_VERBS | DESTRUCTIVE_VERBS:
            w = w[: -len(suffix)]
            break
    if w in DESTRUCTIVE_VERBS:
        return "destructive_change", DESTRUCTIVE_VERBS[w]
    if w in ACTION_VERBS:
        return "external_action", ACTION_VERBS[w]
    if len(w) >= 4:
        for table, kind in ((DESTRUCTIVE_VERBS, "destructive_change"), (ACTION_VERBS, "external_action")):
            for v, lemma in table.items():
                if len(v) >= 4 and _osa(w, v) == 1:
                    return kind, lemma
    return None


def requested_actions(text: str) -> list[dict]:
    """Clauses of the request that ask for an external or destructive action to be *done*.

    "draft the abstract and send it to priya" → [send it to priya]. "how do i post to arxiv?" →
    [] (a question about an action is not a request for one).
    """
    out = []
    for clause in _CLAUSES.split(text or ""):
        c = _POLITE.sub("", clause.strip()).strip()
        if not c or _INFORMATIONAL.match(c):
            continue
        first, _, rest = c.partition(" ")
        hit = _verb(first)
        other = _OTHER_SENSE.get(first.lower())
        if hit and other and other.search(rest):
            continue
        if hit:
            out.append({"kind": hit[0], "lemma": hit[1], "verb": first.lower(), "object": rest.strip()[:200],
                        "clause": c[:240]})
    return out


# ---------------------------------------------------------------------- parsing and validation
class SpecError(ValueError):
    pass


def _clip01(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if 0.0 <= x <= 1.0 else None


def _str(x, n: int = 400) -> str:
    return " ".join(str(x).split())[:n] if x is not None else ""


def parse(text: str) -> dict | None:
    """The JSON object in a model reply, or None."""
    raw = (text or "").strip()
    m = _FENCE.search(raw)
    candidates = [m.group(1)] if m else []
    if "{" in raw and "}" in raw:
        candidates.append(raw[raw.index("{"): raw.rindex("}") + 1])
    for c in candidates:
        try:
            d = json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(d, dict):
            return d
    return None


def topo_levels(steps: list[dict]) -> list[list[str]]:
    """Steps grouped into levels whose members do not depend on each other. Raises SpecError on
    unknown dependencies or cycles."""
    ids = [s["id"] for s in steps]
    if len(set(ids)) != len(ids):
        raise SpecError("duplicate step ids")
    known = set(ids)
    deps = {s["id"]: set(s["depends_on"]) for s in steps}
    for sid, d in deps.items():
        unknown = d - known
        if unknown:
            raise SpecError(f"step {sid} depends on unknown steps {sorted(unknown)}")
        if sid in d:
            raise SpecError(f"step {sid} depends on itself")
    levels, done = [], set()
    while len(done) < len(ids):
        ready = [i for i in ids if i not in done and deps[i] <= done]
        if not ready:
            raise SpecError("the steps contain a dependency cycle")
        levels.append(ready)
        done |= set(ready)
    return levels


def _matches(outcome: dict, action: dict) -> bool:
    """Does a model outcome stand for this requested action? Same kind, and its text or quote uses
    the action's verb (any inflection or a one-edit typo of it)."""
    if outcome["kind"] != action["kind"]:
        return False
    words = re.findall(r"[a-z][a-z'-]*", (outcome["text"] + " " + outcome.get("quote", "")).lower())
    return any((_verb(w) or (None, None))[1] == action["lemma"] for w in words)


def preserve(spec: dict, request: str | None) -> dict:
    """Make every action the request asks for an outcome of the plan. Idempotent."""
    if not request or spec.get("_preserved_for") == request:
        return spec
    asked = requested_actions(request)
    spec["requested_actions"] = asked
    for a in asked:
        if any(_matches(o, a) for o in spec["outcomes"]):
            continue
        oid = f"oR{sum(1 for o in spec['outcomes'] if o.get('restored')) + 1}"
        spec["outcomes"].append({"id": oid, "text": a["clause"], "kind": a["kind"], "quote": a["clause"],
                                 "done_when": "", "restored": True})
        spec["flags"].append(f"intent preserved: the request asks to \"{a['clause'][:80]}\" and the plan had no "
                             f"outcome for it; CI restored it ({oid})")
    _annotate_outcomes(spec)
    spec["_preserved_for"] = request
    return spec


def _annotate_outcomes(spec: dict) -> None:
    """Who can complete each outcome, given what CI can do. CI has no external connectors."""
    served = {}
    for s in spec["steps"]:
        for o in s.get("serves", []):
            served.setdefault(o, []).append(s)
    unsupported = []
    for o in spec["outcomes"]:
        steps = served.get(o["id"], [])
        if o["kind"] == "external_action":
            o["by"] = "you"
            unsupported.append(f"{o['id']}: CI cannot do \"{o['text'][:80]}\"; it can only prepare it")
        elif o["kind"] == "destructive_change":
            o["by"] = "ci_with_approval" if any(s["kind"] in CONSEQUENTIAL_KINDS for s in steps) else "you"
        else:
            o["by"] = "ci"
    spec["unsupported"] = unsupported


def validate(d: dict, sent_notes: list[str], request: str | None = None) -> dict:
    """Normalize the model's object into a TaskSpec. `sent_notes` = ids of the notes the model saw,
    in the order they were numbered. With `request`, intent preservation runs too. Raises
    SpecError if the object is not a usable plan."""
    if not isinstance(d, dict):
        raise SpecError("not an object")
    goal = _str(d.get("goal"), 500)
    if not goal:
        raise SpecError("no goal")
    interps = []
    for it in d.get("interpretations") or []:
        if isinstance(it, dict) and _str(it.get("goal")):
            interps.append({"goal": _str(it["goal"], 300), "p": _clip01(it.get("p"))})
    interps.sort(key=lambda x: -(x["p"] or 0.0))
    flags: list[str] = []
    constraints = []
    for c in d.get("constraints") or []:
        if not (isinstance(c, dict) and _str(c.get("text"))):
            continue
        src = str(c.get("source", "assumed")).lower()
        src = src if src in ("request", "notes", "profile", "assumed") else "assumed"
        cited = []
        if src == "notes":
            for n in c.get("notes") or []:
                try:
                    k = int(n)
                except (TypeError, ValueError):
                    continue
                if 1 <= k <= len(sent_notes):
                    cited.append(sent_notes[k - 1])
            if not cited:  # claimed to come from the person's notes, cites nothing that was sent
                src = "assumed"
                flags.append(f"constraint presented as from your notes but citing none that were sent: {_str(c['text'], 80)}")
        constraints.append({"text": _str(c["text"]), "source": src, "item_ids": cited})
    outcomes = []
    for i, o in enumerate((d.get("outcomes") or [])[:MAX_OUTCOMES]):
        if not (isinstance(o, dict) and _str(o.get("text"))):
            continue
        kind = str(o.get("kind", "")).lower()
        if kind not in OUTCOME_KINDS:
            kind = "external_action"  # an unknown outcome kind is treated as the most restricted
            flags.append(f"outcome {o.get('id', i + 1)} had no valid kind; treated as external")
        outcomes.append({"id": _str(o.get("id") or f"o{i + 1}", 20) or f"o{i + 1}", "text": _str(o["text"], 300),
                         "kind": kind, "quote": _str(o.get("quote"), 240), "done_when": _str(o.get("done_when"), 300)})
    outcome_ids = {o["id"] for o in outcomes}
    steps = []
    for i, s in enumerate((d.get("steps") or [])[:MAX_STEPS]):
        if not (isinstance(s, dict) and _str(s.get("action"))):
            continue
        kind = str(s.get("kind", "")).lower()
        if kind not in KINDS:
            kind = "external"  # unknown kinds are treated as the most restricted, never the least
            flags.append(f"step {s.get('id', i + 1)} had no valid kind; treated as external")
        sid = _str(s.get("id") or f"s{i + 1}", 20) or f"s{i + 1}"
        deps = [_str(x, 20) for x in (s.get("depends_on") or []) if _str(x, 20)]
        serves = [_str(x, 20) for x in (s.get("serves") or []) if _str(x, 20) in outcome_ids]
        role = str(s.get("role", "")).lower()
        steps.append({"id": sid, "action": _str(s["action"], 500), "kind": kind, "depends_on": deps,
                      "serves": serves, "role": role if role in ("preparatory", "fulfilling") else "unstated"})
    if len(d.get("steps") or []) > MAX_STEPS:
        flags.append(f"plan truncated to {MAX_STEPS} steps")
    # An external step that serves no external outcome still means the person asked for an action.
    for s in steps:
        if s["kind"] == "external" and not any(o["kind"] == "external_action" for o in outcomes
                                               if o["id"] in s["serves"]):
            oid = f"oS{s['id']}"
            outcomes.append({"id": oid, "text": s["action"], "kind": "external_action", "quote": "", "done_when": ""})
            s["serves"] = s["serves"] + [oid]
    assumptions = []
    for a in d.get("assumptions") or []:
        if isinstance(a, dict) and _str(a.get("text")):
            assumptions.append({"text": _str(a["text"]), "confidence": _clip01(a.get("confidence")),
                                "consequential": bool(a.get("consequential"))})
    spec = {
        "goal": goal,
        "interpretations": interps[:5],
        "outcomes": outcomes,
        "deliverables": [_str(x) for x in (d.get("deliverables") or []) if _str(x)][:10],
        "constraints": constraints[:15],
        "conflicts": [_str(x, 300) for x in (d.get("conflicts") or []) if _str(x)][:6],
        "success_criteria": [_str(x) for x in (d.get("success_criteria") or []) if _str(x)][:10],
        "dependencies": [_str(x) for x in (d.get("dependencies") or []) if _str(x)][:10],
        "steps": steps,
        "assumptions": assumptions[:12],
        "questions": [_str(x, 300) for x in (d.get("questions") or []) if _str(x)][:6],
        "missing_permissions": [],
        "unsupported": [],
        "flags": flags,
    }
    spec["levels"] = topo_levels(steps) if steps else []
    _annotate_outcomes(spec)
    if request:
        preserve(spec, request)
    return spec


def missing_permissions(spec: dict, workspace: dict | None) -> list[str]:
    """What the steps need that the workspace does not grant. `workspace` is the briefing's
    workspace entry ({"can_run_tests", "can_write"}) or None when there is no authorized folder."""
    kinds = {s["kind"] for s in spec.get("steps", [])}
    out = []
    if kinds & {"read", "run", "write"} and workspace is None:
        out.append("no folder is authorized for this project; reading, running and writing will be refused "
                   "(run from an authorized project directory)")
    elif workspace is not None:
        if "run" in kinds and not workspace.get("can_run_tests"):
            out.append("a step runs code, but this workspace has no test command configured")
        if "write" in kinds and not workspace.get("can_write"):
            out.append("a step changes files, but writes are off for this workspace")
    return out


# ---------------------------------------------------------------------- the gate
@dataclasses.dataclass
class Gate:
    decision: str  # ready | clarify | approval | blocked
    reasons: list[str]
    questions: list[str]
    auto_resolved: list[str]
    yours_to_do: list[str]
    needs_approval: list[str]

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def gate(spec: dict, request: str | None = None) -> Gate:
    spec.setdefault("outcomes", [])
    spec.setdefault("flags", [])
    spec.setdefault("conflicts", [])
    spec.setdefault("missing_permissions", [])
    if request:
        preserve(spec, request)
    reasons, questions = [], list(spec.get("questions") or [])
    interps = spec.get("interpretations") or []
    top = interps[0]["p"] if interps and interps[0]["p"] is not None else None
    second = interps[1]["p"] if len(interps) > 1 and interps[1]["p"] is not None else 0.0
    if top is None and len(interps) > 1:
        reasons.append("several readings of the request, none with a probability")
    elif top is not None and top < GOAL_MIN_P:
        reasons.append(f"the most likely reading has p={top:.2f} (< {GOAL_MIN_P})")
    elif top is not None and len(interps) > 1 and top - second < GOAL_MIN_MARGIN:
        reasons.append(f"two readings are close (p={top:.2f} vs {second:.2f})")
    if reasons and not questions and len(interps) > 1:
        questions.append("Did you mean: " + " — or — ".join(i["goal"] for i in interps[:3]) + "?")
    risky = [a for a in spec.get("assumptions", []) if a["consequential"]
             and (a["confidence"] is None or a["confidence"] < ASSUMPTION_MIN_CONF)]
    for a in risky:
        reasons.append(f"consequential assumption at confidence "
                       f"{'unknown' if a['confidence'] is None else format(a['confidence'], '.2f')}: {a['text']}")
    if risky and not spec.get("questions"):
        questions += [f"Is this right: {a['text']}?" for a in risky[:3]]
    for c in spec["conflicts"]:
        reasons.append(f"conflicting constraints: {c}")
    if spec["conflicts"] and not spec.get("questions"):
        questions.append("These constraints cannot all hold — which one gives way? " + "; ".join(spec["conflicts"][:2]))
    if not spec.get("steps") and not spec["outcomes"]:
        reasons.append("the plan has no steps")
        if not questions:
            questions.append("What should the result be?")
    auto = [a["text"] for a in spec.get("assumptions", []) if a not in risky]
    yours = [f"{o['id']}: {o['text']}" + (" (restored from your request)" if o.get("restored") else "")
             for o in spec["outcomes"] if o.get("by") == "you"]
    approval = [f"{s['id']}: {s['action']}" for s in spec.get("steps", []) if s["kind"] in CONSEQUENTIAL_KINDS]
    if reasons:
        return Gate("clarify", reasons, questions[:6], auto, yours, approval)
    if spec["missing_permissions"]:
        return Gate("blocked", list(spec["missing_permissions"]), [], auto, yours, approval)
    if approval or yours or any(o["kind"] == "destructive_change" for o in spec["outcomes"]):
        why = []
        if approval:
            why.append(f"{len(approval)} step(s) run code or change files; each still passes the workspace governor")
        if yours:
            why.append(f"{len(yours)} outcome(s) need an action outside CI (send, post, book, buy, pay, schedule, "
                       "submit, deploy, contact) and are yours to complete")
        if any(o["kind"] == "destructive_change" for o in spec["outcomes"]):
            why.append("the request deletes, overwrites or resets something")
        return Gate("approval", why, [], auto, yours, approval)
    return Gate("ready", ["only thinking and reading"], [], auto, yours, approval)


# ---------------------------------------------------------------------- records
def _log(ci, uid: str) -> tuple[int | None, list]:
    return ci._state_json(uid, PLANS_STATE, [])


def _write(ci, uid: str, version: int | None, log: list) -> None:
    blob = json.dumps(log[-LOG_MAX:]).encode()
    if version is None:
        ci.store.save_policy_state(uid, PLANS_STATE, blob, note="intent compiler plans")
    else:
        ci.store.overwrite_policy_state(uid, PLANS_STATE, version, blob)


def records(ci, uid: str) -> list[dict]:
    return _log(ci, uid)[1]


def get(ci, uid: str, plan_id: str) -> dict:
    for r in records(ci, uid):
        if r["id"] == plan_id or r["id"].endswith(plan_id):
            return r
    raise KeyError(plan_id)


def _save(ci, uid: str, rec: dict) -> None:
    version, log = _log(ci, uid)
    log = [r for r in log if r["id"] != rec["id"]] + [rec]
    _write(ci, uid, version, log)


def compile_intent(ci, uid: str, text: str, *, answers: str | None = None, parent: str | None = None,
                   pinned: str | None = None, project_id: str | None = None,
                   project_vendors: set[str] | None = None) -> dict:
    """Route `text` in plan mode, validate, preserve intent and gate the reply, and store the record."""
    text = " ".join(str(text).split())
    if not text:
        raise ValueError("nothing to plan")
    request = text if not answers else f"{text}\n\nAnswers to your earlier questions: {' '.join(answers.split())}"
    r = ci.ask_routed(uid, request, mode="plan", pinned=pinned, project_id=project_id,
                      project_vendors=project_vendors)
    sent_notes = [s["id"] for s in r.receipt.get("sent", []) if s.get("where") == "notes"]
    raw = parse(r.text)
    rec = {"id": new_id("plan"), "ts": utcnow().isoformat(), "text": text, "answers": answers, "parent": parent,
           "interaction": r.interaction_id, "served_by": r.receipt.get("route", {}).get("served_by"),
           "project_id": project_id, "context_items": sent_notes, "spec": None, "gate": None, "error": None,
           "status": "invalid", "steps_state": {}, "outcomes_state": {}}
    if raw is None:
        rec["error"] = "the model did not return a JSON plan"
    else:
        try:
            rec["spec"] = validate(raw, sent_notes, request=text)  # preservation reads the person's words only
            ws = None
            if project_id is not None:
                from .workspace import Workspace
                ws = Workspace(ci).briefing(uid, project_id).get("workspace")
            rec["spec"]["missing_permissions"] = missing_permissions(rec["spec"], ws)
            g = gate(rec["spec"], text)
            rec["gate"] = g.as_dict()
            rec["status"] = {"clarify": "needs_answers", "approval": "awaiting_approval", "ready": "ready",
                             "blocked": "blocked"}[g.decision]
        except SpecError as e:
            rec["error"] = f"unusable plan: {e}"
    _save(ci, uid, rec)
    ci.audit.append(uid, "plan.compile", plan=rec["id"], interaction=r.interaction_id, status=rec["status"],
                    steps=len((rec["spec"] or {}).get("steps", [])),
                    restored=sum(1 for o in (rec["spec"] or {}).get("outcomes", []) if o.get("restored")),
                    parent=parent)
    return rec


def answer(ci, uid: str, plan_id: str, answers: str, **kw) -> dict:
    prev = get(ci, uid, plan_id)
    return compile_intent(ci, uid, prev["text"], answers=answers, parent=prev["id"],
                          project_id=kw.pop("project_id", prev.get("project_id")), **kw)


def approve(ci, uid: str, plan_id: str) -> dict:
    rec = get(ci, uid, plan_id)
    if rec["status"] == "needs_answers":
        raise ValueError("this plan has open questions; answer them first (ci plan answer <id> \"…\")")
    if rec["status"] == "blocked":
        raise ValueError("this plan needs permissions the workspace does not grant: "
                         + "; ".join(rec["spec"]["missing_permissions"]))
    if rec["status"] not in ("awaiting_approval", "ready"):
        raise ValueError(f"plan is {rec['status']}")
    rec["status"], rec["approved_at"] = "approved", utcnow().isoformat()
    _save(ci, uid, rec)
    ci.audit.append(uid, "plan.approve", plan=rec["id"])
    return rec


_BY = {"ci": "CI will do it", "ci_with_approval": "CI, with your approval", "you": "yours to do — CI cannot"}


def render(rec: dict) -> str:
    L = [f"plan {rec['id']}  ·  {rec['status']}  ·  served by {rec.get('served_by') or '?'}"]
    if rec.get("error"):
        L.append(f"  ! {rec['error']}")
        return "\n".join(L)
    s, g = rec["spec"], rec["gate"]
    ostate = rec.get("outcomes_state") or {}
    L.append(f"\nGoal: {s['goal']}")
    if len(s["interpretations"]) > 1:
        L.append("Readings: " + "; ".join(f"{i['goal']} (p={i['p'] if i['p'] is not None else '?'})"
                                          for i in s["interpretations"]))
    if s.get("outcomes"):
        L.append("Outcomes you asked for:")
        for o in s["outcomes"]:
            st = (ostate.get(o["id"]) or {}).get("status", "open")
            tag = " [restored from your request]" if o.get("restored") else ""
            L.append(f"  {o['id']} [{o['kind']}] {o['text']}{tag} — {_BY.get(o.get('by'), '?')} · {st}")
    for title, key in (("Success criteria", "success_criteria"), ("Depends on", "dependencies")):
        if s[key]:
            L.append(f"{title}:\n" + "\n".join(f"  - {x}" for x in s[key]))
    if s["constraints"]:
        L.append("Constraints:\n" + "\n".join(f"  - {c['text']}  [{c['source']}]" for c in s["constraints"]))
    if s.get("conflicts"):
        L.append("Conflicts:\n" + "\n".join(f"  ! {c}" for c in s["conflicts"]))
    if s["steps"]:
        L.append("Steps (a level's steps do not depend on each other):")
        for n, lvl in enumerate(s["levels"], 1):
            for sid in lvl:
                st = next(x for x in s["steps"] if x["id"] == sid)
                dep = f" after {', '.join(st['depends_on'])}" if st["depends_on"] else ""
                serves = f" → {', '.join(st['serves'])} ({st['role']})" if st.get("serves") else ""
                L.append(f"  L{n} {sid} [{st['kind']}] {st['action']}{dep}{serves}")
    if g["auto_resolved"]:
        L.append("Assumed (low stakes, not asked):\n" + "\n".join(f"  - {a}" for a in g["auto_resolved"]))
    for title, key in (("Missing permissions", "missing_permissions"), ("CI cannot", "unsupported")):
        if s.get(key):
            L.append(f"{title}:\n" + "\n".join(f"  ! {x}" for x in s[key]))
    if s["flags"]:
        L.append("Checks:\n" + "\n".join(f"  ! {f}" for f in s["flags"]))
    L.append(f"\nDecision: {g['decision']} — " + "; ".join(g["reasons"]))
    if g["questions"]:
        L.append("Questions:\n" + "\n".join(f"  ? {q}" for q in g["questions"]))
        L.append(f"Answer with: ci plan answer {rec['id']} \"…\"")
    if g["yours_to_do"]:
        L.append("Yours to complete (the plan is not done until you mark them):\n"
                 + "\n".join(f"  - {x}" for x in g["yours_to_do"]))
    if g["decision"] in ("ready", "approval") and rec["status"] != "approved":
        L.append(f"Approve with: ci plan approve {rec['id']}")
    return "\n".join(L)
