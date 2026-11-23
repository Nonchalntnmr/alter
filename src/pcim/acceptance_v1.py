"""Offline acceptance evidence verifier and matched benchmark manifest.

No model calls or deployment. A receipt is evidence supplied by an independent checker,
not a claim that this module exercised an application. Missing gates stay not-run.
Run: python -m pcim.acceptance_v1 freeze|verify|compare ...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path

from .sovereignty.content import assert_safe_payload

PROMPT = ("Build a production-quality task management application with authentication, persistent storage, "
          "a modern interface, automated tests, and deployment configuration. Interview me only when necessary, "
          "assemble your own engineering team, build the application, test everything, and deliver the finished product.")
PROMPT_SHA256 = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()
GATES = ("F01", "F02", "F03", "F04", "F05", "F06", "F07", "A01", "A02", "R01", "R02", "R03",
         "P01", "S01", "S02", "S03", "L01", "I01", "I02", "M01", "V01")
WEIGHTS = {"task_lifecycle": 25, "authentication": 25, "setup": 15, "tests": 15, "ui": 10, "documentation": 10}


def freeze() -> dict:
    return {"schema": "alter.benchmark.v1", "prompt": PROMPT, "prompt_sha256": PROMPT_SHA256,
            "rubric": dict(WEIGHTS), "gates": list(GATES), "arms": {"alter": {}, "direct": {}},
            "status": "not-run", "trials": [], "limits": {"seconds": None, "tokens": None, "retries": None,
                                                         "cost_usd": None},
            "limitations": ["No live execution or performance measurement recorded."]}


def _number(value, minimum=0):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= minimum


def validate_matched(manifest: dict) -> None:
    assert_safe_payload(manifest)
    if manifest.get("prompt") != PROMPT or manifest.get("prompt_sha256") != PROMPT_SHA256:
        raise ValueError("prompt bytes differ from frozen user prompt")
    if manifest.get("rubric") != WEIGHTS or manifest.get("gates") != list(GATES):
        raise ValueError("rubric/gates changed")
    for key in ("seconds", "tokens", "retries", "cost_usd"):
        if not _number(manifest.get("limits", {}).get(key), 0 if key in ("retries", "cost_usd") else 1):
            raise ValueError("resource ceilings must be registered before execution")
    arms = manifest.get("arms", {})
    for key in ("model", "model_version", "parameters", "starter_sha256", "tools_sha256", "machine",
                "approval_policy", "network_policy", "clarification_script_sha256"):
        a, b = arms.get("alter", {}).get(key), arms.get("direct", {}).get(key)
        if a is None or a == "" or a != b:
            raise ValueError("unmatched arm configuration: " + key)
    for name in ("alter", "direct"):
        if not arms[name].get("revision") or not _number(arms[name].get("concurrency"), 1):
            raise ValueError("arm revision and concurrency are required")
    if arms["direct"]["concurrency"] != 1:
        raise ValueError("direct arm must use a single continuous model worker")


def verify_receipts(root: Path, receipts: list[dict]) -> dict:
    """Validate evidence integrity; does not substitute for independent behavioral review."""
    root = root.resolve()
    results = {gate: {"status": "not-run", "evidence": []} for gate in GATES}
    seen = set()
    for receipt in receipts:
        assert_safe_payload(receipt)
        gate = receipt.get("id")
        if gate not in results or gate in seen:
            raise ValueError("unknown or duplicate acceptance gate")
        seen.add(gate)
        status = receipt.get("status")
        if status not in ("passed", "failed", "not-run", "inconclusive"):
            raise ValueError("invalid gate status")
        evidence = receipt.get("evidence", [])
        if status in ("passed", "failed") and (not evidence or not receipt.get("reviewer") or not receipt.get("check")):
            raise ValueError("meaningful verdict requires independent check, reviewer and evidence")
        for entry in evidence:
            rel = Path(entry["path"])
            p = (root / rel).resolve()
            if rel.is_absolute() or ".." in rel.parts or root not in p.parents or not p.is_file():
                raise ValueError("evidence must be an existing file inside the evidence root")
            data = p.read_bytes()
            assert_safe_payload(data.decode("utf-8", errors="replace"))
            if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                raise ValueError("evidence digest mismatch")
        results[gate] = receipt
    return {"gates": results, "all_gates_passed": all(r["status"] == "passed" for r in results.values()),
            "scope": "receipt integrity only; behavioral verdicts require independent review"}


def compare(manifest: dict) -> dict:
    validate_matched(manifest)
    trials = manifest.get("trials", [])
    paired, used = [], set()
    for pair in trials:
        if not pair.get("id") or pair["id"] in used:
            raise ValueError("trial IDs must be unique")
        used.add(pair["id"])
        row = {"id": pair["id"]}
        for arm in ("alter", "direct"):
            run = pair[arm]
            if not _number(run.get("elapsed_s")) or not _number(run.get("interventions")):
                raise ValueError("elapsed time and interventions must be measured")
            quality = run.get("quality", {})
            if set(quality) != set(WEIGHTS) or any(not _number(v) or v > 4 for v in quality.values()):
                raise ValueError("quality scores must cover rubric on the 0–4 scale")
            tokens = run.get("provider_tokens")
            if tokens is not None and (not isinstance(tokens, dict) or not tokens
                                       or any(not _number(v) for v in tokens.values())):
                raise ValueError("provider token measurements must be nonnegative or unavailable")
            if run.get("completed") not in (True, False) or not run.get("evidence_manifest_sha256"):
                raise ValueError("completion and evidence binding required")
            row[arm] = {"quality": sum(WEIGHTS[k] * quality[k] / 4 for k in WEIGHTS),
                        "elapsed_s": run["elapsed_s"], "completed": run["completed"],
                        "interventions": run["interventions"], "provider_tokens": tokens,
                        "estimated_cost_usd": run.get("estimated_cost_usd"),
                        "known_charge_usd": run.get("known_charge_usd")}
        row["elapsed_delta_s"] = row["alter"]["elapsed_s"] - row["direct"]["elapsed_s"]
        paired.append(row)
    return {"trials": paired, "paired_trials": len(paired), "general_superiority": "inconclusive",
            "median_elapsed_delta_s": statistics.median(r["elapsed_delta_s"] for r in paired) if paired else None,
            "limitations": ["Fewer than three pairs are exploratory." if len(paired) < 3 else
                            "Review per-trial gates, controls, waits, usage and quality before interpreting differences.",
                            "Missing usage/cost is unavailable, never zero; timing does not establish a completed-task win."]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("output", type=Path)
    v = sub.add_parser("verify")
    v.add_argument("root", type=Path)
    v.add_argument("receipts", type=Path)
    c = sub.add_parser("compare")
    c.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    if args.action == "freeze":
        args.output.write_text(json.dumps(freeze(), indent=2) + "\n")
        return 0
    try:
        if args.action == "verify":
            result = verify_receipts(args.root, json.loads(args.receipts.read_text()))
        else:
            result = compare(json.loads(args.manifest.read_text()))
    except (ValueError, KeyError, OSError, TypeError):
        print(json.dumps({"error": "invalid or unverifiable acceptance evidence"}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if args.action == "compare" or result["all_gates_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
