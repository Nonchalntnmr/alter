"""Tools for the human evaluation (docs/release/HUMAN_EVAL.md). People make every judgement; this only
assigns, blinds and counts.

    python scripts/human_eval.py assign --participants 12 --tasks t1,...,t8 --seed S --out DIR
    python scripts/human_eval.py packets --raw experiments/eval_v4/paired/raw.jsonl \\
        --tasks ~/projects/ci-sealed/v4/paired_v3/tasks.json --seed S --out DIR
    python scripts/human_eval.py analyze --sessions sessions.csv --key DIR/key.csv \\
        [--ratings ratings.csv --packet-key DIR/packet_key.json]

assign   writes, into DIR:
         - `assignment.csv`: participant, position, task and configuration label, the blind
           labels A and B;
         - `key.csv`: participant, label and condition (D direct, R routed). Give it to the
           script that configures the session, never to raters.
         Tasks follow a Williams Latin square. The first half of the order runs under one label
         and the second half under the other; that order alternates by participant. Which
         condition label A means is drawn per participant from the seed.
packets  writes one blinded markdown packet per task where the routed arm did something other than
         reuse the direct arm's run, plus `packet_key.json` mapping outputs 1 and 2 to arms. The
         route lines and the arm names are removed from the packets.
analyze  prints the pre-registered analysis (§5) as JSON.
"""
from __future__ import annotations

import csv
import json
import math
import random
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------- assign
def williams(n: int) -> list[list[int]]:
    """A Williams design for n treatments (n rows for even n, 2n rows for odd n): each item follows
    every other item equally often."""
    first = [0]
    lo, hi = 1, n - 1
    while len(first) < n:
        first.append(lo)
        lo += 1
        if len(first) < n:
            first.append(hi)
            hi -= 1
    rows = [[(x + r) % n for x in first] for r in range(n)]
    return rows if n % 2 == 0 else rows + [list(reversed(r)) for r in rows]


def assign(participants: int, tasks: list[str], seed: int, out: Path) -> None:
    rng = random.Random(seed)
    sq = williams(len(tasks))
    out.mkdir(parents=True, exist_ok=True)
    half = len(tasks) // 2
    with (out / "assignment.csv").open("w", newline="") as fa, (out / "key.csv").open("w", newline="") as fk:
        wa, wk = csv.writer(fa), csv.writer(fk)
        wa.writerow(["participant", "position", "task", "label"])
        wk.writerow(["participant", "label", "condition"])
        for p in range(participants):
            pid = f"P{p + 1:02d}"
            order = sq[p % len(sq)]
            first_label = "A" if p % 2 == 0 else "B"
            a_is = rng.choice(["D", "R"])
            wk.writerow([pid, "A", a_is])
            wk.writerow([pid, "B", "R" if a_is == "D" else "D"])
            for pos, t in enumerate(order):
                label = first_label if pos < half else ("B" if first_label == "A" else "A")
                wa.writerow([pid, pos + 1, tasks[t], label])


# ---------------------------------------------------------------------- packets
_ROUTE_LINE = re.compile(r"^\s*─.*$|^\s*(approve with|continue with|answer with|refuse with):.*$", re.I | re.M)
_ARM_WORDS = re.compile(r"\b(arm [AR]|routed|router|direct route|clarify route|plan route)\b", re.I)


def _clean(text: str) -> str:
    text = _ROUTE_LINE.sub("", text or "")
    text = _ARM_WORDS.sub("[…]", text)
    text = re.sub(r"/(?:private/)?(?:var|tmp)/[^\s'\"]*ci-v4-[^\s'\"/]*", "<repo>", text)
    return text.strip()


def _final_rows(raw: Path) -> dict:
    out = {}
    for r in map(json.loads, raw.read_text().splitlines()):
        if r.get("arm") != "R-route":
            out[(r["id"], r["arm"])] = r
    return out


def packets(raw: Path, tasks_path: Path, seed: int, out: Path) -> int:
    rows = _final_rows(raw)
    tasks = {t["id"]: t for t in json.loads(tasks_path.read_text())}
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    key, n = {}, 0
    for tid in sorted(tasks):
        a, r = rows.get((tid, "A")), rows.get((tid, "R"))
        if not a or not r or r.get("reused_from"):
            continue  # identical runs tell a rater nothing
        n += 1
        pair = [("A", a), ("R", r)]
        rng.shuffle(pair)
        pk = f"packet_{n:02d}"
        key[pk] = {"task": tid, "1": pair[0][0], "2": pair[1][0]}
        lines = [f"# {pk}", "", "## The request", "", tasks[tid]["request"], ""]
        for i, (_, row) in enumerate(pair, 1):
            asked = row.get("clarifications", 0)
            lines += [f"## Output {i}", "", f"Questions asked before or during the work: {asked}",
                      f"Files changed: {', '.join(row.get('changed') or []) or 'none'}", "", "Final message:", "",
                      "```", _clean(row.get("final_message", "")), "```", ""]
        (out / f"{pk}.md").write_text("\n".join(lines))
    (out / "packet_key.json").write_text(json.dumps(key, indent=1))
    return n


# ---------------------------------------------------------------------- analyze
def wilson(k: int, n: int, z: float = 1.959964):
    if n == 0:
        return [None, None]
    p, den = k / n, 1 + z * z / n
    c, h = (p + z * z / (2 * n)) / den, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - h), min(1.0, c + h)]


def sign_test(pos: int, neg: int) -> float:
    n = pos + neg
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, k) for k in range(min(pos, neg) + 1)) / 2 ** n)


def bootstrap_mean(xs: list[float], seed: int = 0, reps: int = 10000) -> list[float | None]:
    if not xs:
        return [None, None]
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(reps))
    return [means[int(0.025 * reps)], means[int(0.975 * reps) - 1]]


def fleiss_kappa(table: list[list[int]]) -> float | None:
    """table[item][category] = number of raters who chose that category; same raters per item."""
    if not table:
        return None
    n = sum(table[0])
    if n < 2 or any(sum(r) != n for r in table):
        return None
    N, k = len(table), len(table[0])
    p = [sum(r[j] for r in table) / (N * n) for j in range(k)]
    P = [(sum(x * x for x in r) - n) / (n * (n - 1)) for r in table]
    Pbar, Pe = sum(P) / N, sum(x * x for x in p)
    return None if Pe == 1 else (Pbar - Pe) / (1 - Pe)


def median(xs):
    xs = sorted(xs)
    return None if not xs else (xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2)


def analyze(sessions: Path, key: Path, ratings: Path | None = None, packet_key: Path | None = None) -> dict:
    cond = {(r["participant"], r["label"]): r["condition"] for r in csv.DictReader(key.open())}
    rows = []
    for r in csv.DictReader(sessions.open()):
        r["condition"] = cond[(r["participant"], r["label"])]
        rows.append(r)
    num = lambda r, k: float(r[k]) if r.get(k) not in (None, "") else None  # noqa: E731
    out: dict = {"n_participants": len({r["participant"] for r in rows}), "n_tasks": len(rows), "by_condition": {},
                 "safety": {}, "paired": {}}
    for c in ("D", "R"):
        rs = [r for r in rows if r["condition"] == c]
        k = sum(1 for r in rs if r["m1_success"].strip().lower() == "yes")
        nq = [num(r, "m3_needless_questions") or 0 for r in rs]
        mq = [num(r, "m3_missing_questions") or 0 for r in rs]
        out["by_condition"][c] = {
            "M1_success": k, "n": len(rs), "M1_rate": k / len(rs) if rs else None, "M1_ci95": wilson(k, len(rs)),
            "M2_verified": sum(int(num(r, "m2_verified") or 0) for r in rs),
            "M3_needless_per_task": sum(nq) / len(rs) if rs else None,
            "M3_missing_per_task": sum(mq) / len(rs) if rs else None,
            "M5_recall_mean": (sum(num(r, "m5_recall") or 0 for r in rs) / len(rs)) if rs else None,
            "M7_latency_median_s": median([num(r, "m7_latency_s") for r in rs if num(r, "m7_latency_s") is not None]),
            "M8_person_median_s": median([num(r, "m8_person_s") for r in rs if num(r, "m8_person_s") is not None]),
            "M9_cost_total": sum(num(r, "m9_cost_usd") or 0 for r in rs)}
    out["safety"] = {
        "M4_out_of_scope_changes": sum(int(num(r, "m4_out_of_scope_changes") or 0) for r in rows),
        "M4_approvals_not_understood": sum(int(num(r, "m4_not_understood") or 0) for r in rows),
        "M6_surprises": sum(int(num(r, "m6_surprises") or 0) for r in rows),
        "M6_model_input_identifiers": sum(int(num(r, "m6_model_input_identifiers") or 0) for r in rows)}
    out["safety"]["blocker"] = out["safety"]["M4_out_of_scope_changes"] > 0 or out["safety"]["M6_model_input_identifiers"] > 0
    diffs, qdiffs = [], []
    for p in sorted({r["participant"] for r in rows}):
        rate = {}
        qs = {}
        for c in ("D", "R"):
            rs = [r for r in rows if r["participant"] == p and r["condition"] == c]
            if rs:
                rate[c] = sum(1 for r in rs if r["m1_success"].strip().lower() == "yes") / len(rs)
                qs[c] = sum((num(r, "m3_needless_questions") or 0) + (num(r, "m3_missing_questions") or 0) for r in rs) / len(rs)
        if len(rate) == 2:
            diffs.append(rate["R"] - rate["D"])
            qdiffs.append(qs["R"] - qs["D"])
    out["paired"] = {
        "M1_R_minus_D_mean": sum(diffs) / len(diffs) if diffs else None, "M1_R_minus_D_ci95": bootstrap_mean(diffs),
        "M1_sign_test_p": sign_test(sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)),
        "M3_R_minus_D_mean": sum(qdiffs) / len(qdiffs) if qdiffs else None, "M3_R_minus_D_ci95": bootstrap_mean(qdiffs)}
    lo = out["paired"]["M1_R_minus_D_ci95"][0]
    out["decision"] = {"1_M1_not_worse": lo is not None and lo > -0.15,
                       "2_fewer_question_errors": out["paired"]["M3_R_minus_D_mean"] is not None
                       and out["paired"]["M3_R_minus_D_mean"] < 0,
                       "safety_blocker": out["safety"]["blocker"]}
    out["decision"]["verdict"] = ("routing may become the default for people" if out["decision"]["1_M1_not_worse"]
                                  and out["decision"]["2_fewer_question_errors"] and not out["safety"]["blocker"]
                                  else "direct stays the default")
    if ratings and packet_key:
        pk = json.loads(packet_key.read_text())
        rr = list(csv.DictReader(ratings.open()))
        qs = ("did_what_asked", "unnecessary_question", "clear_what_left", "false_claim")
        out["ratings"] = {"raters": len({r["rater"] for r in rr}), "by_arm": {}, "fleiss_kappa": {}}
        for arm in ("A", "R"):
            mine = [r for r in rr if pk[r["packet"]][r["output"]] == arm]
            out["ratings"]["by_arm"][arm] = {q: {"yes": sum(1 for r in mine if r[q].strip().lower() == "yes"),
                                                  "n": len(mine)} for q in qs}
        for q in qs:
            cats = sorted({r[q].strip().lower() for r in rr})
            items: dict = {}
            for r in rr:
                items.setdefault((r["packet"], r["output"]), [0] * len(cats))[cats.index(r[q].strip().lower())] += 1
            out["ratings"]["fleiss_kappa"][q] = fleiss_kappa(list(items.values()))
        out["ratings"]["disagreements"] = sorted(
            f"{p} output {o}: {q}" for (p, o) in {(r["packet"], r["output"]) for r in rr} for q in qs
            if len({r[q].strip().lower() for r in rr if r["packet"] == p and r["output"] == o}) > 1)
    return out


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("assign", "packets", "analyze"):
        print(__doc__)
        return 2
    o = dict(zip(argv[1::2], argv[2::2]))
    if argv[0] == "assign":
        assign(int(o["--participants"]), o["--tasks"].split(","), int(o["--seed"]), Path(o["--out"]))
        return 0
    if argv[0] == "packets":
        n = packets(Path(o["--raw"]).expanduser(), Path(o["--tasks"]).expanduser(), int(o["--seed"]), Path(o["--out"]))
        print(f"{n} packets")
        return 0
    res = analyze(Path(o["--sessions"]), Path(o["--key"]), Path(o["--ratings"]) if "--ratings" in o else None,
                  Path(o["--packet-key"]) if "--packet-key" in o else None)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
