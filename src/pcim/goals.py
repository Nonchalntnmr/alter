"""Goals: durable, resumable work for ALTER.

A goal is one objective in one authorized project folder. ALTER works on it through the governed
workspace agent, which is the direct-execution baseline the evaluations measured. Each agent turn
is a checkpoint, so a goal survives a closed terminal, a crash or a sleeping laptop.

**State.** A goal's row, and its events, are stored in the same SQLite store as everything else.
The objective, the checkpoints and the context brief are sealed with the project's key. The
statuses are:
- active: queued, running, waiting_approval, waiting_answer, paused;
- final: done, unverified, failed, stopped, cancelled.

A goal recorded as `running` with no live worker is shown as **interrupted**. Liveness comes from an
OS file lock, which the kernel releases when the worker dies, so nothing is guessed from
timestamps.

**Talking to a running goal.** You add events: steer (a new requirement), pause or cancel. The agent
checks for one before every model step (`Agent.pause_check`) and stops at that safe point, with
every action so far recorded. The runner then pauses, cancels, or sends your words as the next
message, so you lose no progress.

**Limits.** Each goal has a turn limit, a cost limit and a time window. When the window ends, the
goal pauses: work never continues silently past the time it was given. Each agent turn also keeps
its own limits (`agent.TurnLimits`). Writes and test runs need your approval, unless an autonomy
grant covers them (`alter permissions autonomy grant`).

**Done means verified, and not disowned.**
- The verdict is computed from task records across the goal's sessions, never from the model's
  words: `verified` means the project's tests passed after the last change.
- When the agent stops with unverified changes, it is asked at most twice to verify them.
- After that, the goal ends `unverified`, and it is reported as such.
- Passing tests are necessary, not sufficient. The agent ends each report with an outcome line.
  If it says the request is **not reached**, the goal waits for the person; it is never `done`.
  - The words can only withhold "done", never grant it.
  - eval_v7 found the case this closes: a goal added a helper that nothing calls, its own tests
    passed, and it ended `done` while its report said the preview was unchanged.
- **A correction.** `alter say <goal> "…"` to a finished goal (`/fix …` in the `alter` conversation)
  starts a follow-up goal that records the goal it corrects. If the follow-up changes nothing, it
  waits for the person, whatever its outcome line says: its "reached" is the claim the person has
  just rejected.
  - eval_v8 found the case this closes (P01-t04): a follow-up after two corrections changed nothing,
    said `Outcome: reached`, and ended `done` through the nothing-changed path.
  - `alter goals accept <goal>` closes a goal that waits for you as done, on your word. It is
    recorded as yours (`done_by: person`), never as ALTER's "done".

**Learning.**
- When a goal ends, its outcome is written to memory as an episode with full provenance (goal,
  sessions, verdict, files changed, cost), if you granted ALTER that (`alter init` asks).
- The outcome is computed from records. The model's own summary is kept in the goal, labelled
  model-written, and never stored as a fact.
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .types import Trust, new_id, utcnow

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS goals (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL,
  worker_pid INTEGER,
  payload_ct BLOB NOT NULL
)""",
    "CREATE INDEX IF NOT EXISTS goals_project ON goals(user_id, project_id, created_at)",
    """CREATE TABLE IF NOT EXISTS goal_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  goal_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  consumed_at TEXT,
  payload_ct BLOB
)""",
    "CREATE INDEX IF NOT EXISTS goal_events_goal ON goal_events(goal_id, consumed_at)",
)
ACTIVE = ("queued", "running", "waiting_approval", "waiting_answer", "paused")
FINAL = ("done", "unverified", "failed", "stopped", "cancelled")
EVENT_KINDS = ("steer", "pause", "cancel")
MEMORY_SOURCE = "alter-goals"
DEFAULTS = {"max_turns": 6, "max_cost_usd": 5.0, "window_s": 30 * 60}
MAX_NUDGES = 2

# eval_v7 (G against direct): the goal asked 2 of the 14 questions its tasks needed, against
# direct's 9, and needed more corrections. The old text told it to state the outcome it assumed and
# to "choose the smallest scope you can finish", so it guessed and shipped a part. The asking rule
# below is the one `ci do` uses (router.SELECTIVE_DIRECTIVES).
HOW = """How to work on this goal:
- Start by saying, in two to four lines, what you take the intended outcome to be, citing the labels in the context above that you rely on.
- Read what you need in the project first; most details are settled by the files.
- Ask the person one short question, and stop, only if the request leaves open a choice that changes the result and neither the files nor the context settle it: which of two behaviours they want, exactly what to remove and what to keep, or a value only they know. Ask before changing anything. Never ask about what you can find out by reading. Otherwise go ahead, and state any small assumption.
- Do what the person asked, and no more. If part of it cannot be done here, say which part and why; never offer a partial change as the whole.
- After your last change, run the tests; if they fail, fix the cause and run them again.
- End with three short lists: what changed (files), the test result, and what remains. Label any recommendation as a recommendation.
- Last, one line on its own: `Outcome: reached` if what the person asked for now works, or `Outcome: not reached: <what is missing>` if it does not. Passing tests alone do not make it reached."""

_OUTCOME = re.compile(r"^[\W_]*outcome\b[\s:*_`—–-]*(not reached|reached)\b[\s:*_`.,;—–-]*(.*)$", re.I | re.M)


def outcome(text: str | None) -> tuple[str | None, str]:
    """The agent's own last outcome line: ("reached" | "not reached" | None, what it says is missing).
    It can only withhold "done", never grant it: the verdict still comes from the task records."""
    ms = list(_OUTCOME.finditer(text or ""))
    if not ms:
        return None, ""
    return ms[-1].group(1).lower(), ms[-1].group(2).strip().strip("`*_ ").rstrip(".")


class GoalError(RuntimeError):
    pass


class GoalBusy(GoalError):
    pass


class GoalUnreadable(GoalError):
    """The goal's sealed record cannot be opened (damaged ciphertext or wrapped key, or not JSON)."""


@dataclasses.dataclass
class Goal:
    id: str
    user_id: str
    project_id: str
    created_at: str
    updated_at: str
    status: str
    worker_pid: int | None
    payload: dict

    @property
    def objective(self) -> str:
        return self.payload["objective"]


# --------------------------------------------------------------------------- store
class GoalStore:
    def __init__(self, ci):
        self.ci, self.store = ci, ci.store
        for stmt in SCHEMA:
            self.store.execute(stmt)
        self.dir = Path(self.store.path).parent / "goals" if self.store.path != ":memory:" else None
        self.unreadable: list[str] = []  # ids the last list() could not open: damaged records, never shown as goals

    def _key(self, uid: str, pid: str) -> str:
        from .workspace import Workspace
        return Workspace(self.ci).project_key(uid, pid)

    def _goal(self, row) -> Goal:
        from cryptography.exceptions import InvalidTag
        key = self._key(row["user_id"], row["project_id"])  # KeyError: its project, and key, were deleted
        try:
            payload = self.store.open_json(key, row["payload_ct"], row["id"])
        except (InvalidTag, ValueError, TypeError) as e:
            # One damaged row must not take every goal command down with it: it is named, and can be
            # forgotten, while the others keep working.
            raise GoalUnreadable(f"goal {row['id']} cannot be read: its record is damaged ({type(e).__name__}). "
                                 f"`alter goals forget {row['id']}` removes it; a backup of pcim.db restores it") from None
        return Goal(row["id"], row["user_id"], row["project_id"], row["created_at"], row["updated_at"], row["status"],
                    row["worker_pid"], payload)

    def create(self, uid: str, project_id: str, objective: str, **limits) -> Goal:
        objective = " ".join(objective.split())
        if not objective:
            raise GoalError("a goal needs an objective")
        gid, now = new_id("goal"), utcnow().isoformat()
        payload = {"objective": objective, "checkpoints": [], "sessions": [], "session_costs": {}, "turns": 0,
                   "nudges": 0, "cost_unknown": False, "verification": None, "reason": None, "context": None,
                   "steers": [], "memory_item": None, "provider": None, "model": None,
                   **{k: limits.get(k) if limits.get(k) is not None else v for k, v in DEFAULTS.items()}}
        self.store.execute("INSERT INTO goals VALUES (?,?,?,?,?,?,?,?)",
                           (gid, uid, project_id, now, now, "queued", None,
                            self.store.seal_json(self._key(uid, project_id), payload, gid)))
        self.ci.audit.append(uid, "goal.create", goal=gid, project=project_id)
        return self.get(uid, gid)

    def get(self, uid: str, gid: str) -> Goal:
        rows = self.store.query("SELECT * FROM goals WHERE id=? AND user_id=?", (gid, uid))
        if not rows:
            raise KeyError(gid)
        return self._goal(rows[0])

    def resolve(self, uid: str, ref: str, project_id: str | None = None) -> str:
        """An exact id, a unique prefix ("goal_ebfa"), or a unique suffix of at least 4 characters."""
        from .intent_compiler import resolve_id
        # ids straight from the table: a goal whose record is damaged can still be named (to forget it)
        if project_id:
            rows = self.store.query("SELECT id FROM goals WHERE user_id=? AND project_id=? ORDER BY created_at DESC "
                                    "LIMIT 500", (uid, project_id))
        else:
            rows = self.store.query("SELECT id FROM goals WHERE user_id=? ORDER BY created_at DESC LIMIT 500", (uid,))
        ids = [r["id"] for r in rows]
        pre = [i for i in ids if len(ref) >= 6 and i.startswith(ref)]
        if ref not in ids and len(pre) > 1:
            raise ValueError(f"{ref} matches {len(pre)} goals; give more of the id")
        if ref not in ids and len(pre) == 1:
            return pre[0]
        return resolve_id(ids, ref, "goal")

    def list(self, uid: str, project_id: str | None = None, limit: int = 50) -> list[Goal]:
        if project_id:
            rows = self.store.query("SELECT * FROM goals WHERE user_id=? AND project_id=? ORDER BY created_at DESC LIMIT ?",
                                    (uid, project_id, limit))
        else:
            rows = self.store.query("SELECT * FROM goals WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (uid, limit))
        out, self.unreadable = [], []
        for r in rows:
            try:
                out.append(self._goal(r))
            except KeyError:  # its project was deleted, and its key with it: nothing left to show
                continue
            except GoalUnreadable:
                self.unreadable.append(r["id"])
        return out

    def save(self, g: Goal) -> None:
        g.updated_at = utcnow().isoformat()
        n = self.store.execute("UPDATE goals SET status=?, updated_at=?, worker_pid=?, payload_ct=? WHERE id=? AND user_id=?",
                               (g.status, g.updated_at, g.worker_pid,
                                self.store.seal_json(self._key(g.user_id, g.project_id), g.payload, g.id), g.id,
                                g.user_id)).rowcount
        if n != 1:
            raise GoalError(f"goal {g.id} no longer exists (it was forgotten)")

    def delete(self, uid: str, gid: str) -> None:
        try:
            with self.worker_lock(gid):  # held while deleting: no worker can start in between
                self.store.execute("DELETE FROM goal_events WHERE goal_id=? AND user_id=?", (gid, uid))
                self.store.execute("DELETE FROM goals WHERE id=? AND user_id=?", (gid, uid))
                self.store.flush_deleted()
        except GoalBusy:
            raise GoalError(f"goal {gid} is running; pause or cancel it first (alter pause {gid})") from None
        for suffix in (".lock", ".log"):
            if self.dir is not None:
                (self.dir / f"{gid}{suffix}").unlink(missing_ok=True)
        self.ci.audit.append(uid, "goal.forget", goal=gid)

    # ---- events: how a person talks to a goal while it runs
    def add_event(self, uid: str, gid: str, kind: str, text: str = "") -> int:
        if kind not in EVENT_KINDS:
            raise GoalError(f"event kind must be one of {EVENT_KINDS}")
        g = self.get(uid, gid)
        if g.status in FINAL:
            raise GoalError(f"goal {gid} is {g.status}; start a new goal instead")
        ct = self.store.seal_json(self._key(uid, g.project_id), {"text": " ".join(text.split())[:2000]}, f"{gid}:event")
        cur = self.store.execute("INSERT INTO goal_events(goal_id, user_id, at, kind, consumed_at, payload_ct) VALUES (?,?,?,?,?,?)",
                                 (gid, uid, utcnow().isoformat(), kind, None, ct))
        self.ci.audit.append(uid, f"goal.{kind}", goal=gid)
        return cur.lastrowid

    def pending_events(self, uid: str, gid: str) -> list[dict]:
        g = self.get(uid, gid)
        key = self._key(uid, g.project_id)
        rows = self.store.query("SELECT * FROM goal_events WHERE goal_id=? AND user_id=? AND consumed_at IS NULL ORDER BY id",
                                (gid, uid))
        return [{"id": r["id"], "kind": r["kind"], "at": r["at"],
                 "text": (self.store.open_json(key, r["payload_ct"], f"{gid}:event") or {}).get("text", "")} for r in rows]

    def last_event_id(self, gid: str) -> int:
        """The newest event of a goal, consumed or not: a resume carries it, so a pause given after the
        resume was asked for still wins over a worker that starts late."""
        r = self.store.query("SELECT max(id) FROM goal_events WHERE goal_id=?", (gid,))
        return int(r[0][0] or 0)

    def has_pending_event(self, gid: str, kind: str | None = None) -> bool:
        return bool(self.store.query("SELECT 1 FROM goal_events WHERE goal_id=? AND consumed_at IS NULL "
                                     "AND (? IS NULL OR kind=?) LIMIT 1", (gid, kind, kind)))

    def consume(self, ids: list[int]) -> None:
        now = utcnow().isoformat()
        for i in ids:
            self.store.execute("UPDATE goal_events SET consumed_at=? WHERE id=?", (now, i))

    # ---- liveness: an OS lock, released by the kernel when its holder dies
    def _lock_file(self, gid: str) -> Path:
        if self.dir is None:
            raise GoalError("goals need an on-disk store")
        self.dir.mkdir(mode=0o700, exist_ok=True)
        return self.dir / f"{gid}.lock"

    @contextlib.contextmanager
    def worker_lock(self, gid: str, wait_s: float = 0.0):
        import time
        f = open(self._lock_file(gid), "a")
        try:
            deadline = time.monotonic() + wait_s
            while True:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise GoalBusy(f"goal {gid} is already being worked on by another ALTER process") from None
                    time.sleep(0.1)
            yield
        finally:
            f.close()

    def is_live(self, gid: str) -> bool:
        if self.dir is None or not (self.dir / f"{gid}.lock").exists():
            return False
        with open(self.dir / f"{gid}.lock", "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(f, fcntl.LOCK_UN)
            return False

    def shown_status(self, g: Goal) -> str:
        return "interrupted" if g.status == "running" and not self.is_live(g.id) else g.status


# --------------------------------------------------------------------------- evidence
def evidence(ci, uid: str, project_id: str, session_ids: list[str]) -> list[dict]:
    from .governor import Governor
    from .tasks import TaskQueue
    from .workspace import Workspace
    if not session_ids:
        return []
    ws = Workspace(ci)
    sids = set(session_ids)
    return sorted((t.public() for t in TaskQueue(ws, Governor(ws)).list(uid, project_id, limit=2000) if t.session_id in sids),
                  key=lambda t: t["created_at"])


def verify(ci, uid: str, project_id: str, session_ids: list[str], final_text: str = "") -> dict:
    """The `ci do` evidence rule (verify_tasks), across every session of a goal."""
    return verify_tasks(evidence(ci, uid, project_id, session_ids), final_text)


def _at(t: dict, key: str) -> str:
    return t.get(key) or t.get("updated_at") or t["created_at"]


def verify_tasks(ts: list[dict], final_text: str = "") -> dict:
    """Verified means a test run that STARTED after the last write FINISHED passed, and it is the
    latest such run. Ordered by when things ran, not when they were proposed: a write approved after
    a test run that was proposed later must not borrow that run's pass."""
    from .terminal import _CLAIMS_DONE
    writes = sorted((t for t in ts if t["tool"] == "write_file" and t["status"] == "succeeded"),
                    key=lambda t: _at(t, "finished_at"))
    runs = sorted((t for t in ts if t["tool"] == "run_tests" and t["status"] == "succeeded"),
                  key=lambda t: _at(t, "started_at"))
    refused = [{"tool": t["tool"], "path": (t.get("args") or {}).get("path"), "status": t["status"]}
               for t in ts if t["status"] in ("denied", "cancelled", "expired")]
    last_write = _at(writes[-1], "finished_at") if writes else None
    after = [not t.get("reason") for t in runs if last_write and _at(t, "started_at") > last_write]
    if not writes:
        verdict, line = "unchanged", "no file was changed"
    elif after and after[-1]:
        verdict, line = "verified", "verified: the project's tests passed after the last change"
    elif after:
        verdict, line = "not_verified", "not verified: the last test run after the last change did not pass"
    else:
        verdict, line = "not_verified", "not verified: the tests were not run after the last change"
    files = list(dict.fromkeys((w.get("args") or {}).get("path") for w in writes))
    claims = bool(_CLAIMS_DONE.search(final_text or ""))
    return {"verdict": verdict, "line": line, "refused": refused, "writes": len(writes), "files": files,
            "test_runs": len(runs), "tests_passed": sum(not t.get("reason") for t in runs), "claims_done": claims,
            "claim_unverified": claims and verdict != "verified"}


def _task_line(t: dict) -> dict:
    a = t.get("args") or {}
    return {"id": t["id"], "tool": t["tool"], "status": t["status"], "reason": (t.get("reason") or "")[:160],
            "what": a.get("path") or " ".join(str(x) for x in (a.get("argv") or []))[:120]}


# --------------------------------------------------------------------------- runner
def here_for(ci, uid: str, project_id: str):
    from .terminal import Here
    from .workspace import Workspace
    ws = Workspace(ci)
    a = ws.workspace_auth(uid, project_id)
    if a is None:
        raise GoalError("this goal's project no longer has an authorized workspace")
    return Here(project_id, ws.get_project(uid, project_id).name, set(ws.provider_vendors(uid, project_id)),
                a["spec"]["root"])


class GoalRunner:
    def __init__(self, ci, uid: str, here, agent=None, out=None):
        from .terminal import _workspace_agent
        from .textsafe import terminal_safe
        self.ci, self.uid, self.here = ci, uid, here
        self.gs = GoalStore(ci)
        self.agent = agent or _workspace_agent(ci)
        self.out = out or (lambda s: print(terminal_safe(s), flush=True))  # model text never drives the terminal

    # ---- start
    def start(self, objective: str, provider: str | None = None, model: str | None = None,
              use_github: bool | None = None, use_vault: bool | None = None, **limits) -> Goal:
        from . import plan_exec as px
        if provider is None:
            if not self.here.vendors - {"mock"}:  # never run a goal on the test double without being asked to
                raise GoalError("no model may see this project yet: alter permissions provider claude-cli on")
            provider, model = px._model(self.here.vendors - {"mock"}, None, None)
        blocker = self._project_blocker(None)
        if blocker:
            raise GoalError(blocker)
        from . import thirdeye
        from .registry import vendor_of
        requested_at = utcnow().isoformat()  # the person's wait starts here, before the brief is gathered
        # gathered before the goal exists: it cannot list itself, and a failure leaves no half-made goal
        brief = thirdeye.gather(self.ci, self.uid, self.here, objective, use_github=use_github, use_vault=use_vault,
                                vendor=vendor_of(provider))
        g = self.gs.create(self.uid, self.here.project_id, objective, **limits)
        g.payload.update(provider=provider, model=model, use_github=use_github, use_vault=use_vault,
                         requested_at=requested_at)
        self._set_context(g, brief)
        return g

    def correct(self, prev: Goal, correction: str) -> Goal:
        """A follow-up goal from the person's correction of a finished goal: the request with every
        correction so far, the same model while this project still allows its vendor, and the limits
        the person gave. It records the goal it corrects, which `_after` reads."""
        from .registry import vendor_of
        correction = " ".join((correction or "").split())
        if not correction:
            raise GoalError(f'say what is still wrong: alter say {prev.id} "…"')
        if prev.status not in FINAL:
            raise GoalError(f"goal {prev.id} is {prev.status}: alter say adds to it while it runs")
        provider, model = prev.payload.get("provider"), prev.payload.get("model")
        try:
            same = provider is not None and vendor_of(provider) in self.here.vendors
        except ValueError:
            same = False
        g = self.start(f"{prev.objective}\n\nThat is not what I wanted yet: {correction}",
                       provider=provider if same else None, model=model if same else None,
                       use_github=prev.payload.get("use_github"), use_vault=prev.payload.get("use_vault"),
                       **{k: prev.payload.get(k) for k in DEFAULTS})
        g.payload["corrects"] = prev.id
        self.gs.save(g)
        self.ci.audit.append(self.uid, "goal.correct", goal=g.id, corrects=prev.id)
        return g

    def accept(self, gid: str) -> Goal:
        """The person closes a goal that waits for their answer as done, on their own word. Recorded as
        theirs (`done_by: person`), so a report never counts it as ALTER's "done". No model call."""
        g = self.gs.get(self.uid, gid)
        if g.status != "waiting_answer":
            raise GoalError(f"goal {gid} is {g.status}: only a goal that waits for your answer can be accepted")
        if self.gs.is_live(gid):
            raise GoalError(f"goal {gid} is running")
        g.payload["done_by"] = "person"
        return self._finish(g, "done", "you accepted it")

    def _project_blocker(self, g: Goal | None) -> str | None:
        """One goal at a time per project folder: the agent keeps one open session per project, and a new
        session for one goal would close another's, with any approval it waits on."""
        import datetime as dt
        recent = (utcnow() - dt.timedelta(minutes=5)).isoformat()
        for other in self.gs.list(self.uid, self.here.project_id, limit=50):
            if g is not None and other.id == g.id:
                continue
            if (other.status == "running" and self.gs.is_live(other.id)) or (other.status == "queued" and
                                                                             other.created_at >= recent):
                return f"goal {other.id} is {other.status} in this project; pause or cancel it first"
            if other.status == "waiting_approval" and pending_tasks(self.ci, self.uid, other):
                return f"goal {other.id} waits on your approval in this project (alter approve / alter deny)"
        for gid in self.gs.unreadable:  # a damaged record whose worker is still alive: wait for it
            if self.gs.is_live(gid):
                return f"goal {gid} is running in this project, but its record cannot be read; cancel it first"
        open_sid = self.agent.open_session(self.uid, self.here.project_id)
        mine = set(g.payload["sessions"]) if g is not None else set()
        if open_sid and open_sid not in mine and self.agent._load(self.uid, self.here.project_id, open_sid).get("pending"):
            return "a conversation in this project waits on your approval (alter tasks); resolve it first"
        return None

    def _set_context(self, g: Goal, brief) -> str:
        from . import thirdeye
        g.payload["context"] = {"text": brief.for_model(), "cites": brief.cites(), "withheld": brief.withheld,
                                "remembered": sum(e.kind == "remembered" for e in brief.evidence),
                                "sourced": sum(e.kind == "sourced" for e in brief.evidence)}
        self.gs.save(g)
        thirdeye.disclose(self.ci, self.uid, self.here.project_id, brief, g.id)
        return g.payload["context"]["text"]

    def _brief(self, g: Goal) -> str:
        """A fresh brief, under the person's current settings and the goal's vendor; recorded and disclosed."""
        from . import thirdeye
        from .registry import vendor_of
        brief = thirdeye.gather(self.ci, self.uid, self.here, g.objective, use_github=g.payload.get("use_github"),
                                use_vault=g.payload.get("use_vault"), vendor=vendor_of(g.payload["provider"]),
                                exclude_goal=g.id)
        return self._set_context(g, brief)

    # ---- messages
    def _first_message(self, g: Goal) -> str:
        text = g.payload["context"]["text"] if g.payload.get("context") else self._brief(g)
        return f"{text}\n\nGoal: {g.objective}\n\n{HOW}"

    def _resume_message(self, g: Goal) -> str:
        v = verify(self.ci, self.uid, g.project_id, g.payload["sessions"])
        done = ", ".join(v["files"]) or "none"
        return (f"{self._brief(g)}\n\nYou are resuming this goal in a new conversation. The records so far: "
                f"files changed: {done}; test runs: {v['test_runs']} ({v['tests_passed']} passed); {v['line']}.\n\n"
                f"Goal: {g.objective}\n\n{HOW}")

    # ---- the loop
    def run(self, gid: str, window_s: int | None = None, first_turn=None, resume: bool = False,
            resume_after: int | None = None) -> Goal:
        """Work on a goal. A paused goal runs only when `resume` is True, and not when a pause newer than
        the resume request (`resume_after`, an event id) parked it: a pause the person gave must never
        be undone by a worker that happened to start late."""
        with self.gs.worker_lock(gid, wait_s=2.0):
            return self._run_locked(gid, window_s, first_turn, resume, resume_after)

    def _limits_exhausted(self, g: Goal) -> str | None:
        if g.payload["turns"] >= g.payload["max_turns"]:
            return f"turn limit ({g.payload['max_turns']}) reached"
        cost = sum(c for c in g.payload["session_costs"].values() if c is not None)
        if cost >= g.payload["max_cost_usd"]:
            return f"cost limit (${g.payload['max_cost_usd']:.2f}) reached"
        return None

    def _run_locked(self, gid: str, window_s: int | None, first_turn, resume: bool,
                    resume_after: int | None = None) -> Goal:
        import dataclasses as dc
        import time
        g = self.gs.get(self.uid, gid)
        incoming = g.status
        if incoming in FINAL:
            return g
        if first_turn is None and incoming == "paused" and (
                not resume or (resume_after is not None and g.payload.get("pause_event", 0) > resume_after)):
            return g
        if first_turn is None and incoming == "waiting_approval" and pending_tasks(self.ci, self.uid, g):
            return g  # nothing to do until the person approves or refuses
        awaiting_answer = incoming == "waiting_answer" and first_turn is None
        if incoming == "running" and first_turn is None:  # this process holds the lock: the last worker died mid-run
            if self.agent.queue.recover_project_tests(self.uid, g.project_id, wait_s=1.0) is None:
                g.payload["reason"] = "a previous test command is still running in this project; resume after it exits"
                self.gs.save(g)
                return g
            g.payload.setdefault("interruptions", []).append(utcnow().isoformat())
        g.status, g.worker_pid, g.payload["reason"] = "running", os.getpid(), None
        self.gs.save(g)
        self.agent.pause_check = lambda: self.gs.has_pending_event(gid)
        self.agent.cancel_check = lambda: self.gs.has_pending_event(gid, "cancel")
        base_limits = self.agent.limits
        deadline = time.monotonic() + (window_s or g.payload["window_s"])
        message = None
        pending_turn = first_turn
        try:
            while True:
                if pending_turn is None:
                    ev = self.gs.pending_events(self.uid, gid)
                    if any(e["kind"] == "cancel" for e in ev):
                        self.gs.consume([e["id"] for e in ev])
                        return self._cancel(g)
                    if any(e["kind"] == "pause" for e in ev):
                        pauses = [e["id"] for e in ev if e["kind"] == "pause"]
                        self.gs.consume(pauses)
                        g.payload["pause_event"] = max(pauses)
                        if awaiting_answer:  # it was not running: it still waits for the answer
                            return self._park(g, "waiting_answer", "waiting for your answer")
                        return self._park(g, "paused", "paused by you")
                    steers = [e for e in ev if e["kind"] == "steer"]
                    if awaiting_answer and not steers:
                        return self._park(g, "waiting_answer", "waiting for your answer")
                    awaiting_answer = False
                    if steers:
                        self.gs.consume([e["id"] for e in steers])
                        g.payload["steers"] += [{"at": e["at"], "text": e["text"]} for e in steers]
                        said = "\n".join(f"- {e['text']}" for e in steers)
                        message = (f"While you were working, the person added:\n{said}\nTake it into account from "
                                   "here. Keep what is already done unless it conflicts, and say what you changed "
                                   "because of it.")
                        self.out("ALTER: taking your new instruction into account.")
                    why = self._limits_exhausted(g)
                    if why:
                        return self._finish(g, "stopped", why)
                    left = deadline - time.monotonic()
                    if left <= 0:
                        return self._park(g, "paused", "its time window ended; resume to continue")
                    sid = g.payload["sessions"][-1] if g.payload["sessions"] else None
                    same = (sid is not None and not g.payload.pop("force_new", False)
                            and self.agent.would_continue(self.uid, g.project_id, sid, g.payload["provider"],
                                                          g.payload["model"]))
                    if not same:  # a new session would close any other open one: only if none is in use
                        blocker = self._project_blocker(g)
                        if blocker:
                            return self._park(g, "paused", blocker)
                        text = self._first_message(g) if not sid else self._resume_message(g)
                        if message:
                            text += "\n\n" + message
                    else:
                        text = message or "Continue the goal from where you stopped."
                    message = None
                    g.status = "running"
                    self.gs.save(g)
                    # a turn may not run past the goal's window
                    self.agent.limits = dc.replace(base_limits, max_seconds=max(30.0, min(base_limits.max_seconds, left)))
                    turn = self._send(g, text, new_session=not same)
                    self.agent.limits = base_limits
                    if turn is None:
                        return g
                else:
                    turn, pending_turn = pending_turn, None
                if (turn.usage or {}).get("steps", 0):  # only a turn with a model step counts against the limit
                    g.payload["turns"] += 1
                self._checkpoint(g, turn)
                nxt = self._after(g, turn)
                if nxt == "stop":
                    return g
                if nxt:
                    message = nxt
        except KeyboardInterrupt:
            return self._park(g, "paused", "interrupted by you (Ctrl-C); resume to continue")
        finally:
            self.agent.pause_check = None
            self.agent.cancel_check = None
            self.agent.limits = base_limits
            with contextlib.suppress(KeyError, GoalError):
                cur = self.gs.get(self.uid, gid)
                if cur.worker_pid == os.getpid():
                    cur.worker_pid = None
                    self.gs.save(cur)

    def _send(self, g: Goal, text: str, new_session: bool):
        import time
        from .adapters.base import ProviderError
        for attempt in (1, 2):
            try:
                return self.agent.send(self.uid, g.project_id, text, g.payload["provider"], g.payload["model"],
                                       new_session=new_session)
            except ProviderError as e:  # a RuntimeError too: decide by the provider's own retryable flag
                if not e.retryable or attempt == 2:
                    self._park(g, "paused", f"the model call failed ({'twice; ' if attempt == 2 else ''}"
                                            f"{type(e).__name__}: {str(e)[:120]}); resume to try again")
                    return None
                self.out(f"ALTER: the model call failed ({type(e).__name__}); retrying once.")
                time.sleep(3)
            except RuntimeError as e:  # halted, or a session waiting on an approval
                waiting = "waiting on an approval" in str(e)
                self._park(g, "waiting_approval" if waiting else "paused", str(e)[:200])
                return None
            except (PermissionError, ValueError) as e:
                self._finish(g, "failed", str(e)[:200])
                return None
            except Exception as e:  # noqa: BLE001 - an unclassified failure: retry once, then park, never lose state
                if attempt == 2:
                    self._park(g, "paused", f"the model call failed twice ({type(e).__name__}: {str(e)[:120]}); "
                                            "resume to try again")
                    return None
                self.out(f"ALTER: the model call failed ({type(e).__name__}); retrying once.")
                time.sleep(3)
        return None

    def _checkpoint(self, g: Goal, turn) -> None:
        if turn.session_id not in g.payload["sessions"]:
            g.payload["sessions"].append(turn.session_id)
        g.payload["session_costs"][turn.session_id] = turn.cost_usd
        if turn.cost_usd is None:
            g.payload["cost_unknown"] = True
        g.payload["checkpoints"].append({"at": utcnow().isoformat(), "turn": g.payload["turns"], "session": turn.session_id,
                                         "status": turn.status, "stop_reason": turn.stop_reason,
                                         # a stopped or blocked turn's text is the agent's own stop note
                                         "text": (turn.text or "")[:4000],
                                         "model_written": turn.status not in ("stopped", "blocked"),
                                         "tasks": [_task_line(t) for t in turn.tasks], "pending": list(turn.pending)})
        self.gs.save(g)
        self.out(render_turn(turn))

    def _after(self, g: Goal, turn) -> str | None:
        """Decide what follows a turn: a message to send next, None to go round, or "stop"."""
        if turn.status == "awaiting_approval":
            self._park(g, "waiting_approval", f"{len(turn.pending)} action(s) wait for your approval")
            return "stop"
        if turn.status == "blocked":
            self._finish(g, "failed", (turn.text or "the provider lost access")[:200])
            return "stop"
        if turn.status == "stopped":
            if turn.stop_reason == "paused":
                return None  # the loop reads the event that caused it
            if turn.stop_reason == "halted":
                self._park(g, "paused", "Stop-all is on (alter resume clears it)")
                return "stop"
            if turn.stop_reason == "context_changed":  # consent or memory changed: continue in a fresh session
                g.payload["force_new"] = True
                self.gs.save(g)
                return None
            return None  # a per-turn limit: continue in the same session
        v = verify(self.ci, self.uid, g.project_id, g.payload["sessions"], turn.text or "")
        g.payload["verification"] = v
        said, missing = outcome(turn.text)
        g.payload["outcome_said"] = said
        if said == "not reached" and v["verdict"] in ("verified", "unchanged"):
            # tests pass (or nothing changed), but ALTER itself says the request does not work yet:
            # never "done"; the person answers, steers or cancels
            self._park(g, "waiting_answer", "ALTER says the request is not finished"
                       + (f": {missing[:200]}" if missing else ""))
            return "stop"
        if v["verdict"] == "verified":
            self._finish(g, "done", v["line"])
            return "stop"
        if v["verdict"] == "unchanged":
            # Nothing changed and the reply puts a question to the person (anywhere, not only last:
            # the live run ended with a recommendation after two questions): wait for the answer.
            if any(line.rstrip().endswith("?") for line in (turn.text or "").splitlines()):
                self._park(g, "waiting_answer", "ALTER asked you a question")
                return "stop"
            refused = [r for r in v["refused"] if r["tool"] == "write_file"]
            if refused:  # the change did not happen: that is not "done"
                self._finish(g, "stopped", f"{len(refused)} change(s) were refused, not allowed or expired; "
                                           "no file was changed")
                return "stop"
            if g.payload.get("corrects"):
                # A follow-up to the person's correction that changed nothing: its "reached" is the claim the
                # person has just rejected (eval_v8, P01-t04), so the person decides, not the model.
                self._park(g, "waiting_answer", f"nothing changed after your correction to {g.payload['corrects']}; "
                           f"check it: alter goals accept {g.id} if it is right, or alter say {g.id} \"…\"")
                return "stop"
            self._finish(g, "done", "answered; no file was changed")
            return "stop"
        if g.payload["nudges"] < MAX_NUDGES and g.payload["turns"] < g.payload["max_turns"]:
            g.payload["nudges"] += 1
            self.gs.save(g)
            return (f"The task records show: {v['line']}. Run the tests after your last change and fix any failure. "
                    "If the change should not be verified by the tests, say why.")
        self._finish(g, "unverified", v["line"])
        return "stop"

    def _park(self, g: Goal, status: str, reason: str) -> Goal:
        g.status, g.payload["reason"] = status, reason
        self.gs.save(g)
        self.ci.audit.append(self.uid, "goal.park", goal=g.id, status=status)
        self.out(f"ALTER: goal {g.id} is {status}: {reason}.")
        return g

    def _cancel(self, g: Goal) -> Goal:
        """Cancel the goal, every action it still waits on, and its conversation."""
        pend = pending_tasks(self.ci, self.uid, g)
        if g.payload["sessions"]:  # first: a closed session is never resumed by a resolution
            with contextlib.suppress(Exception):
                self.agent.close_session(self.uid, g.project_id, g.payload["sessions"][-1])
        for t in pend:  # the queue directly: Agent.cancel would resume the model loop
            with contextlib.suppress(Exception):
                self.agent.queue.cancel(self.uid, t["id"])
        return self._finish(g, "cancelled", "cancelled by you")

    def _finish(self, g: Goal, status: str, reason: str) -> Goal:
        g.status, g.payload["reason"] = status, reason
        if g.payload["sessions"] and g.payload["verification"] is None:
            g.payload["verification"] = verify(self.ci, self.uid, g.project_id, g.payload["sessions"])
        files = (g.payload.get("verification") or {}).get("files") or []
        if files:  # personal identifiers the model may have copied from its context into a file
            from .identity_guard import scan
            try:
                hits = scan(self.here.root, files)
            except Exception as e:  # noqa: BLE001 - the check must never lose the goal's outcome
                hits = [{"file": "(check failed)", "labels": [type(e).__name__]}]
            g.payload["identifiers"] = hits
            if hits:
                g.payload["reason"] = (f"{reason}; WARNING: {len(hits)} file(s) hold your personal identifiers "
                                       f"({', '.join(sorted({lb for h in hits for lb in h['labels']}))}): review "
                                       "them before you commit or push")
        g.payload["memory_item"] = record_outcome(self.ci, self.uid, g, self.here.name)
        self.gs.save(g)
        self.ci.audit.append(self.uid, "goal.finish", goal=g.id, status=status,
                             verdict=(g.payload["verification"] or {}).get("verdict"))
        self.out(f"ALTER: goal {g.id} is {status}: {reason}.")
        return g

    # ---- control without a model call
    def apply_events(self, gid: str) -> Goal | None:
        """Apply pause and cancel now, when no worker holds the goal. Never calls a model; steers wait for
        the next run. Returns None when a worker holds the goal (it reads the events itself)."""
        try:
            with self.gs.worker_lock(gid, wait_s=2.0):
                g = self.gs.get(self.uid, gid)
                if g.status in FINAL:
                    return g
                ev = self.gs.pending_events(self.uid, gid)
                if any(e["kind"] == "cancel" for e in ev):
                    self.gs.consume([e["id"] for e in ev if e["kind"] in ("cancel", "pause")])
                    return self._cancel(g)
                pauses = [e["id"] for e in ev if e["kind"] == "pause"]
                if pauses:
                    self.gs.consume(pauses)
                    g.payload["pause_event"] = max(pauses)
                    if g.status in ("waiting_answer", "waiting_approval"):  # not running: it keeps waiting
                        self.gs.save(g)
                        return g
                    return self._park(g, "paused", "paused by you")
                return g
        except GoalBusy:
            return None

    # ---- approvals for a goal that waits on them
    def resolve_pending(self, gid: str, approve: bool, reason: str = "", window_s: int | None = None,
                        task_ids: list[str] | None = None) -> Goal:
        """Approve or refuse what a goal waits on: the named tasks only, or all of them. The turn this
        resumes runs under the goal's lock and pause check, like any other turn."""
        import dataclasses as dc
        with self.gs.worker_lock(gid, wait_s=2.0):
            g = self.gs.get(self.uid, gid)
            if g.status != "waiting_approval":
                raise GoalError(f"goal {gid} is {self.gs.shown_status(g)}, not waiting on an approval")
            pend = pending_tasks(self.ci, self.uid, g)
            if task_ids:
                unknown = set(task_ids) - {t["id"] for t in pend}
                if unknown:
                    raise GoalError(f"goal {gid} is not waiting on {', '.join(sorted(unknown))}")
                pend = [t for t in pend if t["id"] in task_ids]
            if not pend:
                return self._run_locked(gid, window_s, None, True)
            if approve:
                why = self._limits_exhausted(g)
                if why:  # approving would start another model turn past the limit: stop cleanly instead
                    return self._stop_at_limit(g, why)
            g.status, g.worker_pid = "running", os.getpid()  # what the person sees while the turn runs
            self.gs.save(g)
            base = self.agent.limits
            self.agent.limits = dc.replace(base, max_seconds=max(30.0, min(base.max_seconds, g.payload["window_s"])))
            self.agent.pause_check = lambda: self.gs.has_pending_event(gid)
            self.agent.cancel_check = lambda: self.gs.has_pending_event(gid, "cancel")
            turn = None
            try:
                for t in pend:
                    out = self.agent.resolve(self.uid, t["id"], approve, reason)
                    if hasattr(out, "session_id"):
                        turn = out
            finally:
                self.agent.pause_check = None
                self.agent.cancel_check = None
                self.agent.limits = base
            if turn is None or (turn.status == "awaiting_approval" and not (turn.usage or {}).get("steps")):
                # part of the step is resolved and the rest still waits: no model step ran, so no turn
                g = self.gs.get(self.uid, gid)
                left = pending_tasks(self.ci, self.uid, g)
                g.worker_pid = None
                return self._park(g, "waiting_approval", f"{len(left)} action(s) wait for your approval")
            return self._run_locked(gid, window_s, turn, True)

    def _stop_at_limit(self, g: Goal, why: str) -> Goal:
        pend = pending_tasks(self.ci, self.uid, g)
        if g.payload["sessions"]:
            with contextlib.suppress(Exception):
                self.agent.close_session(self.uid, g.project_id, g.payload["sessions"][-1])
        for t in pend:
            with contextlib.suppress(Exception):
                self.agent.queue.cancel(self.uid, t["id"])
        return self._finish(g, "stopped", f"{why}; the {len(pend)} action(s) it waited on were cancelled, not run")


def pending_tasks(ci, uid: str, g: Goal) -> list[dict]:
    if not g.payload["sessions"]:
        return []
    return [_task_line(t) for t in evidence(ci, uid, g.project_id, [g.payload["sessions"][-1]])
            if t["status"] == "awaiting_approval"]


# --------------------------------------------------------------------------- outcome memory
def record_outcome(ci, uid: str, g: Goal, project_name: str) -> str | None:
    """An episode computed from the records, written only under the person's grant to ALTER."""
    from .memory import ConsentRequired
    if g.payload.get("memory_item"):
        return g.payload["memory_item"]
    v = g.payload.get("verification") or {}
    cost = sum(c for c in g.payload["session_costs"].values() if c is not None)
    files = ", ".join(v.get("files") or []) or "none"
    text = (f"ALTER goal in {project_name} ({g.created_at[:10]}): \"{g.objective[:200]}\". Ended {g.status}"
            f" ({g.payload.get('reason')}). Files changed: {files}. Test runs: {v.get('test_runs', 0)}, "
            f"{v.get('tests_passed', 0)} passed. Cost ${cost:.2f}{' or more' if g.payload['cost_unknown'] else ''}.")
    try:
        r = ci.remember(uid, text, "episode", source=MEMORY_SOURCE, trust=Trust.IMPORTED, confidence=1.0,
                        tags=("alter", "goal", f"project:{g.project_id}"),
                        provenance={"goal": g.id, "project": g.project_id, "sessions": g.payload["sessions"],
                                    "verdict": v.get("verdict"), "computed_from": "task records", "status": g.status})
    except ConsentRequired:
        return None
    return r.item.id


def grant_outcome_memory(ci, uid: str) -> None:
    ci.ledger.grant(uid, MEMORY_SOURCE, "store", note="alter init: ALTER may store the outcome of each goal")


def has_outcome_grant(ci, uid: str) -> bool:
    from .types import Sensitivity
    return ci.ledger.allows(uid, MEMORY_SOURCE, "store", "*", Sensitivity.PERSONAL, utcnow())[0]


# --------------------------------------------------------------------------- reporting
def render_turn(turn) -> str:
    lines = []
    if turn.text:
        lines.append(turn.text.rstrip())
    for t in turn.tasks:
        a = t.get("args") or {}
        what = a.get("path") or " ".join(str(x) for x in (a.get("argv") or []))
        lines.append(f"  · {t['tool']} {what} → {t['status']}" + (f" ({t['reason'][:80]})" if t.get("reason") else ""))
    return "\n".join(lines)


def report(ci, uid: str, g: Goal, gs: GoalStore | None = None) -> str:
    """What the records show, for "what have you finished?". No model call."""
    from .textsafe import terminal_safe
    gs = gs or GoalStore(ci)
    v = verify(ci, uid, g.project_id, g.payload["sessions"]) if g.payload["sessions"] else None
    cost = sum(c for c in g.payload["session_costs"].values() if c is not None)
    out = [f"Goal {g.id}: {gs.shown_status(g)}" + (f" ({g.payload['reason']})" if g.payload.get("reason") else ""),
           f"  objective: {g.objective}",
           *([f"  corrects: {g.payload['corrects']} (a follow-up to your correction)"] if g.payload.get("corrects") else []),
           *(["  done on your word (alter goals accept)"] if g.payload.get("done_by") == "person" else []),
           f"  turns {g.payload['turns']}/{g.payload['max_turns']} · cost ${cost:.2f}"
           f"{'+' if g.payload['cost_unknown'] else ''} of ${g.payload['max_cost_usd']:.2f}"]
    if g.payload.get("context"):
        c = g.payload["context"]
        out.append(f"  context: {c['remembered']} remembered, {c['sourced']} sourced; not included: "
                   f"{'; '.join(c['withheld']) or 'nothing'}")
    if v:
        out.append(f"  files changed: {', '.join(v['files']) or 'none'}")
        out.append(f"  test runs: {v['test_runs']} ({v['tests_passed']} passed) · {v['line']}")
        if v["refused"]:
            out.append(f"  refused or cancelled: {len(v['refused'])}")
    pend = pending_tasks(ci, uid, g)
    for t in pend:
        out.append(f"  waiting for you: {t['id']} {t['tool']} {t['what']}")
    if g.payload["steers"]:
        out.append(f"  your added instructions: {len(g.payload['steers'])}")
    if g.payload["checkpoints"]:
        cp = g.payload["checkpoints"][-1]
        last = cp["text"].strip()
        if last and cp.get("model_written", True):
            out.append("  last message from ALTER (model-written):")
            out += ["    " + x for x in last.splitlines()[-12:]]
        elif last and g.status not in FINAL:  # a stop note ("Resume to continue") matters only while it can continue
            out.append("  last note (from ALTER's records, not the model):")
            out += ["    " + x for x in last.splitlines()[-12:]]
    for h in g.payload.get("identifiers") or []:
        out.append(f"  WARNING: {h['file']} holds your {', '.join(h['labels'])}: review before you commit or push")
    if g.payload.get("memory_item"):
        out.append(f"  outcome recorded in memory: {g.payload['memory_item']}")
    return terminal_safe("\n".join(out))


# --------------------------------------------------------------------------- background worker
def spawn_worker(ci, gid: str, root: str, window_s: int, resume: bool = False) -> int:
    token = GoalStore(ci).last_event_id(gid) if resume else None
    """A detached worker. `-P` and a working directory outside the project: `python -m` would
    otherwise import a project file named like a module it uses (argparse.py, json.py) before
    anything was approved. Its log holds ids and statuses only, never model text."""
    lock = GoalStore(ci)._lock_file(gid)
    with open(lock.with_suffix(".log"), "a") as f:
        os.chmod(f.name, 0o600)
        p = subprocess.Popen([sys.executable, "-P", "-m", "pcim.goals", "run", gid, "--window", str(int(window_s)),
                              *(["--resume-after", str(token)] if resume else [])],
                             cwd=str(lock.parent), stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True, env=dict(os.environ))
    return p.pid


def _worker_main(argv: list[str]) -> int:
    import argparse
    from .terminal import _open
    ap = argparse.ArgumentParser(prog="python -m pcim.goals")
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("goal")
    ap.add_argument("--window", type=int, default=None)
    ap.add_argument("--resume-after", type=int, default=None, help="resume, unless a newer pause parked it")
    a = ap.parse_args(argv)
    ci, uid = _open()
    try:
        g = GoalStore(ci).get(uid, a.goal)
        r = GoalRunner(ci, uid, here_for(ci, uid, g.project_id), out=lambda s: None)
        g = r.run(a.goal, a.window, resume=a.resume_after is not None, resume_after=a.resume_after)
    except (GoalError, KeyError) as e:
        print(json.dumps({"goal": a.goal, "error": type(e).__name__}), flush=True)
        return 3
    print(json.dumps({"goal": g.id, "status": g.status, "at": utcnow().isoformat()}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main(sys.argv[1:]))
