// MAYANK: the page. Reads the field from /v1/mayank/field, draws it, follows the audit chain live,
// and acts only through ALTER's own routes (approve, deny, pause, steer, halt, probe, challenge).
import { Renderer } from "/static/mayank-gl.js";
import { FORMS, ENTITY_FORMS, sampleBody, assignParents, substrateFor, rng, hash01 } from "/static/mayank-forms.js";
import { generatorFor, difficultyFor, check as checkAnswer, ANSWER_HINT } from "/static/mayank-learn.js";

const $ = s => document.querySelector(s);
const params = new URLSearchParams(location.search);
const STATIC = window.MAYANK_STATIC || null;   // a shared snapshot: demonstration data, no ALTER behind it
const SMALL = matchMedia("(max-width: 800px)").matches;
const N_SUB = Math.max(4000, Math.min(160000, +params.get("points") || (SMALL ? 26000 : 42000)));
const READONLY = "This is a read-only snapshot of demonstration data. With ALTER running (pcim serve), this is where you would act.";

// ------------------------------------------------------------------ token and API
let token = "";
try {
  const m = location.hash.match(/token=([^&]+)/);
  if (m) { sessionStorage.setItem("pcim_token", decodeURIComponent(m[1])); history.replaceState(null, "", location.pathname + location.search); }
  token = sessionStorage.getItem("pcim_token") || "";
} catch (_) {}

let staticField = null;
async function staticApi(path, method) {
  if (method !== "GET") throw new Error(READONLY);
  if (path.startsWith("/v1/mayank/field")) {
    staticField = staticField || await (await fetch(STATIC.field)).json();
    return staticField;
  }
  if (path.startsWith("/v1/mayank/search")) {   // no embedder in a static page: words only, and it says so
    const q = new URLSearchParams(path.split("?")[1]).get("q") || "";
    const words = q.toLowerCase().split(/\W+/).filter(w => w.length > 1);
    const results = staticField.entities.map(e => {
      const label = e.label.toLowerCase(), hay = label + " " + e.text.toLowerCase();
      const hit = words.filter(w => hay.includes(w)).length;
      return { id: e.id, score: hit ? hit / words.length + (words.some(w => label.includes(w)) ? 0.2 : 0) : 0 };
    }).filter(r => r.score > 0).sort((a, b) => b.score - a.score).slice(0, 40);
    return { query: q, embedder: "words", results };
  }
  throw new Error(READONLY);
}

async function api(path, { method = "GET", body } = {}) {
  if (STATIC) return staticApi(path, method);
  const r = await fetch(path, { method, headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
                                body: body ? JSON.stringify(body) : undefined });
  if (r.status === 401) {
    showGate(token ? "That token does not open the store this server reads. Each PCIM_HOME has its own token: use the token file of the home this server was started with." : "");
    throw new Error("unauthorized");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : r.statusText);
  return data;
}

// ------------------------------------------------------------------ palettes and kinds
const PALETTES = {
  copper: ["#4a1f10", "#9c5232", "#cf8a5a", "#e6c29c", "#f5ecdc"],
  lava: ["#3a0a06", "#8f1d0e", "#e0521d", "#f6a640", "#fff1c9"],
  ocean: ["#0b1830", "#1d4a78", "#3f8db0", "#8fd0d6", "#eef8f6"],
  aurora: ["#24123d", "#5b2f86", "#3f8f9a", "#8fd6a8", "#f1f6e2"],
  mono: ["#2a2a2a", "#5c5a57", "#9a9590", "#cfcac2", "#f4f1ea"],
};
const KIND_T = {
  skill: 0.99, memory: 0.93, hypothesis: 0.88, experiment: 0.84, alternative: 0.8, assumption: 0.76, counter: 0.72,
  decision: 0.7, project: 0.66, goal: 0.62, plan: 0.58, objective: 0.54, note: 0.5,
  model: 0.46, run: 0.42, agent: 0.37, task: 0.31, claim: 0.24, evidence: 0.16, exchange: 0.09,
};
const KIND_NAME = { counter: "counterexample", exchange: "request", objective: "outcome / step" };
const ROSE = [0.81, 0.42, 0.37], CYAN = [0.53, 0.79, 0.81], IVORY = [1, 0.95, 0.87];
const NEGATIVE = new Set(["failed", "denied", "gap", "contested", "refuted", "expired", "cancelled", "against"]);
const ACTIVE = new Set(["running", "approved", "awaiting_approval", "queued"]);

function hex(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255]; }
function grad(stops, t) {
  t = Math.max(0, Math.min(1, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(t)), f = t - i, a = hex(stops[i]), b = hex(stops[i + 1]);
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}
const mixc = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
const css = c => `rgb(${c.map(v => Math.round(v * 255)).join(",")})`;

// ------------------------------------------------------------------ state
const S = {
  field: null, model: null, form: "human", palette: localGet("mayank.palette", "copper"), selected: -1, hovered: -1,
  pulled: new Map(), isolate: false, hiddenKinds: new Set(), search: new Map(), trail: new Set(), path: [],
  act: null, cursor: 0, live: false, body: null, parent: null, subSeeds: null, formCache: new Map(),
  pendingActivate: new Set(), refreshTimer: 0, fps: 0, lastEvents: [],
};
function localGet(k, d) { try { return localStorage.getItem(k) || d; } catch (_) { return d; } }
function localSet(k, v) { try { localStorage.setItem(k, v); } catch (_) {} }

const R = (() => {
  try { return new Renderer($("#gl")); }
  catch (e) { fatal(`MAYANK needs WebGL2. ${e.message}`); throw e; }
})();

function fatal(msg) { const f = $("#fatal"); f.textContent = msg; f.style.display = "grid"; }
function toast(msg, ms = 3800) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

// ------------------------------------------------------------------ model
function buildModel(field) {
  const ents = field.entities, byId = new Map(ents.map((e, i) => [e.id, i]));
  const links = [];
  for (const l of field.links) {
    const ai = byId.get(l.a), bi = byId.get(l.b);
    if (ai != null && bi != null) links.push({ ...l, ai, bi });
  }
  const out = ents.map(() => []), inn = ents.map(() => []);
  links.forEach(l => { out[l.ai].push(l); inn[l.bi].push(l); });
  const order = Object.keys(KIND_T);
  const kindList = [...new Set(ents.map(e => e.kind))].sort((a, b) => order.indexOf(a) - order.indexOf(b));
  return { entities: ents, links, byId, out, inn, kindList, semantic: i => (ents[i].xyz || [0])[0] + 0.001 * hash01(ents[i].id) };
}

function entityColor(e, palette) {
  let c = grad(PALETTES[palette], KIND_T[e.kind] ?? 0.5);
  if (e.kind === "counter" || NEGATIVE.has(e.status)) c = mixc(c, ROSE, 0.62);
  if (e.kind === "alternative") c = mixc(c, ROSE, 0.3);
  if (e.status === "superseded" || e.status === "closed" || e.status === "retracted") c = c.map(v => v * 0.45);
  if (["held", "mastered", "succeeded", "verified", "supported"].includes(e.status)) c = mixc(c, IVORY, 0.25);
  if (e.status === "running") c = mixc(c, CYAN, 0.5);
  return c;
}

function entitySize(e) {
  let s = 0.55 + 0.85 * (e.weight ?? 0.5);
  if (["hypothesis", "goal", "run", "project"].includes(e.kind)) s *= 1.35;
  if (e.kind === "skill") s *= 0.8 + 0.6 * (e.meta.p_mastery || 0);
  return s;
}

// ------------------------------------------------------------------ building the particles
function formPositions(form) {
  if (S.formCache.has(form)) return S.formCache.get(form);
  const m = S.model;
  m._focus = null;                       // set by the forms that are about some records only
  const ent = ENTITY_FORMS[form](m);
  const sub = substrateFor(form, m, ent, S.body, S.parent, S.subSeeds);
  const res = { ent, sub, focus: m._focus ? new Set(m._focus) : null };
  S.formCache.set(form, res);
  return res;
}

function initParticles(previous) {
  const m = S.model, E = m.entities.length;
  if (!S.body) {
    S.body = sampleBody(N_SUB, 11);
    const r = rng(23); S.subSeeds = new Float32Array(N_SUB).map(() => r());
  }
  S.formCache.clear();
  const human = ENTITY_FORMS.human(m);
  S.parent = assignParents(S.body.pos, human, E);
  S.formCache.set("human", { ent: human, sub: substrateFor("human", m, human, S.body, S.parent, S.subSeeds), focus: null });
  const target = formPositions(S.form);

  // entities: colors, sizes, seeds; positions continue from where each was (or emerge from the head)
  const eColor = new Float32Array(E * 3), eSize = new Float32Array(E), eSeed = new Float32Array(E), eRef = new Float32Array(E);
  const eFrom = new Float32Array(E * 3);
  m.entities.forEach((e, i) => {
    eColor.set(entityColor(e, S.palette), i * 3); eSize[i] = entitySize(e); eSeed[i] = hash01(e.id); eRef[i] = i;
    const was = previous?.get(e.id);
    if (was) eFrom.set(was, i * 3);
    else if (previous) eFrom.set([(hash01(e.id + "x") - 0.5) * 0.05, 0.82, (hash01(e.id + "z") - 0.5) * 0.05], i * 3);
    else { const r = 2.4 + 1.6 * hash01(e.id + "r"), a = 6.283 * hash01(e.id + "a"); eFrom.set([Math.cos(a) * r, (hash01(e.id + "y") - 0.5) * 2.5, Math.sin(a) * r], i * 3); }
  });
  const eTo = applyPulled(target.ent.slice());
  R.setLayer("entities", { from: eFrom, to: eTo, seed: eSeed, size: eSize, color: eColor, ref: eRef });

  // substrate: same count across rebuilds, so it continues; on first load it condenses from a cloud
  const sColor = new Float32Array(N_SUB * 3), sSize = new Float32Array(N_SUB), sRef = new Float32Array(N_SUB);
  const r = rng(31);
  for (let s = 0; s < N_SUB; s++) {
    const p = S.parent[s];
    const base = p >= 0 ? entityColor(m.entities[p], S.palette) : grad(PALETTES[S.palette], 0.45);
    const k = 0.55 + 0.45 * r();
    sColor[s * 3] = base[0] * k; sColor[s * 3 + 1] = base[1] * k; sColor[s * 3 + 2] = base[2] * k;
    sSize[s] = (0.45 + 0.9 * Math.pow(r(), 3)) * (S.body.region[s] === "head" ? 0.62 : 1); sRef[s] = p;
    if (S.body.region[s] === "head") { sColor[s * 3] *= 0.8; sColor[s * 3 + 1] *= 0.62; sColor[s * 3 + 2] *= 0.5; }
  }
  let sFrom;
  if (previous && R.layers.substrate) sFrom = R.currentPositions("substrate");
  else {
    sFrom = new Float32Array(N_SUB * 3);
    for (let s = 0; s < N_SUB; s++) {
      const a = r() * 6.283, b = Math.acos(2 * r() - 1), rad = 2.2 + r() * 2.6;
      sFrom[s * 3] = Math.sin(b) * Math.cos(a) * rad; sFrom[s * 3 + 1] = Math.cos(b) * rad * 0.7; sFrom[s * 3 + 2] = Math.sin(b) * Math.sin(a) * rad;
    }
  }
  R.setLayer("substrate", { from: sFrom, to: target.sub.slice(), seed: S.subSeeds, size: sSize, color: sColor, ref: sRef });
  S.act = new Float32Array(E);
  R.startMorph(previous ? 1800 : 3600);
  buildLines(); legend(); counts(); updateState();
}

function applyPulled(arr) {
  for (const [i, p] of S.pulled) arr.set(p, i * 3);
  return arr;
}

function recolor() {
  const m = S.model, E = m.entities.length, eColor = new Float32Array(E * 3);
  m.entities.forEach((e, i) => eColor.set(entityColor(e, S.palette), i * 3));
  R.updateColors("entities", eColor);
  const sc = new Float32Array(N_SUB * 3), r = rng(31);
  for (let s = 0; s < N_SUB; s++) {
    const p = S.parent[s], base = p >= 0 ? entityColor(m.entities[p], S.palette) : grad(PALETTES[S.palette], 0.45);
    const k = 0.55 + 0.45 * r(); r();
    sc[s * 3] = base[0] * k; sc[s * 3 + 1] = base[1] * k; sc[s * 3 + 2] = base[2] * k;
    if (S.body.region[s] === "head") { sc[s * 3] *= 0.8; sc[s * 3 + 1] *= 0.62; sc[s * 3 + 2] *= 0.5; }
  }
  R.updateColors("substrate", sc);
  legend(); buildLines();
}

// ------------------------------------------------------------------ forms
const FRAMING = {
  human: [0.0, 1.5, 3.85, [0, -0.04, 0]], constellation: [0.5, 0.98, 3.6, [0, -0.05, 0]], network: [0.3, 1.32, 2.7, [0, 0.12, 0]],
  timeline: [0.6, 1.36, 4.3, [0, 0.08, 0]], grid: [0, 1.5708, 3.75, [0, 0.2, 0]], execution: [0, 1.5, 3.05, [0, 0.12, 0]],
  hypotheses: [0.4, 1.02, 3.3, [0, -0.3, 0]], mastery: [0.2, 1.08, 4.1, [0, -0.18, 0]],
};
const FORM_NOTE = {
  human: () => "Head: memory, skills and hypotheses. Torso: projects, goals, plans and decisions. Arms: runs, agents and tasks. Legs: claims, evidence and requests. Within each part, records that mean similar things sit together.",
  constellation: () => `Position is meaning: each record's text, embedded by this store's own embedder (${S.field.embedder}) and projected to three dimensions. Near means similar.`,
  network: () => `Every filament is a relation ALTER recorded (${S.model.links.length}): supersedes, contradicts, evidence, depends on, part of, informed.`,
  timeline: () => "Time rises up the helix, oldest at the bottom. Each kind keeps its own lane.",
  grid: () => "One band per kind, oldest first, left to right: the record laid flat.",
  execution: () => "Read left to right: what each goal was given (its sources) and its project, then the goal or run, then the agents and every tool call made for it, then the claims those calls support. A cyan pulse is waiting for your approval.",
  hypotheses: () => "Each peak is a hypothesis you put to MAYANK. Its height is the model's estimate that it holds: an estimate, not a measurement. Assumptions ring the base; counterexamples and the strongest alternative sit in the valleys.",
  mastery: () => {
    const now = Date.now(), due = S.model.entities.filter(e => e.kind === "skill" && e.meta.review && Date.parse(e.meta.review.due) <= now);
    return `Your skills, by path. Height is mastery, moved only by unassisted checks: an assisted success never counts.`
      + (S.field.next_challenge ? ` Next challenge: ${S.field.next_challenge.replace("[demo] ", "")}.` : "")
      + (due.length ? ` Due for review now (FSRS): ${due.slice(0, 5).map(e => e.meta.path[e.meta.path.length - 1]).join(", ")}${due.length > 5 ? ` and ${due.length - 5} more` : ""}.` : "")
      + " Select a skill and press T to be tested.";
  },
};

function setForm(form, { frame = true } = {}) {
  if (!FORMS.includes(form) || !S.model) return;
  const cur = R.currentPositions("entities"), curS = R.currentPositions("substrate");
  S.form = form;
  const t = formPositions(form);
  R.updateTargets("entities", cur, applyPulled(t.ent.slice()));
  R.updateTargets("substrate", curS, t.sub.slice());
  R.startMorph(2600);
  R.breath = form === "human" ? 1 : 0;
  R.flow = form === "execution" || form === "network" ? 1 : 0;
  R.subFollow = ["human", "network", "constellation", "execution"].includes(form) ? 1 : 0;
  if (frame) { const f = FRAMING[form]; R.camera.frame(f[0], f[1], f[2], f[3], 2200); }
  document.querySelectorAll("#forms button").forEach(b => b.classList.toggle("on", b.dataset.form === form));
  $("#formnote").textContent = FORM_NOTE[form]();
  buildLines(); updateState();
}

// ------------------------------------------------------------------ relations (lines)
const REL_COLOR = {
  supports: [0.81, 0.54, 0.35, 0.35], contradicts: [0.81, 0.42, 0.37, 0.45], supersedes: [0.53, 0.79, 0.81, 0.4],
  evidence: [0.96, 0.92, 0.85, 0.32], depends_on: [0.9, 0.76, 0.6, 0.35], part_of: [0.96, 0.92, 0.85, 0.12],
  ran_for: [0.81, 0.54, 0.35, 0.22], tests: [0.96, 0.92, 0.85, 0.4], revises: [0.53, 0.79, 0.81, 0.55],
  derived_from: [0.9, 0.76, 0.6, 0.3], informed: [0.96, 0.92, 0.85, 0.12], serves: [0.9, 0.76, 0.6, 0.3], produced: [0.96, 0.92, 0.85, 0.3],
  interrogates: [0.86, 0.5, 0.42, 0.55], served_by: [0.53, 0.79, 0.81, 0.3], used: [0.53, 0.79, 0.81, 0.25],
};
function buildLines() {
  const m = S.model; if (!m) return;
  const L = R.layers.entities, from = L.cpu.from, to = L.cpu.to;
  const focus = formPositions(S.form).focus;
  let list = [];
  if (S.form === "network") list = m.links;
  else if (S.form === "execution" || S.form === "hypotheses") list = m.links.filter(l => focus && focus.has(l.ai) && focus.has(l.bi));
  const sel = S.selected;
  const extra = sel >= 0 ? m.out[sel].concat(m.inn[sel]) : [];
  const trailLinks = m.links.filter(l => S.trail.has(l.ai) && S.trail.has(l.bi));
  const contra = m.links.filter(l => l.rel === "contradicts");   // competing structures stay visible in every form
  const all = [...new Set([...list, ...contra, ...extra, ...trailLinks])];
  const f = new Float32Array(all.length * 6), t = new Float32Array(all.length * 6), c = new Float32Array(all.length * 8);
  all.forEach((l, k) => {
    for (const [j, idx] of [[0, l.ai], [1, l.bi]]) {
      f.set(from.subarray(idx * 3, idx * 3 + 3), k * 6 + j * 3);
      t.set(to.subarray(idx * 3, idx * 3 + 3), k * 6 + j * 3);
    }
    const col = REL_COLOR[l.rel] || [0.9, 0.85, 0.8, 0.15];
    const hi = (sel >= 0 && (l.ai === sel || l.bi === sel)) || (S.trail.has(l.ai) && S.trail.has(l.bi));
    const a = hi ? Math.min(1, col[3] * 2.4) : col[3] * (S.form === "network" ? 0.8 : 1);
    c.set([col[0], col[1], col[2], a, col[0], col[1], col[2], a], k * 8);
  });
  R.setLines(f, t, c);
}

function buildFloor() {
  const segs = [];
  const y = -0.985;
  for (let r = 0.4; r <= 2.01; r += 0.2) {
    const n = 160;
    for (let k = 0; k < n; k++) {
      const a0 = k / n * 6.283, a1 = (k + 1) / n * 6.283;
      segs.push([Math.cos(a0) * r, y, Math.sin(a0) * r, Math.cos(a1) * r, y, Math.sin(a1) * r, r > 1.99 ? 0.07 : 0.035]);
    }
  }
  for (let k = 0; k < 120; k++) {
    const a = k / 120 * 6.283, len = k % 10 === 0 ? 0.1 : 0.04;
    segs.push([Math.cos(a) * 2.03, y, Math.sin(a) * 2.03, Math.cos(a) * (2.03 + len), y, Math.sin(a) * (2.03 + len), 0.1]);
  }
  const f = new Float32Array(segs.length * 6), c = new Float32Array(segs.length * 8);
  segs.forEach((s, i) => { f.set(s.slice(0, 6), i * 6); c.set([0.93, 0.85, 0.75, s[6], 0.93, 0.85, 0.75, s[6]], i * 8); });
  R.setFloor(f, f, c);
}

// ------------------------------------------------------------------ per-entity state (texture)
let stateBuf = null, stateW = 2048, stateH = 1;
function neighbours(i) {
  const m = S.model, set = new Set([i]);
  m.out[i].forEach(l => set.add(l.bi)); m.inn[i].forEach(l => set.add(l.ai));
  return set;
}
function visibility(i) {
  const e = S.model.entities[i];
  if (S.hiddenKinds.has(e.kind)) return 0;
  let v = 1;
  const focus = formPositions(S.form).focus;
  if (focus && !focus.has(i)) v = 0.07;
  if (S.isolate && S.selected >= 0 && !S._iso.has(i)) v = 0.05;
  if (S.search.size && !S.search.has(i) && !S.pulled.has(i)) v = Math.min(v, 0.32);
  if (S.pulled.has(i) || i === S.selected) v = 1;
  return v;
}
function updateState() {
  const m = S.model; if (!m) return;
  const E = m.entities.length;
  stateH = Math.max(1, Math.ceil(E / stateW));
  if (!stateBuf || stateBuf.length !== stateW * stateH * 4) stateBuf = new Uint8Array(stateW * stateH * 4);
  if (S.isolate && S.selected >= 0) S._iso = neighbours(S.selected);
  for (let i = 0; i < E; i++) {
    const e = m.entities[i];
    let flags = 0;
    if (i === S.selected) flags |= 1;
    if (i === S.hovered) flags |= 2;
    if (S.trail.has(i)) flags |= 4;
    if (e.status === "awaiting_approval") flags |= 8;
    if (e.meta && e.meta.conflict) flags |= 16;
    stateBuf[i * 4] = Math.round(Math.min(1, S.act[i]) * 255);
    stateBuf[i * 4 + 1] = flags;
    stateBuf[i * 4 + 2] = Math.round((S.search.get(i) || 0) * 255);
    stateBuf[i * 4 + 3] = Math.round(visibility(i) * 255);
  }
  R.setStateTexture(stateBuf, stateW, stateH);
}

// ------------------------------------------------------------------ labels
const labelsEl = $("#labels");
function zoneLabels() {
  const m = S.model, out = [];
  if (S.form === "human") {
    out.push([[0.36, 0.86, 0], "memory · skills · hypotheses"], [[0.46, 0.38, 0], "projects · goals · plans"],
             [[-0.62, 0.12, 0], "runs · agents · tasks"], [[0.42, -0.56, 0], "claims · evidence · requests"]);
  } else if (S.form === "grid") {
    const n = m.kindList.length;
    m.kindList.forEach((k, ri) => out.push([[-1.24, 0.86 - (ri + 0.5) * 1.42 / n, 0], KIND_NAME[k] || k]));
  } else if (S.form === "mastery") {
    for (const nd of m._treeNodes || []) out.push([[nd.x, nd.y - 0.06, nd.z], nd.label.replace("[demo] ", "")]);
  } else if (S.form === "timeline") {
    out.push([[0, -0.95, 0], "oldest"], [[0, 1.1, 0], "newest"]);
  }
  return out;
}
function renderLabels() {
  if (!S.model) return;
  const m = S.model, show = new Map();
  const add = (i, big) => { if (i >= 0 && visibility(i) > 0.1) show.set(i, big || show.get(i) || false); };
  if (S.form === "hypotheses") m.entities.forEach((e, i) => { if (e.kind === "hypothesis") add(i, false); });
  if (S.form === "execution") m.entities.forEach((e, i) => { if (["goal", "run", "project", "plan"].includes(e.kind)) add(i, false); });
  if (S.form === "mastery") m.entities.forEach((e, i) => { if (e.kind === "skill") add(i, false); });
  for (const i of S.pulled.keys()) add(i, true);
  for (const [i] of [...S.search.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6)) add(i, false);
  S.path.forEach(i => add(i, false));
  add(S.hovered, true); add(S.selected, true);
  let html = "";
  if (!R.morphing || R.mix > 0.85) {
    for (const [p, text] of zoneLabels()) {
      const [x, y, w] = R.toScreen(p);
      if (w > 0) html += `<div class="zone" style="left:${x}px;top:${y}px">${esc(text)}</div>`;
    }
  }
  for (const [i, big] of show) {
    const e = m.entities[i], [x, y, w] = R.toScreen(R.entityPos(i));
    if (w <= 0) continue;
    const extra = e.kind === "hypothesis" && e.meta.p_claim_holds != null ? ` · est. ${Math.round(e.meta.p_claim_holds * 100)}%` : "";
    const name = e.kind === "skill" && !big && e.meta.path ? e.meta.path[e.meta.path.length - 1] : e.label;
    const pct = e.kind === "skill" ? ` · ${Math.round((e.meta.p_mastery || 0) * 100)}%` : extra;
    html += `<div class="lab${big ? " big" : ""}" style="left:${x}px;top:${y}px"><small>${esc(KIND_NAME[e.kind] || e.kind)}${e.status ? " · " + esc(e.status) : ""}${pct}</small>${esc(trunc(name, big ? 70 : 46))}</div>`;
  }
  labelsEl.innerHTML = html;
}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const trunc = (s, n) => (s = String(s || "")).length > n ? s.slice(0, n - 1) + "…" : s;

// ------------------------------------------------------------------ HUD
function legend() {
  const m = S.model, counts = {};
  m.entities.forEach(e => counts[e.kind] = (counts[e.kind] || 0) + 1);
  $("#legend").innerHTML = m.kindList.map(k => {
    const c = css(entityColor({ kind: k, status: "", weight: 1 }, S.palette));
    return `<button data-kind="${k}" class="${S.hiddenKinds.has(k) ? "off" : ""}" title="show or hide"><i style="background:${c}"></i>${esc(KIND_NAME[k] || k)} <span class="n">${counts[k]}</span></button>`;
  }).join("");
  $("#legend").querySelectorAll("button").forEach(b => b.onclick = () => {
    const k = b.dataset.kind; S.hiddenKinds.has(k) ? S.hiddenKinds.delete(k) : S.hiddenKinds.add(k);
    legend(); updateState();
  });
}
function counts() {
  const f = S.field;
  $("#counts").textContent = `${f.entities.length} entities · ${f.links.length} relations · ${N_SUB.toLocaleString()} structure points`
    + (f.omitted ? ` · ${f.omitted} older left out` : "")
    + ` · ${f.links.filter(l => l.rel === "contradicts").length} contradictions`;
  const bad = f.sources.filter(s => s.error);
  if (bad.length) toast(`Could not read: ${bad.map(s => `${s.name} (${s.error})`).join("; ")}`, 9000);
  const demo = $("#demo");
  if (STATIC) { demo.style.display = "block"; demo.textContent = SMALL ? "Demo · made-up data · read-only" : "Demonstration snapshot · made-up data · read-only"; }
  else if (f.demo) { demo.style.display = "block"; demo.textContent = `Demonstration data · ${f.demo} of ${f.entities.length} records are demo (scripts/mayank_demo.py)`; }
  else demo.style.display = "none";
}
function buildChrome() {
  if (SMALL) $("#q").placeholder = "Search, or : for commands";
  $("#forms").innerHTML = FORMS.map((f, i) => `<button data-form="${f}">${f}<span class="k">${i + 1}</span></button>`).join("");
  $("#forms").querySelectorAll("button").forEach(b => b.onclick = () => setForm(b.dataset.form));
  $("#palettes").innerHTML = Object.keys(PALETTES).map(p => `<button data-p="${p}" class="${p === S.palette ? "on" : ""}">${p}</button>`).join("");
  $("#palettes").querySelectorAll("button").forEach(b => b.onclick = () => {
    S.palette = b.dataset.p; localSet("mayank.palette", S.palette);
    $("#palettes").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); recolor();
  });
  $("#smaller").onclick = () => R.scale = Math.max(0.4, R.scale / 1.2);
  $("#larger").onclick = () => R.scale = Math.min(3, R.scale * 1.2);
  $("#light").onclick = () => { R.light = R.light > 0.9 ? 0.55 : 1; $("#light").textContent = `light: ${R.light > 0.9 ? "full" : "dim"}`; };
  $("#reset").onclick = () => { const f = FRAMING[S.form]; R.camera.frame(f[0], f[1], f[2], f[3], 1400); };
  $("#putback").onclick = putBack;
}

// ------------------------------------------------------------------ selection and inspector
function select(i, { fly = false } = {}) {
  S.selected = i; S.trail = new Set(); S.path = [];
  if (i >= 0) { S.trail = foundations(i).nodes; S.act[i] = 1; }
  if (fly && i >= 0) { const p = R.entityPos(i); R.camera.frame(null, null, Math.min(R.camera.radius, 1.6), p, 1300); }
  buildLines(); updateState(); inspector();
}

function foundations(i, depth = 4) {
  // what a record rests on: evidence, support and provenance, followed backwards
  const m = S.model, nodes = new Set([i]), tree = [];
  const back = new Set(["evidence", "supports", "derived_from", "informed", "ran_for", "tests", "produced", "revises", "serves", "depends_on"]);
  const walk = (j, d, into) => {
    if (d >= depth) return;
    for (const l of m.inn[j]) {
      if (!back.has(l.rel) || nodes.has(l.ai)) continue;
      nodes.add(l.ai); const child = { i: l.ai, rel: l.rel, kids: [] }; into.push(child); walk(l.ai, d + 1, child.kids);
    }
    for (const l of m.out[j]) {
      if (!["depends_on", "revises", "derived_from"].includes(l.rel) || nodes.has(l.bi)) continue;
      nodes.add(l.bi); const child = { i: l.bi, rel: l.rel, kids: [] }; into.push(child); walk(l.bi, d + 1, child.kids);
    }
  };
  walk(i, 0, tree);
  return { nodes, tree };
}

function rel(i, name) {
  const e = S.model.entities[i], c = css(entityColor(e, S.palette));
  return `<div class="rel" data-i="${i}"><span class="rn">${esc(name)}</span><i style="background:${c}"></i><span>${esc(trunc(e.label, 64))}</span></div>`;
}
function when(t) {
  if (!t) return "";
  const d = (Date.now() - Date.parse(t)) / 1000;
  if (isNaN(d)) return t;
  if (d < 90) return "just now"; if (d < 5400) return `${Math.round(d / 60)} min ago`;
  if (d < 129600) return `${Math.round(d / 3600)} h ago`; return new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function inspector() {
  const el = $("#inspector"), i = S.selected;
  $("#formnote").style.visibility = i < 0 ? "visible" : "hidden";  // the inspector takes its place
  if (i < 0) { el.classList.remove("show"); return; }
  const m = S.model, e = m.entities[i], meta = e.meta || {};
  const proj = e.project != null && m.byId.has(e.project) ? m.entities[m.byId.get(e.project)].label : null;
  let h = `<button class="x" id="close">×</button>
    <span class="caps"><i style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${css(entityColor(e, S.palette))};margin-right:8px"></i>${esc(KIND_NAME[e.kind] || e.kind)}${e.status ? " · " + esc(e.status) : ""}</span>
    <h2>${esc(e.label)}</h2>
    <div class="meta">${e.t ? `<span>${esc(when(e.t))}</span>` : ""}${proj ? `<span>in <b>${esc(trunc(proj, 40))}</b></span>` : ""}${e.source ? `<span>source <b>${esc(trunc(e.source, 40))}</b></span>` : ""}${e.demo ? `<span style="color:var(--copper)">demonstration record</span>` : ""}</div>`;
  if (e.text && e.text !== e.label) h += `<div class="body">${esc(e.text)}</div>`;
  h += kindDetail(e, meta);
  h += `<div class="acts" id="acts"></div>`;
  // relations
  const groups = {};
  m.out[i].forEach(l => (groups[l.rel] = groups[l.rel] || []).push([l.bi, l.rel]));
  m.inn[i].forEach(l => (groups["← " + l.rel] = groups["← " + l.rel] || []).push([l.ai, "← " + l.rel]));
  const relHtml = Object.values(groups).flat().slice(0, 40).map(([j, n]) => rel(j, n)).join("");
  if (relHtml) h += `<div class="sect"><span class="caps">relations</span>${relHtml}</div>`;
  const f = foundations(i);
  if (f.tree.length) {
    const draw = (nodes) => nodes.map(n => rel(n.i, n.rel) + (n.kids.length ? `<div class="trail">${draw(n.kids)}</div>` : "")).join("");
    h += `<div class="sect"><span class="caps">what this rests on</span><div class="trail">${draw(f.tree)}</div></div>`;
  }
  if (S.path.length > 1) h += `<div class="sect"><span class="caps">path between the two</span>${S.path.map(j => rel(j, "→")).join("")}</div>`;
  h += `<div class="note mono">${esc(e.id)}</div>`;
  el.innerHTML = h; el.classList.add("show");
  el.querySelectorAll(".rel").forEach(r => r.onclick = () => select(+r.dataset.i, { fly: true }));
  $("#close").onclick = () => select(-1);
  actions(e, $("#acts"));
}

function kindDetail(e, meta) {
  const bar = (p, label) => `<div class="sect"><span class="caps">${label}</span><div class="mono" style="color:var(--ink2)">${Math.round(p * 100)}%</div><div class="bar"><div style="width:${p * 100}%"></div></div></div>`;
  let h = "";
  if (e.kind === "hypothesis") {
    if (meta.p_claim_holds != null) h += bar(meta.p_claim_holds, "the model's estimate that this holds (not a measurement)");
    if (meta.revises && S.model.byId.has(meta.revises)) {
      const old = S.model.entities[S.model.byId.get(meta.revises)];
      const p0 = old.meta.p_claim_holds;
      h += `<div class="note">Revised after your evidence${p0 != null && meta.p_claim_holds != null ? `: ${Math.round(p0 * 100)}% → ${Math.round(meta.p_claim_holds * 100)}%` : ""}.</div>`;
      if (meta.evidence_offered) h += `<div class="body">“${esc(meta.evidence_offered.replace("[demo] ", ""))}”</div>`;
    }
    if (meta.what_would_change_my_mind) h += `<div class="sect"><span class="caps">what would change its mind</span><div class="body">${esc(meta.what_would_change_my_mind)}</div></div>`;
  } else if (e.kind === "skill") {
    h += bar(meta.p_mastery || 0, "mastery (unassisted checks only)");
    const u = meta.unassisted || {}, as = meta.assisted || {}, rv = meta.review;
    h += `<div class="meta">${u.n ? `<span>unassisted <b>${u.correct}/${u.n}</b></span>` : ""}${as.n ? `<span>assisted <b>${as.correct}/${as.n}</b> (not counted)</span>` : ""}${meta.trend && meta.trend !== "not enough evidence" ? `<span>trend <b>${esc(meta.trend)}</b></span>` : ""}</div>`;
    if (rv && rv.due) h += `<div class="note">Next unassisted check ${Date.parse(rv.due) <= Date.now() ? "<b>is due now</b>" : "due " + esc(when(rv.due).replace(" ago", " from now"))}. ${esc(rv.reason || "")}</div>`;
    h += `<div class="note">${generatorFor(e.label) ? "MAYANK can test this itself: it writes the question, computes the answer and shows the working only after you answer." : "MAYANK has no generator for this skill yet: answer a check elsewhere, then record it honestly."}</div>`;
  } else if (e.kind === "task") {
    h += `<div class="meta"><span>tool <b>${esc(meta.tool)}</b></span><span>risk <b>${esc(meta.risk)}</b></span>${meta.exit_code != null ? `<span>exit <b>${meta.exit_code}</b></span>` : ""}${meta.duration_ms ? `<span><b>${Math.round(meta.duration_ms)}</b> ms</span>` : ""}<span>by <b>${esc(meta.requested_by)}</b></span></div>`;
    if (meta.reason) h += `<div class="note">${esc(meta.reason)}</div>`;
    const tr = meta.trace || {};
    if (Object.keys(tr).length) {
      h += `<div class="sect"><span class="caps">what it actually did</span>`;
      if (tr.argv) h += `<div class="mono" style="color:var(--ink2)">$ ${esc(tr.argv.join(" "))} → exit ${esc(tr.exit_code)}${tr.timed_out ? " (timed out)" : ""}</div>`;
      if (tr.confinement) h += `<div class="note">${tr.confinement.verified ? `ran in a verified ${esc(tr.confinement.backend)} sandbox, network ${esc(tr.confinement.network)}` : "ran on this machine, unconfined (receipt: verified false)"}</div>`;
      if (tr.path) h += `<div class="mono" style="color:var(--ink2)">${tr.created ? "created" : "wrote"} ${esc(tr.path)} · ${esc(tr.bytes)} bytes${tr.sha256_after ? ` · sha256 ${esc(tr.sha256_after.slice(0, 12))}…` : ""}</div>`;
      if (tr.stdout_tail) h += `<pre class="mono trace">${esc(tr.stdout_tail)}</pre>`;
      if (tr.stderr_tail) h += `<pre class="mono trace err">${esc(tr.stderr_tail)}</pre>`;
      h += `</div>`;
    }
  } else if (e.kind === "memory") {
    h += `<div class="meta"><span>trust <b>${esc(meta.trust)}</b></span><span>sensitivity <b>${esc(meta.sensitivity)}</b></span>${meta.pinned ? "<span><b>pinned</b></span>" : ""}</div>`;
    const against = S.model.out[S.selected].concat(S.model.inn[S.selected]).filter(l => l.rel === "contradicts");
    if (against.length) h += `<div class="note" style="color:var(--rose)">In conflict with ${against.length} other record${against.length > 1 ? "s" : ""}: neither wins until you settle it (confirm one, retract the other).</div>`;
  } else if (e.kind === "assumption") {
    h += `<div class="meta"><span>load-bearing <b>${esc(meta.load_bearing)}</b></span></div>`;
  } else if (e.kind === "goal") {
    h += `<div class="meta">${meta.verdict ? `<span>verdict <b>${esc(meta.verdict)}</b></span>` : ""}${meta.model ? `<span>model <b>${esc(meta.model)}</b></span>` : ""}${meta.turns != null ? `<span><b>${meta.turns}</b> turns</span>` : ""}${meta.checkpoints ? `<span><b>${meta.checkpoints}</b> checkpoints</span>` : ""}${meta.cost_usd != null ? `<span><b>$${(+meta.cost_usd).toFixed(4)}</b></span>` : ""}</div>`;
    if (meta.verification && meta.verification.line) h += `<div class="note">${esc(meta.verification.line)}</div>`;
  } else if (e.kind === "claim") {
    const a = meta.assessments || [];
    if (a.length) h += `<div class="note">${a.map(x => esc(`${x.verdict} (${x.by || "?"})`)).join(" · ")}</div>`;
  } else if (e.kind === "run") {
    if (meta.usage) h += `<div class="meta"><span><b>${meta.usage.steps}</b> steps</span><span><b>${meta.usage.tool_calls}</b> tool calls</span>${meta.steers ? `<span><b>${meta.steers}</b> steers</span>` : ""}</div>`;
    if (meta.events && meta.events.length) h += `<div class="sect"><span class="caps">what happened</span>${meta.events.map(ev => `<div class="mono" style="color:var(--ink2)">${esc(when(ev.at))} · ${esc(ev.type)}</div>`).join("")}</div>`;
    const v = meta.verification || {};
    if (Object.keys(v).length) h += `<div class="note">artifacts current: <b>${v.artifacts_current ? "yes" : "no"}</b> · tests after writes: <b>${v.tests_after_writes ? "yes" : "no"}</b>${v.scope ? ` · ${esc(v.scope)}` : ""}</div>`;
  } else if (e.kind === "agent") {
    h += `<div class="meta"><span>role <b>${esc(meta.role)}</b></span>${(meta.artifacts || []).length ? `<span>makes <b>${esc(meta.artifacts.join(", "))}</b></span>` : ""}${meta.require_tests ? "<span>must pass tests</span>" : ""}</div>`;
    for (const at of meta.attempts || []) h += `<div class="note">attempt: <b>${esc(at.status)}</b>${at.pending ? ` · ${at.pending} waiting for you` : ""}${at.problems.length ? ` · ${esc(at.problems.join("; "))}` : ""}</div>`;
  } else if (e.kind === "model") {
    const served = S.model.inn[S.selected].filter(l => l.rel === "served_by" || l.rel === "used").length;
    h += `<div class="meta"><span>provider <b>${esc(meta.provider)}</b></span><span>used for <b>${served}</b> record${served === 1 ? "" : "s"} here</span></div>`;
  } else if (e.kind === "exchange") {
    h += `<div class="meta">${meta.model ? `<span>model <b>${esc(meta.model)}</b></span>` : ""}${meta.cost_usd != null ? `<span><b>$${(+meta.cost_usd).toFixed(4)}</b></span>` : ""}<span><b>${meta.context_items}</b> context items</span></div>`;
  }
  return h;
}

function btn(label, fn, cls = "") { const b = document.createElement("button"); b.textContent = label; b.className = cls; b.onclick = fn; return b; }
async function act(desc, fn) {
  try { const r = await fn(); toast(desc); scheduleRefresh(200); return r; }
  catch (err) { toast(`${desc.split(":")[0]} failed: ${err.message}`, 7000); }
}

function actions(e, box) {
  if (STATIC) {
    if (e.kind === "skill" && generatorFor(e.label)) box.append(btn("practise, unassisted (T)", () => startAssessment(e), "primary"));
    const note = document.createElement("div"); note.className = "note"; note.textContent = READONLY;
    box.append(note, btn(S.isolate ? "show everything" : "isolate", toggleIsolate), btn("expand", expand));
    return;
  }
  const id = e.id, meta = e.meta || {};
  if (e.kind === "task") {
    if (e.status === "awaiting_approval") {
      box.append(btn("approve and run", () => act("Approved: ALTER ran it", () => api(`/v1/tasks/${id}/approve`, { method: "POST" })), "primary"),
                 btn("deny", () => act("Denied", () => api(`/v1/tasks/${id}/deny`, { method: "POST" })), "danger"));
    } else if (e.status === "approved" || e.status === "running") {
      box.append(btn("cancel", () => act("Cancelled", () => api(`/v1/tasks/${id}/cancel`, { method: "POST" })), "danger"));
    }
  }
  if (e.kind === "run" || e.kind === "agent") {
    const rid = e.kind === "run" ? id : meta.run, pid = e.project;
    for (const [label, path] of [["pause", "pause"], ["resume", "resume"], ["cancel run", "cancel"]]) {
      box.append(btn(label, () => act(`Run: ${label}`, () => api(`/v1/projects/${pid}/runs/${rid}/${path}`, { method: "POST" })), path === "cancel" ? "danger" : ""));
    }
    box.append(btn("steer…", () => {
      const text = prompt("Steer this run (it reaches the next agent turn):");
      if (text) act("Steered", () => api(`/v1/projects/${pid}/runs/${rid}/steer`, { method: "POST", body: { message: text } }));
    }));
  }
  if (e.kind === "memory") {
    box.append(btn("confirm", () => act("Confirmed", () => api(`/v1/memory/${id}/confirm`, { method: "POST" }))),
               btn(meta.pinned ? "unpin" : "pin", () => act(meta.pinned ? "Unpinned" : "Pinned", () => api(`/v1/memory/${id}/pin?pinned=${!meta.pinned}`, { method: "POST" }))),
               btn("retract", () => act("Retracted", () => api(`/v1/memory/${id}/retract`, { method: "POST" })), "danger"));
  }
  if (e.kind === "hypothesis") {
    if (e.status === "open") {
      for (const o of ["held", "failed", "inconclusive"]) {
        box.append(btn(`it ${o === "inconclusive" ? "was inconclusive" : o}`, () => act(`Recorded: ${o}`, () => api(`/v1/mayank/hypotheses/${id}/resolve`, { method: "POST", body: { outcome: o } }))));
      }
    }
    const ta = document.createElement("textarea"); ta.placeholder = "Bring evidence. MAYANK puts it to the same challenge again and must revise only as far as it warrants.";
    box.append(ta, btn("challenge again with this evidence", () => {
      if (!ta.value.trim()) return toast("Write the evidence first.");
      if (!confirm("This sends the claim and your evidence to a model through ALTER's routing (consent and audit apply, and it may cost money). Go on?")) return;
      act("Challenged again: the revision is on the record", () => api(`/v1/mayank/hypotheses/${id}/evidence`, { method: "POST", body: { evidence: ta.value } }));
    }, "primary"));
  }
  if (["memory", "claim", "assumption", "decision", "goal"].includes(e.kind)) {
    box.append(btn("challenge this", () => challengeClaim(e.label)));
  }
  if (e.kind === "assumption") {
    const [cid, k] = [e.id.slice(0, e.id.lastIndexOf("/")), +e.id.slice(e.id.lastIndexOf("/a") + 2)];
    box.append(btn("interrogate this assumption on its own", () => {
      if (!confirmModel()) return;
      act("Challenge recorded: it is linked to the assumption it examines", () => api(`/v1/mayank/hypotheses/${cid}/assumptions/${k}/challenge`, { method: "POST" }));
    }));
  }
  if (e.kind === "run") {
    box.append(btn("advance one agent turn", () => {
      if (!confirmModel()) return;
      act("One agent turn ran: see its tasks", () => api(`/v1/projects/${e.project}/runs/${id}/advance`, { method: "POST" }));
    }, "primary"));
  }
  if (e.kind === "skill" && generatorFor(e.label)) box.append(btn("test me, unassisted (T)", () => startAssessment(e), "primary"));
  if (e.kind === "skill") {
    box.append(btn("I got it right, unassisted", () => act("Recorded an unassisted success", () => api("/v1/growth/probe", { method: "POST", body: { skill: rawSkill(e), correct: true, assisted: false } }))),
               btn("I got it wrong", () => act("Recorded an unassisted miss", () => api("/v1/growth/probe", { method: "POST", body: { skill: rawSkill(e), correct: false, assisted: false } }))));
  }
  box.append(btn(S.isolate ? "show everything" : "isolate", toggleIsolate), btn("expand", expand));
}
function rawSkill(e) { return e.id.startsWith("skill:") ? e.id.slice(6) : e.label; }

function challengeClaim(claim) {
  if (STATIC) { toast(READONLY); return; }
  if (!claim) return;
  if (!confirm(`MAYANK will look for the weakest points in:\n\n“${claim}”\n\nThis is a routed model call through ALTER (consent and audit apply, and it may cost money). Go on?`)) return;
  act("Challenge recorded: see the hypotheses form", async () => { const r = await api("/v1/mayank/hypotheses", { method: "POST", body: { claim } }); setTimeout(() => setForm("hypotheses"), 900); return r; });
}

function toggleIsolate() { S.isolate = !S.isolate && S.selected >= 0; updateState(); inspector(); }

function expand() {
  // spread the selected record's neighbours on a sphere around it
  const i = S.selected; if (i < 0) return;
  const nb = [...neighbours(i)].filter(j => j !== i), c = R.entityPos(i);
  const cur = R.currentPositions("entities"), to = cur.slice();
  nb.forEach((j, k) => {
    const y = 1 - 2 * (k + 0.5) / nb.length, r = Math.sqrt(1 - y * y), a = k * 2.399963;
    const p = [c[0] + Math.cos(a) * r * 0.28, c[1] + y * 0.28, c[2] + Math.sin(a) * r * 0.28];
    to.set(p, j * 3); S.pulled.set(j, p);
  });
  R.updateTargets("entities", cur, to); R.updateTargets("substrate", R.currentPositions("substrate"), R.layers.substrate.cpu.to.slice());
  R.startMorph(1400); buildLines(); pulledUi();
}

function putBack() {
  S.pulled.clear(); pulledUi();
  const cur = R.currentPositions("entities"), t = formPositions(S.form);
  R.updateTargets("entities", cur, t.ent.slice()); R.updateTargets("substrate", R.currentPositions("substrate"), t.sub.slice());
  R.startMorph(1600); buildLines();
}
function pulledUi() {
  const b = $("#putback"); b.style.display = S.pulled.size ? "block" : "none";
  b.textContent = `put back ${S.pulled.size} ${S.pulled.size === 1 ? "entry" : "entries"}`;
}

function pathBetween(a, b) {
  const m = S.model, prev = new Map([[a, -1]]), q = [a];
  while (q.length) {
    const x = q.shift(); if (x === b) break;
    for (const l of m.out[x].concat(m.inn[x])) { const y = l.ai === x ? l.bi : l.ai; if (!prev.has(y)) { prev.set(y, x); q.push(y); } }
  }
  if (!prev.has(b)) return [];
  const p = []; for (let x = b; x !== -1; x = prev.get(x)) p.unshift(x);
  return p;
}

// ------------------------------------------------------------------ pointer
const canvas = $("#gl");
let drag = null;
canvas.addEventListener("contextmenu", e => e.preventDefault());
const touches = new Map();
let pinch = 0;
canvas.addEventListener("pointerdown", e => {
  touches.set(e.pointerId, [e.clientX, e.clientY]);
  if (touches.size === 2) { const [a, b] = [...touches.values()]; pinch = Math.hypot(a[0] - b[0], a[1] - b[1]); drag = null; return; }
  canvas.setPointerCapture(e.pointerId); R.camera.touch();
  const hit = R.pick(e.clientX, e.clientY, 16, i => visibility(i) > 0.1);
  drag = { x: e.clientX, y: e.clientY, button: e.button, shift: e.shiftKey, hit, moved: false, meta: e.metaKey || e.ctrlKey };
});
canvas.addEventListener("pointermove", e => {
  if (touches.has(e.pointerId)) touches.set(e.pointerId, [e.clientX, e.clientY]);
  if (touches.size === 2) {
    const [a, b] = [...touches.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
    if (pinch) R.camera.radius *= pinch / Math.max(1, d);
    pinch = d; R.camera.touch(); return;
  }
  if (!drag) {
    const h = R.pick(e.clientX, e.clientY, 14, i => visibility(i) > 0.1);
    if (h !== S.hovered) { S.hovered = h; updateState(); canvas.classList.toggle("pointing", h >= 0); }
    return;
  }
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
  R.camera.touch();
  if (drag.hit >= 0 && drag.button === 0 && drag.moved) { // pull the record out of the field
    canvas.classList.add("grabbing");
    const p = R.entityPos(drag.hit), [, , w] = R.toScreen(p);
    const cam = R.camera, inv = 2 * Math.tan(0.31) * w / canvas.clientHeight;
    const right = [Math.cos(cam.theta), 0, -Math.sin(cam.theta)];
    const up = [-Math.cos(cam.phi) * Math.sin(cam.theta), Math.sin(cam.phi), -Math.cos(cam.phi) * Math.cos(cam.theta)];
    const np = [p[0] + (right[0] * dx - up[0] * dy) * inv, p[1] + (right[1] * dx - up[1] * dy) * inv, p[2] + (right[2] * dx - up[2] * dy) * inv];
    const L = R.layers.entities, cur = R.currentPositions("entities"), to = L.cpu.to.slice();
    cur.set(np, drag.hit * 3); to.set(np, drag.hit * 3);
    R.updateTargets("entities", cur, to);
    R.mix = 1; S.pulled.set(drag.hit, np); drag.x = e.clientX; drag.y = e.clientY; pulledUi(); buildLines();
    return;
  }
  if (drag.button === 2 || drag.shift) { // pan
    const cam = R.camera, k = cam.radius * 0.0012;
    const right = [Math.cos(cam.theta), 0, -Math.sin(cam.theta)];
    cam.target = [cam.target[0] - right[0] * dx * k, cam.target[1] + dy * k, cam.target[2] - right[2] * dx * k];
  } else { R.camera.vt = -dx * 0.0042; R.camera.vp = -dy * 0.0042; }
  drag.x = e.clientX; drag.y = e.clientY;
});
const lift = e => { touches.delete(e.pointerId); if (touches.size < 2) pinch = 0; };
canvas.addEventListener("pointercancel", e => { lift(e); drag = null; });
canvas.addEventListener("pointerup", e => {
  lift(e);
  canvas.classList.remove("grabbing");
  if (drag && !drag.moved) {
    if (drag.meta && S.selected >= 0 && drag.hit >= 0) { S.path = pathBetween(S.selected, drag.hit); S.trail = new Set(S.path); buildLines(); updateState(); inspector(); }
    else select(drag.hit);
  }
  drag = null;
});
canvas.addEventListener("dblclick", e => { const h = R.pick(e.clientX, e.clientY, 16); if (h >= 0) select(h, { fly: true }); });
canvas.addEventListener("wheel", e => { e.preventDefault(); R.camera.touch(); R.camera.radius *= Math.exp(e.deltaY * 0.0011); }, { passive: false });

// ------------------------------------------------------------------ command line: search and commands
const q = $("#q"), out = $("#cmdout");
let searchTimer = 0, results = [], resultSel = 0;
q.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const v = q.value.trim();
  if (!v) { clearSearch(); return; }
  if (v.startsWith(":")) { showCommands(v); return; }
  searchTimer = setTimeout(() => runSearch(v), 220);
});
q.addEventListener("keydown", e => {
  if (e.key === "Escape") { q.value = ""; clearSearch(); q.blur(); return; }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { resultSel = Math.max(0, Math.min(results.length - 1, resultSel + (e.key === "ArrowDown" ? 1 : -1))); drawResults(); e.preventDefault(); }
  if (e.key === "Enter") {
    const v = q.value.trim();
    if (v.startsWith(":")) { command(v.slice(1)); q.value = ""; out.classList.remove("show"); return; }
    if (results[resultSel]?.ask) askMayank(v);
    else if (results[resultSel]) select(results[resultSel].i, { fly: true });
  }
});
const QUESTION = /\?\s*$|^(what|why|how|which|who|when|where|should|could|can|do|does|did|is|are|was|will|would|explain|tell me|ask)\b/i;
function confirmModel() {
  if (STATIC) { toast(READONLY); return false; }
  try { if (sessionStorage.getItem("mayank.modelok")) return true; } catch (_) {}
  const ok = confirm("This sends your words, and the records ALTER selects for them, to a model through ALTER's routing. Consent, egress checks and the audit apply as always, and it may cost money. Allow model calls for this session?");
  if (ok) try { sessionStorage.setItem("mayank.modelok", "1"); } catch (_) {}
  return ok;
}
async function askMayank(question, { spoken = false } = {}) {
  question = question.replace(/^ask\s+/i, "").trim();
  if (!question || !confirmModel()) return;
  out.classList.remove("show"); q.value = "";   // the question moves to the answer panel
  showAnswer({ question, pending: true });
  try {
    const r = await api("/v1/mayank/ask", { method: "POST", body: { question } });
    showAnswer({ question, ...r });
    S.answerSent = r.sent; showFoundations();
    if (spoken && window.speechSynthesis) speak(r.text);
    scheduleRefresh(400);
  } catch (err) { showAnswer({ question, error: err.message }); }
}
function showFoundations() {
  S.search = new Map((S.answerSent || []).map(id => [S.model.byId.get(id), 1]).filter(([i]) => i != null));
  updateState();
}
function showAnswer(a) {
  const el = $("#answer");
  if (!a) { el.classList.remove("show"); return; }
  let h = `<button class="x" id="ans-close">×</button><span class="caps">mayank · answer</span><h3>${esc(a.question)}</h3>`;
  if (a.pending) h += `<div class="note">Routing your question through ALTER…</div>`;
  else if (a.error) h += `<div class="note" style="color:var(--rose)">${esc(a.error)}</div>`;
  else {
    h += `<div class="body">${esc(a.text)}</div>`;
    h += `<div class="meta">${a.served_by ? `<span>served by <b>${esc(a.served_by)}</b></span>` : ""}${a.cost_usd != null ? `<span><b>$${(+a.cost_usd).toFixed(4)}</b></span>` : ""}<span>drew on <b>${a.sent.length}</b> record${a.sent.length === 1 ? "" : "s"}</span>${a.withheld ? `<span><b>${a.withheld}</b> withheld by egress rules</span>` : ""}</div>`;
    const rows = a.sent.map(id => S.model.byId.get(id)).filter(i => i != null).slice(0, 10).map(i => rel(i, "sent")).join("");
    if (rows) h += `<div class="sect"><span class="caps">what this answer rests on</span>${rows}</div>`;
    h += `<div class="acts"><button id="ans-show">light what it drew on</button>${window.speechSynthesis ? `<button id="ans-speak">read aloud</button>` : ""}</div>`;
  }
  el.innerHTML = h; el.classList.add("show");
  $("#ans-close").onclick = () => { showAnswer(null); S.answerSent = null; clearSearch(); };
  el.querySelectorAll(".rel").forEach(r => r.onclick = () => select(+r.dataset.i, { fly: true }));
  if ($("#ans-show")) $("#ans-show").onclick = showFoundations;
  if ($("#ans-speak")) $("#ans-speak").onclick = () => speak(a.text);
}

async function runSearch(v) {
  try {
    const r = await api(`/v1/mayank/search?q=${encodeURIComponent(v)}&k=40`);
    if (q.value.trim() !== v) return;
    const top = r.results.length ? r.results[0].score : 1;
    S.search = new Map();
    results = [];
    r.results.forEach(x => {
      const i = S.model.byId.get(x.id); if (i == null) return;
      const s = Math.max(0, Math.min(1, x.score / Math.max(0.05, top)));
      if (s > 0.35) S.search.set(i, s);
      results.push({ i, score: x.score });
    });
    results = results.slice(0, 9); resultSel = 0;
    if (QUESTION.test(v)) results.unshift({ ask: v });   // a question: offer to ask, first
    drawResults(); updateState();
  } catch (err) { out.innerHTML = `<div class="r">${esc(err.message)}</div>`; out.classList.add("show"); }
}
function drawResults() {
  out.innerHTML = results.map((x, k) => {
    if (x.ask) return `<div class="r${k === resultSel ? " sel" : ""}" data-ask="1"><i style="background:var(--copper)"></i><span>Ask MAYANK: “${esc(trunc(x.ask, 60))}”</span><span class="s">${STATIC ? "needs ALTER running" : "a routed model call · Enter"}</span></div>`;
    const e = S.model.entities[x.i];
    return `<div class="r${k === resultSel ? " sel" : ""}" data-i="${x.i}"><i style="background:${css(entityColor(e, S.palette))}"></i><span>${esc(trunc(e.label, 70))}</span><span class="s">${esc(KIND_NAME[e.kind] || e.kind)} · ${x.score.toFixed(2)}</span></div>`;
  }).join("") || `<div class="r" style="color:var(--ink3)">nothing close</div>`;
  out.classList.add("show");
  out.querySelectorAll(".r[data-i]").forEach(r => r.onclick = () => select(+r.dataset.i, { fly: true }));
  out.querySelectorAll(".r[data-ask]").forEach(r => r.onclick = () => askMayank(q.value.trim()));
}
function clearSearch() { S.search = new Map(); results = []; out.classList.remove("show"); updateState(); }

const COMMANDS = {
  ask: "ask <question>: MAYANK answers, and shows the records the answer rests on (a routed model call)",
  goal: "goal <objective>: start a run in the selected record's project; its agents appear in the execution form",
  test: "test: an unassisted check on the selected skill, written and marked by MAYANK (no model)",
  listen: "listen: speak a search or a question",
  form: "form <name>: human, constellation, network, timeline, grid, execution, hypotheses, mastery",
  challenge: "challenge <claim>: MAYANK argues against it (a routed model call)",
  approve: "approve: run the selected task you were asked about", deny: "deny: refuse the selected task",
  isolate: "isolate: only the selection and what touches it", expand: "expand: spread the selection's neighbours",
  putback: "putback: return pulled records", clear: "clear: selection, search and isolation",
  halt: "halt: Stop-all (no tool runs until resume)", resume: "resume: lift Stop-all",
  refresh: "refresh: reload the field", help: "help: keys and commands",
};
function showCommands(v) {
  const w = v.slice(1).split(" ")[0];
  out.innerHTML = Object.entries(COMMANDS).filter(([k]) => k.startsWith(w)).map(([k, d]) => `<div class="r"><span class="mono" style="color:var(--copper)">:${k}</span><span style="color:var(--ink2)">${esc(d.split(":").slice(1).join(":"))}</span></div>`).join("");
  out.classList.add("show");
}
async function command(text) {
  const [cmd, ...rest] = text.trim().split(/\s+/), arg = rest.join(" ");
  const sel = S.selected >= 0 ? S.model.entities[S.selected] : null;
  switch (cmd) {
    case "form": setForm(arg); break;
    case "ask": askMayank(arg); break;
    case "goal": startGoal(arg, sel); break;
    case "test": if (sel?.kind === "skill") startAssessment(sel); else toast("Select a skill first (the mastery form, 8)."); break;
    case "listen": listen(); break;
    case "challenge": challengeClaim(arg || sel?.label); break;
    case "approve": if (sel?.status === "awaiting_approval") act("Approved: ALTER ran it", () => api(`/v1/tasks/${sel.id}/approve`, { method: "POST" })); else toast("Select a task that is waiting for you."); break;
    case "deny": if (sel?.status === "awaiting_approval") act("Denied", () => api(`/v1/tasks/${sel.id}/deny`, { method: "POST" })); else toast("Select a task that is waiting for you."); break;
    case "isolate": toggleIsolate(); break;
    case "expand": expand(); break;
    case "putback": putBack(); break;
    case "clear": S.isolate = false; select(-1); clearSearch(); break;
    case "halt": if (confirm("Stop-all: no tool runs until you resume. Go on?")) act("Stop-all is on", () => api("/v1/autonomy/halt", { method: "POST" })); break;
    case "resume": act("Stop-all lifted", () => api("/v1/autonomy/resume", { method: "POST" })); break;
    case "refresh": refresh(); break;
    case "help": default: help(); break;
  }
}
async function startGoal(objective, sel) {
  if (STATIC) { toast(READONLY); return; }
  if (!objective) { toast("Say what the goal is: :goal <objective>"); return; }
  const projects = S.model.entities.filter(e => e.kind === "project");
  const pid = sel?.project || (projects.length === 1 ? projects[0].id : null);
  if (!pid) { toast("Select something in the project this goal belongs to, then try again."); return; }
  await act("Run started: its agents are in the execution form, waiting for you to advance them", () => api(`/v1/projects/${pid}/runs`, { method: "POST", body: { objective } }));
  setTimeout(() => setForm("execution"), 900);
}
function help() {
  toast("1–8 forms · drag to turn · right-drag to move · wheel to zoom · click a point to inspect · ⌘-click a second for the path between · drag a point out to keep it · I isolate · E expand · P put back · [ ] size · L light · / search or ask (end with ?) · T test a skill · V speak · : commands", 9000);
}

document.addEventListener("keydown", e => {
  if (e.target === q || e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
  const n = +e.key;
  if (n >= 1 && n <= FORMS.length) setForm(FORMS[n - 1]);
  else if (e.key === "/" || e.key === ":") { e.preventDefault(); q.focus(); if (e.key === ":") { q.value = ":"; showCommands(":"); } }
  else if (e.key === "Escape") { S.isolate = false; select(-1); clearSearch(); }
  else if (e.key === "i" || e.key === "I") toggleIsolate();
  else if (e.key === "e" || e.key === "E") expand();
  else if (e.key === "p" || e.key === "P") putBack();
  else if (e.key === "[") R.scale = Math.max(0.4, R.scale / 1.2);
  else if (e.key === "]") R.scale = Math.min(3, R.scale * 1.2);
  else if (e.key === "l" || e.key === "L") $("#light").click();
  else if (e.key === "r" || e.key === "R") $("#reset").click();
  else if (e.key === "?") help();
  else if (e.key === "t" || e.key === "T") { const sel = S.selected >= 0 ? S.model.entities[S.selected] : null; if (sel?.kind === "skill") startAssessment(sel); }
  else if (e.key === "v" || e.key === "V") listen();
});

// ------------------------------------------------------------------ assessments: written and marked by MAYANK
let A = null;
function startAssessment(e) {
  const gen = generatorFor(e.label);
  if (!gen) { toast("MAYANK has no generator for this skill yet."); return; }
  select(-1);
  A = { e, gen, d: difficultyFor(e.meta.p_mastery || 0), n: 0, right: 0, item: null, answered: false };
  R.light = 0.45;                       // the field steps back: nothing on screen helps you answer
  $("#assess").classList.add("show");
  $("#as-skill").textContent = e.meta.path ? e.meta.path.join(" / ") : e.label;
  $("#as-rule").textContent = "Answer from your own head. MAYANK wrote this question and computed its answer itself; no model is involved, and the working appears only after you answer. "
    + (STATIC ? "This is a snapshot, so nothing is recorded: practice only." : "Your result is recorded as an unassisted check, and only unassisted checks move mastery.");
  nextQuestion();
}
function nextQuestion() {
  A.item = A.gen(A.d, Math.random); A.answered = false;
  $("#as-q").textContent = A.item.q;
  $("#as-a").value = ""; $("#as-a").placeholder = ANSWER_HINT[A.item.kind];
  $("#as-result").innerHTML = ""; $("#as-submit").textContent = "check my answer";
  $("#as-level").textContent = ["level 1 of 3", "level 2 of 3", "level 3 of 3"][A.d];
  $("#as-a").focus();
}
async function submitAnswer() {
  if (!A) return;
  if (A.answered) { nextQuestion(); return; }
  if (!$("#as-a").value.trim()) return;
  const ok = checkAnswer(A.item, $("#as-a").value);
  A.answered = true; A.n++; if (ok) A.right++;
  const shown = A.item.kind === "number" ? +A.item.answer.toFixed(4) : A.item.answer;
  $("#as-result").innerHTML = `<div class="${ok ? "right" : "wrong"}">${ok ? "Right." : `Not quite: the answer is ${esc(shown)}.`}</div><div class="body">${esc(A.item.why)}</div>`;
  $("#as-score").textContent = `${A.right} of ${A.n} this session`;
  $("#as-submit").textContent = "next question";
  A.d = Math.max(0, Math.min(2, A.d + (ok ? 1 : -1)));
  if (!STATIC) {
    try { const r = await api("/v1/growth/probe", { method: "POST", body: { skill: rawSkill(A.e), correct: ok, assisted: false } });
          $("#as-score").textContent += ` · recorded · mastery now ${Math.round(r.p_mastery * 100)}%`; scheduleRefresh(600); }
    catch (err) { $("#as-score").textContent += ` · not recorded: ${err.message}`; }
  }
}
function endAssessment() { $("#assess").classList.remove("show"); A = null; R.light = 1; }
$("#as-submit").onclick = submitAnswer;
$("#as-close").onclick = endAssessment;
$("#as-a").addEventListener("keydown", ev => { if (ev.key === "Enter") submitAnswer(); if (ev.key === "Escape") endAssessment(); });

// ------------------------------------------------------------------ voice (Web Speech, where the browser has it)
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let listening = null;
if (!SR) $("#mic").style.display = "none";
function listen() {
  if (!SR) { toast("This browser has no speech recognition. Chrome and Safari do."); return; }
  if (listening) { listening.stop(); return; }
  const rec = new SR();
  rec.lang = navigator.language || "en-US"; rec.interimResults = true; rec.maxAlternatives = 1;
  listening = rec; $("#mic").classList.add("on"); q.placeholder = "Listening…";
  rec.onresult = ev => {
    const res = ev.results[ev.results.length - 1], text = res[0].transcript.trim();
    q.value = text;
    if (res.isFinal) {
      if (QUESTION.test(text) && !STATIC) askMayank(text, { spoken: true });
      else runSearch(text);
    }
  };
  rec.onerror = ev => toast(`Voice: ${ev.error}`);
  rec.onend = () => { listening = null; $("#mic").classList.remove("on"); q.placeholder = SMALL ? "Search, or : for commands" : "Search what ALTER knows, ask a question, or type : for a command"; };
  rec.start();
}
function speak(text) {
  if (!window.speechSynthesis) return;
  speechSynthesis.cancel();
  speechSynthesis.speak(new SpeechSynthesisUtterance(String(text).slice(0, 900)));
}
$("#mic").onclick = listen;

// ------------------------------------------------------------------ live: the audit chain
const STRUCTURAL = /^(task|goal|memory|claim|challenge|growth|run|project|note|model|plan|workspace|consent|autonomy|exchange)\./;
function onEvent(ev) {
  const m = S.model;
  let label = "";
  for (const ref of ev.refs) {
    const i = m.byId.get(ref);
    // the record an event is about lights fully; its project is context and only glows
    if (i != null) { const ctx = m.entities[i].kind === "project"; S.act[i] = Math.max(S.act[i], ctx ? 0.25 : 1); if (!ctx) label = label || m.entities[i].label; }
    else S.pendingActivate.add(ref);
  }
  if (ev.event === "growth.probe") { m.entities.forEach((e, i) => { if (e.kind === "skill") S.act[i] = Math.max(S.act[i], 0.25); }); }
  const tick = $("#ticker"), row = document.createElement("div");
  const time = new Date(ev.ts).toLocaleTimeString(undefined, { hour12: false });
  row.textContent = `${time}  ${ev.event}${label ? "  " + trunc(label, 38) : ""}${ev.payload.status ? "  · " + ev.payload.status : ""}`;
  row.className = "fresh"; tick.prepend(row); setTimeout(() => row.classList.remove("fresh"), 2500);
  while (tick.children.length > 9) tick.lastChild.remove();
  S.lastEvents.push(ev); if (S.lastEvents.length > 50) S.lastEvents.shift();
  if (STRUCTURAL.test(ev.event) && !STATIC) scheduleRefresh();
}
function setLive(on, text) {
  S.live = on; $("#livedot").classList.toggle("on", on);
  $("#livetext").textContent = text || (on ? `live · audit #${S.cursor}` : "reconnecting");
}
async function replayLoop() {
  // the audit events recorded when this snapshot was made, replayed on a loop with their own timing
  const rec = await (await fetch(STATIC.replay)).json().catch(() => ({ events: [] }));
  setLive(true, rec.events.length ? `replaying activity recorded ${rec.recorded_at}` : "snapshot");
  if (!rec.events.length) return;
  for (;;) {
    for (const ev of rec.events) {
      await new Promise(res => setTimeout(res, Math.max(900, Math.min(4500, ev.dt * 1000))));
      onEvent({ ...ev, ts: new Date().toISOString() });
    }
    await new Promise(res => setTimeout(res, 5000));
  }
}
async function liveLoop() {
  for (;;) {
    try {
      const r = await api(`/v1/mayank/events?after=${S.cursor}&run_after=${S.runCursor || 0}&wait=20`);
      S.cursor = r.cursor; if (r.run_cursor != null) S.runCursor = r.run_cursor; setLive(true);
      r.events.forEach(onEvent);
    } catch (err) {
      setLive(false, err.message === "unauthorized" ? "token needed" : "reconnecting");
      await new Promise(res => setTimeout(res, 3000));
    }
  }
}
function scheduleRefresh(ms = 1200) { clearTimeout(S.refreshTimer); S.refreshTimer = setTimeout(refresh, ms); }
async function refresh() {
  const field = await api("/v1/mayank/field").catch(err => { toast(`Could not refresh: ${err.message}`); return null; });
  if (!field) return;
  const old = S.model, prev = new Map(), selId = S.selected >= 0 ? old.entities[S.selected].id : null;
  const pulledIds = new Map([...S.pulled].map(([i, p]) => [old.entities[i].id, p]));
  old.entities.forEach((e, i) => prev.set(e.id, R.entityPos(i)));
  const sameShape = field.entities.length === old.entities.length && field.entities.every((e, i) => e.id === old.entities[i].id)
    && field.links.length === old.links.length;
  S.field = field; S.model = buildModel(field);
  S.pulled = new Map([...pulledIds].filter(([id]) => S.model.byId.has(id)).map(([id, p]) => [S.model.byId.get(id), p]));
  const keepAct = S.act;
  if (sameShape) {
    S.formCache.clear(); recolor(); S.act = keepAct; counts();
  } else {
    initParticles(prev);
  }
  for (const ref of S.pendingActivate) { const i = S.model.byId.get(ref); if (i != null) S.act[i] = 1; }
  S.pendingActivate.clear();
  S.selected = selId && S.model.byId.has(selId) ? S.model.byId.get(selId) : -1;
  $("#formnote").textContent = FORM_NOTE[S.form]();
  buildLines(); updateState(); if (S.selected >= 0) inspector();
}

// ------------------------------------------------------------------ frame loop
let last = performance.now(), frames = 0, fpsT = last;
function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000); last = now;
  if (S.model) {
    let any = false;
    for (let i = 0; i < S.act.length; i++) if (S.act[i] > 0.002) { S.act[i] *= Math.exp(-dt / 2.4); any = true; }
    if (any || R.morphing) updateState();
    R.draw(now / 1000, dt);
    renderLabels();
  }
  frames++;
  if (now - fpsT > 1000) {
    S.fps = frames * 1000 / (now - fpsT); frames = 0; fpsT = now;
    const total = (S.model ? S.model.entities.length : 0) + N_SUB;
    $("#perfline").textContent = `${S.fps.toFixed(0)} fps · ${R.drawMs.toFixed(1)} ms cpu/frame · ${total.toLocaleString()} points · webgl2`;
  }
  requestAnimationFrame(frame);
}

// ------------------------------------------------------------------ start
function showGate(message = "") {
  $("#gate").classList.add("show");
  $("#gateerr").textContent = message;
  setLive(false, "token needed");
  $("#enter").onclick = () => { token = $("#token").value.trim(); try { sessionStorage.setItem("pcim_token", token); } catch (_) {} $("#gate").classList.remove("show"); start(); };
  $("#token").onkeydown = e => { if (e.key === "Enter") $("#enter").click(); };
}
let started = false;
async function start() {
  if (!token && !STATIC) { showGate(); return; }
  if (started) return;
  let field;
  try { field = await api("/v1/mayank/field"); }
  catch (err) {
    if (err.message === "unauthorized") return;
    const unreachable = err instanceof TypeError;  // fetch itself failed: no server answered
    fatal(unreachable ? `Can't reach ALTER at ${location.origin}. Is \`pcim serve\` still running there?` : `Could not read the field: ${err.message}`);
    return;
  }
  started = true;
  S.field = field; S.model = buildModel(field); S.cursor = field.cursor; S.runCursor = field.run_cursor || 0;
  buildChrome(); buildFloor();
  initParticles(null);
  setForm(S.form, { frame: false });
  const f = FRAMING.human; R.camera.theta = 0.9; R.camera.radius = 5.2; R.camera.phi = 1.3; R.camera.frame(f[0], f[1], f[2], f[3], 3800);
  if (STATIC) replayLoop(); else { setLive(true, "live"); liveLoop(); }
  window.__mayank.ready = true;
  if (!field.entities.length) toast("This store has no records yet. Seed demonstration data with scripts/mayank_demo.py, or start working with ALTER.", 9000);
}

// for automated checks (and the curious): read-only views plus the same actions as the keys
window.__mayank = {
  ready: false,
  get fps() { return S.fps; }, get form() { return S.form; }, get selected() { return S.selected >= 0 ? S.model.entities[S.selected].id : null; },
  get counts() { return { entities: S.model?.entities.length || 0, links: S.model?.links.length || 0, substrate: N_SUB }; },
  get events() { return S.lastEvents.slice(); }, get morphing() { return R.morphing; }, get drawMs() { return R.drawMs; },
  setForm, select: id => select(S.model.byId.get(id) ?? -1, { fly: true }),
  screenOf: id => { const i = S.model.byId.get(id); return i == null ? null : R.toScreen(R.entityPos(i)); },
  search: v => { q.value = v; return runSearch(v); }, activation: id => S.act[S.model.byId.get(id)] || 0,
  pulled: () => [...S.pulled.keys()].map(i => S.model.entities[i].id),
  ask: (text, opts) => askMayank(text, opts), assess: id => startAssessment(S.model.entities[S.model.byId.get(id)]),
  get assessment() { return A ? { q: A.item.q, answer: A.item.answer, kind: A.item.kind, d: A.d, n: A.n, right: A.right } : null; },
  get highlighted() { return [...S.search.keys()].map(i => S.model.entities[i].id); },
};
requestAnimationFrame(frame);
start();
