"""scripts/eval_v8_pool.py's mechanical checks, on a toy task written here (not from any pool). No model is
called."""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eval_v8_pool as P  # noqa: E402

TOY = {
    "category": "choice", "needs_question": True, "theme": "toy adder",
    "goal": "I want the adder to combine two numbers the way I expect.",
    "facts": [{"id": "f1", "text": "combine means add", "essential": True, "discoverable_in_repo": False, "volunteered": False},
              {"id": "f2", "text": "I wrote it last year", "essential": False, "discoverable_in_repo": False, "volunteered": True}],
    "outside_actions": [], "allowed_write_paths": ["calc.py", "tests/test_calc.py"],
    "files": {"calc.py": "def combine(a, b):\n    return a - b\n", "pytest.ini": "[pytest]\npythonpath = .\n",
              "tests/test_calc.py": "from calc import combine\n\n\ndef test_zero():\n    assert combine(0, 0) == 0\n"},
    "hidden_test": "from calc import combine\n\n\ndef test_add():\n    assert combine(2, 3) == 5\n",
    "route": "clarify", "route_also_ok": [], "route_why": "x", "why": "toy",
    "reference": {"calc.py": "def combine(a, b):\n    return a + b\n"},
    "other_reading": {"calc.py": "def combine(a, b):\n    return a * b\n"},
}


def test_a_valid_task_passes_every_check():
    c = P.safe_check(TOY)
    assert not P.failed(c), P.failed(c)
    assert {"orig_visible_pass", "orig_hidden_fail", "ref_visible_pass", "ref_hidden_pass", "other_visible_pass",
            "other_hidden_fail"} <= set(c)


def test_each_broken_task_fails_the_matching_check():
    t = copy.deepcopy(TOY)
    t["files"]["calc.py"] = "def combine(a, b):\n    return a + b\n"  # the hidden check already passes
    assert "orig_hidden_fail" in P.failed(P.safe_check(t))
    t = copy.deepcopy(TOY)
    t["other_reading"] = {"calc.py": "def combine(a, b):\n    return b + a\n"}  # the other reading is the answer
    assert "other_hidden_fail" in P.failed(P.safe_check(t))
    t = copy.deepcopy(TOY)
    t["facts"][0]["volunteered"] = True  # nothing tacit, yet needs_question
    assert "consistency" in P.failed(P.safe_check(t))
    t = copy.deepcopy(TOY)
    t["reference"] = {"other.py": "x = 1\n"}  # outside allowed_write_paths
    assert "reference_scope" in P.failed(P.safe_check(t))
    t = copy.deepcopy(TOY)
    t["files"]["../escape.py"] = "x = 1\n"
    assert "paths" in P.failed(P.safe_check(t))
    t = copy.deepcopy(TOY)
    t["files"]["calc.py"] = 7  # not a string
    assert P.failed(P.safe_check(t))
