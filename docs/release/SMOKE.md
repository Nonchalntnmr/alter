# Smoke Procedure (`0.2.0rc2`)

## 1. Version and read-only diagnostics

```sh
export HF_HUB_OFFLINE=1
PYTHONPATH=src $PY -m pcim.terminal --version
PYTHONPATH=src $PY -m pcim.terminal doctor
```

## 2. End-to-end terminal suite (offline, 37 checks)

Requires the `[test]` extra (`pytest` is not a runtime dependency):

```sh
PYTHONPATH=src $PY scripts/e2e_terminal.py
```

Expected: `37 of 37 checks passed`. That includes `ci do`: a direct route that waits for
approval before writing, a clarify route that makes no model call, and a plan route that
changes nothing.

**Against an installed wheel instead of this checkout.** Without this flag the script tests the
checkout's `src/`, even when run with the venv's Python:

```sh
E2E_INSTALLED=1 /tmp/alter-venv/bin/python scripts/e2e_terminal.py
```

## 3. Full unit and integration suite

```sh
PYTHONPATH=src $PY -m pytest -q -p no:cacheprovider
```

## 4. Live CLI identity probe (optional, <$0.01 Haiku)

```sh
PYTHONPATH=src $PY -m pcim.adapters.cli_probe --trials 2 --baseline
```

Expected: `clean: true` in isolated trials and `account email` present in `baseline[0].found_in_model_input`.

## 5. Upgrade and rollback (local, no model calls)

1. Create a `PCIM_HOME` with `main`'s code: `pcim init`, `pcim remember "…"`, `ci status`, and one
   `ci "…"` request. That one is a text answer: `ci "…"` never acts in a folder; `ci do` does.
2. Run this release on it: `ci doctor` (exit 0), `ci status`, `ci audit`, `ci twin`, `ci plan "…"`
   and `ci do list`.
3. Go back to `main` on the same data: `ci status`, `pcim memory`, and `pcim audit`. The audit
   must report `"chain_ok": true`.

`main` has no `ci audit` or `ci twin`. There, those words are answered as ordinary requests, so
they are not rollback checks.
