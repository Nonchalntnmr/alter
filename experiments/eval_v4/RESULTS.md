# rc2 evaluation: results

The run followed `PREREGISTRATION.md`. The code was frozen at `a46b960`; the sets, harnesses and
code were frozen in `FROZEN.txt` (`363214f`) before any item was read.

**Losses to the environment.** The machine slept with its lid closed, and 9 records were lost
(`DEVIATION.md`). Each was re-run once (`RERUN.txt`), and every re-run succeeded. Both analyses
are given below. They agree on every decision.

## Part 1: intent (50 new requests)

| metric | target | V3.2 (rc1) | V3.3 (rc2) | McNemar, V3.3 against V3.2 |
|---|---|---|---|---|
| H1: requested actions kept, plan not ready | ≥ 0.95 | 23/23 | **23/23** | no discordant items |
| H2: asks when it must | ≥ 0.80 | 20/23 | **20/23** [0.68, 0.95] | 1 against 1, p = 1.0 |
| **H3: asks when it need not** | ≤ 0.25 | 10/21 = 0.48 | **1/21 = 0.05** [0.01, 0.23] | **0 against 9, p = 0.0039** |
| H4: not ready when it must not be | ≥ 0.95 | 38/38 | **38/38** | none |
| H5: destructive, not ready | 1.0 | 8/8 | 8/8 | |
| H6: valid plan | ≥ 0.95 | 50/50 | **50/50** | |
| H7: no invented external action | ≥ 0.90 | 10/10 | 10/10 | |
| C1: actions kept after an answer | ≥ 0.90 | 23/23 | **9/9** | none |
| W1: withdrawal honoured (exploratory) | | 2/4 | 1/2 | |
| calls and cost | | 85, $2.92 | 72, $2.57 | |

**As registered**, with V3.2's 7 losses counted as failures, V3.2 scores H2 13/23, H6 43/50 and
C1 20/20 (`intent/results_as_registered.json`). The H2 "gain" in that analysis is an artifact of
the losses. With the re-runs, H2 is equal.

**Decision: P1, P2 and P3 all hold, so V3.3 replaces V3.2 as the `ci plan` engine.**
- The H3 fix (an assumption about the person's own action is not asked) works on unseen
  requests.
- The H2 rule (asking about context CI was not given, or a placeholder object) adds no
  measurable recall: H2 is unchanged.
- There is one regression item by item: q08 H2, "handle what marco asked for…", which V3.2 asked
  about and V3.3 did not.

**R10, the bounded re-ask.** Not one first reply out of 157 compiles was non-JSON. The re-ask was
never exercised, so this neither shows that it works nor finds a problem. As stated beforehand,
it remains measured only by unit tests.

**Router** (deterministic, no model call), against the set's route labels:

| metric | target | result | |
|---|---|---|---|
| RT1: asks when the label says ask | ≥ 0.80 | **4/21 = 0.19** [0.08, 0.40] | missed |
| RT2: asks when the label says don't | ≤ 0.10 | 0/21 [0.00, 0.15] | met |
| RT3: agrees with the label | ≥ 0.70 | **33/50 = 0.66** [0.52, 0.78] | missed |
| RT4: destructive and ambiguous, not direct | ≥ 0.80 | **3/7 = 0.43** [0.16, 0.75] | missed |
| RT5: direct when the label says direct | ≥ 0.80 | 25/25 [0.87, 1.00] | met |

**Diagnosis**, written after reading the set, so it is post hoc. The router sent 17 of the 21
"ask first" requests direct:

| kind of request | count | example |
|---|---|---|
| conflicting constraints | 6 | "don't change the public api … but get_items needs to return a dict" |
| a vague goal | 5 | "make the report better", "handle what marco asked for", "fix it the same way you did last time" |
| irreversible with a single target | 3 | "reset the database" |
| other | 3 | |

V3.3's compiler asked on 14 of these 17 on its first compile. The ambiguity is detectable, but
by a model, not by word rules. **The router is safe but nearly inert.**

## Part 2: execution (16 new coding tasks with hidden checks)

| | A: direct | R: routed |
|---|---|---|
| verified (hidden check) | **16/16** [0.81, 1.00] | **16/16** [0.81, 1.00] |
| corrections | 2 | 1 |
| clarifications, approvals, plan reviews | 6, 50, 0 | 6, 47, 2 |
| person-time (weighted, s) | 890 | 860 |
| cost, calls | $2.93, 115 | $2.64, 109 |
| unauthorized changes (E6) | 0 | 0 |
| judged: outside actions surfaced; false claims of completion | 3/3; 0 | 3/3; 0 |

**Where routing acted.** The router left direct on 2 of 16 tasks, both `destructive_ambiguous`.
`r13` and `r14` went to plan.

| on those 2 tasks | A | R |
|---|---|---|
| verified | 2/2 | 2/2 |
| corrections | 1 (on `r14`) | 0 |
| person-time (s) | 230 | 200 |
| cost | $0.65 | $0.36 |

The 5 tasks labelled `clarify` were routed direct. There the agent asked its own question, and
A and R are the same run.

**Decision rule:** all four conditions hold, in both analyses. As registered, `r01` and `r02` are
lost in both arms, and A and R are 14/16 each. So `ROUTING_DEFAULT` stays `"adaptive"`.
- **The gain behind condition 4 is one correction on one task.** The sign test on corrections
  gives p = 1.0.
- Routing changed behaviour on 2 tasks of 16.
- This is a pass by the letter of the rule. It is not evidence that routing improves outcomes in
  general.
- What the run does support:
  - routing is harmless here, with nothing worse and slightly cheaper on the tasks it planned;
  - direct execution is excellent on this kind of task, with 32/32 verified across A in paired v2
    and v3.

## Limits

- The set writer, the simulated person and the judge are models.
- Each item was run once, with n = 50 and n = 16. Non-significance is not equivalence.
- The task mix was chosen knowing what the router is for, so read the per-category table in
  `paired/results.json`.
- The router's question wording is unmeasured. The human evaluation measures it.
