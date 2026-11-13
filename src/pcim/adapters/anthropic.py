"""Anthropic adapter, on the official `anthropic` SDK (1.x).

- The stable system section (preamble + directives + confirmed profile) carries a
  `cache_control` breakpoint, so repeat requests from the same person read it from the
  prompt cache. It only caches once it is ≥512 tokens on current models; shorter prefixes
  silently do not, and `usage.cache_read_input_tokens` stays 0.
- Retrieved notes and the task go in the user turn, after the breakpoint: they change
  every request and must not invalidate the cached prefix.
- `stop_reason == "refusal"` is checked before content is read.
- Server-side refusal fallbacks (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`) are on by default for the models that support them;
  pass `fallbacks=False` to opt out. Claude Haiku 5.5 has no server-side fallback.
- No temperature/top_p: current models reject sampling parameters.
- Failures are ProviderError with `kind`, `retryable` and `status` (base.py): SDK status,
  timeout, connection and validation errors, an unparseable or shapeless reply, and no
  credential at all (which the SDK reports as a TypeError at request time). The SDK's own
  messages are not copied: they embed the error body. Any other exception propagates.
"""

from __future__ import annotations

import contextlib
import json
import os
import time

from .base import AgentStep, ModelResponse, ProviderError, RenderedPrompt, Usage, malformed_response, status_failure
from .pricing import cost_usd

FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
PROVIDER = "anthropic"


def _secrets(client) -> tuple[str, ...]:
    return tuple(v for v in (getattr(client, "api_key", None), getattr(client, "auth_token", None))
                 if isinstance(v, str) and v)


@contextlib.contextmanager
def _provider_errors(client, request: dict | None = None):
    """SDK failures as ProviderError. `request` is used only to keep provider-supplied tokens
    that occur in it out of the message."""
    import anthropic

    try:
        yield
    except anthropic.APIStatusError as e:
        avoid = (json.dumps(request, default=str),) if request else ()
        raise status_failure(PROVIDER, e.status_code, code=getattr(e, "type", None),
                             request_id=getattr(e, "request_id", None), avoid=avoid,
                             secrets=_secrets(client)) from None
    except anthropic.APITimeoutError:
        raise ProviderError(f"{PROVIDER} request timed out (APITimeoutError)", retryable=True, kind="timeout") from None
    except anthropic.APIConnectionError:
        raise ProviderError(f"{PROVIDER} request failed: could not connect or the connection dropped "
                            "(APIConnectionError)", retryable=True, kind="connection") from None
    except anthropic.APIResponseValidationError:
        raise malformed_response(PROVIDER, "failed the SDK's schema check") from None
    except anthropic.APIError as e:
        raise ProviderError(f"{PROVIDER} API error ({type(e).__name__})") from None
    except json.JSONDecodeError:  # a 200 whose body is not JSON escapes the SDK as a bare JSONDecodeError
        raise malformed_response(PROVIDER, "not JSON") from None
    except TypeError as e:
        if "authentication method" not in str(e):  # only the SDK's "no credential" check; a real TypeError is a bug
            raise
        raise ProviderError(f"{PROVIDER}: no API key or credential is configured (ANTHROPIC_API_KEY is not set)",
                            kind="no_credentials") from None
    except anthropic.AnthropicError as e:
        if not _is_credential_error(anthropic, e):
            raise
        raise ProviderError(f"{PROVIDER} credentials could not be loaded ({type(e).__name__})",
                            kind="no_credentials") from None


def _is_credential_error(anthropic, e: Exception) -> bool:
    kinds = tuple(k for k in (getattr(anthropic, "CredentialsError", None), getattr(anthropic, "WorkloadIdentityError", None))
                  if isinstance(k, type))
    return bool(kinds) and isinstance(e, kinds)


def _check_shape(resp) -> None:
    """A reply the SDK could not type (an HTML page, `{}`, a list) has no usage or content list."""
    if getattr(resp, "usage", None) is None or not isinstance(getattr(resp, "content", None), list):
        raise malformed_response(PROVIDER, "no content or usage")


class AnthropicAdapter:
    provider = "anthropic"

    def __init__(
        self,
        model: str = "claude-opus-5-5",
        effort: str = "medium",
        max_tokens: int = 16000,
        fallbacks: bool = True,
        client=None,
        timeout: float = 120.0,
    ):
        try:
            import anthropic  # optional dependency: pip install "contextual-intelligence[anthropic]"
        except ImportError:
            raise ProviderError('the anthropic SDK is not installed: pip install "contextual-intelligence[anthropic]"',
                                kind="config") from None

        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.fallbacks = fallbacks and model in FALLBACK_MODELS
        # The endpoint is pinned: ANTHROPIC_BASE_URL in the environment would otherwise send
        # content a project granted to Anthropic to a proxy. PCIM_ANTHROPIC_BASE_URL is the
        # explicit opt-in for an endpoint the person counts as Anthropic.
        if client is None:
            with _provider_errors(None):  # a configured-but-unloadable credential profile raises here
                client = anthropic.Anthropic(
                    timeout=timeout, base_url=os.environ.get("PCIM_ANTHROPIC_BASE_URL") or "https://api.anthropic.com")
        self.client = client

    def _request(self, prompt: RenderedPrompt, max_tokens: int | None) -> dict:
        return dict(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            system=[{"type": "text", "text": prompt.system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt.user}],
            output_config={"effort": self.effort},
        )

    def generate(self, prompt: RenderedPrompt, max_tokens: int | None = None) -> ModelResponse:
        kwargs = self._request(prompt, max_tokens)
        t0 = time.perf_counter()
        with _provider_errors(self.client, kwargs):
            if self.fallbacks:
                resp = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        latency = (time.perf_counter() - t0) * 1000
        _check_shape(resp)
        u = resp.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        refusal = resp.stop_reason == "refusal"
        text = "" if refusal else "".join(b.text for b in resp.content if b.type == "text")
        if refusal:
            details = getattr(resp, "stop_details", None)
            cat = getattr(details, "category", None) if details else None
            text = f"[declined by the model{f' ({cat})' if cat else ''}]"
        served_by = resp.model or self.model
        return ModelResponse(
            text=text, usage=usage, cost_usd=cost_usd(served_by, usage), latency_ms=latency,
            stop_reason=resp.stop_reason or "", model=served_by, provider=self.provider,
            request_id=getattr(resp, "_request_id", None) or getattr(resp, "id", None), refusal=refusal,
        )

    def agent_step(self, system: str, messages: list[dict], tools: list[dict],
                   max_tokens: int | None = None) -> AgentStep:
        """One turn of a tool-using session (manual loop, so every call passes the governor).

        Tools are sent with `strict: true` and `tool_choice` left at auto, as current models
        require. The assistant content is returned as plain dicts to be appended back
        unchanged.
        """
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            tools=[{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"],
                    "strict": True} for t in tools],
            output_config={"effort": self.effort},
        )
        with _provider_errors(self.client, kwargs):
            if self.fallbacks:
                resp = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        _check_shape(resp)
        u = resp.usage
        usage = Usage(input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0,
                      cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                      cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0)
        content = [b.model_dump(mode="json", exclude_none=True) for b in resp.content]
        refusal = resp.stop_reason == "refusal"
        text = "" if refusal else "".join(b.text for b in resp.content if b.type == "text")
        tool_uses = [] if refusal else [{"id": b.id, "name": b.name, "input": b.input}
                                        for b in resp.content if b.type == "tool_use"]
        served = resp.model or self.model
        return AgentStep(content=content, text=text or ("[declined by the model]" if refusal else ""),
                         tool_uses=tool_uses, stop_reason=resp.stop_reason or "", usage=usage,
                         cost_usd=cost_usd(served, usage), model=served, refusal=refusal)

    def count_tokens(self, prompt: RenderedPrompt) -> int:
        """Exact pre-flight input count from the API (budgeting uses an estimate otherwise)."""
        r = self.client.messages.count_tokens(
            model=self.model, system=prompt.system, messages=[{"role": "user", "content": prompt.user}]
        )
        return r.input_tokens
