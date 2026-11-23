"""HTTP API and dashboard for Personal CI.

Local-first: `pcim serve` binds 127.0.0.1. Every route except onboarding requires the
bearer token issued at onboarding (stored hashed). Every route goes through PersonalCI,
so there is no HTTP path around consent, egress, or the audit log.

Onboarding over HTTP is allowed only while the store has no users, or when
PCIM_ALLOW_ONBOARD=1 — otherwise anyone who can reach the port could mint an account.
"""

from __future__ import annotations

import dataclasses
import os
from importlib import resources
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .adapters.base import ProviderError
from .engine import SCOPE_PREFIX, PersonalCI
from .memory import ConsentRequired
from .types import KINDS, Sensitivity


class OnboardIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = ""
    providers: list[str] = ["mock"]


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    kind: str = "fact"
    key: str | None = None
    value: str | int | float | bool | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    pinned: bool = False
    project_id: str | None = None  # scope to one project; None = user-wide


class CorrectIn(BaseModel):
    content: str
    value: str | int | float | bool | None = None


class IngestIn(BaseModel):
    text: str = Field(min_length=1, max_length=500_000)
    source: str = Field(pattern=r"^ingest:[A-Za-z0-9_./-]+$")
    title: str | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    project_id: str | None = None


class ConsentIn(BaseModel):
    source: str
    purpose: str
    provider: str = "*"
    max_sensitivity: Sensitivity = Sensitivity.PERSONAL
    note: str = ""


class AskIn(BaseModel):
    task: str = Field(min_length=1, max_length=50_000)
    provider: str = "mock"
    model: str | None = None
    budget_tokens: int | None = Field(default=None, ge=0, le=200_000)
    assist_mode: str | None = None


class FeedbackIn(BaseModel):
    task_success: bool | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    thumbs: int | None = Field(default=None, ge=-1, le=1)
    corrected: bool | None = None
    reasked: bool | None = None
    edit_ratio: float | None = Field(default=None, ge=0, le=1)
    comment: str | None = Field(default=None, max_length=2000)


class GoalsIn(BaseModel):
    task_types: list[str]


class ProbeIn(BaseModel):
    skill: str = Field(min_length=1, max_length=200)
    correct: bool
    assisted: bool = False


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(default="", max_length=4000)
    success_criteria: str = Field(default="", max_length=4000)
    constraints: list[str] = Field(default_factory=list, max_length=30)
    providers: list[str] | None = None  # vendors allowed to receive project content; None = onboarding choice

class ProjectPatch(BaseModel):
    name: str | None = None
    objective: str | None = None
    success_criteria: str | None = None
    constraints: list[str] | None = None

class ProviderIn(BaseModel):
    vendor: str

class ClaimIn(BaseModel):
    statement: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)
    uncertainty: str = Field(default="", max_length=1000)

class VerifyIn(BaseModel):
    provider: str | None = None  # None: route deterministically
    model: str | None = None
    include_test_doubles: bool = False

class ResolveIn(BaseModel):
    decision: Literal["verified", "refuted"] | None = None
    note: str = Field(default="", max_length=1000)
    test_task_id: str | None = None  # settle by a run_tests task instead of by decision

class NoteIn(BaseModel):
    kind: str
    text: str = Field(min_length=1, max_length=2000)

class WorkspaceIn(BaseModel):
    root: str
    run_argv: list[str] | None = None
    allow_write: bool = False
    run_timeout_s: int = Field(default=300, ge=1, le=3600)

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=50_000)
    provider: str = "mock"
    model: str | None = None
    new_session: bool = False

class RunStartIn(BaseModel):
    objective: str = Field(min_length=1, max_length=50_000)


class RunSteerIn(BaseModel):
    message: str = Field(min_length=1, max_length=50_000)


class RunApprovalIn(BaseModel):
    approve: bool


class ReasonIn(BaseModel):
    reason: str = ""

class ModeIn(BaseModel):
    mode: str

class GrantIn(BaseModel):
    project_id: str
    tools: list[str]
    max_actions: int = 10
    max_seconds: int = 900
    ttl_minutes: int = 60

class PrefIn(BaseModel):
    topic: str = Field(min_length=1, max_length=60)
    value: str = Field(min_length=1, max_length=300)


def _item_out(i) -> dict:
    d = dataclasses.asdict(i)
    for k in ("valid_from", "valid_to", "recorded_at", "superseded_at"):
        d[k] = d[k].isoformat() if d[k] else None
    d["trust"], d["sensitivity"], d["current"] = i.trust.value, i.sensitivity.value, i.is_current()
    return d


def create_app(ci: PersonalCI, *, run_service=None) -> FastAPI:
    app = FastAPI(title="Personal CI", version="0.1.0",
                  description="Local API for Personal CI. Every call is consent-checked and audited.")

    def user(authorization: str = Header(default="")) -> str:
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        uid = ci.user_for_token(authorization.split(" ", 1)[1].strip())
        if uid is None:
            raise HTTPException(401, "unknown token")
        return uid

    def owned(fn, *a, **k):
        try:
            return fn(*a, **k)
        except KeyError:
            raise HTTPException(404, "not found")

    @app.post("/v1/onboard")
    def onboard(body: OnboardIn):
        if ci.store.list_user_ids() and os.environ.get("PCIM_ALLOW_ONBOARD") != "1":
            raise HTTPException(403, "onboarding is closed once a user exists (set PCIM_ALLOW_ONBOARD=1)")
        if ci.store.get_user(body.user_id):
            raise HTTPException(409, "user exists")
        return ci.onboard(body.user_id, body.display_name, tuple(body.providers))

    @app.get("/v1/me")
    def me(uid: str = Depends(user)):
        return ci.store.get_user(uid)

    @app.delete("/v1/me")
    def erase(uid: str = Depends(user)):
        return ci.erase(uid)

    @app.get("/v1/memory")
    def memory(uid: str = Depends(user), history: bool = False):
        items = ci.memory.all(uid) if history else ci.memory.current(uid)
        return [_item_out(i) for i in items]

    def scope_tags(uid: str, project_id: str | None) -> tuple[str, ...]:
        if project_id is None:
            return ()
        owned(ws.get_project, uid, project_id)  # 404 for a project that is not this user's
        return (f"{SCOPE_PREFIX}{project_id}",)

    @app.post("/v1/memory")
    def remember(body: MemoryIn, uid: str = Depends(user)):
        if body.kind not in KINDS:
            raise HTTPException(422, f"kind must be one of {KINDS}")
        r = ci.remember(uid, body.content, body.kind, key=body.key, value=body.value,
                        sensitivity=body.sensitivity, pinned=body.pinned, tags=scope_tags(uid, body.project_id))
        return {"item": _item_out(r.item), "superseded": r.superseded, "conflicts": r.conflicts,
                "gated_from": r.gated_from, "reaffirmed": r.reaffirmed}

    @app.post("/v1/memory/{item_id}/correct")
    def correct(item_id: str, body: CorrectIn, uid: str = Depends(user)):
        r = owned(ci.memory.correct, uid, item_id, body.content, value=body.value)
        return {"item": _item_out(r.item), "superseded": r.superseded}

    @app.post("/v1/memory/{item_id}/confirm")
    def confirm(item_id: str, promote: bool = True, uid: str = Depends(user)):
        return _item_out(owned(ci.memory.confirm, uid, item_id, promote=promote))

    @app.post("/v1/memory/{item_id}/retract")
    def retract(item_id: str, uid: str = Depends(user)):
        owned(ci.memory.retract, uid, item_id)
        return {"ok": True}

    @app.post("/v1/memory/{item_id}/restore")
    def restore(item_id: str, uid: str = Depends(user)):
        owned(ci.memory.restore, uid, item_id)
        return {"ok": True}

    @app.post("/v1/memory/{item_id}/pin")
    def pin(item_id: str, pinned: bool = True, uid: str = Depends(user)):
        owned(ci.memory.pin, uid, item_id, pinned)
        return {"ok": True}

    @app.delete("/v1/memory/{item_id}")
    def delete(item_id: str, cascade: bool = True, uid: str = Depends(user)):
        return {"deleted": owned(ci.memory.delete, uid, item_id, cascade=cascade)}

    @app.get("/v1/conflicts")
    def conflicts(uid: str = Depends(user)):
        return ci.store.list_conflicts(uid)

    @app.post("/v1/ingest")
    def ingest(body: IngestIn, uid: str = Depends(user)):
        try:
            rs = ci.ingest_text(uid, body.text, body.source, body.title, body.sensitivity,
                                tags=scope_tags(uid, body.project_id))
        except ConsentRequired as e:
            raise HTTPException(403, str(e))
        return {"items": [_item_out(r.item) for r in rs],
                "flagged": [r.item.id for r in rs if "contains_instructions" in r.item.flags]}

    @app.get("/v1/consent")
    def consent(uid: str = Depends(user)):
        return [dataclasses.asdict(g) | {"max_sensitivity": g.max_sensitivity.value,
                                         "active": g.revoked_at is None} for g in ci.ledger.grants(uid)]

    @app.post("/v1/consent")
    def grant(body: ConsentIn, uid: str = Depends(user)):
        try:
            g = ci.consent(uid, body.source, body.purpose, body.provider, body.max_sensitivity, body.note)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return dataclasses.asdict(g) | {"max_sensitivity": g.max_sensitivity.value}

    @app.delete("/v1/consent/{grant_id}")
    def revoke(grant_id: str, delete_stored: bool = False, uid: str = Depends(user)):
        return owned(ci.revoke, uid, grant_id, delete_stored)

    @app.post("/v1/ask")
    def ask(body: AskIn, uid: str = Depends(user)):
        try:
            r = ci.ask(uid, body.task, body.provider, body.model, body.budget_tokens, body.assist_mode)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(422, str(e))
        return {"interaction_id": r.interaction_id, "text": r.text, "receipt": r.receipt}

    @app.post("/v1/feedback/{interaction_id}")
    def feedback(interaction_id: str, body: FeedbackIn, uid: str = Depends(user)):
        signals = {k: v for k, v in body.model_dump().items() if v is not None}
        return {"reward": owned(ci.feedback, uid, interaction_id, **signals)}

    @app.get("/v1/interactions")
    def interactions(uid: str = Depends(user)):
        return ci.store.list_interactions(uid)

    @app.get("/v1/interactions/{interaction_id}")
    def receipt(interaction_id: str, uid: str = Depends(user)):
        return owned(ci.receipt, uid, interaction_id)

    @app.get("/v1/dna")
    def dna(uid: str = Depends(user)):
        return ci.dna(uid)

    @app.get("/v1/metrics")
    def metrics(uid: str = Depends(user)):
        return ci.metrics(uid)

    @app.get("/v1/audit")
    def audit(uid: str = Depends(user), limit: int = 500):
        ok, bad = ci.audit.verify()
        return {"chain_ok": ok, "first_bad_seq": bad, "head": ci.audit.head(),
                "entries": ci.audit.entries(uid)[-limit:]}

    @app.get("/v1/export")
    def export(uid: str = Depends(user)):
        out = ci.export(uid)
        out["projects"] = [{"project": project_out(p), "providers": ws.provider_vendors(uid, p.id),
                            "claims": ledger.list(uid, p.id),
                            "tasks": [t.public() for t in queue.list(uid, p.id, limit=100_000)],
                            "disclosures": agent.disclosures(uid, p.id), "exchange": gateway.messages(uid, p.id)}
                           for p in ws.list_projects(uid)]
        return out

    @app.post("/v1/growth/goals")
    def goals(body: GoalsIn, uid: str = Depends(user)):
        try:
            ci.set_growth_goals(uid, body.task_types)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"ok": True}

    @app.post("/v1/growth/probe")
    def probe(body: ProbeIn, uid: str = Depends(user)):
        return ci.probe(uid, body.skill, body.correct, body.assisted)

    # ------------------------------------------------------------------ workspace slice
    from . import registry
    from .agent import Agent
    from .exchange import ClaimLedger, Gateway
    from .governor import Governor
    from .tasks import TaskQueue, TaskStateError
    from .workspace import Workspace, WorkspaceError, render_briefing

    ws = Workspace(ci)
    ledger = ClaimLedger(ws)
    queue = TaskQueue(ws, Governor(ws), claims=ledger)
    agent = Agent(ci, ws, queue, ledger)
    gateway = Gateway(ci, ws, queue, ledger, agent.adapter)
    queue.recover()  # nothing that was running when the server stopped is re-run
    app.state.workspace, app.state.queue, app.state.agent, app.state.gateway = ws, queue, agent, gateway

    def guard(fn, *a, **k):
        try:
            return fn(*a, **k)
        except KeyError:
            raise HTTPException(404, "not found")
        except PermissionError as e:  # provider access revoked or never granted
            raise HTTPException(403, str(e))
        except (WorkspaceError, ValueError) as e:
            raise HTTPException(422, str(e))
        except (ProviderError, httpx.HTTPError) as e:
            raise HTTPException(502, f"model provider error: {e}")
        except (TaskStateError, RuntimeError) as e:
            raise HTTPException(409, str(e))

    app.state.run_service = run_service

    def runs():
        if app.state.run_service is None:
            try:
                from .run_service import RunService
            except ImportError:
                raise HTTPException(503, "Execution service unavailable; install the integrated ALTER build.") from None
            app.state.run_service = RunService(ci)
        return app.state.run_service

    @app.get("/v1/projects/{pid}/runs")
    def run_list(pid: str, uid: str = Depends(user)):
        guard(ws.get_project, uid, pid)
        return guard(runs().list, uid, pid)

    @app.post("/v1/projects/{pid}/runs")
    def run_start(pid: str, body: RunStartIn, uid: str = Depends(user)):
        guard(ws.get_project, uid, pid)
        if not body.objective.strip():
            raise HTTPException(422, "objective must contain text")
        return guard(runs().start, uid, pid, body.objective, set(guard(ws.provider_vendors, uid, pid)))

    @app.get("/v1/projects/{pid}/runs/{rid}")
    def run_get(pid: str, rid: str, uid: str = Depends(user)):
        return guard(runs().get, uid, pid, rid)

    @app.get("/v1/projects/{pid}/runs/{rid}/events")
    def run_events(pid: str, rid: str, uid: str = Depends(user)):
        return guard(runs().get, uid, pid, rid)["events"]

    @app.post("/v1/projects/{pid}/runs/{rid}/advance")
    def run_advance(pid: str, rid: str, uid: str = Depends(user)):
        guard(ws.get_project, uid, pid)
        return guard(runs().advance, uid, pid, rid)

    @app.post("/v1/projects/{pid}/runs/{rid}/pause")
    def run_pause(pid: str, rid: str, uid: str = Depends(user)):
        guard(ws.get_project, uid, pid)
        return guard(runs().pause, uid, pid, rid)

    @app.post("/v1/projects/{pid}/runs/{rid}/resume")
    def run_resume(pid: str, rid: str, uid: str = Depends(user)):
        guard(ws.get_project, uid, pid)
        return guard(runs().resume, uid, pid, rid)

    @app.post("/v1/projects/{pid}/runs/{rid}/cancel")
    def run_cancel(pid: str, rid: str, uid: str = Depends(user)):
        guard(ws.get_project, uid, pid)
        return guard(runs().cancel, uid, pid, rid)

    @app.post("/v1/projects/{pid}/runs/{rid}/steer")
    def run_steer(pid: str, rid: str, body: RunSteerIn, uid: str = Depends(user)):
        guard(ws.get_project, uid, pid)
        if not body.message.strip():
            raise HTTPException(422, "message must contain text")
        return guard(runs().steer, uid, pid, rid, body.message)

    @app.post("/v1/projects/{pid}/runs/{rid}/approvals/{tid}")
    def run_approval(pid: str, rid: str, tid: str, body: RunApprovalIn, uid: str = Depends(user)):
        guard(ws.get_project, uid, pid)
        return guard(runs().resolve, uid, pid, rid, tid, approve=body.approve)

    def turn_out(t):
        return t.as_dict() if hasattr(t, "as_dict") else t

    def project_out(p):
        return dataclasses.asdict(p)

    @app.get("/v1/projects")
    def projects(uid: str = Depends(user)):
        return [project_out(p) for p in ws.list_projects(uid)]

    @app.post("/v1/projects")
    def create_project(body: ProjectIn, uid: str = Depends(user)):
        return project_out(guard(ws.create_project, uid, body.name, body.objective, body.success_criteria,
                                 body.constraints, body.providers))

    @app.get("/v1/projects/{pid}")
    def project(pid: str, uid: str = Depends(user)):
        return {"project": project_out(guard(ws.get_project, uid, pid)), "briefing": guard(agent.briefing, uid, pid),
                "providers": guard(ws.provider_vendors, uid, pid)}

    @app.get("/v1/projects/{pid}/providers")
    def providers(pid: str, uid: str = Depends(user)):
        return {"vendors": guard(ws.provider_vendors, uid, pid), "known": list(registry.VENDORS)}

    @app.post("/v1/projects/{pid}/providers")
    def grant_provider(pid: str, body: ProviderIn, uid: str = Depends(user)):
        guard(ws.grant_provider, uid, pid, body.vendor, "granted by you")
        return {"vendors": ws.provider_vendors(uid, pid)}

    @app.delete("/v1/projects/{pid}/providers/{vendor}")
    def revoke_provider(pid: str, vendor: str, uid: str = Depends(user)):
        n = guard(ws.revoke_provider, uid, pid, vendor)
        return {"revoked": n, "vendors": ws.provider_vendors(uid, pid)}

    @app.get("/v1/projects/{pid}/disclosures")
    def disclosures(pid: str, uid: str = Depends(user)):
        return guard(agent.disclosures, uid, pid)

    @app.get("/v1/projects/{pid}/briefing")
    def briefing_preview(pid: str, vendor: str, uid: str = Depends(user)):
        """What a new session with `vendor` would be given, and what would be withheld and why."""
        if vendor not in registry.VENDORS:
            raise HTTPException(422, f"vendor must be one of {registry.VENDORS}")
        b = guard(agent.briefing, uid, pid, vendor)
        return {"vendor": vendor, "allowed": vendor in ws.provider_vendors(uid, pid), "text": render_briefing(b),
                "withheld": b["withheld"]}

    @app.post("/v1/projects/{pid}/notes/{nid}/release")
    def release_note(pid: str, nid: str, uid: str = Depends(user)):
        return guard(ws.release_note, uid, pid, nid)

    @app.post("/v1/projects/{pid}/claims/{cid}/release")
    def release_claim(pid: str, cid: str, uid: str = Depends(user)):
        return guard(ledger.release, uid, pid, cid)

    @app.get("/v1/projects/{pid}/claims")
    def claims(pid: str, uid: str = Depends(user)):
        return guard(ledger.list, uid, pid)

    @app.post("/v1/projects/{pid}/claims")
    def add_claim(pid: str, body: ClaimIn, uid: str = Depends(user)):
        return guard(ledger.add, uid, pid, body.statement, body.evidence_refs, body.uncertainty, {"kind": "person"})

    @app.get("/v1/projects/{pid}/route")
    def route(pid: str, role: str = "verify", claim_id: str | None = None, include_test_doubles: bool = False,
              uid: str = Depends(user)):
        author = guard(ledger.get, uid, pid, claim_id)["author"] if claim_id else None
        return guard(registry.route, role, set(guard(ws.provider_vendors, uid, pid)), author,
                     include_test_doubles).as_dict()

    @app.post("/v1/projects/{pid}/claims/{cid}/verify")
    def verify(pid: str, cid: str, body: VerifyIn, uid: str = Depends(user)):
        chosen = None
        provider, model = body.provider, body.model
        if provider is None:
            r = registry.route("verify", set(guard(ws.provider_vendors, uid, pid)), guard(ledger.get, uid, pid, cid)["author"],
                               body.include_test_doubles)
            if r.entry is None:
                raise HTTPException(409, r.reason)
            provider, model, chosen = r.entry.provider, r.entry.model, r.as_dict()
        out = guard(gateway.verify_claim, uid, pid, cid, provider, model)
        return out | {"route": chosen}

    @app.post("/v1/projects/{pid}/claims/{cid}/resolve")
    def resolve_claim(pid: str, cid: str, body: ResolveIn, uid: str = Depends(user)):
        if body.test_task_id:
            return guard(gateway.resolve_with_test, uid, pid, cid, body.test_task_id, body.note)
        if body.decision is None:
            raise HTTPException(422, "give a decision or a test_task_id")
        return guard(ledger.resolve, uid, pid, cid, body.decision, "person", body.note)

    @app.get("/v1/projects/{pid}/exchange")
    def exchange(pid: str, uid: str = Depends(user)):
        return guard(gateway.messages, uid, pid)

    @app.get("/v1/models")
    def models(uid: str = Depends(user)):
        return registry.describe(ledger.track_record(uid))

    @app.patch("/v1/projects/{pid}")
    def patch_project(pid: str, body: ProjectPatch, uid: str = Depends(user)):
        return project_out(guard(ws.update_project, uid, pid, **body.model_dump()))

    @app.delete("/v1/projects/{pid}")
    def delete_project(pid: str, uid: str = Depends(user)):
        return guard(ws.delete_project, uid, pid)

    @app.post("/v1/projects/{pid}/notes")
    def add_note(pid: str, body: NoteIn, uid: str = Depends(user)):
        return guard(ws.add_note, uid, pid, body.kind, body.text, "user")

    @app.post("/v1/projects/{pid}/notes/{nid}/resolve")
    def resolve_note(pid: str, nid: str, uid: str = Depends(user)):
        guard(ws.resolve_note, uid, pid, nid)
        return {"ok": True}

    @app.post("/v1/projects/{pid}/workspace")
    def authorize(pid: str, body: WorkspaceIn, uid: str = Depends(user)):
        aid = guard(ws.authorize_workspace, uid, pid, body.root, body.run_argv, body.allow_write, body.run_timeout_s)
        return {"authorization_id": aid}

    @app.post("/v1/projects/{pid}/chat")
    def chat(pid: str, body: ChatIn, uid: str = Depends(user)):
        guard(ws.get_project, uid, pid)
        return turn_out(guard(agent.send, uid, pid, body.message, body.provider, body.model, body.new_session))

    @app.get("/v1/projects/{pid}/session")
    def session(pid: str, uid: str = Depends(user)):
        guard(ws.get_project, uid, pid)
        return guard(agent.transcript, uid, pid)

    @app.post("/v1/projects/{pid}/session/close")
    def close_session(pid: str, uid: str = Depends(user)):
        guard(ws.get_project, uid, pid)
        return {"reflection": guard(agent.close_session, uid, pid)}

    @app.get("/v1/tasks")
    def tasks(uid: str = Depends(user), project_id: str | None = None, status: str | None = None):
        return [t.public() for t in queue.list(uid, project_id, status)]

    @app.get("/v1/tasks/{tid}")
    def task(tid: str, uid: str = Depends(user)):
        return guard(queue.get, uid, tid).public()

    @app.post("/v1/tasks/{tid}/approve")
    def approve(tid: str, uid: str = Depends(user)):
        return turn_out(guard(agent.resolve, uid, tid, True))

    @app.post("/v1/tasks/{tid}/deny")
    def deny(tid: str, body: ReasonIn, uid: str = Depends(user)):
        return turn_out(guard(agent.resolve, uid, tid, False, body.reason))

    @app.post("/v1/tasks/{tid}/cancel")
    def cancel(tid: str, uid: str = Depends(user)):
        return turn_out(guard(agent.cancel, uid, tid))

    @app.post("/v1/tasks/{tid}/undo")
    def undo(tid: str, uid: str = Depends(user)):
        return guard(queue.undo, uid, tid)

    @app.get("/v1/autonomy")
    def autonomy(uid: str = Depends(user)):
        return {"mode": ws.get_mode(uid), "halted": ws.is_halted(uid), "grants": ws.all_auths(uid)}

    @app.put("/v1/autonomy")
    def set_mode(body: ModeIn, uid: str = Depends(user)):
        guard(ws.set_mode, uid, body.mode)
        return {"mode": body.mode}

    @app.post("/v1/autonomy/grants")
    def grant_autonomy(body: GrantIn, uid: str = Depends(user)):
        return {"authorization_id": guard(ws.grant_autonomy, uid, body.project_id, body.tools, body.max_actions,
                                          body.max_seconds, body.ttl_minutes)}

    @app.delete("/v1/autonomy/grants/{aid}")
    def revoke_grant(aid: str, uid: str = Depends(user)):
        return {"revoked": ws.revoke(uid, aid)}

    @app.post("/v1/autonomy/halt")
    def halt(uid: str = Depends(user)):
        return queue.halt(uid)

    @app.post("/v1/autonomy/resume")
    def resume(uid: str = Depends(user)):
        ws.set_halted(uid, False)
        return {"halted": False}

    @app.post("/v1/preferences")
    def preference(body: PrefIn, uid: str = Depends(user)):
        return guard(agent.set_collaboration_preference, uid, body.topic, body.value)

    dash = Path(str(resources.files("pcim") / "dashboard"))
    if dash.exists():
        app.mount("/static", StaticFiles(directory=dash), name="static")

        @app.get("/", response_class=HTMLResponse)
        def index():
            return (dash / "index.html").read_text()

    return app
