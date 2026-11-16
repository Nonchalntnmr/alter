# Configuration Reference (`0.2.0rc1`)

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PCIM_HOME` | `~/.pcim` | Directory holding `pcim.db`, `token`, `cli_identity_probe.json`, and lock files |
| `PCIM_CLAUDE_BIN` | `claude` | Path or binary name for the Claude Code CLI |
| `PCIM_CLI_REQUIRE_ISOLATION` | unset (`!= "1"`) | Set to `"1"` to refuse `claude-cli` calls unless the installed CLI version is probed clean |
| `PCIM_EMBEDDER` | default embedder | Override embedder name (`hashing-<dim>` or `st:<model>`) |
| `HF_HUB_OFFLINE` | auto when cached | Set to `"1"` to prevent Hugging Face Hub network checks; `SentenceEmbedder._load` sets this automatically if the default model is already cached |
| `PCIM_OPENAI_MODEL` | unset | Default model name for `OpenAICompatAdapter` when not passed explicitly |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Parent Claude CLI config directory (isolated per call when `isolate_identity=True`) |
| `CLAUDE_SECURESTORAGE_CONFIG_DIR` | inherited | Preserved by `ClaudeCLIAdapter` so login credentials remain accessible |
