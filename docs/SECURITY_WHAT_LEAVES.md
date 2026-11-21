# What leaves the machine

**Measured 2026-11-16 on CLI 2.1.316** with `scripts/audit_egress.py`. The report is in
`experiments/egress/audit_2026-11-16.json` and carries shapes and equalities, never values. It was
run twice, with the same result.

**What the audit runs.** CI's own adapter, identity isolation on, in three scenarios: a plain
call, an agent step, and a plain call with the CLI's switches for non-essential traffic. Three
observers watch every call:
- a loopback relay at `ANTHROPIC_BASE_URL`, which forwards to Anthropic;
- a logging CONNECT proxy for every other HTTPS destination;
- socket sampling of the CLI process tree.

Sections 1 to 4 cover only the `claude` CLI path, as measured. Sections 5 to 8 cover every other
path that sends data off the machine in 0.3.0a1, including ALTER's: the Anthropic API key adapter,
OpenAI-compatible and Gemini adapters, local models, `gh`, the vault and the ALTER brief. They come
from reading the code on 2026-11-21 (`docs/release/SAFETY_AUDIT_0.3.0a1.md`), not from a network
measurement.

## 1. The model request (`POST /v1/messages`, to api.anthropic.com)

### The body

| part | what it is | identifiers in it |
|---|---|---|
| `system` | CI's system prompt, plus the CLI's short preamble | none |
| `messages` | the request, CI's protocol and context, the conversation so far | none of the 12+ labels: email, name tokens, username, home path, hostname, account and organization ids |
| `metadata.user_id` | a JSON string: `device_id` (64 hex), `account_uuid`, `session_id` | **the account id**, see below |
| other keys | `model`, `max_tokens`, `stream`, `thinking`, `context_management`, `output_config`, `tools` (empty: CI sends its tools as data, never as API tools) | none |

**What the metadata ids are:**
- **`device_id` is fresh on every call.** It differs between calls, and it is not the CLI's own
  stored device id. Isolation gives each call an empty, temporary config, so the CLI makes a new
  id each time. No stable device id is sent.
- **`account_uuid` is the account's real id.** The request is authenticated with the account's
  OAuth bearer token, so Anthropic already knows the account. The id adds nothing Anthropic
  could not already link. The model does not see it: metadata is not model input.
- **`session_id` is fresh per CLI process**, so a call's requests can be grouped together. It is
  also sent as the `x-claude-code-session-id` header.

### The headers

| header | carries |
|---|---|
| `authorization` | `Bearer <OAuth token>`, the account's login |
| `user-agent` | the CLI version and SDK version (`claude-cli/2.1.316 (external, sdk-cli, agent-sdk/…)`) |
| `x-stainless-os`, `-arch`, `-runtime`, `-runtime-version` | the device's OS family, CPU architecture and Node version |
| `anthropic-beta`, `anthropic-version`, `x-app` | the API features in use |
| `x-claude-code-session-id` | the per-process session id |

No identifier appears in any header.

## 2. Other destinations

| destination | when | what is known |
|---|---|---|
| `api.anthropic.com:443`, outside the API base URL | every call, also with the quiet switches | The path is hidden by TLS. It is the CLI's own account traffic, not the model request. An earlier measurement (`docs/SECURITY_CLI_IDENTITY.md`) saw the CLI refetch the account profile during calls. It goes to the same vendor the person consented to. |
| **`http-intake.logs.us5.datadoghq.com:443`** | **every call, by default** | The CLI's own operational telemetry, sent to Datadog. The content is not observed (TLS). |
| (anything else) | | none seen. Socket sampling found no connection that bypassed the proxy. |

**With the quiet switches,** the Datadog connection does not happen and the call still succeeds.
The switches are:
- `DISABLE_TELEMETRY=1`
- `DISABLE_ERROR_REPORTING=1`
- `DISABLE_AUTOUPDATER=1`
- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`

**Decision for rc2:** the adapter sets these four for every call (§4), so that by default nothing
goes to a third party. A value already set in the person's own environment is kept (`QUIET_ENV` is
applied with `setdefault`), so `DISABLE_TELEMETRY=0` in the shell wins.

## 3. What does not leave

These were never seen in any request, header or destination:
- the account email, its local part and domain;
- the account and organization names;
- `$USER`, the home path and the hostname;
- git's `user.name` and `user.email`;
- the person's files, except what CI puts into `messages`.

## 4. Remaining exposure, and what would change it

1. **The account id in metadata.** It is redundant with authentication, and only the CLI can stop
   sending it. Dropping `accountUuid` from the sanitized profile risks the login, which needs it
   (`PROFILE_REQUIRED`). This is accepted and documented.
2. **Device characteristics** (OS family, CPU architecture, Node version) in the headers. They are
   generic and also accepted.
3. **Unobserved content.** The audit cannot see inside TLS, so the content of the CLI's own
   `api.anthropic.com` calls and of the Datadog telemetry is unobserved. The quiet switches
   remove the telemetry. The account calls go to Anthropic, the vendor already consented to.
4. **Other versions and configurations.** This was measured on one CLI version and one machine. A
   new CLI version can change any of it. The identity probe (`python -m pcim.adapters.cli_probe
   --record`) gates on the model input. Re-run the egress audit too after a CLI update.
5. **Older records.** Interactions stored before isolation existed may still contain identifiers
   the CLI injected then. They are not scrubbed (READINESS §4).

## 5. Every path off the machine in 0.3.0a1 (from the code, 2026-11-21)

Nothing below reaches a model unless that vendor was granted. Two grants apply: the person's egress
grants from `pcim init`, and, inside a project, the project's provider list (`alter permissions
provider … on`). `Workspace.require_provider` checks the project's list when a message is sent, and
the agent checks it again before every model step.

| path | destination | what is sent | opt-in | audited as |
|---|---|---|---|---|
| `claude` CLI adapter (`claude-cli`) | `api.anthropic.com` (via the CLI) | §1 to §4 | provider granted | §1 to §4, plus the rows below |
| Anthropic API adapter (`anthropic`) | `https://api.anthropic.com`, pinned. `ANTHROPIC_BASE_URL` is ignored; `PCIM_ANTHROPIC_BASE_URL` is the explicit override | `model`, `system`, `messages`, `max_tokens`, `output_config`; `tools` for agent steps. Headers: the API key or auth token, plus the SDK's `user-agent` and `x-stainless-*` (OS, CPU architecture, Python and SDK versions). CI sets no `metadata.user_id` | `ANTHROPIC_API_KEY` set, provider granted | `route.plan`, `model.call`, `session.disclosure` |
| OpenAI-compatible adapter (`openai`) | `https://api.openai.com/v1`, or `PCIM_OPENAI_BASE_URL` | `model`, `messages`, the token limit; `tools` for agent steps. `Authorization: Bearer` with the key; httpx's `user-agent`. No `user` field | `OPENAI_API_KEY` set, provider granted | as above |
| Gemini adapter (`google`) | `https://generativelanguage.googleapis.com/v1beta` | `systemInstruction`, `contents`, `generationConfig`; `x-goog-api-key`. `ask` only: it has no agent step, so it never runs a goal | `GEMINI_API_KEY` or `GOOGLE_API_KEY`, provider granted | `route.plan`, `model.call` |
| local adapter (`local`) | loopback only. Any other host is refused, and proxy variables are ignored | as OpenAI-compatible | `PCIM_LOCAL_MODEL` and `PCIM_LOCAL_BASE_URL` set | `model.call` |
| `gh` (Third Eye, GitHub source) | `api.github.com` (or `GH_HOST`), via the person's own `gh` login | one read-only `gh pr list --state all --limit 8 --json number,title,state,mergedAt,updatedAt,headRefName` for the repository the project's git remote names | `alter permissions github on` (off by default) | `alter.source` when switched; `alter.context` lists `github` among a goal's sources. The `gh` request itself is not logged |
| vault (Third Eye) | none: the notes are read on this Mac. Reading an iCloud placeholder makes macOS download that note from iCloud (bounded to 20 s, or 120 s with `alter vault download`) | nothing by itself. Matching sections go to the model inside the brief (§6) | `alter permissions vault on` (off by default) | `alter.source`; `alter.context` with `vault_notes`, the count of vault pieces |
| ALTER brief (`alter`, `alter goals`, `alter ask`) | the goal's provider, or for `alter ask` the routed model | §6 | a goal starts only with a non-test provider granted to the project | `alter.context` (sources, piece count, vault notes); `alter.ask`; the goal's sealed record keeps the brief text and its citations |
| GitHub, the repository itself | `github.com` | only what the owner pushes. The repository is **public** (§8) | `git push` | GitHub |

Proxies: the Anthropic, OpenAI and Gemini clients honour `HTTPS_PROXY`/`ALL_PROXY`, as httpx does.
A proxy sees the destination host, not the TLS content. `local` ignores them, so a loopback request
never leaves the machine.

## 6. What the ALTER brief carries

The brief is built locally (`thirdeye.gather`) and never calls a model. It opens the first message of
every goal session (start and each resume), and of every `alter ask`. It is fenced as data, with a
per-brief nonce:

- `Project: <name> (workspace <folder name>)`. The folder's name only, never its path.
- **Remembered** (ALTER's own records). Up to 8 earlier goals in the project (status, verdict,
  objective ≤200 characters). Up to 5 each of the project's decisions, open questions and next
  steps (≤300 characters), filtered for the vendor that will see them, as the agent's own briefing
  is. Without a vendor (`alter ask`), notes derived from memory are left out.
- **Sourced** (read just now):
  - git: the branch and up to 8 changed file names;
  - up to 20 commit subjects (60 for `alter ask`), with short hash and date. No author names or
    emails: the format is `%h %ad %s`;
  - the first 1,200 characters of `README.md`, `README.rst` or `README`, unless it is a symlink or
    resolves outside the workspace;
  - GitHub (when on): up to 8 PRs, with number, state, title (≤160 characters), head branch and date;
  - vault (when on): up to 6 matching sections (≤1,200 characters each), with `note#heading` and
    the note's `updated`/`date`.
- **Not included**: every source that was off, missing or failed, with the reason.

**Credentials are redacted** (since 0.3.0a1, safety audit S5). Each piece passes through
`sovereignty.credentials.redact` when the brief is built. A credential-shaped string becomes
`[REDACTED:<kind>]`, and the redaction is listed under "Not included". `alter context` shows
exactly what the model will receive. Like the tripwire in `ci ask`, this matches the published
shapes of keys: a secret with no recognisable shape passes.

After the brief, the goal's agent session sends what any `ci do` session sends: the project
briefing, the memory selected for that vendor, the person's words, and tool results (file
contents, search matches, `git status`, the tail of test output). Files whose names look like
secrets (`.env`, keys, `.ssh/`, …) are never read. The absolute workspace path and write backups are
kept from the model (`MODEL_HIDDEN`).

## 7. What stays on the machine

- The store (`pcim.db`), its key (`master.key`) and `token` are in `$PCIM_HOME`.
- Goal worker logs (`$PCIM_HOME/goals/<goal>.log`, 0600, in a 0700 folder) hold ids and statuses.
  They also hold the worker's stderr, so a crash leaves a Python traceback there.
- The vault is only read. ALTER writes nothing to it.

## 8. The repository is public

The GitHub repository (`origin`) has been public since it was created (2026-10-17). Every pushed
commit carries the author's name and email, and three files on `main` carry this machine's home path.
`docs/release/SAFETY_AUDIT_0.3.0a1.md` §1 lists what is there and what removing it would take.
