# eval_v5 protocol: twelve simulated users, four arms, three blinded judges

Written 2026-11-17, before the task pool is read. The decision rules are in `PREREGISTRATION.md`.
**This is engineering validation only.** The people in it are models. It does not replace the human
evaluation (`docs/release/HUMAN_EVAL.md`), and no release claim rests on it alone.

## 1. Audit of the existing evaluation infrastructure

**Reused** (from `scripts/eval_v4_paired.py`, `eval_v4_rerun.py`, `e2e_terminal.py`, `human_eval.py`):
- the hidden check: a pytest file run on a copy of the final repository;
- the sandbox repository: git-initialised, with a change snapshot;
- the fixed approval policy;
- the identity-isolation and frozen-code checks;
- the environment-loss rule and the lid guard, from the v4 deviation, now registered up front;
- the statistics: Wilson intervals, exact McNemar, the sign test and Fleiss' κ;
- the e2e script's stand-in `claude`, now extended so it can play every role offline.

**Built for v5** (`scripts/eval_v5.py`, `tests/test_eval_v5.py`):

| piece | why |
|---|---|
| **real CLI workflows** | v4 called Python functions. v5 runs every arm through the `ci` CLI (`ci do`, `ci plan`, `ci approve`, `ci deny`, `ci execute`, `ci do answer`, `ci plan answer`) in subprocesses, on the frozen code, in a fresh sandbox per run |
| **a simulated person in the loop** | 12 personas (`PERSONAS.md`): the persona writes the request, answers questions, writes corrections, and rates the run. v4 had a fixed answer and a fixed correction text. |
| **fact-level labels** | whether a question was needed is computed from which essential, non-discoverable facts the request left out. v4 used a model's per-item judgement. |
| **three blinded judges, pairwise** | three different models compare A against each other arm in random order. v4 had one judge and no comparison. |
| **the planning-assisted router** | `ci do --route assisted`, new in rc2 |
| **tokens, models and transcripts per run** | |
| **an offline end-to-end test of the harness** | 6 tests, about 20 s. It covers the normal path, questions, corrections, the planner's clarify route, the reuse rule, the judge mapping, scoring and sandbox cleanup. |

## 2. Design

**Personas.** The 12 in `PERSONAS.md`, played by `claude-cli:sonnet`. They were written before the
pool existed.

**Tasks.** 24, written from `TASK_RUBRIC.md` by an independent agent that could not see CI's code,
the personas or earlier sets. Each has the following parts:

| part | what it is |
|---|---|
| the person's `goal` and `facts` | each fact is marked `essential` and `discoverable_in_repo` |
| outside actions | the person's own, which CI cannot do |
| a repository with visible tests | |
| a hidden test | it fails on the original, passes on the reference, and fails on the most natural reading that lacks a non-discoverable essential fact |

The pool was validated by its writer (`validation.json`). Its SHA-256 is recorded before the code
is frozen, and the pool is read only after.

**Assignment.** Each persona gets 2 tasks, from 2 different categories. The assignment is drawn
with the registered seed (`assignment.json`).

**Requests.** For each person and task, the persona writes the request **once** and reports which
facts it stated. Every arm gets the same text (`requests.jsonl`).

**Arms.** Each runs in its own sandbox: a temporary `PCIM_HOME`, a temporary git repository, and
a workspace authorized for that repository only. In each sandbox:
- the test command is `python -m pytest -q -p no:cacheprovider tests`;
- writes are allowed;
- the governor applies;
- `PCIM_CLI_REQUIRE_ISOLATION=1`;
- the CLI's identity-probe record is copied in, which is valid because it is the same machine and
  CLI version;
- the embedder is `hashing-512`, since memory plays no part here.

| arm | CLI | |
|---|---|---|
| A | `ci do --route direct` | the baseline: direct execution by the governed agent |
| R | `ci do`, `PCIM_DO_ROUTING=adaptive` | the keyword router, the preview's default. When it routes direct, R is A's run. |
| P | `ci plan`, then `plan approve`, then `execute` | V3.3 planning and contract execution |
| PA | `ci do --route assisted` | the planning-assisted router: one V3.3 compile triages |

**The arms are never told which arm they are,** and the task never says what is checked.

**The person's loop**, the same for every arm:
- **Questions.** A question the system puts to the person is answered by the persona, at most 2
  per run. That covers a router's question, a plan's open question, or an agent turn that ends in
  a question while the work does not yet pass.
- **Corrections.** When the work stops and the hidden check fails, the persona is told "the result
  is not what you wanted" and writes its own correction. At most 2 per run.
- **Rating.** At the end, the persona rates satisfaction (1–5) and whether it would trust the
  assistant unattended. Its knowledge for this: the exchange, and whether the result did what it
  wanted.

**Approvals.** The policy is fixed and the same in every arm. A `write_file` to a path in
`allowed_write_paths`, and any `run_tests`, are approved. Anything else is denied and counted.

**Limits per run:** 40 CLI rounds, and a 900 s timeout per command.

**Judges.** `claude-cli:haiku`, `sonnet` and `opus`, independently.
- Each compares A with each other arm, run by run (PA–A, P–A, R–A). R–A is skipped where R was
  A's run.
- The two responses are shown in random order as "1" and "2". The order is drawn per comparison
  and judge from the registered seed, and recorded.
- Judges see the goal, the facts and the request. For each response they see the questions and
  answers, the number of corrections, the final message, the changed files and the diff.
- Judges never see the arm names, the hidden test or the persona's rating.
- Each gives absolute marks per response (`achieved`, `unnecessary_question`, `missing_question`,
  `false_claim`, `left_to_person_clear`, `rating`) and a preference.
- **The deterministic hidden check decides success.** The judges are model agreement.

**Randomization.** Recorded and reproducible from the seed in `FROZEN.txt`:
- the assignment;
- the execution order of the four arms within each person–task pair, so that no arm always goes
  first;
- the judges' A/B positions.

**Isolation of the generators and simulated users.**
- **The task writer** read only `TASK_RUBRIC.md`.
- **Personas** see only their row, the goal and the facts, and what the assistant says. They do
  not see the code, the arm, the hidden test, the reference or the labels.
- **Judges** see no arm names, and no hidden test.
- **The implementer** (Claude) does not read the pool until the code is frozen.

**Artifacts**, all under `experiments/eval_v5/`, with this machine's identifiers removed before
anything is written:

| file | holds |
|---|---|
| `raw.jsonl` | one row per run |
| `transcripts/` | per run: every CLI command with its exit code, output and time; every persona call; the agent sessions' displays, with the model per step |
| `requests.jsonl` | the requests |
| `assignment.json` | the assignment |
| `judge.jsonl` | the judgements |
| `RESULTS.json`, `RESULTS.md` | the results |
| `FROZEN.txt` | hashes, seed, code commit and CLI version |

**Losses.**
- A run lost to the environment (the machine slept, the CLI was not ready) is run once more.
- A usage-limit hit stops the run, which resumes later.
- Everything else is final.

## 3. Metrics, defined now

Each metric is per run. Arms are compared over the same 24 person–task pairs.

| metric | definition |
|---|---|
| **success** | the hidden check passes on the final repository |
| needs_question | the request omitted an essential fact the repo does not reveal (deterministic, from `requests.jsonl`) |
| asked | the system put at least one question to the person |
| **clarification recall** | asked, among runs with needs_question |
| **unnecessary questions** | asked, among runs without needs_question |
| question_correct | asked = needs_question |
| corrections | the persona's corrections (0–2) |
| **unauthorized actions** | reported in two separate counts. **Attempted:** a write or run the policy denied, each kept as a safety event with its tool and path. **Successful:** a changed file outside `allowed_write_paths`, or any changed file outside the repository. |
| latency | wall seconds from the first command to the end |
| person-time | approvals × 10 s + plan reviews × 30 s + answers × 45 s + corrections × 60 s |
| **cost** | notional USD from the CLI: agent sessions plus routed compiles |
| tokens in and out | the same sources as cost |
| **satisfaction proxies** | the persona's 1–5 rating and "would trust unattended"; the judges' ratings and preference |

## 4. Limits, stated now

- **Models all the way through.** The writer, the persona and the judges are models of the same
  family as the system under test.
- **The fact labels depend on the persona.** They rely on its own report of which facts it
  stated. Judges' `missing_question` and `unnecessary_question` flags are a second, independent
  read.
- **Sample size.** n = 24 pairs, one run each. It is enough for large effects only.
  Non-significance is not equivalence.
- **Blinding is imperfect.** A planning arm's questions can read like a planner's.
- **Questions inside a plan's execution are not answered.** An agent turn inside contract
  execution that asks a question gets no answer. This is the same as v3 and v4, and it can count
  against P.
