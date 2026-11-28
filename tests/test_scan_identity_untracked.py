"""scripts/scan_identity.py's diff mode on a throwaway repository with fake identifiers.

The pilot dry run found that the C10 scan of a goal's diff missed new files: `git diff` never shows a
file git does not track yet, so an identifier written into a file the goal created was not counted.
`--untracked` reads those files whole, minus ignored ones. Nothing about this machine is involved.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env(tmp: Path) -> dict[str, str]:
    marks = tmp / "marks.json"
    marks.write_text(json.dumps({"username ($USER)": "fakeperson"}))
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(PYTHONPATH=str(ROOT / "src"), SCAN_IDENTITY_MARKERS=str(marks),
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.com")
    return env


def _scan(repo: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "scan_identity.py"), *args],
                          cwd=repo, env=env, capture_output=True, text=True)


def test_the_diff_scan_reads_new_files_only_with_untracked_and_never_ignored_ones(tmp_path):
    env = _env(tmp_path)
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, env=env, check=True, capture_output=True)
    (repo / "calc.py").write_text("x = 1\n")
    (repo / ".gitignore").write_text("ignored.txt\n")
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, env=env, check=True, capture_output=True)
    (repo / "sub" / "notes.md").write_text("Written by a goal for FakePerson.\n")  # new: git does not track it
    (repo / "ignored.txt").write_text("fakeperson\n")                            # ignored: not the goal's work

    plain = _scan(repo / "sub", env, "HEAD")  # from a subfolder too: paths are the repository's
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert re.search(r"username \(\$USER\)\s+0\b", plain.stdout) and "--untracked reads them" in plain.stdout

    full = _scan(repo / "sub", env, "HEAD", "--untracked")
    assert full.returncode == 1, full.stdout + full.stderr
    assert re.search(r"username \(\$USER\)\s+1\s+in: sub/notes\.md$", full.stdout, re.MULTILINE)
    assert "ignored.txt" not in full.stdout and "fakeperson" not in full.stdout.lower()  # never a value
    assert "including 1 untracked file(s) read whole" in full.stdout

    assert _scan(repo, env, "--history", "HEAD", "--untracked").returncode == 2
