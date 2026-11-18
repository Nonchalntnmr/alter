"""A broken pipe on CI's own output is a local error, not a provider failure (READINESS §4.5).

Before: with `ci plan "…" 2>&1 | head -1`, a write to the closed stderr inside the routed call
was recorded as a failure of every routed model, and the request "failed" with a RoutingError.
"""
import pytest
from test_amplify import env, make  # noqa: F401  (env is a fixture)

from pcim.adapters.base import ModelResponse, ProviderError, Usage
from pcim.engine import RoutingError


class Raises:
    provider = "claude-cli"

    def __init__(self, model, exc, log):
        self.model, self.exc, self.log = model, exc, log

    def generate(self, prompt, max_tokens=None):
        self.log.append(self.model)
        raise self.exc


def _install(ci, exc):
    log = []
    for m in ("haiku", "sonnet", "opus"):
        ci.set_adapter("claude-cli", m, Raises(m, exc, log))
    return log


def test_a_broken_pipe_on_our_own_output_propagates_without_blaming_any_model(env, tmp_path):
    ci, _ = make(tmp_path)
    log = _install(ci, BrokenPipeError(32, "Broken pipe"))
    with pytest.raises(BrokenPipeError):
        ci.ask_routed("u", "What does HTTP 429 mean?")
    assert len(log) == 1                                            # no fallback to the other models
    assert not [e for e in ci.audit.entries("u") if e["event"] == "route.fallback"]


def test_a_real_provider_failure_still_falls_back_to_the_next_model(env, tmp_path):
    ci, _ = make(tmp_path)
    log = _install(ci, ProviderError("API Error: 529 overloaded", retryable=True, kind="overloaded"))
    with pytest.raises(RoutingError):
        ci.ask_routed("u", "What does HTTP 429 mean?")
    assert len(log) == 3
    assert len([e for e in ci.audit.entries("u") if e["event"] == "route.fallback"]) == 3
