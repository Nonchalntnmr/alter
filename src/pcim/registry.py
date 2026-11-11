"""Model registry and deterministic routing.

Two names matter for every model CI can call:
- the **provider** is how CI reaches it: `anthropic` (API key, official SDK), `claude-cli`
  (the local Claude Code login), `openai`, `google`, `local`, `mock`;
- the **vendor** is who receives the data: `anthropic`, `openai`, `google`, `local`, `mock`.

Consent, egress and project provider access are decided per vendor, because the vendor is
who sees the content. Revoking Anthropic for a project blocks the API and the CLI alike.

Routing is deterministic and every decision carries its reason. No model is preferred on
claimed quality. The only measured signal is the local claim ledger: how often a model's
assessments were later borne out by a test or by the person. It is reported next to each
entry and is not used to route until there is enough of it to mean something.
"""

from __future__ import annotations

import dataclasses
import os
import shutil

from .adapters.pricing import _table

VENDOR = {"anthropic": "anthropic", "claude-cli": "anthropic", "openai": "openai", "google": "google",
          "local": "local", "mock": "mock"}
VENDORS = ("anthropic", "openai", "google", "local", "mock")
ROLES = ("chat", "verify")
CLI_ALIASES = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}


def vendor_of(provider: str) -> str:
    try:
        return VENDOR[provider]
    except KeyError:
        raise ValueError(f"unknown provider {provider!r}") from None


def model_identity(provider: str | None, model: str | None) -> tuple[str, str] | None:
    """(vendor, canonical model): the same model reached two ways is one identity, so
    `claude-cli:opus` and `anthropic:claude-opus-5-5` cannot verify each other's claims."""
    if not provider or provider not in VENDOR:
        return None
    m = model or ""
    if provider == "claude-cli":
        m = CLI_ALIASES.get(m, m)
    return VENDOR[provider], m


@dataclasses.dataclass(frozen=True)
class ModelEntry:
    provider: str
    model: str  # what CI passes to the adapter
    vendor: str
    label: str
    capabilities: tuple[str, ...]  # chat (tool-using sessions), verify
    transport: str  # api | local-cli | test-double
    needs: str
    privacy: str
    price_id: str | None = None
    test_double: bool = False

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


_ANTHROPIC_PRIVACY = "Sent to Anthropic"
_CLI_NEEDS = "the `claude` CLI on PATH, logged in to a Claude account"


def entries() -> list[ModelEntry]:
    """Every model CI knows how to call, in routing preference order within each tier."""
    out = [
        ModelEntry("claude-cli", "opus", "anthropic", "Claude Opus 5.5 (Claude Code login)", ("chat", "verify"),
                   "local-cli", _CLI_NEEDS, _ANTHROPIC_PRIVACY + " under your Claude account's terms", "claude-opus-5-5"),
        ModelEntry("claude-cli", "sonnet", "anthropic", "Claude Sonnet 5.5 (Claude Code login)", ("chat", "verify"),
                   "local-cli", _CLI_NEEDS, _ANTHROPIC_PRIVACY + " under your Claude account's terms", "claude-sonnet-5-5"),
        ModelEntry("claude-cli", "haiku", "anthropic", "Claude Haiku 5.5 (Claude Code login)", ("chat", "verify"),
                   "local-cli", _CLI_NEEDS, _ANTHROPIC_PRIVACY + " under your Claude account's terms", "claude-haiku-5-5"),
        ModelEntry("anthropic", "claude-opus-5-5", "anthropic", "Claude Opus 5.5 (API)", ("chat", "verify"), "api",
                   "ANTHROPIC_API_KEY", _ANTHROPIC_PRIVACY + " under the API terms", "claude-opus-5-5"),
        ModelEntry("anthropic", "claude-sonnet-5-5", "anthropic", "Claude Sonnet 5.5 (API)", ("chat", "verify"), "api",
                   "ANTHROPIC_API_KEY", _ANTHROPIC_PRIVACY + " under the API terms", "claude-sonnet-5-5"),
        ModelEntry("anthropic", "claude-haiku-5-5", "anthropic", "Claude Haiku 5.5 (API)", ("chat", "verify"), "api",
                   "ANTHROPIC_API_KEY", _ANTHROPIC_PRIVACY + " under the API terms", "claude-haiku-5-5"),
    ]
    openai_model = os.environ.get("PCIM_OPENAI_MODEL", "")
    out.append(ModelEntry("openai", openai_model, "openai", f"OpenAI {openai_model or '(model not set)'}",
                          ("chat", "verify"), "api", "OPENAI_API_KEY and PCIM_OPENAI_MODEL",
                          "Sent to OpenAI under the API terms", openai_model or None))
    # Single-turn only ("ask"): these adapters have no tool-using session in the registry's
    # sense, so `route("chat"|"verify")` never considers them; routing.py does, for `ask`.
    gemini_model = os.environ.get("PCIM_GEMINI_MODEL", "")
    out.append(ModelEntry("google", gemini_model, "google", f"Google {gemini_model or '(model not set)'}",
                          ("ask",), "api", "GEMINI_API_KEY (or GOOGLE_API_KEY) and PCIM_GEMINI_MODEL",
                          "Sent to Google under the API terms", gemini_model or None))
    local_model = os.environ.get("PCIM_LOCAL_MODEL", "")
    out.append(ModelEntry("local", local_model, "local", f"Local model {local_model or '(model not set)'}",
                          ("ask",), "api", "PCIM_LOCAL_MODEL and PCIM_LOCAL_BASE_URL (a loopback server)",
                          "Stays on this machine", None))
    out.append(ModelEntry("mock", "mock-agent", "mock", "Mock agent (test double, not a model)", ("chat", "verify"),
                          "test-double", "nothing", "Never leaves this process", None, test_double=True))
    return out


def availability(e: ModelEntry) -> tuple[bool, str]:
    """Whether CI can call this model now. A configured credential is not proof it works."""
    if e.provider == "mock":
        return True, "always available"
    if e.provider == "claude-cli":
        path = shutil.which(os.environ.get("PCIM_CLAUDE_BIN", "claude"))
        return (True, f"CLI at {path}; login is checked on first call") if path else (False, "`claude` not on PATH")
    if e.provider == "anthropic":
        ok = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        return ok, "API credential in the environment" if ok else "ANTHROPIC_API_KEY is not set"
    if e.provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            return False, "OPENAI_API_KEY is not set"
        return (True, "API key in the environment") if e.model else (False, "PCIM_OPENAI_MODEL is not set")
    if e.provider == "google":
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            return False, "GEMINI_API_KEY is not set"
        return (True, "API key in the environment") if e.model else (False, "PCIM_GEMINI_MODEL is not set")
    if e.provider == "local":
        if not e.model:
            return False, "PCIM_LOCAL_MODEL is not set"
        base = os.environ.get("PCIM_LOCAL_BASE_URL", "")
        return (True, f"local server at {base}") if base else (False, "PCIM_LOCAL_BASE_URL is not set")
    return False, "no adapter with tool use"


def find(provider: str, model: str | None) -> ModelEntry | None:
    for e in entries():
        if e.provider == provider and (model is None or e.model == model):
            return e
    return None


def describe(measured: dict[str, dict] | None = None) -> list[dict]:
    prices = _table()
    out = []
    for e in entries():
        ok, why = availability(e)
        d = dataclasses.asdict(e) | {"key": e.key, "available": ok, "availability": why,
                                     "price_per_mtok": ({k: prices[e.price_id][k] for k in ("input", "output")}
                                                        if e.price_id in prices else None),
                                     "measured": (measured or {}).get(e.key)}
        out.append(d)
    return out


@dataclasses.dataclass
class Route:
    entry: ModelEntry | None
    reason: str
    considered: list[tuple[str, str]]

    def as_dict(self) -> dict:
        return {"provider": self.entry.provider if self.entry else None,
                "model": self.entry.model if self.entry else None,
                "label": self.entry.label if self.entry else None,
                "reason": self.reason, "considered": [{"model": k, "outcome": v} for k, v in self.considered]}


def route(role: str, allowed_vendors: set[str], author: dict | None = None,
          include_test_doubles: bool = False) -> Route:
    """Pick a model for `role` among those available and allowed for this project.

    verify: never the author's own model; prefer a different vendor (independent
    training and infrastructure), then a different model from the same vendor, which
    is weaker independence and says so. Test doubles only when asked for explicitly.
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    author = author or {}
    a_id = model_identity(author.get("provider"), author.get("model"))
    a_vendor = a_id[0] if a_id else None
    tiers: dict[int, list[ModelEntry]] = {0: [], 1: []}
    considered = []
    for e in entries():
        if role not in e.capabilities:
            continue
        ok, why = availability(e)
        if e.test_double and not include_test_doubles:
            considered.append((e.key, "skipped: test double"))
            continue
        if not ok:
            considered.append((e.key, f"unavailable: {why}"))
            continue
        if e.vendor not in allowed_vendors:
            considered.append((e.key, f"no access: {e.vendor} is not authorized for this project"))
            continue
        if role == "verify" and a_id and model_identity(e.provider, e.model) == a_id:
            considered.append((e.key, "skipped: the same model made the claim"))
            continue
        tier = 1 if role == "verify" and a_vendor and e.vendor == a_vendor else 0
        tiers[tier].append(e)
        considered.append((e.key, f"eligible (tier {tier})"))
    if tiers[0]:
        if role == "chat":
            why = "first available model with tool use"
        elif a_vendor:
            why = f"different vendor ({tiers[0][0].vendor}) from the claim's author ({a_vendor})"
        else:
            why = "the person made the claim; first available model"
        return Route(tiers[0][0], why, considered)
    if tiers[1]:
        return Route(tiers[1][0], f"same vendor as the author ({a_vendor}), different model: weaker independence",
                     considered)
    return Route(None, "no available model is authorized for this project", considered)
