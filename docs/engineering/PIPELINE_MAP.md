# Intent-to-execution: what exists, stage by stage

Mapped 2026-11-17 against `release/alter-0.2.0rc2` @ `385cb7b`. This is a map, not a redesign. Every
row names real code, and every gap names the evidence behind it. Nothing here is a measured claim
unless it says so.

| stage | where it lives | status | evidence |
|---|---|---|---|
| user intent | `terminal.py`: `ci "…"` (text answer), `ci do` (work in a folder), `ci plan`. Also `api.py` and `mcp_server.py` | works | e2e 37/37 |
| context interpretation | `engine.ask_routed`: consent-filtered memory selection (`policy/`, `memory.py`, `sovereignty/`), the project briefing (`workspace.py`), task type (`intent.py`), mode (`modes.py`) | works | the earlier milestones |
| uncertainty estimation | `intent_compiler.validate`, from the model's `interpretations[].p` and `assumptions[].confidence`; `router.signals`, which is keyword-based | **uncalibrated**: the p values are the model's own; the keyword signals miss most ambiguity | v4 RT1 4/21 |
| clarification decision | `intent_compiler.gate` (clarify); `router.route` (the keyword rules, the default); `router.route_from_plan` (planning-assisted, opt-in) | the compiler is good (v4 H3 1/21, H2 20/23); the default router is weak | v4 RESULTS |
| model and tool routing | `routing.py` (tiers, preferences, floors), `registry.py`, the fallback chain in `engine`, `mesh.py` (placement); `router.py` picks the first move | works; the routing benchmark is earlier work | `experiments/routing` |
| optional planning | `intent_compiler` (V3.3); `plan_exec` (contract execution) | works; no advantage over direct shown | v2, v4 paired |
| permission validation | `governor.py` (`decide`, `recheck`), `workspace.py` (authorizations, autonomy grants), consent and egress in `sovereignty/` | works; enforced at execution time | denials in the v4 and v5 smoke runs |
| execution | `agent.py` (a governed session, the session lock), `tasks.py` (the queue, Stop-all), `tools.py` | works | 32/32 verified across paired v2 and v3 |
| **result verification** | `plan_exec._analyze_tasks` (a change is required, and the tests must pass after the last write) | **gap on the direct path:** `ci do` reports the agent's own account; nothing checks that tests ran after its last write | code reading; v5 measures false claims through its judges |
| failure recovery | `plan_exec` (interrupted, retry, resume, halt); the agent's Stop-all recovery; the adapters' error classification and fallback; the bounded re-ask | works; the re-ask is covered only by fault injection | `test_reask_recovery.py`; 0/157 live triggers |
| **memory update** | `memory.py`, `feedback.py` (`ci outcome`), `learning.py`, `twin.py`, the skills and spacing code | **gap:** `ci do` sessions record no outcome, so the twin learns nothing from routed work | code reading |
| user-facing outcome | `ic.render`, `px.render`, `terminal._print_turn`; receipts (`ci audit`) | works | e2e |

## Gaps, ranked by the directive's heuristic (impact × confidence / effort)

1. **Default clarification recall.** It is high impact and well evidenced (RT1 0.19). The
   candidate, planning-assisted routing, is under measurement in eval_v5. No change until that
   reports.
2. **No result verification on `ci do`.** It is high impact, the evidence is code reading, and the
   effort is low.
   - **The change:** after a direct session ends, `ci do` reports whether the configured tests
     passed after the last write. It reuses `plan_exec`'s evidence rule, read-only, and changes no
     outcome.
   - **The test:** a scripted session that writes and never tests must be reported as "not
     verified".
   - It is independent of eval_v5's frozen code, so it is built on a separate branch.
3. **No outcome record from `ci do`.** This one is medium. It matters for the cognitive twin
   (§7), and it needs a design for consent first.
4. **A local I/O error inside a routed call** is recorded as a provider failure (READINESS §4.5).
   Low impact, low effort.
5. **Router over-triggers** on "clear", "cut" and one-word commands (READINESS §4.5). Low; it is
   superseded if planning-assisted routing replaces the rules.
