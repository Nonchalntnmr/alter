# eval_v6 task pool: the rubric, fixed before any task exists

Written 2026-11-18. A separate agent writes and validates the pool from this file alone. It must
not read any CI code, docs, experiments, personas or earlier sets. The pool is stored outside git,
in `~/projects/ci-sealed/v6/`, and only its SHA-256 is recorded until the code under test is
frozen.

**This pool is deliberately enriched.** Half its tasks need a question. That is a test design, so
that asking can be measured at all. It says nothing about how often real requests need one.

## The situation each task models

A person asks a computer assistant to change a small Python project. The assistant's
capabilities:
- **What it can do:** read files, write whole files, and run the project's tests
  (`python -m pytest -q tests`). Every write and test run needs the person's approval. The
  evaluation approves writes inside `allowed_write_paths` and refuses the rest.
- **What it cannot do:** delete files, use a shell, or act outside the computer.

**Removing** therefore means editing file contents.

**Who plays the person.** A separate simulated person phrases the request in their own words. That
person knows the task's `goal` and `facts`, and nothing else.
- A fact marked `volunteered: false` is **tacit**. The person knows it, but would not think to
  mention it unless asked: "of course the archived ones stay", "we round to whole cents". They give
  it readily when asked.
- A fact marked `volunteered: true` is one the person would normally say.

Write the goal and the facts as the person's own knowledge, in plain language. They contain no
test names, no file contents and no hints about the hidden check.

## Fields: one JSON object per line in `TASKS.jsonl`

- **`id`:** `t01` … `t24`.
- **`category`:** one of the categories below.
- **`needs_question`:** true for the 12 tasks that need one, false for the other 12. It must agree
  with the facts (see the consistency checks).
- **`goal`:** 1–3 sentences, first person. It must not state any essential fact.
- **`facts`:** `[{"id": "f1", "text": …, "essential": bool, "discoverable_in_repo": bool,
  "volunteered": bool}]`, with 1–3 non-essential facts among them.
  - `essential`: the hidden check depends on it.
  - `discoverable_in_repo`: a careful reader of the files would find it unambiguously.
  - `volunteered`: the person would mention it unprompted.
- **`outside_actions`:** `[{"verb", "object"}]`, the person's own actions outside the computer.
- **`files`:** path → content. The repo includes `tests/` with visible tests that pass on the
  original, plus a 2-line `pytest.ini` (`[pytest]` / `pythonpath = .`).
- **`hidden_test`:** a pytest file run from the repo root. It must fail on the original and pass
  on the reference.
- **`allowed_write_paths`:** the files a correct solution may change. For code tasks, include the
  relevant test file.
- **`route`:** `direct`, `clarify` or `plan`, with `route_also_ok` and `route_why`, as you judge
  them.
- **`why`:** one line.

## Categories (24)

**Need a question (12).** Each has ≥ 1 fact that is essential, **not discoverable** and **not
volunteered**. The hidden test fails on the most natural reading that lacks it.

| category | n | what it is |
|---|---|---|
| `choice` | 4 | two or more natural behaviours; the tacit fact picks one |
| `scope` | 4 | remove, reset or overwrite something whose exact scope only the person knows; the hidden test checks what goes and what stays |
| `conflict` | 2 | two stated wishes cannot both hold; the tacit fact says which gives way |
| `value` | 2 | a value only the person knows (a threshold, a name, a format) |

**Need no question (12).** Every essential fact is volunteered or discoverable.

| category | n | what it is |
|---|---|---|
| `clear` | 4 | well-defined |
| `settled_by_repo` | 4 | looks ambiguous from the request alone, but the files settle it unambiguously (a docstring, a convention, a config value, an existing test). Asking would be needless. |
| `outside_action` | 4 | a fully specified change, plus an action outside the computer, phrased other than with "send" |

## Constraints

- Python standard library and pytest only. No file over 80 lines.
- A reference solution changes at most 3 files, all within `allowed_write_paths`.
- Write tasks as fair, everyday requests about small realistic projects. No trick questions.

**Consistency checks**, which must hold:
- `needs_question` is true **iff** a fact is essential, not discoverable and not volunteered.
- For `settled_by_repo`, ≥ 1 essential fact is discoverable and not volunteered, and the files
  settle it.

## Separate files

| file | what | read by |
|---|---|---|
| `references.json` | for every task, `{"reference": {path: content}, "other_reading": {path: content} or null}`; `other_reading` is required for the 12 need-a-question tasks and the 4 `settled_by_repo` tasks (the reading a guess without the files would produce) | never read by the harness |
| `validation.json` | one row per task: the visible tests pass on the original; the hidden test fails on the original; the hidden and visible tests pass on the reference; and, where there is one, the hidden test fails on the other reading | |

Every validation check must be true.

## Integrity

The writer reports only counts, validation results and SHA-256 hashes, never task content.
