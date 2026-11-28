#!/usr/bin/env python3
"""Seed a throwaway PCIM_HOME with demonstration data for MAYANK (/mayank), and optionally keep it live.

Everything goes through ALTER's real objects (memory, projects, goals, the task queue with real
execution in a scratch folder, the claim ledger, growth probes, runs), so the page reads the same
structures it reads for real work. Every record says "[demo]", and the page labels it as
demonstration data. Two kinds of record cannot be made without a model call and are written
directly, still marked "[demo]" and attributed to "demo (no model call)": challenge records and
one compiled plan.

    python scripts/mayank_demo.py --home /tmp/mayank-demo            # seed once
    python scripts/mayank_demo.py --home /tmp/mayank-demo --live     # then keep ALTER busy
    PCIM_HOME=/tmp/mayank-demo pcim serve                            # http://127.0.0.1:8765/mayank

It refuses any home that holds a store it did not make, and never touches ~/.pcim.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from pcim import challenge, intent_compiler  # noqa: E402
from pcim.cli import write_token  # noqa: E402
from pcim.engine import PersonalCI  # noqa: E402
from pcim.exchange import ClaimLedger  # noqa: E402
from pcim.goals import GoalStore  # noqa: E402
from pcim.governor import Governor  # noqa: E402
from pcim.tasks import TaskQueue  # noqa: E402
from pcim.types import new_id, utcnow  # noqa: E402
from pcim.workspace import Workspace  # noqa: E402

D = "[demo] "
MARKER = ".mayank-demo"

MEMORY = [
    ("fact", "Derivations stick better than intuitions for me: I need to see why, then how", None, None),
    ("preference", "Explanations should start from the smallest example that breaks", None, None),
    ("fact", "Currently studying eigen-decomposition as the road to PCA", None, None),
    ("fact", "Spaced review works for me only when the check is unassisted", None, None),
    ("fact", "An urban heat island is a city area measurably warmer than the land around it", None, None),
    ("fact", "Street temperature data so far: 40 loggers on lamp posts, every 10 minutes, since 2021", None, None),
    ("fact", "Canopy cover comes from aerial imagery, as the share of each block shaded at noon", None, None),
    ("fact", "Comparing many neighbourhoods at once raises the chance of a false positive", None, None),
    ("fact", "Asphalt can be 20 C hotter than shaded grass on a clear afternoon", None, None),
    ("fact", "The 2023 heatwave lasted nine days with nights above 24 C", None, None),
    ("fact", "Fourier series: any square-integrable periodic function is a sum of sines and cosines", None, None),
    ("fact", "Bayes: the posterior is proportional to likelihood times prior", None, None),
    ("fact", "Gradient descent converges for convex L-smooth functions with step size below 2/L", None, None),
    ("fact", "Hamiltonian mechanics: Hamilton's equations are first-order in phase space", None, None),
    ("fact", "Entropy of a fair coin is one bit", None, None),
    ("fact", "Big-O of heap insertion is log n", None, None),
    ("fact", "Black-Scholes assumes constant volatility, which the volatility smile contradicts", None, None),
    ("fact", "Diversification lowers portfolio variance unless the assets are perfectly correlated", None, None),
    ("preference", "Wants to be challenged when a conclusion rests on one summer of data", None, None),
    ("fact", "Study hour", "study_hour", "6 am"),
    ("fact", "Study hour", "study_hour", "9 pm"),
    ("fact", "View on night-time cooling", "night_cooling_view", "trees always help"),
    ("fact", "Primary language for research code", "research_language", "Python"),
    ("fact", "Linear algebra textbook", "la_textbook", "Strang, Introduction to Linear Algebra"),
    ("fact", "Linear algebra textbook", "la_textbook", "Axler, Linear Algebra Done Right"),
]

SKILLS = {  # path: unassisted attempts (True correct), then assisted ones (logged, never counted)
    "mathematics / linear algebra / vectors and spans": ([1, 1, 1, 1, 1, 1], []),
    "mathematics / linear algebra / matrix multiplication": ([1, 1, 0, 1, 1, 1, 1], []),
    "mathematics / linear algebra / determinants": ([1, 0, 1, 1, 1], [1]),
    "mathematics / linear algebra / eigenvalues": ([0, 1, 0, 1, 1], [1, 1]),
    "mathematics / linear algebra / singular value decomposition": ([0, 0, 1], [1, 1, 1]),
    "mathematics / linear algebra / principal component analysis": ([0, 1], [1, 1]),
    "mathematics / probability / bayes rule": ([1, 1, 1, 1, 1, 1], []),
    "mathematics / probability / conditional expectation": ([1, 0, 1, 1], []),
    "mathematics / probability / martingales": ([0, 0, 1], [1]),
    "mathematics / calculus / gradients": ([1, 1, 1, 1, 1], []),
    "mathematics / calculus / convexity": ([1, 1, 0, 1], []),
    "mathematics / analysis / fourier series": ([0, 1, 0], [1]),
    "physics / mechanics / lagrangian": ([1, 0, 1], []),
    "physics / mechanics / hamiltonian": ([0, 0], [1, 1]),
    "physics / thermodynamics / entropy": ([1, 1, 1, 0, 1], []),
    "computer science / algorithms / heaps": ([1, 1, 1, 1], []),
    "computer science / algorithms / dynamic programming": ([1, 0, 0, 1, 1], [1]),
    "computer science / systems / concurrency": ([0, 1], []),
    "finance / portfolio theory / mean-variance": ([1, 1, 1, 1], []),
    "finance / portfolio theory / covariance shrinkage": ([1, 0, 1], [1]),
    "statistics / inference / hypothesis tests": ([1, 1, 0, 1], []),
    "statistics / inference / difference in differences": ([0, 1, 0], [1, 1]),
    "finance / derivatives / black-scholes": ([1, 0], [1]),
    "finance / risk / value at risk": ([0], [1]),
}

CHALLENGES = [
    {"claim": "More tree canopy reliably makes a street cooler, day and night", "status": "open", "p": 0.34,
     "assumptions": [("Canopy is the cause, not the richer, greener neighbourhoods that have it", "high",
                      "canopy and income rise together in this city"),
                     ("Daytime shade and night-time cooling work the same way", "high", "none given"),
                     ("Lamp-post loggers measure what a pedestrian feels", "medium", "none given")],
     "counter": ["Dense canopies can trap heat at night by blocking longwave radiation to the sky",
                 "Two of the shadiest blocks were among the warmest at 3 am during the 2023 heatwave",
                 "Wind through open streets cooled some unshaded blocks faster after sunset"],
     "objection": "Trees cool streets by day through shade, but at night the effect can reverse; one rule for both is wrong",
     "experiment": "Compare blocks matched on income and building height, before and after the 2022 planting, "
                   "separately for afternoons and nights",
     "if_true": "plant for both day and night", "if_false": "plant for shade, and keep night air moving",
     "cost": "two weeks, existing logger data", "change": "matched blocks that cool at night as well as by day"},
    {"claim": "Eigen-decomposition is the only way to compute PCA", "status": "failed", "p": 0.08,
     "assumptions": [("PCA is defined through the covariance matrix", "medium", "standard textbook definition"),
                     ("Numerical methods do not change the result", "low", "none given")],
     "counter": ["The SVD of the centred data matrix gives the same components without forming the covariance",
                 "Iterative methods (power iteration, randomized SVD) find the top components alone"],
     "objection": "SVD of the data matrix is the usual way, and it is more stable",
     "experiment": "Compute PCA of a 1000x50 matrix both ways and compare the components",
     "if_true": "only eigen-decomposition works", "if_false": "SVD is equivalent",
     "cost": "ten minutes", "change": "two methods that agree to numerical precision"},
    {"claim": "Spaced review beats rereading for retaining linear algebra", "status": "held", "p": 0.81,
     "assumptions": [("Retention is measured by unassisted checks", "high", "growth probes count only those"),
                     ("The material is the same in both arms", "medium", "same chapters")],
     "counter": ["Rereading can win when the first exposure was too weak to recall anything"],
     "objection": "The effect could come from the extra time spent, not the spacing",
     "experiment": "Alternate chapters between spaced checks and rereading for four weeks; compare unassisted scores",
     "if_true": "keep spaced review", "if_false": "return to rereading",
     "cost": "four weeks", "change": "no difference in unassisted scores"},
]


def refuse(msg: str) -> None:
    sys.exit(f"mayank_demo: {msg}")


def open_home(home: Path, fresh: bool):
    home = home.expanduser().resolve()
    if home == (Path.home() / ".pcim").resolve():
        refuse("will not use ~/.pcim; give a throwaway folder")
    if fresh:
        if home.exists() and any(home.iterdir()) and not (home / MARKER).exists():
            refuse(f"{home} is not empty and was not made by this script")
        home.mkdir(parents=True, exist_ok=True)
        (home / MARKER).write_text("made by scripts/mayank_demo.py: demonstration data only\n")
    elif not (home / MARKER).exists():
        refuse(f"{home} was not made by this script; seed it first")
    ci = PersonalCI(home)
    return home, ci


def seed(home: Path, ci: PersonalCI) -> dict:
    out = ci.onboard("demo", "Demo", ("mock",))
    write_token(home, out["api_token"])
    uid = out["user_id"] if "user_id" in out else "demo"

    for kind, text, key, value in MEMORY:
        kw = {"key": key, "value": value} if key else {}
        ci.remember(uid, D + (f"{text}: {value}" if key else text), kind, **kw)

    ws = Workspace(ci)
    ledger = ClaimLedger(ws)
    queue = TaskQueue(ws, Governor(ws), claims=ledger)
    goals = GoalStore(ci)
    root = home / "workspace"
    (root / "notes").mkdir(parents=True, exist_ok=True)
    (root / "check_notes.py").write_text("import pathlib, sys\nsys.exit(0 if any(pathlib.Path('notes').glob('*.md')) else 1)\n")

    research = ws.create_project(uid, D + "Street trees and heat", objective=D + "Find out whether tree canopy "
                                 "cools streets, by day and by night, with matched comparisons", providers=["mock"])
    learning = ws.create_project(uid, D + "Linear algebra, properly", objective=D + "Reach unassisted mastery of "
                                 "eigenvalues, SVD and PCA", providers=["mock"])
    ws.authorize_workspace(uid, research.id, str(root), run_argv=[sys.executable, "check_notes.py"], allow_write=True)
    for kind, text in [("decision", "Match blocks on income and building height before comparing"),
                       ("decision", "Treat afternoons and nights as separate questions"),
                       ("next_step", "Rebuild the canopy measure from the 2024 aerial survey"),
                       ("next_step", "Write up the 2023 heatwave as a case study")]:
        ws.add_note(uid, research.id, kind, D + text)
    for kind, text in [("decision", "Axler first, Strang for computation"),
                       ("next_step", "Prove that symmetric matrices have real eigenvalues, unassisted")]:
        ws.add_note(uid, learning.id, kind, D + text)

    tasks = []
    for objective, files in [
        ("Draft the canopy measure note", ["notes/canopy-measure.md", "notes/data-sources.md"]),
        ("Write the 2023 heatwave case study", ["notes/heatwave-2023.md"]),
    ]:
        g = goals.create(uid, research.id, D + objective)
        session = new_id("ses")
        g.payload["sessions"].append(session)
        g.payload["context"] = {"text": "", "cites": [D + "remembered: earlier goal in this project",
                                                      D + "vault: Cities / Heat islands.md",
                                                      D + "git: notes/ (3 files)"], "withheld": []}
        goals.save(g)
        for path in files:
            t = queue.propose(uid, research.id, "write_file", {"path": path, "content": f"# {path}\n\n[demo] draft\n",
                                                                "reason": D + f"write {path}"}, session_id=session)
            tasks.append(queue.approve(uid, t.id))
        t = queue.propose(uid, research.id, "run_tests", {"reason": D + "check the notes exist"}, session_id=session)
        tasks.append(queue.approve(uid, t.id))
    pending = queue.propose(uid, research.id, "write_file", {"path": "notes/next.md", "content": "# next\n",
                                                             "reason": D + "draft the next section (waiting for you)"})

    ledger.add(uid, research.id, D + "Blocks above 30% canopy were cooler on 2023 heatwave afternoons",
               evidence_refs=[t.id for t in tasks[:2]], uncertainty="one episode; not a test")
    ledger.add(uid, research.id, D + "The notes folder holds a draft of every planned note",
               evidence_refs=[tasks[2].id] if len(tasks) > 2 else [])

    for name, (unassisted, assisted) in SKILLS.items():
        for ok in unassisted:
            ci.probe(uid, D + name, bool(ok), assisted=False, source="demo")
        for ok in assisted:
            ci.probe(uid, D + name, bool(ok), assisted=True, source="demo")

    version, log = challenge._log(ci, uid)
    recs = []
    for c in CHALLENGES:
        rid = new_id("ch")
        recs.append({"id": rid, "ts": utcnow().isoformat(), "interaction": None, "claim": D + c["claim"],
                     "parsed": True, "served_by": "demo (no model call)", "status": c["status"],
                     "resolved_at": utcnow().isoformat() if c["status"] != "open" else None, "note": None,
                     "challenge": {"claim": D + c["claim"],
                                   "assumptions": [{"text": D + a, "load_bearing": lb, "evidence_given": ev}
                                                   for a, lb, ev in c["assumptions"]],
                                   "counterexamples": [D + x for x in c["counter"]],
                                   "strongest_objection": D + c["objection"],
                                   "uncertainty": {"p_claim_holds": c["p"], "drivers": [],
                                                   "label": "the model's estimate, not a measurement"},
                                   "falsification": {"experiment": D + c["experiment"], "if_true": c["if_true"],
                                                     "if_false": c["if_false"], "cost": c["cost"]},
                                   "what_would_change_my_mind": c["change"], "verdict": "", "complete": True}})
    # a revision: the person brought evidence and the estimate moved
    first = recs[0]
    rev = dict(first, id=new_id("ch"), revises=first["id"], status="open",
               evidence_offered=D + "Matched blocks planted in 2022 were cooler on 3 of 4 heatwave nights since")
    rev["challenge"] = dict(first["challenge"], uncertainty={"p_claim_holds": 0.46, "drivers": [
        "new out-of-sample evidence raises the estimate", "four episodes are still few"],
        "label": "the model's estimate, not a measurement"})
    recs.append(rev)
    challenge._write(ci, uid, version, log + recs)
    ci.audit.append(uid, "challenge.new", challenge=rev["id"], interaction="demo", parsed=True)

    pversion, plog = intent_compiler._log(ci, uid)
    steps = [("s1", "Collect the logger readings since 2021", [], "o1"),
             ("s2", "Measure canopy per block from the aerial survey", [], "o1"),
             ("s3", "Match blocks on income and building height", ["s1", "s2"], "o1"),
             ("s4", "Compare matched blocks before and after planting, day and night", ["s3"], "o2"),
             ("s5", "Write the result with its uncertainty", ["s4"], "o3")]
    plog.append({"id": new_id("plan"), "ts": utcnow().isoformat(), "text": D + "do street trees cool streets?",
                 "answers": {}, "parent": None, "interaction": None, "served_by": "demo (no model call)",
                 "reask_of": None, "project_id": research.id, "context_items": [], "execution_mode": "contract",
                 "status": "approved", "gate": None, "error": None, "steps_state": {}, "outcomes_state": {},
                 "spec": {"goal": D + "Test whether tree canopy cools streets, day and night",
                          "outcomes": [{"id": "o1", "text": D + "A matched set of blocks with canopy and temperature", "kind": "deliverable", "quote": "", "done_when": "dataset file exists"},
                                       {"id": "o2", "text": D + "A matched before-and-after comparison", "kind": "analysis",
                                        "quote": "", "done_when": "test reported with p-value"},
                                       {"id": "o3", "text": D + "A written result", "kind": "deliverable",
                                        "quote": "", "done_when": "note written"}],
                          "steps": [{"id": s, "action": D + a, "kind": "work", "depends_on": d, "serves": [o],
                                     "role": "fulfilling"} for s, a, d, o in steps]}})
    intent_compiler._write(ci, uid, pversion, plog)

    try:
        from pcim.run_service import RunService
        RunService(ci).start(uid, learning.id, D + "Prove the spectral theorem for real symmetric matrices", {"mock"},
                             work=[{"id": "w1", "role": "researcher", "objective": D + "Collect the standard proofs",
                                    "depends_on": [], "artifacts": [], "require_write": False},
                                   {"id": "w2", "role": "writer", "objective": D + "Write the proof in your words",
                                    "depends_on": ["w1"], "artifacts": ["proof.md"]},
                                   {"id": "w3", "role": "verifier", "objective": D + "Check every step",
                                    "depends_on": ["w2"], "artifacts": [], "require_write": False}])
    except Exception as e:  # runs need the integrated build; the rest of the demo stands without them
        print(f"(no demo run: {type(e).__name__}: {e})")
    return {"uid": uid, "research": research.id, "pending": pending.id, "root": str(root)}


class Busy:
    """One governed action at a time in a seeded home: a real task in the scratch folder (some left
    for you to approve), a new memory, or a new unassisted skill check. Each lands in the audit chain."""

    def __init__(self, home: Path, ci: PersonalCI, seed: int | None = None):
        self.ci = ci
        self.uid = ci.user_for_token((home / "token").read_text().strip())
        self.ws = Workspace(ci)
        ledger = ClaimLedger(self.ws)
        self.queue = TaskQueue(self.ws, Governor(self.ws), claims=ledger)
        # the demo project ALTER may write in (found by its workspace, not its name, so older seeds work too)
        self.project = next((p for p in self.ws.list_projects(self.uid)
                             if (self.ws.briefing(self.uid, p.id).get("workspace") or {}).get("can_write")), None)
        if self.project is None:
            refuse("no demo project with a writable workspace here; seed a fresh home")
        self.rnd, self.n = random.Random(seed), 0

    def step(self) -> str:
        ci, uid, queue, pid, rnd = self.ci, self.uid, self.queue, self.project.id, self.rnd
        self.n += 1
        n, roll = self.n, rnd.random()
        if roll < 0.5:
            path = f"notes/live-{n:03d}.md"
            t = queue.propose(uid, pid, "write_file", {"path": path, "content": f"# {n}\n[demo] live\n",
                                                       "reason": D + f"live draft {n}"})
            if rnd.random() < 0.6:
                queue.approve(uid, t.id)
                return f"wrote {path}"
            return f"{path}: waiting for your approval in MAYANK"
        if roll < 0.7:
            r = queue.approve(uid, queue.propose(uid, pid, "run_tests", {"reason": D + "check the notes"}).id)
            return f"tests: {r.status} {r.reason or ''}".rstrip()
        if roll < 0.85:
            name = rnd.choice(list(SKILLS))
            ok = rnd.random() < 0.6
            ci.probe(uid, D + name, ok, assisted=False, source="demo")
            return f"unassisted check: {name} {'right' if ok else 'wrong'}"
        fact = rnd.choice(["Noted a new source on heat islands", "Linked eigenvalues to stability of dynamics",
                           "Found a counterexample worth testing", "Re-derived the normal equations"])
        ci.remember(uid, D + f"{fact} ({n})", "fact")
        return f"remembered: {fact}"


def live(home: Path, ci: PersonalCI, interval: float) -> None:
    busy = Busy(home, ci)
    print(f"live: one governed action every {interval:.0f}s (Ctrl-C stops)")
    while True:
        print("  " + busy.step())
        time.sleep(interval)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--home", required=True, type=Path, help="a throwaway PCIM_HOME (never ~/.pcim)")
    ap.add_argument("--live", action="store_true", help="after seeding (or on an already seeded home), keep going")
    ap.add_argument("--interval", type=float, default=4.0)
    a = ap.parse_args(argv)
    seeded = (a.home.expanduser() / "token").exists() and (a.home.expanduser() / MARKER).exists()
    home, ci = open_home(a.home, fresh=not seeded)
    if not seeded:
        info = seed(home, ci)
        print(f"seeded {home} with demonstration data (user {info['uid']}); token in {home / 'token'}")
        print(f"serve it:  PCIM_HOME={home} pcim serve   then open http://127.0.0.1:8765/mayank")
    if a.live:
        try:
            live(home, ci, a.interval)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
