"""The claude CLI adapter keeps the account's identity out of the model's input.

Measured first: with only its isolation flags, the CLI put the account email and a working directory
naming the user into every request (2026-11-13, CLI 2.1.316, `python -m pcim.adapters.cli_probe`,
which records the request itself). These tests pin the isolation the adapter now applies and the
rule that an identity-sensitive call is refused unless the installed CLI version was measured clean.
The live measurement is `test_live_probe_finds_no_identity_in_the_model_input` (opt-in).
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

from pcim.adapters import claude_cli
from pcim.adapters.base import PROVIDER_WIDE_KINDS, RenderedPrompt
from pcim.adapters.claude_cli import IDENTITY_ENV, ClaudeCLIAdapter, ClaudeCLIError

EMAIL = "pat.person@example.edu"
OK = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "OK", "total_cost_usd": 0.0})


@pytest.fixture
def person(tmp_path, monkeypatch):
    """A home directory with a logged-in account profile, identity variables, and CI's own home."""
    home = tmp_path / "home" / "patperson"
    home.mkdir(parents=True)
    (home / ".claude.json").write_text(json.dumps({"oauthAccount": {
        "emailAddress": EMAIL, "displayName": "Pat", "fullName": "Pat Person", "organizationName": "Pat's Lab",
        "accountUuid": "acct-1", "organizationUuid": "org-1", "billingType": "stripe_subscription",
        "accountCreatedAt": "2024-01-01", "subscriptionCreatedAt": "2024-01-02", "ccOnboardingFlags": {}}}))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_SECURESTORAGE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("PCIM_CLI_REQUIRE_ISOLATION", raising=False)
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "pcim"))
    for k in IDENTITY_ENV:
        monkeypatch.setenv(k, EMAIL)
    monkeypatch.setattr(claude_cli, "cli_version", lambda binary: "9.9.9")
    return home


def capturing():
    seen = {}

    def run(argv, input, **kw):
        cfg = Path(kw["env"]["CLAUDE_CONFIG_DIR"]) if "CLAUDE_CONFIG_DIR" in kw["env"] else None
        seen.update(argv=argv, input=input, cwd=kw["cwd"], env=dict(kw["env"]), cfg=cfg,
                    cfg_mode=stat.S_IMODE(cfg.stat().st_mode) if cfg else None,
                    file_mode=stat.S_IMODE((cfg / ".claude.json").stat().st_mode) if cfg else None,
                    files={p.name: p.read_text() for p in cfg.iterdir()} if cfg else {})
        return subprocess.CompletedProcess(argv, 0, stdout=OK, stderr="")
    return run, seen


def test_the_cli_runs_outside_home_with_a_sanitized_profile_and_no_identity_in_its_environment(person):
    run, seen = capturing()
    r = ClaudeCLIAdapter(runner=run).generate(RenderedPrompt(system="s", user="u"))
    assert not claude_cli.names_the_person(seen["cwd"]) and str(person) not in seen["cwd"]
    profile = json.loads(seen["files"][".claude.json"])["oauthAccount"]
    assert profile["emailAddress"] == "" and not {"displayName", "fullName", "organizationName"} & set(profile)
    assert profile["billingType"] == "stripe_subscription"  # enough for the CLI to treat the login as set up
    assert time.time() * 1000 - profile["profileFetchedAt"] < 60_000  # marked fresh, so not refetched first
    env = seen["env"]
    assert env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] == ""  # the login itself stays where it was
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert not set(IDENTITY_ENV) & set(env)
    assert seen["cfg_mode"] == 0o700 and seen["file_mode"] == 0o600
    everything = json.dumps([seen["argv"], seen["input"], seen["cwd"], env, seen["files"]])
    assert EMAIL not in everything and "Pat Person" not in everything
    assert not seen["cfg"].exists()  # the per-call config is removed afterwards
    assert r.provider_context["status"] == "unverified" and "9.9.9 has not been probed" in r.provider_context["why"]


def test_a_parent_config_dir_keeps_its_own_login(person, tmp_path, monkeypatch):
    own = tmp_path / "own-config"
    own.mkdir()
    (own / ".claude.json").write_text((person / ".claude.json").read_text())
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(own))
    run, seen = capturing()
    ClaudeCLIAdapter(runner=run).generate(RenderedPrompt(system="s", user="u"))
    assert seen["env"]["CLAUDE_SECURESTORAGE_CONFIG_DIR"] == str(own) and seen["env"]["CLAUDE_CONFIG_DIR"] != str(own)
    assert json.loads(seen["files"][".claude.json"])["oauthAccount"]["emailAddress"] == ""


def record(home: Path, version: str, clean: bool):
    home.mkdir(parents=True, exist_ok=True)
    (home / claude_cli.PROBE_RECORD).write_text(json.dumps({version: {"clean": clean, "probed_at": "t",
                                                                      "found": [] if clean else ["account email"]}}))


def test_an_identity_sensitive_call_is_refused_unless_this_cli_version_was_measured_clean(person, tmp_path):
    run, seen = capturing()
    ad = ClaudeCLIAdapter(runner=run, require_isolation=True)
    with pytest.raises(ClaudeCLIError) as e:
        ad.generate(RenderedPrompt(system="s", user="u"))
    assert e.value.kind == "config" and "unverified" in str(e.value) and not seen  # nothing was run
    assert e.value.kind in PROVIDER_WIDE_KINDS  # so routing does not try the CLI's other models either
    record(tmp_path / "pcim", "9.9.9", clean=False)
    with pytest.raises(ClaudeCLIError) as e:
        ad.generate(RenderedPrompt(system="s", user="u"))
    assert "exposed" in str(e.value) and not seen
    record(tmp_path / "pcim", "9.9.9", clean=True)
    r = ad.generate(RenderedPrompt(system="s", user="u"))
    assert seen and r.provider_context["status"] == "verified"
    record(tmp_path / "pcim", "9.9.8", clean=True)  # a clean record for another version proves nothing here
    with pytest.raises(ClaudeCLIError):
        ad.generate(RenderedPrompt(system="s", user="u"))


def test_the_environment_variable_makes_every_adapter_identity_sensitive(person, monkeypatch):
    monkeypatch.setenv("PCIM_CLI_REQUIRE_ISOLATION", "1")
    run, seen = capturing()
    with pytest.raises(ClaudeCLIError):
        ClaudeCLIAdapter(runner=run).agent_step("s", [{"role": "user", "content": "x"}], [])
    assert not seen


def test_without_a_profile_to_sanitize_or_with_a_workdir_under_home_isolation_is_not_verified(person, tmp_path):
    record(tmp_path / "pcim", "9.9.9", clean=True)
    run, _ = capturing()
    under_home = ClaudeCLIAdapter(runner=run, workdir=person / "sandbox")
    assert under_home.identity_isolation()["status"] == "unverified"
    (person / ".claude.json").write_text("{}")
    st = ClaudeCLIAdapter(runner=run).identity_isolation()
    assert st["status"] == "unverified" and "no stored account profile" in st["why"]


def test_isolation_off_is_said_on_the_receipt(person):
    run, seen = capturing()
    r = ClaudeCLIAdapter(runner=run, isolate_identity=False).generate(RenderedPrompt(system="s", user="u"))
    assert "CLAUDE_CONFIG_DIR" not in seen["env"] and r.provider_context["status"] == "off"
    assert "adds the account email" in r.provider_context["why"]


def test_the_receipt_carries_what_the_provider_added(person, tmp_path):
    from pcim.engine import PersonalCI
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", "Pat", ("claude-cli",))
    run, _ = capturing()
    ci.set_adapter("claude-cli", "sonnet", ClaudeCLIAdapter("sonnet", runner=run))
    res = ci.ask("u", "What does HTTP 429 mean?", "claude-cli", "sonnet")
    assert ci.store.get_interaction(res.interaction_id)["receipt"]["provider_context"]["status"] == "unverified"


def test_routing_never_sends_an_identity_sensitive_request_through_an_unverified_cli(person, tmp_path):
    from pcim.engine import PersonalCI, RoutingError
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", "Pat", ("claude-cli",))
    calls = []
    for m in ("haiku", "sonnet", "opus"):
        def run(argv, input, _m=m, **kw):
            calls.append(_m)
            return subprocess.CompletedProcess(argv, 0, stdout=OK, stderr="")
        ci.set_adapter("claude-cli", m, ClaudeCLIAdapter(m, runner=run, require_isolation=True))
    with pytest.raises(RoutingError) as e:
        ci.ask_routed("u", "What does HTTP 429 mean?")
    assert not calls  # no model on the CLI was called
    assert all(a["kind"] in ("config", "skipped") for a in e.value.attempts)


@pytest.mark.skipif(os.environ.get("PCIM_LIVE_CLI_PROBE") != "1", reason="live: set PCIM_LIVE_CLI_PROBE=1 (a few Haiku calls)")
def test_live_probe_finds_no_identity_in_the_model_input():
    from pcim.adapters import cli_probe
    res = cli_probe.run(trials=2, baseline=True)
    assert res["clean"], res["found"]
    assert all(t["requests"] >= 1 for t in res["trials"])
    assert res["baseline"][0]["found_in_model_input"], "the baseline should show the exposure the isolation removes"
