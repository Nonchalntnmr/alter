// MAYANK assessments: questions MAYANK writes and checks itself, with no model. Each has one right
// answer that MAYANK computes, and the working is shown only after you answer.
// Difficulty 0..2 follows mastery: an unassisted success raises it, a miss lowers it.
// A skill with no generator here falls back to an honest self-check.

const ri = (r, a, b) => a + Math.floor(r() * (b - a + 1));
const pick = (r, xs) => xs[Math.floor(r() * xs.length)];
const nz = (r, a, b) => { let v = 0; while (!v) v = ri(r, a, b); return v; };
const fmt = x => Number.isInteger(x) ? String(x) : String(+x.toFixed(4));
const mat = m => "[" + m.map(row => "[" + row.join(", ") + "]").join(", ") + "]";
const gcd = (a, b) => b ? gcd(b, a % b) : Math.abs(a);
const num = (answer, tol = 1e-6) => ({ kind: "number", answer, tol });
const yesno = yes => ({ kind: "yesno", answer: yes ? "yes" : "no" });

const GEN = [
  ["eigenvalue", (d, r) => {
    if (d === 0) {
      const a = nz(r, -6, 9), b = nz(r, -6, 9);
      return { q: `What is the largest eigenvalue of the diagonal matrix ${mat([[a, 0], [0, b]])}?`, ...num(Math.max(a, b)),
               why: `A diagonal matrix's eigenvalues are its diagonal entries, ${a} and ${b}. The largest is ${Math.max(a, b)}.` };
    }
    if (d === 1) {
      const a = ri(r, -4, 8), b = nz(r, -5, 5);
      return { q: `What is the largest eigenvalue of ${mat([[a, b], [b, a]])}?`, ...num(a + Math.abs(b)),
               why: `trace = ${2 * a}, det = ${a * a - b * b}. λ = (trace ± √(trace² − 4·det))/2 = ${a} ± ${Math.abs(b)}, so the largest is ${a + Math.abs(b)}.` };
    }
    const k = ri(r, 1, 2), lo = ri(r, -4, 4), hi = lo + 5 * k, S = lo + hi;
    const a = (S + 3 * k) / 2, dd = (S - 3 * k) / 2, b = 2 * k;
    return { q: `What is the largest eigenvalue of the symmetric matrix ${mat([[a, b], [b, dd]])}?`, ...num(hi),
             why: `trace = ${S}, det = ${a * dd - b * b}. λ = (${S} ± √(${S * S} − ${4 * (a * dd - b * b)}))/2 = (${S} ± ${5 * k})/2, which gives ${hi} and ${lo}.` };
  }],
  ["determinant", (d, r) => {
    if (d < 2) {
      const R = d ? 9 : 5, m = [[ri(r, -R, R), ri(r, -R, R)], [ri(r, -R, R), ri(r, -R, R)]];
      const v = m[0][0] * m[1][1] - m[0][1] * m[1][0];
      return { q: `det ${mat(m)} = ?`, ...num(v), why: `ad − bc = ${m[0][0]}·${m[1][1]} − ${m[0][1]}·${m[1][0]} = ${v}.` };
    }
    const m = [0, 1, 2].map(() => [0, 1, 2].map(() => ri(r, -3, 4)));
    const c = [m[1][1] * m[2][2] - m[1][2] * m[2][1], m[1][0] * m[2][2] - m[1][2] * m[2][0], m[1][0] * m[2][1] - m[1][1] * m[2][0]];
    const v = m[0][0] * c[0] - m[0][1] * c[1] + m[0][2] * c[2];
    return { q: `det ${mat(m)} = ?`, ...num(v),
             why: `Expand along the first row: ${m[0][0]}·(${c[0]}) − ${m[0][1]}·(${c[1]}) + ${m[0][2]}·(${c[2]}) = ${v}.` };
  }],
  ["matrix multiplication", (d, r) => {
    const n = d === 2 ? 3 : 2, R = d ? 6 : 4;
    const A = [...Array(n)].map(() => [...Array(n)].map(() => ri(r, -R, R)));
    const B = [...Array(n)].map(() => [...Array(n)].map(() => ri(r, -R, R)));
    const i = ri(r, 0, n - 1), j = ri(r, 0, n - 1);
    const terms = A[i].map((x, k) => [x, B[k][j]]), v = terms.reduce((s, [x, y]) => s + x * y, 0);
    return { q: `A = ${mat(A)}, B = ${mat(B)}. What is entry (${i + 1}, ${j + 1}) of AB?`, ...num(v),
             why: `Row ${i + 1} of A times column ${j + 1} of B: ${terms.map(([x, y]) => `${x}·${y}`).join(" + ")} = ${v}.` };
  }],
  ["vector", (d, r) => {
    const n = d === 0 ? 2 : 3, u = [...Array(n)].map(() => nz(r, -6, 6)), v = [...Array(n)].map(() => ri(r, -6, 6));
    if (d === 2) {
      const k = nz(r, -3, 3), t = u.map((x, i) => x * k + (r() < 0.5 && i === 0 ? 1 : 0));
      const cross = [u[1] * t[2] - u[2] * t[1], u[2] * t[0] - u[0] * t[2], u[0] * t[1] - u[1] * t[0]];
      const parallel = cross.every(c => c === 0);   // the answer is computed, never assumed
      return { q: `Is (${t.join(", ")}) in the span of (${u.join(", ")})? (yes or no)`, ...yesno(parallel),
               why: parallel ? `(${t.join(", ")}) = ${k}·(${u.join(", ")}), so it is.`
                             : `u × t = (${cross.join(", ")}) is not zero, so t is not a multiple of u.` };
    }
    const dot = u.reduce((s, x, i) => s + x * v[i], 0);
    return { q: `(${u.join(", ")}) · (${v.join(", ")}) = ?`, ...num(dot), why: `${u.map((x, i) => `${x}·${v[i]}`).join(" + ")} = ${dot}.` };
  }],
  ["singular value", (d, r) => {
    if (d < 2) {
      const a = nz(r, -9, 9), b = nz(r, -9, 9);
      return { q: `What is the largest singular value of ${mat([[a, 0], [0, b]])}?`, ...num(Math.max(Math.abs(a), Math.abs(b))),
               why: `AᵀA = diag(${a * a}, ${b * b}). Singular values are the square roots: ${Math.abs(a)} and ${Math.abs(b)}.` };
    }
    const k = ri(r, 1, 3), [p, q] = pick(r, [[3, 4], [5, 12], [8, 15]]);
    return { q: `What is the largest singular value of ${mat([[p * k, 0], [q * k, 0]])}?`, ...num(k * Math.hypot(p, q)),
             why: `The matrix has rank one: its only nonzero singular value is the length of its first column, √(${p * k}² + ${q * k}²) = ${k * Math.hypot(p, q)}.` };
  }],
  ["principal component", (d, r) => {
    const ev = pick(r, [[6, 3, 1], [5, 3, 2], [7, 2, 1], [4, 4, 2], [8, 1, 1]]), tot = ev.reduce((a, b) => a + b, 0);
    const n = d === 2 ? 2 : 1, part = ev.slice(0, n).reduce((a, b) => a + b, 0);
    return { q: `A covariance matrix has eigenvalues ${ev.join(", ")}. What fraction of the variance do the first ${n === 1 ? "principal component" : "two principal components"} explain? (a decimal)`,
             ...num(part / tot, 0.006), why: `${ev.slice(0, n).join(" + ")} out of a total of ${tot} = ${fmt(part / tot)}.` };
  }],
  ["bayes", (d, r) => {
    const p = pick(r, d ? [0.01, 0.02, 0.005] : [0.1, 0.2]), s = pick(r, [0.9, 0.95, 0.99]), c = pick(r, d === 2 ? [0.95, 0.99] : [0.9, 0.95]);
    const post = s * p / (s * p + (1 - c) * (1 - p));
    return { q: `A condition affects ${p * 100}% of people. A test catches ${s * 100}% of cases and gives a false positive ${fmt((1 - c) * 100)}% of the time. Someone tests positive: what is the probability they have it? (a decimal, 2 places)`,
             ...num(post, 0.006), why: `P(D|+) = P(+|D)P(D) / P(+) = ${s}·${p} / (${s}·${p} + ${fmt(1 - c)}·${fmt(1 - p)}) = ${fmt(post)}.` };
  }],
  ["conditional expectation", (d, r) => {
    if (d < 2) {
      const k = ri(r, 1, 4), vals = [...Array(6 - k)].map((_, i) => k + 1 + i), e = vals.reduce((a, b) => a + b, 0) / vals.length;
      return { q: `A fair die is rolled. What is E[X | X > ${k}]?`, ...num(e, 0.006), why: `Given X > ${k}, X is uniform on {${vals.join(", ")}}, so the mean is ${fmt(e)}.` };
    }
    return { q: "Two fair dice are rolled. What is E[sum | the first die shows 6]?", ...num(9.5, 0.006), why: "6 + E[second die] = 6 + 3.5 = 9.5." };
  }],
  ["martingale", (d, r) => {
    if (d < 2) {
      const n = ri(r, 5, 50), t = ri(r, 3, 30);
      return { q: `You start with $${n} and bet $1 on each of ${t} fair coin flips. What is your expected wealth at the end?`, ...num(n),
               why: `Each fair bet has expected gain 0, so wealth is a martingale: E = $${n}.` };
    }
    const k = ri(r, 1, 8), N = k + ri(r, 2, 10);
    return { q: `In a fair game you win or lose $1 each round, starting with $${k}. What is the probability you reach $${N} before $0? (a decimal)`,
             ...num(k / N, 0.006), why: `For a fair walk, P(reach ${N} first) = start / target = ${k}/${N} = ${fmt(k / N)}.` };
  }],
  ["gradient", (d, r) => {
    const a = d ? nz(r, -3, 3) : 0, b = nz(r, -5, 5), c = ri(r, -6, 6), x = ri(r, -3, 3);
    const f = `${a ? `${a}x³ + ` : ""}${b}x² + ${c}x`, v = 3 * a * x * x + 2 * b * x + c;
    return { q: `f(x) = ${f}. What is f′(${x})?`, ...num(v), why: `f′(x) = ${a ? `${3 * a}x² + ` : ""}${2 * b}x + ${c}; at x = ${x} that is ${v}.` };
  }],
  ["convex", (d, r) => {
    if (d === 0) {
      const a = nz(r, -4, 4), b = ri(r, -5, 5);
      return { q: `Is f(x) = ${a}x² + ${b}x convex on all of ℝ? (yes or no)`, ...yesno(a > 0), why: `f″(x) = ${2 * a}, which is ${a > 0 ? "positive" : "negative"}: ${a > 0 ? "convex" : "not convex"}.` };
    }
    const a = nz(r, -3, 3);
    return { q: `Is f(x) = ${a}x³ + x² convex on all of ℝ? (yes or no)`, ...yesno(false),
             why: `f″(x) = ${6 * a}x + 2 changes sign at x = ${fmt(-2 / (6 * a))}, so f is not convex everywhere.` };
  }],
  ["fourier", (d, r) => {
    if (d < 2) {
      const k = ri(r, 1, 6);
      return { q: `What is the fundamental period of sin(${k}x), in multiples of π? (a number)`, ...num(2 / k, 0.006), why: `Period = 2π/${k} = ${fmt(2 / k)}π.` };
    }
    const k = ri(r, 2, 6), m = k * ri(r, 2, 3) + (r() < 0.5 ? 0 : 1), g = gcd(k, m);
    return { q: `What is the fundamental period of sin(${k}x) + sin(${m}x), in multiples of π?`, ...num(2 / g, 0.006),
             why: `The periods are 2π/${k} and 2π/${m}; their least common multiple is 2π/gcd(${k}, ${m}) = 2π/${g} = ${fmt(2 / g)}π.` };
  }],
  ["entropy", (d, r, name) => {
    const Q = ri(r, 2, 12) * 100, T = pick(r, [200, 250, 300, 400]);
    if (d === 0 || name.includes("thermo")) return { q: `${Q} J of heat flows reversibly into a reservoir at ${T} K. What is its entropy change, in J/K?`, ...num(Q / T, 0.006), why: `ΔS = Q/T = ${Q}/${T} = ${fmt(Q / T)} J/K.` };
    const dist = pick(r, [[0.5, 0.25, 0.25], [0.5, 0.25, 0.125, 0.125], [0.25, 0.25, 0.25, 0.25]]);
    const H = -dist.reduce((s, p) => s + p * Math.log2(p), 0);
    return { q: `What is the entropy, in bits, of a distribution with probabilities ${dist.join(", ")}?`, ...num(H, 0.006),
             why: `H = −Σ p log₂ p = ${dist.map(p => `${p}·${-Math.log2(p)}`).join(" + ")} = ${fmt(H)} bits.` };
  }],
  ["heap", (d, r) => {
    const i = ri(r, 1, 60);
    if (d === 0) return { q: `In a 0-indexed binary heap stored in an array, what is the index of the parent of index ${i}?`, ...num(Math.floor((i - 1) / 2)), why: `parent(i) = ⌊(i − 1)/2⌋ = ${Math.floor((i - 1) / 2)}.` };
    if (d === 1) return { q: `In a 0-indexed binary heap, what is the index of the right child of index ${i}?`, ...num(2 * i + 2), why: `right(i) = 2i + 2 = ${2 * i + 2}.` };
    const n = ri(r, 2, 500);
    return { q: `What is the height (edges from root to the deepest leaf) of a binary heap with ${n} elements?`, ...num(Math.floor(Math.log2(n))), why: `A complete binary tree with n nodes has height ⌊log₂ n⌋ = ${Math.floor(Math.log2(n))}.` };
  }],
  ["dynamic programming", (d, r) => {
    const n = ri(r, 3, d ? 12 : 7), steps = d === 2 ? [1, 2, 3] : [1, 2], w = [1];
    for (let k = 1; k <= n; k++) w[k] = steps.reduce((s, st) => s + (k - st >= 0 ? w[k - st] : 0), 0);
    return { q: `How many ways are there to climb ${n} stairs taking ${steps.join(", ").replace(/, (\d)$/, " or $1")} steps at a time?`, ...num(w[n]),
             why: `ways(k) = ${steps.map(s => `ways(k−${s})`).join(" + ")}, with ways(0) = 1: ${w.join(", ")}. So ${w[n]}.` };
  }],
  ["hypothesis test", (d, r) => {
    const n = pick(r, [16, 25, 36, 100]), sigma = pick(r, [8, 10, 12, 20]), se = sigma / Math.sqrt(n), mu = ri(r, 40, 60), x = mu + ri(r, -6, 6);
    return { q: `σ = ${sigma}, n = ${n}, sample mean ${x}, null mean ${mu}. What is the z statistic? (2 decimals)`, ...num((x - mu) / se, 0.006),
             why: `z = (x̄ − μ₀)/(σ/√n) = (${x} − ${mu})/(${sigma}/${Math.sqrt(n)}) = ${fmt((x - mu) / se)}.` };
  }],
  ["difference in differences", (d, r) => {
    const t0 = ri(r, 20, 30), t1 = t0 + ri(r, -6, 2), c0 = ri(r, 20, 30), c1 = c0 + ri(r, -2, 3);
    return { q: `Treated blocks: ${t0} °C before, ${t1} °C after. Comparison blocks: ${c0} °C before, ${c1} °C after. What is the difference-in-differences estimate of the effect, in °C?`,
             ...num((t1 - t0) - (c1 - c0)), why: `(${t1} − ${t0}) − (${c1} − ${c0}) = ${t1 - t0} − ${c1 - c0} = ${(t1 - t0) - (c1 - c0)}.` };
  }],
  ["mean-variance", (d, r) => {
    const s1 = pick(r, [0.1, 0.2, 0.3]), s2 = pick(r, [0.1, 0.2, 0.4]), w = pick(r, [0.25, 0.5, 0.75]), rho = d === 2 ? pick(r, [0.5, -0.5]) : 0;
    const v = w * w * s1 * s1 + (1 - w) ** 2 * s2 * s2 + 2 * w * (1 - w) * rho * s1 * s2;
    return { q: `Two assets with volatilities ${s1} and ${s2}${rho ? ` and correlation ${rho}` : ", uncorrelated"}. You hold ${w} in the first and ${1 - w} in the second. What is the portfolio variance? (4 decimals)`,
             ...num(v, 0.00006), why: `w₁²σ₁² + w₂²σ₂²${rho ? " + 2w₁w₂ρσ₁σ₂" : ""} = ${fmt(v)}.` };
  }],
  ["shrinkage", (d, r) => {
    const s = pick(r, [0.04, 0.09, 0.012]), t = pick(r, [0.02, 0.05]), dl = pick(r, [0.2, 0.5, 0.8]), v = (1 - dl) * s + dl * t;
    return { q: `Sample covariance ${s}, target ${t}, shrinkage intensity ${dl}. What is the shrunk estimate? (4 decimals)`, ...num(v, 0.00006),
             why: `(1 − δ)·S + δ·F = ${fmt(1 - dl)}·${s} + ${dl}·${t} = ${fmt(v)}.` };
  }],
  ["value at risk", (d, r) => {
    const V = pick(r, [100000, 250000, 1000000]), s = pick(r, [0.01, 0.015, 0.02]), z = 1.645, v = z * s * V;
    return { q: `A $${V.toLocaleString()} position has normally distributed daily returns with mean 0 and σ = ${s}. What is its 1-day 95% VaR in dollars? (z = 1.645)`,
             ...num(v, v * 0.01), why: `VaR = z·σ·V = 1.645 · ${s} · ${V} = $${fmt(v)}.` };
  }],
  ["black-scholes", (d, r) => {
    const S = ri(r, 80, 120), K = ri(r, 80, 120), C = ri(r, 2, 20);
    return { q: `With zero interest rate, a call (strike ${K}) on a stock at ${S} costs ${C}. By put-call parity, what does the put with the same strike and expiry cost?`, ...num(C - S + K),
             why: `C − P = S − K·e^(−rT) = S − K when r = 0, so P = C − S + K = ${C} − ${S} + ${K} = ${C - S + K}.` };
  }],
  ["lagrangian", (d, r) => {
    const w = ri(r, 1, 6), m = pick(r, [1, 2, 4]), k = m * w * w;
    return { q: `L = ½·${m}·ẋ² − ½·${k}·x². What is the angular frequency ω of the motion?`, ...num(w),
             why: `The Euler–Lagrange equation gives ${m}ẍ = −${k}x, so ω = √(k/m) = √(${k}/${m}) = ${w}.` };
  }],
  ["hamiltonian", (d, r) => {
    const m = pick(r, [1, 2, 4]), k = pick(r, [2, 4, 8]), p = ri(r, -4, 4), x = ri(r, -3, 3), H = p * p / (2 * m) + 0.5 * k * x * x;
    return { q: `H = p²/(2·${m}) + ½·${k}·x². What is H at p = ${p}, x = ${x}?`, ...num(H, 0.006), why: `${p * p}/${2 * m} + ½·${k}·${x * x} = ${fmt(H)}.` };
  }],
];

export function generatorFor(skill) {
  const name = String(skill || "").toLowerCase();
  const leaf = name.split("/").pop();
  const hit = GEN.find(([key]) => leaf.includes(key)) || GEN.find(([key]) => name.includes(key));
  return hit ? (d, r) => hit[1](d, r, name) : null;
}

export function difficultyFor(pMastery) { return pMastery >= 0.8 ? 2 : pMastery >= 0.4 ? 1 : 0; }

export function check(item, raw) {
  const s = String(raw || "").trim().toLowerCase();
  if (item.kind === "yesno") return s.startsWith(item.answer[0]);
  let v = NaN;
  const frac = s.match(/^(-?\d+(?:\.\d+)?)\s*\/\s*(-?\d+(?:\.\d+)?)$/);
  if (frac) v = parseFloat(frac[1]) / parseFloat(frac[2]);
  else v = parseFloat(s.replace(/[$,\s]/g, "").replace(/π$/, ""));
  return Number.isFinite(v) && Math.abs(v - item.answer) <= Math.max(item.tol, 1e-9);
}

export const ANSWER_HINT = { number: "a number (fractions like 3/2 are fine)", yesno: "yes or no" };
export const KEYS = GEN.map(([k]) => k);
