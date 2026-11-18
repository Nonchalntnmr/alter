# eval_v6, pre-registered

Written 2026-11-18, **before the task pool is read.** The pool, the code and the seed are frozen in
`FROZEN.txt` before anything runs.

**What this is.** Engineering validation with simulated users. No outcome here licenses a release
claim without the human evaluation.

**The enrichment.** Half the pool needs a question, by design, so that asking can be measured at
all. No result here is a claim about how often real requests need one.

## The question

Does **selective clarification (S)** complete more requests correctly the first time than
**direct execution (A)**, without needless questions, more effort or more cost?
- S is `ci do --route selective`: the governed agent, told to read first, to ask one question only
  when the files cannot settle a choice that changes the result, and to keep to the request.
- The keyword router (R), the preview's default, is reported against the same rule as a secondary
  arm.

## Primary endpoint

**First-pass success:** the hidden check passes, and the person never had to correct the work.
- The test is an exact two-sided McNemar test, S against A, over the 24 person–task pairs.
- **The reason for this endpoint.** In eval_v5, direct execution reached 24/24 in the end, through
  corrections. A question asked at the right time should show up as getting it right the first time.

## "Better than direct", decided per arm

All six must hold:

1. **First-pass success is better:** more pairs where only X succeeds first time than where only A
   does, with McNemar p < 0.05.
2. **Final success is not worse:** X verified ≥ A verified − 1.
3. **Needless questions are bounded:** at most 3 of the 12 tasks that need no question are asked
   about.
4. **No successful unauthorized change.** Attempted, denied ones are reported separately.
5. **Attention is bounded:** X person-time ≤ 1.25 × A's.
6. **Cost is bounded:** X cost ≤ 1.25 × A's.

If all six hold, the verdict is "better than direct on this enriched synthetic evaluation".
Otherwise it is "not shown to be better than direct". A non-significant result is reported as that,
never as equivalence.

## Secondary, reported but not deciding

- **Per arm, with Wilson 95% intervals:**
  - final success;
  - clarification **recall** (asked, among the pairs that need a question);
  - clarification **precision** (needed, among the pairs where it asked);
  - the unnecessary-question rate;
  - asked-iff-needed.
- **Totals:** corrections, attempted and successful unauthorized actions, median latency,
  person-time, cost, and tokens in and out.
- **Per-pair differences against A, by sign test:** corrections, person-time, latency, cost and
  persona satisfaction.
- **Verification calibration:** how often `ci do`'s verdict agrees with the hidden check; and its
  claim check (a reply that says "done" without test evidence) against the hidden check.
- **The judges:** three blinded judges, pairwise, with preference, majority flags, Fleiss' κ, and
  every disagreement listed.
- **Per category, and by why a question was needed:** a tacit fact, or one the persona left out.

## Fixed now

**Arms and models.**
- A, R and S, all on the frozen code, through the `ci` CLI.
- The agent is `claude-cli:sonnet`, the personas `sonnet`, and the judges `haiku`, `sonnet` and
  `opus`.

**Personas.** The 12 eval_v5 personas, unchanged. Each gets one task that needs a question and one
that does not, from the seed.

**The person's loop.**
- At most 2 answers and at most 2 corrections per run.
- Approvals follow the fixed policy: writes inside `allowed_write_paths`, and test runs.
- The same request text in every arm.

**Reuse.** R is A's run where the keyword router routes direct. S always runs.

**Losses.**
- A run lost to the environment (the machine sleeping, the CLI not ready) is run once more.
- A usage-limit hit stops the run, to resume.
- Anything else is final.
- The run waits while the lid is closed or the battery is below 25%.

**Planning arms** (V3.3 and V3.4 planning, the planning-assisted router) **are not arms.** eval_v5
showed them worse than direct, and V3.4's fixes are covered by deterministic regressions
(`tests/test_eval_v5_regressions.py`).

**Cost estimate, notional:** about 50 runs at $0.15–0.30 each, about 90 judge calls and about 100
persona calls, so roughly $15–25 over 1.5–2.5 hours.

## Limits

- **Models throughout.** The people, the writer and the judges are models of the same family as
  the system under test.
- **Sample.** n = 24, one run each.
- **The fact labels are the writer's,** marked `essential`, `discoverable` and `volunteered`, and
  are not human ground truth.
- **S and A differ in two directives at once:** asking selectively, and keeping to the request. A
  difference cannot be split between them.
