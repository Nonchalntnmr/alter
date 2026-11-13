# Handoff: CI intent-compiler milestone

From Claude to the next agent (Gemini in Antigravity), 2026-11-13, about 14:35 UTC.
Read all of this before touching anything.

## 0. Rules from the owner (non-negotiable)

**Outward actions.**
- Do not push, merge into `main`, deploy, or take any outward action.
- Commit locally only. Keep every commit message ending with an attribution line.

**The running experiment.**
- Do not modify the frozen experiment while it runs. Frozen means:
  - `~/projects/ci-intent/src` (must equal commit `a9ddcfa`);
  - `scripts/paired_eval.py`;
  - `experiments/paired/PREREGISTRATION.md` and `experiments/paired/FROZEN.txt`;
  - the task set `~/projects/ci-sealed/paired_v1/tasks.json`.

**Sealed sets.**
- Do not read the new sealed sets until the V3 code is committed and frozen:
  - `~/projects/ci-sealed/intent_heldout_v2.json`;
  - `~/projects/ci-sealed/paired_v2/`;
  - the scratch folder `…/scratchpad/eval_v3_build/`.
- Reading them earlier destroys their value as held-out evidence.

**Results.**
- Never fabricate results, weaken tests, or redefine success.
- Report negative results.
- Non-significance is not equivalence.
- Model agreement (the judge) is not verification. Only the hidden checks verify.

**Model calls and spending.**
- Make live model calls only through the logged-in `claude` CLI, and state the estimated cost
  before each live run.
- Each (task, arm) runs once. There are no retries of billable calls.

**Python environment.**
- Use the shared venv, `PY=~/projects/contextual-intelligence/.venv/bin/python`.
- Always set `PYTHONPATH=<worktree>/src`. The venv's editable install points at the main
  checkout.
- Never run `pip install -e`.

**Other people's work.**
- `~/projects/contextual-intelligence` (the main checkout) belongs to another session, which has
  uncommitted E2 work. Do not touch it.
- Run `git status` in every worktree before working, and look for edits you did not make.

**Personal data.**
- Committed model outputs must not contain the account email, the person's name, the username or
  the institution. Redact them first (see `experiments/intent_heldout/` for how), then re-score
  to confirm nothing changes.
- Redaction is not a privacy fix. The fix is `sec/cli-identity`.

## 1. Worktrees and branches (all local; nothing pushed)

| worktree | branch | head | what it is |
|---|---|---|---|
| `~/projects/ci-intent` | `feat/intent-compiler` | `bc93c86` | V2.1 plus held-out results plus docs (report partly pending). **The frozen paired run executes from here.** |
| `~/projects/ci-intent-v3` | `feat/intent-v3` | `fa81b4e` plus this file | V3 work: new evaluation rubric committed; implementation not started |
| `~/projects/ci-sec-cli` | `sec/cli-identity` | `c9edc64` | CLI identity isolation, tested (from `origin/main` 1f9bd08) |
| `~/projects/ci-intent-prev` | detached `ff74305` | | V0/V1 code for the held-out runner (read-only) |
| `~/projects/ci-main-baseline` | detached `origin/main` 1f9bd08 | | baseline tests (read-only) |

Commits on `feat/intent-compiler`, oldest first:

| commit | what |
|---|---|
| `cc21ffa` | V0 |
| `ff74305` | V1 |
| `58348cc` | V2, typed intent and the state machine |
| `9eb2721` | held-out protocol and runner, frozen |
| `7821157` | held-out runner fix |
| `d22da63` | paired experiment frozen |
| `18f7d37` | held-out results |
| `a9ddcfa` | V2.1, the real-word typo fix |
| `2eb0173` | paired code amended to V2.1 before the tasks were read |
| `bc93c86` | docs in progress |

## 2. What is running or may have died with Claude's session

1. **The frozen paired run (Mission 3).** At handoff, p01–p06 had finished both arms and p07 had
   finished arm A.
   - Progress: `~/projects/ci-intent/experiments/paired/run_stdout.txt`.
   - Results: `experiments/paired/raw.jsonl`, one line per (task, arm), 32 expected.
   - **Check:** `ps aux | grep "[p]aired_eval.py run"` and `wc -l experiments/paired/raw.jsonl`.
   - **If it died before 32 lines,** resume with the same command. It skips pairs already done
     and never re-runs one:
     ```sh
     cd ~/projects/ci-intent && HF_HUB_OFFLINE=1 PYTHONPATH=$HOME/projects/ci-intent/src $PY -W ignore scripts/paired_eval.py run >> experiments/paired/run_stdout.txt 2>&1
     ```
     It refuses to start if `src/` or any frozen hash differs. Do not "fix" that by changing
     hashes.
   - **Expected remaining cost:** about $10–20 notional, about 40–60 min.
2. **The writer agent for the V3 evaluation sets.** Its outputs are
   `~/projects/ci-sealed/intent_heldout_v2.json` and `~/projects/ci-sealed/paired_v2/`, with
   `tasks.json`, `validation.json` and `reference/`.
   - **Check existence and size only.** Do not read them.
   - If they are missing or incomplete, re-create them with a fresh agent that cannot see CI's
     code. Its only spec is `experiments/eval_v3/RUBRIC.md` (committed as `fa81b4e`, sha256
     `81a8d4ce…`). It must validate the paired tasks: the hidden test fails on the original code,
     passes on the reference, and fails on the other reading for vague tasks.
   - Record each set's sha256 without reading the contents.
3. **A review agent for `c9edc64`** (the security branch). Its result is lost if the session
   died. Re-run an independent review of `git -C ~/projects/ci-sec-cli show c9edc64`. It should
   look for fail-open paths, identity leaks, cleanup, and the probe relay's token handling.

## 3. After the paired run finishes (Mission 3, frozen; change nothing in the harness)

```sh
cd ~/projects/ci-intent
wc -l experiments/paired/raw.jsonl                                   # must be 32
HF_HUB_OFFLINE=1 PYTHONPATH=src $PY scripts/paired_eval.py judge     # claude-cli:haiku, small cost
HF_HUB_OFFLINE=1 PYTHONPATH=src $PY scripts/paired_eval.py score     # offline; prints E1–E8 and the decision rule
```

**Verify first.**
- Every raw record's code is `a9ddcfa`, and `FROZEN.txt`'s hashes match. `score` and `check()` do
  this.
- Each (task, arm) appears exactly once.

**Then report.**
- Paired outcomes per task, with A against B for each of: verified, calls, cost, seconds,
  corrections, clarifications, approvals, plan reviews, out-of-scope denials, and plan status.
- The decision rule from `experiments/paired/PREREGISTRATION.md`.
- Wilson intervals, and exact McNemar on E1.
- **p01.** Report it specifically.
- **p12.** A coding change plus creating a ticket. "Ticket" is not in V2.1's lexicon. Did B
  preserve it as an outcome by you, ask about it, block, or drop it silently? Did A mention it
  as not done?
- **Separate** verified completion (the hidden check) from the plan's own status and from the
  judge's model-reported "complete".

**Redact before committing.** Redact identity in `raw.jsonl` and any judge outputs. Check:
`grep -ciE "<identifier pattern removed; use scripts/scan_identity.py>" experiments/paired/*`.
Then re-score, confirm identical, and commit the raw data, the judge outputs and the results.

**Fill in the docs.** In `docs/INTENT_V2_REPORT.md`, fill in Mission 3 and §5 (the merge
recommendation). Also update `CI_INTENT_MESH.md` §8 H3, and run the full suite (expect 452
passed, 2 xfailed).

## 4. Paired v1 so far: confirmed V2.1 defects (from p01–p06; arm A verified all six)

**Interim numbers.** These are not the result.

| arm | verified | calls each | cost each |
|---|---|---|---|
| A (direct) | 6/6 | about 6–8 | $0.15–0.25 |
| B (plan) | 5/6 (p03 failed) | 17–37 | $0.66–3.02 |

**D1. Over-clarification on clear coding tasks.**
- B asked questions on every clear or messy task.
- p03: three `needs_answers` plans in a row, even after "go with your best reading". The arm
  stopped and the task was never done.
- Fix direction: inside an authorized workspace, plan to *read the code* rather than ask.
  - After one answer round, unresolved low-stakes questions become stated assumptions.
  - Only consequential ambiguity (destructive or external) may ask again.

**D2. Extractor false positives on code and technical prose.**
- p02: "**Calls** that don't pass holidays…" was read as call, i.e. contact.
- p05: "**reserve** raises…", "**release** raises…" and "**remove** doesn't…" are function names
  used as subjects. They were read as book, deploy and delete.
- Each became a false `yours_pending` outcome, so all-met plans ended `awaiting_you`.
- Fix direction: a clause-initial word followed by `(`, wrapped in backticks, or followed by a
  third-person or modal verb (raises, returns, is, should, must, does, doesn't) is a subject or
  an identifier, not an imperative.
  - Reconsider inflected forms ("Calls", "emails") at the start of a clause. Imperatives are base
    form.
  - Keep the guarantee: never drop a real requested action, and add tests both ways.

**D3. False `incomplete`.**
- p04 and p06: a `record_claim` tool failure ("the claim ledger is not enabled here") made a
  write step `incomplete`. The dependent steps never ran, although the code change was done and
  verified.
- p01: a self-check step tried to write a scratch script outside the allowed files. The write
  was denied, so the step was incomplete and the plan was `incomplete`, although the work was
  verified.
- Fix direction:
  - Bookkeeping tools (`record_note`, `record_claim`) failing must not decide a step's outcome.
  - Don't offer tools the workspace has disabled.
  - Keep **three independent states**:
    1. outcome success, from evidence (for example, a passing test run after the last write);
    2. plan completion, meaning which steps finished;
    3. authorization compliance, meaning denials and out-of-scope attempts, listed.
  - A denied optional step must show as a denial, not erase verified outcomes. Never mark an
    outcome met without evidence.

**D4. Planner-induced unnecessary steps and cost.**
- Examples: p04's "save a copy of the original outside the project", and "check with a quick
  script".
- One agent turn per step re-reads files.
- B costs 4–15× more than A.
- Fix direction, and the owner's instruction: if planning does not measurably improve execution,
  **simplify or redesign**.
  - The strongest candidate for V3 keeps the intent contract: outcomes, preserved external
    actions, constraints, and questions only when consequential.
  - It executes the contract in **one** governed agent session, with the contract as the
    checklist, rather than one session per step.
  - It judges completion per outcome from evidence.

**D5. Preserving intent across clarification and correction.**
- When a plan is re-compiled from an answer or a correction (`ci plan answer`), the parent
  plan's external and destructive outcomes must carry over, unless the answer explicitly
  withdraws them. Today only the original request text is re-scanned, so an action the *model*
  found, outside the lexicon, can vanish.
- Add tests.

## 5. The remaining sequence (owner's mandate)

1. **Finish and score paired v1.** See §3.
2. **Implement V3 on `feat/intent-v3`.**
   - Merge `sec/cli-identity` into it first (`git merge sec/cli-identity`). Conflicts are
     expected to be small: adapters are untouched by the intent branch, and `engine.py` changes
     are separate lines.
   - Fix D1–D5 and the `agent.py` Stop-all wedge. The wedge is pinned by the strict xfail
     `tests/test_plan_exec.py::test_the_workspace_agent_recovers_by_itself_after_stop_all`.
     When fixed, remove the xfail mark; don't weaken the test.
   - Run the full suite.
3. **Freeze V3 and run the new evaluation.**
   - Write `experiments/eval_v3/PREREGISTRATION.md` **before reading the sets**:
     - metrics: H1–H7 as before, plus C1, preservation after clarification;
     - for the paired evaluation, arms A (direct), B2.1 (V2.1 plan) and B3 (V3), with E1–E8 plus
       false incomplete, out-of-scope attempts, and clarifications on clear tasks;
     - a decision rule.
   - Write a harness: reuse `scripts/paired_eval.py` logic in a new file, never edit the frozen
     one. Set `PCIM_CLI_REQUIRE_ISOLATION=1` for every eval call, after recording the probe:
     `python -m pcim.adapters.cli_probe --trials 4 --record`.
   - Record the hashes in `FROZEN.txt`, commit, then read and run the sets.
   - Budget: about $35–45 notional, 2–3 h. State it before running.
4. **Integration.** Create branch `integration/intent-compiler` from `origin/main`.
   - Merge `feat/intent-compiler` (or `feat/intent-v3`, which contains it) and `sec/cli-identity`.
   - Resolve conflicts safely and run the full suite.
   - **Do not merge to `main`.**
5. **End-to-end checks** in a temporary `PCIM_HOME` with an authorized temporary repo.
   - Commands: `ci plan`, `ci execute`, `ci approve`, `ci deny`, `ci mesh`, `ci twin`, `ci audit`
     and `ci learn`, against their documented behaviour.
   - Behaviours: restart recovery (kill mid-step, expect `interrupted`, then `--retry`), Stop-all
     (`ci halt` / `ci resume`), permission boundaries (writes outside the folder are refused),
     and failure handling.
6. **Finish the docs.** `docs/INTENT_V2_REPORT.md`, `docs/CI_INTENT_MESH.md`,
   `docs/TEST_COMMANDS.md`, and `docs/SECURITY_CLI_IDENTITY.md` (on the security branch). Include
   exact numbers, limitations and blockers.
7. **The final report.** Give commit SHAs, test results, experimental results, blockers, and the
   integration recommendation.

## 6. Results already established (with evidence)

**The held-out intent run** (`experiments/intent_heldout/`, 50 independently labelled items).
- V0, V1 and V2 all meet H1–H7 on the point estimate. McNemar p = 1.0, so the set did not
  separate the versions.
- 16 of the 17 action items passed by asking a question.
- V2 fixed h48. V1's tripwire over-blocked h14 and h50.
- The lower confidence bounds for H1 (0.82), H4 (0.91) and H5 (0.70) are below target.
- Cost: 100 calls, $3.28.
- The 24-item development run's M2 miss (5/6) stands. It is not counted as a success.

**V2.1** (`a9ddcfa`).
- A real word is not read as a typo of an action verb (look≠book, test≠text).
- A post hoc replay on stored replies changes only h40 and h49.

**Security** (`c9edc64`).
- The CLI put the account email and the home-path username into every request. This was
  measured on the request itself, by a loopback relay.
- The fix: a neutral working directory and a sanitized per-call config. It came out clean in
  10/10 trials on CLI 2.1.316.
- Identity-sensitive calls are refused unless verified (`PCIM_CLI_REQUIRE_ISOLATION=1`).
- Open:
  - the CLI refetches the profile during calls (measured clean, not guaranteed);
  - the account id stays in API metadata;
  - other CLI versions are unverified.
- The report is `docs/SECURITY_CLI_IDENTITY.md` on `sec/cli-identity`.

**Test counts.**

| suite | result |
|---|---|
| `feat/intent-compiler` | 452 passed, 2 xfailed |
| `sec/cli-identity` | 352 passed, 1 skipped (live), 1 xfailed |
| `main` | 344 passed, 1 xfailed |

**Known pre-existing issues** (`INTENT_V2_REPORT.md` §2).
- The `agent.py` Stop-all wedge.
- The agent's session lock works in-process only.
- Building `PersonalCI` contacts the Hugging Face Hub unless `HF_HUB_OFFLINE=1`.
- The mesh trusts `mesh.json`.
