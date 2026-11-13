"""Provider-neutral types for Layer F."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from http import HTTPStatus
from typing import Protocol


@dataclasses.dataclass(frozen=True)
class PacketItem:
    id: str
    kind: str
    content: str
    source: str
    trust: str
    valid_from: str  # ISO date
    confidence: float
    conflicted: bool = False
    confirmed_instructions: bool = False


@dataclasses.dataclass
class ContextPacket:
    """Everything CI decided to tell the model, before any provider formatting.

    `profile` holds user-confirmed pinned items: trusted, stable across requests, rendered
    in the system section (and therefore cacheable). `memories` holds what the policy
    selected for this task: rendered as fenced *data* in the user turn, never as
    instructions, whatever their trust.
    """

    packet_id: str
    task: str
    directives: list[str]
    profile: list[PacketItem]
    memories: list[PacketItem]
    nonce: str
    meta: dict = dataclasses.field(default_factory=dict)

    def item_ids(self) -> list[str]:
        return [i.id for i in self.profile] + [i.id for i in self.memories]


@dataclasses.dataclass(frozen=True)
class RenderedPrompt:
    system: str  # stable: preamble + directives + profile
    user: str  # volatile: fenced data block + the task

    def digest(self) -> str:
        """Hash of exactly what was sent. Stored instead of the text itself."""
        return hashlib.sha256(json.dumps([self.system, self.user]).encode()).hexdigest()


@dataclasses.dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclasses.dataclass
class ModelResponse:
    text: str
    usage: Usage
    cost_usd: float | None  # None = price unknown for this model; never silently 0
    latency_ms: float
    stop_reason: str
    model: str
    provider: str
    request_id: str | None = None
    refusal: bool = False


@dataclasses.dataclass
class AgentStep:
    """One model turn in a tool-using session.

    `content` is the provider-native assistant content, appended back unchanged on the
    next request. (Current Claude models bind thinking blocks to the conversation, so
    history must be append-only.)
    """

    content: list
    text: str
    tool_uses: list[dict]  # [{"id", "name", "input"}]
    stop_reason: str
    usage: Usage
    cost_usd: float | None
    model: str
    refusal: bool = False


# ---------------------------------------------------------------- provider failures
# What went wrong, as facts a caller can act on. An adapter builds a ProviderError's message
# from these facts only: never the request, a header or a key, and never free text from
# the provider's error body (bodies can echo the request, and some quote part of the key).
KINDS = ("timeout", "connection", "rate_limit", "server", "auth", "permission", "no_credentials", "quota",
         "request", "malformed", "config", "unknown")
CREDENTIAL_KINDS = frozenset({"auth", "permission", "no_credentials"})  # report as a credential problem
PROVIDER_WIDE_KINDS = frozenset({"auth", "no_credentials", "config"})  # every model on that provider fails alike


class ProviderError(RuntimeError):
    """The provider could not produce a reply (transport failure, auth, malformed output).

    `retryable`: the same request may succeed later (timeouts, connection failures, 408, 429, 5xx).
    `status`: the HTTP status the provider answered with when it answered with an error, else None.
    `kind`: one of KINDS. The defaults keep a bare `ProviderError("...")` meaning what it always did.
    """

    def __init__(self, *args, retryable: bool = False, status: int | None = None, kind: str = "unknown"):
        super().__init__(*args)
        self.retryable = bool(retryable)
        self.status = status
        self.kind = kind

    @property
    def credential_problem(self) -> bool:
        return self.kind in CREDENTIAL_KINDS


_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
QUOTA_CODES = frozenset({"insufficient_quota"})  # OpenAI's 429 for an exhausted balance: waiting does not help
_HINT = {401: "the credential was rejected", 402: "payment required", 403: "permission denied",
         404: "model or endpoint not found", 408: "the request timed out", 413: "the request is too large"}


def safe_token(value, avoid=()) -> str | None:
    """A provider-supplied identifier (error code, request id) fit for a message: one short
    token that is not part of any string in `avoid` (the request, the key), else None."""
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        return None
    if any(a and (value in a or (len(a) >= 8 and a in value)) for a in avoid):
        return None
    return value


def redact(text: str, secrets=()) -> str:
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "[redacted]")
    return text


def classify_status(status: int, code: str | None = None) -> tuple[str, bool]:
    """(kind, retryable) for an HTTP error status."""
    if status == 401:
        return "auth", False
    if status == 403:
        return "permission", False
    if status == 402:
        return "quota", False
    if status == 408:
        return "timeout", True
    if status == 429:
        return ("quota", False) if code in QUOTA_CODES else ("rate_limit", True)
    if 500 <= status <= 599:
        return "server", True
    if 400 <= status <= 499:
        return "request", False
    return "malformed", False  # 1xx/3xx where a reply was expected


def status_failure(provider: str, status: int, *, host: str | None = None, code=None, request_id=None,
                   kind: str | None = None, avoid=(), secrets=()) -> ProviderError:
    """The provider answered with an HTTP error. `code` and `request_id` come from the provider and
    are kept only when they are single tokens found nowhere in `avoid` + `secrets`."""
    avoid = tuple(avoid) + tuple(secrets)
    code, rid = safe_token(code, avoid), safe_token(request_id, avoid)
    k, retryable = classify_status(status, code)
    k = kind or k
    try:
        phrase = " " + HTTPStatus(status).phrase
    except ValueError:
        phrase = ""
    hint = ("the credential was rejected" if k == "auth" else "quota exhausted" if k == "quota"
            else "rate limited" if k == "rate_limit" else "server error" if k == "server"
            else _HINT.get(status, "request rejected" if 400 <= status <= 499 else "unexpected status"))
    msg = (f"{provider} returned HTTP {status}{phrase}{f' from {host}' if host else ''}: {hint}"
           f"{f' ({code})' if code else ''}{f'; request id {rid}' if rid else ''}")
    return ProviderError(redact(msg, secrets), retryable=retryable, status=status, kind=k)


def transport_failure(provider: str, exc: Exception, *, host: str | None = None, secrets=()) -> ProviderError:
    """An httpx error before any HTTP answer. Only the exception's class name is reported: its
    text and its `.request` (headers, body) stay out of the message."""
    import httpx

    name, where = type(exc).__name__, f" to {host}" if host else ""
    if isinstance(exc, httpx.TimeoutException):
        kind, retryable, what = "timeout", True, "timed out"
    elif isinstance(exc, (httpx.NetworkError, httpx.RemoteProtocolError, httpx.ProxyError)):
        kind, retryable, what = "connection", True, "failed: could not connect or the connection dropped"
    elif isinstance(exc, httpx.DecodingError):
        kind, retryable, what = "malformed", False, "failed: the response could not be decoded"
    elif isinstance(exc, (httpx.UnsupportedProtocol, httpx.LocalProtocolError)):
        kind, retryable, what = "config", False, "failed: the endpoint URL or request is not usable"
    else:
        kind, retryable, what = "unknown", False, "failed"
    return ProviderError(redact(f"{provider} request{where} {what} ({name})", secrets), retryable=retryable, kind=kind)


def malformed_response(provider: str, what: str = "unexpected shape") -> ProviderError:
    return ProviderError(f"{provider} returned a malformed response ({what})", kind="malformed")


def url_host(url) -> str | None:
    """host[:port] of an httpx.URL, without userinfo, path or query."""
    host = getattr(url, "host", None)
    if not host:
        return None
    return f"{host}:{url.port}" if getattr(url, "port", None) else host


class Adapter(Protocol):
    provider: str
    model: str

    def generate(self, prompt: RenderedPrompt, max_tokens: int | None = None) -> ModelResponse: ...
