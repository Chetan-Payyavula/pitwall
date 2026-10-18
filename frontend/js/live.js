/* Pit Wall: live timing tower, track map, race control, driver dashboards and
   strategy (tyre deg, pit loss, rejoin, undercut/overcut, pit window, ERS est.).
   Works on three sources: built-in demo race, FastF1 replay of a past race,
   OpenF1 live. Replay/demo run on a local clock the user can play/scrub. */
import { $, state, api, toast, showLoading, hideLoading, esc, fmtLap, fmtGap, clamp, TYRE, PURPLE, GREEN,
  PLOT_BASE, PLOT_CFG, axis, axisTitle, mix } from "./util.js";

const L = {
  source: "demo", meta: null, snap: null, prevSnap: null, snapAt: 0, prevAt: 0, strat: null,
  t: 0, playing: true, speed: 5, visible: false, focus: [], raf: 0, lastFrame: 0,
  inflight: false, stratInflight: false, lastStrat: 0, lastStratLap: -1, msgSeen: new Set(), track: null, error: null,
  rowH: 34, rejoinDriver: null,
};
const STATUS_CLASS = { "1": "green", "2": "yellow", "4": "sc", "5": "red", "6": "vsc", "7": "vsc" };
const KIND_ICON = { sc: "🚨", vsc: "🟡", red: "🟥", yellow: "🟨", "double-yellow": "🟨", green: "🟩", chequered: "🏁", "track-limits": "⚠️",
  penalty: "⚖️", incident: "🔍", blue: "🟦", drs: "📶", info: "ℹ️" };

// ======================================================================= params
function srcParams() {
  if (L.source === "demo") return { source: "demo", year: 0, event: "demo", session: "Race" };
  if (L.source === "openf1") return { source: "openf1", year: 0, event: "latest", session: "Race" };
  const sess = ["Race", "Sprint"].includes(state.session) ? state.session : "Race";
  return { source: "replay", year: state.year, event: state.event, session: sess };
}

// ======================================================================= boot
export function bindLive() {
  $("pw-source").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b && !b.disabled) setSource(b.dataset.v); });
  $("pw-play").addEventListener("click", () => setPlaying(!L.playing));
  $("pw-speed").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; L.speed = +b.dataset.v; seg("pw-speed", b.dataset.v); });
  $("pw-back").addEventListener("click", () => jumpLaps(-1));
  $("pw-fwd").addEventListener("click", () => jumpLaps(1));
  $("pw-scrub").addEventListener("input", (e) => { if (!L.meta) return; L.t = L.meta.start + (+e.target.value / 1000) * (L.meta.end - L.meta.start); L.lastStrat = 0; tick(true); });
  $("pw-tower").addEventListener("click", (e) => { const r = e.target.closest(".tw-row"); if (r) toggleFocus(r.dataset.code); });
  $("pw-rejoin-driver").addEventListener("change", (e) => { L.rejoinDriver = e.target.value; L.lastStrat = 0; tick(true); });
  window.addEventListener("keydown", (e) => {
    if (!L.visible || e.target.closest("input, select, textarea, button")) return;
    if (e.key === " ") { e.preventDefault(); setPlaying(!L.playing); }
    if (e.key === "ArrowRight") jumpLaps(1);
    if (e.key === "ArrowLeft") jumpLaps(-1);
  });
  new ResizeObserver(() => { L.track = null; drawMap(); }).observe($("pw-map").parentElement);
  api("live/sources", {}).then((r) => { if (!r.openf1_configured) $("pw-source").querySelector('[data-v="openf1"]').title = "Needs an OpenF1 account — see README"; }).catch(() => {});
}

function seg(id, v) { $(id).querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === String(v))); }
function setPlaying(p) { L.playing = p; $("pw-play").textContent = p ? "❚❚" : "▶"; }

export async function showLive(visible) {
  L.visible = visible;
  const rb = $("pw-source").querySelector('[data-v="replay"]');
  rb.disabled = state.year === "0";
  rb.title = rb.disabled ? "Pick a season and race in the top bar first" : `Replay ${state.schedule.find((e) => String(e.round) === String(state.event))?.name || "the selected race"}`;
  if (!visible) { cancelAnimationFrame(L.raf); L.raf = 0; return; }
  if (!L.meta) await setSource(L.source);
  if (!L.raf) { L.lastFrame = performance.now(); L.raf = requestAnimationFrame(frame); }
}

/** Called when the season/event selection changes elsewhere. */
export function liveSelectionChanged() {
  if (L.source === "replay") { L.meta = null; if (L.visible) setSource("replay"); }
  const rb = $("pw-source").querySelector('[data-v="replay"]');
  if (rb) rb.disabled = state.year === "0";
}

async function setSource(src) {
  L.source = src; seg("pw-source", src);
  L.meta = L.snap = L.prevSnap = L.strat = null; L.msgSeen.clear(); L.track = null; L.error = null;
  $("pw-live-badge").hidden = src !== "openf1";
  $("pw-transport").classList.toggle("disabled", src === "openf1");
  const p = srcParams();
  const seq = (L.srcSeq = (L.srcSeq || 0) + 1);
  const overlay = src === "replay";
  if (overlay) showLoading("Loading the full race for replay… the first time can take 1–2 minutes while FastF1 downloads every car's telemetry.");
  let meta;
  try {
    meta = await api("live/meta", p);
  } catch (e) {
    if (overlay) hideLoading();
    if (seq !== L.srcSeq) return;           // the user already picked another source
    L.error = e.message; renderError(e.message);
    return;
  }
  if (overlay) hideLoading();
  if (seq !== L.srcSeq) return;
  L.meta = meta;
  $("pw-error").hidden = true;
  $("pw-name").textContent = L.meta.name;
  L.t = src === "openf1" ? (L.meta.now ?? 0) : (L.meta.race_start ?? L.meta.start) - 8;
  L.focus = L.meta.drivers.slice(0, 2).map((d) => d.code);
  L.rejoinDriver = L.focus[0];
  buildTower();
  setPlaying(true);
  L.lastStrat = 0; L.lastStratLap = -1;
  await tick(true);
}

function renderError(msg) {
  const el = $("pw-error"); el.hidden = false;
  el.innerHTML = `<b>Can't start this source.</b> ${esc(msg)}`;
}

function jumpLaps(n) {
  if (!L.meta || L.source === "openf1") return;
  L.t = clamp(L.t + n * (L.meta.typical_lap || 90), L.meta.start, L.meta.end);
  L.lastStrat = 0; tick(true);
}

function toggleFocus(code) {
  const i = L.focus.indexOf(code);
  if (i >= 0) L.focus.splice(i, 1);
  else { L.focus.push(code); if (L.focus.length > 2) L.focus.shift(); }
  if (L.focus.length && !L.focus.includes(L.rejoinDriver)) L.rejoinDriver = L.focus[0];
  L.lastStrat = 0; tick(true);
}

// ======================================================================= loop
function frame(now) {
  if (!L.visible) { L.raf = 0; return; }
  L.raf = requestAnimationFrame(frame);
  const dt = Math.min(0.25, (now - L.lastFrame) / 1000); L.lastFrame = now;
  if (L.meta && L.playing && L.source !== "openf1") {
    L.t += dt * L.speed;
    if (L.t > L.meta.end) { L.t = L.meta.end; setPlaying(false); }
  }
  if (now - L.snapAt > (L.source === "openf1" ? 1000 : 250)) tick(false);
  drawMap();
  updateClock();
}

async function tick(force) {
  if (!L.meta || (L.inflight && !force)) return;
  L.inflight = true;
  const p = { ...srcParams(), t: L.t.toFixed(2), focus: L.focus.join(",") };
  try {
    const s = await api("live/snapshot", p);
    L.prevSnap = L.snap; L.prevAt = L.snapAt; L.snap = s; L.snapAt = performance.now();
    if (L.source === "openf1") L.t = s.t;
    renderSnapshot(s);
  } catch (e) { if (L.source === "openf1") renderError(e.message); }
  finally { L.inflight = false; }
  const due = force || performance.now() - L.lastStrat > 2500 || (L.snap && L.snap.lap !== L.lastStratLap);
  if (due && !L.stratInflight) loadStrategy();
}

async function loadStrategy() {
  L.stratInflight = true; L.lastStrat = performance.now();
  const focus = [...new Set([L.rejoinDriver, ...L.focus].filter(Boolean))];
  try {
    L.strat = await api("live/strategy", { ...srcParams(), t: L.t.toFixed(2), focus: focus.join(",") });
    L.lastStratLap = L.strat.lap;
    renderStrategy(L.strat);
  } catch (e) { /* keep last */ }
  finally { L.stratInflight = false; }
}

// ======================================================================= clock + status
function updateClock() {
  if (!L.meta) return;
  const rs = L.meta.race_start ?? L.meta.start;
  const rel = L.t - rs;
  const sign = rel < 0 ? "−" : "", a = Math.abs(rel), h = Math.floor(a / 3600), m = Math.floor((a % 3600) / 60), s = Math.floor(a % 60);
  $("pw-clock").textContent = `${sign}${h ? h + ":" : ""}${String(m).padStart(h ? 2 : 1, "0")}:${String(s).padStart(2, "0")}`;
  if (L.source !== "openf1") $("pw-scrub").value = String(Math.round(((L.t - L.meta.start) / (L.meta.end - L.meta.start)) * 1000));
}

function renderSnapshot(s) {
  $("pw-lap").innerHTML = `LAP <b>${Math.max(1, s.lap)}</b><span>/${s.total_laps}</span>`;
  const st = s.status, cls = STATUS_CLASS[st.code] || "green";
  const ys = Object.entries(s.yellow_sectors);
  const bar = $("pw-status");
  bar.className = `pw-status ${cls}`;
  bar.innerHTML = `<span class="ps-flag"></span><b>${esc(st.label)}</b>${st.code === "4" ? "<span class='ps-sub'>Safety car deployed — field closing up</span>" : st.code === "6" ? "<span class='ps-sub'>Virtual safety car — hold delta</span>" : st.code === "7" ? "<span class='ps-sub'>VSC ending</span>" : st.code === "5" ? "<span class='ps-sub'>Session suspended</span>" : ""}
    ${ys.map(([sec, f]) => `<span class="ps-chip ${f === "DOUBLE YELLOW" ? "dbl" : ""}">${f === "DOUBLE YELLOW" ? "Double yellow" : "Yellow"} · S${sec}</span>`).join("")}
    ${s.simulated ? "<span class='ps-sim'>simulated race</span>" : ""}`;
  renderTower(s);
  renderMessages(s);
  renderDash(s);
}

// ======================================================================= tower
function buildTower() {
  const el = $("pw-tower");
  el.innerHTML = `<div class="tw-head"><span>P</span><span>Driver</span><span>Gap</span><span>Int</span><span>Last</span><span>Tyre</span><span>Pit</span></div><div class="tw-body" id="tw-body"></div>`;
  $("tw-body").innerHTML = L.meta.drivers.map((d) => `<div class="tw-row" data-code="${d.code}" style="--c:${d.color}">
      <span class="tw-pos"></span><span class="tw-drv"><i></i><b>${d.code}</b><em class="tw-flags"></em></span>
      <span class="tw-gap"></span><span class="tw-int"></span><span class="tw-last"></span><span class="tw-tyre"></span><span class="tw-pit"></span></div>`).join("");
  $("tw-body").style.height = `${L.meta.drivers.length * L.rowH}px`;
}

function renderTower(s) {
  const body = $("tw-body"); if (!body) return;
  const leader = s.drivers[0];
  for (const r of s.drivers) {
    const row = body.querySelector(`[data-code="${r.code}"]`); if (!row) continue;
    row.style.transform = `translateY(${(r.pos - 1) * L.rowH}px)`;
    row.classList.toggle("focus", L.focus.includes(r.code));
    row.classList.toggle("out", r.status === "OUT");
    row.classList.toggle("pit", r.in_pit);
    row.querySelector(".tw-pos").textContent = r.pos;
    const gapTxt = r.status === "OUT" ? "OUT" : r.pos === 1 ? (r.status === "FIN" ? "🏁" : `L${r.lap}`) : r.laps_down ? `+${r.laps_down} LAP` : r.gap == null ? "—" : `+${r.gap.toFixed(1)}`;
    row.querySelector(".tw-gap").textContent = gapTxt;
    const ie = row.querySelector(".tw-int");
    ie.textContent = r.pos === 1 || r.interval == null || r.status === "OUT" ? "" : `+${r.interval.toFixed(1)}`;
    ie.classList.toggle("drs", r.interval != null && r.interval < 1 && r.pos > 1 && s.status.code === "1");
    const le = row.querySelector(".tw-last");
    le.textContent = r.last ? fmtLap(r.last) : "—";
    le.className = `tw-last ${r.last_deleted ? "del" : r.fastest ? "purple" : r.pb ? "pb" : ""}`;
    const tc = TYRE[r.compound] || "#8b93a3";
    row.querySelector(".tw-tyre").innerHTML = r.compound ? `<i style="color:${tc}">${r.compound[0]}</i>${r.tyre_age ?? ""}` : "";
    row.querySelector(".tw-pit").textContent = r.status === "OUT" ? "" : r.in_pit ? "PIT" : r.status === "FIN" ? "FIN" : r.pits || "";
    const flags = [];
    if (r.drs) flags.push(`<span class="fl drs">DRS</span>`);
    if (r.track_limits) flags.push(`<span class="fl tl" title="${r.track_limits} track-limit warnings, ${r.deleted} laps deleted">⚠${r.track_limits}</span>`);
    if (r.investigations && !r.penalties.length) flags.push(`<span class="fl inv" title="Under investigation / noted">🔍</span>`);
    if (r.penalties.length) flags.push(`<span class="fl pen" title="${esc(r.penalties.join("\n"))}">⚖${r.penalties.length}</span>`);
    if (r.blue) flags.push(`<span class="fl blue" title="Blue flag">B</span>`);
    row.querySelector(".tw-flags").innerHTML = flags.join("");
  }
  void leader;
}

// ======================================================================= race control
function renderMessages(s) {
  const el = $("pw-rc");
  const html = s.messages.map((m) => {
    const key = `${m.t}|${m.message}`;
    const fresh = !L.msgSeen.has(key); L.msgSeen.add(key);
    return `<li class="rc ${m.kind} ${fresh && L.msgSeen.size > s.messages.length ? "new" : ""}"><span class="rc-ic">${KIND_ICON[m.kind] || "ℹ️"}</span>
      <div><div class="rc-meta">${m.lap ? `LAP ${m.lap}` : ""}${m.codes.length ? ` · ${m.codes.join(", ")}` : ""}</div><div class="rc-msg">${esc(m.message)}</div></div></li>`;
  }).join("");
  if (el.dataset.sig !== String(s.messages.length) + (s.messages[0]?.t ?? "")) {
    el.innerHTML = html || `<li class="rc info"><div class="rc-msg">No messages yet.</div></li>`;
    el.dataset.sig = String(s.messages.length) + (s.messages[0]?.t ?? "");
  }
}

// ======================================================================= map
function trackGeom(cv) {
  if (L.track && L.track.w === cv.width && L.track.h === cv.height) return L.track;
  const tr = L.meta.track, X = tr.x, Y = tr.y;
  if (!X || !X.length) return null;
  const minX = Math.min(...X), maxX = Math.max(...X), minY = Math.min(...Y), maxY = Math.max(...Y);
  const pad = 30 * (window.devicePixelRatio || 1);
  const k = Math.min((cv.width - 2 * pad) / (maxX - minX || 1), (cv.height - 2 * pad) / (maxY - minY || 1));
  const ox = (cv.width - (maxX - minX) * k) / 2, oy = (cv.height - (maxY - minY) * k) / 2;
  const tx = (x) => ox + (x - minX) * k, ty = (y) => cv.height - (oy + (y - minY) * k);
  const path = new Path2D();
  X.forEach((x, i) => (i ? path.lineTo(tx(x), ty(Y[i])) : path.moveTo(tx(x), ty(Y[i]))));
  path.closePath();
  L.track = { w: cv.width, h: cv.height, tx, ty, path };
  return L.track;
}

function drawMap() {
  const cv = $("pw-map"); if (!cv || !L.meta) return;
  const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); L.track = null; }
  const g = cv.getContext("2d");
  g.clearRect(0, 0, cv.width, cv.height);
  const tg = trackGeom(cv);
  if (!tg) { g.fillStyle = "#8b93a3"; g.font = `${14 * dpr}px "Titillium Web", sans-serif`; g.fillText("Waiting for position data…", 20 * dpr, 30 * dpr); return; }
  const st = L.snap?.status?.code || "1";
  const edge = { "4": "#ffb000", "6": "#ffb000", "7": "#ffb000", "5": "#ff2a2a", "2": "#ffd400" }[st];
  g.lineJoin = "round"; g.lineCap = "round";
  g.strokeStyle = "#0e1117"; g.lineWidth = 18 * dpr; g.stroke(tg.path);
  g.strokeStyle = edge ? mix(edge, "#1a1e26", 0.55) : "#2c323d"; g.lineWidth = 11 * dpr; g.stroke(tg.path);
  g.strokeStyle = "#3d4451"; g.lineWidth = 1.2 * dpr; g.setLineDash([6 * dpr, 8 * dpr]); g.stroke(tg.path); g.setLineDash([]);
  for (const c of L.meta.track.corners || []) {
    g.fillStyle = "#5b6370"; g.font = `${10 * dpr}px "Titillium Web", sans-serif`; g.fillText(c.label, tg.tx(c.x) + 7 * dpr, tg.ty(c.y) - 7 * dpr);
  }
  if (!L.snap) return;
  // interpolate positions between the last two snapshots for smooth motion
  const span = Math.max(1, L.snapAt - L.prevAt), f = clamp((performance.now() - L.snapAt) / span, 0, 1);
  const prev = Object.fromEntries((L.prevSnap?.drivers || []).map((d) => [d.code, d]));
  const cars = [...L.snap.drivers].reverse();
  for (const d of cars) {
    if (d.x == null || d.status === "OUT") continue;
    const p = prev[d.code];
    const x = p && p.x != null && L.playing ? p.x + (d.x - p.x) * f : d.x, y = p && p.y != null && L.playing ? p.y + (d.y - p.y) * f : d.y;
    const X = tg.tx(x), Y = tg.ty(y), foc = L.focus.includes(d.code);
    g.beginPath(); g.arc(X, Y, (foc ? 9 : 7) * dpr, 0, Math.PI * 2);
    g.fillStyle = d.in_pit ? mix(d.color, "#06070a", 0.55) : d.color; g.fill();
    g.lineWidth = (foc ? 3 : 2) * dpr; g.strokeStyle = foc ? "#ffffff" : "#06070a"; g.stroke();
    if (foc || d.pos <= 3) {
      g.font = `700 ${11 * dpr}px "Chakra Petch", "Titillium Web", sans-serif`; g.fillStyle = "#e9ecf1";
      g.fillText(`${d.pos} ${d.code}`, X + 11 * dpr, Y + 4 * dpr);
    }
  }
}

// ======================================================================= dashboards
function renderDash(s) {
  const el = $("pw-dash");
  if (!L.focus.length) { el.innerHTML = `<p class="hint">Click drivers in the timing tower to watch their telemetry (up to two).</p>`; return; }
  const rows = Object.fromEntries(s.drivers.map((d) => [d.code, d]));
  const have = [...el.querySelectorAll(".dash")].map((d) => d.dataset.code).join(",");
  if (have !== L.focus.join(",")) {
    el.innerHTML = L.focus.map((c) => `<div class="dash" data-code="${c}" style="--c:${rows[c]?.color || "#888"}">
      <div class="ds-head"><b class="ds-code">${c}</b><span class="ds-pos"></span><span class="ds-gap"></span><span class="ds-tyre"></span></div>
      <div class="ds-leds">${Array.from({ length: 15 }, (_, i) => `<i class="${i < 5 ? "g" : i < 10 ? "r" : "b"}"></i>`).join("")}</div>
      <div class="ds-main">
        <div class="ds-speed"><b>0</b><span>km/h</span></div>
        <div class="ds-gear"><b>N</b><span>gear</span></div>
        <div class="ds-pedals"><div><i class="thr"></i><span>THR</span></div><div><i class="brk"></i><span>BRK</span></div></div>
        <div class="ds-ers" title="Estimated from speed, throttle and brake — teams don't publish real battery data">
          <div class="batt"><i></i><span class="batt-pct">–</span></div><span class="ers-mode">ERS est.</span>
        </div>
      </div>
      <div class="ds-meta"><span class="drs-badge">DRS</span><span class="ds-rpm"></span><span class="ds-lap"></span></div>
      <canvas class="ds-chart"></canvas>
    </div>`).join("");
  }
  for (const c of L.focus) {
    const r = rows[c], card = el.querySelector(`.dash[data-code="${c}"]`); if (!r || !card) continue;
    const f = s.focus?.[c] || {};
    card.querySelector(".ds-pos").textContent = `P${r.pos}`;
    card.querySelector(".ds-gap").textContent = r.pos === 1 ? "Leader" : r.gap != null ? `${fmtGap(r.gap)} s` : "";
    card.querySelector(".ds-tyre").innerHTML = r.compound ? `<i style="color:${TYRE[r.compound] || "#888"}">${r.compound[0]}</i> ${r.tyre_age ?? ""} laps` : "";
    card.querySelector(".ds-speed b").textContent = r.speed != null ? Math.round(r.speed) : "—";
    card.querySelector(".ds-gear b").textContent = r.gear ? Math.round(r.gear) : "N";
    card.querySelector(".thr").style.height = `${clamp(r.throttle ?? 0, 0, 100)}%`;
    card.querySelector(".brk").style.height = `${clamp(r.brake ?? 0, 0, 100)}%`;
    const rpm = r.rpm ?? 0, lit = Math.round(clamp((rpm - 9500) / 2600, 0, 1) * 15);
    card.querySelectorAll(".ds-leds i").forEach((led, i) => led.classList.toggle("on", i < lit));
    card.querySelector(".ds-rpm").textContent = r.rpm != null ? `${Math.round(r.rpm).toLocaleString()} rpm` : "";
    card.querySelector(".drs-badge").classList.toggle("on", !!r.drs);
    card.querySelector(".ds-lap").textContent = r.last ? `last ${fmtLap(r.last)}` : "";
    const e = f.ers;
    if (e) {
      const col = e.soc > 55 ? "#2bd96b" : e.soc > 25 ? "#ffd400" : "#ff5a5a";
      card.querySelector(".batt i").style.cssText = `height:${e.soc}%;background:${col}`;
      card.querySelector(".batt-pct").textContent = `${Math.round(e.soc)}%`;
      card.querySelector(".ers-mode").textContent = e.mode === 1 ? "DEPLOYING" : e.mode === -1 ? "HARVESTING" : "ERS est.";
      card.querySelector(".ers-mode").className = `ers-mode ${e.mode === 1 ? "dep" : e.mode === -1 ? "har" : ""}`;
    }
    drawSpark(card.querySelector(".ds-chart"), f.window, e?.trace, r.color);
  }
}

function drawSpark(cv, w, soc, color) {
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  if (cv.width !== Math.round(W * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  const g = cv.getContext("2d"); g.clearRect(0, 0, cv.width, cv.height);
  if (!w || !w.t || !w.t.length) return;
  const x = (t) => ((t + 30) / 30) * cv.width;
  const band = (vals, y0, h, col) => {
    g.fillStyle = col;
    for (let i = 1; i < vals.length; i++) if (vals[i] > 0) g.fillRect(x(w.t[i - 1]), y0 + h * (1 - vals[i] / 100), x(w.t[i]) - x(w.t[i - 1]) + 1, h * (vals[i] / 100));
  };
  const h = cv.height;
  band(w.throttle, h * 0.62, h * 0.18, "rgba(43,217,107,.55)");
  band(w.brake, h * 0.82, h * 0.16, "rgba(255,90,90,.75)");
  g.strokeStyle = color; g.lineWidth = 2 * dpr; g.beginPath();
  w.speed.forEach((v, i) => { const yy = h * 0.58 * (1 - (v ?? 0) / 360) + 2 * dpr; i ? g.lineTo(x(w.t[i]), yy) : g.moveTo(x(w.t[i]), yy); });
  g.stroke();
  if (soc && soc.t.length) {
    g.strokeStyle = "#ffd400"; g.setLineDash([4 * dpr, 3 * dpr]); g.lineWidth = 1.5 * dpr; g.beginPath();
    soc.soc.forEach((v, i) => { const yy = h * 0.58 * (1 - v / 100) + 2 * dpr; i ? g.lineTo(x(soc.t[i]), yy) : g.moveTo(x(soc.t[i]), yy); });
    g.stroke(); g.setLineDash([]);
  }
  g.fillStyle = "#565e6c"; g.font = `${9 * dpr}px JetBrains Mono, monospace`;
  g.fillText("−30 s", 3 * dpr, h - 3 * dpr); g.fillText("speed · ERS (dashed) · throttle · brake", cv.width * 0.3, h - 3 * dpr);
}

// ======================================================================= strategy
function renderStrategy(st) {
  renderPitCosts(st);
  renderDeg(st);
  renderRejoin(st);
  renderBattles(st);
  renderWindow(st);
}

function renderPitCosts(st) {
  const pl = st.pit_loss, mode = st.mode;
  const card = (k, label, sub) => `<div class="pc ${k} ${mode === k ? "now" : ""}"><span>${label}</span><b>${pl[k].toFixed(1)}<small>s</small></b><em>${sub}</em></div>`;
  $("pw-costs").innerHTML = `
    ${card("green", "Green flag", pl.source)}
    ${card("vsc", "Under VSC", `saves ${st.cheap_stop_saving.vsc.toFixed(1)} s`)}
    ${card("sc", "Under safety car", `saves ${st.cheap_stop_saving.sc.toFixed(1)} s`)}`;
  const hint = mode === "sc" ? `🚨 <b>Safety car:</b> a stop now costs ~${pl.sc.toFixed(1)} s instead of ${pl.green.toFixed(1)} s. Staying out while rivals stop hands them ~${st.cheap_stop_saving.sc.toFixed(1)} s.`
    : mode === "vsc" ? `🟡 <b>VSC:</b> a stop now costs ~${pl.vsc.toFixed(1)} s — ${st.cheap_stop_saving.vsc.toFixed(1)} s cheaper than under green.`
    : `Green flag: a stop costs ~${pl.green.toFixed(1)} s (time lost over in-lap + out-lap vs a normal lap).`;
  $("pw-cost-hint").innerHTML = hint;
}

function renderDeg(st) {
  const deg = st.degradation, comps = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"].filter((c) => deg.compounds[c]);
  const traces = [];
  for (const c of comps) {
    const col = TYRE[c] || "#888", pts = deg.points[c] || [];
    if (pts.length) traces.push({ x: pts.map((p) => p[0]), y: pts.map((p) => p[1]), mode: "markers", type: "scatter", name: c.toLowerCase(),
      marker: { color: col, size: 6, opacity: 0.55, line: { width: 0 } }, text: pts.map((p) => p[2]), hovertemplate: "%{text} · age %{x}: +%{y:.2f}s<extra></extra>", showlegend: false });
    const maxA = Math.max(25, ...pts.map((p) => p[0]));
    traces.push({ x: [1, maxA], y: [0, deg.compounds[c].deg * (maxA - 1)], mode: "lines", type: "scatter", name: `${c.toLowerCase()} ${deg.compounds[c].deg.toFixed(3)} s/lap`,
      line: { color: col, width: 3, dash: deg.compounds[c].source === "prior" ? "dot" : "solid" }, hoverinfo: "skip" });
  }
  Plotly.react($("pw-deg"), traces, {
    ...PLOT_BASE, margin: { l: 46, r: 10, t: 10, b: 36 }, showlegend: true, legend: { orientation: "h", y: 1.1, x: 0, font: { size: 11 } },
    xaxis: axis({ title: axisTitle("tyre age (laps)"), rangemode: "tozero" }), yaxis: axis({ title: axisTitle("time lost vs fresh tyre (s)"), zeroline: true }),
  }, { ...PLOT_CFG, displayModeBar: false });
  $("pw-deg-table").innerHTML = `<tr><th>Compound</th><th>Deg s/lap</th><th>Pace vs medium</th><th>Laps of data</th><th>Basis</th></tr>` +
    comps.map((c) => { const d = deg.compounds[c]; return `<tr><td><i class="tyre-dot" style="color:${TYRE[c] || "#888"}">${c[0]}</i> ${c.toLowerCase()}</td>
      <td>${d.deg.toFixed(3)}</td><td>${d.pace > 0 ? "+" : ""}${d.pace.toFixed(2)} s</td><td>${d.n}</td><td class="l">${d.source}</td></tr>`; }).join("");
}

function renderRejoin(st) {
  const sel = $("pw-rejoin-driver"), codes = Object.keys(st.drivers);
  if (sel.dataset.sig !== codes.join(",")) {
    sel.innerHTML = codes.map((c) => `<option value="${c}">${c}</option>`).join(""); sel.dataset.sig = codes.join(",");
  }
  if (!codes.includes(L.rejoinDriver)) L.rejoinDriver = codes[0];
  sel.value = L.rejoinDriver;
  const d = st.drivers[L.rejoinDriver]; if (!d) return;
  const me = L.snap?.drivers.find((r) => r.code === L.rejoinDriver);
  const line = (k, label) => {
    const r = d.rejoin[k]; if (!r) return "";
    return `<div class="rj ${k} ${st.mode === k ? "now" : ""}"><span class="rj-k">${label}</span>
      <span class="rj-pos">P${me?.pos ?? "?"} → <b>P${r.pos}</b>${r.places_lost > 0 ? ` <em>−${r.places_lost}</em>` : ""}</span>
      <span class="rj-txt">${r.ahead ? `${r.gap_ahead.toFixed(1)} s behind <b>${r.ahead}</b>` : "leads the race"}${r.behind ? ` · ${r.gap_behind.toFixed(1)} s ahead of <b>${r.behind}</b>` : ""}</span>
      ${r.traffic ? `<span class="rj-warn">traffic</span>` : r.behind && r.gap_behind < 1.0 ? `<span class="rj-warn">under attack</span>` : ""}</div>`;
  };
  $("pw-rejoin").innerHTML = line("green", "Green") + line("vsc", "VSC") + line("sc", "Safety car") +
    `<div class="rj-note">Next tyre: <b style="color:${TYRE[d.next_compound]}">${d.next_compound.toLowerCase()}</b> · current deg ${d.deg.toFixed(3)} s/lap · next laps ${d.next_laps.slice(0, 3).map((v) => fmtLap(v)).join(", ")}</div>`;
  drawLadder(st, d);
}

function drawLadder(st, d) {
  const el = $("pw-ladder"); if (!L.snap) return;
  const rows = L.snap.drivers.filter((r) => r.status === "RUN" || r.status === "PIT");
  const gap = (r) => (r.pos === rows[0].pos ? 0 : r.gap ?? null);
  const me = rows.find((r) => r.code === L.rejoinDriver); if (!me) { el.innerHTML = ""; return; }
  const proj = ["green", "vsc", "sc"].map((k) => d.rejoin[k] && { k, g: d.rejoin[k].projected_gap });
  const maxG = Math.max(10, ...rows.map(gap).filter((v) => v != null && v < 200), ...proj.filter(Boolean).map((p) => p.g)) * 1.05;
  const W = 1000, H = 120, x = (g) => 30 + (g / maxG) * (W - 60);
  let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><line x1="30" y1="60" x2="${W - 30}" y2="60" class="ld-axis"/>`;
  for (let s = 0; s <= maxG; s += maxG > 60 ? 10 : 5) svg += `<line x1="${x(s)}" x2="${x(s)}" y1="56" y2="64" class="ld-tick"/><text x="${x(s)}" y="80" class="ld-num">${s}s</text>`;
  const colors = { green: "#2bd96b", vsc: "#ffd400", sc: "#ffb000" };
  for (const p of proj) if (p && p.g < maxG) svg += `<line x1="${x(gap(me) ?? 0)}" x2="${x(p.g)}" y1="60" y2="60" stroke="${colors[p.k]}" stroke-width="2" stroke-dasharray="4 4" opacity=".6"/>
      <circle cx="${x(p.g)}" cy="60" r="9" fill="none" stroke="${colors[p.k]}" stroke-width="2.5" stroke-dasharray="3 2"/><text x="${x(p.g)}" y="104" class="ld-proj" fill="${colors[p.k]}">${p.k.toUpperCase()}</text>`;
  rows.forEach((r, i) => {
    const g = gap(r); if (g == null || g > maxG) return;
    const up = i % 2 === 0;
    svg += `<circle cx="${x(g)}" cy="60" r="${r.code === me.code ? 9 : 6}" fill="${r.color}" stroke="${r.code === me.code ? "#fff" : "#06070a"}" stroke-width="2"/>
      <text x="${x(g)}" y="${up ? 40 : 30}" class="ld-code" fill="${r.color}">${r.code}</text>`;
  });
  el.innerHTML = svg + "</svg>";
}

function renderBattles(st) {
  const el = $("pw-battles");
  if (!st.battles.length) { el.innerHTML = `<p class="hint">No close battles right now (within 8 s on the same lap).</p>`; return; }
  const col = (c) => L.meta.drivers.find((d) => d.code === c)?.color || "#888";
  el.innerHTML = st.battles.map((b) => {
    const u = b.undercut.slice(0, 3), o = b.overcut.slice(0, 3);
    const cell = (v) => `<td class="${v.gap_after < 0 ? "good" : ""}">${v.gap_after > 0 ? "+" : ""}${v.gap_after.toFixed(1)}</td>`;
    return `<div class="bt ${b.tone}">
      <div class="bt-head"><b style="color:${col(b.behind)}">${b.behind}</b><span>chasing</span><b style="color:${col(b.ahead)}">${b.ahead}</b>
        <span class="bt-gap">${b.gap.toFixed(2)} s</span><span class="bt-verdict">${esc(b.verdict)}</span></div>
      <div class="bt-tyres">${b.behind}: <i style="color:${TYRE[b.tyres.behind[0]]}">${b.tyres.behind[0]?.[0] ?? "?"}</i>${b.tyres.behind[1]} → ${b.new.behind[0]} ·
        ${b.ahead}: <i style="color:${TYRE[b.tyres.ahead[0]]}">${b.tyres.ahead[0]?.[0] ?? "?"}</i>${b.tyres.ahead[1]} · fresh-tyre gain ${b.fresh_tyre_gain.toFixed(2)} s/lap</div>
      <table class="bt-t"><tr><th>gap after both stop</th><th>+1 lap</th><th>+2</th><th>+3</th></tr>
        <tr><td class="l">Undercut — ${b.behind} pits now, ${b.ahead} reacts</td>${u.map(cell).join("")}</tr>
        <tr><td class="l">Overcut — ${b.ahead} pits now, ${b.behind} stays out</td>${o.map(cell).join("")}</tr></table>
    </div>`;
  }).join("");
}

function renderWindow(st) {
  const el = $("pw-window");
  const d = st.drivers[L.rejoinDriver];
  const pw = d?.pit_window;
  if (!pw || !pw.best) { Plotly.purge(el); el.innerHTML = `<p class="hint" style="padding:30px">Not enough laps left to plan a stop.</p>`; $("pw-window-txt").textContent = ""; return; }
  el.querySelector("p")?.remove();
  const traces = Object.entries(pw.curves).map(([c, pts]) => ({ x: pts.map((p) => p[0] + 1), y: pts.map((p) => p[1]), type: "scatter", mode: "lines",
    name: c.toLowerCase(), line: { color: TYRE[c], width: 3 }, hovertemplate: `${c.toLowerCase()}: pit on lap %{x} → +%{y:.1f}s vs best<extra></extra>` }));
  const shapes = pw.no_stop != null ? [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: pw.no_stop, y1: pw.no_stop, line: { color: "#8b93a3", dash: "dot" } }] : [];
  Plotly.react(el, traces, {
    ...PLOT_BASE, margin: { l: 46, r: 10, t: 10, b: 36 }, showlegend: true, legend: { orientation: "h", y: 1.1, x: 0 },
    xaxis: axis({ title: axisTitle("pit on lap") }), yaxis: axis({ title: axisTitle("time vs best plan (s)"), rangemode: "tozero" }), shapes,
    annotations: [{ x: pw.best.lap + 1, y: 0, text: `best: lap ${pw.best.lap + 1} → ${pw.best.compound.toLowerCase()}`, showarrow: true, arrowcolor: "#e9ecf1", ay: -36, font: { color: "#e9ecf1", size: 11 } },
      ...(pw.no_stop != null ? [{ xref: "paper", x: 1, y: pw.no_stop, text: "no more stops", showarrow: false, xanchor: "right", yanchor: "bottom", font: { color: "#8b93a3", size: 10 } }] : [])],
  }, { ...PLOT_CFG, displayModeBar: false });
  $("pw-window-txt").innerHTML = `<b>${L.rejoinDriver}</b>: best plan is to pit at the end of <b>lap ${pw.best.lap + 1}</b> for <b style="color:${TYRE[pw.best.compound]}">${pw.best.compound.toLowerCase()}</b>` +
    (pw.two_compound_rule ? " (must still use a second dry compound)." : pw.best.vs_no_stop > 0 ? ` — but running to the flag is ${pw.best.vs_no_stop.toFixed(1)} s quicker.` : ` — ${(-pw.best.vs_no_stop).toFixed(1)} s quicker than not stopping.`) +
    ` Uses ${st.mode === "green" ? "green-flag" : st.mode.toUpperCase()} pit loss of ${st.pit_loss[st.mode].toFixed(1)} s.`;
}
