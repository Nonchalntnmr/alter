# Deviation: losses to the machine sleeping

Written 2026-11-17, **before any lost item or task is run again.**

## What happened

The laptop's lid was closed at 17:14 on 2026-11-16, during the V3.2 intent pass. macOS's
power log records "Entering Sleep state due to 'Clamshell Sleep'". The machine was on battery,
and from then on it cycled between sleep and short dark wakes. The runs kept going in the wakes.

| run | lost | how it was recorded | what was lost |
|---|---|---|---|
| intent V3.2 | q35–q41, 7 consecutive items | `RoutingError: every routed model failed`, each after about 350 s | **0 model calls, $0**, no model output at all |
| intent V3.3 | nothing | 50/50 compiled, no error | none |
| paired | `r01 A` | `ClaudeCLIError: … Claude Code is still loading your organization's settings` | no plan or work; 2 calls, $0.010 |
| paired | `r02 A` | `ClaudeCLIError: … API Error: Your computer went to sleep mid-response` | the reply cut off; 3 calls, $0.021 |
| paired | `r04` onward | the run was stopped by hand, during `r04 A` and before it recorded anything | not yet run |

`r03 A` completed normally and was verified. `r01`–`r03` were routed `direct`, so R is A's run
there and inherits the same loss.

## The rule, extended

The pre-registered rule re-runs once an arm "lost to the account's usage limit with no output".
Anything else is final. These losses are the same kind of thing: the environment failed, not the
code under test. So a record counts as **lost to the environment** if its error matches one of:
- `went to sleep`;
- `is still loading your organization's settings`;
- `RoutingError` with 0 model calls and $0 cost.

Such a record is run again **once**, as `attempt = 2`, after the machine is awake, its lid is
open, and nothing else has changed:
- the same frozen code;
- the same harness files (hashes unchanged);
- the same sets.

A record with any other error stays final.

**How the re-runs are made.** `scripts/eval_v4_rerun.py` imports the frozen harnesses unchanged
and calls their own `check()` and per-item functions. It refuses anything that does not match a
signature, anything already at attempt 2, and any run while the lid is closed. Its own SHA-256
is recorded in `RERUN.txt` when it runs.

**Both analyses are reported.**
- *As registered*: losses count as failures.
- *With re-runs*: the last attempt counts.

The re-run analysis is primary, because it is what the registered usage-limit rule is for. Where
the two disagree on a decision, both are stated.

**What this cannot change:**
- P1–P3 judge V3.3 only, and V3.3 lost nothing.
- On the 2 lost paired tasks, A and R are the same run, so R minus A is unaffected there. A's
  absolute count is affected.

## For the rest of the run

The remaining tasks and re-runs start only with the lid open. They run under `caffeinate -i`,
and the re-run script checks the lid state first.
