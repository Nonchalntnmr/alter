# ALTER 0.3.0a1 (alpha)

**What it is.**
- The first integrated version: one command, `alter`, that understands, remembers, acts, verifies
  and learns on a real project folder. `docs/ALTER.md` maps the vision to what is built and what
  is not.
- **Alpha:** it works end to end, but is not validated by people.
- **The gate stays CONDITIONAL** (`READINESS.md`).
- **No performance claim** over direct model execution: none has been shown.

## New

- **`alter`.** A conversation, and these subcommands:
  - init, status, context, goals, tasks, approve/deny, memory, permissions, vault, weekly, doctor
    and ask;
  - every `ci` command passes through, and `ci` itself is unchanged.
- **Durable goals** (`goals.py`):
  - **state:** sealed at rest; a checkpoint after every agent turn;
  - **talking to a running goal:** steer, pause and cancel events, read before every model step;
  - **liveness:** an OS lock, so a dead worker reads "interrupted", and resume continues it;
  - **limits:** turns, cost, and a time window that pauses the goal rather than running on;
  - **failures:** a provider failure is retried once, then the goal parks;
  - **verdict:** done means the project's tests passed after the last change.
- **Third Eye** (`thirdeye.py`):
  - a cited brief, split into **remembered** (ALTER's records) and **sourced** (git, GitHub, the
    vault), with a "not included" list;
  - GitHub and the vault are opt-in, and a project can have vault topics.
- **Vault search** (`vault.py`):
  - the vault's own `nos` ranking, unchanged: dev hit@1 18/20 with MRR 0.95, held-out 8/10 with
    MRR 0.88, the same as `nos.py` itself;
  - iCloud placeholders are fetched in parallel, with a time bound.
- **Outcome memory:** each goal's outcome becomes an episode, with provenance, computed from task
  records, under an explicit grant.
- **`Agent.pause_check`:** a pause or a new instruction takes effect at the next model step, with
  every action so far recorded.

## Found in the live setup, and fixed before the demo

1. **The test double could run a goal.** An identity onboarded with only the mock provider made a
   goal run on the mock agent. Goals now refuse, and name the command to grant a real model.
2. **The vault was skipped.** macOS evicted every vault note within an hour (Optimize Mac Storage),
   and placeholders were skipped. They are now fetched, bounded: 119 notes in 5.3 s.
3. **The vault did not know the name ALTER.** The vault knows this project as "Contextual
   Intelligence". Vault topics were added.
4. **A new goal counted itself.** The brief listed the new goal as remembered. It is now gathered
   first.
5. **Ids resolved by suffix only.** Goal ids now resolve by unique prefix too.

## Install

```bash
python3.14 -m venv ~/.alter/venv
~/.alter/venv/bin/pip install "contextual_intelligence-0.3.0a1-py3-none-any.whl[semantic]"
ln -sf ~/.alter/venv/bin/alter ~/.local/bin/alter      # and ci, pcim
alter doctor
cd <project> && alter init --provider claude-cli && alter
```

**Upgrading from 0.2.0rc2.** The data is the same: two new tables are added (`goals`,
`goal_events`), and older versions ignore them. To roll back, install the 0.2.0rc2 wheel. Goals
stay in the database, unused.
