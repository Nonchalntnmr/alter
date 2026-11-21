"""Re-run, once, the rc2 evaluation records lost to the environment (experiments/eval_v4/DEVIATION.md).

    HF_HUB_OFFLINE=1 PCIM_CLI_REQUIRE_ISOLATION=1 PYTHONPATH=~/projects/alter-rc/src \\
        caffeinate -i python scripts/eval_v4_rerun.py intent v32
    HF_HUB_OFFLINE=1 PCIM_CLI_REQUIRE_ISOLATION=1 PYTHONPATH=~/projects/alter-rc2/src \\
        caffeinate -i python scripts/eval_v4_rerun.py paired
    python scripts/eval_v4_rerun.py list        # what would be re-run; calls nothing
    HF_HUB_OFFLINE=1 PCIM_CLI_REQUIRE_ISOLATION=1 PYTHONPATH=~/projects/alter-rc2/src \
        caffeinate -i python scripts/eval_v4_rerun.py resume-paired
        # the paired tasks not yet run, one at a time through the frozen run(), each started
        # only while the lid is open and the battery is not low

It imports the frozen harnesses unchanged and uses their own check() and per-item functions.
- A record is re-run only if its error matches a loss signature in DEVIATION.md and it is the
  first attempt.
- Nothing runs while the laptop's lid is closed.
- The script's own SHA-256 and every re-run go into experiments/eval_v4/RERUN.txt.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "experiments" / "eval_v4" / "RERUN.txt"
SIGNATURES = ("went to sleep", "is still loading your organization's settings")


def lost(r: dict) -> bool:
    e = r.get("error") or ""
    if any(s in e for s in SIGNATURES):
        return True
    return e.startswith("RoutingError") and r.get("calls", 0) == 0 and not r.get("cost_usd")


def lid_closed() -> bool:
    out = subprocess.run(["ioreg", "-r", "-k", "AppleClamshellState", "-d", "4"], capture_output=True, text=True).stdout
    return '"AppleClamshellState" = Yes' in out


def battery_low() -> bool:
    out = subprocess.run(["pmset", "-g", "ps"], capture_output=True, text=True).stdout
    import re as _re
    m = _re.search(r"(\d+)%; discharging", out)
    return bool(m) and int(m.group(1)) < 25


def wait_until_safe() -> None:
    """Wait while the lid is closed or the battery is low and draining."""
    said = False
    while lid_closed() or battery_low():
        if not said:
            print(f"{time.strftime('%H:%M:%S')} waiting: lid closed or battery low", flush=True)
            said = True
        time.sleep(30)


def note(line: str) -> None:
    with LOG.open("a") as f:
        f.write(line + "\n")


def last_by(path: Path, key) -> dict:
    out: dict = {}
    for line in path.read_text().splitlines():
        r = json.loads(line)
        out.setdefault(key(r), []).append(r)
    return out


def intent_todo(pass_: str) -> list[str]:
    import eval_v4_intent as h
    recs = last_by(h.EXP / f"raw_{pass_}.jsonl", lambda r: r["id"])
    return [i for i, v in recs.items() if len(v) == 1 and lost(v[-1])]


def paired_todo() -> list[str]:
    import eval_v4_paired as h
    recs = last_by(h.EXP / "raw.jsonl", lambda r: (r["id"], r["arm"]))
    return [i for (i, arm), v in recs.items() if arm == "A" and len(v) == 1 and lost(v[-1])]


def main(argv: list[str]) -> int:
    if argv[:1] == ["list"]:
        print("intent v32:", intent_todo("v32"), " intent v33:", intent_todo("v33"), " paired A:", paired_todo())
        return 0
    me = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if argv[:1] == ["resume-paired"]:
        import eval_v4_paired as h
        h.check(["A", "R"])
        every = h.tasks()
        note(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} rerun.py {me} resume-paired")
        for t in every:
            raw = h.EXP / "raw.jsonl"
            have = {(r["id"], r["arm"]) for r in map(json.loads, raw.read_text().splitlines())} if raw.exists() else set()
            if (t["id"], "A") in have and (t["id"], "R") in have:
                continue
            wait_until_safe()
            h.tasks = lambda t=t: [t]  # the frozen run(), on this one task
            try:
                h.run(["A", "R"])
            except SystemExit as e:
                print(f"{t['id']}: {e}", flush=True)
            finally:
                h.tasks = lambda: every
        return 0
    if lid_closed():
        sys.exit("the lid is closed: the machine will sleep mid-call. Open it (and plug in) first.")
    if argv[:1] == ["intent"] and len(argv) == 2:
        import eval_v4_intent as h
        pass_ = argv[1]
        h.check(pass_)
        rows = {r["id"]: r for r in h.items()}
        todo = intent_todo(pass_)
        note(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} rerun.py {me} intent {pass_}: {todo}")
        commit = h.frozen()[f"code_{pass_}"]
        with (h.EXP / f"raw_{pass_}.jsonl").open("a") as f:
            for i in todo:
                res = h.one(rows[i]) | {"id": i, "pass": pass_, "commit": commit, "attempt": 2,
                                        "rerun_of": "lost to the environment (DEVIATION.md)"}
                f.write(json.dumps(res, default=str) + "\n")
                f.flush()
                print(f"{i} rerun first={(res['first'] or {}).get('decision')} ${res['cost_usd']:.3f}"
                      + (f" ERR {res['error'][:80]}" if res["error"] else ""), flush=True)
        return 0
    if argv[:1] == ["paired"]:
        import eval_v4_paired as h
        h.check(["A", "R"])
        rows = {t["id"]: t for t in h.tasks()}
        todo = paired_todo()
        note(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} rerun.py {me} paired A: {todo}")
        with (h.EXP / "raw.jsonl").open("a") as f:
            for i in todo:
                with tempfile.TemporaryDirectory(prefix=f"ci-v4-{i}-A-rerun-") as d:
                    r = h.arm_a(rows[i], Path(d))
                r |= {"attempt": 2, "rerun_of": "lost to the environment (DEVIATION.md)"}
                f.write(json.dumps(r, default=str) + "\n")
                f.flush()
                print(f"{i} A rerun verified={r['verified']} calls={r['calls']} ${r['cost_usd']:.3f}"
                      + (f" ERR {r['error'][:80]}" if r["error"] else ""), flush=True)
        return 0
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
