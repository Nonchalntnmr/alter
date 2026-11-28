// MAYANK with your real hands, from the computer's camera. Hand tracking runs in this browser
// (Google's MediaPipe HandLandmarker, WebAssembly); the video never leaves the machine and is never
// recorded. The tracker itself (~17 MB) is fetched once from jsdelivr and Google, only when you turn
// camera hands on. MediaPipe's 21 landmarks per hand are the same layout as MAYANK's hand model, so
// the gestures are the ones the tour shows, now driven by your hands.

const MP = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
const MODEL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";
const TIPS = [4, 8, 12, 16, 20], PIPS = [2, 6, 10, 14, 18];

const d2 = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const d3 = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], (a[2] - b[2]) * 0.5);

// What a hand is doing, from its landmarks alone (x, y in 0..1 of the mirrored image; z relative depth).
export function features(lm) {
  const size = d3(lm[0], lm[9]) || 1e-6;
  const ext = TIPS.map((t, f) => f === 0 ? d3(lm[4], lm[17]) > d3(lm[3], lm[17]) * 1.08 : d3(lm[0], lm[t]) > d3(lm[0], lm[PIPS[f]]) * 1.18);
  const fingers = ext.slice(1).filter(Boolean).length;
  return {
    size, pinch: d3(lm[4], lm[8]) / size,
    point: ext[1] && !ext[2] && !ext[3] && !ext[4],
    open: fingers === 4, fist: fingers === 0,
    roll: Math.atan2(lm[9][1] - lm[0][1], lm[9][0] - lm[0][0]),
    tip: lm[8], wrist: lm[0], mid: [(lm[4][0] + lm[8][0]) / 2, (lm[4][1] + lm[8][1]) / 2],
  };
}

export class CameraHands {
  constructor(hooks) {
    this.h = hooks; this.on = false; this.status = "off";
    this.hand = {}; this.grab = null; this.spread = null; this.cool = 0; this.calm = 0; this.lastT = 0;
    this.seen = 0; this.frames = 0; this.gestures = [];
  }

  async start() {
    this.status = "starting";
    this.video = document.createElement("video");
    this.video.playsInline = true; this.video.muted = true;
    this.stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, facingMode: "user" }, audio: false });
    this.video.srcObject = this.stream; await this.video.play();
    this.h.preview(this.video);
    this.status = "loading the hand tracker";
    const vision = await import(/* webpackIgnore: true */ `${MP}/vision_bundle.mjs`);
    const files = await vision.FilesetResolver.forVisionTasks(`${MP}/wasm`);
    this.tracker = await vision.HandLandmarker.createFromOptions(files, {
      baseOptions: { modelAssetPath: MODEL, delegate: "GPU" }, runningMode: "VIDEO", numHands: 2,
    });
    this.on = true; this.status = "watching for hands";
    const loop = () => {
      if (!this.on) return;
      if (this.video.readyState >= 2 && this.video.currentTime !== this._vt) {
        this._vt = this.video.currentTime;
        const r = this.tracker.detectForVideo(this.video, performance.now());
        this.process({ t: performance.now() / 1000, hands: (r.landmarks || []).map((lm, k) => ({
          label: ((r.handedness || r.handednesses || [])[k] || [{}])[0].categoryName || "Right",
          lm: lm.map(p => [1 - p.x, p.y, p.z]),     // mirrored, like a mirror: your right hand on the right
        })) });
      }
      this._raf = requestAnimationFrame(loop);
    };
    loop();
  }

  stop() {
    this.on = false; this.status = "off";
    cancelAnimationFrame(this._raf);
    if (this.stream) this.stream.getTracks().forEach(t => t.stop());
    if (this.tracker) this.tracker.close();
    this.stream = this.tracker = null; this.grab = this.spread = null;
    this.h.preview(null); this.h.render(null);
  }

  // One frame of tracked hands -> what MAYANK draws and does. Called by the camera loop, or by tests.
  process(frame) {
    const t = frame.t ?? performance.now() / 1000, dt = Math.max(1 / 120, Math.min(0.2, t - (this.lastT || t - 1 / 30)));
    this.lastT = t; this.frames++;
    const hands = frame.hands || [];
    if (hands.length) this.seen++;
    this.h.render(hands.length ? hands.map(x => x.lm) : null, hands.map(x => this.hand[x.label]?.pinched));
    if (!hands.length) { this.release(); this.h.caption("camera on · show a hand"); return; }
    const W = this.h.size();
    const by = {};
    for (const x of hands) {
      const f = features(x.lm), prev = this.hand[x.label] || {};
      const pinched = prev.pinched ? f.pinch < 0.42 : f.pinch < 0.28;          // hysteresis: no flicker at the edge
      const vx = prev.wrist ? (f.wrist[0] - prev.wrist[0]) / dt : 0;
      this.hand[x.label] = { ...f, pinched, vx: (prev.vx || 0) * 0.6 + vx * 0.4, rollPrev: prev.roll };
      by[x.label] = this.hand[x.label];
    }
    const p = by.Right || by.Left, other = by.Right && by.Left ? by.Left : null;
    const px = p.tip[0] * W[0], py = p.tip[1] * W[1];

    // two hands pinching: spread them apart to fan out what the selection touches, together to put back
    if (other && p.pinched && other.pinched) {
      const d = d2(p.mid, other.mid);
      if (!this.spread) this.spread = { d0: d, done: false };
      else if (!this.spread.done && d > this.spread.d0 * 1.35) { this.spread.done = true; this.fire("spread", () => this.h.expand()); }
      else if (!this.spread.done && d < this.spread.d0 * 0.7) { this.spread.done = true; this.fire("together", () => this.h.putBack()); }
      this.h.caption("two hands: apart to spread, together to put back");
      return;
    }
    this.spread = null;

    if (p.pinched) {
      if (!this.grab) {
        const i = this.h.hovered() >= 0 ? this.h.hovered() : this.h.pick(px, py);
        if (i >= 0) { this.grab = { i }; this.fire("pinch", () => this.h.select(i)); }
      } else this.h.pullTo(this.grab.i, p.mid[0] * W[0], p.mid[1] * W[1]);
      this.h.caption(this.grab ? "pinch held: move to pull it out, open to leave it" : "pinch: point at a record first");
      return;
    }
    if (this.grab) this.release();

    if (p.open && Math.abs(p.vx) > 1.6 && t > this.cool) {                   // swipe
      this.cool = t + 1.2;
      this.fire(p.vx < 0 ? "swipe left" : "swipe right", () => this.h.nextForm(p.vx < 0 ? 1 : -1));
    } else if (p.fist && p.rollPrev !== undefined) {                          // twist
      let dr = p.roll - p.rollPrev; if (dr > Math.PI) dr -= 2 * Math.PI; if (dr < -Math.PI) dr += 2 * Math.PI;
      if (Math.abs(dr) > 0.002) this.h.rotate(dr * 1.6);
      this.h.caption("fist: twist to turn the field");
    }
    if (other && p.open && other.open && Math.abs(p.vx) < 0.25 && Math.abs(other.vx) < 0.25) {   // both palms, held
      this.calm += dt;
      if (this.calm > 1.2 && t > this.cool) { this.cool = t + 2; this.calm = 0; this.fire("palms", () => { this.h.select(-1); this.h.putBack(); }); }
    } else this.calm = 0;

    if (!p.fist) {
      const i = this.h.pick(px, py);
      this.h.hover(i);
      if (!(p.open && Math.abs(p.vx) > 1.6)) this.h.caption(p.point ? "pointing: pinch to select" : "open hand: swipe to change form · both palms to put back");
    }
  }

  release() { if (this.grab) { this.grab = null; this.fire("release", () => {}); } }
  fire(name, fn) { this.gestures.push(name); if (this.gestures.length > 50) this.gestures.shift(); fn(); }
}
