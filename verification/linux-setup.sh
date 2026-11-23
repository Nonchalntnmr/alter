#!/usr/bin/env bash
# For a disposable, already-authorized Debian/Ubuntu Linux runner only.
# Operator installs OS packages; this script changes no kernel/security policy.
set -euo pipefail
if [[ "$(uname -s)" != Linux ]]; then
  echo 'BLOCKED: supported Linux runner required' >&2
  exit 2
fi
if [[ ! -x /usr/bin/bwrap || ! -x /usr/bin/python3 ]]; then
  echo 'BLOCKED: operator must install trusted bubblewrap and system Python first' >&2
  exit 2
fi
/usr/bin/python3 -m venv .venv-verify
.venv-verify/bin/python -m pip install -e '.[test]' ruff==0.17.6
.venv-verify/bin/python -m pip check
mkdir -p verification/results
.venv-verify/bin/python -m pip freeze > verification/results/environment-freeze.txt
export PCIM_EMBEDDER=hashing-512 HF_HUB_OFFLINE=1
.venv-verify/bin/python scripts/verify_alt4_execution.py --output verification/results/alt4-execution-report.json
