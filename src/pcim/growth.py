"""Cognitive growth engine — measuring skill without measuring dependence.

Bayesian Knowledge Tracing (Corbett & Anderson, 1994) per skill, with one rule that
matters more than the model: **only unassisted probes update mastery.** An assisted
success is evidence the assistance worked, not that the person learned; counting it would
make the H term in the objective reward exactly the dependency it is meant to prevent.

`next_challenge` picks the skill whose predicted success is closest to 0.85 — the
"eighty-five percent rule" (Wilson et al., Nat. Commun. 2019) used as a default for
desirable difficulty, not as a finding about this person.
"""

from __future__ import annotations

import dataclasses
import datetime as dt


@dataclasses.dataclass
class BKT:
    p_mastery: float = 0.2
    p_learn: float = 0.15
    p_slip: float = 0.10
    p_guess: float = 0.20
    n_probes: int = 0

    def p_correct(self) -> float:
        return self.p_mastery * (1 - self.p_slip) + (1 - self.p_mastery) * self.p_guess

    def observe(self, correct: bool) -> float:
        m, s, g = self.p_mastery, self.p_slip, self.p_guess
        if correct:
            post = m * (1 - s) / (m * (1 - s) + (1 - m) * g)
        else:
            post = m * s / (m * s + (1 - m) * (1 - g))
        self.p_mastery = post + (1 - post) * self.p_learn
        self.n_probes += 1
        return self.p_mastery

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def next_challenge(skills: dict[str, BKT], target: float = 0.85) -> str | None:
    if not skills:
        return None
    return min(skills, key=lambda k: abs(skills[k].p_correct() - target))


# ---------------------------------------------------------------------- independent performance
# The attempt log keeps every recorded attempt, assisted or not, so the two can be compared.
# BKT above still moves on unassisted attempts only. The log is bounded (ATTEMPT_LOG_MAX per
# person, oldest dropped) because the report needs recent evidence, not a lifetime of it.
ATTEMPT_LOG_MAX = 1000
MIN_EVIDENCE = 3  # attempts on each side before a rate is compared or a flag is raised
DEPENDENCY_GAP = 0.25  # assisted rate minus unassisted rate that counts as a gap
REVIEW_AFTER_DAYS = 7  # an unpractised skill is due for a retention check after this long
BASIS = ("From your recorded attempts only (`ci practice`, LEARN-mode outcomes). Mastery moves on unassisted "
         "attempts only. This describes performance on these skills, not intelligence or ability in general.")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson interval for k successes in n, which stays honest at small n."""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)


def _side(xs: list[dict]) -> dict:
    n, k = len(xs), sum(1 for a in xs if a["correct"])
    return {"n": n, "correct": k, "rate": round(k / n, 3) if n else None, "ci95": wilson(k, n)}


def _ts(a: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(a["ts"])


def independence_report(skills: dict[str, dict], attempts: list[dict], now) -> dict:
    """Per skill: unassisted vs assisted accuracy, trend, and evidence-backed flags.

    Flags are statements about recorded attempts, each with its counts:
      dependency_gap        assisted rate exceeds unassisted by ≥ DEPENDENCY_GAP (≥3 each side)
      recurring_difficulty  ≥3 of the last 5 unassisted attempts were wrong
      due_for_review        no unassisted attempt in REVIEW_AFTER_DAYS days
    """
    out = {}
    for s in sorted(set(skills) | {a["skill"] for a in attempts}):
        mine = sorted((a for a in attempts if a["skill"] == s), key=_ts)
        un = [a for a in mine if not a["assisted"]]
        asst = [a for a in mine if a["assisted"]]
        u, h = _side(un), _side(asst)
        gap = round(h["rate"] - u["rate"], 3) if u["n"] >= MIN_EVIDENCE and h["n"] >= MIN_EVIDENCE else None
        flags, evidence = [], {}
        if gap is not None and gap >= DEPENDENCY_GAP:
            flags.append("dependency_gap")
            evidence["dependency_gap"] = (f"with help {h['correct']}/{h['n']}, alone {u['correct']}/{u['n']}")
        last = un[-5:]
        wrong = sum(1 for a in last if not a["correct"])
        if len(last) >= 3 and wrong >= 3:
            flags.append("recurring_difficulty")
            evidence["recurring_difficulty"] = f"{wrong} of the last {len(last)} unassisted attempts were wrong"
        if un:
            idle = (now - _ts(un[-1])).total_seconds() / 86400
            if idle >= REVIEW_AFTER_DAYS:
                flags.append("due_for_review")
                evidence["due_for_review"] = f"last unassisted attempt {idle:.0f} days ago"
        if len(un) >= 2 * MIN_EVIDENCE:
            half = len(un) // 2
            early, late = _side(un[:half])["rate"], _side(un[half:])["rate"]
            trend = ("improving" if late - early >= 0.2 else "declining" if early - late >= 0.2 else "flat")
            trend_d = {"direction": trend, "early_rate": early, "late_rate": late, "n": len(un)}
        else:
            trend_d = {"direction": "not enough evidence", "n": len(un), "needs": 2 * MIN_EVIDENCE}
        state = skills.get(s)
        out[s] = {"unassisted": u, "assisted": h, "dependency_gap": gap, "trend": trend_d, "flags": flags,
                  "evidence": evidence,
                  "mastery": ({"p_mastery": round(state["p_mastery"], 3), "p_correct": round(BKT(**state).p_correct(), 3),
                               "unassisted_probes": state["n_probes"]} if state else None),
                  "last_unassisted": un[-1]["ts"] if un else None}
    return {"skills": out, "overall": {"unassisted": _side([a for a in attempts if not a["assisted"]]),
                                       "assisted": _side([a for a in attempts if a["assisted"]])},
            "basis": BASIS}
