/* Serverless mode (the GitHub Pages build): answers the same /api requests as the Python server.
   Session tables come straight from baked JSON; lap comparison and the pit wall run the backend's
   own Python in a Web Worker (static-worker.js). */
import { showLoading, hideLoading } from "./util.js";

const CFG = window.PITWALL_STATIC || {};
const DATA = new URL(CFG.data || "data/", location.href).href;
const PY = new URL(CFG.py || "py/", location.href).href;
const NOT_HERE = "This session isn't included in the online version. The full app on GitHub can load any session.";

export const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

let manifest = null;
export function getManifest() {
  return (manifest ||= fetch(DATA + "manifest.json").then((r) => { if (!r.ok) throw new Error("Couldn't load the session list"); return r.json(); }));
}
/** Does the online version have this event's race replay? */
export async function hasRace(year, round) {
  const m = await getManifest();
  const ev = (m.years[String(year)] || []).find((e) => String(e.round) === String(round));
  return !!ev && (ev.sessions.includes("Race") || ev.sessions.includes("Sprint"));
}

async function getJSON(rel, missing) {
  const r = await fetch(DATA + rel);
  if (!r.ok) throw new Error(r.status === 404 ? missing : `Couldn't load data (${r.status})`);
  return r.json();
}

// ------------------------------------------------------------------ worker
let worker = null, ready = false, seq = 0, bootError = null;
const pending = new Map();
let statusText = "";

export function startEngine() {
  if (worker) return;
  worker = new Worker(new URL("./static-worker.js", import.meta.url));
  worker.onmessage = (e) => {
    const m = e.data;
    if (m.status) {
      statusText = m.status;
      const el = document.getElementById("loading-text");
      if (el && !document.getElementById("loading").hidden) el.textContent = m.status;
      return;
    }
    if (m.ready) { ready = true; return; }
    if (m.bootError) { bootError = m.bootError; return; }
    const p = pending.get(m.id);
    if (!p) return;
    pending.delete(m.id);
    if (m.error != null) p.reject(new Error(m.error)); else p.resolve(m.result);
  };
  worker.onerror = (e) => {
    const err = new Error(`The analysis engine stopped: ${e.message || "unknown error"}. Reload the page to try again.`);
    pending.forEach((p) => p.reject(err)); pending.clear();
  };
  worker.postMessage({ init: { pyodide: CFG.pyodide, data: DATA, py: PY } });
}

async function call(fn, args, files = []) {
  startEngine();
  if (bootError) throw new Error(`The analysis engine couldn't start (${bootError}). Check your connection and reload.`);
  const cold = !ready;
  if (cold) showLoading(statusText || "Starting the analysis engine…");
  try {
    return await new Promise((resolve, reject) => {
      const id = ++seq;
      pending.set(id, { resolve, reject });
      worker.postMessage({ id, fn, args, files });
    });
  } finally {
    if (cold) hideLoading();
  }
}

// ------------------------------------------------------------------ API
export async function staticApi(path, p = {}) {
  const year = Number(p.year || 0), event = String(p.event ?? "demo"), session = p.session || "Race";
  const dir = `${year}/${event}/${slug(session)}/`;
  switch (path) {
    case "health": return { ok: true, static: true };
    case "schedule":
      return getJSON(`${year}/schedule.json`, `The online version has no ${year} sessions. The full app on GitHub can load any season.`);
    case "overview":
    case "drivers":
      if (year === 0) return call(path, { year: 0, event: "demo", session: "Demo" });
      return getJSON(`${dir}${path}.json`, NOT_HERE);
    case "compare": {
      const codes = String(p.drivers || "").split(",").map((c) => c.trim().toUpperCase()).filter(Boolean);
      const laps = String(p.laps || "").split(",").map((l) => l.trim()).filter(Boolean);
      if (year !== 0 && laps.some((l) => l !== "fastest")) {
        throw new Error("The online version includes each driver's fastest lap. The full app on GitHub can compare any lap.");
      }
      const files = year === 0 ? [] : [`${dir}drivers.json`, `${dir}circuit.json`, ...codes.map((c) => `${dir}laps/${c}.json`)];
      return call("compare", { year, event, session, drivers: codes.join(","), laps: laps.join(","), sectors: Number(p.sectors || 25) }, files);
    }
    case "live/sources": return { openf1_configured: false, static: true };
    case "live/meta":
    case "live/snapshot":
    case "live/strategy": {
      if (p.source === "openf1") {
        throw new Error("Live timing needs the Python server version of Pitwall (see the README on GitHub). The online version replays recorded races.");
      }
      const files = p.source === "replay" ? [`${dir}drivers.json`, `${dir}race.json`, `${dir}race.npz`] : [];
      return call(path.slice(5), { source: p.source, year, event, session, t: Number(p.t || 0), focus: p.focus || "" }, files);
    }
    default:
      throw new Error(`Unknown request ${path}`);
  }
}
