# Rollback Procedure (`0.2.0rc1`)

## Package rollback

1. Stop any running sessions with `ci halt`.
2. Reinstall the previous package version or switch `PYTHONPATH` back to the `main` checkout (`1f9bd08`).
3. Verify `PCIM_HOME` compatibility with `ci status` and `ci audit`.

## Git branch rollback

- `release/alter-0.2.0` has not been merged to `main`. No `main` rollback is required.
- To discard RC-only commits while keeping the integration state, reset `release/alter-0.2.0` to `integration/intent-compiler` (`441ea72`).

## Data considerations

- `PCIM_HOME/pcim.db` schema is backwards-compatible with `main` (`1f9bd08`) for `ci status`, `ci audit`, and `ci twin`.
- Plans compiled under `0.2.0rc1` are stored in policy state (`pcim-plans`) and do not affect direct `ci ask` sessions on `main`.
