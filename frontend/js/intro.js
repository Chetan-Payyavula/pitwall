/* Opening sequence: boot log → five red lights → lights out → warp + shift
   lights → logo slam → wipe. Skippable (button, Esc/Enter/Space), can be
   switched off for good, optional synthesized sound. Pure canvas 2D + CSS. */
import { $, store } from "./util.js";

const KEY = "pitwall.skipIntro";
const T = { lightsIn: 1150, light0: 1350, lightStep: 600, out: 4650, warpEnd: 6850, logo: 6700, wipe: 8700 };

let audio = null;
let soundOn = false;

function ctxAudio() {
  if (!audio) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    audio = new AC();
  }
  if (audio.state === "suspended") audio.resume();
  return audio;
}
function beep(freq = 660, dur = 0.16, vol = 0.18) {
  if (!soundOn) return;
  const a = ctxAudio(); if (!a) return;
  const o = a.createOscillator(), g = a.createGain();
  o.type = "sine"; o.frequency.value = freq;
  g.gain.setValueAtTime(vol, a.currentTime);
  g.gain.exponentialRampToValueAtTime(0.0001, a.currentTime + dur);
  o.connect(g).connect(a.destination); o.start(); o.stop(a.currentTime + dur + 0.02);
}
/** Engine pull through the gears: sawtooth + square through a low-pass. */
function engine(durSec, gears) {
  if (!soundOn) return;
  const a = ctxAudio(); if (!a) return;
  const t0 = a.currentTime, per = durSec / gears;
  const lp = a.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 1400; lp.Q.value = 3;
  const g = a.createGain(); g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(0.16, t0 + 0.08);
  g.gain.setValueAtTime(0.16, t0 + durSec - 0.25); g.gain.exponentialRampToValueAtTime(0.0001, t0 + durSec + 0.3);
  lp.connect(g).connect(a.destination);
  for (const [type, mult, vol] of [["sawtooth", 1, 0.7], ["square", 2, 0.25]]) {
    const o = a.createOscillator(), og = a.createGain(); o.type = type; og.gain.value = vol;
    for (let i = 0; i < gears; i++) {
      const s = t0 + i * per;
      o.frequency.setValueAtTime((95 + i * 6) * mult, s);
      o.frequency.exponentialRampToValueAtTime((260 + i * 14) * mult, s + per * 0.95);
    }
    o.connect(og).connect(lp); o.start(t0); o.stop(t0 + durSec + 0.35);
  }
}

export function introDisabled() {
  const q = new URLSearchParams(location.search).get("intro");
  if (q === "0") return true;
  if (q === "1") return false;
  return store.get(KEY) === "1";
}

/** Plays the intro. Resolves when it has finished or been skipped. */
export function playIntro({ force = false } = {}) {
  const root = $("intro");
  if (!force && introDisabled()) { root.classList.add("gone"); return Promise.resolve(); }

  root.classList.remove("gone", "out");
  $("intro-logo").classList.remove("show");
  $("intro-boot").textContent = "";
  $("intro-never").checked = store.get(KEY) === "1";
  document.body.style.overflow = "hidden";

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const cv = $("intro-canvas"), g = cv.getContext("2d");
  let W = 0, H = 0, DPR = 1;
  const resize = () => {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = cv.clientWidth; H = cv.clientHeight;
    cv.width = W * DPR; cv.height = H * DPR; g.setTransform(DPR, 0, 0, DPR, 0, 0);
  };
  resize(); window.addEventListener("resize", resize);

  // warp particles
  const parts = Array.from({ length: 420 }, () => newPart(true));
  function newPart(spread) {
    const a = Math.random() * Math.PI * 2;
    return { a, r: spread ? Math.random() * 0.9 : Math.random() * 0.05, z: Math.random(), red: Math.random() < 0.28, w: 0.6 + Math.random() * 1.8 };
  }

  const bootLines = [
    ["PITWALL TELEMETRY LAB  v2.0", null],
    ["› syncing timing feed", "OK"],
    ["› decoding car telemetry", "OK"],
    ["› building track model", "OK"],
    ["› warming tyres", "OK"],
  ];
  const lit = [false, false, false, false, false];
  let start = null, raf = 0, done = false, resolveFn, engineStarted = false, gridPhase = 0;
  const fired = new Set();
  const once = (k, fn) => { if (!fired.has(k)) { fired.add(k); fn(); } };

  function drawBoot(t) {
    const per = 190;
    const n = Math.min(bootLines.length, Math.floor(t / per) + 1);
    let html = "";
    for (let i = 0; i < n; i++) {
      const [txt, ok] = bootLines[i];
      const local = t - i * per;
      const shown = txt.slice(0, Math.max(0, Math.floor(local / 6)));
      const dots = ok && shown.length === txt.length ? " " + ".".repeat(Math.max(0, 30 - txt.length)) + " " : "";
      html += shown + (dots && local > per * 0.8 ? `${dots}<span class="ok">${ok}</span>` : "") + "\n";
    }
    const el = $("intro-boot");
    if (el.innerHTML !== html) el.innerHTML = html;
    el.style.opacity = t > T.out ? Math.max(0, 1 - (t - T.out) / 400) : 0.85;
  }

  function drawGrid(phase) {
    // perspective floor with lines rushing toward the viewer
    const hy = H * 0.62;
    g.save();
    g.strokeStyle = "rgba(255,40,40,0.16)"; g.lineWidth = 1;
    for (let i = -12; i <= 12; i++) {
      g.beginPath(); g.moveTo(W / 2 + i * 8, hy); g.lineTo(W / 2 + i * W * 0.16, H); g.stroke();
    }
    const off = phase % 1;
    for (let k = 0; k < 14; k++) {
      const p = Math.pow((k + off) / 14, 2.2);
      const y = hy + (H - hy) * p;
      g.globalAlpha = p * 0.9;
      g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke();
    }
    g.restore();
    const fade = g.createLinearGradient(0, hy - 2, 0, hy + 90);
    fade.addColorStop(0, "rgba(0,0,0,1)"); fade.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = fade; g.fillRect(0, hy - 2, W, 92);
  }

  function drawLights(t) {
    const appear = Math.min(1, Math.max(0, (t - T.lightsIn) / 350));
    if (appear <= 0) return;
    const unit = Math.min(W / 9.5, 120), gap = unit * 0.28;
    const total = unit * 5 + gap * 4, x0 = (W - total) / 2, y0 = H * 0.30 - unit * 1.1;
    g.save();
    g.globalAlpha = appear;
    // gantry beam
    g.fillStyle = "#0f1115"; g.fillRect(x0 - gap, y0 - unit * 0.22, total + gap * 2, unit * 0.16);
    for (let i = 0; i < 5; i++) {
      const x = x0 + i * (unit + gap), y = y0 + (1 - easeOut(appear)) * -60;
      g.fillStyle = "#121419"; roundRect(x, y, unit, unit * 2.2, unit * 0.14); g.fill();
      g.strokeStyle = "#23262e"; g.lineWidth = 2; g.stroke();
      for (let r = 0; r < 4; r++) {
        const cx = x + unit / 2, cy = y + unit * 0.32 + r * unit * 0.5, rad = unit * 0.19;
        const on = lit[i] && r < 2;
        g.beginPath(); g.arc(cx, cy, rad, 0, Math.PI * 2);
        if (on) {
          const grd = g.createRadialGradient(cx - rad * 0.3, cy - rad * 0.3, 1, cx, cy, rad);
          grd.addColorStop(0, "#ffd0d0"); grd.addColorStop(0.35, "#ff2a2a"); grd.addColorStop(1, "#a00000");
          g.fillStyle = grd; g.shadowColor = "#ff1a1a"; g.shadowBlur = unit * 0.6;
        } else { g.fillStyle = "#1d0a0a"; g.shadowBlur = 0; }
        g.fill(); g.shadowBlur = 0;
      }
    }
    g.restore();
  }

  function drawWarp(t, dt) {
    const p = Math.min(1, (t - T.out) / (T.warpEnd - T.out));
    const speed = 0.0006 + easeIn(p) * 0.012;
    const cx = W / 2, cy = H * 0.5, maxR = Math.hypot(W, H) * 0.6;
    g.save(); g.lineCap = "round";
    for (const q of parts) {
      const r0 = q.r;
      q.r += q.r * speed * dt * (0.6 + q.z) + speed * dt * 0.02;
      if (q.r > 1.2) Object.assign(q, newPart(false));
      const a = Math.min(1, q.r * 3) * (0.35 + p * 0.65);
      g.strokeStyle = q.red ? `rgba(255,50,50,${a})` : `rgba(235,240,255,${a * 0.8})`;
      g.lineWidth = q.w * (0.5 + q.r);
      g.beginPath();
      g.moveTo(cx + Math.cos(q.a) * r0 * maxR, cy + Math.sin(q.a) * r0 * maxR * 0.75);
      g.lineTo(cx + Math.cos(q.a) * q.r * maxR, cy + Math.sin(q.a) * q.r * maxR * 0.75);
      g.stroke();
    }
    g.restore();

    // dashboard: shift lights, speed, gear
    const gears = 8, per = (T.warpEnd - T.out) / gears;
    const local = t - T.out;
    const gear = Math.min(gears, Math.floor(local / per) + 1);
    const inGear = (local % per) / per;
    const spd = Math.round(Math.min(1, local / (T.warpEnd - T.out)) ** 0.75 * 338);
    const leds = 15, lw = Math.min(26, W / 30), lx = W / 2 - (leds * lw * 1.35) / 2, ly = H * 0.16;
    const nOn = Math.floor(inGear * (leds + 1));
    for (let i = 0; i < leds; i++) {
      const c = i < 5 ? "#21e06b" : i < 10 ? "#ff2a2a" : "#3d8bff";
      const on = gear < gears ? i < nOn : true;
      g.beginPath(); g.arc(lx + i * lw * 1.35 + lw / 2, ly, lw * 0.36, 0, Math.PI * 2);
      g.fillStyle = on ? c : "#15171c"; g.shadowColor = c; g.shadowBlur = on ? 14 : 0; g.fill();
    }
    g.shadowBlur = 0;
    g.textAlign = "center"; g.fillStyle = "#fff";
    g.font = `700 ${Math.min(120, W / 8)}px "Chakra Petch", sans-serif`;
    g.fillText(String(spd), W / 2, H * 0.5);
    g.font = `600 ${Math.min(16, W / 50)}px "Titillium Web", sans-serif`; g.fillStyle = "#8b93a3";
    g.fillText("KM/H", W / 2, H * 0.5 + 26);
    g.font = `700 ${Math.min(54, W / 16)}px "Chakra Petch", sans-serif`; g.fillStyle = "#ff2a2a";
    g.fillText(String(gear), W / 2, H * 0.5 + 92);
    g.font = `600 12px "Titillium Web", sans-serif`; g.fillStyle = "#8b93a3"; g.fillText("GEAR", W / 2, H * 0.5 + 112);
    return p;
  }

  function frame(now) {
    if (done) return;
    if (start == null) start = now;
    const t = (now - start) * (reduced ? 6 : 1);
    const dt = Math.min(48, now - (frame.last || now)); frame.last = now;

    g.fillStyle = "#000"; g.fillRect(0, 0, W, H);
    // vignette red glow
    const vg = g.createRadialGradient(W / 2, H * 0.35, 10, W / 2, H * 0.35, Math.max(W, H) * 0.7);
    vg.addColorStop(0, "rgba(60,0,0,0.35)"); vg.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = vg; g.fillRect(0, 0, W, H);

    drawBoot(t);
    if (t < T.out) {
      gridPhase += dt / 1000 * 0.6;
      drawGrid(gridPhase);
      for (let i = 0; i < 5; i++) {
        if (t >= T.light0 + i * T.lightStep && !lit[i]) { lit[i] = true; beep(660, 0.14); }
      }
      drawLights(t);
    } else {
      if (lit.some(Boolean)) { lit.fill(false); }
      once("engine", () => { engine((T.warpEnd - T.out) / 1000, 8); engineStarted = true; });
      gridPhase += dt / 1000 * (3 + (t - T.out) / 250);
      drawGrid(gridPhase);
      if (t < T.warpEnd + 150) drawWarp(t, dt);
      // flash on lights out
      const f = 1 - (t - T.out) / 220;
      if (f > 0) { g.fillStyle = `rgba(255,255,255,${f * 0.35})`; g.fillRect(0, 0, W, H); }
    }
    if (t >= T.logo) {
      once("logo", () => $("intro-logo").classList.add("show"));
      const f = 1 - (t - T.logo) / 300;
      if (f > 0) { g.fillStyle = `rgba(255,255,255,${f * 0.8})`; g.fillRect(0, 0, W, H); }
    }
    if (t >= T.wipe) { finish(); return; }
    raf = requestAnimationFrame(frame);
  }

  function finish() {
    if (done) return;
    done = true;
    cancelAnimationFrame(raf);
    window.removeEventListener("resize", resize);
    window.removeEventListener("keydown", onKey);
    root.classList.add("out");
    document.body.style.overflow = "";
    setTimeout(() => { root.classList.add("gone"); resolveFn(); }, 760);
  }
  function onKey(e) { if (["Escape", "Enter", " "].includes(e.key)) { e.preventDefault(); finish(); } }

  $("intro-skip").onclick = finish;
  $("intro-never").onchange = (e) => store.set(KEY, e.target.checked ? "1" : "0");
  const sb = $("intro-sound");
  const paintSound = () => { sb.textContent = soundOn ? "🔊 Sound" : "🔇 Sound"; sb.setAttribute("aria-pressed", String(soundOn)); };
  sb.onclick = () => { soundOn = !soundOn; if (soundOn) ctxAudio(); paintSound(); };
  paintSound();
  window.addEventListener("keydown", onKey);

  return new Promise((res) => { resolveFn = res; raf = requestAnimationFrame(frame); void engineStarted; });

  function roundRect(x, y, w, h, r) { g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r); g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath(); }
}

const easeOut = (x) => 1 - Math.pow(1 - x, 3);
const easeIn = (x) => x * x * x;
