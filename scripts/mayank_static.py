#!/usr/bin/env python3
"""Build MAYANK as a static site you can share: demonstration data only, with no ALTER behind it.

    python scripts/mayank_static.py --out ../mayank-site          # then deploy that folder anywhere static

It seeds a fresh throwaway home with scripts/mayank_demo.py, records some real governed actions in
it, and writes:

    index.html         the page in static mode: read-only, and it says so
    static/*.js        the renderer, unchanged
    data/field.json    the field, exactly as /v1/mayank/field returned it
    data/replay.json   the audit events of the recorded actions, with their timing, replayed as a loop

Nothing is read from ~/.pcim. It refuses to write any file that contains an absolute path, the
throwaway home's path or this machine's user name.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from mayank_demo import Busy, open_home, seed  # noqa: E402
from pcim import mayank  # noqa: E402
from pcim.exchange import ClaimLedger  # noqa: E402
from pcim.governor import Governor  # noqa: E402
from pcim.tasks import TaskQueue  # noqa: E402
from pcim.workspace import Workspace  # noqa: E402

DASH = REPO / "src" / "pcim" / "dashboard"
ASSETS = ("mayank-app.js", "mayank-gl.js", "mayank-forms.js", "mayank-learn.js")


def record(steps: int, pause: float) -> tuple[dict, dict]:
    with tempfile.TemporaryDirectory(prefix="mayank-static-") as tmp:
        home, ci = open_home(Path(tmp) / "home", fresh=True)
        info = seed(home, ci)
        uid = info["uid"]
        start = mayank.audit_seq(ci, uid)
        busy = Busy(home, ci, seed=11)
        for _ in range(steps):
            busy.step()
            time.sleep(pause)
        events, cursor = [], start
        while True:
            got = mayank.events(ci, uid, cursor, wait=0)
            if not got["events"]:
                break
            events += got["events"]
            cursor = got["cursor"]
        ws = Workspace(ci)
        ledger = ClaimLedger(ws)
        try:
            from pcim.run_service import RunService
            runs = RunService(ci)
        except ImportError:
            runs = None
        field = mayank.build(ci, uid, ws=ws, queue=TaskQueue(ws, Governor(ws), claims=ledger), ledger=ledger, runs=runs)
        last = None
        for e in events:
            t = dt.datetime.fromisoformat(e["ts"])
            e["dt"] = round((t - last).total_seconds(), 3) if last else 1.0
            last = t
        replay = {"recorded_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"), "events": events}
        return check(field, str(home)), check(replay, str(home))


def check(obj, home: str):
    """Nothing that names this machine leaves it: absolute paths, the throwaway home, the user name."""
    text = json.dumps(obj)
    bad = [s for s in (home, str(Path(home).resolve()), "/Users/", "/private/", "/home/", getpass.getuser()) if s and s in text]
    if bad:
        raise SystemExit(f"mayank_static: refusing to write data that contains {bad!r}")
    return obj


def build(out: Path, field: dict, replay: dict) -> None:
    if out.exists() and any(out.iterdir()) and not (out / "data" / "field.json").exists():
        raise SystemExit(f"mayank_static: {out} is not empty and is not an earlier build; give an empty folder")
    (out / "static").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(exist_ok=True)
    for name in ASSETS:
        shutil.copyfile(DASH / name, out / "static" / name)
    page = (DASH / "mayank.html").read_text()
    tag = '<script type="module" src="/static/mayank-app.js"></script>'
    assert tag in page
    config = {"field": "/data/field.json", "replay": "/data/replay.json", "recorded": replay["recorded_at"]}
    meta = ('<meta name="description" content="MAYANK: an AI system\'s memory, goals, tasks, hypotheses and skills, '
            'drawn as one explorable field of points. A read-only demonstration with made-up data.">\n'
            '<meta property="og:title" content="MAYANK">\n'
            '<meta property="og:description" content="Intelligence as an explorable field: a read-only demonstration.">\n')
    page = page.replace("<title>MAYANK</title>", "<title>MAYANK</title>\n" + meta)
    page = page.replace(tag, f"<script>window.MAYANK_STATIC = {json.dumps(config)};</script>\n{tag}")
    (out / "index.html").write_text(page)
    (out / "data" / "field.json").write_text(json.dumps(field, separators=(",", ":")))
    (out / "data" / "replay.json").write_text(json.dumps(replay, separators=(",", ":")))
    for f in out.rglob("*"):
        if f.is_file() and f.suffix in (".html", ".js", ".json"):
            check(f.read_text(), "/nonexistent-home")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--steps", type=int, default=36, help="governed actions to record for the replay")
    ap.add_argument("--pause", type=float, default=0.8, help="seconds between recorded actions")
    a = ap.parse_args(argv)
    field, replay = record(a.steps, a.pause)
    build(a.out.expanduser().resolve(), field, replay)
    print(f"built {a.out}: {len(field['entities'])} records, {len(field['links'])} relations, "
          f"{len(replay['events'])} recorded events (all demonstration data)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
