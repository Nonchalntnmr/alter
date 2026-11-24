"""Saved policy labels must survive reopen without implying verified execution."""
import pytest
from fastapi.testclient import TestClient
from pcim.api import create_app
from pcim.engine import PersonalCI
from pcim.workspace import Workspace, render_briefing


@pytest.mark.parametrize('policy', ['none', 'required'])
def test_policy_briefing_reopens_without_execution(tmp_path, policy):
    data, root = tmp_path / 'data', tmp_path / 'project'
    root.mkdir()
    ci = PersonalCI(data, seed=0)
    token = ci.onboard('alice', '', ('mock',))['api_token']
    ws = Workspace(ci)
    pid = ws.create_project('alice', 'Preview').id
    ws.authorize_workspace('alice', pid, str(root), run_argv=['python3', '-c', 'raise SystemExit(99)'],
                           test_confinement=policy)
    client = TestClient(create_app(PersonalCI(data, seed=0)))
    h = {'Authorization': 'Bearer ' + token}
    r = client.get(f'/v1/projects/{pid}', headers=h)
    assert r.status_code == 200
    b = r.json()['briefing']
    assert b['workspace']['test_confinement'] == policy
    text = render_briefing(b)
    assert ('configuration is not a verified receipt' in text.lower()) if policy == 'required' else (
        'backend: none, verified: false' in text and 'cannot satisfy specialist or secured gates' in text)
    assert client.get('/v1/tasks', headers=h).json() == []
    assert list(root.iterdir()) == []
    assert client.get(f'/v1/projects/{pid}').status_code == 401
