/* Overview tab (hero, cards, insights, sector gaps, maps, gauges) and the
   Telemetry tab (stacked traces, live readout, G-G, gears, speed histogram). */
import {
  $, state, colorOf, esc, fmtLap, fmtGap, fmtSec, nearest, interp, fillNulls, sum, mix,
  PLOT_BASE, PLOT_CFG, axis, axisTitle, SPEED_STOPS, TYRE, PURPLE,
} from "./util.js";

// ============================================================ shared geometry
/** SVG path for a track outline, normalised into a size×size box (y flipped). */
export function trackPath(xs, ys, size = 100, pad = 6, step = 1) {
  const X = fillNulls(xs), Y = fillNulls(ys);
  const minX = Math.min(...X), maxX = Math.max(...X), minY = Math.min(...Y), maxY = Math.max(...Y);
  const span = Math.max(maxX - minX, maxY - minY) || 1;
  const k = (size - pad * 2) / span;
  const ox = pad + (size - pad * 2 - (maxX - minX) * k) / 2, oy = pad + (size - pad * 2 - (maxY - minY) * k) / 2;
  const tx = (x) => ox + (x - minX) * k, ty = (y) => size - (oy + (y - minY) * k);
  let d = "";
  for (let i = 0; i < X.length; i += step) d += (d ? "L" : "M") + tx(X[i]).toFixed(1) + " " + ty(Y[i]).toFixed(1);
  return { d: d + "Z", tx, ty };
}

// ============================================================ hero
export function renderHero() {
  const ov = state.overview; if (!ov) { $("hero").hidden = true; return; }
  $("hero").hidden = !["overview", "session"].includes(state.tab);
  const yr = state.year === "0" ? "DEMO" : state.year;
  const ev = state.schedule.find((e) => String(e.round) === String(state.event));
  $("hero-kicker").textContent = [yr, ev ? `Round ${ev.round}` : null, ov.country].filter(Boolean).join(" · ");
  $("hero-title").textContent = ov.event;
  const w = ov.weather;
  const chips = [
    `<span class="hchip">${esc(ov.session)}${ov.simulated ? " · simulated" : ""}</span>`,
    ov.location ? `<span class="hchip">📍 ${esc(ov.location)}</span>` : "",
    ov.date ? `<span class="hchip">📅 ${esc(ov.date)}</span>` : "",
    w ? `<span class="hchip">🌡 Air <b>${fmtSec(w.air, 1)}°</b> · Track <b>${fmtSec(w.track, 1)}°</b></span>` : "",
    w ? `<span class="hchip">${w.rain ? "🌧 Wet running" : "☀️ Dry"}</span>` : "",
    `<span class="hchip">🏎 <b>${ov.drivers.length}</b> drivers</span>`,
  ];
  $("hero-chips").innerHTML = chips.join("");
  const svg = $("hero-track");
  const d = state.data;
  if (d && d.drivers[0].trace.X) {
    const p = trackPath(d.drivers[0].trace.X, d.drivers[0].trace.Y, 100, 4, 2);
    svg.innerHTML = `<path d="${p.d}"/><path class="glow" d="${p.d}" pathLength="312"/>`;
    svg.style.display = "";
  } else svg.style.display = "none";
}

// ============================================================ cards
export function renderCards(d) {
  const best = [0, 1, 2].map((i) => Math.min(...d.drivers.map((x) => x.lap.sectors?.[i] ?? Infinity)));
  $("cards").innerHTML = d.drivers.map((x) => {
    const c = colorOf(x.code), s = x.summary;
    const tyre = x.lap.compound
      ? `<span class="tyre" style="color:${TYRE[x.lap.compound] || "#aaa"}" title="${esc(x.lap.compound)}, ${x.lap.tyre_life ?? "?"} laps old">${x.lap.compound[0]}</span>` : "";
    const gap = x.gap == null ? `<span class="gap ref">reference · lap ${x.lap.lap}</span>`
      : `<span class="gap ${x.gap > 0 ? "pos" : "neg"}">${fmtGap(x.gap)} s · lap ${x.lap.lap}</span>`;
    const secs = (x.lap.sectors || []).map((v, i) =>
      `<span class="${v != null && v === best[i] && d.drivers.length > 1 ? "best" : ""}">S${i + 1} ${v == null ? "—" : v.toFixed(3)}</span>`).join("");
    const num = ov_number(x.code);
    return `<div class="card" style="--c:${c}">
      ${num != null ? `<div class="bignum">${num}</div>` : ""}<div class="shine"></div>
      <div class="who"><span class="code">${x.code}</span><span class="name">${esc(x.name)} · ${esc(x.team)}</span></div>
      <div class="time">${fmtLap(x.lap.time)}${tyre}</div>${gap}
      <div class="secs">${secs}</div>
      ${x.lap.substitute_for ? `<div class="subnote" title="The source's car data for that lap is incomplete">Fastest lap ${esc(x.lap.substitute_for.time_str)} (lap ${x.lap.substitute_for.lap}) has broken telemetry in the source data · showing lap ${x.lap.lap}</div>` : ""}
      ${x.lap.map === "reference" ? `<div class="subnote" title="Speed, throttle, brake and gears are this lap's own data">Car positions are missing in the source data for this lap · map position follows the session's reference lap</div>` : ""}
      <div class="stats">
        <div class="stat"><span>Top speed</span><b>${s.top_speed}</b><i style="--w:${pct(s.top_speed, 280, 360)}%"></i></div>
        <div class="stat"><span>Full throttle</span><b>${s.full_throttle_pct ?? "—"}%</b><i style="--w:${s.full_throttle_pct ?? 0}%"></i></div>
        <div class="stat"><span>Braking</span><b>${s.braking_pct ?? "—"}%</b><i style="--w:${Math.min(100, (s.braking_pct ?? 0) * 4)}%"></i></div>
      </div></div>`;
  }).join("");
}
const pct = (v, lo, hi) => Math.max(4, Math.min(100, ((v - lo) / (hi - lo)) * 100));
const ov_number = (code) => state.overview?.drivers.find((d) => d.code === code)?.number ?? null;

/** Mouse-follow 3D tilt for the driver cards. */
export function bindTilt(container) {
  container.addEventListener("mousemove", (e) => {
    const card = e.target.closest(".card"); if (!card) return;
    const r = card.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
    card.style.transform = `perspective(900px) rotateX(${(0.5 - py) * 10}deg) rotateY(${(px - 0.5) * 14}deg) translateZ(6px)`;
    card.style.setProperty("--mx", `${px * 100}%`); card.style.setProperty("--my", `${py * 100}%`);
  });
  container.addEventListener("mouseout", (e) => {
    const card = e.target.closest(".card");
    if (card && !card.contains(e.relatedTarget)) card.style.transform = "";
  });
}

// ============================================================ insights
export function renderInsights(d) {
  const items = [];
  const add = (ic, code, html) => items.push(`<li style="--c:${colorOf(code)}"><span class="ic">${ic}</span><span>${html}</span></li>`);
  const ref = d.drivers[0];
  const byTime = [...d.drivers].sort((a, b) => a.lap.time - b.lap.time);
  const B = (s) => `<b>${esc(s)}</b>`;

  if (d.drivers.length > 1) {
    const [p1, p2] = byTime;
    add("⏱", p1.code, `${B(p1.code)} has the quickest lap — ${B(fmtLap(p1.lap.time))}, ${B((p2.lap.time - p1.lap.time).toFixed(3) + "s")} clear of ${B(p2.code)}.`);
    for (const x of d.drivers.slice(1)) {
      const deltas = d.corner_stats.map((c) => ({ c, v: c.drivers[x.code]?.delta ?? 0 }));
      if (deltas.length) {
        const gain = deltas.reduce((a, b) => (b.v < a.v ? b : a));
        const loss = deltas.reduce((a, b) => (b.v > a.v ? b : a));
        if (gain.v < -0.005) {
          const dv = gain.c.drivers[x.code].min - gain.c.drivers[ref.code].min;
          add("📈", x.code, `${B(x.code)}'s strongest corner vs ${B(ref.code)} is ${B(gain.c.label)} (${B(fmtGap(gain.v) + "s")})${Math.abs(dv) >= 1 ? `, carrying ${B(Math.abs(dv).toFixed(0) + " km/h")} ${dv > 0 ? "more" : "less"} minimum speed` : ""}.`);
        }
        if (loss.v > 0.005) {
          add("📉", x.code, `${B(x.code)} loses the most to ${B(ref.code)} at ${B(loss.c.label)} (${B(fmtGap(loss.v) + "s")}).`);
        }
      }
      const both = d.braking.filter((r) => r.drivers[x.code] && r.drivers[ref.code]);
      if (both.length >= 2) {
        const avg = sum(both.map((r) => r.drivers[x.code].start - r.drivers[ref.code].start)) / both.length;
        if (Math.abs(avg) >= 1.5) add("🛑", x.code, `${B(x.code)} brakes on average ${B(Math.abs(avg).toFixed(0) + " m")} ${avg > 0 ? "later" : "earlier"} than ${B(ref.code)} over ${both.length} braking zones.`);
      }
      const dts = x.summary.top_speed - ref.summary.top_speed;
      if (Math.abs(dts) >= 2) add("💨", dts > 0 ? x.code : ref.code, `${B(dts > 0 ? x.code : ref.code)} is ${B(Math.abs(dts).toFixed(1) + " km/h")} quicker at the speed trap — ${dts > 0 ? "less drag or more power" : "a straight-line advantage"}.`);
    }
    if (d.dominance) {
      const top = Object.entries(d.dominance.share).sort((a, b) => b[1] - a[1])[0];
      if (top && top[0] !== byTime[0].code) add("🧩", top[0], `${B(top[0])} wins the most mini-sectors (${top[1]}/${d.dominance.sectors.length}) but not the lap — the margins elsewhere decide it.`);
    }
  } else {
    const t = ref.trace, s = ref.summary;
    const slow = d.corner_stats.length ? d.corner_stats.reduce((a, b) => (b.drivers[ref.code].min < a.drivers[ref.code].min ? b : a)) : null;
    if (slow) add("🐢", ref.code, `Slowest corner is ${B(slow.label)} at ${B(slow.drivers[ref.code].min.toFixed(0) + " km/h")} in gear ${B(slow.drivers[ref.code].gear ?? "?")}.`);
    const hb = [...ref.braking].sort((a, b) => b.speed_drop - a.speed_drop)[0];
    if (hb) add("🛑", ref.code, `Biggest stop: ${B(hb.entry_speed.toFixed(0))} → ${B(hb.min_speed.toFixed(0) + " km/h")} at ${B(hb.start.toFixed(0) + " m")}, ${B(hb.duration.toFixed(2) + "s")} on the brakes.`);
    add("💨", ref.code, `Top speed ${B(s.top_speed + " km/h")}, full throttle for ${B(s.full_throttle_pct + "%")} of the lap.`);
    const lat = Math.max(...fillNulls(t.LatG).map(Math.abs)), lon = Math.min(...fillNulls(t.LongG));
    add("🌀", ref.code, `Peak loads: ${B(lat.toFixed(1) + "g")} cornering, ${B(Math.abs(lon).toFixed(1) + "g")} braking.`);
    add("➕", ref.code, `Add a second driver to see where the time is won and lost.`);
  }
  $("insights").innerHTML = items.slice(0, 7).join("") || `<li>No notable differences.</li>`;
  [...$("insights").children].forEach((li, i) => (li.style.animationDelay = `${i * 70}ms`));
}

// ============================================================ sector bars
export function renderSectorBars(d) {
  const el = $("sector-bars");
  const has = d.drivers.every((x) => x.lap.sectors && x.lap.sectors.every((v) => v != null));
  if (!has) { Plotly.purge(el); el.innerHTML = `<p class="hint" style="padding:40px;text-align:center">Sector times aren't available for these laps.</p>`; return; }
  el.innerHTML = "";
  const best = [0, 1, 2].map((i) => Math.min(...d.drivers.map((x) => x.lap.sectors[i])));
  const traces = d.drivers.map((x) => {
    const gaps = x.lap.sectors.map((v, i) => +(v - best[i]).toFixed(3));
    return {
      type: "bar", name: x.code, x: ["Sector 1", "Sector 2", "Sector 3"], y: gaps,
      marker: { color: colorOf(x.code), line: { width: 0 } },
      text: gaps.map((g, i) => (g === 0 ? `★ ${x.lap.sectors[i].toFixed(3)}` : `+${g.toFixed(3)}`)), textposition: "outside",
      textfont: { family: "JetBrains Mono, Consolas, monospace", size: 10, color: gaps.map((g) => (g === 0 ? PURPLE : "#c9ced6")) }, cliponaxis: false,
      hovertemplate: `<b>${x.code}</b> %{x}<br>%{customdata:.3f}s<br>+%{y:.3f} to best<extra></extra>`, customdata: x.lap.sectors,
    };
  });
  const maxGap = Math.max(0.05, ...traces.flatMap((t) => t.y));
  Plotly.react(el, traces, {
    ...PLOT_BASE, barmode: "group", bargap: 0.3, margin: { l: 44, r: 10, t: 18, b: 30 }, showlegend: true,
    legend: { orientation: "h", y: 1.12, x: 1, xanchor: "right", font: { size: 11 } },
    yaxis: axis({ title: axisTitle("gap s"), range: [0, maxGap * 1.3], fixedrange: true }), xaxis: axis({ fixedrange: true }),
  }, PLOT_CFG);
}

// ============================================================ maps
function cornerText(d) {
  return { x: d.corners.map((c) => c.x), y: d.corners.map((c) => c.y), text: d.corners.map((c) => c.label), mode: "text", type: "scatter",
    textfont: { size: 10, color: "#8b93a3" }, hoverinfo: "skip", textposition: "top right" };
}
export const mapLayout = () => ({ ...PLOT_BASE, xaxis: { visible: false }, yaxis: { visible: false, scaleanchor: "x", scaleratio: 1 }, dragmode: "pan", hovermode: "closest" });

export function renderDominance(d) {
  const el = $("dominance");
  if (!d.dominance) {
    Plotly.purge(el);
    el.innerHTML = `<p class="hint placeholder" style="padding:40px;text-align:center">Pick at least two drivers to see who is fastest where.</p>`;
    $("dom-share").innerHTML = ""; $("dom-note").textContent = "";
    return;
  }
  el.querySelector(".placeholder")?.remove();
  const ref = d.drivers[0].trace;
  const traces = [{ x: ref.X, y: ref.Y, mode: "lines", line: { color: "#20252e", width: 16 }, hoverinfo: "skip", type: "scatter" }];
  for (const s of d.dominance.sectors) {
    const others = Object.entries(s.times).filter(([c]) => c !== s.winner).map(([c, t]) => `${c} +${(t - s.times[s.winner]).toFixed(3)}`).join("<br>");
    traces.push({ x: s.x, y: s.y, mode: "lines", type: "scatter", line: { color: colorOf(s.winner), width: 8 },
      hovertemplate: `<b>Mini-sector ${s.index}</b> · ${s.start.toFixed(0)}–${s.end.toFixed(0)} m<br>${s.winner} fastest<br>${others}<extra></extra>` });
  }
  traces.push(cornerText(d));
  Plotly.react(el, traces, mapLayout(), PLOT_CFG);
  const total = d.dominance.sectors.length;
  $("dom-note").textContent = `${total} mini-sectors · colour = fastest driver through that sector`;
  $("dom-share").innerHTML = d.drivers.map((x) => {
    const n = d.dominance.share[x.code] || 0;
    return n ? `<div style="flex:${n};background:${colorOf(x.code)}" title="${x.code}: ${n} of ${total}">${x.code} ${n}</div>` : "";
  }).join("");
}

export function renderSpeedmapSwitch(d) {
  const seg = $("speedmap-driver");
  if (!d.drivers.some((x) => x.code === state.speedmapDriver)) state.speedmapDriver = d.reference;
  seg.innerHTML = d.drivers.map((x) => `<button type="button" data-code="${x.code}" style="--c:${colorOf(x.code)}" class="${x.code === state.speedmapDriver ? "on" : ""}">${x.code}</button>`).join("");
  seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    state.speedmapDriver = b.dataset.code; renderSpeedmapSwitch(d); renderSpeedmap(d);
  }));
}

export function renderSpeedmap(d) {
  const x = d.drivers.find((v) => v.code === state.speedmapDriver) || d.drivers[0];
  const t = x.trace;
  const spd = fillNulls(t.Speed);
  const bx = [], by = [];
  x.braking.forEach((z) => { const i = nearest(t.Distance, z.start); bx.push(t.X[i]); by.push(t.Y[i]); });
  const traces = [
    { x: t.X, y: t.Y, mode: "lines", line: { color: "#14171d", width: 14 }, hoverinfo: "skip", type: "scatter" },
    { x: t.X, y: t.Y, mode: "markers", type: "scattergl",
      marker: { size: 5, color: spd, colorscale: SPEED_STOPS, cmin: Math.min(...spd), cmax: Math.max(...spd), showscale: true,
        colorbar: { title: { text: "km/h", side: "right" }, thickness: 10, len: 0.8, outlinewidth: 0, tickfont: { size: 10 } } },
      customdata: t.Distance.map((dist, i) => [dist, t.nGear?.[i], t.Throttle?.[i]]),
      hovertemplate: "%{marker.color:.0f} km/h · gear %{customdata[1]} · throttle %{customdata[2]:.0f}%<br>%{customdata[0]:.0f} m<extra></extra>" },
    { x: bx, y: by, mode: "markers", type: "scatter", marker: { symbol: "line-ns", size: 12, line: { color: "#fff", width: 2 } }, hoverinfo: "skip" },
    cornerText(d),
  ];
  Plotly.react($("speedmap"), traces, mapLayout(), PLOT_CFG);
}

// ============================================================ gauges
const G_START = 150, G_SWEEP = 240, G_MAX = 360;
function polar(r, deg) { const a = (deg * Math.PI) / 180; return [100 + r * Math.cos(a), 100 + r * Math.sin(a)]; }
function gaugeSVG(x) {
  const c = colorOf(x.code), v = x.summary.top_speed;
  const [sx, sy] = polar(80, G_START), [ex, ey] = polar(80, G_START + G_SWEEP);
  const arc = `M${sx.toFixed(1)} ${sy.toFixed(1)} A80 80 0 1 1 ${ex.toFixed(1)} ${ey.toFixed(1)}`;
  let ticks = "";
  for (let k = 0; k <= G_MAX; k += 20) {
    const deg = G_START + (k / G_MAX) * G_SWEEP, major = k % 60 === 0;
    const [x1, y1] = polar(major ? 62 : 66, deg), [x2, y2] = polar(71, deg);
    ticks += `<line class="g-tick ${major ? "major" : ""}" x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"/>`;
    if (major) { const [tx, ty] = polar(53, deg); ticks += `<text class="g-num" x="${tx.toFixed(1)}" y="${(ty + 2.5).toFixed(1)}">${k}</text>`; }
  }
  const [nx, ny] = polar(70, G_START);
  return `<div class="gauge" style="--c:${c}">
    <svg viewBox="0 0 200 165">
      <path class="g-track" d="${arc}"/>
      <path class="g-val" d="${arc}" stroke="${c}" pathLength="100" stroke-dasharray="100" stroke-dashoffset="100" data-pct="${((v / G_MAX) * 100).toFixed(1)}"/>
      ${ticks}
      <g class="g-needle" data-deg="${((v / G_MAX) * G_SWEEP).toFixed(1)}"><line x1="100" y1="100" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="#fff" stroke-width="2.5" stroke-linecap="round"/></g>
      <circle cx="100" cy="100" r="6" fill="#0b0d11" stroke="${c}" stroke-width="2"/>
      <text class="g-big" x="100" y="138">${v.toFixed(0)}</text>
      <text class="g-unit" x="100" y="150">KM/H TOP SPEED</text>
      <text class="g-code" x="100" y="80">${x.code}</text>
    </svg>
    <div class="g-meta"><span>avg <b>${x.summary.avg_speed}</b></span><span>min <b>${x.summary.min_speed}</b></span><span>WOT <b>${x.summary.full_throttle_pct ?? "—"}%</b></span></div>
  </div>`;
}
export function renderGauges(d) {
  const el = $("gauges");
  el.innerHTML = d.drivers.map(gaugeSVG).join("");
  requestAnimationFrame(() => requestAnimationFrame(() => {
    el.querySelectorAll(".g-val").forEach((p) => p.setAttribute("stroke-dashoffset", String(100 - +p.dataset.pct)));
    el.querySelectorAll(".g-needle").forEach((n) => (n.style.transform = `rotate(${n.dataset.deg}deg)`));
  }));
}

// ============================================================ telemetry tab
export function renderTelemetry(d) {
  const ref = d.reference;
  const rows = { speed: [0.62, 1], delta: [0.49, 0.59], throttle: [0.35, 0.46], brake: [0.245, 0.325], gear: [0.125, 0.215], rpm: [0, 0.095] };
  const traces = [];
  d.drivers.forEach((x, i) => {
    const c = colorOf(x.code), t = x.trace;
    const dash = i > 0 && d.drivers.slice(0, i).some((p) => p.team === x.team) ? "dot" : "solid";
    const L = (y, ax, extra = {}) => ({ x: t.Distance, y, type: "scatter", mode: "lines", line: { color: c, width: 1.5, dash, ...extra }, yaxis: ax, hoverinfo: "none" });
    traces.push(L(t.Speed, "y", { width: 1.8 }));
    traces.push(L(t.Throttle, "y3"));
    traces.push({ ...L(t.Brake.map((b) => i + 0.8 * (b || 0)), "y4", { shape: "hv", dash: "solid" }) });
    traces.push(L(t.nGear, "y5", { shape: "hv" }));
    if (t.RPM) traces.push(L(t.RPM, "y6", { width: 1.2 }));
    if (x.code !== ref) {
      const dl = d.delta[x.code];
      traces.push({ x: dl.distance, y: dl.delta, type: "scatter", mode: "lines", line: { color: c, width: 2 }, yaxis: "y2", hoverinfo: "none",
        fill: "tozeroy", fillcolor: mix(c, "#0b0d12", 0.85) });
    }
  });
  const A = (domain, title, extra = {}) => axis({ domain, title: axisTitle(title), fixedrange: true, ...extra });
  const corners = d.corners.filter((c) => c.distance <= d.lap_length);
  const layout = {
    ...PLOT_BASE, margin: { l: 60, r: 14, t: 26, b: 36 }, hovermode: "x", dragmode: "zoom",
    xaxis: axis({ anchor: "y6", ticksuffix: " m", range: [0, d.lap_length], zeroline: false,
      showspikes: true, spikemode: "across", spikesnap: "cursor", spikecolor: "#e9ecf1", spikethickness: 1, spikedash: "solid" }),
    yaxis: A(rows.speed, "Speed km/h"),
    yaxis2: A(rows.delta, `Δ to ${ref} s`, { zeroline: true, zerolinewidth: 1.5 }),
    yaxis3: A(rows.throttle, "Throttle %", { range: [-5, 105] }),
    yaxis4: A(rows.brake, "Brake", { range: [-0.2, d.drivers.length], tickvals: d.drivers.map((_, i) => i + 0.4), ticktext: d.drivers.map((x) => x.code), showgrid: false }),
    yaxis5: A(rows.gear, "Gear", { range: [0.5, 8.5], dtick: 2 }),
    yaxis6: A(rows.rpm, "RPM"),
    shapes: corners.map((c) => ({ type: "line", xref: "x", yref: "paper", x0: c.distance, x1: c.distance, y0: 0, y1: 1, line: { color: "#252a33", width: 1, dash: "dot" } })),
    annotations: [
      ...corners.map((c) => ({ x: c.distance, y: 1, xref: "x", yref: "paper", yanchor: "bottom", text: c.label, showarrow: false, font: { size: 10, color: "#8b93a3" } })),
      ...(d.drivers.length < 2 ? [{ x: 0.5, y: 0.54, xref: "paper", yref: "paper", text: "Add a second driver for the time delta", showarrow: false, font: { color: "#565e6c" } }] : []),
      { x: 1, y: rows.delta[0], xref: "paper", yref: "paper", xanchor: "right", yanchor: "bottom", text: "above 0 = slower than reference", showarrow: false, font: { size: 10, color: "#565e6c" } },
    ],
  };
  const el = $("telemetry");
  Plotly.react(el, traces, layout, PLOT_CFG);
  renderTelMap(d);
  if (!el.dataset.bound) {
    el.dataset.bound = "1";
    let raf = 0, pending = null;
    const go = () => { raf = 0; scrubTelemetry(pending); };
    el.on("plotly_hover", (e) => { pending = e.points?.[0]?.x; if (!raf) raf = requestAnimationFrame(go); });
    // plotly_unhover fires between samples when hover labels are off → clear on leave instead
    el.addEventListener("mouseleave", () => { pending = null; if (!raf) raf = requestAnimationFrame(go); });
  }
  scrubTelemetry(null);
}

function renderTelMap(d) {
  const ref = d.drivers[0].trace;
  const traces = [
    { x: ref.X, y: ref.Y, mode: "lines", line: { color: "#2a303b", width: 9 }, hoverinfo: "skip", type: "scatter" },
    cornerText(d),
    ...d.drivers.map((x) => ({ x: [null], y: [null], mode: "markers", type: "scatter", hoverinfo: "skip",
      marker: { size: 14, color: colorOf(x.code), line: { color: "#06070a", width: 2 } } })),
  ];
  const el = $("tel-map");
  Plotly.react(el, traces, { ...mapLayout(), dragmode: false }, { ...PLOT_CFG, displayModeBar: false, staticPlot: false });
  el.dataset.cursorStart = 2;
}

function scrubTelemetry(dist) {
  const d = state.data; if (!d) return;
  const m = $("tel-map");
  if (m.data) {
    const xs = [], ys = [], idx = [];
    d.drivers.forEach((x, k) => {
      const t = x.trace;
      if (dist == null) { xs.push([null]); ys.push([null]); }
      else { const i = nearest(t.Distance, dist); xs.push([t.X[i]]); ys.push([t.Y[i]]); }
      idx.push(2 + k);
    });
    Plotly.restyle(m, { x: xs, y: ys }, idx);
  }
  const el = $("readout");
  if (dist == null) {
    $("readout-d").textContent = "";
    el.innerHTML = `<p class="hint" style="margin:0">Hover the telemetry chart to read every channel at any point of the lap.</p>`;
    return;
  }
  $("readout-d").textContent = `${Math.round(dist)} m`;
  el.innerHTML = d.drivers.map((x) => {
    const t = x.trace, i = nearest(t.Distance, dist);
    let delta = "";
    if (x.code !== d.reference) {
      const dl = d.delta[x.code]; const v = interp(dl.distance, dl.delta, dist);
      delta = `<span>Δ <b style="color:${v > 0 ? "var(--bad)" : "var(--good)"}">${fmtGap(v)}</b></span>`;
    }
    const thr = Math.round(t.Throttle?.[i] ?? 0), brk = t.Brake[i] ? 100 : 0;
    return `<div class="ro-row" style="--c:${colorOf(x.code)}">
      <span class="c">${x.code}</span><span class="v"><b>${Math.round(t.Speed[i] ?? 0)}</b> km/h · G${t.nGear?.[i] ?? "-"}</span>
      <div class="bars"><i style="--v:${thr}%;--pc:var(--good)" title="throttle"></i><i style="--v:${brk}%;--pc:var(--bad)" title="brake"></i></div>
      <div class="meta"><span>thr <b>${thr}%</b></span><span>rpm <b>${t.RPM?.[i] != null ? Math.round(t.RPM[i]) : "—"}</b></span>
        <span>lat <b>${(t.LatG?.[i] ?? 0).toFixed(1)}g</b></span><span>long <b>${(t.LongG?.[i] ?? 0).toFixed(1)}g</b></span>${delta}</div>
    </div>`;
  }).join("");
}

export function renderGG(d) {
  const traces = d.drivers.map((x) => ({
    x: x.trace.LatG, y: x.trace.LongG, mode: "markers", type: "scattergl", name: x.code,
    marker: { size: 4, color: colorOf(x.code), opacity: 0.45 },
    hovertemplate: `<b>${x.code}</b><br>lat %{x:.2f}g · long %{y:.2f}g<extra></extra>`,
  }));
  const shapes = [1, 2, 3, 4, 5].map((r) => ({ type: "circle", xref: "x", yref: "y", x0: -r, x1: r, y0: -r, y1: r, line: { color: r % 2 ? "#232832" : "#2e3542", width: 1, dash: "dot" } }));
  Plotly.react($("gg"), traces, {
    ...PLOT_BASE, margin: { l: 44, r: 10, t: 10, b: 36 }, shapes, showlegend: true,
    legend: { orientation: "h", y: 1.08, x: 1, xanchor: "right", font: { size: 11 } },
    xaxis: axis({ title: axisTitle("lateral g"), range: [-5.5, 5.5], zeroline: true }),
    yaxis: axis({ title: axisTitle("longitudinal g"), range: [-6, 3], zeroline: true, scaleanchor: "x", scaleratio: 1 }),
  }, PLOT_CFG);
}

function timeWeights(t) {
  const T = fillNulls(t.Time);
  return T.map((v, i) => (i < T.length - 1 ? Math.max(0, T[i + 1] - v) : 0));
}

export function renderGears(d) {
  const traces = d.drivers.map((x) => {
    const w = timeWeights(x.trace), tot = sum(w) || 1, g = fillNulls(x.trace.nGear);
    const acc = Array(8).fill(0);
    g.forEach((v, i) => { const k = Math.round(v) - 1; if (k >= 0 && k < 8) acc[k] += w[i]; });
    return { type: "bar", name: x.code, x: [1, 2, 3, 4, 5, 6, 7, 8], y: acc.map((v) => (v / tot) * 100), marker: { color: colorOf(x.code) },
      hovertemplate: `<b>${x.code}</b> gear %{x}: %{y:.1f}% of lap<extra></extra>` };
  });
  Plotly.react($("gears"), traces, {
    ...PLOT_BASE, barmode: "group", bargap: 0.25, margin: { l: 40, r: 10, t: 10, b: 36 }, showlegend: true,
    legend: { orientation: "h", y: 1.08, x: 1, xanchor: "right", font: { size: 11 } },
    xaxis: axis({ title: axisTitle("gear"), dtick: 1, fixedrange: true }), yaxis: axis({ ticksuffix: "%", fixedrange: true }),
  }, PLOT_CFG);
}

export function renderHist(d) {
  const bins = Array.from({ length: 37 }, (_, i) => i * 10);
  const traces = d.drivers.map((x) => {
    const w = timeWeights(x.trace), tot = sum(w) || 1, s = fillNulls(x.trace.Speed);
    const acc = Array(bins.length).fill(0);
    s.forEach((v, i) => { const k = Math.min(bins.length - 1, Math.max(0, Math.floor(v / 10))); acc[k] += w[i]; });
    const c = colorOf(x.code);
    return { type: "scatter", mode: "lines", name: x.code, x: bins.map((b) => b + 5), y: acc.map((v) => (v / tot) * 100),
      line: { color: c, width: 2, shape: "spline", smoothing: 0.6 }, fill: "tozeroy", fillcolor: mix(c, "#0b0d12", 0.8),
      hovertemplate: `<b>${x.code}</b> %{x:.0f} km/h: %{y:.1f}%<extra></extra>` };
  });
  Plotly.react($("hist"), traces, {
    ...PLOT_BASE, margin: { l: 40, r: 10, t: 10, b: 36 }, showlegend: true, hovermode: "x unified",
    legend: { orientation: "h", y: 1.08, x: 1, xanchor: "right", font: { size: 11 } },
    xaxis: axis({ title: axisTitle("km/h"), range: [50, 360], fixedrange: true }), yaxis: axis({ ticksuffix: "%", fixedrange: true, rangemode: "tozero" }),
  }, PLOT_CFG);
}
