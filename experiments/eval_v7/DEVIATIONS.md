# eval_v7: deviations and procedural notes

RESULTS.md and RESULTS.json are generated from the run's own files. They cannot see the events
below, so they are recorded here by hand.

## 1. One run lost before it wrote a row: P03-t16, arm G

- **What happened.** The first `run` process stopped at 18:39 (2026-11-21, local time). It had
  just created the P03-t16 G sandbox; nothing was written to the sandbox after that, and no row
  reached raw.jsonl. Ten rows (5 pairs × 2 arms) were complete by then.
- **Cause.** It was not the machine sleeping: pmset recorded no sleep, and the lid was open. The
  process was ended from outside, about 8 minutes after the run began. A tool timeout in whatever
  launched it is the likely cause, but this is not proven.
- **Treatment.** `run` was started again at 20:04 from the same tree and the same frozen code
  (d19bce6), with the same CLI (2.1.316).
  - The harness skipped the 10 completed rows.
  - It ran P03-t16 G as `attempt: 1`, because the lost attempt left no row.
  - Under PREREGISTRATION.md "Losses" this counts as **the single permitted re-run** of a lost run.
    RESULTS.md's "Re-runs under the loss policy: 0" does not count it, because the harness never
    saw the first attempt.
- **Effect.** The re-run produced P03-t16 G verified=True. No other row was affected.

## 2. Judging interrupted at 66 of 72 calls

- The `judge` process ended at 20:43 with 66 of 72 verdicts written, when the session running it
  exited.
- `judge` was started again at 21:09. It skips (pair, compare, judge) triples that are already
  recorded, so no verdict was asked twice. The remaining 6 calls (P12-t09 and P12-t18, three judges
  each) were made, giving 72 calls and 0 errors.
- This is not a deviation from the preregistration. It is recorded for completeness.

## 3. Not verified here

- **The pool's provenance.** FROZEN.txt records `writer_validation=24/24` and
  `writer_isolation=separate session limited to the task rubric`. There is no per-task validation
  record in this folder. The session that wrote the pool should provide one.
