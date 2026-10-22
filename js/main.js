/* App shell: session pickers, driver selection, tabs, data loading, URL state. */
import {
  $, state, DEMO, MAX_DRIVERS, STATIC, api, on, toast, showLoading, hideLoading, assignColors, colorOf, esc, fmtLap,
} from "./util.js";
import { playIntro } from "./intro.js";
import * as C from "./charts.js";
import { renderCorners, bindCorners } from "./corners.js";
import { renderSession, markBoardSelection } from "./session.js";
import { initReplay, buildReplay, setReplayVisible } from "./track3d.js";
import { bindLive, showLive, liveSelectionChanged } from "./live.js";

const TABS = ["overview", "replay", "telemetry", "corners", "pitwall", "session"];
const dirty = new Set(TABS);

// ------------------------------------------------------------------ URL state
function readUrl() {
  const q = new URLSearchParams(location.search);
  if (q.get("year")) state.year = q.get("year");
  if (q.get("event")) state.event = q.get("event");
  if (q.get("session")) state.session = q.get("session");
  const tab = q.get("tab") || location.hash.slice(1);
  if (TABS.includes(tab)) state.tab = tab;
  if (q.get("drivers")) {
    const laps = (q.get("laps") || "").split(",");
    state.selected = q.get("drivers").split(",").filter(Boolean).slice(0, MAX_DRIVERS).map((code, i) => ({ code, lap: laps[i] || "fastest" }));
  }
}
function writeUrl() {
  const q = new URLSearchParams({ year: state.year, event: state.event, session: state.session, tab: state.tab });
  if (state.selected.length) {
    q.set("drivers", state.selected.map((s) => s.code).join(","));
    q.set("laps", state.selected.map((s) => s.lap).join(","));
  }
  history.replaceState(null, "", `?${q}`);
}

// ------------------------------------------------------------------ tabs
function setTab(tab) {
  if (!TABS.includes(tab)) tab = "overview";
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((b) => { const on = b.dataset.tab === tab; b.classList.toggle("active", on); b.setAttribute("aria-selected", String(on)); });
  const ready = tab === "pitwall" ? true : tab === "session" ? !!state.overview : !!state.data;
  $("empty").hidden = ready;
  if (!ready) $("empty-title").textContent = busy.overview || busy.compare ? "Loading…"
    : !state.overview ? "Pick a season, event and session." : "Pick up to 4 drivers — the telemetry loads automatically.";
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", ready && p.dataset.panel === tab));
  setReplayVisible(tab === "replay" && !!state.data);
  showLive(tab === "pitwall");
  safe("header", () => C.renderHero());
  if (ready) renderTab(tab);
  if (tab === "replay" && ready) requestAnimationFrame(() => $("replay").scrollIntoView({ behavior: "smooth", block: "start" }));
  writeUrl();
}

/** Run one chart; a failure is reported but never stops the other charts. */
function safe(name, fn) {
  try { fn(); return true; }
  catch (e) { console.error(`[${name}]`, e); toast(`Couldn't draw ${name}: ${e.message}`); return false; }
}

const VIEWS = {
  overview: (d) => [["lap cards", () => C.renderCards(d)], ["insights", () => C.renderInsights(d)], ["sector bars", () => C.renderSectorBars(d)],
    ["track dominance", () => C.renderDominance(d)], ["speed map", () => { C.renderSpeedmapSwitch(d); C.renderSpeedmap(d); }], ["gauges", () => C.renderGauges(d)]],
  telemetry: (d) => [["telemetry traces", () => C.renderTelemetry(d)], ["g-g diagram", () => C.renderGG(d)],
    ["gear map", () => C.renderGears(d)], ["speed histogram", () => C.renderHist(d)]],
  corners: (d) => [["corner analysis", () => renderCorners(d)]],
  replay: (d) => [["3D replay", () => { if (initReplay()) { buildReplay(d); setReplayVisible(true); } }]],
};

function renderTab(tab) {
  if (tab === "pitwall") return;
  if (tab === "session") {
    if (!dirty.has("session")) { resizePlots(tab); return; }
    dirty.delete("session");
    try { renderSession(); } catch (e) { dirty.add("session"); }   // each part already reported its own error
    return;
  }
  const d = state.data; if (!d) return;
  if (!dirty.has(tab)) { resizePlots(tab); return; }
  dirty.delete(tab);
  const ok = VIEWS[tab](d).map(([name, fn]) => safe(name, fn)).every(Boolean);
  if (!ok) dirty.add(tab);              // try again next time the tab is opened
}
const resizePlots = (tab) => document.querySelectorAll(`[data-panel="${tab}"] .js-plotly-plot`).forEach((p) => safe("resize", () => Plotly.Plots.resize(p)));

// ------------------------------------------------------------------ pickers
async function fillYears() {
  const sel = $("year"), now = new Date().getFullYear();
  let years = Array.from({ length: now - 2018 + 1 }, (_, i) => now - i);
  if (STATIC) {
    const { getManifest, startEngine } = await import("./static-api.js");
    startEngine();                                    // warm up the in-browser Python while the session table loads
    try { years = Object.keys((await getManifest()).years).map(Number).sort((a, b) => b - a); } catch (e) { toast(e.message); }
    if (!new URLSearchParams(location.search).get("year") && years.length) state.year = String(years[0]);
  }
  sel.innerHTML = `<option value="${DEMO}">Demo (offline)</option>` + years.map((y) => `<option value="${y}">${y}</option>`).join("");
  sel.value = state.year;
  if (sel.value !== state.year) { state.year = DEMO; sel.value = DEMO; }
}

let schedSeq = 0;
async function onYear() {
  state.year = $("year").value;
  const ev = $("event"), ss = $("session");
  clearSession();
  if (state.year === DEMO) {
    schedSeq++;
    ev.innerHTML = `<option value="demo">Demo Circuit</option>`; ev.disabled = true;
    ss.innerHTML = `<option value="Demo">Demo lap + simulated race</option>`; ss.disabled = true;
    state.event = "demo"; state.session = "Demo";
    return loadOverview();
  }
  const seq = ++schedSeq;
  ev.disabled = ss.disabled = true;
  ev.innerHTML = `<option>Loading…</option>`; ss.innerHTML = "";
  let sched;
  try { sched = await api("schedule", { year: state.year }); }
  catch (e) { if (seq === schedSeq) { toast(e.message); ev.innerHTML = ""; } return; }
  if (seq !== schedSeq) return;                       // a newer season was picked meanwhile
  state.schedule = sched;
  const done = state.schedule.filter((e) => e.sessions.length);
  if (!done.length) { ev.innerHTML = `<option>No sessions yet</option>`; return; }
  ev.innerHTML = done.map((e) => `<option value="${e.round}">R${e.round} · ${esc(e.name)}</option>`).join("");
  ev.disabled = false;
  const want = done.find((e) => String(e.round) === String(state.event));
  ev.value = want ? want.round : done[done.length - 1].round;
  return onEvent();
}

function onEvent() {
  state.event = $("event").value;
  const e = state.schedule.find((x) => String(x.round) === String(state.event));
  const ss = $("session");
  if (!e) return;
  ss.innerHTML = e.sessions.map((s) => `<option>${esc(s)}</option>`).join("");
  ss.disabled = false;
  ss.value = e.sessions.includes(state.session) ? state.session : (e.sessions.includes("Qualifying") ? "Qualifying" : e.sessions[e.sessions.length - 1]);
  return onSession();
}

function onSession() { state.session = $("session").value; return loadOverview(); }

/** Forget everything from the previous session so nothing stale is ever shown. */
function clearSession() {
  ovSeq++; cmpSeq++; clearTimeout(cmpTimer);
  busy.overview = busy.compare = false;
  state.overview = null; state.data = null;
  TABS.forEach((t) => dirty.add(t));
  renderDriverList();
}

const busy = { overview: false, compare: false };
let ovSeq = 0;
async function loadOverview() {
  clearSession();
  const seq = ovSeq;
  const key = `${state.year}|${state.event}|${state.session}`;
  busy.overview = true;
  renderDriverList();
  setTab(state.tab);
  showLoading(state.year === DEMO ? "Loading the demo session…" : STATIC ? `Loading ${state.session}…`
    : `Loading ${state.session}… the first time can take 30–60 s while FastF1 downloads the session.`);
  let ov = null;
  try { ov = await api("overview", { year: state.year, event: state.event, session: state.session }); }
  catch (e) { if (seq === ovSeq) toast(e.message); }
  finally { hideLoading(); }
  if (seq !== ovSeq || key !== `${state.year}|${state.event}|${state.session}`) return;   // stale answer
  busy.overview = false;
  state.overview = ov;
  const drivers = ov?.drivers || [];
  state.colors = assignColors(drivers);               // official order: the higher-placed teammate keeps the team colour
  const codes = new Set(drivers.map((d) => d.code));
  const noTel = new Set(drivers.filter((d) => d.no_telemetry).map((d) => d.code));
  state.selected = state.selected.filter((s) => codes.has(s.code) && !noTel.has(s.code) && (s.lap === "fastest" || lapRow(s.code, s.lap)));
  if (!state.selected.length) {
    const ranked = drivers.filter((d) => d.best != null && !d.no_telemetry).sort((a, b) => a.best - b.best);
    const first = ranked[0], second = ranked.find((d) => d !== first && d.team !== first?.team) || ranked[1];
    state.selected = [first, second].filter(Boolean).map((d) => ({ code: d.code, lap: "fastest" }));
  }
  sortSelected();
  dirty.add("session");
  liveSelectionChanged();                             // the session changed: the pit wall replay follows it
  renderDriverList();
  setTab(state.tab);
  if (state.selected.length) compare();               // load the telemetry straight away
}

// ------------------------------------------------------------------ selection
const driverRow = (code) => state.overview?.drivers.find((d) => d.code === code);
const lapRow = (code, lap) => driverRow(code)?.laps.find((l) => String(l.lap) === String(lap));
/** Lap time of a selection: the official personal best for "fastest", else that lap's time. */
function lapTimeOf(s) {
  const t = s.lap === "fastest" ? driverRow(s.code)?.best : lapRow(s.code, s.lap)?.time;
  return t == null ? Infinity : t;
}
/** The reference (first) driver is always the one with the fastest chosen lap. */
function sortSelected() {
  const idx = new Map(state.selected.map((s, i) => [s, i]));
  state.selected.sort((a, b) => lapTimeOf(a) - lapTimeOf(b) || idx.get(a) - idx.get(b));
}

function renderDriverList() {
  const list = $("driver-list");
  const ov = state.overview;
  const drivers = ov ? ov.drivers : [];             // already in official order from the server
  if (!drivers.length) {
    list.innerHTML = `<span class="hint">${busy.overview ? "Loading drivers…" : "No drivers loaded."}</span>`;
    $("selected").innerHTML = ""; $("go").disabled = true; return;
  }
  const classified = ov.order === "classification";
  list.innerHTML = drivers.map((d) => {
    const i = state.selected.findIndex((s) => s.code === d.code);
    const pos = classified ? (d.position != null && /^\d+$/.test(d.classified || String(d.position)) ? `P${d.classified || d.position}` : (d.classified || "—")) : "";
    return `<button type="button" class="chip ${i >= 0 ? "on" : ""}${d.no_telemetry ? " nodata" : ""}" data-code="${d.code}" style="--c:${colorOf(d.code)}" title="${esc(d.name)} — ${esc(d.team)}${d.best_str ? ` · best ${d.best_str}` : ""}${d.no_telemetry ? " · telemetry incomplete in the source data" : ""}">
      <span class="bar" style="background:${colorOf(d.code)}"></span>
      ${i >= 0 ? `<span class="ord">${i === 0 ? "REF" : i + 1}</span>` : ""}
      ${classified ? `<span class="pos ${/^P/.test(pos) ? "" : "out"}">${esc(pos)}</span>` : ""}
      ${d.number != null ? `<span class="num">${d.number}</span>` : ""}<span class="code">${d.code}</span><span class="t">${d.best_str || "no time"}</span></button>`;
  }).join("");
  list.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => toggleDriver(b.dataset.code)));

  $("selected").innerHTML = state.selected.map((s, i) => {
    const d = driverRow(s.code);
    // the online build bakes each driver's fastest lap only
    const laps = STATIC && state.year !== DEMO ? [] : (d?.laps || []).filter((l) => l.time != null);
    const opts = [`<option value="fastest">fastest${d?.best_str ? ` · ${d.best_str}` : ""}</option>`]
      .concat(laps.map((l) => `<option value="${l.lap}">lap ${l.lap} · ${fmtLap(l.time)}${l.deleted ? " (deleted)" : ""}${l.lap === d.best_lap ? " ★" : ""}</option>`));
    return `<span class="sel"><b style="color:${colorOf(s.code)}">${s.code}</b>${i === 0 ? " (ref)" : ""}<select data-code="${s.code}">${opts.join("")}</select></span>`;
  }).join("");
  $("selected").querySelectorAll("select").forEach((sel) => {
    const s = state.selected.find((x) => x.code === sel.dataset.code);
    sel.value = s.lap;
    if (!sel.value) { sel.value = "fastest"; s.lap = "fastest"; }
    sel.addEventListener("change", () => { s.lap = sel.value; selectionChanged(); });
  });
  $("go").disabled = state.selected.length === 0;
  markBoardSelection();
}

function selectionChanged() {
  sortSelected();
  renderDriverList();
  if (!state.selected.length) { cmpSeq++; clearTimeout(cmpTimer); state.data = null; TABS.forEach((t) => dirty.add(t)); setTab(state.tab); return; }
  scheduleCompare();
}

function toggleDriver(code) {
  const row = driverRow(code);
  if (!row) return;
  const i = state.selected.findIndex((s) => s.code === code);
  if (i < 0 && row.no_telemetry) { toast(`${code}'s car telemetry for this session is incomplete in the source data, so it can't be compared.`); return; }
  if (i >= 0) state.selected.splice(i, 1);
  else if (state.selected.length < MAX_DRIVERS) state.selected.push({ code, lap: "fastest" });
  else { toast(`Up to ${MAX_DRIVERS} drivers — remove one first.`); return; }
  selectionChanged();
}

// ------------------------------------------------------------------ compare
let cmpSeq = 0, cmpTimer = 0;
/** Several quick clicks → one request, fired shortly after the last click. */
function scheduleCompare() { clearTimeout(cmpTimer); cmpTimer = setTimeout(compare, 450); }

async function compare(ev) {
  ev?.preventDefault();
  clearTimeout(cmpTimer);
  if (!state.selected.length || !state.overview) return;
  sortSelected();
  const seq = ++cmpSeq;
  const key = `${state.year}|${state.event}|${state.session}`;
  const sel = state.selected.map((s) => ({ ...s }));
  busy.compare = true;
  showLoading("Crunching telemetry…");
  let res = null;
  try {
    res = await api("compare", {
      year: state.year, event: state.event, session: state.session,
      drivers: sel.map((s) => s.code).join(","),
      laps: sel.map((s) => s.lap).join(","),
    });
  } catch (e) { if (seq === cmpSeq) toast(e.message); }
  finally { hideLoading(); }
  if (seq !== cmpSeq || key !== `${state.year}|${state.event}|${state.session}`) return;   // superseded by a newer request
  busy.compare = false;
  if (!res) {                                         // never leave charts of a different selection on screen
    state.data = null; TABS.forEach((t) => dirty.add(t)); setTab(state.tab); return;
  }
  state.data = res;
  // the server orders the drivers fastest-first: mirror that so REF always matches the charts
  state.selected = res.selection.map((s) => ({ code: s.code, lap: String(s.lap) }));
  TABS.forEach((t) => dirty.add(t));
  renderDriverList();
  setTab(state.tab);
}

// ------------------------------------------------------------------ boot
async function boot() {
  readUrl();
  await fillYears();
  $("year").addEventListener("change", () => { state.selected = []; onYear(); });
  // lap numbers mean nothing in another session: keep the drivers, go back to their fastest laps
  const resetLaps = () => state.selected.forEach((s) => { s.lap = "fastest"; });
  $("event").addEventListener("change", () => { resetLaps(); onEvent(); });
  $("session").addEventListener("change", () => { resetLaps(); onSession(); });
  $("picker").addEventListener("submit", compare);
  $("tabs").addEventListener("click", (e) => { const b = e.target.closest(".tab"); if (b) setTab(b.dataset.tab); });
  $("replay-intro").addEventListener("click", () => playIntro({ force: true }));
  on("toggle-driver", toggleDriver);
  C.bindTilt($("cards"));
  bindCorners();
  bindLive();

  playIntro();
  setTab(state.tab);
  await onYear();                                     // loads the session and then the telemetry
}
boot();
