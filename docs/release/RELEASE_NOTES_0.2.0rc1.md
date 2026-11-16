# Release Notes: `0.2.0rc1`

**Package:** `contextual-intelligence` `0.2.0rc1` (`ci`, `pcim`, `pcim-mcp`)
**Branch:** `release/alter-0.2.0` (not merged to `main`, not published)

## What is in `0.2.0rc1`

- **Opt-in intent compiler V3 (`ci plan`, `ci execute`):**
  - Compiles rough requests into typed outcomes (`deliverable`, `external_action`, `destructive_change`), constraints with provenance, and dependency-ordered steps.
  - Executes approved plans in **one** governed agent session with the contract as the checklist, judging completion per outcome from tool evidence.
  - Three independent states recorded on execution: `outcome_success`, `plan_completion`, and `authorization_compliance`.
  - Direct execution remains the default (`ci "…"`); V3 replaces V2.1 as the opt-in `ci plan` engine.
- **Claude CLI identity isolation (`sec/cli-identity`):**
  - Runs `claude -p` from a neutral directory outside `$HOME` with a sanitized per-call `CLAUDE_CONFIG_DIR` and hidden git identity.
  - `PCIM_CLI_REQUIRE_ISOLATION=1` refuses calls unless the installed CLI version was probed clean (`python -m pcim.adapters.cli_probe --trials 4 --record`).
- **Production hardening:**
  - `ci doctor [--json]`: read-only diagnostic check (Python/deps, `PCIM_HOME`, `claude` CLI & isolation, HF cache, audit chain, workspaces, Stop-all).
  - `ci --version`: prints `ci 0.2.0rc1`.
  - Per-session `flock` in `Agent._lock` alongside in-process `threading.Lock`.
  - Automatic `HF_HUB_OFFLINE=1` in `SentenceEmbedder._load` when the default embedding model is already cached locally.
- **Fixed:**
  - **M10.** Deleting a project now deletes the `ci` requests made inside it. Their per-row keys are destroyed, in the same transaction.
  - **Planner reply that is not JSON.** One bounded re-ask before the plan is recorded `invalid`. It addresses paired r10, and is unmeasured on unseen data. The failed first call stays on the plan record (`reask_of`), so its cost is not lost.
  - **A store embedded semantically, opened without the `semantic` extra.** It now fails with an instruction (exit 2), not a traceback, and `ci doctor` fails an `embedder` check.
- **Stop-all (`ci halt` / `ci resume`) and recovery:**
  - `ci halt` cancels pending/running actions and blocks model calls; `Agent.send` flushes terminal pending tasks on resume.

## Known limitations and blockers

- Identity isolation is verified only for CLI `2.1.316`; the CLI refetches the account profile during calls (measured clean on 2.1.316, not guaranteed across updates); account id remains in API metadata.
- **V3 compiler quality** on the V3 intent set:
  - **H3 is missed.** It asks on 11/23 clear requests that include an outside action; the target is ≤ 0.25.
  - **H2 is met but regressed.** 18/21: it no longer asks on 3 genuinely vague requests.
  - Planning stays opt-in for this reason, among others: direct execution verified 16/16 against V3 planning's 14/16 on new tasks.
- **Isolation is per `PCIM_HOME`.** A store's `cli_identity_probe.json` records which CLI versions were measured clean, so a new `PCIM_HOME` reports isolation `unverified` until a probe is recorded (RUNBOOK).
- **A store created where `sentence-transformers` was installed needs the `semantic` extra.** Install `contextual-intelligence[semantic]`.
- No human evaluation yet (all labellers/judges so far are models).
