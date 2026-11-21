# Safety audit: ALTER `0.3.0a1`

**Date:** 2026-11-21. **Branch:** `chore/safety-audit`, from `31504c6` (the head of
`feat/alter-alpha`, PR #9). **Scope:** sensitive local paths and identifiers in the repository's
history, credentials in every blob, every path that sends data off the machine, and recovery.
Nothing here was pushed.

**Redaction.** No identifier or secret is written out in full. Each is named by its label from
`scripts/scan_identity.py` (`home path`, `git user.email`, …), and where a value helps, by its first
three characters (`rev…@nor…` for the author email). The scans print labels and counts, never values.

**Method.**
- `git fetch origin`. Then a mirror clone of the public repository into a scratch directory, which
  holds all 18 public refs, including `refs/pull/1-9/*`. GitHub's activity API shows no force-push
  ever, so no orphaned public commits exist outside those refs.
- Every blob, commit message, and author and committer line: 875 blobs and 189 commits public;
  941 blobs and 285 commits local, including unreachable objects. Each was scanned for this
  machine's identifiers (the 17 labels of `scan_identity.py`, plus the hostname's short label and
  generic `/Users/<name>`, `/home/<name>` and email patterns) and for secrets.
- Secrets: gitleaks 8.30.1 over the public mirror and the local repository (`--all`), plus a
  pattern scan of every blob (key shapes, private key blocks, JWTs, URLs with passwords, and
  sensitive file names).
- PR titles and bodies, issues, comments and releases, read through `gh api` (GET only).
- Egress: by reading the code. No model or network call was made.
- Recovery: real `alter`, `ci` and `pcim` commands in a throwaway `PCIM_HOME` and `HOME`, with
  the mock provider. The real `~/.pcim` was never opened.

## Findings

| id | rating | finding | status |
|---|---|---|---|
| R1 | **high** | A missing `master.key` was silently replaced by a new key. Later writes were sealed under it, and restoring the real key then broke `alter memory` for good. | **fixed** `531ce55` (S1) |
| R2 | **high** | One damaged goal record crashed `alter status`, `goals`, `doctor` and `weekly`, blocked new goals in its project, and could not be forgotten. | **fixed** `d88fde5` (S2) |
| R3 | **high** | Undo overwrote changes made after the write: the person's own edits, or later writes. | **fixed** `cb70e30` (S3) |
| R4 | **high** | Undo of a non-UTF-8 file wrote U+FFFD in place of the original bytes. The backup was lossy. | **fixed** `9af798a` (S4) |
| E1 | **high** | The ALTER brief bypassed the credential tripwire. A key in a vault note, the README, a commit subject or a PR title reached the goal's provider. | **fixed** `1204055` (S5) |
| H1 | medium | Every public commit's author email is the owner's university address (`rev…@nor…`). The repository has been public since it was created. | owner decision (§6) |
| H8 | medium | `HISTORY_REWRITE.md` said the repository was private, and the earlier decisions rested on that. | **fixed** `4d87279` (docs) |
| H9 | medium | GitHub secret scanning and push protection are off, and `main` has no branch protection. | owner decision |
| E2 | medium | `alter ask` outside a project sends vault excerpts to whichever vendor routing picks. The vault opt-in names no vendor. | open |
| E3 | medium | The agent path (`ci do`, goals, `alter say`) has no credential tripwire on what the person types or on tool results. Pre-existing since rc2. | open |
| E10 | medium | With the GitHub source on and autonomy granted, anyone's PR title on a public repository reaches the goal's first message. An injected instruction could get a test file written and then run. | open |
| R5 | medium | No undo command exists in `alter`, `ci` or `pcim`. `ci undo <task>` is sent to the model as a question, and exits 0. | open |
| R6 | medium | After a goal worker died, its test process kept running and its task stayed `running`. Resume started a second test run alongside it. | fixed in the recovery follow-up |
| R7 | medium | Backup and restore are not documented. A plain copy of `pcim.db` taken while a worker holds the store open loses committed data. | open (procedure in §4) |
| H2 | low | The home path (`/Us…`, holding the username `nis…`) is in 3 files on `main`. | owner decision (§6) |
| H3 | low | A model answer on `main` names the institution behind the email domain (a leak from before identity isolation). | owner decision (§6) |
| H7 | low | The local repository holds identifiers that are not public: `refs/backup/pre-rewrite-*`, 4 unreachable blobs, and parked `agent/*` branches. | owner decision |
| H10 | low | `scan_identity.py` never flags `main`'s own exposure, and checks only the full hostname. | open |
| C3 | low | `pcim init` prints the API token in full. | open |
| E4 | low | The tripwire's shapes miss `NAME_TOKEN=value` in mid-line (verified with a vault note). | open |
| E5 | low | The vault source cannot exclude a folder or a private note, beyond a fixed skip list. | open |
| E6 | low | The brief's commit subjects carry the GitHub login (`Non…`) from merge commits. | accepted |
| E8 | low | Goal worker logs also receive stderr, so a crash leaves a traceback, though the code says "ids and statuses only". | open |
| R8 | low | Rollback to 0.2.0rc2 works, but `ROLLBACK.md` still describes only rc2. | open |
| R9 | low | `$PCIM_HOME` is created 0755 (its files are 0600), and `.session-*.lock` files accumulate. | open |
| H4, H5, H6, C1, C2, E7, E9, R10 | info | §1 to §4 | — |

---

## 1. History (identifiers on origin and in all blobs)

### Public (all 18 refs of `origin`, including `refs/pull/*`)

| id | what | where (redacted) | public since | removal needs |
|---|---|---|---|---|
| H1 | author email `rev…@nor…` (the `git user.email` and `account email` labels) | author line of all 189 public commits, committer line of 178. The other 11 are GitHub-made merges, with GitHub's noreply address | each push, from 2026-10-17 | rewriting every commit on every branch, plus a force-push. GitHub keeps `refs/pull/1-9/head` read-only, so those old commits stay reachable unless GitHub Support purges them. GitHub's public event stream has carried the email in push payloads since the first push, and third-party archives mirror that stream |
| H2 | home path `/Us…` (holds `username ($USER)` `nis…` and `name token 2`) | `main`: `experiments/milestone1/live-run3-2026-10-24.json` (2 paths, and a CLI scratch-directory slug built from the path); `experiments/routing/learned_on_heldout.txt` (1); `experiments/routing/learned_on_sealed2.txt` (1) | pushed with `main` | a rewrite of `main` and a force-push. A forward commit that redacts the 3 files stops new copies only |
| H3 | the `email domain label` `nor…`, inside a model's answer, which suggested places near that institution. Most likely the model inferred it from the account email that the CLI put into the context before identity isolation existed | `main`: `experiments/e1/results/sonnet-exploratory/results.json` | pushed with `main` | as H2 |
| H4 | the owner's name (`name token 1`, display and full name) | `main`: `README.md`, `pyproject.toml` (authors), `docs/RESEARCH.md`, `docs/STRATEGY.md`, `src/pcim/cli.py` (a usage example), and as a test user id in `tests/test_amplify.py`, `test_api.py`, `test_challenge.py`, `test_security_review_amplify.py` | — | intentional attribution. The test ids could be neutral |
| H5 | GitHub login `Non…`, in the repository URL and in merge-commit subjects | everywhere | — | public by nature |
| H6 | hostname (full, and its short label), account id, organization id and name | **none** in any public blob, message, ref name or PR, issue or comment text | — | — |

The titles and bodies of the 9 PRs (the issues API lists the same 9), comments (0) and releases (0)
hold none of the 17 labels.
The 25 Actions runs use a read-only token and no secrets (the repository has 0 Actions secrets and
0 artifacts).

### Local only (never pushed)

| id | what | where |
|---|---|---|
| H7 | home path, and the identifier fragments of a hand-written grep check | the 8 `refs/backup/pre-rewrite-20261117-185237/*` refs (`experiments/intent/results.json`, `HANDOFF.md`, `experiments/eval_v4/*/run_*_stdout.txt`), kept as the rewrite's rollback |
| H7 | home path | 4 unreachable loose blobs (pruned by a future `git gc`) |
| H7 | the display name as a test user id | 7 parked local branches: `agent/api`, `budget`, `calibration`, `local-models`, `mcp`, `repl`; `rescue/argon-integration-2026-11-11` |

These become public only through `git push --mirror`, `--all` or a refspec glob. The history-rewrite
runbook already says to push named branches only.

### Repository settings

- **H8 (fixed).** The repository was **created public** on 2026-10-17: GitHub's events API
  records a `PublicEvent` at creation. `HISTORY_REWRITE.md` §1 said "The repository is private".
  `4d87279` corrects it.
- **H9.** `security_and_analysis`: secret scanning, push protection, non-provider patterns and
  validity checks are all `disabled`. Dependabot security updates are off. `main` is not protected
  (`GET …/branches/main/protection` → 404), so a force-push to `main` would be accepted. All of
  these are free for a public repository. The workflow pins actions to major tags, not commit SHAs
  (its own comment says so).
- **H10.** `scan_identity.py --history` uses `origin/main` as BASE and counts what BASE already
  holds as "inherited". So the gate is blind to H2 and H3 by design. Its `hostname` marker is the
  full name, including `.local`. Remediation: a mode that scans a ref against the empty tree, and
  a marker for the hostname's first label.

## 2. Credentials (every blob, public and local)

**No real credential was found** in any blob, commit message or ref, public or local, including
unreachable objects. No `.env`, `*.pem`, `*.key`, `id_*`, `.npmrc`, `.pypirc`, `.netrc`, `*.db` or
`*.sqlite` file was ever committed. The only file with "credentials" in its name is code
(`src/pcim/sovereignty/credentials.py`). `.gitignore` excludes `*.db`, `*.db-wal`, `*.key` and
`data/`.

**C1. Fake test values** (gitleaks: 6 hits in public history, 14 with the local branches). Each was
checked without printing it:

| shape (redacted) | why it is fake | where |
|---|---|---|
| AWS access key id `AKI…` (20 characters) | the 16 characters after `AKIA` are consecutive letters | `main`: `tests/test_amplify.py:120`, `tests/test_routing_live_harness.py:32`, `tests/test_challenge.py:94`. Local `agent/*`: `tests/test_repl.py:211,222`, `test_collab.py:296`, `test_api_amplify.py:21`, `test_local_models.py:494` |
| `sk-…` (31 characters) | contains `TEST` | `main`: `tests/test_adapter_errors.py:24` |
| `.env` lines `DB_…`, `AWS…`, a JSON password and a URL with a password | a pasted fixture for the HIGH-severity routing test | `main`: `tests/test_security_review_amplify.py:75-78` |
| URL with userinfo `htt…` | fixture | `main`: `tests/test_adapter_errors.py:202` |
| `STR…` in a `.env` written to a temp repo | fixture, contains `SECRET` | `main`: `docs/review_2026_10_26_pending_tests.py:245`; local: `tests/test_review_2026_10_26.py:245` |
| `-----BEGIN OPENSSH PRIVATE KEY-----` with body `SECRETKEY` | fixture | `main`: `tests/test_alter_review.py:58` |

- **C2 (info).** These fixtures keep tripping scanners. If secret scanning is turned on (H9),
  allowlist `tests/`, or build key-shaped strings at run time, as `tests/test_safety_audit.py` does.
- **C3 (low).** `pcim init` prints the API token in full to the terminal (`src/pcim/cli.py:83` at
  `31504c6`). Remediation: print only where it was saved.

## 3. Egress

`docs/SECURITY_WHAT_LEAVES.md` §5 to §8 (updated in `4d87279`) now lists every path, with what it
sends, its opt-in and its audit record. In summary:

| path | sends | opt-in | audited |
|---|---|---|---|
| `claude` CLI | measured: request, no identifiers but the account id in metadata | provider granted to the project | `route.plan`, `model.call`, `session.disclosure` |
| Anthropic API | `system`, `messages`, `tools`; SDK headers with OS, architecture and runtime. Endpoint pinned; `ANTHROPIC_BASE_URL` ignored | `ANTHROPIC_API_KEY` and a grant | same |
| OpenAI-compatible | `messages`, `tools`; no `user` field | `OPENAI_API_KEY` and a grant | same |
| Gemini | `contents`; `ask` only (no agent step) | `GEMINI_API_KEY` and a grant | `route.plan`, `model.call` |
| local | loopback only, enforced; proxy variables ignored | `PCIM_LOCAL_*` | `model.call` |
| `gh` | one read-only `pr list` (8 PRs) with the person's `gh` login | `alter permissions github on` | `alter.source`; `alter.context` lists `github` |
| vault | nothing by itself; iCloud placeholders are downloaded from Apple | `alter permissions vault on` | `alter.source`; `alter.context` `vault_notes` |
| ALTER brief | project name and folder name, earlier goals, notes, git status, 20 commit subjects (no authors), the README's head, PRs, vault sections | a non-test provider granted to the project | `alter.context` and `alter.ask`; the goal's sealed record keeps the text |

**Code against the document.** The measured `claude` CLI path matches, with one wording fix: the
quiet switches keep a value the person set (`setdefault`, `claude_cli.py:394`). Before this audit
the document covered no other path, so §5 to §8 are new. They come from the code and are not
measured (E7): `scripts/audit_egress.py` covers only the CLI. A measured goal run, with the brief
on, is the next step.

**E1 (high, fixed in `1204055`).** `ci ask` runs `find_credentials` on the request and withholds
every cloud vendor when it finds one (`routing.py:380`). ALTER's brief (`thirdeye.Brief.for_model`,
`thirdeye.py:67`) is the first message of every goal session (`goals.py:410`). It goes through
`Agent.send` (`agent.py:250`), which has no such check. Verified in a temporary home: a vault note
holding a fake AWS key matched a goal's query, and the first message the provider received carried
`['AWS access key id']`. Now every piece of a `Brief` passes through `credentials.redact` when the
brief is built. The match becomes `[REDACTED:<kind>]`, and the redaction is listed under "Not
included". `alter context`, the stored context and the model's input agree. Re-verified with
`alter context`. Test: `test_s5_…`.

**E2 (medium).** With no project folder, `alter ask` searches the vault and calls `ask_routed`
with `project_id=None` (`alter.py:411`). Any vendor in the person's own-statement grants can then
receive vault sections, not only the vendors of some project. The vault opt-in message says
"matching vault sections go to the model" but names no vendor. Remediation: store vendors with the
vault opt-in (`alter permissions vault on --provider …`), and pass them as `project_vendors`.

**E3 (medium, pre-existing).** `Agent.send` sends what the person types and every tool result with
no credential check. `is_secret` keeps files like `.env` and keys from being read, but a key
hard-coded in `config.py` is read and sent. For goals, `alter say` text too. Remediation: run
`find_credentials` on each outgoing user message and tool result, and redact, or ask first. This is
a design change to the agent loop, so it is written up, not done.

**E4 (low).** The tripwire matches published shapes. `DEPLOY_TOKEN=…` in the middle of a line is
not matched, because the assignment pattern is anchored to the start of a line. Extending the
patterns changes `ci ask` routing, so it needs a false-positive check first.

**E5 (low).** Every `*.md` outside `vault.SKIP` (`vault.py:18`) can be matched and sent. A `.md`
symlink is read wherever it points. Remediation: a configurable exclusion (folder, or
`private: true` frontmatter), and skip symlinks that leave the vault.

**E6 (low, accepted).** Merge subjects ("Merge pull request #N from `Non…`/…") put the GitHub login
into the brief. It is public, but CLI identity isolation does not cover the project's own content.

**E10 (medium).** `gh pr list --state all` returns the last 8 PRs whoever opened them, and the
brief does not say who wrote them. On a public repository a stranger chooses those titles (up to
160 characters each). Vault notes can be web clippings too. The brief is fenced as data with a
nonce, and the system prompt says only the person gives instructions. But under an autonomy grant
(`alter permissions autonomy grant`), writes and test runs need no approval. So a model that
followed an injected instruction could write `conftest.py` (or a test) and then run it through
`run_tests`. That is code execution on this machine, bounded by the grant's action count and time
limit. `ci do` under autonomy has the same exposure through any file the model reads; the GitHub
source adds input that no collaborator wrote. Remediation:
- list only PRs by the person or the repository's collaborators (`author_association`), or label
  each PR's author in the brief;
- under autonomy, keep approval for writes to test configuration (`conftest.py`, `pytest.ini`,
  `pyproject.toml`, `package.json` scripts);
- name `<alter_context>` in the agent's system prompt, which calls only `<project_briefing>`,
  `<personal_context>` and tool results data (`agent.py:55`). The brief carries its own "not
  instructions" line. The system prompt is part of the evaluated baseline, so this audit leaves it
  unchanged.

**E8 (low).** `spawn_worker` sends the worker's stderr to the same log (`goals.py:826`), so an
unexpected exception leaves a traceback there. No model text was seen in it, and the file is 0600
in a 0700 folder.

**E9 (info).** Each `gh` request is not logged on its own. `alter.context` records that GitHub
was a source of that goal.

## 4. Recovery (each verified with real commands in a throwaway `PCIM_HOME`)

Setup: `pcim init --user audit --providers mock`, then `alter init --provider mock` in two
throwaway git projects. `HOME` was a throwaway folder too.

### Undo of writes

**Before the fixes:**
- **Plain undo works.** The agent proposed `write notes.txt`, approved with `alter approve`. Then
  `TaskQueue.undo`, which `POST /v1/tasks/{id}/undo` calls, restored `hello`. A second undo was
  refused.
- **R3 (high).** The agent wrote `notes.txt`, the person appended a line, then undo ran. The file
  was back to `hello` and the person's line was gone. No warning.
- **R4 (high).** For a latin-1 file (`caf\xe9 …`), the agent replaced it and undo ran. The file's
  sha1 went from `244b…` to `2fc9…`, and `\xe9` became `ef bf bd` (U+FFFD).

**After the fixes** (`cb70e30`, `9af798a`):
- Undo refuses, and changes nothing, when the file differs from what the write left ("undo any
  later write to it first").
- `write_file` refuses to replace a file that is not UTF-8 text. The latin-1 file's hash was
  unchanged, `ec64…` before and after.
- Undo writes the kept text back as exact UTF-8 bytes, so a BOM and CRLF line ends come back.

**R5 (medium, open).** No CLI can undo. `alter undo task_…` is rejected as an unknown command.
`ci undo task_…` fell through to `cmd_ask` and went to the model as a question, exiting 0. Only the
dashboard's "Undo write" button and `POST /v1/tasks/{id}/undo` (`api.py:525`) can undo.
Remediation: `alter undo TASK` and `ci undo TASK`, calling `TaskQueue.undo`. Until then, use git in
the project.

### Goal interruption and resume

- A goal ran in the background with `run_tests` = `sleep 25` under an autonomy grant. The worker
  was killed with `kill -9` while the test ran.
- `alter goals list` and `alter status` showed `interrupted`. `alter doctor` warned:
  `interrupted: goal_… (alter goals resume <id>)`. The worker log was empty.
- `alter goals resume <id>` continued in a new session, with a fresh brief. The tests ran, and the
  goal ended `done`, exit 0. Nothing was lost.
- **R6 (medium, fixed in the recovery follow-up).** The audit initially observed:
  - The killed worker's test process kept running. `run_tests` starts it in its own session
    (`tools.py:335`).
  - The task stayed `running` in `alter tasks` after the resume, and still does.
  - The resume started a second `sleep 25` while the first was alive. A real suite writing shared
    files could give a false result.

  The test supervisor now terminates its test process group if the caller exits. It holds a
  project lock through cleanup, which prevents a second test from starting. Resume records the
  old attempt as `interrupted` before starting work. The regression kills a real background
  worker while its test holds a file lock, then verifies that resume can run the tests and
  finish the goal.
- Ctrl-C, the end of the window, a pause, and "waiting on approval" were not re-tested here.
  `tests/test_alter_goals.py` and `test_alter_review.py` cover them.

### `$PCIM_HOME` backup and restore

- **An online backup works.**
  - Backup: `sqlite3 "$PCIM_HOME/pcim.db" ".backup '<dir>/pcim.db'"`, plus `master.key`, `token`
    and `goals/`.
  - Restored into an empty folder: `alter goals list` showed both goals, `alter memory` showed 3
    items, and `pcim audit` reported `"chain_ok": true`.
  - Projects are found by their absolute folder, so restore on the same paths.
- **R7 (medium).**
  - A plain `cp pcim.db`, taken while another process had the store open, held 3 memory items. The
    live store had 4: the newest was still in `pcim.db-wal`.
  - The `.backup` file was created 0644.
  - A backup that holds `master.key` next to `pcim.db` is readable by anyone who has the backup.

  **Procedure until there is a `pcim backup`:**

  ```bash
  alter goals list            # nothing running; pause or cancel what is (alter pause <id>)
  umask 077
  B=~/pcim-backup-$(date +%Y%m%d); mkdir -p "$B"
  sqlite3 "$PCIM_HOME/pcim.db" ".backup '$B/pcim.db'"      # consistent even with the WAL in use
  cp -p "$PCIM_HOME/token" "$B/" && cp -Rp "$PCIM_HOME/goals" "$B/"
  # master.key: keep it apart from the backup (a password manager), never in the same archive
  # restore: copy back into an empty $PCIM_HOME (chmod 700), master.key included, then
  alter status && pcim audit | grep chain_ok
  ```

### Rollback to 0.2.0rc2

- The rc2 source (`9a24b42`, identical in tree to `main`'s `c7cbb54`) was run against the restored
  0.3.0a1 store.
- These all worked: `ci --version` (`ci 0.2.0rc2`) and `ci status`; `pcim memory`, which listed
  ALTER's outcome episodes with the rest; `pcim audit` (`chain_ok: true`); `ci audit`, which listed
  the `goal.*` entries; and a `pcim remember`.
- Rolling forward to 0.3.0a1: `alter goals list`, `alter memory` (4 items, including rc2's) and
  `pcim audit` all worked.
- The only schema change is two new tables (`goals`, `goal_events`), which rc2 ignores.
- **R8 (low).** Stop goal workers before rolling back: rc2 has no `alter` to pause them. Goals stay
  in the database, unused. `ROLLBACK.md` still describes only rc2, so it needs a 0.3.0a1 section.

### A damaged goals table

**R2 (high, fixed in `d88fde5`).** One byte of one goal's ciphertext was flipped.

- **Before:**
  - `alter goals list`, `goals list --all`, `goals show <another goal>`, `status`, `doctor` and
    `weekly` all exited 1 with an `InvalidTag` traceback.
  - Starting a goal in that project failed the same way.
  - `alter goals forget` could not resolve the id.
  - The other project's `alter goals list` was unaffected.
- **After:**
  - `alter goals list` shows the good goal, and the damaged one as `unreadable`.
  - `alter status` says `1 unreadable`, and `alter doctor` warns (`goal_records`).
  - New goals start.
  - `alter goals forget <id>` removes the damaged row.

  The same applies to a damaged wrapped key. A missing table is recreated empty by
  `CREATE TABLE IF NOT EXISTS`. A corrupt SQLite file (not one row) is out of scope: restore it from
  the backup.

### A lost store key

**R1 (high, fixed in `531ce55`).** `master.key` was moved away, as after a partial restore.

- **Before:**
  - The first `alter status` made a new `master.key` (fingerprint `2db7…` became `57bf…`) and then
    failed with `InvalidTag`.
  - `pcim remember` **succeeded**, exit 0, under the new key.
  - The original key was put back. `alter memory` then failed with `InvalidTag` for good, because
    one record was wrapped by a key that no longer existed.
- **After:**
  - `alter status` exits 2 with "…master.key is missing, but …pcim.db holds records encrypted under
    it. Restore master.key from your backup…". `pcim remember` exits 1.
  - No key file is created.
  - `alter doctor` shows `[FAIL] master_key`.
  - Putting the key back restores everything.
- **R10 (info).** A key lost with no backup leaves every sealed record unreadable, by design
  (envelope encryption, `crypto.py`). Back up `master.key` separately, or use `PCIM_MASTER_KEY`
  from a secret store.

**R9 (low).** `PersonalCI` creates `$PCIM_HOME` with the default mode, 0755 (`engine.py:194`). The
files inside are 0600 and `goals/` is 0700. Agent sessions leave `.session-<id>.lock` files that
are never removed.

## 5. Fixes in this branch

Each fix has its own commit and regression test (`tests/test_safety_audit.py`). Each test fails on
`31504c6` and passes after its commit.

| commit | finding | change |
|---|---|---|
| `531ce55` | R1 | `KeyRing.load_or_create(..., store=)` refuses to make a key for a non-empty store (`MasterKeyMissing`). `pcim`, `ci` and `alter` print it and exit; `doctor` fails `master_key` |
| `d88fde5` | R2 | `GoalUnreadable`. `GoalStore.list` skips and names damaged rows, `resolve` reads ids from the table, and `alter goals forget` works by id. `list`, `status` and `doctor` show the damaged rows |
| `cb70e30` | R3 | `TaskQueue.undo` checks the file against `sha256_after` and refuses on a mismatch |
| `9af798a` | R4 | `write_file` refuses to replace a file that is not UTF-8 text; the backup is exact; undo writes UTF-8 bytes |
| `1204055` | E1 | `thirdeye.Brief.__post_init__` redacts credential-shaped text in every piece and lists the redaction |
| `4d87279` | H8, docs | `SECURITY_WHAT_LEAVES.md` §5 to §8; `HISTORY_REWRITE.md` corrected to public |

## 6. Decisions for the owner

1. **Rewrite `main`'s history, or not (H1, H2, H3).** Removing the author email and the three
   files from history means rewriting all 189 public commits and force-pushing `main`, plus every
   public branch you keep. Costs:
   - every commit id changes;
   - `refs/pull/1-9/head` keep the old commits, and only GitHub Support can purge them;
   - clones keep copies, and the email is already in GitHub's public event archive.

   **Recommendation: no rewrite.** Instead:
   - (a) one forward commit that redacts the 3 files of H2 and the answer in H3;
   - (b) `git config user.email` set to your GitHub noreply address;
   - (c) "Keep my email addresses private" and "Block command line pushes that expose my email" in
     GitHub's email settings. The API could not check these: the `gh` token lacks the `user` scope.
2. **GitHub settings (H9).** Turn on secret scanning and push protection. Protect `main`: no
   force-pushes, require the `tests` checks. Then any rewrite in decision 1 needs the protection
   lifted on purpose.
3. **Local backup refs (H7).** Keep or delete `refs/backup/pre-rewrite-20261117-185237/*`. Bundles
   exist in `~/projects/ci-backups/`. Deleting the refs and running `git gc --prune=now` removes
   the last local copies of the pre-rewrite identifiers. Never push with `--mirror` or `--all`.
4. **This branch.** It is not pushed. Merge it into `feat/alter-alpha` (PR #9), or open its own
   PR. All its commits carry the same author email as H1, because that is git's configured
   `user.email`. If you switch to the noreply address first, re-author them before pushing. That is
   a rewrite of this unpushed branch only, which this audit did not do.
5. **Open items to schedule:** E2, E3 and E10 (the egress and autonomy design), R5 (an undo
   command), R7 (`pcim backup`), then the low items.
