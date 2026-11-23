// Read-only DOM parity from actual installed-wheel API data; no live acceptance claim.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {JSDOM} = require('../.dev/node_modules/jsdom');
const fixture = JSON.parse(fs.readFileSync('evidence/installed-readonly.json', 'utf8'));
const {pid, run, events} = fixture;
const calls = [], errors = [];
const dom = new JSDOM(fs.readFileSync('evidence/installed-dashboard.html', 'utf8'), {
  url:'http://localhost/', runScripts:'dangerously', beforeParse(w) {
    w.sessionStorage.setItem('pcim_token', 'readonly');
    w.fetch = async (path, options) => {
      calls.push([options.method, path]);
      assert.equal(options.method, 'GET', 'read-only UI must not mutate state');
      let value;
      if (path === '/v1/projects') value = [{id:pid, name:'Installed workflow'}];
      else if (path === '/v1/autonomy') value = {mode:'ask'};
      else if (path === `/v1/projects/${pid}/runs`) value = [run];
      else if (path === `/v1/projects/${pid}/runs/${run.id}`) value = run;
      else if (path === `/v1/projects/${pid}/runs/${run.id}/events`) value = events;
      else value = [];
      return {ok:true, json:async()=>value};
    };
    w.addEventListener('error', e=>errors.push(e.message));
  }
});
(async()=>{
  await new Promise(r=>setTimeout(r,30));
  await dom.window.eval("tab='Execution'; render()");
  await new Promise(r=>setTimeout(r,30));
  const panel = dom.window.document.querySelector('#run-panel');
  for (const value of [run.goal, run.id, run.status, ...run.work.map(w=>w.role), ...events.map(e=>e.kind)]) {
    assert(panel.textContent.includes(value), `missing ${value}`);
  }
  assert(calls.some(([m,p])=>p.endsWith('/events')));
  assert.deepEqual(errors, []);
  console.log('PASS installed dashboard read-only rendering matches real SQLite/API goal, status, roles, events and run ID. DOM proof, not browser visual or Linux execution evidence.');
  dom.window.close();
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1});
