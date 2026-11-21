# Rollback Procedure (`0.3.0a1`)

## Package rollback

Every wheel `scripts/install_alter.sh` installs is kept in `~/.alter/wheels/` (`$ALTER_HOME/wheels`).
To go back to one:

```sh
ls ~/.alter/wheels/
scripts/install_alter.sh --rollback ~/.alter/wheels/contextual_intelligence-<older>-py3-none-any.whl
alter doctor            # ci doctor, for a version without `alter`
```

- **Goals first.** The script refuses while a goal worker from this install runs. Pause it
  (`alter goals pause <goal>`) or let it finish, or pass `--force`.
- **What the script prints.** It names the change (`downgrade: 0.3.0a1 -> 0.2.0rc2`). It warns
  when the wheel is not older than the installed version, and installs it anyway.
- **A version without a command loses its link.** 0.2.0rc2 has no `alter`, so the `alter` link is
  removed, and `ci` and `pcim` stay. Upgrading again puts it back.

**A version that was never installed here.** Build its wheel from git, then roll back to it:

```sh
mkdir -p /tmp/alter-old && git archive release/alter-0.2.0rc2 | tar -x -C /tmp/alter-old
~/.alter/venv/bin/python -m pip wheel --no-deps -w /tmp/alter-old/dist /tmp/alter-old
scripts/install_alter.sh --rollback /tmp/alter-old/dist/contextual_intelligence-0.2.0rc2-py3-none-any.whl
```

**Forward again.** Run the script with the newer wheel (`scripts/install_alter.sh
~/.alter/wheels/contextual_intelligence-0.3.0a1-py3-none-any.whl`), or from the checkout.

## Data considerations

- **No data step, either way.** Your data in `~/.pcim` is not changed by installing, upgrading or
  rolling back.
- **What 0.3.0a1 adds, and what older versions do with it:**
  - the `goals` and `goal_events` tables, created on first use: older versions never read them, so
    goals stay in the database, unused;
  - the `alter-goals` store grant (outcome memory), and outcome episodes: older versions list the
    grant in `pcim consent list`, and show the episodes in `pcim memory` as ordinary items;
  - the `alter_sources` setting (vault and GitHub): older versions ignore it.
- **Verified on 2026-11-21**, in a throwaway `ALTER_HOME` and `PCIM_HOME`:
  - setup: `alter setup --init` and a remembered note under 0.3.0a1;
  - `--rollback` to 0.2.0rc2: `ci status`, `pcim memory` (the note), and `pcim audit` with
    `"chain_ok": true`;
  - upgrade again to 0.3.0a1: `alter status` and `alter memory` show the same data.
- **The embedder is pinned per store and never changes on rollback.** A store embedded
  semantically needs `sentence-transformers` under either version: do not roll back with
  `--no-semantic`.

## Git branch rollback

- 0.3.0a1 is on `main`. To undo the release in git, revert its merge commit on `main` rather than
  resetting the branch.
- The previous release's code stays on `release/alter-0.2.0rc2`.
