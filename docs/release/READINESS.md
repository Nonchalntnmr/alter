# Release readiness

## ALTER V1 release candidate, 2026-11-22 (branch `release/v1-gate`)

**Gate decision: CONDITIONAL.**
- The V1 eval gate **passes on eval_v8**, after **eval_v7 failed it**. Both results stand.
- The human pilot (`PILOT.md`; V1_GATE.md §3, C1–C11) has not run, and nothing below substitutes
  for it.
- The version stays `0.3.0a1`.

| Check | Result |
|---|---|
| Local gate (`scripts/check.sh`) | Ruff and pytest pass: **731 passed, 4 skipped**. |
| Clean install | **Passed.** Details below. |
| Live goal lifecycle | **Passed: 4 goals, $0.24.** Details below. |
| Found by that check, and fixed | Two display issues; details below. |
| **eval_v7** (frozen at `d19bce6`) | **FAILS on 3a**, false done: G 1, A 0. Every other item held. `experiments/eval_v7/RESULTS.md`, `DEVIATIONS.md`. |
| **eval_v8** (fresh pool, frozen at `0db915c`) | **Passes all six items:** non-inferior to direct, and no superiority is claimed. Details below. `experiments/eval_v8/RESULTS.md`, `NOTES.md`. |
| Identity scan | **Clean** on the content, messages and blobs of every pushed commit. The commits carry the owner's git identity as author, as all earlier ones do (H1–H3, the owner's decision). |

**Clean install.**
- The wheel was built from this branch by `scripts/install_alter.sh` into a fresh `ALTER_HOME` and
  `ALTER_BIN`: a fresh venv, without the semantic extra.
- It ran in a fresh shell (an empty environment and a minimal PATH) with a fresh `PCIM_HOME`.
- `alter setup --init` passed. The identity probe recorded the CLI clean (4 Haiku calls).
- `alter doctor` exits 0. Its one warning: `gh` is not on the minimal PATH.

**Live goal lifecycle**, run with the installed `alter` and the real `claude` CLI (sonnet), under an
autonomy grant:
1. **A fix:** done and verified in 13 s.
2. **Cancelled mid-turn:** the worker exited within 7 s. No file was changed, and no test process
   was left behind.
3. **Worker killed with SIGKILL mid-run:**
   - the goal was shown as interrupted, and `alter doctor` warned of it;
   - `alter goals resume` finished it, done and verified, in 19 s;
   - the tests pass when run independently.
4. **A request the folder cannot satisfy:** ALTER changed nothing, wrote `Outcome: not reached`, and
   the goal waits for the person. Before the fix it would have ended "done": its reply contained no
   question mark.

`alter report` agrees: 2 verified of 3 finished goals, false done 0, unauthorized 0, recovery 1/1.

**Found by that check, and fixed**, with tests that fail before the fix:
- a cancelled goal showed the agent's "Resume to continue" stop note, labelled as model-written;
- a waiting goal's reason ended with "..".

**eval_v8 in detail.**
- **3a passes on equality:** 1 false done in each arm, on the same pair, which both arms failed.
- **Secondary results are against G**, none significant:
  - first pass: 15 against 18 (McNemar p = 0.25);
  - asked when a question was needed: 2/12 against 4/12.
- **The code differs slightly from what eval_v8 measured.** eval_v8 measured `src/` at `0db915c`.
  This branch adds main's #19 (the test supervisor), the help text and the two display fixes above.
  The honest-done diff itself is unchanged.

**Open, and why this stays CONDITIONAL:**
1. **The human pilot** (`PILOT.md`) measures C1–C11. It has not run.
2. **Asking.** On both evaluations, G asked for a missing fact less often than direct. V1 does not
   claim better asking (V1_GATE.md §2 and §4), but a person using it should know.
3. **A follow-up goal after a correction that changes nothing can still end "done"** on the model's
   own word (eval_v8, P01-t04). Recommended: such a goal waits for the person. It is not
   implemented here; it needs its own test and evaluation.
4. **The owner's decisions from the safety audit:** history (H1–H3, H7), GitHub settings (H9) and
   egress (E2, E3).

## ALTER V1 integration candidate, 2026-11-21

**Gate decision: CONDITIONAL.** The candidate retains the `0.3.0a1` alpha version.

| Check | Result |
|---|---|
| Local gate | Ruff passed. Pytest passed 708 tests with 5 skipped. |
| Clean install | The wheel installed in a fresh venv with the core dependencies. The installed `alter setup --init`, `alter doctor --json` and `alter report --json` passed with a fresh store. |
| Installed terminal checks | 37 of 37 passed with a simulated provider. |
| Installed goal lifecycle | Verified a code change, cancelled a goal while its provider call was in flight, and recovered a goal after terminating its worker while it was waiting for a provider reply. The cancelled run made one provider call and left its file unchanged. The recovered run finished verified and appeared in `alter report`. |
| Audit and pilot | The safety audit fixes are integrated. `alter report` includes identifier hits and scan coverage for goals with recorded writes. |
| Live checks and evaluation | An independent writer validated and sealed all 24 eval_v7 tasks. The inputs and code are pinned in `experiments/eval_v7/FROZEN.txt`. The live evaluation, live goal lifecycle check, and human pilot are pending. |

The installed recovery check above covered a worker waiting on its provider. A separate offline
regression for R6 covers a worker killed during an active test. It verifies that the test
command stops, its attempt records `interrupted`, and `alter goals resume` verifies the work.
The R6 fix follows the revision pinned for eval_v7.

## ALTER `0.3.0a1` (alpha), 2026-11-21

**Gate decision: CONDITIONAL, unchanged.** This is an **alpha** for the owner's machine.
**V1** has its own gate: `V1_GATE.md` (a narrower claim, criteria and a decision rule) and
`PILOT.md` (the real-user pilot that measures it). It is CONDITIONAL until the pilot runs.
- It makes no claim of research validation or performance over direct model execution.
- It has had no human evaluation.
- The evidence below is engineering evidence: it shows the product works, not that it helps
  people.

| check | result |
|---|---|
| full suite (`scripts/check.sh`, CI's rule set) | **642 passed, 4 skipped**, plus ALTER's own twin tests |
| clean install from the wheel, fresh venv, fresh shell | `alter --version` → `alter 0.3.0a1 (alpha)`; `alter doctor` exits 0 against the real data (`claude` CLI 2.1.316, isolation verified) |
| `scripts/e2e_terminal.py` against the installed wheel | **37 / 37** |
| independent code review (a separate agent, read-only), two passes | **Pass 1: 18 findings**, 4 of them security; all fixed. Of the 18 regression tests, 17 failed on the reviewed code. **Pass 2:** confirmed 13 fixes closed and found 8 more items, including one new bug that fix 18 introduced (cancelling a goal could resume the model). All 8 are fixed, and all 8 tests failed before the fix. Total: **654 passed, 4 skipped.** |
| identity scan of the branch history | **clean**, after one redaction: ALTER had written the owner's name into a test (§ live runs) |

**Live runs** through the installed `alter` with the real `claude` CLI (Opus), in a worktree of
this repository, autonomy granted for that folder only:

| goal | turns | cost | result |
|---|---|---|---|
| "examine everything we've built … finish the next working version of my cognitive twin" (steered while it ran) | 3 | $4.56 | **done, verified** (the project's tests passed after the last change): `twin_do.py` + test |
| "Continue what we were building." | 1 | $1.11 | changed nothing; refused to edit two large files it had received cut off, and asked two questions. **It exposed two bugs**, both fixed: silent truncation of tool results, and a goal ending "done" after asking questions. |
| "Continue …" with the answers (paused after its first checkpoint, then resumed) | 3 | $4.33 | **done, verified**: one failing test run fixed. It used passage edits to `twin.py` and `terminal.py`, wiring `ci twin include/exclude do` and the weekly section. |
| "In alter doctor, the vault check …" (foreground `alter "…"`, on the build with every review fix) | 1 | $1.67 | **done, verified**: `alter doctor` reports iCloud placeholders as fetched when needed. It left one question open as a note. |

**In all:** 4 goals, 26 turns' worth of model steps, $11.67 as the CLI reports it. The goals were
mostly input tokens: each step resends the conversation.

**Still open for this alpha:**
1. **No human evaluation.**
2. **Identifiers from context can reach a file.** ALTER can write identifiers it saw in its
   context into files, and did so once. Run `scripts/scan_identity.py --history` before any push.
   Next step: run that check on a goal's changed files before the goal counts as done.
3. **Agent cost.** With Opus through the CLI, one turn can reach the $2 turn limit. Costs are as
   the CLI reports them.
4. **This configuration only.** Identity isolation is still verified only for `claude` CLI
   2.1.316.

---

# Release readiness: Alter `0.2.0rc2`

**Gate decision: CONDITIONAL.**
- **Ready:** a restricted developer preview on a verified configuration. That means the owner's
  machine, `claude` CLI 2.1.316, and an isolation probe recorded in that `PCIM_HOME`. It still has
  to come **after** the history rewrite (§4.1).
- **Not ready:** broad release, until the human evaluation (`HUMAN_EVAL.md`) has been run and
  passes its rule.
- **Not proposed:** making planning the default for every request. Direct execution stays the
  path for clear work.

**Branch.** `release/alter-0.2.0rc2`, pushed to `origin` after the history rewrite, and open as
PR #7 against `main`. CI is green on the head: ruff, plus pytest on Ubuntu 3.12, Ubuntu 3.14 and
macOS 3.14. Nothing is merged into `main` or published to a package index.

**Sources.** Every result below was produced on 2026-11-17 by a command that ran to completion.
- The evaluation's own numbers are in `experiments/eval_v4/RESULTS.md`.
- The commands are in `SMOKE.md` and `docs/TEST_COMMANDS.md` §8.

## 1. Verification (on `08f011e` unless noted)

| check | result |
|---|---|
| full suite, **on the pushed head** (`a9f3600`) | **589 passed, 4 skipped** with `scripts/check.sh`. In a venv like CI's (no `semantic` extra): 588 passed, 5 skipped. GitHub Actions: all 4 checks green. |
| full suite | **539 passed, 4 skipped**. Skipped: the live probe test (opt-in), and the 3 history-rewrite tests, which pass (3/3) with `FILTER_REPO` set. |
| `main`'s own tests against this code | **343 passed, 2 failed**, both intended: `plan` is a command, not a settable mode; and `main`'s strict xfail for M10 now passes, because M10 is fixed |
| `scripts/e2e_terminal.py`, this checkout | **37 / 37**, including `ci do`: direct waits for approval before writing, clarify makes no model call, plan changes nothing |
| wheel | `contextual_intelligence-0.2.0rc2-py3-none-any.whl`, 339,556 bytes |
| clean install, fresh venv | **passed**; `ci 0.2.0rc2`, `pcim` and `pcim-mcp` installed |
| e2e against the **installed** wheel | **37 / 37** (`E2E_INSTALLED=1`) |
| upgrade, `main` → rc2, `semantic` extra available | **passed**: `doctor` exits 0; `status`, `audit`, `plan` (ready), `do list` and memory all work |
| upgrade, `main` → installed rc2 **without** `semantic` | **fails clearly**: `doctor` exits 1 on the `embedder` check, and `ci plan` exits 2 with the instruction |
| fresh rc2 store without `semantic` | **passed**: `hashing-512`, `doctor` exits 0, `plan` is ready. Then `main` reads it. |
| rollback, rc2 → rc1 (`dd3dcda`) and rc2 → `main` | **passed**: both read rc2's data, rc1 lists rc2's plans, and `main`'s `pcim audit` reports `chain_ok: true` |
| identity probe, live, on this code | **clean 4/4**, agent path included, with the positive control. Recorded for CLI 2.1.316. |
| egress audit, live, on this code | **only `api.anthropic.com`**. No identifiers in the model input or the headers. The account id is in metadata, as documented (`docs/SECURITY_WHAT_LEAVES.md`). |
| identity scan of the final diff against `main` | **clean**: 0 hits on every label, 34,598 added lines |
| identity scan of the branch **history** | **4 commits carry identifiers** (§4.1) |
| history-rewrite rehearsal on a mirror | **verified** for all 8 affected branches: no other ref moved, every `src/` tree identical, dates kept. The real repository is untouched. |

## 2. Evaluation evidence (`experiments/eval_v4/`, frozen before reading)

**Intent (50 new requests).**
- **V3.3 meets P1–P3**, and replaces V3.2 as the `ci plan` engine:

  | metric | V3.3 | V3.2 | McNemar |
  |---|---|---|---|
  | H3: asks needlessly on clear requests | **1/21** | 10/21 | p = 0.004 |
  | H2: asks when it should | 20/23 | 20/23 (unchanged) | |
  | H1 | 23/23 | | |
  | H4 | 38/38 | | |
  | H6 | 50/50 | | |
  | C1 | 9/9 | | |
  | calls, cost | 72, $2.57 | 85, $2.92 | |

- **Intent preservation holds:** no requested outside action was dropped (H1).
- **The bounded re-ask was never exercised:** 0 of 157 first replies were not JSON.

**Router (`ci do`).**
- **Safe, but nearly inert.** It never asked needlessly (RT2 0/21) and never detoured a direct
  request (RT5 25/25).
- **But it missed most of the needed questions** (RT1 4/21) and most destructive ambiguity
  (RT4 3/7). The model compiler caught 14 of the 17 it missed.

**Execution (16 new coding tasks with hidden checks).**
- **Both arms verified every task:** direct 16/16, routed 16/16.
- **Safety:** 0 unauthorized changes, and 0 false claims of completion (judged).
- **Where routing acted** (2 destructive tasks, routed to plan): 0 corrections against 1, and
  $0.36 against $0.65.
- **So the pre-registered rule keeps adaptive routing as `ci do`'s default,** by the margin of one
  correction on one task. Routing is harmless here, but it is not shown to help.

**Environment losses.** The machine slept with the lid closed, and 9 records were lost. Each was
re-run once under a deviation declared beforehand; both analyses agree on every decision.

**eval_v5** (2026-11-18; 12 simulated users, 24 new tasks, four arms through the real CLI, three
blinded judges; frozen at `c5da87a`; `experiments/eval_v5/RESULTS.md`):

| arm | verified | asked needlessly | judged preference against A |
|---|---|---|---|
| direct | 24/24 | 0/21 | — |
| keyword router | 24/24 | 1/21 | 3–2, 19 ties |
| V3.3 planning | 21/24 | 6/21 | 12–8 |
| planning-assisted router | 22/24 | 6/21 | 9–10 |

- **Cost, person-time and median latency:**

  | arm | cost | person-time | median latency |
  |---|---|---|---|
  | direct | $3.62 | 840 s | 39 s |
  | keyword router | $3.77 | 1,050 s | 41 s |
  | V3.3 planning | $8.91 | 2,865 s | 64 s |
  | planning-assisted router | $5.65 | 1,840 s | 57 s |

- **No arm is shown to be better than direct** under the registered rule.
  - Planning-assisted routing fails success non-inferiority, its cost bound and its attention bound.
  - So it stays opt-in.
- **Unauthorized actions:**
  - attempted and denied: 8, all by the planning arms, writing backup files;
  - successful: 0.
- **Limits:**
  - only 3 of 24 requests needed a question, so clarification recall rests on n = 3;
  - judge κ ranges from 0.31 (rating) to 0.68.

**eval_v6** (2026-11-18; 24 new tasks, 12 of which need a question by design; frozen at
`5a007fb`; `experiments/eval_v6/RESULTS.md`):

| arm | verified | first-time success | asked when needed |
|---|---|---|---|
| direct | 24/24 | 18/24 | 8/14 |
| keyword router | 24/24 | 17/24 | 6/14 |
| selective clarification | 24/24 | 17/24 | 6/14 |

- **Selective is not shown to be better than direct.**
- **Clarification is unsolved:** every arm misses about half the questions that are needed.
- **Secondary, to be confirmed:** selective cost 25% less and was faster.

**Earlier evidence still standing:** in paired v1 and v2, direct verified 32/32, against
planning's 9/16 (V2.1) and 14/16 (V3).

## 3. Gate criteria

| criterion | status | evidence |
|---|---|---|
| no critical security vulnerability | **met, for the verified configuration** | Model input is clean on CLI 2.1.316, enforced with `PCIM_CLI_REQUIRE_ISOLATION=1`, and there is no third-party traffic. The account id in metadata duplicates what the OAuth login already identifies. |
| no unauthorized external actions | **met** | E6 = 0 in every arm of every paired run. CI has no tool that acts outside the computer, and the router grants nothing. |
| no known critical correctness regression | **met** | H3 is fixed (1/21); H1, H2, H4 and C1 hold. One item regressed (q08 H2). |
| installation, upgrade, rollback, smoke | **met** | §1 |
| failure recovery documented | **met** | `RUNBOOK.md`, including `ci do` waits; `ROLLBACK.md`; `HISTORY_REWRITE.md` |
| performance against the baseline | **met** | V3.3 is cheaper than V3.2; routed is no worse than direct. |
| **human evaluation** | **not met: designed, not run** | `HUMAN_EVAL.md` |
| **history free of identifiers before a push** | **met for the pushed branch** | §4.1. The rewrite was applied to the 8 affected branches with the owner's approval, and `scan_identity.py --history` was clean before the push. `main` is not rewritten. |

## 4. Open items, and why this is CONDITIONAL

1. **Git history: resolved for this branch, not for `main`.**
   - Four commits carried the home path, the username or identifier fragments. The rewrite
     (`HISTORY_REWRITE.md` §4) was applied on 2026-11-18, with the owner's approval, to the 8
     affected branches only. Old→new ids are in `HISTORY_REWRITE_MAP.txt`, and the backups are
     outside the repository.
   - Before any other branch is pushed, run `scan_identity.py --history` on it.
   - `main` already holds the home path in 3 files. Removing it there needs a force-push to
     `main`, which is a separate decision and has not been done.
2. **No human evaluation: blocks a broad release.** Every labeller, simulated person and judge so
   far is a model.
3. **Router quality.** It is inert on most ambiguity. The next design, which is untested, is a
   router that triages with one V3.3 compile (about $0.035 and 25 s per request) and acts on its
   gate. It needs a fresh set, because this one has been read. Until then, `ci do` behaves almost
   always as direct, and `PCIM_DO_ROUTING=direct` makes that exact.
4. **Identity isolation is configuration-bound.**
   - It is verified only for CLI 2.1.316, and per `PCIM_HOME`.
   - The CLI's own `api.anthropic.com` account traffic is unobserved: TLS hides it.
   - Older stored records are not scrubbed.
   - After a CLI update, re-run the probe **and** the egress audit (RUNBOOK).
5. **Known low-severity issues:**
   - **A closed stdio is misread.** A local I/O error inside a routed call (stderr closed by a
     pipe) is recorded as a provider failure, and every fallback model is tried.
   - **The router over-triggers.** It misreads "clear" and "cut" as destructive, scopes from the
     verb to the end of the request, and treats one-word commands as "nothing to do". These were
     found on review, are unmeasured, and are not fixed.
   - **`HF_HUB_OFFLINE` auto-mode** checks only the default cache.
6. **The re-ask fix for a non-JSON plan (r10)** has never been triggered on live data (0/157).

## 5. Next action for the owner

1. Review PR #7 (`release/alter-0.2.0rc2` → `main`), starting with this file and the three
   `RESULTS.md` files.
2. Merging it into `main` is your decision. It makes the restricted preview the default branch;
   it does not publish a package.
3. Decide separately whether to force-push `main` to remove the 3 inherited home-path files.
4. Recruit 12 participants and 3 raters for the human evaluation (`HUMAN_EVAL.md`), and have
   someone other than the implementer write and seal its task pool.
