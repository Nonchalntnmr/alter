"""End-to-end check of the `ci` commands against their documented behaviour. No model calls.

    HF_HUB_OFFLINE=1 PYTHONPATH=src python scripts/e2e_terminal.py
    E2E_INSTALLED=1 <venv>/bin/python scripts/e2e_terminal.py    # against an installed wheel instead

Everything runs through the real `ci` entry point (pcim.terminal.main) in subprocesses, in a
temporary PCIM_HOME with a temporary git repo authorized as a project. The only stand-in is the
model: `claude` is a small script on PCIM_CLAUDE_BIN that returns a fixed plan to planning calls
and a scripted sequence of tool calls to agent calls (read, write, run tests, finish). Modes,
switched with files in its state directory, make it sleep (to be killed mid-step) or fail like an
API error.

Checks: ci do (direct, clarify, plan); plan / approve / execute / approve-task / deny / --done / --retry / --cancel; restart
recovery after a killed execute; Stop-all (halt, resume); permission boundaries (a write outside the
folder, a plan run from another project, reading outside an authorized folder); a provider failure;
and that mesh, twin, audit and learn run and say what they document. Prints PASS/FAIL per check and
exits non-zero if any check fails.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PY = sys.executable
SRC = Path(__file__).resolve().parents[1] / "src"
RESULTS: list[tuple[str, bool, str]] = []

FAKE = r'''#!{py}
import json, os, sys, time
from pathlib import Path
state = Path({state!r})
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("0.0.0-fake (stand-in)"); sys.exit(0)
prompt = sys.stdin.read()
system = open(argv[argv.index("--system-prompt-file") + 1]).read() if "--system-prompt-file" in argv else ""
def reply(text, code=0):
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": text,
                      "total_cost_usd": 0.0, "usage": {{"input_tokens": 10, "output_tokens": 5}},
                      "modelUsage": {{"claude-sonnet-5-5": {{"costUSD": 0.0}}}}}}))
    sys.exit(code)
if (state / "fail").exists():
    print("API Error: 500 internal server error", file=sys.stderr); sys.exit(1)
if (state / "sleep").exists():
    time.sleep(60)
if "<ci_tools>" in prompt:  # an agent step: the scripted session
    n = int((state / "n").read_text()) if (state / "n").exists() else 0
    (state / "n").write_text(str(n + 1))
    script = json.loads((state / "script.json").read_text())
    step = script[min(n, len(script) - 1)]
    reply(json.dumps(step))
plan = json.loads((state / "plan.json").read_text())
reply(json.dumps(plan))
'''

PLAN = {"goal": "fix add() in calc.py and email raj the diff",
        "interpretations": [{"goal": "fix add() so the tests pass, and email raj the diff", "p": 0.95}],
        "outcomes": [{"id": "o1", "text": "add() returns the sum and the tests pass", "kind": "deliverable"},
                     {"id": "o2", "text": "email raj the diff", "kind": "external_action", "quote": "email raj the diff"}],
        "constraints": [], "success_criteria": ["the tests pass"],
        "steps": [{"id": "s1", "action": "read calc.py", "kind": "read", "serves": ["o1"], "depends_on": []},
                  {"id": "s2", "action": "fix add() in calc.py", "kind": "write", "serves": ["o1"], "depends_on": ["s1"]},
                  {"id": "s3", "action": "run the tests", "kind": "run", "serves": ["o1"], "depends_on": ["s2"]},
                  {"id": "s4", "action": "email raj the diff", "kind": "external", "serves": ["o2"], "depends_on": ["s3"]}],
        "assumptions": [], "questions": []}
FIX = "def add(a, b):\n    return a + b\n"
SESSION = [{"text": "Reading.", "tool_calls": [{"name": "read_file", "input": {"path": "calc.py"}}]},
           {"text": "Fixing.", "tool_calls": [{"name": "write_file", "input": {"path": "calc.py", "content": FIX,
                                                                                "reason": "add() subtracted"}}]},
           {"text": "Testing.", "tool_calls": [{"name": "run_tests", "input": {"reason": "verify the fix"}}]},
           {"text": "Fixed add(); the tests pass. Emailing Raj is yours: I have no email tool.", "tool_calls": []}]


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -- {detail[:400]}"), flush=True)
    return bool(ok)


class Env:
    def __init__(self, root: Path):
        self.root = root
        self.home, self.repo, self.state, self.other = root / "pcim", root / "repo", root / "state", root / "other"
        for d in (self.repo / "tests", self.state, self.other):
            d.mkdir(parents=True, exist_ok=True)
        (self.repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        (self.repo / "tests" / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
        (self.repo / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "commit", "-qm", "start"], check=True)
        fake = root / "bin" / "claude"
        fake.parent.mkdir()
        fake.write_text(FAKE.format(py=PY, state=str(self.state)))
        fake.chmod(0o755)
        (self.state / "plan.json").write_text(json.dumps(PLAN))
        self.script(SESSION)
        self.env = {**os.environ, "PCIM_HOME": str(self.home), "PCIM_CLAUDE_BIN": str(fake), "HF_HUB_OFFLINE": "1"}
        if os.environ.get("E2E_INSTALLED") == "1":  # test the installed package, not this checkout's src/
            self.env.pop("PYTHONPATH", None)
        else:
            self.env["PYTHONPATH"] = str(SRC)
        self.env.pop("PCIM_CLI_REQUIRE_ISOLATION", None)

    def script(self, steps: list[dict]) -> None:
        (self.state / "script.json").write_text(json.dumps(steps))
        (self.state / "n").unlink(missing_ok=True)

    def mode(self, name: str, on: bool) -> None:
        p = self.state / name
        p.write_text("1") if on else p.unlink(missing_ok=True)

    def ci(self, *args: str, cwd: Path | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
        code = "import sys; from pcim.terminal import main; sys.exit(main(sys.argv[1:]))"
        return subprocess.run([PY, "-c", code, *args], cwd=cwd or self.repo, env=self.env, capture_output=True,
                              text=True, timeout=timeout)

    def api(self, code: str) -> str:
        pre = ("import json, sys; from pathlib import Path; from pcim.engine import PersonalCI; "
               "from pcim.workspace import Workspace; from pcim import intent_compiler as ic; "
               f"ci = PersonalCI(Path({str(self.home)!r})); uid = ci.user_for_token(Path({str(self.home / 'token')!r}).read_text().strip()); "
               "ws = Workspace(ci)\n")
        r = subprocess.run([PY, "-c", pre + code], env=self.env, capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise RuntimeError(r.stderr[-800:])
        return r.stdout.strip()

    def plan(self, pid: str) -> dict:
        return json.loads(self.api(f"print(json.dumps(ic.get(ci, uid, {pid!r}), default=str))"))


def pending_of(rec: dict) -> list[str]:
    cs = rec.get("contract_state") or {}
    out = list(cs.get("pending", [])) if cs.get("status") == "waiting" else []
    for st in (rec.get("steps_state") or {}).values():
        if st.get("status") == "waiting":
            out += [t for t in st.get("pending", []) if t not in out]
    return out


def new_plan(e: Env, request: str, cwd: Path | None = None) -> str:
    r = e.ci("plan", request, cwd=cwd)
    pid = next((w for w in r.stdout.split() if w.startswith("plan_")), None)
    if pid is None:
        raise RuntimeError(f"no plan id in: {r.stdout[-400:]} {r.stderr[-400:]}")
    return pid


def drive(e: Env, pid: str, approve=True) -> tuple[subprocess.CompletedProcess, dict]:
    """Execute, approving (or denying) every action a plan waits on, until it stops waiting."""
    r = e.ci("execute", pid)
    for _ in range(10):
        rec = e.plan(pid)
        pend = pending_of(rec)
        if r.returncode != 6 or not pend:
            break
        for t in pend:
            e.ci("approve" if approve else "deny", t, *(() if approve else ("not now",)))
        r = e.ci("execute", pid)
    return r, e.plan(pid)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ci-e2e-") as d:
        e = Env(Path(d))
        r = subprocess.run([PY, "-m", "pcim.cli", "init", "--user", "e2e", "--providers", "claude-cli"],
                           env=e.env, capture_output=True, text=True)
        if not check("pcim init creates an identity", r.returncode == 0 and (e.home / "token").exists(), r.stderr):
            return 1
        out = e.api(f"p = ws.create_project(uid, 'Calc'); ws.authorize_workspace(uid, p.id, {str(e.repo)!r}, "
                    f"run_argv=[sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests'], allow_write=True); "
                    f"q = ws.create_project(uid, 'Other'); ws.authorize_workspace(uid, q.id, {str(e.other)!r}); print(p.id)")
        check("a project is authorized for the repo", out.startswith("proj") or len(out) > 4, out)

        # --- plan: nothing runs; external action kept; approve; execute; approve tasks; --done
        before = (e.repo / "calc.py").read_text()
        pid = new_plan(e, "fix add() in calc.py and email raj the diff")
        rec = e.plan(pid)
        check("ci plan compiles and runs nothing", rec["status"] == "awaiting_approval"
              and (e.repo / "calc.py").read_text() == before, rec["status"])
        check("ci plan keeps the external action as yours", any(o["kind"] == "external_action" and o.get("by") == "you"
                                                                for o in rec["spec"]["outcomes"]))
        r = e.ci("execute", pid)
        check("ci execute refuses an unapproved plan", r.returncode != 0 and "approv" in (r.stdout + r.stderr).lower(),
              r.stdout + r.stderr)
        r = e.ci("plan", "approve", pid)
        check("ci plan approve", r.returncode == 0, r.stdout + r.stderr)
        r = e.ci("execute", pid)
        rec = e.plan(pid)
        check("ci execute pauses for approval with exit 6 and names the action", r.returncode == 6 and pending_of(rec)
              and "ci approve" in r.stdout, f"rc={r.returncode} {r.stdout[-300:]}")
        r, rec = drive(e, pid)
        check("approved actions run: the fix is written and verified", (e.repo / "calc.py").read_text() == FIX
              and (rec.get("outcome_success") or {}).get("outcomes", {}).get("o1", {}).get("status") == "met",
              json.dumps(rec.get("outcome_success"))[:300])
        check("the plan is not done while the external action is yours (exit 6, awaiting_you)",
              r.returncode == 6 and rec["status"] == "awaiting_you", f"rc={r.returncode} {rec['status']}")
        check("three separate states are recorded", all(k in rec for k in ("outcome_success", "plan_completion",
                                                                             "authorization_compliance")))
        r = e.ci("execute", pid, "--done", "o1")
        check("--done refuses an outcome CI owns", r.returncode != 0, r.stdout + r.stderr)
        r = e.ci("execute", pid, "--done", "o2")
        check("--done on your action completes the plan (exit 0)", r.returncode == 0 and e.plan(pid)["status"] == "done",
              f"rc={r.returncode} {r.stdout[-300:]} {r.stderr[-300:]}")

        # --- deny: the step is not done, and nothing is written
        subprocess.run(["git", "-C", str(e.repo), "checkout", "-q", "--", "calc.py"], check=True)
        e.script(SESSION)
        pid2 = new_plan(e, "fix add() in calc.py and email raj the diff")
        e.ci("plan", "approve", pid2)
        r, rec = drive(e, pid2, approve=False)
        check("ci deny: the plan is not done, nothing is written", rec["status"] in ("incomplete", "awaiting_you", "waiting")
              and rec["status"] != "done" and (e.repo / "calc.py").read_text() == before
              and not (rec.get("authorization_compliance") or {"clean": True}).get("clean", True) in (True,),
              f"{rec['status']} {json.dumps(rec.get('authorization_compliance'))[:200]}")

        # --- restart recovery: kill ci execute mid-step; the next run says interrupted; --retry runs it again
        e.script(SESSION)
        pid3 = new_plan(e, "fix add() in calc.py and email raj the diff")
        e.ci("plan", "approve", pid3)
        e.mode("sleep", True)
        code = "import sys; from pcim.terminal import main; sys.exit(main(sys.argv[1:]))"
        proc = subprocess.Popen([PY, "-c", code, "execute", pid3], cwd=e.repo, env=e.env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        for _ in range(60):
            time.sleep(0.5)
            if (e.plan(pid3).get("contract_state") or {}).get("status") == "in_progress":
                break
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        e.mode("sleep", False)
        r = e.ci("execute", pid3)
        rec = e.plan(pid3)
        check("restart recovery: a killed run reads interrupted (exit 4), never silently re-run",
              r.returncode == 4 and rec["status"] == "interrupted", f"rc={r.returncode} {rec['status']}")
        r = e.ci("execute", pid3)
        check("an interrupted plan does not run again by itself", e.plan(pid3)["status"] == "interrupted", r.stdout[-200:])
        e.script(SESSION)
        r = e.ci("execute", pid3, "--retry", "contract")
        check("--retry runs the interrupted plan again, deliberately", r.returncode == 6 and pending_of(e.plan(pid3)),
              f"rc={r.returncode} {r.stdout[-300:]} {r.stderr[-300:]}")

        # --- Stop-all while waiting: halt cancels; approve fails; execute reads halted; resume + retry works
        r = e.ci("halt")
        check("ci halt cancels the pending action", r.returncode == 0 and "cancelled" in r.stdout, r.stdout)
        rec = e.plan(pid3)
        r = e.ci("execute", pid3)
        check("after Stop-all the plan reads halted", e.plan(pid3)["status"] == "halted", f"{e.plan(pid3)['status']}")
        stale = pending_of(rec)
        if stale:
            r = e.ci("approve", stale[0])
            check("a cancelled action cannot be approved afterwards", r.returncode != 0, r.stdout + r.stderr)
        r = e.ci("execute", pid3, "--retry", "contract")
        check("while halted, a retry does not run", e.plan(pid3)["status"] == "halted", f"{e.plan(pid3)['status']}")
        r = e.ci("resume")
        check("ci resume", r.returncode == 0, r.stderr)
        subprocess.run(["git", "-C", str(e.repo), "checkout", "-q", "--", "calc.py"], check=True)
        e.script(SESSION)
        r, rec = drive(e, pid3) if e.ci("execute", pid3, "--retry", "contract").returncode in (0, 6) else (r, e.plan(pid3))
        check("after resume, a retried plan completes its CI work", (rec.get("outcome_success") or {}).get("outcomes", {})
              .get("o1", {}).get("status") == "met", f"{rec['status']} {json.dumps(rec.get('contract_state'))[:300]}")

        # --- cancel
        e.script(SESSION)
        pid4 = new_plan(e, "fix add() in calc.py and email raj the diff")
        e.ci("plan", "approve", pid4)
        e.ci("execute", pid4)
        waiting = pending_of(e.plan(pid4))
        r = e.ci("execute", pid4, "--cancel")
        check("--cancel cancels the plan (exit 0)", r.returncode == 0 and e.plan(pid4)["status"] == "cancelled",
              f"rc={r.returncode} {e.plan(pid4)['status']}")
        if waiting:
            r = e.ci("approve", waiting[0])
            check("an action of a cancelled plan cannot be approved", r.returncode != 0, r.stdout + r.stderr)

        # --- permission boundaries
        subprocess.run(["git", "-C", str(e.repo), "checkout", "-q", "--", "calc.py"], check=True)
        e.script([{"text": "Writing outside.", "tool_calls": [{"name": "write_file", "input": {
            "path": "../escaped.py", "content": "x = 1\n", "reason": "test"}}]}, {"text": "Done.", "tool_calls": []}])
        pid5 = new_plan(e, "fix add() in calc.py and email raj the diff")
        e.ci("plan", "approve", pid5)
        r, rec = drive(e, pid5)
        check("a write outside the authorized folder never happens", not (e.root / "escaped.py").exists()
              and rec["status"] != "done", rec["status"])
        r = e.ci("execute", pid5, cwd=e.other)
        check("a plan does not run from another project's folder", r.returncode != 0
              and "project" in (r.stdout + r.stderr).lower(), r.stdout + r.stderr)
        outside = e.root / "nowhere"
        outside.mkdir()
        r = e.ci("plan", "read calc.py and summarise it", cwd=outside)
        pid6 = next((w for w in r.stdout.split() if w.startswith("plan_")), None)
        check("outside an authorized folder, a plan that reads files is blocked",
              pid6 is not None and e.plan(pid6)["status"] == "blocked", r.stdout[-300:])

        # --- provider failure
        e.script(SESSION)
        pid7 = new_plan(e, "fix add() in calc.py and email raj the diff")
        e.ci("plan", "approve", pid7)
        e.mode("fail", True)
        r = e.ci("execute", pid7)
        e.mode("fail", False)
        check("a provider failure leaves the plan failed (exit 4) and retryable", r.returncode == 4
              and e.plan(pid7)["status"] in ("failed", "halted"), f"rc={r.returncode} {e.plan(pid7)['status']}")

        # --- ci do: routed work in the folder (direct, clarify, plan)
        (e.repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        e.script(SESSION)
        r = e.ci("do", "fix add() in calc.py, it subtracts")
        waiting = [ln.split()[3] for ln in r.stdout.splitlines() if "waiting for you:" in ln]
        check("ci do routes a clear request direct and waits for approval before writing (exit 6)",
              r.returncode == 6 and "─ direct:" in r.stdout and waiting and "a - b" in (e.repo / "calc.py").read_text(),
              f"rc={r.returncode} {r.stdout[-300:]} {r.stderr[-300:]}")
        for _ in range(4):
            if not waiting:
                break
            r = e.ci("approve", waiting[0])
            waiting = [ln.split()[3] for ln in r.stdout.splitlines() if "waiting for you:" in ln]
        check("ci approve continues the ci do session to the end; the fix is written",
              (e.repo / "calc.py").read_text() == FIX and "Fixed add()" in r.stdout, r.stdout[-300:] + r.stderr[-300:])
        e.script(SESSION)
        r = e.ci("do", "redo the cleanup we talked about")
        check("ci do asks first, without a model call, when the request rests on what CI was not given",
              r.returncode == 6 and "ci do answer" in r.stdout and not (e.state / "n").exists(),
              f"rc={r.returncode} {r.stdout[-300:]}")
        before = (e.repo / "calc.py").read_text()
        r = e.ci("do", "remove all the unused helpers from calc.py")
        check("ci do shows a plan for a broad removal and changes nothing",
              r.returncode in (0, 6) and "─ plan:" in r.stdout and (e.repo / "calc.py").read_text() == before
              and not (e.state / "n").exists(), f"rc={r.returncode} {r.stdout[-300:]} {r.stderr[-300:]}")
        r = e.ci("do", "list")
        check("ci do list shows each request with its route", r.returncode == 0 and
              all(w in r.stdout for w in ("direct", "clarify", "plan")), r.stdout[-400:])

        # --- the other commands
        r = e.ci("mesh")
        check("ci mesh lists this machine's compute", r.returncode == 0 and r.stdout.strip() != "", r.stderr[-300:])
        r = e.ci("twin")
        check("ci twin prints the twin's picture", r.returncode == 0 and r.stdout.strip() != "", r.stderr[-300:])
        r = e.ci("audit")
        check("ci audit verifies the chain", r.returncode == 0 and ("verified" in r.stdout.lower() or "ok" in r.stdout.lower()),
              r.stdout[-300:] + r.stderr[-300:])
        r = e.ci("learn", "explain what a closure is")
        check("ci learn answers in learn mode", r.returncode == 0 and r.stdout.strip() != "", r.stdout[-300:] + r.stderr[-300:])
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
