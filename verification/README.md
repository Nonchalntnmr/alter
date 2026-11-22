# ALT-4 five-check verification

Continue application code from a722d731abc99e741782b2d9e4ec1ca526ae16cc. The verifier adds no sandbox bypass and makes no model calls. It executes a real disposable canary/namespace/environment probe through the unchanged `sandbox.confined_command`, then a confined system Python smoke. Only after that passes does it run the five named tests unchanged. Return codes:0 PASS,1 FAIL,2 BLOCKED. A skip/missing result is BLOCKED, never PASS. Synthetic report-parser tests do not prove Linux execution. Generated reports include UTC time, source hashes, confinement receipt, exact test identities and artifact hashes.

## Exact blocked checks

All are in tests/test_specialist_execution.py:

| Exact pytest name | Required capability | Observed failure and intended proof |
|---|---|---|
| test_three_attributable_workers_real_files_and_test_receipts[memory] | Verified Linux bubblewrap plus system Python; in-process test state | run_tests refuses on Darwin; final status incomplete rather than succeeded. Intended proof: three separate workers, real files and passing confined tests. |
| test_three_attributable_workers_real_files_and_test_receipts[sqlite] | Same confinement plus SQLite/process locks | Same refusal/incomplete status. Intended proof also uses actual durable backend. |
| test_modified_artifact_cannot_satisfy_final_completion[memory] | Same confinement; successful initial run | Setup cannot reach succeeded before testing later file mutation. This is a blocked precondition, not proof stale-artifact detection failed. |
| test_modified_artifact_cannot_satisfy_final_completion[sqlite] | Same confinement/initial run plus SQLite | Same blocked initial completion before later mutation assertion. |
| test_sqlite_restart_continues_dependencies_without_repeating_completed_action[sqlite] | Same confinement plus SQLite close/reopen and process locks | Persisted builder survives restart; final verifier cannot run tests and status stays incomplete. Full secured restart proof remains blocked. |

The prior pytest failure messages are retained in receipt-hardening.xml. Their BLOCKED classification comes from the individually observed ToolError `verified test confinement unavailable on this platform`, not from treating all test failures as infrastructure issues.

## Native Linux path (preferred)

Use an already authorized disposable Debian/Ubuntu machine or CI runner with user/network/PID/mount namespaces available to the non-root test user. A Linux label/container alone is insufficient: the actual probe must pass. No new paid VM or remote access is provisioned here.

An administrator on that disposable machine can install prerequisites:

```sh
sudo apt-get update
sudo apt-get install -y bubblewrap python3 python3-venv python3-dev build-essential git patch unzip
```

Do not run these commands on the Mac or an unrelated/shared production host. No sysctl, AppArmor, seccomp, setuid or host security setting should be changed to make the probe pass. If namespaces are denied, choose an already supported runner and retain BLOCKED.

Obtain the full baseline repository, then apply the combined patch from this delivery. Feature commits are local and are not asserted to exist on GitHub:

```sh
git clone https://github.com/Nonchalntnmr/alter.git alter-verify
cd alter-verify
git checkout --detach 9b2f5f48da4250696e18d6e26bd204b6a12f6601
# Copy the downloaded, hash-verified delivery/combined.patch into this checkout.
patch --batch --forward -p1 < combined.patch
bash verification/linux-setup.sh
```

`linux-setup.sh` uses system Python to create a venv for ALTER's control plane. Package constraints resolve for that Python, and the exact resulting environment is saved with pip freeze. The generated project fixture uses system `python3`, which is present inside bubblewrap. It requires only standard-library modules. This setup is reproducible in commands and source identity; package selection is recorded rather than represented as an immutable future PyPI resolution. Python>=3.11 is required by package metadata. Network access for public source/apt/PyPI is needed only during setup. The probe and project tests run without host network.

After a successful real probe, the runner records the five unmodified pytest node IDs and JUnit results. Reports go to verification/results. Setup is prepared but has not been executed on Linux in this run. Preserve the report and JUnit/logs even when return code is2. Do not change the command to ignore errors when using it as an acceptance gate.

## Containers and GitHub CI

No Docker/Podman/Colima/Lima/Multipass/QEMU executable is installed in the current runtime. A future existing Linux container runner can use the same command only if its existing namespace policy passes the real probe. This delivery deliberately prescribes no privileged container, extra host capabilities or unconfined seccomp workaround. If it fails, use the native Linux path above.

The repository already has Linux GitHub Actions jobs, but local gh cannot read the authenticated user or Actions permissions. Public repository readability does not grant workflow access. Its current tests.yml is push/PR-triggered, not workflow_dispatch. No push, remote workflow change or run was made. GitHub documents write access and default-branch workflow_dispatch requirements for manual dispatch: https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow . Bubblewrap's namespace requirements and security scope: https://github.com/containers/bubblewrap .

## Model access and external inputs

These five deterministic execution tests need no real-model credentials. Do not let the separate live-model dependency block their Linux run.

At this admission Paperclip discovery reports an installed, agent-usable OpenAI AI connection. That supports this Paperclip agent; it is not proof of an exported credential, local ALTER adapter integration, selected application model or project egress permission. No stored credential was read or copied. The ALTER process has no Anthropic/OpenAI/Google API credentials, Claude CLI or configured local tool model; registry availability for every nonmock tool route is false. No provider request was made to test authentication or consume quota.

For the later live demonstration, supply an already authorized tool-using provider through ALTER's documented configuration/secure environment injection, with the desired model and project vendor permission. Do not paste secrets into issues, evidence, command history or this README. Paperclip native authentication is neither silently exported nor treated as permission for a billable API call.

What the owner must provide:
1. A nonsecret reference to an existing authorized Linux machine/CI runner and its permitted access method; it must pass the real confinement probe. Alternatively a board user can inspect Paperclip environment inventory, which returned final403 board_access_required to this agent.
2. Read/write/workflow permission if GitHub CI is chosen; no public push or new infrastructure spend is assumed authorized.
3. Separate secure ALTER provider configuration and project permission for the later live demonstration, not for these five tests.

ALT-6 may independently test read-only lifecycle views, creation/pause/cancel/approval-denial persistence and wheel installation using the delivered real service without claiming successful generated-code execution. Its existing ALT-4 dependency and native-session recovery state require parent/board reconciliation; no acceptance requirement is removed by this document.
