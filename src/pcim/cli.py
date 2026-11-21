"""`pcim` — command line for Personal CI.

    pcim init --user nishanth --providers mock anthropic
    pcim remember "I prefer concise answers" --kind preference
    pcim consent grant ingest:notes store
    pcim ingest notes.md --source ingest:notes
    pcim ask "plan my week" --provider anthropic
    pcim feedback <interaction_id> --rating 5
    pcim dna | pcim metrics | pcim export > me.json | pcim serve

Data lives in $PCIM_HOME (default ~/.pcim): pcim.db (encrypted per record), master.key
(0600), token (0600). Nothing there belongs in version control.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .engine import PersonalCI
from .types import KINDS, Sensitivity


def _home() -> Path:
    return Path(os.environ.get("PCIM_HOME", Path.home() / ".pcim"))


def _open_store(home: Path) -> PersonalCI:
    from .sovereignty.crypto import MasterKeyMissing
    try:
        return PersonalCI(home)
    except MasterKeyMissing as e:
        sys.exit(f"pcim: {e}")


def _ci() -> tuple[PersonalCI, str]:
    home = _home()
    tok = home / "token"
    if not tok.exists():
        sys.exit(f"no user yet — run `pcim init` (PCIM_HOME={home})")
    ci = _open_store(home)
    uid = ci.user_for_token(tok.read_text().strip())
    if uid is None:
        sys.exit("token in PCIM_HOME does not match any user")
    return ci, uid


def _print(x) -> None:
    print(json.dumps(x, indent=2, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pcim", description="Personal CI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--user", required=True); p.add_argument("--name", default="")
    p.add_argument("--providers", nargs="+", default=["mock"])
    p = sub.add_parser("remember"); p.add_argument("text"); p.add_argument("--kind", default="fact", choices=KINDS)
    p.add_argument("--key"); p.add_argument("--value"); p.add_argument("--pin", action="store_true")
    p.add_argument("--sensitivity", default="personal", choices=[s.value for s in Sensitivity])
    p = sub.add_parser("ingest"); p.add_argument("path"); p.add_argument("--source", required=True)
    p.add_argument("--sensitivity", default="personal", choices=[s.value for s in Sensitivity])
    p = sub.add_parser("consent"); cs = p.add_subparsers(dest="action", required=True)
    g = cs.add_parser("grant"); g.add_argument("source"); g.add_argument("purpose", choices=["store", "egress"])
    g.add_argument("--provider", default="*"); g.add_argument("--max-sensitivity", default="personal")
    r = cs.add_parser("revoke"); r.add_argument("grant_id"); r.add_argument("--delete-stored", action="store_true")
    cs.add_parser("list")
    p = sub.add_parser("memory"); p.add_argument("--history", action="store_true")
    p = sub.add_parser("delete"); p.add_argument("item_id")
    p = sub.add_parser("confirm", help="confirm an imported or agent-written item; promotes its proposed kind")
    p.add_argument("item_id"); p.add_argument("--keep-kind", action="store_true")
    p = sub.add_parser("ask"); p.add_argument("task"); p.add_argument("--provider", default="mock")
    p.add_argument("--model"); p.add_argument("--budget", type=int); p.add_argument("--mode")
    p.add_argument("--receipt", action="store_true")
    p = sub.add_parser("feedback"); p.add_argument("interaction_id"); p.add_argument("--rating", type=int)
    p.add_argument("--success", choices=["yes", "no"]); p.add_argument("--corrected", action="store_true")
    for name in ("dna", "metrics", "export", "audit"):
        sub.add_parser(name)
    p = sub.add_parser("serve"); p.add_argument("--port", type=int, default=8765); p.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)

    if a.cmd == "init":
        home = _home()
        ci = _open_store(home)
        out = ci.onboard(a.user, a.name, tuple(a.providers))
        fd = os.open(home / "token", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(out["api_token"])
        print(f"created {a.user} in {home}\nAPI token (also saved to {home/'token'}): {out['api_token']}\n"
              f"Granted: egress of your own statements to {', '.join(a.providers)}. Nothing else.")
        return 0
    if a.cmd == "serve":
        import uvicorn
        from .api import create_app
        home = _home()
        print(f"Personal CI on http://{a.host}:{a.port}  (data: {home})")
        uvicorn.run(create_app(_open_store(home)), host=a.host, port=a.port, log_level="warning")
        return 0

    ci, uid = _ci()
    if a.cmd == "remember":
        res = ci.remember(uid, a.text, a.kind, key=a.key, value=a.value, pinned=a.pin,
                          sensitivity=Sensitivity(a.sensitivity))
        _print({"id": res.item.id, "kind": res.item.kind, "superseded": res.superseded, "conflicts": res.conflicts,
                "gated_from": res.gated_from, "flags": res.item.flags})
    elif a.cmd == "ingest":
        rs = ci.ingest_text(uid, Path(a.path).read_text(), a.source, Path(a.path).name, Sensitivity(a.sensitivity))
        _print({"items": len(rs), "flagged_instructions": [r.item.id for r in rs if "contains_instructions" in r.item.flags]})
    elif a.cmd == "consent":
        if a.action == "grant":
            g = ci.consent(uid, a.source, a.purpose, a.provider, Sensitivity(a.max_sensitivity))
            _print({"grant": g.id, "source": g.source, "purpose": g.purpose, "provider": g.provider})
        elif a.action == "revoke":
            _print(ci.revoke(uid, a.grant_id, a.delete_stored))
        else:
            _print([{"id": g.id, "source": g.source, "purpose": g.purpose, "provider": g.provider,
                     "max": g.max_sensitivity.value, "revoked": g.revoked_at} for g in ci.ledger.grants(uid)])
    elif a.cmd == "memory":
        items = ci.memory.all(uid) if a.history else ci.memory.current(uid)
        for i in items:
            print(f"{i.id}  [{i.kind}{' pinned' if i.pinned else ''}] {i.content[:100]}  ({i.source}, {i.trust.value})")
    elif a.cmd == "confirm":
        it = ci.memory.confirm(uid, a.item_id, promote=not a.keep_kind)
        _print({"id": it.id, "kind": it.kind, "trust": it.trust.value})
    elif a.cmd == "delete":
        _print({"deleted": ci.memory.delete(uid, a.item_id)})
    elif a.cmd == "ask":
        r = ci.ask(uid, a.task, a.provider, a.model, a.budget, a.mode)
        print(r.text)
        print(f"\n— interaction {r.interaction_id} · {len(r.receipt['sent'])} notes sent · "
              f"{len(r.receipt['withheld_by_egress'])} withheld · cost "
              f"{'unknown' if r.receipt['cost_usd'] is None else '$%.5f' % r.receipt['cost_usd']}")
        if a.receipt:
            _print(r.receipt)
    elif a.cmd == "feedback":
        sig = {}
        if a.rating:
            sig["rating"] = a.rating
        if a.success:
            sig["task_success"] = a.success == "yes"
        if a.corrected:
            sig["corrected"] = True
        _print({"reward": ci.feedback(uid, a.interaction_id, **sig)})
    elif a.cmd == "dna":
        _print(ci.dna(uid))
    elif a.cmd == "metrics":
        _print(ci.metrics(uid))
    elif a.cmd == "export":
        _print(ci.export(uid))
    elif a.cmd == "audit":
        ok, bad = ci.audit.verify()
        _print({"chain_ok": ok, "first_bad_seq": bad, "head": ci.audit.head(), "entries": ci.audit.entries(uid)[-50:]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
