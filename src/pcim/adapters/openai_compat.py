"""OpenAI-compatible Chat Completions adapter over httpx.

One request shape covers OpenAI itself and the many servers that speak it (vLLM, Ollama,
LM Studio, llama.cpp server, OpenRouter, Together, ...). `provider="local"` points it at
a server on this machine, which is the one configuration where SENSITIVE items may be
granted egress without leaving the device.

Written against the documented /v1/chat/completions shape and tested against a mock
transport. **Not yet run against a live endpoint** in this repository's test suite.

Tool use (`agent_step`) translates CI's canonical transcript (Anthropic-shaped content
blocks: text, tool_use, tool_result) into chat messages with `tool_calls` and `tool`
results, and back. A session is bound to one provider, so the translation only ever runs
in one direction per conversation.

Every failure to get a usable reply is a ProviderError with `kind`, `retryable` and `status`
(base.py). Messages name the provider, host, HTTP status and the error code token at most;
the provider's error text is never copied (it can echo the request or part of the key).
"""

from __future__ import annotations

import json
import os
import time
from urllib.parse import urlparse

import httpx

from .base import (AgentStep, ModelResponse, ProviderError, RenderedPrompt, Usage, malformed_response,
                   status_failure, transport_failure, url_host)
from .pricing import cost_usd

DEFAULT_BASE = {"openai": "https://api.openai.com/v1", "local": "http://127.0.0.1:11434/v1"}


class OpenAICompatAdapter:
    def __init__(
        self,
        model: str,
        provider: str = "openai",
        base_url: str | None = None,
        api_key: str | None = None,
        max_tokens: int = 4096,
        system_role: str = "system",
        token_param: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 120.0,
    ):
        self.model, self.provider = model, provider
        env_base = os.environ.get("PCIM_OPENAI_BASE_URL" if provider == "openai" else "PCIM_LOCAL_BASE_URL")
        self.base_url = (base_url or env_base or DEFAULT_BASE[provider]).rstrip("/")
        host = urlparse(self.base_url).hostname
        if provider == "local" and host not in ("127.0.0.1", "localhost", "::1"):
            # "local" is the one provider that may receive SENSITIVE items; it must stay on this machine.
            # The host only: a base URL can carry credentials in its userinfo or query.
            raise ProviderError(f"the local provider must be on this machine, not {host!r}", kind="config")
        key = api_key or os.environ.get("OPENAI_API_KEY" if provider == "openai" else "PCIM_LOCAL_API_KEY", "")
        if provider == "openai" and not key:
            raise ProviderError("OPENAI_API_KEY is not set", kind="no_credentials")
        self.max_tokens = max_tokens
        self.system_role = system_role
        # api.openai.com uses max_completion_tokens; most compatible servers use max_tokens.
        self.token_param = token_param or ("max_completion_tokens" if provider == "openai" else "max_tokens")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        # "local" ignores HTTP(S)_PROXY / ALL_PROXY (and netrc): httpx does not exempt loopback from
        # proxies, so a request for the server on this machine would otherwise leave it. "openai"
        # keeps the environment's proxy settings.
        self.http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout, transport=transport,
                                 trust_env=provider != "local")

    def generate(self, prompt: RenderedPrompt, max_tokens: int | None = None) -> ModelResponse:
        body = {
            "model": self.model,
            "messages": [
                {"role": self.system_role, "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            self.token_param: max_tokens or self.max_tokens,
        }
        t0 = time.perf_counter()
        r, d = self._post(body)
        latency = (time.perf_counter() - t0) * 1000
        try:
            choice = d["choices"][0]
            msg = choice.get("message") or {}
            refusal = bool(msg.get("refusal"))
            text = msg.get("refusal") if refusal else (msg.get("content") or "")
            usage = self._usage(d)
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            raise malformed_response(self.provider) from None
        served = d.get("model", self.model)
        return ModelResponse(
            text=text, usage=usage, cost_usd=cost_usd(served, usage), latency_ms=latency,
            stop_reason=choice.get("finish_reason") or "", model=served, provider=self.provider,
            request_id=r.headers.get("x-request-id") or d.get("id"), refusal=refusal,
        )

    def _secrets(self) -> tuple[str, ...]:
        auth = self.http.headers.get("authorization", "")
        key = auth[7:] if auth.startswith("Bearer ") else ""
        return (key,) if key else ()

    def _post(self, body: dict) -> tuple[httpx.Response, dict]:
        """POST /chat/completions and return (response, JSON object), or raise a ProviderError that
        carries no request text, header or key."""
        host, secrets = url_host(self.http.base_url), self._secrets()
        try:
            r = self.http.post("/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise transport_failure(self.provider, e, host=host, secrets=secrets) from None
        if not r.is_success:
            try:
                err = r.json().get("error")
            except (ValueError, AttributeError):
                err = None
            err = err if isinstance(err, dict) else {}  # Ollama and others send {"error": "<text>"}: not copied
            code = err.get("code") if isinstance(err.get("code"), str) else err.get("type")
            raise status_failure(self.provider, r.status_code, host=host, code=code,
                                 request_id=r.headers.get("x-request-id"), avoid=(json.dumps(body),), secrets=secrets)
        try:
            d = r.json()
        except ValueError:
            raise malformed_response(self.provider, "not JSON") from None
        if not isinstance(d, dict):
            raise malformed_response(self.provider, "not a JSON object")
        return r, d

    @staticmethod
    def _usage(d: dict) -> Usage:
        u = d.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        return Usage(input_tokens=max(0, (u.get("prompt_tokens") or 0) - cached),
                     output_tokens=u.get("completion_tokens") or 0, cache_read_tokens=cached)

    def _chat_messages(self, system: str, messages: list[dict]) -> list[dict]:
        out = [{"role": self.system_role, "content": system}]
        for m in messages:
            c = m["content"]
            if m["role"] == "assistant":
                text = "".join(b.get("text", "") for b in c if b.get("type") == "text")
                calls = [{"id": b["id"], "type": "function",
                          "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                         for b in c if b.get("type") == "tool_use"]
                # content may be null only alongside tool_calls; an empty turn stays a valid message
                msg = {"role": "assistant", "content": text or (None if calls else "")}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            elif isinstance(c, str):
                out.append({"role": "user", "content": c})
            else:
                texts = []
                for b in c:
                    if b.get("type") == "tool_result":
                        body = b["content"] if isinstance(b["content"], str) else json.dumps(b["content"])
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                    "content": ("did not run: " if b.get("is_error") else "") + body})
                    elif b.get("type") == "text":
                        texts.append(b["text"])
                if texts:
                    out.append({"role": "user", "content": "\n".join(texts)})
        return out

    def agent_step(self, system: str, messages: list[dict], tools: list[dict], max_tokens: int | None = None) -> AgentStep:
        body = {
            "model": self.model,
            "messages": self._chat_messages(system, messages),
            "tools": [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                        "parameters": t["input_schema"], "strict": True}}
                      for t in tools],
            self.token_param: max_tokens or self.max_tokens,
        }
        _, d = self._post(body)
        try:
            choice = d["choices"][0]
            msg = choice.get("message") or {}
            refusal = bool(msg.get("refusal"))
            text = msg.get("refusal") if refusal else (msg.get("content") or "")
            tool_uses = []
            for call in ([] if refusal else msg.get("tool_calls") or []):
                fn = call.get("function") or {}
                try:
                    inp = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    inp = {"_unparseable": (fn.get("arguments") or "")[:500]}
                tool_uses.append({"id": call["id"], "name": fn.get("name", ""),
                                  "input": inp if isinstance(inp, dict) else {"_unparseable": str(inp)[:500]}})
            usage = self._usage(d)
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            raise malformed_response(self.provider) from None
        served = d.get("model", self.model)
        content = ([{"type": "text", "text": text}] if text else []) + [
            {"type": "tool_use", "id": tu["id"], "name": tu["name"], "input": tu["input"]} for tu in tool_uses]
        return AgentStep(content=content, text=text, tool_uses=tool_uses, stop_reason=choice.get("finish_reason") or "",
                         usage=usage, cost_usd=cost_usd(served, usage), model=served, refusal=refusal)
