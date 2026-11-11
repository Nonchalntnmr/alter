"""Spaced review for skills: when the next unassisted check is due, from the attempt log.

Replaces the fixed "due after 7 days" rule with a memory model that lets the interval grow after
each unassisted success and shrink after each unassisted failure.

**Model: FSRS-4.5**, the Free Spaced Repetition Scheduler (Jarrett Ye and the open-spaced-repetition
project), a difficulty / stability / retrievability model descended from the DSR model of

  Ye, Su & Cao. A Stochastic Shortest Path Algorithm for Optimizing Spaced Repetition Scheduling.
  KDD 2022. doi:10.1145/3534678.3539081
  Su, Ye, Nie, Cao & Chen. Optimizing Spaced Repetition Schedule by Capturing the Dynamics of Memory.
  IEEE TKDE 35(10), 2023. doi:10.1109/TKDE.2023.3251721

The formulas and default weights below are FSRS-4.5's as given in the project's algorithm page
(github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm) and its reference implementations
(py-fsrs 3.x, fsrs-rs). These citations were written from memory in a session with no network
access and were not re-checked, so unlike docs/REFERENCES.md they are unverified.

Why FSRS rather than SM-2 (Wozniak, 1990): SM-2 schedules from the *planned* interval and needs a
0-5 quality grade. FSRS conditions every update on the time that actually passed (through R), so a
success after a long gap counts for more than one the same afternoon, and an immediate repeat adds
nothing. That matters here because nobody runs `ci practice` on schedule.

**What the parameters are.** The weights were fitted by the FSRS authors to Anki users' flashcard
reviews: recall of discrete facts, not skills like "sql joins", and not this person. Desired
retention 0.9 is FSRS's default. Nothing here is fitted to the person. A date this module produces
is "when the published default model would schedule the next check", not a prediction of when
this person will forget. Fitting the weights per person (the FSRS optimizer) needs hundreds of
reviews and is not done.

Model (t = days since the previous unassisted attempt, G = grade):
  R(t, S)       = (1 + FACTOR * t / S) ** DECAY                        predicted recall; R(S, S) = 0.9
  interval(S)   = S / FACTOR * (retention ** (1 / DECAY) - 1)          = S at retention 0.9
  first attempt S = W[G-1],  D = W4 - (G - 3) * W5
  success       S' = S * (1 + e^W8 * (11 - D) * S^-W9 * (e^(W10 (1 - R)) - 1))
  failure       S' = min(S, W11 * D^-W12 * ((S + 1)^W13 - 1) * e^(W14 (1 - R)))
  difficulty    D' = W7 * D0(Good) + (1 - W7) * (D - W6 * (G - 3)),  clamped to [1, 10]
Stability updates use the difficulty from before the attempt, as the reference implementations do.

Choices made here, not by FSRS:
- **Binary grades.** The log records right/wrong only, so correct is Good (3) and wrong is Again (1);
  the Hard and Easy weights (W15, W16) are never used.
- **Only unassisted attempts move the schedule**, the same rule as BKT in growth.py. An attempt
  counts only when it is marked `"assisted": False`; a record without the flag is treated as
  assisted. Assisted attempts appear only as a count in `reason`.
- **Fractional days.** Elapsed time is real time, not whole days, so intervals can be hours.
- **Same timestamp.** Ties keep log order (the order they were recorded). Zero elapsed time gives
  R = 1, so an immediate repeat success leaves stability unchanged and an immediate failure is
  still a lapse.
- **The cap on post-lapse stability** (a failure never lengthens the interval). The reference
  implementations apply the same cap; that, too, is from memory.
- **As of `now`.** Attempts stamped after `now` (clock skew, or asking about a past moment) are not
  counted and are reported in `reason`, so a schedule never depends on what had not happened yet.
  Naive timestamps are read as UTC; unreadable ones are skipped and counted.
- **No unassisted evidence, no date.** A skill practised only with help has nothing to retain yet:
  `due`, `interval_days` and `stability` are None and it is never listed by `due()`.
- `MAX_INTERVAL_DAYS` caps the interval (stability itself is not capped).
"""

from __future__ import annotations

import datetime as dt
import math

MODEL = "FSRS-4.5"

# FSRS-4.5 default weights w0..w16 (py-fsrs 3.x and fsrs-rs defaults for FSRS-4.5), fitted by the FSRS
# authors to Anki flashcard review logs. Defaults, not facts about this person.
W = (0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031, 1.6474, 0.1367, 1.0461,
     2.1072, 0.0793, 0.3246, 1.587, 0.2272, 2.8755)
AGAIN, GOOD = 1, 3  # FSRS grades for wrong and correct; the log records nothing that maps to Hard (2) or Easy (4)
DECAY = -0.5  # FSRS-4.5 power forgetting curve exponent
FACTOR = 19 / 81  # = 0.9 ** (1 / DECAY) - 1, so that R(S, S) = 0.9: the definition of stability in FSRS
DESIRED_RETENTION = 0.9  # FSRS's default target recall at the moment of review
D_MIN, D_MAX = 1.0, 10.0  # FSRS difficulty range
S_MIN = 0.01  # days; a numerical floor on stability (CI's choice; FSRS implementations clamp similarly)
MAX_INTERVAL_DAYS = 365.0  # CI's choice: check every skill at least yearly (FSRS implementations default to 36500)


def retrievability(elapsed_days: float, stability: float) -> float:
    """Predicted probability of recall after `elapsed_days` at `stability`, under the default model."""
    return (1 + FACTOR * max(0.0, elapsed_days) / stability) ** DECAY


def interval(stability: float, retention: float = DESIRED_RETENTION) -> float:
    """Days until predicted recall falls to `retention`, capped at MAX_INTERVAL_DAYS."""
    return min(MAX_INTERVAL_DAYS, max(S_MIN, stability / FACTOR * (retention ** (1 / DECAY) - 1)))


def _clamp_d(d: float) -> float:
    return min(D_MAX, max(D_MIN, d))


def _d0(g: int) -> float:
    return _clamp_d(W[4] - (g - 3) * W[5])


def step(state: tuple[float, float] | None, correct: bool, elapsed_days: float) -> tuple[float, float]:
    """One unassisted attempt: (stability, difficulty) before it, or None for the first, to after it."""
    g = GOOD if correct else AGAIN
    if state is None:
        return W[g - 1], _d0(g)
    s, d = state
    r = retrievability(elapsed_days, s)
    if correct:
        s2 = s * (1 + math.exp(W[8]) * (11 - d) * s ** -W[9] * (math.exp(W[10] * (1 - r)) - 1))
    else:
        s2 = min(s, W[11] * d ** -W[12] * ((s + 1) ** W[13] - 1) * math.exp(W[14] * (1 - r)))
    return max(S_MIN, s2), _clamp_d(W[7] * _d0(GOOD) + (1 - W[7]) * (d - W[6] * (g - 3)))


def _when(x) -> dt.datetime | None:
    if isinstance(x, dt.datetime):
        t = x
    elif isinstance(x, str):
        try:
            t = dt.datetime.fromisoformat(x)
        except ValueError:
            return None
    else:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _n(k: int, word: str) -> str:
    return f"{k} {word}{'' if k == 1 else 's'}"


def _span(days: float) -> str:
    if days < 1 / 24:
        return _n(max(1, round(days * 1440)), "minute")
    if days < 1:
        return _n(max(1, round(days * 24)), "hour")
    return f"{days:.1f} days" if days < 10 else f"{days:.0f} days"


def _one(rows: list[tuple[int, dict]], now: dt.datetime, retention: float) -> dict:
    assisted = unreadable = later = 0
    un: list[tuple[dt.datetime, int, bool]] = []
    for i, a in rows:
        if a.get("assisted") is not False:
            assisted += 1
            continue
        t = _when(a.get("ts"))
        if t is None:
            unreadable += 1
        elif t > now:
            later += 1
        else:
            un.append((t, i, bool(a.get("correct"))))
    un.sort(key=lambda x: (x[0], x[1]))  # by time; log order on ties
    notes = []
    if assisted:
        notes.append(f"{_n(assisted, 'assisted attempt')} not counted")
    if later:
        notes.append(f"{_n(later, 'attempt')} stamped after now not counted (clock skew or a later attempt)")
    if unreadable:
        notes.append(f"{_n(unreadable, 'attempt')} with an unreadable timestamp skipped")
    tail = "".join(f"; {x}" for x in notes)
    if not un:
        return {"due": None, "interval_days": None, "stability": None, "difficulty": None, "retrievability": None,
                "elapsed_days": None, "overdue_days": None, "due_now": False, "unassisted": 0, "correct": 0,
                "assisted": assisted, "reason": "no unassisted evidence" + (" before now" if later else "") + tail,
                "_rank": None}
    state, prev = None, None
    for t, _, ok in un:
        state = step(state, ok, 0.0 if prev is None else (t - prev).total_seconds() / 86400)
        prev = t
    s, d = state
    iv = interval(s, retention)
    elapsed = (now - prev).total_seconds() / 86400
    k = sum(ok for _, _, ok in un)
    capped = iv >= MAX_INTERVAL_DAYS
    reason = (f"{_n(len(un), 'unassisted attempt')} ({k} correct), last {'correct' if un[-1][2] else 'wrong'}; "
              f"{MODEL} default weights at retention {retention:g} schedule the next check {_span(iv)} after it"
              + (f" (capped at {MAX_INTERVAL_DAYS:g} days)" if capped else "") + tail)
    return {"due": (prev + dt.timedelta(days=iv)).isoformat(), "interval_days": iv, "stability": s, "difficulty": d,
            "retrievability": retrievability(elapsed, s), "elapsed_days": elapsed, "overdue_days": elapsed - iv,
            "due_now": elapsed >= iv, "unassisted": len(un), "correct": k, "assisted": assisted, "reason": reason,
            "_rank": (-elapsed / iv, -(elapsed - iv))}


def _plan(attempts, now, retention: float) -> dict[str, dict]:
    t = _when(now)
    if t is None:
        raise ValueError("now must be a datetime or an ISO-8601 timestamp")
    if not 0 < retention < 1:
        raise ValueError("retention must be strictly between 0 and 1")
    by_skill: dict[str, list[tuple[int, dict]]] = {}
    for i, a in enumerate(attempts or ()):
        if isinstance(a, dict) and isinstance(a.get("skill"), str):
            by_skill.setdefault(a["skill"], []).append((i, a))
    return {s: _one(rows, t, retention) for s, rows in sorted(by_skill.items())}


_ROUND = ("interval_days", "stability", "difficulty", "retrievability", "elapsed_days", "overdue_days")


def schedule(attempts: list[dict], now, retention: float = DESIRED_RETENTION) -> dict[str, dict]:
    """Per skill in the log: when the next unassisted check is due, and why.

    Returns {skill: {"due": iso | None, "interval_days": float | None, "stability": float | None,
    "difficulty", "retrievability" (predicted recall at `now` under the default model, not a
    measurement), "elapsed_days" (since the last counted attempt), "overdue_days" (negative when not
    yet due), "due_now": bool, "unassisted", "correct", "assisted" (counts), "reason": str}}.
    Deterministic: the same attempts and `now` give the same result, whatever the order of attempts
    with distinct timestamps.
    """
    out = {}
    for s, e in _plan(attempts, now, retention).items():
        e = {k: v for k, v in e.items() if k != "_rank"}
        for k in _ROUND:
            if e[k] is not None:
                e[k] = round(e[k], 4)
        out[s] = e
    return out


def due(attempts: list[dict], now, retention: float = DESIRED_RETENTION) -> list[str]:
    """Skills whose next unassisted check is due at `now`, most overdue first.

    "How overdue" is relative: time since the last counted attempt over the interval, so a skill on a
    1-day interval left for 3 days comes before one on a 60-day interval left for 70. Ties go to the
    larger absolute overdue, then the skill name.
    """
    plan = _plan(attempts, now, retention)
    return sorted((s for s, e in plan.items() if e["due_now"]), key=lambda s: (plan[s]["_rank"], s))


def review_evidence(entry: dict) -> str:
    """The evidence string for a `due_for_review` flag: the gap, the interval it passed, and the counts.

    Carries no skill name, so it is safe in the twin's redacted (counts-only) findings.
    """
    if entry.get("elapsed_days") is None:
        return entry.get("reason", "no unassisted evidence")
    return (f"last unassisted attempt {_span(entry['elapsed_days'])} ago; the review schedule asked for a check "
            f"after {_span(entry['interval_days'])} ({_n(entry['unassisted'], 'unassisted attempt')}, "
            f"{entry['correct']} correct)")
