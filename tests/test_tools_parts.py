"""Large files, found by ALTER's live run: a 60 KB file reached the model cut at 20,000 characters while the
result said "truncated": false, so the agent rightly refused to rewrite it. Reads now come in labelled
parts, writes can replace one exact passage, and a cut result says it was cut."""

import json
import sys

import pytest

from pcim.agent import RESULT_CHARS, Agent
from pcim.engine import PersonalCI
from pcim.governor import Governor
from pcim.tasks import TaskQueue
from pcim.tools import READ_CHARS, Executor, ToolError, read_part, validate_args
from pcim.workspace import Workspace

BIG = "".join(f"line {i:05d}: " + "x" * 60 + "\n" for i in range(1, 3001))  # ~200 KB


def test_a_long_file_is_read_in_labelled_parts_that_add_up_to_the_whole():
    data = BIG.encode()
    parts, offset = [], 1
    while True:
        r = read_part("big.py", data, offset)
        assert len(r["content"]) <= READ_CHARS and r["lines_total"] == 3000
        assert r["truncated"] is True  # no part of this file is the whole file
        parts.append(r["content"])
        if "next_offset" not in r:
            assert r["to_line"] == 3000
            break
        assert int(r["next_offset"]) == r["to_line"] + 1
        offset = int(r["next_offset"])
    assert "".join(parts) == BIG
    small = read_part("s.py", b"a\nb\n")
    assert small["truncated"] is False and small["content"] == "a\nb\n" and "next_offset" not in small
    r = read_part("big.py", data, 100, 3)
    assert r["from_line"] == 100 and r["to_line"] == 102 and r["content"].startswith("line 00100")


def test_read_arguments_are_checked():
    validate_args("read_file", {"path": "a", "offset": "5", "limit": "10"})
    for bad in ({"path": "a", "offset": "0"}, {"path": "a", "limit": "x"}):
        with pytest.raises(ToolError):
            validate_args("read_file", bad)


def test_write_file_replaces_one_exact_passage_and_nothing_else(tmp_path):
    f = tmp_path / "big.py"
    f.write_text(BIG)
    ex = Executor()
    spec = {"root": str(tmp_path)}
    r = ex.run("t1", "write_file", validate_args("write_file", {"path": "big.py", "old": "line 01500: ",
                                                                "new": "line 01500 (edited): ", "reason": "r"}), spec)
    assert f.read_text() == BIG.replace("line 01500: ", "line 01500 (edited): ") and r["backup"] == BIG
    with pytest.raises(ToolError, match="appears 0 times"):
        ex.run("t2", "write_file", {"path": "big.py", "old": "nowhere", "new": "x", "reason": "r"}, spec)
    with pytest.raises(ToolError, match="appears 3000 times"):
        ex.run("t3", "write_file", {"path": "big.py", "old": "x" * 60, "new": "y", "reason": "r"}, spec)
    with pytest.raises(ToolError, match="does not exist"):
        ex.run("t4", "write_file", {"path": "new.py", "old": "a", "new": "b", "reason": "r"}, spec)
    for bad in ({"path": "a", "reason": "r"}, {"path": "a", "content": "c", "old": "o", "new": "n", "reason": "r"},
                {"path": "a", "old": "o", "reason": "r"}, {"path": "a", "old": "", "new": "n", "reason": "r"}):
        with pytest.raises(ToolError):
            validate_args("write_file", bad)


def test_a_passage_edit_is_governed_and_undone_like_any_write(tmp_path):
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", "U", providers=("mock",))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("A = 1\nB = 2\n")
    ws = Workspace(ci)
    p = ws.create_project("u", "M")
    ws.authorize_workspace("u", p.id, str(repo), run_argv=[sys.executable, "-c", "pass"], allow_write=True)
    q = TaskQueue(ws, Governor(ws))
    t = q.propose("u", p.id, "write_file", {"path": "m.py", "old": "B = 2", "new": "B = 3", "reason": "r"})
    assert t.status == "awaiting_approval"  # a passage edit is a write: it asks first
    t = q.approve("u", t.id)
    assert (repo / "m.py").read_text() == "A = 1\nB = 3\n"
    q.undo("u", t.id)
    assert (repo / "m.py").read_text() == "A = 1\nB = 2\n"
    out = q.propose("u", p.id, "write_file", {"path": "../out.py", "old": "a", "new": "b", "reason": "r"})
    assert out.status == "denied" and "outside" in out.reason  # the governor's jail applies to both forms


def test_a_result_too_long_for_the_model_says_where_it_was_cut(tmp_path):
    class T:
        status, reason, tool_use_id, id = "succeeded", None, "tu1", "task_x"
        result = {"content": "z" * (RESULT_CHARS * 2)}
    block = Agent._result_block(None, T())
    assert len(block["content"]) <= RESULT_CHARS + 200 and "this result was cut" in block["content"]
    T.result = {"content": "short"}
    assert json.loads(Agent._result_block(None, T())["content"])["content"] == "short"
