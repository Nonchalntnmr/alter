# eval_v7: deviations and procedural notes

RESULTS.md and RESULTS.json are generated from the run's own files. They cannot see the events
below, so they are recorded here by hand.

## 1. One run lost before it wrote a row: P03-t16, arm G

- **What happened.** The first `run` process stopped at 18:39 (2026-11-21, local time). It had
  just created the P03-t16 G sandbox; nothing was written to the sandbox after that, and no row
  reached raw.jsonl. Ten rows (5 pairs × 2 arms) were complete by then.
- **Cause.** The owner requested a stop to hand the live run from Codex to Claude. Codex stopped
  the coordinator and active evaluation processes and verified that they exited.
- **Treatment.** `run` was started again at 20:04 from the same tree and the same frozen code
  (d19bce6), with the same CLI (2.1.316).
  - The harness skipped the 10 completed rows.
  - It ran P03-t16 G as `attempt: 1`, because the lost attempt left no row.
  - The manual stop falls outside the preregistered loss cases of sleep or a usage limit. The
    restarted attempt is a protocol deviation. The generated re-run count is based on recorded
    rows, and the stopped attempt left none.
- **Effect.** The resumed attempt verified. The false-done record that drove the gate failure was
  on a different pair.

## 2. Judging interrupted at 66 of 72 calls

- The `judge` process ended at 20:43 with 66 of 72 verdicts written, when the session running it
  exited.
- `judge` was started again at 21:09. It skips (pair, compare, judge) triples that are already
  recorded, so no verdict was asked twice. The remaining 6 calls (P12-t09 and P12-t18, three judges
  each) were made, giving 72 calls and 0 errors.
- This is not a deviation from the preregistration. It is recorded for completeness.

## 3. Pool validation evidence

- A separate Codex agent wrote the pool using only the task rubric. The retained seal records
  aggregate validation. All 24 starting projects passed visible tests and failed hidden tests.
  All 24 reference solutions passed both. The 16 alternate readings passed visible tests and
  failed hidden tests as expected.
- The writer removed its temporary per-task validation file with the private staging data after
  sealing. It later verified that both sealed files matched the digests recorded by the freeze.
  The original seal digest and validation count appear in FROZEN.txt.
