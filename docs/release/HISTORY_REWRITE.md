# History rewrite: remove this machine's identifiers before any push

**Status: prepared and rehearsed, not executed.** Executing it is the owner's decision. Nothing
here has been pushed.

## 1. What is in the history

`scripts/scan_identity.py --history` scans each commit's added lines, each commit message, and
every blob new to the branch. It reads the identifiers from this machine at run time, so no value
is written here.

Three unpushed commits put identifiers into the release lineage:

| commit | date | file | what it adds |
|---|---|---|---|
| `ff74305` | 2026-11-13 | `experiments/intent/results.json` | the home path (which contains the username), inside 3 model questions about the CLI's sandbox folder. `cc5d33a` redacted the file later, but the original blob is still in history. |
| `d6cc52f` | 2026-11-13 | `HANDOFF.md` | a hand-written `grep -ciE "…"` check that spells out identifier fragments: the email domain, name pieces and a username fragment |
| `b77dcc6` | 2026-11-16 | `HANDOFF.md` | the same kind of grep check, including the home path. `e822f44` carries it forward. |

These are the only commits that need the rewrite. The diff scan on the final tree
(`scan_identity.py`, with no `--history`) is clean, because the identifiers were removed later.
They are still in history.

**Reported but not rewritten: names.** Seven commits on parked branches use the owner's display
name as a test user id:
- `agent/api`, `agent/budget`, `agent/calibration`, `agent/local-models`, `agent/mcp` and
  `agent/repl`;
- `rescue/argon-integration-2026-11-11`.

`main`'s own tests use the same convention, and `main`'s README and `pyproject.toml` carry the
name as attribution. `rewrite_history.py --include-names` would rewrite these as well.

**Already on `origin/main`, so out of reach of a branch rewrite.** The home path is in three
files on `main`, which is already pushed:
- `experiments/milestone1/live-run3-2026-10-24.json`;
- `experiments/routing/learned_on_heldout.txt`;
- `experiments/routing/learned_on_sealed2.txt`.

Removing them from `main`'s history means a force-push to `main`, which is a separate decision for
the owner. The repository is private. A commit that only redacts those three files would stop
future copies, but the path would stay in the published history.

## 2. What the rewrite costs

- **New IDs from `ff74305` onward.** 48 of the 54 unpushed commits on the 7 affected branches get
  new IDs. Commit dates, author and committer identities, and the parent structure are kept.
- **Messages are remapped, file contents are not.** `git-filter-repo` rewrites commit IDs that
  commit messages cite. It does not touch IDs cited inside files, such as `FROZEN.txt`, the
  reports or `READINESS.md`. Those keep the old IDs, so the map from old to new commits must be
  committed afterwards (§4, step 5).
- **The frozen-code evidence is unchanged.** Every rewritten commit has a byte-identical `src/`
  tree, and the dry run checks this. A claim such as "src equals the frozen commit" can still be
  checked by tree hash, through the map.
- **Worktrees.** Seven worktrees have an affected branch checked out. `apply` refreshes only the
  files that changed in each one, and the dry run shows the only such files are `HANDOFF.md` and
  `experiments/intent/results.json`. The detached helper worktree `~/projects/ci-intent-prev` is
  left at the old `ff74305`. It keeps the old objects alive locally, which is harmless unless
  something pushes it.

## 3. Rehearsal (2026-11-16)

The `dry-run` was run against the real repository. It mirror-cloned the repository into the
scratch directory and rewrote the copy there.

| check | result |
|---|---|
| branches rewritten | 7: `eval/v21-isolated`, `feat/intent-compiler`, `feat/intent-v3`, `feat/intent-v3.1`, `integration/intent-compiler`, `release/alter-0.2.0`, `release/alter-0.2.0rc2` |
| history scan on each rewritten branch | clean on every label. An independent run of `scan_identity.py --history` on the rewritten `release/alter-0.2.0` also exits 0. |
| commit counts | unchanged on every branch |
| files that differ between each original commit and its rewrite | only `HANDOFF.md` and `experiments/intent/results.json` |
| `src/` tree of every rewritten commit | identical |
| dates, identities, parents | identical (parents through the map) |
| `release/alter-0.2.0` final tree | byte-identical to `dd3dcda`'s |
| real repository | untouched: branches unmoved, no temporary refs |

`tests/test_rewrite_history.py` runs the whole cycle on a throwaway repository with fake
identifiers. It covers:
- dry-run, apply and rollback;
- that names in test files are kept;
- that `apply` refuses after a branch has moved;
- that the dry run fails, and `apply` refuses, when an identifier survives the redaction.

The tests skip when `git-filter-repo` is not installed.

## 4. Procedure, for when the owner approves

Run from any worktree of the repository, with `PY=~/projects/contextual-intelligence/.venv/bin/python`.

```bash
# 0. the tool (one-off, any venv)
python3 -m venv /tmp/fr && /tmp/fr/bin/pip install git-filter-repo==2.47.0
export FILTER_REPO=/tmp/fr/bin/git-filter-repo PYTHONPATH=src HF_HUB_OFFLINE=1

# 1. read-only: what would be rewritten, and proof that none of it is pushed
$PY scripts/rewrite_history.py plan

# 2. rehearse on a mirror; the real repository is not touched. It must end with "verified".
$PY scripts/rewrite_history.py dry-run --work ~/ci-rewrite-$(date +%Y%m%d)

# 3. apply. It refuses if a branch moved since step 2 or a worktree is dirty.
#    It writes a bundle and refs/backup/<stamp>/* before it moves anything.
$PY scripts/rewrite_history.py apply --work ~/ci-rewrite-$(date +%Y%m%d)

# 4. verify on the real repository
for b in release/alter-0.2.0rc2 release/alter-0.2.0 integration/intent-compiler; do
  $PY scripts/scan_identity.py --history origin/main --head "$b"; done

# 5. keep old citations resolvable: commit the map
cp ~/ci-rewrite-*/commit-map.txt docs/release/HISTORY_REWRITE_MAP.txt
git add docs/release/HISTORY_REWRITE_MAP.txt && git commit -m "Map of commit IDs before and after the history rewrite"
```

**Rollback.** At any point after step 3:

```bash
$PY scripts/rewrite_history.py rollback --stamp <stamp printed by apply>
```

The bundle in the work directory is a second, independent copy:
`git fetch <bundle> 'refs/heads/*:refs/restored/*'`.

**Afterwards:**
- Never push `refs/backup/*`, because those refs still hold the old history. Delete them, the
  bundle and the work directory once you are satisfied.
- Run `git push` with explicit branch names only; never use `--mirror` or `--all`.
