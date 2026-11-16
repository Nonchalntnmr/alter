# Reproducible test commands, `integration/intent-compiler`

This branch is `origin/main` (1f9bd08) plus three merged branches:
- `feat/intent-compiler`: V2.1, the held-out and paired evidence, and the docs;
- `feat/intent-v3.1`: V3, the review fixes, V3.1, and `sec/cli-identity`.

All commands assume the shared virtualenv and test **the worktree's `src/`**, never the installed
package. (The venv's editable install points at the main checkout, so without `PYTHONPATH=src`
you would be testing `main`.)

```sh
PY=~/projects/contextual-intelligence/.venv/bin/python
BR=~/projects/ci-integration            # this branch
export HF_HUB_OFFLINE=1                 # the embedding model is cached; see the security report
```

## 1. Unit and integration tests (no model calls, no network)

```sh
cd $BR
$PY -c "import sys; sys.path.insert(0,'src'); import pcim; print(pcim.__file__)"   # must print $BR/src/...
PYTHONPATH=src $PY -m pytest -q -p no:cacheprovider
# expected: 499 passed, 1 skipped, 1 xfailed
#   skipped: the live identity probe (opt-in, see section 6)
#   xfail (from main, strict): M10, deleting a project keeps its `ci` requests
#   (the Stop-all wedge xfail is gone: agent.py now recovers, and the test passes strictly)
```

The new work only:

```sh
PYTHONPATH=src $PY -m pytest -q -p no:cacheprovider \
  tests/test_intent_compiler.py tests/test_intent_preservation.py tests/test_plan_exec.py \
  tests/test_mesh.py tests/test_terminal_twin_audit.py tests/test_cli_identity.py
# expected: 155 passed, 1 skipped   (37 + 66 + 26 + 8 + 3 + 15 and 1 skipped)
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

## 5. End-to-end check of the `ci` commands (Mission 8, no model calls)

```sh
cd $BR && PYTHONPATH=src $PY scripts/e2e_terminal.py
# expected: 32 of 32 checks passed
```

**What it runs.** Every `ci` command goes through the real entry point in a subprocess, with a
temporary `PCIM_HOME` and a temporary git repo authorized as a project. `claude` is a scripted
stand-in on `PCIM_CLAUDE_BIN`.

**What it covers:**
- `plan`, `plan approve`, `execute` (pausing for approval), `approve`, `deny`, `--done`,
  `--retry` and `--cancel`;
- restart recovery: a killed `ci execute` reads `interrupted`, never re-runs by itself, and
  `--retry` re-runs it;
- Stop-all: `halt` cancels, approving afterwards fails, a retry during Stop-all is refused, and
  `resume` then retry works;
- permission boundaries: a write outside the folder never happens, a plan does not run from
  another project, and reading outside an authorized folder is blocked;
- a provider failure;
- `mesh`, `twin`, `audit` and `learn`.

On V3 as frozen (9efe636) it gives 30 of 32. The two failures are fixed in V3.1 (d9e281b).

## 6. CLI identity isolation (security branch, merged)

```sh
cd $BR && PYTHONPATH=src $PY -m pytest -q tests/test_cli_identity.py             # offline: 15 passed, 1 skipped
PCIM_LIVE_CLI_PROBE=1 PYTHONPATH=src $PY -m pytest -q tests/test_cli_identity.py -k live   # 3 Haiku calls
PYTHONPATH=src $PY -m pcim.adapters.cli_probe --trials 4 --record                 # measure and record this CLI
```

**How the probe measures.** It records the request the CLI actually sends, through a loopback
relay that forwards to Anthropic.
- It refuses to record a clean result unless its baseline call, with isolation off, shows the
  email: that is the positive control.
- Identity-sensitive work sets `PCIM_CLI_REQUIRE_ISOLATION=1`. Calls are then refused unless the
  installed CLI version is recorded clean.

## 7. V3 evaluation (new sets, pre-registered, frozen before reading)

The evaluation runs from the frozen worktrees: `~/projects/ci-intent-v3` for V3 (9efe636) and
`~/projects/ci-v21-isolated` for V2.1+ (53306b3). The harnesses check those commits, the set
hashes and identity isolation before any call.

```sh
cd ~/projects/ci-intent-v3
export HF_HUB_OFFLINE=1 PCIM_CLI_REQUIRE_ISOLATION=1
PYTHONPATH=~/projects/ci-v21-isolated/src $PY scripts/eval_v3_intent.py run v21
PYTHONPATH=~/projects/ci-intent-v3/src    $PY scripts/eval_v3_intent.py run v3
$PY scripts/eval_v3_intent.py score
PYTHONPATH=~/projects/ci-intent-v3/src    $PY scripts/eval_v3_paired.py run A,B3
PYTHONPATH=~/projects/ci-v21-isolated/src $PY scripts/eval_v3_paired.py run B21
PYTHONPATH=~/projects/ci-intent-v3/src    $PY scripts/eval_v3_paired.py judge
$PY scripts/eval_v3_paired.py score
```

Re-scoring from the committed raw files needs the sets. They are released into
`experiments/eval_v3/` after scoring. Copy them back to `~/projects/ci-sealed/` first; the hash
check guarantees they are the same sets.
