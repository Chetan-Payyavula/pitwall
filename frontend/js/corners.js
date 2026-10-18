/* Corners tab: one 3D coverflow slide per corner + the braking-zone table. */
import { $, state, colorOf, esc, fmtGap, clamp, PLOT_BASE, PLOT_CFG, axis, axisTitle } from "./util.js";
import { trackPath } from "./charts.js";

let idx = 0;
let slides = [];

export function renderCorners(d) {
  const cs = d.corner_stats || [];
  const cf = $("coverflow");
  cf.querySelectorAll(".sl-plot").forEach((p) => Plotly.purge(p));
  if (!cs.length) {
    cf.innerHTML = `<p class="hint" style="text-align:center;padding-top:200px">No corner information for this circuit.</p>`;
    $("cf-dots").innerHTML = ""; slides = [];
    renderBraking(d);
    return;
  }
  const ref = d.drivers[0].trace;
  const base = ref.X ? trackPath(ref.X, ref.Y, 100, 6, 3) : null;
  cf.innerHTML = cs.map((c, i) => slideHTML(c, i, d, base)).join("");
  slides = [...cf.querySelectorAll(".slide")];
  cs.forEach((c, i) => plotSlide(c, i, d));
  slides.forEach((el, i) => el.addEventListener("click", () => { if (i !== idx) go(i); }));
  $("cf-dots").innerHTML = cs.map((c, i) => `<button type="button" data-i="${i}">${esc(c.label)}</button>`).join("");
  $("cf-dots").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => go(+b.dataset.i)));
  idx = clamp(idx, 0, cs.length - 1);
  place();
  renderBraking(d);
}

function slideHTML(c, i, d, base) {
  const codes = d.drivers.map((x) => x.code);
  const best = codes.reduce((a, b) => (c.drivers[b].time < c.drivers[a].time ? b : a));
  const second = codes.filter((x) => x !== best).sort((a, b) => c.drivers[a].time - c.drivers[b].time)[0];
  const verdict = codes.length > 1
    ? `<div class="w" style="--c:${colorOf(best)}">${best} FASTEST</div><div class="d">${fmtGap(c.drivers[second].time - c.drivers[best].time)}s to ${second} · ${c.drivers[best].time.toFixed(3)}s through</div>`
    : `<div class="w" style="--c:${colorOf(best)}">${c.drivers[best].min.toFixed(0)} KM/H</div><div class="d">minimum speed · gear ${c.drivers[best].gear ?? "?"}</div>`;

  let map = "";
  if (base && c.x) {
    let hl = "";
    c.x.forEach((x, k) => { hl += (k ? "L" : "M") + base.tx(x).toFixed(1) + " " + base.ty(c.y[k]).toFixed(1); });
    const apex = d.corners.find((k) => k.label === c.label);
    const dot = apex ? `<circle cx="${base.tx(apex.x).toFixed(1)}" cy="${base.ty(apex.y).toFixed(1)}" r="3.2" fill="#fff"/>` : "";
    map = `<svg class="sl-map" viewBox="0 0 100 100"><path class="full" d="${base.d}"/><path class="hl" d="${hl}"/>${dot}</svg>`;
  }

  const metric = (k, better) => {
    const vals = codes.map((x) => c.drivers[x][k]).filter((v) => v != null);
    if (codes.length < 2 || !vals.length) return null;
    return better === "max" ? Math.max(...vals) : Math.min(...vals);
  };
  const bestMin = metric("min", "max"), bestBrake = metric("brake_at", "max"), bestExit = metric("exit", "max"), bestTime = metric("time", "min");
  const cell = (v, b, fmt) => `<td class="${b != null && v === b ? "good" : ""}">${v == null ? "—" : fmt(v)}</td>`;
  const rows = codes.map((x) => {
    const s = c.drivers[x];
    return `<tr><td style="color:${colorOf(x)}">${x}</td>
      <td>${s.entry.toFixed(0)}</td>${cell(s.min, bestMin, (v) => v.toFixed(0))}${cell(s.exit, bestExit, (v) => v.toFixed(0))}
      <td>${s.gear ?? "—"}</td>${cell(s.brake_at, bestBrake, (v) => v.toFixed(0))}${cell(s.time, bestTime, (v) => v.toFixed(3))}
      <td class="${s.delta > 0 ? "bad" : s.delta < 0 ? "good" : ""}">${x === d.reference ? "ref" : fmtGap(s.delta)}</td></tr>`;
  }).join("");

  return `<article class="slide" data-i="${i}">
    <header class="sl-head">
      <div class="sl-label">${esc(c.label)}<small>${Math.round(c.distance).toLocaleString()} m</small></div>
      <div class="sl-verdict">${verdict}</div>
    </header>
    <div class="sl-body">${map || "<div></div>"}<div class="sl-plot" id="cplot-${i}"></div></div>
    <table class="sl-table"><thead><tr><th>Driver</th><th>Entry</th><th>Min</th><th>Exit</th><th>Gear</th><th>Brake @ m</th><th>Time s</th><th>Δ</th></tr></thead>
    <tbody>${rows}</tbody></table>
  </article>`;
}

function plotSlide(c, i, d) {
  const traces = [];
  d.drivers.forEach((x) => {
    const s = c.drivers[x.code], col = colorOf(x.code);
    traces.push({ x: c.axis, y: s.speed, type: "scatter", mode: "lines", line: { color: col, width: 2.5, shape: "spline", smoothing: 0.5 }, name: x.code,
      hovertemplate: `<b>${x.code}</b> %{y:.0f} km/h @ %{x:.0f} m<extra></extra>` });
    traces.push({ x: [s.min_at], y: [s.min], type: "scatter", mode: "markers+text", marker: { color: col, size: 9, line: { color: "#06070a", width: 2 } },
      text: [s.min.toFixed(0)], textposition: "bottom center", textfont: { color: col, size: 10, family: "JetBrains Mono, Consolas, monospace" }, hoverinfo: "skip" });
    if (s.brake_at != null) {
      const y = s.speed[Math.max(0, c.axis.findIndex((v) => v >= s.brake_at))];
      traces.push({ x: [s.brake_at], y: [y], type: "scatter", mode: "markers", marker: { symbol: "triangle-down", color: col, size: 10 },
        hovertemplate: `<b>${x.code}</b> brakes at %{x:.0f} m<extra></extra>` });
    }
  });
  Plotly.newPlot(`cplot-${i}`, traces, {
    ...PLOT_BASE, margin: { l: 40, r: 8, t: 8, b: 30 }, hovermode: "closest",
    xaxis: axis({ ticksuffix: " m", fixedrange: true }), yaxis: axis({ title: axisTitle("km/h"), fixedrange: true }),
    shapes: [{ type: "line", x0: c.distance, x1: c.distance, yref: "paper", y0: 0, y1: 1, line: { color: "#4a5160", dash: "dot", width: 1 } }],
  }, { ...PLOT_CFG, displayModeBar: false });
}

function place() {
  slides.forEach((el, i) => {
    const o = i - idx, a = Math.abs(o);
    el.style.transform = `translateX(${o * 60}%) translateZ(${-a * 200}px) rotateY(${o === 0 ? 0 : -Math.sign(o) * 32}deg) scale(${a ? 0.88 : 1})`;
    el.style.zIndex = String(100 - a);
    el.style.opacity = a > 2 ? "0" : "1";
    el.style.visibility = a > 3 ? "hidden" : "visible";
    el.classList.toggle("active", o === 0);
  });
  $("cf-dots").querySelectorAll("button").forEach((b, i) => b.classList.toggle("on", i === idx));
}

export function go(i) {
  if (!slides.length) return;
  idx = clamp(i, 0, slides.length - 1);
  place();
}

export function bindCorners() {
  $("cf-prev").addEventListener("click", () => go(idx - 1));
  $("cf-next").addEventListener("click", () => go(idx + 1));
  window.addEventListener("keydown", (e) => {
    if (state.tab !== "corners" || e.target.closest("input, select, textarea")) return;
    if (e.key === "ArrowLeft") go(idx - 1);
    if (e.key === "ArrowRight") go(idx + 1);
  });
  let sx = null;
  const cf = $("coverflow");
  cf.addEventListener("pointerdown", (e) => { sx = e.clientX; });
  cf.addEventListener("pointerup", (e) => {
    if (sx == null) return;
    const dx = e.clientX - sx; sx = null;
    if (Math.abs(dx) > 60) go(idx + (dx < 0 ? 1 : -1));
  });
}

// ------------------------------------------------------------- braking table
function renderBraking(d) {
  const tbl = $("braking");
  if (!d.braking.length) { tbl.innerHTML = `<tr><td>No braking zones detected.</td></tr>`; return; }
  const codes = d.drivers.map((x) => x.code);
  const cols = [["start", "Brake pt", "m"], ["entry_speed", "Entry", "km/h"], ["min_speed", "Min", "km/h"], ["duration", "Time", "s"]];
  const head1 = `<tr><th rowspan="2">Corner</th>${codes.map((c) => `<th class="drv" colspan="${cols.length}" style="--c:${colorOf(c)};text-align:center">${c}</th>`).join("")}</tr>`;
  const head2 = `<tr>${codes.map(() => cols.map(([, l, u]) => `<th>${l} <span class="sub">${u}</span></th>`).join("")).join("")}</tr>`;
  const body = d.braking.map((row) => {
    const present = codes.filter((c) => row.drivers[c]);
    const best = {};
    if (present.length > 1) {
      best.start = present.reduce((a, b) => (row.drivers[b].start > row.drivers[a].start ? b : a));
      best.min_speed = present.reduce((a, b) => (row.drivers[b].min_speed > row.drivers[a].min_speed ? b : a));
    }
    const cells = codes.map((c) => {
      const z = row.drivers[c];
      if (!z) return cols.map(() => `<td class="na">—</td>`).join("");
      return cols.map(([k]) => `<td class="${best[k] === c ? "good" : ""}">${k === "duration" ? z[k].toFixed(2) : Math.round(z[k])}</td>`).join("");
    }).join("");
    return `<tr><td>${esc(row.corner || "—")} <span class="sub">${Math.round(row.at)} m</span></td>${cells}</tr>`;
  }).join("");
  tbl.innerHTML = `<thead>${head1}${head2}</thead><tbody>${body}</tbody>`;
}
