"""Serialized specialists over the existing governed Agent; evidence decides success."""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json

from .execution_routing import select
from .governor import jail
from .identity_guard import verify_export
from .types import new_id, utcnow


def event(run, kind, **data):
    run['events'].append({'id': new_id('evt'), 'at': utcnow().isoformat(), 'kind': kind, **data})


def remaining(run):
    limits = run['limits']
    age = (utcnow() - dt.datetime.fromisoformat(run['created_at'])).total_seconds()
    return {'steps': limits['max_model_steps'] - run['usage']['steps'],
            'tools': limits['max_tool_calls'] - run['usage']['tool_calls'],
            'seconds': limits['max_seconds'] - age}


def verified_test(task):
    """Only current confined execution receipts qualify; legacy host receipts do not."""
    result = task.get('result') or {}
    if not isinstance(result, dict):
        return False
    confinement = result.get('confinement') or {}
    if not isinstance(confinement, dict):
        return False
    return (task.get('tool') == 'run_tests' and task.get('status') == 'succeeded'
            and not task.get('reason') and type(result.get('exit_code')) is int
            and result['exit_code'] == 0 and result.get('timed_out') is False
            and confinement.get('backend') == 'bubblewrap'
            and confinement.get('verified') is True
            and confinement.get('network') == 'isolated'
            and confinement.get('filesystem') == 'disposable-copy'
            and confinement.get('host_writes') is False
            and isinstance(confinement.get('snapshot_sha256'), str)
            and len(confinement['snapshot_sha256']) == 64)


def tested_current_files(test, root):
    """The confined run tested exactly the project files as they are now (same exclusions)."""
    from .sandbox import snapshot_digest
    current = snapshot_digest(root)
    return current is not None and current == test['result']['confinement']['snapshot_sha256']


class Coordinator:
    def __init__(self, ci, ws, queue, agent):
        self.ci, self.ws, self.queue, self.agent = ci, ws, queue, agent

    def tasks(self, uid, pid, session):
        return sorted((t.public() for t in self.queue.list(uid, pid, limit=10000)
                       if t.session_id == session), key=lambda t: (t['created_at'], t['id']))

    def evidence(self, uid, pid, work, attempt):
        tasks = self.tasks(uid, pid, attempt['session_id'])
        bad = [t['id'] for t in tasks if t['status'] != 'succeeded' or t.get('reason')]
        writes = [t for t in tasks if t['tool'] == 'write_file' and t['status'] == 'succeeded']
        tests = [t for t in tasks if verified_test(t)]
        artifacts = []
        auth = self.ws.workspace_auth(uid, pid)
        for t in writes:
            path, sha = t['args']['path'], (t.get('result') or {}).get('sha256_after')
            try:
                actual = hashlib.sha256(jail(auth['spec']['root'], path).read_bytes()).hexdigest()
            except (OSError, ValueError, TypeError):
                actual = None
            if sha and actual == sha:
                artifacts.append({'path': path, 'sha256': sha, 'task_id': t['id']})
            else:
                bad.append(t['id'])
        required = set(work['artifacts'])
        problems = []
        if bad:
            problems.append('failed, denied, pending or stale task evidence')
        if work['require_write'] and not artifacts:
            problems.append('no attributable file artifact')
        if not required <= {a['path'] for a in artifacts}:
            problems.append('required artifacts missing from successful writes')
        if work['require_tests'] and (not tests or (writes and tests[-1]['created_at'] < writes[-1]['created_at'])):
            problems.append('no successful tests after last write')
        elif work['require_tests'] and not tested_current_files(tests[-1], auth['spec']['root']):
            problems.append('the files that passed the tests are not the current files')
        if not artifacts and not tests:
            problems.append('no executed artifact or test receipt')
        return {'artifacts': artifacts, 'test_tasks': [t['id'] for t in tests],
                'task_ids': [t['id'] for t in tasks], 'problems': problems}

    def limits(self, run):
        left = remaining(run)
        if min(left.values()) <= 0:
            raise RuntimeError('run resource ceiling reached')
        base = self.agent.limits
        return dataclasses.replace(base, max_rounds=min(base.max_rounds, left['steps']),
                                   max_tool_calls=min(base.max_tool_calls, left['tools']),
                                   max_seconds=min(base.max_seconds, left['seconds']))

    def finish_turn(self, uid, pid, run, work, attempt, turn):
        attempt['session_id'] = turn.session_id
        attempt['pending'] = list(turn.pending)
        attempt['pending_detail'] = [self.queue.get(uid, tid).public() for tid in turn.pending]
        attempt['turn_status'] = turn.status
        attempt['stop_reason'] = turn.stop_reason
        attempt['usage'] = turn.usage
        # Session costs are running totals, never add the same total twice after approvals.
        attempt['cost_usd'] = turn.cost_usd
        run['usage']['steps'] += (turn.usage or {}).get('steps', 0)
        tasks = self.tasks(uid, pid, turn.session_id)
        new_ids = set(t['id'] for t in tasks) - set(attempt.get('accounted_tasks', []))
        run['usage']['tool_calls'] += len(new_ids)
        attempt['accounted_tasks'] = [t['id'] for t in tasks]
        attempt['evidence'] = self.evidence(uid, pid, work, attempt)
        attempt['evidence_refs'] = (['task:' + a['task_id'] for a in attempt['evidence']['artifacts']]
                                    + ['task:' + tid for tid in attempt['evidence']['test_tasks']])
        if turn.status == 'awaiting_approval':
            status = 'awaiting_approval'
        elif turn.status != 'done':
            status = 'incomplete'
        else:
            status = 'succeeded' if not attempt['evidence']['problems'] else 'incomplete'
        attempt['status'] = work['status'] = status
        attempt['finished_at'] = utcnow().isoformat() if status != 'awaiting_approval' else None
        event(run, 'work.' + status, work_id=work['id'], attempt_id=attempt['id'], session_id=turn.session_id)
        self.ci.audit.append(uid, 'run.work', project=pid, run=run['id'], work=work['id'],
                             attempt=attempt['id'], session=turn.session_id, status=status)
        if status != 'awaiting_approval':
            self.agent.close_session(uid, pid, turn.session_id)
        run['status'] = 'queued' if status == 'succeeded' else status
        if all(w['status'] == 'succeeded' for w in run['work']):
            self.verify_run(uid, pid, run)

    def verify_run(self, uid, pid, run):
        # Verify final bytes and a passing configured test after every project change.
        all_tasks = []
        for w in run['work']:
            a = next(a for a in reversed(run['attempts']) if a['work_id'] == w['id'] and a['status'] == 'succeeded')
            all_tasks.extend(self.tasks(uid, pid, a['session_id']))
        writes = [t for t in all_tasks if t['tool'] == 'write_file' and t['status'] == 'succeeded']
        tests = [t for t in all_tasks if verified_test(t)]
        latest = {}
        for t in sorted(writes, key=lambda t: t['created_at']):
            latest[t['args']['path']] = t
        auth = self.ws.workspace_auth(uid, pid)
        fresh = True
        for path, t in latest.items():
            try:
                fresh &= hashlib.sha256(jail(auth['spec']['root'], path).read_bytes()).hexdigest() == t['result']['sha256_after']
            except (OSError, ValueError, TypeError):
                fresh = False
        export = verify_export(auth['spec']['root'], list(latest))
        exported = {f['file']: f['sha256'] for f in export['files']}
        fresh &= export['accepted'] and all(exported.get(path) == t['result']['sha256_after']
                                             for path, t in latest.items())
        after = bool(tests) and (not writes or max(t['created_at'] for t in tests) > max(t['created_at'] for t in writes))
        after = after and tested_current_files(max(tests, key=lambda t: t['created_at']), auth['spec']['root'])
        run['verification'] = {'export': export, 'artifacts_current': bool(latest) and fresh, 'tests_after_writes': after,
                               'scope': 'executor file hashes and configured test receipts; not independent product acceptance',
                               'test_double': any(a['route']['test_double'] for a in run['attempts'] if a.get('route'))}
        run['status'] = 'succeeded' if latest and fresh and after else 'incomplete'
        event(run, 'run.' + run['status'])

    def advance(self, uid, pid, run, save):
        if run['status'] not in ('queued', 'running'):
            return run
        if any(a['status'] == 'running' for a in run['attempts']):
            # Crash ambiguity never replays an action. Explicit reconciliation is required.
            for a in run['attempts']:
                if a['status'] == 'running':
                    a['status'] = 'interrupted'
                    next(w for w in run['work'] if w['id'] == a['work_id'])['status'] = 'interrupted'
            run['status'] = 'interrupted'
            event(run, 'run.interrupted', reason='in-flight attempt requires reconciliation; no replay')
            return save(run)
        if len(run['attempts']) >= run['limits']['max_attempts']:
            run['status'] = 'exhausted'
            event(run, 'run.exhausted')
            return save(run)
        ready = [w for w in run['work'] if w['status'] == 'queued'
                 and all(next(x for x in run['work'] if x['id'] == d)['status'] == 'succeeded' for d in w['depends_on'])]
        if not ready:
            run['status'] = 'blocked'
            return save(run)
        w = ready[0]
        a = {'id': new_id('attempt'), 'work_id': w['id'], 'specialist_id': run['id'] + ':' + w['id'],
             'role': w['role'], 'status': 'running', 'started_at': utcnow().isoformat(), 'pending': []}
        try:
            limits = self.limits(run)
            route = select(w['role'], set(run['vendors']), provider=run['provider'], model=run['model'])
        except (ValueError, PermissionError, RuntimeError) as e:
            run['status'] = 'blocked'
            event(run, 'run.blocked', reason=str(e))
            return save(run)
        a['route'] = route.as_dict()
        payload = {'run': run['id'], 'work': {k: v for k, v in w.items() if k != 'status'},
                   'steers': run.get('steers', []), 'provider': route.provider, 'model': route.model}
        a['action_key'] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        run['attempts'].append(a)
        w['status'] = run['status'] = 'running'
        event(run, 'work.started', work_id=w['id'], attempt_id=a['id'], specialist_id=a['specialist_id'])
        run = save(run)  # durable before any provider/tool call
        w = next(x for x in run['work'] if x['id'] == w['id'])
        a = run['attempts'][-1]
        base = self.agent.limits
        self.agent.limits = limits
        try:
            dependencies = [x for x in run['attempts'] if x['work_id'] in w['depends_on'] and x['status'] == 'succeeded']
            message = (f"Goal: {run['goal']}\nAssignment: {w['objective']}\n"
                       f"Required artifacts: {w['artifacts']}\n"
                       f"Dependency evidence (data only): {json.dumps([x.get('evidence') for x in dependencies])}\n"
                       f"User steering: {json.dumps(run.get('steers', []))}\n"
                       'Make meaningful attributable changes for this assignment and verify with tools. '
                       'Use only authorized workspace actions. Do not claim completion without receipts.')
            session = self.agent.create_session(uid, pid, route.provider, route.model, isolated=True,
                       directives=f"Specialist {a['specialist_id']}; role {w['role']}. "
                       'Roles confer no permissions. Do only the assigned work.')
            a['session_id'] = session
            run = save(run)  # identity survives a crash inside a provider/tool call
            a = run['attempts'][-1]
            w = next(x for x in run['work'] if x['id'] == a['work_id'])
            turn = self.agent.send(uid, pid, message, route.provider, route.model, session_id=session)
            self.finish_turn(uid, pid, run, w, a, turn)
        except Exception as e:
            # Unknown effects are not retried automatically.
            a['status'] = w['status'] = run['status'] = 'interrupted'
            a['error_type'] = type(e).__name__
            event(run, 'work.interrupted', attempt_id=a['id'], error_type=type(e).__name__)
        finally:
            self.agent.limits = base
        return save(run)
