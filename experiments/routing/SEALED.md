# Sealed routing test set #2

| | |
|---|---|
| file | `routing_sealed2.json`, kept outside every git worktree until the registered run |
| sha256 | `7d365d1a7e26d3bab5b14c4f22864764fbc1ffce18b499f803e455b11ac9d0dd` |
| tasks | 60 (22 light, 22 standard, 16 deep), at least 15 deliberately tricky |
| written | 2026-11-11 by a separate agent given only the labelling rubric, no access to CI code |
| committed | before any estimator, the current lexical one or a learned replacement, has been run on it |

Custody: the integrating session saw the content when the writer returned it, and changes no
estimator code from that point. The learned-estimator branch (`agent/estimator`) was built in a
worktree without this file, the first held-out set, or the reports that quote it.

Registered use: one run per estimator version, reported unedited in REPORT.md: exact accuracy,
under-routing rate, deep recall, and estimated cost against always-Opus. The file is added to
the repository with that first run, and its hash checked by `run.py`.
