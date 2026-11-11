"""Layer E — cognitive adaptation: *how* to help, not just what to send.

A contextual bandit over assistance modes, one Beta posterior per (task type, mode),
with the person's stated intent taking precedence over anything learned:

- If the person marks a task type as something they want to *get better at*, only growth
  modes are eligible there (hint first, Socratic, explain) — a direct answer would win on
  immediate reward and lose on the thing they asked for. This is the policy-level guard
  against the deskilling result (Bastani et al., PNAS 2025: unguarded GPT-4 access left
  students 17% worse once it was removed; a tutor with guardrails largely did not).
- An explicit per-request mode always wins.

Status: implemented and unit-tested; not evaluated against people. Whether a learned
mode policy improves outcomes is G2/G3 (docs/04), not something this code can show.
"""

from __future__ import annotations

import dataclasses

import numpy as np

from .types import TASK_TYPES

MODES = ("direct", "explain", "hint_first", "socratic", "verify_only", "clarify_first", "amplify")
GROWTH_MODES = frozenset({"hint_first", "socratic", "explain"})
# Chosen by a rule or by the person, never by the bandit: clarify by Layer C's VOI rule,
# amplify by the AMPLIFY intelligence mode (modes.py). Keeping amplify out of the bandit means
# a request with no mode behaves exactly as it did before amplify existed.
_NOT_SAMPLED = frozenset({"clarify_first", "amplify"})

DIRECTIVES = {
    "direct": "Give the answer or the finished work directly and concisely.",
    "explain": "Give the answer, then explain the reasoning in a way the person could reuse next time.",
    "hint_first": "Do not give the full answer yet. Give the smallest hint that lets the person take the "
                  "next step themselves, and offer more if they ask.",
    "socratic": "Guide with one or two questions that lead the person to the answer; do not state it.",
    "verify_only": "The person wants their own work checked: point out errors and gaps, do not rewrite it.",
    "clarify_first": "The request is ambiguous in a way that changes the answer. Ask one short clarifying "
                     "question before doing the work.",
    "amplify": "Work as the person's thinking partner, briefly: give your recommendation, the strongest "
               "alternative and the main trade-off between them, and name any assumption in the request that "
               "the evidence does not support. Agree only where the evidence does; do not flatter.",
}


@dataclasses.dataclass
class AssistPolicy:
    alpha: dict = dataclasses.field(default_factory=dict)  # (task, mode) -> successes + prior
    beta: dict = dataclasses.field(default_factory=dict)
    growth_tasks: set = dataclasses.field(default_factory=set)
    prior_success: float = 1.0
    prior_failure: float = 1.0

    def _ab(self, t: str, m: str) -> tuple[float, float]:
        k = f"{t}|{m}"
        return self.alpha.get(k, self.prior_success), self.beta.get(k, self.prior_failure)

    def eligible(self, task_type: str) -> list[str]:
        if task_type in self.growth_tasks:
            return [m for m in MODES if m in GROWTH_MODES]
        return [m for m in MODES if m not in _NOT_SAMPLED]

    def select(self, task_type: str, rng: np.random.Generator, requested: str | None = None,
               among: frozenset[str] | None = None) -> str:
        """`among` narrows the bandit's choice (LEARN mode samples only growth modes)."""
        if requested:
            if requested not in MODES:
                raise ValueError(f"mode must be one of {MODES}")
            return requested
        modes = self.eligible(task_type)
        if among is not None:
            modes = [m for m in MODES if m in among and m not in _NOT_SAMPLED] or modes
        draws = [rng.beta(*self._ab(task_type, m)) for m in modes]
        return modes[int(np.argmax(draws))]

    def update(self, task_type: str, mode: str, reward: float) -> None:
        a, b = self._ab(task_type, mode)
        k = f"{task_type}|{mode}"
        self.alpha[k], self.beta[k] = a + reward, b + (1.0 - reward)

    def to_dict(self) -> dict:
        return {"alpha": self.alpha, "beta": self.beta, "growth_tasks": sorted(self.growth_tasks)}

    @classmethod
    def from_dict(cls, d: dict) -> "AssistPolicy":
        bad = set(d.get("growth_tasks", [])) - set(TASK_TYPES)
        if bad:
            raise ValueError(f"unknown task types {bad}")
        return cls(alpha=dict(d.get("alpha", {})), beta=dict(d.get("beta", {})),
                   growth_tasks=set(d.get("growth_tasks", [])))
