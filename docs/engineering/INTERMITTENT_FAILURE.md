# One intermittent test failure: unidentified (2026-11-17)

**What was seen.** One full-suite run on `feat/do-verification` at `6c09583` reported
`1 failed, 562 passed, 4 skipped`. The run came right after the pre-fix and fixed versions of
`src/pcim/engine.py` were swapped with `git stash push` and `git stash pop`.
- **Its identity was not captured.** The command filtered pytest's output to its summary line.
  That is my error.
- **It was committed anyway.** The commit (`6c09583`) went in without the suite passing, because
  the command did not gate on the test result.

**Reproduction attempts, all clean:**

| attempt | runs | result |
|---|---|---|
| the full suite, plain or through `scripts/check.sh`, at `6c09583`…`9b8cae3` | 13 | 563–567 passed, 0 failed |
| faithful replays of the preceding sequence: pre-fix `engine.py` in the working tree, its test run, the committed file restored, then the full suite at once | 3 | 0 failed |
| the 7 timing- and concurrency-sensitive groups, looped: `test_session_flock_blocks_second_process`, `test_l5_concurrent_attempts_are_lost`, `test_concurrent_turns_on_one_session_lose_nothing`, `test_timeout_and_cancel_kill_the_process`, `test_halt_cancels_and_blocks_until_resume`, `test_hardening.py`, `test_mesh.py` | 25 | 0 failed |

**What is ruled out, and what is not:**
- **Stale bytecode is unlikely.** The two `engine.py` versions differ in size, so their pyc
  check fails and they recompile.
- **A failure rate below about 1 in 40 suite runs is not ruled out.**

**What changed:**
- Every suite run now goes through `scripts/check.sh -rf`. Its `-ra` and `-rf` flags print the name
  of any failing test.
- Commits are gated with `&&` on the check's exit code.
- If it recurs, its name will be in the log, and this file gets the test and its cause.

## Leading hypothesis (2026-11-18): stale bytecode in test runs. Plausible, not confirmed.

While reviewing the verification branch, a deterministic test exposed a real bug.
- **The bug.** A module rewritten to the same byte size within the same second, such as
  `return a * b` to `return a + b`, was imported from the stale `.pyc` by the next test run.
  CPython checks whole-second mtime and size. It reproduces in isolation.
- **Why it fits.** Suite tests that write a wrong version and then the fix of a module, and run
  it in between, would fail only when no second boundary fell between the two writes. That
  matches a rare, timing-dependent failure that a quiet replay does not reproduce.
- **The fix (`690d562`).** `run_tests` now runs the person's test command with
  `PYTHONDONTWRITEBYTECODE=1`, and a regression test covers it.
- **Why it stays unconfirmed.** The failing test's name was never captured, so this remains a
  hypothesis. If the failure recurs after `690d562`, it is something else.
