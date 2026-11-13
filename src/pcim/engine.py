"""Personal CI — the orchestration loop over every PCIM layer.

    ask(task)
      C  classify the task (soft type distribution)
      B  current memory → multi-channel candidates (similarity, standing items, recency, pins)
      ⊕  egress guard: drop everything this provider may not see — BEFORE the policy runs,
         so the policy can never learn to rely on context it is not allowed to send
      D  learned policy selects under the token budget, logging inclusion propensities
      E  assistance mode (bandit; growth goals restrict it)
      F  ContextPacket → render → provider adapter
      G  interaction logged: ids, propensities, x(S), tokens, cost, latency, hash of the
         exact prompt (the prompt text itself is not stored)
    feedback(signals)
      G  signals → reward (feedback.py; verified outcomes dominate, satisfaction is weak)
      H  posterior update, versioned and auditable; assistance-mode update

Every personal datum touched along the way is encrypted at rest under a per-record key,
and every egress decision is in the hash-chained audit log.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import secrets
from pathlib import Path

import numpy as np

from . import modes as intelligence
from .adapters import ContextPacket, PacketItem, get_adapter, render
from .adapters.base import CREDENTIAL_KINDS, PROVIDER_WIDE_KINDS, ProviderError
from .assist import DIRECTIVES, AssistPolicy
from .embed import HASHING_NAME, Embedder, default_embedder_name, embedder_from_name
from .feedback import reward_from_signals
from .growth import ATTEMPT_LOG_MAX, BKT, independence_report, next_challenge
from .intent import classify_task
from .learning import ContinualLearner
from .memory import TemporalMemory, effective_confidence
from .planner import plan_queries
from .policy.candidates import build_candidates, build_planned_candidates
from .policy.learned import PopulationPrior
from .policy.prior import designed_prior
from .profile import intelligence_dna
from .registry import VENDORS, model_identity, vendor_of
from .routing import PREFERENCES, RoutePlan
from .routing import plan as route_plan
from .sovereignty.audit import AuditLog
from .sovereignty.credentials import LOCAL_VENDORS, find_credentials, redact
from .sovereignty.consent import ConsentLedger
from .sovereignty.crypto import KeyRing
from .sovereignty.egress import CONFIRMED_FLAG, INSTRUCTION_FLAG, EgressGuard
from .store import Store
from .types import KINDS, TASK_TYPES, Sensitivity, Trust, new_id, utcnow

ASSIST_STATE = "pcim-assist"
ATTEMPTS_STATE = "pcim-attempts"  # growth.py's attempt log, one sealed JSON document per person
TWIN_STATE = "pcim-twin"  # weekly reviews (twin.py), one version per week
CHALLENGES_STATE = "pcim-challenges"  # challenge.py's log, sealed like the attempt log
SETTING_MODE = "intelligence_mode"
SETTING_PREFERENCE = "routing_preference"


class RoutingError(RuntimeError):
    """No model could answer: routing refused (with the reason) or every candidate failed."""

    def __init__(self, message: str, plan: RoutePlan, attempts: list[dict] | None = None):
        super().__init__(message)
        self.plan = plan
        self.attempts = attempts or []


_FALLBACK_ERRORS: tuple | None = None


def _scrub(obj, needle: str, label: str = "(a forgotten skill)"):
    """Remove a forgotten skill from a weekly snapshot: its key under "skills", and its name
    anywhere in text; checks that pointed at it become inert."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "skills" and isinstance(v, dict):
                out[k] = {sk: _scrub(sv, needle, label) for sk, sv in v.items() if sk != needle}
            elif k == "check" and isinstance(v, dict) and v.get("skill") == needle:
                out[k] = {"type": "forgotten"}
            else:
                out[k] = _scrub(v, needle, label)
        return out
    if isinstance(obj, list):
        return [_scrub(x, needle, label) for x in obj]
    if isinstance(obj, str) and needle in obj:
        return obj.replace(needle, label)
    return obj


def fallback_errors() -> tuple:
    """Failures that mean "this provider could not answer", so the next candidate may try.
    Anything else (a bug, a store error) propagates: falling back would hide it."""
    global _FALLBACK_ERRORS
    if _FALLBACK_ERRORS is None:
        errs: list = [ProviderError, OSError, TimeoutError]
        try:
            import httpx
            errs.append(httpx.HTTPError)
        except ImportError:  # pragma: no cover
            pass
        try:
            import anthropic
            errs.append(anthropic.APIError)
        except ImportError:  # pragma: no cover - the anthropic extra is optional
            pass
        _FALLBACK_ERRORS = tuple(errs)
    return _FALLBACK_ERRORS


@dataclasses.dataclass
class AskResult:
    interaction_id: str
    text: str
    receipt: dict


@dataclasses.dataclass
class ContextSelection:
    probs: dict
    task_type: str
    chosen: list
    sel: object
    policy: object
    decision: object
    n_candidates: int
    profile: list
    memories: list
    explain: list = dataclasses.field(default_factory=list)  # per candidate: ids and numbers, no content
    out_of_scope: int = 0  # current items scoped to another project, never candidates here
    queries: list | None = None  # the retrieval plan, when there was one (planner.py)


SCOPE_PREFIX = "project:"


def in_scope(item, project_id: str | None) -> bool:
    """An item tagged `project:<id>` belongs to that project only; an untagged item is user-wide."""
    scopes = [t[len(SCOPE_PREFIX):] for t in item.tags if t.startswith(SCOPE_PREFIX)]
    return not scopes or project_id in scopes


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


EMBEDDER_META = "embedder"


def bind_embedder(store: Store, embedder: Embedder | None, audit: AuditLog) -> Embedder:
    """The embedder whose vectors this store holds.

    With none given, a store keeps the one it was written with ($PCIM_EMBEDDER only picks
    it for a new store), so a CLI next to the server never mixes spaces. A different one
    given explicitly re-embeds every item first."""
    recorded = store.get_meta(EMBEDDER_META)
    if recorded is None and store.count_items():
        recorded = HASHING_NAME  # every store written before the embedder was recorded
    if embedder is None:
        embedder = embedder_from_name(recorded or default_embedder_name())
    if recorded is not None and recorded != embedder.name:
        n = store.reembed_items(embedder.embed)
        audit.append("system", "memory.reembed", embedder_from=recorded, embedder_to=embedder.name, items=n)
    if recorded != embedder.name:
        store.set_meta(EMBEDDER_META, embedder.name)
    return embedder


class PersonalCI:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        store: Store | None = None,
        embedder: Embedder | None = None,
        prior: PopulationPrior | None = None,
        budget_tokens: int = 1200,
        propensity_samples: int = 32,
        seed: int | None = None,
        plan_context: bool = True,
    ):
        if store is None:
            if data_dir is None:
                store = Store(":memory:", KeyRing.ephemeral())
            else:
                d = Path(data_dir)
                d.mkdir(parents=True, exist_ok=True)
                store = Store(d / "pcim.db", KeyRing.load_or_create(d / "master.key"))
        self.store = store
        self.audit = AuditLog(store)
        self.embedder = bind_embedder(store, embedder, self.audit)
        self.ledger = ConsentLedger(store, self.audit)
        self.egress = EgressGuard(store, self.ledger, self.audit)
        self.memory = TemporalMemory(store, self.embedder, self.audit, self.ledger)
        self.learner = ContinualLearner(store, self.audit, prior or designed_prior())
        self.budget_tokens = budget_tokens
        self.plan_context = plan_context  # ask() plans retrieval with the answering model when it can
        self.propensity_samples = propensity_samples
        self.rng = np.random.default_rng(seed)
        self._adapters: dict[tuple[str, str | None], object] = {}

    # ------------------------------------------------------------------ onboarding
    def onboard(self, user_id: str, display_name: str = "", providers: tuple[str, ...] = ("mock",),
                max_sensitivity: Sensitivity = Sensitivity.PERSONAL) -> dict:
        """Create a user and grant egress of *their own statements* to the chosen providers.

        Nothing else is granted. Every connector and document source needs its own store
        and egress grants, given explicitly (consent()).
        """
        vendors = list(dict.fromkeys(p if p == "*" else vendor_of(p) for p in providers))  # ValueError if unknown
        self.store.create_user(user_id, display_name, settings={"providers": list(providers)})
        for v in vendors:
            self.ledger.grant(user_id, "user", "egress", v, max_sensitivity, note="onboarding: own statements")
        token = secrets.token_urlsafe(32)
        self.store.set_token_hash(user_id, _hash_token(token))
        self.audit.append(user_id, "user.onboard", providers=list(providers))
        return {"user_id": user_id, "api_token": token}

    def user_for_token(self, token: str) -> str | None:
        return self.store.user_for_token_hash(_hash_token(token))

    def consent(self, user_id: str, source: str, purpose: str, provider: str = "*",
                max_sensitivity: Sensitivity = Sensitivity.PERSONAL, note: str = ""):
        vendor = provider if provider == "*" else vendor_of(provider)  # consent is per vendor
        return self.ledger.grant(user_id, source, purpose, vendor, max_sensitivity, note=note)

    def revoke(self, user_id: str, grant_id: str, delete_stored: bool = False) -> dict:
        grants = {g.id: g for g in self.ledger.grants(user_id)}
        if grant_id not in grants:
            raise KeyError(grant_id)
        ok = self.ledger.revoke(user_id, grant_id)
        deleted = []
        g = grants[grant_id]
        if delete_stored and g.purpose == "store":
            import fnmatch
            for it in self.memory.all(user_id):
                if fnmatch.fnmatchcase(it.source, g.source) and self.store.get_item(it.id) is not None:
                    deleted += self.memory.delete(user_id, it.id, cascade=True)
        return {"revoked": ok, "deleted_items": deleted}

    # ------------------------------------------------------------------ memory
    def remember(self, user_id: str, content: str, kind: str = "fact", **kw):
        return self.memory.remember(user_id, content, kind, **kw)

    def ingest_text(self, user_id: str, text: str, source: str, title: str | None = None,
                    sensitivity: Sensitivity = Sensitivity.PERSONAL, kind: str = "fact",
                    tags: tuple[str, ...] = ()) -> list:
        """Split a document into paragraph items. Documents are UNTRUSTED: they can carry
        injected instructions, so they are stored as data and can never become standing
        preferences without the person's confirmation."""
        paras = [p.strip() for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
        out = []
        for n, p in enumerate(paras):
            out.append(self.memory.remember(
                user_id, p, kind, source=source, trust=Trust.UNTRUSTED, sensitivity=sensitivity,
                confidence=0.7, provenance={"title": title or "", "paragraph": n}, tags=tags,
            ))
        return out

    # ------------------------------------------------------------------ the loop
    def _adapter(self, provider: str, model: str | None):
        key = (provider, model)
        if key not in self._adapters:
            self._adapters[key] = get_adapter(provider, model)
        return self._adapters[key]

    def set_adapter(self, provider: str, model: str | None, adapter) -> None:
        """Inject an adapter (tests, or a pre-configured client)."""
        self._adapters[(provider, model)] = adapter

    def _assist(self, user_id: str) -> AssistPolicy:
        loaded = self.store.load_policy_state(user_id, ASSIST_STATE)
        return AssistPolicy.from_dict(json.loads(loaded[1])) if loaded else AssistPolicy()

    def _save_assist(self, user_id: str, ap: AssistPolicy) -> None:
        loaded = self.store.load_policy_state(user_id, ASSIST_STATE)
        blob = json.dumps(ap.to_dict()).encode()
        if loaded:
            self.store.overwrite_policy_state(user_id, ASSIST_STATE, loaded[0], blob)
        else:
            self.store.save_policy_state(user_id, ASSIST_STATE, blob, note="assist policy")

    def set_growth_goals(self, user_id: str, task_types: list[str]) -> None:
        bad = set(task_types) - set(TASK_TYPES)
        if bad:
            raise ValueError(f"unknown task types {sorted(bad)}")
        ap = self._assist(user_id)
        ap.growth_tasks = set(task_types)
        self._save_assist(user_id, ap)
        self.audit.append(user_id, "assist.growth_goals", task_types=sorted(task_types))

    def select_context(self, user_id: str, task: str, provider: str, model_label: str, budget: int,
                       ref: str, project_id: str | None = None, queries: list[str] | None = None) -> "ContextSelection":
        """C → scope → B → egress → D: what this provider may see, and what the policy chose from it.

        Memory scoped to a project is a candidate only for that project; with no project
        (a one-shot ask, an MCP request without one) only user-wide memory is. With
        `queries` (a retrieval plan, planner.py) candidates are what those searches and the
        request find; without, they come from the unplanned channels."""
        if self.store.get_user(user_id) is None:
            raise KeyError(f"unknown user {user_id}")
        now = utcnow()
        probs = classify_task(task)
        p_vec = np.array([probs[t] for t in TASK_TYPES])
        everything = self.memory.current(user_id, now)
        current = [i for i in everything if in_scope(i, project_id)]
        ids, mat = self.memory.embeddings(user_id)
        emb = {i: mat[j] for j, i in enumerate(ids)} if mat is not None else {}
        by_id = {i.id: i for i in current}
        task_emb = self.embedder.embed([task])[0]
        # egress before policy: the policy never learns to rely on what it may not send.
        # Consent is per vendor (who receives the data), not per transport.
        decision = self.egress.filter(user_id, current, vendor_of(provider), model_label, ref=ref)
        band = self.embedder.relevance_band
        if not self.embedder.planned_retrieval:
            queries = None  # an embedder not calibrated for planned searches would find nothing
        if queries is None:
            cs = build_candidates(decision.allowed, emb, task_emb, effective_confidence, now, band=band)
        else:
            q_emb = self.embedder.embed(queries) if queries else np.zeros((0, task_emb.shape[0]))
            cs = build_planned_candidates(decision.allowed, emb, task_emb, q_emb, effective_confidence, now, band=band)
        pol = self.learner.policy_for(user_id)
        sel = pol.select(cs, p_vec, budget, self.rng, propensity_samples=self.propensity_samples)
        chosen = [by_id[cs.ids[j]] for j in sel.idx]

        def pitem(i):
            return PacketItem(
                id=i.id, kind=i.kind, content=i.content, source=i.source, trust=i.trust.value,
                valid_from=i.valid_from.isoformat(), confidence=round(effective_confidence(i, now), 2),
                conflicted="conflicted" in i.flags,
                confirmed_instructions=INSTRUCTION_FLAG in i.flags and CONFIRMED_FLAG in i.flags,
            )
        trusted = lambda i: i.pinned and i.trust.rank >= Trust.USER_STATED.rank  # noqa: E731
        picked = {int(j) for j in sel.idx}
        explain = [{"id": cs.ids[j], "selected": j in picked, "kind": KINDS[int(cs.kind[j])],
                    "pinned": bool(cs.pinned[j]), "similarity": round(float(cs.sim[j]), 3),
                    "age_days": round(float(cs.age_days[j]), 1), "confidence": round(float(cs.confidence[j]), 2),
                    "tokens": int(cs.tokens[j]), "score": round(float(sel.scores[j]), 4)} for j in range(len(cs))]
        explain.sort(key=lambda e: (not e["selected"], -e["score"]))
        return ContextSelection(
            probs=probs, task_type=max(probs, key=probs.get), chosen=chosen, sel=sel, policy=pol,
            decision=decision, n_candidates=len(cs),
            profile=[pitem(i) for i in chosen if trusted(i)], memories=[pitem(i) for i in chosen if not trusted(i)],
            explain=explain, out_of_scope=len(everything) - len(current), queries=queries,
        )

    def ask(self, user_id: str, task: str, provider: str = "mock", model: str | None = None,
            budget_tokens: int | None = None, assist_mode: str | None = None, *,
            intelligence_mode: str | None = None, project_id: str | None = None,
            route: dict | None = None, _mode: "intelligence.ModeDecision | None" = None) -> AskResult:
        """One request to one named model. `intelligence_mode` (execute | amplify | learn) applies
        only when given or when the person set a default; without either, behaviour is unchanged.
        `project_id` adds that project's scoped memory to the candidates. ask_routed() is the
        entry point that also picks the model."""
        budget = budget_tokens or self.budget_tokens
        now = utcnow()
        adapter = self._adapter(provider, model)
        interaction_id = new_id("int")
        queries, plan = None, None
        if self.plan_context and self.embedder.planned_retrieval and getattr(adapter, "can_plan", True):
            # The planner sees the request only, which this vendor receives anyway (planner.py).
            queries, plan = plan_queries(adapter, task)
            self.audit.append(user_id, "context.plan", interaction=interaction_id, provider=provider,
                              queries=None if queries is None else len(queries))
        cx = self.select_context(user_id, task, provider, adapter.model, budget, ref=interaction_id, queries=queries,
                                 project_id=project_id)
        probs, task_type, chosen, sel, pol, decision = cx.probs, cx.task_type, cx.chosen, cx.sel, cx.policy, cx.decision
        profile, memories = cx.profile, cx.memories
        cs_len = cx.n_candidates
        # Memory that looks like it holds a credential never leaves the machine, whatever its
        # consent says: the credential tripwire applies to context as well as to the request.
        cred_withheld: list[str] = []
        if vendor_of(provider) not in LOCAL_VENDORS:
            cred_withheld = [i.id for i in chosen if find_credentials(i.content)]
            if cred_withheld:
                bad = set(cred_withheld)
                chosen = [i for i in chosen if i.id not in bad]
                profile = [x for x in profile if x.id not in bad]
                memories = [x for x in memories if x.id not in bad]
                self.audit.append(user_id, "egress.credential_withheld", interaction=interaction_id, items=len(bad))

        ap = self._assist(user_id)
        md = _mode
        if md is None:
            default = self.get_intelligence_mode(user_id)
            if intelligence_mode is not None or default is not None:
                md = intelligence.resolve(intelligence_mode, default, task_type, ap.growth_tasks)
        mode = ap.select(task_type, self.rng, requested=assist_mode or (md.assist_mode if md else None),
                         among=md.among if md else None)
        packet = ContextPacket(
            packet_id=interaction_id, task=task, directives=[DIRECTIVES[mode], *(md.directives if md else ())],
            profile=profile, memories=memories, nonce=secrets.token_hex(8),
            meta={"policy": pol.name, "version": pol.version, "budget": budget},
        )
        prompt = render(packet)
        resp = adapter.generate(prompt)
        plan_cost = plan.cost_usd if plan is not None else 0.0
        cost = None if resp.cost_usd is None or plan_cost is None else resp.cost_usd + plan_cost

        # G — log
        props = sel.propensities.tolist() if sel.propensities is not None else []
        receipt = {
            "provider": provider, "model": resp.model, "assist_mode": mode, "task_type_probs": probs,
            "sent": [{"id": i.id, "kind": i.kind, "source": i.source, "trust": i.trust.value,
                      "where": "profile" if i.pinned and i.trust.rank >= Trust.USER_STATED.rank else "notes"}
                     for i in chosen],
            "withheld_by_egress": [{"id": i, "reason": r} for i, r in decision.blocked]
                                  + [{"id": i, "reason": "credential-shaped content"} for i in cred_withheld],
            "candidates_considered": cs_len,
            "retrieval_plan": queries,
            "plan_usage": dataclasses.asdict(plan.usage) if plan is not None else None,
            "embedder": self.embedder.name,
            "selection": cx.explain,
            "out_of_scope": cx.out_of_scope,
            "context_tokens": int(sum(i.tokens for i in chosen)),
            "budget": budget, "policy": pol.name, "policy_version": pol.version,
            "usage": dataclasses.asdict(resp.usage), "cost_usd": cost,
            "latency_ms": round(resp.latency_ms + (plan.latency_ms if plan is not None else 0.0), 1),
            "stop_reason": resp.stop_reason, "refusal": resp.refusal, "prompt_sha256": prompt.digest(),
            "egress_audit_hash": decision.audit_hash,
            "intelligence_mode": md.as_dict() if md else None, "project_id": project_id, "route": route,
        }
        self.store.insert_interaction(
            {
                "id": interaction_id, "user_id": user_id, "ts": now.isoformat(), "task_type": task_type,
                "provider": provider, "model": resp.model, "policy": pol.name, "policy_version": pol.version,
                "assist_mode": mode, "selected": [i.id for i in chosen], "propensities": props,
                "x_vector": sel.x.tolist() if sel.x is not None else None,
                "tokens_context": receipt["context_tokens"], "tokens_in": resp.usage.input_tokens,
                "tokens_out": resp.usage.output_tokens, "cache_read": resp.usage.cache_read_tokens,
                "cost_usd": cost, "latency_ms": receipt["latency_ms"], "stop_reason": resp.stop_reason,
                "packet_hash": prompt.digest(),
            },
            task_text=task, response_text=resp.text, receipt=receipt,
        )
        self.audit.append(user_id, "model.call", interaction=interaction_id, provider=provider, model=resp.model,
                          items=len(chosen), prompt_sha256=prompt.digest(), cost_usd=cost)
        return AskResult(interaction_id, resp.text, receipt)

    def feedback(self, user_id: str, interaction_id: str, **signals) -> float | None:
        rec = self.store.get_interaction(interaction_id, with_payload=False)
        if rec is None or rec["user_id"] != user_id:
            raise KeyError(interaction_id)
        r = reward_from_signals(signals)
        self.store.set_feedback(interaction_id, signals, r)
        if r is not None and rec.get("x_vector") is not None:
            version = self.learner.update(user_id, np.array(rec["x_vector"]), r)
            ap = self._assist(user_id)
            ap.update(rec["task_type"], rec["assist_mode"], r)
            self._save_assist(user_id, ap)
            self.audit.append(user_id, "feedback", interaction=interaction_id, reward=round(r, 4),
                              policy_version=version)
        else:
            self.audit.append(user_id, "feedback", interaction=interaction_id, reward=None)
        return r

    # ------------------------------------------------------------------ modes and settings
    def _setting(self, user_id: str, key: str):
        u = self.store.get_user(user_id)
        if u is None:
            raise KeyError(f"unknown user {user_id}")
        return (u.get("settings") or {}).get(key)

    def _set_setting(self, user_id: str, key: str, value) -> None:
        u = self.store.get_user(user_id)
        if u is None:
            raise KeyError(f"unknown user {user_id}")
        s = dict(u.get("settings") or {})
        s[key] = value
        self.store.update_settings(user_id, s)

    def get_intelligence_mode(self, user_id: str) -> str | None:
        """The person's default mode, or None if they never set one."""
        return self._setting(user_id, SETTING_MODE)

    def set_intelligence_mode(self, user_id: str, mode: str) -> str:
        m = intelligence.normalize(mode)
        self._set_setting(user_id, SETTING_MODE, m)
        self.audit.append(user_id, "mode.default", mode=m)
        return m

    def get_routing_preference(self, user_id: str) -> str:
        return self._setting(user_id, SETTING_PREFERENCE) or "balanced"

    def set_routing_preference(self, user_id: str, preference: str) -> str:
        if preference not in PREFERENCES:
            raise ValueError(f"preference must be one of {PREFERENCES}")
        self._set_setting(user_id, SETTING_PREFERENCE, preference)
        self.audit.append(user_id, "route.preference", preference=preference)
        return preference

    # ------------------------------------------------------------------ routing
    def authorized_vendors(self, user_id: str) -> set[str]:
        """Vendors that may receive the person's own words: an active egress grant for source `user`.
        Routing never offers any other vendor, so CI never picks a recipient the person did not."""
        now, grants = utcnow(), self.ledger.grants(user_id)
        return {v for v in VENDORS if any(g.active(now) and g.covers("user", "egress", v) for g in grants)}

    def _latency(self, user_id: str) -> dict:
        """(provider, canonical model) → (p50 ms, n) from this person's own interaction log."""
        by: dict[tuple, list] = {}
        for r in self.store.list_interactions(user_id):
            ident = model_identity(r["provider"], r["model"])
            if ident and r["latency_ms"] is not None:
                by.setdefault((r["provider"], ident[1]), []).append(r["latency_ms"])
        return {k: (float(np.median(v)), len(v)) for k, v in by.items()}

    def route(self, user_id: str, task: str, *, mode: str | None = None, pinned: str | None = None,
              project_vendors: set[str] | None = None, budget_tokens: int | None = None,
              include_test_doubles: bool | None = None, min_tier: int | None = None) -> RoutePlan:
        """Which model would answer `task`, and why. No model is called."""
        allowed = self.authorized_vendors(user_id)
        if project_vendors is not None:
            allowed &= set(project_vendors)
        return route_plan(task, allowed_vendors=allowed, mode=mode, preference=self.get_routing_preference(user_id),
                          budget_tokens=budget_tokens or self.budget_tokens, pinned=pinned,
                          include_test_doubles=include_test_doubles, latency=self._latency(user_id), min_tier=min_tier,
                          embedder=self.embedder)

    def resolve_mode(self, user_id: str, task: str, mode: str | None = None) -> "intelligence.ModeDecision":
        probs = classify_task(task)
        return intelligence.resolve(mode, self.get_intelligence_mode(user_id), max(probs, key=probs.get),
                                    self._assist(user_id).growth_tasks)

    def ask_routed(self, user_id: str, task: str, *, mode: str | None = None, pinned: str | None = None,
                   project_id: str | None = None, project_vendors: set[str] | None = None,
                   budget_tokens: int | None = None, include_test_doubles: bool | None = None,
                   min_tier: int | None = None) -> AskResult:
        """Resolve the mode, route, ask; on a provider failure try the next candidate. Each attempt
        re-runs context selection for its own vendor, so a fallback never sends what that vendor
        may not see. Raises RoutingError when routing refuses or every candidate fails."""
        if self.store.get_user(user_id) is None:
            raise KeyError(f"unknown user {user_id}")
        if project_id is not None and project_vendors is None:
            # Fail closed: project memory may only reach vendors the project allows.
            from .workspace import Workspace
            project_vendors = set(Workspace(self).provider_vendors(user_id, project_id))
        md = self.resolve_mode(user_id, task, mode)
        plan = self.route(user_id, task, mode=md.mode, pinned=pinned, project_vendors=project_vendors,
                          budget_tokens=budget_tokens, include_test_doubles=include_test_doubles, min_tier=min_tier)
        self.audit.append(user_id, "route.plan", chosen=plan.chosen.key if plan.chosen else None,
                          tier=plan.requirement.tier_name, mode=md.mode, refused=plan.refused is not None,
                          credentials_found=len(plan.requirement.credentials))
        if plan.refused:
            raise RoutingError(plan.refused, plan)
        attempts: list[dict] = []
        dead: dict[str, str] = {}  # provider -> why each of its models would fail the same way (key, login, setup)
        for c in plan.candidates:
            if c.entry.provider in dead:
                attempts.append({"model": c.key, "error": f"skipped: {dead[c.entry.provider]}", "kind": "skipped"})
                continue
            info = {"plan": plan.as_dict(), "served_by": c.key, "served_meets": c.meets,
                    "served_tier": c.as_dict()["tier"], "served_est_cost_usd": c.est_cost_usd,
                    "failed_before": list(attempts)}
            try:
                return self.ask(user_id, task, c.entry.provider, c.entry.model or None, budget_tokens,
                                _mode=md, project_id=project_id, route=info)
            except fallback_errors() as e:
                # Every failure still falls back: another vendor or model is a different request. A
                # non-retryable failure is not retried in place (the SDKs already retry transient ones).
                kind, status = getattr(e, "kind", "unknown"), getattr(e, "status", None)
                cred = kind in CREDENTIAL_KINDS
                attempts.append({"model": c.key, "error": redact(f"{type(e).__name__}: {str(e)[:200]}")[0], "kind": kind,
                                 "status": status, "retryable": getattr(e, "retryable", None),
                                 "credential_problem": cred})
                self.audit.append(user_id, "route.fallback", model=c.key, error=type(e).__name__, kind=kind,
                                  status=status, credential_problem=cred)
                if kind in PROVIDER_WIDE_KINDS:  # same key / login / config: its other models cannot do better
                    dead[c.entry.provider] = f"{c.entry.provider} has a {'credential problem' if cred else 'setup problem'}"
        creds = sorted({a["model"].split(":")[0] for a in attempts if a.get("credential_problem")})
        raise RoutingError("every routed model failed" + (f" (credential problem: {', '.join(creds)})" if creds else ""),
                           plan, attempts)

    def record_outcome(self, user_id: str, interaction_id: str, worked: bool, corrected: bool = False,
                       skill: str | None = None) -> dict:
        """A verified outcome: the strongest feedback signal (feedback.py). With `skill`, it is also
        an *assisted* attempt at that skill, which the report compares with unassisted practice."""
        reward = self.feedback(user_id, interaction_id, task_success=bool(worked), corrected=bool(corrected))
        out = {"reward": reward}
        if skill:
            out["attempt"] = self.probe(user_id, skill, bool(worked), assisted=True, source="outcome",
                                        interaction_id=interaction_id)
        return out

    # ------------------------------------------------------------------ growth
    def probe(self, user_id: str, skill: str, correct: bool, assisted: bool = False, source: str = "api",
              interaction_id: str | None = None) -> dict:
        """Record an attempt at `skill`. Assisted attempts are logged but never move mastery."""
        skill = " ".join(str(skill).split())
        if not skill:
            raise ValueError("skill name is empty")
        skills = self.store.list_skills(user_id)
        state = BKT(**skills[skill]) if skill in skills else BKT()
        if not assisted:
            state.observe(bool(correct))
            self.store.put_skill(user_id, skill, state.to_dict())
        self._log_attempt(user_id, {"ts": utcnow().isoformat(), "skill": skill, "correct": bool(correct),
                                    "assisted": bool(assisted), "source": source[:20], "interaction": interaction_id})
        # The audit is plaintext and outlives erasure: it records a tag keyed by the person's own key
        # (destroyed on erase), never the skill's name, which can say more than intended.
        self.audit.append(user_id, "growth.probe", skill_tag=self._tag(user_id, skill), correct=bool(correct),
                          assisted=assisted)
        return {"skill": skill, "p_mastery": state.p_mastery, "counted": not assisted}

    def _state_json(self, user_id: str, name: str, default):
        loaded = self.store.load_policy_state(user_id, name)
        return (loaded[0], json.loads(loaded[1])) if loaded else (None, default)

    def _log_attempt(self, user_id: str, rec: dict) -> None:
        def append(cur: bytes | None) -> bytes:
            log = json.loads(cur) if cur else []
            return json.dumps((log + [rec])[-ATTEMPT_LOG_MAX:]).encode()
        self.store.update_policy_state(user_id, ATTEMPTS_STATE, append, note="attempt log")

    def attempts(self, user_id: str) -> list[dict]:
        return self._state_json(user_id, ATTEMPTS_STATE, [])[1]

    def _tag(self, user_id: str, text: str) -> str:
        return hmac.new(self.store._user_dek(user_id), text.encode(), "sha256").hexdigest()[:16]

    def forget_skill(self, user_id: str, skill: str) -> dict:
        """Delete a skill's mastery state, every logged attempt at it, and its name and evidence in
        every weekly snapshot."""
        version, log = self._state_json(user_id, ATTEMPTS_STATE, [])
        kept = [a for a in log if a["skill"] != skill]
        if version is not None:
            self.store.overwrite_policy_state(user_id, ATTEMPTS_STATE, version, json.dumps(kept).encode())
        had_state = self.store.delete_skill(user_id, skill)
        scrubbed = self._rewrite_weekly(user_id, lambda snap: _scrub(snap, skill))
        self.store.flush_deleted()
        self.audit.append(user_id, "growth.forget", attempts=len(log) - len(kept), state=had_state,
                          snapshots=scrubbed)
        return {"skill": skill, "attempts_deleted": len(log) - len(kept), "state_deleted": had_state}

    def _rewrite_weekly(self, user_id: str, fn) -> int:
        n = 0
        for v in self.store.list_policy_versions(user_id, TWIN_STATE):
            loaded = self.store.load_policy_state(user_id, TWIN_STATE, v["version"])
            snap = json.loads(loaded[1])
            new = fn(snap)
            if new != snap:
                self.store.overwrite_policy_state(user_id, TWIN_STATE, v["version"], json.dumps(new, default=str).encode())
                n += 1
        return n

    def skill_report(self, user_id: str, now=None) -> dict:
        return independence_report(self.store.list_skills(user_id), self.attempts(user_id), now or utcnow())

    # ------------------------------------------------------------------ weekly reviews (twin.py)
    # One immutable version per week, so last week's priorities can be checked against this
    # week's evidence and any weekly update can be rolled back. Re-running within the same
    # week replaces that week's version instead of adding one.
    def weekly_snapshots(self, user_id: str) -> list[dict]:
        out = []
        for v in self.store.list_policy_versions(user_id, TWIN_STATE):
            loaded = self.store.load_policy_state(user_id, TWIN_STATE, v["version"])
            out.append({"version": v["version"], "active": bool(v["active"]), **json.loads(loaded[1])})
        return out

    def active_weekly(self, user_id: str) -> tuple[int | None, dict | None]:
        return self._state_json(user_id, TWIN_STATE, None)

    def save_weekly(self, user_id: str, snapshot: dict) -> int:
        version, active = self.active_weekly(user_id)
        versions = [v["version"] for v in self.store.list_policy_versions(user_id, TWIN_STATE)]
        blob = json.dumps(snapshot, default=str).encode()
        if active is not None and active.get("week") == snapshot["week"] and version == max(versions):
            self.store.overwrite_policy_state(user_id, TWIN_STATE, version, blob)
        else:
            version = self.store.save_policy_state(user_id, TWIN_STATE, blob, note=f"weekly review {snapshot['week']}",
                                                   parent=version)
        self.audit.append(user_id, "twin.snapshot", version=version, week=snapshot["week"])
        return version

    def activate_weekly(self, user_id: str, version: int) -> None:
        self.store.activate_policy_version(user_id, TWIN_STATE, version)
        self.audit.append(user_id, "twin.rollback", version=version)

    def forget_weekly(self, user_id: str) -> int:
        """Delete every weekly snapshot and every --deep request (the digests CI sent)."""
        n = self.store.delete_policy_states(user_id, TWIN_STATE)
        deep = [r["id"] for r in self.store.list_interactions(user_id, with_payload=True)
                if ((r.get("receipt") or {}).get("route") or {}).get("purpose") == "weekly-deep"]
        for iid in deep:
            self.store.delete_interaction(iid)
        self.store.flush_deleted()
        self.audit.append(user_id, "twin.forget", versions=n, deep_requests=len(deep))
        return n

    def next_challenge(self, user_id: str) -> str | None:
        return next_challenge({k: BKT(**v) for k, v in self.store.list_skills(user_id).items()})

    # ------------------------------------------------------------------ inspection
    def receipt(self, user_id: str, interaction_id: str) -> dict:
        rec = self.store.get_interaction(interaction_id)
        if rec is None or rec["user_id"] != user_id:
            raise KeyError(interaction_id)
        items = []
        for s in rec["receipt"]["sent"]:
            it = self.store.get_item(s["id"])
            items.append({**s, "content": it.content if it else "[deleted since — the key is destroyed]"})
        return {**rec["receipt"], "sent": items, "task": rec["task"], "response": rec["response"],
                "feedback": rec.get("feedback"), "reward": rec.get("reward")}

    def dna(self, user_id: str) -> dict:
        return intelligence_dna(self.memory, user_id, self.learner.policy_for(user_id),
                                self.store.list_skills(user_id))

    def metrics(self, user_id: str) -> dict:
        rows = self.store.list_interactions(user_id)
        by_provider: dict[str, dict] = {}
        for r in rows:
            p = by_provider.setdefault(f"{r['provider']}/{r['model']}", {"calls": 0, "cost_usd": 0.0, "cost_unknown": 0,
                                                                          "tokens_in": 0, "tokens_out": 0, "latency_ms": []})
            p["calls"] += 1
            if r["cost_usd"] is None:
                p["cost_unknown"] += 1
            else:
                p["cost_usd"] += r["cost_usd"]
            p["tokens_in"] += r["tokens_in"] or 0
            p["tokens_out"] += r["tokens_out"] or 0
            p["latency_ms"].append(r["latency_ms"] or 0)
        for p in by_provider.values():
            lat = p.pop("latency_ms")
            p["latency_ms_p50"] = float(np.median(lat)) if lat else None
        rewards = [r["reward"] for r in rows if r["reward"] is not None]
        window = 10
        curve = [float(np.mean(rewards[i:i + window])) for i in range(0, len(rewards), window)]
        return {
            "interactions": len(rows), "with_feedback": len(rewards),
            "mean_reward": float(np.mean(rewards)) if rewards else None,
            "reward_curve_per_10": curve, "context_tokens_mean": float(np.mean([r["tokens_context"] or 0 for r in rows])) if rows else 0.0,
            "by_provider": by_provider, "policy_versions": self.learner.versions(user_id),
            "audit_chain_ok": self.audit.verify()[0],
            "caveat": "Reward is from your own feedback; a rising curve is not evidence CI is better than a "
                      "baseline. That needs the paired comparison in `pcim compare` / docs/04 G2.",
        }

    def export(self, user_id: str) -> dict:
        return {
            "format": "pcim-export/1",
            "user": self.store.get_user(user_id),
            "memory": self.memory.export(user_id),
            "consent": [dataclasses.asdict(g) | {"max_sensitivity": g.max_sensitivity.value}
                        for g in self.ledger.grants(user_id)],
            "interactions": self.store.list_interactions(user_id, with_payload=True),
            "dna": self.dna(user_id),
            "growth": {"attempts": self.attempts(user_id), "weekly_reviews": self.weekly_snapshots(user_id),
                       "challenges": self._state_json(user_id, CHALLENGES_STATE, [])[1]},
            "audit": self.audit.entries(user_id),
            "audit_head": self.audit.head(),
        }

    def erase(self, user_id: str) -> dict:
        """Delete the user and everything about them; destroys every key. The audit log keeps
        pseudonymous ids of what was erased (no content), as the record of the erasure."""
        counts = self.store.delete_user(user_id)
        self.learner._cache.pop(user_id, None)
        self.memory._index.pop(user_id, None)
        self.audit.append(user_id, "user.erase", **{k: int(v) for k, v in counts.items()})
        return counts
