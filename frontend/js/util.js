/* Shared state + helpers. Everything else imports from here. */

export const $ = (id) => document.getElementById(id);
export const DEMO = "0";
export const MAX_DRIVERS = 4;
/** True on the serverless (GitHub Pages) build: baked sessions + the backend Python running in the browser. */
export const STATIC = typeof window !== "undefined" && !!window.PITWALL_STATIC;

export const state = {
  year: DEMO,
  event: "demo",
  session: "Demo",
  tab: "overview",
  schedule: [],
  overview: null,   // /api/overview — whole session
  selected: [],     // [{code, lap}]
  data: null,       // /api/compare — selected laps
  colors: {},       // code → display colour (teammates de-duplicated)
};

export const TYRE = { SOFT: "#ff3333", MEDIUM: "#ffd12e", HARD: "#eceff4", INTERMEDIATE: "#43b047", WET: "#2f7ff7", UNKNOWN: "#8b93a3", TEST_UNKNOWN: "#8b93a3" };
export const PURPLE = "#b26bff";
export const GREEN = "#2bd96b";
export const RED = "#ff2a2a";

// ------------------------------------------------------------------ events
export const bus = new EventTarget();
export const emit = (name, detail) => bus.dispatchEvent(new CustomEvent(name, { detail }));
export const on = (name, fn) => bus.addEventListener(name, (e) => fn(e.detail));

// ------------------------------------------------------------------ network
export async function api(path, params) {
  if (STATIC) return (await import("./static-api.js")).staticApi(path, params);
  const res = await fetch(`/api/${path}?${new URLSearchParams(params)}`);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`);
  return body;
}

// ------------------------------------------------------------------ UI bits
// several loads can overlap (e.g. a session switch while a compare is running):
// the overlay stays up until the last one finishes
let loadingCount = 0;
export function showLoading(text) { loadingCount++; $("loading-text").textContent = text; $("loading").hidden = false; }
export function hideLoading() { loadingCount = Math.max(0, loadingCount - 1); if (!loadingCount) $("loading").hidden = true; }
let toastTimer;
export function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 7000);
}
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

// ------------------------------------------------------------------ formatting
export function fmtLap(s) {
  if (s == null || !isFinite(s)) return "—";
  const m = Math.floor(s / 60); const r = s - m * 60;
  return m ? `${m}:${r.toFixed(3).padStart(6, "0")}` : r.toFixed(3);
}
export const fmtGap = (g) => (g == null || !isFinite(g) ? "" : `${g >= 0 ? "+" : "−"}${Math.abs(g).toFixed(3)}`);
export const fmtSec = (s, d = 3) => (s == null || !isFinite(s) ? "—" : s.toFixed(d));

// ------------------------------------------------------------------ colour
const hex2rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
export function mix(hex, other, t) {
  const a = hex2rgb(hex), b = hex2rgb(other);
  return "#" + a.map((v, i) => Math.round(v + (b[i] - v) * t).toString(16).padStart(2, "0")).join("");
}
/** Teammates share a team colour; lighten the second one so they stay distinguishable. */
export function assignColors(drivers) {
  const used = new Set(); const out = {};
  for (const d of drivers) {
    let c = (d.color || "#888888").toLowerCase();
    if (!/^#[0-9a-f]{6}$/.test(c)) c = "#888888";
    if (used.has(c)) c = mix(c, "#ffffff", 0.5);
    used.add(c); out[d.code] = c;
  }
  return out;
}
export const colorOf = (code) => state.colors[code] || "#888888";

/** Slow → fast colour scale used by the speed map, 3D track and corner maps. */
export const SPEED_STOPS = [[0, "#5b2a86"], [0.25, "#2f6fd6"], [0.5, "#1fb58f"], [0.75, "#f2c230"], [1, "#e8352b"]];
export function speedColor(t) {
  t = Math.min(1, Math.max(0, t));
  for (let i = 1; i < SPEED_STOPS.length; i++) {
    const [p1, c1] = SPEED_STOPS[i];
    if (t <= p1) {
      const [p0, c0] = SPEED_STOPS[i - 1];
      return mix(c0, c1, (t - p0) / (p1 - p0));
    }
  }
  return SPEED_STOPS[SPEED_STOPS.length - 1][1];
}
export const rgb01 = (hex) => hex2rgb(hex).map((v) => v / 255);

// ------------------------------------------------------------------ numeric
/** Index of the sample nearest to `x` in a sorted array. */
export function nearest(arr, x) {
  let lo = 0, hi = arr.length - 1;
  if (x <= arr[0]) return 0;
  if (x >= arr[hi]) return hi;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (arr[mid] < x) lo = mid; else hi = mid; }
  return x - arr[lo] < arr[hi] - x ? lo : hi;
}
/** Linear interpolation of y(x) on a sorted x array. */
export function interp(xs, ys, x) {
  const n = xs.length;
  if (x <= xs[0]) return ys[0];
  if (x >= xs[n - 1]) return ys[n - 1];
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] <= x) lo = mid; else hi = mid; }
  const f = (x - xs[lo]) / (xs[hi] - xs[lo] || 1);
  return ys[lo] + (ys[hi] - ys[lo]) * f;
}
/** Replace nulls by the nearest earlier (or later) value. */
export function fillNulls(arr, fallback = 0) {
  const out = arr.slice();
  let last = out.find((v) => v != null);
  if (last == null) return out.map(() => fallback);
  for (let i = 0; i < out.length; i++) { if (out[i] == null || !isFinite(out[i])) out[i] = last; else last = out[i]; }
  return out;
}
export function movingAvg(arr, w) {
  if (w <= 1) return arr.slice();
  const h = w >> 1, n = arr.length, out = new Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0, c = 0;
    for (let j = Math.max(0, i - h); j <= Math.min(n - 1, i + h); j++) { s += arr[j]; c++; }
    out[i] = s / c;
  }
  return out;
}
export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
export const sum = (a) => a.reduce((s, v) => s + v, 0);

// ------------------------------------------------------------------ plotly theme
export const PLOT_BASE = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { family: "Titillium Web, system-ui, sans-serif", color: "#c9ced6", size: 12 },
  margin: { l: 10, r: 10, t: 10, b: 10 },
  showlegend: false,
  hoverlabel: { bgcolor: "#10131a", bordercolor: "#2c3240", font: { family: "JetBrains Mono, monospace", size: 12, color: "#e9ecf1" } },
};
export const PLOT_CFG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d", "toImage"] };
export const axis = (extra = {}) => ({ gridcolor: "#1d222b", zerolinecolor: "#323946", linecolor: "#2a303b", tickfont: { size: 10, color: "#8b93a3" }, ...extra });
export const axisTitle = (text) => ({ text, font: { size: 11, color: "#8b93a3" }, standoff: 6 });
