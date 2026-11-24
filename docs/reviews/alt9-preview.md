# ALT-9 restricted developer preview

Base: `0fdbe0848cf452f911aa4f78ddaa10fcb6aee33f` (merged PR #24).
This patch preserves execution semantics and adds honest policy display and selection.
Owner direction of 2026-11-24 supersedes the original blanket execution-disable request.

## Supported behavior and entrypoint coverage

| Entry point | Supported preview behavior | Verification boundary / coverage |
| --- | --- | --- |
| `alter init`, API workspace authorization | Default/legacy `none` permits approved host tests; `required` refuses without verified bubblewrap | Existing `test_test_confinement_modes.py`, `test_execution_boundaries_v1.py`; installed smoke executes an approved host test, rejects its specialist receipt, and proves required-mode refusal leaves no sentinel file |
| Dashboard Workspace | Shows saved test policy and submits explicit `none`/`required`; required checkbox reflects saved policy | `test_run_dashboard.cjs` checks both values and labels |
| `alter status`, project briefing API, rendered briefing | Shows policy; host tests say unconfined/backend none/verified false; requirement is not proof | `test_preview_labels.py` checks authenticated API read after SQLite reopen, no new tasks or files; installed CLI/API smoke |
| `alter runs` and `/v1/projects/{pid}/runs` | Create/list/read/events, explicit advance, pause/resume/cancel/steer and approval operations | Existing `test_run_interfaces.py`, `test_run_integration.py` cover ownership, adapter parity, persistence/reopen, denial and revocation; creating a plan does not start a background worker |
| Dashboard Execution | Shared lifecycle controls; raw attempts, evidence and verification displayed; preview limitation visible | DOM fixture verifies escaping and actions, not browser or live execution acceptance |
| Natural-language `alter`, legacy `ci` / `pcim`, goals/background/resume/approval | Existing provider consent, governor, approval/autonomy and durable recovery behavior retained | Full existing suite; no claim these paths are disabled. No live provider call made for this review |
| Specialist evidence / `verify_run` | Host receipts cannot qualify; confined receipt must match current snapshot | Existing `test_specialist_receipts.py`, `test_specialist_execution.py`; no gate weakened |
| Linux secured checks | Still BLOCKED 5/5 per supplied owner evidence (AppArmor userns restriction) | Not rerun, no sysctl change or runner provisioning |

No secured/live V1 acceptance, public publication, push or PR is authorized by this result.
The mock identity used by lifecycle smoke does not establish natural-language or live provider execution.
Actual host subprocess execution is separately evidenced by the installed smoke, not simulated.

## Install, launch and inspect

Use Python 3.11 or newer and working Git for repository tools. Install the supplied wheel in a disposable venv:

```sh
python3 -m venv preview
preview/bin/pip install 'contextual_intelligence-0.3.0a1-py3-none-any.whl[api]'
export PCIM_HOME="$PWD/preview-data"
export PCIM_EMBEDDER=hashing-512
preview/bin/alter setup --providers mock --no-memory
# In a disposable project directory:
/path/to/preview/bin/alter init --no-memory --no-write --tests 'python3 -c pass'
/path/to/preview/bin/alter status
/path/to/preview/bin/alter runs list --json
# Optional confined-only policy: use --confine-tests at init.
/path/to/preview/bin/pcim serve --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765 and use the local token through the dashboard login.
Do not put the token in reports. Mock is for local lifecycle inspection only.
A real provider needs separate existing consent/configuration; this review did not configure one.

## Verification commands

```sh
TMPDIR="$PWD/../tmp" PYTHON="$PWD/.venv/bin/python" bash scripts/check.sh
node tests/test_run_dashboard.cjs
python -m build
SMOKE_ROOT=/absolute/disposable/path PCIM_EMBEDDER=hashing-512 installed/bin/python evidence/installed_smoke.py
```

The DOM test requires workspace-local `npm install --prefix .dev jsdom`.
The reviewer evidence archive contains the smoke script, exact environment freeze,
base/result logs, wheel, source distribution and incremental patch. Test results and
artifact hashes are recorded in its `review-result.md` and manifest.
