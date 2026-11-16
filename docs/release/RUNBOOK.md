# Operations & Security Runbook (`0.2.0rc1`)

## Stop-all (`ci halt` / `ci resume`)

- **Emergency stop:** `ci halt` sets the halted flag, blocks all model calls, and cancels pending/running queued actions.
- **Check state:** `ci doctor` reports `[FAIL] stop_all: halted (run ci resume to clear)` and exits `1` while halted.
- **Resume:** `ci resume` clears the halt flag. Interrupted or halted plans are never silently re-run; inspect and retry deliberately with `ci execute <plan> --retry contract` (or `--retry <step>`).

## Re-probing after a `claude` CLI update

`ClaudeCLIAdapter` keys its version cache on `(resolved_path, inode, mtime_ns)`. After any `claude` CLI upgrade:

```sh
python -m pcim.adapters.cli_probe --trials 4 --record
```

Calls with `PCIM_CLI_REQUIRE_ISOLATION=1` will refuse to run until the new CLI version is recorded clean with a positive-control baseline showing the account email.

## Recovery from `interrupted` or locked sessions

- **Interrupted plan:** If a process dies mid-execution, the next `ci execute <plan>` marks it `interrupted` (exit `4`) without re-running. Inspect workspace state (`git status`) and run `ci execute <plan> --retry contract` if safe.
- **Concurrent process lock:** Both `plan_exec.plan_lock` and `Agent._lock` use non-blocking `fcntl.flock` (`.plan-<id>.lock` / `.session-<id>.lock` in `PCIM_HOME`). Wait for the active process to finish rather than deleting lock files while a process is alive.
- **Audit verification:** Run `ci audit` or `ci doctor` to verify the hash-chained audit log.
