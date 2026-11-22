"""Write, validate and seal eval_v8's task pool without the builder reading it; then freeze the evaluation.

    PCIM_CLI_REQUIRE_ISOLATION=1 PYTHONPATH=src python scripts/eval_v8_pool.py write    # writer sessions, checks, repairs
    PYTHONPATH=src python scripts/eval_v8_pool.py seal                                 # ~/projects/ci-sealed/v8, read-only
    PYTHONPATH=src python scripts/eval_v8_pool.py freeze                               # experiments/eval_v8/FROZEN.txt

**The writer.** Separate `claude -p` sessions run through pcim's ClaudeCLIAdapter with isolation:
- no tools, no MCP servers, no settings or hooks, and no session persistence;
- an empty private directory outside $HOME.

A session's only input is `experiments/eval_v6/TASK_RUBRIC.md`, the category and count to write,
and the short theme names already used. For a repair, the session also gets its own task and the
failed checks.

**The checks** are mechanical (experiments/eval_v8/PROTOCOL.md):
- the schema and the rubric's consistency rules;
- the file limits and the reference's scope;
- the original, the reference and the other reading, each run against the visible tests and the
  hidden check.

**The builder never sees task content.** This script prints only categories, ids, check names,
counts and costs. Test output goes to the writer, never to the screen.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUBRIC = ROOT / "experiments" / "eval_v6" / "TASK_RUBRIC.md"
EXP = ROOT / "experiments" / "eval_v8"
SEALED = Path.home() / "projects" / "ci-sealed" / "v8"
WORK = Path.home() / "projects" / "ci-sealed" / "v8-work"  # private, 0700; holds the unsealed batches
PY = sys.executable
SEED = 20261122  # PREREGISTRATION.md
WRITER_MODEL = "opus"
MAX_REPAIRS = 2
MAX_REPLACEMENTS = 2  # per category

CATEGORIES = [  # (category, count, needs_question), the rubric's table
    ("choice", 4, True), ("scope", 4, True), ("conflict", 2, True), ("value", 2, True),
    ("clear", 4, False), ("settled_by_repo", 4, False), ("outside_action", 4, False)]
NEEDS_OTHER = {"choice", "scope", "conflict", "value", "settled_by_repo"}
TASK_FIELDS = ("category", "needs_question", "goal", "facts", "outside_actions", "files", "hidden_test",
               "allowed_write_paths", "route", "route_also_ok", "route_why", "why")

WRITER_SYSTEM = ("You write evaluation tasks exactly to a written rubric. You have no tools and no other context. "
                 "Reply with ONE JSON object and nothing else.")

HOW_RUN = """How your tasks are run and checked (by a script; nobody reads them by hand):
- `files` is written into an empty folder. The visible tests run with `python -m pytest -q -p no:cacheprovider tests` from that folder.
- `hidden_test` is saved as `test_hidden_check.py` in the folder root and run with `python -m pytest -q -p no:cacheprovider test_hidden_check.py`. It imports from the folder root (pytest.ini sets pythonpath = .).
- `reference` and `other_reading` map path -> the FULL new content of each file that reading changes; they are laid over `files`.
- Checks: the visible tests pass on the original; the hidden test fails on the original; both pass on the reference; on the other reading, the visible tests pass and the hidden test fails."""

FORMAT = """Reply with ONE JSON object: {"tasks": [ ... ]}. Each task has every rubric field EXCEPT `id` (assigned later), plus:
- "theme": 3 to 6 words naming the small project (for example "school bake sale till");
- "reference": {path: full content};
- "other_reading": {path: full content} or null."""


# ---------------------------------------------------------------------- the writer
def _adapter():
    from pcim.adapters.claude_cli import ClaudeCLIAdapter
    return ClaudeCLIAdapter(WRITER_MODEL, require_isolation=True, isolate_identity=True, max_budget_usd=4.0,
                            timeout=1200.0)


def _ask(user: str) -> tuple[dict | None, float, str]:
    """One isolated writer session. Returns (object or None, cost, raw text kept only in WORK)."""
    from pcim.adapters.base import RenderedPrompt
    r = _adapter().generate(RenderedPrompt(system=WRITER_SYSTEM, user=user))
    text = (r.text or "").strip()
    i, j = text.find("{"), text.rfind("}")
    try:
        obj = json.loads(text[i:j + 1]) if i >= 0 < j else None
    except ValueError:
        obj = None
    return (obj if isinstance(obj, dict) else None), float(r.cost_usd or 0.0), text


def _rubric() -> str:
    return RUBRIC.read_text()


def write_batch(cat: str, n: int, nq: bool, used: list[str]) -> tuple[list[dict], float]:
    other = "required" if cat in NEEDS_OTHER else "null"
    user = (f"<rubric>\n{_rubric()}\n</rubric>\n\n{HOW_RUN}\n\n"
            f"Write {n} task(s) of category `{cat}` (needs_question = {str(nq).lower()}). `other_reading` is {other} "
            f"for this category. Each task is about a different small everyday project. Do not reuse these themes, "
            f"already used: {', '.join(used) or 'none yet'}.\n\n{FORMAT}")
    obj, cost, raw = _ask(user)
    (WORK / "raw").mkdir(exist_ok=True)
    (WORK / "raw" / f"{cat}-{int(time.time())}.txt").write_text(raw)
    ts = (obj or {}).get("tasks") or []
    return [t for t in ts if isinstance(t, dict)][:n], cost


def repair(task: dict, failed: dict) -> tuple[dict | None, float]:
    user = (f"<rubric>\n{_rubric()}\n</rubric>\n\n{HOW_RUN}\n\n"
            f"Here is a task you wrote, as JSON, and the checks it failed with their output. Fix the task so every "
            f"check holds; keep its category and theme.\n<task>\n{json.dumps(task)}\n</task>\n"
            f"<failed_checks>\n{json.dumps(failed)}\n</failed_checks>\n\n"
            "Reply with ONE JSON object: the corrected task, with every field it had (including theme, reference "
            "and other_reading).")
    obj, cost, _ = _ask(user)
    return obj, cost


# ---------------------------------------------------------------------- the checks
def _safe(path: str) -> bool:
    p = Path(path)
    return bool(path) and not p.is_absolute() and ".." not in p.parts


def _lay(root: Path, files: dict) -> None:
    for path, content in files.items():
        f = root / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)


def _pytest(root: Path, target: str) -> tuple[int, str]:
    try:
        r = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", target], cwd=root, capture_output=True,
                           text=True, timeout=90, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return r.returncode, (r.stdout + r.stderr)[-1500:]
    except subprocess.TimeoutExpired:
        return 124, "timed out after 90 s"


def check(task: dict) -> dict:
    """Every check: name -> (ok, detail). The detail goes to the writer only."""
    c: dict[str, tuple[bool, str]] = {}
    miss = [k for k in TASK_FIELDS + ("reference", "other_reading") if k not in task]
    c["schema"] = (not miss and isinstance(task.get("files"), dict) and isinstance(task.get("facts"), list)
                   and isinstance(task.get("reference"), dict), f"missing or wrong-typed: {miss}")
    if not c["schema"][0]:
        return c
    cat, facts = task["category"], task["facts"]
    want = {k: (n, nq) for k, n, nq in CATEGORIES}
    c["category"] = (cat in want and task["needs_question"] is want[cat][1], f"category {cat!r} needs_question {task['needs_question']!r}")
    ok_facts = all(isinstance(f, dict) and {"id", "text", "essential", "discoverable_in_repo", "volunteered"} <= set(f) for f in facts)
    c["facts_schema"] = (ok_facts, "each fact needs id, text, essential, discoverable_in_repo, volunteered")
    if ok_facts:
        tacit_essential = any(f["essential"] and not f["discoverable_in_repo"] and not f["volunteered"] for f in facts)
        c["consistency"] = (tacit_essential == bool(task["needs_question"]),
                            "needs_question must be true iff a fact is essential, not discoverable and not volunteered")
        non_ess = sum(1 for f in facts if not f["essential"])
        c["nonessential_count"] = (1 <= non_ess <= 3, f"{non_ess} non-essential facts; need 1-3")
        if cat == "settled_by_repo":
            c["settled_fact"] = (any(f["essential"] and f["discoverable_in_repo"] and not f["volunteered"] for f in facts),
                                 "needs an essential fact that is discoverable and not volunteered")
    files, ref, other = task["files"], task["reference"], task["other_reading"]
    allp = set(task["allowed_write_paths"] or [])
    every = {**files, **ref, **(other or {}), "test_hidden_check.py": task["hidden_test"]}
    c["paths"] = (all(_safe(p) for p in every), "paths must be relative, without ..")
    c["pytest_ini"] = ("pytest.ini" in files and "pythonpath" in files.get("pytest.ini", ""), "files needs pytest.ini with pythonpath = .")
    c["tests_dir"] = (any(p.startswith("tests/") for p in files), "files needs tests/ with visible tests")
    long = [p for p, s in every.items() if isinstance(s, str) and s.count("\n") + 1 > 80]
    c["file_lines"] = (not long, f"over 80 lines: {long}")
    c["reference_scope"] = (0 < len(ref) <= 3 and set(ref) <= allp, f"reference changes {sorted(ref)}; allowed {sorted(allp)}")
    c["other_reading"] = ((other is not None and isinstance(other, dict) and len(other) > 0) if cat in NEEDS_OTHER else True,
                          "other_reading is required for this category")
    if not all(ok for ok, _ in c.values()):
        return c
    base = Path(tempfile.mkdtemp(prefix="ci-v8pool-"))
    try:
        for name, overlay in (("orig", {}), ("ref", ref), ("other", other if cat in NEEDS_OTHER else None)):
            if overlay is None:
                continue
            d = base / name
            d.mkdir()
            _lay(d, files)
            _lay(d, overlay)
            (d / "test_hidden_check.py").write_text(task["hidden_test"])
            vrc, vout = _pytest(d, "tests")
            hrc, hout = _pytest(d, "test_hidden_check.py")
            c[f"{name}_visible_pass"] = (vrc == 0, vout)
            if name == "ref":
                c["ref_hidden_pass"] = (hrc == 0, hout)
            else:
                c[f"{name}_hidden_fail"] = (hrc in (1, 2), f"exit {hrc}\n{hout}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return c


def failed(c: dict) -> dict:
    return {k: d for k, (ok, d) in c.items() if not ok}


def safe_check(task: dict) -> dict:
    """check(), with a malformed task (a non-string file, a wrong type) counted as a failed check."""
    try:
        return check(task)
    except Exception as e:  # noqa: BLE001
        return {"well_formed": (False, f"{type(e).__name__}: {e}"[:300])}


# ---------------------------------------------------------------------- the commands
def _state() -> dict:
    f = WORK / "state.json"
    return json.loads(f.read_text()) if f.exists() else {"batches": {}, "cost_usd": 0.0, "repairs": 0,
                                                         "replacements": 0, "sessions": 0}


def _save(st: dict) -> None:
    (WORK / "state.json").write_text(json.dumps(st))


def cmd_write() -> int:
    if os.environ.get("PCIM_CLI_REQUIRE_ISOLATION") != "1":
        sys.exit("set PCIM_CLI_REQUIRE_ISOLATION=1")
    iso = _adapter().identity_isolation()
    if iso["status"] != "verified":
        sys.exit(f"identity isolation is {iso['status']}: {iso['why']}")
    if SEALED.exists():
        sys.exit(f"{SEALED} exists: the pool is sealed")
    old = os.umask(0o077)
    WORK.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = _state()
    for cat, n, nq in CATEGORIES:
        b = st["batches"].get(cat) or []
        tries = 0
        while len([t for t in b if t.get("_ok")]) < n:
            need = n - len([t for t in b if t.get("_ok")])
            if tries > MAX_REPLACEMENTS:
                print(f"{cat}: {need} task(s) still fail after {MAX_REPLACEMENTS} replacements; stopping", flush=True)
                _save(st)
                return 1
            used = [t.get("theme", "") for bb in st["batches"].values() for t in bb if t.get("_ok")]
            new, cost = write_batch(cat, need, nq, used)
            st["cost_usd"] += cost
            st["sessions"] += 1
            if tries:
                st["replacements"] += len(new)
            print(f"{cat}: wrote {len(new)}/{need} (${cost:.2f})", flush=True)
            for t in new:
                for r in range(MAX_REPAIRS + 1):
                    c = safe_check(t)
                    bad = failed(c)
                    if not bad:
                        t["_ok"] = True
                        break
                    print(f"  {cat} #{len(b) + 1}: failed {sorted(bad)}" + (" -> repair" if r < MAX_REPAIRS else ""), flush=True)
                    if r == MAX_REPAIRS:
                        break
                    fixed, cost = repair({k: v for k, v in t.items() if not k.startswith("_")}, bad)
                    st["cost_usd"] += cost
                    st["sessions"] += 1
                    st["repairs"] += 1
                    if fixed:
                        t = fixed
                b.append(t)
            b = [t for t in b if t.get("_ok")]
            st["batches"][cat] = b
            _save(st)
            tries += 1
        print(f"{cat}: {n}/{n} pass every check", flush=True)
    os.umask(old)
    print(f"pool written: 24 tasks; writer sessions {st['sessions']}, repairs {st['repairs']}, "
          f"replacements {st['replacements']}, cost ${st['cost_usd']:.2f}")
    return 0


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cmd_seal() -> int:
    st = _state()
    ts = [t for cat, n, _ in CATEGORIES for t in (st["batches"].get(cat) or [])[:n] if t.get("_ok")]
    if len(ts) != 24:
        sys.exit(f"{len(ts)}/24 validated tasks; run write first")
    rows = []
    for t in ts:  # re-check right before sealing
        bad = failed(safe_check(t))
        if bad:
            sys.exit(f"a {t['category']} task fails {sorted(bad)} at sealing")
        rows.append(t)
    random.Random(SEED + 7).shuffle(rows)
    old = os.umask(0o022)
    SEALED.mkdir(parents=True)
    tasks, refs, val = [], {}, []
    for i, t in enumerate(rows, 1):
        tid = f"t{i:02d}"
        tasks.append({"id": tid, **{k: t[k] for k in TASK_FIELDS}})
        refs[tid] = {"reference": t["reference"], "other_reading": t["other_reading"]}
        c = check(t)
        val.append({"id": tid, "category": t["category"], **{k: ok for k, (ok, _) in c.items()}})
    (SEALED / "TASKS.jsonl").write_text("".join(json.dumps(x) + "\n" for x in tasks))
    (SEALED / "references.json").write_text(json.dumps(refs))
    (SEALED / "validation.json").write_text(json.dumps(val, indent=1))
    cats = {}
    for t in tasks:
        cats[t["category"]] = cats.get(t["category"], 0) + 1
    def count(key):
        return sum(1 for v in val if v.get(key))
    lines = [f"tasks_sha256={_sha(SEALED / 'TASKS.jsonl')}", f"task_count={len(tasks)}",
             f"needs_question={sum(t['needs_question'] for t in tasks)}",
             f"clear={sum(not t['needs_question'] for t in tasks)}", f"validated_count={len(val)}",
             *[f"category_{k}={v}" for k, v in cats.items()],
             f"baseline_visible_pass_count={count('orig_visible_pass')}",
             f"baseline_hidden_fail_count={count('orig_hidden_fail')}",
             f"reference_visible_pass_count={count('ref_visible_pass')}",
             f"reference_hidden_pass_count={count('ref_hidden_pass')}",
             f"other_reading_visible_pass_count={count('other_visible_pass')}",
             f"other_reading_hidden_fail_count={count('other_hidden_fail')}",
             f"writer_model={WRITER_MODEL}", f"writer_sessions={st['sessions']}", f"repairs={st['repairs']}",
             f"replacements={st['replacements']}", f"writer_cost_usd={st['cost_usd']:.2f}",
             f"sealed_at_utc={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
             "independence=Isolated claude -p sessions (no tools, no MCP, no settings, no persistence, an empty "
             "directory outside $HOME); the only repository input was experiments/eval_v6/TASK_RUBRIC.md. Checked "
             "mechanically by scripts/eval_v8_pool.py; the builder saw only ids, categories, check names and counts."]
    (SEALED / "SEALED.txt").write_text("\n".join(lines) + "\n")
    for f in SEALED.iterdir():
        f.chmod(0o400 if f.name != "TASKS.jsonl" else 0o444)
    SEALED.chmod(0o555)
    os.umask(old)
    print("\n".join(x for x in lines if not x.startswith("independence")))
    return 0


def cmd_freeze() -> int:
    if (EXP / "FROZEN.txt").exists():
        sys.exit("experiments/eval_v8/FROZEN.txt exists")
    code = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "0db915c"], capture_output=True, text=True).stdout.strip()
    if subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", code, "--", "src"]).returncode != 0 or \
            subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "src"], capture_output=True,
                           text=True).stdout.strip():
        sys.exit(f"src differs from {code}")
    cli = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.split()[0]
    sealed = dict(x.split("=", 1) for x in (SEALED / "SEALED.txt").read_text().splitlines() if "=" in x)
    rows = [f"frozen_at_utc={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
            f"tasks_sha256={_sha(SEALED / 'TASKS.jsonl')}",
            f"personas_sha256={_sha(ROOT / 'experiments' / 'eval_v5' / 'PERSONAS.md')}",
            f"rubric_sha256={_sha(RUBRIC)}",
            f"prereg_sha256={_sha(EXP / 'PREREGISTRATION.md')}", f"protocol_sha256={_sha(EXP / 'PROTOCOL.md')}",
            f"harness_sha256={_sha(ROOT / 'scripts' / 'eval_v8.py')}",
            f"report_sha256={_sha(ROOT / 'scripts' / 'eval_v8_report.py')}",
            f"pool_script_sha256={_sha(Path(__file__))}",
            f"writer_seal_sha256={_sha(SEALED / 'SEALED.txt')}",
            f"code={code}", "tree=.", f"seed={SEED}", f"cli_version={cli}", "agent_model=claude-cli:sonnet",
            "persona_model=sonnet", "judge_models=haiku,sonnet,opus",
            f"writer_validation={sealed.get('validated_count')}/{sealed.get('task_count')}",
            f"writer_model={WRITER_MODEL}", "writer_isolation=isolated claude -p sessions limited to the task rubric"]
    (EXP / "FROZEN.txt").write_text("\n".join(rows) + "\n")
    print("\n".join(rows))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit({"write": cmd_write, "seal": cmd_seal, "freeze": cmd_freeze}.get(cmd, lambda: sys.exit(__doc__))())
