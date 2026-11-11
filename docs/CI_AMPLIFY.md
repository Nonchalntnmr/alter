# CI amplification slice: routing, modes, skill evidence, the Cognitive Twin

Status: implemented on branch `feat/modes-routing-twin` (2026-11-11), 154 tests passing
(132 existing, 22 new). Nothing in this slice has been run against a live model; see
"What is not shown".

## 1. What CI already was (from the code, not the docs)

| Layer | Where | State |
|---|---|---|
| Bitemporal, encrypted memory; crypto-shredding | `memory.py`, `store.py`, `sovereignty/crypto.py` | implemented, tested |
| Consent per vendor; egress guard before selection; hash-chained audit; injection tripwire | `sovereignty/` | implemented, tested |
| Context selection: learned policy, local embeddings, retrieval planning (now off by default after E2) | `policy/`, `embed.py`, `planner.py` | implemented; E2 confirmatory run done in another branch |
| Adapters: Anthropic SDK, Claude CLI, OpenAI-compatible, Gemini, mock | `adapters/` | implemented; only Anthropic has been live |
| Model registry: availability, per-vendor access, `route()` for `chat`/`verify` | `registry.py` | implemented; role-based, first-available |
| Workspace: projects, notes, per-project vendor access, governed tool loop, durable tasks, autonomy modes and grants | `workspace.py`, `agent.py`, `tasks.py`, `governor.py`, `tools.py` | implemented; review findings #36–#54 open |
| Claim ledger, cross-model verification | `exchange.py` | implemented |
| Assistance styles (bandit) with a growth-goal guard; BKT on unassisted probes only; DNA profile (stated / inferred / learned) | `assist.py`, `growth.py`, `profile.py` | implemented, unit-tested, not evaluated with people |
| Interfaces: `pcim` CLI, HTTP API + dashboard, MCP server | `cli.py`, `api.py`, `mcp_server.py` | implemented |

## 2. Gap analysis against the new direction

**A. Already implemented (kept as is):** memory, consent, egress, audit, crypto-shredding,
context selection, workspace autonomy and governor, claim verification, MCP, the assistance
bandit's growth guard, BKT's unassisted-only rule, the reward ordering in `feedback.py`.

**B. Partially implemented:**
- *Model orchestration.* The registry knew how to reach models and who may receive data, but
  `ask()` took an explicit provider (default `mock`), and `route()` ranked by list order with
  no notion of the request. No fallback on the `ask` path. Gemini had an adapter and no
  registry entry; "local" had neither.
- *Cognitive layer.* Assistance styles existed, but nothing user-facing chose between doing
  and teaching. Skills were updated only through `POST /v1/growth/probe`; assisted attempts
  were discarded (audit only), so independent and assisted performance could not be compared.
- *Terminal.* `pcim` was an admin CLI: `ask` needed `--provider`, nothing explained routing.
- *Evaluation.* E1/E2/G0 measure context selection; nothing measured routing, and
  productivity and learning numbers were not separated.

**C. Missing:** task-aware routing with explanations and fallback; EXECUTE/AMPLIFY/LEARN;
an attempt log and independent-performance report; a weekly review; a routing benchmark;
a credential check on the request text (SECRET memory never left the device, but a key
pasted into a request did).

**D. Needs refactoring (not done here):** `engine.ask` is long and now takes four keyword
extras; `assist.MODES` and `workspace.MODES` share a name with different meanings (this slice
adds `modes.MODES`, a third: assistance style, autonomy, intelligence mode). The open review
findings come first (roadmap item 0); this slice does not touch the files they live in.

**E. Leave unchanged:** the sovereignty layer, the governor's rules, egress-before-policy,
the fixed reward weights, the sealed E2 test set.

## 3. What this slice adds

```
ci "<request>"  ──► terminal.py ──► engine.ask_routed
                                    ├─ modes.resolve        request > growth goal > default
                                    ├─ routing.plan         estimate → filter → rank → fallbacks
                                    │    filters: credentials · availability · your vendor grants
                                    │             ∩ project access · test doubles · context window
                                    └─ for each candidate: engine.ask (context re-selected per vendor)
ci outcome / practice ──► engine.record_outcome / probe ──► attempt log (sealed) + BKT (unassisted only)
ci skills             ──► growth.independence_report
ci weekly             ──► twin.CognitiveTwin ──► evaluation.scorecard + rules ──► versioned snapshot
```

### Routing (`routing.py`, registry additions)
- **Rule:** the cheapest available, authorized model whose tier meets the request's
  requirement; if none does, the most capable eligible one, and the reason says so.
- **Tiers are vendor positioning, not measurement** (Haiku < Sonnet < Opus). Context and
  output limits come from the claude-api reference (1M / 128K, cached 2026-11-03). Unknown
  models are "unrated" and used only when no rated model meets the requirement.
  `$PCIM_CAPABILITIES` rates new models and adds `strengths` (the only route by which a domain
  preference enters, and it comes from the person).
- **Requirement estimate** is lexical (like `intent.classify_task`): reasoning and construction
  markers, multi-part and length, mathematics, mode floor (AMPLIFY and LEARN need at least
  standard), preference (`ci prefer economy|balanced|quality` shifts one tier). The explanation
  quotes the words in the request that set it.
- **Authorization:** routing only offers vendors holding the person's egress grant for their
  own words, intersected with the project's provider access when `ci` runs inside an
  authorized workspace. A pinned model has no fallback.
- **Credential tripwire** (`sovereignty/credentials.py`): a request that looks like it carries a
  key routes only to `local` (or refuses). The audit records that something was found, never
  what.
- **Fallback:** on a provider failure (`ProviderError`, OS/timeout, httpx and Anthropic SDK
  transport errors) the next candidate is tried. Each attempt re-runs context selection for its
  own vendor, so a fallback to a different vendor never carries memory that vendor may not see
  (tested). Any other exception propagates. Attempts are in the receipt and the audit.
- Registry gains `google` and `local` entries with capability `ask` only, so `route("chat"|"verify")`
  is unchanged.

### Modes (`modes.py`, `assist.py`)
EXECUTE → the `direct` style plus "no lessons the person did not ask for". AMPLIFY → a new
`amplify` style: recommendation, strongest alternative, trade-off, unsupported assumptions,
no flattery. LEARN → the bandit restricted to the growth styles, plus one active-recall
question. Precedence: per-request flag, then growth goal (learn), then the configured default,
then execute. `amplify` is excluded from the bandit, so a request with no mode behaves as
before (tested). **Modes never touch autonomy, approvals, tool permissions or routing filters**
(tested: autonomy mode, authorizations and project vendors are unchanged after every mode,
and every mode is refused once grants are revoked).

### Skill evidence (`growth.py`, `engine.py`)
- An attempt log (sealed, bounded to 1,000, in `policy_states`) keeps assisted and unassisted
  attempts; BKT still moves on unassisted attempts only. `ci outcome … --skill` logs an
  *assisted* attempt; `ci practice` logs an unassisted one by default.
- `independence_report`: per skill, alone vs with help (Wilson intervals), trend only with ≥6
  unassisted attempts, and three flags with their counts: `dependency_gap` (assisted rate ≥0.25
  above unassisted, ≥3 each side), `recurring_difficulty` (≥3 of the last 5 unassisted wrong),
  `due_for_review` (no unassisted attempt in 7 days). No trait or ability is inferred.
- `ci skills --forget <skill>` deletes the state and every attempt; export includes the log;
  erasure already covered `policy_states`.

### Scorecards (`evaluation.py`)
Productivity (requests, verified success rate with interval, corrections, approvals and
denials, cost per verified success, latency), cognitive (alone vs with help, flags, learn-mode
use) and orchestration (routed, pinned, recovered by fallback, refusals and credential
refusals, served below requirement, estimated vs actual cost, latency by model), each from
records only and kept apart.

### The Cognitive Twin (`twin.py`, `ci weekly`)
"<your name> 2.0" (`--name` to change it). Framed as broader context and compute plus
accountability, never "smarter". Order: last week's priorities checked against this week's
evidence (kept / partly / missed / n/a, with counts), what you did, where you got stuck, what's
next, challenges (weak assumptions, missed opportunities), at most two priority adjustments,
each with a machine-checkable `check`, then the scorecards.

Rules behind the challenges (each needs a threshold of evidence): unverified code/research
answers, dependency gap, recurring difficulty, retention due, growth goal unpractised, a task
type failing verified checks (proposes a growth goal), stated goal with no related activity,
stale next steps, decisions recorded while claims or questions in that project are open,
repeated questions. With no records it says so and sets nothing.

The profile update is the snapshot: one version per ISO week in `policy_states`
(`pcim-twin`), re-runs in the same week replace it, `--history`, `--rollback N`, `--forget`.
Proposed changes to CI (today: growth goals) apply only with `--apply`.

`--deep` adds one model-written challenge, routed at the deep tier among authorized vendors.
Its digest holds only requests that vendor answered itself, notes from projects that allow it
(model-written notes lineage-checked), and counts; the rest is withheld and counted. It asks
before sending (refuses without `--yes` off a terminal), is labelled model-written, and never
changes the snapshot (tested).

### Terminal (`terminal.py`, `ci` entry point)
Same `$PCIM_HOME` and token as `pcim`. Footer on every answer: mode and why, model, tier and
reason, notes sent and withheld, cost (CLI cost marked notional), interaction id, and the
`ci outcome` command. Inside an authorized workspace it scopes memory and vendors to that
project. Exit codes: 0 ok, 2 usage or no identity, 3 routing refused or every model failed.

## 4. Security implications
- **Narrowed:** a credential in a request no longer reaches an external vendor by default.
  Routing cannot select a vendor without a grant. A fallback cannot widen disclosure.
- **New data at rest:** the attempt log and weekly snapshots (derived counts, skill names,
  short quotes of notes and goals), sealed under the user's key like skills; covered by
  export, erase and explicit delete commands.
- **New egress path:** `ci weekly --deep`, gated as above. Task text in the routing plan is
  stored only inside the sealed interaction payload; the audit gets counts and model keys.
- **Unchanged:** the open high findings #36–#40 (jail escape via temp file, case-insensitive
  `.git`, MCP briefing filter, reflection lines, transitive lineage). The roadmap's rule that
  they close before features still holds; this slice avoided their files so the fixes merge
  cleanly, but it does not reduce that risk.
- **Tripwire limits:** pattern-based; a key with no recognisable shape passes.

## 5. Tests executed (2026-11-11, `PYTHONPATH=<worktree>/src .venv/bin/python -m pytest -q`)
- Baseline before changes: 132 passed.
- After: **154 passed** (132 + 22 in `tests/test_amplify.py`), 10.5 s.
- Mutation checks: removing both credential filters fails the credential test; letting
  assisted attempts move mastery fails two tests; swallowing every exception as a fallback
  fails the fallback test. Removing either credential filter alone is caught by the other.
- End-to-end smoke of the real `ci` command through the real Claude CLI adapter against a
  stand-in `claude` binary (no network) in a scratch `$PCIM_HOME`.

## 6. Routing benchmark (`experiments/routing/`)
- **Held-out (the measurement):** 40 requests written by a separate agent from the rubric alone,
  hashed before the first run. The raw lexical estimate matched **15 / 40** labels (chance is
  about 13), **under-routed 18**, and recognised **3 of 10** deep requests. It does not work on
  unseen requests: difficulty is mostly not in the wording.
- **Response, decided after seeing that set:** the default `balanced` preference never routes
  below standard (`routing.FLOORS`); light needs `ci prefer economy` or a pinned model. The
  estimator was not tuned on the held-out set. Post hoc on the same set: under-routed 7,
  over-routed 15, deep recall still 3 / 10. A new sealed set is needed to measure it.
- **Dev set** (`dataset.json`, 36): written before the estimator but in view while writing it;
  36 / 36 is leakage, kept only as a regression check.
- Estimated list-price cost on the dev set with the floor: routed $1.19, always-Opus $1.70,
  always-Sonnet $0.85, always-Haiku $0.04 (router's own token estimates; not a bill).
- Fallback simulation: with 0, 1, 2 failing providers a light request completed on sonnet,
  opus, haiku (1, 2, 3 attempts; the haiku fallback is marked below requirement); with all three
  failing it refused, listing every error.

## 7. What is not shown, and known limitations
- **No live model call was made** in this slice (a deliberate cost and consent choice). The
  routed models' answer quality against a fixed-model baseline is unmeasured.
- The requirement estimate is lexical and measured weak on held-out requests (15 / 40). The
  standard-tier floor limits the damage; it does not fix the estimate.
- Tiers are vendor positioning. Measured per-model track records (claim ledger) are not yet
  used to route, per the roadmap's "enough settled claims first" rule.
- The twin's rules are heuristics with fixed thresholds chosen by judgement, not fitted. The
  goal-relatedness check uses the store's embedder; with the hashing embedder it is lexical.
- The attempt log is read-modify-write without a lock (single person, local); concurrent
  writers could lose an attempt.
- `ci` loads the embedder per invocation (seconds with MiniLM).
- `ci` is declared in `pyproject.toml` but not installed into the shared venv, because
  reinstalling would repoint the other active session's `pcim` at this worktree. After merging:
  `.venv/bin/pip install -e .`.
- Cognitive "improvement" is not claimed anywhere. The report measures performance on skills
  the person chose to log; whether CI changes it needs the paired design in docs (G2/G3).

## 8. Plan, in order
0. Close review findings #36–#40 (unchanged priority; in progress on `fix/review-2026-10-26`).
1. Merge this branch; reinstall the venv entry points; run `ci` against the real store for a
   week with `ci outcome` recorded honestly.
2. Routing quality, live: a preregistered run of routed vs always-Opus vs always-Sonnet on a
   held-out request set, graded blind (the E2 grader pattern), reporting success, cost per
   success and latency. Kill rule: routed loses more than 3pp success to always-Opus at less
   than 40% cost saving.
3. Replace the lexical estimator (held-out 15 / 40) with whatever wins on a new sealed set: a
   small local classifier over embeddings, or a one-call self-assessment by the cheapest model,
   keeping the one-line explanation.
4. Route on measured track records once each model has enough settled claims (roadmap item).
5. A second vendor live (OpenAI or Gemini keys), so fallback is cross-vendor in practice.
6. LEARN loop: a practice prompt generator that writes an unassisted check for the skill just
   taught, so `ci practice` is not the only source of unassisted evidence.
7. API and dashboard surfaces for modes, routing explanations, skills and weekly reviews
   (the engine methods are interface-neutral already).

## 9. Files
New: `src/pcim/routing.py`, `src/pcim/modes.py`, `src/pcim/evaluation.py`, `src/pcim/twin.py`,
`src/pcim/terminal.py`, `src/pcim/sovereignty/credentials.py`, `tests/test_amplify.py`,
`experiments/routing/{dataset.json,heldout.json,run.py,results.json,REPORT.md}`, this file.
Changed: `src/pcim/engine.py` (ask keyword extras, routing, modes, attempt log, weekly
snapshots, export), `src/pcim/assist.py` (amplify style, `among`), `src/pcim/growth.py`
(attempt-log report), `src/pcim/registry.py` (google and local `ask` entries),
`src/pcim/store.py` (`delete_skill`), `pyproject.toml` (`ci` script), `README.md` (`ci` section).
