"""Three scorecards, kept apart on purpose: productivity, cognitive development, orchestration.

They answer different questions and can move in opposite directions. CI doing more of the
work raises productivity and can lower independent performance; reporting them separately is
what lets that show. Every number comes from CI's own records for the window, with its count,
and a rate is None rather than a guess when there is nothing to divide by.

None of this is evidence that CI makes anyone more capable. That needs unassisted performance
compared over time (growth.independence_report), and even then it speaks only for the skills
practised. Productivity numbers here are not evidence that CI beats a baseline either: that is
the paired comparison in experiments/ (E1, E2).
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from .growth import wilson

ROUTED_TASK_TYPES = ("code", "research")  # where a recorded outcome is most checkable


def _when(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)


def window_interactions(ci, user_id: str, since: dt.datetime, until: dt.datetime) -> list[dict]:
    """Interactions in [since, until), decrypted. Filters on the plaintext timestamp first."""
    ids = [r["id"] for r in ci.store.list_interactions(user_id) if since <= _when(r["ts"]) < until]
    return [ci.store.get_interaction(i) for i in ids]


def window_tasks(ci, user_id: str, since: dt.datetime, until: dt.datetime) -> list[dict]:
    rows = ci.store.query("SELECT id, project_id, tool, status, created_at, approved_at, finished_at, duration_ms "
                          "FROM tasks WHERE user_id=?", (user_id,))
    return [dict(r) for r in rows if since <= _when(r["created_at"]) < until]


def _p50(xs: list[float]) -> float | None:
    return round(float(np.median(xs)), 1) if xs else None


def productivity(rows: list[dict], tasks: list[dict]) -> dict:
    fb = [(r, r.get("feedback") or {}) for r in rows]
    verified = [(r, f) for r, f in fb if f.get("task_success") is not None]
    wins = sum(1 for _, f in verified if f["task_success"])
    corrected = sum(1 for _, f in fb if f.get("corrected"))
    costs = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
    statuses: dict[str, int] = {}
    for t in tasks:
        statuses[t["status"]] = statuses.get(t["status"], 0) + 1
    approvals = sum(1 for t in tasks if t.get("approved_at"))
    denials = statuses.get("denied", 0)
    known_cost = round(sum(costs), 6)
    return {
        "requests": len(rows),
        "with_verified_outcome": len(verified),
        "verified_success_rate": round(wins / len(verified), 3) if verified else None,
        "verified_success_ci95": wilson(wins, len(verified)),
        "corrected": corrected,
        "tool_actions": statuses,
        "human_interventions": {"corrections": corrected, "approvals": approvals, "denials": denials},
        "cost_usd_known": known_cost, "cost_unknown_requests": len(rows) - len(costs),
        "cost_per_verified_success": round(known_cost / wins, 6) if wins else None,
        "latency_ms_p50": _p50([r["latency_ms"] for r in rows if r.get("latency_ms") is not None]),
        "tool_action_ms_p50": _p50([t["duration_ms"] for t in tasks if t.get("duration_ms") is not None]),
        "note": "Success rates count only requests with a recorded outcome (`ci outcome`); "
                "ratings and thumbs are not outcomes.",
    }


def cognitive(attempts: list[dict], report: dict, rows: list[dict]) -> dict:
    un = [a for a in attempts if not a["assisted"]]
    asst = [a for a in attempts if a["assisted"]]
    ku, ka = sum(a["correct"] for a in un), sum(a["correct"] for a in asst)
    flags: dict[str, list[str]] = {}
    for skill, s in report["skills"].items():
        for f in s["flags"]:
            flags.setdefault(f, []).append(skill)
    learn = sum(1 for r in rows if ((r.get("receipt") or {}).get("intelligence_mode") or {}).get("mode") == "learn")
    return {
        "unassisted_attempts": len(un), "unassisted_correct": ku,
        "unassisted_rate": round(ku / len(un), 3) if un else None, "unassisted_ci95": wilson(ku, len(un)),
        "assisted_attempts": len(asst), "assisted_rate": round(ka / len(asst), 3) if asst else None,
        "skills_practised_alone": sorted({a["skill"] for a in un}),
        "flags": flags, "learn_mode_requests": learn,
        "note": report["basis"],
    }


def orchestration(rows: list[dict], audit: list[dict]) -> dict:
    routed = [r for r in rows if (r.get("receipt") or {}).get("route")]
    recovered = [r for r in routed if r["receipt"]["route"].get("failed_before")]
    pinned = [r for r in routed if r["receipt"]["route"]["plan"].get("pinned")]
    below = [r for r in routed if r["receipt"]["route"].get("served_meets") is False]
    by_req: dict[str, int] = {}
    est, actual = 0.0, 0.0
    paired = 0
    for r in routed:
        rt = r["receipt"]["route"]
        t = rt["plan"]["requirement"]["tier"]
        by_req[t] = by_req.get(t, 0) + 1
        if rt.get("served_est_cost_usd") is not None and r.get("cost_usd") is not None:
            est += rt["served_est_cost_usd"]
            actual += r["cost_usd"]
            paired += 1
    by_model: dict[str, list] = {}
    for r in rows:
        if r.get("latency_ms") is not None:
            by_model.setdefault(f"{r['provider']}:{r['model']}", []).append(r["latency_ms"])
    plans = [a for a in audit if a["event"] == "route.plan"]
    refused = [a for a in plans if a["payload"].get("refused")]
    return {
        "requests": len(rows), "routed": len(routed), "pinned": len(pinned),
        "recovered_by_fallback": len(recovered),
        "route_refusals": len(refused),
        "refused_for_credentials": sum(1 for a in refused if a["payload"].get("credentials_found")),
        "served_below_requirement": len(below),
        "requirement_tiers": by_req,
        "estimate_vs_actual_cost_usd": ({"estimated": round(est, 6), "actual": round(actual, 6), "n": paired}
                                        if paired else None),
        "latency_ms_p50_by_model": {k: _p50(v) for k, v in by_model.items()},
        "note": "Routing quality against a fixed-model baseline needs graded live runs "
                "(experiments/routing/REPORT.md); these are operating counts only.",
    }


def scorecard(ci, user_id: str, since: dt.datetime, until: dt.datetime, rows: list[dict] | None = None) -> dict:
    rows = window_interactions(ci, user_id, since, until) if rows is None else rows
    attempts = [a for a in ci.attempts(user_id) if since <= _when(a["ts"]) < until]
    audit = [a for a in ci.audit.entries(user_id) if since <= _when(a["ts"]) < until]
    return {
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "productivity": productivity(rows, window_tasks(ci, user_id, since, until)),
        "cognitive": cognitive(attempts, ci.skill_report(user_id, until), rows),
        "orchestration": orchestration(rows, audit),
    }
