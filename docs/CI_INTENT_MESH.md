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

**Problem.** Given a request *x* (possibly one word), the person's consent-filtered context *C*,
and possibly a project briefing *B*, produce:
- a specification *S* = (goal, deliverables, constraints with provenance, success criteria,
  dependencies, steps as a DAG with action kinds, assumptions with confidence and stakes,
  questions);
- a decision *d* ∈ {ready, clarify, approval}.

**Model and gate.** The model proposes a distribution over readings p(g | x, C, B) and the
rest of *S*. The decision is not the model's. A deterministic gate computes it:

    clarify   if  max p < 0.6,  or  p(1) − p(2) < 0.2,
              or  ∃ assumption a: consequential(a) ∧ conf(a) < 0.8,  or  S has no steps
    approval  else if ∃ step of kind run | write | external
    ready     otherwise

This is the value-of-information rule from `intent.py` made concrete. Ambiguity that would not
change what happens (a non-consequential assumption) is resolved and listed, never asked.
Ambiguity that could waste effort, change or delete something, spend money or contact anyone
stops the plan for a question.

**Checks the model cannot talk its way past:**
- **Provenance.** A constraint "from your notes" must cite note numbers that were actually
  sent on that request. Otherwise it is relabelled *assumed* and flagged.
- **Action kinds.** An unknown step kind is treated as `external`, the most restricted.
- **Graph.** Unknown dependencies and cycles reject the plan. Truncation is flagged.
- **Invalid replies.** A reply that is not a plan is stored as *invalid*, never repaired by
  guessing.

**Tests.** 18 tests in `tests/test_intent_compiler.py`. A mutation check loosened each
threshold and each check in turn (5 mutations). The tests caught every one.

## 3. Plan execution

An approved plan runs level by level through the existing workspace agent, one message per
step. Approving a plan authorizes nothing: every tool call is decided again by the governor,
with the current autonomy mode and grants.

| Step outcome | What happens |
|---|---|
| A step needs approval | The plan pauses durably and shows `ci approve <task>`. |
| An external step | It is recorded as yours to do. Only its dependents wait. |
| Halt | The plan stops before any model call. |
| A governor denial | Recorded as evidence on the step. |

Steps within a level are independent and could run in parallel. This slice runs them one
after another in one session per project, and says so. Nothing runs in the background.

Tests: 5 in `tests/test_plan_exec.py`.

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

<!-- EVAL -->

## 6. Security and privacy findings in this work

1. *Fixed.* Loading the cached embedding model during benchmarks contacted the Hugging Face
   Hub. The benchmark now sets `HF_HUB_OFFLINE` when the model is cached.
2. *Fixed before merge.* The mesh labelled the registry's `local` provider as cloud. It is
   loopback-only by construction and is now on-device.
3. *By design.* Approving a plan is not an authorization. A plan compiled in one project's
   directory refuses to run in another's.
4. *Open.* The mesh trusts `mesh.json`'s `trusted: true` as the person's statement that a LAN
   machine is theirs. There is no attestation.

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

**H2.** No plan ever silently contains an external action. Every send, book, post, buy or
schedule request yields an `external` step and a non-ready decision.

**H3.** Compiling intent before acting reduces corrections per task, compared with asking
directly (`ci "…"`), on matched tasks. This is not yet measured, and it is the next experiment
worth running.
