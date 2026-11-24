// DOM behavior against a service fixture, not a real-browser/live-execution claim.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {JSDOM} = require('../.dev/node_modules/jsdom');
const html = fs.readFileSync('src/pcim/dashboard/index.html', 'utf8');
const calls = [], errors = [];
const run = {id:'r1', goal:'Build <img src=x onerror=alert(1)> app', status:'awaiting_approval',
  plan:{objective:'Plan'}, work:[{role:'builder',id:'w1'}], attempts:[{status:'running',pending:['t1'],evidence:[{sha256:'abc'}]}],
  evidence:[{sha256:'abc'}], approvals:[{id:'t1', status:'awaiting_approval',tool:'write_file'}]};
let failNext = false, policy = 'required';
const dom = new JSDOM(html, {url:'http://localhost/', runScripts:'dangerously', beforeParse(w) {
  w.sessionStorage.setItem('pcim_token','fixture');
  w.prompt = () => 'reviewed';
  w.fetch = async (path, options) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push([options.method, path, body]);
    if (failNext) { failNext = false; return {ok:false,status:409,json:async()=>({detail:'Worker busy'})}; }
    let value;
    if (path === '/v1/autonomy') value = {mode:'ask'};
    else if (path === '/v1/tasks/t1') value = {id:'t1',status:'awaiting_approval',tool:'write_file'};
    else if (path === '/v1/projects') value = [{id:'p1',name:'Project',updated_at:'2026-11-24'}];
    else if (path === '/v1/projects/p1') value = {project:{name:'Project'}, providers:[], briefing:{workspace:{root:'/tmp/fixture',can_run_tests:true,can_write:false,test_confinement:policy},open_questions:[],decisions:[],next_steps:[]}};
    else if (path === '/v1/projects/p1/session') value = {events:[]};
    else if (path === '/v1/projects/p1/runs') value = options.method === 'GET' ? [run] : run;
    else if (path === '/v1/projects/p1/runs/r1/events') value = [{kind:'created'}];
    else if (path.startsWith('/v1/projects/p1/runs/r1')) value = run;
    else value = [];
    return {ok:true,json:async()=>value};
  };
  w.addEventListener('error', e => errors.push(e.message));
}});
const w=dom.window, d=w.document;
const settle=()=>new Promise(r=>setTimeout(r,20));
(async()=>{
  await settle();
  await w.eval("tab='Execution'; render()"); await settle();
  assert(d.querySelector('#run-panel').textContent.includes('builder'));
  assert(d.querySelector('#run-panel').textContent.includes('abc'));
  assert(!d.querySelector('#run-panel img'), 'model text must never create HTML');
  d.querySelector('#run-objective').value = 'Build task app';
  d.querySelector('[data-run-action=start]').click(); await settle();
  assert(calls.some(c=>c[0]==='POST' && c[1]==='/v1/projects/p1/runs' && c[2].objective==='Build task app'));
  for (const action of ['advance','pause','resume','cancel','approve','deny']) {
    d.querySelector(`[data-run-action=${action}]`).click(); await settle();
  }
  assert(calls.some(c=>c[1]==='/v1/projects/p1/runs/r1/approvals/t1' && c[2].approve===true));
  assert(calls.some(c=>c[1]==='/v1/projects/p1/runs/r1/approvals/t1' && c[2].approve===false));
  d.querySelector('#run-message').value='Preserve draft';
  await w.refreshRuns();
  assert.equal(d.querySelector('#run-message').value,'Preserve draft');
  d.querySelector('[data-run-action=steer]').click(); await settle();
  assert(calls.some(c=>c[1]==='/v1/projects/p1/runs/r1/steer' && c[2].message==='Preserve draft'));
  failNext=true; d.querySelector('[data-run-action=pause]').click(); await settle();
  assert(d.querySelector('#run-feedback').textContent.includes('Worker busy'));
  await w.eval("tab='Workspace'; render()"); await settle();
  assert(d.body.textContent.includes('only in a verified Linux sandbox'));
  assert(d.querySelector('#wc').checked);
  d.querySelector('#wr').value='/tmp/fixture';
  await w.authorize(); await settle();
  assert(calls.some(c=>c[1]==='/v1/projects/p1/workspace' && c[2].test_confinement==='required'));
  policy='none'; await w.render(); await settle();
  assert(d.body.textContent.includes('on this machine after approval, unconfined'));
  assert(!d.querySelector('#wc').checked);
  d.querySelector('#wr').value='/tmp/fixture'; await w.authorize(); await settle();
  assert(calls.some(c=>c[1]==='/v1/projects/p1/workspace' && c[2].test_confinement==='none'));
  assert.equal(errors.length,0,errors.join('\n'));
  console.log('PASS: execution view, escaped content, start/control/approval/steer actions, retained drafts, recoverable errors.');
  w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
