# Intent compiler, plan execution and the compute mesh

Branch `feat/intent-compiler`, from `main` at 9085adc (PRs #5 and #6 merged). Written 2026-11-13.

Every number here was measured in this branch. A pre-registered *development* evaluation is
not a confirmatory test, and nothing here has been evaluated with people.

## 1. What already existed (Phase 0)

| Brief asks for | Already on main | This branch |
|---|---|---|
| Model routing and fallback | task-aware routing with a learned estimator (sealed benchmark passed), fallback across models and vendors | reused |
| Execute / Amplify / Learn | `ci -e/-a/-l`, plus `ci challenge` | `ci learn` alias; a fifth, internal `plan` mode that cannot be made a default |
| Skill tracking, `ci weekly` | attempt log, alone-vs-with-help, FSRS spacing, weekly review with versioned priorities | `ci twin` view |
| Contextual retrieval | consent-filtered, egress-checked selection, local embeddings | reused for planning |
| Workspace autonomy, security | governed tool loop, approvals, grants, halt, audit chain | `ci execute` runs on it unchanged |
| Intent → plan | nothing (only a task-type classifier and a VOI rule in `intent.py`) | **built** (`intent_compiler.py`, `ci plan`) |
| Plan → execution | nothing | **built** (`plan_exec.py`, `ci execute`, `ci approve`, `ci deny`) |
| Compute discovery and placement | model availability only | **built** (`mesh.py`, `ci mesh`) |
| `ci audit` | `pcim audit` existed, not in `ci` | `ci audit` |

## 2. The intent compiler

**Problem.** The inputs are a request *x* (possibly one word), the person's consent-filtered
context *C*, and possibly a project briefing *B*. The compiler produces:
- a specification *S*: goal, typed **outcomes**, constraints with provenance, success criteria,
  steps as a DAG with action kinds, and which outcomes each step serves; assumptions with
  confidence and stakes; conflicts; questions;
- a decision *d* ∈ {ready, clarify, approval, blocked}.

**Outcomes are typed** as `deliverable`, `external_action` or `destructive_change`. Each one
says who can complete it:
- CI, for a deliverable;
- you, for any external action (CI has no connectors);
- CI with your approval, or else you, for a destructive change.

Drafting something is preparatory, and never a replacement for sending or posting it.

**Intent preservation (V2, deterministic).** The person's own words are read for action verbs:
- verbs at the start of a clause, after polite prefixes, with one-edit typos tolerated, unless the
  "typo" is itself a common word ("look" is not "book"; V2.1, after the held-out run);
- questions *about* an action, negations, things already done and other senses of a verb
  ("order the list by date") are skipped.

Every requested action the plan has no outcome of the same kind for is **restored** as an
outcome (`oR1`, …) and flagged. An external step that serves no outcome gets one (`oS<step>`).
A model that files "send the summary" as a deliverable has not hidden the send: the kind
decides.

**Gate.** The model proposes p(g | x, C, B) and the rest of *S*. The decision is not the model's:

    clarify   if  max p < 0.6,  or  p(1) − p(2) < 0.2,  or  a consequential assumption has conf < 0.8,
              or  constraints conflict,  or  there are neither steps nor outcomes
    blocked   else if a step needs what the workspace does not grant (no authorized folder for
              read/run/write steps, no test command for run, writes off for write)
    approval  else if a step runs or writes, or any outcome is yours, or any outcome is destructive
    ready     otherwise

Ambiguity that would not change what happens is resolved and listed, never asked.

**Checks the model cannot talk its way past:**
- *Provenance:* a constraint "from your notes" must cite notes actually sent.
- *Unknown kinds:* an unknown step kind is treated as `external`, and an unknown outcome kind
  as `external_action`.
- *Bad graphs:* unknown dependencies and cycles reject the plan.
- *Unusable replies:* a reply that is not a plan is stored as *invalid*, never repaired by guessing.

**Tests.**
- 20 in `tests/test_intent_compiler.py`;
- 58 in `tests/test_intent_preservation.py`: requested-action extraction, the things it must
  ignore (including real words one letter from a verb), restoration, idempotence, relabelled outcomes, destructive changes, conflicts and
  permissions.

**V3 changes to the compiler** (`feat/intent-v3`). Each is pinned by a test that fails on the
version before it.
- **Code and technical prose are not requests.**
  - A clause-initial word followed by `(`, wrapped in backticks, or followed by a finite verb
    ("**reserve** raises…") is a subject or identifier, not an imperative. So is an inflected word
    followed by a relative word ("**Calls** that don't pass…").
  - A base-form verb followed by "that" or "when" is still a request ("send that to priya",
    "post that", "send when ready").
  - Paired v1's false "yours to do" items came from this.
- **Actions survive clarification.**
  - When a plan is recompiled from an answer, the parent plan's external and destructive outcomes
    carry over unless the answer explicitly takes them back.
  - "Explicitly" means the action's own verb, negated: "don't send it", "skip the email". For an
    action with no known verb, its first word plus another of its words: "don't create the Jira
    ticket".
  - Every withdrawal is recorded in `spec["withdrawn"]` and in the flags, never silently.
  - Paired v1's p12 lost a Jira ticket this way.
- **No second question about the code.** After an answer, low-stakes ambiguity no longer keeps a
  plan in `clarify`, unless the plan has a consequence CI itself would carry out, or the request
  is nothing but the person's own action ("send the report": then it still asks who to).
  - The plan is never `ready` while it has an action for the person.
  - Paired v1 lost 5 tasks to repeated questions.
- **The planner is told it cannot see the files,** and that any working directory it is told
  about is its own process's, not the project's. Paired v1 plans had planned around the CLI's
  sandbox directory.

## 3. Plan execution

**V3 replaced step-by-step execution with a contract executed in one session**
(`execution_mode: "contract"`, the default for every plan compiled from V3 on). The reason is
measured, not stylistic. In the paired experiment (§5c), step-by-step execution completed 9 of
16 tasks against direct execution's 16 of 16, at 3.6× the person's time and 3.8× the cost.
Plans compiled before V3 still run step by step, as described at the end of this section.

**Contract execution.**
- An approved plan becomes **one** message to the governed workspace agent, containing:
  - the goal;
  - the outcomes CI must achieve;
  - the outcomes that are the person's, which CI must not try to perform;
  - the constraints and success criteria;
  - an instruction to read, change, test, and report with tool evidence.
- The steps are the checklist, not separate turns.
- **Approving a plan authorizes nothing.** The governor decides every tool call again, under the
  current autonomy mode and grants. Plan code makes only one permissioned call itself:
  `agent.cancel`.
- The agent is offered only the tools the workspace enables: no `record_claim` without a claim
  ledger, no `write_file` with writes off, no `run_tests` without a test command.

**Three separate states, never collapsed into one:**

| state | what it records | how it is decided |
|---|---|---|
| **outcome success** (`met`, `unmet`, `awaiting_you`) | whether the requested work happened | from task records only, never from the model. A CI outcome is met only if the session made a change (when the plan changes files), every change succeeded, and **the tests passed after the last change** whenever the workspace can run them. `judged_by` says that this is the suite passing, not a check of each outcome. The person's outcomes are `yours_pending` until `--done`. |
| **plan completion** | how much of the checklist finished | steps done out of total |
| **authorization compliance** | what was denied or out of scope | denials listed. Bookkeeping failures (`record_note`, `record_claim`) are listed separately and never decide an outcome. |

**The contract's own state:**

| state | meaning |
|---|---|
| `not_started`, `in_progress` | `in_progress` is written *before* the agent is called |
| `waiting` | an action needs you. Each pending action is shown with its tool and target, and the exact `ci approve` and `ci deny` commands. |
| `done`, `incomplete` | the evidence rule above decides |
| `failed` | the agent or the provider could not run the session |
| `halted` | Stop-all. A contract halted before its session starts records `halted` itself. |
| `interrupted` | found `in_progress` at the next run: the process died mid-session. **Never re-run silently.** |
| `cancelled` | `--cancel` |

**Rules:**
- `--retry contract` re-runs a failed, interrupted, incomplete or halted contract, keeping the
  earlier attempt. **During Stop-all a retry is refused** ("run ci resume first").
- `--cancel` closes the session **first**, then cancels its pending actions. So nothing resumes,
  and a late `ci approve` fails.
- **Stop-all:** `ci halt` cancels pending and running actions and blocks model calls. `ci resume`
  lifts it. The workspace agent now recovers by itself after Stop-all: pending actions that ended
  are handed back to the model as results. The strict xfail that pinned the old wedge now passes.
- **One run at a time per plan:** an OS lock on the plan, held by `execute`, `ci approve` and
  `ci deny`, and released if the process dies.
- **A plan is `done` only when every outcome is met or marked done by you.** Otherwise it ends
  by saying **"Not done"**, with its status.

**Parallelism: none.** Parallel execution is deliberately not built.

**Verified end to end.** `scripts/e2e_terminal.py` runs 32 checks through the real `ci` entry
point with a scripted model:
- approve, deny, `--done`, `--retry` and `--cancel`;
- a killed run reading `interrupted`;
- Stop-all, resume and retry;
- writes outside the folder, runs from another project, and reads outside an authorized folder;
- a provider failure;
- `mesh`, `twin`, `audit` and `learn`.

All 32 pass on V3.1 and on the integration branch. On V3 as frozen it was 30 of 32; the display
and the Stop-all retry were fixed in V3.1.

**Step-by-step mode** (plans compiled before V3). Each step is one agent turn, with states
`not_started`, `in_progress`, `done`, `incomplete`, `waiting`, `failed`, `halted`, `interrupted`,
`yours`, `yours_done`, `blocked` and `cancelled`. The same lock, retry, cancel and evidence rules
apply per step.

**Tests:** 26 in `tests/test_plan_exec.py`, plus the end-to-end script.

## 4. The compute mesh

**Discovery** reports what exists, separately and never summed:
- this device: cores, memory, accelerator;
- local embedding models;
- local model servers (Ollama, LM Studio, llama.cpp, plus any listed in `$PCIM_HOME/mesh.json`);
- the registry's cloud models with their availability.

**Placement** is lexicographic: privacy, then capability, then measured speed.

    eligible(r, w)  =  w ∈ workloads(r)  ∧  available(r)  ∧  sensitivity(w) ≤ ceiling(location(r))
                       ∧  tier(r) ≥ tier_min(w)  ∧  (r on-device ∨ vendor(r) granted)
    choose          =  argmin over eligible of (location order, −tier, −measured throughput)

The ceilings by location:

| location | max sensitivity |
|---|---|
| on-device, trusted LAN | sensitive |
| untrusted LAN, cloud | personal |

SECRET goes nowhere. A ceiling is never traded for speed. The egress guard still decides what
context any request carries.

**Measured on this machine** (MacBook Pro, Apple M-series, 10 cores, 16 GB, MPS present),
2026-11-13. These are medians, with the range across repetitions:

| Resource | Throughput | Method |
|---|---|---|
| CPU | 2,060 GFLOP/s (1,999–2,092) | float32 1024×1024 matmul, numpy, 5 reps |
| Local embeddings | 2,609 sentences/s (2,542–2,705) | all-MiniLM-L6-v2, batches of 64, 3 reps |
| Local model servers | none running | — |
| Cloud | not timed | `ci mesh bench --live` times one tiny call per model and is billed |

An earlier run the same day gave 1,957 GFLOP/s and 2,663 sentences/s. The spread between runs
is about 5%.

The consequence on this machine: a SENSITIVE generation has nowhere to run (`ci mesh place
generate --sensitivity sensitive` exits 1). Making that possible needs a local model server,
not code.

Tests: 8 in `tests/test_mesh.py`.
- Discovery is tested against a real local HTTP server.
- Placement is tested as properties: sensitive never leaves your machines, secret never runs.
- Benchmark files are written owner-only.

## 5. Intent evaluation (pre-registered, development set)

Pre-registration, hashes, results and the per-item table: `experiments/intent/`. One live run
on 2026-11-13 through the real routing (21 calls served by Sonnet 5.5, 3 by Opus 5.5) in a fresh
store with no personal memory. 24 items, 348 s wall.

| metric | result | target | |
|---|---|---|---|
| M1 usable plan rate | 24/24 | ≥ 0.95 | pass |
| M2 no silent external action | **5/6** | 6/6 | **miss** |
| M3 clarify recall, consequential-ambiguous | 8/8 | ≥ 0.8 | pass |
| M4 false clarification, clear low-risk | 2/6 | ≤ 2/6 | pass, at the limit |
| M5 one-word requests asked about | 4/4 | reported | — |

**The miss.** `tweet about the launch` was planned as drafting a tweet: four `think` steps, no
external step. So the gate found nothing to approve and returned *ready*. Nothing would have
been posted, because CI has no posting tool. But the half of the request that was posting was
dropped silently, and the person could reasonably think it had been done. The gate cannot catch
a step the model omitted.

**The fix (post hoc, unmeasured).** A deterministic tripwire reads the *request*. If it asks for
an action outside CI (send, email, post, tweet, publish, book, buy, pay, schedule, …) and the plan
has no external step, the plan cannot be ready. The dropped action is listed as yours to decide.
It was added after seeing this result, so it does not change the registered outcome: M2 stays a
miss. The tripwire was then replaced by typed preservation (V2, §2) and measured on a new,
independently labelled set (§5b).

**Also worth knowing:**
- The two false clarifications were "outline a literature review on alpha decay" and "draft
  three questions for my advisor". Both are reasonable to ask about (which papers, which
  identification strategy), but both are cost.
- Every one-word request ("crowding", "weekly", "taxes", "thesis") got a question rather than
  a guessed plan.
- These labels are the developer's own, on a 24-item set. The result shows the compiler is
  usable and catches what it was designed to catch. It does not show it generalizes.

## 5b. Intent evaluation (pre-registered, held-out set)

Pre-registration, protocol, hashes, replies, scores and the released set are in
`experiments/intent_heldout/`. Full tables with Wilson intervals are in
[INTENT_V2_REPORT.md](INTENT_V2_REPORT.md) §1.

**Design.**
- 50 messy requests, written and labelled from a fixed rubric by an agent that could not see
  CI's code.
- Frozen before the set was read: the set's hash and three code versions, V0 (cc21ffa), V1
  (ff74305, with the tripwire) and V2 (58348cc).
- Two live passes: 100 calls, $3.28 notional.

**Result.** Every version meets every registered target on the point estimate, and the set
barely separates them.
- **H1 intent preservation:** 17/17 for all three, because the development-set failure did not
  recur. 16 of the 17 passed because the plan asked a question, so the score cannot show that an
  action survives the answer.
- **H2 clarify recall:** 21/21 for all three.
- **H3 false clarification:** 2/13 for V2, 3/13 for V0 and V1.
- **H4 and H5 never ready:** 37/37 and 9/9.
- **H7 no false external:** 10/10 for V2, 9/10 for the others.
- **V2 against V0 and V1:** McNemar p = 1.0 on H1 and H4. V2 fixes one item and has no
  regressions.
- **Outside the registered metrics:** V1's tripwire wrongly blocked two informational questions;
  V2 does not.
- **Uncertainty:** the intervals are wide. The lower bounds for H1 (0.82), H4 (0.91) and H5
  (0.70) are below their targets.

**What the run found.**
- V2's typo tolerance read "look" as "book", and the same rule caught "test", "clean", "port"
  and others. V2.1 (a9ddcfa) excludes real words.
- Replaying V2.1's deterministic layer on the stored replies removes exactly the two spurious
  restorations and changes nothing else.
- That is post hoc, on a set that has since been read. It is not held-out evidence.

## 5c. Paired execution evaluations (v1 and V3)

Two pre-registered paired evaluations compared direct execution (`A`) against planning (`B`) on
matched coding and external-action tasks, judged by hidden verification checks (and Haiku model
judgement for surfacing/completion claims).

- **Paired v1 (`experiments/paired/`, 16 tasks, V2.1):** Direct `A` verified **16/16** ($3.31, 112
  calls, 48 s median) vs `B` **9/16** ($12.46, 230 calls, 127 s median); McNemar p = 0.016. Verdict:
  **"not justified."** Every verified `B` run still ended `incomplete` or falsely `awaiting_you`.
- **V3 paired (`experiments/eval_v3/paired/`, 16 new tasks, 48 arms, `PCIM_CLI_REQUIRE_ISOLATION=1`):**
  - `A` (direct): **16/16** [0.81, 1.00], 0.00 mean corrections, 645 s person time, $2.62, 102 calls.
  - `B3` (V3 plan + single-session contract): **14/16** [0.64, 0.97], 0.125 mean corrections, 1380 s
    person time, $3.56, 126 calls, **0** false incompletes, **0** clear-task clarifications.
  - `B21` (V2.1+): **7/16** [0.23, 0.67], 0.50 mean corrections, 2380 s person time, $7.21, 179
    calls, 3 false incompletes (`r07`, `r09`, `r16`), 12 clear-task clarifications.
  - **Primary (`B3` vs `A`):** McNemar p = 0.50, person-seconds sign-test p = 0.00052. Verdict:
    **"not justified on this evidence"** (direct execution remains the default).
  - **Secondary (`B3` vs `B21`):** McNemar p = 0.0156 (`B3_only=7`, `B21_only=0`), corrections
    p = 0.0391, clarifications p = 3.05e-05; `fixes_confirmed: true`. V3 replaces V2.1 as the
    opt-in `ci plan` engine.

## 6. Security and privacy findings in this work

1. *Fixed.* Loading the cached embedding model during benchmarks contacted the Hugging Face
   Hub. The benchmark now sets `HF_HUB_OFFLINE` when the model is cached.
2. *Fixed before merge.* The mesh labelled the registry's `local` provider as cloud. It is
   loopback-only by construction and is now on-device.
3. *By design.* Approving a plan is not an authorization. A plan compiled in one project's
   directory refuses to run in another's.
4. *Open.* The mesh trusts `mesh.json`'s `trusted: true` as the person's statement that a LAN
   machine is theirs. There is no attestation.
5. *Mitigated on this branch (CLI 2.1.316), from `sec/cli-identity`.* The `claude` CLI added
   the logged-in account's email address and a working directory naming the user to every
   request, despite every isolation flag.
   - This was measured on the request itself, through a loopback relay, not inferred.
   - The adapter now runs the CLI:
     - from a fresh directory outside `$HOME`;
     - with a per-call config holding a sanitized account profile;
     - with git identity hidden.
   - Measured on CLI 2.1.316: 18 of 18 isolated trials carried no identifier (12 before the review fixes, 6 after), and the baseline
     with isolation off still carried the email.
   - Isolation counts as verified only for a CLI version the probe recorded clean.
     `PCIM_CLI_REQUIRE_ISOLATION=1` refuses any call that is not verified. The V3 evaluation ran
     with it set.
   - **Open:**
     - the CLI refetches the profile during calls (measured clean, not guaranteed);
     - the account id stays in API metadata;
     - other CLI versions are unverified;
     - older stored records are not scrubbed.
   - Details: [SECURITY_CLI_IDENTITY.md](SECURITY_CLI_IDENTITY.md), and the independent review:
     [SECURITY_CLI_IDENTITY_REVIEW.md](SECURITY_CLI_IDENTITY_REVIEW.md).
6. *Fixed on this branch.* After Stop-all cancelled a pending action, the workspace agent refused
   every further message in that session.
   - It now hands the ended actions back to the model as results and continues.
   - The strict xfail that pinned the defect passes, so the mark is removed.
7. *Open, pre-existing on main.* The agent's session lock is in-process only. Plan execution adds
   a per-plan file lock.
8. *Fixed on this branch.* `ci halt` sent "halt" to a model. It is now Stop-all.

Details and options for each: [INTENT_V2_REPORT.md](INTENT_V2_REPORT.md) §2.

## 7. Gaps, stated plainly

**Phase 4.** Execution is sequential. There are no specialist agents beyond the one governed
workspace agent, and no remote execution adapter beyond OpenAI-compatible HTTP.

**Phase 5.** Most of what this phase asks for exists on main: the attempt log, alone vs with
help, spacing, and the weekly review. Two things are missing:
- measuring calibration and transfer on unfamiliar tasks;
- progressive reduction of assistance.

**Phase 6.**
- *Already exists:* the formal parts for retrieval, routing and verification are in
  `RESEARCH.md` and `CI_AMPLIFY.md`. This document adds intent reconstruction and placement.
- *Not done:* no manuscript-level evaluation exists yet. Every comparison against stateless,
  memory-enabled and single-agent baselines is still to be run.

**Owner's merge gates.** The real-model evaluation against fixed Sonnet and Opus is still open
on main. This branch does not change routing estimation, so it does not affect that gate.

## 8. Falsifiable claims this work makes

**H1.** On a sealed set of rough instructions labelled by someone other than the developer, the
gate asks on ≥ 80% of consequential-ambiguous items and on ≤ 33% of clear low-risk ones. This
is the same targets as the development evaluation, but now independent.
- *Status:* met on the held-out set (§5b): 21/21 asked when it must, 2/13 asked when it must
  not.
- *Caveat:* the labeller is a model, not a person.

**H2.** No plan ever silently drops an external action. Every send, book, post, buy or schedule
request yields an outcome the person owns, and a decision that is not ready.
- *Status:* 17/17 on the held-out set, and 37/37 not ready when they must not be.
- *Caveat:* the old versions scored the same, so the set did not test the failure this guards
  against.

**H3.** Compiling intent before acting reduces corrections per task, compared with giving the
same request to the same governed agent directly, on matched tasks.
- *Status:* **refuted against direct execution** in both paired evaluations (§5c): V2.1 had 0.44
  mean corrections vs 0.00 for direct (9/16 vs 16/16 verified), and V3 had 0.125 vs 0.00 (14/16 vs
  16/16 verified, McNemar p = 0.50, decision: "not justified on this evidence"). Against V2.1+, V3
  significantly improves verified completion (14/16 vs 7/16, p = 0.0156) and reduces corrections
  (p = 0.0391) and clear-task clarifications (p = 3.05e-05). Direct execution remains the default;
  V3 is the opt-in `ci plan` engine. See [INTENT_V2_REPORT.md](INTENT_V2_REPORT.md) §4b and §5.
