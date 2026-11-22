# eval_v7 protocol: ALTER goals against direct execution (the V1 eval gate)

Written 2026-11-21, before the pool exists or is read. It inherits everything in
`experiments/eval_v6/PROTOCOL.md` unless stated:
- sandboxes, with a fresh `PCIM_HOME` and repository per run;
- the persona loop, where tacit facts are never volunteered;
- the fixed approval policy: approve writes to allowed paths and test runs, deny the rest;
- three blinded judges, pairwise, in seeded order;
- redaction, the loss and quota rules, and the lid and battery guards.

## Arms

| arm | what runs | how the person's side is driven |
|---|---|---|
| A | `ci do --route direct --model claude-cli:sonnet` | `ci approve/deny TASK`, `ci do answer` (answers and corrections) |
| G | `alter goals start --model claude-cli:sonnet --minutes 14 "<request>"` | `alter approve/deny TASK` (one task at a time); answers with `alter say` and `alter goals resume`; a correction is a follow-up goal holding the request and the correction; a paused or stopped goal is resumed at most twice |

Both arms use the same model, the same sandbox permissions and the same per-turn limits. G adds:
- the Third Eye brief, from the sandbox repository's git and README (vault and GitHub are off);
- the goal instructions;
- at most two prompts to verify;
- the goal's own limits.

## Pool

24 new tasks, written by an agent that builds nothing else, under `experiments/eval_v6/TASK_RUBRIC.md`
unchanged: the same category mix, with 12 tasks needing a question by design.
- It is validated by the writer: each hidden check fails on the starting files and passes with a
  reference solution that is never stored.
- It is sealed read-only at `~/projects/ci-sealed/v7/`.
- The builder of the system under test never reads it before the run.

## Runs

24 person–task pairs (the eval_v5 personas, 2 tasks each, 1 needing a question and 1 not) × 2
arms, run in a seeded order within each pair, one run each.

## Metrics

As eval_v6, plus:
- **false_done:** the system reported success (A: `ci do`'s verdict "verified"; G: the goal's status
  "done"), but the hidden check fails;
- **done without evidence:** G ended a goal "done" with a verdict other than verified or unchanged;
- **goal status, goals started, resumes** (G).
