# Handover to Gemini: Alter (`contextual-intelligence`)

From Claude, 2026-11-16. This replaces the 2026-11-13 handover. Read all of it before acting.
Copy: `HANDOFF.md` on branch `feat/intent-v3`.

## 0. Rules, plus what went wrong last time

**Outward actions.**
- Do not push, merge into `main`, deploy, or take any outward action.
- Commit locally only. End every commit message with a co-author line naming yourself.

**Frozen code.** Never edit `src/` in these worktrees:

| worktree | must stay at |
|---|---|
| `~/projects/ci-intent` | `a9ddcfa` (paired v1) |
| `~/projects/ci-intent-v3` | `9efe636` (V3 evaluation) |
| `~/projects/ci-v21-isolated` | `53306b3` (V2.1+) |

The harnesses refuse to run if `src/` differs. Committing experiment outputs or docs there is fine.

**Live calls.**
- Make live model calls only through the logged-in `claude` CLI, and state the estimated cost
  first.
- **The account has a 5-hour usage limit.**
  - It killed 14 arms of paired v1.
  - Don't run helper agents alongside a live evaluation.
  - Stage the runs.
  - The harnesses retry an arm lost to the usage limit **once**. If it happens, wait for the
    reset before re-running.

**Rules from what went wrong last time:**
- **Any change to the CLI adapter or the child process's environment must be verified live:**
  `PYTHONPATH=src python -m pcim.adapters.cli_probe --trials 2 --baseline`.
  - Your earlier L4 change (`55c6b77`, leaving `CLAUDE_SECURESTORAGE_CONFIG_DIR` unset) broke the
    login: every isolated call failed "Not logged in".
  - The offline tests, which use fake runners, passed anyway. A live probe caught it. It was
    fixed in `0f68403`.
- **Every fix needs a test that fails on the commit before it.** Check this by running the new
  test against the old source:
  `git archive <old> src | tar -x -C /tmp/x && PYTHONPATH=/tmp/x/src pytest …`.
  - Several of your V3 changes had paths that **silently dropped real requests**: "send that to
    priya" was skipped, and an unrelated "don't" in an answer withdrew "email the report". They
    also had **false-completion paths**: a session that changed nothing became `done`.
  - All were fixed in `1b8cc3e`, each pinned by such a test.
- **Never weaken or delete a test to make it pass.** The only removal allowed is a strict xfail
  mark when the defect it pins is fixed.

**Personal data.**
- Committed model outputs must not contain the account email, the person's name, the username or
  the institution.
- Check before calling a branch reviewable:
  `git diff origin/main | grep '^+' | grep -ciE "<identifier pattern removed; use scripts/scan_identity.py>"`
  must print 0.

**Reporting.**
- The hidden checks are verification. The Haiku judge is model agreement, and must be labelled
  as such.
- Non-significance is not equivalence.
- Report negative results.

**Python.**
- Use the shared venv: `PY=~/projects/contextual-intelligence/.venv/bin/python`.
- Always set `PYTHONPATH=<worktree>/src`, and `HF_HUB_OFFLINE=1`.
- Never run `pip install -e`.
- Never touch `~/projects/contextual-intelligence` (another session's uncommitted work).

## 1. Branches (all local, nothing pushed)

| worktree | branch | head | what |
|---|---|---|---|
| `~/projects/ci-intent` | `feat/intent-compiler` | `cc5d33a` | V2.1, held-out v1 and paired v1 evidence, V2 report |
| `~/projects/ci-sec-cli` | `sec/cli-identity` | `0f68403` | CLI identity isolation; review fixes M1–M4, L1–L4, plus the login fix |
| `~/projects/ci-intent-v3` | `feat/intent-v3` | `6fee196` | V3 (frozen `src` = `9efe636`), the V3 evaluation (harnesses, prereg, results). **B21 is running here.** |
| `~/projects/ci-intent-v31` | `feat/intent-v3.1` | `d9e281b` | V3.1: contract approve display and Stop-all/retry consistency. e2e 32/32 |
| `~/projects/ci-v21-isolated` | `eval/v21-isolated` | `53306b3` | V2.1 plus the isolation fix. **An evaluation arm only; never integrate it.** |
| `~/projects/ci-integration` | `integration/intent-compiler` | `de6ab16` | `origin/main` + intent-compiler + intent-v3.1 + docs. **The review branch.** Integration docs commit `bfce2e4`: preserve it. |
| `~/projects/alter-rc` | `release/alter-0.2.0` | `de6ab16` | the production-readiness work goes here. **Nothing done yet.** |

**Test status at handover:**

| branch | result |
|---|---|
| integration, V3.1 | 499 passed, 1 skipped (the opt-in live probe), 1 xfailed (M10, from `main`) |
| `scripts/e2e_terminal.py` | 32/32 |
| `main`'s tests against the integration code | 343 passed, 1 failed (the intended `plan`-mode change), 1 xfailed |

## 2. Running now: nothing

**The B21 arm finished cleanly after this handover was first written.**
- `raw.jsonl` has 48 lines: 48 unique (task, arm) pairs, no duplicates, no errors.
- B21 (V2.1+) verified **7/16**, at $7.21 and 179 calls.
- Its final record is `r16 B21 verified=True status=incomplete`.
- **Do not re-run anything.** Go straight to §3.1 (judge and score).

## 3. Do next, in this order

1. **Judge and score** (about 50 Haiku calls, under $0.10):
   ```sh
   cd ~/projects/ci-intent-v3
   HF_HUB_OFFLINE=1 PCIM_CLI_REQUIRE_ISOLATION=1 PYTHONPATH=$HOME/projects/ci-intent-v3/src $PY scripts/eval_v3_paired.py judge
   $PY scripts/eval_v3_paired.py score     # writes experiments/eval_v3/paired/results.json
   ```
   - Report the **primary** decision (A against B3, the same 4-condition rule as v1) and the
     **secondary** checks (B3 against B21).
   - Separate per-task E1–E8 and F1–F4, with Wilson intervals and paired tests.
2. **Commit** on `feat/intent-v3`.
   - The outputs go in `experiments/eval_v3/paired/`.
   - Release the set: `cp ~/projects/ci-sealed/paired_v2/tasks.json experiments/eval_v3/paired/`.
   - Run the identity grep first. The A and B3 outputs had 0 hits.
3. **Merge** `feat/intent-v3` into `integration/intent-compiler`, then integration into
   `release/alter-0.2.0`. Re-run the full suite and the e2e check on both.
4. **Write the results** on the integration branch:
   - `docs/INTENT_V2_REPORT.md`: add a "V3" section and fill in §5, the merge recommendation.
     The only remaining "PENDING" text is in §5.
   - `docs/CI_INTENT_MESH.md`: add §5c (the paired results; §3 already refers to it) and update
     §8 (claims).

## 4. Results so far (all committed unless marked)

**Paired v1** (`eb21b53`; frozen V2.1, 16 tasks).
- Direct (A): **16/16** [0.81, 1.00]. Plan + step execution (B): **9/16** [0.33, 0.77].
  McNemar p = 0.016.
- B used 3.6× the person time and 3.8× the cost ($12.46 against $3.31), 230 calls against 112,
  with a median latency of 127 s against 48 s.
- Verdict: **"not justified."**
- B never reported `done`: all 9 of its verified tasks ended `incomplete` or falsely
  `awaiting_you`.
- **p12:** B dropped the Jira ticket after clarification (a silent omission). A's final message
  left it open for the person. The judge scored both as not surfacing it, against my reading of
  A; reported as model judgement.

**V3 intent evaluation** (`6fee196`; 50 new items; V2.1+ against V3).
- H1 26/26 for both. H4 39/39, H5 6/6, H6 50/50, H7 10/10.
- C1 (preservation after the answer): 26/26 and 25/25.
- **H3 (≤ 0.25) missed by both:** V2.1+ 16/23, V3 11/23. Every V3 false question is on a clear
  request that includes an outside action (book, email, RSVP, pay, file a ticket).
- **H2 regressed for V3:** 18/21. It no longer asks on 3 genuinely vague requests (q29, q39,
  q45).
- W1 (withdrawal): V2.1+ 2/4, V3 4/4, recorded visibly.

**V3 paired, A and B3** (descriptive, from `raw.jsonl`; not yet scored, judged or committed).

| | A | B3 |
|---|---|---|
| verified | **16/16** | **14/16** |
| cost | $2.62 | $3.56 |
| calls | 102 | 126 |
| median latency | 47 s | 75 s |
| modelled person time | 645 s | 1,380 s |

- B3's failures:
  - **r10:** the planner did not return JSON, so the plan was `invalid` and the arm stopped.
  - **r15:** a vague task V3 did not ask about. It went with its own reading and failed even
    after 2 corrections. It reported `incomplete`, with no false completion.
- **B3 had zero false incompletes** (V2.1 had 9/9). Every verified task ended `done`, or
  `awaiting_you` when the task has an external action.
- **Expected primary verdict: "not justified."** E1(B3) = 14 < 15 = E1(A) − 1, and person time
  is above 1.5×.

**V3 paired, B21** (complete; descriptive, not yet scored).
- **7 of 16** verified, at $7.21 and 179 calls.
- 7 arms stopped after repeated clarifications, with no work done (r02, r03, r04, r08, r10, r11,
  r12).
- Costs ran to $1.44 per arm.
- **Expected secondary result: V3 is far better than V2.1+.**

**Decision-gate reading** (for Phase 6, if B21 finishes as expected):
- V3 should **replace V2.1 as the `ci plan` engine.**
- Planning should stay **opt-in**: direct execution remains the default path, because A ≥ B3 on
  both paired evaluations.
- Do not make planning the default.

## 5. Known defects and open issues (prioritised)

1. **V3 compiler quality.**
   - H3: it asks on clear outside-action requests.
   - H2: it doesn't ask on genuinely vague ones.
   - r10: an invalid JSON plan. Proposed fix: **one bounded re-ask** on an invalid plan reply
     (as `ClaudeCLIAdapter.agent_step` already does), as V3.2 with a test.
   - Any compiler change is unmeasured until a **new** independently written set exists. Every
     existing set has now been read.
2. **The agent's session lock is in-process only** (`agent.py` `_lock` is a `threading.Lock`).
   Two terminals can drive one direct session. Fix: an `flock` per session, like
   `plan_exec.plan_lock`, plus a two-process test.
3. **Building `PersonalCI` contacts the Hugging Face Hub** unless `HF_HUB_OFFLINE=1`. Fix: go
   offline when the model is cached, as `mesh.py` does, plus a test.
4. **Identity isolation, still open:**
   - it is verified only for CLI 2.1.316;
   - the CLI refetches the profile during every call (measured clean, not guaranteed);
   - the account id stays in API metadata;
   - older stored records are not scrubbed.
   Docs: `docs/SECURITY_CLI_IDENTITY.md` and `SECURITY_CLI_IDENTITY_REVIEW.md`.
5. **M10** (on `main`): deleting a project keeps the `ci` requests made in it. It is pinned by a
   strict xfail.
6. **Mesh trust:** `mesh.json`'s `trusted: true` has no attestation.
7. **History.** Commit `ff74305` on `feat/intent-compiler` still holds the home path in history,
   although the current files are redacted. `main` has the path in old experiment files
   (milestone1, routing). Removing either needs a history rewrite: **the owner decides; don't do
   it yourself.**

## 6. The owner's latest directive: "ALTER production readiness" (not started)

The owner asked for:
- **Phase 0:** finish and preserve the experiment (§2 and §3 above);
- **Phase 1:** an audit of 10 areas: reasoning, planning and execution, routing, memory,
  authorization boundaries, recovery, security and injection and secrets, CLI and install,
  performance, observability;
- **Phase 2–3:** capability and production engineering;
- **Phase 4:** tests;
- **Phase 5:** a release candidate (versioned artifacts, install docs, config docs, security and
  ops runbooks, release notes, rollback, readiness checklist, smoke procedure);
- **Phase 6:** a decision gate.

**Rules for this directive:**
- Work on `release/alter-0.2.0` (`~/projects/alter-rc`).
- Do not deploy or publish anything.
- **Don't claim readiness from unit tests alone.**

**The highest-leverage, evidence-backed items, in suggested order:**
1. **`ci doctor`**, a diagnostic that only reads. It checks:
   - the Python version and dependencies;
   - the `PCIM_HOME` identity;
   - the `claude` binary, its version, its login, and `ClaudeCLIAdapter().identity_isolation()`;
   - the HF model cache and offline state;
   - the audit chain;
   - the authorized workspaces;
   - Stop-all.

   It should exit non-zero on blocking problems. Also add `ci --version`.
2. **Version 0.2.0rc1** in `pyproject.toml` and `pcim/__init__.py`.
3. **Fix §5 items 2 and 3** (the session lock and the Hub contact), each with a regression test.
4. **Prompt-injection tests.** Tool output or a file that tells the agent to write outside the
   folder, approve its own action, or run a shell command must not do so. Use the mock agent: the
   governor and authorization must stop it whatever the model "decides". Also test a forged turn
   delimiter in a tool result for the CLI adapter.
5. **A concurrency test** for two processes on one plan or one session.
6. **An install test.**
   - Build: `cd ~/projects/alter-rc && $PY -m pip wheel . --no-deps -w dist/`.
   - Then in a fresh venv: `python3 -m venv /tmp/alter-venv && /tmp/alter-venv/bin/pip install dist/*.whl`.
     Installing the dependencies downloads from PyPI. That is a network action; say so before
     doing it.
   - Then run `ci --help`, `pcim init`, `ci doctor`, and `scripts/e2e_terminal.py` against the
     installed package.
7. **An upgrade test.** Make a `PCIM_HOME` with `main`'s code (`~/projects/ci-main-baseline`,
   at `origin/main`), then run the RC's `ci status`, `ci audit`, `ci twin` and `ci plan` on it.
8. **Release docs** in `docs/release/`: `RELEASE_NOTES_0.2.0rc1.md`, `INSTALL.md`, `CONFIG.md`
   (every env var: `PCIM_HOME`, `PCIM_CLAUDE_BIN`, `PCIM_CLI_REQUIRE_ISOLATION`, `HF_HUB_OFFLINE`,
   `PCIM_EMBEDDER`, …), `RUNBOOK.md` (security and ops: Stop-all, isolation re-probe after a CLI
   update, recovery from `interrupted`), `ROLLBACK.md`, `READINESS.md` (the checklist with actual
   results) and `SMOKE.md` (the doctor and e2e commands).
9. **Phase 6 gate.**
   - Likely **CONDITIONAL**: V3 replaces V2.1 for `ci plan`, and planning stays opt-in.
   - Blockers to state honestly:
     - identity isolation is verified only for the installed CLI version;
     - H2 and H3 compiler quality;
     - M10;
     - no human evaluation yet.

## 7. Command reference

```sh
PY=~/projects/contextual-intelligence/.venv/bin/python; export HF_HUB_OFFLINE=1
cd <worktree> && PYTHONPATH=src $PY -m pytest -q -p no:cacheprovider               # full suite
PYTHONPATH=src $PY scripts/e2e_terminal.py                                          # 32 e2e checks, no model calls
PYTHONPATH=src $PY -m pcim.adapters.cli_probe --trials 2 --baseline                 # live identity probe (Haiku, <$0.01)
PYTHONPATH=src $PY -m pcim.adapters.cli_probe --trials 4 --record                   # record a clean result for this CLI
cd ~/projects/ci-main-baseline && PYTHONPATH=<worktree>/src $PY -m pytest -q        # main's tests on new code
git -C ~/projects/ci-integration merge-tree --write-tree HEAD origin/main           # conflict check
```

Full test-command list: `docs/TEST_COMMANDS.md` on the integration branch.
