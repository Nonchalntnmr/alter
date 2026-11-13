Alter
A research prototype of a user-owned context layer that sits in front of any foundation
model. It decides what each model should know about you for each request, learns from
outcomes which context actually helps, and can prove what it sent and what it deleted.

**Start here.** Read `docs/RESEARCH.md` §0 first; it is half a page. The short version:

- **Shown, in simulation only:** a value model learned *across a population* beats a
  tuned strong-memory heuristic by about 15pp.
- **Small and conditional:** per-person adaptation adds 1–3pp, and only when people
  really differ and give dense feedback.
- **Not yet tested:** nothing has been evaluated on real people or real models. The next
  experiment (G1a) is designed and needs API keys.

## What's here, and how real it is

| part | status |
|---|---|
| **Milestone 1, the brief's sixteen-step demonstration**: one model starts a project, the app restarts, a different model continues without re-explaining, a claim is verified independently, an approved reversible edit runs, revoking a provider blocks it (`docs/MILESTONE1_RESULTS.md`) | **passes offline** (`tests/test_milestone1_demo.py`, Anthropic and OpenAI on recorded wire formats) and **passed 16/16 live** with Claude Opus 5.5 and Haiku 5.5 through the local Claude Code login (all Anthropic: cross-model, not yet cross-vendor) |
| **Cross-model layer**: per-project provider access checked before every model step; disclosure log of what each provider was sent; claim and evidence ledger where model agreement is never verification; context exchange gateway with the brief's `AgentMessage` format; deterministic, explained routing (`exchange.py`, `registry.py`) | working, tested |
| **`claude-cli` adapter**: real model calls through `claude -p` with its tools, MCP servers, settings and hooks off; CI's tools through a JSON protocol, every call governed by CI (`adapters/claude_cli.py`) | working **live** |
| **Workspace slice**: projects, notes with provenance, sessions that resume from a compiled briefing, governed tools (read/list/search/git status/run tests/write/note/claim), durable task queue with approvals, expiry, cancel, undo, halt; three autonomy modes enforced server-side (`workspace.py`, `governor.py`, `tools.py`, `tasks.py`, `agent.py`) | working, tested (115 tests in total); run on a live server with a restart in the middle |
| **Engineering hardening (2026-10-26)**: bounded agent turns (rounds, tool calls, time, cost, tokens), durable rounds, no lost updates under concurrency, idempotent tool calls, project-scoped memory, explained context selection, a timeout on model-written regexes, cross-user isolation tests; CI's own overhead per turn down 6.5× at 3,000 memories (`docs/CI_ENGINEERING_AUDIT.md`, `docs/CI_BENCHMARKS.md`) | working, tested, measured |
| **E1 evaluation**: CI vs no context vs naive history on 24 fixed tasks, preregistered (`experiments/e1/`) | **CI beats no context** (46/48 vs 18/48, Haiku 5.5). **CI does not beat naive history on success** (46 vs 44, p = 0.69), but keeps private, injected and stale content out of 100% of prompts, where naive history sends all of it. Selection is not yet relevance-selective (it sends 57% more tokens) |
| MCP server `pcim-mcp`: briefing, consent-filtered context, gated memory, notes, outcomes (`mcp_server.py`) | working against an in-memory MCP client; **not yet registered with a real agent** |
| Temporal memory: bitemporal, supersession vs correction, conflicts, TTLs, lineage (`memory.py`) | working, tested |
| Sovereignty: per-record encryption with crypto-shredding, consent per source × provider, lineage-aware egress, hash-chained audit, injection write-gating (`sovereignty/`) | working, tested |
| Learned context policy: population EM prior, posterior-mean action, density-greedy knapsack (`policy/`) | working; validated in simulation only (G0b) |
| G0/G0b simulator, runner, pre-registrations, deviation logs, reports (`observatory/`, `experiments/`) | working |
| Model adapters: Anthropic (official SDK), OpenAI-compatible, Gemini | tested against recorded wire formats; **no live call through these** (no keys configured). Tool use: Anthropic, OpenAI, `claude-cli`, mock; not Gemini |
| Mock model and mock agent | labelled test doubles, not models; they say so in every reply |
| Assistance-mode bandit, knowledge tracing, intent and VOI | experimental: implemented and unit-tested, not validated on people |
| FastAPI backend + dashboard (Workspace, Autonomy, Memory, Consent, Receipts, Audit) + `pcim` CLI | working on localhost |
| Streaming to the UI, scheduled background work, OS-level sandbox, Postgres/pgvector, multi-user hosting | **not built**; see `docs/CI_SECURITY_MODEL.md` and `docs/CI_ROADMAP.md` |

**Stack decisions**, which deviate from the brief on purpose:

- **SQLite instead of Postgres.** Local-first is the sovereignty argument made concrete:
  one encrypted file, no server.
- **No Redis.** One user on one machine does not need it.
- **A static dashboard instead of Next.js.** This repo's job is to test the thesis.
- All three are on the 90-day roadmap for the hosted, multi-tenant version.

## Run it

```bash
cd ~/projects/contextual-intelligence
.venv/bin/python -m pytest -q                       # 115 tests, ~8 s
export PCIM_HOME=~/.pcim                             # where your encrypted store lives
.venv/bin/pcim init --user nishanth --providers mock
.venv/bin/pcim remember "I prefer concise answers" --kind preference
.venv/bin/pcim ask "plan my week" --receipt
.venv/bin/pcim serve                                 # dashboard at http://127.0.0.1:8765
.venv/bin/pcim-mcp                                   # MCP server over stdio, for terminal agents
```

**Real models without an API key.** If the `claude` CLI is installed and logged in, use
`--providers claude-cli` at init. In the dashboard, the models marked available in the
Models tab can be picked for chat. Each call is billed to, or metered against, that Claude
account. The milestone demo runs live with:

```bash
.venv/bin/python scripts/milestone1_live.py --home /tmp/ci-live --out /tmp/ci-live/report
```

**Real models with keys.** Set `ANTHROPIC_API_KEY`, then use `--providers mock anthropic` at init and
`--provider anthropic` when asking. For OpenAI, set `OPENAI_API_KEY` and `PCIM_OPENAI_MODEL`. The Anthropic adapter defaults to `claude-opus-5-5` at
effort `medium`, with prompt caching on the stable system section. It also turns on
**server-side refusal fallbacks** (`fallbacks: "default"`); pass `fallbacks=False` to
`AnthropicAdapter` to opt out.

**Re-running the experiments.** These are reproducible; the confirmatory runs refuse to
run twice.

```bash
.venv/bin/python -m pcim.observatory.g0 --exp g0  --dev --seed 0     # dev scale, ~25 s
.venv/bin/python -m pcim.observatory.g0 --exp g0b --dev --seed 10
```

## The `ci` command

One terminal command over the same store. CI picks the model (the cheapest authorized one
whose tier meets the request), says why, falls back if a provider fails, and never routes to a
vendor you have not granted, or anywhere off this machine when the request looks like it
contains a key. See [docs/CI_AMPLIFY.md](docs/CI_AMPLIFY.md).

```bash
ci "why does this test pass locally but fail in CI?"   # execute mode by default
ci -a "should we move from SQLite to Postgres?"         # amplify: options, trade-offs, challenge
ci -l "explain Bayes' rule"                             # learn: hints, recall, practice
ci route "…"                                            # the routing decision, no model call
ci outcome <interaction> worked|failed                  # a verified outcome
ci practice "bayes rule" right                          # an unassisted attempt
ci skills                                               # alone vs with help, per skill
ci weekly                                               # the Cognitive Twin's weekly review
```

## Docs

- **Start with these five:**
  - `docs/CI_ARCHITECTURE.md`: components, dependency graph, the request lifecycle traced through the code, trust boundaries, and where the code differs from the intended design.
  - `docs/CI_ENGINEERING_AUDIT.md`: every significant issue found, with severity, root cause, fix and the test or measurement that verifies it, plus what remains open.
  - `docs/CI_SECURITY_MODEL.md`: threats, controls and their tests; consent enforcement; memory isolation; MCP; the data lifecycle and exactly what deletion does and does not reach.
  - `docs/CI_BENCHMARKS.md`: CI's own overhead before and after, live latency and cost, and the E1 evaluation.
  - `docs/CI_ROADMAP.md`: what comes next and what result would show it worked.
- **Supporting:**
  - `docs/TEST_STRATEGY.md`: what each test layer proves.
  - `docs/MILESTONE1_RESULTS.md`: the live runs, including the ones that did not pass.
  - `experiments/e1/REPORT.md`: the evaluation, with its misgrades.
  - `docs/EXISTING_SYSTEM_AUDIT.md`, `docs/IMPLEMENTATION_PLAN.md`: the earlier audits and plans, kept as history.

- `docs/RESEARCH.md` covers feasibility, the PCIM architecture with its math, the G0
  evidence, the G1–G3 programme, falsifiable hypotheses, the roadmap and the evidence bar.
- `docs/STRATEGY.md` covers the business model, unit economics, competition (as of
  2026-10), the pitch and deck outline, hiring, fundraising, the execution roadmap and the
  ten risks.
- `docs/REFERENCES.md` lists 83 sources, all checked by web search (not through LibKey).
- `experiments/g0/` and `experiments/g0b/` hold the pre-registrations, deviation logs,
  configs, reports and diagnostics.

## Personal data

Your store, its key and its token live in `$PCIM_HOME`, outside the repo. `.gitignore`
also excludes `data/`, `*.db` and `*.key`. This repo has its own `git init`, because
`~` is itself a git repository.
