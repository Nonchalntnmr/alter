"""Loading the local model prints nothing the person cannot act on: no torch macOS note, no Hub
"offline mode" notice, no progress bar for a load from disk, and no FutureWarning from our own call.
The same libraries' other warnings, every error, and a first download's progress bars still show."""
import importlib.util
import logging
import os
import subprocess
import sys
import types
import warnings
from pathlib import Path

import pytest

from pcim import embed

REPO = Path(__file__).resolve().parents[1]
TORCH_NOTE = "torch.distributed.elastic.multiprocessing.redirects"
HUB = "huggingface_hub.hf_api"


@pytest.fixture
def clean_loggers():
    torch_logger, hub = logging.getLogger(TORCH_NOTE), logging.getLogger(HUB)
    level, filters = torch_logger.level, list(hub.filters)
    yield
    torch_logger.setLevel(level)
    hub.filters[:] = filters


def test_the_hub_offline_notice_goes_and_other_hub_warnings_stay(clean_loggers, caplog):
    embed._quiet_library_notices()
    embed._quiet_library_notices()
    assert logging.getLogger(HUB).filters.count(embed._OFFLINE_NOTICE) == 1
    with caplog.at_level(logging.WARNING):
        logging.getLogger(HUB).warning("Could not reach the Hub (offline mode is enabled). Using cached commit hash")
        logging.getLogger(HUB).warning("Ignored error while caching commit hash: disk full")
    assert [r.getMessage() for r in caplog.records] == ["Ignored error while caching commit hash: disk full"]


def test_the_torch_macos_note_goes_and_its_errors_stay(clean_loggers, caplog):
    embed._quiet_library_notices()
    with caplog.at_level(logging.WARNING):
        logging.getLogger(TORCH_NOTE).warning("NOTE: Redirects are currently not supported in MacOs.")
        logging.getLogger(TORCH_NOTE).error("a real failure")
    assert [r.getMessage() for r in caplog.records] == ["a real failure"]


class _Bars:
    def __init__(self, on=True):
        self.on = on

    def is_progress_bar_enabled(self):
        return self.on

    def enable_progress_bar(self):
        self.on = True

    def disable_progress_bar(self):
        self.on = False


def _fake_model(bars, seen, *, new_name=True):
    class FakeST:
        def __init__(self, name, device="cpu"):
            seen["bars_during_load"] = bars.on

        def get_sentence_embedding_dimension(self):
            warnings.warn("renamed to get_embedding_dimension", FutureWarning, stacklevel=2)
            return 384

    if new_name:
        FakeST.get_embedding_dimension = lambda self: 384
    return types.SimpleNamespace(SentenceTransformer=FakeST)


def _load(tmp_path, monkeypatch, bars, *, cached, new_name=True):
    home = tmp_path / "home"
    model_dir = home / ".cache" / "huggingface" / "hub" / f"models--{embed.DEFAULT_SEMANTIC_MODEL.replace('/', '--')}"
    if cached:
        model_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    seen = {}
    monkeypatch.setitem(sys.modules, "sentence_transformers", _fake_model(bars, seen, new_name=new_name))
    monkeypatch.setattr(embed, "_progress_bars", lambda: bars)
    se = embed.SentenceEmbedder()
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)  # our own call must not warn
        se._load()
    return se, seen


def test_a_cached_model_loads_without_a_progress_bar_and_puts_bars_back(tmp_path, monkeypatch, clean_loggers):
    bars = _Bars(on=True)
    se, seen = _load(tmp_path, monkeypatch, bars, cached=True)
    assert seen["bars_during_load"] is False and bars.on is True and se.dim == 384


def test_bars_the_person_turned_off_stay_off(tmp_path, monkeypatch, clean_loggers):
    bars = _Bars(on=False)
    _load(tmp_path, monkeypatch, bars, cached=True)
    assert bars.on is False


def test_a_first_download_keeps_its_progress_bars(tmp_path, monkeypatch, clean_loggers):
    bars = _Bars(on=True)
    _, seen = _load(tmp_path, monkeypatch, bars, cached=False)
    assert seen["bars_during_load"] is True and bars.on is True


def test_an_older_sentence_transformers_still_gives_the_dimension(tmp_path, monkeypatch, clean_loggers):
    bars = _Bars(on=True)
    with pytest.raises(FutureWarning):  # only the old name exists, and it warns: we do not hide that
        _load(tmp_path, monkeypatch, bars, cached=True, new_name=False)


@pytest.mark.parametrize("cached", [True, False])
def test_checking_for_the_extra_first_still_loads_a_cached_model_offline(tmp_path, monkeypatch, clean_loggers, cached):
    """huggingface_hub reads HF_HUB_OFFLINE once, on import, so the check (which imports) decides it too."""
    home = tmp_path / "home"
    if cached:
        (home / ".cache" / "huggingface" / "hub" /
         f"models--{embed.DEFAULT_SEMANTIC_MODEL.replace('/', '--')}").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    seen = {}

    class Recorder(types.ModuleType):
        def __getattr__(self, name):
            raise AttributeError(name)

    def find_offline_at_import(name, *a, **k):
        if name == "sentence_transformers":
            seen["offline_at_import"] = os.environ.get("HF_HUB_OFFLINE")
            return Recorder(name)
        return real_import(name, *a, **k)

    import builtins
    real_import = builtins.__import__
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    monkeypatch.setattr(builtins, "__import__", find_offline_at_import)
    assert embed.semantic_available() is True
    assert seen["offline_at_import"] == ("1" if cached else None)


_MODEL_CACHE = (Path.home() / ".cache" / "huggingface" / "hub" /
                f"models--{embed.DEFAULT_SEMANTIC_MODEL.replace('/', '--')}")


@pytest.mark.skipif(importlib.util.find_spec("sentence_transformers") is None or not _MODEL_CACHE.is_dir(),
                    reason="needs the semantic extra and the cached model")
def test_the_real_model_loads_and_embeds_with_nothing_on_stderr():
    env = {k: v for k, v in os.environ.items() if k not in ("HF_HUB_OFFLINE", "HF_HUB_DISABLE_PROGRESS_BARS")}
    env["PYTHONPATH"] = str(REPO / "src")
    code = ("from pcim.embed import SentenceEmbedder, semantic_available; assert semantic_available(); "
            "e = SentenceEmbedder(); print(e.embed(['hello']).shape, e.dim)")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["(1,", "384)", "384"]
    assert r.stderr == ""
