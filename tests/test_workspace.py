"""The workspace slice: governor, executor, durable queue, autonomy, agent loop, resume."""

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from pcim.agent import Agent
from pcim.adapters.mock_agent import MockAgentAdapter, TAG
from pcim.engine import PersonalCI
from pcim.governor import Governor, PathError, jail
from pcim.tasks import TaskQueue, TaskStateError
from pcim.workspace import Workspace, WorkspaceError, validate_root

PY = sys.executable


def make(tmp_path, data="data"):
    ci = PersonalCI(tmp_path / data, seed=0)
    if not ci.store.list_user_ids():
        ci.onboard("u", "U", providers=("mock", "anthropic"))
    ws = Workspace(ci)
    q = TaskQueue(ws, Governor(ws))
    return ci, ws, q, Agent(ci, ws, q)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "src").mkdir()
    (r / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (r / ".env").write_text("API_KEY=supersecret\n")
    (r / "notes.txt").write_text("hello\n")
    return r


@pytest.fixture
def env(tmp_path, repo):
    ci, ws, q, agent = make(tmp_path)
    p = ws.create_project("u", "Alpha research", objective="find a robust signal")
    ws.authorize_workspace("u", p.id, str(repo), run_argv=[PY, "-c", "print('3 passed')"], allow_write=True)
    return ci, ws, q, agent, p, repo


# ---------------------------------------------------------------- roots and paths
def test_roots_that_are_too_broad_are_refused(tmp_path):
    for bad in ("/", os.path.expanduser("~"), os.path.dirname(os.path.expanduser("~"))):
        with pytest.raises(WorkspaceError):
            validate_root(bad)
    assert validate_root(tmp_path) == tmp_path.resolve()


def test_jail_blocks_traversal_absolute_symlink_secrets_and_git_writes(repo, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    (repo / "link").symlink_to(outside)
    for bad in ("../outside.txt", str(outside), "link", ".env", "src/../../outside.txt"):
        with pytest.raises(PathError):
            jail(repo, bad)
    with pytest.raises(PathError):
        jail(repo, ".git/config", for_write=True)
    assert jail(repo, "src/app.py") == (repo / "src" / "app.py").resolve()


# ---------------------------------------------------------------- governor and modes
def test_governor_rule_table(env):
    ci, ws, q, agent, p, repo = env
    gov = q.gov
    assert gov.decide("u", p.id, "read_file", {"path": "notes.txt"}).action == "execute"
    assert gov.decide("u", p.id, "run_tests", {"reason": "x"}).action == "approve"
    assert gov.decide("u", p.id, "write_file", {"path": "a.txt", "content": "", "reason": "x"}).action == "approve"
    assert gov.decide("u", p.id, "read_file", {"path": ".env"}).action == "deny"
    assert gov.decide("u", p.id, "send_email", {}).action == "deny"
    ws.set_mode("u", "copilot")
    assert gov.decide("u", p.id, "run_tests", {"reason": "x"}).action == "approve"
    ws.set_mode("u", "autonomous")  # without a grant, autonomous mode still asks
    assert gov.decide("u", p.id, "run_tests", {"reason": "x"}).action == "approve"


def test_no_workspace_means_no_file_tools(tmp_path):
    ci, ws, q, _ = make(tmp_path)
    p = ws.create_project("u", "No workspace")
    t = q.propose("u", p.id, "read_file", {"path": "x"})
    assert t.status == "denied" and "no workspace" in t.reason
    t2 = q.propose("u", p.id, "record_note", {"kind": "decision", "text": "notes need no workspace"})
    assert t2.status == "succeeded"


def test_bad_arguments_are_denied_not_executed(env):
    _, _, q, _, p, _ = env
    assert q.propose("u", p.id, "read_file", {"path": "x", "extra": "y"}).status == "denied"
    assert q.propose("u", p.id, "read_file", {}).status == "denied"


# ---------------------------------------------------------------- executor
def test_read_list_search_and_secrets_never_listed(env):
    _, _, q, _, p, _ = env
    r = q.propose("u", p.id, "read_file", {"path": "src/app.py"})
    assert r.status == "succeeded" and "return a + b" in r.result["content"]
    files = q.propose("u", p.id, "list_files", {"pattern": "**/*"}).result["files"]
    assert "src/app.py" in files and ".env" not in files
    m = q.propose("u", p.id, "search_code", {"pattern": "API_KEY|return", "glob": "**/*"}).result["matches"]
    assert any("app.py:2" in x for x in m) and not any("supersecret" in x for x in m)


def test_write_needs_approval_then_undo_restores(env):
    _, _, q, _, p, repo = env
    t = q.propose("u", p.id, "write_file", {"path": "notes.txt", "content": "changed\n", "reason": "test"})
    assert t.status == "awaiting_approval" and (repo / "notes.txt").read_text() == "hello\n"
    t = q.approve("u", t.id)
    assert t.status == "succeeded" and (repo / "notes.txt").read_text() == "changed\n"
    assert "backup" not in t.public()
    q.undo("u", t.id)
    assert (repo / "notes.txt").read_text() == "hello\n"
    with pytest.raises(TaskStateError):
        q.undo("u", t.id)


def test_run_tests_scrubs_secrets_from_the_environment(env, monkeypatch):
    _, ws, q, _, p, repo = env
    monkeypatch.setenv("FAKE_API_KEY", "leak-me")
    monkeypatch.setenv("HARMLESS_SETTING", "ok")
    ws.authorize_workspace("u", p.id, str(repo), allow_write=True,
                           run_argv=[PY, "-c", "import os;print(os.environ.get('FAKE_API_KEY'), os.environ.get('HARMLESS_SETTING'))"])
    t = q.approve("u", q.propose("u", p.id, "run_tests", {"reason": "r"}).id)
    assert t.result["stdout_tail"].strip() == "None ok"


def test_timeout_and_cancel_kill_the_process(env):
    _, ws, q, _, p, repo = env
    ws.authorize_workspace("u", p.id, str(repo), run_argv=[PY, "-c", "import time; time.sleep(30)"], run_timeout_s=1)
    t0 = time.time()
    t = q.approve("u", q.propose("u", p.id, "run_tests", {"reason": "r"}).id)
    assert t.result["timed_out"] and time.time() - t0 < 10
    ws.authorize_workspace("u", p.id, str(repo), run_argv=[PY, "-c", "import time; time.sleep(30)"], run_timeout_s=60)
    pending = q.propose("u", p.id, "run_tests", {"reason": "r"})
    th = threading.Thread(target=q.approve, args=("u", pending.id))
    th.start()
    for _ in range(50):
        if q.get("u", pending.id).status == "running":
            break
        time.sleep(0.05)
    assert q.recover()["interrupted"] == 0 and q.get("u", pending.id).status == "running"
    q.cancel("u", pending.id)
    th.join(timeout=10)
    assert not th.is_alive() and q.get("u", pending.id).status == "cancelled"


def test_legacy_test_record_waits_for_the_old_process_to_exit(env, tmp_path):
    _, _, q, _, p, _ = env
    old = q.propose("u", p.id, "run_tests", {"reason": "from an earlier install"})
    q.store.execute("UPDATE tasks SET status='running' WHERE id=?", (old.id,))
    run_dir = tmp_path / "data" / "run"
    run_dir.mkdir()
    with subprocess.Popen([PY, "-c", "import time; time.sleep(20)"], start_new_session=True) as old_process:
        try:
            (run_dir / f"{old.id}.json").write_text(json.dumps({"pgid": old_process.pid}))
            assert q.recover_project_tests("u", p.id) is None
            other = q.propose("u", p.id, "run_tests", {"reason": "verify work"})
            blocked = q.approve("u", other.id)
            assert blocked.status == "failed" and "older test command" in blocked.reason
            assert old_process.poll() is None and q.get("u", old.id).status == "running"
        finally:
            old_process.terminate()
    assert q.recover_project_tests("u", p.id) == 1
    assert q.get("u", old.id).status == "interrupted"


def test_git_status_refuses_the_home_directory_repository(tmp_path, monkeypatch):
    home = tmp_path / "fakehome"
    proj = home / "Downloads" / "proj"
    proj.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(home)], check=True)
    monkeypatch.setenv("HOME", str(home))
    ci, ws, q, _ = make(tmp_path)
    p = ws.create_project("u", "Inside home repo")
    ws.authorize_workspace("u", p.id, str(proj))
    t = q.propose("u", p.id, "git_status", {})
    assert t.status == "failed" and "home-directory repository" in t.reason


# ---------------------------------------------------------------- durability, expiry, halt, autonomy
def test_approval_expires_and_restart_never_reruns(env, tmp_path):
    ci, ws, q, agent, p, repo = env
    stale = TaskQueue(ws, q.gov, approval_ttl_s=0)
    t = stale.propose("u", p.id, "run_tests", {"reason": "r"})
    time.sleep(0.01)
    assert stale.approve("u", t.id).status == "expired"
    t2 = q.propose("u", p.id, "run_tests", {"reason": "r"})
    ci.store.execute("UPDATE tasks SET status='running' WHERE id=?", (t2.id,))  # simulate a crash mid-run
    assert q.recover()["interrupted"] == 1
    assert q.get("u", t2.id).status == "interrupted"


def test_halt_cancels_and_blocks_until_resume(env):
    _, ws, q, _, p, _ = env
    t = q.propose("u", p.id, "run_tests", {"reason": "r"})
    out = q.halt("u")
    assert out["cancelled"] == 1 and q.get("u", t.id).status == "cancelled"
    assert q.propose("u", p.id, "read_file", {"path": "notes.txt"}).status == "denied"
    ws.set_halted("u", False)
    assert q.propose("u", p.id, "read_file", {"path": "notes.txt"}).status == "succeeded"


def test_autonomous_grant_limits_revocation_and_expiry(env):
    _, ws, q, _, p, _ = env
    ws.set_mode("u", "autonomous")
    aid = ws.grant_autonomy("u", p.id, ["run_tests"], max_actions=2, max_seconds=600, ttl_minutes=30)
    assert q.propose("u", p.id, "run_tests", {"reason": "1"}).status == "succeeded"
    assert q.propose("u", p.id, "run_tests", {"reason": "2"}).status == "succeeded"
    assert q.propose("u", p.id, "run_tests", {"reason": "3"}).status == "awaiting_approval"  # limit reached
    assert q.propose("u", p.id, "write_file", {"path": "x", "content": "", "reason": "r"}).status == "awaiting_approval"
    aid2 = ws.grant_autonomy("u", p.id, ["run_tests"], max_actions=5)
    ws.revoke("u", aid2)
    assert q.propose("u", p.id, "run_tests", {"reason": "4"}).status == "awaiting_approval"
    aid3 = ws.grant_autonomy("u", p.id, ["run_tests"], max_actions=5)
    ws.store.execute("UPDATE authorizations SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (aid3,))
    assert q.propose("u", p.id, "run_tests", {"reason": "5"}).status == "awaiting_approval"
    with pytest.raises(WorkspaceError):
        ws.grant_autonomy("u", p.id, ["delete_everything"])


def test_revocation_after_approval_blocks_at_execution(env):
    _, ws, q, _, p, repo = env
    t = q.propose("u", p.id, "write_file", {"path": "notes.txt", "content": "x", "reason": "r"})
    for a in ws.active_auths("u", p.id, "workspace"):
        ws.revoke("u", a["id"])
    t = q.approve("u", t.id)
    assert t.status == "denied" and "at execution" in t.reason and (repo / "notes.txt").read_text() == "hello\n"


# ---------------------------------------------------------------- agent loop
def test_agent_reads_automatically_and_pauses_for_tests(env):
    ci, ws, q, agent, p, repo = env
    agent.set_adapter("mock", None, MockAgentAdapter(script=[
        {"text": "Let me look first.", "tool_uses": [{"id": "tu1", "name": "read_file", "input": {"path": "src/app.py"}}]},
        {"text": "Now the tests.", "tool_uses": [{"id": "tu2", "name": "run_tests", "input": {"reason": "check add()"}}]},
        {"text": "Tests pass: 3 passed."},
    ]))
    turn = agent.send("u", p.id, "is add() correct?")
    assert turn.status == "awaiting_approval" and len(turn.pending) == 1
    statuses = {t["tool"]: t["status"] for t in turn.tasks}
    assert statuses == {"read_file": "succeeded", "run_tests": "awaiting_approval"}
    with pytest.raises(RuntimeError):
        agent.send("u", p.id, "hello?")  # cannot talk past a pending approval
    turn2 = agent.resolve("u", turn.pending[0], approve=True)
    assert turn2.status == "done" and turn2.text == "Tests pass: 3 passed."
    msgs = agent.adapter("mock", None).requests[-1]["messages"]
    tr = msgs[-1]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "tu2" and "3 passed" in tr["content"]


def test_denial_goes_back_to_the_model_as_an_error(env):
    _, _, q, agent, p, repo = env
    agent.set_adapter("mock", None, MockAgentAdapter(script=[
        {"tool_uses": [{"id": "tw", "name": "write_file", "input": {"path": "notes.txt", "content": "x", "reason": "r"}}]},
        {"text": "Understood, I won't change it."},
    ]))
    turn = agent.send("u", p.id, "rewrite notes")
    turn2 = agent.resolve("u", turn.pending[0], approve=False, reason="not now")
    assert turn2.status == "done" and (repo / "notes.txt").read_text() == "hello\n"
    tr = agent.adapter("mock", None).requests[-1]["messages"][-1]["content"][0]
    assert tr["is_error"] and "denied: not now" in tr["content"]


def test_demo_A_resume_across_processes(tmp_path, repo):
    ci, ws, q, agent, = make(tmp_path)
    p = ws.create_project("u", "Crowding paper", objective="decide whether rho(J) is worth reviving")
    ws.authorize_workspace("u", p.id, str(repo), run_argv=[PY, "-c", "print('ok')"])
    ws.add_note("u", p.id, "open_question", "Does the moving-average baseline still win at weekly horizons?")
    agent.send("u", p.id, "note decision: drop the rho(J) lead-time programme")
    agent.send("u", p.id, "run tests")  # pauses for approval
    agent.close_session("u", p.id)
    del ci, ws, q, agent  # "close CI"

    ci2, ws2, q2, agent2 = make(tmp_path)  # "reopen CI": a new process on the same store
    b = ws2.briefing("u", p.id)
    assert b["open_questions"][0]["text"].startswith("Does the moving-average")
    assert any("drop the rho(J)" in d["text"] and d["source"] == "model" for d in b["decisions"])
    assert len(b["pending_approvals"]) == 1
    # the test run was only proposed: it must not read as done
    assert "proposed running the tests — awaiting approval" in b["last_session"]["lines"]
    assert not any(line.startswith("ran the tests") for line in b["last_session"]["lines"])
    turn = agent2.send("u", p.id, "where were we?")
    assert TAG in turn.text and "moving-average baseline" in turn.text and "[model-written by mock-agent" in turn.text


def test_demo_E_collaboration_preference_persists(tmp_path, repo):
    ci, ws, q, agent = make(tmp_path)
    p = ws.create_project("u", "Prefs")
    agent.set_collaboration_preference("u", "explanations", "skip the basics; assume graduate-level statistics")
    agent.send("u", p.id, "hi", new_session=True)
    ci2, ws2, q2, agent2 = make(tmp_path)
    agent2.send("u", p.id, "hi again", new_session=True)
    system = agent2.adapter("mock", None).requests[-1]["system"]
    assert "assume graduate-level statistics" in system


def test_anthropic_tool_loop_against_recorded_wire_format(env):
    anthropic = pytest.importorskip("anthropic")
    import httpx2
    import json
    from pcim.adapters.anthropic import AnthropicAdapter

    ci, ws, q, agent, p, repo = env
    seen = []
    replies = [
        {"content": [{"type": "text", "text": "Reading."},
                     {"type": "tool_use", "id": "toolu_01", "name": "read_file", "input": {"path": "src/app.py"}}],
         "stop_reason": "tool_use"},
        {"content": [{"type": "text", "text": "add() returns a + b, which is correct."}], "stop_reason": "end_turn"},
    ]

    def handler(req):
        seen.append(json.loads(req.content))
        r = replies[len(seen) - 1]
        return httpx2.Response(200, json={"id": f"msg_{len(seen)}", "type": "message", "role": "assistant",
                                          "model": "claude-opus-5-5", "content": r["content"],
                                          "stop_reason": r["stop_reason"], "stop_sequence": None,
                                          "usage": {"input_tokens": 900, "output_tokens": 40}})

    client = anthropic.Anthropic(api_key="t", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    agent.set_adapter("anthropic", None, AnthropicAdapter("claude-opus-5-5", client=client))
    turn = agent.send("u", p.id, "check add()", provider="anthropic")
    assert turn.status == "done" and "correct" in turn.text and turn.cost_usd > 0
    first, second = seen
    assert {t["name"] for t in first["tools"]} >= {"read_file", "run_tests"} and all(t["strict"] for t in first["tools"])
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "<project_briefing" in first["messages"][0]["content"]
    assert second["messages"][1]["content"][1]["type"] == "tool_use"  # assistant content appended back unchanged
    assert second["messages"][2]["content"][0]["tool_use_id"] == "toolu_01"
