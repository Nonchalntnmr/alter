"""Offline persistence evidence; no model execution is represented by these tests."""
import sqlite3

import pytest
from pcim.run_state import (
    IdempotencyConflict,
    LeaseConflict,
    RevisionConflict,
    RunStateStore,
)
from pcim.sovereignty.crypto import KeyRing
from pcim.store import Store


@pytest.fixture
def env(tmp_path):
    keyring = KeyRing.ephemeral()
    path = tmp_path / 'state.db'
    store = Store(path, keyring)
    for uid, pid in [('u', 'p'), ('u', 'other'), ('v', 'foreign')]:
        key = store.new_project_key(uid)
        store.execute('INSERT INTO projects VALUES (?,?,?,?,?,?,?)',
                      (pid, uid, key, 'now', 'now', 'active', store.seal_json(key, {}, pid)))
    yield store, RunStateStore(store), path, keyring
    store.close()


def test_restart_encryption_approval_and_migration(env):
    _store, runs, path, keyring = env
    snapshot = {'goal': 'SECRET GOAL', 'work': [{'objective': 'SECRET WORK'}],
                'status': 'awaiting_approval', 'approval_id': 'approval-1'}
    run = runs.create('u', 'p', snapshot)
    assert run['revision'] == 1 and 'id' not in snapshot
    second = Store(path, keyring)
    try:
        restored = RunStateStore(second).get('u', 'p', run['id'])
        assert restored == run
        assert restored['approval_id'] == 'approval-1'
        # Existing baseline tables remain intact; repeated migrations are safe.
        assert second.query('SELECT count(*) FROM projects')[0][0] == 3
        RunStateStore(second)
        raw = b''.join(bytes(r[0]) for r in second.query('SELECT payload_ct FROM specialist_runs'))
        assert b'SECRET' not in raw
    finally:
        second.close()


def test_owner_project_filters_all_reads(env):
    _, runs, _, _ = env
    run = runs.create('u', 'p', {})
    for uid, pid in [('v', 'p'), ('u', 'other'), ('v', 'foreign')]:
        for operation in [lambda uid=uid, pid=pid: runs.get(uid, pid, run['id']),
                          lambda uid=uid, pid=pid: runs.save(uid, pid, run['id'], {}, 1),
                          lambda uid=uid, pid=pid: runs.replay(uid, pid, run['id']),
                          lambda uid=uid, pid=pid: runs.append_event(uid, pid, run['id'], {}, 'x')]:
            with pytest.raises(ValueError):
                operation()
    assert runs.list('u', 'other') == []


def test_cross_connection_cas_and_atomic_rollback(env):
    _, runs, path, keyring = env
    run = runs.create('u', 'p', {})
    second = Store(path, keyring)
    try:
        other = RunStateStore(second)
        saved = other.save('u', 'p', run['id'], {'goal': 'new'}, 1)
        with pytest.raises(RevisionConflict):
            runs.save('u', 'p', run['id'], {'goal': 'lost'}, 1)
        assert runs.get('u', 'p', run['id']) == saved
        with pytest.raises(ValueError):
            runs.save('u', 'p', run['id'], {'bad': float('nan')}, 2)
        assert runs.get('u', 'p', run['id']) == saved
    finally:
        second.close()


def test_causal_idempotent_bounded_replay(env):
    _, runs, _, _ = env
    rid = runs.create('u', 'p', {})['id']
    other = runs.create('u', 'other', {})['id']
    seq = runs.append_event('u', 'p', rid, {'kind': 'started'}, 'start')
    assert runs.append_event('u', 'p', rid, {'kind': 'started'}, 'start') == seq
    with pytest.raises(IdempotencyConflict):
        runs.append_event('u', 'p', rid, {'kind': 'changed'}, 'start')
    with pytest.raises(ValueError):
        runs.append_event('u', 'other', other, {'parent_seq': seq}, 'bad')
    end = runs.append_event('u', 'p', rid, {'kind': 'succeeded', 'parent_seq': seq}, 'end')
    assert len(runs.replay('u', 'p', rid, limit=1)) == 1
    assert runs.replay('u', 'p', rid, after_seq=seq)[0]['seq'] == end
    with pytest.raises(ValueError):
        runs.replay('u', 'p', rid, limit=1001)


def test_lease_fences_and_project_lock(env):
    _, runs, path, keyring = env
    token = runs.claim_lease('u', 'p', 'work', 'a')
    with pytest.raises(LeaseConflict):
        runs.claim_lease('u', 'p', 'work', 'b')
    runs.release_lease('u', 'p', 'work', 'a', token)
    newer = runs.claim_lease('u', 'p', 'work', 'b')
    assert newer > token
    with pytest.raises(LeaseConflict):
        runs.release_lease('u', 'p', 'work', 'a', token)
    second = Store(path, keyring)
    try:
        other = RunStateStore(second)
        with runs.lock('u', 'p'):
            with pytest.raises(LeaseConflict), other.lock('u', 'p'):
                pass
            with other.lock('u', 'other'):
                pass
        with other.lock('u', 'p'):
            pass
    finally:
        second.close()


def test_attempt_dedup_restart_escalation_and_provenance(env):
    _, runs, path, keyring = env
    run = runs.create('u', 'p', {'status': 'running', 'attempts': []})
    attempt = {'id': 'a1', 'action_key': 'write:1', 'status': 'started'}
    run = runs.record_attempt('u', 'p', run['id'], attempt, run['revision'])
    second = Store(path, keyring)
    try:
        other = RunStateStore(second)
        recovered = other.reconcile('u', 'p', run['id'], run['revision'])
        assert recovered['attempts'][0]['status'] == 'escalated'
        assert recovered['status'] == 'escalated'
        with pytest.raises(IdempotencyConflict):
            other.record_attempt('u', 'p', run['id'], dict(attempt, id='a2'), recovered['revision'])
    finally:
        second.close()
    done = runs.create('u', 'p', {'attempts': []})
    success = {'id': 'a', 'action_key': 'effect', 'status': 'succeeded'}
    with pytest.raises(ValueError, match='provenance'):
        runs.record_attempt('u', 'p', done['id'], success, 1)
    success['evidence_refs'] = ['executor:receipt:hash']
    done = runs.record_attempt('u', 'p', done['id'], success, 1)
    assert runs.record_attempt('u', 'p', done['id'], success, 1) == done
    with pytest.raises(IdempotencyConflict):
        runs.record_attempt('u', 'p', done['id'], dict(success, id='retry'), 2)


def test_reconcile_preserves_approval_wait_and_completed_work(env):
    _, runs, _, _ = env
    run = runs.create('u', 'p', {'status': 'awaiting_approval', 'approval_id': 'q',
                               'attempts': [{'id': 'a', 'status': 'awaiting_approval'}]})
    restored = runs.reconcile('u', 'p', run['id'], 1)
    assert restored['status'] == 'awaiting_approval' and restored['approval_id'] == 'q'
    assert restored['attempts'] == run['attempts']


def test_database_content_is_not_plaintext(env):
    store, runs, _, _ = env
    rid = runs.create('u', 'p', {})['id']
    runs.append_event('u', 'p', rid, {'secret': 'PRIVATE EVENT'}, 'e')
    ct = store.query('SELECT payload_ct FROM specialist_events')[0][0]
    assert b'PRIVATE EVENT' not in ct
    assert sqlite3.sqlite_version


def test_project_deletion_removes_optional_ledger(env):
    store, runs, _, _ = env
    rid = runs.create('u', 'p', {})['id']
    runs.append_event('u', 'p', rid, {'kind': 'x'}, 'x')
    runs.claim_lease('u', 'p', 'resource', 'holder')
    keep = runs.create('u', 'other', {})
    key = store.query("SELECT key_id FROM projects WHERE id='p'")[0][0]
    store.delete_project_rows('p', key)
    for table in ['specialist_runs', 'specialist_revisions', 'specialist_events', 'specialist_leases']:
        assert not store.query(f'SELECT * FROM {table} WHERE project_id=?', ('p',))
    assert runs.get('u', 'other', keep['id']) == keep


def test_expired_fences_cannot_authorize_dispatch(env, monkeypatch):
    _, runs, _, _ = env
    monkeypatch.setattr('pcim.run_state.time.time', lambda: 100)
    fence = runs.claim_lease('u', 'p', 'w', 'a', ttl_s=1)
    runs.check_lease('u', 'p', 'w', 'a', fence)
    monkeypatch.setattr('pcim.run_state.time.time', lambda: 102)
    with pytest.raises(LeaseConflict):
        runs.check_lease('u', 'p', 'w', 'a', fence)
    next_fence = runs.claim_lease('u', 'p', 'w', 'a', ttl_s=1)
    assert next_fence > fence
    with pytest.raises(LeaseConflict):
        runs.check_lease('u', 'p', 'w', 'a', fence)


def test_terminal_receipt_cannot_be_erased_by_snapshot_save(env):
    _, runs, _, _ = env
    run = runs.create('u', 'p', {})
    done = runs.record_attempt('u', 'p', run['id'], {
        'id': 'a', 'action_key': 'effect', 'status': 'succeeded', 'evidence_refs': ['receipt']}, 1)
    with pytest.raises(IdempotencyConflict):
        runs.save('u', 'p', run['id'], {'attempts': []}, done['revision'])


def test_process_death_releases_lock_and_committed_snapshot_survives(env):
    import subprocess
    import sys

    _, _runs, path, _keyring = env
    # Child uses an inherited test-only KEK via stdin, never a credential in argv/logs.
    import base64
    import os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from pcim.sovereignty.crypto import KeyRing

    kek = AESGCM.generate_key(bit_length=256)
    child_path = path.parent / 'child.db'
    code = '''
import base64, os, sys
from pcim.sovereignty.crypto import KeyRing
from pcim.store import Store
from pcim.run_state import RunStateStore
s=Store(sys.argv[1], KeyRing(base64.b64decode(sys.stdin.readline())))
k=s.new_project_key('u')
s.execute('INSERT INTO projects VALUES (?,?,?,?,?,?,?)', ('p','u',k,'now','now','active',s.seal_json(k,{},'p')))
r=RunStateStore(s)
with r.lock('u','p'):
    r.create('u','p',{'status':'awaiting_approval','approval_id':'q'})
    os._exit(17)
'''
    result = subprocess.run([sys.executable, '-c', code, str(child_path)],
                            input=base64.b64encode(kek).decode() + '\n', text=True,
                            capture_output=True, check=False, timeout=15, env=os.environ.copy())
    assert result.returncode == 17, result.stderr
    reopened = Store(child_path, KeyRing(kek))
    try:
        recovered = RunStateStore(reopened)
        with recovered.lock('u', 'p'):
            assert recovered.list('u', 'p')[0]['approval_id'] == 'q'
    finally:
        reopened.close()


def test_online_backup_includes_ledger_and_refuses_live_worker(env):
    from pcim.backup import BackupError, backup

    store, _runs, path, keyring = env
    # backup expects the application's conventional database basename.
    home = path.parent / 'home'
    home.mkdir()
    target = Store(home / 'pcim.db', keyring)
    store._db.backup(target._db)
    try:
        ledger = RunStateStore(target)
        run = ledger.create('u', 'p', {'goal': 'persist'})
        with ledger.lock('u', 'p'), pytest.raises(BackupError, match='running'):
            backup(home, path.parent / 'blocked')
        backup(home, path.parent / 'backup')
        restored = Store(path.parent / 'backup' / 'pcim.db', keyring)
        try:
            assert RunStateStore(restored).get('u', 'p', run['id']) == run
        finally:
            restored.close()
    finally:
        target.close()


def test_snapshot_adapter_cannot_duplicate_completed_action(env):
    _, runs, _, _ = env
    run = runs.create('u', 'p', {})
    attempt = {'id': 'a', 'action_key': 'effect', 'status': 'succeeded', 'evidence_refs': ['receipt']}
    done = runs.record_attempt('u', 'p', run['id'], attempt, 1)
    with pytest.raises(IdempotencyConflict):
        runs.save('u', 'p', run['id'], dict(done, attempts=[attempt, dict(attempt, id='again')]), 2)


def test_snapshot_revision_history_survives_restart_and_conflict(env):
    _, runs, path, keyring = env
    run = runs.create('u', 'p', {'goal': 'one'})
    runs.save('u', 'p', run['id'], {'goal': 'two'}, 1)
    with pytest.raises(RevisionConflict):
        runs.save('u', 'p', run['id'], {'goal': 'discard'}, 1)
    second = Store(path, keyring)
    try:
        ledger = RunStateStore(second)
        assert [x['goal'] for x in ledger.history('u', 'p', run['id'])] == ['one', 'two']
        assert len(ledger.history('u', 'p', run['id'], after_revision=1, limit=1)) == 1
        with pytest.raises(ValueError):
            ledger.history('u', 'other', run['id'])
    finally:
        second.close()
