// MAYANK, hypothetically, with hands and a headset. Nothing here reads a camera or a headset:
// the hands are a kinematic model (21 joints each, the layout hand-tracking APIs use), driven by
// the mouse or by a scripted tour, and the headset view is the scene drawn once per eye.
// What the gestures DO is real: they call the same select, pull-out, spread, form and put-back
// actions as the mouse.

const FINGERS = [ // [knuckle x, y, z] in hand space (x right, y up, z toward the viewer), segment lengths
  { base: [-0.030, -0.018, -0.022], len: [0.036, 0.030, 0.026], dir: [-0.62, 0.12, -0.78] }, // thumb
  { base: [-0.026, 0.008, -0.088], len: [0.042, 0.026, 0.021] },                             // index
  { base: [-0.004, 0.010, -0.093], len: [0.046, 0.029, 0.022] },                             // middle
  { base: [0.017, 0.008, -0.089], len: [0.042, 0.027, 0.021] },                              // ring
  { base: [0.035, 0.002, -0.080], len: [0.033, 0.021, 0.019] },                              // pinky
];
export const POSES = {
  relaxed: { curl: [0.25, 0.2, 0.28, 0.34, 0.4], pinch: 0, spread: 0.5 },
  open: { curl: [0, 0, 0, 0, 0], pinch: 0, spread: 1 },
  point: { curl: [0.7, 0, 0.95, 1, 1], pinch: 0, spread: 0.2 },
  pinch: { curl: [0.2, 0.42, 0.5, 0.6, 0.65], pinch: 1, spread: 0.3 },
  fist: { curl: [0.8, 1, 1, 1, 1], pinch: 0, spread: 0 },
};
const lerp = (a, b, k) => a + (b - a) * k;
const lerp3 = (a, b, k) => [lerp(a[0], b[0], k), lerp(a[1], b[1], k), lerp(a[2], b[2], k)];
const norm = v => { const n = Math.hypot(...v) || 1; return v.map(x => x / n); };
export function blendPose(a, b, k) {
  return { curl: a.curl.map((c, i) => lerp(c, b.curl[i], k)), pinch: lerp(a.pinch, b.pinch, k), spread: lerp(a.spread, b.spread, k) };
}

// 21 joints in hand space: wrist, then 4 per finger (thumb first).
export function handJoints(pose, side = 1) {
  const J = [[0, 0, 0]];
  const tips = [];
  for (let f = 0; f < 5; f++) {
    const F = FINGERS[f], base = [F.base[0] * side, F.base[1], F.base[2]];
    const spread = (f - 2) * 0.13 * pose.spread * side;
    let d = F.dir ? norm([F.dir[0] * side, F.dir[1], F.dir[2]]) : norm([Math.sin(spread), 0.22, -Math.cos(spread)]);
    const pts = [base];
    let p = base, bend = 0;
    for (let k = 0; k < 3; k++) {
      bend += pose.curl[f] * [0.95, 1.15, 0.75][k] * (f === 0 ? 0.6 : 1);
      // curl folds the finger down toward the palm: rotate the direction about the hand's x axis
      const dd = [d[0], d[1] * Math.cos(-bend) - d[2] * Math.sin(-bend), d[1] * Math.sin(-bend) + d[2] * Math.cos(-bend)];
      p = [p[0] + dd[0] * F.len[k], p[1] + dd[1] * F.len[k], p[2] + dd[2] * F.len[k]];
      pts.push(p);
    }
    tips.push(pts);
    J.push(...pts);
  }
  if (pose.pinch > 0) { // thumb tip meets the index tip
    const t = tips[0], ix = tips[1][3], k = pose.pinch;
    const goal = [t[0], lerp3(t[0], ix, 0.42), lerp3(t[0], ix, 0.78), ix];
    for (let j = 1; j < 4; j++) J[j] = lerp3(t[j], [goal[j][0], goal[j][1] - 0.006 * (j === 2 ? 1 : 0), goal[j][2]], k);
  }
  return J;
}
export const BONES = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [0, 9], [9, 10], [10, 11], [11, 12],
  [0, 13], [13, 14], [14, 15], [15, 16], [0, 17], [17, 18], [18, 19], [19, 20], [5, 9], [9, 13], [13, 17]];

// hand space -> world: anchor in the viewer's frame (x right, y up, z back), rolled about forward
export function toWorld(J, B, anchor, roll = 0, scale = 1) {
  const cr = Math.cos(roll), sr = Math.sin(roll);
  const back = B.f.map(v => -v);
  const at = B.eye.map((e, i) => e + B.r[i] * anchor[0] + B.u[i] * anchor[1] + back[i] * anchor[2]);
  return J.map(([x, y, z]) => {
    const xr = (x * cr - y * sr) * scale, yr = (x * sr + y * cr) * scale, zr = z * scale;
    return at.map((a, i) => a + B.r[i] * xr + B.u[i] * yr + back[i] * zr);
  });
}

// ------------------------------------------------------------------ the room a headset would see
export function roomLines() {
  const L = [], c = (a, b, al) => L.push([a, b, al]), y0 = -0.985, y1 = 1.75;
  for (let x = -4; x <= 4.001; x += 0.25) c([x, y0, -3.2], [x, y0, 3.4], 0.05 * (1 - Math.abs(x) / 5));
  for (let z = -3.2; z <= 3.401; z += 0.25) c([-4, y0, z], [4, y0, z], 0.05 * (1 - Math.abs(z) / 5));
  for (const [a, b] of [[[-4, y0, -3.2], [-4, y1, -3.2]], [[4, y0, -3.2], [4, y1, -3.2]], [[-4, y1, -3.2], [4, y1, -3.2]],
    [[-4, y1, -3.2], [-4, y1, 3.4]], [[4, y1, -3.2], [4, y1, 3.4]], [[-4, y0, -3.2], [-4, y0, 3.4]], [[4, y0, -3.2], [4, y0, 3.4]]]) c(a, b, 0.14);
  for (let x = -4; x <= 4.001; x += 0.5) c([x, y0, -3.2], [x, y1, -3.2], 0.035);
  for (let y = y0; y <= y1; y += 0.5) c([-4, y, -3.2], [4, y, -3.2], 0.035);
  const box = (x0, x1, yb, yt, z0, z1, al) => {
    const P = [[x0, yb, z0], [x1, yb, z0], [x1, yb, z1], [x0, yb, z1], [x0, yt, z0], [x1, yt, z0], [x1, yt, z1], [x0, yt, z1]];
    for (const [i, j] of [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]]) c(P[i], P[j], al);
  };
  box(1.0, 1.4, -3.15, -0.45, 1.05, -2.6, 0.16);    // a window
  box(-3.6, -2.2, y0, -0.25, -2.9, -2.1, 0.12);      // a desk
  box(-3.4, -2.4, -0.25, 0.25, -3.05, -2.95, 0.12);  // a screen on it
  box(2.6, 3.4, y0, -0.45, 1.2, 2.2, 0.1);           // a chair-height box
  const from = new Float32Array(L.length * 6), color = new Float32Array(L.length * 8);
  L.forEach(([a, b, al], k) => { from.set([...a, ...b], k * 6); color.set([0.78, 0.72, 0.66, al, 0.78, 0.72, 0.66, al], k * 8); });
  return { from, color };
}

// ------------------------------------------------------------------ the scripted tour
// t in seconds -> right and left hand poses, anchors (viewer frame) and what happens.
export const TOUR = [
  { at: 0.0, cap: "POINT · aim at a record", right: "point" },
  { at: 2.6, cap: "PINCH · select it", right: "pinch", act: "select" },
  { at: 4.2, cap: "PINCH AND PULL · take it out of the field", right: "pinch", act: "pull" },
  { at: 6.6, cap: "OPEN YOUR HAND · it stays where you left it", right: "open", act: "release" },
  { at: 7.8, cap: "TWO HANDS APART · spread what it touches", right: "pinch", left: "pinch", act: "spread" },
  { at: 10.4, cap: "SWIPE · the next form", right: "open", act: "swipe" },
  { at: 12.6, cap: "TWIST · turn the field", right: "fist", act: "twist" },
  { at: 14.8, cap: "PALMS DOWN · put everything back", right: "open", left: "open", act: "reset" },
  { at: 17.0, cap: "", end: true },
];
