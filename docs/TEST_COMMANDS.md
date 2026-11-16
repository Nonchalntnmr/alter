# Reproducible test commands, `feat/intent-compiler`

All commands assume the shared virtualenv and test **the worktree's `src/`**, never the
installed package. (The venv's editable install points at the main checkout, so without
`PYTHONPATH=src` you would be testing `main`.)

```sh
PY=~/projects/contextual-intelligence/.venv/bin/python
BR=~/projects/ci-intent                 # this branch
export HF_HUB_OFFLINE=1                 # the embedding model is cached; see the security report
```

## 1. Unit and integration tests (no model calls, no network)

```sh
cd $BR
$PY -c "import sys; sys.path.insert(0,'src'); import pcim; print(pcim.__file__)"   # must print $BR/src/...
PYTHONPATH=src $PY -m pytest -q -p no:cacheprovider
# expected: 452 passed, 2 xfailed
#   xfail 1 (from main): M10, deleting a project keeps its `ci` requests
#   xfail 2 (this branch, strict): the workspace agent does not recover by itself after Stop-all
```

The new work only:

```sh
PYTHONPATH=src $PY -m pytest -q -p no:cacheprovider \
  tests/test_intent_compiler.py tests/test_intent_preservation.py \
  tests/test_plan_exec.py tests/test_mesh.py tests/test_terminal_twin_audit.py
# expected: 108 passed, 1 xfailed   (20 + 58 + 19 and 1 xfail + 8 + 3)
```

## 2. Regression against `main`

```sh
git -C $BR fetch origin
git worktree add --detach ~/projects/ci-main-baseline origin/main   # once; read-only use

cd ~/projects/ci-main-baseline
PYTHONPATH=src $PY -m pytest -q -p no:cacheprovider
# expected on main itself: 344 passed, 1 xfailed

PYTHONPATH=$BR/src $PY -m pytest -q -p no:cacheprovider
# main's tests against the branch code. Expected: 343 passed, 1 xfailed, 1 failed:
#   test_amplify.py::test_modes_never_change_autonomy_permissions_or_routing_filters
#   intended: the branch adds a fifth, internal `plan` mode that cannot be set as a default,
#   and that test enumerates the settable modes.

cd $BR && git merge-tree --write-tree HEAD origin/main >/dev/null && echo "merges cleanly"
```

## 3. Held-out intent evaluation (Mission 2)

Re-scoring from the committed raw replies makes no model calls:

```sh
cd $BR
mkdir -p ~/projects/ci-sealed
cp experiments/intent_heldout/heldout_set_v1.json ~/projects/ci-sealed/intent_heldout_v1.json
$PY scripts/intent_heldout_eval.py score        # checks the set hash first; writes results.json
```

Re-running the live passes needs the logged-in `claude` CLI, about 100 calls (two passes of 50),
and the two code versions checked out exactly:

```sh
git worktree add --detach ~/projects/ci-intent-prev ff74305
PYTHONPATH=~/projects/ci-intent-prev/src $PY scripts/intent_heldout_eval.py run old
PYTHONPATH=$BR/src                       $PY scripts/intent_heldout_eval.py run new
```

The runner refuses to start if the set, pre-registration, protocol or runner hashes differ
from `experiments/intent_heldout/FROZEN.txt`, or if either `src/` differs from its commit.
Delete `raw_<pass>.jsonl` first. The runner appends, and a live re-run is a new sample, not a
replication of the same replies.

**Post hoc replay** (not a registered analysis, no model calls): the deterministic layer of a later
compiler version is run on the stored V2 replies. The first command must report no differences,
because it uses the code the run used. That validates the replay.

```sh
git -C $BR archive 58348cc src | tar -x -C /tmp/v2src     # mkdir /tmp/v2src first
PYTHONPATH=/tmp/v2src/src $PY scripts/intent_heldout_replay.py v2_check "58348cc"   # expect: no differences
PYTHONPATH=src            $PY scripts/intent_heldout_replay.py v2_1 "V2.1"           # expect: h40 and h49 only
```

**The real-word list** used by V2.1's typo rule (needs the cached all-MiniLM-L6-v2 model):

```sh
PYTHONPATH=src $PY scripts/gen_real_word_neighbours.py   # prints the 151-word _REAL_WORDS constant
```

## 4. Paired planning experiment (Mission 3)

```sh
cd $BR
PYTHONPATH=src $PY scripts/paired_eval.py run      # live; see experiments/paired/ for the budget
PYTHONPATH=src $PY scripts/paired_eval.py judge    # live, claude-cli:haiku, E7/E8 for arm A only
PYTHONPATH=src $PY scripts/paired_eval.py score    # offline
```

The harness checks `experiments/paired/FROZEN.txt` (task-set, pre-registration and harness
hashes, plus the code commit) before any call.
