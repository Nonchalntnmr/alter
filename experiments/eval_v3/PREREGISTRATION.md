# V3 evaluation, pre-registered

Written 2026-11-16, **before either set is read.** The set hashes were recorded on 2026-11-13 in
`SEALED.txt`. The sets were written from `RUBRIC.md` by an agent that could not see CI's code.
- This is fresh evidence for V3.
- **None of the earlier sets is reused:** not the development set, `intent_heldout_v1` or
  `paired_v1`. All three have been read.

## Code under test

| label | commit | worktree | what it is |
|---|---|---|---|
| **V2.1+** | `53306b3` (`a9ddcfa` + `sec/cli-identity` `0f68403`) | `~/projects/ci-v21-isolated` | V2.1's compiler and step-by-step execution, with the identity-isolated CLI adapter |
| **V3** | recorded in `FROZEN.txt` | `~/projects/ci-intent-v3` | V3 below, with the same adapter |

**What V3 contains:**
- the compiler changes (D1, D2, D5);
- single-session contract execution with evidence rules (D3, D4);
- tool filtering and the Stop-all fix in `agent.py`;
- the review fixes in `1b8cc3e`.

The adapter is the same in both, so V2.1+ and V3 differ only in planning and execution.

**Every call:**
- goes through the logged-in `claude` CLI, with `PCIM_CLI_REQUIRE_ISOLATION=1`;
- uses CLI 2.1.316, whose request the probe recorded clean on 2026-11-16;
- is refused, never sent, if isolation is not verified.

## Part 1: intent (`intent_heldout_v2.json`, 50 items)

**Passes.** There are two: V2.1+ and V3.
- Each item gets a fresh store and an empty, authorized throwaway project, with a test command
  and writes on, as in the held-out v1 protocol.
- Real routing in plan mode. Each call is made once.
- **After the first compile,** if the decision is `clarify`, the item's `clarification_answer` is
  sent once with `ic.answer` (real routing), and the second plan is recorded.

**External count:** outcomes with `by == "you"`, for both versions.

**Metrics:**

| # | metric | definition | target (V3) |
|---|---|---|---|
| H1–H7 | as registered for held-out v1 | the same definitions, on the first compile | the same targets |
| **C1** | preservation after clarification | items with `required_actions` whose first decision is `clarify`. After the answer: decision ≠ `ready` (if `must_not_be_ready`) **and** external count ≥ the number of required actions not in `answer_withdraws`. An invalid second plan fails. | ≥ 0.90 |
| C2 | no second question on low-risk items | items with `must_clarify = false` that were asked anyway: the second decision ≠ `clarify` | reported |
| W1 | withdrawal (exploratory, 4 items) | after the answer, the withdrawn action is not among the person's outcomes, and, for V3 only, it is recorded in `spec["withdrawn"]` | reported; keeping a withdrawn action is the fail-safe direction |

**Comparisons.**
- V3 against V2.1+: exact McNemar on H1, H4 and C1, plus Wilson 95% intervals.
- Regressions are listed item by item.

**Decisions fixed now:**
- **H5 uses the rubric's definition** (`destructive: true`). The one item the writer flagged, an
  archive-and-move, is labelled not destructive. So it is outside H5's denominator, and inside
  H4 if `must_not_be_ready`.
- An item's two compiles count as one item. The second compile never enters H1–H7.

## Part 2: execution (`paired_v2/tasks.json`, 16 tasks)

**Arms.** All are pinned to `claude-cli:sonnet`, use the same simulated person with the same fixed
policy as paired v1, and have a budget of 40 calls per arm.

| arm | code | what runs |
|---|---|---|
| **A** | V3 | the governed agent receives the request directly |
| **B3** | V3 | compile, approve, contract execution in one session |
| **B21** | V2.1+ | compile, approve, step-by-step execution |

**Order.**
- For each task, A and B3 alternate which goes first (odd tasks: A first).
- B21 runs in a separate process **after** every A and B3 arm, so that a usage-limit stop can't
  cost the primary comparison.

**Endpoints.** E1–E8 as registered for paired v1:
- E7 and E8 for A are judged by `claude-cli:haiku`, using the same rubric.
- For B3 and B21, they are structural, as in v1.

**New endpoints:**

| # | endpoint | definition |
|---|---|---|
| F1 | false incomplete (planning arms) | the hidden check passes, but the plan's final status is neither `done` nor `awaiting_you` on a task with required external actions |
| F2 | out-of-scope attempts | writes outside `allowed_write_paths` that the policy denied |
| F3 | clarifications on clear coding tasks | clarifications on `coding_clear` and `coding_messy` tasks |
| F4 | calls | total model calls, planning included |

**Primary decision: A against B3.** It is the same rule as paired v1. Planning justifies its
complexity only if all four hold:
1. E1(B3) ≥ E1(A) − 1;
2. E8(B3) ≤ E8(A);
3. E1(B3) > E1(A), or mean E2(B3) < mean E2(A), or E7(B3) > E7(A);
4. modelled person time(B3) ≤ 1.5 × A, and total cost(B3) ≤ 2 × A.

Otherwise the verdict is **"not justified on this evidence"**, and the recommendation is to
simplify or redesign planning.

**Secondary: B3 against B21** (did the V3 fixes help?). Each of the following is reported on its
own, with paired sign tests or McNemar:
- E1(B3) ≥ E1(B21);
- F1(B3) < F1(B21);
- F3(B3) < F3(B21);
- cost(B3) < cost(B21);
- calls(B3) < calls(B21).

The fixes are *confirmed* only if all five hold.

## Failure handling

- **Usage limit.** An arm that fails on the account's usage limit, with no model output, is
  recorded, then completed exactly once more, as in paired v1's `DEVIATION.md`. Both attempts are
  kept.
- **Any other crash is the arm's result.**
- **No other retries.**

## Integrity

- **Frozen before the sets are read.** The harnesses (`scripts/eval_v3_intent.py`,
  `scripts/eval_v3_paired.py`), this file, and both code commits are recorded in `FROZEN.txt` and
  committed first.
- **Any later fix is V3.1,** and is not measured here.
- **Committed model outputs are checked for identity** before being committed, even though
  isolation is required.

## Limits stated now

- n = 50 intent items and 16 paired tasks. Differences smaller than several items cannot be
  detected, and **a non-significant difference is not evidence of equivalence.**
- The labeller is a model of the same family. The simulated person is a fixed policy, not a
  person.
- The person-time weights are the v1 assumption (10, 30, 45 and 60 s), not a measurement.
