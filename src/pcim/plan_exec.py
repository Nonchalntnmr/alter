"""`ci execute`: run an approved plan through the governed workspace agent, step by step.

An approved plan is a dependency graph (intent_compiler.topo_levels). Execution walks it level
by level. Each step is one message to the workspace agent (agent.py), so every tool call it
makes passes the governor with the current autonomy mode and grants — approving the plan never
authorized a tool. Per step:

    think / read / run / write   → agent turn; done | waiting (an action needs your approval) | failed
    external                     → recorded as yours to do; CI cannot do it. Its dependents wait
                                   until you mark it done (`ci execute <id> --done s3`)

The plan's state is saved after every step, so `ci execute <id>` resumes where it stopped: after
you approve a pending action (`ci approve <task>`), after a failure you fixed, or after a crash.

Parallelism, stated plainly: levels are computed, and steps within a level are independent,
but this slice runs them one after another in one agent session per project. Nothing here runs
in the background; work happens while the command runs.
"""

from __future__ import annotations

from . import intent_compiler as ic
from .agent import Agent
from .governor import Governor
from .registry import route
from .tasks import TERMINAL, TaskQueue
from .types import utcnow
from .workspace import Workspace

STEP_DONE = ("done", "yours_done")


class ExecError(ValueError):
    pass


def _message(rec: dict, step: dict, n: int, total: int) -> str:
    s = rec["spec"]
    crit = "; ".join(s["success_criteria"][:4]) or "none stated"
    return (f"Plan {rec['id']}, step {step['id']} ({n} of {total}): {step['action']}\n"
            f"Overall goal: {s['goal']}\nSuccess criteria for the whole plan: {crit}\n"
            "Do only this step. Use tools to check rather than assume, and end by saying what you verified "
            "and what you did not.")


def _model(here_vendors: set[str], provider: str | None, model: str | None) -> tuple[str, str | None]:
    if provider:
        return provider, model
    r = route("chat", here_vendors)
    if r.entry is None:
        raise ExecError(f"no model may run this project's steps: {r.reason}")
    return r.entry.provider, r.entry.model or None


def execute(ci, uid: str, plan_id: str, project_id: str, vendors: set[str], *, provider: str | None = None,
            model: str | None = None, agent: Agent | None = None, done: list[str] | None = None) -> dict:
    rec = ic.get(ci, uid, plan_id)
    if rec["status"] not in ("approved", "executing", "waiting", "failed"):
        raise ExecError(f"plan is {rec['status']}; only an approved plan runs (ci plan approve {rec['id']})")
    if rec.get("project_id") and rec["project_id"] != project_id:
        raise ExecError("this plan was compiled in another project's directory; run it from there")
    if agent is None:
        ws = Workspace(ci)
        agent = Agent(ci, ws, TaskQueue(ws, Governor(ws)))
    state: dict = rec.setdefault("steps_state", {})
    steps = {s["id"]: s for s in rec["spec"]["steps"]}
    for sid in done or []:
        if sid not in steps or steps[sid]["kind"] != "external":
            raise ExecError(f"only an external step can be marked done by you; {sid} is not one")
        state[sid] = {"status": "yours_done", "at": utcnow().isoformat()}
    prov, mdl = _model(vendors, provider, model)
    order = [sid for level in rec["spec"]["levels"] for sid in level]
    rec["status"] = "executing"
    outcome = "done"
    for n, sid in enumerate(order, 1):
        step, st = steps[sid], state.get(sid) or {}
        if st.get("status") in STEP_DONE:
            continue
        blocked_by = [d for d in step["depends_on"] if (state.get(d) or {}).get("status") not in STEP_DONE]
        if blocked_by:
            outcome = "blocked"
            state[sid] = {"status": "blocked", "by": blocked_by, "at": utcnow().isoformat()}
            continue
        if step["kind"] == "external":
            state[sid] = {"status": "yours", "at": utcnow().isoformat()}
            outcome = "blocked"
            continue
        if st.get("status") == "waiting":  # resume: did the pending actions get resolved?
            q = TaskQueue(Workspace(ci), Governor(Workspace(ci)))
            if any(q.get(uid, t).status not in TERMINAL for t in st.get("pending", [])):
                outcome = "waiting"
                break
            tr = agent.transcript(uid, project_id, st.get("session"))
            if tr.get("pending"):  # approving one action led to another that needs you
                state[sid] = dict(st, pending=list(tr["pending"]))
                outcome = "waiting"
                break
            state[sid] = dict(st, status="done", at=utcnow().isoformat(), note="resumed after approval")
            continue
        try:
            turn = agent.send(uid, project_id, _message(rec, step, n, len(order)), prov, mdl)
        except (RuntimeError, ValueError, PermissionError) as e:  # halted, vendor not allowed, provider down
            state[sid] = {"status": "failed", "why": str(e)[:300], "at": utcnow().isoformat()}
            outcome = "failed"
            break
        entry = {"session": turn.session_id, "text": turn.text[:2000], "tasks": [t["id"] for t in turn.tasks],
                 "task_status": {t["id"]: t["status"] for t in turn.tasks}, "model": f"{prov}:{mdl or ''}",
                 "at": utcnow().isoformat()}
        if turn.status == "done":
            state[sid] = entry | {"status": "done"}
        elif turn.status == "awaiting_approval":
            state[sid] = entry | {"status": "waiting", "pending": list(turn.pending)}
            outcome = "waiting"
            break
        else:
            state[sid] = entry | {"status": "failed", "why": turn.status}
            outcome = "failed"
            break
    if outcome == "done" and all((state.get(s) or {}).get("status") in STEP_DONE for s in order):
        rec["status"] = "done"
    elif outcome == "done":
        outcome = "blocked"
    if rec["status"] != "done":
        rec["status"] = {"waiting": "waiting", "failed": "failed"}.get(outcome, "executing")
    rec["steps_state"] = state
    ic._save(ci, uid, rec)
    ci.audit.append(uid, "plan.execute", plan=rec["id"], outcome=outcome, status=rec["status"],
                    done=sum(1 for s in order if (state.get(s) or {}).get("status") in STEP_DONE), steps=len(order))
    return {"plan": rec, "outcome": outcome}


def render(res: dict) -> str:
    rec = res["plan"]
    L = [f"plan {rec['id']} · {rec['status']} · this run: {res['outcome']}"]
    for level in rec["spec"]["levels"]:
        for sid in level:
            s = next(x for x in rec["spec"]["steps"] if x["id"] == sid)
            st = rec["steps_state"].get(sid) or {"status": "not started"}
            extra = ""
            if st.get("tasks"):
                extra = " · tools: " + ", ".join(f"{t[-6:]}={v}" for t, v in st.get("task_status", {}).items())
            if st.get("by"):
                extra = f" · waiting on {', '.join(st['by'])}"
            L.append(f"  {sid} [{s['kind']}] {st['status']:<10} {s['action'][:70]}{extra}")
            if st.get("status") == "waiting":
                L += [f"      approve: ci approve {t}" for t in st.get("pending", [])]
            if st.get("status") == "yours":
                L.append(f"      yours to do; then: ci execute {rec['id']} --done {sid}")
    return "\n".join(L)
