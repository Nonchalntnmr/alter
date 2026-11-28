// MAYANK renderer: WebGL2, no dependencies. One draw for the dim structure (additive), one for the
// entities (depth-tested discs), one for relations (lines), one for the instrument floor.
// Positions morph on the GPU: every particle has a `from` and a `to` and its own delay, so a change
// of form flows instead of cutting. CPU code mirrors the easing exactly (morphAt) for picking.

const VS_POINTS = `#version 300 es
precision highp float;
layout(location=0) in vec3 aFrom;
layout(location=1) in vec3 aTo;
layout(location=2) in float aSeed;
layout(location=3) in float aSize;
layout(location=4) in vec3 aColor;
layout(location=5) in float aRef;      // entity index this particle shows or belongs to; -1 none
uniform mat4 uProj, uView;
uniform float uTime, uMix, uScale, uBreath, uEntities, uLight, uFlow, uSubFollow;
uniform sampler2D uState;              // per entity: r activation, g flags, b search, a visibility
uniform int uIsEntity;
out vec3 vColor; out float vAlpha; out float vRing; out float vCore;

float stagger(float m, float s) { float t = clamp(m * 1.7 - s * 0.7, 0.0, 1.0); return t * t * (3.0 - 2.0 * t); }
vec3 curl(vec3 p, float t) {
  return vec3(sin(p.y * 7.1 + t * 0.9) + sin(p.z * 5.3 - t * 0.6),
              sin(p.z * 6.7 + t * 0.7) + sin(p.x * 4.9 + t * 1.1),
              sin(p.x * 6.1 - t * 0.8) + sin(p.y * 5.7 + t * 0.5)) * 0.5;
}
void main() {
  float m = stagger(uMix, aSeed);
  vec3 p = mix(aFrom, aTo, m);
  float mid = sin(m * 3.14159);
  p += curl(p * 1.3 + aSeed * 9.0, uTime) * (0.11 * mid + 0.0035);           // flow in transit, drift at rest
  p.xz *= 1.0 + uBreath * 0.012 * sin(uTime * 1.25) * smoothstep(-0.2, 0.6, p.y); // breathing (body only)
  vec4 st = vec4(0.0, 0.0, 0.0, 1.0);
  if (aRef >= 0.0) {
    float w = 2048.0;
    st = texelFetch(uState, ivec2(int(mod(aRef, w)), int(floor(aRef / w))), 0);
  }
  float act = st.r, flags = st.g * 255.0, search = st.b, vis = st.a;
  if (uIsEntity == 0) vis = mix(1.0, vis, uSubFollow);   // structure of a form shows whatever it belongs to
  bool selected = mod(flags, 2.0) >= 1.0, hovered = mod(floor(flags / 2.0), 2.0) >= 1.0;
  bool trail = mod(floor(flags / 4.0), 2.0) >= 1.0, pending = mod(floor(flags / 8.0), 2.0) >= 1.0;
  vec4 mv = uView * vec4(p, 1.0);
  gl_Position = uProj * mv;
  float boost = 1.0 + act * 1.6 + search * 1.2 + (hovered ? 0.8 : 0.0) + (selected ? 1.4 : 0.0) + (trail ? 0.5 : 0.0);
  float size = aSize * uScale * boost / max(0.25, -mv.z);
  if (uIsEntity == 0) size *= mix(0.55, 1.0, vis);
  gl_PointSize = clamp(size, uIsEntity == 1 ? 3.2 : 1.0, 64.0);   // a record is never smaller than a readable dot
  vec3 c = aColor * (uLight * (0.7 + 0.3 * vis));
  c = mix(c, vec3(1.0, 0.93, 0.84), clamp((act * 0.75 + search * 0.55) * (uIsEntity == 1 ? 1.0 : 0.6), 0.0, 1.0));
  if (pending) c = mix(c, vec3(0.50, 0.82, 0.86), 0.55 + 0.25 * sin(uTime * 4.0));
  float flowPulse = 0.65 + 0.35 * sin(uTime * 2.6 - aSeed * 40.0);
  float base = uIsEntity == 1 ? 1.0 : mix(1.0, flowPulse, uFlow);
  vAlpha = base * mix(0.04, 1.0, vis) * (uIsEntity == 1 ? 1.0 : 0.62 + act * 0.32 + search * 0.3);   // a lit region glows, it does not bloom
  vColor = c; vRing = selected ? 1.0 : (hovered ? 0.6 : 0.0); vCore = uIsEntity == 1 ? 1.0 : 0.0;
}`;

const FS_POINTS = `#version 300 es
precision highp float;
in vec3 vColor; in float vAlpha; in float vRing; in float vCore;
out vec4 o;
void main() {
  vec2 q = gl_PointCoord * 2.0 - 1.0;
  float r = length(q);
  if (r > 1.0) discard;
  if (vCore > 0.5) {
    float disc = smoothstep(1.0, 0.82, r) * (0.55 + 0.45 * smoothstep(0.9, 0.0, r));
    float ring = vRing > 0.0 ? smoothstep(0.08, 0.0, abs(r - 0.86)) * vRing : 0.0;
    float hole = vRing > 0.9 ? smoothstep(0.52, 0.62, r) : 1.0;     // a selected entry is a ring, as in a record you hold
    o = vec4(vColor * (disc * hole + ring * 1.4), max(disc * hole, ring) * vAlpha);
  } else {
    float glow = exp(-r * r * 3.2);
    o = vec4(vColor * glow * vAlpha, glow * vAlpha);
  }
}`;

const VS_LINES = `#version 300 es
precision highp float;
layout(location=0) in vec3 aFrom;
layout(location=1) in vec3 aTo;
layout(location=2) in vec4 aColor;
uniform mat4 uProj, uView; uniform float uMix;
out vec4 vColor;
void main() {
  float t = clamp(uMix * 1.7 - 0.35, 0.0, 1.0); t = t * t * (3.0 - 2.0 * t);
  gl_Position = uProj * uView * vec4(mix(aFrom, aTo, t), 1.0);
  vColor = aColor;
}`;
const FS_LINES = `#version 300 es
precision highp float; in vec4 vColor; out vec4 o; void main() { o = vec4(vColor.rgb * vColor.a, vColor.a); }`;

const VS_BG = `#version 300 es
layout(location=0) in vec2 aPos; out vec2 vUv;
void main() { vUv = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.999, 1.0); }`;
const FS_BG = `#version 300 es
precision highp float; in vec2 vUv; uniform float uTime; uniform vec2 uRes; out vec4 o;
float h(vec2 p) { return fract(sin(dot(p, vec2(41.3, 289.1))) * 43758.5453); }
void main() {
  vec2 p = (vUv - vec2(0.5, 0.56)) * vec2(uRes.x / uRes.y, 1.0);
  float d = length(p);
  vec3 c = vec3(0.020, 0.016, 0.014) + vec3(0.075, 0.045, 0.030) * exp(-d * d * 3.5);
  c += vec3(0.012, 0.014, 0.020) * smoothstep(0.2, 1.2, vUv.y);
  c += (h(gl_FragCoord.xy + fract(uTime)) - 0.5) * 0.012;             // film grain, against banding
  o = vec4(c, 1.0);
}`;

export function morphAt(mixv, seed) { // must match stagger() in VS_POINTS
  let t = Math.max(0, Math.min(1, mixv * 1.7 - seed * 0.7));
  return t * t * (3 - 2 * t);
}

function compile(gl, vs, fs) {
  const p = gl.createProgram();
  for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    gl.attachShader(p, s);
  }
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  const u = {};
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) { const name = gl.getActiveUniform(p, i).name; u[name] = gl.getUniformLocation(p, name); }
  return { p, u };
}

// ---------------------------------------------------------------- matrices
export function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
  return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0]);
}
export function lookAt(eye, target, up = [0, 1, 0]) {
  let zx = eye[0] - target[0], zy = eye[1] - target[1], zz = eye[2] - target[2];
  let l = Math.hypot(zx, zy, zz); zx /= l; zy /= l; zz /= l;
  let xx = up[1] * zz - up[2] * zy, xy = up[2] * zx - up[0] * zz, xz = up[0] * zy - up[1] * zx;
  l = Math.hypot(xx, xy, xz); xx /= l; xy /= l; xz /= l;
  const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
  return new Float32Array([xx, yx, zx, 0, xy, yy, zy, 0, xz, yz, zz, 0,
    -(xx * eye[0] + xy * eye[1] + xz * eye[2]), -(yx * eye[0] + yy * eye[1] + yz * eye[2]), -(zx * eye[0] + zy * eye[1] + zz * eye[2]), 1]);
}
export function project(m, x, y, z) { // m = proj*view (column-major)
  const cx = m[0] * x + m[4] * y + m[8] * z + m[12], cy = m[1] * x + m[5] * y + m[9] * z + m[13];
  const cw = m[3] * x + m[7] * y + m[11] * z + m[15];
  return [cx / cw, cy / cw, cw];
}
export function mul(a, b) {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
  }
  return o;
}

// ---------------------------------------------------------------- camera
export class Camera {
  constructor() {
    this.theta = 0.0; this.phi = 1.42; this.radius = 3.3; this.target = [0, 0.05, 0];
    this.vt = 0; this.vp = 0; this.fly = null; this.auto = true; this.idleSince = performance.now();
  }
  frame(theta, phi, radius, target, ms = 1400) {
    this.fly = { t0: performance.now(), ms, from: [this.theta, this.phi, this.radius, ...this.target],
                 to: [theta ?? this.theta, phi ?? this.phi, radius ?? this.radius, ...(target ?? this.target)] };
  }
  update(dt) {
    const now = performance.now();
    if (this.fly) {
      const k = Math.min(1, (now - this.fly.t0) / this.fly.ms), e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      const v = this.fly.from.map((a, i) => a + (this.fly.to[i] - a) * e);
      [this.theta, this.phi, this.radius] = v; this.target = v.slice(3);
      if (k >= 1) this.fly = null;
    }
    this.theta += this.vt; this.phi += this.vp; this.vt *= 0.9; this.vp *= 0.9;
    if (this.auto && now - this.idleSince > 6000 && !this.fly) this.theta += dt * 0.045;
    this.phi = Math.max(0.15, Math.min(Math.PI - 0.15, this.phi));
    this.radius = Math.max(0.35, Math.min(9, this.radius));
  }
  eye() {
    const s = Math.sin(this.phi);
    return [this.target[0] + this.radius * s * Math.sin(this.theta), this.target[1] + this.radius * Math.cos(this.phi),
            this.target[2] + this.radius * s * Math.cos(this.theta)];
  }
  touch() { this.idleSince = performance.now(); this.fly = null; }
}

// ---------------------------------------------------------------- renderer
export class Renderer {
  constructor(canvas) {
    const gl = canvas.getContext("webgl2", { antialias: true, alpha: false, powerPreference: "high-performance" });
    if (!gl) throw new Error("WebGL2 is not available in this browser");
    this.gl = gl; this.canvas = canvas;
    this.pts = compile(gl, VS_POINTS, FS_POINTS);
    this.lin = compile(gl, VS_LINES, FS_LINES);
    this.bg = compile(gl, VS_BG, FS_BG);
    this.bgVao = gl.createVertexArray(); gl.bindVertexArray(this.bgVao);
    const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    this.state = gl.createTexture();
    this.layers = {}; this.lines = null; this.floor = null;
    this.mix = 1; this.morphStart = 0; this.morphMs = 2400;
    this.camera = new Camera(); this.scale = 1; this.light = 1; this.breath = 1; this.flow = 0; this.subFollow = 1;
    this.frames = []; this.drawMs = 0;
  }

  setStateTexture(data, w, h) {
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.state);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  }

  // layer: {from, to, seed, size, color, ref} typed arrays; count
  setLayer(name, a) {
    const gl = this.gl;
    const L = this.layers[name] || (this.layers[name] = { vao: gl.createVertexArray(), bufs: {} });
    gl.bindVertexArray(L.vao);
    const put = (key, loc, data, comps) => {
      const buf = L.bufs[key] || (L.bufs[key] = gl.createBuffer());
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
      gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, comps, gl.FLOAT, false, 0, 0);
    };
    put("from", 0, a.from, 3); put("to", 1, a.to, 3); put("seed", 2, a.seed, 1); put("size", 3, a.size, 1);
    put("color", 4, a.color, 3); put("ref", 5, a.ref, 1);
    L.count = a.seed.length; L.cpu = a;
  }
  updateTargets(name, from, to) {
    const gl = this.gl, L = this.layers[name];
    gl.bindBuffer(gl.ARRAY_BUFFER, L.bufs.from); gl.bufferSubData(gl.ARRAY_BUFFER, 0, from);
    gl.bindBuffer(gl.ARRAY_BUFFER, L.bufs.to); gl.bufferSubData(gl.ARRAY_BUFFER, 0, to);
    L.cpu.from = from; L.cpu.to = to;
  }
  updateColors(name, color) {
    const gl = this.gl, L = this.layers[name];
    gl.bindBuffer(gl.ARRAY_BUFFER, L.bufs.color); gl.bufferSubData(gl.ARRAY_BUFFER, 0, color);
    L.cpu.color = color;
  }
  setLines(from, to, color) {
    const gl = this.gl;
    const L = this.lines || (this.lines = { vao: gl.createVertexArray(), bufs: [gl.createBuffer(), gl.createBuffer(), gl.createBuffer()] });
    gl.bindVertexArray(L.vao);
    [[from, 3], [to, 3], [color, 4]].forEach(([d, c], i) => {
      gl.bindBuffer(gl.ARRAY_BUFFER, L.bufs[i]); gl.bufferData(gl.ARRAY_BUFFER, d, gl.DYNAMIC_DRAW);
      gl.enableVertexAttribArray(i); gl.vertexAttribPointer(i, c, gl.FLOAT, false, 0, 0);
    });
    L.count = from.length / 3;
  }
  setFloor(from, to, color) { // same program as lines, its own buffers
    const gl = this.gl;
    const L = this.floor || (this.floor = { vao: gl.createVertexArray(), bufs: [gl.createBuffer(), gl.createBuffer(), gl.createBuffer()] });
    gl.bindVertexArray(L.vao);
    [[from, 3], [to, 3], [color, 4]].forEach(([d, c], i) => {
      gl.bindBuffer(gl.ARRAY_BUFFER, L.bufs[i]); gl.bufferData(gl.ARRAY_BUFFER, d, gl.DYNAMIC_DRAW);
      gl.enableVertexAttribArray(i); gl.vertexAttribPointer(i, c, gl.FLOAT, false, 0, 0);
    });
    L.count = from.length / 3;
  }

  startMorph(ms = 2400) { this.mix = 0; this.morphStart = performance.now(); this.morphMs = ms; }
  get morphing() { return this.mix < 1; }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2), c = this.canvas;
    const w = Math.floor(c.clientWidth * dpr), h = Math.floor(c.clientHeight * dpr);
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    this.dpr = dpr;
  }

  matrices() {
    const c = this.canvas, aspect = c.width / Math.max(1, c.height);
    this.proj = perspective(0.62, aspect, 0.02, 60);
    this.view = lookAt(this.camera.eye(), this.camera.target);
    this.pv = mul(this.proj, this.view);
    return this.pv;
  }

  draw(t, dt) {
    const gl = this.gl, t0 = performance.now();
    this.resize();
    this.camera.update(dt);
    if (this.mix < 1) this.mix = Math.min(1, (performance.now() - this.morphStart) / this.morphMs);
    this.matrices();
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.disable(gl.DEPTH_TEST); gl.depthMask(false); gl.disable(gl.BLEND);
    gl.useProgram(this.bg.p);
    gl.uniform1f(this.bg.u.uTime, t); gl.uniform2f(this.bg.u.uRes, this.canvas.width, this.canvas.height);
    gl.bindVertexArray(this.bgVao); gl.drawArrays(gl.TRIANGLES, 0, 3);

    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE); // additive: structure and relations glow
    gl.useProgram(this.lin.p);
    gl.uniformMatrix4fv(this.lin.u.uProj, false, this.proj); gl.uniformMatrix4fv(this.lin.u.uView, false, this.view);
    if (this.floor) { gl.uniform1f(this.lin.u.uMix, 1); gl.bindVertexArray(this.floor.vao); gl.drawArrays(gl.LINES, 0, this.floor.count); }
    if (this.lines && this.lines.count) { gl.uniform1f(this.lin.u.uMix, this.mix); gl.bindVertexArray(this.lines.vao); gl.drawArrays(gl.LINES, 0, this.lines.count); }

    gl.useProgram(this.pts.p);
    const u = this.pts.u;
    gl.uniformMatrix4fv(u.uProj, false, this.proj); gl.uniformMatrix4fv(u.uView, false, this.view);
    gl.uniform1f(u.uTime, t); gl.uniform1f(u.uMix, this.mix); gl.uniform1f(u.uBreath, this.breath);
    gl.uniform1f(u.uLight, this.light); gl.uniform1f(u.uFlow, this.flow); gl.uniform1f(u.uSubFollow, this.subFollow);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.state); gl.uniform1i(u.uState, 0);
    const pxScale = this.canvas.height / 900;
    if (this.layers.substrate) {
      gl.uniform1i(u.uIsEntity, 0); gl.uniform1f(u.uScale, 8.5 * this.scale * pxScale);
      gl.bindVertexArray(this.layers.substrate.vao); gl.drawArrays(gl.POINTS, 0, this.layers.substrate.count);
    }
    if (this.layers.entities) {
      gl.enable(gl.DEPTH_TEST); gl.depthMask(true); gl.clear(gl.DEPTH_BUFFER_BIT);
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.uniform1i(u.uIsEntity, 1); gl.uniform1f(u.uScale, 12.0 * this.scale * pxScale);
      gl.bindVertexArray(this.layers.entities.vao); gl.drawArrays(gl.POINTS, 0, this.layers.entities.count);
    }
    this.drawMs = this.drawMs * 0.9 + (performance.now() - t0) * 0.1;
  }

  // where entity i is right now (without the small drift), for picking and labels
  entityPos(i) {
    const L = this.layers.entities; if (!L) return [0, 0, 0];
    const m = morphAt(this.mix, L.cpu.seed[i]), f = L.cpu.from, to = L.cpu.to;
    return [f[i * 3] + (to[i * 3] - f[i * 3]) * m, f[i * 3 + 1] + (to[i * 3 + 1] - f[i * 3 + 1]) * m, f[i * 3 + 2] + (to[i * 3 + 2] - f[i * 3 + 2]) * m];
  }
  currentPositions(name) {
    const L = this.layers[name], n = L.count, out = new Float32Array(n * 3), f = L.cpu.from, to = L.cpu.to, s = L.cpu.seed;
    for (let i = 0; i < n; i++) {
      const m = morphAt(this.mix, s[i]);
      out[i * 3] = f[i * 3] + (to[i * 3] - f[i * 3]) * m; out[i * 3 + 1] = f[i * 3 + 1] + (to[i * 3 + 1] - f[i * 3 + 1]) * m;
      out[i * 3 + 2] = f[i * 3 + 2] + (to[i * 3 + 2] - f[i * 3 + 2]) * m;
    }
    return out;
  }
  toScreen(p) {
    const [x, y, w] = project(this.pv, p[0], p[1], p[2]);
    const c = this.canvas;
    return [(x * 0.5 + 0.5) * c.clientWidth, (1 - (y * 0.5 + 0.5)) * c.clientHeight, w];
  }
  pick(mx, my, radius = 16, filter = null) {
    const L = this.layers.entities; if (!L) return -1;
    let best = -1, bd = radius * radius;
    for (let i = 0; i < L.count; i++) {
      if (filter && !filter(i)) continue;
      const [sx, sy, w] = this.toScreen(this.entityPos(i));
      if (w <= 0) continue;
      const d = (sx - mx) ** 2 + (sy - my) ** 2;
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }
}
