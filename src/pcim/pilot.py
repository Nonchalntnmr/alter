"""`alter report`: the pilot's metrics, computed from ALTER's own records. No model call.

What it reads, all on this machine:
- **goals** (`goals.GoalStore`): status, verdict, checkpoints, session costs, and the interruptions
  a goal records when it is picked up after its worker died;
- **the tasks of each goal's sessions** (`tasks.TaskQueue`, grouped as `goals.evidence` groups them).
  An action that needs permission (a write or a test run) ran either under an autonomy grant (the
  task's `authorization_id`) or because the person approved it (`task.approve` in the audit log).
  One that ran with neither is counted as unauthorized: by construction there should be none;
- **goal events**: each steer event is an answer or an added instruction from the person;
- **the audit log**: `goal.park` (every pause, whatever its cause), `task.approve`, `task.deny`;
- **the pilot log** (`alter report --log`): the person's task and their own estimate, written before
  the run and sealed with the person's key, like every other policy state.

`export()` is what a pilot participant shares: ids, counts, rates, costs and times. It holds no
objective, no task text, no file name, no reason string and no skill name.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import statistics

from . import goals as G
from .growth import wilson
from .textsafe import terminal_safe
from .types import new_id, utcnow

LOG_STATE = "alter-pilot-log"  # one sealed JSON list per person (store.update_policy_state)
LOG_MAX = 5000
LOG_TEXT_MAX = 500
MAX_ESTIMATE_MIN = 7 * 24 * 60
FREE = ("read", "note")  # risks that run without permission in every mode; everything else needs it


# --------------------------------------------------------------------------- the pilot log
def log_task(ci, uid: str, text: str, estimate_minutes: float, project_id: str | None = None) -> dict:
    """The person's own task and how long they think it would take them alone, written before the run."""
    text = " ".join(str(text).split())
    if not text:
        raise ValueError("the task text is empty")
    try:
        est = float(estimate_minutes)
    except (TypeError, ValueError):
        raise ValueError("the estimate must be a number of minutes") from None
    if not 0 < est <= MAX_ESTIMATE_MIN:  # also refuses NaN and infinity
        raise ValueError(f"the estimate must be more than 0 and at most {MAX_ESTIMATE_MIN} minutes (a week)")
    rec = {"id": new_id("plog"), "at": utcnow().isoformat(), "project_id": project_id, "text": text[:LOG_TEXT_MAX],
           "estimate_minutes": round(est, 1)}

    def append(cur: bytes | None) -> bytes:
        log = json.loads(cur) if cur else []
        return json.dumps((log + [rec])[-LOG_MAX:]).encode()

    ci.store.update_policy_state(uid, LOG_STATE, append, note="alter pilot log")
    ci.audit.append(uid, "alter.pilot_log", entry=rec["id"], project=project_id)  # ids only, never the text
    return rec


def log_entries(ci, uid: str) -> list[dict]:
    loaded = ci.store.load_policy_state(uid, LOG_STATE)
    return json.loads(loaded[1]) if loaded else []


def forget_log(ci, uid: str) -> int:
    """Withdrawal: delete every pilot log entry, every version of it, and its trace in the WAL."""
    n = len(log_entries(ci, uid))
    ci.store.delete_policy_states(uid, LOG_STATE)
    ci.store.flush_deleted()
    ci.audit.append(uid, "alter.pilot_log_forget", entries=n)
    return n


def _link(entries: list[dict], goals: list[G.Goal]) -> dict[str, str]:
    """Each entry goes with the first goal created after it, in its project, that no earlier entry took."""
    used: set[str] = set()
    out = {}
    for e in sorted(entries, key=lambda e: e["at"]):
        g = next((g for g in goals if g.created_at >= e["at"] and g.id not in used
                  and e.get("project_id") in (None, g.project_id)), None)
        if g is not None:
            used.add(g.id)
            out[e["id"]] = g.id
    return out


# --------------------------------------------------------------------------- helpers
def _secs(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    try:
        return round((dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).total_seconds(), 1)
    except (TypeError, ValueError):
        return None


def _median(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 4) if xs else None


def _mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def _rate(k: int, n: int) -> dict:
    ci = wilson(k, n)
    return {"k": k, "n": n, "rate": round(k / n, 3) if n else None, "ci95": list(ci) if ci else None}


def _task_rows(ci, uid: str, project_ids: set[str]) -> dict[str, list[dict]]:
    """Every task of these projects, by session: who allowed it, and whether it ran."""
    from .governor import Governor
    from .tasks import TaskQueue
    from .workspace import Workspace
    ws = Workspace(ci)
    q = TaskQueue(ws, Governor(ws))
    out: dict[str, list[dict]] = {}
    for pid in sorted(project_ids):
        try:
            ts = q.list(uid, pid, limit=1_000_000)
        except KeyError:  # the project and its key are gone
            continue
        for t in ts:
            if t.session_id:
                out.setdefault(t.session_id, []).append(
                    {"id": t.id, "tool": t.tool, "risk": t.risk, "status": t.status,
                     "authorization_id": t.authorization_id, "started_at": t.started_at})
    return out


# --------------------------------------------------------------------------- the report
def compute(ci, uid: str, days: int | None = 30, project_id: str | None = None, now: dt.datetime | None = None) -> dict:
    """The pilot metrics for goals created in the last `days` days (None: all), in one project or all."""
    now = now or utcnow()
    since = (now - dt.timedelta(days=days)).isoformat() if days else None
    gs = G.GoalStore(ci)
    every = sorted(gs.list(uid, None, limit=1_000_000), key=lambda g: g.created_at)
    goals = [g for g in every if (since is None or g.created_at >= since)
             and (project_id is None or g.project_id == project_id)]
    by_session = _task_rows(ci, uid, {g.project_id for g in goals})

    approved, refused, paused = set(), set(), set()
    for e in ci.audit.entries(uid):
        p = e["payload"] if isinstance(e["payload"], dict) else {}
        if e["event"] == "task.approve":
            approved.add(p.get("task"))
        elif e["event"] == "task.deny":
            refused.add(p.get("task"))
        elif e["event"] == "goal.park" and p.get("status") == "paused":
            paused.add(p.get("goal"))
    events: dict[str, dict[str, int]] = {}
    for r in ci.store.query("SELECT goal_id, kind, COUNT(*) AS n FROM goal_events WHERE user_id=? "
                            "GROUP BY goal_id, kind", (uid,)):
        events.setdefault(r["goal_id"], {})[r["kind"]] = r["n"]

    rows = []
    for g in goals:
        shown = gs.shown_status(g)
        ts = [t for sid in dict.fromkeys(g.payload.get("sessions") or []) for t in by_session.get(sid, [])]
        needs = [t for t in ts if t["risk"] not in FREE]
        ran = [t for t in needs if t["started_at"]]
        cps = g.payload.get("checkpoints") or []
        ev = events.get(g.id, {})
        first = next((c["at"] for c in cps if (c.get("text") or "").strip()), None)
        verification = g.payload.get("verification") or {}
        hits = g.payload.get("identifiers")
        scan_failed = any(h["file"] == "(check failed)" for h in hits or [])
        file_hits = len(hits) if hits is not None and not scan_failed else None
        rows.append({
            "goal": g.id, "project": g.project_id, "created_at": g.created_at, "status": shown,
            "final": g.status in G.FINAL, "verdict": verification.get("verdict"),
            "files_written": len(verification.get("files") or []),
            "turns": g.payload.get("turns", 0),
            "cost_usd": round(sum(c for c in (g.payload.get("session_costs") or {}).values() if c is not None), 6),
            "cost_unknown": bool(g.payload.get("cost_unknown")),
            "approvals": sum(t["id"] in approved for t in needs),
            "answers": ev.get("steer", 0), "pauses_asked": ev.get("pause", 0), "cancels_asked": ev.get("cancel", 0),
            "refused_by_person": sum(t["status"] == "denied" and t["id"] in refused for t in ts),
            "denied_by_policy": [{"task": t["id"], "tool": t["tool"]} for t in ts
                                 if t["status"] == "denied" and t["id"] not in refused],
            "ran_under_grant": sum(bool(t["authorization_id"]) for t in ran),
            "ran_approved_by_person": sum(not t["authorization_id"] and t["id"] in approved for t in ran),
            "ran_without_permission": [{"task": t["id"], "tool": t["tool"]} for t in ran
                                       if not t["authorization_id"] and t["id"] not in approved],
            "first_response_s": _secs(g.payload.get("requested_at") or g.created_at, first),
            "wall_s": _secs(g.created_at, cps[-1]["at"]) if cps and g.status in G.FINAL else None,
            "paused": g.id in paused,  # parked as paused, whatever the cause (the audit's goal.park)
            "interruptions": len(g.payload.get("interruptions") or []) + (shown == "interrupted"),
            "identifier_files": file_hits, "identifier_scan_failed": scan_failed,
            "corrects": g.payload.get("corrects"),  # a follow-up to the person's correction of that goal
            "done_by": g.payload.get("done_by") or ("alter" if g.status == "done" else None),
        })

    fin = [r for r in rows if r["final"]]
    wrote_files = [r for r in fin if r["files_written"]]
    flagged = [r for r in wrote_files if r["identifier_files"]]
    verified = sum(r["status"] == "done" and r["verdict"] == "verified" for r in fin)
    false_done = [r["goal"] for r in fin if r["status"] == "done" and r["verdict"] not in ("verified", "unchanged")]
    total_cost = round(sum(r["cost_usd"] for r in rows), 4)
    under = sum(r["ran_under_grant"] for r in rows)
    by_person = sum(r["ran_approved_by_person"] for r in rows)
    rec = [r for r in rows if r["paused"] or r["interruptions"]]
    rec_done = sum(r["status"] == "done" for r in rec)
    rec_other = sum(r["final"] and r["status"] != "done" for r in rec)

    row_of = {r["goal"]: r for r in rows}
    entries = log_entries(ci, uid)
    linked = _link(entries, every)
    log_rows = []
    for e in entries:
        if (since and e["at"] < since) or (project_id and e.get("project_id") != project_id):
            continue
        r = row_of.get(linked.get(e["id"], ""))
        log_rows.append({"entry": e["id"], "at": e["at"], "project": e.get("project_id"), "text": e.get("text", ""),
                         "estimate_minutes": e["estimate_minutes"], "goal": r["goal"] if r else None,
                         "status": r["status"] if r else None, "verdict": r["verdict"] if r else None,
                         "wall_minutes": round(r["wall_s"] / 60, 1) if r and r["wall_s"] is not None else None,
                         "approvals_and_answers": r["approvals"] + r["answers"] if r else None})

    attempts = [a for a in ci.attempts(uid) if since is None or str(a.get("ts", "")) >= since]
    flags = [d["flags"] + ([] if d["trend"]["direction"] != "declining" else ["declining"])
             for d in ci.skill_report(uid, now)["skills"].values()]

    return {
        "scope": {"days": days, "since": since, "project": project_id or "all", "generated_at": now.isoformat(),
                  "model_calls": 0},
        "goals": {"n": len(rows), "finished": len(fin), "active": len(rows) - len(fin),
                  "by_status": dict(sorted(collections.Counter(r["status"] for r in rows).items()))},
        "verified_done": _rate(verified, len(fin)),
        "done_nothing_changed": sum(r["status"] == "done" and r["verdict"] == "unchanged" for r in fin),
        "false_done": {"n": len(false_done), "goals": false_done},
        "corrections": {"follow_ups": sum(bool(r["corrects"]) for r in rows),
                        "done_on_your_word": [r["goal"] for r in fin if r["done_by"] == "person"]},
        "identifiers": {"goals_with_hits": len(flagged), "files": sum(r["identifier_files"] for r in flagged),
                        "goals": [r["goal"] for r in flagged],
                        "scanned_goals": sum(r["identifier_files"] is not None for r in wrote_files),
                        "unscanned_goals": sum(r["identifier_files"] is None and not r["identifier_scan_failed"]
                                               for r in wrote_files),
                        "scan_failures": sum(r["identifier_scan_failed"] for r in wrote_files)},
        "unauthorized": {"ran_without_permission": [x for r in rows for x in r["ran_without_permission"]],
                         "denied_by_policy": [x for r in rows for x in r["denied_by_policy"]],
                         "refused_by_person": sum(r["refused_by_person"] for r in rows)},
        "effort": {"approvals": sum(r["approvals"] for r in rows), "answers": sum(r["answers"] for r in rows),
                   "per_finished_goal_median": _median(r["approvals"] + r["answers"] for r in fin),
                   "per_finished_goal_mean": _mean([r["approvals"] + r["answers"] for r in fin]),
                   "pauses_asked": sum(r["pauses_asked"] for r in rows),
                   "cancels_asked": sum(r["cancels_asked"] for r in rows)},
        "autonomy": {"ran_under_grant": under, "ran_approved_by_person": by_person,
                     "share": round(under / (under + by_person), 3) if under + by_person else None},
        "cost": {"total_usd": total_cost, "median_per_finished_goal_usd": _median(r["cost_usd"] for r in fin),
                 "per_verified_goal_usd": round(total_cost / verified, 4) if verified else None,
                 "goals_with_unknown_cost": sum(r["cost_unknown"] for r in rows)},
        "time": {"first_response_median_s": _median(r["first_response_s"] for r in rows),
                 "first_response_n": sum(r["first_response_s"] is not None for r in rows),
                 "wall_median_s": _median(r["wall_s"] for r in fin), "wall_n": sum(r["wall_s"] is not None for r in fin)},
        "recovery": {"paused_or_interrupted": len(rec), "later_done": rec_done, "ended_otherwise": rec_other,
                     "not_finished": len(rec) - rec_done - rec_other, "rate": _rate(rec_done, rec_done + rec_other)},
        "pilot_log": {"logged": len(log_rows), "linked": sum(x["goal"] is not None for x in log_rows),
                      "entries": log_rows},
        "skills": {"alone": _rate(sum(bool(a.get("correct")) for a in attempts if not a.get("assisted")),
                                  sum(not a.get("assisted") for a in attempts)),
                   "assisted": _rate(sum(bool(a.get("correct")) for a in attempts if a.get("assisted")),
                                     sum(bool(a.get("assisted")) for a in attempts)),
                   "flagged_dependency_gap": sum("dependency_gap" in f for f in flags),
                   "flagged_declining": sum("declining" in f for f in flags)},
        "per_goal": rows,
    }


def export(rep: dict) -> dict:
    """The report without the person's own words: what a participant shares."""
    out = json.loads(json.dumps(rep))
    for e in out["pilot_log"]["entries"]:
        e.pop("text", None)
    return out


# --------------------------------------------------------------------------- text
def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _ci(r: dict) -> str:
    return f"{_pct(r['rate'])}; 95% CI {_pct(r['ci95'][0])}–{_pct(r['ci95'][1])}" if r["n"] else "none yet"


def _dur(s: float | None) -> str:
    return "n/a" if s is None else (f"{s:.0f} s" if s < 120 else f"{s / 60:.1f} min")


def _money(x: float | None) -> str:
    return "n/a" if x is None else f"${x:.2f}"


def _each(d: dict) -> str:
    return ", ".join(f"{n} {k}" for k, n in d.items())


def render(rep: dict, where: str) -> str:
    g, v, c, t, e, a, rc, u = (rep[k] for k in ("goals", "verified_done", "cost", "time", "effort", "autonomy",
                                                  "recovery", "unauthorized"))
    days = f"the last {rep['scope']['days']} days" if rep["scope"]["days"] else "all time"
    fin = {k: n for k, n in g["by_status"].items() if k in G.FINAL}
    act = {k: n for k, n in g["by_status"].items() if k not in G.FINAL}
    denied = collections.Counter(x["tool"] for x in u["denied_by_policy"])
    ids = rep["identifiers"]
    lines = [
        f"ALTER report · goals created in {days} · {where} · from ALTER's own records (no model call)",
        f"Goals: {g['n']} · finished {g['finished']}" + (f" ({_each(fin)})" if fin else "")
        + f" · active {g['active']}" + (f" ({_each(act)})" if act else ""),
        f"Verified done: {v['k']} of {v['n']} finished goals ({_ci(v)}) · done with nothing changed: "
        f"{rep['done_nothing_changed']}",
        f"False done (done without a verified or unchanged verdict): {rep['false_done']['n']}"
        + (f" ({', '.join(rep['false_done']['goals'])})" if rep["false_done"]["n"] else ""),
        f"Follow-ups to your corrections: {rep['corrections']['follow_ups']} · done on your word (alter goals "
        f"accept): {len(rep['corrections']['done_on_your_word'])}",
        f"Identifiers written into files (C10): {ids['files']} file(s) in {ids['goals_with_hits']} goal(s)"
        + (f" ({', '.join(ids['goals'])}: review before you commit)" if ids["files"] else "")
        + f" · {ids['scanned_goals']} scanned · {ids['unscanned_goals']} unscanned · {ids['scan_failures']} failed",
        f"Unauthorized: {len(u['ran_without_permission'])} action(s) ran without your approval or a grant"
        + (f" ({', '.join(x['task'] for x in u['ran_without_permission'])})" if u["ran_without_permission"] else "")
        + f" · {len(u['denied_by_policy'])} denied by policy" + (f" ({_each(denied)})" if denied else "")
        + f" · {u['refused_by_person']} refused by you",
        f"Your effort: {e['approvals']} approval(s), {e['answers']} answer(s) or instruction(s) · per finished goal: "
        f"median {e['per_finished_goal_median'] if e['per_finished_goal_median'] is not None else 'n/a'}, mean "
        f"{e['per_finished_goal_mean'] if e['per_finished_goal_mean'] is not None else 'n/a'} · pauses "
        f"{e['pauses_asked']}, cancels {e['cancels_asked']}",
        f"Autonomy: {a['ran_under_grant']} of {a['ran_under_grant'] + a['ran_approved_by_person']} actions that "
        f"needed permission ran under a grant ({_pct(a['share'])}); you approved {a['ran_approved_by_person']}",
        f"Cost: {_money(c['total_usd'])} in all · median {_money(c['median_per_finished_goal_usd'])} per finished goal"
        f" · {_money(c['per_verified_goal_usd'])} per verified goal"
        + (f" · cost unknown for {c['goals_with_unknown_cost']} goal(s), so these are lower bounds"
           if c["goals_with_unknown_cost"] else ""),
        f"Time: request to first response, median {_dur(t['first_response_median_s'])} (n={t['first_response_n']})"
        f" · created to last checkpoint, median {_dur(t['wall_median_s'])} per finished goal (n={t['wall_n']})",
        f"Recovery: {rc['paused_or_interrupted']} goal(s) paused or interrupted · later done {rc['later_done']}, "
        f"ended otherwise {rc['ended_otherwise']}, not finished {rc['not_finished']}"
        + (f" ({rc['rate']['k']} of {rc['rate']['n']}: {_ci(rc['rate'])})" if rc["rate"]["n"] else ""),
    ]
    pl = rep["pilot_log"]
    lines.append(f"Pilot log: {pl['logged']} task(s) logged, {pl['linked']} linked to a goal")
    for x in pl["entries"]:
        to = (f"{x['goal']} {x['status']}" + (f" ({x['verdict']})" if x["verdict"] else "")
              + (f", {x['wall_minutes']} min" if x["wall_minutes"] is not None else "")) if x["goal"] else "no goal yet"
        lines.append(f"  {x['entry']}  {x['at'][:10]}  your estimate {x['estimate_minutes']:g} min → {to}"
                     f"  \"{x['text'][:60]}\"")
    s = rep["skills"]
    lines.append(f"Skills (ci practice, every project): alone {s['alone']['k']}/{s['alone']['n']}, with help "
                 f"{s['assisted']['k']}/{s['assisted']['n']} in this window · flagged now: dependency gap "
                 f"{s['flagged_dependency_gap']}, declining {s['flagged_declining']}")
    lines.append("Done = the project's tests passed after the last change, from the task records. "
                 "--json exports this without your words.")
    return terminal_safe("\n".join(lines))
