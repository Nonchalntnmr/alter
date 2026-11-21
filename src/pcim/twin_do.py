"""What the Cognitive Twin may learn from `ci do` work: consent and a counts-only summary.

The first, smallest piece of docs/engineering/TWIN_DO_OUTCOMES.md (a proposal until now).

- **Default off.** Nothing here reads a `ci do` record until the person opts in (`include_do`).
  `exclude_do` reverses it. Both are sealed per-user policy state, like the twin's name, and both
  are written to the audit log.
- **Built on records CI already keeps.** It reads the `pcim-do` log that `ci do` writes
  (terminal.DO_STATE). It adds no store and no new outcome flow.
- **Counts only.** `do_summary` returns integers: no request text, no answers, no ids. So a
  weekly snapshot that stores it holds no request text.
- **Removable.** `ci do forget` rewrites or deletes the `pcim-do` log, so a forgotten request is
  absent from the next summary. tests/test_twin_do.py checks this.

Wired into `ci twin include|exclude do` (terminal.cmd_twin) and into `ci weekly`'s review, render and
snapshot (twin.py).
"""

from __future__ import annotations

import datetime as dt
import json

# Same name as terminal.DO_STATE. terminal imports twin, so this module does not import terminal;
# tests/test_twin_do.py checks that the two names match.
DO_STATE = "pcim-do"
SOURCES_STATE = "pcim-twin-sources"
VERDICTS = ("verified", "not_verified", "unchanged")


def _sources(ci, user_id: str) -> dict:
    _, cur = ci._state_json(user_id, SOURCES_STATE, None)
    return cur if isinstance(cur, dict) else {}


def do_included(ci, user_id: str) -> bool:
    """Has the person allowed the twin to read their `ci do` records? False unless they opted in."""
    return bool(_sources(ci, user_id).get("do"))


def _set_do(ci, user_id: str, on: bool) -> bool:
    def update(cur):
        try:
            state = json.loads(cur) if cur else {}
        except (TypeError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        state["do"] = bool(on)
        return json.dumps(state).encode()

    ci.store.update_policy_state(user_id, SOURCES_STATE, update, note="twin sources")
    ci.audit.append(user_id, "twin.include" if on else "twin.exclude", source="do")
    return bool(on)


def include_do(ci, user_id: str) -> bool:
    """Opt in: the twin may count request, route, answers and verification in `ci do` records."""
    return _set_do(ci, user_id, True)


def exclude_do(ci, user_id: str) -> bool:
    """Opt out again. Summaries return None from now on."""
    return _set_do(ci, user_id, False)


def _ts(s) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def do_summary(ci, user_id: str, since: dt.datetime, until: dt.datetime) -> dict | None:
    """Counts from `ci do` records made in [since, until), or None when the person has not opted in.

    Integers only: nothing quotes a request, an answer or an id.
    """
    if not do_included(ci, user_id):
        return None
    _, log = ci._state_json(user_id, DO_STATE, [])
    rows = []
    for r in log if isinstance(log, list) else []:
        t = _ts((r or {}).get("ts"))
        if t is not None and since <= t < until:
            rows.append(r)
    out = {"requests": len(rows),
           "answered_a_question": sum(1 for r in rows if r.get("answers")),
           "waiting_for_answer": sum(1 for r in rows if r.get("status") == "needs_answer"),
           "no_verdict": 0,
           "claimed_done_unverified": 0}
    for v in VERDICTS:
        out[v] = 0
    for r in rows:
        ver = r.get("verification") or {}
        verdict = ver.get("verdict")
        if verdict in VERDICTS:
            out[verdict] += 1
        else:
            out["no_verdict"] += 1
        if ver.get("claim_unverified"):
            out["claimed_done_unverified"] += 1
    return out
