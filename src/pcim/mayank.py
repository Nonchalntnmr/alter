"""MAYANK: ALTER's own state as one field of entities a person can see, search and question.

The page at /mayank draws this field as points. This module is what makes each bright point
mean something: every entity is a record ALTER really holds, read through the same objects the
API and the terminal use (so sealed content is opened only for its owner), never invented here.

    memory       TemporalMemory items; a correction `supersedes`, a detected conflict `contradicts`
    project      authorized projects; their decisions and next steps are entities too
    goal, task   durable goals and every tool call ALTER proposed or ran for them
    run, agent   specialist runs and their work items (each an agent with a role and objective)
    claim        the claim and evidence ledger; evidence refs link a claim to the tasks behind it
    hypothesis   `ci challenge` records: the person's claim, the assumptions it rests on, the
                 counterexamples against it, the strongest alternative and the experiment that
                 could settle it, each its own entity
    skill        growth mastery (only unassisted attempts move it) and its review schedule
    plan         intent-compiler plans; outcomes and steps with their dependencies
    exchange     routed requests (asks), with the model that served them

Positions in the "constellation" form come from the store's own embedder (the same one memory
uses), projected to three dimensions. Live activity comes from the audit chain: every governed
action is already recorded there, so the page shows what ALTER did, not an animation of it.

Anything marked "[demo]" (scripts/mayank_demo.py) is flagged `demo` so the page can say so.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
import time

import numpy as np

import re

from .memory import CONFLICT_FLAG

LINK_STRIP = re.compile(r"!?\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")

MAX_ENTITIES = 4000
TEXT_MAX = 600
DEMO_MARK = "[demo]"

# Where each kind lives on the body. The page uses this; it is here so the data and its
# reading agree in one place.
REGIONS = {
    "memory": "head", "skill": "head", "hypothesis": "head", "assumption": "head",
    "counter": "head", "alternative": "head", "experiment": "head",
    "project": "torso", "goal": "torso", "plan": "torso", "objective": "torso", "decision": "torso", "note": "torso",
    "run": "arms", "agent": "arms", "task": "arms",
    "claim": "legs", "evidence": "legs", "exchange": "legs", "model": "arms",
    "knowledge": "head", "debate": "head", "argument": "head", "commit": "legs",
}

_cache_lock = threading.Lock()
_vectors: dict[str, np.ndarray] = {}  # sha1(text) -> unit vector, shared by every user's field (text only)
_fields: dict[str, tuple[tuple, float, dict]] = {}  # uid -> ((audit seq, run seq), built at, field)
FIELD_TTL_S = 30.0


def _clip(s, n=TEXT_MAX) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


class _Field:
    def __init__(self):
        self.entities: dict[str, dict] = {}
        self.links: list[dict] = []
        self.sources: list[dict] = []

    def add(self, eid: str, kind: str, label, *, text="", t=None, status="", project=None, weight=0.5,
            source="", meta=None, t_end=None) -> str:
        label, text = str(label or ""), str(text or "")
        demo = DEMO_MARK in label or DEMO_MARK in text or source == "demo"
        clean = _clip(label.replace(DEMO_MARK, ""), 160) or kind
        self.entities[eid] = {
            "id": eid, "kind": kind, "region": REGIONS.get(kind, "torso"), "label": clean,
            "text": _clip(text.replace(DEMO_MARK, "")), "t": _iso(t), "status": str(status or ""),
            "project": project, "weight": float(max(0.0, min(1.0, weight))), "source": str(source or ""),
            "demo": demo, "meta": meta or {}, "t_end": _iso(t_end),
        }
        return eid

    def model(self, provider, model) -> str:
        """One entity per model ALTER used, so you can see which model did what."""
        mid = f"model:{provider or '?'}:{model}"
        if mid not in self.entities:
            mock = provider == "mock"  # the offline test double: it answers, but it is not a model
            self.add(mid, "model", str(model), status="used", weight=0.7, source="demo" if mock else str(provider or ""),
                     text="the offline mock provider: a test double, not a real model" if mock else f"provider {provider or 'unknown'}",
                     meta={"provider": provider, "model": model, "test_double": mock})
        return mid

    def link(self, a: str | None, b: str | None, rel: str, t=None) -> None:
        if a and b and a != b:
            self.links.append({"a": a, "b": b, "rel": rel, **({"t": _iso(t)} if t else {})})

    def source(self, name: str, fn) -> None:
        before = len(self.entities)
        try:
            fn()
            self.sources.append({"name": name, "entities": len(self.entities) - before, "error": None})
        except Exception as e:  # one unreadable source must not blank the field; it is reported instead
            self.sources.append({"name": name, "entities": len(self.entities) - before,
                                 "error": f"{type(e).__name__}: {_clip(e, 200)}"})


def _base(v):
    """An absolute path keeps only its last part: traces show what ran, never where on this machine."""
    v = str(v)
    return v.rsplit("/", 1)[-1] if v.startswith("/") else v


def _trace(t) -> dict:
    """What a tool call actually did, from its result: the file it wrote, the test output it produced."""
    r = t.result if isinstance(t.result, dict) else {}
    out = {}
    if t.tool == "write_file":
        out = {k: r.get(k) for k in ("path", "created", "bytes", "sha256_after") if k in r}
    elif t.tool == "run_tests":
        out = {"argv": [_base(a) for a in (r.get("argv") or [])][:8], "exit_code": r.get("exit_code"),
               "timed_out": r.get("timed_out"), "stdout_tail": str(r.get("stdout_tail") or "")[-900:],
               "stderr_tail": str(r.get("stderr_tail") or "")[-400:]}
        conf = r.get("confinement") or {}
        if isinstance(conf, dict):
            out["confinement"] = {k: conf.get(k) for k in ("backend", "verified", "network") if k in conf}
    elif t.tool in ("read_file", "search_code"):
        out = {k: r.get(k) for k in ("path", "bytes", "lines_total", "matches") if k in r}
    return {k: v for k, v in out.items() if v not in (None, "", [])}


# --------------------------------------------------------------------------- sources
def _memory(f: _Field, ci, uid: str) -> None:
    items = ci.memory.all(uid)
    for i in items:
        current = i.is_current()
        status = "current" if current else ("superseded" if i.superseded_at else "closed")
        if getattr(i, "status", None) and str(getattr(i, "status")) not in ("active",):
            status = str(getattr(i, "status"))
        label = f"{i.key} = {i.value}" if i.key and i.value is not None else i.content
        f.add(i.id, "memory", label, text=i.content, t=i.recorded_at, status=status,
              t_end=i.superseded_at or (i.valid_to if i.valid_to and not current else None),
              weight=float(i.confidence) * (1.0 if current else 0.4), source=i.source,
              meta={"memory_kind": i.kind, "trust": getattr(i.trust, "value", str(i.trust)),
                    "sensitivity": getattr(i.sensitivity, "value", str(i.sensitivity)),
                    "pinned": bool(getattr(i, "pinned", False)), "key": i.key,
                    "conflict": CONFLICT_FLAG in (getattr(i, "flags", ()) or ()),
                    "valid_from": _iso(i.valid_from), "valid_to": _iso(i.valid_to)})
    for i in items:
        if getattr(i, "superseded_by", None):
            f.link(i.superseded_by, i.id, "supersedes")
        for p in getattr(i, "parents", None) or []:
            f.link(i.id, p, "derived_from")
    for c in ci.store.list_conflicts(uid, status=None):
        f.link(c["item_a"], c["item_b"], "contradicts", t=c.get("detected_at"))


def _workspace(f: _Field, ci, uid: str, ws, queue, ledger, runs) -> None:
    from .goals import GoalStore
    goals = GoalStore(ci)
    session_owner: dict[str, str] = {}
    for p in ws.list_projects(uid):
        f.add(p.id, "project", p.name, text=p.objective or p.success_criteria, t=p.created_at, status=p.status,
              project=p.id, weight=0.9, meta={"constraints": p.constraints[:8], "success": _clip(p.success_criteria, 300)})
        for n in p.notes:
            kind = "decision" if n.get("kind") == "decision" else "note"
            nid = f.add(n.get("id") or f"{p.id}/note/{hashlib.sha1(str(n).encode()).hexdigest()[:10]}", kind,
                        n.get("text") or n.get("content") or "", text=n.get("text") or "", t=n.get("created_at"),
                        status="resolved" if n.get("resolved_at") else "open", project=p.id, weight=0.5,
                        source=n.get("source", ""), meta={"note_kind": n.get("kind")})
            f.link(nid, p.id, "part_of")
        for g in goals.list(uid, p.id, limit=200):
            pay = g.payload or {}
            ver = pay.get("verification") or {}
            f.add(g.id, "goal", g.objective, text=pay.get("reason") or "", t=g.created_at, status=g.status,
                  project=p.id, weight=0.85,
                  meta={"verdict": ver.get("verdict") if isinstance(ver, dict) else None,
                        "verification": ({k: ver.get(k) for k in ("verdict", "line", "tests_after_writes")
                                          if k in ver} if isinstance(ver, dict) else None),
                        "provider": pay.get("provider"), "model": pay.get("model"), "turns": pay.get("turns"),
                        "checkpoints": len(pay.get("checkpoints") or []), "steers": len(pay.get("steers") or []),
                        "cost_usd": round(sum((pay.get("session_costs") or {}).values()), 4) if pay.get("session_costs") else None,
                        "cites": ((pay.get("context") or {}).get("cites") or [])[:12]})
            f.link(g.id, p.id, "part_of")
            if pay.get("model"):
                f.link(g.id, f.model(pay.get("provider"), pay["model"]), "used")
            for s in pay.get("sessions") or []:
                session_owner[s if isinstance(s, str) else str(s)] = g.id
            for k, cite in enumerate(((pay.get("context") or {}).get("cites") or [])[:12]):
                ev = f.add(f"{g.id}/cite/{k}", "evidence", cite, text=cite, t=g.created_at, status="cited",
                           project=p.id, weight=0.35, source="third eye")
                f.link(ev, g.id, "evidence")
            if pay.get("memory_item"):
                f.link(g.id, pay["memory_item"], "produced")
        if runs is not None:
            for r in runs.list(uid, p.id):
                f.add(r["id"], "run", r.get("goal") or "run", t=r.get("created_at"), status=r.get("status"),
                      project=p.id, weight=0.8, meta={"usage": r.get("usage"), "limits": r.get("limits"),
                                                      "provider": r.get("provider"), "model": r.get("model"),
                                                      "events": [{"type": ev.get("type"), "at": ev.get("at"), "reason": _clip(ev.get("reason") or "", 200)}
                                                                 for ev in (r.get("events") or [])[-12:]],
                                                      "verification": {k: v for k, v in (r.get("verification") or {}).items()
                                                                       if k in ("artifacts_current", "tests_after_writes", "scope")},
                                                      "steers": len(r.get("steers") or []),
                                                      "attempts": len(r.get("attempts") or []),
                                                      "cost_usd": round(sum(float(a.get("cost_usd") or 0) for a in r.get("attempts") or []), 4),
                                                      "pending": [t for a in r.get("attempts") or [] for t in a.get("pending") or []]})
                if r.get("model"):
                    f.link(r["id"], f.model(r.get("provider"), r["model"]), "used")
                f.link(r["id"], p.id, "part_of")
                for w in r.get("work") or []:
                    aid = f.add(f"{r['id']}/{w['id']}", "agent", f"{w.get('role', 'agent')}: {w.get('objective', '')}",
                                text=w.get("objective", ""), t=r.get("created_at"), status=w.get("status"),
                                project=p.id, weight=0.7,
                                meta={"role": w.get("role"), "artifacts": list(w.get("artifacts") or []),
                                      "require_tests": w.get("require_tests"), "run": r["id"], "work": w["id"]})
                    f.entities[aid]["meta"]["attempts"] = [
                        {"status": a.get("status"), "problems": (a.get("evidence") or {}).get("problems") or [],
                         "pending": len(a.get("pending") or []), "finished_at": a.get("finished_at")}
                        for a in (r.get("attempts") or []) if a.get("work_id") == w["id"]][-4:]
                    f.link(aid, r["id"], "part_of")
                    for dep in w.get("depends_on") or []:
                        f.link(aid, f"{r['id']}/{dep}", "depends_on")
                for a in r.get("attempts") or []:
                    if a.get("session_id"):
                        session_owner[a["session_id"]] = f"{r['id']}/{a.get('work_id')}"
        for t in queue.list(uid, p.id, limit=1000):
            args = t.args or {}
            what = args.get("path") or args.get("query") or args.get("reason") or ""
            f.add(t.id, "task", f"{t.tool} {what}".strip(), text=args.get("reason") or "", t=t.created_at,
                  status=t.status, project=p.id, weight=0.55 if t.status == "succeeded" else 0.65,
                  meta={"tool": t.tool, "risk": t.risk, "reason": t.reason, "requested_by": t.requested_by,
                        "started_at": t.started_at, "finished_at": t.finished_at, "duration_ms": t.duration_ms,
                        "exit_code": (t.result or {}).get("exit_code") if isinstance(t.result, dict) else None,
                        "session": t.session_id, "trace": _trace(t)})
            f.link(t.id, session_owner.get(t.session_id or "", p.id), "ran_for")
        from .exchange import author_label, claim_status
        for c in ledger.list(uid, p.id):
            author = c.get("author") if isinstance(c.get("author"), dict) else {}
            f.add(c["id"], "claim", c.get("statement") or "claim", text=c.get("uncertainty") or "",
                  t=c.get("created_at"), status=c.get("status") or claim_status(c), project=p.id, weight=0.7,
                  source=author_label(author),
                  meta={"assessments": [{"verdict": a.get("verdict"), "by": a.get("by") or a.get("model")}
                                        for a in (c.get("assessments") or [])][:8],
                        "resolution": c.get("resolution"), "evidence_refs": list(c.get("evidence_refs") or [])[:20]})
            f.link(c["id"], p.id, "part_of")
            for ref in c.get("evidence_refs") or []:
                f.link(str(ref), c["id"], "evidence")


def _challenges(f: _Field, ci, uid: str) -> None:
    from . import challenge
    for rec in challenge.records(ci, uid):
        ch = rec.get("challenge") or {}
        unc = ch.get("uncertainty") or {}
        hid = f.add(rec["id"], "hypothesis", rec.get("claim"), text=ch.get("verdict") or "", t=rec.get("ts"),
                    status=rec.get("status"), weight=0.9,
                    source=rec.get("served_by") or "", meta={
                        "p_claim_holds": unc.get("p_claim_holds"), "p_label": unc.get("label"),
                        "drivers": unc.get("drivers") or [], "parsed": rec.get("parsed"),
                        "what_would_change_my_mind": ch.get("what_would_change_my_mind"),
                        "note": rec.get("note"), "resolved_at": rec.get("resolved_at"),
                        "revises": rec.get("revises"), "evidence_offered": rec.get("evidence_offered")})
        if rec.get("revises"):
            f.link(hid, rec["revises"], "revises")
        if rec.get("interrogates"):
            f.link(hid, rec["interrogates"], "interrogates")
        weights = {"high": 0.85, "medium": 0.6, "low": 0.4}
        for k, a in enumerate(ch.get("assumptions") or []):
            aid = f.add(f"{rec['id']}/a{k}", "assumption", a.get("text"), text=a.get("evidence_given") or "",
                        t=rec.get("ts"), status=a.get("load_bearing"), weight=weights.get(a.get("load_bearing"), 0.4),
                        source=rec.get("served_by") or "", meta={"load_bearing": a.get("load_bearing"),
                                                                 "evidence_given": a.get("evidence_given")})
            f.link(aid, hid, "supports")
        for k, c in enumerate(ch.get("counterexamples") or []):
            f.link(f.add(f"{rec['id']}/c{k}", "counter", c, t=rec.get("ts"), status="against", weight=0.6,
                         source=rec.get("served_by") or ""), hid, "contradicts")
        if ch.get("strongest_objection"):
            f.link(f.add(f"{rec['id']}/alt", "alternative", ch["strongest_objection"], t=rec.get("ts"),
                         status="alternative", weight=0.75, source=rec.get("served_by") or ""), hid, "contradicts")
        fals = ch.get("falsification") or {}
        if fals.get("experiment"):
            f.link(f.add(f"{rec['id']}/x", "experiment", fals["experiment"],
                         text=" · ".join(f"{k}: {fals[k]}" for k in ("if_true", "if_false", "cost") if fals.get(k)),
                         t=rec.get("ts"), status=rec.get("status"), weight=0.7, meta=fals), hid, "tests")


def _mastery_history(ci, uid: str) -> dict:
    from .growth import BKT
    hist: dict[str, list] = {}
    state: dict[str, BKT] = {}
    for a in ci.attempts(uid):
        if a.get("assisted"):
            continue
        b = state.setdefault(a["skill"], BKT())
        b.observe(bool(a.get("correct")))
        hist.setdefault(a["skill"], []).append([a.get("ts"), round(b.p_mastery, 3)])
    return {k: v[-200:] for k, v in hist.items()}


def _skills(f: _Field, ci, uid: str) -> None:
    report = {}
    try:
        report = ci.skill_report(uid) or {}
    except Exception:
        report = {}
    per = report.get("skills") if isinstance(report.get("skills"), dict) else {}
    try:
        history = _mastery_history(ci, uid)
    except Exception:
        history = {}
    for name, state in ci.store.list_skills(uid).items():
        p = float(state.get("p_mastery", state.get("p_known", 0.0)) or 0.0)
        status = "mastered" if p >= 0.95 else "learning" if p >= 0.4 else "gap"
        sid = f.add(f"skill:{name}", "skill", name, t=state.get("updated_at"), status=status, weight=0.3 + 0.7 * p,
                    meta={"p_mastery": round(p, 3), "path": [s.strip() for s in name.replace(DEMO_MARK, "").split("/")],
                          "review": (per.get(name) or {}).get("review"),
                          "unassisted": (per.get(name) or {}).get("unassisted"),
                          "assisted": (per.get(name) or {}).get("assisted"),
                          "dependency_gap": (per.get(name) or {}).get("dependency_gap"),
                          "trend": ((per.get(name) or {}).get("trend") or {}).get("direction"),
                          "history": history.get(name, [])})
        parts = [s.strip() for s in name.split("/")]
        if len(parts) > 1:  # "mathematics / linear algebra / eigenvalues": a prerequisite tree by path
            f.link(sid, f"skill:{' / '.join(parts[:-1])}" if " / " in name else f"skill:{'/'.join(parts[:-1])}",
                   "part_of")


def _plans(f: _Field, ci, uid: str) -> None:
    from . import intent_compiler
    for rec in intent_compiler.records(ci, uid):
        spec = rec.get("spec") or {}
        pid = f.add(rec["id"], "plan", spec.get("goal") or rec.get("text"), text=rec.get("text") or "",
                    t=rec.get("ts"), status=rec.get("status") or ("approved" if rec.get("approved") else "compiled"),
                    project=rec.get("project_id"), weight=0.75, source=rec.get("served_by") or "")
        if rec.get("project_id"):
            f.link(pid, rec["project_id"], "part_of")
        for o in spec.get("outcomes") or []:
            f.link(f.add(f"{rec['id']}/{o['id']}", "objective", o.get("text"), text=o.get("done_when") or "",
                         t=rec.get("ts"), status=o.get("kind"), project=rec.get("project_id"), weight=0.6), pid, "part_of")
        for s in spec.get("steps") or []:
            sid = f.add(f"{rec['id']}/{s['id']}", "objective", s.get("action"), t=rec.get("ts"), status=s.get("kind"),
                        project=rec.get("project_id"), weight=0.5, meta={"role": s.get("role"), "step": True})
            f.link(sid, pid, "part_of")
            for d in s.get("depends_on") or []:
                f.link(sid, f"{rec['id']}/{d}", "depends_on")
            for o in s.get("serves") or []:
                f.link(sid, f"{rec['id']}/{o}", "serves")


_git_cache: dict[tuple, list] = {}


def _git(f: _Field, ci, uid: str, ws) -> None:
    """Each authorized project's git history (subjects and dates; never authors), newest 300."""
    import subprocess
    seen = set()
    for p in ws.list_projects(uid):
        try:
            root = ws.workspace_auth(uid, p.id)["spec"]["root"]
        except Exception:
            continue
        if not root or root in seen:
            continue
        seen.add(root)
        try:
            head = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if head.returncode:
            continue
        key = (root, head.stdout.strip())
        if key not in _git_cache:
            log = subprocess.run(["git", "-C", root, "log", "-n", "300", "--date=iso-strict", "--format=%H%x1f%ad%x1f%s"],
                                 capture_output=True, text=True, timeout=15)
            _git_cache[key] = [ln.split("\x1f") for ln in log.stdout.splitlines() if ln.count("\x1f") == 2]
        prev = None
        for sha, when, subject in reversed(_git_cache[key]):  # oldest first, so each follows the one before
            cid = f"commit:{sha[:12]}"
            f.add(cid, "commit", subject, t=when, status="committed", project=p.id, weight=0.35,
                  source="git", meta={"sha": sha[:12], "repo": root.rstrip("/").rsplit("/", 1)[-1]})
            f.link(cid, p.id, "part_of")
            if prev:
                f.link(cid, prev, "follows")
            prev = cid


def _vault(f: _Field, ci, uid: str, ws) -> None:
    """The person's notes (Obsidian vault): shown here, on this machine. Whether ALTER may SEND vault
    notes to a model is a separate setting (`alter config vault`), which this does not change.
    Notes still in iCloud are listed by title, never downloaded by building the field."""
    import datetime as _dt
    from . import thirdeye
    from .vault import Vault
    # only a vault the person configured: never thirdeye's fallback search of ~/Documents, which on a
    # throwaway or demo home would find (and show, or publish) the real owner's notes
    path = (ws._settings(uid).get(thirdeye.SETTINGS_KEY) or {}).get("vault_path")
    if not path:
        return
    v = Vault(path, fetch_timeout=None)
    names = list(v.notes)[:1500]
    for name in names:
        d = v.notes[name]
        rel = d["path"]
        body = LINK_STRIP.sub(lambda m: m.group(1), d["body"]).strip()
        f.add(f"note:{rel}", "knowledge", name, text=body[:500],
              t=_dt.datetime.fromtimestamp(d["mtime"], _dt.timezone.utc), status="note",
              weight=min(1.0, 0.35 + 0.08 * len(v.backlinks.get(name, ()))), source="vault",
              meta={"path": rel, "area": rel.split("/")[0] if "/" in rel else "", "tags": list(d["tags"])[:8],
                    "backlinks": len(v.backlinks.get(name, ())), "downloaded": True})
    for name in names:
        for link in v.notes[name]["links"]:
            target = v.resolve(link)
            if target and target in v.notes:
                f.link(f"note:{v.notes[name]['path']}", f"note:{v.notes[target]['path']}", "links_to")
    for rel in v.not_downloaded[:500]:
        f.add(f"note:{rel}", "knowledge", rel.rsplit("/", 1)[-1][:-3], text="", status="in iCloud", weight=0.3,
              source="vault", meta={"path": rel, "area": rel.split("/")[0] if "/" in rel else "", "downloaded": False})


DEBATES_STATE = "mayank_debates"
SIDES = ("for", "against")


def debates(ci, uid: str) -> list[dict]:
    return ci._state_json(uid, DEBATES_STATE, [])[1]


def _write_debates(ci, uid: str, version, log) -> None:
    blob = json.dumps(log[-200:]).encode()
    if version is None:
        ci.store.save_policy_state(uid, DEBATES_STATE, blob, note="mayank debates")
    else:
        ci.store.overwrite_policy_state(uid, DEBATES_STATE, version, blob)


def _debates(f: _Field, ci, uid: str) -> None:
    for d in debates(ci, uid):
        ps = [t["p"] for t in d["turns"] if t.get("p") is not None]
        p = (d.get("judge") or {}).get("p")
        did = f.add(d["id"], "debate", d["claim"], text=(d.get("judge") or {}).get("text") or "", t=d["ts"],
                    status="judged" if d.get("judge") else f"{len(d['turns'])} turns", weight=0.9,
                    source=" vs ".join(m or "ALTER's choice" for m in d.get("models") or []),
                    meta={"p_claim_holds": p if p is not None else (sum(ps) / len(ps) if ps else None),
                          "p_label": "the judge's estimate" if p is not None else "the debaters' average estimate",
                          "turns": len(d["turns"]), "models": d.get("models"), "judge_model": (d.get("judge") or {}).get("served_by")})
        for k, t in enumerate(d["turns"]):
            aid = f.add(f"{d['id']}/t{k}", "argument", t["text"][:160], text=t["text"], t=t["ts"], status=t["side"],
                        weight=0.7, source=t.get("served_by") or "",
                        meta={"side": t["side"], "p": t.get("p"), "round": k // 2 + 1, "model": t.get("served_by")})
            f.link(aid, did, "supports" if t["side"] == "for" else "contradicts")
            for i in t.get("sent") or []:
                f.link(i, aid, "informed")


def _exchanges(f: _Field, ci, uid: str) -> None:
    for r in ci.store.list_interactions(uid, with_payload=True)[-400:]:
        route = (r.get("receipt") or {}).get("route") or {}
        f.add(r["id"], "exchange", r.get("task") or route.get("purpose") or "request",
              text=r.get("response") or "", t=r.get("ts"), status=route.get("purpose") or r.get("task_type") or "",
              weight=0.35, source=route.get("served_by") or r.get("model") or "",
              meta={"model": r.get("model"), "provider": r.get("provider"), "cost_usd": r.get("cost_usd"),
                    "mode": r.get("assist_mode"), "context_items": len(r.get("selected") or []),
                    "reward": r.get("reward")})
        for i in (r.get("selected") or [])[:40]:
            f.link(i, r["id"], "informed")
        if r.get("model"):
            f.link(r["id"], f.model(r.get("provider"), r["model"]), "served_by")


# --------------------------------------------------------------------------- field
def audit_seq(ci, uid: str) -> int:
    row = ci.store.query("SELECT MAX(seq) AS s FROM audit WHERE user_id=?", (uid,))
    return int(row[0]["s"] or 0) if row else 0


def run_seq(ci, uid: str) -> int:
    """Specialist runs keep their own event log (specialist_events), not the audit chain, so the
    field watches both. Run and project ids are plaintext there; the events themselves stay sealed."""
    try:
        row = ci.store.query("SELECT MAX(seq) AS s FROM specialist_events WHERE user_id=?", (uid,))
    except Exception:  # no run has ever been stored here
        return 0
    return int(row[0]["s"] or 0) if row else 0


def build(ci, uid: str, *, ws, queue, ledger, runs=None) -> dict:
    f = _Field()
    f.source("memory", lambda: _memory(f, ci, uid))
    f.source("workspace", lambda: _workspace(f, ci, uid, ws, queue, ledger, runs))
    f.source("hypotheses", lambda: _challenges(f, ci, uid))
    f.source("skills", lambda: _skills(f, ci, uid))
    f.source("plans", lambda: _plans(f, ci, uid))
    f.source("exchanges", lambda: _exchanges(f, ci, uid))
    f.source("debates", lambda: _debates(f, ci, uid))
    f.source("vault", lambda: _vault(f, ci, uid, ws))
    f.source("git", lambda: _git(f, ci, uid, ws))
    ents = list(f.entities.values())
    if len(ents) > MAX_ENTITIES:  # keep the newest; the count says what was left out
        ents.sort(key=lambda e: e["t"] or "", reverse=True)
        ents = ents[:MAX_ENTITIES]
    ids = {e["id"] for e in ents}
    links, seen = [], set()
    for ln in f.links:
        key = (ln["a"], ln["b"], ln["rel"])
        if ln["a"] in ids and ln["b"] in ids and key not in seen:  # noqa: SIM102
            seen.add(key)
            links.append(ln)
    xyz, embedder = _project(ci, ents)
    for e, p in zip(ents, xyz):
        e["xyz"] = [round(float(v), 4) for v in p]
    kinds: dict[str, int] = {}
    for e in ents:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    try:  # the skill whose predicted unassisted success is nearest 0.85 (growth.next_challenge)
        next_challenge = ci.next_challenge(uid)
    except Exception:
        next_challenge = None
    return {"entities": ents, "links": links, "sources": f.sources, "kinds": kinds, "next_challenge": next_challenge,
            "omitted": max(0, len(f.entities) - len(ents)), "demo": sum(e["demo"] for e in ents),
            "cursor": audit_seq(ci, uid), "run_cursor": run_seq(ci, uid), "embedder": embedder, "regions": REGIONS}


def field(ci, uid: str, **kw) -> dict:
    """The field, rebuilt when the audit chain or a run's event log has moved, or after FIELD_TTL_S."""
    seq = (audit_seq(ci, uid), run_seq(ci, uid))
    with _cache_lock:
        hit = _fields.get(uid)
    if hit and hit[0] == seq and time.monotonic() - hit[1] < FIELD_TTL_S:
        return hit[2]
    built = build(ci, uid, **kw)
    with _cache_lock:
        _fields[uid] = (seq, time.monotonic(), built)
    return built


def _text_of(e: dict) -> str:
    return f"{e['label']} {e['text'][:240]}".strip()


def _vectors_for(ci, texts: list[str]) -> np.ndarray:
    keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
    with _cache_lock:
        missing = [(k, t) for k, t in zip(keys, texts) if k not in _vectors]
    if missing:
        fresh = ci.embedder.embed([t for _, t in missing])
        with _cache_lock:
            if len(_vectors) > 50000:
                _vectors.clear()
            for (k, _), v in zip(missing, fresh):
                _vectors[k] = np.asarray(v, dtype=np.float32)
    with _cache_lock:
        return np.stack([_vectors[k] for k in keys]) if keys else np.zeros((0, 1), dtype=np.float32)


def _project(ci, ents: list[dict]) -> tuple[np.ndarray, str]:
    """Each entity's text, embedded by the store's embedder, projected to 3D (PCA), in [-1, 1]."""
    name = getattr(ci.embedder, "name", type(ci.embedder).__name__)
    if not ents:
        return np.zeros((0, 3)), name
    v = _vectors_for(ci, [_text_of(e) for e in ents]).astype(np.float64)
    v = v - v.mean(axis=0, keepdims=True)
    if len(ents) < 4 or not np.any(v):
        out = np.zeros((len(ents), 3))
        out[:, : min(3, v.shape[1])] = v[:, :3]
    else:
        _, _, vt = np.linalg.svd(v, full_matrices=False)
        out = v @ vt[:3].T
        if out.shape[1] < 3:
            out = np.pad(out, ((0, 0), (0, 3 - out.shape[1])))
    scale = np.percentile(np.abs(out), 98, axis=0)
    scale[scale == 0] = 1.0
    return np.clip(out / scale, -1.0, 1.0), name


def search(ci, uid: str, q: str, k: int = 24, **kw) -> dict:
    """Entities nearest the query by the store's embedder, plus exact word matches (named first)."""
    q = " ".join(str(q or "").split())[:300]
    if not q:
        return {"query": q, "results": []}
    ents = field(ci, uid, **kw)["entities"]
    if not ents:
        return {"query": q, "results": []}
    m = _vectors_for(ci, [_text_of(e) for e in ents])
    qv = np.asarray(ci.embedder.embed([q])[0], dtype=np.float32)
    sims = m @ qv
    words = [w for w in q.lower().split() if len(w) > 2]
    scored = []
    for e, s in zip(ents, sims):
        hay = _text_of(e).lower()
        lexical = sum(w in hay for w in words) / len(words) if words else 0.0
        scored.append((float(s) + 0.5 * lexical, float(s), lexical, e["id"]))
    scored.sort(reverse=True)
    return {"query": q, "embedder": getattr(ci.embedder, "name", type(ci.embedder).__name__),
            "results": [{"id": i, "score": round(sc, 4), "semantic": round(se, 4), "lexical": round(lx, 3)}
                        for sc, se, lx, i in scored[:max(1, min(k, 200))]]}


# --------------------------------------------------------------------------- live
def events(ci, uid: str, after: int, wait: float = 20.0, poll: float = 0.4, run_after: int | None = None) -> dict:
    """Audit entries after `after` (and run events after `run_after`), waiting up to `wait` seconds
    for the first one (long poll). A run event names its run and project, so their points light."""
    deadline = time.monotonic() + max(0.0, min(wait, 25.0))
    while True:
        rows = ci.store.query("SELECT seq, ts, event, payload FROM audit WHERE user_id=? AND seq>? ORDER BY seq LIMIT 200",
                              (uid, int(after)))
        runs = []
        if run_after is not None:
            try:
                runs = ci.store.query("SELECT seq, run_id, project_id FROM specialist_events WHERE user_id=? AND seq>? "
                                      "ORDER BY seq LIMIT 200", (uid, int(run_after)))
            except Exception:
                runs = []
        if rows or runs or time.monotonic() >= deadline:
            out = []
            for r in rows:
                try:
                    payload = json.loads(r["payload"])
                except (TypeError, ValueError):
                    payload = {}
                refs = sorted({str(v) for v in payload.values() if isinstance(v, str) and len(v) < 120})
                out.append({"seq": r["seq"], "ts": r["ts"], "event": r["event"], "refs": refs,
                            "payload": {k: v for k, v in payload.items() if isinstance(v, (str, int, float, bool))}})
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            for r in runs:
                out.append({"seq": None, "run_seq": r["seq"], "ts": now, "event": "run.update",
                            "refs": [r["run_id"], r["project_id"]], "payload": {}})
            result = {"events": out, "cursor": rows[-1]["seq"] if rows else int(after)}
            if run_after is not None:
                result["run_cursor"] = runs[-1]["seq"] if runs else int(run_after)
            return result
        time.sleep(poll)


def challenge_with_evidence(ci, uid: str, challenge_id: str, evidence: str, **kw) -> dict:
    """Put the person's new evidence to the same challenge again, as a new record that revises the
    old one, so the change in the model's estimate (or its refusal to change) is on the record."""
    from . import challenge
    old = next((r for r in challenge.records(ci, uid) if r["id"] == challenge_id), None)
    if old is None:
        raise KeyError(challenge_id)
    evidence = " ".join(str(evidence or "").split())[:1500]
    if not evidence:
        raise ValueError("evidence is empty")
    prompt = (f"{old['claim']}\n\nNew evidence from the person, to weigh against the earlier challenge "
              f"(revise the estimate only as far as this evidence warrants, and say why):\n{evidence}")
    rec = challenge.challenge(ci, uid, prompt, **kw)
    version, log = challenge._log(ci, uid)
    for r in log:
        if r["id"] == rec["id"]:
            r.update(claim=old["claim"], revises=challenge_id, evidence_offered=evidence)
    challenge._write(ci, uid, version, log)
    return rec | {"claim": old["claim"], "revises": challenge_id, "evidence_offered": evidence}


def ask(ci, uid: str, question: str, **kw) -> dict:
    """A question to MAYANK, answered through ALTER's routing (consent, egress, audit as always). The
    answer comes back with the records that were sent with it, so the page can show what it rests on."""
    question = " ".join(str(question or "").split())[:4000]
    if not question:
        raise ValueError("the question is empty")
    r = ci.ask_routed(uid, question, **kw)
    rec = r.receipt or {}
    return {"interaction_id": r.interaction_id, "text": r.text,
            "served_by": (rec.get("route") or {}).get("served_by") or rec.get("model"),
            "cost_usd": rec.get("cost_usd"), "sent": [s["id"] for s in rec.get("sent") or []],
            "withheld": len(rec.get("withheld_by_egress") or [])}


def challenge_assumption(ci, uid: str, challenge_id: str, k: int, **kw) -> dict:
    """Interrogate one assumption of a hypothesis on its own: the assumption becomes the claim under
    challenge, and the new record says which assumption of which hypothesis it examines."""
    from . import challenge
    old = next((r for r in challenge.records(ci, uid) if r["id"] == challenge_id), None)
    if old is None:
        raise KeyError(challenge_id)
    assumptions = (old.get("challenge") or {}).get("assumptions") or []
    if not 0 <= k < len(assumptions):
        raise KeyError(f"{challenge_id}/a{k}")
    text = assumptions[k]["text"]
    rec = challenge.challenge(ci, uid, text, **kw)
    version, log = challenge._log(ci, uid)
    for r in log:
        if r["id"] == rec["id"]:
            r.update(interrogates=f"{challenge_id}/a{k}", about=challenge_id)
    challenge._write(ci, uid, version, log)
    return rec | {"interrogates": f"{challenge_id}/a{k}", "about": challenge_id}


P_LINE = re.compile(r"P\(claim holds\)\s*[=:]\s*(0(?:\.\d+)?|1(?:\.0+)?)", re.I)


def _p(text: str):
    m = P_LINE.search(text or "")
    return float(m.group(1)) if m else None


def debate_start(ci, uid: str, claim: str, models=(None, None, None)) -> dict:
    """Two models take opposite sides of a claim, citing the person's own records; a third judges."""
    claim = " ".join(str(claim or "").split())[:500]
    if len(claim) < 3:
        raise ValueError("the claim is empty")
    models = list(models or (None, None, None))[:3] + [None] * (3 - len(list(models or [])[:3]))
    from . import registry
    ok = {r["key"] for r in registry.describe({}) if r.get("available")}
    for m in models:
        if m is not None and m not in ok:
            raise ValueError(f"model {m!r} is not available here")
    from .types import new_id, utcnow
    rec = {"id": new_id("deb"), "ts": utcnow().isoformat(), "claim": claim, "models": models, "turns": [], "judge": None}
    version, log = ci._state_json(uid, DEBATES_STATE, [])
    _write_debates(ci, uid, version, log + [rec])
    ci.audit.append(uid, "debate.new", debate=rec["id"])
    return rec


def _find(ci, uid, did):
    version, log = ci._state_json(uid, DEBATES_STATE, [])
    for d in log:
        if d["id"] == did:
            return version, log, d
    raise KeyError(did)


MAX_TURNS = 6


def debate_turn(ci, uid: str, did: str) -> dict:
    """The next turn: sides alternate, each sees the arguments so far and the records ALTER selects."""
    from .types import utcnow
    _, _, d = _find(ci, uid, did)
    if len(d["turns"]) >= MAX_TURNS:
        raise ValueError("this debate has had its turns; ask for the judge")
    side = SIDES[len(d["turns"]) % 2]
    so_far = "\n".join(f"{t['side'].upper()}: {t['text'][:600]}" for t in d["turns"][-4:]) or "(none yet)"
    prompt = (f"A structured debate for the person whose records you are given.\nClaim: {d['claim']}\n"
              f"Your side: argue that the claim {'HOLDS' if side == 'for' else 'DOES NOT HOLD'}. Argue honestly: concede "
              f"what the evidence does not support, and answer the other side's strongest point.\n"
              f"Arguments so far:\n{so_far}\nUse the person's records you were given as evidence and cite them by "
              f"their bracket numbers. At most 120 words, then a last line exactly like "
              f"\"P(claim holds) = 0.xx\" with your honest estimate (not your side's).")
    r = ci.ask_routed(uid, prompt, pinned=d["models"][0 if side == "for" else 1])
    rec = r.receipt or {}
    turn = {"side": side, "text": (r.text or "").strip()[:2000], "p": _p(r.text), "ts": utcnow().isoformat(),
            "served_by": (rec.get("route") or {}).get("served_by"), "interaction": r.interaction_id,
            "sent": [x["id"] for x in rec.get("sent") or []]}
    version, log, d = _find(ci, uid, did)
    d["turns"].append(turn)
    _write_debates(ci, uid, version, log)
    ci.audit.append(uid, "debate.turn", debate=did, side=side, interaction=r.interaction_id)
    return turn


def debate_judge(ci, uid: str, did: str) -> dict:
    from .types import utcnow
    _, _, d = _find(ci, uid, did)
    if not d["turns"]:
        raise ValueError("nothing to judge yet")
    both = "\n\n".join(f"{t['side'].upper()} ({t.get('served_by') or 'model'}): {t['text'][:900]}" for t in d["turns"])
    prompt = (f"Judge this debate for the person.\nClaim: {d['claim']}\n\n{both}\n\nSay which arguments the evidence "
              f"actually supports, name the strongest point on each side, and what evidence would settle it. At most "
              f"120 words, then a last line exactly like \"P(claim holds) = 0.xx\".")
    r = ci.ask_routed(uid, prompt, pinned=d["models"][2])
    rec = r.receipt or {}
    judge = {"text": (r.text or "").strip()[:2000], "p": _p(r.text), "ts": utcnow().isoformat(),
             "served_by": (rec.get("route") or {}).get("served_by"), "interaction": r.interaction_id}
    version, log, d = _find(ci, uid, did)
    d["judge"] = judge
    _write_debates(ci, uid, version, log)
    ci.audit.append(uid, "debate.judge", debate=did, interaction=r.interaction_id)
    return judge


def history(ci, uid: str, limit: int = 4000) -> dict:
    """The audit chain in time order (names, times and the ids each entry names), for the time-lapse."""
    rows = ci.store.query("SELECT seq, ts, event, payload FROM audit WHERE user_id=? ORDER BY seq DESC LIMIT ?",
                          (uid, int(max(1, min(limit, 20000)))))
    out = []
    for r in reversed(rows):
        try:
            payload = json.loads(r["payload"])
        except (TypeError, ValueError):
            payload = {}
        out.append({"seq": r["seq"], "ts": r["ts"], "event": r["event"],
                    "refs": sorted({str(v) for v in payload.values() if isinstance(v, str) and len(v) < 120})})
    return {"events": out}

