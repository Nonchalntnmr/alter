"""Routing benchmark (offline, deterministic, no model calls).

    PYTHONPATH=src python experiments/routing/run.py            # prints results.json to stdout

1. Requirement accuracy: the raw lexical estimate (no floor) against hand labels.
   dataset.json: written before the estimator by the same session, which then wrote the
   estimator with it in view; a regression check, not a measure. heldout.json: written by a
   separate agent from the rubric alone, hashed before the estimator first ran on it; that
   first run is the registered result. The balanced floor was added after it, so the
   "with_floor_posthoc" row is not a clean measurement of the floor.
2. Estimated cost against fixed-model baselines on a Claude-only machine (the CLI), using the
   router's own token estimates and the pricing table. An estimate, not a bill.
3. Fallback recovery: providers made to fail in rank order; does the request still complete,
   on which model, after how many attempts.

What this cannot show: whether the routed model's *answers* are as good as the baseline's.
That needs live calls graded blind (the E2 harness pattern) and is listed as remaining work.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASET_SHA256 = "92630d77a21f0facda5f2ead99f9f9355b17d5e110f5571ad4ac8838f14eac2c"
HELDOUT_SHA256 = "47f42367e1fdb483bd6b88419d815c858791bc315e66686d00a9ee0ad0901d0c"  # written by a separate agent


def main() -> dict:
    def load(name, expected):
        raw = (HERE / name).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected:
            sys.exit(f"{name} changed since it was registered ({digest})")
        return json.loads(raw)["tasks"], digest
    tasks, digest = load("dataset.json", DATASET_SHA256)
    heldout, heldout_digest = load("heldout.json", HELDOUT_SHA256)

    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "PCIM_LOCAL_MODEL",
              "PCIM_CAPABILITIES"):
        os.environ.pop(k, None)
    tmp = Path(tempfile.mkdtemp())
    stub = tmp / "claude"
    stub.write_text("#!/bin/sh\nexit 1\n")
    stub.chmod(0o755)
    os.environ["PCIM_CLAUDE_BIN"] = str(stub)  # makes the CLI "available"; nothing is ever run
    os.environ.setdefault("PCIM_EMBEDDER", "hashing-512")

    from pcim import routing
    from pcim.adapters.base import ModelResponse, ProviderError, Usage
    from pcim.engine import PersonalCI, RoutingError

    # 1. requirement accuracy
    def score(ts, floor=False):
        confusion = {a: {b: 0 for b in (1, 2, 3)} for a in (1, 2, 3)}
        misses = []
        for t in ts:
            r = routing.estimate(t["text"], apply_floor=floor, estimator="lexical")
            confusion[t["tier"]][r.tier] += 1
            if r.tier != t["tier"]:
                misses.append({"id": t["id"], "label": t["tier"], "estimate": r.tier, "signals": r.signals})
        n = len(ts)
        exact = sum(confusion[a][a] for a in (1, 2, 3))
        under = sum(confusion[a][b] for a in (1, 2, 3) for b in (1, 2, 3) if b < a)
        over = sum(confusion[a][b] for a in (1, 2, 3) for b in (1, 2, 3) if b > a)
        deep_recall = confusion[3][3] / sum(confusion[3].values()) if sum(confusion[3].values()) else None
        return {"n": n, "exact": exact, "accuracy": round(exact / n, 3), "under_routed": under, "over_routed": over,
                "deep_recall": None if deep_recall is None else round(deep_recall, 3),
                "confusion_label_by_estimate": confusion, "misses": misses}
    n = len(tasks)

    # 2. estimated cost vs fixed baselines (Claude CLI only, list prices)
    by_tier_model = {1: "claude-cli:haiku", 2: "claude-cli:sonnet", 3: "claude-cli:opus"}
    cost = {"routed": 0.0, "always_opus": 0.0, "always_sonnet": 0.0, "always_haiku": 0.0, "oracle_label": 0.0}
    for t in tasks:
        p = routing.plan(t["text"], allowed_vendors={"anthropic"})  # default preference: with the floor
        req = p.requirement

        def c(key):
            e = next(e for e in routing.entries() if e.key == key)
            return routing.estimated_cost(e, req.est_input_tokens, routing.OUTPUT_TOKENS[req.tier])
        cost["routed"] += p.chosen.est_cost_usd
        cost["always_opus"] += c("claude-cli:opus")
        cost["always_sonnet"] += c("claude-cli:sonnet")
        cost["always_haiku"] += c("claude-cli:haiku")
        cost["oracle_label"] += c(by_tier_model[t["tier"]])
    cost = {k: round(v, 4) for k, v in cost.items()}

    # 3. fallback recovery
    class Ad:
        can_plan = False

        def __init__(self, model, fail):
            self.model, self.fail, self.calls = model, fail, 0

        def generate(self, prompt, max_tokens=None):
            self.calls += 1
            if self.fail:
                raise ProviderError("simulated outage")
            return ModelResponse("ok", Usage(1, 1), 0.0, 1.0, "end_turn", self.model, "claude-cli")

    recovery = []
    probe = "What does HTTP status 429 mean?"
    order = [c.entry.model for c in routing.plan(probe, allowed_vendors={"anthropic"}).candidates]
    for k in range(0, len(order) + 1):
        ci = PersonalCI(tmp / f"r{k}", seed=0)
        ci.onboard("u", providers=("claude-cli",))
        ads = {m: Ad(m, i < k) for i, m in enumerate(order)}
        for m, a in ads.items():
            ci.set_adapter("claude-cli", m, a)
        try:
            r = ci.ask_routed("u", probe)
            recovery.append({"failing": k, "completed": True, "served_by": r.receipt["route"]["served_by"],
                             "attempts": len(r.receipt["route"]["failed_before"]) + 1,
                             "served_meets_requirement": r.receipt["route"]["served_meets"]})
        except RoutingError as e:
            recovery.append({"failing": k, "completed": False, "attempts": len(e.attempts)})

    return {"dataset_sha256": digest, "heldout_sha256": heldout_digest, "n": n,
            "requirement_dev": score(tasks), "requirement_heldout": score(heldout),
            "requirement_heldout_with_floor_posthoc": score(heldout, floor=True),
            "estimated_cost_usd": cost, "fallback_recovery": recovery}


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
