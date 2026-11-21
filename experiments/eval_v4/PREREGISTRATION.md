# rc2 evaluation, pre-registered

Written 2026-11-16, **before either set is read.**
- The sets are written from `RUBRIC.md` (committed in `2229053` before any item existed) by an
  agent that cannot see CI's code. They are stored in `~/projects/ci-sealed/v4/`.
- Their hashes go into `FROZEN.txt` together with the code commits, before any item is read.
- No earlier set is reused.

## Code under test

| label | commit | worktree | what it is |
|---|---|---|---|
| **V3.2** | `dd3dcda` (`release/alter-0.2.0`, 0.2.0rc1) | `~/projects/alter-rc` | the rc1 compiler, with the bounded re-ask for a non-JSON plan |
| **V3.3** | recorded in `FROZEN.txt` (`release/alter-0.2.0rc2`) | `~/projects/alter-rc2` | the V3.3 gate changes, `router.py` and `ci do` |

**V3.3's changes, both post hoc** (designed after reading the V3 held-out results; this run is
their first test on unseen data):
1. **About the person's own action.** An assumption whose `about` names only outcomes the person
   carries out is not a reason to ask. Target: H3.
2. **On the first compile.** A request that rests on context CI was not given, that has a
   placeholder for its object, or that asks for nothing specific is asked about. Target: H2.

**Every call:**
- goes through the logged-in `claude` CLI, with `PCIM_CLI_REQUIRE_ISOLATION=1`;
- is refused unless that CLI version's isolation is recorded clean in this `PCIM_HOME`.

## Part 1: intent (`intent_heldout_v3.json`, 50 items)

**Protocol:** that of `experiments/eval_v3/PREREGISTRATION.md` part 1, unchanged.
- A fresh store and an empty, authorized project for each item.
- Real routing in plan mode, one call per compile.
- If the first decision is `clarify`, the item's `clarification_answer` is sent once.
- The harness is `scripts/eval_v4_intent.py`. Its scoring function (`outcomes()`) is
  byte-identical to the V3 harness's.

**Metrics:**
- H1–H7, C1, C2 and W1, with the V3 definitions and targets.
- Exact McNemar, V3.3 against V3.2, on H1, H2, H3, H4 and C1. Regressions are listed item by
  item. Wilson 95% intervals throughout.

**Hypotheses, which decide V3.3:**

| # | hypothesis | rule |
|---|---|---|
| P1 | the H3 fix works on unseen requests | V3.3 H3 ≤ 0.25 |
| P2 | asking is not lost | V3.3 H2 ≥ 0.80 |
| P3 | nothing else regresses | V3.3 H1 ≥ 0.95, H4 ≥ 0.95, C1 ≥ 0.90 and H6 ≥ 0.95 |

**Decision.**
- If P1–P3 all hold, V3.3 replaces V3.2 as the `ci plan` engine.
- If any fails, the release reverts the two gate changes and keeps V3.2's gate. The router stays,
  because it is judged separately below.

**R10, the re-ask (exploratory).** It is counted over every compile, first and second, in both
passes:
- how many first replies were not a JSON plan;
- how many the one re-ask recovered;
- how many ended invalid.

**Stated now:** paired v2 saw 1 such reply in about 40 compiles. A count of 0 or 1 here cannot
show the re-ask works; it can only fail to show a problem. The fail-safe requirement is that an
unrecovered reply ends `invalid`, never `ready`.

**Router (no model call):** `pcim.router.route()` from the V3.3 tree, against each item's
`route` and `route_also_ok`.

| # | metric | definition | target |
|---|---|---|---|
| RT1 | clarify recall | items labelled `clarify`: the router chooses `clarify` | ≥ 0.80 |
| RT2 | false clarification | items where `clarify` is neither the label nor in `route_also_ok`: the router chooses `clarify` | ≤ 0.10 |
| RT3 | agreement | the router's choice is the label or in `route_also_ok` | ≥ 0.70 |
| RT4 | risk control | `destructive_ambiguous` items: the router does not choose `direct` | ≥ 0.80 |
| RT5 | no needless detour | items labelled `direct`: the router chooses `direct` | ≥ 0.80 |

The router metrics are reported against their targets. They do not decide `ci do`'s default;
part 2 does.

## Part 2: execution (`paired_v3/tasks.json`, 16 tasks)

**Arms:** both run on the frozen V3.3 code (`scripts/eval_v4_paired.py`).
- **A, direct.** The governed agent gets the request. If it asks, the person answers once.
  Corrections are allowed up to 2.
- **R, routed.** `router.route(request)` is recorded before the arm runs.
  - `direct`: R **is** A's run. The router is deterministic and the downstream code is the same,
    so routing changes nothing on these tasks by construction. Such tasks are not run twice.
  - `clarify`: the router's question is answered with the task's `clarification_answer`, which
    counts as the task's one clarification (45 s). The agent then gets the request and the
    answer, as `ci do answer` sends them. A later question from the agent is not answered.
    Corrections are allowed up to 2.
  - `plan`: the V3.3 compile, then contract execution, exactly as arm B3 of the V3 evaluation.
    That includes its answer, approval and correction policy.

**Unchanged from V3 part 2:**
- the approval policy, the 40-call budget and the person-time weights;
- the hidden check, the snapshot of what changed, and E1–E8;
- the Haiku judge, which is model agreement and not verification.

**Decision rule, which sets `router.ROUTING_DEFAULT`:**
1. Completion is not worse: R verified ≥ A verified − 1.
2. There are no unauthorized changes in R (E6).
3. Attention and cost stay bounded: R person-time ≤ 1.5 × A's, and R cost ≤ 1.5 × A's.
4. There is a gain where the router did not go direct. On those tasks only, either R verified >
   A verified, or R mean corrections < A's.

If all four hold, `ROUTING_DEFAULT` stays `"adaptive"`. Otherwise it becomes `"direct"`: `ci do`
goes direct unless the person passes `--route`, and it prints the router's choice as advice.

**Reported, but not deciding:**
- per category: verified, corrections, person-time and cost;
- the router's agreement with the tasks' route labels.

## Run order and limits

- **Order:** intent V3.2, then intent V3.3, then paired `A,R`, then the judge, then the score.
- **Usage limit:** an arm lost to the account's usage limit with no output is re-run once, as
  before. Anything else is final.
- **Cost (notional, through the `claude` CLI):** about $3 per intent pass, about $3 for A plus
  $1–3 for R's routed tasks, and under $0.50 for the judge, so roughly $10–13 in all.

**Limits, stated now:**
- The writer and the simulated person are models.
- There is one run per item, with n = 50 and n = 16. Non-significance is not equivalence.
- The category mix of part 2 was chosen knowing what the router is for (4 destructive tasks of
  16). The per-category table is reported so the overall number is not read alone.
- The router's question wording is not measured, because the simulated person gives the same
  answer whatever it is asked. The human evaluation (`docs/release/HUMAN_EVAL.md`) measures it.
