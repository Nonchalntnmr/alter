"""The spaced-review scheduler (`pcim.spacing`): properties on synthetic attempt histories.

Every history here is made up. The tests check how the schedule responds to evidence (success
lengthens, failure shortens, assisted attempts change nothing, ordering, robustness), and pin the
first steps to FSRS-4.5's published default weights. They say nothing about any real person.
"""

import datetime as dt
import math
import random

import pytest

from pcim import spacing as sp

T0 = dt.datetime(2026, 1, 1, 9, 0, tzinfo=dt.timezone.utc)


def day(d: float) -> dt.datetime:
    return T0 + dt.timedelta(days=d)


def att(d: float, correct: bool, skill: str = "s", assisted: bool = False) -> dict:
    return {"ts": day(d).isoformat(), "skill": skill, "correct": correct, "assisted": assisted, "source": "test",
            "interaction": None}


def entry(history: list[dict], now_day: float, skill: str = "s") -> dict:
    return sp.schedule(history, day(now_day))[skill]


def last_day(history: list[dict]) -> float:
    return max((dt.datetime.fromisoformat(a["ts"]) - T0).total_seconds() / 86400 for a in history)


def histories(n: int = 40, seed: int = 0):
    """Deterministic random unassisted histories: (history, day of last attempt)."""
    rng = random.Random(seed)
    for _ in range(n):
        d, h = 0.0, []
        for _ in range(rng.randint(1, 8)):
            h.append(att(d, rng.random() < 0.7))
            d += rng.choice([0.0, 0.1, 0.5, 1, 2, 5, 13, 40])
        yield h, last_day(h)


# ---------------------------------------------------------------- the model, pinned to its published defaults
def test_first_attempts_and_the_curve_match_fsrs_4_5_defaults():
    ok, wrong = entry([att(0, True)], 0), entry([att(0, False)], 0)
    assert ok["stability"] == pytest.approx(3.7145) and ok["difficulty"] == pytest.approx(5.1618)  # w2, w4
    assert wrong["stability"] == pytest.approx(0.4872)  # w0
    assert wrong["difficulty"] == pytest.approx(5.1618 + 2 * 1.2298)  # w4 - (1 - 3) * w5
    for s in (0.3, 3.7, 50.0):
        assert sp.retrievability(s, s) == pytest.approx(0.9)  # stability: the time recall falls to 90%
        assert sp.interval(s) == pytest.approx(s)  # at the default retention the interval is the stability
    assert ok["interval_days"] == pytest.approx(3.7145) and ok["due"] == (T0 + dt.timedelta(days=3.7145)).isoformat()
    # one success on the due date, worked by hand from the published formula
    s, d, r = 3.7145, 5.1618, 0.9
    expect = s * (1 + math.exp(1.6474) * (11 - d) * s ** -0.1367 * (math.exp(1.0461 * (1 - r)) - 1))
    assert entry([att(0, True), att(3.7145, True)], 4)["stability"] == pytest.approx(expect, abs=1e-3)
    assert "FSRS-4.5" in ok["reason"] and "1 unassisted attempt (1 correct)" in ok["reason"]


# ---------------------------------------------------------------- the four properties
def test_success_lengthens_the_interval():
    for h, last in histories():
        before = entry(h, last)["interval_days"]
        for gap in (0.01, 0.5, 1, 3, 10, 60):
            after = entry(h + [att(last + gap, True)], last + gap)["interval_days"]
            assert after > before or after == sp.MAX_INTERVAL_DAYS, (h, gap)


def test_an_immediate_repeat_success_adds_nothing():
    # Zero elapsed time means predicted recall 1: massed repetition does not build stability in this model.
    one = entry([att(0, True)], 0)
    three = entry([att(0, True), att(0, True), att(0, True)], 0)
    assert three["stability"] == one["stability"] and three["interval_days"] == one["interval_days"]
    assert three["unassisted"] == 3 and three["correct"] == 3


def test_failure_shortens_the_interval_and_never_lengthens_it():
    built = [att(0, True), att(4, True), att(18, True)]
    before = entry(built, 18)["interval_days"]
    for gap in (0, 0.5, 5, 30, 200):
        assert entry(built + [att(18 + gap, False)], 18 + gap)["interval_days"] < before
    # A long gap raises post-lapse stability in the raw formula; the cap keeps a failure from lengthening.
    for h, last in histories():
        before = entry(h, last)["interval_days"]
        for gap in (0, 0.5, 5, 30, 200):
            assert entry(h + [att(last + gap, False)], last + gap)["interval_days"] <= before, (h, gap)
    tiny = [att(0, False)]
    assert entry(tiny + [att(30, False)], 30)["stability"] <= entry(tiny, 0)["stability"]


def test_assisted_attempts_never_change_the_schedule():
    rng = random.Random(1)
    keys = ("due", "interval_days", "stability", "difficulty", "retrievability", "overdue_days", "due_now",
            "unassisted", "correct")
    for h, last in histories(seed=2):
        now = last + 3
        base = entry(h, now)
        noisy = list(h)
        for _ in range(6):  # assisted successes and failures, before, between, at and after unassisted ones
            noisy.insert(rng.randint(0, len(noisy)), att(rng.choice([0, last, last + 1, now + 5]),
                                                         rng.random() < 0.5, assisted=True))
        noisy.append({"ts": day(last + 1).isoformat(), "skill": "s", "correct": False})  # no flag: not counted
        got = entry(noisy, now)
        assert {k: got[k] for k in keys} == {k: base[k] for k in keys}
        assert got["assisted"] == 7 and "7 assisted attempts not counted" in got["reason"]
    assert sp.due([att(0, True, assisted=True), att(9, False, assisted=True)], day(30)) == []


def test_stability_is_monotone_in_consecutive_successes():
    h, d, seen = [], 0.0, []
    for _ in range(8):  # each check on its due date
        h.append(att(d, True))
        e = entry(h, d)
        seen.append(e["stability"])
        d += e["interval_days"]
    assert all(b > a for a, b in zip(seen, seen[1:])), seen
    rng = random.Random(3)
    for _ in range(30):  # after any start, any positive gaps
        h = [att(0, rng.random() < 0.5)]
        d, prev = 0.0, entry(h, 0)["stability"]
        for _ in range(6):
            d += rng.choice([0.01, 0.3, 1, 4, 20, 90])
            h.append(att(d, True))
            cur = entry(h, d)["stability"]
            assert cur > prev, h
            prev = cur


# ---------------------------------------------------------------- what is due, and in what order
def test_due_lists_only_due_skills_most_overdue_first():
    log = [att(0, True, "a"),  # interval 3.7 days, 10 days since: 2.7 intervals
           att(8, False, "b"),  # interval 0.49 days, 2 days since: 4.1 intervals
           att(9, True, "c"),  # interval 3.7 days, 1 day since: not due
           att(6.2, True, "d"),  # interval 3.71 days, 3.8 since: just due
           att(1, True, "e", assisted=True)]  # no unassisted evidence: never due
    now = day(10)
    plan = sp.schedule(log, now)
    assert sp.due(log, now) == ["b", "a", "d"]
    assert plan["c"]["due_now"] is False and plan["c"]["overdue_days"] < 0
    assert all(plan[s]["due_now"] and plan[s]["overdue_days"] >= 0 for s in ("a", "b", "d"))
    assert plan["e"]["due"] is None and plan["e"]["stability"] is None
    # relative, not absolute: "a" is 6.3 days late on a 3.7-day interval, "b" only 1.5 days late on 0.49 days
    assert plan["a"]["overdue_days"] > plan["b"]["overdue_days"]
    # predicted recall orders the same way as relative overdueness while no interval is capped
    assert plan["b"]["retrievability"] < plan["a"]["retrievability"] < plan["d"]["retrievability"]
    twins = [att(0, True, "y"), att(0, True, "x")]
    assert sp.due(twins, now) == ["x", "y"]  # identical histories: by name


def test_a_skill_becomes_due_exactly_when_its_interval_has_passed():
    h = [att(0, True)]
    iv = entry(h, 0)["interval_days"]
    assert sp.due(h, day(iv - 0.001)) == []
    assert sp.due(h, day(iv)) == ["s"]
    assert entry(h, iv)["due"] == day(iv).isoformat()


# ---------------------------------------------------------------- robustness
def test_empty_and_assisted_only_histories():
    assert sp.schedule([], T0) == {} and sp.due([], T0) == []
    assert sp.schedule(None, T0) == {}
    e = entry([att(0, True, assisted=True), att(1, False, assisted=True), att(2, True, assisted=True)], 3)
    assert (e["due"], e["interval_days"], e["stability"], e["due_now"]) == (None, None, None, False)
    assert e["reason"] == "no unassisted evidence; 3 assisted attempts not counted"
    assert (e["unassisted"], e["assisted"]) == (0, 3)
    assert sp.review_evidence(e) == e["reason"]


def test_same_timestamp_attempts_are_deterministic_and_keep_log_order():
    h = [att(0, True), att(0, False), att(0, True)]
    assert sp.schedule(h, day(1)) == sp.schedule(list(h), day(1))
    a, b = entry([att(0, True), att(0, False)], 1), entry([att(0, False), att(0, True)], 1)
    assert "last wrong" in a["reason"]
    assert "last correct" in b["reason"] and a["stability"] != b["stability"]


def test_order_of_attempts_with_distinct_timestamps_does_not_matter():
    rng = random.Random(4)
    for h, last in histories(seed=5):
        h = [x for i, x in enumerate(h) if i == 0 or x["ts"] != h[i - 1]["ts"]]
        shuffled = list(h)
        rng.shuffle(shuffled)
        assert sp.schedule(shuffled, day(last + 2)) == sp.schedule(h, day(last + 2))


def test_attempts_stamped_after_now_are_not_counted():
    past = [att(0, True), att(4, True)]
    skewed = past + [att(20, False), att(21, True, assisted=True)]
    got, base = entry(skewed, 10), entry(past, 10)
    assert {k: got[k] for k in ("due", "stability", "interval_days")} == \
           {k: base[k] for k in ("due", "stability", "interval_days")}
    assert "1 attempt stamped after now not counted (clock skew or a later attempt)" in got["reason"]
    only_future = entry([att(5, True)], 1)
    assert only_future["due"] is None and only_future["reason"].startswith("no unassisted evidence before now")
    # appending later attempts never rewrites the schedule as of an earlier moment
    assert entry(past, 10)["due"] == entry(past + [att(11, False)], 10)["due"]
    assert entry(skewed, 21)["stability"] < base["stability"]  # once now passes it, the failure counts


def test_naive_unreadable_and_string_timestamps():
    naive = [{"ts": "2026-01-01T09:00:00", "skill": "s", "correct": True, "assisted": False}]
    assert entry(naive, 1)["due"] == entry([att(0, True)], 1)["due"]  # naive is read as UTC
    assert sp.schedule(naive, "2026-01-02T09:00:00") == sp.schedule(naive, day(1))
    bad = [att(0, True), {"ts": "yesterday", "skill": "s", "correct": False, "assisted": False},
           {"ts": None, "skill": "s", "correct": False, "assisted": False}, {"skill": None}, "junk"]
    e = entry(bad, 1)
    assert e["unassisted"] == 1 and "2 attempts with an unreadable timestamp skipped" in e["reason"]
    with pytest.raises(ValueError):
        sp.schedule([], "not a time")
    with pytest.raises(ValueError):
        sp.schedule([], T0, retention=1.0)


def test_retention_and_the_interval_cap():
    h = [att(0, True), att(4, True)]
    assert entry(h, 4)["interval_days"] > sp.schedule(h, day(4), retention=0.95)["s"]["interval_days"]
    h, d = [], 0.0
    for _ in range(7):
        h.append(att(d, True))
        d += entry(h, d)["interval_days"]
    e = entry(h, d)
    assert e["interval_days"] == sp.MAX_INTERVAL_DAYS and e["stability"] > sp.MAX_INTERVAL_DAYS
    assert "capped at 365 days" in e["reason"]


def test_review_evidence_carries_counts_and_no_skill_name():
    log = [att(0, True, "bayes rule"), att(0, False, "bayes rule"), att(0, False, "bayes rule")]
    e = sp.schedule(log, day(8))["bayes rule"]
    text = sp.review_evidence(e)
    assert e["due_now"] and "bayes" not in text
    assert text.startswith("last unassisted attempt 8.0 days ago; the review schedule asked for a check after ")
    assert text.endswith("(3 unassisted attempts, 1 correct)")
