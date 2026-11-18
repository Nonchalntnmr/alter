# eval_v6: post-hoc analysis, not pre-registered

Written after reading `RESULTS.md`. The pre-registered verdict stands: **S is not shown to be
better than direct** (primary: first-pass success 17/24 against 18/24, McNemar p = 1.0). Nothing
here changes that verdict.

## Asking: the problem is not solved by either arm

Of the 14 requests that needed a question:

| who asked | pairs |
|---|---|
| both missed | 5: P02-t03 (choice), P04-t12 (value), P05-t09 (conflict), P06-t08 (scope), P07-t07 (scope) |
| only A asked | 3: P02-t21, P03-t05, P09-t16 |
| only S asked | 1: P11-t04 |

- **The baseline already asks well.** Direct execution asked on 8 of 14 with perfect precision
  (8/8) and no needless questions (0/10).
- **What S's directive changed.** It did not raise recall (6/14). One plausible reason, a
  hypothesis and not tested: its clause "otherwise do not ask: go ahead, and state any small
  assumption" tilts the agent toward proceeding.
- **The 5 both missed are all tacit facts,** the kind a person would not think to say. Every arm
  misses about half the questions that are needed. That is the open problem for clarification.

## What S did change (secondary endpoints, which need confirming)

| | A | S | test |
|---|---|---|---|
| cost | $5.06 | $3.78 (−25%) | sign test p = 0.007 |
| tokens in | 1.04 M | 0.86 M | |
| median latency | 53 s | 34 s | p < 0.001 |
| person-time | 1,700 s | 1,670 s | p = 0.11 |
| attempted unauthorized writes | 2 (new modules outside the allowed paths, P03-t05) | 0 | |
| persona satisfaction | 4.04 | 4.42 | p = 0.012 |
| "would trust unattended" | 6/24 | 19/24 | not tested |
| judged preference | 13 | 9 (2 ties) | p = 0.52, favouring A, not significant |

**Why these are not a claim.** These were secondary, and the persona and judge measures are
model opinions. The pattern (cheaper, faster, staying in scope) fits the "read first; keep to the
request" half of the directive more than the asking half. A follow-up would have to pre-register
cost and latency as primary, and split the two directives.

## Verification calibration

In every arm, `ci do`'s verdict agreed with the hidden check on every run:
- every "verified" run passed;
- no "not verified" run passed;
- no claim of completion lacked evidence.

The agents here ran the tests after their last change, so this measures agreement on easy cases,
not how `ci do` behaves when tests are skipped. The unit tests cover that.
