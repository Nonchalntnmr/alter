# ALT-7 execution and acceptance integration

Baseline: `9b2f5f48da4250696e18d6e26bd204b6a12f6601`. This slice preserves tool names and CLI entry points. It does not establish integrated V1 acceptance.

## Execution boundary

Where tests run is the workspace's `test_confinement` setting (owner decision, 2026-11-23). `"none"` (the default, and every workspace authorized before the setting existed) runs the approved command on the host after approval, as ALTER V1 does; its receipt is `HOST_TEST_RECEIPT` (`backend: none, verified: false`), so it is honest evidence for a V1 goal and never confined evidence. `"required"` (`alter init --confine-tests`, or `test_confinement` in the HTTP API) always enters `sandbox.confined_command`, with no host fallback. The Linux adapter requires a root-owned, non-writable `/usr/bin/bwrap` or `/bin/bwrap` and `/usr/bin/python3` for its real denial probe. It does not install or reconfigure either. Unsupported platforms and failed probes refuse before project code starts.

Every invocation probes a disposable outside canary, network/PID namespace separation, environment removal and writable isolated workspace. Tests then use a bounded disposable snapshot, without secrets by filename, repository metadata, virtual environments or node_modules. Symlinks, hard-linked/special files and oversized snapshots fail closed. Descriptor-relative reads refuse raced symlinks. No project writes are copied back. The probe verifies namespace separation; it is not a universal sandbox escape proof.

The sandbox exposes read-only system runtime directories (`/usr/bin`, `/usr/lib`, `/usr/lib64`, `/bin`, `/lib`, `/lib64` where present), a private proc/dev/tmp, and `/work`. It excludes host home, `/usr/local`, host network and inherited environment. Configure a system test runner such as `python3`, or a relative project executable; host virtualenv absolute paths are refused. Runtime dependencies must already be available in the permitted runtime. This is intentionally narrower than trusting an arbitrary host development environment.

The existing supervisor keeps cancellation/timeout and project-lock lifetime behavior. For confined runs, CPU, address-space (Linux), process (Linux, a per-user bound of 4096), descriptor, core and output-file limits supplement confinement, and stdin is closed; host runs keep V1's behavior. Output is spooled into bounded files, inspected before tail truncation, and contaminated strings are withheld, in both modes. macOS real sandbox execution is **not implemented**: a required-confinement run refuses there. Every confined receipt records `snapshot_sha256`, and the specialist worker and final gates recompute it from the live project, so a receipt only counts for the exact files it tested. Only system interpreters under /usr are visible inside the sandbox; a project that needs its own virtualenv cannot be tested confined yet.

## Provider and artifact integration requirements

`sovereignty.content.assert_safe_payload(payload, markers=None)` recursively examines strings in dicts/lists/tuples/dataclasses and raises `ContentBlocked` with fixed reason labels only. It recognizes credential assignments, common provider tokens, private-key headers, bearer values, credential URLs and explicitly designated synthetic canaries. Exact private markers can be supplied; marker labels/values are never echoed. This finite detector has false positives/negatives and is not a complete DLP system. Encoded/fragmented secrets and arbitrary personal identifiers require further review.

ALT-4 must invoke this helper immediately before **every assembled provider request**, including raw user/project text, historical messages, retries, resume and planning. Do not rewrite signed history; block a contaminated request and surface the label-only error. `EgressGuard` now blocks secret content despite broad consent, rejects wrong-user items and fails closed on unexamined/cross-user lineage. Existing consent and instruction gates remain in force. Tool reads scan whole content before pagination; search omits contaminated files; writes/undo backups reject secret content. These controls do not replace the final assembled-request preflight. ALT-7 does not edit ALT-4's owned agent/runner files.

Before accepting exportable artifacts, call `identity_guard.verify_export(root, exact_relative_files, marks=None)`. Require `accepted`, then package exactly the returned file digests. Missing, unreadable, unsafe, binary and private-content files fail this text-artifact gate. A separate explicitly reviewed binary-artifact policy is needed for images/build outputs. The historical advisory identifier scan remains compatible; it is insufficient to accept a new artifact by itself. Integrate this stronger gate in the completion/export owner before claiming safe delivery.

## Checks

Run from the repository with a Python 3.14 test environment:

```
python -m pytest -q tests/test_execution_boundaries_v1.py tests/test_acceptance_v1.py tests/test_memory_and_sovereignty.py tests/test_isolation.py tests/test_tools_parts.py
python -m pcim.acceptance_v1 freeze benchmark-manifest.json
python -m pcim.acceptance_v1 verify evidence-root receipts.json
python -m pcim.acceptance_v1 compare benchmark-manifest.json
```

The frozen manifest has the exact user prompt and UTF-8 digest, fixed gates/rubric, empty results, and **unset ceilings**. Configure identical arms and authorized numerical time/token/retry/cost ceilings before scored runs. The validator refuses changed prompt bytes and unmatched resources/model/tools/policies. Missing token/cost telemetry stays unavailable; fewer than three pairs are exploratory; the reporter never manufactures a winner. No live benchmark has run.

The acceptance receipt checker verifies evidence hashes and keeps missing gates not-run. Supplied reviewer verdicts still need independent behavioral review. It cannot establish app quality from a filename, role label, exit code or model narrative. `acceptance_http.check_app` supplies executable local auth/task/ownership/logout checks using a documented JSON route contract. Map that contract to the generated app before scoring. It accepts explicit loopback HTTP targets only, uses synthetic accounts and removes its task fixture; accounts remain in the disposable app database. It never emits credentials/response bodies. Its partial checks remain inconclusive for full F02/F03 until expiry/password-storage/UI/input/storage review finishes. Fake app tests verify the evaluator itself; they are not live application evidence.

Other matrix gates (UI, restart, full clean installation/provider flow, attributable ALTER agents, comprehensive approvals, audit correlation, recursive proof and matched live performance) remain integration-stage checks, with explicit not-run receipts until independently exercised. Security tests here cover write approval replay, changed payload, symlink swap, project/user task separation, secret egress and failure traces; they do not authorize any real protected side effect.

## Residual review and integration

- Final raw/history provider-request preflight and artifact acceptance wiring must be integrated by their owners; the helpers alone do not close those paths.
- Real bubblewrap execution, cancellation, denial and resource behavior require supported Linux evidence. No live Linux pass was obtained on the macOS development host.
- Unconfined host runs never satisfy the specialist or secured gates or the five Linux checks (`orchestration.verified_test`). That, not refusing all tests on macOS, is what "unverified execution paths remain disabled" means. The five checks skip, with the reason, where no verified sandbox exists, and `ALTER_REQUIRE_CONFINEMENT=1` (the Linux CI job and `scripts/verify_alt4_execution.py`) makes them run.
- Native Git remains unavailable on this host; a workspace-local Dulwich dependency is used only to create/export the feature commit, not as a fake Git executable for application tests.
- Full integrated independent review, real-provider demonstration, application acceptance, fair matched trials and recursive PR proof are outstanding. No production deployment, protected merge, external communication, paid service or credential/host-policy change is performed by this slice.
