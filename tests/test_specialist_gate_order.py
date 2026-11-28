"""Both specialist completion gates (a work item's evidence and the run's final verification) order
tasks by when they ran, not when they were proposed. With approvals, a test proposed before a write
can run after it, and two writes to one file can land in either order. Synthetic receipts test the
gate and never claim real confined execution; what a test run proved is still bound to the snapshot
it tested."""
import hashlib
from types import SimpleNamespace

from pcim.orchestration import Coordinator
from pcim.sandbox import snapshot_digest


def at(second):
    return f"2026-11-28T00:00:{second:02d}+00:00"


def write(tid, path, content, proposed, finished):
    return {'id': tid, 'tool': 'write_file', 'status': 'succeeded', 'reason': None, 'args': {'path': path},
            'created_at': at(proposed), 'started_at': at(finished - 1), 'finished_at': at(finished),
            'result': {'sha256_after': hashlib.sha256(content.encode()).hexdigest()}}


def ran_tests(tid, root, proposed, started):
    return {'id': tid, 'tool': 'run_tests', 'status': 'succeeded', 'reason': None, 'args': {},
            'created_at': at(proposed), 'started_at': at(started), 'finished_at': at(started + 1),
            'result': {'exit_code': 0, 'timed_out': False, 'confinement': {
                'backend': 'bubblewrap', 'verified': True, 'network': 'isolated',
                'filesystem': 'disposable-copy', 'host_writes': False, 'snapshot_sha256': snapshot_digest(root)}}}


def judge(root, tasks):
    """(the work item's problems, the run's status) for these tasks in one session."""
    coordinator = Coordinator(None, SimpleNamespace(workspace_auth=lambda *_: {'spec': {'root': str(root)}}), None, None)
    coordinator.tasks = lambda *_: sorted(tasks, key=lambda t: (t['created_at'], t['id']))  # as Coordinator.tasks
    work = {'id': 'w', 'artifacts': ['calc.py'], 'require_write': True, 'require_tests': True}
    attempt = {'work_id': 'w', 'status': 'succeeded', 'session_id': 's'}
    run = {'work': [work], 'attempts': [attempt], 'events': []}
    problems = coordinator.evidence('u', 'p', work, attempt)['problems']
    coordinator.verify_run('u', 'p', run)
    return problems, run['status']


def test_a_test_proposed_before_the_write_but_run_after_it_counts(tmp_path):
    (tmp_path / 'calc.py').write_text('fixed')
    tasks = [ran_tests('t', tmp_path, proposed=1, started=5), write('w1', 'calc.py', 'fixed', proposed=2, finished=3)]
    assert judge(tmp_path, tasks) == ([], 'succeeded')


def test_a_test_proposed_after_the_write_but_run_before_it_does_not(tmp_path):
    (tmp_path / 'calc.py').write_text('fixed')  # even a snapshot that matches cannot outrank the order
    tasks = [write('w1', 'calc.py', 'fixed', proposed=1, finished=6), ran_tests('t', tmp_path, proposed=2, started=3)]
    problems, status = judge(tmp_path, tasks)
    assert problems == ['no successful tests after last write'] and status == 'incomplete'


def test_a_test_that_started_before_the_write_finished_does_not_count(tmp_path):
    (tmp_path / 'calc.py').write_text('fixed')
    tasks = [write('w1', 'calc.py', 'fixed', proposed=1, finished=4), ran_tests('t', tmp_path, proposed=2, started=4)]
    assert judge(tmp_path, tasks)[0] == ['no successful tests after last write']


def test_two_writes_to_one_file_approved_out_of_order(tmp_path):
    (tmp_path / 'calc.py').write_text('first draft')  # proposed first, landed last: what is on disk
    tasks = [write('w1', 'calc.py', 'first draft', proposed=1, finished=4),
             write('w2', 'calc.py', 'second draft', proposed=2, finished=3),
             ran_tests('t', tmp_path, proposed=3, started=5)]
    assert judge(tmp_path, tasks) == ([], 'succeeded')


def test_rewriting_a_file_supersedes_the_earlier_write_instead_of_voiding_the_work(tmp_path):
    (tmp_path / 'calc.py').write_text('second draft')
    tasks = [write('w1', 'calc.py', 'first draft', proposed=1, finished=2),
             write('w2', 'calc.py', 'second draft', proposed=3, finished=4),
             ran_tests('t', tmp_path, proposed=5, started=6)]
    assert judge(tmp_path, tasks) == ([], 'succeeded')


def test_a_file_changed_outside_alter_after_its_last_write_still_voids_both_gates(tmp_path):
    (tmp_path / 'calc.py').write_text('second draft')
    tasks = [write('w1', 'calc.py', 'first draft', proposed=1, finished=2),
             write('w2', 'calc.py', 'second draft', proposed=3, finished=4),
             ran_tests('t', tmp_path, proposed=5, started=6)]
    (tmp_path / 'calc.py').write_text('edited by someone else')
    problems, status = judge(tmp_path, tasks)
    assert 'failed, denied, pending or stale task evidence' in problems and status == 'incomplete'


def test_records_without_run_times_fall_back_to_when_they_were_proposed(tmp_path):
    (tmp_path / 'calc.py').write_text('fixed')
    old_write = write('w1', 'calc.py', 'fixed', proposed=1, finished=2)
    old_test = ran_tests('t', tmp_path, proposed=2, started=3)
    for t in (old_write, old_test):
        t.pop('started_at'), t.pop('finished_at')
    assert judge(tmp_path, [old_write, old_test]) == ([], 'succeeded')
