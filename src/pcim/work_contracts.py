"""Versioned specialist contracts. Assignment never confers tool authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Protocol

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RunLimits:
    max_attempts: int = 6
    max_attempts_per_work: int = 2
    max_model_steps: int = 48
    max_tool_calls: int = 120
    max_seconds: float = 900

    def __post_init__(self):
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0
               for v in asdict(self).values()):
            raise ValueError("run limits must be positive")
        if any(not isinstance(getattr(self, k), int) for k in asdict(self) if k != "max_seconds"):
            raise ValueError("count limits must be integers")
        import math
        if not math.isfinite(self.max_seconds):
            raise ValueError("time limit must be finite")


@dataclass(frozen=True)
class WorkItem:
    id: str
    role: str
    objective: str
    depends_on: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    require_write: bool = True
    require_tests: bool = False

    def as_dict(self):
        return asdict(self) | {"status": "queued"}


def validate_work(work: list[dict]) -> list[dict]:
    if not work or len(work) > 32:
        raise ValueError("a run needs 1–32 work items")
    out = []
    for raw in work:
        w = WorkItem(**{k: v for k, v in raw.items() if k != "status"})
        if not all(isinstance(s, str) and s.strip() for s in (w.id, w.role, w.objective)):
            raise ValueError("work id, role and objective are required")
        if not isinstance(w.require_write, bool) or not isinstance(w.require_tests, bool):
            raise ValueError("evidence requirements must be booleans")
        if isinstance(w.depends_on, str) or isinstance(w.artifacts, str):
            raise ValueError("dependencies and artifacts must be lists")
        for path in w.artifacts:
            p = PurePosixPath(path)
            if not path or p.is_absolute() or '..' in p.parts or '\\' in path:
                raise ValueError("artifacts must be workspace relative paths")
        out.append(w.as_dict())
    ids = [w['id'] for w in out]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate work ids")
    remaining, done = list(out), set()
    while remaining:
        ready = [w for w in remaining if set(w['depends_on']) <= done]
        if not ready:
            raise ValueError("unknown dependency or dependency cycle")
        for w in ready:
            done.add(w['id'])
            remaining.remove(w)
    return out


def default_plan(goal: str) -> list[dict]:
    """Conservative execution decomposition; workers interpret the goal, not keywords."""
    return [w.as_dict() for w in (
        WorkItem('build', 'builder', f'Implement the core behavior requested by: {goal}'),
        WorkItem('interface', 'interface', f'Integrate the user interface and usage documentation for: {goal}', ('build',)),
        WorkItem('verify', 'verifier', f'Add meaningful behavioral checks for: {goal}; run the configured test suite',
                 ('build', 'interface'), require_tests=True),
    )]


class RunState(Protocol):
    """Durable adapters must encrypt snapshots, scope ownership, CAS, and lock the project."""
    def create(self, user_id: str, project_id: str, snapshot: dict) -> dict: ...
    def get(self, user_id: str, project_id: str, run_id: str) -> dict: ...
    def list(self, user_id: str, project_id: str) -> list[dict]: ...
    def save(self, user_id: str, project_id: str, run_id: str, snapshot: dict, expected_revision: int) -> dict: ...
    def lock(self, user_id: str, project_id: str): ...
