"""Train the learned routing estimator (src/pcim/routing_learned.py) and report its numbers.

    HF_HUB_OFFLINE=1 PYTHONPATH=src python experiments/routing/train_learned.py              # retrain + write + report
    HF_HUB_OFFLINE=1 PYTHONPATH=src python experiments/routing/train_learned.py --check      # retrain, compare bytes
    HF_HUB_OFFLINE=1 PYTHONPATH=src python experiments/routing/train_learned.py --evaluate F # score the shipped model

Offline and deterministic: no model call, no network (the MiniLM model must already be in the
Hugging Face cache). Retraining on the same machine and library versions reproduces
src/pcim/data/routing_model.json byte for byte (`--check`); another machine may differ in the
last bits of the sentence embeddings.

What is measured where:
  - train.json (395, written for this estimator): nested 5-fold CV x 3 repeats. The choice of L2,
    the temperature and the escalation threshold happen inside each outer fold, so the numbers
    include those choices. The model family (logistic regression on MiniLM + hashing features)
    was chosen beforehand by plain CV on the same file, so they are slightly optimistic.
  - dataset.json (36): the router's development set; its author wrote the lexical estimator
    with it in view (36 / 36 is leakage) and this estimator's author read it before writing
    train.json. A regression check, not a measurement.
  - `--evaluate FILE`: the one measurement that counts, on a sealed set from another author.
    Prints aggregates only (no task text) and the preregistered targets below.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import numpy as np  # noqa: E402

from pcim import routing_learned as rl  # noqa: E402

HERE = Path(__file__).resolve().parent
TRAIN, DEV = HERE / "train.json", HERE / "dataset.json"

# Preregistered before any sealed run (2026-11-12), from the nested-CV numbers in the artifact.
# Metrics are for the primary head, escalated tiers, no balanced floor (the raw estimate), unless
# noted. "expected" is the CV point estimate; "target" is the pass line, set below it because a
# set from another author will be harder than CV on this author's own writing.
# For reference, the lexical estimator on the earlier held-out set: exact 0.375, under-routed 0.45,
# deep recall 0.30. Cost: under routing's token estimates even oracle routing costs about 0.72 of
# always-Opus on train.json's tier mix, so a 40% saving is out of reach for any router here.
PREREGISTERED = {
    "under_routed": {"expected": 0.032, "target_at_most": 0.10},
    "deep_recall": {"expected": 0.932, "target_at_least": 0.80},
    "exact": {"expected": 0.689, "target_at_least": 0.55},
    "cost_vs_always_opus_with_balanced_floor": {"expected": 0.823, "target_at_most": 0.90},
}


def score(art: dict, tasks: list[dict], head: str, st) -> dict:
    texts = [t["text"] for t in tasks]
    y = np.array([t["tier"] for t in tasks]) - 1
    out = [rl.assess(x, embedder=st, artifact=art, head=head) for x in texts]
    if head != rl.FALLBACK:
        assert all(a.head == rl.PRIMARY for a in out), "the sentence model did not load"
    tiers = np.array([a.tier for a in out]) - 1
    pred = np.array([a.predicted for a in out]) - 1
    opus = rl.estimated_cost(texts, np.full(len(y), 3), y + 1)
    conf = np.array([a.confidence for a in out])
    return {"argmax": rl.route_metrics(y, pred), "escalated": rl.route_metrics(y, tiers),
            "escalated_balanced_floor": rl.route_metrics(y, np.maximum(tiers, 1)),
            "escalations": int(sum(a.escalated for a in out)), "low_confidence": int(sum(a.low_confidence for a in out)),
            "mean_confidence": round(float(conf.mean()), 3), "argmax_accuracy": round(float(np.mean(pred == y)), 3),
            "cost_vs_always_opus": round(rl.estimated_cost(texts, tiers + 1, y + 1) / opus, 3),
            "cost_vs_always_opus_with_balanced_floor": round(
                rl.estimated_cost(texts, np.maximum(tiers, 1) + 1, y + 1) / opus, 3),
            "cost_vs_always_opus_oracle_labels": round(rl.estimated_cost(texts, y + 1, y + 1) / opus, 3)}


def lexical(tasks: list[dict]) -> dict:
    from pcim import routing
    y = np.array([t["tier"] for t in tasks]) - 1
    pred = np.array([routing.estimate(t["text"], apply_floor=False).tier for t in tasks]) - 1
    return rl.route_metrics(y, pred)


def build(out: Path) -> dict:
    from pcim.embed import SentenceEmbedder
    st = SentenceEmbedder()
    art = rl.train(TRAIN, embedder=st)
    rl.save(art, out)  # then score what was written, as it will be loaded
    shipped = rl.load(out)
    dev = rl.load_tasks(DEV)
    art["evaluation"]["dev_dataset_json"] = {
        "note": "development set, read before train.json was written; not a measurement",
        rl.PRIMARY: score(shipped, dev, rl.PRIMARY, st), rl.FALLBACK: score(shipped, dev, rl.FALLBACK, st),
        "lexical_raw": lexical(dev)}
    art["evaluation"]["lexical_raw_on_train_json"] = {
        "note": "train.json was written with the lexical markers in view, including buzzword-easy and plain-hard "
                "cases on purpose; not a fair measure of the lexical estimator",
        **lexical(rl.load_tasks(TRAIN))}
    rl.save(art, out)
    return art


def report(art: dict) -> None:
    for head in (rl.PRIMARY, rl.FALLBACK):
        h, ev = art["heads"][head], art["evaluation"][head]
        print(f"\n== {head}  (l2={h['l2']}, T={h['temperature']}, threshold={h['threshold']})")
        for k in ("argmax", "escalated", "escalated_balanced_floor"):
            print(f"  nested CV {k:26s} {ev[k]}")
        print(f"  escalation rate {ev['escalation_rate']}  cost vs always-Opus {ev['cost_vs_always_opus']}")
        print(f"  ECE before T {ev['calibration_before_temperature']['ece_mean']}  after T "
              f"{ev['calibration_after_temperature']['ece_mean']}")
        for row in ev["calibration_after_temperature"]["pooled"]["reliability"]:
            print(f"    {row}")
        print(f"  dev dataset.json {art['evaluation']['dev_dataset_json'][head]}")
    print(f"\n  lexical on dataset.json (leakage) {art['evaluation']['dev_dataset_json']['lexical_raw']}")
    print(f"  lexical on train.json {art['evaluation']['lexical_raw_on_train_json']}")


def evaluate(path: Path) -> None:
    from pcim.embed import SentenceEmbedder
    res = score(rl.load(), rl.load_tasks(path), rl.PRIMARY, SentenceEmbedder())
    print(json.dumps(res, indent=1))
    esc = res["escalated"]
    got = {"under_routed": esc["under_routed"], "deep_recall": esc["deep_recall"], "exact": esc["exact"],
           "cost_vs_always_opus_with_balanced_floor": res["cost_vs_always_opus_with_balanced_floor"]}
    for k, reg in PREREGISTERED.items():
        (bound, line), = [(b, v) for b, v in reg.items() if b.startswith("target")]
        ok = got[k] <= line if bound.endswith("most") else got[k] >= line
        print(f"{k:42s} got {got[k]:<6} expected {reg['expected']:<6} {bound} {line}: {'PASS' if ok else 'FAIL'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="retrain and compare with the shipped artifact")
    ap.add_argument("--evaluate", type=Path, help="score the shipped artifact on a labelled file")
    a = ap.parse_args()
    if a.evaluate:
        return evaluate(a.evaluate)
    if not a.check:
        return report(build(rl.ARTIFACT))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / rl.ARTIFACT.name
        report(build(out))
        same = out.read_bytes() == rl.ARTIFACT.read_bytes()
    print(f"\nreproduced {rl.ARTIFACT.name} byte for byte: {same}")
    sys.exit(0 if same else 1)


if __name__ == "__main__":
    main()
