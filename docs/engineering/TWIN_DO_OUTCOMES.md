# The cognitive twin and `ci do` work: an investigation and a design (not built)

Written 2026-11-18. **Status: a proposal.** Nothing here is implemented or measured.

## What the twin sees today (read from `src/pcim/twin.py`)

`ci weekly` builds its review from four sources:
- routed text interactions (`ci "…"`, through `evaluation.window_interactions`);
- practice attempts (`ci practice`);
- outcomes the person recorded (`ci outcome`);
- projects.

It makes no model call unless `--deep` is given, and every statement carries its counts.

**It never sees `ci do`.** Work done in a folder (the request, its route, the questions asked, the
corrections, and since `7e77b1d` whether the tests passed after the last change) is in the
`pcim-do` records and in the agent sessions. The twin reads neither. So the part of CI most
likely to save the person effort is the one part the twin cannot learn from.

## What would make it useful, measurably

| question the twin could answer | the evidence it would use | why it is useful |
|---|---|---|
| "Which kinds of request needed a correction or a question this week?" | `ci do` records: route, answers, corrections, verification | It shows where the person's phrasing, or CI's routing, costs them time. This is the recurring-inefficiency item in §7. |
| "Which requests were never verified?" | `verification.verdict == not_verified` | Unverified changes are risk the person may not know they carry. |
| "What do you repeat?" | the twin's existing `_repeats` (embedding similarity) over `ci do` texts | It is a candidate reusable automation. It is proposed only, never run. |

## Consent and control, which come first

- **Default off.** The twin reads `ci do` records only after `ci twin include do`. That command
  states what is read (request text, route, counts, verification), and its reverse is
  `ci twin exclude do`.
- **Local only.** Plain `ci weekly` sends nothing. `--deep` would send counts and the person's own
  request texts only to a vendor allowed by that project, exactly as interactions are filtered
  today.
- **Removable.** `ci do forget` (`9b8cae3`) removes a request and its conversation. The next
  weekly review must not count it. That needs a test.
- **No silent retention.** The twin stores weekly snapshots of counts, not request texts.

## How it would be tested before it ships

1. **Unit tests.**
   - Excluded by default.
   - Counts match the records.
   - A forgotten request disappears from the next review.
   - `--deep` withholds texts from projects that do not allow the vendor.
2. **A usefulness check, pre-registered** (a small human study, or a part of `HUMAN_EVAL.md`). It
   asks whether a review that includes `ci do` items leads the person to change something, such
   as how they phrase requests or whether they run tests, more often than a review without them.
   Self-reported liking does not count: the twin's own rule is "not tuned for approval".

## Why it is not built now

The directive ranks safety, task completion, missed clarifications and reliability above the twin.
eval_v5 is still measuring the routing questions that decide what `ci do` records are worth
learning from. This becomes the next twin item once eval_v5 and the human evaluation report.
