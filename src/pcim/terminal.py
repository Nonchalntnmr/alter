"""`ci`: the terminal entry point to Contextual Intelligence.

    ci "why does this test pass locally but fail in CI?"    ask; CI picks the model and says why
    ci -l "explain Bayes' rule"                               learn mode for this request
    ci -a "should we move from SQLite to Postgres?"           amplify mode
    ci -e "rename usr to user in this line: usr = f()"        execute mode
    ci --model claude-cli:haiku "…"                           pin a model (no fallback)
    ci challenge "we should rewrite the backend in Rust"      adversarial reasoning: assumptions,
                                                              counterexamples, the falsifying experiment
    ci challenge --resolve <id> held|failed|inconclusive      record what the experiment showed
    ci route "…"                                              the routing decision; calls nothing
    ci mode [execute|amplify|learn]                           show or set the default mode
    ci prefer [economy|balanced|quality]                      show or set the routing preference
    ci models                                                 capability registry, availability, your grants
    ci outcome <interaction> worked|failed [--corrected] [--skill S]
    ci practice <skill> right|wrong [--assisted]              record an attempt at a skill
    ci skills [--forget SKILL]                                alone vs with help, per skill
    ci scorecard [--days 7]                                   productivity / cognitive / orchestration
    ci weekly [--days 7] [--deep [--yes]] [--apply] [--dry-run] [--history] [--rollback N] [--forget] [--name N]
    ci status
    ci plan "rough idea"                                      intent compiler: goal, steps, assumptions, the
                                                              questions that matter; nothing runs
    ci plan [list] | show <id> | answer <id> "…" | approve <id>
    ci execute <plan> [--done ID] [--retry contract|STEP] [--cancel]
                                                              run an approved plan in one governed agent session,
                                                              its steps as the checklist (inside its project folder)
    ci do "fix the flaky date test"                           work in this project folder: CI routes it
                                                              (direct, clarify or plan) and says why
    ci do answer <id> "…" · ci do list [--json]               answer its question, or continue the session
    ci do forget <id> | --all                                 remove routed requests and their conversations
    ci approve <task> · ci deny <task> ["why"]                resolve an action the agent is waiting on
    ci halt · ci resume                                       Stop-all: cancel every pending or running action
    ci undo <task>                                            undo a write (refused if the file changed since)
    ci learn "…"                                              same as ci -l
    ci twin                                                   the Cognitive Twin's picture, labelled by evidence
    ci mesh [bench [--live] | place embed|generate|verify --sensitivity S]
                                                              your compute, measured; where work may run
    ci audit [--limit N]                                      verify the audit chain; what CI did
    ci doctor [--json]                                        read-only environment, identity, and store check
    ci --version                                              print the installed version

Workspace-aware: inside a directory that a project has authorized, requests also use that
project's memory, and only the vendors that project allows. Data and identity are the same as
`pcim` ($PCIM_HOME, default ~/.pcim; `pcim init` creates them). A request whose first word is a
subcommand name needs quotes, or `ci ask …`.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import re
import sys
from pathlib import Path

from . import modes as intelligence
from .cli import _home
from .engine import PersonalCI, RoutingError
from .registry import availability, entries
from .routing import PREFERENCES, TIERS, capability
from .twin import CognitiveTwin, render
from .textsafe import terminal_safe
from .types import approx_tokens
from .workspace import Workspace

SUBCOMMANDS = ("ask", "route", "mode", "prefer", "models", "outcome", "practice", "skills", "scorecard",
               "weekly", "status", "challenge", "plan", "mesh", "twin", "audit", "learn", "execute", "approve", "deny", "halt",
               "resume", "help", "do", "doctor", "undo")
# Explicit words only: "…should I say yes" is a question, not a recorded correct attempt.
RIGHT = {"right", "correct"}
WRONG = {"wrong", "incorrect"}


class AmbiguousWorkspace(Exception):
    pass


@dataclasses.dataclass
class Here:
    project_id: str
    name: str
    vendors: set[str]
    root: str


def _open() -> tuple[PersonalCI, str]:
    home = _home()
    tok = home / "token"
    if not tok.exists():
        print(f"No CI identity in {home}. Create one first:\n  alter setup     "
              "(or: pcim init --user <id> --providers claude-cli)",
              file=sys.stderr)
        raise SystemExit(2)
    from .sovereignty.crypto import MasterKeyMissing
    try:
        ci = PersonalCI(home)
    except MasterKeyMissing as e:
        print(f"ci: {e}", file=sys.stderr)
        raise SystemExit(2) from None
    uid = ci.user_for_token(tok.read_text().strip())
    if uid is None:
        print(f"The token in {home} does not match any user.", file=sys.stderr)
        raise SystemExit(2)
    return ci, uid


def _here(ci: PersonalCI, uid: str, cwd: Path | None = None) -> Here | None:
    """The project whose authorized workspace contains the current directory (the deepest one)."""
    cwd = (cwd or Path.cwd()).resolve()
    ws = Workspace(ci)
    hits = []
    for a in ws.active_auths(uid, None, "workspace"):
        root = Path(a["spec"]["root"]).resolve()
        if cwd == root or root in cwd.parents:
            hits.append((len(root.parts), a["project_id"], str(root)))
    if not hits:
        return None
    depth = max(h[0] for h in hits)
    tied = sorted({h[1] for h in hits if h[0] == depth})
    if len(tied) > 1:  # which project's vendors apply is the person's call, not a guess
        names = ", ".join(ws.get_project(uid, pid).name for pid in tied)
        raise AmbiguousWorkspace(f"more than one project authorizes this directory ({names}); "
                                 "revoke one of the workspace authorizations so CI knows which rules apply")
    best = next((h[1], h[2]) for h in hits if h[0] == depth)
    p = ws.get_project(uid, best[0])
    return Here(p.id, p.name, set(ws.provider_vendors(uid, p.id)), best[1])


def _print_json(x) -> None:
    print(json.dumps(x, indent=2, default=str))


def _money(x: float | None) -> str:
    return "unknown" if x is None else f"${x:.4f}"


# ---------------------------------------------------------------------- ask
def cmd_ask(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ci", description="Ask CI. It picks the model, and says why.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("-e", "--execute", dest="mode", action="store_const", const="execute", help="just do it")
    g.add_argument("-a", "--amplify", dest="mode", action="store_const", const="amplify", help="think it through with me")
    g.add_argument("-l", "--learn", dest="mode", action="store_const", const="learn", help="help me learn to do it")
    g.add_argument("--mode", dest="mode")
    ap.add_argument("--model", help="pin provider:model, e.g. claude-cli:haiku (no fallback)")
    ap.add_argument("--explain", action="store_true", help="print the full routing decision")
    ap.add_argument("--budget", type=int, help="context token budget")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("task", nargs="+")
    a = ap.parse_args(argv)
    task = " ".join(a.task)
    ci, uid = _open()
    try:
        here = _here(ci, uid)
    except AmbiguousWorkspace as e:
        print(f"Not sent: {e}", file=sys.stderr)
        return 2
    try:
        r = ci.ask_routed(uid, task, mode=a.mode, pinned=a.model, project_id=here.project_id if here else None,
                          project_vendors=here.vendors if here else None, budget_tokens=a.budget)
    except RoutingError as e:
        print("\n".join(e.plan.explain()), file=sys.stderr)
        for x in e.attempts:
            print(f"  failed: {x['model']}: {x['error']}", file=sys.stderr)
        return 3
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if a.json:
        _print_json({"text": r.text, "interaction_id": r.interaction_id, "receipt": r.receipt})
        return 0
    rec, route = r.receipt, r.receipt["route"]
    md = rec["intelligence_mode"]
    chosen = route["plan"]["chosen"]
    served = route["served_by"]
    label = chosen["label"] if chosen and chosen["model"] == served else served
    print(terminal_safe(r.text.rstrip()))
    print()
    where = f" · {terminal_safe(here.name)}" if here else ""
    print(f"─ {md['mode']} ({md['why']}) · {label} · {route['plan']['requirement']['tier']} task: "
          f"{route['plan']['reason']}{where}")
    cost = _money(rec["cost_usd"]) + (" notional" if served.startswith("claude-cli") and rec["cost_usd"] else "")
    print(f"  {len(rec['sent'])} notes sent, {len(rec['withheld_by_egress'])} withheld · {cost} · "
          f"{rec['latency_ms'] / 1000:.1f}s · {r.interaction_id}")
    if route["failed_before"]:
        print("  fell back after: " + "; ".join(f"{x['model']} ({x['error'][:60]})" for x in route["failed_before"]))
    hint = f"  ci outcome {r.interaction_id} worked|failed"
    if md["mode"] == "learn":
        hint += " --skill <skill>   ·   then try one alone: ci practice <skill> right|wrong"
    print(hint)
    if a.explain:
        print()
        _print_json(route["plan"])
    return 0


def cmd_route(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ci route", description="Show which model would answer, and why. Calls nothing.")
    ap.add_argument("--mode")
    ap.add_argument("--model")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("task", nargs="+")
    a = ap.parse_args(argv)
    task = " ".join(a.task)
    ci, uid = _open()
    try:
        here = _here(ci, uid)
    except AmbiguousWorkspace as e:
        print(e, file=sys.stderr)
        return 2
    md = ci.resolve_mode(uid, task, a.mode)
    plan = ci.route(uid, task, mode=md.mode, pinned=a.model, project_vendors=here.vendors if here else None)
    if a.json:
        _print_json({"mode": md.as_dict(), "plan": plan.as_dict()})
        return 0
    print(f"Mode: {md.mode} ({md.why})" + (f" · project {here.name}, vendors {', '.join(sorted(here.vendors)) or 'none'}"
                                           if here else ""))
    print("\n".join(plan.explain()))
    return 0 if plan.chosen else 3


# ---------------------------------------------------------------------- settings
def _one_word(cmd: str, argv: list[str]) -> bool:
    if len(argv) > 1:
        print(f"ci {cmd} takes one word. To ask something, quote it or use `ci ask …`; nothing was changed.",
              file=sys.stderr)
        return False
    return True


def cmd_mode(argv: list[str]) -> int:
    if not _one_word("mode", argv):
        return 2
    ci, uid = _open()
    if argv:
        try:
            m = ci.set_intelligence_mode(uid, argv[0])
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        print(f"Default mode: {m}. Per request: ci -e | -a | -l. Growth goals still default to learn for their task types; "
              "modes never change what CI may do or send.")
        return 0
    cur = ci.get_intelligence_mode(uid)
    goals = sorted(ci._assist(uid).growth_tasks)
    print(f"Default mode: {cur or intelligence.DEFAULT_MODE}{'' if cur else ' (built-in default)'}")
    print(f"Growth goals (learn by default there): {', '.join(goals) or 'none'}")
    print("Modes: execute (just do it) · amplify (options, trade-offs, challenge) · learn (hints, recall, practice)")
    return 0


def cmd_prefer(argv: list[str]) -> int:
    if not _one_word("prefer", argv):
        return 2
    ci, uid = _open()
    if argv:
        try:
            print(f"Routing preference: {ci.set_routing_preference(uid, argv[0])}")
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        return 0
    print(f"Routing preference: {ci.get_routing_preference(uid)} (one of {', '.join(PREFERENCES)}; "
          "economy routes one tier lower, quality one tier higher)")
    return 0


def cmd_models(argv: list[str]) -> int:
    ci, uid = _open()
    allowed = ci.authorized_vendors(uid)
    rows = []
    for e in entries():
        if not ({"chat", "ask"} & set(e.capabilities)):
            continue
        cap = capability(e)
        ok, why = availability(e)
        rows.append({"model": e.key if e.model else f"{e.provider} (no model set)", "label": e.label,
                     "tier": TIERS.get(cap.tier, "unrated"), "context": cap.context_window, "available": ok,
                     "why": why, "authorized": e.vendor in allowed, "privacy": e.privacy, "tier_source": cap.source})
    if "--json" in argv:
        _print_json(rows)
        return 0
    print(f"Vendors you have authorized for your own words: {', '.join(sorted(allowed)) or 'none'}")
    for r in rows:
        state = "ready" if r["available"] and r["authorized"] else ("not authorized" if r["available"] else "unavailable")
        print(f"  {r['model']:<28} {r['tier']:<9} {state:<15} {r['why']}")
    print("Tiers are vendor positioning, not CI measurements; override in $PCIM_CAPABILITIES.")
    return 0


# ---------------------------------------------------------------------- outcomes and practice
def cmd_outcome(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ci outcome", description="Record whether an answer worked (a verified outcome).")
    ap.add_argument("interaction_id")
    ap.add_argument("result", choices=["worked", "failed"])
    ap.add_argument("--corrected", action="store_true", help="it worked only after you corrected it")
    ap.add_argument("--skill", help="also log it as an assisted attempt at this skill")
    a = ap.parse_args(argv)
    ci, uid = _open()
    try:
        out = ci.record_outcome(uid, a.interaction_id, a.result == "worked", a.corrected, a.skill)
    except KeyError:
        print(f"no interaction {a.interaction_id}", file=sys.stderr)
        return 2
    print(f"Recorded: {a.result}{' after correction' if a.corrected else ''}"
          + (f"; assisted attempt at {a.skill} logged (does not move mastery)" if a.skill else ""))
    return 0 if out is not None else 1


def cmd_practice(argv: list[str]) -> int:
    assisted = "--assisted" in argv
    args = [x for x in argv if x != "--assisted"]
    if len(args) < 2 or args[-1].lower() not in RIGHT | WRONG:
        print("usage: ci practice <skill> right|wrong [--assisted]", file=sys.stderr)
        return 2
    skill, correct = " ".join(args[:-1]).strip(), args[-1].lower() in RIGHT
    if not skill:
        print("usage: ci practice <skill> right|wrong [--assisted]", file=sys.stderr)
        return 2
    ci, uid = _open()
    r = ci.probe(uid, skill, correct, assisted=assisted, source="practice")
    if r["counted"]:
        print(f"{skill}: {'right' if correct else 'wrong'}, alone. Mastery estimate {r['p_mastery']:.2f}.")
    else:
        print(f"{skill}: {'right' if correct else 'wrong'}, with help. Logged; mastery moves on unassisted attempts only.")
    return 0


def cmd_skills(argv: list[str]) -> int:
    ci, uid = _open()
    if "--forget" in argv:
        i = argv.index("--forget")
        skill = " ".join(argv[i + 1:])
        if not skill:
            print("usage: ci skills --forget <skill>", file=sys.stderr)
            return 2
        _print_json(ci.forget_skill(uid, skill))
        return 0
    rep = ci.skill_report(uid)
    if "--json" in argv:
        _print_json(rep)
        return 0
    if not rep["skills"]:
        print("No attempts recorded yet. `ci practice <skill> right|wrong` after you try something alone.")
        return 0
    for s, d in rep["skills"].items():
        u, h = d["unassisted"], d["assisted"]
        alone = f"{u['correct']}/{u['n']}" + (f" (95% {u['ci95'][0]:.0%}–{u['ci95'][1]:.0%})" if u["ci95"] else "")
        helped = f"{h['correct']}/{h['n']}" if h["n"] else "—"
        mastery = f"{d['mastery']['p_mastery']:.2f}" if d["mastery"] else "—"
        print(f"  {s:<24} alone {alone:<20} with help {helped:<7} mastery {mastery:<5} trend {d['trend']['direction']}"
              + (f"  [{', '.join(d['flags'])}]" if d["flags"] else ""))
    print(rep["basis"])
    return 0


def cmd_scorecard(argv: list[str]) -> int:
    from .evaluation import scorecard
    from .types import utcnow
    import datetime as dt
    try:
        days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 7
        if not 1 <= days <= 365:
            raise ValueError
    except (ValueError, IndexError):
        print("usage: ci scorecard [--days N]  (N from 1 to 365)", file=sys.stderr)
        return 2
    ci, uid = _open()
    now = utcnow()
    _print_json(scorecard(ci, uid, now - dt.timedelta(days=days), now))
    return 0


# ---------------------------------------------------------------------- challenge
def cmd_challenge(argv: list[str]) -> int:
    from . import challenge as ch
    ap = argparse.ArgumentParser(prog="ci challenge",
                                 description="Stress-test an idea: assumptions, counterexamples, a falsifying experiment.")
    ap.add_argument("--resolve", nargs=2, metavar=("ID", "OUTCOME"), help="held | failed | inconclusive")
    ap.add_argument("--note")
    ap.add_argument("--list", action="store_true", help="challenges whose experiment has not been recorded")
    ap.add_argument("--all", action="store_true", help="with --list: include resolved ones")
    ap.add_argument("--forget", action="store_true", help="delete the challenge log")
    ap.add_argument("--model", help="pin provider:model (no fallback)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("claim", nargs="*")
    a = ap.parse_args(argv)
    ci, uid = _open()
    if a.forget:
        print(f"Deleted {ch.forget(ci, uid)} challenges.")
        return 0
    if a.resolve:
        try:
            rec = ch.resolve(ci, uid, a.resolve[0], a.resolve[1].lower(), a.note)
        except KeyError:
            print(f"no challenge {a.resolve[0]}", file=sys.stderr)
            return 2
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        print(f"Recorded: the experiment says the claim {rec['status']}"
              + (f" ({rec['note']})" if rec["note"] else "") + ".")
        return 0
    if a.list or not a.claim:
        recs = ch.records(ci, uid)
        rows = recs if a.all else [r for r in recs if r["status"] == "open"]
        if a.json:
            _print_json({"records": rows, "metrics": ch.metrics(recs)})
            return 0
        for r in rows:
            exp = (r["challenge"] or {}).get("falsification", {}).get("experiment") or "(unstructured reply)"
            print(terminal_safe(f"  {r['id']}  {r['status']:<12} {r['ts'][:10]}  {r['claim'][:60]}\n      test: {exp[:110]}"))
        if not rows:
            print("No open challenges." if recs else "No challenges yet: ci challenge \"<an idea you believe>\"")
        m = ch.metrics(recs)
        if m["challenges"]:
            print(f"Tested {m['experiments_resolved']} of {m['challenges']} · held {m['held']} · failed {m['failed']}"
                  f" · inconclusive {m['inconclusive']}"
                  + (f" · challenger Brier {m['challenger_brier']} (n={m['challenger_brier_n']})"
                     if m["challenger_brier"] is not None else ""))
        return 0
    claim = " ".join(a.claim)
    try:
        here = _here(ci, uid)
    except AmbiguousWorkspace as e:
        print(f"Not sent: {e}", file=sys.stderr)
        return 2
    try:
        rec = ch.challenge(ci, uid, claim, pinned=a.model, project_id=here.project_id if here else None,
                           project_vendors=here.vendors if here else None)
    except RoutingError as e:
        print("\n".join(e.plan.explain()), file=sys.stderr)
        for x in e.attempts:
            print(f"  failed: {x['model']}: {x['error']}", file=sys.stderr)
        return 3
    if a.json:
        _print_json({k: v for k, v in rec.items() if k != "receipt"} | {"route": rec["receipt"]["route"]})
        return 0
    print(terminal_safe(ch.render(rec)))
    rt = rec["receipt"]["route"]
    print(f"\n─ challenge · {rt['served_by']} · {rt['plan']['requirement']['tier']} · {_money(rec['receipt']['cost_usd'])}"
          f" · {rec['id']}")
    if rec["parsed"]:
        print(f"  ran the experiment? ci challenge --resolve {rec['id']} held|failed|inconclusive --note \"…\"")
    return 0


# ---------------------------------------------------------------------- weekly
def cmd_weekly(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ci weekly", description="The weekly review by your Cognitive Twin.")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--deep", action="store_true", help="add one model-written challenge (asks first)")
    ap.add_argument("--yes", action="store_true", help="with --deep: send without asking")
    ap.add_argument("--apply", nargs="*", metavar="ID", help="accept proposed changes to CI (all, or these ids)")
    ap.add_argument("--dry-run", action="store_true", help="do not save this week's snapshot")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--rollback", type=int, metavar="VERSION")
    ap.add_argument("--forget", action="store_true", help="delete every weekly snapshot")
    ap.add_argument("--name", help="what to call your twin (default: '<your name> 2.0')")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    ci, uid = _open()
    twin = CognitiveTwin(ci)
    if a.name:
        print(f"Your twin is now {twin.set_name(uid, a.name)}.")
    if a.history:
        snaps = ci.weekly_snapshots(uid)
        if a.json:
            _print_json(snaps)
            return 0
        for s in snaps:
            print(f"  v{s['version']}{' (active)' if s['active'] else ''}  {s['week']}  "
                  f"{len(s.get('priorities', []))} priorities · {len(s.get('skills', {}))} skills · "
                  f"{len(s.get('challenges', []))} challenges")
        if not snaps:
            print("No weekly snapshots yet.")
        return 0
    if a.rollback is not None:
        try:
            ci.activate_weekly(uid, a.rollback)
        except KeyError as e:
            print(e, file=sys.stderr)
            return 2
        print(f"Weekly version {a.rollback} is active again; next week's review checks its priorities.")
        return 0
    if a.forget:
        print(f"Deleted {ci.forget_weekly(uid)} weekly snapshots.")
        return 0

    review = twin.review(uid, days=a.days)
    applied = twin.apply(uid, review, a.apply or None) if a.apply is not None else None
    if applied:
        review.adjustments = [x | {"applied": any(y["id"] == x["id"] for y in applied)} for x in review.adjustments]
    deep = None
    if a.deep:
        deep = _deep(twin, uid, review, a.yes)
    version = None if a.dry_run else twin.save(uid, review)
    if a.json:
        _print_json(review.as_dict() | {"saved_version": version, "deep": deep, "applied": applied})
        return 0
    print(render(review, version, deep, applied))
    return 0


def _deep(twin: CognitiveTwin, uid: str, review, yes: bool) -> dict | None:
    plan = twin.deep_plan(uid)
    if plan.refused:
        print("--deep: no model can take it: " + plan.refused, file=sys.stderr)
        return None
    c = plan.chosen
    text, withheld = twin.digest(uid, review, c.entry.vendor)
    wh = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in withheld.items() if v) or "nothing"
    print(f"--deep sends ~{approx_tokens(text):,} tokens of this week's facts to {c.entry.label} "
          f"({c.entry.privacy}); estimate {_money(c.est_cost_usd)}. Withheld: {wh}.", file=sys.stderr)
    if not yes:
        if not sys.stdin.isatty():
            print("Not sent: confirm with --yes when not at a terminal.", file=sys.stderr)
            return None
        if input("Send? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Not sent.", file=sys.stderr)
            return None
    try:
        return twin.deep(uid, review, plan)
    except RoutingError as e:
        print("--deep failed: " + str(e) + "".join(f"\n  {x['model']}: {x['error']}" for x in e.attempts),
              file=sys.stderr)
        return None


def cmd_status(argv: list[str]) -> int:
    ci, uid = _open()
    try:
        here = _here(ci, uid)
    except AmbiguousWorkspace as e:
        here = None
        print(f"Here: {e}")
    twin = CognitiveTwin(ci)
    print(f"{uid} · mode {ci.get_intelligence_mode(uid) or intelligence.DEFAULT_MODE} · routing "
          f"{ci.get_routing_preference(uid)} · vendors {', '.join(sorted(ci.authorized_vendors(uid))) or 'none'}")
    print(f"Here: {here.name} ({here.root}; vendors {', '.join(sorted(here.vendors)) or 'none'})" if here
          else "Here: no authorized project contains this directory (user-wide memory only)")
    version, snap = ci.active_weekly(uid)
    if snap and snap.get("priorities"):
        review = twin.review(uid, days=7)
        print(f"This week's priorities ({twin.name(uid)}, v{version}, {snap['week']}):")
        from .twin import resolve
        labels = twin.labels_now(uid)
        for p in snap["priorities"]:
            status, ev = twin.check(p["check"], review.facts, review.since, review.until)
            print(terminal_safe(f"  [{status}] {resolve(p['action'], labels)} ({ev})"))
    else:
        print("No weekly priorities yet: `ci weekly`.")
    return 0



# ---------------------------------------------------------------------- plan (intent compiler)
def cmd_plan(argv: list[str]) -> int:
    from . import intent_compiler as ic
    actions = ("show", "answer", "approve", "list")
    ci, uid = _open()
    if not argv or argv[0] == "list":
        recs = ic.records(ci, uid)[-15:]
        if not recs:
            print('No plans yet: ci plan "rough idea"')
        for r in reversed(recs):
            goal = (r.get("spec") or {}).get("goal") or r.get("error") or r["text"]
            print(terminal_safe(f"{r['id']}  {r['status']:<18} {goal[:90]}"))
        return 0
    if argv[0] in actions[:3]:
        if len(argv) < 2 or (argv[0] == "answer" and len(argv) < 3):
            print(f"usage: ci plan {argv[0]} <id>" + (' "answers"' if argv[0] == "answer" else ""), file=sys.stderr)
            return 2
        try:
            if argv[0] == "show":
                rec = ic.get(ci, uid, argv[1])
            elif argv[0] == "approve":
                rec = ic.approve(ci, uid, argv[1])
            else:
                rec = ic.answer(ci, uid, argv[1], " ".join(argv[2:]))
        except KeyError:
            print(f"no plan {argv[1]}", file=sys.stderr)
            return 2
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        except RoutingError as e:
            print("\n".join(e.plan.explain()), file=sys.stderr)
            return 3
        print(terminal_safe(ic.render(rec)))
        return 0
    ap = argparse.ArgumentParser(prog="ci plan", description="Turn a rough idea into a checked task specification.")
    ap.add_argument("--model", help="pin provider:model (no fallback)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("idea", nargs="+")
    a = ap.parse_args(argv)
    try:
        here = _here(ci, uid)
    except AmbiguousWorkspace as e:
        print(f"Not sent: {e}", file=sys.stderr)
        return 2
    try:
        rec = ic.compile_intent(ci, uid, " ".join(a.idea), pinned=a.model,
                                project_id=here.project_id if here else None,
                                project_vendors=here.vendors if here else None)
    except RoutingError as e:
        print("\n".join(e.plan.explain()), file=sys.stderr)
        for x in e.attempts:
            print(f"  failed: {x['model']}: {x['error']}", file=sys.stderr)
        return 3
    if a.json:
        _print_json(rec)
    else:
        print(terminal_safe(ic.render(rec)))
    return 0 if rec["status"] != "invalid" else 4


# ---------------------------------------------------------------------- mesh
def cmd_mesh(argv: list[str]) -> int:
    from . import mesh
    from .types import Sensitivity
    ap = argparse.ArgumentParser(prog="ci mesh", description="Your compute: what exists, measured, and where work may run.")
    ap.add_argument("action", nargs="?", default="show", choices=("show", "bench", "place"))
    ap.add_argument("workload", nargs="?", help="for place: embed | generate | verify")
    ap.add_argument("--live", action="store_true", help="bench: also time one tiny call per cloud model (billed)")
    ap.add_argument("--sensitivity", default="personal", choices=[x.value for x in Sensitivity])
    ap.add_argument("--tier", type=int, choices=(1, 2, 3), help="place: minimum capability tier")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    home = _home()
    try:
        res = mesh.discover(home)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    if a.action == "bench":
        ci, uid = _open() if a.live else (None, None)
        if a.live:
            n = sum(1 for r in res if r.kind == "llm" and r.available and r.location == "cloud")
            print(f"Timing {n} cloud model(s) with one one-word call each; billed to those accounts.")
        out = mesh.bench(home, res, live=a.live, ci=ci, uid=uid)
        if a.json:
            _print_json(out)
            return 0
        print(f"Measured {out['measured_at'][:19]} on {out['host']} (this device unless marked):")
        for k, v in out["results"].items():
            print(f"  {k:<34} " + (v.get("error") or f"{v['median']} {v['unit']}"
                                    + (f" (min {v['min']}, max {v['max']}, {v['reps']} reps)" if "min" in v else "")))
        return 0
    if a.action == "place":
        if not a.workload:
            print("usage: ci mesh place embed|generate|verify [--sensitivity S] [--tier N]", file=sys.stderr)
            return 2
        vendors = None
        try:
            ci, uid = _open()
            vendors = set(ci.authorized_vendors(uid))
        except SystemExit:
            pass
        try:
            pl = mesh.place(res, a.workload, Sensitivity(a.sensitivity), a.tier, mesh.last_bench(home), vendors)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        if a.json:
            _print_json(pl.as_dict())
            return 0
        print(f"{a.workload} at {a.sensitivity}: {pl.chosen or 'nowhere'}" + (f"  (then {', '.join(pl.fallbacks)})" if pl.fallbacks else ""))
        for r in pl.reasons:
            print(f"  {r}")
        for x in pl.excluded:
            print(f"  not {x['resource']}: {x['why']}")
        return 0 if pl.chosen else 1
    if a.json:
        _print_json({"resources": [r.as_dict() for r in res], "last_bench": mesh.last_bench(home)})
        return 0
    lb = mesh.last_bench(home) or {}
    measured = lb.get("results", {})
    print("Resources (nothing here is summed: your device, your machines and your cloud accounts are separate):")
    for r in res:
        m = measured.get(r.id)
        mtxt = f" · measured {m['median']} {m['unit']}" if m and "median" in m else ""
        state = "ok " if r.available else "-- "
        print(terminal_safe(f"  {state}{r.id:<30} {r.location:<11} ≤{r.ceiling.value:<9} {r.detail}{mtxt}"))
    print(f"\nLast benchmark: {lb.get('measured_at', 'never')[:19]}   ·   ci mesh bench   ·   ci mesh place generate --sensitivity sensitive")
    return 0


# ---------------------------------------------------------------------- twin, audit, learn
def cmd_twin(argv: list[str]) -> int:
    """The Cognitive Twin's current picture, each part labelled with what kind of evidence it is."""
    from . import challenge as ch
    from . import intent_compiler as ic
    from .growth import independence_report
    from .types import utcnow
    ci, uid = _open()
    twin = CognitiveTwin(ci)
    if argv:
        from . import twin_do
        if len(argv) == 2 and argv[0] in ("include", "exclude") and argv[1] == "do":
            if argv[0] == "include":
                twin_do.include_do(ci, uid)
                print("The twin now counts your `ci do` requests in `ci weekly` (counts only, no request text). "
                      "Undo: ci twin exclude do")
            else:
                twin_do.exclude_do(ci, uid)
                print("The twin no longer reads your `ci do` records.")
            return 0
        print("usage: ci twin [include do | exclude do]", file=sys.stderr)
        return 2
    dna = ci.dna(uid)
    rep = independence_report(ci.store.list_skills(uid), ci.attempts(uid), utcnow())
    print(f"{twin.name(uid)} — a model of how {uid} works, built from what you recorded. It is not you, and it says which parts are facts.")
    stated = dna.get("stated", {})
    print(f"\nStated by you (facts about your preferences): " + ", ".join(f"{len(v)} {k}" for k, v in stated.items()))
    for k, v in stated.items():
        for it in v[:3]:
            print(terminal_safe(f"  [{k}] {it['content'][:100]}"))
    inferred = dna.get("inferred", [])
    print(f"Inferred by CI (hypotheses, with confidence): {len(inferred)}")
    learned = dna.get("learned", {})
    print(f"Learned from outcomes: {learned.get('status', '?')}; {len(learned.get('findings', []))} confident finding(s)")
    skills = rep.get("skills", rep) if isinstance(rep, dict) else {}
    if skills:
        print("\nSkills (alone vs with help; only unassisted attempts count as yours):")
        for name, s in list(skills.items())[:8]:
            if isinstance(s, dict):
                alone, helped = s.get("unassisted") or {}, s.get("assisted") or {}
                flags = ", ".join(s.get("flags", [])) if isinstance(s.get("flags"), list) else ""
                print(terminal_safe(f"  {name:<24} alone {alone.get('correct', 0)}/{alone.get('n', 0)} · "
                                    f"with help {helped.get('correct', 0)}/{helped.get('n', 0)}" + (f" · {flags}" if flags else "")))
    else:
        print("\nSkills: none recorded yet (ci practice <skill> right|wrong).")
    open_ch = [r for r in ch.records(ci, uid) if r["status"] == "open"]
    plans = ic.records(ci, uid)
    print(f"\nOpen challenges (experiments not yet run): {len(open_ch)}")
    print(f"Plans: {sum(1 for p in plans if p['status'] == 'needs_answers')} waiting on your answers, "
          f"{sum(1 for p in plans if p['status'] in ('awaiting_approval', 'ready'))} waiting on approval")
    from . import twin_do
    print("ci do records: " + ("included (counts only) · ci twin exclude do" if twin_do.do_included(ci, uid)
                               else "not read (default) · ci twin include do"))
    print("\nWeekly review: ci weekly   ·   evidence behind any of this: ci audit, pcim export")
    return 0


def cmd_audit(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ci audit", description="Verify the audit chain and show what CI did.")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    ci, uid = _open()
    ok, bad = ci.audit.verify()
    entries_ = ci.audit.entries(uid)
    counts: dict[str, int] = {}
    for e in entries_:
        counts[e["event"]] = counts.get(e["event"], 0) + 1
    if a.json:
        _print_json({"chain_ok": ok, "first_bad_seq": bad, "head": ci.audit.head(), "counts": counts,
                     "recent": entries_[-a.limit:]})
        return 0 if ok else 5
    print(f"Audit chain: {'verified' if ok else f'BROKEN at entry {bad}'} · {len(entries_)} entries · head {ci.audit.head()[:16]}…")
    print("By event: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:12]))
    for e in entries_[-a.limit:]:
        print(terminal_safe(f"  {e['ts'][:19]}  {e['event']:<24} {json.dumps(e['payload'])[:110]}"))
    return 0 if ok else 5


def cmd_learn(argv: list[str]) -> int:
    """`ci learn "…"` is `ci -l "…"`: learn mode for this request."""
    return cmd_ask(["-l", *argv])



# ---------------------------------------------------------------------- execute, approve, deny
def _workspace_agent(ci):
    from .agent import Agent
    from .governor import Governor
    from .tasks import TaskQueue
    ws = Workspace(ci)
    return Agent(ci, ws, TaskQueue(ws, Governor(ws)))


def cmd_execute(argv: list[str]) -> int:
    from . import plan_exec as px
    ap = argparse.ArgumentParser(prog="ci execute", description="Run an approved plan through the governed workspace agent.")
    ap.add_argument("plan")
    ap.add_argument("--done", action="append", default=[], metavar="ID", help="mark an external step or outcome you did")
    ap.add_argument("--retry", action="append", default=[], metavar="STEP",
                    help="re-run a failed, interrupted, incomplete or halted step")
    ap.add_argument("--cancel", action="store_true", help="cancel the plan and any action it is waiting on")
    ap.add_argument("--model", help="pin provider:model for the agent")
    a = ap.parse_args(argv)
    ci, uid = _open()
    try:
        here = _here(ci, uid)
    except AmbiguousWorkspace as e:
        print(f"Not run: {e}", file=sys.stderr)
        return 2
    if here is None:
        print("Run this inside a directory a project has authorized; steps act on that workspace only.", file=sys.stderr)
        return 2
    prov, _, mdl = (a.model or "").partition(":")
    try:
        res = px.execute(ci, uid, a.plan, here.project_id, here.vendors, provider=prov or None, model=mdl or None,
                         agent=_workspace_agent(ci), done=a.done, retry=a.retry, cancel=a.cancel)
    except KeyError:
        print(f"no plan {a.plan}", file=sys.stderr)
        return 2
    except (px.ExecError, ValueError, RuntimeError) as e:
        print(e, file=sys.stderr)
        return 2
    print(terminal_safe(px.render(res)))
    # 0 finished (done, or cancelled as asked) · 6 needs you · 4 not achieved (incomplete, failed, interrupted, halted)
    return {"done": 0, "cancelled": 0, "waiting": 6, "awaiting_you": 6}.get(res["outcome"], 4)


def cmd_approve(argv: list[str], approve: bool = True) -> int:
    if not argv:
        print(f"usage: ci {'approve' if approve else 'deny'} <task id>" + ("" if approve else ' ["reason"]'), file=sys.stderr)
        return 2
    from . import plan_exec as px
    ci, uid = _open()
    pid = px.plan_for_task(ci, uid, argv[0])
    try:
        # a plan's action is resolved under the plan's lock, so `ci execute` never judges a step
        # while the turn this resumes is still running
        with px.plan_lock(ci, pid) if pid else contextlib.nullcontext():
            out = _workspace_agent(ci).resolve(uid, argv[0], approve, " ".join(argv[1:]))
    except KeyError:
        print(f"no task {argv[0]}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as e:
        print(e, file=sys.stderr)
        return 2
    d = out.as_dict() if hasattr(out, "as_dict") else out
    if "status" in d:
        print(f"{'Approved' if approve else 'Denied'}. Session {d['status']}.")
        if not pid:  # a plan's turn is reported by `ci execute`; a `ci do` session is reported here
            _print_turn(out)
            if d["status"] == "done" and d.get("session_id"):
                task = _workspace_agent(ci).queue.get(uid, argv[0])
                v = _session_verification(ci, uid, task.project_id, d["session_id"], d.get("text") or "")
                version, log = _do_log(ci, uid)
                rec = next((r for r in log if r.get("session_id") == d["session_id"]), None)
                if rec is not None:
                    rec["verification"], rec["status"] = v, "done"
                    _do_save(ci, uid, rec)
                _print_verification(v)
        elif d.get("text"):
            print(terminal_safe(d["text"][:300]))
    else:
        print(f"{'Approved' if approve else 'Denied'}: task {d['task']['status']}")
    if pid:
        print(f"Plan {pid}: run `ci execute {pid}` to record the step and continue.")
    return 0


def _routing_default(built_in: str) -> str:
    """PCIM_DO_ROUTING=direct|adaptive|assisted overrides the built-in default (evaluations set it)."""
    v = os.environ.get("PCIM_DO_ROUTING", "").strip().lower()
    return v if v in ("direct", "adaptive", "assisted", "selective") else built_in


DO_STATE = "pcim-do"
# "ci do you think …", "ci do we have …": a yes/no question, not work to do
_QUESTION_SUBJECTS = frozenset({"i", "you", "u", "we", "they", "he", "she"})
DO_LOG_MAX = 200


def _do_log(ci, uid: str) -> tuple[int | None, list]:
    return ci._state_json(uid, DO_STATE, [])


def _do_save(ci, uid: str, rec: dict) -> None:
    version, log = _do_log(ci, uid)
    log = [r for r in log if r["id"] != rec["id"]] + [rec]
    blob = json.dumps(log[-DO_LOG_MAX:]).encode()
    if version is None:
        ci.store.save_policy_state(uid, DO_STATE, blob, note="ci do: routed requests")
    else:
        ci.store.overwrite_policy_state(uid, DO_STATE, version, blob)


def _print_turn(turn) -> None:
    """An agent turn: its text, then every action waiting on the person, with the commands for it."""
    d = turn.as_dict() if hasattr(turn, "as_dict") else turn
    if d.get("text"):
        print(terminal_safe(d["text"].rstrip()))
    by_id = {t.get("id"): t for t in d.get("tasks") or []}
    for tid in d.get("pending") or []:
        t = by_id.get(tid, {})
        args = t.get("args") or {}
        what = args.get("path") or " ".join(str(x) for x in (args.get("argv") or [])) or ""
        print(terminal_safe(f"  waiting for you: {tid}  {t.get('tool', '?')} {what}".rstrip()))
    if d.get("pending"):
        print("  approve with: ci approve <task>   ·   refuse with: ci deny <task> \"why\"")


_CLAIMS_DONE = re.compile(r"\b(?:done|fixed|works|working now|complete[d]?|finished|all (?:the )?tests pass(?:ed)?|"
                          r"tests (?:now )?pass(?:ed)?|resolved|implemented)\b", re.I)


def _session_verification(ci, uid: str, project_id: str, sid: str, final_text: str = "") -> dict:
    """What the session's own task records show, read-only (plan_exec's evidence rule):
    - verified: the project's tests passed after the last change;
    - not_verified: files changed, with no passing test run after the last change;
    - unchanged: no file was changed.
    It never changes an outcome. It says what the record supports, apart from what the agent says."""
    from .governor import Governor
    from .tasks import TaskQueue
    ws = Workspace(ci)
    from .goals import verify_tasks
    ts = [t.public() for t in TaskQueue(ws, Governor(ws)).list(uid, project_id, limit=1000) if t.session_id == sid]
    v = verify_tasks(ts, final_text)  # ordered by when actions ran, not when they were proposed
    out = {k: v[k] for k in ("verdict", "line", "refused", "writes", "test_runs", "claims_done", "claim_unverified")}
    if v["verdict"] == "not_verified":  # ci do's own wording, kept
        out["line"] = ("not verified: the last test run after the change did not pass" if "did not pass" in v["line"] else
                       "not verified: the tests were not run after the last change "
                       "(ask CI to run them, or run them yourself)")
    return out


def _print_verification(v: dict) -> None:
    print(f"─ {v['line']}")
    if v.get("claim_unverified"):
        print("  the reply says the work is done; the records do not show the project's tests passing after the "
              "last change, so treat that as unverified")
    if v["refused"]:
        print(f"  refused or cancelled: {len(v['refused'])} action(s): "
              + terminal_safe("; ".join(f"{x['tool']} {x['path'] or ''}".strip() for x in v["refused"])))


def _do_act(ci, uid: str, here, rec: dict, r, text: str, model: str | None, plan: dict | None = None) -> int:
    """Carry out a route: ask, plan, or hand the request to the governed agent. With `plan`
    (planning-assisted routing), the plan already compiled is shown rather than compiled again."""
    from . import intent_compiler as ic
    from . import plan_exec as px
    from .router import CLARIFY, PLAN
    rec["route"] = r.as_dict()
    ci.audit.append(uid, "do.route", request=rec["id"], decision=r.decision, project=here.project_id,
                    answered=bool(rec.get("answers")))
    if r.decision == CLARIFY:
        rec["status"] = "needs_answer"
        _do_save(ci, uid, rec)
        print(terminal_safe(r.question or "What should the result be?"))
        print(f"─ clarify: {terminal_safe('; '.join(r.reasons))}")
        print(f'  answer with: ci do answer {rec["id"]} "…"')
        return 6
    if r.decision == PLAN:
        if plan is None:
            plan = ic.compile_intent(ci, uid, rec["text"], answers=rec.get("answers"), pinned=model,
                                     project_id=here.project_id, project_vendors=here.vendors)
        rec["status"], rec["plan_id"] = "planned", plan["id"]
        _do_save(ci, uid, rec)
        print(terminal_safe(ic.render(plan)))
        print(f"─ plan: {terminal_safe('; '.join(r.reasons))}")
        return 0 if plan["status"] in ("ready", "awaiting_approval") else 6 if plan["status"] == "needs_answers" else 1
    prov, _, mdl = (model or rec.get("model") or "").partition(":")
    try:
        prov, mdl = px._model(here.vendors, prov or None, mdl or None)
        from .router import SELECTIVE, SELECTIVE_DIRECTIVES
        turn = _workspace_agent(ci).send(uid, here.project_id, text, prov, mdl, new_session=not rec.get("session_id"),
                                         directives=SELECTIVE_DIRECTIVES if rec.get("route_mode") == SELECTIVE else None)
    except (px.ExecError, RuntimeError, ValueError, PermissionError) as e:
        _do_save(ci, uid, rec)
        print(terminal_safe(f"Not run: {e}"), file=sys.stderr)
        return 2
    rec["status"], rec["session_id"] = turn.status, turn.session_id
    if turn.status == "done":
        rec["verification"] = _session_verification(ci, uid, here.project_id, turn.session_id, turn.text)
    _do_save(ci, uid, rec)
    _print_turn(turn)
    print()
    if rec.get("verification") and turn.status == "done":
        _print_verification(rec["verification"])
    print(f"─ direct: {terminal_safe('; '.join(r.reasons))}")
    if r.yours:
        print("  yours to do (CI cannot act outside the computer): " + terminal_safe("; ".join(r.yours)))
    print(f'  continue with: ci do answer {rec["id"]} "…"')
    return 0 if turn.status == "done" else 6 if turn.status == "awaiting_approval" else 4


def _do_forget(ci, uid: str, args: list[str]) -> int:
    """Remove routed requests: their text, answers, route and verification, and the agent
    conversation each one started. The record of each action approved, refused or run stays (it
    is the audit of what happened to your files), and so does any plan a request led to."""
    from .intent_compiler import resolve_id
    if not args:
        print('usage: ci do forget <id> | ci do forget --all', file=sys.stderr)
        return 2
    version, log = _do_log(ci, uid)
    if args[0] == "--all":
        gone = list(log)
    else:
        try:
            rid = resolve_id([r["id"] for r in log], args[0], "request")
        except KeyError:
            print(f"no request {args[0]}", file=sys.stderr)
            return 2
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        gone = [r for r in log if r["id"] == rid]
    keep = [r for r in log if r not in gone]
    if version is not None:
        if keep:
            ci.store.overwrite_policy_state(uid, DO_STATE, version, json.dumps(keep).encode())
        else:
            ci.store.delete_policy_states(uid, DO_STATE)
    sessions = 0
    for r in gone:
        if r.get("session_id"):
            sessions += ci.store.execute("DELETE FROM sessions WHERE id=? AND user_id=?",
                                         (r["session_id"], uid)).rowcount
    ci.store.flush_deleted()  # secure_delete is on; the WAL is truncated so nothing lingers there
    ci.audit.append(uid, "do.forget", requests=len(gone), sessions=sessions)
    print(f"Forgot {len(gone)} request(s) and {sessions} agent conversation(s): the text, answers, route and "
          "verification are gone.")
    print("Kept: the record of each action you approved, refused or ran (ci audit), and any plan a request led "
          "to (ci plan list).")
    return 0


def cmd_do(argv: list[str]) -> int:
    from .router import ASSISTED, DIRECT, PLAN, ROUTING_DEFAULT, SELECTIVE, Route, route, route_from_plan
    from .types import new_id, utcnow
    if not argv or argv[0] in ("-h", "--help"):
        print('usage: ci do "<request>" [--route direct|plan|assisted|selective] [--model provider:model] [--explain]\n'
              '       ci do answer <id> "<answer>"\n       ci do list [--json]\n       ci do forget <id> | --all', file=sys.stderr)
        return 2
    if argv[0].lower() in _QUESTION_SUBJECTS and len(argv) > 1:  # "ci do you think …" is a question
        return cmd_ask(["do", *argv])
    ci, uid = _open()
    if argv[0] == "forget":
        return _do_forget(ci, uid, argv[1:])
    if argv[0] == "list":
        log = _do_log(ci, uid)[1]
        if "--json" in argv:
            _print_json(log)
        for r in log[-20:]:
            print(terminal_safe(f"{r['id']}  {r['route']['decision']:7} {r['status']:17} {r['text'][:60]}"))
        return 0
    try:
        here = _here(ci, uid)
    except AmbiguousWorkspace as e:
        print(f"Not run: {e}", file=sys.stderr)
        return 2
    if here is None:
        print("Run this inside a directory a project has authorized: `ci do` works on that folder only. "
              "For a question with no folder, use ci \"…\".", file=sys.stderr)
        return 2
    if argv[0] == "answer":
        if len(argv) < 3:
            print('usage: ci do answer <id> "<answer>"', file=sys.stderr)
            return 2
        log = _do_log(ci, uid)[1]
        from .intent_compiler import resolve_id
        try:
            rid = resolve_id([r["id"] for r in log], argv[1], "request")
        except KeyError:
            print(f"no request {argv[1]}", file=sys.stderr)
            return 2
        except ValueError as e:  # ambiguous: never answer whichever request came first
            print(str(e), file=sys.stderr)
            return 2
        rec = next(r for r in log if r["id"] == rid)
        if rec["project_id"] != here.project_id:
            print("that request belongs to another project folder", file=sys.stderr)
            return 2
        answer = " ".join(" ".join(argv[2:]).split())
        if rec.get("session_id"):
            # Its own conversation, if still open, gets the answer as the next message. A later
            # request or the idle timeout closes it (one open session per project), and then the
            # answer would land in someone else's conversation, or alone in a new one. So it
            # starts again with the request and the answer together.
            if _workspace_agent(ci).open_session(uid, here.project_id) == rec["session_id"]:
                return _do_act(ci, uid, here, rec, Route(**rec["route"]), answer, None)
            rec["session_id"], rec["answers"] = None, answer
            return _do_act(ci, uid, here, rec, Route(**rec["route"]),
                           f"{rec['text']}\n\nAnswer to your question: {answer}", None)
        if rec["status"] != "needs_answer":
            hint = f"answer the plan with: ci plan answer {rec['plan_id']} \"…\"" if rec.get("plan_id") else ""
            print(f"{rec['id']} is {rec['status']}" + (f"; {hint}" if hint else ""), file=sys.stderr)
            return 2
        rec["answers"] = answer
        if rec.get("assist_plan"):  # planning-assisted: the answer goes to the plan, which routes again
            from . import intent_compiler as ic
            from .router import route_from_plan
            plan2 = ic.answer(ci, uid, rec["assist_plan"], answer, pinned=rec.get("model"),
                              project_vendors=here.vendors)
            rec["assist_plan"] = plan2["id"]
            r = route_from_plan(plan2, answered=True)
            return _do_act(ci, uid, here, rec, r, f"{rec['text']}\n\nAnswer to your question: {answer}", None,
                           plan=plan2 if r.decision == PLAN else None)
        r = route(rec["text"], answered=True, answers=answer)
        return _do_act(ci, uid, here, rec, r, f"{rec['text']}\n\nAnswer to your question: {answer}", None)
    ap = argparse.ArgumentParser(prog="ci do")
    ap.add_argument("--route", choices=(DIRECT, PLAN, ASSISTED, SELECTIVE),
                    help="override the routing decision; assisted = triage with one plan compile; selective = direct, "
                         "asking only when the files cannot settle a choice")
    ap.add_argument("--model", help="pin provider:model for the agent")
    ap.add_argument("--explain", action="store_true", help="print the signals behind the routing decision")
    ap.add_argument("request", nargs="+")
    a = ap.parse_args(argv)
    text = " ".join(" ".join(a.request).split())
    if (a.route or _routing_default(ROUTING_DEFAULT)) == ASSISTED:
        from . import intent_compiler as ic
        rec = {"id": new_id("do"), "ts": utcnow().isoformat(), "text": text, "project_id": here.project_id,
               "answers": None, "status": "new", "session_id": None, "plan_id": None, "model": a.model,
               "route_mode": ASSISTED}
        try:
            plan = ic.compile_intent(ci, uid, text, pinned=a.model, project_id=here.project_id,
                                     project_vendors=here.vendors)
        except (RoutingError, ValueError) as e:
            plan = {"id": None, "status": "invalid", "error": str(e)[:200], "spec": None, "gate": None}
        rec["assist_plan"] = plan.get("id")
        r = route_from_plan(plan)
        rec["route"] = r.as_dict()
        if a.explain:
            _print_json({"decision": r.decision, "reasons": r.reasons, "signals": r.signals})
        return _do_act(ci, uid, here, rec, r, text, a.model, plan=plan if r.decision == PLAN else None)
    if (a.route or _routing_default(ROUTING_DEFAULT)) == SELECTIVE:
        r = Route(DIRECT, ["direct, asking only if the files cannot settle a choice that changes the result; each "
                           "write and test run still needs your approval"], None, route(text).yours, {"selective": True})
        rec = {"id": new_id("do"), "ts": utcnow().isoformat(), "text": text, "project_id": here.project_id,
               "answers": None, "status": "new", "session_id": None, "plan_id": None, "route": r.as_dict(),
               "model": a.model, "route_mode": SELECTIVE}
        return _do_act(ci, uid, here, rec, r, text, a.model)
    r = route(text)
    if a.route and a.route != r.decision:
        r = Route(a.route, [f"you chose {a.route} (CI would have chosen {r.decision})"], None, r.yours, r.signals)
    elif not a.route and _routing_default(ROUTING_DEFAULT) == DIRECT and r.decision != DIRECT:
        r = Route(DIRECT, [f"direct is the default; CI's router suggests {r.decision}: {'; '.join(r.reasons)} "
                           f"(ci do --route {r.decision if r.decision == PLAN else DIRECT} …)"], None, r.yours, r.signals)
    if a.explain:
        from .router import MEASURED
        _print_json({"decision": r.decision, "reasons": r.reasons, "signals": r.signals, "measured": MEASURED})
    rec = {"id": new_id("do"), "ts": utcnow().isoformat(), "text": text, "project_id": here.project_id,
           "answers": None, "status": "new", "session_id": None, "plan_id": None, "route": r.as_dict(),
           "model": a.model}
    return _do_act(ci, uid, here, rec, r, text, a.model)


def cmd_halt(argv: list[str]) -> int:
    """Stop-all: block new execution and model calls, cancel queued and running actions."""
    from .governor import Governor
    from .tasks import TaskQueue
    ci, uid = _open()
    ws = Workspace(ci)
    r = TaskQueue(ws, Governor(ws)).halt(uid)
    print(f"Halted. {r['cancelled']} pending or running action(s) cancelled. Nothing runs until `ci resume`.")
    return 0


def cmd_resume(argv: list[str]) -> int:
    ci, uid = _open()
    Workspace(ci).set_halted(uid, False)
    print("Resumed. A halted plan stays halted until you retry it: ci execute <plan> --retry contract "
          "(or --retry <step> for a plan run step by step).")
    return 0


def cmd_deny(argv: list[str]) -> int:
    return cmd_approve(argv, approve=False)


def cmd_undo(argv: list[str]) -> int:
    """Undo a write the agent made: restore the file it replaced, or remove the file it created. Refused
    when the file changed after that write (yours or a later write), so nothing is ever overwritten."""
    from .governor import Governor
    from .tasks import TaskQueue, TaskStateError
    if len(argv) != 1:
        print("usage: ci undo <task>   (the write's task id: ci audit, alter tasks or alter goals show)", file=sys.stderr)
        return 2
    ci, uid = _open()
    ws = Workspace(ci)
    try:
        r = TaskQueue(ws, Governor(ws)).undo(uid, argv[0])
    except KeyError:
        print(f"no task {argv[0]}", file=sys.stderr)
        return 2
    except (TaskStateError, ValueError, PermissionError) as e:
        print(f"Not undone: {e}", file=sys.stderr)
        return 2
    print(terminal_safe(f"Undone: {r.get('path', 'the file')} is back to how it was before task {argv[0]}."))
    return 0


def cmd_doctor(argv: list[str]) -> int:
    """Read-only diagnostic check for environment, identity, CLI isolation, cache, store, and Stop-all."""
    import importlib
    import importlib.util
    import os
    import shutil
    from .adapters.claude_cli import ClaudeCLIAdapter, cli_version, sanitized_profile
    from .embed import DEFAULT_SEMANTIC_MODEL

    ap = argparse.ArgumentParser(prog="ci doctor", description="Read-only diagnostic check.")
    ap.add_argument("--json", action="store_true", dest="as_json")
    a = ap.parse_args(argv)

    checks: list[dict] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    # 1. Python & core dependencies
    py_ok = sys.version_info >= (3, 11)
    missing_deps = [pkg for pkg in ("numpy", "pydantic", "cryptography", "httpx")
                    if importlib.util.find_spec(pkg) is None]
    if py_ok and not missing_deps:
        add("python_deps", "ok", f"Python {sys.version.split()[0]}, core deps importable")
    else:
        add("python_deps", "fail",
            f"Python {sys.version.split()[0]} (need >=3.11); missing={missing_deps}")

    # 2. PCIM_HOME identity
    home = _home()
    state_ok = (home / "token").is_file() and (home / "pcim.db").is_file()
    if state_ok:
        add("pcim_home", "ok", f"{home} initialized")
    else:
        add("pcim_home", "fail", f"{home} not initialized (run `pcim init`)")
    if (home / "pcim.db").is_file() and not (home / "master.key").is_file() and not os.environ.get("PCIM_MASTER_KEY"):
        add("master_key", "fail", f"{home / 'master.key'} is missing and the store cannot be read without it: "
                                  "restore it from your backup (no new key is made)")

    # 3. claude CLI binary, version, login, and isolation
    ad = ClaudeCLIAdapter()
    bin_path = shutil.which(ad.binary)
    ver = cli_version(ad.binary) if bin_path else None
    has_login = sanitized_profile() is not None
    iso = ad.identity_isolation()
    if not bin_path:
        st = "fail" if ad.require_isolation else "warn"
        add("claude_cli", st, f"binary {ad.binary!r} not found")
    elif not has_login:
        st = "fail" if ad.require_isolation else "warn"
        add("claude_cli", st, f"v{ver or '?'} at {bin_path}; no stored account profile, so identity isolation "
                              "cannot be applied or verified")
    elif ad.require_isolation and iso["status"] != "verified":
        add("claude_cli", "fail", f"v{ver or '?'} isolation={iso['status']}: {iso['why']}")
    else:
        st = "ok" if iso["status"] == "verified" else "warn"
        add("claude_cli", st, f"v{ver or '?'} isolation={iso['status']} ({iso['why']})")

    # 4. HF model cache & offline state
    hf_dir = (Path.home() / ".cache" / "huggingface" / "hub" /
              ("models--" + DEFAULT_SEMANTIC_MODEL.replace("/", "--")))
    offline_env = os.environ.get("HF_HUB_OFFLINE", "")
    if hf_dir.exists():
        add("hf_cache", "ok", f"cached ({DEFAULT_SEMANTIC_MODEL}); HF_HUB_OFFLINE={offline_env or 'auto'}")
    else:
        add("hf_cache", "warn", f"not cached ({DEFAULT_SEMANTIC_MODEL}); HF_HUB_OFFLINE={offline_env or 'unset'}")

    # 5-8. Embedder, audit chain, authorized workspaces, Stop-all (only if initialized and readable)
    if state_ok and not any(c["name"] == "master_key" for c in checks):
        ci, uid = _open()
        try:
            from .embed import semantic_available
            emb = ci.embedder.name
            if emb.startswith("st:") and not semantic_available():
                add("embedder", "fail", f"this store's memory is embedded with {emb}, but sentence-transformers is not "
                                        "installed: pip install 'contextual-intelligence[semantic]'")
            else:
                add("embedder", "ok", emb)
            audit_ok, audit_err = ci.audit.verify()
            add("audit_chain", "ok" if audit_ok else "fail",
                "verified" if audit_ok else f"verification failed: {audit_err}")
            ws = Workspace(ci)
            projects = ws.list_projects(uid)
            bad_ws = []
            ws_count = 0
            for p in projects:
                w = ws.briefing(uid, p.id).get("workspace")
                if w:
                    ws_count += 1
                    if not Path(w["root"]).is_dir():
                        bad_ws.append(w["root"])
            if bad_ws:
                add("workspaces", "warn", f"missing workspace dirs: {', '.join(bad_ws)}")
            else:
                add("workspaces", "ok", f"{ws_count} authorized workspace(s) present")
            halted = ws.is_halted(uid)
            add("stop_all", "fail" if halted else "ok",
                "halted (run `ci resume` to clear)" if halted else "clear")
        finally:
            ci.store.close()

    blocking = any(c["status"] == "fail" for c in checks)
    if a.as_json:
        print(json.dumps({"ok": not blocking, "checks": checks}, indent=1))
    else:
        for c in checks:
            tag = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[c["status"]]
            print(f"[{tag}] {c['name']}: {c['detail']}")
    return 1 if blocking else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("help", "-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] in ("-V", "--version"):  # not bare "version": a request may start with that word
        from . import __version__
        print(f"ci {__version__}")
        return 0
    cmd = argv[0]
    table = {"ask": cmd_ask, "route": cmd_route, "mode": cmd_mode, "prefer": cmd_prefer, "models": cmd_models,
             "outcome": cmd_outcome, "practice": cmd_practice, "skills": cmd_skills, "scorecard": cmd_scorecard,
             "weekly": cmd_weekly, "status": cmd_status, "challenge": cmd_challenge, "plan": cmd_plan,
             "mesh": cmd_mesh, "twin": cmd_twin, "audit": cmd_audit, "learn": cmd_learn,
             "execute": cmd_execute, "approve": cmd_approve, "deny": cmd_deny, "halt": cmd_halt,
             "resume": cmd_resume, "doctor": cmd_doctor, "do": cmd_do, "undo": cmd_undo}
    from .embed import EmbedderUnavailable
    try:
        if cmd in table:
            return table[cmd](argv[1:])
        return cmd_ask(argv)
    except EmbedderUnavailable as e:  # a setup problem, said plainly rather than as a traceback
        print(f"ci: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
