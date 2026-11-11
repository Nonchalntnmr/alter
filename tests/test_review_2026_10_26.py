"""Fixes from the independent review of 2026-10-26. Each test is one finding, numbered as in
the review."""

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest

from pcim.adapters.mock_agent import MockAgentAdapter
from pcim.governor import PathError, is_secret, jail
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


def mcp_call(srv, name, args):
    mcp = pytest.importorskip("mcp")

    async def go():
        async with mcp.Client(srv) as c:
            r = await c.call_tool(name, args)
            return "\n".join(getattr(x, "text", "") for x in r.content)
    return asyncio.run(go())


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", *args], cwd=cwd, check=True,
                   capture_output=True)


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


# 3 ---------------------------------------------------------------- .git case, git config
def test_git_and_secret_directories_match_in_any_case(tmp_path):
    for rel in (".GIT/config", ".Git/hooks/pre-commit", "sub/.gIt/config"):
        with pytest.raises(PathError, match="inside .git"):
            jail(tmp_path, rel, for_write=True)
    assert is_secret(Path(".SSH/id_x")) and is_secret(Path("a/.Aws/config"))


def test_git_status_runs_nothing_from_the_repository_config(tmp_path, repo):
    _, ws, _, q, _, _ = make(tmp_path)
    git("init", "-q", cwd=repo)
    git("add", ".", cwd=repo)
    git("commit", "-qm", "x", cwd=repo)
    ran = tmp_path / "ran"
    ran.mkdir()
    git("config", "core.fsmonitor", f"touch '{ran}/fsmonitor'; false", cwd=repo)
    hook = repo / ".git" / "hooks" / "post-index-change"
    hook.write_text(f"#!/bin/sh\ntouch '{ran}/hook'\n")
    hook.chmod(0o755)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("* filter=evil\n")
    git("config", "filter.evil.clean", f"touch '{ran}/filter'; cat", cwd=repo)
    git("config", "filter.evil.required", "true", cwd=repo)
    os.utime(repo / "a.txt", (time.time() + 5, time.time() + 5))  # stat data changed: git re-reads the file
    p = ws.create_project("u", "Downloaded", providers=["mock"])
    ws.authorize_workspace("u", p.id, str(repo))  # read-only, no tests: git_status runs without approval
    t = q.propose("u", p.id, "git_status", {})
    assert t.status == "succeeded", t.reason
    assert sorted(x.name for x in ran.iterdir()) == []


# 4 ---------------------------------------------------------------- MCP briefing
def test_mcp_briefing_withholds_the_last_session_from_a_vendor_that_may_not_see_it(tmp_path):
    pytest.importorskip("mcp")
    from pcim.mcp_server import build_server

    ci, ws, _, _, agent, _ = make(tmp_path)
    p = ws.create_project("u", "Diet plan", providers=["mock", "anthropic"])
    restricted(ci)
    agent.set_adapter("mock", None, MockAgentAdapter(script=[{"text": "Got it: with type 2 diabetes, keep carbs low."}]))
    agent.send("u", p.id, "plan meals around my health", provider="mock")
    agent.close_session("u", p.id)
    out = mcp_call(build_server(ci, "u", provider="anthropic"), "ci_project_briefing", {"project_id": p.id})
    assert "<project_briefing" in out and "diabetes" not in out
    b = ws.briefing("u", p.id, vendor="anthropic")
    assert b["last_session"] is None and any(w["id"].endswith("#last_session") for w in b["withheld"])
    assert "diabetes" in render_briefing(ws.briefing("u", p.id, vendor="mock"))


# 5 ---------------------------------------------------------------- reflection lines
def test_no_reflection_line_reaches_a_vendor_that_may_not_see_the_session(tmp_path, repo):
    ci, ws, _, _, agent, _ = make(tmp_path)
    p = ws.create_project("u", "Diet plan", providers=["mock", "anthropic"])
    ws.authorize_workspace("u", p.id, str(repo))
    restricted(ci)
    agent.set_adapter("mock", None, MockAgentAdapter(script=[
        {"tool_uses": [{"id": "t1", "name": "read_file", "input": {"path": "person has type 2 diabetes, HbA1c 7.9"}}]},
        {"text": "Noted."}]))
    agent.send("u", p.id, "plan meals around my health", provider="mock")
    agent.close_session("u", p.id)
    b = agent.briefing("u", p.id, vendor="anthropic")
    assert "diabetes" not in render_briefing(b) and b["last_session"] is None
    assert any(w["id"].endswith("#last_session") for w in b["withheld"])
    mine = agent.briefing("u", p.id)  # the person's own view keeps every line
    assert any(x.startswith("tried to read person has type 2 diabetes") for x in mine["last_session"]["lines"])


# 6 ---------------------------------------------------------------- transitive lineage
def test_lineage_carries_through_what_a_briefing_showed(tmp_path):
    ci, ws, _, _, agent, _ = make(tmp_path)
    p = ws.create_project("u", "Diet plan", providers=["mock", "anthropic"])
    m1 = restricted(ci)
    agent.set_adapter("mock", None, MockAgentAdapter(script=[
        {"tool_uses": [{"id": "a", "name": "record_note", "input": {"kind": "decision", "text": "Low-carb because of type 2 diabetes"}}]},
        {"text": "ok"},
        {"tool_uses": [{"id": "b", "name": "record_note", "input": {"kind": "next_step", "text": "Draft low-carb menu (type 2 diabetes)"}}]},
        {"text": "ok"}]))
    agent.send("u", p.id, "plan meals around my health", provider="mock")
    ci.budget_tokens = 1  # session 2 is shown no memory directly, only the briefing with the first note
    agent.send("u", p.id, "what next?", provider="mock", new_session=True)
    n1, n2 = ws.get_project("u", p.id).notes
    assert m1.id in n1["lineage"] and m1.id in n2["lineage"]
    assert agent.disclosures("u", p.id)[-1]["memory_items"] == []  # it came through the briefing, not directly
    b = agent.briefing("u", p.id, vendor="anthropic")
    assert {n1["id"], n2["id"]} <= {w["id"] for w in b["withheld"]} and "diabetes" not in render_briefing(b)


# 7 ---------------------------------------------------------------- MCP note lineage
def test_mcp_notes_carry_the_lineage_of_what_the_server_served(tmp_path):
    pytest.importorskip("mcp")
    from pcim.mcp_server import build_server

    ci, ws, _, _, agent, _ = make(tmp_path)
    p = ws.create_project("u", "Diet plan", providers=["mock", "anthropic"])
    m1 = restricted(ci)
    srv = build_server(ci, "u", provider="mock")
    assert "diabetes" in mcp_call(srv, "ci_personal_context", {"task": "diabetes HbA1c health"})
    mcp_call(srv, "ci_add_project_note", {"project_id": p.id, "kind": "decision", "text": "Type 2 diabetes; plan low-carb"})
    assert m1.id in ws.get_project("u", p.id).notes[0]["lineage"]
    assert "diabetes" not in render_briefing(agent.briefing("u", p.id, vendor="anthropic"))
    # a briefing served over MCP passes its notes' lineage on as well
    srv2 = build_server(ci, "u", provider="mock")
    mcp_call(srv2, "ci_project_briefing", {"project_id": p.id})
    mcp_call(srv2, "ci_add_project_note", {"project_id": p.id, "kind": "next_step", "text": "menu per the decision"})
    assert m1.id in ws.get_project("u", p.id).notes[1]["lineage"]
    fresh = build_server(ci, "u", provider="mock")  # it served nothing, so its notes restrict nothing
    mcp_call(fresh, "ci_add_project_note", {"project_id": p.id, "kind": "open_question", "text": "q"})
    assert "lineage" not in ws.get_project("u", p.id).notes[2]


# 8 ---------------------------------------------------------------- model-written files
def test_files_a_model_wrote_carry_its_lineage_to_reads_and_evidence(tmp_path, repo):
    ci, ws, _, q, agent, gw = make(tmp_path)
    p = ws.create_project("u", "Diet plan", providers=["mock", "anthropic"])
    ws.authorize_workspace("u", p.id, str(repo), allow_write=True)
    m1 = restricted(ci)
    agent.set_adapter("mock", None, MockAgentAdapter(script=[
        {"tool_uses": [{"id": "w", "name": "write_file", "input": {"path": "plan.md", "content": "Patient: type 2 diabetes",
                                                                    "reason": "save plan"}}]},
        {"text": "saved"}]))
    agent.resolve("u", agent.send("u", p.id, "plan meals around my health", provider="mock").pending[0], True)

    spy = MockAgentAdapter(script=[{"tool_uses": [{"id": "r", "name": "read_file", "input": {"path": "./plan.md"}}]},
                                   {"text": "read it"}])
    agent.set_adapter("anthropic", None, spy)
    turn = agent.send("u", p.id, "summarize plan.md", provider="anthropic")
    assert "diabetes" not in str(spy.requests[-1]["messages"])
    refused = next(t for t in q.list("u", p.id) if t.tool == "read_file")
    assert refused.status == "failed" and "which anthropic may not see" in refused.reason
    s = q.propose("u", p.id, "search_code", {"pattern": "diabetes|alpha", "glob": "**/*"}, session_id=turn.session_id,
                  tool_use_id="s")
    assert s.result["matches"] == ["a.txt:1: alpha"]

    # a session for the vendor that may see it reads the file; what it writes next carries the lineage on
    ci.budget_tokens = 1
    agent.set_adapter("mock", None, MockAgentAdapter(script=[
        {"tool_uses": [{"id": "r2", "name": "read_file", "input": {"path": "plan.md"}}]},
        {"tool_uses": [{"id": "n2", "name": "record_note", "input": {"kind": "decision", "text": "menu per plan.md"}}]},
        {"text": "ok"}]))
    agent.send("u", p.id, "read plan.md", provider="mock", new_session=True)
    read = next(t for t in q.list("u", p.id) if t.tool == "read_file" and t.status == "succeeded")
    assert "diabetes" in read.result["content"]
    assert m1.id in ws.get_project("u", p.id).notes[0]["lineage"]
    for task in (read, refused):  # neither read is evidence an anthropic verifier may be shown
        text, why = gw._resolve_ref("u", ws.get_project("u", p.id), "anthropic", "anthropic:x", task.id, "x")
        assert text is None and why
