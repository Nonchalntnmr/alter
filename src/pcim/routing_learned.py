"""Learned requirement estimate: request text -> tier 1/2/3, a confidence, and a one-line reason.

routing.estimate's keyword markers matched 15 of 40 labels on a held-out set written without
sight of the code, under-routed 18 and found 3 of 10 deep requests: difficulty is mostly not in
the wording. This module replaces that guess with a small classifier trained offline on
labelled requests (experiments/routing/train.json) and shipped as weights only
(data/routing_model.json; no training text is stored).

Model. Multinomial logistic regression (numpy, L2, class-balanced) on local embeddings:
  - primary head: the cached MiniLM sentence model (embed.SentenceEmbedder) concatenated with
    the hashing embedder. Chosen by one plain 5-fold x 3 CV comparison on train.json, before
    the nested evaluation and not repeated in train_learned.py: exact 0.73, against MiniLM
    alone 0.68, hashing alone 0.66, MiniLM nearest centroid 0.67, MiniLM 15-NN 0.65;
  - fallback head: the hashing embedder alone, used when the sentence model is not the
    configured embedder or cannot load. It is lexical, measured weaker, and the reason says so.
The L2 strength is chosen by inner cross-validation (out-of-fold log loss), the probabilities
are recalibrated by one temperature fitted on out-of-fold logits, and everything runs on this
machine: the request never leaves it to be estimated.

Escalation. Confidence is the calibrated probability of the most likely tier. Below a threshold
chosen on out-of-fold predictions, the estimate escalates to the higher of the two most likely
tiers (light unsure of standard -> standard; standard unsure of deep -> deep) and the reason
says it escalated because it was unsure. Threshold rule, fixed before it was run: the lowest
threshold whose out-of-fold under-routing is at most 10% and deep recall at least 0.90; if none
reaches both, the one minimising 3 x under-routing + over-routing (under-routing assumed three
times as costly). Under-routing costs answer quality; over-routing costs money.

Measured (nested CV, train.json, 395 tasks; artifact["evaluation"] has the rest). Primary head,
most likely tier: exact 0.75, under-routed 0.13, deep recall 0.77. With escalation (threshold
0.75, escalates 25%): exact 0.69, under-routed 0.03, over-routed 0.28, deep recall 0.93;
estimated cost 0.78 of always-Opus (oracle labels 0.72). ECE 0.052 before the temperature,
0.041 after. The hashing fallback with escalation: exact 0.57, under-routed 0.06, deep recall
0.84; the temperature does not improve its ECE (0.053 -> 0.063). L2 was chosen at the low edge
of its grid in every fold (0.3), so a smaller value might do better; it was not tried.

What the numbers mean. Cross-validation on train.json measures how well the model learns one
author's labels on that author's writing. dataset.json (36) is a development check that the
author had read. Neither is a held-out measurement; that needs a sealed set from another
author (train_learned.py --evaluate). The labels are judgement against a rubric, not measured
model performance, and the tiers are vendor positioning (routing.py).
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import math
import os
import threading
from pathlib import Path

import numpy as np

from .embed import DEFAULT_SEMANTIC_MODEL, HASHING_NAME, Embedder, HashingEmbedder, SentenceEmbedder, \
    default_embedder_name

ARTIFACT = Path(__file__).resolve().parent / "data" / "routing_model.json"
FORMAT = "pcim-routing-model/1"
SEMANTIC = f"st:{DEFAULT_SEMANTIC_MODEL}"
PRIMARY = f"{SEMANTIC}+{HASHING_NAME}"
FALLBACK = HASHING_NAME
TIER_NAMES = {1: "light", 2: "standard", 3: "deep"}

L2_GRID = (0.3, 1.0, 3.0)
THRESHOLDS = (0.0, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9)
TARGET_UNDER, TARGET_DEEP_RECALL, UNDER_WEIGHT = 0.10, 0.90, 3.0
FOLDS, ITERS, MIN_GROUP = 5, 600, 3
RELIABILITY_EDGES = (1 / 3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


# ---------------------------------------------------------------------- arrays in JSON
def _pack(a: np.ndarray, dtype: str = "<f4") -> dict:
    a = np.ascontiguousarray(a, dtype=dtype)
    return {"dtype": dtype, "shape": list(a.shape), "b64": base64.b64encode(a.tobytes()).decode()}


def _unpack(d: dict) -> np.ndarray:
    return np.frombuffer(base64.b64decode(d["b64"]), dtype=d["dtype"]).reshape(d["shape"]).astype(np.float64)


# ---------------------------------------------------------------------- the model
def _folds(y: np.ndarray, k: int, seed: int) -> np.ndarray:
    """Stratified fold labels, deterministic for a seed."""
    rng = np.random.default_rng(seed)
    f = np.empty(len(y), dtype=int)
    for c in np.unique(y):
        idx = np.flatnonzero(y == c)
        rng.shuffle(idx)
        f[idx] = np.arange(len(idx)) % k
    return f


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def _fit_lr(X: np.ndarray, y: np.ndarray, l2: float) -> tuple[np.ndarray, np.ndarray]:
    """Class-balanced multinomial logistic regression with an L2 penalty, by Nesterov-accelerated
    gradient descent on standardized features. Returns (W, b) on the raw features:
    logits = X @ W.T + b. Deterministic."""
    n, d = X.shape
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-6
    Z = np.hstack([(X - mu) / sd, np.ones((n, 1))])
    sw = (n / (3 * np.bincount(y, minlength=3)))[y] / n
    Y = np.eye(3)[y]
    reg = np.ones((d + 1, 1))
    reg[-1] = 0.0  # no penalty on the intercept
    step = 1.0 / (0.5 * np.linalg.norm(Z * np.sqrt(sw)[:, None], 2) ** 2 + l2)
    W = np.zeros((d + 1, 3))
    V, t = W.copy(), 1.0
    for _ in range(ITERS):
        grad = Z.T @ ((_softmax(Z @ V) - Y) * sw[:, None]) + l2 * reg * V
        Wn = V - step * grad
        tn = (1 + math.sqrt(1 + 4 * t * t)) / 2
        V, W, t = Wn + (t - 1) / tn * (Wn - W), Wn, tn
    Wf = (W[:-1] / sd[:, None]).T
    return Wf, W[-1] - Wf @ mu


def _nll(P: np.ndarray, y: np.ndarray) -> float:
    return float(-np.mean(np.log(np.clip(P[np.arange(len(y)), y], 1e-12, None))))


def _fit_temperature(logits: np.ndarray, y: np.ndarray) -> float:
    """One temperature minimising the log loss of out-of-fold logits (golden-section search)."""
    lo, hi = math.log(0.05), math.log(20.0)
    g = (math.sqrt(5) - 1) / 2
    f = lambda s: _nll(_softmax(logits / math.exp(s)), y)  # noqa: E731
    a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    fa, fb = f(a), f(b)
    for _ in range(60):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - g * (hi - lo)
            fa = f(a)
        else:
            lo, a, fa = a, b, fb
            b = lo + g * (hi - lo)
            fb = f(b)
    return round(math.exp((lo + hi) / 2), 4)


def escalate(P: np.ndarray, threshold: float) -> tuple[np.ndarray, np.ndarray]:
    """Tier index per row (0-based) after escalation, and whether it escalated. Below the
    threshold the estimate takes the higher of the two most likely tiers."""
    order = np.argsort(-P, axis=1, kind="stable")
    top, second = order[:, 0], order[:, 1]
    low = P[np.arange(len(P)), top] < threshold
    out = np.where(low, np.maximum(top, second), top)
    return out, low & (out != top)


def route_metrics(y: np.ndarray, pred: np.ndarray) -> dict:
    """y, pred: 0-based tier indices."""
    deep = y == 2
    return {"n": int(len(y)), "exact": round(float(np.mean(pred == y)), 3),
            "under_routed": round(float(np.mean(pred < y)), 3), "over_routed": round(float(np.mean(pred > y)), 3),
            "deep_recall": round(float(np.mean(pred[deep] == 2)), 3) if deep.any() else None}


def choose_threshold(P: np.ndarray, y: np.ndarray) -> float:
    """The rule in the module docstring, on calibrated out-of-fold probabilities."""
    rows = []
    for th in THRESHOLDS:
        m = route_metrics(y, escalate(P, th)[0])
        rows.append((th, m))
    ok = [th for th, m in rows if m["under_routed"] <= TARGET_UNDER and (m["deep_recall"] or 0) >= TARGET_DEEP_RECALL]
    if ok:
        return min(ok)
    return min(rows, key=lambda r: (UNDER_WEIGHT * r[1]["under_routed"] + r[1]["over_routed"], r[0]))[0]


def calibration(P: np.ndarray, y: np.ndarray) -> dict:
    """Expected calibration error of the top-tier probability, and the reliability table."""
    conf, hit = P.max(axis=1), (P.argmax(axis=1) == y).astype(float)
    bins = np.searchsorted(np.array(RELIABILITY_EDGES[1:-1]), conf, side="right")
    table, ece = [], 0.0
    for i, (lo, hi) in enumerate(zip(RELIABILITY_EDGES, RELIABILITY_EDGES[1:])):
        m = bins == i
        n = int(m.sum())
        if n:
            ece += n / len(y) * abs(hit[m].mean() - conf[m].mean())
        table.append({"bin": f"{lo:.2f}-{hi:.2f}", "n": n, "mean_confidence": round(float(conf[m].mean()), 3) if n else None,
                      "accuracy": round(float(hit[m].mean()), 3) if n else None})
    return {"ece": round(float(ece), 4), "log_loss": round(_nll(P, y), 4), "reliability": table}


def _oof_logits(X: np.ndarray, y: np.ndarray, l2: float, seed: int) -> np.ndarray:
    f, out = _folds(y, FOLDS, seed), np.zeros((len(y), 3))
    for k in range(FOLDS):
        W, b = _fit_lr(X[f != k], y[f != k], l2)
        out[f == k] = X[f == k] @ W.T + b
    return out


def fit_head(X: np.ndarray, y: np.ndarray, seed: int = 0) -> dict:
    """The whole recipe on one training set: L2 by inner CV, temperature and threshold on the
    out-of-fold predictions, then the final fit on everything. y is 0-based."""
    oof = {l2: _oof_logits(X, y, l2, seed) for l2 in L2_GRID}
    l2 = min(L2_GRID, key=lambda c: (round(_nll(_softmax(oof[c]), y), 6), c))
    temperature = _fit_temperature(oof[l2], y)
    threshold = choose_threshold(_softmax(oof[l2] / temperature), y)
    W, b = _fit_lr(X, y, l2)
    return {"W": W, "b": b, "l2": l2, "temperature": temperature, "threshold": threshold}


def _predict(head: dict, X: np.ndarray) -> np.ndarray:
    return _softmax((X @ head["W"].T + head["b"]) / head["temperature"])


def nested_cv(X: np.ndarray, y: np.ndarray, repeats: int = 3) -> dict:
    """Outer 5-fold CV around fit_head, so the reported numbers include choosing L2, the
    temperature and the threshold. Returns out-of-fold predictions per repeat."""
    reps = []
    for r in range(repeats):
        f = _folds(y, FOLDS, 1000 + r)
        P, raw, chosen = np.zeros((len(y), 3)), np.zeros((len(y), 3)), []
        tiers = np.zeros(len(y), dtype=int)
        for k in range(FOLDS):
            tr, te = f != k, f == k
            h = fit_head(X[tr], y[tr], seed=r)
            logits = X[te] @ h["W"].T + h["b"]
            raw[te], P[te] = _softmax(logits), _softmax(logits / h["temperature"])
            tiers[te] = escalate(P[te], h["threshold"])[0]
            chosen.append({"l2": h["l2"], "temperature": h["temperature"], "threshold": h["threshold"]})
        reps.append({"P": P, "P_uncalibrated": raw, "tiers": tiers, "chosen": chosen})
    return {"repeats": reps}


def estimated_cost(texts: list[str], tiers: np.ndarray, label_tiers: np.ndarray) -> float:
    """List-price USD for answering each text at a tier (Claude haiku/sonnet/opus), with
    routing's own token estimates; output length follows the label (the task, not the model).
    An estimate for comparing strategies, not a bill."""
    from .adapters.pricing import PRICES
    from .routing import OUTPUT_TOKENS, SYSTEM_OVERHEAD
    from .types import approx_tokens
    price = {1: PRICES["claude-haiku-5-5"], 2: PRICES["claude-sonnet-5-5"], 3: PRICES["claude-opus-5-5"]}
    total = 0.0
    for text, t, lab in zip(texts, tiers, label_tiers):
        tin, tout = approx_tokens(text) + 1200 + SYSTEM_OVERHEAD, OUTPUT_TOKENS[int(lab)]
        total += (tin * price[int(t)]["input"] + tout * price[int(t)]["output"]) / 1e6
    return total


def summarize(cv: dict, texts: list[str], y: np.ndarray) -> dict:
    """Nested-CV numbers: raw argmax, escalated, and escalated under the balanced floor."""
    def avg(ds):
        return {k: ds[0][k] if k == "n" else round(float(np.mean([d[k] for d in ds])), 3) for k in ds[0]}
    raw, esc, floor, cal, cal_raw, cost, esc_rate = [], [], [], [], [], [], []
    opus = estimated_cost(texts, np.full(len(y), 3), y + 1)
    for rep in cv["repeats"]:
        P, tiers = rep["P"], rep["tiers"]
        raw.append(route_metrics(y, P.argmax(axis=1)))
        esc.append(route_metrics(y, tiers))
        floor.append(route_metrics(y, np.maximum(tiers, 1)))
        cal.append(calibration(P, y))
        cal_raw.append(calibration(rep["P_uncalibrated"], y))
        esc_rate.append(float(np.mean(tiers != P.argmax(axis=1))))
        cost.append({"escalated": estimated_cost(texts, tiers + 1, y + 1) / opus,
                     "escalated_balanced_floor": estimated_cost(texts, np.maximum(tiers, 1) + 1, y + 1) / opus,
                     "oracle_labels": estimated_cost(texts, y + 1, y + 1) / opus})
    pooled = np.concatenate([r["P"] for r in cv["repeats"]])
    ys = np.tile(y, len(cv["repeats"]))
    return {"repeats": len(cv["repeats"]), "folds": FOLDS,
            "argmax": avg(raw), "escalated": avg(esc), "escalated_balanced_floor": avg(floor),
            "escalation_rate": round(float(np.mean(esc_rate)), 3),
            "cost_vs_always_opus": {k: round(float(np.mean([c[k] for c in cost])), 3) for k in cost[0]},
            "calibration_after_temperature": {"ece_mean": round(float(np.mean([c["ece"] for c in cal])), 4),
                                              "pooled": calibration(pooled, ys)},
            "calibration_before_temperature": {"ece_mean": round(float(np.mean([c["ece"] for c in cal_raw])), 4)},
            "chosen_per_outer_fold": [c for r in cv["repeats"] for c in r["chosen"]]}


# ---------------------------------------------------------------------- training
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_tasks(path: str | Path) -> list[dict]:
    raw = json.loads(Path(path).read_text())
    tasks = raw["tasks"] if isinstance(raw, dict) else raw
    for t in tasks:
        if t.get("tier") not in (1, 2, 3) or not isinstance(t.get("text"), str):
            raise ValueError(f"{t.get('id', '?')}: needs tier 1-3 and text")
    return tasks


def features(head: str, texts: list[str], embedder: Embedder | None = None) -> np.ndarray:
    hashing = HashingEmbedder(int(HASHING_NAME.split("-")[1])).embed(texts).astype(np.float64)
    if head == FALLBACK:
        return hashing
    if head != PRIMARY:
        raise ValueError(f"unknown head {head!r}")
    st = embedder if embedder is not None and embedder.name == SEMANTIC else SentenceEmbedder(DEFAULT_SEMANTIC_MODEL)
    return np.hstack([st.embed(texts).astype(np.float64), hashing])


def train(path: str | Path, *, heads: tuple[str, ...] = (PRIMARY, FALLBACK), evaluate: bool = True,
          embedder: Embedder | None = None) -> dict:
    """Train every head on a labelled file ({"tasks": [{"tier", "text", "kind"?}]}) and return the
    artifact (a JSON-able dict: weights, temperature, threshold and group centroids; no texts).
    `evaluate` adds nested-CV numbers. Deterministic for the same file, versions and machine."""
    path = Path(path)
    tasks = load_tasks(path)
    texts = [t["text"] for t in tasks]
    y = np.array([t["tier"] for t in tasks]) - 1
    art = {"format": FORMAT,
           "trained_on": {"file": path.name, "sha256": _sha256(path), "n": len(tasks),
                          "by_tier": {TIER_NAMES[k + 1]: int((y == k).sum()) for k in range(3)}},
           "recipe": {"model": "multinomial logistic regression, class-balanced, L2 by inner 5-fold CV",
                      "l2_grid": list(L2_GRID), "calibration": "one temperature on out-of-fold logits",
                      "escalation": "below the threshold, the higher of the two most likely tiers",
                      "threshold_rule": f"lowest with OOF under-routing <= {TARGET_UNDER} and deep recall >= "
                                        f"{TARGET_DEEP_RECALL}; else min {UNDER_WEIGHT:g} x under + over"},
           "heads": {}, "evaluation": {}}
    for name in heads:
        X = features(name, texts, embedder)
        h = fit_head(X, y)
        entry = {"dim": int(X.shape[1]), "W": _pack(h["W"]), "b": [round(float(v), 6) for v in h["b"]],
                 "l2": h["l2"], "temperature": h["temperature"], "threshold": h["threshold"]}
        if name == PRIMARY:
            entry["groups"] = _groups(tasks, X[:, :X.shape[1] - int(HASHING_NAME.split("-")[1])], y)
        art["heads"][name] = entry
        if evaluate:
            art["evaluation"][name] = summarize(nested_cv(X, y), texts, y)
    return art


def _groups(tasks: list[dict], E: np.ndarray, y: np.ndarray) -> dict:
    """Centroids of labelled (kind, tier) groups in the sentence space, for the explanation."""
    keys = sorted({(t.get("kind") or "other", int(t["tier"])) for t in tasks})
    names, cents, sizes = [], [], []
    for kind, tier in keys:
        m = np.array([(t.get("kind") or "other") == kind and t["tier"] == tier for t in tasks])
        if m.sum() < MIN_GROUP:
            continue
        c = E[m].mean(axis=0)
        names.append([kind, tier])
        sizes.append(int(m.sum()))
        cents.append(c / (np.linalg.norm(c) or 1.0))
    return {"names": names, "sizes": sizes, "centroids": _pack(np.array(cents), "<f2")}


def save(artifact: dict, path: str | Path = ARTIFACT) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(artifact, indent=1, sort_keys=True) + "\n")


# ---------------------------------------------------------------------- inference
_ART: dict | None = None
_EMB: dict[str, object] = {}
_LOCK = threading.Lock()


def load(path: str | Path | None = None) -> dict:
    """The artifact with arrays decoded. The packaged one is cached."""
    global _ART
    if path is None and _ART is not None:
        return _ART
    raw = json.loads(Path(path or ARTIFACT).read_text())
    if raw.get("format") != FORMAT:
        raise ValueError(f"{path or ARTIFACT}: not a {FORMAT} artifact")
    for h in raw["heads"].values():
        h["W"], h["b"] = _unpack(h["W"]), np.array(h["b"])
        if "groups" in h:
            h["groups"]["centroids"] = _unpack(h["groups"]["centroids"])
    if path is None:
        _ART = raw
    return raw


def _sentence_model(embedder: Embedder | None) -> tuple[Embedder | None, str | None]:
    """The MiniLM embedder (the caller's, or one loaded once), or None and why not."""
    if embedder is not None and embedder.name == SEMANTIC:
        return embedder, None
    configured = default_embedder_name()
    if configured != SEMANTIC:
        return None, f"configured embedder is {configured}"
    with _LOCK:
        if SEMANTIC not in _EMB:
            try:
                e = SentenceEmbedder(DEFAULT_SEMANTIC_MODEL)
                e.embed(["warm-up"])
                _EMB[SEMANTIC] = e
            except Exception as exc:  # ImportError, OSError (model not cached), RuntimeError: all mean "not here"
                _EMB[SEMANTIC] = f"sentence model unavailable ({type(exc).__name__})"
        got = _EMB[SEMANTIC]
    return (got, None) if not isinstance(got, str) else (None, got)


@dataclasses.dataclass(frozen=True)
class Assessment:
    tier: int  # after escalation: what routing should require
    predicted: int  # the most likely tier
    confidence: float  # calibrated probability of `predicted`
    probs: dict[int, float]
    low_confidence: bool
    escalated: bool
    threshold: float
    head: str
    fallback: str | None  # why the sentence model was not used, if it was not
    group: str | None  # closest labelled group, e.g. "concurrency (deep, 11 labelled)"
    reason: str  # one line


def assess(task: str, *, embedder: Embedder | None = None, artifact: dict | None = None,
           head: str | None = None) -> Assessment:
    """Estimate the tier `task` needs. Local and deterministic; no model call. `head=FALLBACK`
    forces the hashing head (for evaluating it)."""
    art = artifact or load()
    if head == FALLBACK:
        st, why = None, "asked for the fallback head"
    elif PRIMARY in art["heads"]:
        st, why = _sentence_model(embedder)
    else:
        st, why = None, "artifact has no sentence head"
    name = PRIMARY if st is not None else FALLBACK
    h = art["heads"][name]
    hashing = HashingEmbedder(int(HASHING_NAME.split("-")[1])).embed([task]).astype(np.float64)
    sem = st.embed([task]).astype(np.float64) if st is not None else None
    X = np.hstack([sem, hashing]) if sem is not None else hashing
    P = _softmax((X @ h["W"].T + h["b"]) / h["temperature"])
    tier_idx, esc = escalate(P, h["threshold"])
    predicted, tier = int(P[0].argmax()) + 1, int(tier_idx[0]) + 1
    conf = float(P[0].max())
    probs = {k + 1: round(float(P[0, k]), 3) for k in range(3)}
    group = None
    if sem is not None and "groups" in h:
        g = h["groups"]
        sims = g["centroids"] @ sem[0]
        i = int(np.argmax(sims))
        kind, gt = g["names"][i]
        group = f"{kind} ({TIER_NAMES[gt]}, {g['sizes'][i]} labelled)"
    others = ", ".join(f"{TIER_NAMES[k]} {p:.2f}" for k, p in sorted(probs.items(), key=lambda kv: -kv[1])
                       if k != predicted)
    src = "learned estimate" if st is not None else f"learned estimate, hashing fallback (lexical, weaker; {why})"
    if esc[0]:
        body = (f"unsure: {TIER_NAMES[predicted]} p={conf:.2f} is below {h['threshold']:.2f} with "
                f"{TIER_NAMES[tier]} {probs[tier]:.2f} close, so escalated to {TIER_NAMES[tier]}")
    else:
        body = f"{TIER_NAMES[predicted]} p={conf:.2f} ({others})"
        if conf < h["threshold"]:
            body += f", low confidence (below {h['threshold']:.2f}); the runner-up is not higher, so no escalation"
    reason = f"{src}: {body}" + (f"; closest labelled group: {group}" if group else "")
    return Assessment(tier=tier, predicted=predicted, confidence=round(conf, 3), probs=probs,
                      low_confidence=conf < h["threshold"], escalated=bool(esc[0]), threshold=h["threshold"],
                      head=name, fallback=why if st is None else None, group=group, reason=" ".join(reason.split()))


def estimate_tier(task: str, *, embedder: Embedder | None = None) -> tuple[int, float, str]:
    """(tier 1-3 after escalation, calibrated confidence in the most likely tier, one-line reason)."""
    a = assess(task, embedder=embedder)
    return a.tier, a.confidence, a.reason
