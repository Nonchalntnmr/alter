# eval_v6 protocol

Written 2026-11-18, before the pool is read. It inherits everything in
`experiments/eval_v5/PROTOCOL.md` §2 unless stated:
- sandboxes, with a fresh `PCIM_HOME` and repository per run;
- every step through the `ci` CLI;
- the fixed approval policy;
- the persona loop;
- three blinded judges in pairwise, seeded A/B order;
- redacted transcripts;
- the loss rule;
- the guards.

## What is different from eval_v5, and why

| | eval_v5 | eval_v6 | why |
|---|---|---|---|
| arms | A, R, P (V3.3 planning), PA (planning-assisted) | A (direct), R (keyword router), **S (selective clarification)** | eval_v5 showed planning worse than direct. S is the direct-first candidate: the governed agent, told to ask one question only when the files cannot settle a choice, and to keep to the request. It adds no model call. |
| pool | 24 tasks, 3 of which ended up needing a question | 24 tasks, **12 that need one by design**: a fact that is essential, not discoverable and tacit | to measure asking at all. The enrichment is a design choice, not a frequency claim. |
| needs-question label | the persona's report of which facts it stated | **the task's tacit facts**, plus any volunteered essential fact the persona left out | deterministic, from the task. Both reasons are recorded. |
| persona | could state any fact | **never mentions a tacit fact unless asked** | that is what makes the need real |
| assignment | 2 tasks of different categories per persona | **1 task that needs a question and 1 that does not**, per persona | balances the personas' phrasing across both kinds |
| primary endpoint | blinded preference | **first-pass success**: verified with no correction | it is the value a well-timed question should create |
| new per-run fields | | first-pass success; `ci do`'s verification verdict and claim check | verification calibration |

## Runs

- **Arms:** 24 person–task pairs × 3 arms. R is A's run where the keyword router routes direct.
- **Order:** within each pair, the arms run in a seeded order.
- **Recorded:**
  - requests, `assignment.json` and `raw.jsonl`;
  - `transcripts/` (every CLI command, persona call and agent turn, with the served model per
    step);
  - `judge.jsonl`, `RESULTS.json` and `RESULTS.md`;
  - `FROZEN.txt`, with the hashes, seed, code commit, CLI version and models.

## Metrics (per run)

| metric | definition |
|---|---|
| success | the hidden check passes on the final repository |
| first-pass success | success with 0 corrections |
| needs_question | see above |
| asked | the system put a question to the person (an agent turn ending in a question before the work passes, or a router's question) |
| clarification recall | asked, among runs with needs_question |
| clarification precision | needs_question, among runs that asked |
| unnecessary questions | asked, among runs without needs_question |
| corrections | 0–2, written by the persona when the hidden check fails |
| unauthorized | attempted: writes or runs denied by the policy, each listed with tool and path. Successful: a change outside the allowed paths, or outside the repository. |
| latency | wall seconds per run |
| person-time | approvals × 10 s + answers × 45 s + corrections × 60 s |
| cost, tokens | from the CLI's own accounting |
| satisfaction proxies | the persona's 1–5 rating and "would trust unattended"; the judges' ratings and preference |
| verification calibration | `ci do`'s verdict, and its claim check, against the hidden check |

## Limits

These are restated in `PREREGISTRATION.md`.
