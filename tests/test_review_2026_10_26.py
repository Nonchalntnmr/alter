"""Fixes from the independent review of 2026-10-26. Each test is one finding, numbered as in
the review."""

import pytest

from pcim.adapters.mock_agent import MockAgentAdapter
from pcim.types import Trust
from pcim.workspace import render_briefing

from test_exchange_and_providers import make

HEALTH = "Diagnosed with type 2 diabetes; HbA1c 7.9%"


def restricted(ci, content=HEALTH, **kw):
    """A memory item from a source the person allowed to reach mock only, never anthropic."""
    if not any(g.source == "ingest:health" for g in ci.ledger.grants("u")):
        ci.consent("u", "ingest:health", "store")
        ci.consent("u", "ingest:health", "egress", provider="mock")
    return ci.memory.remember("u", content, "fact", source="ingest:health", trust=Trust.IMPORTED, **kw).item


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "a.txt").write_text("alpha\n")
    return r


# 1 ---------------------------------------------------------------- superseded lineage
def test_content_derived_from_a_superseded_item_still_needs_the_vendors_consent(tmp_path):
    ci, ws, ledger, _, agent, _ = make(tmp_path)
    p = ws.create_project("u", "Diet plan", providers=["mock", "anthropic"])
    m1 = restricted(ci, key="hba1c", value=7.9)
    agent.set_adapter("mock", None, MockAgentAdapter(script=[
        {"tool_uses": [{"id": "t1", "name": "record_note", "input": {"kind": "decision", "text": "Plan around the type 2 diabetes"}},
                       {"id": "t2", "name": "record_claim", "input": {"statement": "The person has type 2 diabetes",
                                                                       "evidence": "", "uncertainty": ""}}]},
        {"text": "done"}]))
    agent.send("u", p.id, "plan meals around my health", provider="mock")
    note, claim = ws.get_project("u", p.id).notes[0], ledger.list("u", p.id)[0]
    restricted(ci, "HbA1c 7.1%", key="hba1c", value=7.1)  # supersedes m1; anthropic was never granted the source
    assert not ci.store.get_item(m1.id).is_current()
    b = agent.briefing("u", p.id, vendor="anthropic")
    assert "diabetes" not in render_briefing(b)
    assert {note["id"], claim["id"]} <= {w["id"] for w in b["withheld"]}
    assert ws.derived_blocked("u", [m1.id], "mock") is None  # the vendor that was allowed still is


# 2 ---------------------------------------------------------------- write_file temp path
def test_write_file_cannot_be_redirected_through_a_planted_temp_name(tmp_path, repo):
    _, ws, _, q, _, _ = make(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "authorized_keys"
    victim.write_text("legit\n")
    (repo / "run.sh").write_text("echo hi\n")
    (repo / "run.sh").chmod(0o755)
    (repo / ".run.sh.ci-tmp").symlink_to(victim)  # committed to the repo by whoever wrote it
    p = ws.create_project("u", "Repo", providers=["mock"])
    ws.authorize_workspace("u", p.id, str(repo), allow_write=True)
    t = q.approve("u", q.propose("u", p.id, "write_file", {"path": "run.sh", "content": "attacker\n", "reason": "r"}).id)
    assert t.status == "succeeded" and victim.read_text() == "legit\n"
    assert (repo / "run.sh").read_text() == "attacker\n" and not (repo / "run.sh").is_symlink()
    assert (repo / "run.sh").stat().st_mode & 0o777 == 0o755  # the file keeps its permissions
    assert sorted(x.name for x in repo.iterdir()) == [".run.sh.ci-tmp", "a.txt", "run.sh"]  # no temp file left
