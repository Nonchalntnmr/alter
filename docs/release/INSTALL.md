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

**Existing data.** If your `PCIM_HOME` was created where `sentence-transformers` was installed,
its memory is embedded semantically. Install the `semantic` extra as well:

```sh
/tmp/alter-venv/bin/pip install "dist/contextual_intelligence-0.2.0rc1-py3-none-any.whl[semantic]"
```

Without it, `ci doctor` fails its `embedder` check, and requests stop with that instruction.

## Initialize and verify

```sh
export PCIM_HOME=~/.pcim
pcim init --user "<name>" --providers claude-cli
ci --version
ci doctor
```

**The identity probe.** Isolation counts as verified only for a CLI version recorded clean in
**this** `PCIM_HOME`, so record it once per home and again after every `claude` update. Each
trial is one Haiku call, well under a cent.

```sh
python -m pcim.adapters.cli_probe --trials 4 --record   # from the installed package; refuses to record without a positive control
ci doctor                                                # claude_cli should now read isolation=verified
```

Set `PCIM_CLI_REQUIRE_ISOLATION=1` for any work that must not carry your identity. Calls are
then refused unless the probe has verified the installed CLI.
