# Rollback Procedure (`0.2.0rc2`)

## Package rollback

1. Stop any running sessions with `ci halt`.
2. Reinstall the previous package version (`0.2.0rc1`, `release/alter-0.2.0` at `dd3dcda`), or switch `PYTHONPATH` back to the `main` checkout (`1f9bd08`).
3. Verify the data with `main`'s commands: `ci status`, `pcim memory`, and `pcim audit` (expect `"chain_ok": true`). `main` has no `ci audit`.

## Git branch rollback

- `release/alter-0.2.0` has not been merged to `main`. No `main` rollback is required.
- To discard RC-only commits while keeping the integration state, reset `release/alter-0.2.0` to `integration/intent-compiler` (`441ea72`).

## Data considerations

- **Verified on 2026-11-16:** after this release had written to a store (an audit entry, a compiled plan), `main` (`1f9bd08`) still reads it with `ci status` and `pcim memory`, and `pcim audit` verifies the chain.
- **The embedder is pinned per store and never changes on rollback.** A store embedded semantically needs `sentence-transformers` under either version.
- Plans compiled under `0.2.0rc1`/`rc2` are stored in policy state (`pcim-plans`), and `ci do` requests in `pcim-do`. Older versions ignore both: they are policy-state rows those versions never read.
