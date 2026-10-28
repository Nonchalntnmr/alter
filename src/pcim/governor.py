"""The governor: the backend authority over every tool call.

`decide()` returns execute | approve | deny for a proposed call. `recheck()` repeats the
checks at execution time, because an approval given a minute ago does not survive a halt,
a revoked workspace, or a symlink swapped in since. The UI only ever displays these
decisions; nothing it does can widen them.

Rules that hold in every mode (CI_ARCHITECTURE.md, "Governor rules"):
- paths resolve (symlinks included) inside the project's authorized root;
- secret-looking files are never read or written;
- `.git/` is never written; there is no git write operation at all;
- run_tests executes only the argv the person configured;
- external side effects (send, publish, spend) do not exist as tools, so they cannot be called.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import fnmatch
from pathlib import Path

from .types import utcnow
from .workspace import Workspace

RISK = {
    "read_file": "read", "list_files": "read", "search_code": "read", "git_status": "read",
    "record_note": "note", "record_claim": "note",
    "run_tests": "run", "write_file": "write",
}
PATH_TOOLS = {"read_file", "write_file"}
SECRET_NAMES = (".env", ".env.*", "*.key", "*.pem", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
                "*credentials*", "*secret*", ".netrc", ".pypirc", ".npmrc", "*.db", "*.sqlite", "*.kdbx",
                "master.key", "token")
SECRET_DIRS = {".ssh", ".aws", ".gnupg", ".gcloud", ".azure", ".kube", ".docker", ".pcim"}


class PathError(ValueError):
    pass


@dataclasses.dataclass
class Decision:
    action: str  # execute | approve | deny
    reason: str
    authorization_id: str | None = None


def is_secret(path: Path) -> bool:
    # Case-folded: on a case-insensitive filesystem (macOS by default) .SSH is .ssh.
    return any(fnmatch.fnmatch(path.name.lower(), pat) for pat in SECRET_NAMES) or any(
        part.lower() in SECRET_DIRS for part in path.parts)


def jail(root: str | Path, rel: str, for_write: bool = False) -> Path:
    """Resolve `rel` inside `root` or raise. Rejects traversal, symlink escape, secrets, .git writes."""
    if not isinstance(rel, str) or not rel or "\x00" in rel:
        raise PathError("path must be a non-empty string")
    root_r = Path(root).resolve()
    p = (root_r / rel).resolve()
    if p != root_r and root_r not in p.parents:
        raise PathError(f"{rel} is outside the authorized workspace")
    rel_parts = p.relative_to(root_r).parts
    if is_secret(p.relative_to(root_r)) if rel_parts else False:
        raise PathError(f"{rel} looks like a secret; CI never reads or writes it")
    if for_write and any(part.lower() == ".git" for part in rel_parts):  # .GIT/config is .git/config on macOS
        raise PathError("writing inside .git is not allowed")
    return p


class Governor:
    def __init__(self, ws: Workspace):
        self.ws = ws

    def _base_checks(self, user_id: str, project_id: str, tool: str, args: dict) -> tuple[Decision | None, dict | None]:
        if self.ws.is_halted(user_id):
            return Decision("deny", "halted: execution is stopped until you resume"), None
        if tool not in RISK:
            return Decision("deny", f"unknown tool {tool!r}"), None
        risk = RISK[tool]
        if risk == "note":
            return None, None
        ws = self.ws.workspace_auth(user_id, project_id)
        if ws is None:
            return Decision("deny", "no workspace directory is authorized for this project"), None
        spec = ws["spec"]
        if tool in PATH_TOOLS:
            try:
                jail(spec["root"], args.get("path", ""), for_write=(tool == "write_file"))
            except PathError as e:
                return Decision("deny", str(e)), ws
        if tool == "run_tests" and not spec.get("run_argv"):
            return Decision("deny", "no test command is configured for this workspace"), ws
        if tool == "write_file" and not spec.get("allow_write"):
            return Decision("deny", "writes are not allowed in this workspace"), ws
        # git_status additionally refuses a home-directory repository at run time (tools.py).
        return None, ws

    def _autonomous_cover(self, user_id: str, project_id: str, tool: str) -> tuple[str | None, str]:
        now = utcnow()
        for a in self.ws.active_auths(user_id, project_id, "autonomous"):
            s = a["spec"]
            if tool not in s["tools"]:
                continue
            if a["used_actions"] >= s["max_actions"]:
                return None, f"autonomous grant {a['id']} has used all {s['max_actions']} actions"
            if a["first_used_at"] and (now - dt.datetime.fromisoformat(a["first_used_at"])).total_seconds() > s["max_seconds"]:
                return None, f"autonomous grant {a['id']} has run past its {s['max_seconds']}s limit"
            return a["id"], "covered by autonomous grant"
        return None, "no active autonomous grant covers this tool"

    def decide(self, user_id: str, project_id: str, tool: str, args: dict) -> Decision:
        d, _ = self._base_checks(user_id, project_id, tool, args)
        if d:
            return d
        risk = RISK[tool]
        if risk in ("read", "note"):
            return Decision("execute", f"{risk} tools run without approval in every mode")
        mode = self.ws.get_mode(user_id)
        if mode == "autonomous":
            aid, why = self._autonomous_cover(user_id, project_id, tool)
            if aid:
                return Decision("execute", why, aid)
            return Decision("approve", f"{why}; needs your approval")
        return Decision("approve", f"{tool} {'runs code' if risk == 'run' else 'changes files'}; "
                                   f"{mode} mode needs your approval")

    def recheck(self, user_id: str, project_id: str, tool: str, args: dict, authorization_id: str | None) -> Decision:
        """At execution time: everything except the approval itself is checked again."""
        d, _ = self._base_checks(user_id, project_id, tool, args)
        if d:
            return d
        if authorization_id:  # an autonomous execution must still be covered now
            aid, why = self._autonomous_cover(user_id, project_id, tool)
            if aid != authorization_id:
                return Decision("deny", f"autonomous grant no longer covers this call: {why}")
        return Decision("execute", "rechecked")
