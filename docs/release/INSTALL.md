# Install Guide (`0.3.0a1`)

`scripts/install_alter.sh` installs ALTER for one person, and upgrades, rolls back and uninstalls
it. `alter setup` does the first-run onboarding. Both are safe to run again.

## Prerequisites

- **macOS or Linux, with `bash` and `git`.**
- **Python >= 3.11.** macOS's own `python3` is older: `brew install python@3.13`, or the installer
  from python.org.
- **The Claude Code CLI (`claude`), logged in.** ALTER's goals run through it. Install it
  (`npm install -g @anthropic-ai/claude-code`), run `claude` once and log in. `alter setup` checks
  both and says what is missing.
- **Optional:** `gh`, if ALTER may read the project's pull requests (`alter config set github on`).
- **Network:** the first install downloads the dependencies from PyPI. The semantic extra (on by
  default) adds `sentence-transformers` and `torch`, about 1 GB, and its model is fetched from
  Hugging Face on first use.

## Install, on a clean Mac

```sh
git clone <the ALTER repository URL> ~/projects/alter
cd ~/projects/alter
scripts/install_alter.sh                 # builds a wheel from this checkout and installs it
alter setup                              # identity, claude check, outcome memory, next steps
cd <your project> && alter init --tests "<its test command>"
alter doctor
```

Given a wheel instead of a checkout, the script needs the checkout's `requirements-lock.txt` (or
`--constraints FILE`):

```sh
scripts/install_alter.sh path/to/contextual_intelligence-0.3.0a1-py3-none-any.whl
```

**What the script does.**
1. **Python:** `--python PY`, else the newest of `python3.14` … `python3.11`, `python3` on PATH that
   is >= 3.11.
2. **Venv:** creates `${ALTER_HOME:-~/.alter}/venv`, or keeps the one there. A venv whose Python was
   removed (a Homebrew upgrade) is recreated; so is one whose Python differs from `--python`.
3. **Package:** installs the wheel with every dependency pinned by `requirements-lock.txt`, and with
   the `semantic` extra unless `--no-semantic`. It keeps a copy of the wheel in
   `~/.alter/wheels/`, for rollback.
4. **Commands:** links `alter`, `ci` and `pcim` into `${ALTER_BIN:-~/.local/bin}`.
   - A file already there that is not this install's link is moved to `NAME.pre-alter` first.
   - It says so if that folder is not on your PATH, and prints the line to add to `~/.zshrc`.
5. **Check:** runs `alter doctor`. On a new computer its only failure is `pcim_home` (no identity
   yet), until `alter setup`.

It never changes your data in `PCIM_HOME` (default `~/.pcim`). Options: `--no-semantic`,
`--python PY`, `--constraints FILE`, `--no-doctor`, `--force`; `scripts/install_alter.sh --help`.

**Without the semantic extra.** `--no-semantic` installs the core only, and a new store uses the
offline lexical embedder. A store created with the semantic extra installed keeps needing it:
`alter doctor` fails its `embedder` check without it. A store's embedder never changes on its own.

## Onboard: `alter setup`

```sh
alter setup                                  # identity "me", claude-cli as the provider
alter setup --init --tests "python -m pytest -q"   # and make the current folder a project
```

It asks nothing. Flags: `--user ID` (default `me`), `--providers P …` (default `claude-cli`),
`--no-memory`, `--init` with `--tests "CMD"` and `--no-write`.

- **Identity.** Created in `PCIM_HOME` if there is none, like `pcim init`. The id is a label: setup
  never takes it from your account or your name. The token is written with mode 0600 and never
  printed.
- **An existing identity is never changed.** Not its id, token or providers. `--user` is then
  ignored. A store whose token file is missing or wrong is left alone, with the reason.
- **Claude CLI.** Checks that `claude` (or `$PCIM_CLAUDE_BIN`) exists and is logged in. If not, it
  says how to fix it. No model is called.
- **Outcome memory.** Granted, unless `--no-memory`, or unless you turned it off before (setup
  leaves your choice alone).
- **Next steps** are printed at the end.

**Optional, the identity probe.** Identity isolation counts as verified only for a `claude` version
recorded clean in this `PCIM_HOME`. Record it once, and again after every `claude` update. Each
trial is one Haiku call, well under a cent.

```sh
~/.alter/venv/bin/python -m pcim.adapters.cli_probe --trials 4 --record
alter doctor        # claude_cli should now read isolation=verified
```

Set `PCIM_CLI_REQUIRE_ISOLATION=1` for any work that must not carry your identity. Calls are then
refused unless the probe has verified the installed CLI.

## Configure: `alter config`

```sh
alter config                       # list: vault, vault.path, github, memory, autonomy.mode
alter config set vault.path ~/Documents/MyVault
alter config set vault on
alter config get memory
```

`CONFIG.md` lists every key. `alter permissions` still works, and reads and writes the same
settings, plus the per-project ones (providers, vault topics, autonomy grants).

## Upgrade

```sh
cd ~/projects/alter && git pull          # or download the newer wheel
scripts/install_alter.sh                 # or: scripts/install_alter.sh path/to/the-newer.whl
alter doctor
```

- **The script prints what it does:** `upgrade: 0.3.0a1 -> 0.3.0a2`, `reinstall` for a rebuild of
  the same version, or `downgrade`.
- **It keeps the venv and the links.** It updates the dependencies to the lock file, and keeps a
  copy of the new wheel next to the old ones in `~/.alter/wheels/`.
- **A rebuild at the same version keeps both wheels.** The first wheel keeps its original path.
  The installer prints the path of the new wheel under `~/.alter/wheels/rebuilds/`. Either path
  works with `scripts/install_alter.sh --rollback <wheel>`.
- **It refuses while a goal runs** from this install: replacing the package under a running goal
  would break it. Pause the goal (`alter goals pause <goal>`) or let it finish. `--force` goes
  ahead anyway.
- **Your data is not migrated; it does not need to be.**
  - A newer version adds what it needs on first use, as 0.3.0a1 adds the `goals` and
    `goal_events` tables.
  - Older versions ignore them, so a rollback needs no data step (`ROLLBACK.md`).
- **Install a newer Python too:** `scripts/install_alter.sh --python python3.14` recreates the venv
  with it.

## Uninstall

```sh
scripts/install_alter.sh --uninstall
```

It removes this install's links and `~/.alter/venv`, and puts back anything it had moved to
`NAME.pre-alter`. It keeps `~/.alter/wheels/` and your data in `~/.pcim`; delete them by hand to
remove them too.

## By hand

What the script does, for a machine where you would rather type it:

```sh
python3.13 -m pip wheel --no-deps -w dist .
python3.13 -m venv ~/.alter/venv
~/.alter/venv/bin/pip install -c requirements-lock.txt "dist/contextual_intelligence-0.3.0a1-py3-none-any.whl[semantic]"
mkdir -p ~/.local/bin
for c in alter ci pcim; do ln -s ~/.alter/venv/bin/$c ~/.local/bin/$c; done   # fails if one is there: move it first
alter setup
alter doctor
```

To run the test suite from an installed wheel, install the `[test]` extra as well.
