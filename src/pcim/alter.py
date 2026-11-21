"""`alter`: ALTER's terminal. ALPHA: works end to end, not yet validated by a human evaluation.

    alter                                  talk to ALTER (in a project folder: goals; elsewhere: questions)
    alter "finish the next version"        a goal for this project folder, worked on now; Ctrl-C pauses it
    alter ask "what changed this month?"   a question answered from cited context (one model call, no tools)
    alter init [--name N] [--tests "CMD"] [--no-write] [--no-memory]
                                           make this folder a project ALTER may work in
    alter status                           who, where, what is running, what waits for you
    alter context ["topic"]                what ALTER can see here, and where each piece came from (no model call)
    alter goals [list | show ID | start "…" [--background] | resume ID [--background]
                 | say ID "…" | pause ID | cancel ID | forget ID]
    alter approve [TASK] · alter deny [TASK] ["why"]
                                           resolve what a goal waits on (no TASK: everything it waits on)
    alter tasks                            actions waiting for you, and the last actions in this project
    alter memory [forget ID | remember "…"]
    alter permissions [vault on [--path P] | vault off | github on|off | memory on|off
                       | autonomy grant [--actions N] [--minutes M] | autonomy revoke]
    alter vault search "…" | eval [FILE] | download
    alter weekly · models · twin · mesh · audit · doctor · halt · resume · plan · do · …
                                           every `ci` command works here too (`ci` itself still works)

ALTER works through the same governed agent as `ci do`, which is the direct-execution baseline the
evaluations measured. Writes and test runs need your approval unless you grant autonomy. A goal is
done only when the records show the project's tests passing after the last change.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shlex
import shutil
import sys
import threading
import time
from pathlib import Path

from . import __version__
from . import goals as G
from . import terminal as T
from .textsafe import terminal_safe

# `ci` commands that `alter` passes through unchanged (`alter ask` is ALTER's own: cited context)
CI_COMMANDS = frozenset({"route", "mode", "prefer", "models", "outcome", "practice", "skills", "scorecard", "challenge",
                         "plan", "mesh", "twin", "audit", "learn", "execute", "halt", "resume", "do"})
CONTROL = {
    "pause": re.compile(r"^(pause|stop|hold on|wait)( (it|now|please))?[.!]*$", re.I),
    "status": re.compile(r"^(status|progress|what have you (finished|done)( so far)?|where are we|"
                         r"how('s| is) it going)[?.!]*$", re.I),
    "resume": re.compile(r"^(resume|continue|go on|keep going|carry on)[.!]*$", re.I),
    "cancel": re.compile(r"^(cancel|abort)( (it|the goal))?[.!]*$", re.I),
    "approve": re.compile(r"^(yes|approve|ok(ay)?|go ahead|do it)[.!]*$", re.I),
    "deny": re.compile(r"^(no|deny|don'?t)[.!]*$", re.I),
}


def control(text: str) -> str | None:
    t = " ".join(text.split())
    return next((k for k, rx in CONTROL.items() if rx.match(t)), None)


# --------------------------------------------------------------------------- helpers
def _ctx():
    ci, uid = T._open()
    try:
        here = T._here(ci, uid)
    except T.AmbiguousWorkspace as e:
        print(f"alter: {e}", file=sys.stderr)
        raise SystemExit(2) from None
    return ci, uid, here


def _need_here(here) -> bool:
    if here is None:
        print("This folder is not a project ALTER may work in. Run `alter init` here first "
              "(or cd into a project folder).", file=sys.stderr)
        return False
    return True


def _latest_active(ci, uid, here) -> G.Goal | None:
    for g in G.GoalStore(ci).list(uid, here.project_id, limit=20):
        if g.status in G.ACTIVE:
            return g
    return None


def _goal(ci, uid, ref: str, project_id: str | None = None) -> G.Goal:
    gs = G.GoalStore(ci)
    try:
        return gs.get(uid, gs.resolve(uid, ref, project_id))
    except KeyError:
        raise SystemExit(f"alter: no goal {ref}") from None
    except ValueError as e:
        raise SystemExit(f"alter: {e}") from None


def _limits(a) -> dict:
    return {"max_turns": a.turns, "max_cost_usd": a.budget, "window_s": a.minutes * 60 if a.minutes else None}


# --------------------------------------------------------------------------- init
def _detect_tests(root: Path) -> list[str] | None:
    venv_py = root / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else (shutil.which("python3") or sys.executable)
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() or (root / "tests").is_dir():
        return [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    pkg = root / "package.json"
    if pkg.exists():
        with contextlib.suppress(ValueError, OSError):
            if (json.loads(pkg.read_text()).get("scripts") or {}).get("test"):
                return [shutil.which("npm") or "npm", "test", "--silent"]
    return None


def cmd_init(argv: list[str]) -> int:
    from .workspace import Workspace, WorkspaceError
    ap = argparse.ArgumentParser(prog="alter init", description="Make this folder a project ALTER may work in.")
    ap.add_argument("--name")
    ap.add_argument("--tests", help='the test command, e.g. "python -m pytest -q" (default: detected)')
    ap.add_argument("--no-write", action="store_true", help="ALTER may read and run tests, never write")
    ap.add_argument("--no-memory", action="store_true", help="do not store goal outcomes in memory")
    a = ap.parse_args(argv)
    ci, uid = T._open()
    root = Path.cwd().resolve()
    here = T._here(ci, uid, root)
    ws = Workspace(ci)
    if here is not None and Path(here.root).resolve() == root:
        print(f"Already a project: {here.name} ({root}). `alter permissions` shows what ALTER may do here.")
        return 0
    run_argv = shlex.split(a.tests) if a.tests else _detect_tests(root)
    try:
        p = ws.create_project(uid, a.name or root.name)
        ws.authorize_workspace(uid, p.id, str(root), run_argv=run_argv, allow_write=not a.no_write)
    except WorkspaceError as e:
        print(f"alter init: {e}", file=sys.stderr)
        return 2
    if not a.no_memory:
        G.grant_outcome_memory(ci, uid)
    print(f"Project {p.name} ({p.id}) at {root}")
    print(f"  ALTER may read files here; {'write files and ' if not a.no_write else ''}run "
          f"{' '.join(run_argv) if run_argv else 'no test command (pass --tests)'}; each write and test run asks you "
          "first unless you grant autonomy.")
    print(f"  model providers that may see this project: {', '.join(ws.provider_vendors(uid, p.id)) or 'none'}")
    print("  goal outcomes are " + ("not stored" if a.no_memory else "stored in your memory (alter memory; forget any)"))
    print("Next: alter context   ·   alter \"what should we do next?\"   ·   alter permissions")
    return 0


# --------------------------------------------------------------------------- status, context, tasks
def cmd_status(argv: list[str]) -> int:
    from . import thirdeye
    from .workspace import Workspace
    ci, uid, here = _ctx()
    ws = Workspace(ci)
    gs = G.GoalStore(ci)
    print(f"ALTER {__version__} (alpha) · user {uid} · data {T._home()}")
    print(f"  mode {ws.get_mode(uid)} · Stop-all {'ON (alter resume)' if ws.is_halted(uid) else 'off'}")
    s = thirdeye.settings(ws, uid)
    print(f"  sources: repo on · github {'on' if s['github'] else 'off'} · vault {'on' if s['vault'] else 'off'}"
          f" ({s['vault_path']})")
    print(f"  goal outcomes to memory: {'on' if G.has_outcome_grant(ci, uid) else 'off'}")
    if here is None:
        print("  here: not a project folder (alter init)")
        goals = gs.list(uid, limit=10)
    else:
        a = ws.workspace_auth(uid, here.project_id)["spec"]
        print(f"  here: {here.name} · {here.root}")
        print(f"    write {'yes' if a.get('allow_write') else 'no'} · tests {' '.join(a['run_argv']) if a.get('run_argv') else 'none'}"
              f" · providers {', '.join(sorted(here.vendors)) or 'none'}")
        goals = gs.list(uid, here.project_id, limit=10)
    active = [g for g in goals if g.status in G.ACTIVE]
    print(f"  goals: {len(active)} active, {len(goals) - len(active)} recent finished")
    for g in active:
        print(terminal_safe(f"    {g.id}  {gs.shown_status(g):17} {g.objective[:70]}"))
        for t in G.pending_tasks(ci, uid, g):
            print(terminal_safe(f"      waiting for you: {t['id']} {t['tool']} {t['what']}  (alter approve / alter deny)"))
    return 0


def cmd_context(argv: list[str]) -> int:
    from . import thirdeye
    ci, uid, here = _ctx()
    if not _need_here(here):
        return 2
    print(terminal_safe(thirdeye.gather(ci, uid, here, " ".join(argv) or here.name).for_terminal()))
    return 0


def cmd_tasks(argv: list[str]) -> int:
    from .governor import Governor
    from .tasks import TaskQueue
    from .workspace import Workspace
    ci, uid, here = _ctx()
    ws = Workspace(ci)
    q = TaskQueue(ws, Governor(ws))
    waiting = q.list(uid, None, status="awaiting_approval")
    print(f"Waiting for you: {len(waiting)}")
    for t in waiting:
        d = G._task_line(t.public())
        print(terminal_safe(f"  {d['id']}  {d['tool']} {d['what']}"))
    if here is not None:
        print(f"\nLast actions in {here.name}:")
        for t in q.list(uid, here.project_id, limit=15):
            d = G._task_line(t.public())
            print(terminal_safe(f"  {d['id']}  {d['tool']:11} {d['status']:17} {d['what'][:60]}"))
    return 0


# --------------------------------------------------------------------------- goals
def _run_foreground(ci, uid, here, g: G.Goal, window_s: int | None = None) -> int:
    r = G.GoalRunner(ci, uid, here)
    try:
        g = r.run(g.id, window_s)
    except G.GoalBusy as e:
        print(f"alter: {e}. Watch it with: alter goals show {g.id}", file=sys.stderr)
        return 3
    print()
    print(G.report(ci, uid, g))
    return _exit_for(g)


def _exit_for(g: G.Goal) -> int:
    return {"done": 0, "waiting_approval": 6, "waiting_answer": 6, "paused": 5}.get(g.status, 1)


def start_goal(ci, uid, here, objective: str, *, background: bool = False, limits: dict | None = None,
               quiet: bool = False) -> tuple[G.Goal, int]:
    r = G.GoalRunner(ci, uid, here, out=(lambda s: None) if quiet else None)
    g = r.start(objective, **(limits or {}))
    c = g.payload["context"]
    if not quiet:
        print(f"ALTER: goal {g.id} · context: {c['remembered']} remembered, {c['sourced']} sourced"
              + (f" · not included: {'; '.join(c['withheld'])}" if c["withheld"] else ""))
    if background:
        pid = G.spawn_worker(ci, g.id, here.root, g.payload["window_s"])
        if not quiet:
            print(f"ALTER: working in the background (pid {pid}, up to {g.payload['window_s'] // 60} min). "
                  f"alter goals show {g.id} · alter say {g.id} \"…\" · alter pause {g.id}")
        return g, 0
    return g, _run_foreground(ci, uid, here, g)


def cmd_goals(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="alter goals")
    ap.add_argument("action", nargs="?", default="list",
                    choices=["list", "show", "start", "resume", "say", "pause", "cancel", "forget"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--background", action="store_true")
    ap.add_argument("--all", action="store_true", help="list goals of every project")
    ap.add_argument("--turns", type=int)
    ap.add_argument("--budget", type=float, help="cost limit in USD")
    ap.add_argument("--minutes", type=int, help="time window")
    a = ap.parse_args(argv)
    ci, uid, here = _ctx()
    gs = G.GoalStore(ci)
    pid = here.project_id if here and not a.all else None
    if a.action == "list":
        gl = gs.list(uid, pid, limit=30)
        if not gl:
            print("No goals yet." + ("" if here else " (Not in a project folder: showing every project.)"))
        for g in gl:
            v = (g.payload.get("verification") or {}).get("verdict") or ""
            print(terminal_safe(f"{g.id}  {gs.shown_status(g):17} {v:12} {g.created_at[:10]}  {g.objective[:60]}"))
        return 0
    if a.action == "start":
        if not _need_here(here):
            return 2
        return start_goal(ci, uid, here, " ".join(a.rest), background=a.background, limits=_limits(a))[1]
    if not a.rest:
        print(f"usage: alter goals {a.action} <goal>" + (' "…"' if a.action == "say" else ""), file=sys.stderr)
        return 2
    g = _goal(ci, uid, a.rest[0], pid)
    if a.action == "show":
        print(G.report(ci, uid, g, gs))
        return 0
    if a.action == "forget":
        gs.delete(uid, g.id)
        print(f"Forgot goal {g.id} and its events. Kept: the record of each action (alter tasks, ci audit), and its "
              "outcome in memory if one was stored (alter memory forget <id>).")
        return 0
    if a.action in ("say", "pause", "cancel"):
        return _event(ci, uid, g, {"say": "steer"}.get(a.action, a.action), " ".join(a.rest[1:]))
    # resume
    h = G.here_for(ci, uid, g.project_id)
    if g.status in G.FINAL:
        print(f"Goal {g.id} is {g.status}; start a new goal to go further.", file=sys.stderr)
        return 2
    if gs.is_live(g.id):
        print(f"Goal {g.id} is already running. alter goals show {g.id}")
        return 0
    if a.background:
        p = G.spawn_worker(ci, g.id, h.root, (a.minutes or 0) * 60 or g.payload["window_s"])
        print(f"ALTER: resumed in the background (pid {p}).")
        return 0
    return _run_foreground(ci, uid, h, g, (a.minutes or 0) * 60 or None)


def _event(ci, uid, g: G.Goal, kind: str, text: str = "") -> int:
    gs = G.GoalStore(ci)
    if kind == "steer" and not text.strip():
        print('usage: alter say <goal> "what to change"', file=sys.stderr)
        return 2
    try:
        gs.add_event(uid, g.id, kind, text)
    except G.GoalError as e:
        print(f"alter: {e}", file=sys.stderr)
        return 2
    live = gs.is_live(g.id)
    when = "at its next step" if live else "when the goal resumes (alter goals resume " + g.id + ")"
    print({"steer": f"Noted. ALTER will take it into account {when}.",
           "pause": f"ALTER will pause {when if live else 'at once: it is not running'}.",
           "cancel": f"ALTER will cancel the goal {when}."}[kind])
    if not live and kind in ("pause", "cancel"):  # nothing running reads the event: apply it now
        with contextlib.suppress(G.GoalBusy):
            G.GoalRunner(ci, uid, G.here_for(ci, uid, g.project_id), out=lambda s: None).run(g.id)
    return 0


def cmd_event(kind: str, argv: list[str]) -> int:
    ci, uid, here = _ctx()
    if argv and not (here is not None and kind == "steer" and len(argv) == 1):
        g = _goal(ci, uid, argv[0], here.project_id if here else None)
        argv = argv[1:]
    else:
        g = _latest_active(ci, uid, here) if here else None
        if g is None:
            print(f"alter: no active goal here; name one: alter {'say' if kind == 'steer' else kind} <goal>",
                  file=sys.stderr)
            return 2
    return _event(ci, uid, g, kind, " ".join(argv))


def cmd_resolve(argv: list[str], approve: bool) -> int:
    ci, uid, here = _ctx()
    gs = G.GoalStore(ci)
    reason = ""
    if argv and argv[0].startswith("task_"):
        task = argv[0]
        reason = " ".join(argv[1:])
        owner = next((g for g in gs.list(uid, None, limit=200)
                      if g.status == "waiting_approval" and any(t["id"] == task for t in G.pending_tasks(ci, uid, g))), None)
        if owner is None:  # not a goal's action: `ci approve` handles it as before
            return T.cmd_approve(argv, approve=approve)
        g = owner
    else:
        reason = " ".join(argv)
        cands = [g for g in gs.list(uid, here.project_id if here else None, limit=50) if g.status == "waiting_approval"]
        if not cands:
            print("Nothing waits for your approval." + ("" if here else " (Not in a project folder.)"))
            return 0
        g = cands[0]
    pend = G.pending_tasks(ci, uid, g)
    print(f"{'Approving' if approve else 'Refusing'} for goal {g.id}:")
    for t in pend:
        print(terminal_safe(f"  {t['id']}  {t['tool']} {t['what']}"))
    h = G.here_for(ci, uid, g.project_id)
    try:
        g = G.GoalRunner(ci, uid, h).resolve_pending(g.id, approve, reason)
    except (G.GoalError, ValueError, RuntimeError) as e:
        print(f"alter: {e}", file=sys.stderr)
        return 2
    print()
    print(G.report(ci, uid, g))
    return _exit_for(g)


# --------------------------------------------------------------------------- ask (one model call, cited)
ASK = """Answer the person's question from the context below, and from nothing else.
Mark every statement with where it comes from: [remembered: label] for ALTER's own records, [sourced: label] for something read just now, or [recommendation] for your own suggestion.
If the context does not answer part of the question, say so plainly instead of guessing.

{context}

Question: {q}"""


def cmd_ask(argv: list[str]) -> int:
    from . import thirdeye
    from .engine import RoutingError
    if not argv:
        print('usage: alter ask "question"', file=sys.stderr)
        return 2
    ci, uid, here = _ctx()
    q = " ".join(argv)
    if here is not None:
        brief = thirdeye.gather(ci, uid, here, q, history=60)
    else:
        from .workspace import Workspace
        s = thirdeye.settings(Workspace(ci), uid)
        ev, wh = thirdeye._vault(s["vault_path"], q) if s["vault"] else ([], ["vault: off (alter permissions vault on)"])
        brief = thirdeye.Brief("(no project folder)", str(Path.cwd()), q, ev, wh)
    try:
        r = ci.ask_routed(uid, ASK.format(context=brief.for_model(), q=q),
                          project_id=here.project_id if here else None)
    except RoutingError as e:
        print(f"Not answered: {e}", file=sys.stderr)
        return 2
    ci.audit.append(uid, "alter.ask", project=here.project_id if here else None, pieces=len(brief.evidence),
                    sources=sorted({e.source for e in brief.evidence}))
    print(terminal_safe(r.text.rstrip()))
    print(f"─ context: {sum(e.kind == 'remembered' for e in brief.evidence)} remembered, "
          f"{sum(e.kind == 'sourced' for e in brief.evidence)} sourced"
          + (f" · not included: {'; '.join(brief.withheld)}" if brief.withheld else ""))
    return 0


# --------------------------------------------------------------------------- memory, permissions, vault
def cmd_memory(argv: list[str]) -> int:
    ci, uid = T._open()
    if argv[:1] == ["forget"] and len(argv) == 2:
        gone = ci.memory.delete(uid, argv[1])
        print(f"Deleted {len(gone)} item(s) (and anything derived from them). This cannot be undone.")
        return 0
    if argv[:1] == ["remember"] and len(argv) >= 2:
        r = ci.remember(uid, " ".join(argv[1:]), "fact")
        print(f"Remembered {r.item.id} (you stated it).")
        return 0
    if argv:
        print('usage: alter memory [forget ID | remember "…"]', file=sys.stderr)
        return 2
    items = ci.memory.current(uid)
    print(f"{len(items)} memory item(s). Each line: id, kind, trust, confidence, source, date.")
    for i in items:
        print(terminal_safe(f"{i.id}  {i.kind:10} {i.trust.value:14} {i.confidence:.2f}  {i.source:12} "
                            f"{str(getattr(i, 'recorded_at', '') or '')[:10]}  {i.content[:90]}"))
    return 0


def cmd_permissions(argv: list[str]) -> int:
    from . import thirdeye
    from .workspace import Workspace, WorkspaceError
    ci, uid, here = _ctx()
    ws = Workspace(ci)
    if argv[:1] in (["vault"], ["github"]) and len(argv) >= 2 and argv[1] in ("on", "off"):
        path = argv[argv.index("--path") + 1] if "--path" in argv else None
        try:
            s = thirdeye.set_source(ws, uid, argv[0], argv[1] == "on", path)
        except ValueError as e:
            print(f"alter: {e}", file=sys.stderr)
            return 2
        print(f"{argv[0]}: {'on' if s[argv[0]] else 'off'}" + (f" ({s['vault_path']})" if argv[0] == "vault" else ""))
        if argv[0] == "vault" and argv[1] == "on":
            print("  matching vault sections go to the model with each goal and question; `alter context` shows them")
        return 0
    if argv[:1] == ["memory"] and len(argv) == 2 and argv[1] in ("on", "off"):
        if argv[1] == "on":
            G.grant_outcome_memory(ci, uid)
        else:
            for gr in ci.ledger.grants(uid):
                if gr.source == G.MEMORY_SOURCE:
                    ci.ledger.revoke(uid, gr.id)
        print(f"goal outcomes to memory: {argv[1]}")
        return 0
    if argv[:1] == ["autonomy"]:
        if not _need_here(here):
            return 2
        ap = argparse.ArgumentParser(prog="alter permissions autonomy")
        ap.add_argument("action", choices=["grant", "revoke"])
        ap.add_argument("--actions", type=int, default=30)
        ap.add_argument("--minutes", type=int, default=60)
        a = ap.parse_args(argv[1:])
        if a.action == "revoke":
            n = sum(ws.revoke(uid, x["id"]) for x in ws.active_auths(uid, here.project_id, "autonomous"))
            ws.set_mode(uid, "collaborative")
            print(f"Revoked {n} autonomy grant(s); every write and test run asks you again.")
            return 0
        try:
            aid = ws.grant_autonomy(uid, here.project_id, ["write_file", "run_tests"], max_actions=a.actions,
                                    max_seconds=a.minutes * 60, ttl_minutes=a.minutes)
        except WorkspaceError as e:
            print(f"alter: {e}", file=sys.stderr)
            return 2
        ws.set_mode(uid, "autonomous")
        print(f"Autonomy {aid}: in {here.root} only, ALTER may write files and run the tests without asking, up to "
              f"{a.actions} actions within {a.minutes} minutes. Everything else still asks. Stop at once: alter halt. "
              "Undo: alter permissions autonomy revoke.")
        return 0
    if argv:
        print(__doc__.split("alter permissions", 1)[1].split("alter vault", 1)[0].strip(), file=sys.stderr)
        return 2
    s = thirdeye.settings(ws, uid)
    print(f"mode: {ws.get_mode(uid)} · Stop-all: {'ON' if ws.is_halted(uid) else 'off'}")
    print(f"sources: repo on · github {'on' if s['github'] else 'off'} · vault {'on' if s['vault'] else 'off'} "
          f"({s['vault_path']})")
    print(f"goal outcomes to memory: {'on' if G.has_outcome_grant(ci, uid) else 'off'}")
    projects = [ws.get_project(uid, here.project_id)] if here else ws.list_projects(uid)
    for p in projects:
        w = ws.workspace_auth(uid, p.id)
        print(f"\n{p.name} ({p.id})")
        if w:
            sp = w["spec"]
            print(f"  folder {sp['root']} · write {'yes' if sp.get('allow_write') else 'no'} · tests "
                  f"{' '.join(sp['run_argv']) if sp.get('run_argv') else 'none'}")
        print(f"  providers: {', '.join(ws.provider_vendors(uid, p.id)) or 'none'}")
        for x in ws.active_auths(uid, p.id, "autonomous"):
            sp = x["spec"]
            print(f"  autonomy {x['id']}: {', '.join(sp['tools'])}; {x['used_actions']}/{sp['max_actions']} actions; "
                  f"expires {str(x.get('expires_at'))[:16]}")
    return 0


def cmd_vault(argv: list[str]) -> int:
    from . import thirdeye
    from .vault import Vault, cite, dataless, evaluate
    from .workspace import Workspace
    ci, uid = T._open()
    s = thirdeye.settings(Workspace(ci), uid)
    root = Path(s["vault_path"]) if s["vault_path"] else None
    if root is None or not root.is_dir():
        print(f"No vault {'at ' + str(root) if root else 'found'}. alter permissions vault on --path <folder>",
              file=sys.stderr)
        return 2
    if argv[:1] == ["download"]:
        todo = [p for p in root.rglob("*.md") if ".obsidian" not in p.parts and dataless(p)]
        print(f"{len(todo)} note(s) are in iCloud only; reading them asks iCloud for them…")
        done = []

        def pull(p):
            with contextlib.suppress(OSError):
                p.read_bytes()
                done.append(p)
        threads = [threading.Thread(target=pull, args=(p,), daemon=True) for p in todo]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 120
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))
        left = sum(dataless(p) for p in todo)
        print(f"Downloaded {len(todo) - left}; {left} still in iCloud only." + (" Open the vault in Obsidian, or check "
              "that this app may use iCloud Drive." if left else ""))
        return 0 if not left else 1
    v = Vault(str(root))
    if v.not_downloaded:
        print(f"({len(v.not_downloaded)} note(s) are in iCloud only and were skipped: alter vault download)")
    if argv[:1] == ["search"] and len(argv) > 1:
        hits = v.search(" ".join(argv[1:]))
        if not hits:
            print("No matching notes. (Abstaining rather than guessing.)")
        for n, h in enumerate(hits, 1):
            print(terminal_safe(f"{n}. {cite(h)}  ({h['path']})\n   {h['snippet']}\n"))
        return 0
    if argv[:1] == ["eval"]:
        f = Path(argv[1]) if len(argv) > 1 else root / "13 Automation" / "Tools" / "eval.json"
        r = evaluate(v, json.loads(f.read_text()))
        print(f"{r['n']} questions · hit@1 {r['hit1']}/{r['n']} · hit@3 {r['hit3']}/{r['n']} · MRR {r['mrr']}  ({f.name})")
        for m in r["misses"]:
            print(terminal_safe(f"  miss: {m['q']!r} rank={m['rank']} top3={m['top3']}"))
        return 0
    print('usage: alter vault search "…" | eval [FILE] | download', file=sys.stderr)
    return 2


# --------------------------------------------------------------------------- weekly, doctor
def cmd_weekly(argv: list[str]) -> int:
    rc = T.cmd_weekly(argv)
    ci, uid = T._open()
    days = 7
    if "--days" in argv:
        with contextlib.suppress(IndexError, ValueError):
            days = int(argv[argv.index("--days") + 1])
    from .types import utcnow
    import datetime as dt
    since = (utcnow() - dt.timedelta(days=days)).isoformat()
    gl = [g for g in G.GoalStore(ci).list(uid, None, limit=500) if g.created_at >= since]
    by: dict[str, int] = {}
    for g in gl:
        by[g.status] = by.get(g.status, 0) + 1
    cost = sum(c for g in gl for c in g.payload["session_costs"].values() if c is not None)
    files = {f for g in gl for f in ((g.payload.get("verification") or {}).get("files") or [])}
    print(f"\nALTER goals, last {days} days: {len(gl)}"
          + (f" ({', '.join(f'{v} {k}' for k, v in sorted(by.items()))})" if gl else "")
          + f" · files changed {len(files)} · cost ${cost:.2f}. Done = tests passed after the last change.")
    print("  All goal work is AI-assisted; your own unassisted practice is `ci practice` / `ci skills`.")
    return rc


def cmd_doctor(argv: list[str]) -> int:
    from . import thirdeye
    from .vault import dataless
    from .workspace import Workspace
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = T.cmd_doctor(["--json"])
    try:
        checks = json.loads(buf.getvalue())["checks"]
    except (ValueError, KeyError):
        print(buf.getvalue())
        return rc

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})
    on_path = shutil.which("alter")
    add("alter_command", "ok" if on_path else "warn", on_path or "`alter` is not on PATH in this shell")
    add("git", "ok" if shutil.which("git") else "fail", shutil.which("git") or "git not found")
    gh = shutil.which("gh")
    add("github_cli", "ok" if gh else "warn", gh or "gh not found: GitHub context is unavailable")
    home_ok = (T._home() / "token").is_file()
    if home_ok:
        ci, uid = T._open()
        s = thirdeye.settings(Workspace(ci), uid)
        vp = Path(s["vault_path"]) if s["vault_path"] else None
        if not s["vault"]:
            add("vault", "ok", f"off (alter permissions vault on); " + (f"found {vp}" if vp else "none found"))
        elif vp is None or not vp.is_dir():
            add("vault", "fail", f"on, but no vault at {vp}")
        else:
            notes = [p for p in vp.rglob("*.md") if ".obsidian" not in p.parts]
            dl = sum(dataless(p) for p in notes)
            add("vault", "warn" if dl else "ok", f"{len(notes)} notes at {vp}" + (f"; {dl} in iCloud only (alter vault "
                                                                                   "download)" if dl else ""))
        gs = G.GoalStore(ci)
        stale = [g.id for g in gs.list(uid, None, limit=200) if gs.shown_status(g) == "interrupted"]
        add("goals", "warn" if stale else "ok",
            f"interrupted: {', '.join(stale)} (alter goals resume <id>)" if stale else "no interrupted goal")
        add("outcome_memory", "ok", "on" if G.has_outcome_grant(ci, uid) else "off (alter permissions memory on)")
    blocking = any(c["status"] == "fail" for c in checks)
    if "--json" in argv:
        print(json.dumps({"ok": not blocking, "checks": checks}, indent=1))
    else:
        print(f"ALTER {__version__} (alpha)")
        for c in checks:
            print(f"[{ {'ok': 'OK  ', 'warn': 'WARN', 'fail': 'FAIL'}[c['status']] }] {c['name']}: {c['detail']}")
    return 1 if blocking else 0


# --------------------------------------------------------------------------- conversation
HELP = """Type a goal or a question in plain words. While a goal runs you can keep talking:
  say anything      → added to the running goal at its next step
  "status" / "what have you finished?"  → what the records show (no model call)
  "pause" · "resume" · "cancel" · "yes" / "no" (approve or refuse what it waits on)
  /new <goal>  start another goal · /goals · /context · /status · /memory · /quit"""


class Conversation:
    """The `alter` session. Goals run in a background worker, so the conversation never blocks on one."""

    def __init__(self, ci, uid, here, out=print, poll_s: float = 1.0):
        self.ci, self.uid, self.here, self.out, self.poll_s = ci, uid, here, out, poll_s
        self.gs = G.GoalStore(ci)
        self.watching: str | None = None
        self._seen = 0
        self._last_status = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def active(self) -> G.Goal | None:
        return _latest_active(self.ci, self.uid, self.here) if self.here else None

    def watch(self, gid: str) -> None:
        with self._lock:
            self.watching, self._seen, self._last_status = gid, len(self.gs.get(self.uid, gid).payload["checkpoints"]), None

    def poll(self) -> None:
        """Print what a watched goal recorded since the last poll."""
        with self._lock:
            if not self.watching:
                return
            g = self.gs.get(self.uid, self.watching)
            for cp in g.payload["checkpoints"][self._seen:]:
                lines = [cp["text"].rstrip()] if cp["text"].strip() else []
                lines += [f"  · {t['tool']} {t['what']} → {t['status']}" for t in cp["tasks"]]
                self.out(terminal_safe("\n".join(lines)))
            self._seen = len(g.payload["checkpoints"])
            shown = self.gs.shown_status(g)
            if shown != self._last_status and shown != "running":
                self.out(terminal_safe(f"ALTER: goal {g.id} is {shown}" + (f": {g.payload['reason']}" if g.payload.get("reason") else "")
                                       + "."))
                for t in G.pending_tasks(self.ci, self.uid, g):
                    self.out(terminal_safe(f"  waiting for you: {t['tool']} {t['what']}  (say yes or no)"))
                if g.status in G.FINAL:
                    self.watching = None
            self._last_status = shown

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_s):
            with contextlib.suppress(Exception):
                self.poll()

    def _spawn(self, g: G.Goal) -> None:
        G.spawn_worker(self.ci, g.id, self.here.root, g.payload["window_s"])
        self.watch(g.id)

    def handle(self, line: str) -> bool:
        """One utterance. Returns False to end the conversation."""
        text = line.strip()
        if not text:
            return True
        if text in ("/quit", "/exit", "exit", "quit"):
            return False
        if text == "/help":
            self.out(HELP)
            return True
        cmd = {"/goals": lambda: cmd_goals([]), "/status": lambda: cmd_status([]), "/memory": lambda: cmd_memory([]),
               "/context": lambda: cmd_context([])}.get(text)
        if cmd:
            cmd()
            return True
        if self.here is None:
            cmd_ask([text])
            return True
        g = self.active()
        if text.startswith("/new "):
            g, text = None, text[5:]
        if g is None:
            g = G.GoalRunner(self.ci, self.uid, self.here, out=lambda s: None).start(text)
            c = g.payload["context"]
            self.out(f"ALTER: on it (goal {g.id}; context {c['remembered']} remembered, {c['sourced']} sourced). "
                     "Keep talking: I read what you say at my next step.")
            self._spawn(g)
            return True
        k = control(text)
        live = self.gs.is_live(g.id)
        if k == "status":
            self.out(G.report(self.ci, self.uid, g, self.gs))
        elif k in ("pause", "cancel"):
            _event(self.ci, self.uid, g, k)
        elif k == "resume":
            if live:
                self.out("ALTER: it is already running.")
            elif g.status == "waiting_approval":
                self.out("ALTER: it waits on your approval: say yes or no.")
            else:
                self._spawn(g)
                self.out(f"ALTER: resuming goal {g.id}.")
        elif k in ("approve", "deny") and g.status == "waiting_approval" and not live:
            for t in G.pending_tasks(self.ci, self.uid, g):
                self.ci.audit.append(self.uid, "alter.resolve", goal=g.id, task=t["id"], approve=k == "approve")
            self._resolve_in_background(g, k == "approve")
        else:
            self.gs.add_event(self.uid, g.id, "steer", text)
            if live:
                self.out("ALTER: noted; I'll take it into account at my next step.")
            elif g.status == "waiting_approval":
                self.out("ALTER: noted for after the action that waits on you (say yes or no).")
            else:
                self._spawn(g)
                self.out("ALTER: noted; continuing with it.")
        return True

    def _resolve_in_background(self, g: G.Goal, approve: bool) -> None:
        """Approving resumes the agent's turn, which can take minutes: run it in a worker thread."""
        self.watch(g.id)

        def go():
            from .engine import PersonalCI
            ci2 = PersonalCI(T._home())
            with contextlib.suppress(Exception):
                G.GoalRunner(ci2, self.uid, self.here, out=lambda s: None).resolve_pending(g.id, approve)
        threading.Thread(target=go, daemon=True).start()
        self.out("ALTER: " + ("approved; continuing." if approve else "refused; telling the agent."))

    def run(self, inp=input) -> int:
        self.out(f"ALTER {__version__} (alpha) · " + (f"project {self.here.name} ({self.here.root})" if self.here
                                                   else "no project here: questions only (alter init makes one)"))
        g = self.active()
        if g:
            self.out(terminal_safe(f"Active goal {g.id} ({self.gs.shown_status(g)}): {g.objective[:80]}"))
            self.watch(g.id)
        self.out('Type a goal or a question. "status", "pause", /help, /quit.')
        th = threading.Thread(target=self._loop, daemon=True)
        th.start()
        try:
            while True:
                try:
                    line = inp("alter> ")
                except (EOFError, KeyboardInterrupt):
                    self.out("")
                    break
                if not self.handle(line):
                    break
        finally:
            self._stop.set()
            th.join(timeout=2)
            with contextlib.suppress(Exception):
                self.poll()
        g = self.active()
        if g and self.gs.is_live(g.id):
            self.out(f"Goal {g.id} keeps running in the background within its window. alter goals show {g.id}")
        return 0


def repl() -> int:
    ci, uid, here = _ctx()
    return Conversation(ci, uid, here).run()


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        if sys.stdin.isatty():
            return repl()
        print(__doc__)
        return 0
    if argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if argv[0] in ("-V", "--version"):
        print(f"alter {__version__} (alpha)")
        return 0
    cmd, rest = argv[0], argv[1:]
    from .embed import EmbedderUnavailable
    table = {"init": cmd_init, "status": cmd_status, "context": cmd_context, "goals": cmd_goals, "goal": cmd_goals,
             "tasks": cmd_tasks, "memory": cmd_memory, "permissions": cmd_permissions, "vault": cmd_vault,
             "weekly": cmd_weekly, "doctor": cmd_doctor, "ask": cmd_ask,
             "approve": lambda a: cmd_resolve(a, True), "deny": lambda a: cmd_resolve(a, False),
             "say": lambda a: cmd_event("steer", a), "pause": lambda a: cmd_event("pause", a),
             "cancel": lambda a: cmd_event("cancel", a), "ci": T.main}
    try:
        if cmd in table:
            return table[cmd](rest)
        if cmd in CI_COMMANDS:
            return T.main(argv)
        ci, uid, here = _ctx()
        if here is None:  # no project here: a question, answered from cited context
            return cmd_ask(argv)
        return start_goal(ci, uid, here, " ".join(argv))[1]
    except EmbedderUnavailable as e:
        print(f"alter: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
