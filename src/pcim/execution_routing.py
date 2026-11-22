"""Specialist routing uses registry consent/capability/availability, never implicit mocks."""
from dataclasses import asdict, dataclass

from . import registry


@dataclass(frozen=True)
class ExecutionRoute:
    provider: str
    model: str | None
    role: str
    reason: str
    test_double: bool
    cost_basis: str = 'unknown; no charge inferred'

    def as_dict(self):
        return asdict(self)


def select(role: str, allowed_vendors: set[str], *, provider=None, model=None) -> ExecutionRoute:
    if provider:
        entry = registry.find(provider, model)
        if entry is None or 'chat' not in entry.capabilities:
            raise ValueError('selected model has no registered tool-using adapter')
        if entry.vendor not in allowed_vendors:
            raise PermissionError('selected vendor is not allowed for this run')
        ok, reason = registry.availability(entry)
        if not ok:
            raise RuntimeError(reason)
        return ExecutionRoute(provider, model, role, 'explicit selection; ' + reason, entry.test_double)
    r = registry.route('chat', allowed_vendors)
    if r.entry is None:
        raise RuntimeError(r.reason)
    e = r.entry
    return ExecutionRoute(e.provider, e.model or None, role, r.reason, e.test_double)
