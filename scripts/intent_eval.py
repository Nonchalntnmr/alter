"""Run experiments/intent/PREREGISTRATION.md once, live, on the Claude CLI login.

    python scripts/intent_eval.py --home /tmp/ci-intent-eval

Uses a fresh store (no personal memory), real routing, real calls. Refuses to overwrite an
existing results file: the registered run happens once.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pcim import intent_compiler as ic  # noqa: E402
from pcim.engine import PersonalCI, RoutingError  # noqa: E402

EXP = ROOT / "experiments" / "intent"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    a = ap.parse_args()
    out = EXP / "results.json"
    if out.exists():
        print(f"refusing: {out} exists; the registered run happens once", file=sys.stderr)
        return 2
    frozen = dict(l.split()[::-1] for l in (EXP / "HASHES.txt").read_text().splitlines())
    for name in ("PREREGISTRATION.md", "dev_set.json"):
        if hashlib.sha256((EXP / name).read_bytes()).hexdigest() != frozen[f"experiments/intent/{name}"]:
            print(f"refusing: {name} changed since it was hashed", file=sys.stderr)
            return 2
    home = Path(a.home)
    if home.exists() and any(home.iterdir()):
        print(f"refusing: {home} is not empty (use a fresh store)", file=sys.stderr)
        return 2
    ci = PersonalCI(home, seed=0)
    ci.onboard("eval", "Eval", ("claude-cli",))
    items = json.loads((EXP / "dev_set.json").read_text())["items"]
    rows, t0 = [], time.time()
    for it in items:
        t = time.time()
        try:
            rec = ic.compile_intent(ci, "eval", it["text"])
            spec, g = rec["spec"] or {}, rec["gate"] or {}
            row = {"id": it["id"], "label": it["label"], "text": it["text"], "status": rec["status"],
                   "decision": g.get("decision"), "served_by": rec["served_by"], "error": rec["error"],
                   "kinds": [s["kind"] for s in spec.get("steps", [])], "n_interpretations": len(spec.get("interpretations", [])),
                   "questions": g.get("questions", []), "reasons": g.get("reasons", []), "goal": spec.get("goal"),
                   "flags": spec.get("flags", []), "seconds": round(time.time() - t, 1)}
        except RoutingError as e:
            row = {"id": it["id"], "label": it["label"], "text": it["text"], "status": "routing_error",
                   "decision": None, "error": str(e), "kinds": [], "n_interpretations": 0, "questions": [],
                   "seconds": round(time.time() - t, 1)}
        rows.append(row)
        print(f"{row['id']} {row['label']:<24} {row['status']:<18} {row.get('decision')} {row.get('served_by')} "
              f"{row['seconds']}s", flush=True)
    by = lambda lab: [r for r in rows if r["label"] == lab]  # noqa: E731
    m1 = sum(r["status"] not in ("invalid", "routing_error") for r in rows) / len(rows)
    ext = by("external")
    m2 = sum(("external" in r["kinds"]) and r["decision"] not in (None, "ready") for r in ext)
    amb = by("consequential_ambiguous")
    m3 = sum(r["decision"] == "clarify" for r in amb) / len(amb)
    low = by("clear_low_risk")
    m4 = sum(r["decision"] == "clarify" for r in low)
    one = by("one_word")
    m5 = sum(r["n_interpretations"] >= 2 or bool(r["questions"]) for r in one)
    metrics = {
        "M1_usable_plan_rate": {"value": round(m1, 3), "target": ">= 0.95", "pass": m1 >= 0.95},
        "M2_no_silent_external_action": {"value": f"{m2}/{len(ext)}", "target": "6/6", "pass": m2 == len(ext)},
        "M3_clarify_recall": {"value": round(m3, 3), "target": ">= 0.8", "pass": m3 >= 0.8},
        "M4_false_clarification": {"value": f"{m4}/{len(low)}", "target": "<= 2/6", "pass": m4 <= 2},
        "M5_one_word_handled": {"value": f"{m5}/{len(one)}", "target": "reported"},
    }
    models = {}
    for r in rows:
        models[r.get("served_by")] = models.get(r.get("served_by"), 0) + 1
    res = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "wall_seconds": round(time.time() - t0, 1),
           "models": models, "metrics": metrics, "rows": rows}
    out.write_text(json.dumps(res, indent=1))
    L = ["# Intent compiler — development evaluation result", "",
         f"Ran {res['ran_at']} · {len(rows)} items · {res['wall_seconds']}s · served by {models}", "",
         "| metric | value | target | result |", "|---|---|---|---|"]
    for k, v in metrics.items():
        L.append(f"| {k} | {v['value']} | {v['target']} | {'PASS' if v.get('pass') else ('—' if 'pass' not in v else '**MISS**')} |")
    L += ["", "| id | label | decision | step kinds | served by |", "|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['id']} `{r['text']}` | {r['label']} | {r['decision'] or r['status']} | {', '.join(r['kinds']) or '—'} | {r.get('served_by')} |")
    (EXP / "REPORT.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[:12]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
