# Release Notes: `0.2.0rc2`, restricted developer preview

**Package:** `contextual-intelligence` `0.2.0rc2` (`ci`, `pcim`, `pcim-mcp`)
**Branch:** `release/alter-0.2.0rc2`. It is not merged to `main`, not pushed and not published.
**Publication is held** until the owner decides.

## Who and what this preview is for

- **Who.** The owner, and invited developers, on a **verified configuration**:
  - macOS, with `claude` CLI 2.1.316;
  - `PCIM_CLI_REQUIRE_ISOLATION=1`;
  - an identity probe recorded in the same `PCIM_HOME`
    (`python -m pcim.adapters.cli_probe --trials 4 --record`).

  On any other CLI version, calls are refused until a probe is recorded.
- **What to try:**
  - `ci do` and `ci plan` in a throwaway or version-controlled project folder;
  - read what CI says it will do, and approve each write yourself.
- **What this preview does not claim.** It does not claim that routing (`ci do`) or planning
  (`ci plan`) does better than direct execution. In every evaluation so far, direct execution
  verified every coding task. Routing and planning are offered for how they handle ambiguity and
  approvals, not as faster or more successful. The human evaluation (`HUMAN_EVAL.md`) has to
  establish any such claim first.
- **What to report.** Any of these:
  - a change you did not approve;
  - anything sent that you did not expect (`ci audit` shows what was sent);
  - a question that wasted your time;
  - a question CI should have asked.

## New

- **`ci do "…"`: work in an authorized project folder.** It is the terminal path to the governed
  agent. Until rc2 that agent was reachable only through the HTTP API and inside plan execution;
  `ci "…"` gives a text answer and has no workspace tools. A deterministic router (`router.py`,
  no model call) picks the first move and says why:
  - `direct`: the agent starts. Each write and each test run still waits for `ci approve <task>`.
  - `clarify`: CI asks one question first, when the request rests on something CI was not given,
    has a placeholder for its object, or asks for nothing specific. Answer with
    `ci do answer <id> "…"`. CI never asks a second time.
  - `plan`: for a removal or overwrite with a broad scope, the intent compiler shows the plan for
    approval. Then `ci plan approve` and `ci execute`.

  **The keyword router misses most ambiguity** (see Evaluation). In practice `ci do` almost always
  goes direct, and the agent asks its own questions.

  **Planning-assisted routing** is opt-in: `ci do --route assisted`, or
  `PCIM_DO_ROUTING=assisted`. One V3.3 plan compile triages the request, for about $0.035 and 25 s
  per request:
  - it asks the plan's questions, once;
  - it shows the plan for approval when something would be removed or overwritten;
  - otherwise it goes direct.

  It is being evaluated in `experiments/eval_v5/`.

  Other forms: `ci do list`, `ci do --route direct|plan|assisted`, `ci do --explain`. With
  `PCIM_DO_ROUTING=direct`, `ci do` always goes direct and prints the router's choice as advice.
- **Intent compiler V3.3.** These rules were written after reading the V3 results and are tested
  on new data below.
  - An assumption about an action the person carries out themselves is shown under "check when
    you do your part", not asked. The assumption must name its outcome (`about`).
  - A first compile asks when the request rests on context CI was not given.
- **Privacy: no third-party traffic from the `claude` CLI.** The adapter sets the CLI's quiet
  switches on every call:
  - `DISABLE_TELEMETRY`
  - `DISABLE_ERROR_REPORTING`
  - `DISABLE_AUTOUPDATER`
  - `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`

  Measured on CLI 2.1.316: without them, every call also contacted Datadog's log intake. With
  them, only Anthropic. `docs/SECURITY_WHAT_LEAVES.md` lists exactly what is sent.
- **Release tooling:**
  - `scripts/scan_identity.py --history` scans every commit, message and blob new to a branch.
  - `scripts/rewrite_history.py` rehearses and applies a verified, reversible redaction of
    unpushed history.
  - `scripts/audit_egress.py` records what a CLI call sends and where.
  - `scripts/human_eval.py` covers the human evaluation's assignment, blinding and analysis.

## Fixed

- **`ci approve`** prints the agent's whole reply and the next action waiting for approval. It
  used to cut the reply at 300 characters.
- **`ci do answer`** never continues another request's conversation. A later request, or the idle
  timeout, closes the earlier session; the answer then starts again with the request.
- **The identity-probe record** is replaced atomically, so a call that reads it never sees it
  half-written.
- **The version test** checks that the package and `pyproject.toml` agree.
- **The invalid-JSON recovery path** now has deterministic regression tests
  (`tests/test_reask_recovery.py`). They cover recovery in one re-ask, the bound of two calls, an
  invalid plan never being approvable, and the re-ask after an answer. Mutation-checked: removing
  the re-ask, or making it unbounded, makes them fail.

## Corrected documentation

- **rc1's "direct execution remains the default (`ci "…"`)".** `ci "…"` is a text answer.
- **rc1's statement that only `ff74305` holds an identifier in history.** Four unpushed commits
  did. History has now been rewritten with the owner's approval, and every branch scans clean
  (`docs/release/HISTORY_REWRITE.md`; ids in `HISTORY_REWRITE_MAP.txt`).

## Evaluation (`experiments/eval_v4/RESULTS.md`; sets frozen before they were read)

- **Intent compiler V3.3 against V3.2, on 50 new requests.**
  - It asks needlessly on 1/21 clear requests, against 10/21 (McNemar p = 0.004).
  - Asking when it should is unchanged at 20/23.
  - No action was dropped (23/23), and every plan was valid (50/50).
  - It is cheaper: $2.57 against $2.92.
  - **So V3.3 is the `ci plan` engine.**
- **Router.** It is safe but nearly inert: it asks when it should on only 4/21. The model compiler
  catches what it misses.
- **Execution, on 16 new coding tasks.**
  - Direct and routed each verified 16/16, with no unauthorized change.
  - Routing changed what happened on 2 tasks.
  - The registered rule keeps the keyword router as the preview's default. **No performance
    advantage is claimed:** the difference rests on one correction on one task.
- **eval_v5** (12 simulated users, 4 arms, 3 blinded judges): see
  `experiments/eval_v5/RESULTS.md`. It is engineering validation, not a release claim.

## Known limitations

See `docs/release/READINESS.md` §4.
