/* 3D lap replay (three.js): the track is a ribbon built from the reference
   lap's X/Y/Z positions, cars are low-poly models driven by each driver's own
   time-stamped positions. Several camera modes, a HUD leaderboard with live
   gaps, a mini-map, track colouring modes and an elevation exaggeration. */
import * as THREE from "three";
import { OrbitControls } from "../vendor/OrbitControls.js";
import { $, state, colorOf, fillNulls, movingAvg, interp, speedColor, fmtLap, fmtGap, clamp } from "./util.js";

/** sRGB hex → linear RGB triple for vertex colours (three.js works in linear space). */
const _c = new THREE.Color();
const lin = (hex) => { _c.set(hex); return [_c.r, _c.g, _c.b]; };

const CAR_SCALE = 2.6;   // cars are drawn bigger than life so they read from a distance
const TRACK_W = 15;      // track width in metres
const CAR_LEN = 5.6;     // metres
const CAM_NAMES = { chase: "CHASE CAM", onboard: "ONBOARD", tv: "TV CAMERA", heli: "HELICOPTER", orbit: "FREE CAMERA", auto: "AUTO DIRECTOR" };
const AUTO_SEQ = ["chase", "tv", "onboard", "heli", "tv"];

let R = null;            // renderer, scene, camera … (created once)
let S = null;            // contents for the current data set
let visible = false;
const opts = { cam: "chase", color: "speed", speed: 1, elev: 3, playing: true, focus: null };

// ======================================================================== init
export function initReplay() {
  if (R) return true;
  const canvas = $("r3d");
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
  } catch (e) {
    $("replay").innerHTML = `<p class="hint" style="padding:60px;text-align:center">3D view needs WebGL, which this browser has turned off. (${e.message})</p>`;
    return false;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.1;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x05060a);
  scene.fog = new THREE.Fog(0x05060a, 500, 1600);
  const camera = new THREE.PerspectiveCamera(55, 1, 0.05, 6000);
  camera.position.set(0, 300, 380);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  controls.maxPolarAngle = Math.PI * 0.49; controls.enabled = false;

  scene.add(new THREE.HemisphereLight(0xbcd2ff, 0x0b0e13, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 2.2); sun.position.set(200, 400, 150); scene.add(sun);
  const rim = new THREE.DirectionalLight(0xff4040, 0.6); rim.position.set(-300, 120, -200); scene.add(rim);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(6000, 6000), new THREE.MeshStandardMaterial({ color: 0x0a0c11, roughness: 1 }));
  ground.rotation.x = -Math.PI / 2; ground.position.y = -0.4; scene.add(ground);
  const grid = new THREE.GridHelper(6000, 240, 0x1a2030, 0x10141b);
  grid.position.y = -0.35; grid.material.transparent = true; grid.material.opacity = 0.55; scene.add(grid);

  // distant star field
  const starPos = new Float32Array(1500 * 3);
  for (let i = 0; i < 1500; i++) {
    const th = Math.random() * Math.PI * 2, ph = Math.random() * Math.PI * 0.45, r = 2500 + Math.random() * 800;
    starPos.set([r * Math.cos(th) * Math.sin(ph + 0.6), r * Math.cos(ph + 0.6) + 200, r * Math.sin(th) * Math.sin(ph + 0.6)], i * 3);
  }
  const stars = new THREE.Points(new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(starPos, 3)),
    new THREE.PointsMaterial({ color: 0x8090b0, size: 2, sizeAttenuation: false, fog: false, transparent: true, opacity: 0.6 }));
  scene.add(stars);

  const world = new THREE.Group(); scene.add(world);
  R = { renderer, scene, camera, controls, world, clock: new THREE.Clock(), raf: 0, t: 0, hudAt: 0, look: new THREE.Vector3(), snap: true, tvCam: -1, autoAt: 0, autoIdx: 0 };

  new ResizeObserver(resize).observe($("replay"));
  bindUI();
  return true;
}

function resize() {
  if (!R) return;
  const el = $("replay"), w = el.clientWidth, h = el.clientHeight;
  if (!w || !h) return;
  R.renderer.setSize(w, h, false);
  R.camera.aspect = w / h; R.camera.updateProjectionMatrix();
}

// ======================================================================== UI
function segSet(id, v) { $(id).querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === String(v))); }
function bindUI() {
  $("r-play").addEventListener("click", togglePlay);
  $("r-scrub").addEventListener("input", (e) => { if (S) R.t = (+e.target.value / 1000) * S.maxT; });
  $("r-speed").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; opts.speed = +b.dataset.v; segSet("r-speed", b.dataset.v); });
  $("r-cam").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setCam(b.dataset.v); });
  $("r-color").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; opts.color = b.dataset.v; segSet("r-color", b.dataset.v); paintTrack(); });
  $("r-focus").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; opts.focus = b.dataset.v; segSet("r-focus", b.dataset.v); R.snap = true; R.tvCam = -1; });
  $("r-elev").addEventListener("change", (e) => { opts.elev = +e.target.value; if (state.data) buildReplay(state.data, true); });
  $("r-full").addEventListener("click", () => {
    const el = $("replay");
    if (document.fullscreenElement) document.exitFullscreen(); else el.requestFullscreen?.();
  });
  window.addEventListener("keydown", (e) => {
    if (!visible || e.target.closest("input, select, textarea, button")) return;
    if (e.key === " ") { e.preventDefault(); togglePlay(); }
    const k = { 1: "chase", 2: "onboard", 3: "tv", 4: "heli", 5: "orbit", 6: "auto" }[e.key];
    if (k) setCam(k);
  });
}
function togglePlay() { opts.playing = !opts.playing; $("r-play").textContent = opts.playing ? "❚❚" : "▶"; }
function setCam(mode) {
  opts.cam = mode; segSet("r-cam", mode);
  R.controls.enabled = mode === "orbit";
  if (mode === "orbit" && S) { R.camera.position.set(0, S.span * 0.75, S.span * 0.95); R.controls.target.set(0, 0, 0); R.camera.fov = 50; R.camera.updateProjectionMatrix(); }
  if (mode === "auto") { R.autoAt = 0; R.autoIdx = 0; }
  R.snap = true; R.tvCam = -1;
  $("r-camname").textContent = CAM_NAMES[mode];
}

// ======================================================================== build
function dispose(obj) {
  obj.traverse((o) => {
    o.geometry?.dispose();
    const mats = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
    mats.forEach((m) => { m.map?.dispose(); m.dispose(); });
  });
}

export function buildReplay(d, keepTime = false) {
  if (!R) return;
  const prevT = R.t;
  for (const c of [...R.world.children]) { R.world.remove(c); dispose(c); }

  const ref = d.drivers[0].trace;
  if (!ref.X || !ref.Y) { $("r-sub").textContent = "No position data for this lap."; S = null; return; }
  const D = ref.Distance, lapLen = D.at(-1) - D[0];
  const RX = movingAvg(fillNulls(ref.X), 5), RY = movingAvg(fillNulls(ref.Y), 5);
  const RZ = ref.Z ? movingAvg(fillNulls(ref.Z), 9) : RX.map(() => 0);
  let arc = 0; for (let i = 1; i < RX.length; i++) arc += Math.hypot(RX[i] - RX[i - 1], RY[i] - RY[i - 1]);
  const mPerUnit = lapLen / (arc || 1);
  const minX = Math.min(...RX), maxX = Math.max(...RX), minY = Math.min(...RY), maxY = Math.max(...RY);
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2, spanData = Math.max(maxX - minX, maxY - minY) || 1;
  const SC = 420 / spanData;                 // world units per data unit
  const wpm = SC / mPerUnit;                 // world units per metre
  const zMin = Math.min(...RZ);
  const toW = (x, y, z, out = new THREE.Vector3()) => out.set((x - cx) * SC, (z - zMin) * SC * opts.elev + 0.3 * wpm, -(y - cy) * SC);

  // ---- resampled centre line
  const step = 5, n = Math.min(1600, Math.max(50, Math.round(lapLen / step)));
  const sd = Array.from({ length: n }, (_, i) => D[0] + (i * lapLen) / (n - 1));
  const P = sd.map((s) => toW(interp(D, RX, s), interp(D, RY, s), interp(D, RZ, s)));
  const closed = Math.hypot(RX[0] - RX.at(-1), RY[0] - RY.at(-1)) * mPerUnit < 80;
  const Nrm = P.map((p, i) => {
    const a = P[i === 0 ? (closed ? n - 2 : 0) : i - 1], b = P[i === n - 1 ? (closed ? 1 : n - 1) : i + 1];
    const t = new THREE.Vector3(b.x - a.x, 0, b.z - a.z).normalize();
    return new THREE.Vector3(-t.z, 0, t.x);
  });
  const hw = (TRACK_W * wpm) / 2;
  const edge = (side, extra = 0, dy = 0) => P.map((p, i) => p.clone().addScaledVector(Nrm[i], side * (hw + extra)).add(new THREE.Vector3(0, dy, 0)));
  const L = edge(1), Rr = edge(-1);

  // ---- track surface (vertex coloured)
  const pos = new Float32Array(n * 2 * 3), col = new Float32Array(n * 2 * 3), idx = [];
  for (let i = 0; i < n; i++) { pos.set([L[i].x, L[i].y, L[i].z, Rr[i].x, Rr[i].y, Rr[i].z], i * 6); }
  for (let i = 0; i < n - 1; i++) { const a = i * 2; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
  if (closed) { const a = (n - 1) * 2; idx.push(a, a + 1, 0, a + 1, 1, 0); }
  const tg = new THREE.BufferGeometry();
  tg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  tg.setAttribute("color", new THREE.BufferAttribute(col, 3));
  tg.setIndex(idx);
  const trackMesh = new THREE.Mesh(tg, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide, toneMapped: false }));
  R.world.add(trackMesh);

  // shoulder (darker, wider, slightly lower)
  const L2 = edge(1, 3.5 * wpm, -0.15 * wpm), R2 = edge(-1, 3.5 * wpm, -0.15 * wpm);
  R.world.add(stripMesh(L2, R2, 0x14171e, closed));

  // white edge lines
  const lineMat = new THREE.LineBasicMaterial({ color: 0xe9ecf1, transparent: true, opacity: 0.75 });
  for (const e of [edge(1, 0, 0.05 * wpm), edge(-1, 0, 0.05 * wpm)]) {
    const g = new THREE.BufferGeometry().setFromPoints(closed ? [...e, e[0]] : e);
    R.world.add(new THREE.Line(g, lineMat));
  }

  // curtains from the track edge down to the ground (show elevation)
  const curtainMat = new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.28, side: THREE.DoubleSide, depthWrite: false });
  for (const e of [L2, R2]) {
    const cp = new Float32Array(n * 6), cc = new Float32Array(n * 6), ci = [];
    for (let i = 0; i < n; i++) {
      cp.set([e[i].x, e[i].y, e[i].z, e[i].x, -0.3, e[i].z], i * 6);
      cc.set([...lin("#8a1a1a"), ...lin("#07080c")], i * 6);
    }
    for (let i = 0; i < n - 1; i++) { const a = i * 2; ci.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
    const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(cp, 3)); g.setAttribute("color", new THREE.BufferAttribute(cc, 3)); g.setIndex(ci);
    R.world.add(new THREE.Mesh(g, curtainMat));
  }

  // barrier walls along both shoulders (alternating advertising boards → sense of speed)
  {
    const wp = [], wc = [], wH = 2.4 * wpm, boards = [lin("#1b2030"), lin("#c81414"), lin("#1b2030"), lin("#d9dde5")];
    const W1 = edge(1, 6 * wpm, 0), W2 = edge(-1, 6 * wpm, 0);
    for (const e of [W1, W2]) {
      for (let i = 0; i < n - 1; i++) {
        const a = e[i], b = e[i + 1], col = boards[Math.floor((i * step) / 25) % boards.length];
        for (const v of [[a, 0], [b, 0], [b, wH], [a, 0], [b, wH], [a, wH]]) { wp.push(v[0].x, v[0].y + v[1], v[0].z); wc.push(...col); }
      }
    }
    const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.Float32BufferAttribute(wp, 3)); g.setAttribute("color", new THREE.Float32BufferAttribute(wc, 3));
    R.world.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide, toneMapped: false })));
  }

  // kerbs: red/white strips outside both edges around every corner
  const kp = [], kc = [];
  const kerbW = 1.6 * wpm;
  for (const c of d.corners) {
    for (let s = c.distance - 45, k = 0; s < c.distance + 45; s += 4, k++) {
      const i0 = Math.round(((s - D[0]) / lapLen) * (n - 1)), i1 = Math.round(((s + 4 - D[0]) / lapLen) * (n - 1));
      if (i0 < 0 || i1 >= n || i0 === i1) continue;
      const [r, g, b] = k % 2 ? lin("#eeeeee") : lin("#e01818");
      for (const side of [1, -1]) {
        const a0 = P[i0].clone().addScaledVector(Nrm[i0], side * hw), b0 = P[i0].clone().addScaledVector(Nrm[i0], side * (hw + kerbW));
        const a1 = P[i1].clone().addScaledVector(Nrm[i1], side * hw), b1 = P[i1].clone().addScaledVector(Nrm[i1], side * (hw + kerbW));
        for (const v of [a0, b0, a1, b0, b1, a1]) { kp.push(v.x, v.y + 0.04 * wpm, v.z); kc.push(r, g, b); }
      }
    }
  }
  if (kp.length) {
    const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.Float32BufferAttribute(kp, 3)); g.setAttribute("color", new THREE.Float32BufferAttribute(kc, 3));
    R.world.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide, toneMapped: false })));
  }

  // start / finish: chequered strip + gantry
  {
    const dir = P[1].clone().sub(P[0]).setY(0).normalize();
    const len = 4 * wpm;
    const a = L[0].clone().add(new THREE.Vector3(0, 0.06 * wpm, 0)), b = Rr[0].clone().add(new THREE.Vector3(0, 0.06 * wpm, 0));
    const c = b.clone().addScaledVector(dir, len), e = a.clone().addScaledVector(dir, len);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute([a, b, c, a, c, e].flatMap((v) => [v.x, v.y, v.z]), 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1], 2));
    R.world.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({ map: checkerTexture(), side: THREE.DoubleSide })));
    const postMat = new THREE.MeshStandardMaterial({ color: 0x2a2f38, metalness: 0.6, roughness: 0.4 });
    const H = 9 * wpm;
    for (const p of [L2[0], R2[0]]) {
      const m = new THREE.Mesh(new THREE.BoxGeometry(0.8 * wpm, H, 0.8 * wpm), postMat);
      m.position.copy(p).add(new THREE.Vector3(0, H / 2, 0)); R.world.add(m);
    }
    const beamLen = L2[0].distanceTo(R2[0]);
    const beam = new THREE.Mesh(new THREE.BoxGeometry(beamLen, 1.4 * wpm, 0.6 * wpm), new THREE.MeshBasicMaterial({ color: 0xff1e1e, toneMapped: false }));
    beam.position.copy(L2[0]).lerp(R2[0], 0.5).add(new THREE.Vector3(0, H, 0));
    beam.lookAt(beam.position.clone().add(dir)); R.world.add(beam);
  }

  // corner labels
  for (const c of d.corners) {
    const s = clamp(c.distance, D[0], D.at(-1));
    const p = toW(interp(D, RX, s), interp(D, RY, s), interp(D, RZ, s));
    const sp = textSprite(c.label, "#ffffff", "rgba(10,12,16,.82)", 30);
    sp.position.copy(p).add(new THREE.Vector3(0, 14 * wpm, 0));
    sp.scale.set(16 * wpm, 7 * wpm, 1);
    R.world.add(sp);
  }

  // ---- cars
  const carScale = wpm * CAR_SCALE;
  const cars = d.drivers.map((x) => {
    const t = x.trace;
    const T0 = fillNulls(t.Time), t0 = T0[0];
    const T = T0.map((v) => v - t0);
    const X = movingAvg(fillNulls(t.X), 5), Y = movingAvg(fillNulls(t.Y), 5), Z = t.Z ? movingAvg(fillNulls(t.Z), 9) : X.map(() => 0);
    const W = X.map((_, i) => toW(X[i], Y[i], Z[i]));
    const color = colorOf(x.code);
    const car = buildCar(color);
    car.group.scale.setScalar(carScale);
    const label = textSprite(x.code, color, "rgba(6,8,12,.85)", 34);
    label.scale.set(2.5, 1.1, 1); label.position.set(0, 2.4, 0);
    car.group.add(label);
    R.world.add(car.group);
    const trailN = 46;
    const trailGeo = new THREE.BufferGeometry();
    trailGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(trailN * 3), 3));
    const tc = new Float32Array(trailN * 3), [cr, cg, cb] = lin(color);
    for (let k = 0; k < trailN; k++) { const f = 1 - k / trailN; tc.set([cr * f + 0.02 * (1 - f), cg * f + 0.024 * (1 - f), cb * f + 0.04 * (1 - f)], k * 3); }
    trailGeo.setAttribute("color", new THREE.BufferAttribute(tc, 3));
    const trail = new THREE.Line(trailGeo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.95 }));
    trail.frustumCulled = false;
    R.world.add(trail);
    return {
      code: x.code, color, car, label, trail, trailN, T, W, end: T.at(-1),
      D: fillNulls(t.Distance), V: fillNulls(t.Speed), G: fillNulls(t.nGear, 1), TH: fillNulls(t.Throttle || t.Speed.map(() => 0)),
      B: t.Brake.map((b) => (b ? 1 : 0)), pos: new THREE.Vector3(), dir: new THREE.Vector3(0, 0, 1), dist: 0,
    };
  });

  // TV camera spots along the circuit
  const tvSpots = [];
  for (let k = 0; k < 14; k++) {
    const i = Math.floor((k + 0.5) * (n / 14));
    const side = k % 2 ? 1 : -1;
    tvSpots.push({ i, pos: P[i].clone().addScaledVector(Nrm[i], side * hw * 7).add(new THREE.Vector3(0, hw * 5 + 4 * wpm, 0)) });
  }

  S = {
    d, P, n, sd, closed, wpm, cars, tvSpots, trackMesh, span: 420,
    carLen: CAR_LEN * carScale, refD: cars[0].D, refT: cars[0].T,
    maxT: Math.max(...cars.map((c) => c.end)),
    mini: { X: RX, Y: RY, minX, maxX, minY, maxY },
  };
  if (!d.drivers.some((x) => x.code === opts.focus)) opts.focus = d.reference;
  R.t = keepTime ? Math.min(prevT, S.maxT) : 0;
  R.snap = true; R.tvCam = -1;
  R.scene.fog.near = 420 * 1.2; R.scene.fog.far = 420 * 4;

  $("r-focus").innerHTML = d.drivers.map((x) => `<button data-v="${x.code}" style="--c:${colorOf(x.code)}" class="${x.code === opts.focus ? "on" : ""}">${x.code}</button>`).join("");
  buildBoard();
  paintTrack();
  setCam(opts.cam);
}

function stripMesh(A, B, color, closed) {
  const n = A.length, p = new Float32Array(n * 6), idx = [];
  for (let i = 0; i < n; i++) p.set([A[i].x, A[i].y, A[i].z, B[i].x, B[i].y, B[i].z], i * 6);
  for (let i = 0; i < n - 1; i++) { const a = i * 2; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
  if (closed) { const a = (n - 1) * 2; idx.push(a, a + 1, 0, a + 1, 1, 0); }
  const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(p, 3)); g.setIndex(idx);
  return new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide }));
}

function checkerTexture() {
  const c = document.createElement("canvas"); c.width = 128; c.height = 32;
  const g = c.getContext("2d");
  for (let x = 0; x < 16; x++) for (let y = 0; y < 4; y++) { g.fillStyle = (x + y) % 2 ? "#111" : "#f2f2f2"; g.fillRect(x * 8, y * 8, 8, 8); }
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.magFilter = THREE.NearestFilter;
  return t;
}

function textSprite(text, color, bg, size) {
  const c = document.createElement("canvas"); c.width = 128; c.height = 56;
  const g = c.getContext("2d");
  g.fillStyle = bg; g.beginPath(); g.roundRect?.(6, 6, 116, 44, 10); if (!g.roundRect) g.rect(6, 6, 116, 44); g.fill();
  g.strokeStyle = color; g.lineWidth = 3; g.stroke();
  g.fillStyle = color; g.font = `700 ${size}px "Chakra Petch", "Titillium Web", sans-serif`; g.textAlign = "center"; g.textBaseline = "middle";
  g.fillText(text, 64, 30);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, depthWrite: false, fog: false }));
  sp.renderOrder = 10;
  return sp;
}

/** A generic open-wheel race car, built in metres facing +z. */
function buildCar(color) {
  const g = new THREE.Group();
  const paint = new THREE.MeshStandardMaterial({ color, metalness: 0.45, roughness: 0.32, emissive: color, emissiveIntensity: 0.22 });
  const carbon = new THREE.MeshStandardMaterial({ color: 0x15171c, metalness: 0.35, roughness: 0.55 });
  const rubber = new THREE.MeshStandardMaterial({ color: 0x0b0b0d, roughness: 0.95 });
  const box = (w, h, l, mat, x, y, z) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, l), mat); m.position.set(x, y, z); g.add(m); return m; };
  box(1.4, 0.05, 3.4, carbon, 0, 0.06, -0.1);          // floor
  box(0.9, 0.42, 3.4, paint, 0, 0.4, 0.15);            // monocoque
  box(0.42, 0.26, 1.5, paint, 0, 0.32, 2.5);           // nose
  box(1.9, 0.06, 0.5, carbon, 0, 0.1, 3.15);           // front wing
  box(1.9, 0.05, 0.28, paint, 0, 0.19, 3.0);           // front flap
  box(1.55, 0.36, 1.7, paint, 0, 0.36, -0.3);          // sidepods
  box(0.46, 0.52, 1.7, paint, 0, 0.78, -0.55);         // engine cover / airbox
  box(1.05, 0.06, 0.42, carbon, 0, 0.98, -2.25);       // rear wing
  box(1.05, 0.05, 0.3, paint, 0, 1.09, -2.2);          // rear flap
  box(0.05, 0.6, 0.7, carbon, 0.53, 0.78, -2.2);       // end plates
  box(0.05, 0.6, 0.7, carbon, -0.53, 0.78, -2.2);
  box(0.08, 0.08, 0.55, carbon, 0, 0.92, 0.72);        // halo
  const helmet = new THREE.Mesh(new THREE.SphereGeometry(0.17, 16, 12), new THREE.MeshStandardMaterial({ color: 0xf2f2f2, metalness: 0.2, roughness: 0.3 }));
  helmet.position.set(0, 0.75, 0.5); g.add(helmet);
  const wheels = [], glows = [];
  const wheelGeo = new THREE.CylinderGeometry(0.36, 0.36, 0.38, 20);
  const glowMat = new THREE.MeshBasicMaterial({ color: 0xff7a1a, transparent: true, opacity: 0 });
  for (const [x, z, w] of [[0.82, 1.75, 1], [-0.82, 1.75, 1], [0.84, -1.55, 1.15], [-0.84, -1.55, 1.15]]) {
    const wh = new THREE.Mesh(wheelGeo, rubber); wh.rotation.z = Math.PI / 2; wh.scale.set(1, w, 1); wh.position.set(x, 0.36, z); g.add(wh); wheels.push(wh);
    const gl = new THREE.Mesh(new THREE.SphereGeometry(0.16, 10, 8), glowMat);
    gl.position.set(x * 0.72, 0.36, z); g.add(gl); glows.push(gl);
  }
  const light = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.09, 0.05), new THREE.MeshBasicMaterial({ color: 0xff2020 }));
  light.position.set(0, 0.45, -2.42); g.add(light);
  // soft blob shadow
  const sc = document.createElement("canvas"); sc.width = sc.height = 64;
  const sg = sc.getContext("2d"), grd = sg.createRadialGradient(32, 32, 2, 32, 32, 32);
  grd.addColorStop(0, "rgba(0,0,0,.75)"); grd.addColorStop(1, "rgba(0,0,0,0)"); sg.fillStyle = grd; sg.fillRect(0, 0, 64, 64);
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(2.6, 6.2), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(sc), transparent: true, depthWrite: false }));
  shadow.rotation.x = -Math.PI / 2; shadow.position.y = 0.01; g.add(shadow);
  return { group: g, wheels, glows, glowMat, light };
}

// ======================================================================== colouring
function paintTrack() {
  if (!S) return;
  const d = S.d, ref = d.drivers[0].trace, col = S.trackMesh.geometry.attributes.color;
  let mode = opts.color;
  if (mode === "dominance" && !d.dominance) mode = "speed";
  const V = fillNulls(ref.Speed), D = ref.Distance;
  const vmin = Math.min(...V), vmax = Math.max(...V);
  const TH = fillNulls(ref.Throttle || V.map(() => 0));
  for (let i = 0; i < S.n; i++) {
    const s = S.sd[i];
    let hex;
    if (mode === "speed") hex = speedColor((interp(D, V, s) - vmin) / (vmax - vmin || 1));
    else if (mode === "dominance") {
      const sec = d.dominance.sectors.find((z) => s >= z.start && s <= z.end) || d.dominance.sectors.at(-1);
      hex = colorOf(sec.winner);
    } else if (mode === "pedals") {
      const k = Math.min(ref.Brake.length - 1, Math.max(0, D.findIndex((v) => v >= s)));
      const th = interp(D, TH, s);
      hex = ref.Brake[k] ? "#ff2a2a" : th >= 98 ? "#2bd96b" : th > 15 ? "#e0b81f" : "#5b6270";
    } else hex = "#262b34";
    const [r, g, b] = lin(hex);
    col.setXYZ(i * 2, r, g, b); col.setXYZ(i * 2 + 1, r, g, b);
  }
  col.needsUpdate = true;
  // legend
  const lg = $("r-legend");
  if (mode === "speed") lg.innerHTML = `Speed · ${d.drivers[0].code}<div class="lg-bar" style="background:linear-gradient(90deg,${[0, .25, .5, .75, 1].map(speedColor).join(",")})"></div><div class="lg-ends"><span>${vmin.toFixed(0)}</span><span>${vmax.toFixed(0)} km/h</span></div>`;
  else if (mode === "dominance") lg.innerHTML = `Fastest per mini-sector${d.drivers.map((x) => `<div class="lg-item" style="--c:${colorOf(x.code)}"><i></i>${x.code} · ${d.dominance.share[x.code] || 0}</div>`).join("")}`;
  else if (mode === "pedals") lg.innerHTML = `Pedals · ${d.drivers[0].code}${[["#2bd96b", "full throttle"], ["#e0b81f", "partial"], ["#5b6270", "coasting"], ["#ff2a2a", "braking"]].map(([c, t]) => `<div class="lg-item" style="--c:${c}"><i></i>${t}</div>`).join("")}`;
  else lg.innerHTML = "";
  lg.style.display = lg.innerHTML ? "" : "none";
}

// ======================================================================== HUD
function buildBoard() {
  $("r-board").innerHTML = S.cars.map((c) => `
    <div class="hb-row" data-code="${c.code}" style="--c:${c.color}">
      <span class="hb-pos">1</span><span class="hb-code">${c.code}</span><span class="hb-gap">—</span>
      <div class="hb-tele"><span class="hb-spd"><b>0</b> km/h</span><span class="hb-gear">N</span>
        <span class="hb-pedals"><i class="thr" style="--pc:var(--good);--v:0%"></i><i class="brk" style="--pc:var(--bad);--v:0%"></i></span></div>
    </div>`).join("");
  S.rows = Object.fromEntries([...$("r-board").children].map((el) => [el.dataset.code, {
    el, pos: el.querySelector(".hb-pos"), gap: el.querySelector(".hb-gap"), spd: el.querySelector(".hb-spd b"),
    gear: el.querySelector(".hb-gear"), thr: el.querySelector(".thr"), brk: el.querySelector(".brk"),
  }]));
}

function updateHud() {
  const t = R.t, refCar = S.cars[0];
  const ct = Math.min(t, S.maxT), mm = Math.floor(ct / 60);
  $("r-clock").textContent = `${mm}:${(ct - mm * 60).toFixed(3).padStart(6, "0")}`;
  $("r-sub").textContent = `${opts.speed}× · ${refCar.code} reference lap ${fmtLap(refCar.end)}`;
  const ranking = S.cars.map((c) => {
    const done = t >= c.end;
    const gap = c === refCar ? 0 : done && t >= refCar.end ? c.end - refCar.end : t - interp(S.refD, S.refT, c.dist);
    return { c, done, gap, key: done ? -1e6 + c.end : -c.dist };
  }).sort((a, b) => a.key - b.key);
  ranking.forEach((r, k) => {
    const row = S.rows[r.c.code]; if (!row) return;
    row.el.style.order = String(k);
    row.pos.textContent = String(k + 1);
    row.el.classList.toggle("focus", r.c.code === opts.focus);
    if (r.c === refCar) { row.gap.textContent = r.done ? "FINISH" : "REF"; row.gap.className = "hb-gap"; }
    else { row.gap.textContent = fmtGap(r.gap); row.gap.className = `hb-gap ${r.gap > 0 ? "pos" : "neg"}`; }
    const i = r.c.idx ?? 0;
    row.spd.textContent = Math.round(r.c.V[i]);
    row.gear.textContent = r.c.G[i] ? Math.round(r.c.G[i]) : "N";
    row.thr.style.setProperty("--v", `${clamp(r.c.TH[i], 0, 100)}%`);
    row.brk.style.setProperty("--v", r.c.B[i] ? "100%" : "0%");
  });
  $("r-scrub").value = String(Math.round((t / S.maxT) * 1000));
  drawMinimap();
}

function drawMinimap() {
  const cv = $("r-minimap"), g = cv.getContext("2d"), W = cv.width, H = cv.height, m = S.mini;
  const span = Math.max(m.maxX - m.minX, m.maxY - m.minY) || 1, k = (W - 40) / span;
  const ox = (W - (m.maxX - m.minX) * k) / 2, oy = (H - (m.maxY - m.minY) * k) / 2;
  const tx = (x) => ox + (x - m.minX) * k, ty = (y) => H - (oy + (y - m.minY) * k);
  g.clearRect(0, 0, W, H);
  g.lineJoin = "round"; g.strokeStyle = "#3a4250"; g.lineWidth = 6; g.beginPath();
  for (let i = 0; i < m.X.length; i += 3) (i ? g.lineTo : g.moveTo).call(g, tx(m.X[i]), ty(m.Y[i]));
  g.closePath(); g.stroke();
  for (const c of [...S.cars].reverse()) {
    const i = c.idx ?? 0, d = c.D[i];
    const x = interp(S.refD, m.X, d), y = interp(S.refD, m.Y, d);
    g.beginPath(); g.arc(tx(x), ty(y), c.code === opts.focus ? 7 : 5.5, 0, Math.PI * 2);
    g.fillStyle = c.color; g.fill(); g.lineWidth = 2; g.strokeStyle = "#06070a"; g.stroke();
  }
}

// ======================================================================== frame
const _v = new THREE.Vector3(), _w = new THREE.Vector3(), _up = new THREE.Vector3(0, 1, 0);
function sampleW(c, t, out) {
  const T = c.T, n = T.length;
  if (t <= T[0]) return out.copy(c.W[0]);
  if (t >= T[n - 1]) return out.copy(c.W[n - 1]);
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (T[mid] <= t) lo = mid; else hi = mid; }
  return out.copy(c.W[lo]).lerp(c.W[hi], (t - T[lo]) / (T[hi] - T[lo] || 1));
}
function indexAt(c, t) {
  const T = c.T; let lo = 0, hi = T.length - 1;
  if (t <= T[0]) return 0; if (t >= T[hi]) return hi;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (T[mid] <= t) lo = mid; else hi = mid; }
  return lo;
}

function update(dt) {
  if (!S) return;
  if (opts.playing) {
    R.t += dt * opts.speed;
    if (R.t > S.maxT + 1.5) { R.t = 0; R.snap = true; }
  }
  const t = Math.min(R.t, S.maxT);
  for (const c of S.cars) {
    const tc = Math.min(t, c.end);
    sampleW(c, tc, c.pos);
    sampleW(c, Math.min(tc + 0.15, c.end), _v);
    if (_v.distanceToSquared(c.pos) > 1e-8) c.dir.copy(_v).sub(c.pos).normalize();
    else if (tc > 0.15) { sampleW(c, tc - 0.15, _w); if (_w.distanceToSquared(c.pos) > 1e-8) c.dir.copy(c.pos).sub(_w).normalize(); }
    c.idx = indexAt(c, tc);
    c.dist = c.D[c.idx];
    c.car.group.position.copy(c.pos);
    c.car.group.lookAt(_v.copy(c.pos).add(c.dir));
    const v = c.V[c.idx] / 3.6, braking = c.B[c.idx] && t < c.end;
    for (const w of c.car.wheels) w.rotation.x += (opts.playing && t < c.end ? v * dt * opts.speed : 0) / 0.36;
    c.car.glowMat.opacity += ((braking ? 0.95 : 0) - c.car.glowMat.opacity) * Math.min(1, dt * 12);
    c.car.light.material.color.setHex(braking || (Math.floor(R.t * 4) % 2 && t >= c.end) ? 0xff3030 : 0x551010);
    c.label.visible = !(["onboard", "chase"].includes(currentCam()) && c.code === opts.focus);
    // trail
    const arr = c.trail.geometry.attributes.position.array;
    for (let k = 0; k < c.trailN; k++) { sampleW(c, Math.max(0, tc - k * 0.045), _w); arr[k * 3] = _w.x; arr[k * 3 + 1] = _w.y + 0.3 * S.wpm; arr[k * 3 + 2] = _w.z; }
    c.trail.geometry.attributes.position.needsUpdate = true;
  }
  updateCamera(dt);
  R.hudAt += dt;
  if (R.hudAt > 0.08) { R.hudAt = 0; updateHud(); }
}

function currentCam() {
  if (opts.cam !== "auto") return opts.cam;
  return AUTO_SEQ[R.autoIdx % AUTO_SEQ.length];
}

function updateCamera(dt) {
  const cam = R.camera;
  if (opts.cam === "auto") {
    R.autoAt += dt;
    if (R.autoAt > 6.5) { R.autoAt = 0; R.autoIdx++; R.snap = true; R.tvCam = -1; }
    $("r-camname").textContent = `AUTO · ${CAM_NAMES[currentCam()]}`;
  }
  const mode = currentCam();
  if (mode === "orbit") { R.controls.update(); return; }
  const c = S.cars.find((x) => x.code === opts.focus) || S.cars[0];
  const L = S.carLen, p = c.pos, f = c.dir;
  const flat = _w.copy(f).setY(0).normalize();
  let want, look, fov, k;
  if (mode === "chase") {
    want = p.clone().addScaledVector(flat, -2.4 * L).addScaledVector(_up, 0.75 * L);
    look = p.clone().addScaledVector(f, 1.6 * L).addScaledVector(_up, 0.2 * L); fov = 62; k = 6;
  } else if (mode === "onboard") {
    want = p.clone().addScaledVector(f, -0.02 * L).addScaledVector(_up, 0.2 * L);
    look = p.clone().addScaledVector(f, 4 * L).addScaledVector(_up, 0.12 * L); fov = 78; k = 30;
  } else if (mode === "heli") {
    want = p.clone().addScaledVector(flat, -11 * L).addScaledVector(_up, 6 * L);
    look = p.clone(); fov = 46; k = 2.5;
  } else { // tv
    let best = 0, bd = Infinity;
    S.tvSpots.forEach((s, i) => { const dd = s.pos.distanceToSquared(p); if (dd < bd) { bd = dd; best = i; } });
    if (best !== R.tvCam) { R.tvCam = best; R.snap = true; }
    want = S.tvSpots[best].pos; look = p.clone();
    fov = clamp((2 * Math.atan((4 * L) / (2 * Math.sqrt(bd))) * 180) / Math.PI, 6, 45); k = 8;
  }
  const a = R.snap ? 1 : 1 - Math.exp(-dt * k);
  cam.position.lerp(want, a);
  R.look.lerp(look, R.snap ? 1 : 1 - Math.exp(-dt * Math.max(k, 8)));
  cam.lookAt(R.look);
  cam.fov += (fov - cam.fov) * (R.snap ? 1 : Math.min(1, dt * 4)); cam.updateProjectionMatrix();
  R.snap = false;
}

function loop() {
  if (!visible || !R) { if (R) R.raf = 0; return; }
  R.raf = requestAnimationFrame(loop);
  const dt = Math.min(0.1, R.clock.getDelta());
  update(dt);
  R.renderer.render(R.scene, R.camera);
}

export function setReplayVisible(v) {
  visible = v;
  if (v && R && !R.raf) { R.clock.getDelta(); resize(); R.raf = requestAnimationFrame(loop); }
}
export const replayReady = () => !!R;
