# Routing benchmark

Offline and deterministic: no model is called. `PYTHONPATH=src python experiments/routing/run.py`.

## Development set (`dataset.json`, 36 tasks, sha256 `92630d77…`)

Written and hashed before `routing.estimate` existed, by the session that then wrote the
estimator **with this file in view**. Several estimator markers come from these tasks' wording
("study schedule", "what's weak", "trustworthy", "zero downtime", "numerically"). So:

| | result |
|---|---|
| tier agreement | 36 / 36 |
| what it means | the estimator encodes its author's labels for these 36 tasks. **Not a measure of accuracy.** Kept as a regression check only. |

Estimated list-price cost over the same 36 tasks on a Claude-CLI-only machine, using the
router's own token estimates (an estimate, not a bill, and silent on answer quality):

| strategy | estimated cost |
|---|---|
| routed (raw estimate, before the floor) | $0.947 |
| always Opus (what first-available routing picks here) | $1.419 |
| always Sonnet | $0.710 |
| always Haiku | $0.036 |
| the label's tier (oracle) | $0.947 |

Fallback recovery (before the floor), light request, providers failing in rank order:

| failing | completed | served by | attempts |
|---|---|---|---|
| 0 | yes | claude-cli:haiku | 1 |
| 1 | yes | claude-cli:sonnet | 2 |
| 2 | yes | claude-cli:opus | 3 |
| 3 | no: refused with every error listed | | 3 |

## Held-out set (`heldout.json`, 40 tasks, sha256 `47f42367…`), the registered result

Written by a separate agent from the labelling rubric alone, with no access to any CI code;
saved and hashed before the estimator ran on it; this is the first and only run of the raw
estimator on it, unedited.

| | raw lexical estimate |
|---|---|
| exact tier | **15 / 40 (0.375)**; three-way chance is about 0.33 |
| under-routed (estimate below label) | **18 / 40** |
| over-routed | 7 / 40 |
| deep requests recognised as deep | **3 / 10** (6 of the 10 were estimated *light*) |

Confusion (rows = label, columns = estimate): light 8 / 4 / 3 · standard 11 / 4 / 0 · deep 6 / 1 / 3.

What fails: difficulty that is not in the wording. Plain questions with hard content (a
lock-free `pop()`, AES-GCM nonce limits at 2^40 messages, a study-design critique, staggered
TWFE) carry no marker and land at light; trivial requests that say "design", "architecture",
"strategy", "regex" or "prove" are pushed up. **The lexical estimator does not work on unseen
requests.**

### Decision taken after seeing it (so not a clean measurement)

Under-routing costs answer quality, over-routing costs money. The default preference
(`balanced`) now never routes below standard; light is used only under `ci prefer economy` or
a pinned model (`routing.FLOORS`). The estimator itself was **not** tuned on this set. On the
same set, post hoc: exact 18 / 40, under-routed 7, over-routed 15, deep recall still 3 / 10.
Measuring the floor, or a replacement estimator, needs a new sealed set.

## Sealed set #2 (`sealed2.json`, 60 tasks, sha256 `7d365d1a…`): the registered run

Written by a separate agent from the rubric alone; hashed and committed (SEALED.md) before any
estimator ran on it; kept outside every worktree until this run; run once per estimator, below
unedited (`learned_on_sealed2.txt`, `lexical_on_sealed2.json`). The learned estimator's targets were
preregistered by its author before the run, from cross-validation on its own training set.

| (n = 60; 16 deep) | lexical markers | lexical + floor | **learned** (escalating) | preregistered line |
|---|---|---|---|---|
| under-routed | 33 (55%) | 16 (27%) | **1 (1.7%; 95% CI 0.3–8.9%)** | at most 10% |
| deep recognised as deep | 0 / 16 | 0 / 16 | **15 / 16 (94%; CI 72–99%)** | at least 80% |
| exact tier | 14 / 60 | 20 / 60 | **36 / 60 (60%; CI 47–71%)** | at least 55% |
| est. cost vs always-Opus (with the floor) | | | **0.837** | at most 0.90 |

All four preregistered targets pass. On the first held-out set (40 tasks; the learned estimator's
author never saw it): under-routed 0, deep 10 / 10, exact 26 / 40 (65%), cost 0.837
(`learned_on_heldout.txt`). Caveats: 60 and 40 requests are small samples (the intervals above);
the labels are one labeller's judgement of the lowest trustworthy tier; the cost is the router's
own token estimate at list price; and none of this measures answer quality, which needs the live
evaluation. Oracle routing (the label's own tier) costs 0.706 of always-Opus on this set, so a
40% saving is out of reach for any router under the standard floor.

## Not shown

Whether routed answers are as good as a fixed model's. That needs live calls graded blind,
preregistered like E2 (docs/CI_AMPLIFY.md, plan item 2).
