# Independent review of c9edc64 (`sec/cli-identity`), 2026-11-13

The reviewer was a separate agent. It read the diff and ran the offline tests and fake-runner
experiments, with no network and no model calls. It found **4 medium defects, 4 low, and none
high**. None is fixed yet. **Fix them before merging this branch, and before any evaluation
relies on `PCIM_CLI_REQUIRE_ISOLATION=1`.**

## Confirmed defects

**M1. `verified` with an incomplete profile, so the email still leaks.** (`claude_cli.py`, `sanitized_profile`)
- *The defect.* Any non-empty `oauthAccount` counts as sanitizable.
- *Scenario.* `{"emailAddress": "a@b.c"}` plus a clean record gives `verified` under
  `require_isolation`. The CLI then receives a blank-email-only profile. That is the
  configuration the report measured as refetched, so the email leaks.
- *The probe is blind in the same state.* Its markers come from the same file.
- *Fix.*
  - Require the fields the clean run had (at least `accountUuid`, `organizationUuid`,
    `billingType`, `accountCreatedAt`, `subscriptionCreatedAt` and `ccOnboardingFlags`).
  - Store that key set in the probe record, and require it to match.

**M2. The profile is read twice, and the second read fails open.** (the `identity_isolation` pre-check, then `_isolated_env`)
- *Scenario.* A concurrent rewrite of `~/.claude.json` makes the second read return `None`. The
  config is then written with no `oauthAccount`, the CLI fetches the profile, and the email
  leaks. The receipt is recomputed after the call, so it can still say `verified`.
- *Fix.*
  - Read the profile once, and use it for both the decision and the file written.
  - Raise a `config` error in `_isolated_env` when isolation is on and there is no profile.
  - Build the receipt from what was actually written.

**M3. The CLI version is cached for the whole process, by binary name only.** (`cli_version`)
- *Scenario.* An auto-update swaps the symlink from 2.1.316 to 2.1.317. The unprobed 2.1.317 then
  runs as `verified` and passes `require_isolation`.
- *Fix.* Resolve the real path, key the cache on (realpath, inode, mtime), and run that resolved
  path in argv.

**M4 (rated low by the reviewer, treat as medium).** The per-call config directory, which holds
the account and organization ids, leaks if anything fails between `_isolated_env` and the `try`.
For example, `mkstemp` fails on a read-only workdir.
- *Fix.* Create the directory inside the `try`, and clean it up in `finally`.

## Low

- **L1. The probe misses non-ASCII markers.** `scan` uses `json.dumps` with `ensure_ascii=True`,
  so a name like "José Núñez" is never found. Use `ensure_ascii=False`.
  - Make `--record` require a positive control: the baseline must show the email.
  - Add the hostname and the full name from the account database as markers.
- **L2. A malformed probe-record entry crashes the call.** A value like `{"9.9.9": "clean"}`
  raises `AttributeError` after the CLI has already run. It isn't a `ProviderError`, so routing
  doesn't fall back. Check `isinstance(rec, dict)`.
- **L3. `self.last_isolation` is shared adapter state.** Thread-pooled e1/e2 calls can attach
  another call's context, and an `agent_step` retry loses the first call's refetch flag. Have
  `_call` return the isolation dict.
- **L4. Some identity still reaches the child's environment.**
  - `HOME`, `USER`, `LOGNAME` and `PWD` are passed through. They are measured safe only on
    2.1.316, and the first test's name ("no identity in its environment") overstates what it
    checks.
  - `CLAUDE_SECURESTORAGE_CONFIG_DIR=""` relies on the CLI treating an empty value as unset. Set
    it only when the parent sets it.

## Speculative (worth designing for)

- **"Verified" rests on an observed ordering, once per version.** It depends on the per-call
  profile refetch finishing after the request is built. If the CLI retries internally (429/529)
  and rebuilds the user context, the retry could carry the email.
  - Stronger: under `require_isolation`, route production calls through the loopback relay as an
    **egress filter** that blocks any `/v1/messages` body containing a marker. That enforces
    isolation per request, not per version.
  - Weaker but cheap: make the per-call `.claude.json` read-only (0400 in a 0500 directory).
- **The workdir is per adapter, re-created with `mkdir(exist_ok=True)`, and never removed.** On a
  shared `/tmp`, another local user could pre-create the path after a temp cleaner deletes it.
  Use a fresh `mkdtemp` per call.
- **The probe measures with `ANTHROPIC_BASE_URL` set, which production never does.** The baseline
  showing the email lowers this risk, but doesn't remove it.

## Smaller points

- `PCIM_CLI_REQUIRE_ISOLATION` must be exactly "1", and it is read when the adapter is built.
- e1 and e2 never opt in.
- No test covers the fallback to a *different* consented provider.

## Checked, no issue

- **Relay:** loopback only, on port 0. It forwards only to `api.anthropic.com` over verified
  HTTPS, and drops the Host header. Auth headers are forwarded and never stored or printed.
  `record()` writes only labels, booleans, timestamps and counts, to a 0600 file.
- **Per-call config:** created with `mkdtemp` (0700), and the file with `O_EXCL` (0600).
- **Refusal path:** its messages carry no identity. Routing marks the provider dead and skips its
  other models.
