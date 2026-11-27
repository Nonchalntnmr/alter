# Shared terminal and web execution

This adapter change consumes ALT-4's `pcim.run_service.RunService` and ALT-5's state backend. The baseline alone does not contain them. Install an integrated source/package before using shared execution. A missing service produces an actionable CLI error / HTTP 503. No adapter implements a coordinator, worker loop, or persistence layer.

## Local install

Python 3.14 was used for verification. The package requires Python >= 3.12, the lowest version CI tests. From integrated source:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install '.[api]'
.venv/bin/alter --version
.venv/bin/ci --help
.venv/bin/alter runs --help
.venv/bin/python -m pip check
```

Or install the attached local wheel with its API extra into a fresh venv. No public release is needed. Dependencies download from PyPI. The adapter-only wheel is installable but cannot execute specialist runs until core/state are integrated.

Use `alter setup` for a new identity; existing identities remain unchanged. In a disposable project directory use `alter init --tests 'python -m pytest -q'` and review provider/project permissions. Existing `alter` natural-language goals, `alter goals`, `ci`, `pcim` and command forwarding remain available. Shared specialist execution is explicitly selected through `alter runs`; it does not silently migrate legacy goals.

```sh
alter runs start 'Build a task management application with authentication, persistent storage, a modern UI, automated tests and local deployment configuration'
alter runs list --json
alter runs show RUN --json
alter runs advance RUN
alter runs events RUN
alter runs pause RUN
alter runs resume RUN
alter runs say RUN 'Preserve the existing public interfaces'
alter runs approve RUN TASK
alter runs deny RUN TASK
alter runs cancel RUN
```

All commands use the owner identity and authorized project for the current directory. `start` saves a plan; `advance` performs at most one specialist step synchronously. Resume changes state; explicitly advance to execute the next step. Inspect pending task details with `alter tasks` before approving. Approval does not grant any permissions beyond the underlying governed action. Ctrl-C during a synchronous operation may leave interrupted state: inspect the run and reconcile through the service; do not assume the operation rolled back or blindly repeat it.

## Web/API

Start the existing local dashboard with `pcim serve` (127.0.0.1 default; `scripts/install_alter.sh` installs it unless `--no-web`); paste your existing API bearer token. In Execution select the project, describe the outcome, create a plan, inspect it, then select Run next step. The same service snapshots supply plan/tasks/roles, progress/attempts, evidence, verification, pending approvals and history. Polling is read-only, pauses when hidden or a run action has focus, and preserves objective/steering drafts. Retry errors through Refresh or the affected control. No browser visual/accessibility certification is claimed from DOM tests.

Canonical routes (all require bearer auth):

- `GET/POST /v1/projects/{pid}/runs` (POST `{objective}`; permitted vendors come from server-owned project settings)
- `GET /v1/projects/{pid}/runs/{rid}` and `/events`
- `POST .../{rid}/advance`, `/pause`, `/resume`, `/cancel`
- `POST .../{rid}/steer` with `{message}`
- `POST .../{rid}/approvals/{tid}` with `{approve: true|false}`

Service calls always receive authenticated uid and project id. Service ownership checks are required on every run/action; foreign IDs remain absent. Existing task endpoints supply exact pending action details; pending IDs originate only from the selected run's latest attempt. HTTP errors: 401 bearer, 404 absent/foreign, 403 permissions, 422 invalid input, 409 busy/transition/resource state, 502 provider errors, 503 missing core service.

## Contract / integration

Frozen ALT-4 contract: `RunService(ci, state=None, agent=None)`. `start(uid,pid,goal,vendors:set[str])`, `list(uid,pid)`, `get(uid,pid,rid)`, `advance/pause/resume/cancel(uid,pid,rid)`, `steer(uid,pid,rid,text)`, `resolve(uid,pid,rid,tid,approve=bool)`. Snapshot fields include goal/work/attempts/events/verification; pending approvals are the last attempt's pending task IDs. No independent frontend state transitions are inferred.

The dedicated Python tests use strict-signature service fixtures and compare CLI/HTTP calls and results, ownership/authentication, errors, input validation and legacy dispatch. These are adapter checks, not live model or integrated engine evidence.

```sh
PCIM_EMBEDDER=hashing-512 .venv/bin/python -m pytest -q tests/test_run_interfaces.py tests/test_api.py
npm install --prefix .dev jsdom
node tests/test_run_dashboard.cjs
```

The DOM check exercises actual dashboard JavaScript with fixture HTTP responses, tests action binding, escaping, drafts and error handling. It is not a real-browser visual test. Native Git on this host invokes missing Apple developer tools. Workspace-local Dulwich is sufficient for branch/commit/patch export; it is not a substitute verdict for native-Git-dependent application tests.

## Independent integration verification (2026-11-23)

The current integrated wheel includes ALT-4 engine/state/security and receipt hardening (`a722d73`) plus human-denial fix (`fa88305`). These upstream changes are combined with the adapters; do not apply their patches again. Foreign-owner mutation routes now check project ownership before entering the service lock, consistently returning HTTP 404.

`tests/test_run_integration.py` uses actual RunService and encrypted SQLite. Five checks verify CLI/API create/get/list/events, pause/resume/cancel, owner rejection, error recovery, and reopened state. Four denial variants explicitly select the deterministic mock provider only to obtain pending governed tasks, then deny through CLI or API after provider revocation or budget exhaustion. They assert no additional model request, no file write, persisted denied task and rejection of later approval. They are persistence/control evidence, not live-model acceptance.

Reproduce the installed-wheel check from outside the source import path:

```sh
python3.14 -m venv clean
clean/bin/python -m pip install 'dist/contextual_intelligence-0.3.0a1-py3-none-any.whl[api]'
cd evidence
PCIM_EMBEDDER=hashing-512 ../clean/bin/python installed-smoke.py
cd ..
node tests/test_installed_readonly.cjs
```

The smoke script creates disposable state, imports from site-packages, compares CLI/API state and reopens SQLite, and saves the installed dashboard HTML and real API JSON for the read-only DOM parity check. Install jsdom into `.dev` first as above. This run reinstalled the rebuilt wheel without dependencies into the previously prepared isolated clean-install venv; it did not repeat network dependency resolution into a new venv.

Results: 23 interface/API checks passed; installed smoke, both DOM scripts, fatal-rule lint, pip check, alter version and ci help passed. Core selection (run state, specialist receipts, specialist execution) yielded 83 passed, 5 failed, 2 skipped. The five secured-success cases remain unverified on this macOS host and are not counted as passes. No Linux runner is authorized/available in the recorded board decision. ALT-6 remains incomplete until ALT-4's five real confined Linux checks pass; no generated application, live provider, visual browser, benchmark or V1 acceptance claim.
