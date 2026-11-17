# Configuration Reference (`0.2.0rc2`)

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PCIM_HOME` | `~/.pcim` | Directory holding `pcim.db`, `token`, `cli_identity_probe.json`, and lock files |
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
