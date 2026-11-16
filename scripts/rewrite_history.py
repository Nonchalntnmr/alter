"""Remove this machine's identifiers from unpushed history, safely. Run from the repository.

    PYTHONPATH=src python scripts/rewrite_history.py plan    [--base origin/main]
    PYTHONPATH=src python scripts/rewrite_history.py dry-run --work DIR [--base origin/main]
    PYTHONPATH=src python scripts/rewrite_history.py apply   --work DIR
    PYTHONPATH=src python scripts/rewrite_history.py rollback --stamp STAMP

plan      Read-only. It names the commits that add an identifier, the local branches that contain
          them, and the worktrees that have those branches checked out. Commits whose only
          identifier is a name are reported but not rewritten unless --include-names is given.
          The reason: main's README and pyproject already carry the owner's name as attribution,
          and main's tests use the display name as a user id. It refuses when an
          offending commit is already on a remote-tracking ref, because a local rewrite cannot
          unpublish that.
dry-run   Mirror-clones the repository into DIR and rewrites the affected branches there with
          git-filter-repo, then verifies the result. The real repository is not touched. The
          verification is:
          - the history scan of every rewritten branch is clean;
          - each branch keeps the same number of commits;
          - every rewritten commit differs from its original only in the redacted files;
          - every commit's src/ tree is byte-identical, so frozen-code evidence still holds;
          - author and committer dates are unchanged.
apply     Needs the verified dry-run in DIR. It first checks that no branch moved since the
          dry-run and that every affected worktree is clean. Then:
          - it writes a bundle of the affected branches into DIR;
          - it records refs/backup/<stamp>/<branch>;
          - it moves each branch with a compare-and-swap update-ref;
          - it refreshes only the files that changed in each affected worktree.
          The old commit -> new commit map is written to DIR/commit-map.txt.
rollback  Moves every branch back to refs/backup/<stamp>/<branch>, again compare-and-swap.

Replacements, built at run time from the same identifiers scripts/scan_identity.py uses, so none
of them is written into the repository:
- the home path becomes <parent>/<user>, the token the earlier redaction (cc5d33a) used;
- any grep pattern in quotes that spells out an identifier becomes
  "<identifier pattern removed; use scripts/scan_identity.py>".
The expressions file lives in DIR with mode 600. Delete DIR once you have finished.

Never push refs/backup/*: those refs still hold the old history.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scan_identity  # noqa: E402

PATTERN_TOKEN = "<identifier pattern removed; use scripts/scan_identity.py>"


def git(*args: str, cwd: str | Path | None = None, check: bool = True, inp: str | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace",
                       input=inp)
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def filter_repo() -> str:
    exe = os.environ.get("FILTER_REPO") or shutil.which("git-filter-repo")
    if not exe:
        raise SystemExit("git-filter-repo not found: pip install git-filter-repo (into any venv), "
                         "then set FILTER_REPO=<path to git-filter-repo>")
    return exe


NAME_LABELS = ("account display name", "account full name", "git user.name")


def is_name(label: str) -> bool:
    """Names are the owner's public attribution: main's README and pyproject already carry them,
    and main's tests use the display name as a user id. They are reported, not rewritten."""
    return label in NAME_LABELS or label.startswith("name token")


def offending(base: str, cwd: str | Path | None = None) -> dict[str, dict[str, set[str]]]:
    """commit -> file -> labels, for every commit on a local branch (not on BASE) whose own added
    lines or message carry an identifier."""
    marks = scan_identity._marks()
    out: dict[str, dict[str, set[str]]] = {}
    for sha in git("rev-list", "--branches", "--not", base, cwd=cwd).split():
        patch = git("show", "--format=%B%n@@@END-MESSAGE@@@", "--diff-merges=dense-combined",
                    "--no-color", sha, cwd=cwd)
        msg, _, diff = patch.partition("@@@END-MESSAGE@@@")
        for label, v in marks.items():
            if v in msg.lower():
                out.setdefault(sha, {}).setdefault("(commit message)", set()).add(label)
        current = None
        for line in diff.splitlines():
            if line.startswith("+++ "):
                current = line[6:] if line.startswith("+++ b/") else None
            elif current and "+" in line[:2] and not line.startswith("+++"):
                low = line.lower()
                for label, v in marks.items():
                    if v in low:
                        out.setdefault(sha, {}).setdefault(current, set()).add(label)
    return out


def severe(bad: dict[str, dict[str, set[str]]]) -> dict[str, dict[str, set[str]]]:
    """The commits that carry something other than a name."""
    return {sha: files for sha, files in bad.items()
            if any(not is_name(lb) for labels in files.values() for lb in labels)}


def affected(base: str, cwd: str | Path | None = None, names: bool = False):
    bad = offending(base, cwd)
    target = bad if names else severe(bad)
    branches = sorted({b.strip().lstrip("*+ ").strip() for sha in target
                       for b in git("branch", "--contains", sha, cwd=cwd).splitlines() if b.strip()})
    branches = [b for b in branches if not b.startswith("(")]
    trees: dict[str, str] = {}
    wt = None
    for line in git("worktree", "list", "--porcelain", cwd=cwd).splitlines():
        if line.startswith("worktree "):
            wt = line[9:]
        elif line.startswith("branch refs/heads/") and wt:
            name = line[len("branch refs/heads/"):]
            if name in branches:
                trees[name] = wt
    return bad, target, branches, trees


def check_unpushed(bad: dict[str, set[str]], cwd: str | Path | None = None) -> None:
    for sha in bad:
        remote = git("branch", "-r", "--contains", sha, cwd=cwd).strip()
        if remote:
            raise SystemExit(f"{sha[:7]} is already on {remote.split()[0]}: a local rewrite cannot "
                             "unpublish it. That needs a force-push, which is the owner's decision.")


def cmd_plan(base: str, names: bool = False) -> int:
    bad, target, branches, trees = affected(base, names=names)
    check_unpushed(target)
    print(f"base: {base}")
    print(f"commits that add an identifier ({len(bad)}; {len(target)} to rewrite):")
    for sha in git("rev-list", "--reverse", "--branches", "--not", base).split():
        if sha in bad:
            subj = git("show", "-s", "--format=%ad %s", "--date=short", sha).strip()
            tag = "rewrite" if sha in target else "names only, as on main: reported, not rewritten"
            print(f"  {sha[:7]}  {subj[:84]}\n           [{tag}]")
            for f, labels in sorted(bad[sha].items()):
                print(f"           {f}: {', '.join(sorted(labels))}")
    print(f"local branches to rewrite ({len(branches)}):")
    for b in branches:
        n = len(git("rev-list", f"{base}..{b}").split())
        print(f"  {b:34} {n} commits after {base}" + (f"   worktree: {trees[b]}" if b in trees else ""))
    for block in git("worktree", "list", "--porcelain").strip().split("\n\n"):
        rows = block.splitlines()
        if rows and "detached" in rows:
            head = next((r[5:] for r in rows if r.startswith("HEAD ")), "")
            if any(subprocess.run(["git", "merge-base", "--is-ancestor", sha, head]).returncode == 0
                   for sha in target):
                print(f"  detached worktree at an affected commit (left alone; it keeps the old "
                      f"objects alive locally, which is harmless unless pushed): {rows[0][9:]}")
    print("none of the commits to rewrite is on a remote-tracking ref")
    return 0


def expressions() -> str:
    marks = scan_identity._marks()
    home = scan_identity.markers().get("home path") or str(Path.home())   # original case
    lines = [f"{home}==>{str(Path(home).parent)}/<user>"]
    toks = sorted({re.escape(v) for v in marks.values() if v and v != home.lower()}, key=len, reverse=True)
    if toks:
        # only hand-written grep patterns: a test's quoted user id must not change
        lines.append(f'regex:(?i)grep -ciE "[^"\\n]*(?:{"|".join(toks)})[^"\\n]*"==>grep -ciE "{PATTERN_TOKEN}"')
    return "\n".join(lines) + "\n"


def offending_range(base: str, head: str) -> dict[str, dict[str, set[str]]]:
    """offending(), for one range, in the current directory's repository."""
    marks = scan_identity._marks()
    out: dict[str, dict[str, set[str]]] = {}
    for sha in git("rev-list", f"{base}..{head}").split():
        patch = git("show", "--format=%B%n@@@END-MESSAGE@@@", "--diff-merges=dense-combined", "--no-color", sha)
        msg, _, diff = patch.partition("@@@END-MESSAGE@@@")
        for label, v in marks.items():
            if v in msg.lower():
                out.setdefault(sha, {}).setdefault("(commit message)", set()).add(label)
        current = None
        for line in diff.splitlines():
            if line.startswith("+++ "):
                current = line[6:] if line.startswith("+++ b/") else None
            elif current and "+" in line[:2] and not line.startswith("+++"):
                for label, v in marks.items():
                    if v in line.lower():
                        out.setdefault(sha, {}).setdefault(current, set()).add(label)
    return out


def tips(branches: list[str], cwd: str | Path | None = None) -> dict[str, str]:
    return {b: git("rev-parse", f"refs/heads/{b}", cwd=cwd).strip() for b in branches}


def cmd_dry_run(base: str, work: Path, names: bool = False) -> int:
    repo = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    _, bad, branches, trees = affected(base, names=names)
    check_unpushed(bad)
    if not branches:
        print("nothing to rewrite")
        return 0
    work.mkdir(parents=True, exist_ok=True)
    mirror = work / "mirror.git"
    if mirror.exists():
        shutil.rmtree(mirror)
    git("clone", "--mirror", "--quiet", str(repo), str(mirror))
    old = tips(branches, mirror)
    base_sha = git("rev-parse", base).strip()
    git("update-ref", "refs/rewrite-base", base_sha, cwd=mirror)
    exprs = work / "expressions.txt"
    exprs.write_text(expressions())
    os.chmod(exprs, 0o600)
    olds = {b: git("rev-list", f"{base_sha}..{old[b]}", cwd=mirror).split() for b in branches}
    refs = [f"refs/heads/{b}" for b in branches]
    cmd = [filter_repo(), "--force", "--refs", *refs, "^refs/rewrite-base",
           "--replace-text", str(exprs), "--replace-message", str(exprs),
           "--prune-empty", "never", "--prune-degenerate", "never"]
    r = subprocess.run(cmd, cwd=mirror, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"filter-repo failed:\n{r.stderr[-2000:]}")
    new = tips(branches, mirror)
    cmap = {}
    for line in (mirror / "filter-repo" / "commit-map").read_text().splitlines()[1:]:
        o, n = line.split()
        cmap[o] = n
    redacted = {f for files in bad.values() for f in files if f != "(commit message)"}

    problems: list[str] = []
    cwd0 = os.getcwd()
    os.chdir(mirror)
    try:
        for b in branches:
            left = offending_range(base_sha, f"refs/heads/{b}")
            left = {sha: f for sha, f in left.items() if names or
                    any(not is_name(lb) for labels in f.values() for lb in labels)}
            if left:
                problems.append(f"{b}: identifiers remain in {sorted(x[:7] for x in left)}")
    finally:
        os.chdir(cwd0)
    for b in branches:
        n_new = len(git("rev-list", f"{base_sha}..{new[b]}", cwd=mirror).split())
        if n_new != len(olds[b]):
            problems.append(f"{b}: {len(olds[b])} commits before, {n_new} after")
    m = {"cwd": mirror}  # a --refs rewrite does not gc, so the old commits are still in the mirror
    for o in sorted({c for cs in olds.values() for c in cs}):
        n = cmap.get(o)
        if not n or set(n) == {"0"}:
            problems.append(f"{o[:7]} has no rewritten commit")
            continue
        if o == n:
            continue
        if subprocess.run(["git", "cat-file", "-e", o], cwd=mirror).returncode != 0:
            problems.append(f"{o[:7]}: the original is gone from the mirror, so it cannot be compared")
            continue
        changed = set(git("diff", "--name-only", o, n, **m).split())
        if not changed <= redacted:
            problems.append(f"{o[:7]} -> {n[:7]} also changes {sorted(changed - redacted)}")
        if git("rev-parse", "-q", "--verify", f"{o}:src", check=False, **m).strip() != \
                git("rev-parse", "-q", "--verify", f"{n}:src", check=False, **m).strip():
            problems.append(f"{o[:7]} -> {n[:7]}: src/ tree differs")
        fmt = "--format=%ad|%cd|%an|%ae|%cn|%ce|%P"
        po, pn = git("show", "-s", fmt, o, **m).split("|"), git("show", "-s", fmt, n, **m).split("|")
        if po[:6] != pn[:6]:
            problems.append(f"{o[:7]} -> {n[:7]}: dates or identities differ")
        if [cmap.get(x, x) for x in po[6].split()] != pn[6].split():
            problems.append(f"{o[:7]} -> {n[:7]}: parents do not map")
    rewritten = len({c for cs in olds.values() for c in cs if cmap.get(c, c) != c})
    state = {"base": base_sha, "branches": branches, "old": old, "new": new, "verified": not problems,
             "worktrees": trees, "redacted_files": sorted(redacted), "made": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (work / "state.json").write_text(json.dumps(state, indent=1))
    (work / "commit-map.txt").write_text("".join(f"{o} {cmap[o]}\n" for o in sorted(cmap)))
    print(f"\ndry run in {mirror}: {len(branches)} branches, {len(cmap)} commits mapped, "
          f"{rewritten} distinct commits rewritten; files redacted: {', '.join(sorted(redacted))}")
    for b in branches:
        print(f"  {b:34} {old[b][:7]} -> {new[b][:7]}")
    if problems:
        print("VERIFICATION FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("verified: scans clean; same commit counts; only the redacted files change; every src/ "
          "tree identical; dates and names kept. The real repository was not touched.")
    print(f"next, if the owner approves: PYTHONPATH=src python scripts/rewrite_history.py apply --work {work}")
    return 0


def _refresh_worktree(path: str, old: str, new: str) -> None:
    files = [f for f in git("diff", "--name-only", old, new).split() if f]
    if files:
        git("checkout", new, "--", *files, cwd=path)


def cmd_apply(work: Path) -> int:
    state = json.loads((work / "state.json").read_text())
    if not state.get("verified"):
        raise SystemExit("the dry run did not verify; nothing applied")
    mirror = work / "mirror.git"
    branches, old, new = state["branches"], state["old"], state["new"]
    now = tips(branches)
    moved = [b for b in branches if now[b] != old[b]]
    if moved:
        raise SystemExit(f"branches moved since the dry run: {moved}. Run dry-run again.")
    for b, wt in state["worktrees"].items():
        if git("status", "--porcelain", cwd=wt).strip():
            raise SystemExit(f"worktree {wt} ({b}) has uncommitted changes; commit or stash them first")
    stamp = time.strftime("pre-rewrite-%Y%m%d-%H%M%S")
    bundle = work / f"{stamp}.bundle"
    git("bundle", "create", str(bundle), *[f"refs/heads/{b}" for b in branches])
    git("bundle", "verify", str(bundle))
    for b in branches:
        git("update-ref", f"refs/backup/{stamp}/{b}", old[b])
    git("fetch", "--quiet", str(mirror), *[f"refs/heads/{b}:refs/rewritten/{stamp}/{b}" for b in branches])
    for b in branches:
        git("update-ref", "-m", f"rewrite_history {stamp}", f"refs/heads/{b}", new[b], old[b])
        git("update-ref", "-d", f"refs/rewritten/{stamp}/{b}")
    for b, wt in state["worktrees"].items():
        _refresh_worktree(wt, old[b], new[b])
    print(f"applied. stamp: {stamp}\n  bundle: {bundle}\n  backup refs: refs/backup/{stamp}/*\n"
          f"  commit map: {work / 'commit-map.txt'}")
    for b in branches:
        print(f"  {b:34} {old[b][:7]} -> {new[b][:7]}")
    print(f"rollback: PYTHONPATH=src python scripts/rewrite_history.py rollback --stamp {stamp}")
    print("Never push refs/backup/*. Delete them and the bundle once you are satisfied.")
    return 0


def cmd_rollback(stamp: str) -> int:
    refs = git("for-each-ref", "--format=%(refname)", f"refs/backup/{stamp}/").split()
    if not refs:
        raise SystemExit(f"no refs under refs/backup/{stamp}/")
    wts = {}
    wt = None
    for line in git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            wt = line[9:]
        elif line.startswith("branch refs/heads/") and wt:
            wts[line[len("branch refs/heads/"):]] = wt
    for ref in refs:
        b = ref[len(f"refs/backup/{stamp}/"):]
        cur = git("rev-parse", f"refs/heads/{b}").strip()
        back = git("rev-parse", ref).strip()
        if b in wts and git("status", "--porcelain", cwd=wts[b]).strip():
            raise SystemExit(f"worktree {wts[b]} ({b}) has uncommitted changes")
        git("update-ref", "-m", f"rewrite_history rollback {stamp}", f"refs/heads/{b}", back, cur)
        if b in wts:
            _refresh_worktree(wts[b], cur, back)
        print(f"  {b:34} {cur[:7]} -> {back[:7]}")
    print("rolled back; the backup refs are kept")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("plan", "dry-run", "apply", "rollback"):
        print(__doc__)
        return 2
    names = "--include-names" in argv
    argv = [a for a in argv if a != "--include-names"]
    opts = dict(zip(argv[1::2], argv[2::2]))
    base = opts.get("--base", "origin/main")
    if argv[0] == "plan":
        return cmd_plan(base, names)
    if argv[0] == "dry-run":
        return cmd_dry_run(base, Path(opts["--work"]).resolve(), names)
    if argv[0] == "apply":
        return cmd_apply(Path(opts["--work"]).resolve())
    return cmd_rollback(opts["--stamp"])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
