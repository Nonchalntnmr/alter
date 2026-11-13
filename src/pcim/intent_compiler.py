"""Intent compiler: a rough instruction → a typed, inspectable task specification.

    one word / shorthand / rough idea
      → routed model call in `plan` mode (consent-filtered personal context, model fallback)
      → parse + validate (this module; the model's JSON is never trusted as-is)
      → provenance check: a constraint "from your notes" must cite notes that were really sent
      → dependency graph check (unknown steps, cycles)
      → gate: ready | clarify | approval        (code, not the model)
      → sealed, versioned record you can inspect, answer, approve

The gate is the safety-relevant part, so it is deterministic and tested on its own:

- **clarify** when the goal itself is ambiguous (top reading below 0.6, or within 0.2 of the
  next), when any *consequential* assumption has confidence below 0.8, or when there is
  nothing to do. The fewest questions are surfaced; low-risk assumptions are resolved
  automatically and listed, never asked.
- **approval** when the plan contains steps that run code or change files (they will also
  pass the workspace governor at execution), or steps of kind `external`, which CI cannot
  perform at all: they are listed as yours to do.
- **ready** otherwise (think/read only).

Nothing here executes anything. Approving a plan does not authorize a tool: every step that
reaches a tool goes through the governor, which re-decides with the current mode and grants.
"""

from __future__ import annotations

import dataclasses
import json
import re

from .types import new_id, utcnow

PLANS_STATE = "pcim-plans"
KINDS = ("think", "read", "run", "write", "external")
CONSEQUENTIAL_KINDS = frozenset({"run", "write"})
GOAL_MIN_P = 0.6
GOAL_MIN_MARGIN = 0.2
ASSUMPTION_MIN_CONF = 0.8
MAX_STEPS = 20
LOG_MAX = 200
_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)
# Post hoc (added after the 2026-11-13 development run, where "tweet about the launch" was planned
# as drafting only and gated ready): words in the *request* that ask for an action outside CI.
# If the request has one and the plan has no external step, the plan cannot be ready.
# Deliberately broad: a false hit ("explain the order book") costs one approval, a miss costs a
# silently dropped action.
EXTERNAL_VERBS = re.compile(
    r"\b(send|e-?mail|mail|message|dm|post|tweet|publish|book|buy|purchase|order|pay|transfer|schedule|"
    r"invite|submit|upload|deploy|share|text me|call)\b", re.I)


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
    """Steps grouped into levels that may run in parallel. Raises SpecError on unknown deps or cycles."""
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


def validate(d: dict, sent_notes: list[str]) -> dict:
    """Normalize the model's object into a TaskSpec. `sent_notes` = ids of the notes the model saw,
    in the order they were numbered. Raises SpecError if the object is not a usable plan."""
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
        steps.append({"id": sid, "action": _str(s["action"], 500), "kind": kind, "depends_on": deps})
    if len(d.get("steps") or []) > MAX_STEPS:
        flags.append(f"plan truncated to {MAX_STEPS} steps")
    assumptions = []
    for a in d.get("assumptions") or []:
        if isinstance(a, dict) and _str(a.get("text")):
            conf = _clip01(a.get("confidence"))
            assumptions.append({"text": _str(a["text"]), "confidence": conf,
                                "consequential": bool(a.get("consequential"))})
    spec = {
        "goal": goal,
        "interpretations": interps[:5],
        "deliverables": [_str(x) for x in (d.get("deliverables") or []) if _str(x)][:10],
        "constraints": constraints[:15],
        "success_criteria": [_str(x) for x in (d.get("success_criteria") or []) if _str(x)][:10],
        "dependencies": [_str(x) for x in (d.get("dependencies") or []) if _str(x)][:10],
        "steps": steps,
        "assumptions": assumptions[:12],
        "questions": [_str(x, 300) for x in (d.get("questions") or []) if _str(x)][:6],
        "flags": flags,
    }
    spec["levels"] = topo_levels(steps) if steps else []
    return spec


@dataclasses.dataclass
class Gate:
    decision: str  # ready | clarify | approval
    reasons: list[str]
    questions: list[str]
    auto_resolved: list[str]
    yours_to_do: list[str]
    needs_approval: list[str]

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def gate(spec: dict, request: str | None = None) -> Gate:
    reasons, questions = [], list(spec.get("questions") or [])
    asked_external = EXTERNAL_VERBS.search(request or "")
    unplanned = bool(asked_external) and not any(s["kind"] == "external" for s in spec.get("steps", []))
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
    if not spec.get("steps"):
        reasons.append("the plan has no steps")
        if not questions:
            questions.append("What should the result be?")
    auto = [a["text"] for a in spec.get("assumptions", []) if a not in risky]
    yours = [f"{s['id']}: {s['action']}" for s in spec.get("steps", []) if s["kind"] == "external"]
    if unplanned:
        yours.append(f"your request says \"{asked_external.group(0)}\" but the plan has no step that does it; "
                     "CI cannot, so decide whether you will")
    approval = [f"{s['id']}: {s['action']}" for s in spec.get("steps", []) if s["kind"] in CONSEQUENTIAL_KINDS]
    if reasons:
        return Gate("clarify", reasons, questions[:6], auto, yours, approval)
    if approval or yours:
        why = []
        if approval:
            why.append(f"{len(approval)} step(s) run code or change files; each still passes the workspace governor")
        if yours:
            why.append(f"{len(yours)} step(s) act outside CI (send, buy, publish, delete, contact) and are yours to do")
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
    """Route `text` in plan mode, validate and gate the reply, and store the record."""
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
           "status": "invalid", "steps_state": {}}
    if raw is None:
        rec["error"] = "the model did not return a JSON plan"
    else:
        try:
            rec["spec"] = validate(raw, sent_notes)
            g = gate(rec["spec"], text)
            rec["gate"] = g.as_dict()
            rec["status"] = {"clarify": "needs_answers", "approval": "awaiting_approval", "ready": "ready"}[g.decision]
        except SpecError as e:
            rec["error"] = f"unusable plan: {e}"
    _save(ci, uid, rec)
    ci.audit.append(uid, "plan.compile", plan=rec["id"], interaction=r.interaction_id, status=rec["status"],
                    steps=len((rec["spec"] or {}).get("steps", [])), parent=parent)
    return rec


def answer(ci, uid: str, plan_id: str, answers: str, **kw) -> dict:
    prev = get(ci, uid, plan_id)
    return compile_intent(ci, uid, prev["text"], answers=answers, parent=prev["id"],
                          project_id=kw.pop("project_id", prev.get("project_id")), **kw)


def approve(ci, uid: str, plan_id: str) -> dict:
    rec = get(ci, uid, plan_id)
    if rec["status"] == "needs_answers":
        raise ValueError("this plan has open questions; answer them first (ci plan answer <id> \"…\")")
    if rec["status"] not in ("awaiting_approval", "ready"):
        raise ValueError(f"plan is {rec['status']}")
    rec["status"], rec["approved_at"] = "approved", utcnow().isoformat()
    _save(ci, uid, rec)
    ci.audit.append(uid, "plan.approve", plan=rec["id"])
    return rec


def render(rec: dict) -> str:
    L = [f"plan {rec['id']}  ·  {rec['status']}  ·  served by {rec.get('served_by') or '?'}"]
    if rec.get("error"):
        L.append(f"  ! {rec['error']}")
        return "\n".join(L)
    s, g = rec["spec"], rec["gate"]
    L.append(f"\nGoal: {s['goal']}")
    if len(s["interpretations"]) > 1:
        L.append("Readings: " + "; ".join(f"{i['goal']} (p={i['p'] if i['p'] is not None else '?'})"
                                          for i in s["interpretations"]))
    for title, key in (("Deliverables", "deliverables"), ("Success criteria", "success_criteria"),
                       ("Depends on", "dependencies")):
        if s[key]:
            L.append(f"{title}:\n" + "\n".join(f"  - {x}" for x in s[key]))
    if s["constraints"]:
        L.append("Constraints:\n" + "\n".join(f"  - {c['text']}  [{c['source']}]" for c in s["constraints"]))
    if s["steps"]:
        L.append("Steps (levels may run in parallel):")
        for n, lvl in enumerate(s["levels"], 1):
            for sid in lvl:
                st = next(x for x in s["steps"] if x["id"] == sid)
                dep = f" after {', '.join(st['depends_on'])}" if st["depends_on"] else ""
                L.append(f"  L{n} {sid} [{st['kind']}] {st['action']}{dep}")
    if g["auto_resolved"]:
        L.append("Assumed (low stakes, not asked):\n" + "\n".join(f"  - {a}" for a in g["auto_resolved"]))
    if s["flags"]:
        L.append("Checks:\n" + "\n".join(f"  ! {f}" for f in s["flags"]))
    L.append(f"\nDecision: {g['decision']} — " + "; ".join(g["reasons"]))
    if g["questions"]:
        L.append("Questions:\n" + "\n".join(f"  ? {q}" for q in g["questions"]))
        L.append(f"Answer with: ci plan answer {rec['id']} \"…\"")
    if g["yours_to_do"]:
        L.append("Yours to do (CI cannot):\n" + "\n".join(f"  - {x}" for x in g["yours_to_do"]))
    if g["decision"] != "clarify" and rec["status"] != "approved":
        L.append(f"Approve with: ci plan approve {rec['id']}")
    return "\n".join(L)
