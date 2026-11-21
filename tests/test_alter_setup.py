"""`alter setup` and `alter config`, offline, in a throwaway PCIM_HOME and HOME. No model is called:
the `claude` CLI is missing, or a stand-in that only answers --version."""

import json
import os
import subprocess
import sys

import pytest

from pcim import alter, cli
from pcim import goals as G
from pcim import terminal as T
from pcim.adapters.claude_cli import PROFILE_REQUIRED
from pcim.engine import PersonalCI

FAKE_CLAUDE = "#!{py}\nimport sys\nif sys.argv[1:] == ['--version']:\n    print('9.9.9 (Claude Code)')\n    sys.exit(0)\nsys.exit(3)\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "pcim"))
    monkeypatch.setenv("PCIM_EMBEDDER", "hashing-512")
    monkeypatch.setenv("PCIM_CLAUDE_BIN", str(tmp_path / "no-claude-here"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("PCIM_CLI_REQUIRE_ISOLATION", raising=False)
    monkeypatch.chdir(work)
    return tmp_path


def fake_claude(tmp_path, monkeypatch, logged_in: bool):
    b = tmp_path / "bin" / "claude"
    b.parent.mkdir(exist_ok=True)
    b.write_text(FAKE_CLAUDE.format(py=sys.executable))
    b.chmod(0o755)
    monkeypatch.setenv("PCIM_CLAUDE_BIN", str(b))
    if logged_in:  # what `claude` stores after a login: the account profile (no email needed here)
        acct = {k: "x" for k in PROFILE_REQUIRED}
        (tmp_path / "home" / ".claude.json").write_text(json.dumps({"oauthAccount": acct}))


def store():
    ci = PersonalCI(T._home())
    return ci, ci.store.list_user_ids()


def live_memory_grants(ci, uid):
    return [g for g in ci.ledger.grants(uid) if g.source == G.MEMORY_SOURCE and g.revoked_at is None]


# --------------------------------------------------------------------------- setup
def test_setup_creates_the_identity_me_with_outcome_memory_and_says_how_to_get_claude(env, capsys):
    assert alter.main(["setup"]) == 0
    out = capsys.readouterr().out
    tok = env / "pcim" / "token"
    assert tok.is_file() and (tok.stat().st_mode & 0o777) == 0o600
    ci, users = store()
    assert users == ["me"]  # a label, never the account or the person's name
    assert ci.user_for_token(tok.read_text().strip()) == "me"
    assert ci.store.get_user("me")["display_name"] == ""
    assert ci.store.get_user("me")["settings"]["providers"] == ["claude-cli"]
    assert len(live_memory_grants(ci, "me")) == 1
    assert "identity: me created" in out and "goal outcomes to memory: on" in out
    assert "claude CLI: not found" in out and "npm install -g @anthropic-ai/claude-code" in out
    assert "alter doctor" in out and "alter init" in out  # next steps
    assert tok.read_text().strip() not in out  # the token is never printed


def test_setup_never_changes_an_existing_identity(env, capsys):
    assert cli.main(["init", "--user", "pat", "--providers", "mock"]) == 0
    tok = (env / "pcim" / "token").read_text()
    ci, _ = store()
    before = (ci.store.get_user("pat"), [g.id for g in ci.ledger.grants("pat")])
    capsys.readouterr()
    assert alter.main(["setup", "--user", "someone-else", "--providers", "claude-cli", "--no-memory"]) == 0
    out = capsys.readouterr().out
    assert "identity: pat (already there; left unchanged)" in out and "--user someone-else ignored" in out
    assert "claude CLI: not needed by this identity (providers: mock)" in out
    ci, users = store()
    assert users == ["pat"] and (env / "pcim" / "token").read_text() == tok
    assert (ci.store.get_user("pat"), [g.id for g in ci.ledger.grants("pat")]) == before
    assert alter.main(["setup"]) == 0  # run again: the same identity, memory granted once
    ci, users = store()
    assert users == ["pat"] and len(live_memory_grants(ci, "pat")) == 1
    assert alter.main(["setup"]) == 0
    assert len(live_memory_grants(store()[0], "pat")) == 1


def test_setup_refuses_a_store_whose_token_is_missing_or_wrong(env, capsys):
    assert cli.main(["init", "--user", "pat", "--providers", "mock"]) == 0
    tok = env / "pcim" / "token"
    tok.write_text("not-the-token")
    assert alter.main(["setup"]) == 2
    assert "matches no user" in capsys.readouterr().err and tok.read_text() == "not-the-token"
    tok.unlink()
    assert alter.main(["setup"]) == 2
    assert "token file is missing" in capsys.readouterr().err
    assert store()[1] == ["pat"] and not tok.exists()  # no second identity next to the first


def test_setup_with_an_unknown_provider_writes_no_identity(env, capsys):
    assert alter.main(["setup", "--providers", "no-such-provider"]) == 2
    assert not (env / "pcim" / "token").exists() and store()[1] == []
    assert alter.main(["setup"]) == 0  # and a corrected run works in the same folder
    assert store()[1] == ["me"]


def test_setup_keeps_memory_off_when_asked_or_when_the_person_turned_it_off(env, capsys):
    assert alter.main(["setup", "--no-memory"]) == 0
    assert "goal outcomes to memory: off" in capsys.readouterr().out
    ci, _ = store()
    assert not G.has_outcome_grant(ci, "me")
    assert alter.main(["setup"]) == 0  # never granted or refused yet: granted now
    assert G.has_outcome_grant(store()[0], "me")
    assert alter.main(["config", "set", "memory", "off"]) == 0
    capsys.readouterr()
    assert alter.main(["setup"]) == 0  # turned off by the person: setup leaves it off
    assert "off, as you set it" in capsys.readouterr().out
    assert not G.has_outcome_grant(store()[0], "me")


def test_setup_reports_whether_claude_is_logged_in(env, capsys, monkeypatch):
    fake_claude(env, monkeypatch, logged_in=False)
    assert alter.main(["setup"]) == 0
    out = capsys.readouterr().out
    assert "v9.9.9" in out and "not logged in" in out and "log in (/login)" in out
    fake_claude(env, monkeypatch, logged_in=True)
    assert alter.main(["setup"]) == 0
    out = capsys.readouterr().out
    assert "v9.9.9" in out and "logged in" in out and "not logged in" not in out
    assert "fix the claude CLI" not in out and "pcim.adapters.cli_probe" in out  # isolation not yet verified


def test_setup_init_makes_the_current_folder_a_project(env, capsys):
    assert alter.main(["setup", "--init", "--tests", "true", "--no-write"]) == 0
    out = capsys.readouterr().out
    assert "Project work" in out and "alter init" not in out.rsplit("Next:", 1)[1]
    ci, uid = T._open()
    here = T._here(ci, uid)
    assert here is not None and here.name == "work"
    from pcim.workspace import Workspace
    spec = Workspace(ci).workspace_auth(uid, here.project_id)["spec"]
    assert spec["run_argv"] == ["true"] and not spec["allow_write"]
    assert len(live_memory_grants(ci, uid)) == 1  # setup and init granted it once between them
    assert alter.main(["setup", "--init"]) == 0 and "Already a project" in capsys.readouterr().out


def test_setup_flags_that_need_init(env):
    with pytest.raises(SystemExit) as e:
        alter.main(["setup", "--tests", "true"])
    assert e.value.code == 2


def test_doctor_points_to_setup_before_there_is_an_identity(env, capsys):
    assert alter.main(["doctor", "--json"]) == 1
    checks = {c["name"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert checks["pcim_home"]["status"] == "fail" and "alter setup" in checks["pcim_home"]["detail"]
    with pytest.raises(SystemExit):
        alter.main(["status"])
    assert "alter setup" in capsys.readouterr().err
    assert alter.main(["setup"]) == 0
    capsys.readouterr()
    assert alter.main(["doctor", "--json"]) == 0  # no claude CLI is a warning, not a failure
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True


# --------------------------------------------------------------------------- config
def test_config_lists_gets_and_sets_each_setting(env, capsys):
    assert alter.main(["setup"]) == 0
    capsys.readouterr()
    assert alter.main(["config", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"vault": "off", "vault.path": "", "github": "off", "memory": "on",
                                                   "autonomy.mode": "collaborative"}
    assert alter.main(["config"]) == 0 and "autonomy.mode" in capsys.readouterr().out

    vault = env / "vault"
    vault.mkdir()
    assert alter.main(["config", "set", "vault", "on"]) == 2  # no vault under ~/Documents, none set
    assert "alter config set vault.path" in capsys.readouterr().err
    assert alter.main(["config", "set", "vault.path", str(vault)]) == 0
    assert alter.main(["config", "get", "vault"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "off"  # setting the path does not turn it on
    assert alter.main(["config", "set", "vault", "on"]) == 0
    assert alter.main(["config", "set", "github", "yes"]) == 0
    assert alter.main(["config", "set", "memory", "off"]) == 0
    assert alter.main(["config", "set", "autonomy.mode", "Copilot"]) == 0
    capsys.readouterr()
    assert alter.main(["config", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"vault": "on", "vault.path": str(vault.resolve()), "github": "on",
                                                   "memory": "off", "autonomy.mode": "copilot"}
    for key, want in (("vault.path", str(vault.resolve())), ("memory", "off"), ("autonomy.mode", "copilot")):
        assert alter.main(["config", "get", key]) == 0 and capsys.readouterr().out.strip() == want


def test_config_and_permissions_read_and_write_the_same_settings(env, capsys):
    assert alter.main(["setup"]) == 0
    assert alter.main(["config", "set", "github", "on"]) == 0
    capsys.readouterr()
    assert alter.main(["permissions"]) == 0
    assert "github on" in capsys.readouterr().out
    assert alter.main(["permissions", "github", "off"]) == 0
    assert alter.main(["permissions", "memory", "on"]) == 0
    assert alter.main(["permissions", "memory", "on"]) == 0
    capsys.readouterr()
    assert alter.main(["config", "get", "github"]) == 0 and capsys.readouterr().out.strip() == "off"
    ci, _ = store()
    assert len(live_memory_grants(ci, "me")) == 1  # granting again adds no second grant
    assert alter.main(["permissions", "memory", "off"]) == 0
    assert alter.main(["config", "get", "memory"]) == 0 and capsys.readouterr().out.strip().endswith("off")
    assert live_memory_grants(store()[0], "me") == []


def test_config_autonomous_mode_still_asks_without_a_grant(env, capsys):
    assert alter.main(["setup"]) == 0
    capsys.readouterr()
    assert alter.main(["config", "set", "autonomy.mode", "autonomous"]) == 0
    assert "still ask unless an autonomy grant covers them" in capsys.readouterr().out


@pytest.mark.parametrize("argv, err", [
    (["get", "nope"], "no key 'nope'"),
    (["set", "nope", "on"], "no key 'nope'"),
    (["set", "github", "maybe"], "expected on or off"),
    (["set", "memory", "2"], "expected on or off"),
    (["set", "autonomy.mode", "reckless"], "mode must be one of"),
    (["set", "vault.path", "/no/such/folder/here"], "no vault at"),
    (["set", "vault.path", " "], "needs a folder"),
    (["set", "github"], "usage"),
    (["get"], "usage"),
    (["frobnicate"], "usage"),
    (["list", "extra"], "usage"),
])
def test_config_refuses_bad_input_and_changes_nothing(env, capsys, argv, err):
    assert alter.main(["setup"]) == 0
    capsys.readouterr()
    assert alter.main(["config", "list", "--json"]) == 0
    before = capsys.readouterr().out
    assert alter.main(["config", *argv]) == 2
    assert err in capsys.readouterr().err
    assert alter.main(["config", "list", "--json"]) == 0 and capsys.readouterr().out == before


def test_config_needs_an_identity(env, capsys):
    with pytest.raises(SystemExit) as e:
        alter.main(["config", "list"])
    assert e.value.code == 2 and "alter setup" in capsys.readouterr().err
    assert alter.main(["config", "--help"]) == 0 and "vault.path" in capsys.readouterr().out


def test_setup_and_config_through_the_installed_entry_point(env):
    """The `alter` console script runs pcim.alter:main; the same through `python -m`, in a fresh process."""
    run = lambda *a: subprocess.run([sys.executable, "-m", "pcim.alter", *a], capture_output=True, text=True,  # noqa: E731
                                    env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    r = run("setup", "--user", "me")
    assert r.returncode == 0, r.stderr
    r = run("config", "get", "autonomy.mode")
    assert r.returncode == 0 and r.stdout.strip() == "collaborative"
