# MAYANK

MAYANK is a spatial view of ALTER's own state, at `/mayank` on `pcim serve`. It draws a person-shaped
cloud of points. Each bright point is a record ALTER holds: a memory, a goal, a task, an agent, a
claim, a hypothesis, a skill or a plan. The dim points around them are structure. The same points
re-form into seven other views (forms) of the same records. The page follows the audit chain live.
Every action it takes goes through ALTER's existing routes, so it has no authority of its own.

```sh
pcim serve                                   # then open http://127.0.0.1:8765/mayank and paste ~/.pcim/token
```

To see it without your own data, use clearly marked demonstration data in a throwaway home:

```sh
python scripts/mayank_demo.py --home /tmp/mayank-demo           # seed
PCIM_HOME=/tmp/mayank-demo pcim serve                           # open /mayank; token in /tmp/mayank-demo/token
python scripts/mayank_demo.py --home /tmp/mayank-demo --live    # optional: keep ALTER busy (real tasks in a scratch folder)
```

## What each point is

`src/pcim/mayank.py` builds the field from ALTER's records, reading them through the same objects
the API and the terminal use. Sealed content is opened only for its owner, and nothing is invented.

| Kind | Source | Relations |
|---|---|---|
| memory | `TemporalMemory` (bitemporal) | `supersedes` (a keyed correction), `contradicts` (a detected conflict), `derived_from` |
| project, decision, note | `Workspace` | `part_of` |
| goal | `GoalStore`; its Third Eye citations become `evidence` points | `evidence`, `produced` (outcome memory) |
| task | `TaskQueue`: every tool call proposed or run | `ran_for` (its goal or agent, by session) |
| run, agent | `RunService`: specialist runs and their work items | `part_of`, `depends_on` |
| claim | `ClaimLedger` | `evidence` (the tasks it cites) |
| hypothesis, assumption, counterexample, alternative, experiment | `ci challenge` records | `supports`, `contradicts`, `tests`, `revises` |
| skill | growth mastery (Bayesian Knowledge Tracing; only unassisted attempts move it) | the tree comes from the skill's path, `a / b / c` |
| plan, outcome / step | intent-compiler plans | `depends_on`, `serves` |
| request | routed model calls (interactions) | `informed` (the memory items sent) |

- If a source can't be read, the field lists it under `sources` with its error, and still draws
  everything else.
- The field keeps the newest 4,000 entities and reports how many it left out.
- Constellation positions come from the store's own embedder (the one memory uses), projected to
  three dimensions with PCA.
- Records marked `[demo]` are flagged, and the page shows a banner with their count.

## Forms

The same particles move between forms. Each record keeps its point, and only the arrangement changes.

1. **Human.**
   - Head: memory, skills and hypotheses.
   - Torso: projects, goals, plans and decisions.
   - Arms: runs, agents and tasks.
   - Legs: claims, evidence and requests.

   Within each part, records with similar meaning sit together. Each structure point belongs to its
   nearest record, so when a record's event arrives, that part of the body lights up.
2. **Constellation.** Position is meaning (embedder and PCA).
3. **Network.** Every recorded relation is drawn as a filament.
4. **Timeline.** A rising helix with the oldest records at the bottom. Each kind has its own lane.
5. **Grid.** One band per kind, oldest first.
6. **Execution.** Read left to right:
   - a goal's sources and its project;
   - the goal or run;
   - its agents and tasks;
   - the claims those tasks support.

   A task waiting for approval pulses cyan.
7. **Hypotheses.** Each peak is a challenged claim. Its height is the model's estimate that the claim
   holds, which is labelled as an estimate, not a measurement. Assumptions ring the base, weighted by
   how load-bearing they are. Counterexamples and the strongest alternative sit in the valleys, and
   the experiment that could settle the claim sits above it.
8. **Mastery.** The skill tree, with height showing mastery from unassisted checks. The form names
   the next challenge from growth's `next_challenge`.

## Interaction

- **Navigate:** drag to turn, right-drag to move, scroll to zoom.
- **Inspect:** click a point to open the inspector, which shows:
  - kind and status;
  - the record's provenance: source, time and project;
  - its relations;
  - **what this rests on**, the chain back through evidence, support and provenance.

  The chain is also highlighted in the field.
- **Find a path:** ⌘-click a second point to see the path between the two.
- **Search:** `/` searches by meaning (the store's embedder) and by words. Matching records light
  up, and the rest dim.
- **Commands:** `:` opens a command line: `form`, `challenge <claim>`, `approve`, `deny`, `isolate`,
  `expand`, `putback`, `halt`, `resume`, `refresh` and `help`.
- **Pull out:** drag a point to pull it out of the field, where it stays. "Put back N entries" returns
  every pulled point.
- **Shortcuts:** `1`–`8` switch forms, `I` isolates, `E` expands, `[` and `]` change point size, `L`
  changes the light.
- **Palettes:** copper (the default), lava, ocean, aurora and mono.

## Acting through ALTER

| On | Action | Route (existing unless noted) |
|---|---|---|
| a task waiting for you | approve (it runs now), deny, cancel | `/v1/tasks/{id}/approve`, `/deny`, `/cancel` |
| a run or agent | pause, resume, cancel, steer | `/v1/projects/{pid}/runs/{rid}/…` |
| a memory | confirm, pin, retract | `/v1/memory/{id}/…` |
| anything claimed | challenge this (a routed model call; the page asks first) | **new** `POST /v1/mayank/hypotheses` → `challenge.challenge` |
| a hypothesis | record held, failed or inconclusive | **new** `POST /v1/mayank/hypotheses/{id}/resolve` → `challenge.resolve` |
| a hypothesis | bring evidence (a routed model call; the page asks first) | **new** `POST /v1/mayank/hypotheses/{id}/evidence` |
| a skill | record an unassisted right or wrong answer | `/v1/growth/probe` (`assisted: false`) |
| everything | Stop-all, and resume | `/v1/autonomy/halt`, `/resume` |

**Bringing evidence** puts the claim back to the same challenge, together with the person's evidence,
as a new record that `revises` the old one. The change in the model's estimate is then on the
record: the inspector shows it as "34% → 46%", or shows that it didn't move. The challenge mode's
routing, consent, egress checks and audit apply as they do to any other request.

**Live updates.** `GET /v1/mayank/events?after=SEQ&wait=20` long-polls the audit chain. Each event
names the records it touched, and those records light up. An event that changes records triggers a
field refresh: existing points move from where they are, and new records emerge from the head.

## How it is built, and why

- **WebGL2, with no dependencies and no build step.** There are three ES modules and one HTML file
  in `src/pcim/dashboard/`, shipped in the wheel by the existing `dashboard/*` package data. Neither
  Three.js nor React Three Fiber is used:
  - the package serves static files from Python, and has no Node toolchain to bundle them;
  - a CDN would break the offline, local-first rule, and would tell a third party when the page is
    opened.

  The scene is points, lines and one background quad, which takes about 400 lines of renderer code.
- **The GPU does the morphing.** Every particle has a `from` position, a `to` position and its own
  delay. A change of form is a uniform going from 0 to 1, with turbulence in transit and a slight
  drift at rest. The CPU mirrors the easing exactly, so picking and labels track the points.
- **WebGPU is not used.** WebGL2 already holds the frame-rate cap at 160,000 points, and works in more
  browsers.
- **Long polling, not a WebSocket.** Traffic from server to page is one-way, and actions are ordinary
  REST calls. Long polling also needs no new server dependency.

**Measured** (Chrome, headless, through ANGLE on Metal with an Apple M5): 60 fps, the browser's frame
cap, in every form at 42,000, 120,000 and 160,000 points, with about 0.1 ms of CPU per frame. GPU time
was not measured separately.

Building the field from a real store with 172 records took 3.8 s with the MiniLM embedder (this
includes loading the model), and 0.01 s once the embeddings were cached. With demonstration data and
the hashing embedder it takes 49 ms.

## Not built yet

These parts of the brief are not implemented. Nothing on the page pretends otherwise.

- **Voice, hand gestures, WebXR or mixed reality.** The renderer is plain WebGL2, so a WebXR session
  could reuse it, but nothing has been built or tested on that hardware.
- **Generated assessments.** MAYANK records unassisted checks that the person reports. It doesn't yet
  write the questions itself.
- **Questioning a single assumption.** It can be challenged as a new claim, but not yet in place
  within its hypothesis.
- **Plans don't run from the page.** Plans are drawn, but compiling or running one still happens in
  the terminal (`ci plan`, `alter`).
- **Agent internals.** The page shows what the audit chain and the records say. It doesn't show, and
  doesn't claim to show, a model's internal reasoning.

## Tests

- `tests/test_mayank.py` covers:
  - each source and its relations;
  - statuses and measurements coming from the records;
  - an unreadable source being reported while the rest still draws;
  - demonstration flags;
  - search;
  - field caching keyed to the audit chain;
  - the long-poll feed;
  - authentication on the routes and the shipped assets;
  - the hypothesis routes, with the model call mocked;
  - the demo seeder refusing a home it didn't make.
- An end-to-end browser pass (Playwright driving Chrome, outside the repository) checked 18
  interactions:
  - clicking to inspect;
  - semantic search, and Enter flying to the top result;
  - pulling a point out and putting it back;
  - approving a waiting task: it ran, its event arrived live, and its point lit up;
  - recording a hypothesis outcome;
  - an unassisted check moving mastery from 0.28 to 0.70;
  - switching forms from the keyboard;
  - no page errors.
