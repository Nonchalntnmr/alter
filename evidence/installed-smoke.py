import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import pcim
from pcim import alter
from pcim.api import create_app
from pcim.engine import PersonalCI
from pcim.workspace import Workspace
from fastapi.testclient import TestClient

assert 'site-packages' in pcim.__file__, pcim.__file__
with tempfile.TemporaryDirectory(dir='.') as folder:
    ci = PersonalCI(Path(folder) / 'state', seed=0)
    token = ci.onboard('owner', '', ('mock',))['api_token']
    pid = Workspace(ci).create_project('owner', 'Installed workflow').id
    alter._ctx = lambda: (ci, 'owner', SimpleNamespace(project_id=pid))
    def cli(action, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert alter.main(['runs', action, *args, '--json']) == 0
        return json.loads(out.getvalue())
    client = TestClient(create_app(ci))
    headers = {'Authorization': 'Bearer ' + token}
    run = cli('start', 'Build an installed calculator')
    path = f'/v1/projects/{pid}/runs/{run["id"]}'
    assert client.get(path, headers=headers).json() == run
    assert client.post(path + '/pause', headers=headers).json()['status'] == 'paused'
    assert cli('resume', run['id'])['status'] == 'queued'
    snapshot = client.get(path, headers=headers).json()
    events = client.get(path + '/events', headers=headers).json()
    dashboard = client.get('/')
    assert dashboard.status_code == 200 and 'refreshRuns' in dashboard.text
    Path('installed-dashboard.html').write_text(dashboard.text)
    Path('installed-readonly.json').write_text(json.dumps({'pid': pid, 'run': snapshot, 'events': events}))
    assert cli('cancel', run['id'])['status'] == 'cancelled'
    ci.store.close()
    reopened = PersonalCI(Path(folder) / 'state', seed=0)
    assert TestClient(create_app(reopened)).get(path, headers=headers).json()['status'] == 'cancelled'
    reopened.store.close()
print('PASS installed CLI/API create/read/events/pause/resume/cancel/reopen and dashboard package resources; import:', pcim.__file__)
