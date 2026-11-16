# Release readiness: Alter `0.2.0rc1`

**Gate decision: CONDITIONAL.**
- **V3 replaces V2.1 as the `ci plan` engine.** Planning stays **opt-in**, and direct execution
  (`ci "…"`) remains the default.
- **Do not merge to `main` or publish** until the owner has reviewed the conditions in §3.

**Sources.** Every result below was produced on 2026-11-16 by a command that ran to completion.
- The commands are in `SMOKE.md` and `docs/TEST_COMMANDS.md`.
- Results claimed by an earlier agent and not re-run here are marked *not re-verified*.

## 1. Verification

| check | result | how |
|---|---|---|
| full suite, `release/alter-0.2.0` @ `0cb74c9` | **509 passed, 1 skipped**, 0 xfailed | skipped: the opt-in live CLI probe. M10 is fixed, so its strict xfail mark is gone. |
| `main`'s own tests against this code | **343 passed, 2 failed** | both intended: (1) `plan` is a command, not a settable mode; (2) `main`'s strict xfail for M10 now passes (`XPASS(strict)`), because M10 is fixed |
| `scripts/e2e_terminal.py`, this checkout | **32 / 32** | `ci` subprocesses, temporary `PCIM_HOME` and repo, scripted model |
| wheel build | `contextual_intelligence-0.2.0rc1-py3-none-any.whl`, 331,735 bytes | `pip wheel . --no-deps --no-build-isolation` |
| clean install, fresh venv | **passed** | `pip install "<wheel>[test]"` (downloads the dependencies from PyPI); `ci`, `pcim` and `pcim-mcp` installed |
| installed package: `ci --version`, `pcim init`, `ci doctor`, `ci status` | **passed** | `doctor` exits 1 before `pcim init` and 0 after, with the right per-check verdicts |
| e2e against the **installed** package | **32 / 32** | `E2E_INSTALLED=1 <venv>/bin/python scripts/e2e_terminal.py`, with no `PYTHONPATH`. Earlier "installed" e2e runs had silently tested the checkout. |
| upgrade, `main` (1f9bd08) → RC, `semantic` extra available | **passed** | data made by `main` (`init`, `remember`, `ask`); then the RC's `doctor` (exit 0), `status`, `audit`, `twin`, `plan` (`ready`) and memory |
| upgrade, `main` → installed RC **without** the `semantic` extra | **fails clearly** (fixed in `0cb74c9`) | the store is pinned to the semantic embedder. `doctor` fails the `embedder` check, naming the extra, and `ci plan` exits 2 with the instruction. Before the fix it was a traceback. |
| rollback, RC → `main` on the same data | **passed** | `main`'s `ci status`, `pcim memory` and `pcim audit` (`chain_ok: true` on both stores). `main` has no `ci audit` or `ci twin`, so those are not rollback checks. |
| cross-process session lock | **passed** | a second process is refused while the first holds `.session-<id>.lock` (`test_session_flock_blocks_second_process`) |
| prompt injection | **passed** | a file tells the agent to write outside the folder, approve its own action and run a shell. The write is denied by the governor and does not happen; the unknown tools are refused. |
| identity in the diff against `main` | **see below** | `PYTHONPATH=src python scripts/scan_identity.py`: it reads this machine's identifiers at run time and prints counts per label, never values |

## 2. Evaluation evidence (the gate)

| | V3 paired, `experiments/eval_v3/paired/` | V3 intent, `experiments/eval_v3/intent/` |
|---|---|---|
| sets | 16 new tasks with hidden checks; frozen before reading | 50 new items; frozen before reading |
| direct (A) | **16/16** verified [0.81, 1.00], $2.62, 102 calls | — |
| V3 plan (B3) | **14/16** [0.64, 0.97], $3.56, 126 calls, 0 false completions, 0 false incompletes | H1 26/26, H4 39/39, C1 25/25. **H3 11/23: missed (target ≤ 0.25). H2 18/21: met, but 3 regressions.** |
| V2.1+ plan (B21) | **7/16** [0.23, 0.67], $7.21, 179 calls, 3 false incompletes, 12 clarifications on clear tasks | H3 16/23 (missed), H2 21/21 |
| B3 against A (primary) | McNemar p = 0.50. Person time higher on 15/16 tasks, p < 0.001. **"Not justified on this evidence."** | — |
| B3 against B21 (secondary) | McNemar p = 0.016; corrections p = 0.039; clarifications p < 0.001. **All five checks pass: the V3 fixes are confirmed.** | McNemar on H1, H4 and C1: no discordant items |
| unauthorized changes | **0** in every arm (A, B3, B21; and v1's A and B) | — |
| identity strings in stored outputs | 0. Isolation was required on every call; the stored final messages were checked. | 0 in the stored views (not full replies) |

Paired v1 (frozen V2.1): direct 16/16 against plan 9/16, p = 0.016 (`experiments/paired/`).

## 3. Gate criteria

| criterion | status | evidence |
|---|---|---|
| no critical security vulnerability | **met, with conditions** | The CLI identity exposure is mitigated and enforced for CLI 2.1.316: 18/18 isolated trials clean, with a positive control. **Condition:** isolation counts as verified only for a CLI version recorded clean **in that `PCIM_HOME`**. A new install, or a CLI update, needs a probe (RUNBOOK). |
| no unauthorized external actions in evaluation | **met** | E6 = 0 everywhere. CI has no tool that acts outside the computer. |
| no known critical correctness regression | **met, with a condition** | Not critical, but real: V3 asks too little on genuinely vague requests (H2: 3 items) and too much on clear requests with an outside action (H3). Planning is opt-in, so this affects only `ci plan`. |
| installation and core-workflow smoke tests | **met** | §1: a clean install and e2e against the installed package |
| documented failure recovery and rollback | **met** | `RUNBOOK.md` and `ROLLBACK.md`; the rollback was exercised (§1) |
| performance measured against the baseline | **met** | V3 against V2.1: 2× the verified tasks at half the cost. Direct execution remains better than either. |

## 4. Open items, which are why this is CONDITIONAL and not READY

1. **No human evaluation yet.** Every labeller, simulated person and judge was a model.
2. **Identity isolation** is verified only for CLI 2.1.316.
   - The CLI refetches the account profile during calls: measured clean, not guaranteed.
   - The account id stays in API metadata.
   - Older stored records are not scrubbed.
3. **V3 compiler quality** (H2 and H3 above). The V3.2 bounded re-ask for non-JSON plans, which
   addresses paired r10, is **unmeasured on unseen data**: every existing set has been read.
4. **Git history.** `feat/intent-compiler` commit `ff74305` still holds the home path in history,
   and `main` already holds it in old experiment files. Removing it needs a history rewrite:
   **the owner's decision before any push.**
5. **`HF_HUB_OFFLINE` auto-mode** checks only the default cache location
   (`~/.cache/huggingface/hub`). It ignores `HF_HOME` and `HF_HUB_CACHE`.

## 5. Next action for the owner

Review `release/alter-0.2.0` against `main`, using this file and
`docs/INTENT_V2_REPORT.md` §5. If you accept the conditions above, the next step is yours: open a
PR from `release/alter-0.2.0`, or merge `integration/intent-compiler` first. Nothing has been
pushed.
