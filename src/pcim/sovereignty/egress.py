"""The egress guard — the only path by which personal context reaches a model provider.

Every candidate item passes these checks, in order, and every decision (allowed and
blocked, with a reason code) is written to the audit log before anything is sent:

1. ``secret``                — SECRET never leaves the device.
2. ``not-current``           — superseded, retracted or expired items are not context
                               (temporal correctness enforced twice: here and in memory).
3. ``unconfirmed-instructions`` — text flagged as containing instructions is withheld from
                               every provider until the user confirms it in the dashboard.
4. lineage consent           — the item's own source *and every ancestor's source* must
                               hold an active egress grant for this provider at or above
                               the item's sensitivity.

Passing the guard does not make the content safe to *follow*; the renderer still fences it
as data. The guard decides what the provider may *see*.
"""

from __future__ import annotations

import dataclasses
import hashlib

from ..store import Store
from ..types import MemoryItem, Sensitivity, utcnow
from .audit import AuditLog
from .consent import ConsentLedger
from .content import labels

INSTRUCTION_FLAG = "contains_instructions"
CONFIRMED_FLAG = "user_confirmed_safe"


@dataclasses.dataclass
class EgressDecision:
    allowed: list[MemoryItem]
    blocked: list[tuple[str, str]]  # (item id, reason code)
    audit_hash: str


class EgressGuard:
    def __init__(self, store: Store, ledger: ConsentLedger, audit: AuditLog, max_lineage_depth: int = 8):
        self.store = store
        self.ledger = ledger
        self.audit = audit
        self.max_depth = max_lineage_depth

    def lineage_sources(self, item: MemoryItem) -> tuple[set[str], Sensitivity, bool]:
        """Sources and max sensitivity across the item and its ancestors.

        The third value is False if an ancestor has been deleted. A derived item whose
        parent was erased is withheld: the user's deletion of the source is taken to cover
        what was inferred from it, and the item is surfaced for review instead.
        """
        sources, sens, intact = {item.source}, item.sensitivity, True
        frontier, seen, depth = list(item.parents), {item.id}, 0
        while frontier and depth < self.max_depth:
            nxt = []
            for pid in frontier:
                if pid in seen:
                    continue
                seen.add(pid)
                parent = self.store.get_item(pid)
                if parent is None or parent.user_id != item.user_id:
                    intact = False
                    continue
                sources.add(parent.source)
                if parent.sensitivity.rank > sens.rank:
                    sens = parent.sensitivity
                nxt.extend(parent.parents)
            frontier, depth = nxt, depth + 1
        if any(pid not in seen for pid in frontier):
            intact = False  # an unexamined ancestor cannot be presumed consented
        return sources, sens, intact

    def check(self, user_id: str, item: MemoryItem, provider: str, grants=None,
              require_current: bool = True) -> tuple[bool, str]:
        """`require_current=False` skips only the temporal check: for content a model derived
        from the item while it was current, which still needs every other check now."""
        now = utcnow()
        if item.user_id != user_id:
            return False, "wrong-owner"
        if item.sensitivity is Sensitivity.SECRET:
            return False, "secret"
        if labels(item.content):
            return False, "secret-content"
        if require_current and not item.is_current(now):
            return False, "not-current"
        if INSTRUCTION_FLAG in item.flags and CONFIRMED_FLAG not in item.flags:
            return False, "unconfirmed-instructions"
        sources, sens, intact = self.lineage_sources(item)
        if not intact:
            return False, "ancestor-deleted"
        if sens is Sensitivity.SECRET:
            return False, "secret-ancestor"
        for src in sorted(sources):
            ok, reason = self.ledger.allows(user_id, src, "egress", provider, sens, now, grants=grants)
            if not ok:
                return False, f"{reason}:{src}"
        return True, "ok"

    def filter(
        self, user_id: str, items: list[MemoryItem], provider: str, model: str, ref: str
    ) -> EgressDecision:
        allowed, blocked = [], []
        grants = self.ledger.grants(user_id)  # one snapshot of the ledger for the whole decision
        for it in items:
            ok, reason = self.check(user_id, it, provider, grants=grants)
            if ok:
                allowed.append(it)
            else:
                blocked.append((it.id, reason))
        # The permitted set is recorded as a count and a hash of its sorted ids, which is
        # verifiable against the store and does not grow the log with the size of memory.
        # What was actually sent is itemized in the receipt or session disclosure. Refusals
        # are itemized here (up to 200; the count is always exact).
        h = self.audit.append(
            user_id,
            "egress",
            ref=ref,
            provider=provider,
            model=model,
            allowed_n=len(allowed),
            allowed_sha256=hashlib.sha256(",".join(sorted(i.id for i in allowed)).encode()).hexdigest(),
            blocked=[[i, r[:120]] for i, r in blocked[:200]],
            blocked_n=len(blocked),
            tokens=sum(i.tokens for i in allowed),
        )
        return EgressDecision(allowed, blocked, h)
