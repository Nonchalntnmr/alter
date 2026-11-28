"""Text embeddings.

Two embedders, one protocol:

- `HashingEmbedder` is lexical, not semantic: signed feature hashing of word unigrams,
  bigrams and character trigrams. Deterministic, offline, no model download, which makes
  it the right default for tests. It is the wrong default for any quality claim: "car"
  and "automobile" are unrelated to it, and so are "near where I live" and "I moved to
  Austin". E1 measured what that costs: context precision 3.3%.
- `SentenceEmbedder` runs a sentence-transformers model on this machine (the `semantic`
  extra). The model is downloaded once from Hugging Face; no memory content leaves the
  machine to be embedded. Selection has to run locally for data minimization to mean
  anything: a remote model that ranks every allowed memory has been sent every allowed
  memory.

Vectors from different embedders live in different spaces, so a store records which
embedder wrote its vectors (`embedder_for_store`) and is re-embedded when that changes.

Raw cosine means different things per model (MiniLM puts unrelated sentences near 0,
hashing near 0.05). `relevance_band` is the similarity range each embedder maps onto
relevance 0..1 in the context policy's features; the bands are calibrated on the E2
development set (experiments/e2/CALIBRATION.md), never on a test set.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from pathlib import Path
from typing import Protocol

import numpy as np

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")

DEFAULT_SEMANTIC_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
HASHING_NAME = "hashing-512"


class Embedder(Protocol):
    dim: int
    name: str
    relevance_band: tuple[float, float]  # cosine at relevance 0 and at relevance 1
    planned_retrieval: bool  # calibrated for planned searches (policy/candidates.py)

    def embed(self, texts: list[str]) -> np.ndarray:  # (n, dim), rows L2-normalised
        ...


class HashingEmbedder:
    # The band every release before E2 used (features.relevance_ramp's original constants).
    relevance_band = (0.2, 0.8)
    # Planned searches are short paraphrases ("the person's car"); lexical overlap with the
    # memory they target is near zero, so planning through this embedder finds nothing
    # (E2 development set: recall 0.07).
    planned_retrieval = False

    def __init__(self, dim: int = 512):
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _features(self, text: str) -> list[str]:
        words = _WORD.findall(text.lower())
        feats = [f"w:{w}" for w in words]
        feats += [f"b:{a}_{b}" for a, b in zip(words, words[1:])]
        for w in words:
            padded = f"#{w}#"
            feats += [f"c:{padded[i:i + 3]}" for i in range(len(padded) - 2)]
        return feats

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            counts: dict[int, float] = {}
            for f in self._features(text):
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
                idx, sign = h % self.dim, 1.0 if (h >> 63) & 1 else -1.0
                weight = 0.5 if f.startswith("c:") else 1.0
                counts[idx] = counts.get(idx, 0.0) + sign * weight
            for idx, v in counts.items():
                out[row, idx] = np.sign(v) * np.log1p(abs(v))
            n = np.linalg.norm(out[row])
            if n > 0:
                out[row] /= n
        return out


class _DropOfflineNotice(logging.Filter):
    """huggingface_hub warns "Could not reach the Hub (... offline mode is enabled ...)" on every load in
    offline mode, which SentenceEmbedder._load turns on itself for a cached model. Only that notice goes."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "offline mode is enabled" not in record.getMessage()


_OFFLINE_NOTICE = _DropOfflineNotice()


def _model_cached(model: str) -> bool:
    return (Path.home() / ".cache" / "huggingface" / "hub" / ("models--" + model.replace("/", "--"))).exists()


def _before_model_import(model: str = DEFAULT_SEMANTIC_MODEL) -> bool:
    """Call before anything imports sentence-transformers. huggingface_hub reads HF_HUB_OFFLINE once, when it is
    imported, so this decides then: a cached model loads offline and nothing is fetched. Returns whether it is."""
    cached = _model_cached(model)
    if cached:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    _quiet_library_notices()
    return cached


def _quiet_library_notices() -> None:
    """Silence the notices printed on every import or load of the local model that nobody can act on.
    The same libraries' other warnings, and every error, still show."""
    # torch, on macOS: it cannot redirect a child process's output, which ALTER never asks it to do
    logging.getLogger("torch.distributed.elastic.multiprocessing.redirects").setLevel(logging.ERROR)
    logging.getLogger("huggingface_hub.hf_api").addFilter(_OFFLINE_NOTICE)  # adds it once


class SentenceEmbedder:
    """A local sentence-transformers model. Loaded on first use, so commands that never embed
    do not pay for importing torch."""

    # Calibrated for all-MiniLM-L6-v2 on the E2 development set; other models need their own.
    BANDS = {DEFAULT_SEMANTIC_MODEL: (0.1, 0.5)}
    planned_retrieval = True

    def __init__(self, model: str = DEFAULT_SEMANTIC_MODEL):
        if model not in self.BANDS:
            raise ValueError(f"no calibrated relevance band for {model!r}; calibrate it on a development set first")
        self.model = model
        self.name = f"st:{model}"
        self.relevance_band = self.BANDS[model]
        self.dim = 384
        self._m = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._m is None:
                cached = _before_model_import(self.model)
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as e:
                    raise EmbedderUnavailable(
                        f"this store's memory is embedded with {self.name}, which needs the `semantic` extra: "
                        "pip install 'contextual-intelligence[semantic]' (a store keeps the embedder it was "
                        "written with, so its memory never mixes two vector spaces)") from e
                # From disk the "Loading weights" bar lasts under a second; a first download keeps its bars.
                bars = _progress_bars() if cached else None
                was_on = bars is not None and bars.is_progress_bar_enabled()
                if was_on:
                    bars.disable_progress_bar()
                try:
                    self._m = SentenceTransformer(self.model, device="cpu")
                finally:
                    if was_on:
                        bars.enable_progress_bar()
                # sentence-transformers 6 renamed get_sentence_embedding_dimension, and warns on the old name
                dim = getattr(self._m, "get_embedding_dimension", None) or self._m.get_sentence_embedding_dimension
                self.dim = dim()
        return self._m

    def embed(self, texts: list[str]) -> np.ndarray:
        m = self._load()
        with self._lock:  # one forward pass at a time; torch's CPU kernels are already parallel
            out = m.encode(list(texts), normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        return np.asarray(out, dtype=np.float32).reshape(len(texts), -1)


def embedder_from_name(name: str) -> Embedder:
    if name.startswith("hashing-"):
        return HashingEmbedder(int(name.split("-", 1)[1]))
    if name.startswith("st:"):
        return SentenceEmbedder(name[3:])
    raise ValueError(f"unknown embedder {name!r}")


class EmbedderUnavailable(RuntimeError):
    """The store's recorded embedder cannot run here (for example, its extra is not installed)."""


def _progress_bars():
    """transformers' switch for its progress bars, or None without transformers."""
    try:
        from transformers.utils import logging as hf_logging
    except ImportError:
        return None
    return hf_logging


def semantic_available() -> bool:
    _before_model_import()
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def default_embedder_name() -> str:
    """$PCIM_EMBEDDER if set; otherwise semantic when the extra is installed, else hashing."""
    env = os.environ.get("PCIM_EMBEDDER")
    if env:
        return env
    return f"st:{DEFAULT_SEMANTIC_MODEL}" if semantic_available() else HASHING_NAME
