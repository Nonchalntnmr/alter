# Protocol deviation: arms lost to the account's usage limit

Recorded on 2026-11-15, **before re-running anything, and before any of these arms has an outcome.**

## What happened

On the first attempt (2026-11-13), the logged-in account hit its 5-hour usage limit at task p10.
Every arm from p10 to p16 (14 arms) failed with no model output:
- **Arm A:** one call each, refused with "You've hit your 5-hour limit". Cost $0.
- **Arm B:** zero calls (`RoutingError`, every routed model refused). Cost $0.

The tasks affected are p10–p13 (all of `coding_plus_external`, including p12) and p14–p16 (all of
`vague_needs_answer`). p01–p09 completed normally, with no errors.

## The deviation

The pre-registration says each (task, arm) runs once, and a crash counts as a failure, not a
retry. Those 14 arms did not run: no request reached a model, and no output exists to retry
away. So they are treated as **interrupted** and completed **once** with the harness's own
resume mechanism. The frozen code, harness, tasks and scoring are unchanged.

- **The original records are preserved.** They are in
  `raw_first_attempt_with_quota_failures.jsonl`, all 32 lines exactly as written.
- **`raw.jsonl` now holds the 18 completed arms.** The resumed run appends the 14 missing ones.
- **If the limit is hit again,** the affected arms are reported as lost to the quota, not as CI
  failures. They will not be attempted a third time.

## How it is reported

The results report all three of the following:
- **The primary analysis:** the completed run.
- **The strict pre-registered reading:** quota failures counted as failures in both arms. This is
  symmetric, so it adds only concordant pairs.
- **A per-protocol analysis on p01–p09 alone.**
