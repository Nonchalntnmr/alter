# What leaves the machine when CI uses the `claude` CLI

**Measured 2026-11-16 on CLI 2.1.316** with `scripts/audit_egress.py`. The report is in
`experiments/egress/audit_2026-11-16.json` and carries shapes and equalities, never values. It was
run twice, with the same result.

**What the audit runs.** CI's own adapter, identity isolation on, in three scenarios: a plain
call, an agent step, and a plain call with the CLI's switches for non-essential traffic. Three
observers watch every call:
- a loopback relay at `ANTHROPIC_BASE_URL`, which forwards to Anthropic;
- a logging CONNECT proxy for every other HTTPS destination;
- socket sampling of the CLI process tree.

The rest of this file covers only the `claude` CLI path. Other providers (the Anthropic API key
adapter, Gemini, OpenAI-compatible, local models) are not covered by this measurement.

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
goes to a third party.

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
