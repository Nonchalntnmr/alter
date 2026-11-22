# Configuration Reference (`0.3.0a1`)

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PCIM_HOME` | `~/.pcim` | Directory holding `pcim.db`, `token`, `cli_identity_probe.json`, and lock files |
| `ALTER_HOME` | `~/.alter` | `scripts/install_alter.sh`: holds `venv/` and `wheels/` (every wheel installed, kept for rollback) |
| `ALTER_BIN` | `~/.local/bin` | `scripts/install_alter.sh`: where it links `alter`, `ci` and `pcim` |
| `PYTHON` | this checkout's `.venv`, else the main checkout's | `scripts/check.sh` only: the interpreter for the checks |
| `PCIM_CLAUDE_BIN` | `claude` | Path or binary name for the Claude Code CLI |
| `PCIM_CLI_REQUIRE_ISOLATION` | unset (`!= "1"`) | Set to `"1"` to refuse `claude-cli` calls unless the installed CLI version is probed clean |
| `PCIM_EMBEDDER` | default embedder | Override embedder name (`hashing-<dim>` or `st:<model>`) |
| `HF_HUB_OFFLINE` | auto when cached | Set to `"1"` to prevent Hugging Face Hub network checks. `SentenceEmbedder._load` sets it if the model is in the **default** cache (`~/.cache/huggingface/hub`); with `HF_HOME` or `HF_HUB_CACHE` elsewhere, set it yourself. |
| `PCIM_OPENAI_MODEL` | unset | Default model name for `OpenAICompatAdapter` when not passed explicitly |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Parent Claude CLI config directory (isolated per call when `isolate_identity=True`) |
| `CLAUDE_SECURESTORAGE_CONFIG_DIR` | set per call | `ClaudeCLIAdapter` sets it for the child: the parent's value, else the parent's `CLAUDE_CONFIG_DIR`, else `""` (the CLI's default login). Leaving it unset breaks the login (measured). |
| `E2E_INSTALLED` | unset | `scripts/e2e_terminal.py` only: `1` tests the installed package instead of the checkout |
| `PCIM_LIVE_CLI_PROBE` | unset | tests only: `1` runs the live identity-probe test (a few Haiku calls) |
| `PCIM_DO_ROUTING` | the built-in `router.ROUTING_DEFAULT` | `direct` or `adaptive`: what `ci do` does when no `--route` is given. `direct` still prints the router's advice. |
| `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `DISABLE_AUTOUPDATER`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | set to `1` for every `claude` call | `ClaudeCLIAdapter` sets them unless you set them yourself. Without them, CLI 2.1.316 sends telemetry to Datadog on every call (`docs/SECURITY_WHAT_LEAVES.md`). |
| `FILTER_REPO` | `git-filter-repo` on PATH | `scripts/rewrite_history.py` only: the path to `git-filter-repo` |
| `SCAN_IDENTITY_MARKERS` | unset | tests only: a JSON file of fake identifiers that replaces this machine's in `scan_identity.py` and `rewrite_history.py` (they say so when it is used) |

## Per-person settings: `alter config`

`alter config` (or `alter config list [--json]`), `alter config get KEY`, `alter config set KEY VALUE`.
They are stored with your identity in `PCIM_HOME`, and apply to every project.

| Key | Values | Default | What it does | Also |
|---|---|---|---|---|
| `vault` | `on`, `off` | `off` | Search your Obsidian vault for each goal and question. Matching sections go to the model, and `alter context` shows them. | `alter permissions vault on\|off` |
| `vault.path` | a folder | the vault under `~/Documents` that has `13 Automation/Tools/nos.py`, else the only one there | Which vault. Setting it does not turn the vault on. | `alter permissions vault on --path P` |
| `github` | `on`, `off` | `off` | Read the project's pull requests with `gh` (read-only). They go to the model with each goal. | `alter permissions github on\|off` |
| `memory` | `on`, `off` | `on` after `alter setup` or `alter init`, unless `--no-memory` | Store each goal's outcome in your memory, as an episode (`alter memory`; `alter memory forget <id>`). | `alter permissions memory on\|off` |
| `autonomy.mode` | `copilot`, `collaborative`, `autonomous` | `collaborative` | Reads and notes never ask. A write or test run asks, unless the mode is `autonomous` and an autonomy grant for that project covers it. | `alter permissions autonomy grant\|revoke` (sets it too) |

`on`/`off` also accept `true`/`false`, `yes`/`no` and `1`/`0`. A refused value changes nothing and
exits 2.

Per-project settings stay under `alter permissions`. These are the model providers, vault topics,
autonomy grants, and the workspace (`alter init --tests`, `--no-write`).
