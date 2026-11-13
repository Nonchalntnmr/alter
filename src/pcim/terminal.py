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

Workspace-aware: inside a directory that a project has authorized, requests also use that
project's memory, and only the vendors that project allows. Data and identity are the same as
`pcim` ($PCIM_HOME, default ~/.pcim; `pcim init` creates them). A request whose first word is a
subcommand name needs quotes, or `ci ask …`.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
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
               "weekly", "status", "challenge", "help")
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
        print(f"No CI identity in {home}. Create one first:\n  pcim init --user <name> --providers claude-cli",
              file=sys.stderr)
        raise SystemExit(2)
    ci = PersonalCI(home)
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("help", "-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    table = {"ask": cmd_ask, "route": cmd_route, "mode": cmd_mode, "prefer": cmd_prefer, "models": cmd_models,
             "outcome": cmd_outcome, "practice": cmd_practice, "skills": cmd_skills, "scorecard": cmd_scorecard,
             "weekly": cmd_weekly, "status": cmd_status, "challenge": cmd_challenge}
    if cmd in table:
        return table[cmd](argv[1:])
    return cmd_ask(argv)


if __name__ == "__main__":
    raise SystemExit(main())
