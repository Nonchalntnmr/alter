# eval_v5, pre-registered

Written 2026-11-17, **before the task pool is read.**
- The pool's hash, the code commit and the seed go into `FROZEN.txt` before anything runs.
- `PROTOCOL.md` is the design.
- This is engineering validation with simulated users. No outcome here licenses a release claim
  without the human evaluation.

## The question

Is any arm **demonstrably better than direct execution (A)** on this evaluation?
- **PA**, the planning-assisted router: the primary candidate.
- **P**, V3.3 planning.
- **R**, the keyword router, the preview's default.

## Primary endpoint

**Blinded pairwise preference.** For each person–task pair, the majority preference of the three
judges between X and A. Ties go to "tie". Where R is A's run, the pair is a tie.

The test is an exact two-sided sign test on X-preferred against A-preferred, ties excluded. It is
Holm-adjusted across the family {PA, P, R}.

## "Better than direct", decided per arm

All five must hold:

1. **Success is not worse:** X verified ≥ A verified − 1, by the hidden check.
2. **No unauthorized changes and no change outside the repository** in X.
3. **Attention is bounded:** X person-time ≤ 1.25 × A's.
4. **Cost is bounded:** X cost ≤ 1.5 × A's.
5. **X is preferred to A** more often than the reverse, with a Holm-adjusted p < 0.05.

If all hold, the verdict is "better than direct on this synthetic evaluation". Otherwise it is
"not shown to be better than direct". A non-significant result is reported as that, never as
equivalence.

## Secondary, reported but not deciding

| endpoint | analysis |
|---|---|
| success per arm | Wilson 95%; exact McNemar against A |
| question correctness (asked iff a question was needed) | exact McNemar against A |
| clarification recall; unnecessary-question rate | Wilson 95%, per arm |
| corrections, person-time, latency, cost, persona satisfaction | per-pair difference against A; sign test |
| tokens in and out | totals |
| the judges' majority flags (false claim, unnecessary question, missing question) | counts |
| agreement between the three judges | Fleiss' κ, per item |
| per category | counts |

## What the result changes

**No default changes without the owner's decision.** The keyword router stays `ci do`'s default
for the restricted preview, as instructed.

| verdict | consequence |
|---|---|
| PA "better than direct" | a recommendation: make PA the preview's default, and put PA against A in the human evaluation |
| PA not shown better | PA stays opt-in, and the next design step is reported from the per-category and judge data |
| P or R "better than direct" | reported the same way |

## Fixed now

- **Reuse.** R reuses A's run when the keyword router routes direct. The router is deterministic
  on the same text, and the downstream is the same, so this is identical by construction. PA
  always runs, because its route comes from a model call.
- **Order.** Requests first, then the person–task pairs in assignment order. Within a pair, the
  arms run in an order drawn from the seed.
- **Losses.** A run lost to the environment is run once more. A usage-limit hit stops the run, to
  resume later. Anything else is final.
- **Models and versions.**
  - the agent and planner: `claude-cli:sonnet`;
  - the persona: `sonnet`;
  - the judges: `haiku`, `sonnet` and `opus`.

  The served model ids are recorded per call where the CLI reports them. The CLI version is in
  `FROZEN.txt`.

**Cost estimate, notional through the `claude` CLI:**
- about 75 runs at $0.15–0.35 each;
- about 150 judge calls;
- about 150 persona calls;

so roughly $25–40, over 3–5 hours.

## Limits

These are restated from `PROTOCOL.md` §4:
- the people and judges are models;
- n = 24, one run each;
- blinding is imperfect for planning arms;
- the needs-question label rests on the persona's own report of what it said;
- agent questions inside plan execution go unanswered.
