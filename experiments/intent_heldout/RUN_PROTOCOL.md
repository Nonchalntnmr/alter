# Held-out run protocol

Written 2026-11-13 after V2 was committed (`58348cc`) and **before the held-out set was read**.
It fixes what the pre-registration left open. Nothing here changes a metric's definition or a
target.

## Code under test

| pass | code | worktree |
|---|---|---|
| old format | `ff74305` (detached) | `~/projects/ci-intent-prev` |
| new format | `58348cc` | `~/projects/ci-intent` |

The runner refuses to start if either worktree's `src/` differs from its commit.

V0 and V1 are scored from the **same old-format replies**. In `ff74305`, `gate(spec)` without
the request text is identical to the `cc21ffa` gate, and the prompt format is unchanged between
the two commits (checked with `git diff cc21ffa ff74305`).
- V0 = `validate(reply)` then `gate(spec)`.
- V1 = `validate(reply)` then `gate(spec, request)`.

## Environment, identical for both passes

- A fresh store for every item, holding no memory.
- One throwaway project per item. Its folder is empty and authorized, with a test command
  configured and writes on.
  - **Why:** V2 marks a plan `blocked` when it has file steps but no authorized folder. Without a
    folder, V2 would pass H4 and H5 just by being blocked. With the folder, that rule never fires.
  - The run reports how many V2 plans were `blocked`; the expected number is 0.
- Real routing in plan mode, through the logged-in `claude` CLI. Nothing is pinned.
- Items run sequentially, in set order. Each call is made once and never retried. A failed call
  is recorded as `invalid`.
- `HF_HUB_OFFLINE=1`, because the embedding model is cached.

## Operational definitions

- **External count, V0/V1:** the number of steps of kind `external`, as registered. The V1
  tripwire adds a line to "yours to do" but no step. So for H1 it counts only through the
  decision, not through the external count.
- **External count, V2:** the number of outcomes CI cannot complete itself (`by == "you"`). That
  is every `external_action`, and every `destructive_change` the plan has no CI step for. A
  destructive change CI makes with your approval is not counted.
- **Decision:** the gate's decision.
  - An `invalid` record has no decision. It passes H4 and H5 (it cannot run), and fails H1,
    H2, H6 and the H7 count condition only where those definitions require it.
- **"Not marked complete"** (H1): no compile-time record is ever complete, so this condition is
  always true here. Completion is tested in execution (`tests/test_plan_exec.py`), not in this run.

## Exploratory, labelled as such, not targets

- **V1-tripwire-counted H1:** V1 with each tripwire line counted as one external action.
- **V2 including approved destructive:** V2's external count plus destructive outcomes done by
  CI with approval.
- **Per-category tables.**
