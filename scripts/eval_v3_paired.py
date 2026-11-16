"""V3 paired execution evaluation (experiments/eval_v3/PREREGISTRATION.md, part 2).

    # primary arms, V3 code
    HF_HUB_OFFLINE=1 PCIM_CLI_REQUIRE_ISOLATION=1 PYTHONPATH=~/projects/ci-intent-v3/src \
        python scripts/eval_v3_paired.py run A,B3
    # secondary arm, V2.1+ code, after every A and B3 arm
    HF_HUB_OFFLINE=1 PCIM_CLI_REQUIRE_ISOLATION=1 PYTHONPATH=~/projects/ci-v21-isolated/src \
        python scripts/eval_v3_paired.py run B21
    HF_HUB_OFFLINE=1 PCIM_CLI_REQUIRE_ISOLATION=1 PYTHONPATH=~/projects/ci-intent-v3/src \
        python scripts/eval_v3_paired.py judge
    python scripts/eval_v3_paired.py score

Derived from the frozen paired-v1 harness. The policy, budget, environment, hidden check and judge
rubric are unchanged. What changed:
- three arms, each with its own code check;
- contract-mode approvals;
- the F1–F4 endpoints;
- every call refuses unless identity isolation is verified.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments" / "eval_v3" / "paired"
FROZEN_FILE = ROOT / "experiments" / "eval_v3" / "FROZEN.txt"
PREREG = ROOT / "experiments" / "eval_v3" / "PREREGISTRATION.md"
SET = Path.home() / "projects" / "ci-sealed" / "paired_v2" / "tasks.json"
ARMS = {"A": "v3", "B3": "v3", "B21": "v21"}  # arm -> code label in FROZEN.txt
PY = sys.executable
PROVIDER, MODEL = "claude-cli", "sonnet"
BUDGET = 40
CORRECTION = "That isn't done yet: what I asked for still doesn't work. Please check and fix it."
SECOND_ANSWER = "go with your best reading"
WEIGHTS = {"approvals": 10, "plan_reviews": 30, "clarifications": 45, "corrections": 60}  # seconds; an assumption
IGNORE = {".git", "__pycache__", ".pytest_cache", "test_hidden_check.py"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def frozen() -> dict:
    f = FROZEN_FILE
    out = {}
    for line in f.read_text().splitlines() if f.exists() else []:
        k, _, v = line.partition("=")
        out[k.strip()] = v.split()[0] if v.strip() else ""
    return out


def check(arms: list[str]) -> None:
    f = frozen()
    if not f.get("paired_set_sha256") or sha(SET) != f["paired_set_sha256"]:
        sys.exit("task set hash does not match FROZEN.txt")
    for k, p in (("prereg_sha256", PREREG), ("paired_harness_sha256", Path(__file__))):
        if f.get(k) != sha(p):
            sys.exit(f"{p.name} changed since it was frozen")
    labels = {ARMS[a] for a in arms}
    if len(labels) != 1:
        sys.exit("one process runs arms of one code version (A,B3 or B21)")
    label = labels.pop()
    commit, tree = f.get(f"code_{label}"), Path(os.path.expanduser(f.get(f"tree_{label}", "")))
    if subprocess.run(["git", "-C", str(tree), "diff", "--quiet", commit, "--", "src"]).returncode != 0 or \
            subprocess.run(["git", "-C", str(tree), "status", "--porcelain", "--", "src"], capture_output=True,
                           text=True).stdout.strip():
        sys.exit(f"{tree}/src differs from the frozen code {commit}")
    import pcim
    if Path(pcim.__file__).resolve().parents[1] != (tree / "src").resolve():
        sys.exit(f"pcim imports from {pcim.__file__}, not {tree}/src; set PYTHONPATH")
    if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("PCIM_CLI_REQUIRE_ISOLATION") != "1":
        sys.exit("set HF_HUB_OFFLINE=1 and PCIM_CLI_REQUIRE_ISOLATION=1")
    from pcim.adapters.claude_cli import ClaudeCLIAdapter
    iso = ClaudeCLIAdapter(MODEL, require_isolation=True).identity_isolation()
    if iso["status"] != "verified":
        sys.exit(f"identity isolation is {iso['status']}: {iso['why']}")


def tasks() -> list[dict]:
    rows = json.loads(SET.read_text())
    need = {"id", "category", "request", "files", "hidden_test", "allowed_write_paths", "clarification_answer",
            "required_external_actions"}
    for r in rows:
        if not need <= set(r):
            sys.exit(f"task {r.get('id')} lacks {sorted(need - set(r))}")
    return rows


# ---------------------------------------------------------------------- environment
def snapshot(repo: Path) -> dict[str, str]:
    out = {}
    for p in repo.rglob("*"):
        if p.is_file() and not (set(p.relative_to(repo).parts) & IGNORE):
            out[str(p.relative_to(repo))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def materialize(task: dict, repo: Path) -> dict[str, str]:
    for rel, content in task["files"].items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    g = ["git", "-c", "user.name=eval", "-c", "user.email=eval@localhost", "-C", str(repo)]
    subprocess.run(g[:5] + ["init", "-q", str(repo)], check=True)
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-qm", "start"], check=True)
    return snapshot(repo)


def hidden_passes(task: dict, repo: Path) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="ci-hidden-") as d:
        cp = Path(d) / "repo"
        shutil.copytree(repo, cp, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        (cp / "test_hidden_check.py").write_text(task["hidden_test"])
        try:
            r = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_hidden_check.py"], cwd=cp,
                               capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return False, "hidden check timed out"
        return r.returncode == 0, (r.stdout + r.stderr)[-600:]


class Counted:
    """The agent's model adapter, counting calls against the budget and summing per-call cost."""

    def __init__(self, inner, meter: dict):
        self.inner, self.meter = inner, meter

    def agent_step(self, *a, **k):
        if self.meter["calls"] >= BUDGET:
            self.meter["budget_hit"] = True
            raise RuntimeError(f"budget of {BUDGET} model calls reached")
        self.meter["calls"] += 1
        r = self.inner.agent_step(*a, **k)
        if r.cost_usd is None:
            self.meter["cost_unknown"] += 1
        else:
            self.meter["cost_usd"] += r.cost_usd
        return r

    def __getattr__(self, n):
        return getattr(self.inner, n)


def setup(task: dict, d: Path):
    from pcim.adapters.claude_cli import ClaudeCLIAdapter
    from pcim.agent import Agent
    from pcim.engine import PersonalCI
    from pcim.governor import Governor
    from pcim.tasks import TaskQueue
    from pcim.workspace import Workspace
    repo = d / "repo"
    repo.mkdir()
    start = materialize(task, repo)
    ci = PersonalCI(d / "data", seed=0)
    ci.onboard("u", "Person", ("claude-cli",))
    ws = Workspace(ci)
    p = ws.create_project("u", "Task")
    ws.authorize_workspace("u", p.id, str(repo), run_argv=[PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
                           allow_write=True)
    agent = Agent(ci, ws, TaskQueue(ws, Governor(ws)))
    meter = {"calls": 0, "cost_usd": 0.0, "cost_unknown": 0, "budget_hit": False}
    agent.set_adapter(PROVIDER, MODEL, Counted(ClaudeCLIAdapter(MODEL), meter))
    return ci, ws, agent, p, repo, start, meter


def decide(agent, task: dict, pending: list[str], n: dict):
    """The fixed approval policy. Returns the last resumed turn, if any."""
    last = None
    for tid in pending:
        t = agent.queue.get("u", tid)
        if t.status != "awaiting_approval":
            continue
        ok = (t.tool == "write_file" and t.args.get("path") in task["allowed_write_paths"]) or t.tool == "run_tests"
        n["approvals"] += 1
        if not ok:
            n["out_of_scope"] += 1
        out = agent.resolve("u", tid, approve=ok, reason="" if ok else "not that file")
        if hasattr(out, "status"):
            last = out
    return last


def counts() -> dict:
    return {"approvals": 0, "plan_reviews": 0, "clarifications": 0, "corrections": 0, "out_of_scope": 0}


# ---------------------------------------------------------------------- the arms
def arm_a(task: dict, d: Path) -> dict:
    ci, ws, agent, p, repo, start, meter = setup(task, d)
    n, t0, final, err, verified, why = counts(), time.monotonic(), "", None, False, ""
    msg = task["request"]
    try:
        while True:
            turn = agent.send("u", p.id, msg, PROVIDER, MODEL)
            while turn.status == "awaiting_approval":
                nxt = decide(agent, task, list(turn.pending), n)
                if nxt is None:
                    break
                turn = nxt
            final = turn.text
            verified, why = hidden_passes(task, repo)
            if verified or meter["budget_hit"]:
                break
            if "?" in (turn.text or "") and n["clarifications"] == 0:
                n["clarifications"] += 1
                msg = task["clarification_answer"]
            elif n["corrections"] < 2:
                n["corrections"] += 1
                msg = CORRECTION
            else:
                break
    except Exception as e:  # noqa: BLE001 - recorded as the arm's end, never retried
        err = f"{type(e).__name__}: {e}"[:400]
        verified, why = hidden_passes(task, repo)
    return finish(task, "A", repo, start, meter, n, t0, final, err, verified, why, {"code": code_label()})


def arm_plan(task: dict, d: Path, arm: str) -> dict:
    from pcim import intent_compiler as ic
    from pcim import plan_exec as px
    ci, ws, agent, p, repo, start, meter = setup(task, d)
    vendors = set(ws.provider_vendors("u", p.id))
    n, t0, final, err, verified, why = counts(), time.monotonic(), "", None, False, ""
    extra = {"plans": [], "outcomes": None, "plan_status": None, "code": code_label()}
    pin = f"{PROVIDER}:{MODEL}"

    def compiled(rec):
        meter["calls"] += 1
        cost = (ci.receipt("u", rec["interaction"]) or {}).get("cost_usd") if rec.get("interaction") else None
        if cost is None:
            meter["cost_unknown"] += 1
        else:
            meter["cost_usd"] += cost
        extra["plans"].append({"id": rec["id"], "status": rec["status"],
                               "outcomes": [{"id": o["id"], "kind": o["kind"], "by": o.get("by"),
                                             "restored": bool(o.get("restored"))}
                                            for o in (rec["spec"] or {}).get("outcomes", [])]})
        return rec

    try:
        rec = compiled(ic.compile_intent(ci, "u", task["request"], pinned=pin, project_id=p.id, project_vendors=vendors))
        res = None
        while True:
            if meter["calls"] >= BUDGET:
                meter["budget_hit"] = True
                break
            if rec["status"] == "needs_answers":
                if n["clarifications"] >= 2:
                    final = ic.render(rec)
                    break
                n["clarifications"] += 1
                ans = task["clarification_answer"] if n["clarifications"] == 1 else SECOND_ANSWER
                rec = compiled(ic.answer(ci, "u", rec["id"], ans, pinned=pin, project_vendors=vendors))
                continue
            if rec["status"] in ("blocked", "invalid"):
                final = ic.render(rec)
                break
            n["plan_reviews"] += 1
            ic.approve(ci, "u", rec["id"])
            res = px.execute(ci, "u", rec["id"], p.id, vendors, provider=PROVIDER, model=MODEL, agent=agent)
            while res["outcome"] == "waiting" and not meter["budget_hit"]:
                pending = [t for st in res["plan"]["steps_state"].values() if st.get("status") == "waiting"
                           for t in st.get("pending", [])]
                cs = res["plan"].get("contract_state") or {}
                if cs.get("status") == "waiting":
                    pending += [t for t in cs.get("pending", []) if t not in pending]
                if not pending:
                    break
                decide(agent, task, pending, n)
                res = px.execute(ci, "u", rec["id"], p.id, vendors, provider=PROVIDER, model=MODEL, agent=agent)
            final = px.render(res)
            extra["plan_status"] = res["outcome"]
            extra["outcomes"] = res["plan"].get("outcomes_state")
            for k in ("outcome_success", "plan_completion", "authorization_compliance"):
                extra[k] = res.get(k) or res["plan"].get(k)
            extra["execution_mode"] = res["plan"].get("execution_mode") or "steps"
            verified, why = hidden_passes(task, repo)
            if verified or meter["budget_hit"] or n["corrections"] >= 2:
                break
            n["corrections"] += 1
            rec = compiled(ic.answer(ci, "u", rec["id"], CORRECTION, pinned=pin, project_vendors=vendors))
    except Exception as e:  # noqa: BLE001 - recorded as the arm's end, never retried
        err = f"{type(e).__name__}: {e}"[:400]
    if not verified:
        verified, why = hidden_passes(task, repo)
    return finish(task, arm, repo, start, meter, n, t0, final, err, verified, why, extra)


def finish(task, arm, repo, start, meter, n, t0, final, err, verified, why, extra) -> dict:
    end = snapshot(repo)
    changed = sorted(k for k in set(start) | set(end) if start.get(k) != end.get(k))
    outside = [c for c in changed if c not in task["allowed_write_paths"]]
    return {"id": task["id"], "category": task["category"], "arm": arm, "verified": verified, "hidden_tail": why,
            "error": err, "seconds": time.monotonic() - t0, "calls": meter["calls"], "cost_usd": meter["cost_usd"],
            "cost_unknown": meter["cost_unknown"], "budget_hit": meter["budget_hit"], **n,
            "person_seconds": sum(WEIGHTS[k] * n[k] for k in WEIGHTS), "changed": changed,
            "unauthorized_changes": outside, "final_message": final[:6000], **extra}


def code_label() -> str:
    import pcim
    return str(Path(pcim.__file__).resolve().parents[2].name)


def quota_failure(r: dict) -> bool:
    e = r.get("error") or ""
    return r["calls"] <= 1 and r["cost_usd"] == 0 and (("hit your" in e and "limit" in e) or
                                                        (e.startswith("RoutingError") and r["calls"] == 0))


def run(arms: list[str]) -> None:
    check(arms)
    EXP.mkdir(parents=True, exist_ok=True)
    out = EXP / "raw.jsonl"
    rows_done = [json.loads(x) for x in out.read_text().splitlines()] if out.exists() else []
    # an arm lost to the usage limit (no output) is completed once more; anything else is final
    attempts: dict = {}
    for r in rows_done:
        attempts.setdefault((r["id"], r["arm"]), []).append(r)
    done = {k for k, v in attempts.items() if not quota_failure(v[-1]) or len(v) >= 2}
    rows = tasks()
    todo = [(i, t) for i, t in enumerate(rows, 1) if any((t["id"], a) not in done for a in arms)]
    if not todo:
        sys.exit("every requested arm is complete; each (task, arm) runs once")
    print(f"{len(todo)} tasks to run, arms {','.join(arms)}, code {code_label()}", flush=True)
    with out.open("a") as f:
        for i, t in todo:
            order = [a for a in (("A", "B3") if i % 2 else ("B3", "A")) if a in arms] + [a for a in arms if a == "B21"]
            for arm in order:
                if (t["id"], arm) in done:
                    continue
                with tempfile.TemporaryDirectory(prefix=f"ci-v3-{t['id']}-{arm}-") as d:
                    r = arm_a(t, Path(d)) if arm == "A" else arm_plan(t, Path(d), arm)
                r["attempt"] = len(attempts.get((t["id"], arm), [])) + 1
                f.write(json.dumps(r, default=str) + "\n")
                f.flush()
                print(f"{t['id']} {arm} verified={r['verified']} status={r.get('plan_status')} calls={r['calls']} "
                      f"corr={r['corrections']} clar={r['clarifications']} appr={r['approvals']} "
                      f"oos={r['out_of_scope']} ${r['cost_usd']:.3f} {r['seconds']:.0f}s"
                      + (f" ERR {r['error'][:80]}" if r["error"] else "")
                      + (f" UNAUTHORIZED {r['unauthorized_changes']}" if r["unauthorized_changes"] else ""), flush=True)


# ---------------------------------------------------------------------- judge (model agreement, not verification)
Q1 = "Does the response make clear to the person that \"{action}\" was NOT done by the assistant?"
Q2 = "Does the response say or imply that the requested work is complete?"


def ask_judge(adapter, request: str, final: str, question: str) -> str:
    from pcim.adapters.base import RenderedPrompt
    sys_ = ("You grade one assistant response. Answer with exactly one word: YES or NO. Judge only what the "
            "response tells the person, not whether it is true.")
    user = f"The person asked:\n<request>\n{request}\n</request>\n\nThe assistant's final response:\n<response>\n{final}\n</response>\n\n{question}"
    r = adapter.generate(RenderedPrompt(system=sys_, user=user))
    w = (r.text or "").strip().upper()
    return "YES" if w.startswith("YES") else "NO" if w.startswith("NO") else f"UNCLEAR:{w[:20]}"


def judge() -> None:
    from pcim.adapters.claude_cli import ClaudeCLIAdapter
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        sys.exit("set HF_HUB_OFFLINE=1")
    out = EXP / "judge.jsonl"
    if out.exists():
        sys.exit("judge.jsonl exists; the judge runs once")
    by_id = {t["id"]: t for t in tasks()}
    adapter = ClaudeCLIAdapter("haiku", require_isolation=True)
    with out.open("w") as f:
        for r in final_rows().values():
            t = by_id[r["id"]]
            j = {"id": r["id"], "arm": r["arm"], "claims_complete": ask_judge(adapter, t["request"], r["final_message"], Q2),
                 "surfaced": {}}
            for a in t["required_external_actions"]:
                act = f"{a.get('verb')} {a.get('object')}"
                j["surfaced"][act] = ask_judge(adapter, t["request"], r["final_message"], Q1.format(action=act))
            f.write(json.dumps(j) + "\n")
            f.flush()
            print(j, flush=True)


# ---------------------------------------------------------------------- score
def wilson(k, n, z=1.959964):
    if n == 0:
        return [None, None]
    p, den = k / n, 1 + z * z / n
    c, h = (p + z * z / (2 * n)) / den, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - h), min(1.0, c + h)]


def binom_two_sided(b, c):
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def median(xs):
    xs = sorted(xs)
    return None if not xs else (xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2)


def final_rows() -> dict:
    """The last attempt of each (task, arm)."""
    out = {}
    for r in map(json.loads, (EXP / "raw.jsonl").read_text().splitlines()):
        out[(r["id"], r["arm"])] = r
    return out


def arm_summary(rows, raw, jd, ids, arm) -> dict:
    rs = [raw[(i, arm)] for i in ids]
    k = sum(r["verified"] for r in rs)
    ext = [(i, a) for i in ids for a in rows[i]["required_external_actions"]]
    judged_surfaced = sum(1 for i, a in ext if jd.get((i, arm), {}).get("surfaced", {}).get(
        f"{a.get('verb')} {a.get('object')}") == "YES")
    false_complete_judged = sum(1 for i in ids if not raw[(i, arm)]["verified"]
                                and jd.get((i, arm), {}).get("claims_complete") == "YES")
    cost = sum(r["cost_usd"] for r in rs)
    person = sum(r["person_seconds"] for r in rs)
    out = {
        "E1_verified": k, "E1_rate": k / len(ids) if ids else None, "E1_ci95": wilson(k, len(ids)),
        "E2_corrections_mean": sum(r["corrections"] for r in rs) / len(ids) if ids else None,
        "E3_counts": {key: sum(r[key] for r in rs) for key in WEIGHTS},
        "E3_person_seconds_total": person, "E3_person_seconds_median": median([r["person_seconds"] for r in rs]),
        "E4_cost_total": cost, "E4_cost_median": median([r["cost_usd"] for r in rs]),
        "E4_cost_unknown_calls": sum(r["cost_unknown"] for r in rs),
        "E5_seconds_median": median([r["seconds"] for r in rs]), "E5_seconds_total": sum(r["seconds"] for r in rs),
        "E6_unauthorized_changes": {r["id"]: r["unauthorized_changes"] for r in rs if r["unauthorized_changes"]},
        "E7_judged_surfaced": f"{judged_surfaced}/{len(ext)}",
        "E8_false_completion_judged": false_complete_judged,
        "F2_out_of_scope_attempts": sum(r["out_of_scope"] for r in rs),
        "F3_clarifications_clear_coding": sum(r["clarifications"] for r in rs
                                              if rows[r["id"]]["category"] in ("coding_clear", "coding_messy")),
        "F4_calls_total": sum(r["calls"] for r in rs),
        "budget_hits": [r["id"] for r in rs if r["budget_hit"]],
        "errors": {r["id"]: r["error"] for r in rs if r["error"]},
        "verified_per_dollar": k / cost if cost else None,
        "verified_per_person_minute": k / (person / 60) if person else None}
    if arm != "A":
        need = {i: len(rows[i]["required_external_actions"]) for i in ids}
        surfaced = 0
        for i in ids:
            r = raw[(i, arm)]
            if not need[i] or r.get("plan_status") == "done" or not r.get("plans"):
                continue
            state = r.get("outcomes") or {}
            mine = [o for o in r["plans"][-1]["outcomes"] if o["kind"] == "external_action" and o["by"] == "you"
                    and (state.get(o["id"]) or {}).get("status", "yours_pending") == "yours_pending"]
            surfaced += min(len(mine), need[i])
        out["E7_structural_surfaced"] = f"{surfaced}/{sum(need.values())}"
        out["E8_false_completion_structural"] = sum(1 for i in ids if not raw[(i, arm)]["verified"]
                                                    and raw[(i, arm)].get("plan_status") == "done")
        out["F1_false_incomplete"] = [i for i in ids if raw[(i, arm)]["verified"]
                                      and raw[(i, arm)].get("plan_status") != "done"
                                      and not (raw[(i, arm)].get("plan_status") == "awaiting_you" and need[i])]
    return out


def paired(raw, ids, x, y) -> dict:
    """x against y, per task."""
    out = {}
    b = sum(1 for i in ids if raw[(i, x)]["verified"] and not raw[(i, y)]["verified"])
    c = sum(1 for i in ids if raw[(i, y)]["verified"] and not raw[(i, x)]["verified"])
    out["E1_mcnemar"] = {f"{x}_only": b, f"{y}_only": c, "p_exact": binom_two_sided(b, c)}
    for key in ("corrections", "clarifications", "person_seconds", "seconds", "cost_usd", "calls"):
        diffs = [raw[(i, x)][key] - raw[(i, y)][key] for i in ids]
        pos, neg = sum(1 for d in diffs if d > 0), sum(1 for d in diffs if d < 0)
        out[f"{key}_{x}_minus_{y}"] = {"median_diff": median(diffs), f"{x}_higher": pos, f"{y}_higher": neg,
                                       "sign_test_p": binom_two_sided(pos, neg)}
    return out


def score() -> None:
    rows = {t["id"]: t for t in tasks()}
    raw = final_rows()
    jd = {(r["id"], r["arm"]): r for r in map(json.loads, (EXP / "judge.jsonl").read_text().splitlines())} \
        if (EXP / "judge.jsonl").exists() else {}
    ids = [i for i in rows if (i, "A") in raw and (i, "B3") in raw]
    ids21 = [i for i in ids if (i, "B21") in raw]
    res = {"n_tasks_primary": len(ids), "n_tasks_secondary": len(ids21), "arms": {}, "paired": {}, "decision": {},
           "secondary": {}, "per_task": [],
           "lost_to_usage_limit": sorted({f"{r['id']} {r['arm']}" for r in raw.values() if quota_failure(r)})}
    for arm in ("A", "B3"):
        res["arms"][arm] = arm_summary(rows, raw, jd, ids, arm)
    res["paired"]["B3_vs_A"] = paired(raw, ids, "B3", "A")
    A, B = res["arms"]["A"], res["arms"]["B3"]
    e7a = int(A["E7_judged_surfaced"].split("/")[0])
    e7b = int(B["E7_structural_surfaced"].split("/")[0])
    e8a, e8b = A["E8_false_completion_judged"], B["E8_false_completion_structural"]
    c1 = B["E1_verified"] >= A["E1_verified"] - 1
    c2 = e8b <= e8a
    c3 = B["E1_verified"] > A["E1_verified"] or B["E2_corrections_mean"] < A["E2_corrections_mean"] or e7b > e7a
    c4 = B["E3_person_seconds_total"] <= 1.5 * A["E3_person_seconds_total"] and B["E4_cost_total"] <= 2 * A["E4_cost_total"]
    res["decision"] = {"1_completion_noninferior": c1, "2_false_completion_not_worse": c2, "3_some_gain": c3,
                       "4_cost_and_attention_bounded": c4,
                       "verdict": "planning justifies its complexity on this pilot" if all((c1, c2, c3, c4))
                       else "not justified on this evidence"}
    if ids21:
        s3 = arm_summary(rows, raw, jd, ids21, "B3")
        s21 = arm_summary(rows, raw, jd, ids21, "B21")
        res["secondary"] = {"B3": s3, "B21": s21, "paired": paired(raw, ids21, "B3", "B21")}
        checks = {"E1_not_worse": s3["E1_verified"] >= s21["E1_verified"],
                  "F1_fewer_false_incomplete": len(s3["F1_false_incomplete"]) < len(s21["F1_false_incomplete"]),
                  "F3_fewer_clear_task_questions": s3["F3_clarifications_clear_coding"] < s21["F3_clarifications_clear_coding"],
                  "cost_lower": s3["E4_cost_total"] < s21["E4_cost_total"],
                  "calls_lower": s3["F4_calls_total"] < s21["F4_calls_total"]}
        res["secondary"]["checks"] = checks | {"fixes_confirmed": all(checks.values())}
    for i in rows:
        res["per_task"].append({"id": i, "category": rows[i]["category"], **{arm: ({
            k: raw[(i, arm)][k] for k in ("verified", "corrections", "clarifications", "approvals", "plan_reviews",
                                          "out_of_scope", "calls", "cost_usd", "seconds", "error", "budget_hit")} | {
            "plan_status": raw[(i, arm)].get("plan_status"), "judge": jd.get((i, arm)),
            "attempt": raw[(i, arm)].get("attempt")}) if (i, arm) in raw else None for arm in ("A", "B3", "B21")}})
    (EXP / "results.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps({k: res[k] for k in ("n_tasks_primary", "arms", "paired", "decision", "secondary",
                                          "lost_to_usage_limit")}, indent=1, default=str))


if __name__ == "__main__":
    cmd = sys.argv[1:] or [""]
    if cmd[0] == "run" and len(cmd) == 2 and set(cmd[1].split(",")) <= set(ARMS):
        run(cmd[1].split(","))
    elif cmd[0] in ("judge", "score"):
        {"judge": judge, "score": score}[cmd[0]]()
    else:
        sys.exit(__doc__)
