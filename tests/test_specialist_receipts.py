"""Synthetic receipts test the gate, never claim real confined execution."""
import copy

import pytest

from pcim.orchestration import Coordinator, verified_test
from pcim.sandbox import snapshot_digest


def receipt(root=None):
    # A synthetic receipt for the gate; with `root`, it names that folder's current snapshot.
    return {'tool': 'run_tests', 'status': 'succeeded', 'result': {
        'exit_code': 0, 'timed_out': False, 'confinement': {
            'backend': 'bubblewrap', 'verified': True, 'network': 'isolated',
            'filesystem': 'disposable-copy', 'host_writes': False,
            'snapshot_sha256': snapshot_digest(root) if root is not None else '0' * 64}}}


@pytest.mark.parametrize('field,value', [
    ('exit_code', False), ('exit_code', None), ('exit_code', 1),
    ('timed_out', True), ('timed_out', None),
])
def test_incomplete_or_failed_execution_does_not_qualify(field, value):
    task = receipt()
    task['result'][field] = value
    assert not verified_test(task)


@pytest.mark.parametrize('field,value', [
    ('backend', 'host'), ('verified', False), ('verified', 1),
    ('network', 'host'), ('filesystem', 'host'), ('host_writes', True),
    ('snapshot_sha256', None), ('snapshot_sha256', 'short'), ('snapshot_sha256', 0),
])
def test_unverified_or_weaker_confinement_does_not_qualify(field, value):
    task = receipt()
    task['result']['confinement'][field] = value
    assert not verified_test(task)


def test_legacy_host_receipt_and_missing_receipt_do_not_qualify():
    task = receipt()
    task['result'].pop('confinement')
    assert not verified_test(task)
    task.pop('result')
    assert not verified_test(task)
    assert verified_test(receipt())


def test_both_work_and_final_completion_reject_legacy_receipt(tmp_path):
    import hashlib
    from types import SimpleNamespace

    artifact = tmp_path / 'result.txt'
    artifact.write_text('actual artifact')
    write = {'id': 'write', 'tool': 'write_file', 'status': 'succeeded', 'created_at': '1',
             'args': {'path': artifact.name},
             'result': {'sha256_after': hashlib.sha256(artifact.read_bytes()).hexdigest()}}
    test = dict(receipt(tmp_path), id='test', created_at='2')
    legacy = copy.deepcopy(test)
    legacy['result'].pop('confinement')
    coordinator = Coordinator(None, SimpleNamespace(workspace_auth=lambda *_: {'spec': {'root': str(tmp_path)}}), None, None)
    coordinator.tasks = lambda *_: [write, legacy]
    work = {'id': 'w', 'artifacts': [], 'require_write': True, 'require_tests': True}
    attempt = {'work_id': 'w', 'status': 'succeeded', 'session_id': 's'}
    assert coordinator.evidence('u', 'p', work, attempt)['problems']
    run = {'work': [work], 'attempts': [attempt], 'events': []}
    coordinator.verify_run('u', 'p', run)
    assert run['status'] == 'incomplete'
    coordinator.tasks = lambda *_: [write, test]
    assert not coordinator.evidence('u', 'p', work, attempt)['problems']
    coordinator.verify_run('u', 'p', run)
    assert run['status'] == 'succeeded'


@pytest.mark.parametrize('value', ['legacy', [0], 1])
def test_malformed_receipt_cannot_qualify(value):
    task = receipt()
    task['result'] = value
    assert not verified_test(task)
    task = receipt()
    task['result']['confinement'] = value
    assert not verified_test(task)


def test_host_receipt_never_qualifies():
    from pcim.tools import HOST_TEST_RECEIPT
    task = receipt()
    task['result']['confinement'] = dict(HOST_TEST_RECEIPT)
    assert not verified_test(task)


def test_receipt_for_other_files_cannot_complete_work_or_run(tmp_path):
    """A passing confined run proves only the snapshot it tested: a later change voids it."""
    import hashlib
    from types import SimpleNamespace

    artifact = tmp_path / 'result.txt'
    artifact.write_text('tested content')
    test = dict(receipt(tmp_path), id='test', created_at='2')
    helper = tmp_path / 'helper.py'
    helper.write_text('changed after the tests ran\n')  # not written through ALTER, not tested
    write = {'id': 'write', 'tool': 'write_file', 'status': 'succeeded', 'created_at': '1',
             'args': {'path': artifact.name},
             'result': {'sha256_after': hashlib.sha256(artifact.read_bytes()).hexdigest()}}
    coordinator = Coordinator(None, SimpleNamespace(workspace_auth=lambda *_: {'spec': {'root': str(tmp_path)}}), None, None)
    coordinator.tasks = lambda *_: [write, test]
    work = {'id': 'w', 'artifacts': [], 'require_write': True, 'require_tests': True}
    attempt = {'work_id': 'w', 'status': 'succeeded', 'session_id': 's'}
    assert 'the files that passed the tests are not the current files' in coordinator.evidence('u', 'p', work, attempt)['problems']
    run = {'work': [work], 'attempts': [attempt], 'events': []}
    coordinator.verify_run('u', 'p', run)
    assert run['status'] == 'incomplete' and not run['verification']['tests_after_writes']
    helper.unlink()  # back to exactly the tested files
    assert not coordinator.evidence('u', 'p', work, attempt)['problems']
    coordinator.verify_run('u', 'p', run)
    assert run['status'] == 'succeeded'
