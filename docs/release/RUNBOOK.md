# Operations & Security Runbook (`0.2.0rc2`)

## Stop-all (`ci halt` / `ci resume`)

- **Emergency stop:** `ci halt` sets the halted flag, blocks all model calls, and cancels pending/running queued actions.
- **Check state:** `ci doctor` reports `[FAIL] stop_all: halted (run ci resume to clear)` and exits `1` while halted.
- **Resume:** `ci resume` clears the halt flag. Interrupted or halted plans are never silently re-run; inspect and retry deliberately with `ci execute <plan> --retry contract` (or `--retry <step>`).

## Re-probing after a `claude` CLI update

`ClaudeCLIAdapter` keys its version cache on `(resolved_path, inode, mtime_ns)`. After any `claude` CLI upgrade:

```sh
~/.alter/venv/bin/python -m pcim.adapters.cli_probe --trials 4 --record
```

Calls with `PCIM_CLI_REQUIRE_ISOLATION=1` will refuse to run until the new CLI version is recorded clean with a positive-control baseline showing the account email.

**Also check the destinations.** The probe checks the model input only. A new CLI version can
add or change other traffic, so from a checkout, also run:

```sh
HF_HUB_OFFLINE=1 PYTHONPATH=src ~/.alter/venv/bin/python scripts/audit_egress.py --out /tmp/egress.json
```

Then compare `analysis` with `docs/SECURITY_WHAT_LEAVES.md`. With the quiet switches,
`datadog_contacted_by_default` must be `false`.

**Don't record while work is running.** The record is replaced atomically from rc2 on, but a
version recorded mid-run changes what later calls in that run accept.

**Where the record lives.** The record is in `$PCIM_HOME/cli_identity_probe.json`, so every
`PCIM_HOME` needs its own probe. `ci doctor` shows `isolation=verified` or `unverified` for the
installed CLI.

## A `ci do` request waiting on you

- **Waiting on an approval.** `ci do` prints `waiting for you: <task> <tool> <path>`. Resolve it
  with `ci approve <task>` or `ci deny <task> "why"`; the session continues and prints the
  rest. `ci halt` cancels it.
- **Waiting on an answer.** A clarify route leaves the request at `needs_answer`. Reply with
  `ci do answer <id> "…"`. `ci do list` shows each request with its route and state.
- **Choosing the route yourself.** `ci do --route direct|plan "…"` overrides the router. With
  `PCIM_DO_ROUTING=direct`, `ci do` always goes direct and only prints the router's advice.

## Recovery from `interrupted` or locked sessions

- **Interrupted plan:** If a process dies mid-execution, the next `ci execute <plan>` marks it `interrupted` (exit `4`) without re-running. Inspect workspace state (`git status`) and run `ci execute <plan> --retry contract` if safe.
- **Concurrent process lock:** Both `plan_exec.plan_lock` and `Agent._lock` use non-blocking `fcntl.flock` (`.plan-<id>.lock` / `.session-<id>.lock` in `PCIM_HOME`). Wait for the active process to finish rather than deleting lock files while a process is alive.
- **Audit verification:** Run `ci audit` or `ci doctor` to verify the hash-chained audit log.

## Before any push: identifiers in history

`PYTHONPATH=src python scripts/scan_identity.py --history` must be clean for every branch you
push. If it is not, follow `HISTORY_REWRITE.md`: dry-run first, then apply only on the owner's
decision.

## A store that cannot load its embedder

- **Symptom.** `ci doctor` shows `[FAIL] embedder: …needs sentence-transformers`, and requests stop
  with "this store's memory is embedded with st:…".
- **Fix.** `pip install 'contextual-intelligence[semantic]'`.
- **Don't switch embedders to get around it.** Forcing a different one with `PCIM_EMBEDDER`
  re-embeds every memory item and changes retrieval. Do it only deliberately.
