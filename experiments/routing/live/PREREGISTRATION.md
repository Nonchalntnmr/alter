# Live routing evaluation: preregistration

**Written 2026-11-12, before any model call in this evaluation and before the test set exists.**

- **What is registered:** this file, `rubric.md` and `harness.py`. The registration is the commit that adds this
  file. At run time the harness compares the three files with that commit (`git diff`). Any difference, committed
  or not, is a deviation: it is listed in the report, and the run is not confirmatory.
- **The test set:** a new sealed set, held by the integrator and given on the command line at run time. The
  harness prints its sha256 and writes it to `manifest.json` before any model call. `--expect-sha256 <hash from
  sealing>` makes a mismatch stop the run. A confirmatory run must pass the flag.
- **Routing code:** the routed arm is CI's routing as of the commit the run uses. The manifest records that
  commit and the sha256 of the routing path (`routing.py`, `registry.py`, `engine.py`, `modes.py`, `assist.py`,
  the renderer, the CLI adapter, pricing) before any call. The report names it. Routing may change before the
  first live call on the test set, because nothing can be tuned on a set nobody has seen. After that call, a
  routing change is evaluated on a new sealed set, not this one.

## Question

On requests a person would type into `ci`, does routed model selection keep blind-graded answer quality while
cutting cost? The comparisons are always-Opus, always-Sonnet and, optionally, always-Haiku. Everything runs
through the logged-in Claude CLI.

Three sub-questions:

1. Does routed lose more than 3 percentage points of success to always-Opus? What does it save?
2. Does routing's per-request choice buy anything over always-Sonnet, which is cheaper still?
3. When routing serves a request with a model below Opus, how often does that cost a success?

## What this is not

- **Not a measure of CI's context engine.** Context is held constant across arms on purpose, so the model is
  the only difference.
- **Not a bill.** Cost is the CLI's `total_cost_usd`. That is list price, it includes the CLI's own small calls,
  and it is notional under a Claude subscription.
- **Not general.** The result covers one synthetic person, one test set and one vendor's models in one time
  window.
- **Not a test of routing's fallback.** That was measured offline (`../REPORT.md`). Here a provider failure is
  retried the same way in every arm.

## Arms

All arms answer through `PersonalCI.ask(user, request, provider="claude-cli", model=<pinned>,
intelligence_mode="execute")`, the public engine API. Each (request, arm) is first planned with
`PersonalCI.route`, so routing's credential tripwire, availability check and vendor grants apply to every arm.

| arm | model | registered |
|---|---|---|
| `routed` | the model `PersonalCI.route` picks at the default preference (`balanced`: never below standard) | yes |
| `always_sonnet` | `claude-cli:sonnet` | yes |
| `always_opus` | `claude-cli:opus` | yes |
| `always_haiku` | `claude-cli:haiku`, only with `--with-haiku` | exploratory |

Held constant across arms:

- **Store:** one scratch store. `$PCIM_HOME` is a new temporary directory, never `~/.pcim`, deleted after the run.
- **Memory:** synthetic only, either the eight statements in `harness.py` or the test set's own `memories`.
- **Selection:** the hashing embedder, no retrieval planning, and the greedy learned policy, so every arm gets
  the same notes. The harness checks this and reports it as `context_identical`.
- **Mode:** `execute` for every arm.
- **CLI settings:** the CLI's default effort for each model, as `ci` uses it.

## Data

- **Format:** `pcim-routing-live/1` (Appendix A).
- **Author:** a separate agent, working from the brief in Appendix B without reading CI's code, the routing
  data or this harness. The engineer does not read the set. It is sealed, and its sha256 is recorded before
  any call.
- **Size:** 120 requests, 2 repetitions, 3 registered arms. That is 240 (request, repetition) pairs per
  comparison, 720 answers and 722 grades: 720 plus the 2-call grader check. A run with fewer than 100 requests
  left after exclusions is not confirmatory.
- **Composition:**
  - 40 requests each at the author's light, standard and deep labels;
  - 24 requests each of code, writing, research, planning and learning;
  - at least 40 requests carrying an objective `check`.
- **Not used:**
  - `../heldout.json`, because the standard floor was chosen after seeing it;
  - `../dataset.json`, because the estimator was written with it in view.

## Outcomes

### Primary

- **Success.** Per answer, the blind grader's verdict against `rubric.md`: `success`, and no `serious_error`.
  An answer graded successful *with* a serious error counts as a failure and is counted as an inconsistent
  grade.
- **Failed calls.** A call that still fails after every retry counts as a failure for that arm, as in E2.
- **Cost.** The CLI's reported `total_cost_usd` per answer, summed over pairs where both arms report it.

### Secondary

- **Verified completion.** Computed on requests with a `check`: an exact value, a number within a tolerance,
  a regex, or required and forbidden elements. Scoring is string and number matching on the raw answer.
  Model-written code is never executed. The grader is not shown the check.
- **Cost per success** and **latency** (median and p90, from the adapter's measurement), per arm.
- **Under-routing harm.** Among pairs where routed was served below Opus:
  - harm: routed failed where always-Opus succeeded;
  - gain: the reverse;
  - net harm rate, exact McNemar p, and both broken down by the author's tier.
- **Over-routing.** Pairs routed to Opus where always-Sonnet also succeeded, and the extra cost.
- **Grader against the checks.** Per arm, on checkable answers: agreement, *grader pass and check fail*
  (leniency), and *grader fail and check pass*.
- **Repetition noise.** Per arm, the requests whose two repetitions disagree.
- **Routing against the author's labels.** Exact, under and over, for the requirement estimate and for the
  served tier. This is the new sealed measurement of the estimator with the floor that `../REPORT.md` asks for.
- **Integrity counts:**
  - errors and ungraded rows;
  - refusals;
  - answers served by a model other than the one asked for;
  - answers with a self-name redacted;
  - identical context across arms.

## Hypotheses

| | hypothesis | direction | role |
|---|---|---|---|
| H1 | success(routed) − success(always_opus) > −3 pp | routed is not worse by more than the margin | primary (R1) |
| H2 | cost(routed) ≤ 0.60 × cost(always_opus) | routed is cheaper by at least 40% | primary (R2) |
| H3 | success(routed) > success(always_sonnet) | routed is better: it sends estimated-deep requests to Opus | secondary (R3) |
| H4 | success(always_opus) > success(always_sonnet) | the set separates the tiers | validity |
| H5 | harm > gain among pairs served below Opus | under-routing costs successes | secondary |
| H6 | verified(routed) − verified(always_opus) > −3 pp | as H1, on the checkable subset | secondary; guards R1 |

H2 is expected to fail. On the dev set, routing's own estimate is a 30% saving (Known before registration).
It is registered anyway because the kill rule depends on it.

## Analysis

- **Unit:** the (request, repetition) pair. Two arms are compared on the pairs where both have an outcome.
- **Success differences:**
  - point estimate (b − c) / n;
  - 95% percentile bootstrap interval, 10,000 resamples, seed 20261114, resampling requests so that a request's
    two repetitions move together;
  - exact two-sided McNemar test on the discordant pairs.
- **Cost:** the ratio of summed reported cost, with the same bootstrap. Saving = 1 − ratio.
- **Per-arm rates:** Wilson 95% intervals.
- **Multiple testing:**
  - registered McNemar family: routed vs always-Opus, routed vs always-Sonnet, always-Opus vs always-Sonnet,
    Holm-corrected at α = 0.05;
  - Haiku comparisons: a separate exploratory family, also Holm.
- **Sensitivity:** the H1 difference recomputed over pairs where neither routed nor always-Opus had a provider
  error.
- **Order and seeds:**
  - answers: a seeded shuffle of (request, arm, repetition), seed 20261112, so provider drift hits every arm
    alike;
  - grading: shuffled separately, seed 20261113.

All of this is `analyze()` and `decide()` in `harness.py`, which `tests/test_routing_live_harness.py` exercises.

## Decision rules

**Kill rule (point estimates).** Routing is killed when routed loses more than 3 pp of success to always-Opus
*and* saves less than 40% of always-Opus's cost.

| loss | saving | decision |
|---|---|---|
| > 3 pp | < 40% | **kill** |
| > 3 pp | ≥ 40% | **trade-off** (reported with the price per lost success) |
| ≤ 3 pp | any | **keep** |

Consequence of a kill, registered now: the report recommends that `balanced` stop routing below the most capable
authorized model until a replacement estimator passes a new registered run on a new sealed set. The default is
not changed in this experiment.

**Claims (95% intervals).**

R1, quality against always-Opus:

- **supported:** lower bound > −3 pp;
- **falsified:** upper bound < −3 pp;
- **otherwise:** inconclusive.
- A *supported* R1 becomes inconclusive when the objective checks show a point loss beyond 3 pp (H6).

R2, saving against always-Opus:

- **supported:** the saving's lower bound ≥ 40%;
- **falsified:** its upper bound < 40%;
- **otherwise:** inconclusive.

The routing claim ("routing keeps answer quality while cutting cost") holds only if R1 and R2 both hold:

- **supported:** R1 and R2 are both supported;
- **falsified:** either one is falsified;
- **otherwise:** inconclusive.

R3, selection against always-Sonnet:

- **falsified:** the upper bound of routed − Sonnet is below 3 pp, so any gain is smaller than the registered
  worthwhile gain, at a higher cost;
- **supported:** otherwise, if the lower bound is above 0;
- **otherwise:** inconclusive.

If H4 is not supported (the set does not show Opus beating Sonnet), a supported routing claim carries the
registered note that keeping quality then says little about routing's choices.

**What falsifies the routing claim.**

- R1 is falsified: routed is worse than always-Opus by more than 3 pp, with the whole 95% interval below −3 pp.
- Or R2 is falsified: the saving's whole interval is below 40%.

Separately, R3 falsified means always-Sonnet is as good for less money. That falsifies the claim that
per-request selection is worth having on this kind of request, even where the routing claim holds.

**Claims and negative results.**

- A result licenses only "on this test set, through the Claude CLI, …".
- Negative, reversed or inconclusive results are reported in the same place and with the same prominence.

## Sample size

Routed under `balanced` answers with Sonnet or Opus. Where it picks Opus, it and always-Opus differ only by
sampling, so the discordance between them is modest.

For a paired non-inferiority test at a 3 pp margin, with one-sided α = 0.025, 80% power and a true difference
of 0, the number of pairs needed is (z₀.₉₇₅ + z₀.₈)² × p_disc / 0.03² = 8,721 × p_disc. Here p_disc is the
discordant-pair rate.

| p_disc | pairs needed for 3 pp | detectable margin at 240 pairs | P(kill fires \| true loss 0) at 240 pairs |
|---|---|---|---|
| 0.05 | 436 | 4.0 pp | 0.02 |
| 0.10 | 872 | 5.7 pp | 0.07 |
| 0.15 | 1,308 | 7.0 pp | 0.12 |

These figures use the normal approximation and ignore clustering of repetitions within a request, which
widens all of them.

**Registered:** 120 requests × 2 repetitions, which is 240 pairs.

- **R1 at 3 pp.** It can be shown only if discordance is at most about 2.8%. More likely, R1 comes out
  inconclusive. That is stated now, so an inconclusive R1 is the expected outcome, not a failure of the run.
- **The kill rule.** It is decidable at any size. It fires by chance with the probabilities in the last column.
  It misses a true 6 pp loss with about the same probability.
- **Why not more.** The run spends Opus and grader usage roughly in proportion to size (Known before
  registration). A larger sealed set run with the same harness and rules is a valid replication.

## Grader independence

- **Who grades.** The only live provider on this machine is the Claude CLI, so the grader cannot come from a
  different vendor. The registered grader is `claude-cli:claude-fable-5-1`: Claude Fable 5.1, which is not an
  answering model. It is the same vendor, so independence is weaker. It may share the answering models' blind
  spots, and it may prefer one tier's style. The report says so on its first page.
- **Never an answering model.** The harness refuses a grader that any arm uses in the run, under any provider
  name.
- **A different-vendor grader.** If one is available at run time (`--grader openai:<model>` or
  `--grader google:<model>`), it is preferred. The manifest names the grader before any call. The grader cannot
  change within a run, because resuming refuses it.

Mitigations:

1. **Blind.**
   - The grader sees the request, the author's criteria and reference answer, the synthetic notes, the rubric
     and one answer.
   - It never sees the arm, model, cost, latency or routing receipt.
   - Self-names ("Claude Opus 5.5", "claude-sonnet-5-5", "Anthropic") are redacted in every arm alike.
   - A guard refuses to send a grader input that still carries a model name or arm label.
   - Identical answers from different arms reach the grader as byte-identical input (tested).
2. **Shuffled and separate.** Each answer is graded in its own call, in a seeded order across arms and
   requests, under an opaque id.
3. **Reference-based where possible.** Every request carries the author's criteria, and many carry a reference
   answer.
4. **Objective checks.** Checks on at least a third of requests are scored with no model. The grader's
   disagreement with them, per arm, measures its reliability and any leniency that differs by arm. They also
   guard R1 (H6).
5. **Hand check.** After the run, the engineer reads 40 graded answers chosen with seed 20261115, arm hidden,
   and records agreement. Disagreements are listed. The registered numbers stand either way.
6. **Grader check.** Before any answer is bought, two grader calls on a fixed synthetic item must pass "4" and
   fail "5". If they do not, the run stops.

## Exclusions and missing data

**Before any call**, decided from the request text alone. The harness prints counts and ids, never the text.

- Excluded: credential-shaped text (CI's tripwire would keep it from every vendor).
- Excluded: names an AI model or vendor (its answers cannot be blinded).
- Excluded: duplicate request text.
- Refused outright: a structurally malformed set, including an invalid check. Nothing is silently dropped.

**Answers.**

- A provider failure is retried after 10, 40 and 120 s. A call that still fails counts as a failed task for
  that arm and has no cost.
- A resumed run retries items that ended in error. `answers.jsonl` keeps every record.

**Grades.**

- A reply that is not a grade is retried once within each attempt, and attempts follow the same schedule.
- A grade that still fails is excluded from the success analysis and reported.
- So is an input the blinding guard or the credential tripwire refuses to send.
- If more than 2% of rows have an answer but no grade, the run is reported as **compromised**.

## Pilot and resume

- **Pilot.** `--limit N` (the first N requests of a seeded shuffle, so a larger N is a superset) or `--tasks`.
  A pilot is never confirmatory.
- **Pilots on the sealed set.** They are for operations only: errors, the grader check, cost, latency and
  blinding.
  - The harness withholds quality from a pilot's report and console output.
  - The decision to continue may rest only on those operational numbers.
  - The engineer does not open a pilot's `results.json` before the full run.
- **Recommended first step.** A dry run and a live pilot on a development file, such as a converted copy of
  `../dataset.json`, before touching the sealed set.
- **Resume.**
  - The full run uses the same `--out`. Answers and grades already written are reused: answers by
    (request, arm, repetition), grades by the hash of exactly what the grader saw. A crash or a pilot never
    re-bills a finished item.
  - Each answer and grade is fsynced as it is written. A line cut short by a crash is redone.
  - Resuming refuses if any of these changed: the test set, arms, repetitions, grader, mode, a registered file,
    or the routing code.

## Known before registration

**The router's own estimate on the dev set.** The harness's estimate mode, offline with no model call, on the
36 dev requests converted to this format: 72 answers per arm, 218 grader calls.

| | estimate |
|---|---|
| routed | $2.39 (Opus 8 requests, Sonnet 28) |
| always-Sonnet | $1.70 |
| always-Opus | $3.40 |
| always-Haiku | $0.09 |
| grader (Fable 5.1) | $12.92 |
| total | $20.41 |

- **Saving.** That is a 30% saving for routed against always-Opus. Under `balanced` the most it can save is about
  50%, if everything went to Sonnet. So H2 (≥ 40%) is expected to fail unless real reported costs differ a lot
  from the router's token guesses, for example if Opus writes much longer answers. The kill rule will likely
  turn on the quality loss.
- **The grader is the largest cost.** That share depends on the guess of 600 output tokens per grade, thinking
  included.
- **At the registered size.** Scaled to 120 requests, the same estimate is roughly $25 for the three arms and
  $43 for the grader, at list price. This is an extrapolation, not a measurement. The harness prints the real
  estimate for the sealed set before any call.

**What routing already showed** (`../REPORT.md`):

- Under `balanced`, routing never uses Haiku, so routed differs from always-Sonnet only on requests it
  estimates deep.
- On the held-out set, the raw estimate matched 15 of 40 labels, under-routed 18 and recognised 3 of 10 deep
  requests. With the floor, post hoc, it under-routed 7.
- Many author-labelled deep requests will probably be served by Sonnet. H5 tests directly whether that costs
  quality.

**The pipeline.** It has run only with fake adapters: a dry run and 13 tests. No real model has seen the grader
prompt, so the rubric's behaviour on real answers is unknown. That is what the development pilot is for.

## Threats to validity

**The grader.**

- It comes from the same vendor as the answering models (see Grader independence).
- Length and style can reveal a tier despite redaction. The rubric says not to reward length, but that cannot be
  removed.

**The data.**

- The test set's author is probably a model of the same family, and its tier labels are judgements, not
  measurements.
- One synthetic person. Context is deliberately simpler than production, which uses MiniLM and may plan
  retrieval.

**The CLI.**

- Each call carries the CLI's own preamble.
- `total_cost_usd` includes the CLI's own small calls.
- Rate limits add retries and latency noise, and latency includes process start.
- Effort is the CLI's default for each model.

**Time.** The vendor can update models during the run. Interleaving the arms spreads that across them, but it
cannot remove it.

**The code.** Routing on the PR branch has moved since this slice (the standard floor; fallback errors carry a
kind and retryability). The harness uses only `PersonalCI.route` and `PersonalCI.ask` with a pinned provider and
model, so it does not depend on those internals.

## Running it

From the repository root, with `PYTHONPATH=src` and the project venv:

```
# offline: the estimate, then a refusal (no call)
python experiments/routing/live/harness.py SEALED.json --out experiments/routing/live/results/run1
# offline: the whole pipeline with fake adapters
python experiments/routing/live/harness.py SEALED.json --out /tmp/dry --dry-run
# live pilot on a development file, then on the sealed set (operations only), then the full run
python experiments/routing/live/harness.py DEV.json --out /tmp/dev-pilot --live --limit 10
python experiments/routing/live/harness.py SEALED.json --out experiments/routing/live/results/run1 --live --limit 10 --expect-sha256 <hash>
python experiments/routing/live/harness.py SEALED.json --out experiments/routing/live/results/run1 --live --expect-sha256 <hash>
```

`--max-usd X` refuses to start when the estimate exceeds X. `--with-haiku` adds the exploratory arm.

**Outputs, in `--out`:**

- `manifest.json`, written before any call;
- `grader_check.json`;
- `answers.jsonl`;
- `blinded.jsonl`, exactly what the grader is given;
- `blinding_key.json`;
- `grades.jsonl`;
- `results.json`;
- `report.md`.

## Deviations

None yet. Any change to a registered file after the commit that adds this one is listed here and in the
report, with its date and reason.

## Appendix A: test-set format

```json
{
  "format": "pcim-routing-live/1",
  "name": "...", "author": "...", "written": "YYYY-MM-DD",
  "memories": [{"kind": "preference", "content": "..."}],
  "tasks": [
    {"id": "t001", "text": "the request, as the person would type it",
     "criteria": ["what a successful answer must do or contain"],
     "reference": "a short reference answer (optional)",
     "tier": 2, "category": "code",
     "check": {"type": "number", "value": 22.22, "tolerance": 0.05}}
  ]
}
```

**Fields:**

- `memories` is optional, synthetic only, and has one of CI's six kinds per item. Without it, the harness's own
  synthetic person is used.
- `tier` (1 light, 2 standard, 3 deep) and `category` are optional, and the grader never sees them.

**Checks:**

- `{"type": "exact", "value": "..."}` compares the whole answer, ignoring case, edge punctuation and markdown
  emphasis;
- `{"type": "number", "value": x, "tolerance": t}` passes when some number in the answer is within t of x;
- `{"type": "regex", "pattern": "...", "flags": "i"}`;
- `{"type": "elements", "required": [regex, ...], "forbidden": [regex, ...]}`, matched case-insensitively.

## Appendix B: brief for the test-set author

Write 120 requests that one person might type into a terminal assistant over a few weeks of real work. Do not
read CI's code, `experiments/routing/`, or this harness, and do not think about how any router works.

- **Mix:**
  - 24 requests each of code, writing, research, planning and learning;
  - within those, 40 requests each that you would label light, standard and deep. The label is the lowest
    capability tier you would trust to do the request well.
- **Make hard requests hard in content, not wording.** A short plain question can be deep, and a request that
  says "design" or "strategy" can be trivial.
- **For each request:**
  - 1–4 criteria that separate a correct answer from a plausible wrong one;
  - a short reference answer where one exists.
- **Checks.** Give at least 40 requests a `check` on something a correct answer must contain: a value, a number
  with a tolerance, a name, a keyword. For code requests, check text only (an algorithm, an API, a guard
  clause), never the result of running code.
- **Never include:**
  - any mention of AI models or vendors;
  - credentials or realistic secrets;
  - data about real people.
- **Notes.** Requests may draw on the synthetic person's notes, but each must be answerable without them. You
  may supply your own `memories` instead. The default notes:
  - I work as an operations analyst at a regional bus company.
  - I like answers that start with the conclusion and then give the reasoning.
  - When I ask for code, I use Python 3.12 with type hints unless I say otherwise.
  - Use metric units and 24-hour times.
  - My laptop runs macOS; at work I use a Linux server over SSH.
  - I am preparing for a statistics certification exam in the spring.
  - I can't install software on my work machine without approval from IT.
  - I live in Porto and commute by train.
- **Hand-off.** Save the file, compute its sha256, and give both to the integrator. The engineer does not see
  the file.
