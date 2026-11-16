"""`ci execute`: run an approved plan through the governed workspace agent, step by step.

An approved plan is a dependency graph (intent_compiler.topo_levels). Execution walks it in
order. Each step is one message to the workspace agent (agent.py), so every tool call it makes
passes the governor with the current autonomy mode and grants. Approving the plan authorized
no tool.

Every step is always in exactly one observable state, recorded in the plan:

    not_started → in_progress → done          every tool the step used succeeded (tests passed),
                                              judged from the task records, never from the model
                              → incomplete    the turn ended but a tool was denied, failed,
                                              cancelled or expired, or tests failed
                              → waiting       an action needs you (ci approve / ci deny)
                              → failed        the agent could not take the step (provider, vendor)
                              → halted        Stop-all (ci halt) stopped the turn
    in_progress, found at the next run → interrupted   the process died mid-step; never re-run
                                                        silently, because the step may have acted
    external steps → yours → yours_done       CI cannot do them; you mark them (--done)
    dependents of a step that is yours → blocked
    --cancel → cancelled                      the session is closed first, then its pending
                                              actions are cancelled, so nothing resumes

`--retry STEP` re-runs a failed, interrupted, incomplete or halted step. Nothing else re-runs
one. A step that is not done stops the plan; only an external step lets independent steps go on.

Completion is about **outcomes**, not steps. A plan is `done` only when every outcome is met:
- a deliverable or a destructive change by CI is met when every step serving it is `done`;
- an external action (or a destructive change CI has no step for) is met only when you mark it.

One run at a time: `execute`, and `ci approve` / `ci deny` on a plan's action, hold an OS lock
on the plan (released if the process dies). Steps run one after another; nothing runs in the
background, and nothing runs in parallel.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path

from . import intent_compiler as ic
from .agent import Agent
from .governor import Governor
from .registry import route
from .tasks import TERMINAL, TaskQueue
from .types import utcnow
from .workspace import Workspace

DONE = ("done", "yours_done")
RETRYABLE = ("failed", "interrupted", "incomplete", "halted")
STOPS = RETRYABLE + ("cancelled",)  # a step in one of these stops the plan until you act
NOT_RUNNABLE = ("done", "cancelled")
BAD_TASK = ("denied", "failed", "cancelled", "expired", "interrupted")
BOOKKEEPING_TOOLS = frozenset({"record_note", "record_claim"})
STEP_STATES = ("not_started", "in_progress", "done", "incomplete", "waiting", "failed", "halted", "interrupted",
               "yours", "yours_done", "blocked", "cancelled")


class ExecError(ValueError):
    pass


# ---------------------------------------------------------------------- one run at a time
@contextlib.contextmanager
def plan_lock(ci, plan_id: str):
    """Exclusive, non-blocking, released by the OS if the holder dies."""
    d = Path(ci.store.path).parent if getattr(ci.store, "path", ":memory:") != ":memory:" else None
    if d is None:
        yield
        return
    path = d / f".plan-{plan_id}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ExecError("this plan is being run or resolved in another process; wait for it to finish") from None
        yield
    finally:
        os.close(fd)


def plan_for_task(ci, uid: str, task_id: str) -> str | None:
    """The plan whose waiting step is waiting on this task, if any."""
    for rec in ic.records(ci, uid):
        for st in (rec.get("steps_state") or {}).values():
            if st.get("status") == "waiting" and task_id in st.get("pending", []):
                return rec["id"]
    return None


# ---------------------------------------------------------------------- helpers
def _message(rec: dict, step: dict, n: int, total: int) -> str:
    s = rec["spec"]
    crit = "; ".join(s["success_criteria"][:4]) or "none stated"
    return (f"Plan {rec['id']}, step {step['id']} ({n} of {total}): {step['action']}\n"
            f"Overall goal: {s['goal']}\nSuccess criteria for the whole plan: {crit}\n"
            "Do only this step. Use tools to check rather than assume, and end by saying what you verified "
            "and what you did not.")


def _contract_message(rec: dict) -> str:
    s = rec["spec"]
    ci_outcomes = [f"- {o['id']}: {o['text']}" + (f" (done when: {o['done_when']})" if o.get("done_when") else "")
                   for o in s.get("outcomes", []) if o.get("by") != "you"]
    user_outcomes = [f"- {o['id']}: {o['text']}" for o in s.get("outcomes", []) if o.get("by") == "you"]
    constraints = [f"- {c['text']}" for c in s.get("constraints", [])]
    crit = "; ".join(s.get("success_criteria") or []) or "none stated"
    parts = [f"Plan {rec['id']} — Goal: {s['goal']}"]
    if ci_outcomes:
        parts.append("Outcomes to achieve:\n" + "\n".join(ci_outcomes))
    if user_outcomes:
        parts.append("Outcomes for the person (CI cannot perform these; prepare only if needed):\n"
                     + "\n".join(user_outcomes))
    if constraints:
        parts.append("Constraints:\n" + "\n".join(constraints))
    parts.append(f"Success criteria: {crit}")
    parts.append("Complete the CI outcomes in this session. Use tools to read the code, make the needed changes, "
                 "and run tests to verify. End by summarizing what you verified with tool evidence and what remains "
                 "for the person.")
    return "\n\n".join(parts)


def _model(vendors: set[str], provider: str | None, model: str | None) -> tuple[str, str | None]:
    if provider:
        return provider, model
    r = route("chat", vendors)
    if r.entry is None:
        raise ExecError(f"no model may run this project's steps: {r.reason}")
    return r.entry.provider, r.entry.model or None


def _analyze_tasks(tasks: list[dict], *, require_tests_after_write: bool = True, require_change: bool = False) -> dict:
    """Analyze task records into work problems, denials, bookkeeping failures, and evidence."""
    problems, denials, bookkeeping = [], [], []
    last_write_idx, last_pass_test_idx = None, None
    non_denied_work_problem = False
    for i, t in enumerate(tasks):
        is_bookkeeping = t["tool"] in BOOKKEEPING_TOOLS
        if t["status"] in ("denied", "cancelled", "expired"):
            denials.append({"id": t["id"], "tool": t["tool"], "status": t["status"], "reason": t.get("reason") or ""})
        if is_bookkeeping:
            if t["status"] in BAD_TASK or t["status"] not in TERMINAL:
                bookkeeping.append(f"{t['tool']} {t['status']}" + (f": {t['reason']}" if t.get("reason") else ""))
            continue
        if t["status"] in BAD_TASK:
            problems.append(f"{t['tool']} {t['status']}" + (f": {t['reason']}" if t.get("reason") else ""))
            if t["status"] != "denied":
                non_denied_work_problem = True
        elif t["status"] not in TERMINAL:
            problems.append(f"{t['tool']} {t['status']}: never finished")
            non_denied_work_problem = True
        elif t["tool"] == "run_tests" and t.get("reason"):
            problems.append(f"run_tests: {t['reason']}")
            non_denied_work_problem = True
        elif t["tool"] == "write_file" and t["status"] == "succeeded":
            last_write_idx = i
        elif t["tool"] == "run_tests" and t["status"] == "succeeded" and not t.get("reason"):
            last_pass_test_idx = i
    tests_after_write = (last_pass_test_idx is not None and last_pass_test_idx > last_write_idx) if last_write_idx is not None else (last_pass_test_idx is not None)
    if last_write_idx is not None and require_tests_after_write:
        evidence_ok = tests_after_write and not non_denied_work_problem
    else:
        evidence_ok = (not problems) or (tests_after_write and not non_denied_work_problem)
    if require_change and last_write_idx is None:
        # a plan that changes files is not achieved by a session that changed nothing ("I can't do this")
        problems.append("no file was changed, though the plan changes files")
        evidence_ok = False
    return {"achieved": not problems, "evidence_ok": evidence_ok, "problems": problems,
            "denials": denials, "bookkeeping_failures": bookkeeping, "tests_after_write": tests_after_write}


def _task_verdict(tasks: list[dict]) -> tuple[bool, list[str]]:
    """(achieved, problems) from the step's task records alone. Bookkeeping tools do not fail a step."""
    info = _analyze_tasks(tasks)
    return info["achieved"], info["problems"]


def _session_tasks(queue: TaskQueue, uid: str, project_id: str, session: str | None, since: str) -> list[dict]:
    """Every task the step's session created since the step started, including ones a resumed
    turn ran after you approved something."""
    return [t.public() for t in reversed(queue.list(uid, project_id, limit=10_000))
            if t.session_id == session and t.created_at >= since]


def _clear_stale_pending(ci, agent: Agent, queue: TaskQueue, uid: str, project_id: str) -> str | None:
    """Stop-all cancels pending actions without resuming their session, and a cancelled action can
    no longer be approved or denied, so the session would refuse every new message forever (a
    defect in agent.py, reported, not fixed here). Before a step runs, close such a session; its
    reflection is built from task records, so nothing in it reads as done that did not run."""
    sid = agent.open_session(uid, project_id)
    if not sid:
        return None
    pend = agent.transcript(uid, project_id, sid).get("pending") or []
    if pend and all(queue.get(uid, t).status in TERMINAL for t in pend):
        agent.close_session(uid, project_id, sid)
        ci.audit.append(uid, "plan.close_stale_session", session=sid, pending=len(pend))
        return sid
    return None


def _outcomes(rec: dict, state: dict) -> dict:
    """Per outcome: met | unmet | yours_pending | yours_done, with the steps that decide it."""
    spec, out = rec["spec"], {}
    prev = rec.get("outcomes_state") or {}
    non_external = [s for s in spec["steps"] if s["kind"] != "external"]
    for o in spec.get("outcomes", []):
        if (prev.get(o["id"]) or {}).get("status") == "yours_done":
            out[o["id"]] = prev[o["id"]]
            continue
        if o.get("by") == "you":
            out[o["id"]] = {"status": "yours_pending"}
            continue
        linked = [s for s in spec["steps"] if o["id"] in s.get("serves", [])]
        serving = linked or non_external
        statuses = {s["id"]: (state.get(s["id"]) or {}).get("status", "not_started") for s in serving}
        met = bool(serving) and all(v in DONE for v in statuses.values())
        out[o["id"]] = {"status": "met" if met else "unmet", "steps": statuses,
                        "judged_by": "linked steps" if linked else "all CI steps (the plan linked none to this outcome)"}
    return out


def _now() -> str:
    return utcnow().isoformat()


# ---------------------------------------------------------------------- execute
def execute(ci, uid: str, plan_id: str, project_id: str, vendors: set[str], *, provider: str | None = None,
            model: str | None = None, agent: Agent | None = None, done: list[str] | None = None,
            retry: list[str] | None = None, cancel: bool = False) -> dict:
    with plan_lock(ci, plan_id):
        return _execute(ci, uid, plan_id, project_id, vendors, provider=provider, model=model, agent=agent,
                        done=done, retry=retry, cancel=cancel)


def _execute(ci, uid, plan_id, project_id, vendors, *, provider, model, agent, done, retry, cancel) -> dict:
    rec = ic.get(ci, uid, plan_id)
    if rec["status"] in NOT_RUNNABLE:
        raise ExecError(f"plan is {rec['status']}")
    if rec["status"] in ("needs_answers", "awaiting_approval", "ready", "blocked", "invalid"):
        raise ExecError(f"plan is {rec['status']}; only an approved plan runs (ci plan approve {rec['id']})")
    if rec.get("project_id") and rec["project_id"] != project_id:
        raise ExecError("this plan was compiled in another project's directory; run it from there")
    ws = Workspace(ci)
    queue = TaskQueue(ws, Governor(ws))
    agent = agent or Agent(ci, ws, queue)
    state: dict = rec.setdefault("steps_state", {})
    ostate: dict = rec.setdefault("outcomes_state", {})
    steps = {s["id"]: s for s in rec["spec"]["steps"]}
    outcomes = {o["id"]: o for o in rec["spec"].get("outcomes", [])}
    order = [sid for level in rec["spec"]["levels"] for sid in level]

    if cancel:
        return _cancel(ci, uid, rec, state, agent, project_id)

    for x in done or []:
        if x in outcomes:
            if outcomes[x].get("by") != "you":
                raise ExecError(f"{x} is CI's to complete; its steps' task records decide it, not a mark")
            ostate[x] = {"status": "yours_done", "at": _now()}
        elif x in steps and steps[x]["kind"] == "external":
            state[x] = {"status": "yours_done", "at": _now()}
            for oid in steps[x].get("serves", []):
                if oid in outcomes and outcomes[oid].get("by") == "you":
                    ostate[oid] = {"status": "yours_done", "at": _now(), "via": x}
        else:
            raise ExecError(f"only an external step or an outcome that is yours can be marked done; {x} is neither")
    if retry and ws.is_halted(uid):
        raise ExecError("Stop-all is on (ci halt): run ci resume first, then retry")
    for x in retry or []:
        if rec.get("execution_mode") == "contract" and x in ("contract", "all"):
            cs = rec.get("contract_state") or {}
            if cs.get("status") not in RETRYABLE:
                raise ExecError(f"contract is {cs.get('status') or 'not_started'}; only {', '.join(RETRYABLE)} can be retried")
            rec["contract_state"] = {"status": "not_started", "previous": cs, "at": _now()}
            for sid in order:
                if (state.get(sid) or {}).get("status") in RETRYABLE:
                    state[sid] = {"status": "not_started", "at": _now()}
            continue
        if x not in steps:
            raise ExecError(f"no step {x}")
        cur = (state.get(x) or {}).get("status")
        if cur not in RETRYABLE:
            raise ExecError(f"step {x} is {cur or 'not_started'}; only {', '.join(RETRYABLE)} steps can be retried")
        state[x] = {"status": "not_started", "previous": state.get(x), "at": _now()}
        if rec.get("execution_mode") == "contract" and (rec.get("contract_state") or {}).get("status") in RETRYABLE:
            rec["contract_state"] = {"status": "not_started", "previous": rec.get("contract_state"), "at": _now()}

    if rec.get("execution_mode") == "contract":
        return _execute_contract(ci, uid, rec, project_id, vendors, provider=provider, model=model,
                                 agent=agent, ws=ws, queue=queue)

    chosen: list = []  # the model is resolved only if a step actually needs one

    def pick() -> tuple[str, str | None]:
        if not chosen:
            chosen.append(_model(vendors, provider, model))
        return chosen[0]

    rec["status"] = "executing"
    halt = None
    for n, sid in enumerate(order, 1):
        step, st = steps[sid], state.get(sid) or {}
        status = st.get("status", "not_started")
        if status in DONE:
            continue
        if status == "in_progress":  # we hold the plan's lock, so the run that set this is gone
            state[sid] = dict(st, status="interrupted", at=_now())
            halt = "interrupted"
            break
        if status in STOPS:
            halt = status
            break
        blocked_by = [d for d in step["depends_on"] if (state.get(d) or {}).get("status") not in DONE]
        if blocked_by:
            state[sid] = {"status": "blocked", "by": blocked_by, "at": _now()}
            continue
        if step["kind"] == "external":
            state[sid] = {"status": "yours", "at": _now()}
            continue
        if status == "waiting":
            if any(queue.get(uid, t).status not in TERMINAL for t in st.get("pending", [])):
                halt = "waiting"
                break
            tr = agent.transcript(uid, project_id, st.get("session"))
            live = [t for t in tr.get("pending", []) if queue.get(uid, t).status not in TERMINAL]
            if live:  # approving one action led to another that needs you
                state[sid] = dict(st, pending=live)
                halt = "waiting"
                break
            if st.get("started"):
                tasks = _session_tasks(queue, uid, project_id, st.get("session"), st["started"])
            else:  # recorded before steps carried a start time: the turn's own task list
                tasks = [queue.get(uid, t).public() for t in dict.fromkeys(st.get("tasks", []) + st.get("pending", []))]
            ok, problems = _task_verdict(tasks)
            verdict = "done" if ok else ("halted" if ws.is_halted(uid) else "incomplete")  # Stop-all cancelled it
            state[sid] = dict(st, status=verdict, problems=problems,
                              task_status={t["id"]: t["status"] for t in tasks}, at=_now())
            if not ok:
                halt = verdict
                break
            continue
        if ws.is_halted(uid):  # Stop-all: leave the step untouched, so nothing reads as attempted
            halt = "halted"
            break
        prov, mdl = pick()
        carry = {"previous": st["previous"]} if st.get("previous") else {}  # a retry keeps the earlier attempt
        # Runnable. Record in_progress first, so a crash during the step shows as interrupted
        # instead of being repeated.
        stale = _clear_stale_pending(ci, agent, queue, uid, project_id)
        if stale:
            carry = carry | {"closed_stale_session": stale}
        started = _now()
        state[sid] = carry | {"status": "in_progress", "started": started, "at": started,
                              "model": f"{prov}:{mdl or ''}"}
        rec["steps_state"] = state
        ic._save(ci, uid, rec)
        try:
            turn = agent.send(uid, project_id, _message(rec, step, n, len(order)), prov, mdl)
        except (RuntimeError, ValueError, PermissionError) as e:  # halted meanwhile, vendor not allowed, provider down
            halted = ws.is_halted(uid)
            state[sid] = carry | {"status": "halted" if halted else "failed", "why": str(e)[:300],
                                  "started": started, "at": _now()}
            halt = state[sid]["status"]
            break
        entry = carry | {"session": turn.session_id, "text": turn.text[:2000], "tasks": [t["id"] for t in turn.tasks],
                 "task_status": {t["id"]: t["status"] for t in turn.tasks}, "model": f"{prov}:{mdl or ''}",
                 "started": started, "at": _now()}
        if turn.status == "awaiting_approval":
            state[sid] = entry | {"status": "waiting", "pending": list(turn.pending)}
            halt = "waiting"
            break
        if turn.status != "done":
            stopped = "halted" if turn.stop_reason == "halted" or ws.is_halted(uid) else "failed"
            state[sid] = entry | {"status": stopped, "why": f"{turn.status}: {turn.stop_reason or ''}".strip(": ")}
            halt = stopped
            break
        ok, problems = _task_verdict(turn.tasks)
        state[sid] = entry | {"status": "done" if ok else "incomplete", "problems": problems}
        if not ok:
            halt = "incomplete"
            break

    for sid in order:  # every step has a recorded state, including the ones not reached
        state.setdefault(sid, {"status": "not_started"})
    rec["steps_state"] = state
    rec["outcomes_state"] = _outcomes(rec, state)
    o_status = [v["status"] for v in rec["outcomes_state"].values()]
    steps_ok = all(state[s]["status"] in DONE for s in order)
    if halt:
        rec["status"] = halt
    elif steps_ok and all(s in ("met", "yours_done") for s in o_status):
        rec["status"] = "done"
    elif "yours_pending" in o_status or any(state[s]["status"] in ("yours", "blocked") for s in order):
        rec["status"] = "awaiting_you"
    else:
        rec["status"] = "incomplete"
    ic._save(ci, uid, rec)
    ci.audit.append(uid, "plan.execute", plan=rec["id"], status=rec["status"],
                    steps_done=sum(1 for s in order if state[s]["status"] in DONE), steps=len(order),
                    outcomes_met=sum(1 for s in o_status if s in ("met", "yours_done")), outcomes=len(o_status))
    return {"plan": rec, "outcome": rec["status"]}


def _pending_detail(queue: TaskQueue, uid: str, ids: list[str]) -> list[dict]:
    """What each action waiting on the person is: the tool and its target, for the approve prompt."""
    out = []
    for tid in ids:
        try:
            t = queue.get(uid, tid)
        except KeyError:
            continue
        args = t.args or {}
        target = args.get("path") or args.get("reason") or ""
        out.append({"id": tid, "tool": t.tool, "target": str(target)[:80]})
    return out


def _contract_outcomes(rec: dict, evidence_ok: bool, attempted: bool) -> dict:
    """Judge each outcome in contract mode from tool evidence, never from model claims."""
    spec, out = rec["spec"], {}
    prev = rec.get("outcomes_state") or {}
    for o in spec.get("outcomes", []):
        if (prev.get(o["id"]) or {}).get("status") == "yours_done":
            out[o["id"]] = prev[o["id"]]
            continue
        if o.get("by") == "you":
            out[o["id"]] = {"status": "yours_pending"}
            continue
        met = attempted and evidence_ok
        # what "met" rests on, said plainly: the session's evidence, not a check of this outcome
        out[o["id"]] = {"status": "met" if met else "unmet",
                        "judged_by": rec.get("_evidence_basis") or "the session's task records"}
    return out


def _execute_contract(ci, uid: str, rec: dict, project_id: str, vendors: set[str], *,
                      provider: str | None, model: str | None, agent: Agent, ws: Workspace, queue: TaskQueue) -> dict:
    """Execute the intent contract in ONE governed agent session, judging outcomes from evidence."""
    state: dict = rec.setdefault("steps_state", {})
    cs: dict = rec.get("contract_state") or {"status": "not_started"}
    order = [sid for level in rec["spec"]["levels"] for sid in level]
    steps = {s["id"]: s for s in rec["spec"]["steps"]}
    ws_info = ws.briefing(uid, project_id).get("workspace") or {}
    # tests are required after any change whenever the workspace can run them, whether or not the plan
    # happened to list a "run" step; a plan that changes files needs at least one change
    require_tests = bool(ws_info.get("can_run_tests"))
    require_change = any(s["kind"] == "write" for s in steps.values())

    status = cs.get("status", "not_started")
    halt = None
    tasks: list[dict] = []

    if status == "in_progress":
        cs = dict(cs, status="interrupted", at=_now())
        halt = "interrupted"
    elif status in STOPS:
        halt = status
    elif status == "waiting":
        if any(queue.get(uid, t).status not in TERMINAL for t in cs.get("pending", [])):
            halt = "waiting"
        else:
            tr = agent.transcript(uid, project_id, cs.get("session"))
            live = [t for t in tr.get("pending", []) if queue.get(uid, t).status not in TERMINAL]
            if live:
                cs = dict(cs, pending=live, pending_detail=_pending_detail(queue, uid, live))
                halt = "waiting"
            else:
                tasks = _session_tasks(queue, uid, project_id, cs.get("session"), cs["started"])
                info = _analyze_tasks(tasks, require_tests_after_write=require_tests, require_change=require_change)
                verdict = "done" if info["evidence_ok"] else ("halted" if ws.is_halted(uid) else "incomplete")
                cs = dict(cs, status=verdict, problems=info["problems"], denials=info["denials"],
                          bookkeeping_failures=info["bookkeeping_failures"],
                          task_status={t["id"]: t["status"] for t in tasks}, at=_now())
                if not info["evidence_ok"]:
                    halt = verdict
    elif status == "not_started":
        if ws.is_halted(uid):
            halt = "halted"
            cs = dict(cs, status="halted", why="Stop-all was on before the session started", at=_now())
        else:
            prov, mdl = _model(vendors, provider, model)
            _clear_stale_pending(ci, agent, queue, uid, project_id)
            started = _now()
            cs = {"status": "in_progress", "started": started, "at": started, "model": f"{prov}:{mdl or ''}"}
            rec["contract_state"] = cs
            rec["status"] = "executing"
            ic._save(ci, uid, rec)
            try:
                turn = agent.send(uid, project_id, _contract_message(rec), prov, mdl)
            except (RuntimeError, ValueError, PermissionError) as e:
                halted = ws.is_halted(uid)
                cs = {"status": "halted" if halted else "failed", "why": str(e)[:300], "started": started, "at": _now()}
                halt = cs["status"]
            else:
                tasks = list(turn.tasks)
                entry = {"session": turn.session_id, "text": turn.text[:2000], "tasks": [t["id"] for t in tasks],
                         "task_status": {t["id"]: t["status"] for t in tasks}, "model": f"{prov}:{mdl or ''}",
                         "started": started, "at": _now()}
                if turn.status == "awaiting_approval":
                    cs = entry | {"status": "waiting", "pending": list(turn.pending),
                                  "pending_detail": _pending_detail(queue, uid, list(turn.pending))}
                    halt = "waiting"
                elif turn.status != "done":
                    stopped = "halted" if turn.stop_reason == "halted" or ws.is_halted(uid) else "failed"
                    cs = entry | {"status": stopped, "why": f"{turn.status}: {turn.stop_reason or ''}".strip(": ")}
                    halt = stopped
                else:
                    info = _analyze_tasks(tasks, require_tests_after_write=require_tests, require_change=require_change)
                    cs = entry | {"status": "done" if info["evidence_ok"] else "incomplete",
                                  "problems": info["problems"], "denials": info["denials"],
                                  "bookkeeping_failures": info["bookkeeping_failures"]}
                    if not info["evidence_ok"]:
                        halt = "incomplete"

    rec["contract_state"] = cs
    evidence_ok = cs.get("status") == "done"
    rec["_evidence_basis"] = ("tests passed after the last change (the suite, not a check of each outcome)"
                              if require_tests else "the session finished without a failed or denied change "
                              "(no test command, so nothing was run)")
    for sid in order:
        if (state.get(sid) or {}).get("status") in DONE:
            continue
        if steps[sid]["kind"] == "external":
            state[sid] = {"status": "yours", "at": _now()}
        elif cs.get("status") in ("done", "waiting", "halted", "failed", "interrupted", "incomplete"):
            state[sid] = {"status": cs["status"], "session": cs.get("session"), "at": _now()}
        else:
            state.setdefault(sid, {"status": "not_started"})

    rec["steps_state"] = state
    rec["outcomes_state"] = _contract_outcomes(rec, evidence_ok, attempted=(cs.get("status") == "done"))
    o_status = [v["status"] for v in rec["outcomes_state"].values()]
    denials = cs.get("denials") or []
    bookkeeping = cs.get("bookkeeping_failures") or []
    if halt:
        rec["status"] = halt
    elif all(s in ("met", "yours_done") for s in o_status):
        rec["status"] = "done"
    elif "yours_pending" in o_status or any(state[s]["status"] in ("yours", "blocked") for s in order):
        rec["status"] = "awaiting_you"
    else:
        rec["status"] = "incomplete"

    rec["outcome_success"] = {"status": "met" if all(s in ("met", "yours_done") for s in o_status)
                              else ("awaiting_you" if "yours_pending" in o_status else "unmet"),
                              "outcomes": rec["outcomes_state"]}
    rec["plan_completion"] = {"steps_done": sum(1 for s in order if state[s]["status"] in DONE),
                              "steps_total": len(order), "status": rec["status"]}
    rec["authorization_compliance"] = {"denials": denials, "bookkeeping_failures": bookkeeping,
                                       "clean": not denials}
    ic._save(ci, uid, rec)
    ci.audit.append(uid, "plan.execute", plan=rec["id"], status=rec["status"], mode="contract",
                    steps_done=rec["plan_completion"]["steps_done"], steps=len(order),
                    outcomes_met=sum(1 for s in o_status if s in ("met", "yours_done")), outcomes=len(o_status))
    return {"plan": rec, "outcome": rec["status"], "outcome_success": rec["outcome_success"],
            "plan_completion": rec["plan_completion"], "authorization_compliance": rec["authorization_compliance"]}


def _cancel(ci, uid: str, rec: dict, state: dict, agent: Agent, project_id: str) -> dict:
    """Close each waiting step's session first, so cancelling its actions resumes nothing; then
    cancel them. Steps not done are recorded as cancelled; done ones keep their evidence."""
    n, sessions = 0, set()
    waiting_entries = list(state.values())
    if (rec.get("contract_state") or {}).get("status") == "waiting":
        waiting_entries.append(rec["contract_state"])
    for st in waiting_entries:
        if st.get("status") == "waiting":
            if st.get("session") and st["session"] not in sessions:
                with contextlib.suppress(KeyError):
                    agent.close_session(uid, project_id, st["session"])
                sessions.add(st["session"])
            for t in st.get("pending", []):
                try:
                    agent.cancel(uid, t)
                    n += 1
                except Exception:  # noqa: BLE001 - already resolved; the plan is cancelled regardless
                    pass
    if rec.get("contract_state") and rec["contract_state"].get("status") != "done":
        rec["contract_state"] = dict(rec["contract_state"], status="cancelled", at=_now())
    for s in rec["spec"]["steps"]:
        cur = state.get(s["id"]) or {"status": "not_started"}
        if cur["status"] not in DONE:
            state[s["id"]] = dict(cur, status="cancelled", was=cur["status"], at=_now())
    rec["status"] = "cancelled"
    rec["outcomes_state"] = _outcomes(rec, state)
    ic._save(ci, uid, rec)
    ci.audit.append(uid, "plan.cancel", plan=rec["id"], pending_cancelled=n, sessions_closed=len(sessions))
    return {"plan": rec, "outcome": "cancelled"}


def _render_contract(rec: dict) -> list[str]:
    cs = rec.get("contract_state") or {}
    L = [f"  contract: {cs.get('status', 'not_started')}" + (f" · session {cs['session']}" if cs.get("session") else "")
         + " (the steps below are its checklist, run in one session)"]
    if cs.get("status") == "waiting":
        det = {d["id"]: d for d in cs.get("pending_detail") or []}
        for t in cs.get("pending", []):
            d = det.get(t)
            what = f"  ({d['tool']} {d['target']})".rstrip() if d else ""
            L.append(f"      approve: ci approve {t}   ·   deny: ci deny {t}{what}")
    for pr in cs.get("problems") or []:
        L.append(f"      ! {pr}")
    if cs.get("why"):
        L.append(f"      ! {cs['why'][:160]}")
    for dn in cs.get("denials") or []:
        L.append(f"      denied: {dn.get('tool')} {dn.get('status')}" + (f": {dn['reason'][:100]}" if dn.get("reason") else ""))
    for bk in cs.get("bookkeeping_failures") or []:
        L.append(f"      (bookkeeping only, does not decide the outcome) {bk}")
    if cs.get("status") == "halted":
        L.append(f"      Stop-all is on: ci resume, then ci execute {rec['id']} --retry contract")
    elif cs.get("status") in RETRYABLE:
        L.append(f"      re-run deliberately: ci execute {rec['id']} --retry contract")
    return L


def render(res: dict) -> str:
    rec = res["plan"]
    contract = rec.get("execution_mode") == "contract"
    L = [f"plan {rec['id']} · {rec['status']}"]
    if contract:
        L += _render_contract(rec)
    for level in rec["spec"]["levels"]:
        for sid in level:
            s = next(x for x in rec["spec"]["steps"] if x["id"] == sid)
            st = rec["steps_state"].get(sid) or {"status": "not_started"}
            extra = ""
            if st.get("task_status"):
                extra = " · tools: " + ", ".join(f"{t[-6:]}={v}" for t, v in st["task_status"].items())
            if st.get("by"):
                extra = f" · waiting on {', '.join(st['by'])}"
            L.append(f"  {sid} [{s['kind']}] {st['status']:<12} {s['action'][:64]}{extra}")
            for pr in st.get("problems") or []:
                L.append(f"      ! {pr}")
            if st.get("why"):
                L.append(f"      ! {st['why'][:160]}")
            if st.get("status") == "yours":
                L.append(f"      yours to do; then: ci execute {rec['id']} --done {sid}")
            if contract:
                continue  # the contract block above says what to approve, retry or resume
            if st.get("status") == "waiting":
                L += [f"      approve: ci approve {t}   ·   deny: ci deny {t}" for t in st.get("pending", [])]
            if st.get("status") in RETRYABLE:
                L.append(f"      re-run deliberately: ci execute {rec['id']} --retry {sid}")
            if st.get("status") == "halted":
                L.append("      Stop-all is on: ci resume, then retry")
    if rec.get("outcomes_state"):
        L.append("Outcomes:")
        for o in rec["spec"].get("outcomes", []):
            os_ = rec["outcomes_state"].get(o["id"]) or {}
            hint = f"  (when done: ci execute {rec['id']} --done {o['id']})" if os_.get("status") == "yours_pending" else ""
            L.append(f"  {o['id']} {os_.get('status', '?'):<13} {o['text'][:70]}{hint}")
    if contract and rec.get("outcome_success"):
        pc, ac = rec.get("plan_completion") or {}, rec.get("authorization_compliance") or {}
        basis = next((v.get("judged_by") for v in (rec.get("outcomes_state") or {}).values() if v.get("judged_by")), "")
        L.append(f"Outcome: {rec['outcome_success']['status']} · checklist {pc.get('steps_done', 0)}/{pc.get('steps_total', 0)}"
                 f" · authorization: " + ("clean" if ac.get("clean", True) else f"{len(ac.get('denials') or [])} denied")
                 + (f" · met means: {basis}" if basis else ""))
    if rec["status"] != "done":
        L.append(f"Not done: {rec['status']}. Nothing above is complete unless it says done, met or yours_done.")
    return "\n".join(L)
