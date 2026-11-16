# Smoke Procedure (`0.2.0rc1`)

## 1. Version and read-only diagnostics

```sh
export HF_HUB_OFFLINE=1
PYTHONPATH=src $PY -m pcim.terminal --version
PYTHONPATH=src $PY -m pcim.terminal doctor
```

## 2. End-to-end terminal suite (offline, 32 checks)

Requires the `[test]` extra (`pytest` is not a runtime dependency):

```sh
PYTHONPATH=src $PY scripts/e2e_terminal.py
```

Expected: `32 of 32 checks passed`.

## 3. Full unit and integration suite

```sh
PYTHONPATH=src $PY -m pytest -q -p no:cacheprovider
```

## 4. Live CLI identity probe (optional, <$0.01 Haiku)

```sh
PYTHONPATH=src $PY -m pcim.adapters.cli_probe --trials 2 --baseline
```

Expected: `clean: true` in isolated trials and `account email` present in `baseline[0].found_in_model_input`.
