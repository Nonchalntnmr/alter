"""Release gate: does a branch add this machine's identifiers? Prints counts and locations, never values.

    PYTHONPATH=src python scripts/scan_identity.py [BASE]             # the diff against BASE
    PYTHONPATH=src python scripts/scan_identity.py --history [BASE] [--head REF]   # every commit in BASE..REF

Default BASE: origin/main.

The identifiers are read from this machine at run time, the same set the CLI identity probe uses:
- the account email and its local part, the account names, and the account and organization ids;
- $USER, the home path and the hostname;
- git's user.name and user.email.
So nothing personal has to be written into the repository to check for it.

Diff mode scans only the lines the diff adds. It answers "does the final tree add an identifier?".

History mode answers "would pushing this branch publish an identifier?". It scans three things.
1. Each commit's own added lines. Merges count only lines that differ from every parent, so a
   line is charged to the commit that introduced it.
2. Each commit message.
3. Every blob reachable from HEAD and not from BASE, read whole. This catches content no patch
   view shows.

A blob that carries an identifier no more often than BASE's version of the same file does is
counted as inherited: that exposure is BASE's, and does not fail the scan.

Besides markers(), the scan looks for the email domain, each name token (4+ letters) and what is
left of $USER once a name token is removed, because hand-written grep patterns spell those out.

Author and committer lines are reported apart. git user.name and user.email are expected there,
since every commit carries its author.

Exits 1 if any identifier is found in content or messages; metadata alone does not fail it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from pcim.adapters.cli_probe import markers as _machine_markers


def markers() -> dict[str, str]:
    """This machine's identifiers. SCAN_IDENTITY_MARKERS (a JSON file of label -> value) replaces
    them; only tests use it, and the scan says so when it does."""
    path = os.environ.get("SCAN_IDENTITY_MARKERS")
    if path:
        print(f"(identifiers read from {path}, not from this machine)", file=sys.stderr)
        return json.loads(open(path).read())
    return _machine_markers()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True,
                          errors="replace").stdout


def _marks() -> dict[str, str]:
    """markers(), plus the fragments a hand-written grep pattern tends to spell out."""
    base = {label: value.lower() for label, value in markers().items()}  # read once
    out = dict(base)
    email = base.get("account email") or base.get("git user.email") or ""
    if "@" in email and len(email.split("@", 1)[1]) >= 6:
        domain = email.split("@", 1)[1]
        out["email domain"] = domain
        if len(domain.split(".")[0]) >= 6:
            out["email domain label"] = domain.split(".")[0]
    names = {t for k in ("account full name", "account display name", "git user.name")
             for t in base.get(k, "").replace(".", " ").split() if len(t) >= 4}
    for i, t in enumerate(sorted(names)):
        out[f"name token {i + 1}"] = t
    user = base.get("username ($USER)", "")
    for t in names:
        rest = user.replace(t, "")
        if rest != user and len(rest) >= 4:
            out["username fragment"] = rest
    return out


def _inherited(base: str, path: str, value: str) -> int:
    """How often BASE's version of PATH already contains VALUE (0 if BASE has no such path)."""
    r = subprocess.run(["git", "show", f"{base}:{path}"], capture_output=True)
    return r.stdout.decode("utf-8", errors="replace").lower().count(value) if r.returncode == 0 else 0


def scan_diff(base: str) -> int:
    diff = _git("diff", base)
    marks = _marks()
    hits = {label: 0 for label in marks}
    files: dict[str, set[str]] = {}
    current = None
    added = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            continue
        if not line.startswith("+"):
            continue
        added += 1
        low = line[1:].lower()
        for label, value in marks.items():
            n = low.count(value)
            if n:
                hits[label] += n
                if current:
                    files.setdefault(label, set()).add(current)
    print(f"scanned {added} added lines against {base}")
    for label in sorted(hits):
        where = f"   in: {', '.join(sorted(files.get(label, ())))}" if hits[label] else ""
        print(f"  {label:22} {hits[label]}{where}")
    return 1 if any(hits.values()) else 0


def scan_history(base: str, head: str = "HEAD") -> int:
    marks = _marks()
    commits = _git("rev-list", "--reverse", f"{base}..{head}").split()
    content: dict[str, dict[str, set[str]]] = {}   # label -> commit -> files
    message: dict[str, set[str]] = {}               # label -> commits
    meta: dict[str, int] = {}                       # label -> commits whose author/committer match
    for sha in commits:
        who = _git("show", "-s", "--format=%an%n%ae%n%cn%n%ce", sha).lower()
        msg = _git("show", "-s", "--format=%B", sha).lower()
        patch = _git("show", "--format=", "--diff-merges=dense-combined", "--no-color", sha)
        current = None
        for line in patch.splitlines():
            if line.startswith("+++ "):
                current = line[6:] if line.startswith("+++ b/") else None
                continue
            if line.startswith("++") and current:          # a combined-diff line: '++' or '+ '/' +'
                low = line[2:].lower()
            elif line.startswith("+") and current:
                low = line[1:].lower()
            elif line.startswith(" +") and current:         # combined diff, added against one parent
                low = line[2:].lower()
            else:
                continue
            for label, value in marks.items():
                if value in low:
                    content.setdefault(label, {}).setdefault(sha, set()).add(current)
        for label, value in marks.items():
            if value in msg:
                message.setdefault(label, set()).add(sha)
            if value in who:
                meta[label] = meta.get(label, 0) + 1

    # every blob reachable from HEAD but not from BASE, read whole
    objs = _git("rev-list", "--objects", f"{base}..{head}").splitlines()
    blob_paths: dict[str, str] = {}
    for row in objs:
        parts = row.split(" ", 1)
        if len(parts) == 2:
            blob_paths[parts[0]] = parts[1]
    kinds = subprocess.run(["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
                           input="\n".join(blob_paths), capture_output=True, text=True, check=True).stdout
    blobs = [ln.split()[0] for ln in kinds.splitlines() if ln.endswith(" blob")]
    blob_hits: dict[str, set[str]] = {}
    inherited: dict[str, set[str]] = {}
    for sha in blobs:
        data = subprocess.run(["git", "cat-file", "blob", sha], capture_output=True, check=True).stdout
        low = data.decode("utf-8", errors="replace").lower()
        for label, value in marks.items():
            n = low.count(value)
            if n:
                path = blob_paths[sha]
                # a file that already carries it on BASE, no more often, is BASE's exposure
                target = inherited if n <= _inherited(base, path, value) else blob_hits
                target.setdefault(label, set()).add(path)

    short = lambda s: s[:7]  # noqa: E731
    print(f"history of {base}..{head}: {len(commits)} commits, {len(blobs)} blobs new to the branch")
    failed = False
    for label in sorted(marks):
        c = content.get(label, {})
        m = message.get(label, set())
        b = blob_hits.get(label, set())
        print(f"  {label:22} content: {len(c)} commits   messages: {len(m)}   "
              f"blobs: {len(b)} (+{len(inherited.get(label, ()))} as on {base})   "
              f"author/committer: {meta.get(label, 0)}")
        for sha in sorted(c, key=commits.index):
            print(f"      {short(sha)} adds it in: {', '.join(sorted(c[sha]))}")
        for sha in sorted(m, key=commits.index):
            print(f"      {short(sha)} message")
        for path in sorted(b):
            print(f"      blob: {path}")
        failed |= bool(c or m or b)
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    history = "--history" in argv
    head = "HEAD"
    if "--head" in argv:
        i = argv.index("--head")
        head = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    rest = [a for a in argv if a != "--history"]
    base = rest[0] if rest else "origin/main"
    return scan_history(base, head) if history else scan_diff(base)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
