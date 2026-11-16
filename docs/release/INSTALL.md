# Install Guide (`0.2.0rc1`)

## Prerequisites

- Python `>= 3.11`
- Core dependencies: `numpy>=1.26`, `pydantic>=2.5`, `cryptography>=42`, `httpx>=0.27`
- Optional extras: `api`, `anthropic`, `neural`, `semantic`, `mcp`, `test`, `all`

## Build wheel locally

```sh
cd ~/projects/alter-rc
$PY -m pip wheel . --no-deps -w dist/
```

## Install into a clean virtualenv

Installing dependencies into an empty virtualenv downloads packages from PyPI:

```sh
python3 -m venv /tmp/alter-venv
/tmp/alter-venv/bin/pip install dist/contextual_intelligence-0.2.0rc1-py3-none-any.whl
```

To provision `pytest` for running `scripts/e2e_terminal.py` or the test suite without adding `pytest` to runtime dependencies, install the `[test]` extra:

```sh
/tmp/alter-venv/bin/pip install "dist/contextual_intelligence-0.2.0rc1-py3-none-any.whl[test]"
```

## Initialize and verify

```sh
export PCIM_HOME=~/.pcim
pcim init --user "<name>" --providers claude-cli
ci --version
ci doctor
```

If using the `claude` CLI with identity isolation enforced (`PCIM_CLI_REQUIRE_ISOLATION=1`), probe and record the installed CLI version first:

```sh
PYTHONPATH=src $PY -m pcim.adapters.cli_probe --trials 4 --record
```
