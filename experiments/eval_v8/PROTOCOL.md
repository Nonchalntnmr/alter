# eval_v8 protocol: ALTER goals against direct execution (the V1 eval gate, second attempt)

Written 2026-11-21, before the pool exists or is read. It is `experiments/eval_v7/PROTOCOL.md`
unchanged, except for the pool, the code and the seed below.
- **Arms:** A is `ci do --route direct --model claude-cli:sonnet`. G is
  `alter goals start --model claude-cli:sonnet --minutes 14`. Both are driven exactly as in eval_v7.
- **Personas:** the eval_v5 personas: 24 person–task pairs × 2 arms, one run each.
- **Judges:** the same three, blinded and pairwise.
- **Metrics, loss rules and guards:** the same.
- **The harness:** `scripts/eval_v8.py` is `scripts/eval_v7.py` with only its paths and docstring
  changed. The report is `scripts/eval_v8_report.py`.

## Code under test

Commit `0db915c` (`feat/goal-honest-done`): release/alter-v1 @ 882fa96, plus
- the safety-audit follow-ups,
- the orphaned-test-run fix,
- the honest-done fix.

`FROZEN.txt` records it, and the harness refuses to run if `src/` differs from it.

## Pool

24 new tasks under `experiments/eval_v6/TASK_RUBRIC.md` unchanged (the same category mix, with 12
tasks needing a question by design), written and checked by `scripts/eval_v8_pool.py`:
- **Writer:** separate `claude -p` sessions (model `opus`) run through pcim's isolated CLI adapter:
  - no tools, no MCP servers, no settings or hooks, and no session persistence;
  - an empty private directory outside $HOME.
  Each session's only input is the rubric, plus the category and count it must write. It never sees
  CI code, experiments, personas or any earlier pool.
- **Batches:** one per category. A batch is told only the short theme names of earlier batches, so
  that projects do not repeat.
- **Validation (mechanical, by the script; every check must hold):**
  - the schema and the rubric's consistency checks;
  - no file over 80 lines;
  - a reference that changes at most 3 files, all in `allowed_write_paths`;
  - the visible tests pass on the original;
  - the hidden check fails on the original;
  - the visible tests and the hidden check pass on the reference;
  - where there is an other reading (the 12 need-a-question tasks and the 4 `settled_by_repo`
    tasks), the visible tests pass on it and the hidden check fails on it.
- **Repairs:** a task that fails a check goes back to a fresh writer session with the check names
  and test output, at most twice. After that it is replaced by a new task of the same category.
  Every repair and replacement is counted in `SEALED.txt`.
- **Sealing:** read-only at `~/projects/ci-sealed/v8/`, as `TASKS.jsonl`, `references.json`,
  `validation.json` and `SEALED.txt`.
  - Ids are assigned by a seeded shuffle.
  - **The builder never reads the task contents before the run.** The script prints only ids,
    categories, check names and counts.

## Runs, metrics

As eval_v7.
