#!/usr/bin/env bash
# Run the CI checks locally: ruff (when installed), then pytest, with the project venv.
# Mirrors .github/workflows/tests.yml; keep the ruff and pytest flags in step with it.
#
#   scripts/check.sh                 # from any directory
#   scripts/check.sh -k routing -x   # extra arguments go to pytest
#   PYTHON=/path/to/python scripts/check.sh
#
# The interpreter is $PYTHON, else this checkout's .venv, else the main checkout's .venv
# (a git worktree has none of its own). Tests import this checkout's src/, whatever the
# venv has installed, and run against a throwaway PCIM_HOME, never ~/.pcim.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo"

find_python() {
    if [[ -n "${PYTHON:-}" ]]; then
        command -v "$PYTHON" || { echo "check.sh: PYTHON=$PYTHON is not executable" >&2; return 1; }
        return 0
    fi
    if [[ -x "$repo/.venv/bin/python" ]]; then
        echo "$repo/.venv/bin/python"
        return 0
    fi
    local common
    if common="$(git rev-parse --git-common-dir 2>/dev/null)" && common="$(cd "$common" && pwd -P)" \
            && [[ -x "$(dirname "$common")/.venv/bin/python" ]]; then
        echo "$(dirname "$common")/.venv/bin/python"
        return 0
    fi
    echo "check.sh: no project venv found (looked in $repo/.venv and the main checkout's .venv); set PYTHON" >&2
    return 1
}

py="$(find_python)"
echo "python: $py ($("$py" -c 'import sys; print(sys.version.split()[0])'))"

# Same environment as CI: the offline lexical embedder, no model downloads, and a fresh
# PCIM_HOME so a test that forgets to set its own never touches the person's store.
export PCIM_EMBEDDER=hashing-512
export HF_HUB_OFFLINE=1
export PYTHONPATH="$repo/src"
PCIM_HOME="$(mktemp -d "${TMPDIR:-/tmp}/pcim-check.XXXXXX")"
export PCIM_HOME
trap 'rm -rf "$PCIM_HOME"' EXIT

status=0

# ruff: the venv's, else one on PATH. The rule set is pinned here, as in CI, so a ruff
# config elsewhere on the machine cannot change the result.
ruff_cmd=()
if "$py" -m ruff --version >/dev/null 2>&1; then
    ruff_cmd=("$py" -m ruff)
elif command -v ruff >/dev/null 2>&1; then
    ruff_cmd=(ruff)
fi
if [[ ${#ruff_cmd[@]} -gt 0 ]]; then
    echo "== ruff ($("${ruff_cmd[@]}" --version))"
    "${ruff_cmd[@]}" check --isolated --no-cache --select E9,F63,F7,F82 --target-version py311 \
        src tests scripts || status=$?
else
    echo "== ruff: not installed, skipped (CI runs ruff==0.17.4; '$py -m pip install ruff' to run it here)"
fi

echo "== pytest"
"$py" -m pytest -q -p no:cacheprovider -ra "$@" || status=$?

if [[ $status -eq 0 ]]; then
    echo "check.sh: all checks passed"
else
    echo "check.sh: FAILED (exit $status)" >&2
fi
exit "$status"
