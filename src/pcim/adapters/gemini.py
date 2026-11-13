"""Gemini adapter over the generateContent REST endpoint (httpx).

Written against the documented v1beta request/response shape and tested against a mock
transport. **Not yet run against the live API** in this repository's test suite.
Thinking tokens (`thoughtsTokenCount`) are billed as output and counted as output here.

Every failure to get a usable reply is a ProviderError with `kind`, `retryable` and `status`
(base.py). A rejected key comes back from Gemini as HTTP 400 with reason API_KEY_INVALID,
not 401; it is reported as kind "auth" with status 400.
"""

from __future__ import annotations

import json
import os
import time

import httpx

from .base import (ModelResponse, ProviderError, RenderedPrompt, Usage, malformed_response, status_failure,
                   transport_failure, url_host)
from .pricing import cost_usd

BASE = "https://generativelanguage.googleapis.com/v1beta"


def _error_codes(r: httpx.Response) -> tuple[str | None, str | None]:
    """(status, reason) from a Google error body: {"error": {"status", "details": [{"reason"}]}}."""
    try:
        err = r.json().get("error")
    except (ValueError, AttributeError):
        return None, None
    if not isinstance(err, dict):
        return None, None
    reasons = [d.get("reason") for d in err.get("details") or [] if isinstance(d, dict)]
    return err.get("status"), next((x for x in reasons if isinstance(x, str)), None)


class GeminiAdapter:
    provider = "google"

    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 8192,
                 transport: httpx.BaseTransport | None = None, timeout: float = 120.0):
        self.model = model
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            # a ProviderError (still a RuntimeError, as before), so routing can fall back
            raise ProviderError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set", kind="no_credentials")
        self.max_tokens = max_tokens
        self.http = httpx.Client(base_url=BASE, headers={"x-goog-api-key": key}, timeout=timeout, transport=transport)

    def _secrets(self) -> tuple[str, ...]:
        return tuple(v for v in (self.http.headers.get("x-goog-api-key"),) if v)

    def _post(self, path: str, body: dict) -> dict:
        """POST and return the JSON object, or raise a ProviderError that carries no request text."""
        host, secrets = url_host(self.http.base_url), self._secrets()
        try:
            r = self.http.post(path, json=body)
        except httpx.HTTPError as e:
            raise transport_failure(self.provider, e, host=host, secrets=secrets) from None
        if not r.is_success:
            status, reason = _error_codes(r)
            raise status_failure(self.provider, r.status_code, host=host, code=reason or status,
                                 kind="auth" if reason == "API_KEY_INVALID" else None,
                                 avoid=(json.dumps(body),), secrets=secrets)
        try:
            d = r.json()
        except ValueError:
            raise malformed_response(self.provider, "not JSON") from None
        if not isinstance(d, dict):
            raise malformed_response(self.provider, "not a JSON object")
        return d

    def generate(self, prompt: RenderedPrompt, max_tokens: int | None = None) -> ModelResponse:
        body = {
            "systemInstruction": {"parts": [{"text": prompt.system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt.user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens or self.max_tokens},
        }
        t0 = time.perf_counter()
        d = self._post(f"/models/{self.model}:generateContent", body)
        latency = (time.perf_counter() - t0) * 1000
        try:
            cands = d.get("candidates") or []
            finish = cands[0].get("finishReason", "") if cands else (d.get("promptFeedback") or {}).get("blockReason", "")
            parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            refusal = finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII") or not cands
            um = d.get("usageMetadata") or {}
            cached = um.get("cachedContentTokenCount", 0) or 0
            usage = Usage(
                input_tokens=max(0, (um.get("promptTokenCount") or 0) - cached),
                output_tokens=(um.get("candidatesTokenCount") or 0) + (um.get("thoughtsTokenCount") or 0),
                cache_read_tokens=cached,
            )
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            raise malformed_response(self.provider) from None
        return ModelResponse(
            text=text if not refusal else f"[declined by the model ({finish or 'blocked'})]",
            usage=usage, cost_usd=cost_usd(self.model, usage), latency_ms=latency, stop_reason=finish,
            model=d.get("modelVersion", self.model), provider=self.provider, request_id=d.get("responseId"),
            refusal=refusal,
        )
