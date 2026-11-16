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

## 3. Plan execution

An approved plan runs level by level through the existing workspace agent, one message per
step. Approving a plan authorizes nothing: the governor decides every tool call again, under the
current autonomy mode and grants. The only permissioned call in plan code is `agent.cancel`.

**Every step is always in one recorded state.**

| state | meaning |
|---|---|
| `not_started` | not reached (recorded, not implied) |
| `in_progress` | written *before* the agent is called |
| `done` | every tool the step used succeeded and tests passed, **judged from task records, never from the model** |
| `incomplete` | the turn ended, but a tool was denied, failed, cancelled or expired, or the tests failed |
| `waiting` | an action needs you: `ci approve` / `ci deny` |
| `failed` | the agent could not take the step (provider, vendor) |
| `halted` | Stop-all stopped it |
| `interrupted` | found `in_progress` at the next run: the process died mid-step. **Never re-run silently**, because it may have acted. |
| `yours`, `yours_done` | an external step; only you can mark it |
| `blocked` | it depends on a step that is yours |
| `cancelled` | `--cancel` |

**Rules:**
- A step that is not done stops the plan. Only an external step lets independent steps go on.
- `--retry STEP` re-runs a failed, interrupted, incomplete or halted step, and keeps the earlier
  attempt. Nothing else re-runs a step.
- `--cancel` closes the step's session **first**, then cancels its pending actions. So nothing
  resumes, and a late `ci approve` cannot revive them.
- `ci halt` is Stop-all. `ci resume` lifts it. Before this branch, `ci halt` fell through to a
  model question. A halted step is left untouched if it had not started.
- **One run at a time per plan:** `execute` holds an OS lock on the plan, and so do `ci approve`
  and `ci deny` on a plan's action. The lock is released if the process dies. So `ci execute`
  never judges a step while the turn it resumed is still running.
- **Completion is about outcomes.** A plan is `done` only when every outcome is met. A CI outcome
  is met when every step serving it is done. An external outcome is met only when you mark it
  (`--done`). Otherwise the plan is `awaiting_you`, `incomplete`, `waiting`, `failed`,
  `interrupted`, `halted` or `cancelled`, and it ends by saying **"Not done"**.

**Parallelism: none.** Steps within a level are independent, but they run one after another.
Parallel execution is deliberately not built until sequential execution and cancellation are
shown to be reliable.

**Tests:** 19 in `tests/test_plan_exec.py` (counting parametrized cases), plus one strict
xfail that pins an agent defect (§6).

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

## 6. Security and privacy findings in this work

1. *Fixed.* Loading the cached embedding model during benchmarks contacted the Hugging Face
   Hub. The benchmark now sets `HF_HUB_OFFLINE` when the model is cached.
2. *Fixed before merge.* The mesh labelled the registry's `local` provider as cloud. It is
   loopback-only by construction and is now on-device.
3. *By design.* Approving a plan is not an authorization. A plan compiled in one project's
   directory refuses to run in another's.
4. *Open.* The mesh trusts `mesh.json`'s `trusted: true` as the person's statement that a LAN
   machine is theirs. There is no attestation.
5. *Open, pre-existing on main.* The `claude` CLI adds the logged-in account's email address and
   its working-directory path to the model's context, despite every isolation flag the adapter
   sets.
   - In the held-out run, identifying details appeared in 40 of 100 replies and were written into
     plans. CI never approved sending this data, and no receipt lists it.
   - The committed replies are redacted.
6. *Open, pre-existing on main.* After Stop-all cancels a pending action, the workspace agent
   refuses every further message in that session.
   - Plan execution closes the stale session before a retry.
   - Direct sessions stay wedged. This is pinned by a strict xfail.
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
- *Status:* under test in the paired experiment (`experiments/paired/`). The result goes in
  [INTENT_V2_REPORT.md](INTENT_V2_REPORT.md) §1.
