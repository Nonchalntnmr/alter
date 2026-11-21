"""`alter` end to end, offline: identity, `alter init`, Third Eye context from a real git repository and a
fixture vault, a goal run through the real `claude` CLI adapter (a stand-in binary plays the model),
approvals, memory, the conversation, and `ci` compatibility. No model is called."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pcim import alter, cli
from pcim import goals as G
from pcim import thirdeye
from pcim import vault as V

PY = sys.executable
FAKE = r'''#!{py}
import json, sys
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("0.0.0-fake"); sys.exit(0)
prompt = sys.stdin.read()
def reply(obj):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": text, "total_cost_usd": 0.001,
                      "usage": {{"input_tokens": 100, "output_tokens": 20}}, "modelUsage": {{"claude-fake": {{"costUSD": 0.001}}}}}}))
    sys.exit(0)
if "<ci_tools>" in prompt:
    body = prompt.split("</ci_tools>", 1)[1]
    if "<alter_context" in body and "Goal:" not in body:
        reply({{"text": "no goal", "tool_calls": []}})
    wrote, tested = '"write_file"' in body, '"run_tests"' in body
    if wrote and tested:
        reply({{"text": "Intended outcome: add() returns the sum [git:status]. Changed calc.py; the tests pass.", "tool_calls": []}})
    if wrote:
        reply({{"text": "Testing.", "tool_calls": [{{"name": "run_tests", "input": {{"reason": "verify"}}}}]}})
    reply({{"text": "Fixing.", "tool_calls": [{{"name": "write_file", "input": {{"path": "calc.py",
            "content": "def add(a, b):\n    return a + b\n", "reason": "fix"}}}}]}})
reply("From the records: the last commit added calc.py [sourced: git:status]. [recommendation] Fix add().")
'''


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})


@pytest.fixture
def home(tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text(FAKE.format(py=PY))
    fake.chmod(0o755)
    monkeypatch.setenv("PCIM_CLAUDE_BIN", str(fake))
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PCIM_EMBEDDER", "hashing-512")
    monkeypatch.delenv("PCIM_CLI_REQUIRE_ISOLATION", raising=False)
    monkeypatch.setenv("PYTHONPATH", str(Path(alter.__file__).resolve().parents[1]))  # for background workers
    assert cli.main(["init", "--user", "u", "--providers", "claude-cli"]) == 0
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "README.md").write_text("# Calc\nA tiny calculator.\n")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "calc: first version")
    vault = tmp_path / "vault"
    (vault / "01 Projects").mkdir(parents=True)
    (vault / "01 Projects" / "Calc.md").write_text("---\ntype: project\nstatus: active\n---\n# Calc\n## Goal\n"
                                                   "Calc must add numbers correctly before the demo. See [[Demo]].\n")
    (vault / "Demo.md").write_text("# Demo\nThe demo shows the calculator adding two numbers on stage.\n")
    monkeypatch.chdir(repo)
    return tmp_path, repo, vault


def check_cmd():
    return f"{PY} -c \"import calc; assert calc.add(2, 3) == 5; print('1 passed')\""


def test_the_full_loop_through_the_alter_command(home, capsys):
    tmp, repo, vault = home
    assert alter.main(["init", "--tests", check_cmd()]) == 0
    assert "Project proj" in capsys.readouterr().out
    assert alter.main(["permissions", "vault", "on", "--path", str(vault)]) == 0
    assert alter.main(["context", "calc demo"]) == 0
    ctx = capsys.readouterr().out
    assert "[git:status]" in ctx and "calc: first version" in ctx and "[Calc#Goal]" in ctx and "github: off" in ctx
    assert alter.main(["permissions", "autonomy", "grant", "--actions", "10", "--minutes", "10"]) == 0
    rc = alter.main(["fix", "add()", "so", "it", "returns", "the", "sum"])
    out = capsys.readouterr().out
    assert rc == 0 and "verified: the project's tests passed after the last change" in out
    assert (repo / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert alter.main(["goals"]) == 0 and "done" in capsys.readouterr().out
    assert alter.main(["memory"]) == 0
    mem = capsys.readouterr().out
    assert "episode" in mem and "Ended done" in mem and G.MEMORY_SOURCE in mem
    # a later goal remembers this one: ALTER's own records are part of the next brief
    assert alter.main(["context"]) == 0 and "[goal:goal_" in capsys.readouterr().out


def test_approvals_through_alter_and_compatibility_with_ci(home, capsys):
    tmp, repo, vault = home
    alter.main(["init", "--tests", check_cmd()])
    capsys.readouterr()
    assert alter.main(["goals", "start", "fix add()"]) == 6  # waits for the write's approval
    assert "waiting for you" in capsys.readouterr().out
    assert alter.main(["status"]) == 0 and "waiting for you" in capsys.readouterr().out
    assert alter.main(["approve"]) == 6  # then the test run's
    assert alter.main(["approve"]) == 0
    out = capsys.readouterr().out
    assert "verified" in out
    assert alter.main(["do", "list"]) == 0  # every ci command still works through alter
    assert alter.main(["--version"]) == 0 and "alpha" in capsys.readouterr().out


def test_refusing_an_action_is_reported_and_the_file_is_unchanged(home, capsys):
    tmp, repo, vault = home
    alter.main(["init", "--tests", check_cmd()])
    alter.main(["goals", "start", "fix add()"])
    capsys.readouterr()
    alter.main(["deny", "not", "now"])
    out = capsys.readouterr().out
    assert "Refusing" in out and (repo / "calc.py").read_text().endswith("a - b\n")


def test_ask_answers_from_cited_context_with_one_call(home, capsys):
    tmp, repo, vault = home
    alter.main(["init", "--tests", check_cmd()])
    capsys.readouterr()
    assert alter.main(["ask", "what", "changed?"]) == 0
    out = capsys.readouterr().out
    assert "[sourced: git:status]" in out and "[recommendation]" in out and "remembered" in out


def test_outside_a_project_plain_words_are_a_question_not_work(home, capsys, monkeypatch):
    tmp, repo, vault = home
    elsewhere = tmp / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert alter.main(["what", "is", "calc?"]) == 0
    assert "[recommendation]" in capsys.readouterr().out
    assert alter.main(["goals", "start", "x"]) == 2


def test_doctor_reports_alter_checks(home, capsys):
    alter.main(["init", "--tests", check_cmd()])
    capsys.readouterr()
    alter.main(["doctor", "--json"])
    names = {c["name"] for c in json.loads(capsys.readouterr().out)["checks"]}
    assert {"pcim_home", "alter_command", "git", "vault", "goals", "outcome_memory"} <= names


def test_the_conversation_runs_goals_in_the_background_and_answers_status_from_records(home, capsys):
    tmp, repo, vault = home
    alter.main(["init", "--tests", check_cmd()])
    alter.main(["permissions", "autonomy", "grant"])
    capsys.readouterr()
    from pcim import terminal as T
    ci, uid = T._open()
    here = T._here(ci, uid)
    said = []
    conv = alter.Conversation(ci, uid, here, out=said.append, poll_s=0.2)
    assert conv.handle("fix add() so it returns the sum")
    gid = conv.watching
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        g = G.GoalStore(ci).get(uid, gid)
        if g.status in G.FINAL:
            break
        time.sleep(0.3)
    conv.poll()
    assert g.status == "done", G.report(ci, uid, g)
    assert any("on it" in s for s in said) and any(f"goal {gid} is done" in s for s in said)
    assert conv.handle("/quit") is False


def test_control_words_are_whole_utterances_only():
    assert alter.control("Pause.") == "pause" and alter.control("what have you finished?") == "status"
    assert alter.control("stop using tabs") is None and alter.control("no tabs please") is None
    assert alter.control("yes") == "approve" and alter.control("keep going") == "resume"


def test_vault_search_skips_icloud_placeholders_instead_of_blocking(home, monkeypatch):
    tmp, repo, vault = home
    real = V.dataless
    monkeypatch.setattr(V, "dataless", lambda p: str(p).endswith("Demo.md") or real(p))
    v = V.Vault(str(vault))
    assert v.not_downloaded == ["Demo.md"] and "Calc" in v.notes and "Demo" not in v.notes
    ev, withheld = thirdeye._vault(str(vault), "calc")
    assert ev and any("iCloud" in w for w in withheld)


def test_the_vault_copy_ranks_like_nos(home):
    tmp, repo, vault = home
    v = V.Vault(str(vault))
    r = V.evaluate(v, [{"q": "demo on stage", "expect": ["Demo"]}, {"q": "add numbers correctly", "expect": ["Calc"]}])
    assert r["hit1"] == 2 and r["mrr"] == 1.0


def test_a_goal_never_runs_on_the_test_double_unless_asked(home, capsys, monkeypatch):
    tmp, repo, vault = home
    assert alter.main(["init", "--tests", check_cmd(), "--provider", "mock"]) == 0
    assert "no model may see this project yet" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        alter.main(["fix", "add()"])
    assert e.value.code == 2 and "alter permissions provider claude-cli on" in capsys.readouterr().err
    assert alter.main(["permissions", "provider", "claude-cli", "on"]) == 0
    assert "anthropic" in capsys.readouterr().out


def test_placeholders_are_requested_from_icloud_before_they_are_skipped(home, monkeypatch):
    tmp, repo, vault = home
    asked = []
    state = {"Demo.md": True}  # a placeholder that arrives when it is read
    monkeypatch.setattr(V, "dataless", lambda p: state.get(os.path.basename(str(p)), False))

    def fake_materialize(paths, timeout=20.0):
        asked.extend(os.path.basename(p) for p in paths)
        state["Demo.md"] = False
    monkeypatch.setattr(V, "materialize", fake_materialize)
    v = V.Vault(str(vault))
    assert asked == ["Demo.md"] and "Demo" in v.notes and v.not_downloaded == []
