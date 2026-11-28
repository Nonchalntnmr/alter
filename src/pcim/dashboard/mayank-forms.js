// MAYANK geometry: the body, and every form the same particles can take.
// Pure functions of the field (entities, links) and a seed, so a form is reproducible and the
// same entity is the same particle in every form. Units: the body stands about 2 tall, y up.

export function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0;
    return s / 4294967296;
  };
}

export function hash01(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return ((h >>> 0) % 100000) / 100000;
}

function gauss(r) { // Box-Muller
  const u = Math.max(1e-9, r()), v = r();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(6.283185307 * v);
}

// ------------------------------------------------------------------ the body (signed distance)
const caps = [];   // [ax, ay, az, bx, by, bz, r, region]
const ells = [];   // [cx, cy, cz, rx, ry, rz, region]
function cap(a, b, r, region) { caps.push([...a, ...b, r, region]); }
function ell(c, rr, region) { ells.push([...c, ...rr, region]); }

ell([0, 0.80, 0.0], [0.100, 0.122, 0.112], "head");
ell([0, 0.715, 0.035], [0.062, 0.05, 0.065], "head");                 // jaw
cap([0, 0.60, -0.005], [0, 0.70, 0.005], 0.046, "torso");              // neck
ell([0, 0.43, 0.0], [0.185, 0.155, 0.105], "torso");                  // chest
ell([0, 0.22, 0.005], [0.150, 0.140, 0.092], "torso");                // abdomen
ell([0, 0.02, 0.0], [0.165, 0.095, 0.100], "legs");                   // pelvis
cap([-0.17, 0.545, -0.01], [0.17, 0.545, -0.01], 0.058, "torso");     // shoulders
for (const s of [-1, 1]) {
  cap([0.205 * s, 0.535, -0.01], [0.285 * s, 0.24, 0.0], 0.047, "arms");
  cap([0.285 * s, 0.24, 0.0], [0.33 * s, -0.035, 0.055], 0.038, "arms");
  cap([0.33 * s, -0.035, 0.055], [0.345 * s, -0.135, 0.07], 0.030, "arms");
  cap([0.085 * s, -0.04, 0.0], [0.105 * s, -0.47, 0.012], 0.072, "legs");
  cap([0.105 * s, -0.47, 0.012], [0.115 * s, -0.875, -0.012], 0.050, "legs");
  cap([0.115 * s, -0.91, -0.02], [0.125 * s, -0.935, 0.085], 0.032, "legs");
}

function sdCap(px, py, pz, c) {
  const bax = c[3] - c[0], bay = c[4] - c[1], baz = c[5] - c[2];
  const pax = px - c[0], pay = py - c[1], paz = pz - c[2];
  const h = Math.max(0, Math.min(1, (pax * bax + pay * bay + paz * baz) / (bax * bax + bay * bay + baz * baz)));
  const dx = pax - bax * h, dy = pay - bay * h, dz = paz - baz * h;
  return Math.sqrt(dx * dx + dy * dy + dz * dz) - c[6];
}
function sdEll(px, py, pz, e) { // approximate, good near the surface
  const x = (px - e[0]) / e[3], y = (py - e[1]) / e[4], z = (pz - e[2]) / e[5];
  const k0 = Math.sqrt(x * x + y * y + z * z);
  return (k0 - 1) * Math.min(e[3], e[4], e[5]);
}
function smin(a, b, k) { const h = Math.max(k - Math.abs(a - b), 0) / k; return Math.min(a, b) - h * h * k * 0.25; }

export function bodySDF(x, y, z) {
  let d = 1e9, region = "torso", best = 1e9;
  for (const e of ells) { const v = sdEll(x, y, z, e); d = smin(d, v, 0.05); if (v < best) { best = v; region = e[6]; } }
  for (const c of caps) { const v = sdCap(x, y, z, c); d = smin(d, v, 0.04); if (v < best) { best = v; region = c[7]; } }
  return [d, region];
}

// Points on and inside the body: a shell (the silhouette), a sparse interior, a dense folded brain,
// and the spine with nerves running out along the limbs.
export function sampleBody(n, seed = 11) {
  const r = rng(seed), pos = new Float32Array(n * 3), region = new Array(n);
  let i = 0;
  const put = (x, y, z, reg) => { pos[i * 3] = x; pos[i * 3 + 1] = y; pos[i * 3 + 2] = z; region[i] = reg; i++; };
  const nBrain = Math.floor(n * 0.12), nSpine = Math.floor(n * 0.07);
  // brain: a folded surface inside the skull (gyri from a sum of sines)
  while (i < nBrain) {
    const u = r() * Math.PI * 2, v = Math.acos(2 * r() - 1);
    const fold = 0.06 * Math.sin(9 * u + 3 * Math.sin(5 * v)) * Math.sin(7 * v + 2 * Math.cos(4 * u));
    const s = 0.80 + fold + (r() - 0.5) * 0.05;
    const x = Math.sin(v) * Math.cos(u) * 0.088 * s, y = 0.825 + Math.cos(v) * 0.098 * s, z = Math.sin(v) * Math.sin(u) * 0.098 * s;
    if (y > 0.76 || Math.abs(x) > 0.03) put(x, y, z - 0.004, "head");
  }
  // spine and nerves
  const spineEnd = i + nSpine;
  while (i < spineEnd) {
    const t = r();
    if (t < 0.45) { // spine curve
      const y = 0.66 - r() * 0.66;
      put((r() - 0.5) * 0.012, y, -0.06 + 0.025 * Math.sin(y * 6) + (r() - 0.5) * 0.01, "torso");
    } else { // nerve: from the spine along a limb, jittered
      const limb = caps[3 + Math.floor(r() * (caps.length - 3))];
      const h = r();
      put(limb[0] + (limb[3] - limb[0]) * h + gauss(r) * 0.006, limb[1] + (limb[4] - limb[1]) * h + gauss(r) * 0.006,
          limb[2] + (limb[5] - limb[2]) * h + gauss(r) * 0.006, limb[7]);
    }
  }
  // shell and interior by rejection
  while (i < n) {
    const x = (r() - 0.5) * 0.8, y = -0.97 + r() * 1.92, z = (r() - 0.5) * 0.36;
    const [d, reg] = bodySDF(x, y, z);
    const shell = d < 0 && d > -0.012, interior = d < -0.012 && r() < 0.035;
    if (shell || interior) put(x, y, z, reg);
  }
  return { pos, region };
}

// Region volumes for entity anchors: points well inside each part of the body.
function sampleRegion(reg, count, r) {
  const out = [];
  let guard = 0;
  while (out.length < count && guard++ < count * 4000) {
    const x = (r() - 0.5) * 0.8, y = -0.97 + r() * 1.92, z = (r() - 0.5) * 0.36;
    const [d, rr] = bodySDF(x, y, z);
    if (d < -0.008 && rr === reg) out.push([x, y, z]);
  }
  while (out.length < count) out.push([0, 0.3, 0]);
  return out;
}

const REGION_AXIS = { // how each region is ordered, so semantic neighbours sit side by side
  head: p => Math.atan2(p[2], p[0]) + p[1] * 3,
  torso: p => p[1] * 4 + p[0],
  arms: p => (p[0] < 0 ? -1 : 1) * (1 - p[1]),
  legs: p => (p[0] < 0 ? -1 : 1) * (1 - p[1]),
};

// ------------------------------------------------------------------ forms
// Each returns Float32Array(3*E) of entity positions.
export const FORMS = ["human", "constellation", "network", "timeline", "grid", "execution", "hypotheses", "mastery"];

export function humanEntities(model) {
  const E = model.entities.length, out = new Float32Array(E * 3), r = rng(5);
  const byRegion = {};
  model.entities.forEach((e, i) => { (byRegion[e.region] = byRegion[e.region] || []).push(i); });
  for (const [reg, idx] of Object.entries(byRegion)) {
    const anchors = sampleRegion(reg, idx.length, r);
    const key = REGION_AXIS[reg] || REGION_AXIS.torso;
    anchors.sort((a, b) => key(a) - key(b));
    idx.sort((a, b) => model.semantic(a) - model.semantic(b));
    idx.forEach((ei, k) => out.set(anchors[k], ei * 3));
  }
  return out;
}

export function constellationEntities(model) {
  const E = model.entities.length, out = new Float32Array(E * 3);
  model.entities.forEach((e, i) => {
    const [x, y, z] = e.xyz || [0, 0, 0];
    out[i * 3] = x * 1.25; out[i * 3 + 1] = 0.15 + y * 0.75; out[i * 3 + 2] = z * 1.25;
  });
  return out;
}

export function networkEntities(model) {
  // force layout seeded from the semantic positions: springs along links, repulsion on a grid
  const E = model.entities.length, p = constellationEntities(model), v = new Float32Array(E * 3);
  const links = model.links, iters = E > 1500 ? 60 : 140, cell = 0.25;
  for (let it = 0; it < iters; it++) {
    const grid = new Map();
    for (let i = 0; i < E; i++) {
      const k = `${Math.floor(p[i * 3] / cell)},${Math.floor(p[i * 3 + 1] / cell)},${Math.floor(p[i * 3 + 2] / cell)}`;
      (grid.get(k) || grid.set(k, []).get(k)).push(i);
    }
    for (let i = 0; i < E; i++) {
      const cx = Math.floor(p[i * 3] / cell), cy = Math.floor(p[i * 3 + 1] / cell), cz = Math.floor(p[i * 3 + 2] / cell);
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
        const g = grid.get(`${cx + dx},${cy + dy},${cz + dz}`);
        if (!g) continue;
        for (const j of g) {
          if (j <= i) continue;
          let ax = p[i * 3] - p[j * 3], ay = p[i * 3 + 1] - p[j * 3 + 1], az = p[i * 3 + 2] - p[j * 3 + 2];
          const d2 = ax * ax + ay * ay + az * az + 1e-4;
          if (d2 > cell * cell) continue;
          const f = 0.0009 / d2;
          ax *= f; ay *= f; az *= f;
          v[i * 3] += ax; v[i * 3 + 1] += ay; v[i * 3 + 2] += az;
          v[j * 3] -= ax; v[j * 3 + 1] -= ay; v[j * 3 + 2] -= az;
        }
      }
    }
    for (const l of links) {
      const a = l.ai, b = l.bi;
      const dx = p[b * 3] - p[a * 3], dy = p[b * 3 + 1] - p[a * 3 + 1], dz = p[b * 3 + 2] - p[a * 3 + 2];
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) + 1e-6, f = (d - 0.16) * 0.02 / d;
      v[a * 3] += dx * f; v[a * 3 + 1] += dy * f; v[a * 3 + 2] += dz * f;
      v[b * 3] -= dx * f; v[b * 3 + 1] -= dy * f; v[b * 3 + 2] -= dz * f;
    }
    for (let i = 0; i < E * 3; i++) {
      const c = (i % 3 === 1 ? 0.15 : 0);
      v[i] += (c - p[i]) * 0.002; // gentle pull to the centre
      p[i] += Math.max(-0.05, Math.min(0.05, v[i]));
      v[i] *= 0.82;
    }
  }
  return p;
}

export function timelineEntities(model) {
  // a rising helix: time runs up and around; each kind keeps its own lane (radius)
  const E = model.entities.length, out = new Float32Array(E * 3);
  const ts = model.entities.map(e => (e.t ? Date.parse(e.t) : NaN));
  const valid = ts.filter(t => !isNaN(t));
  const t0 = valid.length ? Math.min(...valid) : 0, t1 = valid.length ? Math.max(...valid) : 1;
  const order = [...Array(E).keys()].sort((a, b) => (ts[a] || 0) - (ts[b] || 0) || a - b);
  const kinds = model.kindList;
  order.forEach((i, rank) => {
    const u = (t1 > t0 && !isNaN(ts[i])) ? (ts[i] - t0) / (t1 - t0) : rank / Math.max(1, E - 1);
    const w = 0.7 * u + 0.3 * rank / Math.max(1, E - 1); // even out bursts recorded in one session
    const ang = w * Math.PI * 2 * 2.25, lane = kinds.indexOf(model.entities[i].kind);
    const rad = 0.55 + 0.035 * lane;
    out[i * 3] = Math.cos(ang) * rad; out[i * 3 + 1] = -0.85 + w * 1.85; out[i * 3 + 2] = Math.sin(ang) * rad;
  });
  return out;
}

export const GRID = { top: 0.86, height: 1.42, left: -1.1, width: 2.2 };
export function gridEntities(model) {
  // horizontal bands, one per kind, oldest first left to right: the archive laid flat
  const E = model.entities.length, out = new Float32Array(E * 3), kinds = model.kindList, n = kinds.length;
  const rows = {};
  model.entities.forEach((e, i) => { (rows[e.kind] = rows[e.kind] || []).push(i); });
  kinds.forEach((k, ri) => {
    const idx = (rows[k] || []).sort((a, b) => String(model.entities[a].t).localeCompare(String(model.entities[b].t)) || a - b);
    const y = GRID.top - (ri + 0.5) * GRID.height / n, perRow = Math.max(1, Math.min(idx.length, 64));
    idx.forEach((ei, j) => {
      const sub = Math.floor(j / perRow), col = j % perRow;
      out[ei * 3] = GRID.left + GRID.width * (col + 0.5) / perRow;
      out[ei * 3 + 1] = y + sub * 0.012;
      out[ei * 3 + 2] = 0.03;
    });
  });
  return out;
}

function layered(model, members, depOf) {
  // a left-to-right layout of a dependency graph: layer = longest path from a root
  const layer = new Map();
  const visit = (i, seen = new Set()) => {
    if (layer.has(i)) return layer.get(i);
    if (seen.has(i)) return 0;
    seen.add(i);
    const deps = depOf(i).filter(j => members.has(j));
    const L = deps.length ? 1 + Math.max(...deps.map(j => visit(j, seen))) : 0;
    layer.set(i, L);
    return L;
  };
  members.forEach(i => visit(i));
  return layer;
}

export function executionEntities(model) {
  // goals, runs, plans -> agents, objectives -> tasks -> evidence and claims, left to right.
  const E = model.entities.length, out = new Float32Array(E * 3);
  const exec = new Set(["project", "goal", "run", "agent", "task", "plan", "objective", "claim", "evidence"]);
  const members = new Set(model.entities.map((e, i) => exec.has(e.kind) ? i : -1).filter(i => i >= 0));
  const parents = i => model.out[i].filter(l => ["part_of", "ran_for", "depends_on", "serves"].includes(l.rel)).map(l => l.bi)
    .concat(model.inn[i].filter(l => l.rel === "evidence").map(l => l.ai));
  const layer = layered(model, members, parents);
  const maxL = Math.max(1, ...layer.values());
  const cols = {};
  members.forEach(i => { const L = layer.get(i) || 0; (cols[L] = cols[L] || []).push(i); });
  for (const [L, idx] of Object.entries(cols)) {
    idx.sort((a, b) => model.semantic(a) - model.semantic(b));
    idx.forEach((i, k) => {
      const n = idx.length;
      out[i * 3] = -1.2 + 2.4 * (+L) / maxL;
      out[i * 3 + 1] = 0.12 + (n > 1 ? (k / (n - 1) - 0.5) * Math.min(1.5, 0.11 * n) : 0);
      out[i * 3 + 2] = 0.12 * Math.sin(k * 1.7 + +L);
    });
  }
  background(model, out, members, 2.4);
  return out;
}

export function hypothesesEntities(model) {
  // each hypothesis is a peak (height = the model's estimate that it holds); what supports it
  // rings its base, what argues against it sits in the valleys beyond.
  const E = model.entities.length, out = new Float32Array(E * 3);
  const hyps = model.entities.map((e, i) => e.kind === "hypothesis" || e.kind === "debate" ? i : -1).filter(i => i >= 0);
  const members = new Set();
  const peaks = [];
  hyps.forEach((h, k) => {
    const ang = k / Math.max(1, hyps.length) * Math.PI * 2 + 0.4, R = hyps.length > 1 ? 0.62 : 0;
    const p = model.entities[h].meta.p_claim_holds;
    const cx = Math.cos(ang) * R, cz = Math.sin(ang) * R, top = -0.55 + 1.1 * (p == null ? 0.5 : p);
    out.set([cx, top, cz], h * 3); members.add(h);
    peaks.push({ x: cx, z: cz, h: top + 0.62, s: 0.22, i: h });
    const around = model.inn[h];
    let ka = 0, kc = 0, kx = 0;
    const nA = around.filter(l => l.rel === "supports").length || 1;
    const nC = around.filter(l => l.rel === "contradicts").length || 1;
    for (const l of around) {
      const j = l.ai; members.add(j);
      if (l.rel === "supports") {
        const a = ka++ / nA * Math.PI * 2, rr = 0.11 + 0.08 * (1 - model.entities[j].weight);
        out.set([cx + Math.cos(a) * rr, top - 0.16 - 0.1 * (1 - model.entities[j].weight), cz + Math.sin(a) * rr], j * 3);
      } else if (l.rel === "contradicts") {
        const a = ang + (kc++ / nC - 0.5) * 1.3, rr = 0.34;
        out.set([cx + Math.cos(a) * rr, -0.6 + (model.entities[j].kind === "alternative" ? 0.25 : 0), cz + Math.sin(a) * rr], j * 3);
      } else if (l.rel === "tests") {
        out.set([cx * 0.4, top + 0.18, cz * 0.4], j * 3);
      } else if (l.rel === "revises") {
        kx++;
      }
    }
  });
  background(model, out, members, 2.3);
  model._peaks = peaks;
  return out;
}

export function masteryEntities(model) {
  // the skill tree by path ("mathematics / linear algebra / eigenvalues"): domains inside,
  // skills outside; height is mastery (gaps sink).
  const E = model.entities.length, out = new Float32Array(E * 3);
  const skills = model.entities.map((e, i) => e.kind === "skill" ? i : -1).filter(i => i >= 0);
  const domains = [...new Set(skills.map(i => (model.entities[i].meta.path || [])[0] || "skills"))].sort();
  const members = new Set(skills);
  const tree = {};
  skills.forEach(i => {
    const path = model.entities[i].meta.path || [model.entities[i].label];
    const d = path[0] || "skills", mid = path.length > 2 ? path[1] : "";
    ((tree[d] = tree[d] || {})[mid] = tree[d][mid] || []).push(i);
  });
  const nodes = [];
  domains.forEach((d, di) => {
    const a0 = di / domains.length * Math.PI * 2, span = Math.PI * 2 / domains.length;
    const mids = Object.keys(tree[d]).sort();
    nodes.push({ label: d, x: Math.cos(a0 + span / 2) * 0.22, y: -0.5, z: Math.sin(a0 + span / 2) * 0.22, level: 0 });
    mids.forEach((m, mi) => {
      const am = a0 + span * (mi + 0.5) / mids.length;
      if (m) nodes.push({ label: m, x: Math.cos(am) * 0.55, y: -0.48, z: Math.sin(am) * 0.55, level: 1, parent: d });
      const leaf = tree[d][m];
      leaf.forEach((i, li) => {
        const al = a0 + span * (mi + (li + 0.5) / leaf.length) / mids.length;
        const p = model.entities[i].meta.p_mastery || 0;
        out.set([Math.cos(al) * 0.95, -0.45 + p * 1.05, Math.sin(al) * 0.95], i * 3);
      });
    });
  });
  model._treeNodes = nodes;
  background(model, out, members, 2.3);
  return out;
}

function background(model, out, members, R) {
  // entities a form is not about keep their particle: a quiet ring far behind, out of focus
  const rest = model.entities.map((e, i) => members.has(i) ? -1 : i).filter(i => i >= 0);
  rest.forEach((i, k) => {
    const a = k / Math.max(1, rest.length) * Math.PI * 2;
    out[i * 3] = Math.cos(a) * R; out[i * 3 + 1] = -0.9 + 0.05 * Math.sin(k); out[i * 3 + 2] = Math.sin(a) * R;
  });
  model._focus = members;
}

export const ENTITY_FORMS = {
  human: humanEntities, constellation: constellationEntities, network: networkEntities,
  timeline: timelineEntities, grid: gridEntities, execution: executionEntities,
  hypotheses: hypothesesEntities, mastery: masteryEntities,
};

// ------------------------------------------------------------------ substrate per form
// The dim particles: structure, not records. Each belongs to the entity nearest it in the body
// (`parent`), so when an entity activates, its part of the body lights.
export function substrateFor(form, model, ent, body, parent, seeds) {
  const S = parent.length, out = new Float32Array(S * 3), r = rng(17 + FORMS.indexOf(form));
  const E = model.entities.length;
  if (form === "human") { out.set(body.pos); return out; }
  if (form === "constellation") {
    for (let s = 0; s < S; s++) {
      const p = parent[s], u = seeds[s];
      if (u < 0.7 && p >= 0) { // nebula around its entity
        const spread = 0.035 + 0.06 * (model.entities[p].weight || 0.5);
        out[s * 3] = ent[p * 3] + gauss(r) * spread; out[s * 3 + 1] = ent[p * 3 + 1] + gauss(r) * spread * 0.7;
        out[s * 3 + 2] = ent[p * 3 + 2] + gauss(r) * spread;
      } else { // the galactic disc: logarithmic spiral arms
        const arm = Math.floor(r() * 3), t = Math.pow(r(), 0.6), ang = arm * 2.094 + t * 5.2 + gauss(r) * 0.25;
        const rad = 0.1 + t * 1.35;
        out[s * 3] = Math.cos(ang) * rad; out[s * 3 + 1] = -0.25 + gauss(r) * 0.03 * (1.2 - t); out[s * 3 + 2] = Math.sin(ang) * rad;
      }
    }
    return out;
  }
  if (form === "network" || form === "execution") {
    const links = form === "execution" ? model.links.filter(l => model._focus.has(l.ai) && model._focus.has(l.bi)) : model.links;
    for (let s = 0; s < S; s++) {
      const u = seeds[s];
      if (links.length && u < 0.8) { // filaments: particles strung along each relation
        const l = links[s % links.length], t = r();
        const a = l.ai * 3, b = l.bi * 3, bow = Math.sin(t * Math.PI) * 0.03;
        out[s * 3] = ent[a] + (ent[b] - ent[a]) * t + gauss(r) * 0.006;
        out[s * 3 + 1] = ent[a + 1] + (ent[b + 1] - ent[a + 1]) * t + bow + gauss(r) * 0.006;
        out[s * 3 + 2] = ent[a + 2] + (ent[b + 2] - ent[a + 2]) * t + gauss(r) * 0.006;
      } else { // a faint halo around each entity
        const p = parent[s] >= 0 ? parent[s] : Math.floor(r() * E);
        out[s * 3] = ent[p * 3] + gauss(r) * 0.025; out[s * 3 + 1] = ent[p * 3 + 1] + gauss(r) * 0.025;
        out[s * 3 + 2] = ent[p * 3 + 2] + gauss(r) * 0.025;
      }
    }
    return out;
  }
  if (form === "timeline") { // the helix itself, in strands, and a ring for each tenth of the span
    for (let s = 0; s < S; s++) {
      const u = r(), strand = Math.floor(r() * 5);
      if (seeds[s] < 0.85) {
        const ang = u * Math.PI * 2 * 2.25, rad = 0.52 + strand * 0.03 + gauss(r) * 0.004;
        out[s * 3] = Math.cos(ang) * rad; out[s * 3 + 1] = -0.85 + u * 1.85 + gauss(r) * 0.004; out[s * 3 + 2] = Math.sin(ang) * rad;
      } else {
        const ring = Math.floor(r() * 10) / 10, a = r() * Math.PI * 2;
        out[s * 3] = Math.cos(a) * 0.95; out[s * 3 + 1] = -0.85 + ring * 1.85; out[s * 3 + 2] = Math.sin(a) * 0.95;
      }
    }
    return out;
  }
  if (form === "grid") { // each band filled by the structure of its kind: dense where a kind is large
    const kinds = model.kindList, n = kinds.length, bandH = GRID.height / n;
    for (let s = 0; s < S; s++) {
      const p = parent[s], ri = p >= 0 ? kinds.indexOf(model.entities[p].kind) : Math.floor(r() * n);
      out[s * 3] = GRID.left - 0.04 + (GRID.width + 0.08) * r();
      out[s * 3 + 1] = GRID.top - (ri + 0.5) * bandH + (r() - 0.5) * bandH * 0.58;
      out[s * 3 + 2] = (r() - 0.5) * 0.03;
    }
    return out;
  }
  if (form === "hypotheses") { // the landscape: a height field with a peak per hypothesis
    const peaks = model._peaks || [], side = Math.ceil(Math.sqrt(S));
    for (let s = 0; s < S; s++) {
      const gx = (s % side) / side, gz = Math.floor(s / side) / side;
      const x = (gx - 0.5) * 2.6 + (r() - 0.5) * 0.01, z = (gz - 0.5) * 2.6 + (r() - 0.5) * 0.01;
      let y = -0.78;
      for (const p of peaks) { const d2 = (x - p.x) ** 2 + (z - p.z) ** 2; y += p.h * Math.exp(-d2 / (2 * p.s * p.s)); }
      y += 0.015 * Math.sin(x * 9) * Math.cos(z * 7);
      out[s * 3] = x; out[s * 3 + 1] = y - 0.05; out[s * 3 + 2] = z;
    }
    return out;
  }
  if (form === "mastery") { // branches from domain to area to skill, and a ground disc
    const nodes = model._treeNodes || [], skills = model.entities.map((e, i) => e.kind === "skill" ? i : -1).filter(i => i >= 0);
    const segs = [];
    for (const n of nodes) {
      if (n.level === 1) { const d = nodes.find(m => m.level === 0 && m.label === n.parent); if (d) segs.push([d.x, d.y, d.z, n.x, n.y, n.z]); }
      else segs.push([0, -0.62, 0, n.x, n.y, n.z]);
    }
    skills.forEach(i => {
      const path = model.entities[i].meta.path || [];
      const n = nodes.find(m => m.level === 1 && m.label === path[1] && m.parent === path[0]) ||
                nodes.find(m => m.level === 0 && m.label === path[0]);
      if (n) segs.push([n.x, n.y, n.z, ent[i * 3], ent[i * 3 + 1], ent[i * 3 + 2]]);
    });
    for (let s = 0; s < S; s++) {
      if (segs.length && seeds[s] < 0.7) {
        const g = segs[s % segs.length], t = r();
        out[s * 3] = g[0] + (g[3] - g[0]) * t + gauss(r) * 0.004; out[s * 3 + 1] = g[1] + (g[4] - g[1]) * t + gauss(r) * 0.004;
        out[s * 3 + 2] = g[2] + (g[5] - g[2]) * t + gauss(r) * 0.004;
      } else {
        const a = r() * Math.PI * 2, rad = Math.sqrt(r()) * 1.15;
        out[s * 3] = Math.cos(a) * rad; out[s * 3 + 1] = -0.64; out[s * 3 + 2] = Math.sin(a) * rad;
      }
    }
    return out;
  }
  out.set(body.pos);
  return out;
}

// Each body particle's nearest entity (in the human form), through a uniform grid.
export function assignParents(bodyPos, entPos, E) {
  const S = bodyPos.length / 3, parent = new Int32Array(S).fill(-1);
  if (!E) return parent;
  const cell = 0.08, grid = new Map(), key = (x, y, z) => `${x},${y},${z}`;
  for (let i = 0; i < E; i++) {
    const k = key(Math.floor(entPos[i * 3] / cell), Math.floor(entPos[i * 3 + 1] / cell), Math.floor(entPos[i * 3 + 2] / cell));
    (grid.get(k) || grid.set(k, []).get(k)).push(i);
  }
  for (let s = 0; s < S; s++) {
    const x = bodyPos[s * 3], y = bodyPos[s * 3 + 1], z = bodyPos[s * 3 + 2];
    const cx = Math.floor(x / cell), cy = Math.floor(y / cell), cz = Math.floor(z / cell);
    let best = -1, bd = 1e9;
    for (let ring = 1; ring <= 4 && best < 0; ring++) {
      for (let dx = -ring; dx <= ring; dx++) for (let dy = -ring; dy <= ring; dy++) for (let dz = -ring; dz <= ring; dz++) {
        const g = grid.get(key(cx + dx, cy + dy, cz + dz));
        if (!g) continue;
        for (const i of g) {
          const d = (entPos[i * 3] - x) ** 2 + (entPos[i * 3 + 1] - y) ** 2 + (entPos[i * 3 + 2] - z) ** 2;
          if (d < bd) { bd = d; best = i; }
        }
      }
    }
    parent[s] = best;
  }
  return parent;
}
