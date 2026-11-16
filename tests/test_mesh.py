"""`ci mesh`: discovery against a real local HTTP server, measured benchmarks, privacy-first placement."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from test_amplify import env  # noqa: F401  (fixture)

from pcim import mesh, terminal
from pcim.types import Sensitivity


class _Models(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"data": [{"id": "qwen-local"}]}).encode()
        self.send_response(200 if self.path.endswith("/models") else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Models)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def R(id_, kind, loc, workloads=("generate", "verify"), tier=None, available=True, model=None):
    return mesh.Resource(id_, kind, loc, available, "test", workloads, tier, model=model)


POOL = [R("local:qwen", "llm", "on-device", tier=1), R("studio:llama", "llm", "lan-trusted", tier=2),
        R("friend:gpu", "llm", "lan", tier=3), R("claude-cli:opus", "llm", "cloud", tier=3),
        R("claude-cli:haiku", "llm", "cloud", tier=1), R("dead:x", "llm", "on-device", available=False),
        R("local-embed", "embed", "on-device", ("embed",), model="minilm"),
        R("hashing-embed", "embed", "on-device", ("embed",), model="hashing")]


def test_sensitive_work_never_leaves_your_own_machines_and_secret_goes_nowhere():
    for tier in (None, 1, 2, 3):
        p = mesh.place(POOL, "generate", Sensitivity.SENSITIVE, tier)
        chosen = [p.chosen] + p.fallbacks if p.chosen else []
        assert all(c in ("local:qwen", "studio:llama") for c in chosen), (tier, chosen)
    assert mesh.place(POOL, "generate", Sensitivity.SENSITIVE, 3).chosen is None  # nothing local is strong enough
    s = mesh.place(POOL, "generate", Sensitivity.SECRET)
    assert s.chosen is None and "never" in s.reasons[0]


def test_capability_vendor_grants_and_availability_filter_before_speed():
    p = mesh.place(POOL, "generate", Sensitivity.PERSONAL, 3, allowed_vendors={"anthropic"})
    assert p.chosen == "friend:gpu" and "claude-cli:opus" in p.fallbacks
    q = mesh.place(POOL, "generate", Sensitivity.PERSONAL, 3, allowed_vendors=set())
    assert q.chosen == "friend:gpu" and "claude-cli:opus" not in q.fallbacks
    assert any(x["resource"] == "claude-cli:opus" and "not granted" in x["why"] for x in q.excluded)
    assert any(x["resource"] == "dead:x" and x["why"] == "unavailable" for x in q.excluded)


def test_embedding_prefers_a_real_local_model_over_the_lexical_fallback():
    assert mesh.place(POOL, "embed", Sensitivity.SENSITIVE).chosen == "local-embed"
    assert mesh.place([r for r in POOL if r.id != "local-embed"], "embed").chosen == "hashing-embed"
    with pytest.raises(ValueError):
        mesh.place(POOL, "train")


def test_discovery_finds_a_listening_server_and_lists_configured_ones_even_when_down(server, tmp_path, env):
    (tmp_path / "mesh.json").write_text(json.dumps({"endpoints": [
        {"name": "mine", "base_url": server},
        {"name": "studio", "base_url": "http://10.255.255.1:11434/v1", "trusted": True},
        {"name": "cafe", "base_url": "http://10.255.255.2:11434/v1"}]}))
    res = {r.id: r for r in mesh.discover(tmp_path, probe_timeout=0.3)}
    assert res["mine:qwen-local"].available and res["mine:qwen-local"].location == "on-device"
    assert not res["studio"].available and res["studio"].location == "lan-trusted"
    assert res["studio"].ceiling is Sensitivity.SENSITIVE and res["cafe"].ceiling is Sensitivity.PERSONAL
    assert res["device"].available and "cores" in res["device"].detail
    assert any(r.location == "cloud" for r in res.values())  # registry models are listed, not merged


def test_a_bad_config_is_an_error_not_a_silent_skip(tmp_path):
    (tmp_path / "mesh.json").write_text('{"endpoints": [{"name": "x", "base_url": "file:///etc/passwd"}]}')
    with pytest.raises(ValueError, match="http"):
        mesh.load_config(tmp_path)


def test_bench_measures_and_saves_owner_only(tmp_path):
    out = mesh.bench(tmp_path, [], live=False)
    cpu = out["results"]["device"]
    assert cpu["unit"] == "GFLOP/s" and 0 < cpu["min"] <= cpu["median"] <= cpu["max"] and cpu["reps"] == 5
    f = tmp_path / mesh.BENCH_FILE
    assert f.exists() and (os.stat(f).st_mode & 0o777) == 0o600
    assert mesh.last_bench(tmp_path)["measured_at"] == out["measured_at"]


def test_terminal_place_exit_code_says_whether_anything_may_run(env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PCIM_HOME", str(tmp_path))
    rc = terminal.main(["mesh", "place", "generate", "--sensitivity", "secret"])
    assert rc == 1 and "nowhere" in capsys.readouterr().out
    assert terminal.main(["mesh", "place", "embed", "--sensitivity", "sensitive"]) == 0


def test_the_local_provider_is_on_device_and_the_host_is_not_a_service(env, tmp_path, monkeypatch):
    monkeypatch.setenv("PCIM_LOCAL_MODEL", "qwen")
    res = {r.id: r for r in mesh.discover(tmp_path, probe_timeout=0.2)}
    local = [r for r in res.values() if r.id.startswith("local:")]
    assert local and all(r.location == "on-device" and r.ceiling is Sensitivity.SENSITIVE for r in local)
    assert res["device"].workloads == ()
    assert "device" not in [mesh.place(list(res.values()), "embed").chosen] + mesh.place(list(res.values()), "embed").fallbacks
