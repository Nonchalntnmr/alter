"""Projects, autonomy settings and authorizations: the durable state the workspace runs on.

A project holds what a person is trying to do and what has been learned so far:
objective, constraints, success criteria, and notes (open questions, decisions, next
steps), each with a provenance (`user`, `model`, `system`, plus which provider and model
wrote it) and a timestamp. Model-written notes are always labelled as such and rendered as
data, never as instructions.

Provider access: a project names the vendors that may receive its content (briefing,
transcript, tool results, claims). It is checked before every model step and every
delegation, so revoking a vendor stops the next request, including one that would resume
a session paused on an approval. New projects start with the providers chosen at
onboarding, recorded as explicit, revocable grants.

Everything a project says is sealed under the project's own key. Deleting a project
destroys that key, and with it every transcript, task argument and task result.

Autonomy:
- **Mode** (copilot | collaborative | autonomous) lives in the user's settings.
- A **workspace authorization** attaches one directory to a project, the test argv the
  person allows, and whether writes are allowed.
- An **autonomous authorization** names tools, a max action count, a max duration, and an
  expiry. It is checked on every call and is never widened by anything a task does.
- **Halt** blocks all execution until the person resumes.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import subprocess
from pathlib import Path

from .engine import SCOPE_PREFIX, PersonalCI
from .registry import VENDORS, vendor_of
from .types import new_id, utcnow

MODES = ("copilot", "collaborative", "autonomous")
NOTE_KINDS = ("open_question", "decision", "next_step", "summary")
EXECUTABLE_TOOLS = ("read_file", "list_files", "search_code", "git_status", "record_note", "record_claim",
                    "run_tests", "write_file")


class WorkspaceError(ValueError):
    pass


class ProviderAccessDenied(PermissionError):
    pass


@dataclasses.dataclass
class Project:
    id: str
    name: str
    objective: str
    success_criteria: str
    notes: list[dict]
    status: str
    created_at: str
    updated_at: str
    constraints: list[str] = dataclasses.field(default_factory=list)

    def open_notes(self, kind: str) -> list[dict]:
        return [n for n in self.notes if n["kind"] == kind and not n.get("resolved_at")]


def _home_is_git_repo() -> bool:
    try:
        r = subprocess.run(["git", "-C", str(Path.home()), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and Path(r.stdout.strip()).resolve() == Path.home().resolve()
    except Exception:
        return False


def validate_root(root: str | Path) -> Path:
    """A workspace root must be an existing directory that is not /, $HOME, or an ancestor of $HOME."""
    p = Path(root).expanduser().resolve()
    home = Path.home().resolve()
    if not p.is_dir():
        raise WorkspaceError(f"{p} is not a directory")
    if p == Path("/") or p == home or p in home.parents:
        raise WorkspaceError(f"{p} is too broad to authorize; choose a project directory")
    return p


class Workspace:
    def __init__(self, ci: PersonalCI):
        self.ci = ci
        self.store = ci.store
        self.audit = ci.audit

    # ------------------------------------------------------------------ projects
    def _payload(self, row) -> dict:
        return self.store.open_json(row["key_id"], row["payload_ct"], row["id"])

    def _row(self, user_id: str, project_id: str):
        rows = self.store.query("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
        if not rows:
            raise KeyError(project_id)
        return rows[0]

    def _to_project(self, row) -> Project:
        p = self._payload(row)
        return Project(id=row["id"], name=p["name"], objective=p.get("objective", ""),
                       success_criteria=p.get("success_criteria", ""), notes=p.get("notes", []),
                       status=row["status"], created_at=row["created_at"], updated_at=row["updated_at"],
                       constraints=p.get("constraints", []))

    def _save(self, row, payload: dict) -> None:
        now = utcnow().isoformat()
        self.store.execute("UPDATE projects SET payload_ct=?, updated_at=? WHERE id=?",
                           (self.store.seal_json(row["key_id"], payload, row["id"]), now, row["id"]))

    @staticmethod
    def _clean_constraints(constraints) -> list[str]:
        return [c.strip()[:500] for c in (constraints or []) if isinstance(c, str) and c.strip()][:30]

    def create_project(self, user_id: str, name: str, objective: str = "", success_criteria: str = "",
                       constraints: list[str] | None = None, providers: list[str] | None = None) -> Project:
        """`providers` names the vendors (or providers) that may receive this project's content;
        None means the providers chosen at onboarding."""
        if not name.strip():
            raise WorkspaceError("a project needs a name")
        vendors = self._onboarded_vendors(user_id) if providers is None else self._vendors(providers)
        pid, key_id, now = new_id("prj"), self.store.new_project_key(user_id), utcnow().isoformat()
        payload = {"name": name.strip(), "objective": objective, "success_criteria": success_criteria,
                   "constraints": self._clean_constraints(constraints), "notes": [], "provider_access": "explicit"}
        self.store.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?)",
                           (pid, user_id, key_id, now, now, "active", self.store.seal_json(key_id, payload, pid)))
        self.audit.append(user_id, "project.create", project=pid)
        for v in vendors:
            self.grant_provider(user_id, pid, v, note="project created")
        return self.get_project(user_id, pid)

    def get_project(self, user_id: str, project_id: str) -> Project:
        return self._to_project(self._row(user_id, project_id))

    def list_projects(self, user_id: str) -> list[Project]:
        rows = self.store.query("SELECT * FROM projects WHERE user_id=? ORDER BY updated_at DESC", (user_id,))
        return [self._to_project(r) for r in rows]

    def update_project(self, user_id: str, project_id: str, **fields) -> Project:
        row = self._row(user_id, project_id)
        p = self._payload(row)
        for k in ("name", "objective", "success_criteria"):
            if k in fields and fields[k] is not None:
                p[k] = fields[k]
        if fields.get("constraints") is not None:
            p["constraints"] = self._clean_constraints(fields["constraints"])
        self._save(row, p)
        self.audit.append(user_id, "project.update", project=project_id, fields=sorted(fields))
        return self.get_project(user_id, project_id)

    def add_note(self, user_id: str, project_id: str, kind: str, text: str, source: str = "user",
                 by: dict | None = None, lineage: list[str] | None = None) -> dict:
        """`by` records who wrote a model note: {"provider", "model", "session"}. `lineage` lists
        the memory items the writing model had been shown; a vendor that may not see one of
        them is not shown the note either (derived_blocked)."""
        if kind not in NOTE_KINDS:
            raise WorkspaceError(f"note kind must be one of {NOTE_KINDS}")
        if source not in ("user", "model", "system"):
            raise WorkspaceError("bad note source")
        text = text.strip()[:2000]
        if not text:
            raise WorkspaceError("empty note")
        row = self._row(user_id, project_id)
        p = self._payload(row)
        note = {"id": new_id("note"), "kind": kind, "text": text, "source": source,
                "created_at": utcnow().isoformat(), "resolved_at": None}
        if by:
            note["by"] = {k: str(by[k])[:80] for k in ("provider", "model", "session") if by.get(k)}
        if lineage:
            note["lineage"] = list(dict.fromkeys(lineage))[:200]
        p.setdefault("notes", []).append(note)
        self._save(row, p)
        self.audit.append(user_id, "project.note", project=project_id, note=note["id"], kind=kind, source=source)
        return note

    def resolve_note(self, user_id: str, project_id: str, note_id: str) -> None:
        row = self._row(user_id, project_id)
        p = self._payload(row)
        for n in p.get("notes", []):
            if n["id"] == note_id:
                n["resolved_at"] = utcnow().isoformat()
                self._save(row, p)
                self.audit.append(user_id, "project.note_resolved", project=project_id, note=note_id)
                return
        raise KeyError(note_id)

    def release_note(self, user_id: str, project_id: str, note_id: str) -> dict:
        """The person has read a model-written note and lets every authorized vendor see it,
        whatever memory the writing model had been shown."""
        row = self._row(user_id, project_id)
        p = self._payload(row)
        for n in p.get("notes", []):
            if n["id"] == note_id:
                n["released_at"] = utcnow().isoformat()
                self._save(row, p)
                self.audit.append(user_id, "project.note_release", project=project_id, note=note_id)
                return n
        raise KeyError(note_id)

    def delete_project(self, user_id: str, project_id: str) -> dict:
        """Destroys the project key (transcripts, tasks, claims, exchange records) and deletes the
        memory scoped to this project, with everything derived from it. User-wide memory stays."""
        row = self._row(user_id, project_id)
        tag = f"{SCOPE_PREFIX}{project_id}"
        doomed = []
        for item in self.ci.memory.all(user_id):
            if tag in item.tags and item.id not in doomed and self.store.get_item(item.id) is not None:
                doomed += self.ci.memory.delete(user_id, item.id, cascade=True)
        counts = self.store.delete_project_rows(project_id, row["key_id"])
        counts["memory_items"] = len(doomed)
        self.audit.append(user_id, "project.delete", project=project_id, **{k: int(v) for k, v in counts.items()})
        return counts

    def project_key(self, user_id: str, project_id: str) -> str:
        return self._row(user_id, project_id)["key_id"]

    # ------------------------------------------------------------------ autonomy settings
    def _settings(self, user_id: str) -> dict:
        u = self.store.get_user(user_id)
        if u is None:
            raise KeyError(user_id)
        return u["settings"]

    def get_mode(self, user_id: str) -> str:
        return self._settings(user_id).get("autonomy_mode", "collaborative")

    def set_mode(self, user_id: str, mode: str) -> None:
        if mode not in MODES:
            raise WorkspaceError(f"mode must be one of {MODES}")
        s = self._settings(user_id)
        s["autonomy_mode"] = mode
        self.store.update_settings(user_id, s)
        self.audit.append(user_id, "autonomy.mode", mode=mode)

    def is_halted(self, user_id: str) -> bool:
        return bool(self._settings(user_id).get("halted"))

    def set_halted(self, user_id: str, halted: bool) -> None:
        s = self._settings(user_id)
        s["halted"] = halted
        self.store.update_settings(user_id, s)
        self.audit.append(user_id, "autonomy.halt" if halted else "autonomy.resume")

    # ------------------------------------------------------------------ authorizations
    def _insert_auth(self, user_id: str, project_id: str | None, kind: str, spec: dict,
                     expires_at: dt.datetime | None) -> str:
        aid = new_id("auth")
        self.store.execute(
            "INSERT INTO authorizations(id, user_id, project_id, kind, spec, created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
            (aid, user_id, project_id, kind, json.dumps(spec), utcnow().isoformat(),
             expires_at.isoformat() if expires_at else None),
        )
        return aid

    def authorize_workspace(self, user_id: str, project_id: str, root: str, run_argv: list[str] | None = None,
                            allow_write: bool = False, run_timeout_s: int = 300) -> str:
        self._row(user_id, project_id)
        p = validate_root(root)
        if run_argv is not None:
            if not run_argv or not all(isinstance(a, str) and a for a in run_argv):
                raise WorkspaceError("run_argv must be a non-empty list of strings")
            if run_argv[0] in ("sh", "bash", "zsh", "fish", "sudo", "su") or any(c in run_argv[0] for c in ";|&`$"):
                raise WorkspaceError("run_argv must name a test runner directly, not a shell")
        for old in self.active_auths(user_id, project_id, "workspace"):
            self.revoke(user_id, old["id"], audit=False)
        spec = {"root": str(p), "run_argv": run_argv, "allow_write": bool(allow_write),
                "run_timeout_s": int(run_timeout_s), "home_is_git_repo": _home_is_git_repo()}
        aid = self._insert_auth(user_id, project_id, "workspace", spec, None)
        self.audit.append(user_id, "workspace.authorize", project=project_id, auth=aid, write=bool(allow_write),
                          run=run_argv is not None)
        return aid

    def grant_autonomy(self, user_id: str, project_id: str, tools: list[str], max_actions: int = 10,
                       max_seconds: int = 900, ttl_minutes: int = 60) -> str:
        self._row(user_id, project_id)
        bad = set(tools) - set(EXECUTABLE_TOOLS)
        if bad or not tools:
            raise WorkspaceError(f"unknown tools {sorted(bad)}" if bad else "name at least one tool")
        if not self.active_auths(user_id, project_id, "workspace"):
            raise WorkspaceError("authorize a workspace for this project first")
        if not (1 <= max_actions <= 200 and 1 <= max_seconds <= 86400 and 1 <= ttl_minutes <= 7 * 24 * 60):
            raise WorkspaceError("limits out of range")
        spec = {"tools": sorted(set(tools)), "max_actions": int(max_actions), "max_seconds": int(max_seconds)}
        aid = self._insert_auth(user_id, project_id, "autonomous", spec,
                                utcnow() + dt.timedelta(minutes=ttl_minutes))
        self.audit.append(user_id, "autonomy.grant", project=project_id, auth=aid, tools=spec["tools"],
                          max_actions=max_actions, max_seconds=max_seconds, ttl_minutes=ttl_minutes)
        return aid

    def revoke(self, user_id: str, auth_id: str, audit: bool = True) -> bool:
        cur = self.store.execute("UPDATE authorizations SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
                                 (utcnow().isoformat(), auth_id, user_id))
        if cur.rowcount and audit:
            self.audit.append(user_id, "authorization.revoke", auth=auth_id)
        return cur.rowcount > 0

    def active_auths(self, user_id: str, project_id: str | None, kind: str) -> list[dict]:
        now = utcnow()
        rows = self.store.query(
            "SELECT * FROM authorizations WHERE user_id=? AND kind=? AND revoked_at IS NULL"
            + (" AND project_id=?" if project_id else ""),
            (user_id, kind, project_id) if project_id else (user_id, kind))
        out = []
        for r in rows:
            if r["expires_at"] and dt.datetime.fromisoformat(r["expires_at"]) <= now:
                continue
            d = dict(r)
            d["spec"] = json.loads(d["spec"])
            out.append(d)
        return out

    def all_auths(self, user_id: str) -> list[dict]:
        rows = self.store.query("SELECT * FROM authorizations WHERE user_id=? ORDER BY created_at DESC", (user_id,))
        return [dict(r) | {"spec": json.loads(r["spec"])} for r in rows]

    def workspace_auth(self, user_id: str, project_id: str) -> dict | None:
        a = self.active_auths(user_id, project_id, "workspace")
        return a[0] if a else None

    def consume(self, auth_id: str) -> None:
        self.store.execute(
            "UPDATE authorizations SET used_actions = used_actions + 1, first_used_at = COALESCE(first_used_at, ?) "
            "WHERE id=?", (utcnow().isoformat(), auth_id))

    # ------------------------------------------------------------------ provider access
    @staticmethod
    def _vendors(names) -> list[str]:
        out = []
        for n in names or []:
            v = n if n in VENDORS else vendor_of(n)  # ValueError for an unknown name
            if v not in out:
                out.append(v)
        return out

    def _onboarded_vendors(self, user_id: str) -> list[str]:
        return self._vendors(p for p in self._settings(user_id).get("providers", []) if p != "*")

    def grant_provider(self, user_id: str, project_id: str, vendor: str, note: str = "") -> str:
        self._row(user_id, project_id)
        if vendor not in VENDORS:
            raise WorkspaceError(f"vendor must be one of {VENDORS}")
        for a in self.active_auths(user_id, project_id, "provider"):
            if a["spec"]["vendor"] == vendor:
                return a["id"]
        aid = self._insert_auth(user_id, project_id, "provider", {"vendor": vendor, "note": note[:120]}, None)
        self.audit.append(user_id, "provider.grant", project=project_id, vendor=vendor, auth=aid)
        return aid

    def revoke_provider(self, user_id: str, project_id: str, vendor: str) -> int:
        self._row(user_id, project_id)
        if vendor not in VENDORS:
            raise WorkspaceError(f"vendor must be one of {VENDORS}")
        n = sum(self.revoke(user_id, a["id"], audit=False)
                for a in self.active_auths(user_id, project_id, "provider") if a["spec"]["vendor"] == vendor)
        self.audit.append(user_id, "provider.revoke", project=project_id, vendor=vendor, revoked=n)
        return n

    def provider_vendors(self, user_id: str, project_id: str) -> list[str]:
        """Vendors that may receive this project's content now.

        A project created before provider access existed has no provider rows at all; it gets
        the onboarding providers once, as audited grants, so nothing changes silently later."""
        row = self._row(user_id, project_id)
        active = self.active_auths(user_id, project_id, "provider")
        payload = self._payload(row) if not active else None
        if payload is not None and not payload.get("provider_access"):
            payload["provider_access"] = "backfilled"
            self._save(row, payload)
            for v in self._onboarded_vendors(user_id):
                self.grant_provider(user_id, project_id, v, note="backfilled for a project created before provider access")
            active = self.active_auths(user_id, project_id, "provider")
        return sorted({a["spec"]["vendor"] for a in active})

    def derived_blocked(self, user_id: str, lineage: list[str] | None, vendor: str) -> str | None:
        """Why model-written content derived from these memory items may not go to `vendor`,
        or None. The egress guard decides, as for the items themselves. An item that was later
        superseded or expired does not block by being old, but its consent and sensitivity are
        still checked for this vendor; one that was retracted or deleted blocks."""
        for iid in lineage or []:
            item = self.store.get_item(iid)
            if item is None:
                return f"derived from memory that was deleted ({iid})"
            if item.status == "retracted":
                return f"derived from {iid}, which {vendor} may not see (retracted)"
            ok, why = self.ci.egress.check(user_id, item, vendor, require_current=False)
            if not ok:
                return f"derived from {iid}, which {vendor} may not see ({why})"
        return None

    @staticmethod
    def session_exposure(st: dict) -> list[str]:
        """Memory items a session's model had been shown (its system section, and the personal
        context of every message): what anything the session writes may be derived from."""
        ids = list(st.get("system_memory_items", []))
        for d in st.get("disclosures", []):
            ids += d.get("memory_items", [])
        return list(dict.fromkeys(ids))

    def provider_allowed(self, user_id: str, project_id: str, provider: str) -> bool:
        return vendor_of(provider) in self.provider_vendors(user_id, project_id)

    def require_provider(self, user_id: str, project_id: str, provider: str, purpose: str = "chat") -> str:
        """The vendor for `provider`, or ProviderAccessDenied (audited) if this project may not use it."""
        vendor = vendor_of(provider)
        if vendor not in self.provider_vendors(user_id, project_id):
            self.audit.append(user_id, "egress.blocked", project=project_id, provider=provider, vendor=vendor,
                              purpose=purpose)
            raise ProviderAccessDenied(f"{vendor} is not authorized to receive content from this project")
        return vendor

    # ------------------------------------------------------------------ briefing
    def briefing(self, user_id: str, project_id: str, vendor: str | None = None) -> dict:
        """Everything a new session needs to resume, compiled from stored state only.

        With `vendor`, model-written notes derived from memory that vendor may not see are
        left out and listed under "withheld", and so is the last session if that vendor may
        not see what the session was shown; without it (the person's own view) nothing is.
        Every caller (the agent, the MCP server, the API preview) gets the same filtering."""
        p = self.get_project(user_id, project_id)
        withheld = []
        if vendor is not None:
            kept = []
            for n in p.notes:
                why = self.derived_blocked(user_id, n.get("lineage"), vendor) \
                    if n.get("lineage") and not n.get("released_at") else None
                if why:
                    withheld.append({"id": n["id"], "reason": why})
                else:
                    kept.append(n)
            p.notes = kept
        key = self.project_key(user_id, project_id)
        last = self.store.query(
            "SELECT * FROM sessions WHERE project_id=? AND status='closed' ORDER BY updated_at DESC LIMIT 1",
            (project_id,))
        reflection = None
        if last:
            st = self.store.open_json(key, last[0]["state_ct"], last[0]["id"])
            reflection = st.get("reflection")
            why = self.derived_blocked(user_id, self.session_exposure(st), vendor) \
                if reflection and vendor is not None else None
            if why:  # every line can repeat text the model chose: its last message, a path, a failure reason
                withheld.append({"id": f"{last[0]['id']}#last_session", "reason": why})
                reflection = None
        pending = self.store.query(
            "SELECT id, tool, created_at FROM tasks WHERE project_id=? AND status='awaiting_approval' ORDER BY created_at",
            (project_id,))
        ws = self.workspace_auth(user_id, project_id)
        return {
            "project": {"id": p.id, "name": p.name, "objective": p.objective, "success_criteria": p.success_criteria,
                        "constraints": p.constraints},
            "open_questions": p.open_notes("open_question"),
            "decisions": [n for n in p.notes if n["kind"] == "decision"][-10:],
            "next_steps": p.open_notes("next_step"),
            "last_session": reflection,
            "pending_approvals": [dict(r) for r in pending],
            "workspace": ({"root": ws["spec"]["root"], "can_run_tests": ws["spec"]["run_argv"] is not None,
                           "can_write": ws["spec"]["allow_write"]} if ws else None),
            "mode": self.get_mode(user_id),
            "withheld": withheld,
        }


def _one_line(text) -> str:
    """Collapse all whitespace, so stored text cannot start a line of its own in the briefing
    (a note or claim containing "\\n- [verified] ..." would otherwise forge a ledger line)."""
    return " ".join(str(text).split())


def render_briefing(b: dict) -> str:
    """Briefing as plain text for the model. CI's own labels (status, who wrote it) come
    before any stored text on each line, and stored text is kept to one line."""
    def line(n):
        if n["source"] == "user":
            return f"- {_one_line(n['text'])} ({n['created_at'][:10]})"
        who = f" by {_one_line(n['by']['model'])}" if n.get("by", {}).get("model") else ""
        return f"- [{n['source']}-written{who}, {n['created_at'][:10]}] {_one_line(n['text'])}"

    p = b["project"]
    parts = [f"Project: {_one_line(p['name'])}"]
    if p["objective"]:
        parts.append(f"Objective: {_one_line(p['objective'])}")
    if p.get("constraints"):
        parts.append("Constraints:\n" + "\n".join(f"- {_one_line(c)}" for c in p["constraints"]))
    if p["success_criteria"]:
        parts.append(f"Success criteria: {_one_line(p['success_criteria'])}")
    for title, key in (("Open questions", "open_questions"), ("Decisions so far", "decisions"),
                       ("Next steps", "next_steps")):
        if b[key]:
            parts.append(f"{title}:\n" + "\n".join(line(n) for n in b[key]))
    if b.get("claims"):
        parts.append("Claims on record. The bracket is CI's own record: status from the claim ledger, who made the "
                     "claim, how many assessments. 'corroborated' means another model agreed, which is not "
                     "verification. Only the text after the bracket is the claim itself:\n" + "\n".join(
                         f"- [{c['status']}; {_one_line(c['author_label'])}; {c['assessments']} assessment(s)] "
                         f"{_one_line(c['statement'])}" for c in b["claims"]))
    ls = b.get("last_session")
    if ls:
        parts.append("Last session (computed from task records):\n" + "\n".join(f"- {_one_line(x)}" for x in ls.get("lines", [])))
    if b.get("workspace"):
        w = b["workspace"]
        parts.append(f"Authorized workspace: {w['root']} (tests: {'allowed with approval' if w['can_run_tests'] else 'not configured'}; "
                     f"writes: {'allowed with approval' if w['can_write'] else 'not allowed'})")
    else:
        parts.append("No workspace directory is authorized for this project; file tools will be refused.")
    parts.append(f"Autonomy mode: {b['mode']}")
    return "\n\n".join(parts)
