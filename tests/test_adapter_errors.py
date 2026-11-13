"""Provider failures on the single-turn ask path, for every adapter: each one comes back as a
ProviderError with `retryable`, `status` and `kind`, so routing can fall back, and no
message ever carries the API key or any part of the request.

The fake servers are hostile on purpose: their error bodies, reason phrases, request-id
headers and exception texts echo the request and the key. No network: httpx / httpx2
MockTransport and fake subprocess runners only.
"""

import json
import pickle
import subprocess

import httpx
import pytest

from pcim.adapters import ContextPacket, PacketItem, render
from pcim.adapters.base import ModelResponse, ProviderError, Usage
from pcim.adapters.claude_cli import ClaudeCLIAdapter, ClaudeCLIError
from pcim.adapters.gemini import GeminiAdapter
from pcim.adapters.openai_compat import OpenAICompatAdapter
from pcim.engine import PersonalCI, RoutingError, fallback_errors

KEY = "sk-TESTKEY-7f3a9c1e5b2d4a6f8e0c"
PRIVATE = "Meera's surprise is on 14 March at Saffron"  # a selected note: the user turn
PINNED = "Prefers answers in metric units, always"  # a confirmed profile item: the system section
TASK = "Plan the anniversary dinner for next Friday"
NEVER = (KEY, KEY[:12], KEY[-12:], PRIVATE, PINNED, TASK, "Saffron", "Bearer")


def _prompt():
    item = PacketItem("mem_1", "fact", PRIVATE, "user", "user_stated", "2026-05-02", 1.0)
    pin = PacketItem("mem_2", "preference", PINNED, "user", "user_confirmed", "2025-01-01", 1.0)
    return render(ContextPacket("int_1", TASK, ["Be concise."], [pin], [item], nonce="n0nce"))


def assert_clean(e):
    for text in (str(e), repr(e)):
        leaked = [s for s in NEVER if s in text]
        assert not leaked, f"{leaked} in {text!r}"


def _failure(adapter, call=None):
    with pytest.raises(ProviderError) as info:
        (call or (lambda: adapter.generate(_prompt())))()
    e = info.value
    assert isinstance(e, fallback_errors()) and isinstance(e, RuntimeError)  # routing falls back on it
    assert_clean(e)
    return e


# ---------------------------------------------------------------- hostile fake servers
def _echo(req) -> str:
    """Everything a careless error path could copy: the body and every credential header."""
    return f"{req.content.decode()} {req.headers.get('authorization')} {req.headers.get('x-goog-api-key')} " \
           f"{req.headers.get('x-api-key')}"


def http_case(mod, case):
    """A MockTransport handler for `mod` (httpx or httpx2) that fails as `case` says."""
    def handler(req):
        if case == "timeout":
            raise mod.ReadTimeout(f"timed out sending {_echo(req)}", request=req)
        if case == "connect":
            raise mod.ConnectError(f"[Errno 61] Connection refused while sending {_echo(req)}", request=req)
        if case == "not_json":
            return mod.Response(200, content=f"<html>proxy login {_echo(req)}</html>".encode(),
                                headers={"content-type": "application/json"})
        if case == "not_object":
            return mod.Response(200, json=[_echo(req)])
        status = int(case)
        return mod.Response(status, json={"error": {"code": status, "status": "Saffron", "type": "Saffron",
                                                    "message": f"rejected {_echo(req)}"}},
                            headers={"x-request-id": KEY, "request-id": KEY},
                            extensions={"reason_phrase": KEY.encode()})
    return handler


# case -> (kind, retryable, status)
EXPECTED = {
    "timeout": ("timeout", True, None),
    "connect": ("connection", True, None),
    "400": ("request", False, 400),
    "401": ("auth", False, 401),
    "403": ("permission", False, 403),
    "404": ("request", False, 404),
    "408": ("timeout", True, 408),
    "429": ("rate_limit", True, 429),
    "500": ("server", True, 500),
    "503": ("server", True, 503),
    "not_json": ("malformed", False, None),
    "not_object": ("malformed", False, None),
}


def gemini(handler):
    return GeminiAdapter("gemini-test", api_key=KEY, transport=httpx.MockTransport(handler))


def openai(handler):
    return OpenAICompatAdapter("gpt-test", api_key=KEY, transport=httpx.MockTransport(handler))


def local(handler):
    return OpenAICompatAdapter("llama-test", provider="local", base_url="http://127.0.0.1:11434/v1", api_key=KEY,
                               transport=httpx.MockTransport(handler))


HTTP_ADAPTERS = {"google": gemini, "openai": openai, "local": local}


# ---------------------------------------------------------------- gemini, openai, local
@pytest.mark.parametrize("case", list(EXPECTED))
@pytest.mark.parametrize("name", list(HTTP_ADAPTERS))
def test_http_adapters_report_every_failure_as_a_classified_provider_error(name, case):
    e = _failure(HTTP_ADAPTERS[name](http_case(httpx, case)))
    assert (e.kind, e.retryable, e.status) == EXPECTED[case]
    assert str(e).startswith(name)
    if case.isdigit():
        assert f"HTTP {case}" in str(e)  # the status and the stdlib phrase, never the server's reason phrase


@pytest.mark.parametrize("name", list(HTTP_ADAPTERS))
def test_a_json_reply_of_the_wrong_shape_is_malformed_not_a_raw_key_error(name):
    shape = {"google": {"candidates": [{"content": "not a dict"}]}, "openai": {"choices": []}, "local": {"id": "x"}}
    e = _failure(HTTP_ADAPTERS[name](lambda req: httpx.Response(200, json=shape[name])))
    assert (e.kind, e.retryable, e.status) == ("malformed", False, None) and "malformed" in str(e)


def test_missing_keys_are_provider_errors_and_still_runtime_errors(monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for make in (lambda: GeminiAdapter("gemini-test"), lambda: OpenAICompatAdapter("gpt-test")):
        with pytest.raises(RuntimeError) as info:  # what Gemini raised before; ProviderError is one
            make()
        e = info.value
        assert isinstance(e, ProviderError) and isinstance(e, fallback_errors())
        assert (e.kind, e.retryable, e.status, e.credential_problem) == ("no_credentials", False, None, True)
        assert "is not set" in str(e)


def test_gemini_reports_a_rejected_key_as_auth_although_it_answers_400():
    def handler(req):
        return httpx.Response(400, json={"error": {"code": 400, "message": "API key not valid. " + _echo(req),
                                                   "status": "INVALID_ARGUMENT",
                                                   "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                                                "reason": "API_KEY_INVALID"}]}})
    e = _failure(gemini(handler))
    assert (e.kind, e.retryable, e.status, e.credential_problem) == ("auth", False, 400, True)
    assert "API_KEY_INVALID" in str(e) and "credential was rejected" in str(e)


def test_openai_messages_carry_the_code_and_request_id_and_quota_is_not_retryable():
    def rejected(req):
        return httpx.Response(401, json={"error": {"message": "Incorrect API key provided: sk-TESTK***8e0c",
                                                   "type": "invalid_request_error", "code": "invalid_api_key"}},
                              headers={"x-request-id": "req_abc123"})
    e = _failure(openai(rejected))
    assert str(e) == ("openai returned HTTP 401 Unauthorized from api.openai.com: the credential was rejected "
                      "(invalid_api_key); request id req_abc123")
    assert "sk-TESTK" not in str(e)

    def broke(req):
        return httpx.Response(429, json={"error": {"message": "You exceeded your current quota", "type": "insufficient_quota",
                                                   "code": "insufficient_quota"}})
    e = _failure(openai(broke))
    assert (e.kind, e.retryable, e.status) == ("quota", False, 429)

    def ollama(req):  # {"error": "<free text>"}: nothing of it is copied
        return httpx.Response(404, json={"error": f"model 'llama-test' not found, request {_echo(req)}"})
    e = _failure(local(ollama))
    assert (e.kind, e.status) == ("request", 404) and "127.0.0.1:11434" in str(e) and "not found" in str(e)


def test_openai_agent_step_failures_are_normalised_too():
    msgs = [{"role": "user", "content": TASK + " " + PRIVATE}]
    for case in ("timeout", "401", "429", "not_json"):
        ad = openai(http_case(httpx, case))
        e = _failure(ad, lambda: ad.agent_step(PINNED, msgs, []))
        assert (e.kind, e.retryable, e.status) == EXPECTED[case]
    ad = openai(lambda req: httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"function": {}}]}}]}))
    e = _failure(ad, lambda: ad.agent_step(PINNED, msgs, []))  # a tool call with no id: was a raw KeyError
    assert e.kind == "malformed"


def test_a_local_request_never_goes_through_a_proxy_from_the_environment(monkeypatch):
    for k in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "all_proxy", "https_proxy", "http_proxy"):
        monkeypatch.setenv(k, "http://proxy.example:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    url = httpx.URL("http://127.0.0.1:11434/v1/chat/completions")
    ad = OpenAICompatAdapter("llama-test", provider="local", base_url="http://127.0.0.1:11434/v1")  # real transport
    assert ad.http.trust_env is False and not ad.http._mounts
    assert ad.http._transport_for_url(url) is ad.http._transport  # direct, no proxy pool
    # openai keeps the environment's proxy settings (unchanged)
    oa = OpenAICompatAdapter("gpt-test", api_key=KEY)
    assert oa.http._transport_for_url(httpx.URL("https://api.openai.com/v1/chat/completions")) is not oa.http._transport


def test_the_local_provider_check_names_the_host_but_not_credentials_in_the_url():
    with pytest.raises(ProviderError, match="on this machine") as info:
        OpenAICompatAdapter("m", provider="local", base_url="http://user:hunter2pass@evil.example.com/v1?key=abc123")
    assert info.value.kind == "config" and "evil.example.com" in str(info.value)
    assert "hunter2pass" not in str(info.value) and "abc123" not in str(info.value)


# ---------------------------------------------------------------- anthropic (official SDK)
def anthropic_adapter(handler, model="claude-opus-5-5"):
    anthropic = pytest.importorskip("anthropic")
    import httpx2
    from pcim.adapters.anthropic import AnthropicAdapter

    client = anthropic.Anthropic(api_key=KEY, max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    return AnthropicAdapter(model, client=client)


ANTHROPIC_EXPECTED = {**{k: v for k, v in EXPECTED.items() if k != "not_object"},
                      "529": ("server", True, 529), "not_object": ("malformed", False, None),
                      "html": ("malformed", False, None), "empty": ("malformed", False, None)}


def _anthropic_handler(case):
    httpx2 = pytest.importorskip("httpx2")
    if case == "html":  # the SDK hands back a str for a non-JSON content type
        return lambda req: httpx2.Response(200, content=b"<html>captive portal</html>", headers={"content-type": "text/html"})
    if case == "empty":
        return lambda req: httpx2.Response(200, json={})
    return http_case(httpx2, case)


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-haiku-5-5"])  # beta (fallbacks) and plain paths
@pytest.mark.parametrize("case", list(ANTHROPIC_EXPECTED))
def test_anthropic_reports_every_failure_as_a_classified_provider_error(case, model):
    e = _failure(anthropic_adapter(_anthropic_handler(case), model))
    assert (e.kind, e.retryable, e.status) == ANTHROPIC_EXPECTED[case]
    assert str(e).startswith("anthropic")


def test_anthropic_status_messages_keep_the_error_type_and_request_id():
    httpx2 = pytest.importorskip("httpx2")

    def handler(req):
        return httpx2.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": _echo(req)}},
                               headers={"request-id": "req_011abc"})
    e = _failure(anthropic_adapter(handler))
    assert str(e) == "anthropic returned HTTP 429 Too Many Requests: rate limited (rate_limit_error); request id req_011abc"


def test_anthropic_agent_step_failures_are_normalised_too():
    for case in ("timeout", "401", "529", "empty"):
        ad = anthropic_adapter(_anthropic_handler(case))
        e = _failure(ad, lambda: ad.agent_step(PINNED, [{"role": "user", "content": TASK + " " + PRIVATE}], []))
        assert (e.kind, e.retryable, e.status) == ANTHROPIC_EXPECTED[case]


def _no_anthropic_credentials(monkeypatch, tmp_path):
    import os
    for k in list(os.environ):
        if k.startswith("ANTHROPIC_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("HOME", str(tmp_path))  # no on-disk SDK profile either
    monkeypatch.setenv("PCIM_ANTHROPIC_BASE_URL", "http://127.0.0.1:9")  # nothing could leave the machine anyway


def test_anthropic_with_no_credential_at_all_is_a_provider_error_not_a_type_error(monkeypatch, tmp_path):
    pytest.importorskip("anthropic")
    from pcim.adapters.anthropic import AnthropicAdapter

    _no_anthropic_credentials(monkeypatch, tmp_path)
    e = _failure(AnthropicAdapter("claude-opus-5-5"))  # the SDK raises TypeError at request time
    assert (e.kind, e.retryable, e.status, e.credential_problem) == ("no_credentials", False, None, True)
    assert "ANTHROPIC_API_KEY is not set" in str(e)


def test_anthropic_with_an_unloadable_credential_profile_fails_at_construction_as_a_provider_error(monkeypatch, tmp_path):
    pytest.importorskip("anthropic")
    from pcim.adapters.anthropic import AnthropicAdapter

    _no_anthropic_credentials(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "empty"))  # selects a profile that does not exist
    e = _failure(None, lambda: AnthropicAdapter("claude-opus-5-5"))
    assert e.kind == "no_credentials" and "CredentialsError" in str(e)


def test_anthropic_bugs_still_propagate():
    ad = anthropic_adapter(_anthropic_handler("200"))

    def bad_call(**kw):
        raise TypeError("create() got an unexpected keyword argument 'temperature'")
    ad.client.beta.messages.create = bad_call
    with pytest.raises(TypeError, match="unexpected keyword"):
        ad.generate(_prompt())


# ---------------------------------------------------------------- claude CLI (fake runner)
def cli(stdout, stderr="", rc=1, raises=None, echo=False):
    """A runner standing in for subprocess.run. With `echo`, the CLI's output quotes the request,
    the system prompt file and the key it was given, as a careless crash dump would."""
    seen = {}

    def run(argv, input, **kw):
        if raises:
            raise raises
        dump = ""
        if echo:
            system = open(argv[argv.index("--system-prompt-file") + 1]).read()
            dump = f" while handling {input!r} with system {system!r} and key {kw['env'].get('ANTHROPIC_API_KEY')}"
        seen["argv"] = argv
        out = stdout(dump) if callable(stdout) else stdout
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr=stderr + dump)
    return run


def cli_error(result, subtype="success"):
    return lambda dump="": json.dumps({"type": "result", "subtype": subtype, "is_error": True, "result": result + dump})


# case -> (runner arguments, (kind, retryable, status))
CLI_CASES = {
    "timeout": (dict(stdout="", raises=subprocess.TimeoutExpired(["claude"], 1)), ("timeout", True, None)),
    "api_timeout": (dict(stdout=cli_error("API Error: Request timed out.")), ("timeout", True, None)),
    "connect": (dict(stdout=cli_error("API Error: Connection error.")), ("connection", True, None)),
    "401": (dict(stdout=cli_error('Failed to authenticate. API Error: 401 {"type":"error","error":{"type":'
                                  '"authentication_error","message":"Invalid bearer token"}}')), ("auth", False, 401)),
    "429": (dict(stdout=cli_error('API Error: 429 {"type":"error","error":{"type":"rate_limit_error"}}')),
            ("rate_limit", True, 429)),
    "500": (dict(stdout=cli_error('API Error: 500 {"type":"error","error":{"type":"api_error"}}')), ("server", True, 500)),
    "529": (dict(stdout=cli_error('API Error: 529 {"type":"error","error":{"type":"overloaded_error"}}')),
            ("server", True, 529)),
    "credit": (dict(stdout=cli_error("Credit balance is too low")), ("quota", False, None)),
    "not_json": (dict(stdout="<html>not json", rc=0), ("malformed", False, None)),
    "not_object": (dict(stdout='["a", "list"]', rc=0), ("malformed", False, None)),
    "missing_key": (dict(stdout=cli_error("Not logged in · Please run /login")), ("no_credentials", False, None)),
    "rejected_key_stderr": (dict(stdout="", stderr="Invalid API key · Please run /login"), ("auth", False, None)),
    "no_binary": (dict(stdout="", raises=FileNotFoundError("claude")), ("config", False, None)),
    "not_executable": (dict(stdout="", raises=PermissionError(13, "Permission denied")), ("config", False, None)),
}


@pytest.mark.parametrize("echo", [False, True])
@pytest.mark.parametrize("case", list(CLI_CASES))
def test_claude_cli_reports_every_failure_as_a_classified_provider_error(case, echo, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)  # what the child CLI would authenticate with
    kwargs, expected = CLI_CASES[case]
    runner = cli(**kwargs, echo=echo)
    e = _failure(ClaudeCLIAdapter(workdir=tmp_path / "s", runner=runner))
    assert isinstance(e, ClaudeCLIError) and (e.kind, e.retryable, e.status) == expected
    ad = ClaudeCLIAdapter(workdir=tmp_path / "s", runner=runner)
    e = _failure(ad, lambda: ad.agent_step(PINNED, [{"role": "user", "content": TASK + " " + PRIVATE}], []))
    assert (e.kind, e.retryable, e.status) == expected


def test_claude_cli_output_that_quotes_the_request_is_withheld_and_keys_are_masked(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    ad = ClaudeCLIAdapter(workdir=tmp_path / "s", runner=cli(cli_error("API Error: 500 boom"), echo=True))
    e = _failure(ad)
    assert e.kind == "server" and "withheld" in str(e)  # classified from the raw text, reported without it
    ad = ClaudeCLIAdapter(workdir=tmp_path / "s", runner=cli("", stderr=f"crashed; key {KEY}, also sk-ant-api03-abcdefgh"))
    e = _failure(ad)
    assert str(e) == "claude CLI exited 1 without JSON: crashed; key [redacted], also [redacted]"


def test_provider_error_attributes_are_backwards_compatible_and_survive_pickling():
    old = ProviderError("down")
    assert (old.retryable, old.status, old.kind, old.credential_problem, str(old)) == (False, None, "unknown", False, "down")
    assert ClaudeCLIError("x").kind == "unknown"
    e = pickle.loads(pickle.dumps(ProviderError("busy", retryable=True, status=503, kind="server")))
    assert (str(e), e.retryable, e.status, e.kind) == ("busy", True, 503, "server")


# ---------------------------------------------------------------- through the engine
class Recorder:
    can_plan = False

    def __init__(self, model, fail=None):
        self.provider, self.model, self.fail, self.prompts = "claude-cli", model, fail, []

    def generate(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        if self.fail:
            raise self.fail
        return ModelResponse(text=f"answer from {self.model}", usage=Usage(10, 5), cost_usd=0.001, latency_ms=5.0,
                             stop_reason="end_turn", model=self.model, provider=self.provider)


@pytest.mark.parametrize("case", ["not_json", "401", "timeout"])
def test_a_gemini_failure_falls_back_to_another_vendor_and_the_record_carries_no_key(case, monkeypatch, tmp_path):
    """A 200 that is not JSON used to escape ask_routed as a bare JSONDecodeError."""
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "PCIM_OPENAI_MODEL", "GOOGLE_API_KEY",
              "PCIM_LOCAL_MODEL", "PCIM_LOCAL_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\nexit 1\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PCIM_CLAUDE_BIN", str(binary))
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("PCIM_GEMINI_MODEL", "gemini-test")
    caps = tmp_path / "caps.json"
    caps.write_text(json.dumps({"google:gemini-test": {"tier": 2}}))
    monkeypatch.setenv("PCIM_CAPABILITIES", str(caps))
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", "U", ("claude-cli", "google"))
    recs = {m: Recorder(m, fail=None if m == "haiku" else ProviderError("down")) for m in ("haiku", "sonnet", "opus")}
    for m, r in recs.items():
        ci.set_adapter("claude-cli", m, r)
    ci.set_adapter("google", "gemini-test", gemini(http_case(httpx, case)))

    r = ci.ask_routed("u", "What does HTTP status 429 mean?")
    route = r.receipt["route"]
    assert route["served_by"] == "claude-cli:haiku"
    google = [a for a in route["failed_before"] if a["model"] == "google:gemini-test"]
    assert len(google) == 1 and google[0]["error"].startswith("ProviderError: google")
    record = json.dumps([r.receipt, ci.audit.entries("u")], default=str)
    assert KEY not in record and KEY[:12] not in record

    with pytest.raises(RoutingError) as info:  # pinned: no fallback, but a clean refusal instead of a crash
        ci.ask_routed("u", "What does HTTP status 429 mean?", pinned="google:gemini-test")
    assert [a["model"] for a in info.value.attempts] == ["google:gemini-test"]
def test_a_credential_failure_skips_that_providers_other_models_and_is_reported(monkeypatch, tmp_path):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "PCIM_OPENAI_MODEL", "GOOGLE_API_KEY",
              "PCIM_LOCAL_MODEL", "PCIM_LOCAL_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\nexit 1\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PCIM_CLAUDE_BIN", str(binary))
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("PCIM_GEMINI_MODEL", "gemini-test")
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", "U", ("claude-cli", "google"))
    recs = {m: Recorder(m, fail=ClaudeCLIError("Not logged in", kind="no_credentials")) for m in ("haiku", "sonnet", "opus")}
    for m, r in recs.items():
        ci.set_adapter("claude-cli", m, r)
    ci.set_adapter("google", "gemini-test", gemini(http_case(httpx, "401")))
    with pytest.raises(RoutingError, match=r"credential problem: claude-cli, google") as info:
        ci.ask_routed("u", "What does HTTP status 429 mean?")
    tried = [r for r in recs.values() if r.prompts]
    assert len(tried) == 1  # the first claude-cli failure stands for its other models
    skipped = [a for a in info.value.attempts if a["kind"] == "skipped"]
    assert len(skipped) == 2 and all("claude-cli has a credential problem" in a["error"] for a in skipped)
    assert any(a.get("status") == 401 and a.get("credential_problem") for a in info.value.attempts)
