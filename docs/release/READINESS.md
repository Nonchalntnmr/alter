# Release Readiness Checklist: `0.2.0rc1`

**Gate decision:** **CONDITIONAL** — V3 replaces V2.1 as the opt-in `ci plan` engine; direct execution remains the default (`ci "…"`). Do not merge to `main` or publish until the owner reviews the open blockers below.

## Verification summary

| Check | Result | Scope / Notes |
|---|---|---|
| Full `pytest` suite (`alter-rc`) | **506 passed, 1 skipped, 1 xfailed** | Opt-in live CLI probe skipped (`PCIM_LIVE_CLI_PROBE!=1`); `M10` xfailed |
| `main`'s tests against `alter-rc/src` | **343 passed, 1 failed, 1 xfailed** | 1 expected failure: `test_modes_never_change_autonomy_permissions_or_routing_filters` (`plan` is command-only) |
| `scripts/e2e_terminal.py` (`alter-rc`) | **32/32 passed** | Offline mock provider in temporary `PCIM_HOME` |
| Full `pytest` suite (`ci-integration`) | **499 passed, 1 skipped, 1 xfailed** | Same skip/xfail |
| `scripts/e2e_terminal.py` (`ci-integration`) | **32/32 passed** | Same |
| Clean `[test]` wheel install + E2E | **Passed (32/32)** | Built `contextual_intelligence-0.2.0rc1-py3-none-any.whl` (331,005 bytes, 73 files, `Requires-Python: >=3.11`, `pytest>=8` in `[test]` extra only); installed in `/tmp/alter-venv`; ran `ci --version`, `ci doctor`, `e2e_terminal.py` |
| Upgrade & rollback (`main` <-> `0.2.0rc1`) | **Passed** | `pcim init` on `ci-main-baseline`, `ci doctor` on `0.2.0rc1`, `ci status` on `ci-main-baseline` |
| `ci doctor` & `ci --version` | **Passed** | Tested uninitialized (`exit 1`), healthy (`exit 0`), halted (`exit 1`) |
| Cross-process `flock` (`Agent._lock`) | **Passed** | Subprocess blocked with `RuntimeError` while parent held session lock; in-process concurrency test also passes |
| HF Hub offline auto-mode | **Passed** | `SentenceEmbedder._load` sets `HF_HUB_OFFLINE=1` when model cache exists |
| Prompt-injection & adversarial tests | **Passed (7/7)** | Outside writes, self-approve, shell, forged CLI turn delimiters, unauthorized external actions, secret scrubbing/withholding, interrupted contracts, corrupted audit/invalid plans |
| V3 paired eval (`experiments/eval_v3/paired/`) | **48/48 arms scored** | A: 16/16 ($2.62, 102 calls); B3: 14/16 ($3.56, 126 calls); B21: 7/16 ($7.21, 179 calls); B3 vs B21 McNemar `p=0.0156` |
| Identity grep (`diff origin/main`) | **0 hits** | Checked after redacting one `tmp_path` username in `raw.jsonl` |

## Open blockers (stated explicitly)

1. **Identity isolation:** Verified only for `claude` CLI `2.1.316`; profile refetch during calls is measured clean on `2.1.316`, not guaranteed across versions; account id remains in API metadata.
2. **Compiler quality (H2 / H3):** V3 missed H2 (`18/21`) and H3 (`11/23`) on the V3 intent set, and `r10` failed on non-JSON planner output.
3. **`M10` (pre-existing on `main`):** Deleting a project retains `ci` requests (pinned by strict xfail).
4. **No human evaluation yet:** All labellers and judges so far are models.
