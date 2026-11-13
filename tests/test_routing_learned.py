"""The learned routing estimator (src/pcim/routing_learned.py).

conftest pins PCIM_EMBEDDER to the hashing embedder, so most tests exercise the fallback head.
The one test of the sentence head loads the cached MiniLM model offline and skips without it.
No test makes a network or model call.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from pcim import embed, routing_learned as rl

ROOT = Path(__file__).resolve().parents[1]
TRAIN = ROOT / "experiments" / "routing" / "train.json"
DEV = ROOT / "experiments" / "routing" / "dataset.json"


def stub_artifact(probs, threshold=0.6):
    """A fallback head that ignores the text and returns `probs` (light, standard, deep)."""
    head = {"W": np.zeros((3, 512)), "b": np.log(np.array(probs, dtype=float)), "temperature": 1.0,
            "threshold": threshold}
    return {"heads": {rl.FALLBACK: head}}


def test_the_shipped_artifact_is_weights_only_and_names_how_it_was_trained():
    raw = ROOT.joinpath("src/pcim/data/routing_model.json").read_text()
    art = json.loads(raw)
    assert art["format"] == rl.FORMAT and set(art["heads"]) == {rl.PRIMARY, rl.FALLBACK}
    assert len(raw) < 200_000
    tasks = rl.load_tasks(TRAIN)
    assert art["trained_on"]["n"] == len(tasks) >= 300
    assert art["trained_on"]["sha256"] == rl._sha256(TRAIN)
    assert not [t["id"] for t in tasks if t["text"][:40] in raw]  # no training text is stored
    for name in (rl.PRIMARY, rl.FALLBACK):  # nested-CV numbers and calibration travel with the weights
        ev = art["evaluation"][name]
        assert {"argmax", "escalated", "calibration_after_temperature", "cost_vs_always_opus"} <= set(ev)
        assert sum(r["n"] for r in ev["calibration_after_temperature"]["pooled"]["reliability"]) == 3 * len(tasks)


def test_estimate_tier_returns_a_tier_a_confidence_and_one_line_that_admits_the_fallback():
    for task in ("What does HTTP status 429 mean?", "", "x" * 20_000,
                 "Design the architecture for a multi-tenant SaaS with per-tenant keys, and explain the trade-offs.",
                 "Traceback (most recent call last):\n  File \"a.py\", line 1\nKeyError: 'id'"):
        tier, conf, reason = rl.estimate_tier(task)
        assert tier in (1, 2, 3) and 0.0 <= conf <= 1.0
        assert reason and "\n" not in reason
        assert "hashing fallback" in reason and "configured embedder is hashing-512" in reason


def test_low_confidence_escalates_to_the_higher_of_the_two_likeliest_tiers_and_says_why():
    cases = [((0.50, 0.40, 0.10), 2, True),   # unsure light vs standard -> standard
             ((0.10, 0.48, 0.42), 3, True),   # borderline standard / deep -> straight to deep
             ((0.50, 0.10, 0.40), 3, True),   # light vs deep -> deep
             ((0.10, 0.50, 0.40), 3, True),
             ((0.40, 0.45, 0.15), 2, False),  # runner-up is lower: stays standard, still flagged
             ((0.05, 0.15, 0.80), 3, False),
             ((0.90, 0.07, 0.03), 1, False)]  # confident: no escalation
    for probs, tier, escalated in cases:
        a = rl.assess("anything", artifact=stub_artifact(probs), head=rl.FALLBACK)
        assert (a.tier, a.escalated) == (tier, escalated), probs
        assert a.predicted == int(np.argmax(probs)) + 1 and a.confidence == pytest.approx(max(probs), abs=1e-3)
        assert a.low_confidence == (max(probs) < 0.6)
        if escalated:
            assert "unsure" in a.reason and f"escalated to {rl.TIER_NAMES[tier]}" in a.reason
        elif a.low_confidence:
            assert "low confidence" in a.reason and "no escalation" in a.reason
        else:
            assert "unsure" not in a.reason and "low confidence" not in a.reason


def test_threshold_rule_prefers_the_cheapest_threshold_that_meets_both_targets():
    y = np.array([0, 1, 2, 2])
    sure = np.array([[0.9, 0.05, 0.05], [0.05, 0.9, 0.05], [0.05, 0.05, 0.9], [0.05, 0.05, 0.9]])
    assert rl.choose_threshold(sure, y) == 0.0  # nothing to escalate
    unsure = sure.copy()
    unsure[3] = [0.05, 0.55, 0.40]  # a deep task the model calls standard, unsure
    th = rl.choose_threshold(unsure, y)
    assert th > 0.55 and rl.escalate(unsure, th)[0][3] == 2
    assert th == min(t for t in rl.THRESHOLDS if t > 0.55)


def test_temperature_scaling_undoes_overconfidence():
    rng = np.random.default_rng(0)
    logits = rng.normal(size=(2000, 3))
    y = np.array([rng.choice(3, p=p) for p in rl._softmax(logits)])  # labels drawn from the true probabilities
    assert rl._fit_temperature(logits * 4, y) == pytest.approx(4, rel=0.2)
    cal = rl.calibration(rl._softmax(logits), y)
    assert cal["ece"] < 0.05 and sum(r["n"] for r in cal["reliability"]) == 2000


def test_retraining_reproduces_the_shipped_fallback_head_exactly():
    shipped = rl.load()["heads"][rl.FALLBACK]
    art = rl.train(TRAIN, heads=(rl.FALLBACK,), evaluate=False)
    new = art["heads"][rl.FALLBACK]
    assert new["W"] == json.loads((ROOT / "src/pcim/data/routing_model.json").read_text())["heads"][rl.FALLBACK]["W"]
    assert (new["l2"], new["temperature"], new["threshold"]) == (shipped["l2"], shipped["temperature"],
                                                                  shipped["threshold"])
    assert np.allclose(np.array(new["b"]), shipped["b"])


def test_train_save_load_round_trip_on_a_small_file(tmp_path):
    light = ["Convert %d km to miles." % i for i in range(8)]
    std = ["Write a Python function that parses %s files and handles errors." % w for w in
           ("csv", "json", "yaml", "toml", "ini", "xml", "log", "tsv")]
    deep = ["Design a %s system with consistency trade-offs across regions and a failure analysis." % w for w in
            ("billing", "chat", "search", "booking", "payments", "storage", "queue", "ledger")]
    tasks = [{"id": f"x{i}", "tier": t, "kind": k, "text": x} for i, (t, k, x) in
             enumerate([(1, "c", s) for s in light] + [(2, "w", s) for s in std] + [(3, "d", s) for s in deep])]
    f = tmp_path / "tiny.json"
    f.write_text(json.dumps({"tasks": tasks}))
    art = rl.train(f, heads=(rl.FALLBACK,), evaluate=False)
    rl.save(art, tmp_path / "m.json")
    assert rl.train(f, heads=(rl.FALLBACK,), evaluate=False) == art  # deterministic
    loaded = rl.load(tmp_path / "m.json")
    assert rl.assess("Convert 99 km to miles.", artifact=loaded, head=rl.FALLBACK).tier == 1
    assert rl.assess("Design a calendar system with consistency trade-offs across regions and a failure analysis.",
                     artifact=loaded, head=rl.FALLBACK).tier == 3
    f.write_text(json.dumps({"tasks": [{"id": "bad", "tier": 4, "text": "x"}]}))
    with pytest.raises(ValueError):
        rl.train(f, heads=(rl.FALLBACK,))


def test_a_sentence_model_that_cannot_load_falls_back_and_says_so(monkeypatch):
    class Broken:
        def __init__(self, *a, **k):
            raise ImportError("no sentence-transformers here")
    monkeypatch.setattr(rl, "default_embedder_name", lambda: rl.SEMANTIC)
    monkeypatch.setattr(rl, "SentenceEmbedder", Broken)
    monkeypatch.setattr(rl, "_EMB", {})
    a = rl.assess("What does HTTP status 429 mean?")
    assert a.head == rl.FALLBACK and a.fallback == "sentence model unavailable (ImportError)"
    assert "sentence model unavailable (ImportError)" in a.reason


def test_the_sentence_head_offline(monkeypatch):
    """Loads the cached MiniLM model (seconds); skipped where it is not installed or cached."""
    if not embed.semantic_available():
        pytest.skip("sentence-transformers not installed")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    try:
        st = embed.SentenceEmbedder()
        st.embed(["warm-up"])
    except Exception as e:  # not cached here
        pytest.skip(f"MiniLM not available offline: {type(e).__name__}")
    a = rl.assess("Our two replicas sometimes both think they're the leader after a network blip; we use a heartbeat "
                  "with a 3 second timeout. How do we stop split brain?", embedder=st)
    assert a.head == rl.PRIMARY and a.fallback is None and a.tier == 3 and not a.escalated
    assert "closest labelled group:" in a.reason and "fallback" not in a.reason
    assert rl.assess("What does HTTP status 429 mean?", embedder=st).tier == 1
    # the development-set numbers recorded in the artifact are what the shipped weights give
    tasks = rl.load_tasks(DEV)
    y = np.array([t["tier"] for t in tasks]) - 1
    tiers = np.array([rl.assess(t["text"], embedder=st).tier for t in tasks]) - 1
    recorded = rl.load()["evaluation"]["dev_dataset_json"][rl.PRIMARY]["escalated"]
    assert rl.route_metrics(y, tiers) == recorded
