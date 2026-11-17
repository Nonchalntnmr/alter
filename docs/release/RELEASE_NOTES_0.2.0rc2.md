# Release Notes: `0.2.0rc2`

**Package:** `contextual-intelligence` `0.2.0rc2` (`ci`, `pcim`, `pcim-mcp`)
**Branch:** `release/alter-0.2.0rc2`. It is not merged to `main`, not pushed and not published.

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

  Other forms: `ci do list`, `ci do --route direct|plan`, `ci do --explain`. With
  `PCIM_DO_ROUTING=direct`, `ci do` always goes direct and prints the router's choice as advice.
  The evaluation's rule decides the built-in default (below).
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

## Corrected documentation

- **rc1's "direct execution remains the default (`ci "…"`)".** `ci "…"` is a text answer.
- **rc1's statement that only `ff74305` holds an identifier in history.** Three unpushed commits
  do: `ff74305`, `d6cc52f` and `b77dcc6` (`docs/release/HISTORY_REWRITE.md`).

## Evaluation

See `docs/release/READINESS.md` §2 and `experiments/eval_v4/`.

## Known limitations

See `docs/release/READINESS.md` §4.
