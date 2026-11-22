# ALTER 0.3.0a1: the product, as built

**ALPHA.** It works end to end on the owner's machine, through the installed `alter` command. It is
**not** validated by a human evaluation. It claims **no** performance advantage over direct model
execution, and none has been shown (eval_v5, eval_v6). The release gate stays **CONDITIONAL**
(`docs/release/READINESS.md`).

## 1. One loop

`alter` turns a sentence into verified work on a real project folder:

```
understand ─▶ remember ─▶ act ─▶ verify ─▶ learn
 Third Eye     ALTER's     the governed   task       outcome episode
 brief, cited  records     agent (direct  records,   in memory, with
                           baseline)      not words  provenance
```

1. **Understand.** `thirdeye.gather` builds a brief. Every piece is cited and labelled.
   - **Remembered:** ALTER's own records: earlier goals and project notes.
   - **Sourced:** read just now, from the git repository, GitHub (`gh`, read-only) and the
     Obsidian vault.
   - **Not included:** anything left out, with the reason.
2. **Act.** The goal goes to the governed workspace agent, which is the same agent `ci do` uses
   and the arm the evaluations measured as the baseline.
   - Reads are free. Writes and test runs ask you, unless you grant autonomy for this folder.
   - The agent is told to say what it takes the intended outcome to be (citing the brief), to
     pick the smallest verifiable scope, and to run the tests after its last change.
   - No planner is added: planning did not beat direct execution in eval_v5 or eval_v6.
3. **Verify.** The goal is `done` only when the task records show the project's tests passing
   after the last change, across every session of the goal.
   - The agent's own words are never the evidence.
   - Changes left unverified get at most two prompts to verify, then the goal ends `unverified`.
4. **Learn.** When a goal ends, its outcome is written to memory as an episode, under your grant.
   - It records the objective, the status, the files changed, the test runs and the cost.
   - It is computed from the records, with provenance (goal, sessions, verdict).
   - The model's summary stays in the goal, labelled model-written. It is never stored as a fact.
   - The next goal's brief lists this goal as remembered.

## 2. What is implemented, and what is not

| vision component | in this release | where |
|---|---|---|
| **ALTER EGO**, the cognitive twin | **Memory:** bitemporal, with provenance, confidence, expiry, correction and crypto-shredded deletion. **Weekly review** (`alter weekly` = `ci weekly` plus ALTER's goals). **Verified outcomes** become episodes. Skills are separated into alone vs assisted (`ci practice`, `ci skills`). | `memory.py`, `twin.py`, `goals.record_outcome` |
| **THIRD EYE**, contextual perception | **Brief:** repository, GitHub and vault, cited, with an explicit "not included" list. Each source is opt-in. | `thirdeye.py` |
| | Not built: terminal history, other connected apps. | |
| **Intent compiler** | `ci plan` (V3.4) is available, but is **not** used by goals: it was not shown to help. Goals go direct. | `intent_compiler.py` |
| **Execution engine** | **Adapters:** filesystem (reads of large files in labelled parts; one-passage edits), tests, git status and notes, all governed. **Approvals, autonomy grants, Stop-all, undo.** | `agent.py`, `tools.py`, `governor.py`, `tasks.py` |
| | Not built: package installation, browser automation, external APIs, scheduled jobs. | |
| **Intelligence router** | **Model routing and fallback** for questions. **`ci do` routing**, with direct as the default. | `routing.py`, `router.py` |
| **Knowledge foundation** (Obsidian vault) | **Vault search:** the vault's own `nos` ranking, with identical eval numbers. **iCloud placeholders** are fetched with a time bound. **Per-project vault topics.** **`alter ask`:** answers with every statement marked remembered, sourced or recommendation. | `vault.py`, `alter.cmd_ask` |
| | Not built: semantic vault search, a knowledge graph, contradiction tracking, vault write-back. | |
| **Autonomous goal engine** | **Durable goals:** sealed at rest, with checkpoints, events (steer, pause, cancel), approval gates, a background worker, an interruption shown as such, and resume. **Limits:** turns, cost and a time window. **Retry:** a provider failure is retried once, then the goal parks. | `goals.py` |
| | Not built: parallel sub-goals, a dependency graph between goals, a scheduler. | |
| **Talking while it runs** | **The `alter` conversation:** goals run in a worker. What you say becomes a steer event, read before the next model step. "status" answers from the records, with no model call. "pause", "resume", "cancel", "yes", "no". | `alter.Conversation`, `Agent.pause_check` |
| **Verified learning** | **Outcome episodes, weekly goal stats, regression tests.** | `goals.py`, `alter.cmd_weekly` |
| | Not built: per-model performance tracking from goals, workflow optimisation. | |
| **Compute orchestration** | **`ci mesh`:** discovery, measured benchmarks and privacy-first placement, on this machine and the configured providers. It claims no capacity that is not there. | `mesh.py` |
| | Not built: distributing work across devices. | |

## 3. Commands

```
alter                         conversation (in a project folder: goals; elsewhere: questions)
alter "…"                     one goal, worked on now; Ctrl-C pauses it
alter ask "…"                 one question, answered from cited context (one model call, no tools)
alter setup                   first run: identity, claude check, outcome memory (--init: this folder too)
alter init                    make this folder a project (--tests, --provider, --no-write, --no-memory)
alter config [list | get K | set K V]   vault, vault.path, github, memory, autonomy.mode
alter status · context · tasks · memory · weekly · doctor
alter goals [list | show | start [--background] | resume | say | pause | cancel | forget]
alter approve · alter deny    what a goal waits on
alter permissions [provider claude-cli on | vault on | vault topic "…" | github on | memory on|off
                   | autonomy grant --actions N --minutes M | autonomy revoke]
alter vault search · eval · download
alter report [--days N] [--json] [--project here|all]
                              the V1 pilot's metrics from ALTER's own records (no model call)
alter report --log "…" --estimate-minutes N    log a task and your own estimate before the run
alter <any ci command>        `ci` itself is unchanged
```

## 4. Permissions and safety

- **Projects:** ALTER works only inside a folder you made a project with `alter init`, never `$HOME`
  or above it.
- **Model providers** are per project (`alter permissions provider claude-cli on`).
  - A goal never runs on the test double unless that is the only provider and you passed it
    explicitly.
- **Sources** that leave the machine with a goal are GitHub and the vault. Both are off until you
  turn them on. Each brief is logged in the audit (`alter.context`) by source and count, and the
  goal keeps its text.
- **Writes and test runs** ask you, unless an autonomy grant covers them.
  - A grant names the tools, a maximum number of actions and a time limit, for one folder.
  - Everything else always asks.
  - `alter halt` stops everything at once.
- **What ALTER cannot do:**
  - publish anything;
  - push;
  - send messages;
  - spend money, beyond model usage within the goal's cost limit;
  - install packages;
  - act outside the computer.

  There is no tool for any of these.

## 5. Evidence so far

Engineering evidence only (`docs/release/READINESS.md` has the details).
- **Live goals.** Three goals ran through the installed `alter` on this repository:
  - one steered while it ran;
  - one paused and resumed;
  - one continued from ALTER's own records, after "Continue what we were building."
  Two ended **verified**, and their code (twin_do and its CLI and weekly wiring) is in this release,
  marked as written by ALTER.
- **Bugs the live runs exposed.** The third goal surfaced two bugs, both fixed: silently truncated
  tool results on large files, and a goal ending "done" after asking questions.
- **An independent review** found 18 issues and all are fixed, each with a regression test.

## 6. Known limits (alpha)

- **Identifiers from context can reach files.** ALTER can write personal identifiers it saw in
  its context into files. It did so once, using the owner's name as a test user; this was caught
  by `scripts/scan_identity.py --history` before any push. Scan before you push. Next step: run that
  check on a goal's changed files before it counts as done.

- **No human evaluation.** Nothing here shows ALTER helps people more than direct use of a model.
- **Weak tests weaken "verified".** It means "the project's own tests passed after the last
  change", and a weak test suite makes that weak evidence.
- **The vault search is keyword-based** (BM25 plus titles and links): "car" will not find
  "automobile".
- **The conversation prints progress** by polling, every second. It is not streaming.
- **One worker per goal.** Two goals in the same project share the agent's one open session per
  project, so run one goal at a time in a folder.
- **Verified for one configuration only.** Identity isolation is verified for `claude` CLI 2.1.316
  only (`docs/SECURITY_WHAT_LEAVES.md`). Re-run `alter doctor` after a CLI update.
