"""ALTER checks the files a goal wrote for the person's identifiers (V1 criterion C10). A live goal once
wrote the owner's name into a test as the test user. Hits are reported by label and file, never by value."""

import json

import pytest
from test_alter_goals import RUN, env, runner, start  # noqa: F401  (fixture)

from pcim import goals as G
from pcim import identity_guard as ig

MARKS = {"account full name": "Ada Lovelace", "username ($USER)": "adalovelace", "home path": "/Users/adalovelace",
         "account email": "ada.l@analytical-engine.org"}


@pytest.fixture
def marks(tmp_path, monkeypatch):
    f = tmp_path / "marks.json"
    f.write_text(json.dumps(MARKS))
    monkeypatch.setenv("PCIM_IDENTITY_MARKERS", str(f))
    return ig.markers()


def test_markers_add_the_fragments_a_hand_written_identifier_spells_out(marks):
    assert marks["email domain"] == "analytical-engine.org"
    assert "lovelace" in marks.values()  # the name token (4+ letters); "ada" is too short to be one
    assert "username fragment" not in marks  # what is left of $USER ("ada") is under 4 letters: too short to scan for
    assert {"account full name", "home path", "account email", "username ($USER)"} <= set(marks)


def test_scan_reports_labels_and_files_never_values(marks, tmp_path):
    (tmp_path / "t.py").write_text('USER = "Lovelace"\nHOME = "/Users/adalovelace/x"\n')
    (tmp_path / "clean.py").write_text("x = 1\n")
    hits = ig.scan(tmp_path, ["t.py", "clean.py", "../outside.py", "gone.py"])
    assert [h["file"] for h in hits] == ["t.py"]
    assert "home path" in hits[0]["labels"] and any(lb.startswith("name token") for lb in hits[0]["labels"])
    assert "lovelace" not in json.dumps(hits).lower()


def test_a_goal_that_writes_an_identifier_says_so_and_still_records_its_outcome(env, marks):
    ci, ws, p, repo, here = env
    leak = {"text": "Adding a test.", "tool_uses": [{"id": "w1", "name": "write_file", "input": {
        "path": "calc.py", "content": "def add(a, b):\n    return a + b\nOWNER = 'Ada Lovelace'\n", "reason": "fix"}}]}
    r, _ = runner(ci, here, [leak, RUN(), {"text": "Done; tests pass."}])
    g = r.run(start(r).id)
    assert g.status == "done" and "WARNING" in g.payload["reason"] and "personal identifiers" in g.payload["reason"]
    assert g.payload["identifiers"][0]["file"] == "calc.py"
    rep = G.report(ci, "u", g)
    assert "WARNING: calc.py holds your" in rep and "lovelace" not in rep.lower()
    assert "lovelace" not in json.dumps(g.payload["identifiers"]).lower()


def test_a_clean_goal_has_no_warning(env, marks):
    ci, ws, p, repo, here = env
    from test_alter_goals import W
    r, _ = runner(ci, here, [W(), RUN(), {"text": "Done."}])
    g = r.run(start(r).id)
    assert g.status == "done" and g.payload["identifiers"] == [] and "WARNING" not in g.payload["reason"]
