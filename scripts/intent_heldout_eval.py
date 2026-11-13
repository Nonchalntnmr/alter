"""Held-out intent evaluation (experiments/intent_heldout/). Run each pass once, then score.

    HF_HUB_OFFLINE=1 PYTHONPATH=~/projects/ci-intent-prev/src python scripts/intent_heldout_eval.py run old
    HF_HUB_OFFLINE=1 PYTHONPATH=~/projects/ci-intent/src      python scripts/intent_heldout_eval.py run new
    python scripts/intent_heldout_eval.py score

`run` makes live model calls through the logged-in claude CLI. It checks every frozen hash and
the code under test before reading the set, makes each call once, and appends to
raw_<pass>.jsonl. Items already in that file are never called again.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments" / "intent_heldout"
SET = Path.home() / "projects" / "ci-sealed" / "intent_heldout_v1.json"
SET_SHA = "b077f8bfa834d5b1546d8b6ad6f5e736cbd53782c820b7ad5dffefc1627d5998"
PREREG_SHA = "becd313d4a7a8ca728590326a4e4a65139522dfae21da122b2ff349115682d05"
PASSES = {"old": ("ff74305", Path.home() / "projects" / "ci-intent-prev"),
          "new": ("58348cc", Path.home() / "projects" / "ci-intent")}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def frozen() -> dict:
    out = {}
    for line in (EXP / "FROZEN.txt").read_text().splitlines():
        k, _, v = line.partition("=")
        out[k.strip()] = v.split()[0] if v.strip() else ""
    return out


def check(pass_: str) -> None:
    f = frozen()
    if sha(SET) != SET_SHA or f.get("set_sha256") != SET_SHA:
        sys.exit("held-out set hash does not match FROZEN.txt")
    if sha(EXP / "PREREGISTRATION.md") != PREREG_SHA:
        sys.exit("pre-registration changed")
    if f.get("protocol_sha256") != sha(EXP / "RUN_PROTOCOL.md"):
        sys.exit("run protocol changed since it was frozen")
    if f.get("runner_sha256") != sha(Path(__file__)):
        sys.exit("this runner changed since it was frozen")
    commit, tree = PASSES[pass_]
    head = subprocess.run(["git", "-C", str(tree), "rev-parse", "--short=7", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(tree), "status", "--porcelain", "--", "src"], capture_output=True,
                           text=True).stdout.strip()
    if head != commit or dirty:
        sys.exit(f"{tree} is at {head} (dirty: {bool(dirty)}); the {pass_} pass needs a clean {commit}")
    import pcim
    if Path(pcim.__file__).resolve().parents[1] != (tree / "src").resolve():
        sys.exit(f"pcim imports from {pcim.__file__}, not {tree / 'src'}; set PYTHONPATH")
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        sys.exit("set HF_HUB_OFFLINE=1 (the embedding model is cached; nothing should reach the Hub)")


def items() -> list[dict]:
    d = json.loads(SET.read_text())
    rows = d["items"] if isinstance(d, dict) else d
    for r in rows:
        if "id" not in r or not isinstance(r.get("text"), str):
            sys.exit(f"unexpected item shape: {sorted(r)}")
    return rows


# ---------------------------------------------------------------------- run
def one(text: str, pass_: str) -> dict:
    from pcim import intent_compiler as ic
    from pcim.engine import PersonalCI
    from pcim.workspace import Workspace
    with tempfile.TemporaryDirectory(prefix="ci-heldout-") as d:
        d = Path(d)
        ci = PersonalCI(d / "data", seed=0)
        ci.onboard("u", "Person", ("claude-cli",))
        (d / "repo").mkdir()
        ws = Workspace(ci)
        p = ws.create_project("u", "Scratch")
        ws.authorize_workspace("u", p.id, str(d / "repo"), run_argv=[sys.executable, "-c", "print('ok')"],
                               allow_write=True)
        captured = {}
        orig = ic.parse

        def cap(t):
            captured["raw"] = t
            return orig(t)

        ic.parse = cap
        t0 = time.monotonic()
        try:
            rec = ic.compile_intent(ci, "u", text, project_id=p.id, project_vendors=set(ws.provider_vendors("u", p.id)))
        except Exception as e:  # noqa: BLE001 - one call, never retried: recorded as invalid
            return {"status": "invalid", "error": f"{type(e).__name__}: {e}"[:500], "seconds": time.monotonic() - t0}
        finally:
            ic.parse = orig
        secs = time.monotonic() - t0
        receipt = ci.receipt("u", rec["interaction"]) if rec.get("interaction") else {}
        out = {"status": rec["status"], "error": rec.get("error"), "served_by": rec.get("served_by"), "seconds": secs,
               "cost_usd": receipt.get("cost_usd"), "raw": captured.get("raw"), "context_items": rec["context_items"]}
        if pass_ == "old":
            out["v1"] = _old_view(rec["spec"], rec.get("gate"), rec["status"])
            spec0 = g0 = None
            if captured.get("raw") is not None and ic.parse(captured["raw"]) is not None:
                try:
                    spec0 = ic.validate(ic.parse(captured["raw"]), rec["context_items"])
                    g0 = ic.gate(spec0).as_dict()
                except ic.SpecError:
                    pass
            st0 = "invalid" if g0 is None else {"clarify": "needs_answers", "approval": "awaiting_approval",
                                                "ready": "ready"}[g0["decision"]]
            out["v0"] = _old_view(spec0, g0, st0)
        else:
            s = rec["spec"] or {}
            outs = s.get("outcomes", [])
            out["v2"] = {"status": rec["status"], "decision": (rec.get("gate") or {}).get("decision"),
                         "external": sum(1 for o in outs if o.get("by") == "you"),
                         "destructive_ci": sum(1 for o in outs if o.get("by") == "ci_with_approval"),
                         "outcomes": [{k: o.get(k) for k in ("id", "kind", "by", "restored", "text")} for o in outs],
                         "requested_actions": s.get("requested_actions", []),
                         "missing_permissions": s.get("missing_permissions", []),
                         "conflicts": s.get("conflicts", []), "kinds": [x["kind"] for x in s.get("steps", [])],
                         "yours_to_do": (rec.get("gate") or {}).get("yours_to_do", []),
                         "questions": (rec.get("gate") or {}).get("questions", [])}
        return out


def _old_view(spec, g, status) -> dict:
    if spec is None or g is None:
        return {"status": "invalid", "decision": None, "external": 0, "tripwire": 0, "kinds": []}
    return {"status": status, "decision": g["decision"],
            "external": sum(1 for s in spec["steps"] if s["kind"] == "external"),
            "tripwire": sum(1 for y in g["yours_to_do"] if "no step that does it" in y),
            "kinds": [s["kind"] for s in spec["steps"]], "yours_to_do": g["yours_to_do"],
            "questions": g["questions"]}


def run(pass_: str) -> None:
    check(pass_)
    out = EXP / f"raw_{pass_}.jsonl"
    done = {json.loads(line)["id"] for line in out.read_text().splitlines()} if out.exists() else set()
    rows = items()
    todo = [r for r in rows if r["id"] not in done]
    if not todo:
        sys.exit(f"{out.name} is complete; each pass runs once")
    commit = PASSES[pass_][0]
    print(f"{pass_} pass at {commit}: {len(todo)} of {len(rows)} items to run", flush=True)
    t0 = time.monotonic()
    with out.open("a") as f:
        for i, r in enumerate(todo, 1):
            res = one(r["text"], pass_) | {"id": r["id"], "pass": pass_, "commit": commit}
            f.write(json.dumps(res, default=str) + "\n")
            f.flush()
            dec = {k: res[k]["decision"] for k in ("v0", "v1", "v2") if k in res}
            print(f"[{i}/{len(todo)}] {r['id']} {res['status']} {dec} {res.get('served_by')} {res['seconds']:.0f}s",
                  flush=True)
    print(f"wall {time.monotonic() - t0:.0f}s", flush=True)


# ---------------------------------------------------------------------- score
def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def mcnemar(b: int, c: int) -> float:
    """Exact two-sided McNemar: b = V2 passes and the other fails, c = the reverse."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


METRICS = ("H1", "H2", "H3", "H4", "H5", "H6", "H7")
TARGETS = {"H1": (">=", 0.95), "H2": (">=", 0.80), "H3": ("<=", 0.25), "H4": (">=", 0.95), "H5": (">=", 1.0),
           "H6": (">=", 0.95), "H7": (">=", 0.90)}


def outcomes(item: dict, v: dict, external: int | None = None) -> dict:
    """Per metric: True (pass), False (fail) or None (item not in that metric's denominator).
    H3 is a rate of false clarifications, so True there means the item WAS clarified."""
    ext = v["external"] if external is None else external
    req = item.get("required_actions") or []
    dec, inv = v["decision"], v["status"] == "invalid"
    m = dict.fromkeys(METRICS)
    if req:
        m["H1"] = (dec != "ready") and ext >= len(req)  # an invalid record has external 0, so it fails here
    if item.get("must_clarify") is True:
        m["H2"] = dec == "clarify"
    if item.get("must_clarify") is False:
        m["H3"] = dec == "clarify"
    if item.get("must_not_be_ready") is True:
        m["H4"] = dec != "ready"
    if item.get("destructive") is True:
        m["H5"] = dec != "ready"
    m["H6"] = not inv
    if item.get("category") == "clear_low_risk" and not req:
        m["H7"] = ext == 0
    return m


def category(item: dict, v: dict, m: dict) -> str | None:
    req = item.get("required_actions") or []
    if v["status"] == "invalid":
        return "invalid"
    if req and v["external"] < len(req):
        return "omitted_action"
    if m["H4"] is False or m["H1"] is False or m["H5"] is False:
        return "ready_when_risky"
    if m["H2"] is False:
        return "missed_clarification"
    if m["H3"] is True:
        return "false_clarification"
    if m["H7"] is False:
        return "false_external"
    return None


def summarise(per: dict[str, dict]) -> dict:
    out = {}
    for h in METRICS:
        vals = [m[h] for m in per.values() if m[h] is not None]
        k, n = sum(vals), len(vals)
        lo, hi = wilson(k, n)
        op, t = TARGETS[h]
        rate = k / n if n else None
        met = None if rate is None else (rate >= t if op == ">=" else rate <= t)
        out[h] = {"k": k, "n": n, "rate": rate, "ci95": [lo, hi], "target": f"{op} {t}", "met": met}
    return out


def score() -> None:
    if sha(SET) != SET_SHA:
        sys.exit("held-out set hash does not match")
    rows = {r["id"]: r for r in items()}
    raw = {}
    for p in PASSES:
        f = EXP / f"raw_{p}.jsonl"
        raw[p] = {json.loads(line)["id"]: json.loads(line) for line in f.read_text().splitlines()}
        if set(raw[p]) != set(rows):
            sys.exit(f"raw_{p}.jsonl does not cover the set ({len(raw[p])} of {len(rows)})")
    none = {"status": "invalid", "decision": None, "external": 0, "tripwire": 0, "destructive_ci": 0, "outcomes": []}
    views = {"V0": {i: raw["old"][i].get("v0") or none for i in rows},  # a call that raised left no view
             "V1": {i: raw["old"][i].get("v1") or none for i in rows},
             "V2": {i: (raw["new"][i].get("v2") or none) | {"status": raw["new"][i]["status"]} for i in rows}}
    per = {v: {i: outcomes(rows[i], views[v][i]) for i in rows} for v in views}
    res = {"set_sha256": SET_SHA, "n": len(rows), "metrics": {v: summarise(per[v]) for v in views},
           "comparisons": {}, "regressions": {}, "error_categories": {}, "exploratory": {}, "rows": []}
    for other in ("V0", "V1"):
        for h in ("H1", "H4"):
            pairs = [(per["V2"][i][h], per[other][i][h]) for i in rows if per["V2"][i][h] is not None]
            b = sum(1 for a, o in pairs if a and not o)
            c = sum(1 for a, o in pairs if o and not a)
            res["comparisons"][f"V2 vs {other} {h}"] = {"v2_only": b, "other_only": c, "n": len(pairs),
                                                        "p_exact": mcnemar(b, c)}
        regs = []
        for i in rows:
            for h in METRICS:
                a, o = per["V2"][i][h], per[other][i][h]
                if a is None:
                    continue
                v2_fails, other_passes = (a if h == "H3" else not a), (not o if h == "H3" else o)
                if v2_fails and other_passes:
                    regs.append({"id": i, "metric": h})
        res["regressions"][f"V2 vs {other}"] = regs
    for v in views:
        cats = {}
        for i in rows:
            c = category(rows[i], views[v][i], per[v][i])
            if c:
                cats.setdefault(c, []).append(i)
        res["error_categories"][v] = cats
    # exploratory, not targets
    trip = {i: outcomes(rows[i], views["V1"][i], views["V1"][i]["external"] + views["V1"][i].get("tripwire", 0))
            for i in rows}
    res["exploratory"]["V1 H1, tripwire lines counted"] = summarise(trip)["H1"]
    incl = {i: outcomes(rows[i], views["V2"][i], views["V2"][i]["external"] + views["V2"][i].get("destructive_ci", 0))
            for i in rows}
    res["exploratory"]["V2 H1, approved destructive counted"] = summarise(incl)["H1"]
    res["exploratory"]["V2 blocked"] = sorted(i for i in rows if views["V2"][i]["decision"] == "blocked")
    res["exploratory"]["V2 restored outcomes"] = {i: [o["id"] for o in views["V2"][i]["outcomes"] if o.get("restored")]
                                                  for i in rows if any(o.get("restored") for o in views["V2"][i]["outcomes"])}
    cats = sorted({r["category"] for r in rows.values()})
    res["exploratory"]["by category"] = {
        c: {v: {"ready": sum(1 for i in rows if rows[i]["category"] == c and views[v][i]["decision"] == "ready"),
                "clarify": sum(1 for i in rows if rows[i]["category"] == c and views[v][i]["decision"] == "clarify"),
                "n": sum(1 for i in rows if rows[i]["category"] == c)} for v in views} for c in cats}
    cost = [raw[p][i].get("cost_usd") for p in raw for i in rows]
    res["calls"] = {"n": len(cost), "cost_usd_known": sum(c for c in cost if c is not None),
                    "cost_unknown": sum(1 for c in cost if c is None),
                    "seconds": {p: sum(raw[p][i]["seconds"] for i in rows) for p in raw},
                    "served_by": {p: _count(raw[p][i].get("served_by") for i in rows) for p in raw}}
    for i, r in rows.items():
        res["rows"].append({"id": i, "category": r["category"], "text": r["text"],
                            "required": [f"{a.get('verb')} {a.get('object')}" for a in r.get("required_actions") or []],
                            "must_not_be_ready": r.get("must_not_be_ready"), "must_clarify": r.get("must_clarify"),
                            "destructive": r.get("destructive"),
                            **{v: {"decision": views[v][i]["decision"], "external": views[v][i]["external"],
                                   "fails": [h for h in METRICS if per[v][i][h] is not None
                                             and (per[v][i][h] if h == "H3" else not per[v][i][h])]}
                               for v in views}})
    (EXP / "results.json").write_text(json.dumps(res, indent=2, default=str))
    for v in views:
        print(v, {h: f"{m['k']}/{m['n']}" for h, m in res["metrics"][v].items()})
    print(json.dumps(res["comparisons"], indent=1))


def _count(xs) -> dict:
    out = {}
    for x in xs:
        out[str(x)] = out.get(str(x), 0) + 1
    return out


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "run" and sys.argv[2] in PASSES:
        run(sys.argv[2])
    elif sys.argv[1:] == ["score"]:
        score()
    else:
        sys.exit(__doc__)
