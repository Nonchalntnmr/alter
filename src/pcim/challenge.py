"""`ci challenge`: evidence-driven adversarial reasoning, tracked to an outcome.

Instead of agreeing with the person's conclusion, CI finds the assumptions it rests on, builds
counterexamples, states how uncertain it is, and proposes the cheapest experiment that could
show it is wrong. The request is an ordinary routed request in the `challenge` mode
(modes.py): the same routing, consent, egress, credential tripwire and audit as any other, at
the deep tier by default (a weak challenger produces false confidence), and no mode can widen
what CI may do or send.

What makes it more than a prompt is the loop after it. Each challenge is logged (sealed, in the
person's own store, bounded), and the person records what happened when they ran the
experiment: `ci challenge --resolve <id> held|failed|inconclusive`. That gives three measurable
things, reported in the decision section of the scorecard (evaluation.py):

1. follow-through: how many challenged ideas were actually tested;
2. outcomes: how many held up and how many failed;
3. the challenger's calibration: the Brier score of the model's `p_claim_holds` against the
   recorded outcomes. This scores the model, not the person, and is shown only with enough
   resolved challenges.

The probability is always labelled as the model's estimate. Nothing here measures anyone's
judgment or intelligence; it records what was tested and what happened.
"""

from __future__ import annotations

import json
import re

from .engine import CHALLENGES_STATE
from .types import new_id, utcnow

LOG_MAX = 500
OUTCOMES = ("held", "failed", "inconclusive")
MIN_FOR_CALIBRATION = 10
_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)


def parse(text: str) -> dict | None:
    """The structured challenge, or None if the reply does not have the required shape."""
    raw = (text or "").strip()
    m = _FENCE.search(raw)
    candidates = [m.group(1)] if m else []
    if "{" in raw:
        candidates.append(raw[raw.index("{"): raw.rindex("}") + 1])
    for c in candidates:
        try:
            d = json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(d, dict):
            return _validate(d)
    return None


def _validate(d: dict) -> dict | None:
    claim = d.get("claim")
    assumptions = [a for a in d.get("assumptions") or [] if isinstance(a, dict) and str(a.get("text", "")).strip()]
    counter = [str(c) for c in d.get("counterexamples") or [] if str(c).strip()]
    fals = d.get("falsification") if isinstance(d.get("falsification"), dict) else {}
    if not (isinstance(claim, str) and claim.strip() and assumptions and counter and str(fals.get("experiment", "")).strip()):
        return None
    unc = d.get("uncertainty") if isinstance(d.get("uncertainty"), dict) else {}
    p = unc.get("p_claim_holds")
    try:
        p = float(p)
        p = p if 0.0 <= p <= 1.0 else None
    except (TypeError, ValueError):
        p = None
    lb = lambda a: str(a.get("load_bearing", "")).lower() if str(a.get("load_bearing", "")).lower() in ("high", "medium", "low") else "unrated"  # noqa: E731
    return {
        "claim": claim.strip()[:500],
        "assumptions": [{"text": str(a["text"]).strip()[:400], "load_bearing": lb(a),
                         "evidence_given": str(a.get("evidence_given", "none"))[:300]} for a in assumptions[:8]],
        "counterexamples": [c[:400] for c in counter[:6]],
        "strongest_objection": str(d.get("strongest_objection", ""))[:500],
        "uncertainty": {"p_claim_holds": p, "drivers": [str(x)[:200] for x in (unc.get("drivers") or [])[:5]],
                        "label": "the model's estimate, not a measurement"},
        "falsification": {k: str(fals.get(k, ""))[:400] for k in ("experiment", "if_true", "if_false", "cost")},
        "what_would_change_my_mind": str(d.get("what_would_change_my_mind", ""))[:400],
        "verdict": str(d.get("verdict", ""))[:500],
        "complete": len(assumptions) >= 2 and bool(counter) and p is not None,
    }


# ---------------------------------------------------------------------- the log
def _log(ci, uid: str) -> tuple[int | None, list]:
    return ci._state_json(uid, CHALLENGES_STATE, [])


def _write(ci, uid: str, version: int | None, log: list) -> None:
    blob = json.dumps(log[-LOG_MAX:]).encode()
    if version is None:
        ci.store.save_policy_state(uid, CHALLENGES_STATE, blob, note="challenge log")
    else:
        ci.store.overwrite_policy_state(uid, CHALLENGES_STATE, version, blob)


def challenge(ci, uid: str, claim: str, *, pinned: str | None = None, project_id: str | None = None,
              project_vendors: set[str] | None = None) -> dict:
    """Route the claim in challenge mode, parse the reply, log it. Raises RoutingError like ask_routed."""
    r = ci.ask_routed(uid, claim, mode="challenge", pinned=pinned, project_id=project_id,
                      project_vendors=project_vendors)
    parsed = parse(r.text)
    rec = {"id": new_id("ch"), "ts": utcnow().isoformat(), "interaction": r.interaction_id,
           "claim": " ".join(claim.split())[:500], "parsed": parsed is not None, "challenge": parsed,
           "served_by": r.receipt["route"]["served_by"], "status": "open", "resolved_at": None, "note": None}
    version, log = _log(ci, uid)
    _write(ci, uid, version, log + [rec])
    ci.audit.append(uid, "challenge.new", challenge=rec["id"], interaction=r.interaction_id, parsed=rec["parsed"])
    return rec | {"text": r.text, "receipt": r.receipt}


def resolve(ci, uid: str, challenge_id: str, outcome: str, note: str | None = None) -> dict:
    """Record what happened when the person ran the experiment."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    version, log = _log(ci, uid)
    for rec in log:
        if rec["id"] == challenge_id:
            rec.update(status=outcome, resolved_at=utcnow().isoformat(), note=(note or "")[:500] or None)
            _write(ci, uid, version, log)
            ci.audit.append(uid, "challenge.resolve", challenge=challenge_id, outcome=outcome)
            return rec
    raise KeyError(challenge_id)


def records(ci, uid: str) -> list[dict]:
    return _log(ci, uid)[1]


def forget(ci, uid: str) -> int:
    n = len(records(ci, uid))
    ci.store.delete_policy_states(uid, CHALLENGES_STATE)
    ci.audit.append(uid, "challenge.forget", records=n)
    return n


def metrics(recs: list[dict]) -> dict:
    """Decision-quality numbers from the log: follow-through, outcomes, the challenger's calibration."""
    n = len(recs)
    parsed = [r for r in recs if r["parsed"]]
    complete = [r for r in parsed if r["challenge"]["complete"]]
    resolved = [r for r in recs if r["status"] != "open"]
    decided = [r for r in resolved if r["status"] in ("held", "failed")]
    scored = [r for r in decided if r["parsed"] and r["challenge"]["uncertainty"]["p_claim_holds"] is not None]
    brier = None
    if len(scored) >= MIN_FOR_CALIBRATION:
        brier = round(sum((r["challenge"]["uncertainty"]["p_claim_holds"] - (1.0 if r["status"] == "held" else 0.0)) ** 2
                          for r in scored) / len(scored), 4)
    return {
        "challenges": n,
        "structured": len(parsed), "complete": len(complete),
        "experiments_resolved": len(resolved),
        "follow_through_rate": round(len(resolved) / n, 3) if n else None,
        "held": sum(1 for r in resolved if r["status"] == "held"),
        "failed": sum(1 for r in resolved if r["status"] == "failed"),
        "inconclusive": sum(1 for r in resolved if r["status"] == "inconclusive"),
        "challenger_brier": brier,
        "challenger_brier_n": len(scored),
        "note": ("Follow-through and outcomes are what you recorded. The Brier score rates the model's probability "
                 f"estimates against those outcomes, and needs at least {MIN_FOR_CALIBRATION} decided challenges."),
    }


def render(rec: dict) -> str:
    c = rec.get("challenge")
    if not c:
        return ((rec.get("text") or "").strip() + "\n\n(The reply did not have the challenge structure, so it is "
                "shown as is and its experiment cannot be tracked.)")
    w = [f"CLAIM  {c['claim']}"]
    w.append("\nASSUMPTIONS (high = the claim fails without it)")
    w += [f"  [{a['load_bearing']}] {a['text']}  (evidence given: {a['evidence_given']})" for a in c["assumptions"]]
    w.append("\nCOUNTEREXAMPLES")
    w += [f"  • {x}" for x in c["counterexamples"]]
    if c["strongest_objection"]:
        w.append(f"\nSTRONGEST OBJECTION  {c['strongest_objection']}")
    p = c["uncertainty"]["p_claim_holds"]
    w.append(f"\nUNCERTAINTY  {'not given' if p is None else f'{p:.0%}'} that the claim holds: {c['uncertainty']['label']}"
             + (f"; depends on {', '.join(c['uncertainty']['drivers'])}" if c["uncertainty"]["drivers"] else ""))
    f = c["falsification"]
    w.append(f"\nCHEAPEST EXPERIMENT THAT COULD PROVE IT WRONG  {f['experiment']}"
             + (f" ({f['cost']})" if f["cost"] else ""))
    if f["if_true"] or f["if_false"]:
        w.append(f"  if it holds: {f['if_true']}\n  if it doesn't: {f['if_false']}")
    if c["what_would_change_my_mind"]:
        w.append(f"\nWHAT WOULD CHANGE MY MIND  {c['what_would_change_my_mind']}")
    if c["verdict"]:
        w.append(f"\nVERDICT  {c['verdict']}")
    return "\n".join(w)
