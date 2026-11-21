"""Safety audit R6: a goal worker killed while its tests ran left the test process running and the task
"running" for good. A resume then started a second suite next to it. Now each run's process group is
recorded with its owner, and a run whose owner is gone is stopped and its task marked interrupted. A
live owner's run is never touched."""

import json
import os
import subprocess
import sys
import time

import pytest

from pcim.engine import PersonalCI
from pcim.governor import Governor
from pcim.tasks import TaskQueue
from pcim.workspace import Workspace

PY = sys.executable


@pytest.fixture
def q(tmp_path):
    ci = PersonalCI(tmp_path / "data", seed=0)
    ci.onboard("u", "U", providers=("mock",))
    repo = tmp_path / "repo"
    repo.mkdir()
    ws = Workspace(ci)
    p = ws.create_project("u", "R")
    run_dir = tmp_path / "data" / "run"
    # the test command checks that its own run is recorded while it runs
    ws.authorize_workspace("u", p.id, str(repo), run_argv=[PY, "-c", f"import os, sys; sys.exit(0 if len(os.listdir({str(run_dir)!r})) == 1 else 1)"],
                           allow_write=True)
    return TaskQueue(ws, Governor(ws)), p, run_dir


def _dead_pid() -> int:
    p = subprocess.Popen([PY, "-c", "pass"])
    p.wait()
    return p.pid


def _alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_a_run_is_recorded_while_it_runs_and_forgotten_after(q):
    queue, p, run_dir = q
    t = queue.approve("u", queue.propose("u", p.id, "run_tests", {"reason": "r"}).id)
    assert t.status == "succeeded" and not t.reason  # the suite saw its own record
    assert list(run_dir.glob("task_*.json")) == []


def _orphan(queue, p, run_dir, owner: int):
    t = queue.propose("u", p.id, "run_tests", {"reason": "r"})
    queue.store.execute("UPDATE tasks SET status='running' WHERE id=?", (t.id,))
    sleeper = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"], start_new_session=True)
    run_dir.mkdir(exist_ok=True)
    (run_dir / f"{t.id}.json").write_text(json.dumps({"task": t.id, "pgid": sleeper.pid, "owner": owner}))
    return t, sleeper


def test_a_run_whose_worker_died_is_stopped_and_marked(q):
    queue, p, run_dir = q
    t, sleeper = _orphan(queue, p, run_dir, _dead_pid())
    r = queue.reap_orphans()
    sleeper.wait(timeout=10)
    assert r == {"stopped": 1, "interrupted": 1} and not _alive(sleeper.pid)
    assert queue.get("u", t.id).status == "interrupted" and not list(run_dir.glob("task_*.json"))


def test_a_live_workers_run_is_never_touched(q):
    queue, p, run_dir = q
    t, sleeper = _orphan(queue, p, run_dir, os.getpid())
    try:
        assert queue.reap_orphans() == {"stopped": 0, "interrupted": 0}
        time.sleep(0.2)
        assert _alive(sleeper.pid) and queue.get("u", t.id).status == "running"
    finally:
        os.killpg(sleeper.pid, 9)
        sleeper.wait()


def test_resuming_an_interrupted_goal_stops_the_dead_workers_test_run_first():
    import tempfile
    from pathlib import Path

    from test_alter_goals import RUN, W, runner, start  # noqa: F401
    from pcim.terminal import Here
    d = Path(tempfile.mkdtemp())
    ci = PersonalCI(d / "data", seed=0)
    ci.onboard("u", "U", providers=("mock",))
    repo = d / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    ws = Workspace(ci)
    p = ws.create_project("u", "Calc")
    ws.authorize_workspace("u", p.id, str(repo), run_argv=[PY, "-c", "import sys; sys.path.insert(0, '.'); import calc; "
                                                           "assert calc.add(2, 3) == 5"], allow_write=True)
    here = Here(p.id, "Calc", {"mock"}, str(repo))
    r, _ = runner(ci, here, [W(), RUN(), {"text": "Done."}])
    g = start(r)
    g.status = "running"  # as if its worker had died mid-run
    r.gs.save(g)
    t, sleeper = _orphan(r.agent.queue, p, d / "data" / "run", _dead_pid())
    g = r.run(g.id, resume=True)
    sleeper.wait(timeout=10)
    assert not _alive(sleeper.pid) and r.agent.queue.get("u", t.id).status == "interrupted"
    assert g.status == "done" and g.payload["interruptions"]
