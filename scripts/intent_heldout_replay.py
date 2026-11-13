"""POST HOC, not a registered analysis: replay a compiler version's deterministic layer on the
held-out run's stored V2 model replies.

The new-format pass stored each raw model reply. Everything after the model call (parse, validate
with intent preservation, missing permissions, gate) is deterministic. So a later version of that
layer can be scored on the *same replies* without new model calls, the way V0 and V1 were scored
from the same old-format replies.

The set has been read since the run, so this is a regression check on a seen set, not held-out
evidence. First it replays with the code the run used (58348cc) and requires an exact match with the
recorded decisions and external counts, which validates the replay. Then it replays with the code on
PYTHONPATH.

    PYTHONPATH=<src to test> python scripts/intent_heldout_replay.py <label> <code description>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments" / "intent_heldout"
SET = Path.home() / "projects" / "ci-sealed" / "intent_heldout_v1.json"
WORKSPACE = {"can_run_tests": True, "can_write": True}  # the run's throwaway project: tests configured, writes on


def view(ic, text: str, raw: str | None, context_items: list) -> dict:
    text = " ".join(str(text).split())
    parsed = ic.parse(raw) if raw is not None else None
    if parsed is None:
        return {"status": "invalid", "decision": None, "external": 0, "restored": []}
    try:
        spec = ic.validate(parsed, context_items, request=text)
        spec["missing_permissions"] = ic.missing_permissions(spec, WORKSPACE)
        g = ic.gate(spec, text)
    except ic.SpecError:
        return {"status": "invalid", "decision": None, "external": 0, "restored": []}
    outs = spec.get("outcomes", [])
    return {"decision": g.decision, "external": sum(1 for o in outs if o.get("by") == "you"),
            "restored": [o["text"] for o in outs if o.get("restored")]}


def main() -> None:
    from pcim import intent_compiler as ic
    label = sys.argv[1] if len(sys.argv) > 1 else "replay"
    code = sys.argv[2] if len(sys.argv) > 2 else "unlabelled"  # a commit or description, not a local path
    d = json.loads(SET.read_text())
    items = {r["id"]: r for r in (d["items"] if isinstance(d, dict) else d)}
    raw = [json.loads(line) for line in (EXP / "raw_new.jsonl").read_text().splitlines()]
    out, diffs = {}, []
    for r in raw:
        v = view(ic, items[r["id"]]["text"], r.get("raw"), r.get("context_items", []))
        rec = r.get("v2") or {"decision": None, "external": 0}
        out[r["id"]] = v
        if (v["decision"], v["external"]) != (rec.get("decision"), rec.get("external")):
            diffs.append({"id": r["id"], "recorded": {"decision": rec.get("decision"), "external": rec.get("external"),
                                                      "restored": [o["text"] for o in rec.get("outcomes", [])
                                                                   if o.get("restored")]},
                          label: v})
    print(json.dumps({"code": code, "n": len(raw), "differs_from_recorded_v2": diffs}, indent=1))
    (EXP / f"replay_{label}.json").write_text(json.dumps({"code": code, "items": out, "diffs": diffs},
                                                         indent=1))


if __name__ == "__main__":
    main()
