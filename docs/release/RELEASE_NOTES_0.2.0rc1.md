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
- **Stop-all (`ci halt` / `ci resume`) and recovery:**
  - `ci halt` cancels pending/running actions and blocks model calls; `Agent.send` flushes terminal pending tasks on resume.

## Known limitations and blockers

- Identity isolation is verified only for CLI `2.1.316`; the CLI refetches the account profile during calls (measured clean on 2.1.316, not guaranteed across updates); account id remains in API metadata.
- V3 compiler quality: missed H2 (18/21) and H3 (11/23) on the V3 intent set, and 1/16 paired arms (`r10`) failed on non-JSON planner output.
- Pre-existing `M10` on `main`: deleting a project retains `ci` requests (strict xfail).
- No human evaluation yet (all labellers/judges so far are models).
