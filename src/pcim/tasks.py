"""Durable task queue: every proposed tool call becomes a row before anything runs.

States:
    awaiting_approval → approved → running → succeeded | failed | cancelled
    awaiting_approval → denied | cancelled | expired
    running (process died) → interrupted        — never silently re-run

Arguments and results are sealed with the project's key. An approval is good for
`approval_ttl_s` (default one hour); after that the task expires and has to be proposed
again, so a stale click cannot fire an action whose context has changed. Autonomous
actions count against their grant when they start, not when they succeed, so a failing
action cannot loop past its limit.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import time

from .governor import RISK, Governor
from .tools import Cancelled, Executor, ToolError, validate_args
from .types import new_id, utcnow
from .workspace import Workspace

log = logging.getLogger(__name__)

LIVE = ("awaiting_approval", "approved", "running")
TERMINAL = ("succeeded", "failed", "cancelled", "denied", "interrupted", "expired")


class TaskStateError(RuntimeError):
    pass


@dataclasses.dataclass
class Task:
    id: str
    project_id: str
    session_id: str | None
    tool: str
    risk: str
    status: str
    requested_by: str
    tool_use_id: str | None
    authorization_id: str | None
    reason: str | None
    created_at: str
    updated_at: str
    approved_at: str | None
    started_at: str | None
    finished_at: str | None
    duration_ms: float | None
    args: dict
    result: dict | None

    def public(self, include_backup: bool = False) -> dict:
        d = dataclasses.asdict(self)
        if d["result"] and not include_backup:
            d["result"] = {k: v for k, v in d["result"].items() if k != "backup"}
        return d


class TaskQueue:
    def __init__(self, ws: Workspace, governor: Governor, executor: Executor | None = None,
                 approval_ttl_s: int = 3600, claims=None):
        self.ws, self.gov = ws, governor
        self.executor = executor or Executor()
        self.ttl = approval_ttl_s
        self.claims = claims  # exchange.ClaimLedger; record_claim fails without one
        self.store, self.audit = ws.store, ws.audit

    # ------------------------------------------------------------------ persistence
    def _key(self, user_id: str, project_id: str) -> str:
        return self.ws.project_key(user_id, project_id)

    def _set(self, task_id: str, **cols) -> None:
        cols["updated_at"] = utcnow().isoformat()
        sets = ", ".join(f"{k}=?" for k in cols)
        self.store.execute(f"UPDATE tasks SET {sets} WHERE id=?", (*cols.values(), task_id))

    def _seal(self, user_id: str, project_id: str, obj, task_id: str, part: str) -> bytes:
        return self.store.seal_json(self._key(user_id, project_id), obj, f"{task_id}/{part}")

    def get(self, user_id: str, task_id: str) -> Task:
        rows = self.store.query("SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, user_id))
        if not rows:
            raise KeyError(task_id)
        r = rows[0]
        key = self._key(user_id, r["project_id"])
        return Task(
            id=r["id"], project_id=r["project_id"], session_id=r["session_id"], tool=r["tool"], risk=r["risk"],
            status=r["status"], requested_by=r["requested_by"], tool_use_id=r["tool_use_id"],
            authorization_id=r["authorization_id"], reason=r["reason"], created_at=r["created_at"],
            updated_at=r["updated_at"], approved_at=r["approved_at"], started_at=r["started_at"],
            finished_at=r["finished_at"], duration_ms=r["duration_ms"],
            args=self.store.open_json(key, r["args_ct"], f"{r['id']}/args"),
            result=self.store.open_json(key, r["result_ct"], f"{r['id']}/result"),
        )

    def list(self, user_id: str, project_id: str | None = None, status: str | None = None, limit: int = 200) -> list[Task]:
        q, a = "SELECT id FROM tasks WHERE user_id=?", [user_id]
        if project_id:
            q += " AND project_id=?"
            a.append(project_id)
        if status:
            q += " AND status=?"
            a.append(status)
        q += f" ORDER BY created_at DESC LIMIT {int(limit)}"
        return [self.get(user_id, r["id"]) for r in self.store.query(q, tuple(a))]

    # ------------------------------------------------------------------ lifecycle
    def propose(self, user_id: str, project_id: str, tool: str, args: dict, requested_by: str = "model",
                session_id: str | None = None, tool_use_id: str | None = None) -> Task:
        if session_id and tool_use_id:  # the same model call proposed twice is one task, never a second side effect
            dup = self.store.query("SELECT id FROM tasks WHERE session_id=? AND tool_use_id=? AND user_id=?",
                                   (session_id, tool_use_id, user_id))
            if dup:
                self.audit.append(user_id, "task.duplicate", task=dup[0]["id"], project=project_id)
                return self.get(user_id, dup[0]["id"])
        tid, now = new_id("task"), utcnow().isoformat()
        try:
            validate_args(tool, args)
            d = self.gov.decide(user_id, project_id, tool, args)
            status, reason, aid = {"execute": "approved", "approve": "awaiting_approval", "deny": "denied"}[d.action], \
                d.reason, d.authorization_id
        except ToolError as e:
            status, reason, aid = "denied", str(e), None
            args = args if isinstance(args, dict) else {"raw": str(args)[:2000]}
        self.store.execute(
            "INSERT INTO tasks(id, user_id, project_id, session_id, tool, risk, status, requested_by, tool_use_id, "
            "authorization_id, reason, created_at, updated_at, approved_at, args_ct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, user_id, project_id, session_id, tool, RISK.get(tool, "unknown"), status, requested_by, tool_use_id,
             aid, reason, now, now, now if status == "approved" else None,
             self._seal(user_id, project_id, args, tid, "args")),
        )
        self.audit.append(user_id, "task.propose", task=tid, project=project_id, tool=tool[:40], status=status,
                          requested_by=requested_by[:40], authorization=aid)
        if status == "approved":
            self._execute(user_id, tid)
        return self.get(user_id, tid)

    def approve(self, user_id: str, task_id: str) -> Task:
        t = self.get(user_id, task_id)
        if t.status != "awaiting_approval":
            raise TaskStateError(f"task is {t.status}, not awaiting approval")
        age = (utcnow() - dt.datetime.fromisoformat(t.created_at)).total_seconds()
        if age > self.ttl:
            self._set(task_id, status="expired", reason=f"approval window of {self.ttl}s passed; propose it again")
            self.audit.append(user_id, "task.expired", task=task_id)
            return self.get(user_id, task_id)
        self._set(task_id, status="approved", approved_at=utcnow().isoformat())
        self.audit.append(user_id, "task.approve", task=task_id)
        self._execute(user_id, task_id)
        return self.get(user_id, task_id)

    def deny(self, user_id: str, task_id: str, reason: str = "") -> Task:
        t = self.get(user_id, task_id)
        if t.status not in ("awaiting_approval", "approved"):
            raise TaskStateError(f"task is {t.status}")
        self._set(task_id, status="denied", reason=(reason or "denied by you")[:500])
        self.audit.append(user_id, "task.deny", task=task_id)
        return self.get(user_id, task_id)

    def cancel(self, user_id: str, task_id: str) -> Task:
        t = self.get(user_id, task_id)
        if t.status in ("awaiting_approval", "approved"):
            self._set(task_id, status="cancelled", reason="cancelled before it ran")
        elif t.status == "running":
            self.executor.cancel(task_id)  # _execute records the outcome
        else:
            raise TaskStateError(f"task is already {t.status}")
        self.audit.append(user_id, "task.cancel", task=task_id)
        return self.get(user_id, task_id)

    def halt(self, user_id: str) -> dict:
        """Stop everything: block new execution, cancel queued work, kill running processes."""
        self.ws.set_halted(user_id, True)
        n = 0
        for t in self.list(user_id, limit=10_000):
            if t.status in LIVE:
                try:
                    self.cancel(user_id, t.id)
                    n += 1
                except TaskStateError:
                    pass
        return {"halted": True, "cancelled": n}

    def recover(self) -> dict:
        """Call at startup. Nothing that was mid-flight when the process died is re-run."""
        now = utcnow().isoformat()
        a = self.store.execute("UPDATE tasks SET status='interrupted', reason='process stopped while running', "
                               "updated_at=? WHERE status='running'", (now,)).rowcount
        cutoff = (utcnow() - dt.timedelta(seconds=self.ttl)).isoformat()
        b = self.store.execute("UPDATE tasks SET status='expired', reason='approval window passed before restart', "
                               "updated_at=? WHERE status IN ('approved','awaiting_approval') AND COALESCE(approved_at, created_at) < ?",
                               (now, cutoff)).rowcount
        return {"interrupted": a, "expired": b}

    def _execute(self, user_id: str, task_id: str) -> None:
        t = self.get(user_id, task_id)
        d = self.gov.recheck(user_id, t.project_id, t.tool, t.args, t.authorization_id)
        if d.action != "execute":
            self._set(task_id, status="denied", reason=f"at execution: {d.reason}"[:500])
            self.audit.append(user_id, "task.blocked_at_execution", task=task_id)
            return
        if t.authorization_id:
            self.ws.consume(t.authorization_id)
        self._set(task_id, status="running", started_at=utcnow().isoformat())
        t0 = time.perf_counter()
        try:
            if t.tool == "record_note":
                note = self.ws.add_note(user_id, t.project_id, t.args["kind"], t.args["text"], source="model",
                                        by=self._author(t), lineage=self.exposure(user_id, t))
                result = {"note_id": note["id"]}
            elif t.tool == "record_claim":
                if self.claims is None:
                    raise ToolError("the claim ledger is not enabled here")
                c = self.claims.add(user_id, t.project_id, t.args["statement"], t.args["evidence"],
                                    t.args["uncertainty"], author={"kind": "model", **self._author(t)},
                                    lineage=self.exposure(user_id, t))
                result = {"claim_id": c["id"], "status": c["status"], "evidence_refs": c["evidence_refs"]}
            else:
                spec = self.ws.workspace_auth(user_id, t.project_id)["spec"]
                result = self.executor.run(task_id, t.tool, t.args, spec)
            status, reason = "succeeded", None
            if t.tool == "run_tests" and (result.get("exit_code") != 0 or result.get("timed_out")):
                reason = "tests timed out" if result.get("timed_out") else f"tests exited {result.get('exit_code')}"
        except Cancelled as e:
            status, reason, result = "cancelled", str(e), None
        except (ToolError, OSError, ValueError) as e:
            status, reason, result = "failed", str(e)[:500], None
        except Exception as e:  # a bug, not a tool outcome: logged, and the task fails visibly instead of hanging in "running"
            log.exception("task %s (%s) raised", task_id, t.tool)
            status, reason, result = "failed", f"internal error ({type(e).__name__})", None
        ms = (time.perf_counter() - t0) * 1000
        self._set(task_id, status=status, reason=reason, finished_at=utcnow().isoformat(), duration_ms=ms,
                  result_ct=self._seal(user_id, t.project_id, result, task_id, "result") if result is not None else None)
        self.audit.append(user_id, "task.finish", task=task_id, tool=t.tool, status=status, ms=round(ms, 1))

    def _author(self, t: Task) -> dict:
        """Which provider and model proposed this task, from its session."""
        if not t.session_id:
            return {}
        rows = self.store.query("SELECT provider, model FROM sessions WHERE id=?", (t.session_id,))
        return {"provider": rows[0]["provider"], "model": rows[0]["model"], "session": t.session_id} if rows else {}

    def exposure(self, user_id: str, t: Task) -> list[str]:
        """Memory items the model behind this task had been shown in its session so far:
        what anything it writes may be derived from."""
        if not t.session_id:
            return []
        rows = self.store.query("SELECT state_ct FROM sessions WHERE id=? AND user_id=?", (t.session_id, user_id))
        if not rows:
            return []
        st = self.store.open_json(self._key(user_id, t.project_id), rows[0]["state_ct"], t.session_id)
        return self.ws.session_exposure(st)

    def undo(self, user_id: str, task_id: str) -> dict:
        """Restore the file a write_file task replaced (or remove the one it created)."""
        from .governor import jail
        t = self.get(user_id, task_id)
        if t.tool != "write_file" or t.status != "succeeded" or not t.result or t.result.get("undone"):
            raise TaskStateError("only a successful, not-yet-undone write can be undone")
        if self.ws.is_halted(user_id):
            raise TaskStateError("halted")
        spec = self.ws.workspace_auth(user_id, t.project_id)["spec"]
        p = jail(spec["root"], t.args["path"], for_write=True)
        if t.result["created"]:
            p.unlink(missing_ok=True)
        else:
            p.write_text(t.result["backup"])
        res = dict(t.result, undone=True, undone_at=utcnow().isoformat())
        self._set(task_id, result_ct=self._seal(user_id, t.project_id, res, task_id, "result"))
        self.audit.append(user_id, "task.undo", task=task_id)
        return {"undone": True, "path": t.args["path"]}
