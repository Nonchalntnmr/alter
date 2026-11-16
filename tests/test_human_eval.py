"""scripts/human_eval.py: counterbalancing, blinding and the pre-registered analysis, on synthetic data."""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import human_eval as he  # noqa: E402


def test_williams_square_balances_first_order_carryover():
    for n in (4, 8, 5):
        rows = he.williams(n)
        assert all(sorted(r) == list(range(n)) for r in rows)
        follows = Counter((r[i], r[i + 1]) for r in rows for i in range(n - 1))
        assert len(set(follows.values())) == 1  # every ordered pair equally often


def test_assignment_is_counterbalanced_and_the_key_is_separate(tmp_path):
    he.assign(12, [f"t{i}" for i in range(1, 9)], seed=7, out=tmp_path)
    a = list(csv.DictReader((tmp_path / "assignment.csv").open()))
    k = list(csv.DictReader((tmp_path / "key.csv").open()))
    assert "condition" not in a[0]
    assert len(a) == 96 and all(sum(1 for r in a if r["participant"] == p) == 8 for p in {r["participant"] for r in a})
    for p in {r["participant"] for r in a}:
        assert Counter(r["label"] for r in a if r["participant"] == p) == {"A": 4, "B": 4}
        assert {r["condition"] for r in k if r["participant"] == p} == {"D", "R"}
    firsts = Counter(next(r["label"] for r in a if r["participant"] == p and r["position"] == "1") for p in
                     {r["participant"] for r in a})
    assert firsts["A"] == firsts["B"] == 6


def test_packets_are_blind_and_skip_reused_runs(tmp_path):
    raw = tmp_path / "raw.jsonl"
    rows = [{"id": "r01", "arm": "A", "final_message": "Fixed it.\n─ direct: clear", "changed": ["a.py"], "clarifications": 0},
            {"id": "r01", "arm": "R-route", "route": "clarify"},
            {"id": "r01", "arm": "R", "final_message": "Done after your answer. The router asked.", "changed": ["a.py"],
             "clarifications": 1},
            {"id": "r02", "arm": "A", "final_message": "x", "changed": []},
            {"id": "r02", "arm": "R", "reused_from": "A"}]
    raw.write_text("\n".join(json.dumps(r) for r in rows))
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{"id": "r01", "request": "fix a"}, {"id": "r02", "request": "fix b"}]))
    assert he.packets(raw, tasks, seed=1, out=tmp_path / "p") == 1
    text = (tmp_path / "p" / "packet_01.md").read_text()
    assert "─" not in text and "router" not in text.lower() and "arm" not in text.lower()
    key = json.loads((tmp_path / "p" / "packet_key.json").read_text())
    assert key["packet_01"]["task"] == "r01" and {key["packet_01"]["1"], key["packet_01"]["2"]} == {"A", "R"}


def test_analysis_counts_and_decides_as_registered(tmp_path):
    key = tmp_path / "key.csv"
    key.write_text("participant,label,condition\nP01,A,D\nP01,B,R\nP02,A,R\nP02,B,D\n")
    s = tmp_path / "s.csv"
    head = open(Path(__file__).resolve().parents[1] / "experiments" / "human_eval" / "session_template.csv").read()
    lines = [head.strip()]
    for p, lab, ok, needless in (("P01", "A", "yes", 1), ("P01", "B", "yes", 0), ("P02", "A", "yes", 0), ("P02", "B", "no", 1)):
        lines.append(f"{p},t1,{lab},{ok},1,{needless},0,0,0,1,0,0,10,60,0.1,30,5")
    s.write_text("\n".join(lines) + "\n")
    res = he.analyze(s, key)
    assert res["by_condition"]["D"]["M1_success"] == 1 and res["by_condition"]["R"]["M1_success"] == 2
    assert res["paired"]["M1_R_minus_D_mean"] == 0.5
    assert res["safety"]["blocker"] is False
    assert res["decision"]["2_fewer_question_errors"] is True


def test_fleiss_kappa_matches_a_known_value():
    # perfect agreement on two categories, spread across items
    assert he.fleiss_kappa([[3, 0], [0, 3], [3, 0]]) == 1.0
    assert he.fleiss_kappa([[2, 1], [1, 2]]) < 0.1
