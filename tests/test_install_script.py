"""scripts/install_alter.sh end to end: build the wheel from this checkout, install it into a throwaway
ALTER_HOME and ALTER_BIN without the semantic extra, run the installed `alter` (--version, setup,
doctor --json), upgrade to a newer wheel, roll back, and uninstall.

pip downloads the core dependencies and setuptools (for the build), so the test needs PyPI or the
index in PIP_INDEX_URL: it is skipped when that cannot be reached, and marked slow (-m "not slow"
leaves it out). The commands run with a throwaway HOME and no `claude`; no model is called. The
person's own ~/.alter, ~/.local/bin and ~/.pcim are never used.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

from pcim import __version__

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "install_alter.sh"
NEWER = "99.0"


def _index_reachable() -> bool:
    url = urlparse(os.environ.get("PIP_INDEX_URL") or "https://pypi.org/simple/")
    try:
        socket.create_connection((url.hostname, url.port or (443 if url.scheme == "https" else 80)), timeout=3).close()
        return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed"),
    pytest.mark.skipif(not _index_reachable(), reason="no network: pip cannot download the dependencies"),
]


def _run(argv, env, cwd=None, check=True):
    r = subprocess.run([str(a) for a in argv], env=env, cwd=cwd, capture_output=True, text=True, timeout=600)
    if check:
        assert r.returncode == 0, f"{argv}: exit {r.returncode}\n{r.stdout}\n{r.stderr}"
    return r


def _newer_checkout(dest: Path) -> Path:
    """A copy of this checkout whose version is NEWER: what the next release looks like to the script."""
    dest.mkdir()
    for name in ("pyproject.toml", "README.md", "requirements-lock.txt"):
        shutil.copy2(REPO / name, dest / name)
    shutil.copytree(REPO / "src", dest / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    (dest / "scripts").mkdir()
    shutil.copy2(SCRIPT, dest / "scripts" / SCRIPT.name)
    for f, old, new in ((dest / "pyproject.toml", f'version = "{__version__}"', f'version = "{NEWER}"'),
                        (dest / "src" / "pcim" / "__init__.py", f'__version__ = "{__version__}"',
                         f'__version__ = "{NEWER}"')):
        text = f.read_text()
        assert old in text
        f.write_text(text.replace(old, new))
    return dest / "scripts" / SCRIPT.name


def test_install_setup_doctor_upgrade_rollback_uninstall(tmp_path):
    alter_home, alter_bin, pcim_home = tmp_path / "alter", tmp_path / "bin", tmp_path / "pcim"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PCIM_CLI_REQUIRE_ISOLATION",
                                                             "CLAUDE_CONFIG_DIR", "VIRTUAL_ENV")}
    env.update(ALTER_HOME=str(alter_home), ALTER_BIN=str(alter_bin), PCIM_HOME=str(pcim_home),
               PCIM_EMBEDDER="hashing-512", HF_HUB_OFFLINE="1")
    # A command of the person's own where a link goes: kept as pcim.pre-alter, put back by --uninstall
    alter_bin.mkdir()
    mine = alter_bin / "pcim"
    mine.write_text("#!/bin/sh\necho mine\n")
    mine.chmod(0o755)

    # 1. install: the script builds the wheel from this checkout (no wheel given)
    r = _run(["bash", SCRIPT, "--no-semantic", "--no-doctor", "--python", sys.executable], env)
    assert f"installing {__version__}" in r.stdout and "building a wheel" in r.stderr
    venv_bin = (alter_home / "venv" / "bin").resolve()
    for c in ("alter", "ci", "pcim"):
        link = alter_bin / c
        assert link.is_symlink() and Path(os.readlink(link)) == venv_bin / c
    assert (alter_bin / "pcim.pre-alter").read_text() == "#!/bin/sh\necho mine\n"
    kept = sorted(p.name for p in (alter_home / "wheels").glob("*.whl"))
    assert kept == [f"contextual_intelligence-{__version__}-py3-none-any.whl"]
    probe = _run([venv_bin / "python", "-c", "import pcim, importlib.util as u; "
                  "print(pcim.__file__); print(u.find_spec('sentence_transformers') is not None)"], env)
    where, semantic = probe.stdout.split()
    assert Path(where).resolve().is_relative_to(alter_home.resolve()) and semantic == "False"

    # 2. the installed commands, through the links, as a new person: empty HOME, no claude CLI
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    cmd_env = {**env, "HOME": str(home), "PATH": f"{alter_bin}{os.pathsep}{env.get('PATH', '')}",
               "PCIM_CLAUDE_BIN": str(tmp_path / "no-claude-here")}
    alter = alter_bin / "alter"
    assert _run([alter, "--version"], cmd_env).stdout.strip() == f"alter {__version__} (alpha)"
    r = _run([alter, "setup"], cmd_env, cwd=work)
    assert "identity: me created" in r.stdout and "claude CLI: not found" in r.stdout
    assert (pcim_home / "token").is_file()
    r = _run([alter, "doctor", "--json"], cmd_env, cwd=work)
    doc = json.loads(r.stdout)
    checks = {c["name"]: c["status"] for c in doc["checks"]}
    assert doc["ok"] is True and checks["pcim_home"] == "ok" and checks["alter_command"] == "ok"
    assert checks["claude_cli"] == "warn" and checks["outcome_memory"] == "ok"

    # 3. run again: nothing to change, nothing backed up twice
    r = _run(["bash", SCRIPT, "--no-semantic", "--no-doctor", alter_home / "wheels" / kept[0]], env)
    assert f"reinstall: {__version__} -> {__version__}" in r.stdout and "linked" not in r.stdout
    assert sorted(p.name for p in alter_bin.iterdir()) == ["alter", "ci", "pcim", "pcim.pre-alter"]

    # 4. upgrade: a newer release over this one; the data is kept
    newer_script = _newer_checkout(tmp_path / "newer")
    r = _run(["bash", newer_script, "--no-semantic"], env | {"PCIM_CLAUDE_BIN": str(tmp_path / "no-claude-here"),
                                                              "HOME": str(home)})
    assert f"upgrade: {__version__} -> {NEWER}" in r.stdout and "doctor: nothing blocking" in r.stdout
    assert _run([alter, "--version"], cmd_env).stdout.strip() == f"alter {NEWER} (alpha)"
    assert "user me" in _run([alter, "status"], cmd_env, cwd=work).stdout
    assert len(list((alter_home / "wheels").glob("*.whl"))) == 2

    # 5. roll back to the wheel installed first
    r = _run(["bash", SCRIPT, "--no-semantic", "--no-doctor", "--rollback", alter_home / "wheels" / kept[0]], env)
    assert f"downgrade: {NEWER} -> {__version__}" in r.stdout
    assert _run([alter, "--version"], cmd_env).stdout.strip() == f"alter {__version__} (alpha)"
    assert _run([alter, "config", "get", "memory"], cmd_env).stdout.strip() == "on"

    # 6. uninstall: links gone, the person's own command back, the data untouched
    r = _run(["bash", SCRIPT, "--uninstall"], env)
    assert not (alter_home / "venv").exists()
    assert sorted(p.name for p in alter_bin.iterdir()) == ["pcim"]
    assert not mine.is_symlink() and mine.read_text() == "#!/bin/sh\necho mine\n"
    assert (pcim_home / "token").is_file() and (pcim_home / "pcim.db").is_file()
    assert (alter_home / "wheels").is_dir()  # kept for a reinstall
    r = _run(["bash", SCRIPT, "--uninstall"], env)  # twice is fine
    assert "removed" not in r.stdout


def test_install_refuses_what_it_should_not_touch(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(ALTER_HOME=str(tmp_path / "alter"), ALTER_BIN=str(tmp_path / "bin"), PCIM_HOME=str(tmp_path / "pcim"))
    (tmp_path / "alter").mkdir()
    (tmp_path / "alter" / "venv").mkdir()  # not a venv: never cleared or removed
    (tmp_path / "alter" / "venv" / "keep.txt").write_text("x")
    r = _run(["bash", SCRIPT, "--no-semantic", "--python", sys.executable], env, check=False)
    assert r.returncode == 1 and "is not a venv" in r.stderr
    r = _run(["bash", SCRIPT, "--uninstall"], env, check=False)
    assert r.returncode == 1 and (tmp_path / "alter" / "venv" / "keep.txt").exists()
    r = _run(["bash", SCRIPT, "--no-semantic", tmp_path / "missing.whl"], env, check=False)
    assert r.returncode == 1 and "no wheel at" in r.stderr
    wrong = tmp_path / "other-1.0-py3-none-any.whl"
    wrong.write_text("")
    r = _run(["bash", SCRIPT, "--no-semantic", wrong], env, check=False)
    assert r.returncode == 1 and "is not a contextual-intelligence wheel" in r.stderr
    assert sorted(p.name for p in (tmp_path / "alter").iterdir()) == ["venv"]  # refused before installing anything
    assert _run(["bash", SCRIPT, "--bogus"], env, check=False).returncode == 1
    assert "--rollback" in _run(["bash", SCRIPT, "--help"], env).stdout
