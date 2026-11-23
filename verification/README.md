# Verifying the five confined specialist checks

Five cases in `tests/test_specialist_execution.py` need passing tests inside a verified Linux
sandbox:

- `test_three_attributable_workers_real_files_and_test_receipts` (memory and SQLite);
- `test_modified_artifact_cannot_satisfy_final_completion` (memory and SQLite);
- `test_sqlite_restart_continues_dependencies_without_repeating_completed_action` (SQLite).

Where no verified sandbox exists (macOS, or Linux without a working bubblewrap), they are
skipped with the reason, never passed. With `ALTER_REQUIRE_CONFINEMENT=1` they run, and they fail
if confinement is unavailable.

## Where tests run

A workspace's `test_confinement` setting decides it.

- **`"none"`**, the default (and every workspace authorized before the setting existed), runs
  approved tests on the host, as ALTER V1 does. The receipt says `backend: none, verified: false`.
- **`"required"`** (`alter init --confine-tests`, or `test_confinement` in the HTTP API) runs them
  only in the bubblewrap sandbox, and refuses where it is unavailable.

Specialist completion accepts only confined receipts whose `snapshot_sha256` matches the live
project. A host receipt never counts there.

## Running the verifier

On a disposable Debian or Ubuntu machine, or a CI runner, that already permits unprivileged
user, network, PID and mount namespaces:

```sh
sudo apt-get install -y bubblewrap python3 python3-venv   # root-owned /usr/bin/bwrap
bash verification/linux-setup.sh
```

`scripts/verify_alt4_execution.py` first runs a real probe:
- an outside canary must stay unchanged;
- the network and PID namespaces must be separate;
- credentials must not reach the sandbox.

It then runs a confined system-Python smoke test, and only then the five cases. Its exit code is
0 when all pass, 1 when any fails, and 2 when blocked. Its report records source hashes, the
confinement receipt and the JUnit results. Change no kernel, AppArmor or seccomp policy to make the
probe pass. If the probe is denied, the result is BLOCKED.

The `confined-linux` job in `.github/workflows/tests.yml` runs the same verifier on a hosted
`ubuntu-24.04` runner and uploads the report. It is not yet a required check.

Only system interpreters and packages under /usr are visible inside the sandbox, so the fixture
uses only the standard library.
