"""Task-aware model routing: which available, authorized model should answer a request, and why.

registry.py knows how to reach each model and whether it can be called now, and its `route()`
picks for the workspace roles (chat, verify). This module adds what a one-command terminal
needs: a capability profile per model, a requirement estimate per request, and one rule that
fits in a sentence:

    the cheapest available, authorized model whose capability tier meets the request's
    requirement; if none meets it, the most capable eligible one, and the explanation says so.

Filters, in order, each recorded in `considered` so the decision can be shown:
  1. credential tripwire: a request that looks like it contains a key goes only to vendors
     that keep data on this machine (sovereignty/credentials.py);
  2. availability (registry.availability);
  3. authorization: the vendor must hold the person's egress grant for their own statements
     and, inside a project, the project's provider access. Routing never picks a vendor the
     person has not chosen;
  4. test doubles only when nothing real is authorized, or when asked for explicitly;
  5. context window, where it is known.

What the tiers are: ordinal positions within one vendor's line (Haiku < Sonnet < Opus), from
the vendor's own descriptions, not from measurement by CI. A model CI has no profile for is
"unrated" and is used only when no rated model meets the requirement. Overrides and new
models: $PCIM_CAPABILITIES, a JSON file mapping "<provider:model>" or "<model>" to
{"tier": 1-3, "context_window": int, "max_output": int, "strengths": ["code", "math", ...]}.
`strengths` is the only way a domain preference enters routing, and it comes from the person,
not from CI. Measured evidence (the claim ledger's track record in registry.describe, and
experiments/routing/) is reported alongside; routing on it waits until there is enough.

The requirement estimate is lexical and deliberately simple, like intent.classify_task: it has
to be explainable in one line and cheap enough to run before every request. It is also weak:
on a held-out set it matched 15 of 40 labels and under-routed 18 (experiments/routing/REPORT.md),
which is why the default preference never routes below standard (FLOORS). Replacing it is the
first item of remaining work.

Fallback: `RoutePlan.candidates` is the chosen model followed by up to four alternatives in
the same order. The engine tries them in turn on a provider failure (engine.ask_routed). A
model the person pinned has no fallbacks: an explicit choice is never silently swapped.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re

from .adapters.pricing import _table
from .intent import classify_task
from .registry import ModelEntry, availability, entries, model_identity
from .sovereignty.credentials import LOCAL_VENDORS, find_credentials
from .types import approx_tokens

TIERS = {1: "light", 2: "standard", 3: "deep"}
PREFERENCES = ("economy", "balanced", "quality")
# The lowest tier each preference will route to. On a held-out set written without sight of
# this code, the lexical estimate under-routed 18 of 40 requests and found 3 of 10 deep ones
# (experiments/routing/REPORT.md). Under-routing costs answer quality, so only `economy`, the
# person's explicit choice, routes to the light tier. Decided after seeing that set, so it can
# no longer measure this floor cleanly.
FLOORS = {"economy": 1, "balanced": 2, "quality": 2}
OUTPUT_TOKENS = {1: 500, 2: 1500, 3: 4000}  # budgeting guesses for the cost estimate only
SYSTEM_OVERHEAD = 300  # preamble and directives, tokens
MAX_FALLBACKS = 4
MIN_LATENCY_SAMPLES = 5


@dataclasses.dataclass(frozen=True)
class Capability:
    tier: int | None  # None = unrated
    context_window: int | None = None  # tokens; None = unknown, not checked
    max_output: int | None = None
    strengths: tuple[str, ...] = ()
    source: str = "unrated: no profile for this model; add one in $PCIM_CAPABILITIES"


_CLAUDE = "tier: Anthropic's positioning of its model lines; limits: claude-api reference cached 2026-11-03"
PROFILES: dict[str, Capability] = {
    "claude-opus-5-5": Capability(3, 1_000_000, 128_000, (), _CLAUDE),
    "claude-sonnet-5-5": Capability(2, 1_000_000, 128_000, (), _CLAUDE),
    "claude-haiku-5-5": Capability(1, 1_000_000, 128_000, (), _CLAUDE),
}


class CapabilityFileError(ValueError):
    pass


def load_overrides(path: str | None = None) -> dict[str, dict]:
    path = path or os.environ.get("PCIM_CAPABILITIES")
    if not path or not os.path.exists(path):
        return {}
    raw = json.load(open(path))
    if not isinstance(raw, dict):
        raise CapabilityFileError("the capabilities file must be a JSON object")
    for k, d in raw.items():
        if not isinstance(d, dict):
            raise CapabilityFileError(f"{k}: expected an object")
        if "tier" in d and d["tier"] not in (1, 2, 3, None):
            raise CapabilityFileError(f"{k}: tier must be 1, 2, 3 or null")
        for f in ("context_window", "max_output"):
            if f in d and d[f] is not None and (not isinstance(d[f], int) or d[f] <= 0):
                raise CapabilityFileError(f"{k}: {f} must be a positive integer")
        if "strengths" in d and not (isinstance(d["strengths"], list) and all(isinstance(s, str) for s in d["strengths"])):
            raise CapabilityFileError(f"{k}: strengths must be a list of strings")
    return raw


def canonical(e: ModelEntry) -> str:
    ident = model_identity(e.provider, e.model)
    return ident[1] if ident else e.model


def capability(e: ModelEntry, overrides: dict | None = None) -> Capability:
    ov = load_overrides() if overrides is None else overrides
    canon = canonical(e)
    base = PROFILES.get(canon, Capability(None))
    if e.test_double:
        base = Capability(None, source="test double: its answers are not a model's")
    for k in (e.key, canon):
        if k in ov:
            d = ov[k]
            return Capability(tier=d.get("tier", base.tier), context_window=d.get("context_window", base.context_window),
                              max_output=d.get("max_output", base.max_output),
                              strengths=tuple(d.get("strengths", base.strengths)),
                              source=f"your capabilities file ({k})")
    return base


# ---------------------------------------------------------------------- requirement
def _rx(*pats: str) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.I) for p in pats)


_DEEP = _rx(r"\bprove\b.*\b(general|for all|every)\b", r"\bderiv(e|ation)\b", r"\barchitect", r"\bdesign (a|an|the)\b",
            r"\btrade-?offs?\b", r"\bthreat model", r"\bsecurity review\b", r"\brefactor\b", r"\bmigrat(e|ion)\b",
            r"\broot cause\b", r"\brace condition\b", r"\bconcurren", r"\bdistributed\b", r"\btheorem\b",
            r"\bstrategy\b", r"\bend-to-end\b", r"\bresearch design\b", r"\bcritique\b", r"\btrustworthy\b",
            r"\bzero downtime\b", r"\bnumerically\b", r"\bformal (proof|verification)\b")
_STANDARD = _rx(r"\bdebug", r"\bfix (the|this|my) (bug|error|code|test)", r"\bimplement", r"\bwrite (a|an|the|some)? ?"
                r"(python |sql |bash |js |typescript )?(function|script|class|tests?|query|program|regex|parser)",
                r"\bexplain (why|how)\b", r"\banaly[sz]", r"\bcompare\b", r"\breview\b", r"\balgorithm",
                r"\bregex\b", r"\bunit tests?\b", r"\bprove\b", r"\bdecide\b", r"\bpros and cons\b",
                r"\bstudy (plan|schedule)\b|\bschedule for\b", r"\bspeed up\b|\boptimi[sz]e\b", r"\blimitations\b",
                r"\bintuition\b", r"\bwhat'?s weak\b")
_MATH = _rx(r"\bintegral\b", r"\bderivative\b", r"\bprobabilit", r"\bexpected value\b", r"\bmatri(x|ces)\b",
            r"\beigen", r"\bequation", r"\bsolve for\b", r"\bvariance\b", r"\bregression\b", r"\bstatistic",
            r"\bbayes", r"\bp-?value\b", r"\bconfidence interval\b")
_CODE = _rx(r"```", r"\btraceback\b", r"\bstack trace\b", r"\b\w+\.(py|ts|tsx|js|go|rs|java|rb|sql|sh)\b",
            r"\bdef \w+\(", r"\b(Type|Value|Key|Attribute|Index|Name)Error\b", r"\bexception\b", r"\bSELECT\b.*\bFROM\b",
            r"\bfunction\b", r"\bmodule\b", r"\bquery\b")


@dataclasses.dataclass
class Requirement:
    tier: int
    task_type: str
    task_type_p: float
    domains: list[str]
    est_input_tokens: int
    est_output_tokens: int
    signals: list[str]
    credentials: list[str]

    @property
    def tier_name(self) -> str:
        return TIERS[self.tier]


def estimate(task: str, *, mode: str | None = None, preference: str = "balanced", budget_tokens: int = 1200,
             min_tier: int | None = None, apply_floor: bool = True) -> Requirement:
    """What this request needs, with the reasons. Deterministic, local, no model call.
    `apply_floor=False` gives the raw lexical estimate (what the benchmark measures)."""
    if preference not in PREFERENCES:
        raise ValueError(f"preference must be one of {PREFERENCES}")
    probs = classify_task(task)
    ttype = max(probs, key=probs.get)
    signals, domains = [], []
    deep, std = _matches(_DEEP, task), _matches(_STANDARD, task)
    if any(p.search(task) for p in _CODE) or (ttype == "code" and probs[ttype] > 0.5):
        domains.append("code")
    if any(p.search(task) for p in _MATH):
        domains.append("math")
    if deep:
        tier = 3
        signals.append("asks for deep reasoning: " + ", ".join(f"'{w}'" for w in deep[:3]))
    elif std:
        tier = 2
        signals.append("asks for analysis or construction: " + ", ".join(f"'{w}'" for w in std[:3]))
    else:
        tier = 1
        signals.append("no reasoning or construction markers")
    n_parts = len(re.findall(r"(?m)^\s*(\d+[.)]|[-*•])\s+\S", task)) + max(0, task.count("?") - 1)
    if n_parts >= 3 and tier < 3:
        tier += 1
        signals.append(f"{n_parts} parts")
    if len(task) > 4000 and tier < 3:
        tier = 3
        signals.append(f"long request ({approx_tokens(task):,} tokens)")
    elif len(task) > 1500 and tier < 2:
        tier = 2
        signals.append(f"long request ({approx_tokens(task):,} tokens)")
    if "math" in domains and tier < 2:
        tier = 2
        signals.append("mathematics")
    if mode == "challenge" and tier < 3:
        tier = 3
        signals.append("challenge mode: a weak challenger produces false confidence")
    if mode in ("amplify", "learn") and tier < 2:
        tier = 2
        signals.append(f"{mode} mode: {'trade-offs need reasoning' if mode == 'amplify' else 'a wrong hint teaches the wrong thing'}")
    if preference == "economy" and tier > 1:
        tier -= 1
        signals.append("your preference: economy (one tier lower)")
    elif preference == "quality" and tier < 3:
        tier += 1
        signals.append("your preference: quality (one tier higher)")
    if apply_floor and tier < FLOORS[preference]:
        tier = FLOORS[preference]
        signals.append(f"{preference} routing starts at {TIERS[tier]} (held-out: the estimate under-routed 18 of 40)")
    if min_tier and tier < min_tier:
        tier = min_tier
        signals.append(f"this command needs at least {TIERS[min_tier]}")
    est_in = approx_tokens(task) + budget_tokens + SYSTEM_OVERHEAD
    return Requirement(tier=tier, task_type=ttype, task_type_p=round(probs[ttype], 2), domains=domains,
                       est_input_tokens=est_in, est_output_tokens=OUTPUT_TOKENS[tier], signals=signals,
                       credentials=find_credentials(task))


def _matches(pats: tuple[re.Pattern, ...], text: str) -> list[str]:
    """The words in the request that set the tier, so the explanation quotes the person."""
    out = []
    for p in pats:
        m = p.search(text)
        if m:
            w = " ".join(m.group(0).split())
            out.append(w if len(w) <= 30 else w[:27] + "...")
    return list(dict.fromkeys(x.lower() for x in out))


# ---------------------------------------------------------------------- the plan
@dataclasses.dataclass
class Candidate:
    entry: ModelEntry
    capability: Capability
    est_cost_usd: float | None
    meets: bool
    latency_ms_p50: float | None = None

    @property
    def key(self) -> str:
        return _key(self.entry)

    def as_dict(self) -> dict:
        return {"model": self.entry.key, "label": self.entry.label, "vendor": self.entry.vendor,
                "tier": TIERS.get(self.capability.tier, "unrated"), "meets_requirement": self.meets,
                "est_cost_usd": self.est_cost_usd, "cost_note": _cost_note(self.entry),
                "latency_ms_p50": self.latency_ms_p50, "capability_source": self.capability.source}


def _key(e: ModelEntry) -> str:
    return e.key if e.model else f"{e.provider} (no model set)"


def _cost_note(e: ModelEntry) -> str | None:
    if e.provider == "claude-cli":
        return "list price; notional under a Claude subscription"
    if e.test_double or e.vendor == "local":
        return "no charge"
    return None


@dataclasses.dataclass
class RoutePlan:
    requirement: Requirement
    candidates: list[Candidate]
    considered: list[dict]
    reason: str
    refused: str | None = None
    pinned: bool = False

    @property
    def chosen(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    def as_dict(self) -> dict:
        r = self.requirement
        return {"chosen": self.chosen.as_dict() if self.chosen else None, "reason": self.reason,
                "refused": self.refused, "pinned": self.pinned,
                "requirement": {"tier": r.tier_name, "task_type": r.task_type, "task_type_p": r.task_type_p,
                                "domains": r.domains, "signals": r.signals, "est_input_tokens": r.est_input_tokens,
                                "est_output_tokens": r.est_output_tokens, "credentials_found": r.credentials},
                "fallbacks": [c.key for c in self.candidates[1:]], "considered": self.considered}

    def explain(self) -> list[str]:
        r = self.requirement
        lines = []
        if self.chosen:
            c = self.chosen
            cost = "unknown" if c.est_cost_usd is None else f"~${c.est_cost_usd:.4f}"
            note = _cost_note(c.entry)
            lines.append(f"Route: {c.entry.label}: {self.reason}.")
            lines.append(f"  Needs: {r.tier_name} · {r.task_type} task (p={r.task_type_p})"
                         + (f" · {', '.join(r.domains)}" if r.domains else "") + f" · {'; '.join(r.signals)}")
            lines.append(f"  Estimate: {cost}{f' ({note})' if note else ''} · ~{r.est_input_tokens + r.est_output_tokens:,}"
                         f" tokens{f' of {c.capability.context_window:,}' if c.capability.context_window else ''}"
                         + (f" · p50 {c.latency_ms_p50:.0f} ms here" if c.latency_ms_p50 else ""))
            lines.append(f"  Tier source: {c.capability.source}")
            if self.candidates[1:]:
                lines.append("  If it fails: " + " → ".join(
                    f"{x.key} ({TIERS.get(x.capability.tier, 'unrated')})" for x in self.candidates[1:]))
        else:
            lines.append(f"No route: {self.refused or self.reason}.")
            lines.append(f"  Needs: {r.tier_name} · {r.task_type} task · {'; '.join(r.signals)}")
        groups: dict[str, list[str]] = {}
        for c in self.considered:
            if not c["outcome"].startswith(("chosen", "fallback", "eligible")):
                groups.setdefault(c["outcome"], []).append(c["model"])
        for outcome, models in groups.items():
            lines.append(f"  Not used ({outcome}): {', '.join(models)}")
        return lines


def estimated_cost(e: ModelEntry, in_tokens: int, out_tokens: int) -> float | None:
    if e.test_double or e.vendor == "local":
        return 0.0
    p = _table().get(e.price_id or "")
    if p is None:
        return None
    return round((in_tokens * p["input"] + out_tokens * p["output"]) / 1e6, 6)


def plan(task: str, *, allowed_vendors: set[str], mode: str | None = None, preference: str = "balanced",
         budget_tokens: int = 1200, pinned: str | None = None, include_test_doubles: bool | None = None,
         latency: dict | None = None, min_tier: int | None = None, overrides: dict | None = None) -> RoutePlan:
    """Rank every known model for `task`. `allowed_vendors` is already the intersection of the
    person's egress grants and, in a project, its provider access. `latency` maps
    (provider, canonical model) to (p50 ms, n) from this machine's own interaction log."""
    req = estimate(task, mode=mode, preference=preference, budget_tokens=budget_tokens, min_tier=min_tier)
    ov = load_overrides() if overrides is None else overrides
    allowed = set(allowed_vendors)
    if req.credentials:
        allowed &= LOCAL_VENDORS
    real_allowed = {v for v in allowed if v != "mock"}
    doubles = (not real_allowed) if include_test_doubles is None else include_test_doubles
    considered, eligible = [], []
    for order, e in enumerate(entries()):
        if not ({"chat", "ask"} & set(e.capabilities)):
            continue
        if pinned and e.key != pinned:
            continue
        cap = capability(e, ov)
        ok, why = availability(e)
        if req.credentials and e.vendor not in LOCAL_VENDORS:
            considered.append({"model": _key(e), "outcome": f"withheld: the request looks like it contains a credential "
                                                            f"({', '.join(req.credentials)})"})
            continue
        if not ok:
            considered.append({"model": _key(e), "outcome": f"unavailable: {why}"})
            continue
        if e.vendor not in allowed:
            considered.append({"model": _key(e), "outcome": f"not authorized: no grant from you for {e.vendor}"
                                                            " (or this project does not allow it)"})
            continue
        if e.test_double and not doubles:
            considered.append({"model": _key(e), "outcome": "skipped: test double"})
            continue
        need = req.est_input_tokens + req.est_output_tokens
        if cap.context_window and need > cap.context_window:
            considered.append({"model": _key(e), "outcome": f"too small: ~{need:,} tokens > {cap.context_window:,}"})
            continue
        lat = (latency or {}).get((e.provider, canonical(e)))
        cand = Candidate(e, cap, estimated_cost(e, req.est_input_tokens, req.est_output_tokens),
                         meets=cap.tier is not None and cap.tier >= req.tier,
                         latency_ms_p50=lat[0] if lat and lat[1] >= MIN_LATENCY_SAMPLES else None)
        eligible.append((order, cand))

    if pinned:
        if not eligible:
            why = considered[0]["outcome"] if considered else f"{pinned} is not a model CI knows"
            return RoutePlan(req, [], considered, "", refused=f"{pinned}: {why}", pinned=True)
        c = eligible[0][1]
        considered.append({"model": c.key, "outcome": "chosen: you pinned it"})
        return RoutePlan(req, [c], considered, "you chose this model" + ("" if c.meets else
                         f" (its tier, {TIERS.get(c.capability.tier, 'unrated')}, is below the estimate)"), pinned=True)

    def strength(c: Candidate) -> int:
        return -len(set(c.capability.strengths) & set(req.domains))

    def cost(c: Candidate) -> float:
        return c.est_cost_usd if c.est_cost_usd is not None else float("inf")

    def lat(c: Candidate) -> float:
        return c.latency_ms_p50 if c.latency_ms_p50 is not None else float("inf")

    meets = sorted((x for x in eligible if x[1].meets), key=lambda x: (x[1].capability.tier, strength(x[1]),
                                                                        cost(x[1]), lat(x[1]), x[0]))
    below = sorted((x for x in eligible if not x[1].meets and x[1].capability.tier is not None),
                   key=lambda x: (-x[1].capability.tier, strength(x[1]), cost(x[1]), x[0]))
    unrated = [x for x in eligible if x[1].capability.tier is None]
    ranked = [c for _, c in meets + below + unrated]
    if not ranked:
        real = [c for c in considered if "test double" not in c["outcome"]]
        why = ("the request looks like it contains a credential, and no local model is configured"
               if req.credentials else "no available model is authorized" if real else "nothing but test doubles")
        return RoutePlan(req, [], considered, "", refused=why)
    chosen, fallbacks = ranked[0], ranked[1:1 + MAX_FALLBACKS]
    if chosen.meets:
        reason = f"cheapest authorized model that meets the {req.tier_name} requirement"
        if strength(chosen):
            reason += f" (you rated it strong at {', '.join(set(chosen.capability.strengths) & set(req.domains))})"
    elif chosen.capability.tier is not None:
        reason = (f"no authorized model meets the {req.tier_name} requirement; using the most capable one available "
                  f"({TIERS[chosen.capability.tier]})")
    else:
        reason = "only unrated models are authorized and available; quality relative to the requirement is unknown"
    for c in ranked:
        outcome = ("chosen" if c is chosen else "fallback" if c in fallbacks else "eligible") + \
                  ("" if c.meets else ", below requirement" if c.capability.tier else ", unrated")
        considered.append({"model": c.key, "outcome": outcome})
    return RoutePlan(req, [chosen] + fallbacks, considered, reason)
