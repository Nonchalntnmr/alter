"""Personal compute mesh: what compute you actually have, measured, and where a workload may run.

    discover()  this device (cores, memory, accelerator), local embedding models, local model
                servers (Ollama, LM Studio, llama.cpp) and any you list in $PCIM_HOME/mesh.json,
                and the cloud models in the registry with their availability
    bench()     measured, never assumed: CPU matmul throughput, local embedding throughput,
                local model generation speed; cloud latency only with live=True (it is billed)
    place()     where a workload may run: privacy first (a SENSITIVE workload never leaves your
                own machines, SECRET goes nowhere), then capability, then measured latency and cost

What this does not do: create compute. Remote capacity is whatever your listed machines and
your cloud accounts already provide, reported separately from this device and never summed
into a single "mesh capacity" number.

$PCIM_HOME/mesh.json (optional):
    {"endpoints": [{"name": "studio", "base_url": "http://192.168.1.20:11434/v1",
                    "location": "lan", "trusted": true}]}
`trusted: true` declares the machine yours, which lets SENSITIVE workloads run there.
"""

from __future__ import annotations

import dataclasses
import json
import os
import platform
import statistics
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .registry import availability, entries, vendor_of
from .routing import capability
from .types import Sensitivity, utcnow

BENCH_FILE = "mesh_bench.json"
LOCAL_SERVERS = (("ollama", "http://127.0.0.1:11434/v1"), ("lm-studio", "http://127.0.0.1:1234/v1"),
                 ("llama.cpp", "http://127.0.0.1:8080/v1"))
WORKLOADS = ("embed", "generate", "verify")
# How far each location may be trusted with data, as a sensitivity ceiling.
_CEILING = {"on-device": Sensitivity.SENSITIVE, "lan-trusted": Sensitivity.SENSITIVE,
            "lan": Sensitivity.PERSONAL, "cloud": Sensitivity.PERSONAL}


@dataclasses.dataclass
class Resource:
    id: str
    kind: str  # device | embed | llm
    location: str  # on-device | lan | lan-trusted | cloud
    available: bool
    detail: str
    workloads: tuple[str, ...] = ()
    tier: int | None = None  # capability tier for llm resources (routing.py); None = unrated
    est_cost: str = "free (your hardware)"
    model: str | None = None
    base_url: str | None = None

    @property
    def ceiling(self) -> Sensitivity:
        return _CEILING[self.location]

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["max_sensitivity"] = self.ceiling.value
        return d


# ---------------------------------------------------------------------- discovery
def _memory_bytes() -> int | None:
    try:
        if platform.system() == "Darwin":
            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout)
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _accelerator() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return f"cuda ({torch.cuda.get_device_name(0)})"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps (Apple GPU)"
    except Exception:  # noqa: BLE001 - torch is optional; any import problem means "none found"
        pass
    return "none found"


def _probe(base_url: str, timeout: float) -> tuple[bool, list[str], str]:
    try:
        r = httpx.get(base_url.rstrip("/") + "/models", timeout=timeout)
        if r.status_code != 200:
            return False, [], f"HTTP {r.status_code}"
        models = [m.get("id", "?") for m in (r.json().get("data") or []) if isinstance(m, dict)]
        return True, models, f"{len(models)} model(s)"
    except (httpx.HTTPError, ValueError) as e:
        return False, [], type(e).__name__


def _is_local(url: str) -> bool:
    return urlparse(url).hostname in ("127.0.0.1", "localhost", "::1")


def load_config(home: Path | None) -> dict:
    if home is None:
        return {}
    p = Path(home) / "mesh.json"
    if not p.exists():
        return {}
    try:
        cfg = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"{p}: {e}") from None
    for ep in cfg.get("endpoints", []):
        if not str(ep.get("base_url", "")).startswith(("http://", "https://")):
            raise ValueError(f"{p}: endpoint {ep.get('name')!r} needs an http(s) base_url")
    return cfg


def discover(home: Path | None = None, probe_timeout: float = 0.6, include_test_doubles: bool = False) -> list[Resource]:
    out: list[Resource] = []
    mem = _memory_bytes()
    out.append(Resource("device", "device", "on-device", True,
                        f"{platform.system()} {platform.machine()}, {os.cpu_count()} cores, "
                        f"{'?' if mem is None else round(mem / 2**30)} GB, accelerator: {_accelerator()}",
                        ()))  # the host itself serves no workload; the services on it do
    try:
        import importlib.util
        have_st = importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):
        have_st = False
    from .embed import DEFAULT_SEMANTIC_MODEL
    cached = (Path.home() / ".cache" / "huggingface" / "hub" /
              ("models--" + DEFAULT_SEMANTIC_MODEL.replace("/", "--"))).exists()
    out.append(Resource("local-embed", "embed", "on-device", have_st and cached,
                        f"{DEFAULT_SEMANTIC_MODEL}: " + ("installed and cached" if have_st and cached else
                                                         "sentence-transformers missing" if not have_st else
                                                         "model not downloaded yet"),
                        ("embed",), model=DEFAULT_SEMANTIC_MODEL))
    out.append(Resource("hashing-embed", "embed", "on-device", True, "lexical hashing embedder (always available, weak)",
                        ("embed",), model="hashing"))
    servers = [(n, u, "on-device", False) for n, u in LOCAL_SERVERS]
    for ep in load_config(home).get("endpoints", []):
        loc = "on-device" if _is_local(ep["base_url"]) else ("lan-trusted" if ep.get("trusted") else "lan")
        servers.append((ep.get("name") or ep["base_url"], ep["base_url"], loc, True))
    for name, url, loc, configured in servers:
        ok, models, why = _probe(url, probe_timeout)
        if not ok and not configured:
            continue  # unconfigured local ports that are not listening are not resources
        for m in (models or [None]):
            out.append(Resource(f"{name}:{m}" if m else name, "llm", loc, ok, f"{url} — {why}",
                                ("generate", "verify") if ok else (), None, "free (your hardware)", m, url))
    for e in entries():
        if e.test_double and not include_test_doubles:
            continue
        ok, why = availability(e)
        cap = capability(e)
        # The registry's `local` provider is a model server on this machine by construction
        # (openai_compat refuses a non-loopback base URL for it), so it is on-device, not cloud.
        on_device = e.transport == "test-double" or e.provider == "local"
        out.append(Resource(e.key, "llm", "on-device" if on_device else "cloud", ok,
                            f"{e.label} — {why}", tuple(c for c in ("generate", "verify") if ok), cap.tier,
                            "free (your hardware)" if on_device else "billed to your account", e.model))
    return out


# ---------------------------------------------------------------------- benchmarks
def _timed(fn, reps: int) -> list[float]:
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return times


def _summary(values: list[float], unit: str, n_reps: int, note: str) -> dict:
    return {"median": round(statistics.median(values), 3), "min": round(min(values), 3), "max": round(max(values), 3),
            "unit": unit, "reps": n_reps, "note": note}


def bench_cpu(n: int = 1024, reps: int = 5) -> dict:
    import numpy as np
    a = np.random.default_rng(0).standard_normal((n, n), dtype=np.float32)
    b = a.T.copy()
    a @ b  # warm-up
    gflops = [2 * n**3 / t / 1e9 for t in _timed(lambda: a @ b, reps)]
    return _summary(gflops, "GFLOP/s", reps, f"float32 {n}x{n} matmul, numpy")


def bench_embed(sentences: int = 64, reps: int = 3) -> dict | None:
    try:
        from .embed import DEFAULT_SEMANTIC_MODEL, SentenceEmbedder
        cached = (Path.home() / ".cache" / "huggingface" / "hub" /
                  ("models--" + DEFAULT_SEMANTIC_MODEL.replace("/", "--"))).exists()
        if cached:  # a cached model needs no network; without this the loader checks the Hub for updates
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
        emb = SentenceEmbedder(DEFAULT_SEMANTIC_MODEL)
        texts = [f"note {i}: the person prefers concise answers about topic {i % 7}" for i in range(sentences)]
        emb.embed(texts[:4])  # warm-up; the model loads lazily, so this is where a missing extra shows
    except Exception:  # noqa: BLE001 - not installed / not cached: nothing to measure
        return None
    rates = [sentences / t for t in _timed(lambda: emb.embed(texts), reps)]
    return _summary(rates, "sentences/s", reps, f"{DEFAULT_SEMANTIC_MODEL}, batch of {sentences}")


def bench_server(base_url: str, model: str, timeout: float = 60.0) -> dict | None:
    """One short generation on a local or LAN OpenAI-compatible server: tokens per second."""
    body = {"model": model, "messages": [{"role": "user", "content": "Count from 1 to 30, comma separated."}],
            "max_tokens": 120}
    try:
        t0 = time.perf_counter()
        r = httpx.post(base_url.rstrip("/") + "/chat/completions", json=body, timeout=timeout)
        dt_s = time.perf_counter() - t0
        r.raise_for_status()
        toks = (r.json().get("usage") or {}).get("completion_tokens")
    except (httpx.HTTPError, ValueError):
        return None
    if not toks:
        return {"median": round(dt_s, 3), "unit": "s for one reply (server reported no token count)", "reps": 1}
    return {"median": round(toks / dt_s, 1), "unit": "tokens/s", "reps": 1, "note": f"{toks} tokens in {dt_s:.2f}s"}


def bench_cloud(ci, uid: str, key: str) -> dict:
    """One tiny live call: latency only. Billed to the account behind `key`."""
    provider, _, model = key.partition(":")
    from .adapters import get_adapter
    from .adapters.base import RenderedPrompt
    ad = get_adapter(provider, model or None)
    t0 = time.perf_counter()
    resp = ad.generate(RenderedPrompt(system="Reply with the single word: ok", user="ok?"), max_tokens=10)
    return {"median": round((time.perf_counter() - t0), 2), "unit": "s for a one-word reply", "reps": 1,
            "note": f"served by {resp.model}; includes process start for CLI transports", "cost_usd": resp.cost_usd}


def bench(home: Path | None, resources: list[Resource], *, live: bool = False, ci=None, uid: str | None = None) -> dict:
    res = {"measured_at": utcnow().isoformat(), "host": platform.node(), "results": {}}
    res["results"]["device"] = bench_cpu()
    e = bench_embed()
    if e:
        res["results"]["local-embed"] = e
    for r in resources:
        if r.kind == "llm" and r.available and r.base_url and r.location != "cloud" and r.model:
            b = bench_server(r.base_url, r.model)
            if b:
                res["results"][r.id] = b
    if live:
        for r in resources:
            if r.kind == "llm" and r.available and r.location == "cloud":
                try:
                    res["results"][r.id] = bench_cloud(ci, uid, r.id)
                except Exception as ex:  # noqa: BLE001 - report the failure as data
                    res["results"][r.id] = {"error": f"{type(ex).__name__}: {str(ex)[:160]}"}
    if home is not None:
        p = Path(home) / BENCH_FILE
        p.write_text(json.dumps(res, indent=1))
        os.chmod(p, 0o600)
    return res


def last_bench(home: Path | None) -> dict | None:
    if home is None or not (Path(home) / BENCH_FILE).exists():
        return None
    return json.loads((Path(home) / BENCH_FILE).read_text())


# ---------------------------------------------------------------------- placement
@dataclasses.dataclass
class Placement:
    workload: str
    sensitivity: str
    min_tier: int | None
    chosen: str | None
    fallbacks: list[str]
    reasons: list[str]
    excluded: list[dict]

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def place(resources: list[Resource], workload: str, sensitivity: Sensitivity = Sensitivity.PERSONAL,
          min_tier: int | None = None, measured: dict | None = None,
          allowed_vendors: set[str] | None = None) -> Placement:
    """Privacy first, then capability, then measured speed. A ceiling is never traded for speed."""
    if workload not in WORKLOADS:
        raise ValueError(f"workload must be one of {WORKLOADS}")
    excluded, ok = [], []
    results = (measured or {}).get("results", {})
    if sensitivity is Sensitivity.SECRET:
        return Placement(workload, sensitivity.value, min_tier, None, [], ["SECRET data is never processed by a model"], [])
    for r in resources:
        if workload not in r.workloads:
            continue
        why = None
        if not r.available:
            why = "unavailable"
        elif sensitivity.rank > r.ceiling.rank:
            why = f"{r.location} may receive at most {r.ceiling.value}"
        elif min_tier is not None and r.kind == "llm" and (r.tier or 0) < min_tier:
            why = f"tier {r.tier or 'unrated'} is below the required {min_tier}"
        elif allowed_vendors is not None and r.location == "cloud" and vendor_of(r.id.split(":")[0]) not in allowed_vendors:
            why = "you have not granted this vendor"
        if why:
            excluded.append({"resource": r.id, "why": why})
        else:
            ok.append(r)

    def speed(r: Resource) -> float:
        m = results.get(r.id) or {}
        return -float(m["median"]) if m.get("unit") in ("tokens/s", "sentences/s") else 0.0

    # On-device and your own machines first (privacy and cost), then measured speed, then tier.
    order = {"on-device": 0, "lan-trusted": 1, "lan": 2, "cloud": 3}
    if workload == "embed":
        ok.sort(key=lambda r: (r.model == "hashing", order[r.location], speed(r)))
    else:
        ok.sort(key=lambda r: (order[r.location], -(r.tier or 0), speed(r)))
    reasons = [f"{len(ok)} eligible resource(s) for {workload} at {sensitivity.value}"]
    if not ok:
        reasons.append("nothing you have may run this; add a local model server or grant a vendor")
    return Placement(workload, sensitivity.value, min_tier, ok[0].id if ok else None, [r.id for r in ok[1:4]],
                     reasons, excluded)
