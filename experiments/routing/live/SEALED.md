# Sealed test set for the live routing evaluation

| | |
|---|---|
| file | `live_test_set.json`, kept outside every git worktree until the registered run |
| sha256 | `3139fe8fab2d1ade8eb4cd94dc9413f303643d388c9d7d08da6e883e9fa860be` (pass as `--expect-sha256`) |
| tasks | 120: 24 each of code, writing, research, planning, learning; 40 each at tiers 1, 2, 3; 81 with an objective check; 0 excluded by the registered screens |
| written | 2026-11-12 by a separate agent from Appendix B's brief, with no access to CI's code, the routing data or the harness |
| custody | written straight to a file and sealed without the integrating session reading the requests (it saw counts only) |

Estimates printed by the harness before any call (list price, routing's token guesses; notional under a
Claude subscription): pilot of 10 requests $6.99 with a Fable 5.2 grader; full run $73.74 (Fable 5.2) or
$83.48 (Fable 5.1, the default). The routed arm would send 61 of 120 requests to Opus and 59 to Sonnet
(estimated saving against always-Opus about 14%).
