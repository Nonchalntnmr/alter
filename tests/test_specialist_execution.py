"""Real local effects with deterministic model replies; never live-provider evidence."""
import contextlib
import copy
import json
import os
from pathlib import Path
import threading

import pytest

from pcim.adapters.mock_agent import MockAgentAdapter
from pcim.agent import Agent
from pcim.engine import PersonalCI
from pcim.execution_routing import select
from pcim.governor import Governor
from pcim.run_service import RunService
from pcim.tasks import TaskQueue
from pcim.work_contracts import RunLimits, validate_work
from pcim.workspace import Workspace


class MemoryState:
    """Test-only protocol adapter; persistence/recovery is tested by ALT-5."""
    def __init__(self):
        self.rows = {}
        self.mutex = threading.Lock()

    @contextlib.contextmanager
    def lock(self, uid, pid):
        if not self.mutex.acquire(blocking=False):
            raise RuntimeError('project busy')
        try:
            yield
        finally:
            self.mutex.release()

    def create(self, uid, pid, snapshot):
        s = copy.deepcopy(snapshot)
        s['revision'] = 1
        self.rows[uid, pid, s['id']] = s
        return copy.deepcopy(s)

    def get(self, uid, pid, rid):
        return copy.deepcopy(self.rows[uid, pid, rid])

    def list(self, uid, pid):
        return [copy.deepcopy(s) for (u, p, _), s in self.rows.items() if (u, p) == (uid, pid)]

    def save(self, uid, pid, rid, snapshot, expected_revision):
        assert self.rows[uid, pid, rid]['revision'] == expected_revision
        s = copy.deepcopy(snapshot)
        s['revision'] += 1
        self.rows[uid, pid, rid] = s
        return copy.deepcopy(s)


def tool(name, **args):
    return {'tool_uses': [{'id': name, 'name': name, 'input': args}]}


def write(path, content):
    return tool('write_file', path=path, content=content, reason='assigned work')


@pytest.fixture(params=["memory", "sqlite"])
def env(tmp_path, monkeypatch, request):
    # Prevent default home data and optional semantic model downloads.
    monkeypatch.setenv('PCIM_HOME', str(tmp_path / 'home'))
    ci = PersonalCI(tmp_path / 'data', seed=0)
    ci.onboard('u', 'U', providers=('mock',))
    ws = Workspace(ci)
    p = ws.create_project('u', 'Specialist fixture')
    root = tmp_path / 'project'
    root.mkdir()
    check = "from pathlib import Path; exec(Path('calc.py').read_text()); assert add(2,3)==5; assert Path('README.txt').read_text()=='Use add'; import runpy; runpy.run_path('checks.py')"
    ws.authorize_workspace('u', p.id, str(root), run_argv=['python3', '-c', check], allow_write=True)
    ws.set_mode('u', 'autonomous')
    ws.grant_autonomy('u', p.id, ['write_file', 'run_tests'], max_actions=30)
    q = TaskQueue(ws, Governor(ws))
    agent = Agent(ci, ws, q)
    if request.param == "sqlite":
        from pcim.run_state import RunStateStore
        state = RunStateStore(ci.store)
    else:
        state = MemoryState()
    service = RunService(ci, state=state, agent=agent)
    return ci, ws, p.id, root, agent, service


def script(agent, steps):
    ad = MockAgentAdapter(script=steps)
    agent.set_adapter('mock', None, ad)
    return ad


def start(s, pid, **kwargs):
    return s.start('u', pid, 'Build a calculator with instructions and behavioral tests', {'mock'}, provider='mock', **kwargs)


def three_steps():
    return [write('calc.py', 'def add(a,b): return a+b\n'), {'text': 'built'},
            write('README.txt', 'Use add'), {'text': 'documented'},
            write('checks.py', 'from calc import add\nassert add(2,3)==5\n'), tool('run_tests', reason='verify behavior'), {'text': 'verified'}]


def test_three_attributable_workers_real_files_and_test_receipts(env):
    ci, ws, pid, root, agent, s = env
    ad = script(agent, three_steps())
    run = start(s, pid)
    for _ in range(3):
        run = s.advance('u', pid, run['id'])
    assert run['status'] == 'succeeded', run
    assert len({a['session_id'] for a in run['attempts']}) == 3
    assert len({a['specialist_id'] for a in run['attempts']}) == 3
    assert [a['role'] for a in run['attempts']] == ['builder', 'interface', 'verifier']
    assert all(a['evidence']['artifacts'] for a in run['attempts'])
    assert run['verification']['tests_after_writes']
    assert run['verification']['test_double'] is True
    assert (root / 'calc.py').read_text() == 'def add(a,b): return a+b\n'
    assert 'Dependency evidence' in ad.requests[-3]['messages'][0]['content']
    assert s.advance('u', pid, run['id']) == run
    evidence_dir = os.environ.get('ALTER_ENGINE_EVIDENCE_DIR')
    if evidence_dir:
        destination = Path(evidence_dir)
        destination.mkdir(parents=True, exist_ok=True)
        destination.joinpath('three-worker-run.json').write_text(json.dumps(run, indent=2, default=str))
        receipts = [t.public() for t in s.queue.list('u', pid)]
        for receipt in receipts:
            if receipt.get('result'):
                receipt['result'].pop('abs_path', None)
        destination.joinpath('three-worker-receipts.json').write_text(json.dumps(receipts, indent=2, default=str))


def test_model_claim_without_effect_cannot_complete_or_unlock_dependent(env):
    _, _, pid, _, agent, s = env
    script(agent, [{'text': 'All done. All tests pass.'}])
    run = s.advance('u', pid, start(s, pid)['id'])
    assert run['status'] == 'incomplete'
    assert run['work'][1]['status'] == 'queued'
    assert run['attempts'][0]['evidence']['problems']


def test_approval_survives_recreated_service_and_never_auto_grants(env):
    ci, ws, pid, root, agent, s = env
    ws.set_mode('u', 'collaborative')
    script(agent, [write('calc.py', 'def add(a,b): return a+b'), {'text': 'done'}])
    run = s.advance('u', pid, start(s, pid)['id'])
    assert run['status'] == 'awaiting_approval'
    assert not (root / 'calc.py').exists()
    tid = run['attempts'][-1]['pending'][0]
    s2 = RunService(ci, s.state, agent)
    run = s2.resolve('u', pid, run['id'], tid, approve=True)
    assert (root / 'calc.py').exists()
    assert run['work'][0]['status'] == 'succeeded'
    with pytest.raises(ValueError):
        s2.resolve('u', pid, run['id'], tid, approve=True)


def test_denied_action_cannot_complete(env):
    _, ws, pid, root, agent, s = env
    ws.set_mode('u', 'collaborative')
    script(agent, [write('calc.py', 'x'), {'text': 'done anyway'}])
    run = s.advance('u', pid, start(s, pid)['id'])
    run = s.resolve('u', pid, run['id'], run['attempts'][-1]['pending'][0], approve=False)
    assert run['status'] == 'incomplete'
    assert not (root / 'calc.py').exists()


def test_cancel_closes_before_cancelling_pending_actions(env):
    _, ws, pid, root, agent, s = env
    ws.set_mode('u', 'collaborative')
    ad = script(agent, [write('calc.py', 'x')])
    run = s.advance('u', pid, start(s, pid)['id'])
    run = s.cancel('u', pid, run['id'])
    assert run['status'] == 'cancelled' and len(ad.requests) == 1
    assert not (root / 'calc.py').exists()


def test_interrupted_attempt_is_never_replayed(env):
    _, _, pid, root, agent, s = env
    run = start(s, pid)
    run['status'] = 'running'
    run['attempts'].append({'id':'a', 'work_id':'build', 'status':'running'})
    s.state.save('u', pid, run['id'], run, run['revision'])
    run = s.advance('u', pid, run['id'])
    assert run['status'] == 'interrupted' and not list(root.iterdir())
    with pytest.raises(ValueError):
        s.retry('u', pid, run['id'], 'build')


def test_retry_is_explicit_and_bounded(env):
    _, _, pid, _, agent, s = env
    script(agent, [{'text':'no work'}, {'text':'still no work'}])
    run = s.advance('u', pid, start(s, pid)['id'])
    run = s.retry('u', pid, run['id'], 'build')
    run = s.advance('u', pid, run['id'])
    assert len(run['attempts']) == 2
    with pytest.raises(ValueError, match='ceiling'):
        s.retry('u', pid, run['id'], 'build')


def test_resource_ceiling_prevents_next_worker(env):
    _, _, pid, _, agent, s = env
    ad = script(agent, three_steps())
    run = start(s, pid, limits={'max_attempts':1})
    run = s.advance('u', pid, run['id'])
    assert run['work'][0]['status'] == 'succeeded'
    n = len(ad.requests)
    run = s.advance('u', pid, run['id'])
    assert run['status'] == 'exhausted' and len(ad.requests) == n


def test_pause_steer_resume_and_project_lock(env):
    _, _, pid, _, agent, s = env
    run = start(s, pid)
    run = s.pause('u', pid, run['id'])
    assert s.advance('u', pid, run['id'])['status'] == 'paused'
    s.steer('u', pid, run['id'], 'Use clear names')
    run = s.resume('u', pid, run['id'])
    assert run['steers'] == ['Use clear names']
    with s.state.lock('u', pid), pytest.raises((RuntimeError, ValueError), match='busy|active'):
        s.advance('u', pid, run['id'])


def test_isolated_sessions_preserve_user_session_and_target_explicitly(env):
    _, _, pid, _, agent, s = env
    script(agent, [{'text':'hello'}, {'text':'specialist'}, {'text':'again'}])
    normal = agent.send('u', pid, 'hello').session_id
    isolated = agent.create_session('u', pid, 'mock', isolated=True)
    agent.send('u', pid, 'assigned', session_id=isolated)
    assert agent.open_session('u', pid) == normal
    assert agent.send('u', pid, 'continue', session_id=isolated).session_id == isolated
    agent.close_session('u', pid, isolated)
    with pytest.raises(ValueError, match='closed'):
        agent.send('u', pid, 'late', session_id=isolated)


def test_governor_denies_traversal_from_specialist(env):
    _, _, pid, root, agent, s = env
    script(agent, [write('../escape.txt', 'bad'), {'text':'done'}])
    run = s.advance('u', pid, start(s, pid)['id'])
    assert run['status'] == 'incomplete'
    assert not (root.parent / 'escape.txt').exists()


def test_failed_real_test_prevents_completion(env):
    _, _, pid, root, agent, s = env
    steps = three_steps()
    steps[0] = write('calc.py', 'def add(a,b): return a-b\n')
    script(agent, steps)
    run = start(s, pid)
    for _ in range(3):
        run = s.advance('u', pid, run['id'])
    assert run['status'] == 'incomplete'
    assert not run['attempts'][-1]['evidence']['test_tasks']


@pytest.mark.parametrize('work', [[], [{'id':'a','role':'x','objective':'x','depends_on':['a']}],
    [{'id':'a','role':'x','objective':'x','depends_on':['missing']}],
    [{'id':'a','role':'x','objective':'x','artifacts':['../x']}],
    [{'id':'a','role':'x','objective':'x'}, {'id':'a','role':'y','objective':'y'}]])
def test_invalid_dependency_contracts(work):
    with pytest.raises(ValueError):
        validate_work(work)


def test_routes_require_consent_and_never_implicitly_use_mock():
    with pytest.raises(PermissionError):
        select('builder', set(), provider='mock')
    assert select('verifier', {'mock'}, provider='mock').test_double
    with pytest.raises(RuntimeError):
        select('builder', set())


@pytest.mark.parametrize('limits', [{'max_attempts':0}, {'max_seconds':float('nan')}, {'max_model_steps':1.5}])
def test_invalid_limits(limits):
    with pytest.raises(ValueError):
        RunLimits(**limits)


def test_modified_artifact_cannot_satisfy_final_completion(env):
    _, _, pid, root, agent, s = env
    script(agent, three_steps())
    run = start(s, pid)
    for _ in range(3):
        run = s.advance('u', pid, run['id'])
    assert run['status'] == 'succeeded'
    (root / 'calc.py').write_text('def add(a,b): return -1')
    s.coordinator.verify_run('u', pid, run)
    assert run['status'] == 'incomplete' and not run['verification']['artifacts_current']


def test_foreign_owner_and_foreign_task_cannot_resolve_approval(env):
    _, ws, pid, root, agent, s = env
    ws.set_mode('u', 'collaborative')
    script(agent, [write('calc.py', 'x')])
    run = s.advance('u', pid, start(s, pid)['id'])
    with pytest.raises(KeyError):
        s.get('other-user', pid, run['id'])
    foreign = s.queue.propose('u', pid, 'write_file', {'path':'other.txt','content':'x','reason':'unrelated'})
    with pytest.raises(ValueError, match='belong'):
        s.resolve('u', pid, run['id'], foreign.id, approve=True)
    assert not list(root.iterdir())


def test_changed_provider_access_prevents_pending_tool_effect(env):
    _, ws, pid, root, agent, s = env
    ws.set_mode('u', 'collaborative')
    script(agent, [write('calc.py', 'x')])
    run = s.advance('u', pid, start(s, pid)['id'])
    ws.revoke_provider('u', pid, 'mock')
    with pytest.raises(PermissionError):
        s.resolve('u', pid, run['id'], run['attempts'][-1]['pending'][0], approve=True)
    assert not list(root.iterdir())


def test_sqlite_restart_continues_dependencies_without_repeating_completed_action(env):
    ci, ws, pid, root, agent, s = env
    from pcim.run_state import RunStateStore
    if not isinstance(s.state, RunStateStore):
        pytest.skip("SQLite restart case; memory adapter is tested separately")
    script(agent, three_steps()[:2])
    run = s.advance('u', pid, start(s, pid)['id'])
    first_attempt = run['attempts'][0]
    location = Path(ci.store.path).parent
    ci.store.close()
    ci2 = PersonalCI(location, seed=0)
    restored = RunService(ci2)
    script(restored.agent, three_steps()[2:])
    assert restored.get('u', pid, run['id'])['work'][0]['status'] == 'succeeded'
    for _ in range(2):
        run = restored.advance('u', pid, run['id'])
    assert run['status'] == 'succeeded', run
    assert run['attempts'][0] == first_attempt
    assert all(a.get('action_key') and a.get('evidence_refs') for a in run['attempts'])
    assert len(restored.state.replay('u', pid, run['id'])) == len(run['events'])
    assert len([t for t in restored.queue.list('u', pid) if t.tool == 'write_file' and t.args['path'] == 'calc.py']) == 1
    ci2.store.close()


def test_sqlite_restart_preserves_exact_pending_approval_and_resolves_once(env):
    ci, ws, pid, root, agent, s = env
    from pcim.run_state import RunStateStore
    if not isinstance(s.state, RunStateStore):
        pytest.skip('SQLite approval restart case')
    ws.set_mode('u', 'collaborative')
    script(agent, [write('calc.py', 'def add(a,b): return a+b')])
    run = s.advance('u', pid, start(s, pid)['id'])
    tid = run['attempts'][-1]['pending'][0]
    assert not (root / 'calc.py').exists()
    location = Path(ci.store.path).parent
    ci.store.close()
    ci2 = PersonalCI(location, seed=0)
    restored = RunService(ci2)
    script(restored.agent, [{'text':'verified result receipt'}])
    before = restored.get('u', pid, run['id'])
    assert before['status'] == 'awaiting_approval'
    assert before['attempts'][-1]['pending_detail'][0]['id'] == tid
    run = restored.resolve('u', pid, run['id'], tid, approve=True)
    assert run['work'][0]['status'] == 'succeeded'
    assert (root / 'calc.py').read_text() == 'def add(a,b): return a+b'
    with pytest.raises(ValueError):
        restored.resolve('u', pid, run['id'], tid, approve=True)
    ci2.store.close()


@pytest.mark.parametrize('historical', [False, True])
def test_final_request_preflight_blocks_raw_and_historical_content(env, historical):
    from pcim.sovereignty.content import ContentBlocked
    ci, _, pid, _, agent, _ = env
    ad = script(agent, [{'text': 'safe'}])
    sid = agent.create_session('u', pid, 'mock', isolated=True)
    canary = 'ALTER_SECRET_CANARY_preflight123'
    if historical:
        st = agent._load('u', pid, sid)
        st['messages'].append({'role': 'user', 'content': canary})
        agent._save('u', pid, sid, st)
    with pytest.raises(ContentBlocked) as error:
        agent.send('u', pid, 'continue' if historical else canary, session_id=sid)
    assert not ad.requests
    assert canary not in str(error.value)


def test_final_export_rechecks_bytes_despite_successful_receipts(tmp_path):
    import hashlib
    from types import SimpleNamespace
    from pcim.orchestration import Coordinator

    path = tmp_path / 'artifact.txt'
    path.write_text('safe artifact')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    tasks = [dict(id='write', tool='write_file', status='succeeded', created_at='1',
                  args={'path': path.name}, result={'sha256_after': digest}),
             dict(id='test', tool='run_tests', status='succeeded', created_at='2',
                  result={'exit_code': 0, 'timed_out': False, 'confinement': {
                      'backend': 'bubblewrap', 'verified': True, 'network': 'isolated',
                      'filesystem': 'disposable-copy', 'host_writes': False}})]
    coordinator = Coordinator(None, SimpleNamespace(workspace_auth=lambda *_: {'spec': {'root': str(tmp_path)}}), None, None)
    coordinator.tasks = lambda *_: tasks
    run = {'work': [{'id': 'w'}], 'attempts': [{'work_id': 'w', 'status': 'succeeded', 'session_id': 's'}], 'events': []}
    coordinator.verify_run('u', 'p', run)
    assert run['status'] == 'succeeded'
    path.write_text('ALTER_SECRET_CANARY_exportbytes123')
    # Even a forged current digest cannot bypass the content acceptance gate.
    tasks[0]['result']['sha256_after'] = hashlib.sha256(path.read_bytes()).hexdigest()
    coordinator.verify_run('u', 'p', run)
    assert run['status'] == 'incomplete'
    assert not run['verification']['export']['accepted']


def test_planning_preflight_blocks_before_generate():
    from pcim.planner import plan_queries
    from pcim.sovereignty.content import ContentBlocked
    class NeverCalled:
        def generate(self, prompt):
            pytest.fail('contaminated planner request reached adapter')
    with pytest.raises(ContentBlocked):
        plan_queries(NeverCalled(), 'ALTER_SECRET_CANARY_planning123')


def test_generate_preflight_blocks_before_adapter(env, monkeypatch):
    from pcim.sovereignty.content import ContentBlocked
    ci, _, _, _, _, _ = env
    with pytest.raises(ContentBlocked):
        ci.ask('u', 'ALTER_SECRET_CANARY_generate123', provider='mock')


@pytest.mark.parametrize('condition', ['revoked-provider', 'exhausted-budget', 'normal'])
def test_human_denial_never_requires_model_access_or_remaining_budget(env, condition):
    _, ws, pid, root, agent, service = env
    ws.set_mode('u', 'collaborative')
    adapter = script(agent, [write('calc.py', 'unsafe pending change'), {'text': 'must not be called'}])
    run = service.advance('u', pid, start(service, pid)['id'])
    task_id = run['attempts'][-1]['pending'][0]
    before = len(adapter.requests)
    if condition == 'revoked-provider':
        ws.revoke_provider('u', pid, 'mock')
    if condition == 'exhausted-budget':
        run['usage']['steps'] = run['limits']['max_model_steps']
        service.state.save('u', pid, run['id'], run, run['revision'])
    result = service.resolve('u', pid, run['id'], task_id, approve=False)
    assert service.queue.get('u', task_id).status == 'denied'
    assert result['status'] == 'incomplete'
    assert len(adapter.requests) == before
    assert not (root / 'calc.py').exists()
    assert agent._load('u', pid, result['attempts'][-1]['session_id'])['_row']['status'] == 'closed'
    assert service.get('u', pid, run['id'])['status'] == 'incomplete'


def test_denial_cancels_sibling_pending_actions_without_continuation(env):
    ci, ws, pid, root, agent, service = env
    ws.set_mode('u', 'collaborative')
    first = write('first.txt', 'first')['tool_uses'][0]
    second = dict(write('second.txt', 'second')['tool_uses'][0], id='second')
    adapter = script(agent, [{'tool_uses': [first, second]}])
    run = service.advance('u', pid, start(service, pid)['id'])
    pending = run['attempts'][-1]['pending']
    assert len(pending) == 2
    result = service.resolve('u', pid, run['id'], pending[0], approve=False)
    assert [service.queue.get('u', tid).status for tid in pending] == ['denied', 'cancelled']
    assert result['attempts'][-1]['pending'] == []
    assert result['status'] == 'incomplete' and len(adapter.requests) == 1
    assert not list(root.iterdir())
    from pcim.run_state import RunStateStore
    if isinstance(service.state, RunStateStore):
        location = Path(ci.store.path).parent
        ci.store.close()
        reopened = PersonalCI(location, seed=0)
        restored = RunService(reopened)
        assert restored.get('u', pid, run['id'])['status'] == 'incomplete'
        assert [restored.queue.get('u', tid).status for tid in pending] == ['denied', 'cancelled']
        with pytest.raises(ValueError):
            restored.resolve('u', pid, run['id'], pending[0], approve=True)
        assert not list(root.iterdir())
        reopened.store.close()
