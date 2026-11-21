## ALTER 0.3.0a1 (alpha): one command that understands, remembers, acts, verifies and learns

**Status:**
- **Alpha.** It works end to end through the installed `alter`.
- **Gate stays CONDITIONAL.** There is no human evaluation.
- **No performance claim** over direct model execution: eval_v5 and eval_v6 showed none.
- **Product map:** `docs/ALTER.md` lists what is built and what is not.

### What is in it

| piece | what it does |
|---|---|
| `alter` (`src/pcim/alter.py`) | A **conversation**: goals run in a worker, so you can keep talking: steer, "status", "pause", "yes" / "no". **Subcommands:** init, status, context, goals, tasks, approve / deny, memory, permissions, vault, weekly, doctor and ask. **Every `ci` command** passes through, and `ci` is unchanged. |
| **Goals** (`goals.py`) | **State:** durable and sealed at rest, with a checkpoint for each agent turn. **Talking to it:** steer, pause and cancel are read before every model step (`Agent.pause_check`). **Liveness** comes from an OS lock. **Limits:** turns, cost and a time window. **Done** means the project's tests passed after the last change, judged from the task records. |
| **Third Eye** (`thirdeye.py`) | A cited brief. **Remembered:** ALTER's goals and notes, filtered by vendor. **Sourced:** git, GitHub (opt-in) and the vault (opt-in). Anything left out is listed. |
| **Vault** (`vault.py`) | The vault's own `nos` ranking, unchanged, with identical eval numbers. iCloud placeholders are fetched with a time bound. |
| **Tools** (`tools.py`, `agent.py`) | **Reads** of large files come in labelled parts. **`write_file`** can replace one exact passage. **A cut tool result** says that it was cut. |
| **Outcome memory** | An episode computed from task records, with provenance, under an explicit grant. |
| **Written by ALTER** | `twin_do`: the twin may count `ci do` outcomes. It is opt-in and counts only (two commits, reviewed by hand). |

### Evidence
- **Gates:**
  - `scripts/check.sh` passes: 654 passed, 4 skipped;
  - `e2e_terminal.py` passes 37 / 37 on the installed wheel;
  - a clean install from a fresh shell works, and `alter doctor` exits 0.
- **Live runs** through the installed `alter` with the real `claude` CLI, on a worktree of this
  repository:
  - two goals ended **verified**;
  - one was steered while it ran;
  - one was paused and resumed;
  - one continued from ALTER's own records;
  - one exposed two bugs, both fixed.
  - The table is in `docs/release/READINESS.md`.
- **An independent review** in two passes:
  - **Pass 1:** 18 issues, 4 of them security. All are fixed, and 17 of their 18 regression tests
    failed on the reviewed code.
  - **Pass 2:** confirmed 13 fixes and found 8 more items, including one new bug that a pass-1 fix
    introduced: cancel could resume the model. All 8 are fixed, and all 8 tests failed before the
    fix (`tests/test_alter_review.py`).
- **Identity scan of the branch history:** clean. One identifier ALTER wrote into a test was
  redacted before this push.

### Before merging
- CI green on this PR.
- Both review passes are resolved; the gate is 654 passed, 4 skipped.

### Not in this PR
- **No package is published** and **no GitHub release** is made. The repository is public, so a
  release is the owner's call.
- **`main`'s old history** is not rewritten.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
