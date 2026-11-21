"""scripts/rewrite_history.py on a throwaway repository: dry-run, apply and rollback.

Needs git-filter-repo (FILTER_REPO=<path>, or on PATH); skipped otherwise. The identifiers are
fake and come from SCAN_IDENTITY_MARKERS, so nothing about this machine is involved.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILTER_REPO = os.environ.get("FILTER_REPO") or shutil.which("git-filter-repo")
pytestmark = pytest.mark.skipif(not FILTER_REPO, reason="git-filter-repo is not installed")


def _env(tmp: Path) -> dict[str, str]:
    marks = tmp / "marks.json"
    marks.write_text(json.dumps({"home path": "/home/fakeperson", "username ($USER)": "fakeperson",
                                 "account display name": "Fakename"}))
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(PYTHONPATH=str(ROOT / "src"), SCAN_IDENTITY_MARKERS=str(marks), FILTER_REPO=FILTER_REPO,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.com")
    return env


def _git(repo: Path, env: dict, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True).stdout


def _run(repo: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "rewrite_history.py"), *args],
                          cwd=repo, env=env, capture_output=True, text=True)


def test_dry_run_apply_and_rollback(tmp_path):
    env = _env(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, env, "init", "-q", "-b", "main")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-qm", "base")
    _git(repo, env, "checkout", "-qb", "feat")
    (repo / "notes.md").write_text('check: `grep -ciE "acme|fakeperson" out/*`\n')
    (repo / "data.json").write_text('{"cwd": "/home/fakeperson/sandbox"}\n')
    (repo / "test_u.py").write_text('USER = "fakename"\n')  # a name as a test user id: kept, as on main
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-qm", "adds identifiers")
    (repo / "src" / "b.py").write_text("y = 2\n")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-qm", "more code")
    old = _git(repo, env, "rev-parse", "feat").strip()
    old_src = _git(repo, env, "rev-parse", "feat:src").strip()

    plan = _run(repo, env, "plan", "--base", "main")
    assert plan.returncode == 0, plan.stderr
    assert "feat" in plan.stdout

    work = tmp_path / "work"
    dry = _run(repo, env, "dry-run", "--work", str(work), "--base", "main")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "verified" in dry.stdout
    assert _git(repo, env, "rev-parse", "feat").strip() == old       # the real repo is untouched
    assert not _git(repo, env, "for-each-ref", "refs/backup", "refs/rewritten", "refs/rewrite-check").strip()

    applied = _run(repo, env, "apply", "--work", str(work))
    assert applied.returncode == 0, applied.stdout + applied.stderr
    new = _git(repo, env, "rev-parse", "feat").strip()
    assert new != old
    assert _git(repo, env, "rev-parse", "feat:src").strip() == old_src
    assert "fakeperson" not in _git(repo, env, "show", "feat:data.json")
    assert "fakeperson" not in _git(repo, env, "show", "feat:notes.md")
    assert _git(repo, env, "show", "feat:test_u.py") == 'USER = "fakename"\n'   # names are not rewritten
    assert "fakeperson" not in _git(repo, env, "log", "-p", "main..feat")
    assert not _git(repo, env, "status", "--porcelain").strip()          # the checked-out worktree was refreshed
    stamp = next(line.split()[-1] for line in applied.stdout.splitlines() if line.startswith("applied. stamp:"))
    assert _git(repo, env, "rev-parse", f"refs/backup/{stamp}/feat").strip() == old

    back = _run(repo, env, "rollback", "--stamp", stamp)
    assert back.returncode == 0, back.stdout + back.stderr
    assert _git(repo, env, "rev-parse", "feat").strip() == old
    assert not _git(repo, env, "status", "--porcelain").strip()


def test_apply_refuses_when_a_branch_moved_after_the_dry_run(tmp_path):
    env = _env(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, env, "init", "-q", "-b", "main")
    (repo / "f").write_text("base\n")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-qm", "base")
    _git(repo, env, "checkout", "-qb", "feat")
    (repo / "d.json").write_text('"/home/fakeperson/x"\n')
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-qm", "leak")
    work = tmp_path / "work"
    assert _run(repo, env, "dry-run", "--work", str(work), "--base", "main").returncode == 0
    (repo / "g").write_text("later\n")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-qm", "after the dry run")
    moved = _git(repo, env, "rev-parse", "feat").strip()
    r = _run(repo, env, "apply", "--work", str(work))
    assert r.returncode != 0 and "moved since the dry run" in (r.stdout + r.stderr)
    assert _git(repo, env, "rev-parse", "feat").strip() == moved


def test_dry_run_fails_when_an_identifier_survives_the_redaction(tmp_path):
    env = _env(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, env, "init", "-q", "-b", "main")
    (repo / "f").write_text("base\n")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-qm", "base")
    _git(repo, env, "checkout", "-qb", "feat")
    (repo / "t.py").write_text('OWNER = "fakeperson"\n')   # a username outside any grep pattern
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-qm", "leak")
    old = _git(repo, env, "rev-parse", "feat").strip()
    r = _run(repo, env, "dry-run", "--work", str(tmp_path / "work"), "--base", "main")
    assert r.returncode == 1 and "identifiers remain" in r.stdout
    assert json.loads((tmp_path / "work" / "state.json").read_text())["verified"] is False
    assert _run(repo, env, "apply", "--work", str(tmp_path / "work")).returncode != 0
    assert _git(repo, env, "rev-parse", "feat").strip() == old
