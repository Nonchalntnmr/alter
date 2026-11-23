"""Real SQLite/service adapter checks. Scripted denial is not live acceptance."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pcim import alter
from pcim.api import create_app
from pcim.engine import PersonalCI
from pcim.run_service import RunService
from pcim.workspace import Workspace
from pcim.adapters.mock_agent import MockAgentAdapter


@pytest.fixture
def integrated(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('PCIM_HOME', str(tmp_path / 'home'))
    location = tmp_path / 'state'
    ci = PersonalCI(location, seed=0)
    tokens = {u: ci.onboard(u, '', ('mock',))['api_token'] for u in ('alice', 'bob')}
    ws = Workspace(ci)
    pid = ws.create_project('alice', 'Integration').id
    root = tmp_path / 'work'
    root.mkdir()
    ws.authorize_workspace('alice', pid, str(root), run_argv=['python3', '-c', 'assert True'], allow_write=True)
    service = RunService(ci)
    client = TestClient(create_app(ci, run_service=service))
    headers = {'Authorization': 'Bearer ' + tokens['alice']}
    monkeypatch.setattr(alter, '_ctx', lambda: (ci, 'alice', SimpleNamespace(project_id=pid)))

    def cli(action, *args):
        assert alter.main(['runs', action, *args, '--json']) == 0
        return json.loads(capsys.readouterr().out)

    yield ci, ws, pid, root, service, client, headers, tokens, cli, location
    ci.store.close()


def test_real_service_lifecycle_cross_surface_and_reopen(integrated):
    ci, ws, pid, root, service, client, headers, tokens, cli, location = integrated
    base = f'/v1/projects/{pid}/runs'
    run = cli('start', 'Build a calculator')
    rid = run['id']
    path = base + '/' + rid
    assert run['status'] == 'queued' and not run['attempts']
    assert client.get(path, headers=headers).json() == cli('show', rid)
    assert client.get(base, headers=headers).json() == cli('list')
    assert client.get(path + '/events', headers=headers).json() == cli('events', rid)
    assert client.post(path + '/pause', headers=headers).json()['status'] == 'paused'
    assert cli('resume', rid)['status'] == 'queued'
    assert cli('pause', rid)['status'] == 'paused'
    assert client.post(path + '/resume', headers=headers).json()['status'] == 'queued'
    before = cli('show', rid)
    assert client.post(base, headers=headers, json={'objective': 'duplicate'}).status_code == 409
    assert cli('show', rid) == before
    bob = {'Authorization': 'Bearer ' + tokens['bob']}
    for suffix in ('', '/events'):
        assert client.get(path + suffix).status_code == 401
        assert client.get(path + suffix, headers=bob).status_code == 404
    for action in ('pause', 'resume', 'cancel'):
        assert client.post(path + '/' + action, headers=bob).status_code == 404
    final = cli('cancel', rid)
    assert client.get(path, headers=headers).json() == final
    assert client.post(path + '/resume', headers=headers).status_code == 422
    assert not list(root.iterdir())
    reopened = PersonalCI(location, seed=0)
    try:
        restored = TestClient(create_app(reopened))
        assert restored.get(path, headers=headers).json() == final
        assert restored.get(path + '/events', headers=headers).json() == final['events']
    finally:
        reopened.store.close()


@pytest.mark.parametrize('surface', ['cli', 'http'])
@pytest.mark.parametrize('condition', ['revoked', 'budget'])
def test_denial_persists_across_surfaces_without_model_continuation(integrated, surface, condition):
    ci, ws, pid, root, service, client, headers, tokens, cli, location = integrated
    ws.set_mode('alice', 'collaborative')
    adapter = MockAgentAdapter(script=[{'tool_uses': [{'id': 'write', 'name': 'write_file',
        'input': {'path': 'pending.txt', 'content': 'pending', 'reason': 'assigned'}}]}])
    service.agent.set_adapter('mock', None, adapter)
    # Explicit test provider: automatic production routing excludes test doubles.
    run = service.start('alice', pid, 'Build a calculator', {'mock'}, provider='mock')
    rid = run['id']
    path = f'/v1/projects/{pid}/runs/{rid}'
    pending = client.post(path + '/advance', headers=headers)
    assert pending.status_code == 200, pending.text
    run = pending.json()
    tid = run['attempts'][-1]['pending'][0]
    assert cli('show', rid)['attempts'][-1]['pending_detail'][0]['id'] == tid
    if condition == 'revoked':
        ws.revoke_provider('alice', pid, 'mock')
    else:
        run['usage']['steps'] = run['limits']['max_model_steps']
        service.state.save('alice', pid, rid, run, run['revision'])
    before = len(adapter.requests)
    if surface == 'cli':
        final = cli('deny', rid, tid)
    else:
        response = client.post(path + '/approvals/' + tid, headers=headers, json={'approve': False})
        assert response.status_code == 200, response.text
        final = response.json()
    assert final['status'] == 'incomplete'
    assert len(adapter.requests) == before == 1
    assert service.queue.get('alice', tid).status == 'denied'
    assert not list(root.iterdir())
    assert cli('show', rid) == client.get(path, headers=headers).json() == final
    reopened = PersonalCI(location, seed=0)
    try:
        restored = TestClient(create_app(reopened))
        assert restored.get(path, headers=headers).json() == final
        assert RunService(reopened).queue.get('alice', tid).status == 'denied'
        assert restored.post(path + '/approvals/' + tid, headers=headers, json={'approve': True}).status_code == 422
    finally:
        reopened.store.close()
