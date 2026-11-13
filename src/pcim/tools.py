"""Typed tools the executor runs. Each takes validated arguments and the workspace spec.

Nothing here decides whether a call is allowed. That is the governor's job, and it is
asked twice, at proposal time and again right before execution. These functions only
make sure a call, once allowed, cannot do more than it says:
- every path goes through `jail()`;
- output is capped;
- run_tests uses the configured argv with no shell, a timeout, its own process group (so
  cancel kills the children too), and an environment with keys and tokens removed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

from .governor import is_secret, jail

MAX_READ = 200_000
MAX_WRITE = 500_000
MAX_LIST = 500
MAX_MATCHES = 200
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", "dist", "build"}
_SCRUB = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION)", re.IGNORECASE)
_SCRUB_PREFIX = ("AWS_", "GITHUB_", "GH_", "ANTHROPIC", "OPENAI", "GEMINI", "GOOGLE_", "AZURE_", "PCIM_", "HF_")

TOOL_SCHEMAS = {
    "read_file": {"description": "Read a text file inside the authorized project workspace.",
                  "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "path relative to the workspace root"}},
                                   "required": ["path"], "additionalProperties": False}},
    "list_files": {"description": "List files in the workspace matching a glob (default **/*). Skips .git, virtualenvs, node_modules.",
                   "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}},
                                    "required": ["pattern"], "additionalProperties": False}},
    "search_code": {"description": "Search workspace files for a regular expression. Returns path:line: text.",
                    "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}, "glob": {"type": "string"}},
                                     "required": ["pattern", "glob"], "additionalProperties": False}},
    "git_status": {"description": "Show git status of the workspace (read-only).",
                   "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    "run_tests": {"description": "Run the person's configured test command in the workspace. Needs approval unless an autonomous grant covers it.",
                  "input_schema": {"type": "object", "properties": {"reason": {"type": "string", "description": "one line: why run tests now"}},
                                   "required": ["reason"], "additionalProperties": False}},
    "write_file": {"description": "Create or overwrite a text file in the workspace. Needs approval unless an autonomous grant covers it. The previous version is kept for undo.",
                   "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"},
                                                                     "reason": {"type": "string"}},
                                    "required": ["path", "content", "reason"], "additionalProperties": False}},
    "record_note": {"description": "Record an open question, decision, or next step on the project. It is shown to the person as model-written.",
                    "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["open_question", "decision", "next_step"]},
                                                                      "text": {"type": "string"}},
                                     "required": ["kind", "text"], "additionalProperties": False}},
    "record_claim": {"description": "Put one checkable claim you are making on the project's claim ledger, so it can be verified independently. "
                                    "It stays unverified until a test or the person settles it; another model agreeing does not verify it.",
                     "input_schema": {"type": "object", "properties": {
                         "statement": {"type": "string", "description": "one specific, checkable claim"},
                         "evidence": {"type": "string", "description": "ids of tasks (task_...), notes (note_...) or claims (clm_...) from this project that support it, comma-separated; empty if none"},
                         "uncertainty": {"type": "string", "description": "what you are unsure about; empty if nothing"}},
                         "required": ["statement", "evidence", "uncertainty"], "additionalProperties": False}},
}


class ToolError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


def validate_args(tool: str, args: dict) -> dict:
    schema = TOOL_SCHEMAS.get(tool)
    if schema is None:
        raise ToolError(f"unknown tool {tool}")
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    props, required = schema["input_schema"]["properties"], schema["input_schema"]["required"]
    extra = set(args) - set(props)
    missing = [k for k in required if k not in args]
    if extra or missing:
        raise ToolError(f"bad arguments for {tool}: extra={sorted(extra)} missing={missing}")
    for k, v in args.items():
        if not isinstance(v, str):
            raise ToolError(f"{tool}.{k} must be a string")
        if "enum" in props[k] and v not in props[k]["enum"]:
            raise ToolError(f"{tool}.{k} must be one of {props[k]['enum']}")
    return args


def scrubbed_env() -> dict:
    # GIT_CONFIG_KEY_n matches KEY, so drop the whole GIT_CONFIG_* family together: a leftover
    # GIT_CONFIG_COUNT without its keys makes every git call fail ("missing config key").
    return {k: v for k, v in os.environ.items()
            if not _SCRUB.search(k) and not k.startswith(_SCRUB_PREFIX) and not k.startswith("GIT_CONFIG_")}


def _iter_files(root: Path, pattern: str):
    for p in root.glob(pattern or "**/*"):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or not p.is_file() or is_secret(rel):
            continue
        try:  # glob follows symlinks; keep only files that really live inside the root
            p.resolve().relative_to(root)
        except ValueError:
            continue
        yield p, rel


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _search(root: Path, pattern: str, glob: str, exclude=()) -> dict:
    """`exclude`: resolved paths to skip. "files" lists the resolved paths that matched, so the
    caller knows whose content the matches carry (tasks.TaskQueue._read)."""
    rx = re.compile(pattern)
    matches, files, exclude = [], {}, set(exclude)
    for p, rel in _iter_files(root, glob):
        real = str(p.resolve())
        if real in exclude or p.stat().st_size > 1_000_000:
            continue
        try:
            text = p.read_text(errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line[:2000]):
                matches.append(f"{rel}:{n}: {line.strip()[:300]}")
                files[real] = None
                if len(matches) >= MAX_MATCHES:
                    return {"matches": matches, "truncated": True, "files": list(files)}
    return {"matches": matches, "truncated": False, "files": list(files)}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    """git with nothing from the repository's config that runs a command. The repository may
    have arrived with its .git (an archive, a shared folder) or a model may have written to
    it, and git status would otherwise start core.fsmonitor, the post-index-change hook, and
    the clean filter of every file whose stat data changed. Filter drivers have names of the
    repository's choosing, so each one configured is disabled by name; reading config runs
    nothing."""
    env = scrubbed_env()
    keys = subprocess.run(["git", "-C", str(root), "config", "--null", "--name-only", "--get-regexp", r"^filter\."],
                          capture_output=True, text=True, timeout=15, env=env).stdout.split("\0")
    drivers = dict.fromkeys(k[len("filter."):k.rindex(".")] for k in keys if k.count(".") >= 2)
    over = [("core.fsmonitor", "false"), ("core.hooksPath", "/dev/null")]
    for d in drivers:
        over += [(f"filter.{d}.clean", ""), (f"filter.{d}.smudge", ""), (f"filter.{d}.process", ""),
                 (f"filter.{d}.required", "false")]
    env["GIT_CONFIG_COUNT"] = str(len(over))  # unlike -c, a key here cannot be split by an "=" in a driver name
    for n, (k, v) in enumerate(over):
        env[f"GIT_CONFIG_KEY_{n}"], env[f"GIT_CONFIG_VALUE_{n}"] = k, v
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=15, env=env)


def _search_in_child(root: Path, pattern: str, glob: str, timeout_s: float, exclude=()) -> dict:
    """The model writes the regex, and Python's re has no timeout: a pattern like (a+)+$ can
    backtrack for hours. The search runs in a child process that is killed at the deadline."""
    try:
        r = subprocess.run([sys.executable, "-m", "pcim.tools"], input=json.dumps(
            {"root": str(root), "pattern": pattern, "glob": glob, "exclude": sorted(exclude)}), capture_output=True,
            text=True, timeout=timeout_s, env=scrubbed_env())
    except subprocess.TimeoutExpired:
        raise ToolError(f"search took longer than {timeout_s:g}s and was stopped; simplify the pattern")
    if r.returncode != 0:
        raise ToolError(f"search failed: {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout)


class Executor:
    def __init__(self, search_timeout_s: float = 10.0):
        self._procs: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()
        self.search_timeout_s = search_timeout_s

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            self._cancelled.add(task_id)
            proc = self._procs.get(task_id)
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                time.sleep(0.2)
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return True
        return False

    def run(self, task_id: str, tool: str, args: dict, spec: dict, exclude=()) -> dict:
        """`exclude`: resolved paths search_code must skip (files this caller may not read)."""
        root = Path(spec["root"]).resolve()
        if tool == "read_file":
            p = jail(root, args["path"])
            if not p.is_file():
                raise ToolError(f"{args['path']} does not exist")
            data = p.read_bytes()
            if b"\x00" in data[:8192]:
                raise ToolError(f"{args['path']} is binary")
            return {"path": args["path"], "bytes": len(data), "truncated": len(data) > MAX_READ,
                    "content": data[:MAX_READ].decode("utf-8", errors="replace")}
        if tool == "list_files":
            files = []
            for _, rel in _iter_files(root, args.get("pattern") or "**/*"):
                files.append(str(rel))
                if len(files) >= MAX_LIST:
                    break
            return {"files": sorted(files), "truncated": len(files) >= MAX_LIST}
        if tool == "search_code":
            if len(args["pattern"]) > 200:
                raise ToolError("pattern too long")
            try:
                re.compile(args["pattern"])
            except re.error as e:
                raise ToolError(f"bad regex: {e}")
            return _search_in_child(root, args["pattern"], args.get("glob") or "**/*", self.search_timeout_s, exclude)
        if tool == "git_status":
            # A folder with no repository of its own makes git walk up to an enclosing one. On a
            # machine where $HOME is itself a repository that would mean listing (and leaking)
            # the whole home directory, so the real top level is checked first.
            top = _git(root, "rev-parse", "--show-toplevel")
            if top.returncode != 0:
                raise ToolError("not a git repository")
            toplevel = Path(top.stdout.strip()).resolve()
            if toplevel == Path.home().resolve() or toplevel in Path.home().resolve().parents:
                raise ToolError("refusing git in the home-directory repository")
            # dirty: otherwise git runs inside each submodule, under that submodule's own config
            r = _git(root, "status", "--porcelain=v1", "-b", "--ignore-submodules=dirty")
            if r.returncode != 0:
                raise ToolError(r.stderr.strip()[:500] or "not a git repository")
            return {"status": r.stdout[:20_000]}
        if tool == "write_file":
            content = args["content"]
            if len(content.encode()) > MAX_WRITE:
                raise ToolError("content too large")
            p = jail(root, args["path"], for_write=True)
            before = p.read_bytes() if p.exists() else None
            p.parent.mkdir(parents=True, exist_ok=True)
            # A new temp file, never an existing name: a fixed name could already be in the
            # workspace as a symlink (a cloned repo can carry one) and take the write outside it.
            tmp = p.with_name(f".{p.name}.{secrets.token_hex(6)}.ci-tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(content.encode())
                if before is not None:  # an overwritten file keeps its permissions (a script stays executable)
                    os.chmod(tmp, stat.S_IMODE(p.stat().st_mode) & 0o777)
                os.replace(tmp, p)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            return {"path": args["path"], "abs_path": str(p), "created": before is None, "bytes": len(content.encode()),
                    "sha256_before": _sha(before) if before is not None else None,
                    "sha256_after": _sha(content.encode()),
                    "backup": before.decode("utf-8", errors="replace") if before is not None else None}
        if tool == "run_tests":
            argv, timeout = spec["run_argv"], int(spec.get("run_timeout_s", 300))
            t0 = time.time()
            proc = subprocess.Popen(argv, cwd=root, env=scrubbed_env(), stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, start_new_session=True)
            with self._lock:
                self._procs[task_id] = proc
                cancelled_early = task_id in self._cancelled
            if cancelled_early:  # cancel arrived between "running" and Popen; outside the lock
                self.cancel(task_id)
            try:
                out, err = proc.communicate(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                self.cancel(task_id)
                out, err = proc.communicate()
                timed_out = True
            finally:
                with self._lock:
                    self._procs.pop(task_id, None)
            if task_id in self._cancelled and not timed_out:
                raise Cancelled("cancelled while running")
            return {"argv": argv, "exit_code": proc.returncode, "timed_out": timed_out,
                    "duration_s": round(time.time() - t0, 2), "stdout_tail": out[-6000:], "stderr_tail": err[-3000:]}
        raise ToolError(f"{tool} is not executed by the executor")


if __name__ == "__main__":  # the search child (see _search_in_child)
    req = json.load(sys.stdin)
    json.dump(_search(Path(req["root"]), req["pattern"], req["glob"], req.get("exclude", ())), sys.stdout)
