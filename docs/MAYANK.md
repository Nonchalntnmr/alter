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

**A static demonstration you can share.** This has no ALTER behind it and holds demonstration data
only:

```sh
python scripts/mayank_static.py --out ../mayank-site     # then deploy the folder to any static host
```

The builder seeds a fresh throwaway home and records real governed actions in it. It writes the
field and those audit events as JSON next to the page. The page then runs read-only:
- no sign-in;
- search matches words, since a static page has no embedder;
- the recorded events replay on a loop, labelled as a replay;
- every action button is replaced by a note saying this is a snapshot.

The builder refuses to write any file that contains an absolute path, the throwaway home's path or
the machine's user name.

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
| a skill | take a test MAYANK writes and marks (below), or record a check done elsewhere | `/v1/growth/probe` (`assisted: false`) |
| a question (end it with `?`, `:ask`, or say it) | MAYANK answers, and shows the records the answer rests on (a routed model call; the page asks once per session) | **new** `POST /v1/mayank/ask` → `ask_routed` |
| an assumption | interrogate it on its own: it becomes the claim under challenge, linked back to it (`interrogates`) | **new** `POST /v1/mayank/hypotheses/{id}/assumptions/{k}/challenge` |
| a project (`:goal <objective>`) | start a run; its agents appear in the execution form | `POST /v1/projects/{pid}/runs` |
| a run | advance one agent turn (a model call; the page asks first) | `POST /v1/projects/{pid}/runs/{rid}/advance` |
| everything | Stop-all, and resume | `/v1/autonomy/halt`, `/resume` |

**Bringing evidence** puts the claim back to the same challenge, together with the person's evidence,
as a new record that `revises` the old one. The change in the model's estimate is then on the
record: the inspector shows it as "34% → 46%", or shows that it didn't move. The challenge mode's
routing, consent, egress checks and audit apply as they do to any other request.

**Answers show what they rest on.** An answer comes back with the IDs of the memory items ALTER sent
with the question (`receipt.sent`). Those points light up, the answer panel lists them, and the new
request record links to each one (`informed`) and to the model that served it (`served_by`).

**Tests MAYANK writes and marks itself.** `mayank-learn.js` has 23 question generators. Each one
computes its own answer, with no model involved, for skills such as:
- eigenvalues, determinants, SVD, PCA;
- Bayes' rule, martingales, gradients, convexity, Fourier periods;
- heaps, dynamic programming, hypothesis tests, difference-in-differences;
- portfolio variance, shrinkage, VaR, put-call parity;
- Lagrangian and Hamiltonian mechanics, entropy.

How it works:
- Difficulty starts from mastery and moves up or down after each answer.
- The rest of the page dims while you answer.
- The working appears only after you answer.
- The result is recorded as an unassisted check, the only kind that moves mastery.

The mastery form lists skills that are due for review on the FSRS schedule. A skill with no generator
falls back to an honest self-check. A test (outside the repository) verified 28,800 generated
questions against independently computed answers.

**Traces.** A task shows what actually ran:
- the command (absolute paths cut to their last part), its exit code and the tail of its output;
- whether it ran sandboxed or on the host;
- for a write, the file, its size and its hash.

A goal shows its verification line, a run shows its event history, and an agent shows its attempts
and their problems.

**Contradictions** are drawn in every form. A memory in conflict pulses rose and says that neither
claim wins until you settle it.

**Voice** uses the browser's Web Speech API, where available (Chrome, Safari). Press `V` or the
microphone, then say a search or a question. Questions are asked, and spoken answers are read
aloud.

**Hands and a headset, simulated.** These show how MAYANK would be operated by gesture and in a
headset. No camera or headset is read, and the page says so on screen the whole time.
- **Simulated hands (`G`).** `mayank-xr.js` has a kinematic hand model with 21 joints, the layout
  hand-tracking APIs use, and poses for point, pinch, open, fist and twist. The right hand follows
  the cursor, points when it is over a record and pinches on click.
- **Gesture tour (`H`).** A scripted pair of hands performs each gesture: point, pinch to select,
  pinch and pull a record out, open the hand to leave it there, spread two hands to fan out its
  neighbours, swipe to the next form, twist to turn the field, and put palms down to put everything
  back.

  The gestures are not animations of results. They call the same select, pull-out, expand, form and
  put-back actions as the mouse.
- **Headset preview (`X`).** The scene is drawn once per eye, side by side, with lens vignettes. You
  stand life-size in front of the figure, with both hands visible.
  - A record is picked by the ray from your eye through where the fingertip points, and pinching
    selects it. The ray is aimed from the pointing pose, so the curling finger of a pinch doesn't
    move it.
  - Drag while pinching to pull the record toward you; shift + move or the arrow keys look around.
  - `M` switches between a wireframe room, which stands in for camera passthrough in mixed reality,
    and a void (virtual reality).

**Live updates.** `GET /v1/mayank/events?after=SEQ&run_after=SEQ&wait=20` long-polls two sources:
- the audit chain;
- the specialist runs' own event log. Runs do not write to the audit chain, and only their run and
  project IDs are read.

Each event names the records it touched, and those records light up. An event that changes records
triggers a field refresh: existing points move from where they are, and new records emerge from
the head.

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

- **Real hand tracking and real headsets.** The hands and the headset view are simulations, driven
  by the mouse and a script. Real hand tracking (camera or WebXR hand input) and real headset
  rendering (a WebXR session) are not built, and have not been tried on any hardware. The renderer
  already draws per eye, which a WebXR session would reuse.
- **Voice on real hardware.** The voice path was tested with a stand-in speech recognizer, because a
  headless browser has no microphone. Recognition itself is the browser's.
- **Questions for arbitrary skills.** Tests cover the 23 generators. MAYANK does not ask a model to
  write questions for other skills.
- **Plans run only from the terminal.** Plans are drawn, but compiling or running an intent-compiler
  plan still happens in the terminal (`ci plan`, `alter`). Runs, by contrast, can be started and
  advanced from the page.
- **Agent internals.** The page shows what the records and the audit chain say. It doesn't show, and
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
  - the demo seeder refusing a home it didn't make;
  - asking, with the records sent;
  - interrogating an assumption;
  - traces without absolute paths;
  - contradictions and review schedules;
  - a new run reaching the field and the live feed.
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
- A second browser pass checked 16 more:
  - a question offered as "Ask MAYANK", answered with the model that served it and the 24 records
    it drew on, which lit up;
  - a test question written, a right answer marked right with its working and recorded, and a wrong
    answer marked wrong;
  - a test run's trace;
  - interrogating an assumption;
  - `:goal` starting a run whose agents appear;
  - a spoken question asked, answered and read aloud;
  - no page errors.
- A third browser pass checked the simulated hands and headset, with 14 checks:
  - the hand appears and says no camera is used;
  - pointing and pinching selects a record;
  - the tour runs all eight gestures, and its pinch, pull, spread and swipe do the real actions;
  - palms down puts everything back;
  - the headset view draws both hands in two eyes, and its ray picks a record;
  - a pinch selects that record, and the aim holds during the pinch;
  - `M` switches the room;
  - it holds 60 fps;
  - `Esc` exits;
  - no page errors.
