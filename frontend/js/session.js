/* Session tab: weather, classification, speed traps, ideal laps, and for races
   position chart, tyre strategy and race pace. Uses /api/overview only. */
import { $, state, colorOf, esc, fmtLap, fmtGap, fmtSec, emit, toast, TYRE, PURPLE, PLOT_BASE, PLOT_CFG, axis, axisTitle, mix } from "./util.js";

export function renderSession() {
  const ov = state.overview; if (!ov) return;
  const ok = [];
  const run = (name, fn) => { try { fn(); ok.push(true); } catch (e) { console.error(`[${name}]`, e); toast(`Couldn't draw ${name}: ${e.message}`); ok.push(false); } };
  run("the weather", () => renderWeather(ov));
  run("the classification", () => renderBoard(ov));
  run("the speed traps", () => renderTraps(ov));
  run("the ideal laps", () => renderIdeal(ov));
  const race = ov.is_race && ov.drivers.some((d) => d.laps.some((l) => l.position != null));
  $("race-block").hidden = !race;
  $("laps-block").hidden = race;
  if (race) { run("the position chart", () => renderPositions(ov)); run("the tyre strategy", () => renderStrategy(ov)); run("the race pace", () => renderPace(ov)); }
  else run("the lap times", () => renderLapScatter(ov));
  if (ok.includes(false)) throw new Error("some session charts failed");   // lets main.js retry next time
}

function renderWeather(ov) {
  const w = ov.weather;
  if (!w) { $("weather").innerHTML = ""; return; }
  const box = (i, label, val) => `<div class="wx"><div class="i">${i}</div><div><span>${label}</span><b>${val}</b></div></div>`;
  $("weather").innerHTML = [
    box("🌡", "Air temp", `${fmtSec(w.air, 1)}°C`),
    box("🛣", "Track temp", `${fmtSec(w.track, 1)}°C`),
    box("🔥", "Track peak", `${fmtSec(w.track_max, 1)}°C`),
    box("💧", "Humidity", `${fmtSec(w.humidity, 0)}%`),
    box("🌬", "Wind", `${fmtSec(w.wind, 1)} m/s`),
    box(w.rain ? "🌧" : "☀️", "Conditions", w.rain ? "Wet" : "Dry"),
  ].join("");
}

/** h:mm:ss.sss for a race time. */
function fmtRace(sec) {
  if (sec == null || !isFinite(sec)) return "—";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), r = sec - h * 3600 - m * 60;
  return `${h}:${String(m).padStart(2, "0")}:${r.toFixed(3).padStart(6, "0")}`;
}
const posCell = (r) => {
  const txt = r.classified || (r.position != null ? String(r.position) : "—");
  return `<span class="pos-badge ${/^\d+$/.test(txt) ? "" : "out"}">${esc(txt)}</span>`;
};
const drvCells = (r) => `<td class="drv-cell" style="--c:${colorOf(r.code)}"><i></i><b>${r.code}</b><span>${esc(r.name)}</span></td><td class="l">${esc(r.team)}</td>`;
const pts = (v) => (v == null || v === 0 ? "" : Number.isInteger(v) ? String(v) : v.toFixed(1));
const minOf = (xs) => Math.min(...xs.filter((x) => x != null && isFinite(x)));

/** Official order straight from the server: race/sprint/qualifying = classification, practice = best lap. */
function renderBoard(ov) {
  const rows = ov.drivers;
  const kind = ov.order !== "classification" ? "practice" : ov.is_race ? "race" : rows.some((r) => r.q) ? "quali" : "practice";
  const fastest = minOf(rows.map((r) => r.best));
  const bestSec = [0, 1, 2].map((i) => minOf(rows.map((r) => r.sectors[i])));
  const bestTrap = Math.max(...rows.map((r) => r.traps.ST ?? -Infinity));
  const bestIdeal = minOf(rows.map((r) => r.ideal));
  const sel = new Set(state.selected.map((s) => s.code));
  const purple = (cond) => (cond ? "purple" : "");
  const lapsDone = (r) => r.laps.reduce((m, l) => Math.max(m, l.lap ?? 0), 0);
  const secCells = (r) => r.sectors.map((v, k) => `<td class="${purple(v != null && v === bestSec[k])}">${v == null ? "—" : v.toFixed(3)}</td>`).join("");
  const trapCell = (r) => `<td class="${purple(r.traps.ST != null && r.traps.ST === bestTrap)}">${r.traps.ST != null ? r.traps.ST.toFixed(0) : "—"}</td>`;
  let head, body;

  if (kind === "race") {
    const winner = rows.find((r) => r.position === 1);
    head = ["Pos", "Driver", "Team", "Grid", "+/−", "Time / gap", "Pts", "Fastest lap", "Trap km/h", "Laps"];
    body = rows.map((r) => {
      let res;
      if (r === winner && r.race_time != null) res = fmtRace(r.race_time);
      else if (r.race_time != null) res = `+${r.race_time.toFixed(3)}`;
      else res = esc(r.status || "—");
      const moved = r.grid && r.position && /^\d+$/.test(r.classified || "") ? r.grid - r.position : null;
      const mv = moved == null ? "" : moved > 0 ? `<span class="good">▲${moved}</span>` : moved < 0 ? `<span class="bad">▼${-moved}</span>` : "–";
      return `<tr data-code="${r.code}" class="${sel.has(r.code) ? "sel" : ""}"><td>${posCell(r)}</td>${drvCells(r)}
        <td>${r.grid ? r.grid : "PL"}</td><td>${mv}</td><td>${res}</td><td>${pts(r.points)}</td>
        <td class="${purple(r.best != null && r.best === fastest)}">${fmtLap(r.best)}${r.best_lap && !ov.simulated ? ` <small>L${r.best_lap}</small>` : ""}</td>
        ${trapCell(r)}<td>${lapsDone(r) || "—"}</td></tr>`;
    }).join("");
    $("board-title").textContent = `${ov.session || "Race"} classification`;
    $("board-note").innerHTML = `official result${ov.simulated ? " (simulated)" : ""} · <b class="purple">purple</b> = fastest lap · click a row to add/remove the driver`;
  } else if (kind === "quali") {
    const bestQ = [0, 1, 2].map((i) => minOf(rows.map((r) => r.q?.[i])));
    const pole = rows.find((r) => r.position === 1)?.best ?? fastest;
    head = ["Pos", "Driver", "Team", "Q1", "Q2", "Q3", "Best lap", "Gap", "S1", "S2", "S3", "Ideal", "Trap km/h", "Laps"];
    body = rows.map((r) => {
      const q = [0, 1, 2].map((i) => { const v = r.q?.[i]; return `<td class="${purple(v != null && v === bestQ[i])}">${fmtLap(v)}</td>`; }).join("");
      const gap = r.best != null && pole != null && r.position !== 1 ? fmtGap(r.best - pole) : "";
      return `<tr data-code="${r.code}" class="${sel.has(r.code) ? "sel" : ""}"><td>${posCell(r)}</td>${drvCells(r)}${q}
        <td class="${purple(r.best != null && r.best === fastest)}">${fmtLap(r.best)}</td><td>${gap}</td>${secCells(r)}
        <td class="${purple(r.ideal != null && r.ideal === bestIdeal)}">${fmtLap(r.ideal)}</td>${trapCell(r)}<td>${r.laps.filter((l) => l.time != null).length}</td></tr>`;
    }).join("");
    $("board-title").textContent = `${ov.session || "Qualifying"} classification`;
    $("board-note").innerHTML = `official result · best lap = fastest valid lap (deleted laps never count) · <b class="purple">purple</b> = fastest · click a row to add/remove the driver`;
  } else {
    head = ["Pos", "Driver", "Team", "Best lap", "Gap", "S1", "S2", "S3", "Ideal", "Left on table", "Trap km/h", "Laps"];
    let n = 0;
    body = rows.map((r) => {
      const pos = r.best != null ? ++n : null;
      const gap = r.best != null && pos > 1 ? fmtGap(r.best - fastest) : "";
      const pot = r.best != null && r.ideal != null ? r.best - r.ideal : null;
      return `<tr data-code="${r.code}" class="${sel.has(r.code) ? "sel" : ""}"><td><span class="pos-badge">${pos ?? "—"}</span></td>${drvCells(r)}
        <td class="${purple(pos === 1)}">${fmtLap(r.best)}</td><td>${gap}</td>${secCells(r)}
        <td class="${purple(r.ideal != null && r.ideal === bestIdeal)}">${fmtLap(r.ideal)}</td>
        <td>${pot != null && pot > 0.0005 ? "−" + pot.toFixed(3) : "—"}</td>${trapCell(r)}<td>${r.laps.filter((l) => l.time != null).length}</td></tr>`;
    }).join("");
    $("board-title").textContent = "Timesheet";
    $("board-note").innerHTML = `ordered by best lap (deleted laps never count) · <b class="purple">purple</b> = fastest · click a row to add/remove the driver`;
  }
  $("board").innerHTML = `<thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${body}</tbody>`;
  $("board").querySelectorAll("tbody tr").forEach((tr) => tr.addEventListener("click", () => emit("toggle-driver", tr.dataset.code)));
}

export function markBoardSelection() {
  const sel = new Set(state.selected.map((s) => s.code));
  $("board").querySelectorAll("tbody tr").forEach((tr) => tr.classList.toggle("sel", sel.has(tr.dataset.code)));
}

function hbar(el, rows, valueKey, fmt, title, range) {
  const y = rows.map((r) => r.code), x = rows.map((r) => r[valueKey]);
  Plotly.react(el, [{
    type: "bar", orientation: "h", y, x, marker: { color: rows.map((r) => colorOf(r.code)) },
    text: x.map(fmt), textposition: "outside", cliponaxis: false, textfont: { family: "JetBrains Mono, Consolas, monospace", size: 11, color: "#c9ced6" },
    hovertemplate: "<b>%{y}</b> %{text}<extra></extra>",
  }], {
    ...PLOT_BASE, margin: { l: 46, r: 50, t: 6, b: 32 }, bargap: 0.25,
    xaxis: axis({ title: axisTitle(title), range, fixedrange: true }),
    yaxis: axis({ autorange: "reversed", fixedrange: true, tickfont: { family: "Chakra Petch, Titillium Web, sans-serif", size: 12, color: "#e9ecf1" } }),
  }, { ...PLOT_CFG, displayModeBar: false });
}

function renderTraps(ov) {
  const rows = ov.drivers.filter((d) => d.traps.ST != null).map((d) => ({ code: d.code, v: d.traps.ST })).sort((a, b) => b.v - a.v);
  if (!rows.length) { Plotly.purge($("traps")); $("traps").innerHTML = `<p class="hint">No speed-trap data.</p>`; return; }
  const lo = Math.min(...rows.map((r) => r.v)), hi = Math.max(...rows.map((r) => r.v));
  hbar($("traps"), rows, "v", (v) => v.toFixed(1), "km/h", [lo - (hi - lo) * 0.6 - 3, hi + (hi - lo) * 0.15 + 1]);
}

function renderIdeal(ov) {
  const rows = ov.drivers.filter((d) => d.best != null && d.ideal != null).sort((a, b) => a.ideal - b.ideal);
  if (!rows.length) { Plotly.purge($("ideal")); $("ideal").innerHTML = `<p class="hint">No sector data.</p>`; return; }
  const top = rows[0].ideal;
  Plotly.react($("ideal"), [
    { type: "bar", orientation: "h", name: "Ideal lap", y: rows.map((r) => r.code), x: rows.map((r) => r.ideal - top), marker: { color: rows.map((r) => colorOf(r.code)) },
      customdata: rows.map((r) => fmtLap(r.ideal)), hovertemplate: "<b>%{y}</b> ideal %{customdata} (+%{x:.3f})<extra></extra>" },
    { type: "bar", orientation: "h", name: "Time left on the table", y: rows.map((r) => r.code), x: rows.map((r) => Math.max(0, r.best - r.ideal)),
      marker: { color: rows.map((r) => mix(colorOf(r.code), "#0b0d12", 0.6)), pattern: { shape: "/", fgcolor: "#0b0d12", size: 5 } },
      customdata: rows.map((r) => fmtLap(r.best)), hovertemplate: "<b>%{y}</b> best lap %{customdata} · %{x:.3f}s from ideal<extra></extra>" },
  ], {
    ...PLOT_BASE, barmode: "stack", bargap: 0.25, margin: { l: 46, r: 14, t: 6, b: 32 }, showlegend: true,
    legend: { orientation: "h", y: -0.12, font: { size: 11 } },
    xaxis: axis({ title: axisTitle(`seconds to best ideal lap (${fmtLap(top)})`), fixedrange: true, rangemode: "tozero" }),
    yaxis: axis({ autorange: "reversed", fixedrange: true, tickfont: { family: "Chakra Petch, Titillium Web, sans-serif", size: 12, color: "#e9ecf1" } }),
  }, { ...PLOT_CFG, displayModeBar: false });
}

const focusCodes = (ov, n) => {
  const sel = state.selected.map((s) => s.code).filter((c) => ov.drivers.some((d) => d.code === c));
  return sel.length ? sel : ov.drivers.slice(0, n).map((d) => d.code);   // official order
};

function renderPositions(ov) {
  const focus = new Set(focusCodes(ov, 0));
  const traces = ov.drivers.map((d) => {
    const L = d.laps.filter((l) => l.position != null);
    const on = !focus.size || focus.has(d.code);
    return { x: L.map((l) => l.lap), y: L.map((l) => l.position), type: "scatter", mode: "lines+markers", name: d.code,
      line: { color: colorOf(d.code), width: on ? 3 : 1.5, shape: "spline", smoothing: 0.4 }, opacity: on ? 1 : 0.28,
      marker: { size: L.map((l) => (l.pit ? 9 : 0)), symbol: "diamond", color: colorOf(d.code), line: { color: "#fff", width: 1 } },
      hovertemplate: `<b>${d.code}</b> lap %{x}: P%{y}<extra></extra>` };
  });
  const n = ov.drivers.length;
  const last = ov.drivers.map((d) => { const L = d.laps.filter((l) => l.position != null); return L.length ? { code: d.code, lap: L.at(-1).lap, pos: L.at(-1).position } : null; }).filter(Boolean);
  Plotly.react($("positions"), traces, {
    ...PLOT_BASE, margin: { l: 40, r: 60, t: 10, b: 36 }, hovermode: "closest",
    xaxis: axis({ title: axisTitle("lap"), fixedrange: false }),
    yaxis: axis({ dtick: 1, range: [n + 0.5, 0.5], tickprefix: "P", fixedrange: true }),
    annotations: last.map((l) => ({ x: l.lap, y: l.pos, text: l.code, xanchor: "left", xshift: 8, showarrow: false, font: { color: colorOf(l.code), family: "Chakra Petch, Titillium Web, sans-serif", size: 12 } })),
  }, PLOT_CFG);
  $("race-note").textContent = `${ov.simulated ? "simulated race · " : ""}◆ = pit stop · highlighted: ${[...focus].join(", ") || "all"}`;
}

function renderStrategy(ov) {
  const order = ov.order === "classification" ? ov.drivers
    : [...ov.drivers].sort((a, b) => (a.laps.at(-1)?.position ?? 99) - (b.laps.at(-1)?.position ?? 99));
  const traces = [], seen = new Set();
  for (const d of order) {
    const stints = [];
    for (const l of d.laps) {
      const key = `${l.stint}-${l.compound}`;
      const s = stints.at(-1);
      if (!s || s.key !== key) stints.push({ key, compound: l.compound || "UNKNOWN", start: l.lap, end: l.lap });
      else s.end = l.lap;
    }
    for (const s of stints) {
      const c = TYRE[s.compound] || "#8b93a3";
      traces.push({ type: "bar", orientation: "h", y: [d.code], x: [s.end - s.start + 1], base: [s.start - 1], name: s.compound,
        showlegend: !seen.has(s.compound), marker: { color: c, line: { color: "#06070a", width: 2 } },
        text: [`${s.compound[0]} · ${s.end - s.start + 1}`], textposition: "inside", insidetextanchor: "middle", textfont: { color: "#06070a", size: 11, family: "Chakra Petch, Titillium Web, sans-serif" },
        hovertemplate: `<b>${d.code}</b> ${s.compound}: laps ${s.start}–${s.end}<extra></extra>` });
      seen.add(s.compound);
    }
  }
  Plotly.react($("strategy"), traces, {
    ...PLOT_BASE, barmode: "overlay", bargap: 0.3, margin: { l: 46, r: 10, t: 6, b: 36 },
    xaxis: axis({ title: axisTitle("lap"), fixedrange: true }),
    yaxis: axis({ autorange: "reversed", fixedrange: true, categoryorder: "array", categoryarray: order.map((d) => d.code), tickfont: { family: "Chakra Petch, Titillium Web, sans-serif", size: 12, color: "#e9ecf1" } }),
  }, { ...PLOT_CFG, displayModeBar: false });
  $("tyre-legend").innerHTML = [...seen].map((c) => `<span><i style="--c:${TYRE[c] || "#8b93a3"}"></i>${c.toLowerCase()}</span>`).join("");
}

function cleanLaps(d) {
  const L = d.laps.filter((l) => l.time != null && !l.pit && l.lap > 1);
  if (!L.length) return [];
  const med = [...L].sort((a, b) => a.time - b.time)[Math.floor(L.length / 2)].time;
  const pitOut = new Set(d.laps.filter((l) => l.pit).map((l) => l.lap + 1));
  return L.filter((l) => l.time < med * 1.07 && !pitOut.has(l.lap));
}

function renderPace(ov) {
  const codes = focusCodes(ov, 5);
  const traces = codes.map((code) => {
    const d = ov.drivers.find((x) => x.code === code); const L = cleanLaps(d);
    return { x: L.map((l) => l.lap), y: L.map((l) => l.time), type: "scatter", mode: "lines+markers", name: code,
      line: { color: colorOf(code), width: 2 }, marker: { size: 7, color: L.map((l) => TYRE[l.compound] || "#888"), line: { color: colorOf(code), width: 1.5 } },
      customdata: L.map((l) => [fmtLap(l.time), l.compound, l.tyre_life]),
      hovertemplate: `<b>${code}</b> lap %{x}: %{customdata[0]}<br>%{customdata[1]} · %{customdata[2]} laps old<extra></extra>` };
  });
  Plotly.react($("pace"), traces, {
    ...PLOT_BASE, margin: { l: 56, r: 10, t: 10, b: 36 }, showlegend: true, legend: { orientation: "h", y: 1.06, x: 1, xanchor: "right" },
    xaxis: axis({ title: axisTitle("lap") }), yaxis: axis({ title: axisTitle("lap time s") }),
  }, PLOT_CFG);
}

function renderLapScatter(ov) {
  const codes = focusCodes(ov, 6);
  const pole = Math.min(...ov.drivers.map((d) => d.best ?? Infinity));
  const traces = codes.map((code, i) => {
    const d = ov.drivers.find((x) => x.code === code);
    const L = d.laps.filter((l) => l.time != null && l.time < pole * 1.07);
    return { x: L.map((l) => l.lap + (i - codes.length / 2) * 0.06), y: L.map((l) => l.time), type: "scatter", mode: "markers", name: code,
      marker: { size: 10, color: L.map((l) => TYRE[l.compound] || "#888"), line: { color: colorOf(code), width: 2.5 } },
      customdata: L.map((l) => [l.lap, fmtLap(l.time), l.compound]),
      hovertemplate: `<b>${code}</b> lap %{customdata[0]}: %{customdata[1]} · %{customdata[2]}<extra></extra>` };
  });
  Plotly.react($("lapscatter"), traces, {
    ...PLOT_BASE, margin: { l: 56, r: 10, t: 10, b: 36 }, showlegend: true, legend: { orientation: "h", y: 1.06, x: 1, xanchor: "right" },
    xaxis: axis({ title: axisTitle("lap number") }), yaxis: axis({ title: axisTitle("lap time s") }),
  }, PLOT_CFG);
}
