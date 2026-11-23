"""Adapter contract tests. Service fixture is explicitly not live execution evidence."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pcim import alter
from pcim.api import create_app
from pcim.engine import PersonalCI
from pcim.workspace import Workspace


class ServiceStub:
    def __init__(self):
        self.calls = []
        self.owner = 'alice'
        self.snapshot = {'id': 'run-1', 'project_id': 'p', 'objective': 'Build task app', 'status': 'pending',
                         'work': [{'id': 'w1', 'role': 'builder'}], 'approvals': [], 'events': [{'kind': 'created', 'run_id': 'run-1'}]}

    def _call(self, action, uid, *args, **kwargs):
        self.calls.append((action, uid, args, kwargs))
        if uid != self.owner:
            raise KeyError('hidden')
        if action == 'list':
            return [dict(self.snapshot)]
        return dict(self.snapshot)

    def start(self, uid, pid, goal, vendors):
        assert isinstance(vendors, set)
        return self._call('start', uid, pid, goal, vendors)

    def list(self, uid, pid):
        return self._call('list', uid, pid)

    def get(self, uid, pid, rid):
        return self._call('get', uid, pid, rid)

    def advance(self, uid, pid, rid):
        return self._call('advance', uid, pid, rid)

    def pause(self, uid, pid, rid):
        return self._call('pause', uid, pid, rid)

    def resume(self, uid, pid, rid):
        return self._call('resume', uid, pid, rid)

    def cancel(self, uid, pid, rid):
        return self._call('cancel', uid, pid, rid)

    def steer(self, uid, pid, rid, text):
        return self._call('steer', uid, pid, rid, text)

    def resolve(self, uid, pid, rid, tid, *, approve):
        return self._call('resolve', uid, pid, rid, tid, approve=approve)


@pytest.fixture
def surface(tmp_path):
    ci = PersonalCI(tmp_path, seed=0)
    tokens = {uid: ci.onboard(uid, '', ('mock',))['api_token'] for uid in ('alice', 'bob')}
    project = Workspace(ci).create_project('alice', 'CLI/API fixture')
    service = ServiceStub()
    client = TestClient(create_app(ci, run_service=service))
    return client, service, project.id, {uid: {'Authorization': 'Bearer ' + token} for uid, token in tokens.items()}


def test_auth_and_project_ownership_before_service(surface):
    client, service, pid, headers = surface
    for method, path, body in [('GET', f'/v1/projects/{pid}/runs', None),
                               ('POST', f'/v1/projects/{pid}/runs', {'objective': 'Build it'}),
                               ('GET', f'/v1/projects/{pid}/runs/run-1', None),
                               ('POST', f'/v1/projects/{pid}/runs/run-1/pause', None)]:
        assert client.request(method, path, json=body).status_code == 401
    assert not service.calls
    for method in ('GET', 'POST'):
        response = client.request(method, f'/v1/projects/{pid}/runs', headers=headers['bob'],
                                  json={'objective': 'Build it'} if method == 'POST' else None)
        assert response.status_code == 404
    assert not service.calls


@pytest.mark.parametrize('action', ['pause', 'resume', 'cancel', 'events', 'advance', 'show', 'say', 'approve', 'deny'])
def test_cli_api_parity(surface, monkeypatch, capsys, action):
    client, service, pid, headers = surface
    monkeypatch.setattr(alter, '_ctx', lambda: (None, 'alice', SimpleNamespace(project_id=pid)))
    monkeypatch.setattr(alter, '_run_service', lambda ci: service)
    monkeypatch.setattr(alter, '_run_vendors', lambda *a: {'mock'})
    rest = ['run-1']
    path = f'/v1/projects/{pid}/runs/run-1'
    body = None
    if action in ('show', 'events'):
        method = 'GET'
        if action == 'events':
            path += '/events'
    else:
        method = 'POST'
        if action == 'say':
            rest += ['New guidance']; path += '/steer'; body = {'message': 'New guidance'}
        elif action in ('approve', 'deny'):
            rest += ['task-1']; path += '/approvals/task-1'
            body = {'approve': action == 'approve'}
        else:
            path += '/' + action
    assert alter.main(['runs', action, *rest, '--json']) == 0
    cli_output = json.loads(capsys.readouterr().out)
    cli_call = service.calls[-1]
    response = client.request(method, path, headers=headers['alice'], json=body)
    assert response.status_code == 200
    assert cli_output == response.json()
    assert service.calls[-1] == cli_call
    # A different bearer identity must reach the service as that identity.
    assert client.request(method, path, headers=headers['bob'], json=body).status_code == 404


def test_start_and_list_parity(surface, monkeypatch, capsys):
    client, service, pid, headers = surface
    monkeypatch.setattr(alter, '_ctx', lambda: (None, 'alice', SimpleNamespace(project_id=pid)))
    monkeypatch.setattr(alter, '_run_service', lambda ci: service)
    monkeypatch.setattr(alter, '_run_vendors', lambda *a: {'mock'})
    for action in ('start', 'list'):
        args = ['Build task app'] if action == 'start' else []
        assert alter.main(['runs', action, *args, '--json']) == 0
        cli_result = json.loads(capsys.readouterr().out)
        cli_call = service.calls[-1]
        response = client.request('POST' if action == 'start' else 'GET', f'/v1/projects/{pid}/runs',
                                  headers=headers['alice'], json={'objective': 'Build task app'} if args else None)
        assert response.status_code == 200
        assert response.json() == cli_result
        assert service.calls[-1] == cli_call


def test_validation_and_recoverable_errors(surface, monkeypatch, capsys):
    client, service, pid, headers = surface
    assert client.post(f'/v1/projects/{pid}/runs', headers=headers['alice'], json={'objective': '   '}).status_code == 422
    assert client.post(f'/v1/projects/{pid}/runs/r/steer', headers=headers['alice'], json={'message': ''}).status_code == 422
    assert not service.calls
    monkeypatch.setattr(alter, '_ctx', lambda: (None, 'alice', SimpleNamespace(project_id=pid)))
    def unavailable(ci):
        raise RuntimeError('Install integrated build')
    monkeypatch.setattr(alter, '_run_service', unavailable)
    assert alter.main(['runs', 'list']) == 2
    assert 'Install integrated build' in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        alter.main(['runs', 'approve', 'run-1'])
    assert exc.value.code == 2


@pytest.mark.parametrize('error,status', [(PermissionError('denied'), 403), (RuntimeError('busy'), 409),
                                        (ValueError('invalid state'), 422), (KeyError('hidden'), 404)])
def test_service_error_mapping(surface, monkeypatch, error, status):
    client, service, pid, headers = surface
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(service, 'pause', fail)
    assert client.post(f'/v1/projects/{pid}/runs/run-1/pause', headers=headers['alice']).status_code == status


def test_legacy_ci_dispatch_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(alter.T, 'main', lambda argv: calls.append(argv) or 0)
    assert alter.main(['ci', 'status']) == 0
    assert alter.main(['models']) == 0
    assert calls == [['status'], ['models']]
