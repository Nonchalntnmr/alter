"""CI Cognitive Twin: `ci weekly`, a weekly review by "<your name> 2.0".

What the twin is, and is not. It is not smarter than you. It has broader context (every
request, decision, practice attempt and outcome CI recorded this week, which nobody holds in
their head at once), more compute, and one job: to be accountable for pushing you. It opens by
checking last week's priorities against this week's evidence, then says what you did, where
you got stuck and what is next, then challenges: weak assumptions, missed opportunities, and
at most two priority adjustments, each with a check it will run next week.

Rules it keeps:
- **Evidence, not vibes.** Every statement is computed from CI's records and carries its
  counts. Nothing about the person is inferred beyond them: no traits, no "intelligence".
  Too little evidence is reported as too little evidence.
- **Local by default.** The review makes no model call; nothing leaves the machine.
- **`--deep` is explicit and filtered.** It adds one model-written challenge, routed like any
  request (authorized vendors only, deep tier). The digest it sends holds only what that
  vendor may already see: the requests it answered itself, notes from projects that allow it
  (lineage-checked), and counts. Everything else is withheld and counted. The terminal asks
  before sending. The reply is labelled model-written and never changes the profile.
- **The profile update is the snapshot.** Per-skill evidence for the week, the flags and the
  priorities are saved as one version per week (engine.save_weekly), and can be inspected,
  rolled back and deleted. Changes to CI's behaviour (a new growth goal, which makes LEARN the
  default for that task type) are proposed, and applied only with `--apply`.
- **Not tuned for approval.** feedback.py explains why "the person liked it" is the weakest
  signal and the most dangerous to optimise. The twin measures against what the person said
  they want (their goals, growth goals, last week's priorities), not against how they feel.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib

import numpy as np

from . import modes as intelligence
from .engine import RoutingError, fallback_errors
from .evaluation import ROUTED_TASK_TYPES, scorecard, window_interactions
from .registry import vendor_of
from .sovereignty.credentials import find_credentials
from .sovereignty.injection import scan as scan_instructions
from .types import TASK_TYPES, Trust, utcnow
from .workspace import Workspace

STALE_DAYS = 7
MAX_PRIORITIES = 2
MIN_REQUESTS_FOR_GOAL_CHECK = 5
FAILURE_RATE_FOR_GROWTH_GOAL = 0.5
MIN_VERIFIED_FOR_GROWTH_GOAL = 4
SNIPPET = 90

FRAMING = ("Broader context and more compute than you have in the moment, and accountable for pushing you. "
           "Everything below is from CI's records; nothing is inferred about you beyond them.")


def iso_week(t: dt.datetime) -> str:
    y, w, _ = t.isocalendar()
    return f"{y}-W{w:02d}"


def _snip(text: str, n: int = SNIPPET) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _q(text: str, n: int = SNIPPET) -> str:
    """Stored text quoted into a prompt: one line, capped, angle brackets neutralised so it can
    never open or close a block of the prompt it is placed in."""
    return _snip(text, n).replace("<", "‹").replace(">", "›")


def _when(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)


def _n(k: int, word: str) -> str:
    return f"{k} {word}{'' if k == 1 else 's'}"


def _pid(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


@dataclasses.dataclass
class Finding:
    kind: str  # weak_assumption | missed_opportunity
    rule: str
    statement: str  # may quote the person's records; shown locally only
    redacted: str  # counts only, no quoted content: what --deep may send
    severity: float
    priority: dict | None = None
    adjustment: dict | None = None


@dataclasses.dataclass
class Review:
    name: str
    user_id: str
    since: dt.datetime
    until: dt.datetime
    week: str
    did: list[str]
    stuck: list[str]
    next: list[str]
    accountability: list[dict]
    challenges: list[Finding]
    priorities: list[dict]
    adjustments: list[dict]
    skills: dict
    scorecard: dict
    previous_version: int | None
    thin: bool
    facts: dict = dataclasses.field(default_factory=dict, repr=False)

    def snapshot(self) -> dict:
        """What is saved as this week's version: derived evidence and decisions, no transcripts."""
        return {"format": "pcim-twin/1", "week": self.week, "name": self.name,
                "window": {"since": self.since.isoformat(), "until": self.until.isoformat()},
                "generated_at": utcnow().isoformat(), "previous_version": self.previous_version,
                "skills": self.skills, "priorities": self.priorities, "accountability": self.accountability,
                "challenges": [{"kind": c.kind, "rule": c.rule, "statement": c.statement} for c in self.challenges],
                "adjustments": self.adjustments, "scorecard": self.scorecard}

    def as_dict(self) -> dict:
        return self.snapshot() | {"did": self.did, "stuck": self.stuck, "next": self.next, "thin": self.thin}


class CognitiveTwin:
    def __init__(self, ci, ws: Workspace | None = None):
        self.ci = ci
        self.ws = ws or Workspace(ci)

    # ------------------------------------------------------------------ identity
    def name(self, user_id: str) -> str:
        set_name = self.ci._setting(user_id, "twin_name")
        if set_name:
            return set_name
        u = self.ci.store.get_user(user_id) or {}
        return f"{(u.get('display_name') or user_id).strip()} 2.0"

    def set_name(self, user_id: str, name: str) -> str:
        name = " ".join(name.split())[:60]
        if not name:
            raise ValueError("empty name")
        self.ci._set_setting(user_id, "twin_name", name)
        return name

    # ------------------------------------------------------------------ evidence
    def _facts(self, user_id: str, since: dt.datetime, until: dt.datetime) -> dict:
        ci = self.ci
        rows = window_interactions(ci, user_id, since, until)
        attempts_all = ci.attempts(user_id)
        attempts = [a for a in attempts_all if since <= _when(a["ts"]) < until]
        projects = self.ws.list_projects(user_id)
        claims = [dict(r) for r in ci.store.query(
            "SELECT id, project_id, status, created_at, updated_at FROM claims WHERE user_id=?", (user_id,))]
        goals = [i for i in ci.memory.current(user_id, until)
                 if i.kind == "goal" and i.trust.rank >= Trust.USER_STATED.rank]
        return {"rows": rows, "attempts": attempts, "attempts_all": attempts_all, "projects": projects,
                "claims": claims, "goals": goals, "growth_tasks": set(ci._assist(user_id).growth_tasks),
                "report": ci.skill_report(user_id, until)}

    def _relevance(self, a: list[str], b: list[str]) -> np.ndarray:
        """Embedding similarity mapped onto the embedder's relevance band: 0 unrelated, 1 same."""
        if not a or not b:
            return np.zeros((len(a), len(b)))
        ea, eb = self.ci.embedder.embed(a), self.ci.embedder.embed(b)
        lo, hi = self.ci.embedder.relevance_band
        return np.clip((ea @ eb.T - lo) / (hi - lo), 0.0, 1.0)

    def _repeats(self, texts: list[str], threshold: float = 0.85) -> list[list[int]]:
        texts = texts[:200]
        if len(texts) < 3:
            return []
        r = self._relevance(texts, texts)
        parent = list(range(len(texts)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                if r[i, j] >= threshold:
                    parent[find(i)] = find(j)
        groups: dict[int, list[int]] = {}
        for i in range(len(texts)):
            groups.setdefault(find(i), []).append(i)
        return sorted((g for g in groups.values() if len(g) >= 3), key=len, reverse=True)

    # ------------------------------------------------------------------ accountability
    def check(self, check: dict, f: dict, since: dt.datetime, until: dt.datetime) -> tuple[str, str]:
        t = check.get("type")
        if t == "practice":
            un = [a for a in f["attempts"] if a["skill"] == check["skill"] and not a["assisted"]]
            k = sum(a["correct"] for a in un)
            ev = f"{len(un)} unassisted attempts at {check['skill']}, {k} correct"
            if len(un) >= check["min_attempts"] and k >= check["min_correct"]:
                return "kept", ev
            return ("partly" if un else "missed"), ev
        if t == "unassisted_attempts":
            n = sum(1 for a in f["attempts"] if not a["assisted"])
            return ("kept" if n >= check["min"] else "partly" if n else "missed"), f"{n} unassisted attempts"
        if t == "verified_ratio":
            rel = [r for r in f["rows"] if r["task_type"] in check["task_types"]]
            if not rel:
                return "n/a", f"no {' or '.join(check['task_types'])} requests this week"
            v = sum(1 for r in rel if (r.get("feedback") or {}).get("task_success") is not None)
            ratio = v / len(rel)
            return ("kept" if ratio >= check["min"] else "partly" if v else "missed"), \
                f"{v} of {len(rel)} have a recorded outcome"
        if t == "note_resolved":
            for p in f["projects"]:
                for n in p.notes:
                    if n["id"] == check["note_id"]:
                        return ("kept", "closed") if n.get("resolved_at") else ("missed", "still open")
            return "n/a", "the note no longer exists"
        if t == "goal_activity":
            texts = [r["task"] for r in f["rows"]] + [n["text"] for p in f["projects"] for n in p.notes
                                                       if since <= _when(n["created_at"]) < until]
            k = int((self._relevance([check["text"]], texts) >= 0.5).sum()) if texts else 0
            return ("kept" if k >= check["min"] else "partly" if k else "missed"), f"{k} related requests or notes"
        if t == "claims_settled":
            k = sum(1 for c in f["claims"] if c["project_id"] == check["project_id"]
                    and c["status"] in ("verified", "refuted") and since <= _when(c["updated_at"]) < until)
            return ("kept" if k >= check["min"] else "missed"), f"{k} claims settled by a test or by you"
        return "n/a", "unknown check"

    # ------------------------------------------------------------------ the review
    def review(self, user_id: str, days: int = 7, now: dt.datetime | None = None) -> Review:
        if self.ci.store.get_user(user_id) is None:
            raise KeyError(f"unknown user {user_id}")
        until = now or utcnow()
        since = until - dt.timedelta(days=days)
        week = iso_week(until)
        f = self._facts(user_id, since, until)
        rows, attempts, projects, report = f["rows"], f["attempts"], f["projects"], f["report"]

        # previous snapshot: last week's, even when this week's has already been saved once
        snaps = {s["version"]: s for s in self.ci.weekly_snapshots(user_id)}
        active = next((s for s in snaps.values() if s["active"]), None)
        prev = snaps.get(active.get("previous_version")) if active and active["week"] == week else active
        accountability = []
        for p in (prev or {}).get("priorities", []):
            status, ev = self.check(p["check"], f, since, until)
            accountability.append({"priority": p["action"], "status": status, "evidence": ev, "week": prev["week"]})

        # ---- what you did
        by_type: dict[str, int] = {}
        by_mode: dict[str, int] = {}
        for r in rows:
            by_type[r["task_type"]] = by_type.get(r["task_type"], 0) + 1
            m = ((r.get("receipt") or {}).get("intelligence_mode") or {}).get("mode")
            if m:
                by_mode[m] = by_mode.get(m, 0) + 1
        sc = scorecard(self.ci, user_id, since, until, rows=rows)
        prod, cog = sc["productivity"], sc["cognitive"]
        did = []
        if rows:
            did.append(f"{_n(len(rows), 'request')}: " + ", ".join(f"{t} {n}" for t, n in sorted(by_type.items(), key=lambda x: -x[1]))
                       + (f" · modes: " + ", ".join(f"{m} {n}" for m, n in sorted(by_mode.items(), key=lambda x: -x[1]))
                          if by_mode else ""))
            if prod["with_verified_outcome"]:
                did.append(f"{prod['with_verified_outcome']} with a recorded outcome, "
                           f"{round((prod['verified_success_rate'] or 0) * prod['with_verified_outcome'])} of them worked")
        ok_tools = prod["tool_actions"].get("succeeded", 0)
        if ok_tools:
            did.append(f"{_n(ok_tools, 'tool action')} succeeded in your projects")
        decisions = [(p, n) for p in projects for n in p.notes
                     if n["kind"] == "decision" and since <= _when(n["created_at"]) < until]
        resolved = [(p, n) for p in projects for n in p.notes
                    if n.get("resolved_at") and since <= _when(n["resolved_at"]) < until]
        for p, n in decisions[:3]:
            did.append(f"decision in {p.name}: “{_snip(n['text'], 70)}”")
        if len(decisions) > 3:
            did.append(f"… and {len(decisions) - 3} more decisions")
        if resolved:
            did.append(f"closed {_n(len(resolved), 'note')} (next steps or questions)")
        if cog["unassisted_attempts"] or cog["assisted_attempts"]:
            did.append(f"practice: {_n(cog['unassisted_attempts'], 'unassisted attempt')} ({cog['unassisted_correct']} right)"
                       f" across {_n(len(cog['skills_practised_alone']), 'skill')}; {cog['assisted_attempts']} with help")

        # ---- where you got stuck
        stuck = []
        failed = [r for r in rows if (r.get("feedback") or {}).get("task_success") is False
                  or (r.get("feedback") or {}).get("corrected")]
        if failed:
            stuck.append(f"{len(failed)} answers failed or needed correction: "
                         + "; ".join(f"“{_snip(r['task'], 50)}”" for r in failed[:3]))
        bad_tools = {k: v for k, v in prod["tool_actions"].items() if k in ("failed", "denied", "interrupted")}
        if bad_tools:
            stuck.append("tool actions " + ", ".join(f"{k} {v}" for k, v in bad_tools.items()))
        for s, d in report["skills"].items():
            if "recurring_difficulty" in d["flags"]:
                stuck.append(f"{s}: {d['evidence']['recurring_difficulty']}")
        groups = self._repeats([r["task"] for r in rows])
        for g in groups[:2]:
            stuck.append(f"asked {len(g)} times about the same thing: “{_snip(rows[g[0]]['task'], 60)}”")
        now_ = until
        old_q = sorted(((p, n) for p in projects for n in p.open_notes("open_question")
                        if (now_ - _when(n["created_at"])).days >= STALE_DAYS), key=lambda x: x[1]["created_at"])
        for p, n in old_q[:2]:
            stuck.append(f"open question in {p.name} for {(now_ - _when(n['created_at'])).days} days: “{_snip(n['text'], 60)}”")

        # ---- what's next
        nxt = []
        open_steps = sorted(((p, n) for p in projects for n in p.open_notes("next_step")),
                            key=lambda x: x[1]["created_at"])
        for p, n in open_steps[:3]:
            nxt.append(f"{p.name}: {_snip(n['text'], 70)} ({(now_ - _when(n['created_at'])).days}d open)")
        due = [s for s, d in report["skills"].items() if "due_for_review" in d["flags"]]
        if due:
            nxt.append("retention checks due: " + ", ".join(due[:4]))
        ch = self.ci.next_challenge(user_id)
        if ch:
            nxt.append(f"next challenge (closest to 85% predicted success): {ch}")

        # ---- challenges
        cands: list[Finding] = []
        cr = [r for r in rows if r["task_type"] in ROUTED_TASK_TYPES]
        cr_v = sum(1 for r in cr if (r.get("feedback") or {}).get("task_success") is not None)
        if len(cr) >= 3 and cr_v / len(cr) < 0.5:
            sev = 0.5 + 0.4 * (1 - cr_v / len(cr)) * min(1.0, len(cr) / 10)
            head = "none of them has" if cr_v == 0 else f"only {cr_v} of them have"
            stmt = (f"You're treating {len(cr) - cr_v} of {len(cr)} code and research answers as correct, but {head} "
                    f"a recorded outcome. Mark what happened with `ci outcome <id> worked|failed`.")
            cands.append(Finding("weak_assumption", "unverified_answers", stmt, stmt, sev,
                                 priority={"action": "Record an outcome for at least half of your code and research answers",
                                           "check": {"type": "verified_ratio", "min": 0.5,
                                                     "task_types": list(ROUTED_TASK_TYPES)}}))
        for s, d in report["skills"].items():
            if "dependency_gap" in d["flags"]:
                stmt = f"You may be overestimating how well you know {s}: {d['evidence']['dependency_gap']}."
                cands.append(Finding("weak_assumption", "dependency_gap", stmt,
                                     f"A skill shows a dependency gap: {d['evidence']['dependency_gap']}.", 0.8,
                                     priority=_practice(s)))
            if "recurring_difficulty" in d["flags"]:
                stmt = (f"{d['evidence']['recurring_difficulty'].capitalize()} at {s}. Repeating the same attempt isn't "
                        f"working: work one example in learn mode (`ci -l`), then try alone.")
                cands.append(Finding("weak_assumption", "recurring_difficulty", stmt,
                                     f"Recurring difficulty on one skill: {d['evidence']['recurring_difficulty']}.", 0.75,
                                     priority=_practice(s)))
            if "due_for_review" in d["flags"]:
                stmt = f"{s}: {d['evidence']['due_for_review']}. One unassisted check says whether it stuck."
                cands.append(Finding("missed_opportunity", "retention_due", stmt,
                                     f"A skill is due for review: {d['evidence']['due_for_review']}.", 0.35,
                                     priority=_practice(s, attempts=1, correct=1)))
        un_week = sum(1 for a in attempts if not a["assisted"])
        for t in sorted(f["growth_tasks"]):
            k = by_type.get(t, 0)
            if k >= 2 and un_week == 0:
                stmt = (f"You said you want to get better at {t}. CI helped with {k} {t} requests this week and you "
                        f"recorded no unassisted attempts, so there is no evidence of progress either way.")
                cands.append(Finding("missed_opportunity", "growth_unpractised", stmt, stmt, 0.6,
                                     priority={"action": "Make 3 unassisted attempts (`ci practice <skill> right|wrong`)",
                                               "check": {"type": "unassisted_attempts", "min": 3}}))
        for t in TASK_TYPES:
            v = [r for r in rows if r["task_type"] == t and (r.get("feedback") or {}).get("task_success") is not None]
            bad = [r for r in v if not r["feedback"]["task_success"] or r["feedback"].get("corrected")]
            if len(v) >= MIN_VERIFIED_FOR_GROWTH_GOAL and len(bad) / len(v) >= FAILURE_RATE_FOR_GROWTH_GOAL \
                    and t not in f["growth_tasks"]:
                stmt = (f"{len(bad)} of {len(v)} verified {t} answers this week failed or needed correction. "
                        f"Taking more {t} answers as given is the weak assumption; learning the part that keeps "
                        f"failing is cheaper.")
                cands.append(Finding("weak_assumption", "failing_task_type", stmt, stmt, 0.65,
                                     adjustment={"kind": "growth_goal", "task_type": t,
                                                 "why": f"{len(bad)}/{len(v)} verified {t} answers failed or were corrected",
                                                 "effect": f"{t} requests default to learn mode unless you ask otherwise"}))
        if f["goals"] and len(rows) >= MIN_REQUESTS_FOR_GOAL_CHECK:
            texts = [r["task"] for r in rows] + [n["text"] for p in projects for n in p.notes
                                                 if since <= _when(n["created_at"]) < until]
            rel = self._relevance([g.content for g in f["goals"]], texts)
            for g, row in zip(f["goals"], rel):
                if int((row >= 0.5).sum()) == 0:
                    stmt = (f"Your stated goal “{_snip(g.content, 70)}” had no related activity this week "
                            f"(0 of {len(rows)} requests, 0 project notes).")
                    cands.append(Finding("missed_opportunity", "goal_neglected", stmt,
                                         f"A stated goal had no related activity in {len(rows)} requests.", 0.55,
                                         priority={"action": f"Spend at least two working sessions on “{_snip(g.content, 50)}”",
                                                   "check": {"type": "goal_activity", "text": g.content, "min": 2}}))
        stale = [(p, n) for p, n in open_steps if (now_ - _when(n["created_at"])).days >= STALE_DAYS]
        if stale:
            p, n = stale[0]
            age = (now_ - _when(n["created_at"])).days
            stmt = (f"{len(stale)} next steps have been open more than a week; the oldest ({age} days, {p.name}): "
                    f"“{_snip(n['text'], 60)}”. Do it, or drop it on purpose.")
            cands.append(Finding("missed_opportunity", "stale_next_steps", stmt,
                                 f"{len(stale)} next steps open more than a week (oldest {age} days).",
                                 min(0.7, 0.5 + 0.05 * len(stale)),
                                 priority={"action": f"Close or drop: “{_snip(n['text'], 50)}” ({p.name})",
                                           "check": {"type": "note_resolved", "project_id": p.id, "note_id": n["id"]}}))
        for p in projects:
            d_here = [n for q, n in decisions if q.id == p.id]
            if not d_here:
                continue
            open_c = [c for c in f["claims"] if c["project_id"] == p.id and c["status"] in ("unverified", "contested")]
            open_qs = p.open_notes("open_question")
            if open_c or open_qs:
                parts = ([f"{len(open_c)} claims there are unverified or contested"] if open_c else []) + \
                        ([f"{len(open_qs)} questions are still open"] if open_qs else [])
                stmt = f"In {p.name} you recorded {len(d_here)} decisions while " + " and ".join(parts) + \
                       ". Check that the decisions don't rest on them."
                cands.append(Finding("weak_assumption", "decided_on_open_questions", stmt,
                                     f"Decisions recorded in a project with {len(open_c)} unsettled claims and "
                                     f"{len(open_qs)} open questions.", 0.6,
                                     priority=({"action": f"Settle one claim in {p.name} with a test or your own check",
                                                "check": {"type": "claims_settled", "project_id": p.id, "min": 1}}
                                               if open_c else None)))
        for g in groups[:1]:
            stmt = (f"You asked about the same thing {len(g)} times (“{_snip(rows[g[0]]['task'], 50)}”). Save the answer "
                    f"as a pinned note (`pcim remember … --pin`) or practise it until you don't need to ask.")
            cands.append(Finding("missed_opportunity", "repeated_question", stmt,
                                 f"The same question was asked {len(g)} times.", 0.4))

        thin = not rows and not attempts and not decisions and not resolved
        if thin:
            stmt = ("CI recorded nothing this week, so there is nothing to push on. That is the finding: if you worked "
                    "this week, CI didn't see it.")
            cands.append(Finding("missed_opportunity", "no_evidence", stmt, stmt, 0.3))

        cands.sort(key=lambda c: -c.severity)
        priorities, seen = [], set()
        for c in cands:
            if c.priority and len(priorities) < MAX_PRIORITIES:
                key = (c.priority["check"]["type"], c.priority["check"].get("skill"), c.priority["check"].get("note_id"))
                if key in seen:
                    continue
                seen.add(key)
                priorities.append(c.priority | {"id": _pid(c.priority["action"]), "from": c.rule,
                                                 "evidence": c.statement})
        adjustments = [c.adjustment | {"id": _pid(c.adjustment["kind"] + c.adjustment.get("task_type", ""))}
                       for c in cands if c.adjustment]

        skills = {}
        for s, d in report["skills"].items():
            wk = [a for a in attempts if a["skill"] == s]
            if not wk and not d["flags"]:
                continue
            un_ = [a for a in wk if not a["assisted"]]
            skills[s] = {"mastery": d["mastery"], "flags": d["flags"], "evidence": d["evidence"], "trend": d["trend"],
                         "week": {"unassisted": len(un_), "unassisted_correct": sum(a["correct"] for a in un_),
                                  "assisted": len(wk) - len(un_)}}
        return Review(self.name(user_id), user_id, since, until, week, did, stuck, nxt, accountability, cands,
                      priorities, adjustments, skills, sc, prev["version"] if prev else None, thin, facts=f)

    # ------------------------------------------------------------------ the profile update
    def save(self, user_id: str, review: Review) -> int:
        return self.ci.save_weekly(user_id, review.snapshot())

    def apply(self, user_id: str, review: Review, ids: list[str] | None = None) -> list[dict]:
        """Apply proposed adjustments (all, or those whose id is in `ids`). Today: growth goals."""
        done = []
        ap = self.ci._assist(user_id)
        goals = set(ap.growth_tasks)
        for a in review.adjustments:
            if ids is not None and a["id"] not in ids:
                continue
            if a["kind"] == "growth_goal" and a["task_type"] not in goals:
                goals.add(a["task_type"])
                done.append(a)
        if done:
            self.ci.set_growth_goals(user_id, sorted(goals))
        return done

    # ------------------------------------------------------------------ --deep
    def _may_quote(self, user_id: str, row: dict, vendor: str) -> str | None:
        """Why a past request may not be quoted to `vendor` (a withheld-count key), or None.
        Quotable only if this vendor answered it and, when it was made inside a project, that
        project (still) allows the vendor."""
        try:
            if vendor_of(row["provider"]) != vendor:
                return "requests_answered_by_other_vendors"
        except ValueError:
            return "requests_answered_by_other_vendors"
        pid = (row.get("receipt") or {}).get("project_id")
        if pid:
            try:
                if vendor not in self.ws.provider_vendors(user_id, pid):
                    return "requests_from_projects_not_allowing_this_vendor"
            except KeyError:  # the project was deleted: its content goes nowhere
                return "requests_from_projects_not_allowing_this_vendor"
        return None

    def _skill_quotable(self, user_id: str, skill: str, vendor: str, attempts: list[dict]) -> bool:
        """A skill name is the person's words when they logged it themselves; when it was logged on
        an answer (`ci outcome --skill`), it may go only where that answer may be quoted."""
        for a in attempts:
            if a["skill"] != skill or not a.get("interaction"):
                continue
            row = self.ci.store.get_interaction(a["interaction"])
            if row is None or self._may_quote(user_id, row, vendor):
                return False
        return True

    def digest(self, user_id: str, review: Review, vendor: str) -> tuple[str, dict]:
        """The facts `vendor` may receive, as text, and what was withheld and why.

        Quoted text is only: requests this vendor answered itself (and whose project still allows
        it), notes from projects that allow it (model-written ones lineage-checked), and skill
        names the person logged or that came from answers this vendor may see. Every quoted line
        then passes the credential tripwire and the instruction tripwire, and angle brackets in
        it are neutralised, so stored text cannot close or forge the prompt's <facts> block."""
        f = review.facts
        withheld = {"requests_answered_by_other_vendors": 0, "requests_from_projects_not_allowing_this_vendor": 0,
                    "projects_not_allowing_this_vendor": 0, "notes_blocked_by_lineage": 0,
                    "skill_names_from_other_vendors": 0, "lines_with_credentials": 0,
                    "lines_flagged_as_instructions": 0}
        lines = [f"Week {review.week} ({review.since.date()} to {review.until.date()}). Counts are CI's records."]

        def quote(line: str) -> None:
            if find_credentials(line):
                withheld["lines_with_credentials"] += 1
            elif scan_instructions(line):
                withheld["lines_flagged_as_instructions"] += 1
            else:
                lines.append(line)

        own = []
        for r in f["rows"]:
            why = self._may_quote(user_id, r, vendor)
            if why:
                withheld[why] += 1
            else:
                own.append(r)
        lines.append(f"Requests: {len(f['rows'])} total; by type: "
                     + ", ".join(f"{t} {sum(1 for r in f['rows'] if r['task_type'] == t)}" for t in TASK_TYPES
                                 if any(r["task_type"] == t for r in f["rows"])))
        for r in own[:12]:
            fb = r.get("feedback") or {}
            outcome = ("worked" if fb.get("task_success") else "failed" if fb.get("task_success") is False else "no outcome")
            quote(f"- [{r['task_type']}, {outcome}{', corrected' if fb.get('corrected') else ''}] {_q(r['task'], 140)}")
        for p in f["projects"]:
            if vendor not in self.ws.provider_vendors(user_id, p.id):
                withheld["projects_not_allowing_this_vendor"] += 1
                continue
            quote(f"Project {_q(p.name, 60)}, objective: {_q(p.objective, 120)}")
            notes = [n for n in p.notes if not n.get("resolved_at") or _when(n["resolved_at"]) >= review.since]
            for n in notes[-20:]:
                if n.get("source") == "model" and self.ws.derived_blocked(user_id, n.get("lineage"), vendor):
                    withheld["notes_blocked_by_lineage"] += 1
                    continue
                quote(f"  - {n['kind']}{' (closed)' if n.get('resolved_at') else ''}: {_q(n['text'], 120)}")
        for i, (s, d) in enumerate(review.skills.items(), 1):
            w = d["week"]
            name = _q(s, 60) if self._skill_quotable(user_id, s, vendor, f["attempts_all"]) else None
            if name is None:
                withheld["skill_names_from_other_vendors"] += 1
            quote(f"Skill {name or f'#{i} (name withheld)'}: this week {w['unassisted']} alone "
                  f"({w['unassisted_correct']} right), {w['assisted']} with help; flags: {', '.join(d['flags']) or 'none'}")
        lines.append("CI's own findings (counts only): " + " | ".join(c.redacted for c in review.challenges))
        return "\n".join(lines), withheld

    def deep_plan(self, user_id: str):
        return self.ci.route(user_id, "weekly review: challenge assumptions and set priorities", mode="amplify",
                             min_tier=3)

    def deep(self, user_id: str, review: Review, plan=None) -> dict:
        """One model-written challenge over the filtered digest. Labelled, never evidence.

        Fallback stays within the vendor the person was asked about (the plan's chosen vendor):
        their confirmation names one recipient, so a failure there never sends the digest to a
        different vendor. The finished prompt passes the credential tripwire once more."""
        plan = plan or self.deep_plan(user_id)
        if plan.refused:
            raise RoutingError(plan.refused, plan)
        confirmed = plan.chosen.entry.vendor
        md = intelligence.resolve("amplify", None, "planning", set())
        attempts = []
        for c in [c for c in plan.candidates if c.entry.vendor == confirmed]:
            text, withheld = self.digest(user_id, review, c.entry.vendor)
            prompt = DEEP_PROMPT.format(name=_q(review.name, 60), facts=text)
            if find_credentials(prompt):
                raise RoutingError("the weekly digest still contains something credential-shaped; not sent", plan)
            try:
                res = self.ci.ask(user_id, prompt, c.entry.provider, c.entry.model or None, _mode=md,
                                  route={"plan": plan.as_dict(), "served_by": c.key, "served_meets": c.meets,
                                         "served_tier": c.as_dict()["tier"], "served_est_cost_usd": c.est_cost_usd,
                                         "failed_before": list(attempts), "purpose": "weekly-deep"})
            except fallback_errors() as e:
                attempts.append({"model": c.key, "error": f"{type(e).__name__}: {str(e)[:200]}"})
                continue
            return {"text": res.text, "interaction_id": res.interaction_id, "model": c.entry.label,
                    "withheld": withheld, "cost_usd": res.receipt["cost_usd"]}
        raise RoutingError(f"every {confirmed} model failed; nothing was sent to another vendor", plan, attempts)


def _practice(skill: str, attempts: int = 3, correct: int = 2) -> dict:
    return {"action": f"{attempts} unassisted attempt{'s' if attempts > 1 else ''} at {skill}"
                      + (f", at least {correct} right" if attempts > 1 else ""),
            "check": {"type": "practice", "skill": skill, "min_attempts": attempts, "min_correct": correct}}


DEEP_PROMPT = """You are {name}: this person's weekly reviewer. You are not smarter than they are. You have broader context (the facts below, which they don't hold in their head at once) and you are accountable for pushing them, not for pleasing them. Do not flatter and do not soften a finding the facts support.

Using only the facts below:
1. Name the weakest assumption the facts suggest they are making, and the fact that shows it.
2. Name one missed opportunity.
3. Set at most two priority adjustments for next week, each with a check they can verify themselves.
If the facts are too thin to support a point, say so instead of guessing. Do not infer traits or abilities; talk about the work.

<facts>
{facts}
</facts>"""


def render(review: Review, saved_version: int | None = None, deep: dict | None = None,
           applied: list[dict] | None = None) -> str:
    w = []
    w.append(f"{review.name} · week {review.week} · {review.since.date()} → {review.until.date()}")
    w.append(FRAMING)
    if review.accountability:
        w.append("\nLAST WEEK'S PRIORITIES")
        mark = {"kept": "✓", "partly": "~", "missed": "✗", "n/a": "·"}
        for a in review.accountability:
            w.append(f"  {mark.get(a['status'], '·')} {a['priority']}: {a['status']} ({a['evidence']})")
    for title, items, empty in (("WHAT YOU DID", review.did, "nothing recorded"),
                                ("WHERE YOU GOT STUCK", review.stuck, "nothing recorded as stuck"),
                                ("WHAT'S NEXT", review.next, "no open next steps or due checks")):
        w.append(f"\n{title}")
        w += [f"  • {x}" for x in items] or [f"  ({empty})"]
    w.append("\nCHALLENGE")
    if review.challenges:
        for c in review.challenges[:6]:
            w.append(f"  {'! weak assumption' if c.kind == 'weak_assumption' else '? missed opportunity'}: {c.statement}")
    else:
        w.append("  Nothing the evidence supports pushing on this week. That is not the same as everything being fine.")
    w.append("\nPRIORITY ADJUSTMENTS")
    w += [f"  {i}. {p['action']}" for i, p in enumerate(review.priorities, 1)] or \
        ["  (none: too little evidence to set one)"]
    if review.adjustments:
        w.append("\nPROPOSED CHANGES TO CI")
        for a in review.adjustments:
            state = "applied" if applied and any(x["id"] == a["id"] for x in applied) else "run `ci weekly --apply` to accept"
            w.append(f"  [{a['id']}] make {a['task_type']} a growth goal: {a['effect']} ({a['why']}) · {state}")
    s = review.scorecard
    p, c, o = s["productivity"], s["cognitive"], s["orchestration"]
    w.append("\nSCORECARD (kept apart on purpose)")
    rate = "n/a" if p["verified_success_rate"] is None else f"{p['verified_success_rate']:.0%}"
    w.append(f"  productivity   {p['requests']} requests · verified success {rate} of {p['with_verified_outcome']} · "
             f"{p['corrected']} corrected · cost ${p['cost_usd_known']:.4f}"
             + (f" (+{p['cost_unknown_requests']} unpriced)" if p["cost_unknown_requests"] else ""))
    urate = "n/a" if c["unassisted_rate"] is None else f"{c['unassisted_rate']:.0%}"
    arate = "n/a" if c["assisted_rate"] is None else f"{c['assisted_rate']:.0%}"
    w.append(f"  cognitive      alone {urate} of {c['unassisted_attempts']} · with help {arate} of "
             f"{c['assisted_attempts']} · {c['learn_mode_requests']} learn-mode requests")
    w.append(f"  orchestration  {o['routed']} routed · {o['recovered_by_fallback']} recovered by fallback · "
             f"{o['route_refusals']} refused ({o['refused_for_credentials']} for credentials) · "
             f"{o['served_below_requirement']} served below requirement")
    if deep:
        w.append(f"\nMODEL-WRITTEN CHALLENGE ({deep['model']}; not evidence, did not change your profile)")
        w += ["  " + line for line in deep["text"].strip().splitlines()]
        wh = {k: v for k, v in deep["withheld"].items() if v}
        if wh:
            w.append("  withheld from that model: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in wh.items()))
    if saved_version is not None:
        w.append(f"\nSkill profile saved as weekly version {saved_version}. "
                 "`ci weekly --history` · `ci weekly --rollback N` · `ci weekly --forget`")
    return "\n".join(w)
