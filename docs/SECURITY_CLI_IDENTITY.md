# Security issue: the `claude` CLI adapter exposes the account's identity to the model

| | |
|---|---|
| **Status** | Mitigated on branch `sec/cli-identity` for CLI 2.1.316. Measured on the request itself, and enforced for identity-sensitive calls. Open for any other CLI version until it is probed. |
| **Affects** | `src/pcim/adapters/claude_cli.py` on `main` (1f9bd08) and every branch built on it |
| **Found** | 2026-11-13, in the held-out intent evaluation |
| **Kind** | Privacy: data reaches the model that CI's consent filter never approved and its receipts never listed |

## 1. What happened

Every call CI made through the logged-in `claude` CLI carried the following into the model's input:
- the **email address of the logged-in account**;
- a **working directory under `$HOME`**, which contains the macOS username.

This happened despite every isolation flag the adapter sets: tools off, no MCP, no settings files
or hooks (`--setting-sources ""`), a replaced system prompt, no session persistence, and an empty
working directory.

**Evidence from model behaviour.**
- In the held-out run, identifying details appeared in **40 of 100** replies. None of the 50
  requests mentioned the person, and each ran in a fresh CI store holding no memory.
- The model wrote the email into plan assumptions, built a full name from the username, and
  named the person's institution in its questions.

**Evidence from the request itself.** This is the measurement that matters.
- `python -m pcim.adapters.cli_probe` points the CLI, for the probe's calls only, at a loopback
  relay. The relay records the body of every `/v1/messages` request and forwards it unchanged to
  Anthropic.
- On `main`'s adapter, the recorded model input held the account email, its local part, the
  username, the home path, and git's `user.email`.

**Root cause.** Read from the CLI 2.1.316 bundle, not from documentation.
- **The email.** The CLI builds a user-context block, "The user's email address is …", from the
  stored account profile (`oauthAccount.emailAddress` in its config file).
  - The only switch that turns it off is `ANTHROPIC_UNIX_SOCKET`, which changes the transport.
  - No flag turns it off.
  - When the stored profile is missing or stale, the CLI fetches it again.
  - `--bare` skips memory and CLAUDE.md, but it requires API-key authentication, so it cannot be
    used with the OAuth login CI relies on.
- **The username.** The CLI tells the model its working directory. That was
  `~/.pcim/cli-sandbox`.

## 2. The mitigation (branch `sec/cli-identity`)

`ClaudeCLIAdapter` isolates identity by default (`isolate_identity=True`):

1. **Working directory.** A fresh 0700 directory under the system temp directory, checked not to
   contain the home path or the user name.
2. **A per-call config directory.**
   - Each call gets its own `CLAUDE_CONFIG_DIR` (0700). It holds only a *sanitized* account
     profile: the non-identifying fields the CLI needs to treat the login as set up, with
     `emailAddress` blank, no name fields, and `profileFetchedAt` set to now.
   - `CLAUDE_SECURESTORAGE_CONFIG_DIR` keeps the login itself (keychain or `~/.claude`) where it
     was.
   - The directory is deleted after the call.
3. **Hidden git identity.** `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`.
4. **Identity variables dropped** from the child environment: `CLAUDE_CODE_USER_EMAIL`, account
   and organization ids, `GIT_AUTHOR_*`, `GIT_COMMITTER_*`, `EMAIL`.

**Verification and enforcement.**
- The mitigation relies on internal CLI behaviour, so it counts as **verified only for a CLI
  version whose recorded request the probe found clean**:
  `python -m pcim.adapters.cli_probe --record` writes `$PCIM_HOME/cli_identity_probe.json`.
- `identity_isolation()` reports `verified`, `exposed`, `unverified` or `off`, with the reason.
  Every receipt carries it as `provider_context`.
- **Identity-sensitive calls are refused unless the status is `verified`.** This applies with
  `require_isolation=True`, or `PCIM_CLI_REQUIRE_ISOLATION=1` for a whole process.
  - The refusal is a provider-wide `config` error. Routing therefore never tries the CLI's other
    models.
  - Routing falls back to the person's other consented providers, such as the API adapter or a
    local model. If there are none, it fails with a `RoutingError`.
  - The test `test_routing_never_sends_an_identity_sensitive_request_through_an_unverified_cli`
    checks that no CLI model is called.

## 3. Measurements, 2026-11-13, CLI 2.1.316

| configuration | trials | requests recorded | identifiers in the model's input |
|---|---|---|---|
| `main`'s adapter | 1 | 1 | account email, email local part, username, home path, git `user.email` |
| candidate: neutral working directory + an empty `CLAUDE_CONFIG_DIR` | 2 | 2 | account email, email local part, git `user.email` (the CLI fetched the profile) |
| candidate: + a profile holding only a blank email | 2 | 2 | the same (the CLI refetched the incomplete profile) |
| candidate: + the sanitized profile, marked fresh | 2 | 2 | none |
| this branch, isolated (`--trials 6 --baseline`) | 6 (3 plain, 3 agent path) | 9 | **none** |
| this branch, isolated (`--trials 4 --record`) | 4 | ≥ 4 | **none** (recorded clean) |
| this branch, isolated (live test, opt-in) | 2 | ≥ 2 | **none** |
| this branch, isolation off (baseline) | 2 | 2 | account email, email local part, git `user.email` |

Markers checked: the account email and its local part, the display, full and organization names,
the account and organization ids, `$USER`, the home path, and git's `user.name` and `user.email`.

The probe prints and records only whether each identifier is present, never its value.

## 4. What is not fixed, stated plainly

- **A timing race.** In every isolated trial the CLI fetched the full profile, email included, in
  the background *during* the call. The recorded request was clean every time, because the CLI
  builds the request before the fetch finishes. That is an observed ordering, not a guarantee.
  Receipts record `profile_refetched_during_call` so it stays visible.
- **The account id is in the API `metadata`** (`metadata.user_id`) in every configuration. The
  model does not see that field, and it goes to the account's own vendor. It is not removed.
- **Other CLI versions are `unverified`** until probed. In default mode, unverified calls still
  run. The receipt says `unverified`, but nothing is refused. Refusing by default would make CI
  unusable on a fresh install until the probe has run. Evaluations, and any workflow that must
  carry no identity, should set `PCIM_CLI_REQUIRE_ISOLATION=1`.
- **Older records are not scrubbed.** Records that CI stored before this fix still hold identity
  where models wrote it into replies or plans.
- **Committed experiment outputs.** These were redacted by hand. **That redaction keeps the data
  out of the repository; it did nothing about the exposure, which this branch addresses.**
- **The model can still infer identity from what the person sends** (their notes, their files).
  CI's consent filter governs that, not this fix.
- **A path with no CLI context at all.** The Anthropic API adapter (an API key) does not go
  through the CLI.

## 5. Tests and commands

```sh
PY=~/projects/contextual-intelligence/.venv/bin/python; export HF_HUB_OFFLINE=1
PYTHONPATH=src $PY -m pytest -q -p no:cacheprovider tests/test_cli_identity.py      # 8 passed, 1 skipped (live)
PCIM_LIVE_CLI_PROBE=1 PYTHONPATH=src $PY -m pytest -q tests/test_cli_identity.py -k live   # 3 Haiku calls
PYTHONPATH=src $PY -m pcim.adapters.cli_probe --trials 4 --baseline --record        # measure and record this CLI
```
