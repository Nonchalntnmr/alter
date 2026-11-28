"""MAYANK's field (/v1/mayank/*): every entity is a record ALTER really holds, read through the same
objects as the rest of the API, with the relations ALTER recorded between them; search uses the
store's embedder; the live feed is the audit chain; demonstration records say so; and the page only
acts through ALTER's own routes. No network, no model call (the one model-backed route is mocked)."""
import subprocess
import sys
import threading
import time
from importlib import resources
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pcim import challenge, intent_compiler, mayank
from pcim.api import create_app
from pcim.engine import PersonalCI
from pcim.exchange import ClaimLedger
from pcim.goals import GoalStore
from pcim.governor import Governor
from pcim.tasks import TaskQueue
from pcim.types import new_id, utcnow
from pcim.workspace import Workspace

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def world(tmp_path):
    """A store with one of everything the field reads, made through ALTER's own objects."""
    ci = PersonalCI(tmp_path / "data", seed=0)
    token = ci.onboard("ana", "", ("mock",))["api_token"]
    ci.remember("ana", "Study hour: 6 am", "fact", key="study_hour", value="6 am")
    ci.remember("ana", "Study hour: 9 pm", "fact", key="study_hour", value="9 pm")
    ci.remember("ana", "Eigenvalues of a symmetric matrix are real", "fact")
    ws = Workspace(ci)
    ledger = ClaimLedger(ws)
    queue = TaskQueue(ws, Governor(ws), claims=ledger)
    root = tmp_path / "proj"
    root.mkdir()
    p = ws.create_project("ana", "Heat study", objective="test canopy cooling", providers=["mock"])
    ws.authorize_workspace("ana", p.id, str(root), allow_write=True)
    ws.add_note("ana", p.id, "decision", "Use monthly data")
    goals = GoalStore(ci)
    g = goals.create("ana", p.id, "Draft the note")
    g.payload["sessions"].append("ses_1")
    goals.save(g)
    t = queue.approve("ana", queue.propose("ana", p.id, "write_file", {"path": "a.md", "content": "x", "reason": "draft"},
                                           session_id="ses_1").id)
    claim = ledger.add("ana", p.id, "The note exists", evidence_refs=[t.id])
    version, log = challenge._log(ci, "ana")
    ch = {"id": new_id("ch"), "ts": utcnow().isoformat(), "interaction": None, "claim": "Canopy cools streets at night",
          "parsed": True, "served_by": "test", "status": "open", "resolved_at": None, "note": None,
          "challenge": {"claim": "Canopy cools streets at night",
                        "assumptions": [{"text": "It can be measured in advance", "load_bearing": "high", "evidence_given": "none"}],
                        "counterexamples": ["2009 was a market rebound"], "strongest_objection": "Beta explains it",
                        "uncertainty": {"p_claim_holds": 0.3, "drivers": [], "label": "the model's estimate, not a measurement"},
                        "falsification": {"experiment": "Out-of-sample lead test", "if_true": "", "if_false": "", "cost": ""},
                        "what_would_change_my_mind": "a lead of a month", "verdict": "", "complete": True}}
    challenge._write(ci, "ana", version, log + [ch])
    for ok in (True, True, False):
        ci.probe("ana", "mathematics / linear algebra / eigenvalues", ok)
    pv, plog = intent_compiler._log(ci, "ana")
    plog.append({"id": "plan_1", "ts": utcnow().isoformat(), "text": "test it", "project_id": p.id, "status": "approved",
                 "spec": {"goal": "Test canopy cooling", "outcomes": [{"id": "o1", "text": "A result", "kind": "deliverable"}],
                          "steps": [{"id": "s1", "action": "Collect data", "kind": "work", "depends_on": [], "serves": ["o1"]},
                                    {"id": "s2", "action": "Run the test", "kind": "work", "depends_on": ["s1"], "serves": ["o1"]}]}})
    intent_compiler._write(ci, "ana", pv, plog)
    mayank._fields.clear()
    return {"ci": ci, "ws": ws, "queue": queue, "ledger": ledger, "pid": p.id, "goal": g.id, "task": t.id,
            "claim": claim["id"], "challenge": ch["id"], "token": token, "data": tmp_path / "data"}


def build(w):
    return mayank.build(w["ci"], "ana", ws=w["ws"], queue=w["queue"], ledger=w["ledger"])


def rels(f):
    by = {e["id"]: e for e in f["entities"]}
    return {(by[l["a"]]["kind"], l["rel"], by[l["b"]]["kind"]) for l in f["links"]}


def test_every_source_becomes_entities_with_the_relations_alter_recorded(world):
    f = build(world)
    assert all(s["error"] is None for s in f["sources"]), f["sources"]
    for kind in ("memory", "project", "decision", "goal", "task", "claim", "hypothesis", "assumption", "counter",
                 "alternative", "experiment", "skill", "plan", "objective"):
        assert f["kinds"].get(kind), kind
    r = rels(f)
    assert ("memory", "supersedes", "memory") in r          # a keyed correction
    assert ("task", "ran_for", "goal") in r                 # a tool call, attributed to its goal by session
    assert ("task", "evidence", "claim") in r               # the claim's evidence ref
    assert ("assumption", "supports", "hypothesis") in r
    assert ("counter", "contradicts", "hypothesis") in r and ("alternative", "contradicts", "hypothesis") in r
    assert ("experiment", "tests", "hypothesis") in r
    assert ("objective", "depends_on", "objective") in r    # plan steps keep their order
    assert ("goal", "part_of", "project") in r and ("decision", "part_of", "project") in r
    ids = {e["id"] for e in f["entities"]}
    assert all(l["a"] in ids and l["b"] in ids for l in f["links"])
    assert all(-1 <= v <= 1 for e in f["entities"] for v in e["xyz"])
    assert f["demo"] == 0 and not any(e["demo"] for e in f["entities"])
    regions = {e["kind"]: e["region"] for e in f["entities"]}
    assert regions["memory"] == "head" and regions["goal"] == "torso" and regions["task"] == "arms" and regions["claim"] == "legs"


def test_statuses_and_measurements_come_from_the_records(world):
    f = build(world)
    by = {e["id"]: e for e in f["entities"]}
    assert by[world["task"]]["status"] == "succeeded" and by[world["task"]]["meta"]["tool"] == "write_file"
    assert by[world["challenge"]]["meta"]["p_claim_holds"] == 0.3
    assert "not a measurement" in by[world["challenge"]]["meta"]["p_label"]
    skill = by["skill:mathematics / linear algebra / eigenvalues"]
    assert skill["meta"]["path"] == ["mathematics", "linear algebra", "eigenvalues"]
    assert 0 < skill["meta"]["p_mastery"] < 1
    old = [e for e in f["entities"] if e["kind"] == "memory" and "6 am" in e["label"]][0]
    assert old["status"] == "superseded" and old["weight"] < 0.5


def test_one_unreadable_source_is_reported_and_the_rest_still_draws(world, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("damaged")
    monkeypatch.setattr(mayank, "_skills", broken)
    f = build(world)
    bad = [s for s in f["sources"] if s["error"]]
    assert [s["name"] for s in bad] == ["skills"] and "damaged" in bad[0]["error"]
    assert f["kinds"].get("memory") and not f["kinds"].get("skill")


def test_demonstration_records_are_flagged_and_their_marker_hidden(world):
    world["ci"].remember("ana", "[demo] A made-up fact", "fact")
    f = build(world)
    demo = [e for e in f["entities"] if e["demo"]]
    assert len(demo) == 1 and f["demo"] == 1 and demo[0]["label"] == "A made-up fact"


def test_search_finds_by_meaning_and_by_words(world):
    kw = {"ws": world["ws"], "queue": world["queue"], "ledger": world["ledger"]}
    r = mayank.search(world["ci"], "ana", "eigenvalues", **kw)
    by = {e["id"]: e for e in mayank.field(world["ci"], "ana", **kw)["entities"]}
    top = [by[x["id"]]["label"] for x in r["results"][:2]]
    assert any("igenvalue" in t for t in top), top
    assert mayank.search(world["ci"], "ana", "   ", **kw)["results"] == []


def test_the_field_is_rebuilt_when_the_audit_chain_moves(world):
    kw = {"ws": world["ws"], "queue": world["queue"], "ledger": world["ledger"]}
    a = mayank.field(world["ci"], "ana", **kw)
    assert mayank.field(world["ci"], "ana", **kw) is a        # nothing changed: the cached field
    world["ci"].remember("ana", "A new fact", "fact")
    b = mayank.field(world["ci"], "ana", **kw)
    assert b is not a and b["cursor"] > a["cursor"] and len(b["entities"]) == len(a["entities"]) + 1


def test_events_wait_for_the_next_audit_entry_and_name_what_it_touched(world):
    ci = world["ci"]
    cursor = mayank.audit_seq(ci, "ana")
    t0 = time.monotonic()
    assert mayank.events(ci, "ana", cursor, wait=0.3, poll=0.05)["events"] == []
    assert time.monotonic() - t0 >= 0.25
    threading.Timer(0.2, lambda: ci.audit.append("ana", "task.finish", task=world["task"], status="succeeded")).start()
    r = mayank.events(ci, "ana", cursor, wait=5, poll=0.05)
    assert [e["event"] for e in r["events"]] == ["task.finish"] and world["task"] in r["events"][0]["refs"]
    assert r["cursor"] == r["events"][0]["seq"]


def test_routes_need_the_token_and_the_page_and_assets_are_served(world):
    c = TestClient(create_app(PersonalCI(world["data"], seed=0)))
    h = {"Authorization": "Bearer " + world["token"]}
    for path in ("/v1/mayank/field", "/v1/mayank/search?q=x", "/v1/mayank/events?wait=0"):
        assert c.get(path).status_code == 401
    f = c.get("/v1/mayank/field", headers=h).json()
    assert f["kinds"]["goal"] == 1 and f["cursor"] > 0
    assert c.get("/v1/mayank/events", params={"after": f["cursor"], "wait": 0}, headers=h).json()["events"] == []
    page = c.get("/mayank")
    assert page.status_code == 200 and "<title>MAYANK</title>" in page.text and "/static/mayank-app.js" in page.text
    for asset in ("mayank-app.js", "mayank-gl.js", "mayank-forms.js", "mayank-learn.js", "mayank-xr.js"):
        r = c.get(f"/static/{asset}")
        assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    shipped = {p.name for p in resources.files("pcim").joinpath("dashboard").iterdir()}
    assert {"mayank.html", "mayank-app.js", "mayank-gl.js", "mayank-forms.js", "mayank-learn.js", "mayank-xr.js"} <= shipped  # package-data "dashboard/*"


def test_outcomes_and_evidence_go_through_the_challenge_log(world, monkeypatch):
    c = TestClient(create_app(PersonalCI(world["data"], seed=0)))
    h = {"Authorization": "Bearer " + world["token"]}
    r = c.post(f"/v1/mayank/hypotheses/{world['challenge']}/resolve", json={"outcome": "maybe"}, headers=h)
    assert r.status_code == 422
    r = c.post(f"/v1/mayank/hypotheses/{world['challenge']}/resolve", json={"outcome": "inconclusive", "note": "n=4"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "inconclusive"
    assert c.post("/v1/mayank/hypotheses/ch_nope/resolve", json={"outcome": "held"}, headers=h).status_code == 404

    seen = {}

    def fake_challenge(ci, uid, claim, **kw):  # stands in for the routed model call
        seen["claim"] = claim
        rec = {"id": new_id("ch"), "ts": utcnow().isoformat(), "interaction": None, "claim": claim, "parsed": True,
               "challenge": {"uncertainty": {"p_claim_holds": 0.45}}, "served_by": "fake", "status": "open",
               "resolved_at": None, "note": None}
        version, log = challenge._log(ci, uid)
        challenge._write(ci, uid, version, log + [rec])
        return rec
    monkeypatch.setattr(challenge, "challenge", fake_challenge)
    r = c.post(f"/v1/mayank/hypotheses/{world['challenge']}/evidence", json={"evidence": "3 of 4 nights cooler"}, headers=h)
    assert r.status_code == 200, r.text
    assert "3 of 4 nights cooler" in seen["claim"] and "Canopy cools streets at night" in seen["claim"]
    new = [x for x in challenge.records(world["ci"], "ana") if x.get("revises") == world["challenge"]]
    assert len(new) == 1 and new[0]["claim"] == "Canopy cools streets at night"  # the claim, not the prompt
    mayank._fields.clear()
    f = c.get("/v1/mayank/field", headers=h).json()
    by = {e["id"]: e for e in f["entities"]}
    assert any(l["rel"] == "revises" and l["a"] == new[0]["id"] and l["b"] == world["challenge"] for l in f["links"])
    assert by[new[0]["id"]]["meta"]["evidence_offered"] == "3 of 4 nights cooler"
    assert c.post(f"/v1/mayank/hypotheses/{world['challenge']}/evidence", json={"evidence": "  "}, headers=h).status_code == 422


def test_the_demo_seeder_marks_everything_and_refuses_other_homes(tmp_path):
    other = tmp_path / "someone"
    other.mkdir()
    (other / "store.db").write_text("not mine")
    env = {"PCIM_EMBEDDER": "hashing-512", "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "mayank_demo.py"), "--home", str(other)],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode != 0 and "was not made by this script" in r.stderr
    home = tmp_path / "demo"
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "mayank_demo.py"), "--home", str(home)],
                       capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stderr
    ci = PersonalCI(home)
    uid = ci.user_for_token((home / "token").read_text().strip())
    ws = Workspace(ci)
    ledger = ClaimLedger(ws)
    f = mayank.build(ci, uid, ws=ws, queue=TaskQueue(ws, Governor(ws), claims=ledger), ledger=ledger)
    assert f["entities"] and f["demo"] == len(f["entities"])  # every record says it is a demonstration
    assert any(e["kind"] == "task" and e["status"] == "awaiting_approval" for e in f["entities"])


def test_api_request_models_have_unique_names():
    """A second class with an existing name silently replaces the first for every route defined after
    it (MAYANK's first hypothesis model once broke claim resolution that way)."""
    import re
    names = re.findall(r"^class (\w+)\(BaseModel\)", (REPO / "src" / "pcim" / "api.py").read_text(), re.M)
    assert len(names) == len(set(names)), sorted(n for n in set(names) if names.count(n) > 1)


def test_the_static_build_holds_only_demonstration_data_and_no_local_paths(tmp_path):
    out = tmp_path / "site"
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "mayank_static.py"), "--out", str(out), "--steps", "3",
                        "--pause", "0"], capture_output=True, text=True, timeout=300,
                       env={"PCIM_EMBEDDER": "hashing-512", "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    import json
    field = json.loads((out / "data" / "field.json").read_text())
    replay = json.loads((out / "data" / "replay.json").read_text())
    assert field["entities"] and field["demo"] == len(field["entities"])
    assert replay["events"] and all("dt" in e for e in replay["events"])
    page = (out / "index.html").read_text()
    assert "window.MAYANK_STATIC" in page and "/static/mayank-app.js" in page
    assert {p.name for p in (out / "static").iterdir()} == {"mayank-app.js", "mayank-gl.js", "mayank-forms.js", "mayank-learn.js", "mayank-xr.js"}
    for f in out.rglob("*.json"):
        text = f.read_text()
        assert "/Users/" not in text and "/private/" not in text and str(tmp_path) not in text


def test_ask_answers_through_routing_and_says_what_it_drew_on(world):
    c = TestClient(create_app(PersonalCI(world["data"], seed=0)))
    h = {"Authorization": "Bearer " + world["token"]}
    r = c.post("/v1/mayank/ask", json={"question": "What do I know about eigenvalues?"}, headers=h)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["text"] and a["served_by"] and a["interaction_id"]
    assert a["sent"]                                    # the records sent with the question, by id
    mayank._fields.clear()
    f = c.get("/v1/mayank/field", headers=h).json()
    by = {e["id"]: e for e in f["entities"]}
    ex = by[a["interaction_id"]]
    assert ex["kind"] == "exchange"
    sent_links = {l["a"] for l in f["links"] if l["rel"] == "informed" and l["b"] == ex["id"]}
    assert set(a["sent"]) <= sent_links                 # the answer's foundations are in the field
    assert any(l["rel"] == "served_by" and l["a"] == ex["id"] and by[l["b"]]["kind"] == "model" for l in f["links"])
    assert c.post("/v1/mayank/ask", json={"question": ""}, headers=h).status_code == 422


def test_one_assumption_can_be_interrogated_on_its_own(world, monkeypatch):
    seen = {}

    def fake_challenge(ci, uid, claim, **kw):
        seen["claim"] = claim
        rec = {"id": new_id("ch"), "ts": utcnow().isoformat(), "interaction": None, "claim": claim, "parsed": True,
               "challenge": {}, "served_by": "fake", "status": "open", "resolved_at": None, "note": None}
        version, log = challenge._log(ci, uid)
        challenge._write(ci, uid, version, log + [rec])
        return rec
    monkeypatch.setattr(challenge, "challenge", fake_challenge)
    c = TestClient(create_app(PersonalCI(world["data"], seed=0)))
    h = {"Authorization": "Bearer " + world["token"]}
    r = c.post(f"/v1/mayank/hypotheses/{world['challenge']}/assumptions/0/challenge", headers=h)
    assert r.status_code == 200, r.text
    assert seen["claim"] == "It can be measured in advance"
    assert c.post(f"/v1/mayank/hypotheses/{world['challenge']}/assumptions/9/challenge", headers=h).status_code == 404
    mayank._fields.clear()
    f = c.get("/v1/mayank/field", headers=h).json()
    assert any(l["rel"] == "interrogates" and l["a"] == r.json()["id"] and l["b"] == f"{world['challenge']}/a0"
               for l in f["links"])


def test_traces_show_what_ran_and_never_where_on_this_machine(world):
    import json
    f = build(world)
    task = next(e for e in f["entities"] if e["id"] == world["task"])
    assert task["meta"]["trace"] == {"path": "a.md", "created": True, "bytes": 1,
                                     "sha256_after": task["meta"]["trace"]["sha256_after"]}
    assert "abs_path" not in json.dumps(f) and str(world["data"].parent) not in json.dumps(f)
    assert mayank._base("/usr/bin/python3") == "python3" and mayank._base("pytest") == "pytest"


def test_a_contradiction_and_the_review_schedule_reach_the_field(world):
    from pcim.types import Trust
    world["ci"].remember("ana", "Study hour: noon", "fact", key="study_hour", value="noon", trust=Trust.INFERRED, confidence=0.6)
    f = build(world)
    flagged = [e for e in f["entities"] if e["kind"] == "memory" and e["meta"]["conflict"]]
    assert flagged and any(l["rel"] == "contradicts" and flagged[0]["id"] in (l["a"], l["b"]) for l in f["links"])
    skill = next(e for e in f["entities"] if e["kind"] == "skill")
    assert skill["meta"]["review"]["due"] and skill["meta"]["unassisted"]["n"] == 3


def test_starting_a_run_reaches_the_field_and_the_live_feed(world):
    """Runs keep their own event log, not the audit chain: the field and the feed watch both."""
    from pcim.run_service import RunService
    kw = {"ws": world["ws"], "queue": world["queue"], "ledger": world["ledger"], "runs": RunService(world["ci"])}
    a = mayank.field(world["ci"], "ana", **kw)
    run_after = a["run_cursor"]
    run = kw["runs"].start("ana", world["pid"], "Summarise the notes", {"mock"})
    b = mayank.field(world["ci"], "ana", **kw)
    assert b is not a and any(e["id"] == run["id"] for e in b["entities"])
    feed = mayank.events(world["ci"], "ana", b["cursor"], wait=0, run_after=run_after)
    assert any(e["event"] == "run.update" and run["id"] in e["refs"] for e in feed["events"])
    assert feed["run_cursor"] > run_after
