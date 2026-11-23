# Specialist execution service (ALT-4)

The service adds a structured builder -> interface -> verifier dependency plan for a natural-language goal. Each assignment runs in its own attributable Agent session, through the existing TaskQueue, Governor and Executor. Role labels grant no tool permission. A custom dependency DAG can be provided as `work`; unknown dependencies, cycles and unsafe artifact paths are rejected. The default decomposition is a conservative template, not a claim that a model generated a goal-specific plan.

```python
from pcim.run_service import RunService
service = RunService(ci)  # requires ALT-5's RunStateStore(ci.store)
run = service.start(uid, project_id, goal, set(workspace.provider_vendors(uid, project_id)))
run = service.advance(uid, project_id, run['id'])  # one ready worker per call
```

`start(uid, project_id, goal, vendors, *, provider=None, model=None, work=None, limits=None)` creates state only. `get`, `advance`, `pause`, `resume`, `cancel` take `(uid, project_id, run_id)`. `list` takes `(uid, project_id)`. `steer` adds `text`; `retry` adds `work_id`; `resolve` adds `task_id, *, approve: bool`. All return JSON-compatible snapshots. Events are `snapshot['events']`. `attempts[-1]['pending']` contains task IDs; `pending_detail` supplies public task records for exact-action review. CLI and API adapters must use the same service; an adapter does not create a new executor or approve a plan's tools implicitly.

Snapshot schema version 1 includes `id, project_id, goal, status, revision, created_at, work, attempts, events, limits, usage, verification, steers`. Work includes dependency IDs and evidence requirements; attempts include stable specialist identity, separate session ID, route, pending task IDs, usage, task references, file hashes and executed test references. `RunState` in work_contracts.py specifies the persistence protocol; ALT-5 owns its encrypted SQLite implementation and migration/lease behavior. No production in-memory fallback is provided.

One unfinished run per project is permitted. All service mutations use the persistence adapter's project lock and CAS revision. `advance` serializes specialists and performs at most one assignment. Pause and cancel operate between advance calls; they do not asynchronously interrupt a provider call while the project lock is held. Legacy interactive sessions remain separate and are not closed by specialist session creation. Direct callers outside RunService must respect project execution ownership; the lock does not claim to serialize arbitrary external editors or legacy CLI writes.

A worker succeeds only with completed tool records and an attributable current file hash or executed test receipt. Required artifact names must occur in that worker's successful writes. The default three workers each require meaningful file output; the verifier also needs passing configured tests after its last write. Final success requires current final file hashes and a successful configured test after the final run write. Model completion text is never positive evidence. These checks establish executed artifacts and configured tests, not arbitrary product acceptance: callers must supply independent behavioral coverage and evaluate the delivered application. The service does not claim the broad V1 application/demo gates from receipt-only verification.

Limits bound aggregate attempts, per-work attempts, model steps, tool actions and elapsed wall time. Agent turn limits remain in force. The elapsed limit includes paused time. A provider call already in flight may exceed the time ceiling before returning; provider-specific transport timeouts still apply. Token/cost fields are observed when available, not a prepaid spending authorization or a guarantee against single-call overshoot. No new service, model charge or permission is enabled.

Retries are explicit and bounded. In-flight/crashed actions become interrupted and are never replayed by advance or retry; reconciliation must inspect task records and actual state before a future implementation authorizes a new attempt. Pending approvals persist. Resolving checks owner, project, session, task status, run budget and current provider permission. Cancellation closes sessions before cancelling pending tasks so they cannot resume. Late or unrelated approvals cannot resolve through this service. Existing TaskQueue approval rules still govern direct legacy entry points.

Routing uses registered tool capability, current availability and allowed vendors. Explicit provider/model pins cannot silently fall back. Automatic routing excludes test doubles; mock requires explicit selection. Reliability, latency and prices are not fabricated or treated as fresh provider measurements. The specialized routing slice currently reuses registry selection rather than inventing unsupported comparative quality scores.

## Verification and remaining integration

`tests/test_specialist_execution.py` uses deterministic mock replies but real local filesystem actions and configured Python behavioral checks under the baseline Executor. It covers three identities, dependency blocking, unsupported completion, approvals/denials, cancellation, isolation, stale artifact detection, owner/task binding, changed provider consent, interrupted attempts and bounded retries/resources. These results are not live-model or OS-sandbox evidence.

ALT-5's exact run_state.py/store.py/backup.py patch is now integrated. Actual SQLite tests close/reopen the store between workers and while awaiting approval; completed effects are not repeated. Attempts carry stable action_key and executor evidence_refs, and events are mirrored idempotently to the encrypted append-only ledger. ALT-6 owns CLI/API wiring. ALT-7 supplies the final request content preflight and confined test executor; integrated test execution on unsupported macOS confinement must remain unavailable, not bypass security. System Git calls still invoke missing Apple Command Line Tools. Workspace-local Dulwich supports actual feature-branch commits/export without changing the host.

No real tool-using provider is currently available: Claude CLI is absent, Anthropic/OpenAI credentials are absent, and the OpenAI model is unset. Google/local registry entries support single-turn ask rather than Agent tools and are also unconfigured. An authorized configured tool-using provider plus independent acceptance checks are needed for the live three-worker application demonstration. No model was called or billed by these tests; Paperclip inference is separate and unmeasured here.

## Security integration

The final Agent request is checked with `assert_safe_payload` after tools and history are assembled, immediately before each adapter call. This same loop handles retries and resumed approvals. PersonalCI's generated prompt and the query planner also preflight their complete rendered prompts before generate. Contaminated requests raise label-only ContentBlocked errors; stored history is not rewritten.

Final specialist completion calls `verify_export` for the final write manifest and binds every returned digest to the successful write receipt. Unsafe, missing, binary or contaminated artifacts cannot qualify as completed. This finite text policy does not establish arbitrary behavioral acceptance or a binary export policy.

Specialist test runs need a workspace with `test_confinement="required"`. On macOS that refuses before executing project code, so specialist completion stays BLOCKED there. Ordinary ALTER goals keep running approved tests on the host (owner decision, 2026-11-23); those host receipts never satisfy the specialist gates.

Worker and final completion now both reject legacy host-test receipts. A qualifying test must have integer exit code zero, an explicit false timeout flag, and a bubblewrap receipt recording verified confinement, isolated network, disposable-copy filesystem and no host writes. Missing, malformed or weaker receipts are insufficient even when task status says succeeded. Receipt-gate unit tests are synthetic and do not establish real sandbox execution.
