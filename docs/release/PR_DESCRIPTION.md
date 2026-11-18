# Alter 0.2.0rc2: restricted developer preview

**Head:** `release/alter-0.2.0rc2` **Base:** `main` (`1f9bd08`)
**Status:** for review. Pushed and opened by the owner's instruction. **Merging into `main` is the
owner's decision:** see "Before merging".

## Summary

This PR adds four things to the terminal:
- **`ci plan`:** typed outcomes, a deterministic gate, and approval before anything runs.
- **`ci execute`:** governed execution of an approved plan.
- **`ci do`:** governed work in an authorized folder. The keyword router is the default, and there
  are two opt-in modes: planning-assisted (`--route assisted`) and selective clarification
  (`--route selective`). `ci do` reports what was verified, apart from what the agent says.
- **Identity isolation** for the `claude` CLI.

It also bundles the evaluations behind each decision.

It is a **restricted developer preview**: the owner's machine, CLI 2.1.316, and an identity probe
recorded in that `PCIM_HOME`. **It makes no claim that routing or planning beats direct
execution.** The evidence so far does not show that (see Evidence).

## What changes, by area (327 files, +81,473 / −48; most of it evaluation records)

**`src/` (18 files, +3,479 / −44)**

| file | what |
|---|---|
| `intent_compiler.py` (new) | V3.4: request → typed spec (outcomes `by` ci / ci_with_approval / you), deterministic preservation of requested actions, the gate, the bounded re-ask. V3.4 keeps a plan to the request: no invented deletions, no unrequested backups, no re-asking a resolved conflict. These fix every eval_v5 planning failure. |
| `plan_exec.py` (new) | contract execution in one governed agent session; three independent states (outcome success, plan completion, authorization compliance); Stop-all, retry, resume |
| `router.py` (new) | `ci do` routing: the keyword router (the default), `route_from_plan` (planning-assisted, opt-in) and `SELECTIVE_DIRECTIVES` (selective clarification, opt-in) |
| `terminal.py` | `ci plan`, `execute`, `approve`, `deny`, `halt`, `resume`, `do` (with `answer`, `list`, `forget`), `doctor`, `audit`, `twin`, `mesh`, `learn` and `--version`. `ci do` says whether the tests passed after the last change, and flags a done claim that lacks that evidence. Typed ids resolve to exactly one plan or request. |
| `adapters/claude_cli.py`, `cli_probe.py` | identity isolation, measured on the request. The CLI's quiet switches are set on every call, so no third-party telemetry is sent. The probe record is written atomically. |
| `mesh.py` (new) | local compute discovery and placement |
| `agent.py`, `store.py`, `workspace.py`, `embed.py`, `modes.py`, `engine.py`, `tools.py` | the session lock across processes; the M10 fix (deleting a project deletes its requests); a clear error for a semantically embedded store opened without the `semantic` extra; plan mode; per-session directives; a local broken pipe is not blamed on the model; test runs write no bytecode (a same-size fix was otherwise tested as stale `.pyc`) |

**Other areas**

| area | what |
|---|---|
| `tests/` (19 files, +3,276) | 589 passed, 4 skipped |
| `scripts/` | the evaluation harnesses (v3, v4, v5, v6) and their report generators, `e2e_terminal.py` (37 checks), `scan_identity.py`, `rewrite_history.py`, `audit_egress.py`, `human_eval.py` |
| `experiments/` | pre-registrations, frozen hashes, raw records, results and the released sets, for the dev, held-out v1, paired v1, V3 (v3), rc2 (v4), eval_v5 and eval_v6 evaluations |
| `docs/` | `docs/release/` (readiness, release notes, install, config, runbook, rollback, smoke, history rewrite, human evaluation), `SECURITY_WHAT_LEAVES.md`, the reports |

## Evidence: what it does and does not show

**Intent compiler V3.3, on 50 unseen requests (v4):**
- It asks needlessly on 1/21 clear requests, against 10/21 before (p = 0.004).
- It asks when needed on 20/23 (unchanged).
- It keeps every requested action, and every plan is valid.

**Execution, on 16 unseen coding tasks (v4):**
- Direct and routed both verified 16/16, with 0 unauthorized changes.
- Routing changed 2 tasks, and **no advantage is claimed.**
- The keyword router misses most needed questions (4/21). That is why the planning-assisted
  router exists. It is being evaluated in eval_v5.

**eval_v5** (12 simulated users, 24 new tasks, 4 arms through the real CLI, 3 blinded judges):
- direct verified 24/24, the keyword router 24/24, V3.3 planning 21/24, planning-assisted routing
  22/24;
- **no arm is shown to be better than direct;**
- the planning arms cost 1.6–2.5× as much and asked needlessly on 6/21 clear requests;
- 0 successful unauthorized actions, and 8 denied, all from the planning arms.

Full results: `experiments/eval_v5/RESULTS.md`.

**eval_v6** (24 new tasks, half of which need a question by design; direct, the keyword router and
selective clarification):
- all three verified 24/24;
- first-time success: direct 18/24, the keyword router 17/24, selective 17/24, so **selective is
  not shown to be better than direct**;
- every arm asks on only about half the requests that need a question; direct already asks with
  precision 8/8;
- secondary, to be confirmed:
  - selective cost 25% less (p = 0.007) and was faster (median 34 s against 53 s);
  - it made 0 out-of-scope attempts, against direct's 2;
- `ci do`'s verification verdict agreed with the hidden check on every run.

Full results: `experiments/eval_v6/RESULTS.md`, with a separate post-hoc analysis in
`ANALYSIS.md`.

**Privacy (`docs/SECURITY_WHAT_LEAVES.md`):**
- No identifier is in the model input.
- No third-party destination is contacted.
- The account id is in API metadata, which duplicates the OAuth login.

**History:**
- Rewritten with the owner's approval. Every branch in this PR scans clean, and the old→new id map
  is in `docs/release/HISTORY_REWRITE_MAP.txt`.
- The author and committer lines carry git's configured name and email, as on `main`.

## Test plan, all run on the head

- `scripts/check.sh` (the CI rule set: ruff `E9,F63,F7,F82`, then pytest): 589 passed, 4 skipped. The skips
  are the opt-in live probe, plus the 3 rewrite tests when `git-filter-repo` is absent (they pass
  with it).
- `python scripts/e2e_terminal.py`: 37/37, on the checkout and on the installed wheel.
- `main`'s own tests against this code: 343 passed and 2 intended failures (`plan` is not a
  settable mode; M10's xfail now passes).
- Upgrade from `main`, and rollback to rc1 and to `main`, on the same data (`SMOKE.md` §5).
- `scripts/scan_identity.py` (the diff) and `--history`: clean.
- GitHub Actions on the head: ruff, plus pytest on Ubuntu 3.12, Ubuntu 3.14 and macOS 3.14, all green.
  CI installs no `semantic` extra. That exposed one crash in `ci mesh bench`, fixed in a9f3600 with a regression test.

## Reviewer guide

1. `docs/release/READINESS.md`: the gate and the open items.
2. `src/pcim/intent_compiler.py` `gate()` and `src/pcim/router.py`: the decisions.
3. `src/pcim/adapters/claude_cli.py` `_isolated_env`, `QUIET_ENV`: privacy.
4. `experiments/eval_v4/RESULTS.md`, `experiments/eval_v5/RESULTS.md` and `experiments/eval_v6/RESULTS.md`: the evidence.

## Before merging (the owner's decisions)

- **Whether the restricted preview should go ahead at all,** given that no performance advantage
  over direct execution has been shown.
- **The human evaluation** (`docs/release/HUMAN_EVAL.md`) is required before any broad release.
- **`main`'s own history** still holds the home path in 3 files from before this work. Removing
  it means a force-push to `main`, which is not part of this PR.

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)
