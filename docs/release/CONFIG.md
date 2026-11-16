# Configuration Reference (`0.2.0rc1`)

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
