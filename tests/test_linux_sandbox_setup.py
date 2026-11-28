"""Setting up confined test runs on Linux: when the bubblewrap probe fails because AppArmor refuses
unprivileged user namespaces (Ubuntu 23.10 and later), the failure says so and where the fix is;
`alter init --confine-tests` and `alter doctor` say whether the sandbox works here; and the
AppArmor profile CI installs is the one the install guide gives, granting bubblewrap nothing more
than user namespaces. No network, no model call; the real probe is never faked as a pass."""
import json
import re
from pathlib import Path

import pytest

from pcim import alter, cli, sandbox, terminal

REPO = Path(__file__).resolve().parents[1]
PROFILE = REPO / "scripts" / "linux" / "bwrap.apparmor"


@pytest.mark.parametrize("value, hinted", [("1\n", True), ("0\n", False), (None, False)])
def test_the_apparmor_restriction_is_named_only_where_it_is_on(tmp_path, monkeypatch, value, hinted):
    knob = tmp_path / "apparmor_restrict_unprivileged_userns"
    if value is not None:
        knob.write_text(value)
    monkeypatch.setattr(sandbox, "APPARMOR_USERNS", knob)
    hint = sandbox._userns_hint()
    assert bool(hint) is hinted
    if hinted:
        assert "kernel.apparmor_restrict_unprivileged_userns=1" in hint and "scripts/linux/bwrap.apparmor" in hint


@pytest.mark.skipif(not Path("/usr/bin/python3").is_file(), reason="the probe needs /usr/bin/python3")
def test_a_failed_probe_says_why_and_runs_nothing(tmp_path, monkeypatch):
    knob = tmp_path / "knob"
    knob.write_text("1")
    monkeypatch.setattr(sandbox, "APPARMOR_USERNS", knob)
    monkeypatch.setattr(sandbox.os, "readlink", lambda path: "ns:[4026531840]")  # no /proc/self/ns off Linux
    refusing = tmp_path / "bwrap"  # stands in for a bubblewrap the kernel refuses namespaces to
    refusing.write_text("#!/bin/sh\necho 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n")
    refusing.chmod(0o755)
    work, outside = tmp_path / "work", tmp_path / "outside"
    work.mkdir()
    outside.write_text("unchanged")
    with pytest.raises(sandbox.SandboxUnavailable) as failed:
        sandbox._probe(str(refusing), work, outside)
    assert str(failed.value).startswith("confinement probe failed; project code was not executed; AppArmor")
    assert outside.read_text() == "unchanged" and not list(work.iterdir())


@pytest.mark.parametrize("why, confined, status, start", [
    (None, 0, "ok", "bubblewrap passed the confinement probe"),
    (None, 2, "ok", "bubblewrap passed the confinement probe"),
    ("verified test confinement unavailable on this platform", 0, "ok", "not used (approved tests run on this machine)"),
    ("verified test confinement unavailable on this platform", 2, "warn", "2 workspace(s) run tests only in the sandbox"),
])
def test_doctor_warns_only_when_a_workspace_needs_the_missing_sandbox(monkeypatch, why, confined, status, start):
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: why)
    name, got_status, detail = terminal._test_sandbox_check(confined)
    assert (name, got_status) == ("test_sandbox", status) and detail.startswith(start)
    assert why is None or detail.endswith(why)


def _confined_project(tmp_path, monkeypatch, capsys, why):
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: why)
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PCIM_EMBEDDER", "hashing-512")
    assert cli.main(["init", "--user", "u", "--providers", "mock"]) == 0
    repo = tmp_path / "proj"
    repo.mkdir()
    monkeypatch.chdir(repo)
    capsys.readouterr()
    assert alter.main(["init", "--no-memory", "--tests", "python3 -c pass", "--confine-tests"]) == 0
    return capsys.readouterr().out


def test_init_with_confined_tests_says_at_once_that_they_cannot_run_here(tmp_path, monkeypatch, capsys):
    out = _confined_project(tmp_path, monkeypatch, capsys, "confinement probe failed; project code was not executed")
    assert "  so no test can run here: confinement probe failed; project code was not executed" in out.splitlines()
    terminal.cmd_doctor(["--json"])
    check = {c["name"]: c for c in json.loads(capsys.readouterr().out)["checks"]}["test_sandbox"]
    assert check["status"] == "warn" and check["detail"].startswith("1 workspace(s) run tests only in the sandbox")


def test_init_with_confined_tests_says_when_the_sandbox_works(tmp_path, monkeypatch, capsys):
    out = _confined_project(tmp_path, monkeypatch, capsys, None)
    assert "  the sandbox works here: bubblewrap passed the confinement probe" in out.splitlines()


def test_init_without_confined_tests_runs_no_probe(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: pytest.fail("probed without --confine-tests"))
    monkeypatch.setenv("PCIM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PCIM_EMBEDDER", "hashing-512")
    assert cli.main(["init", "--user", "u", "--providers", "mock"]) == 0
    (tmp_path / "proj").mkdir()
    monkeypatch.chdir(tmp_path / "proj")
    assert alter.main(["init", "--no-memory", "--tests", "python3 -c pass"]) == 0


def _rules(text):
    """The profile's statements, comments and blank lines left out."""
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def test_the_profile_lets_bubblewrap_create_user_namespaces_and_nothing_more():
    rules = _rules(PROFILE.read_text())
    assert rules == ["abi <abi/4.0>,", "include <tunables/global>",
                     "profile bwrap /usr/bin/bwrap flags=(unconfined) {", "userns,",
                     "include if exists <local/bwrap>", "}"]


def test_ci_and_the_install_guide_install_this_same_file():
    workflow = (REPO / ".github" / "workflows" / "tests.yml").read_text()
    guide = (REPO / "docs" / "release" / "INSTALL.md").read_text()
    install = "sudo install -m 0644 scripts/linux/bwrap.apparmor /etc/apparmor.d/bwrap"
    load = "sudo apparmor_parser -r /etc/apparmor.d/bwrap"
    for text in (workflow, guide, PROFILE.read_text()):
        assert install in text and load in text
    job = workflow.split("\n  confined-linux:\n", 1)[1]
    assert job.index(load) < job.index("scripts/verify_alt4_execution.py")
    # The kernel setting is recorded, never turned off.
    assert not re.search(r"apparmor_restrict_unprivileged_userns\s*=\s*0|sysctl\s+-w", workflow)
