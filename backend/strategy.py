"""Race strategy models, computed only from what has happened up to time t.

* Tyre degradation per compound (fuel-corrected, pooled within-stint slopes,
  shrunk towards sensible priors while data is thin).
* Pit loss under green, VSC and safety car.
* Where a car would rejoin if it pitted now.
* Undercut / overcut outcomes for every close battle.
* Pit-window optimiser (best lap to stop, per compound).
* ERS battery estimate — teams do not publish real state-of-charge data, so
  this is a physics model driven by speed, throttle and brake. It is labelled
  as an estimate everywhere it is shown.

Every assumption is a named constant below.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from .livecore import RaceData, lap_history, snapshot

FUEL_PER_LAP = 0.055          # s/lap gained as fuel burns off (~1.7 kg/lap × ~0.032 s/kg)
DRY = ("SOFT", "MEDIUM", "HARD")
PRIOR_DEG = {"SOFT": 0.09, "MEDIUM": 0.055, "HARD": 0.035, "INTERMEDIATE": 0.07, "WET": 0.05}
PRIOR_PACE = {"SOFT": -0.45, "MEDIUM": 0.0, "HARD": 0.35, "INTERMEDIATE": 4.0, "WET": 7.0}
WARMUP = {"SOFT": 0.25, "MEDIUM": 0.5, "HARD": 0.9, "INTERMEDIATE": 0.6, "WET": 0.6}
PRIOR_WEIGHT = 8              # laps of "virtual evidence" behind each prior
DEFAULT_PIT_LOSS = 22.0
VSC_RATIO = 0.60              # pit loss under VSC ≈ 60% of green
SC_RATIO = 0.50               # under a safety car ≈ 50% of green
TRAFFIC_GAP = 1.0             # s — rejoining closer than this to a car ahead = dirty air


# =========================================================================== laps
def _clean(history: list[dict]) -> list[dict]:
    by = defaultdict(list)
    for r in history:
        if r["lap"] > 1 and not r["neutral"] and not r["pit_in"] and not r["pit_out"] and r["time"] and r.get("compound"):
            by[r["code"]].append(r)
    out = []
    for rows in by.values():
        med = float(np.median([r["time"] for r in rows]))
        out += [r for r in rows if r["time"] < med * 1.05]
    return out


def fit_degradation(history: list[dict]) -> dict:
    rows = _clean(history)
    stints = defaultdict(list)
    for r in rows:
        stints[(r["code"], r["stint"], r["compound"])].append(r)
    fits = {}
    for (code, stint, comp), rs in stints.items():
        if len(rs) < 3:
            continue
        a = np.array([r["age"] for r in rs], float)
        tc = np.array([r["time"] + FUEL_PER_LAP * r["lap"] for r in rs], float)
        if np.ptp(a) < 2:
            continue
        slope, icpt = np.polyfit(a, tc, 1)
        fits[(code, stint, comp)] = {"slope": float(np.clip(slope, -0.05, 0.6)), "base": float(icpt + slope), "n": len(rs),
                                     "ages": a, "tc": tc}

    compounds = {}
    seen = {k[2] for k in fits} | {r["compound"] for r in history if r.get("compound")}
    for comp in seen:
        fs = [f for (c, s, cp), f in fits.items() if cp == comp]
        n = sum(f["n"] for f in fs)
        prior = PRIOR_DEG.get(comp, 0.06)
        deg = (sum(f["slope"] * f["n"] for f in fs) + PRIOR_WEIGHT * prior) / (n + PRIOR_WEIGHT)
        compounds[comp] = {"deg": round(float(deg), 4), "n": n, "source": "measured" if n >= 12 else ("data + prior" if n else "prior")}

    # pace offsets between compounds, from drivers who ran more than one
    base_by = defaultdict(dict)
    for (code, stint, comp), f in fits.items():
        base_by[code].setdefault(comp, []).append(f["base"])
    ref = "MEDIUM" if "MEDIUM" in compounds else next(iter(compounds), "MEDIUM")
    for comp in compounds:
        diffs = [np.mean(b[comp]) - np.mean(b[ref]) for b in base_by.values() if comp in b and ref in b]
        prior = PRIOR_PACE.get(comp, 0) - PRIOR_PACE.get(ref, 0)
        pace = (sum(diffs) + 2 * prior) / (len(diffs) + 2) if comp != ref else 0.0
        compounds[comp]["pace"] = round(float(pace), 3)
    for comp in DRY:
        compounds.setdefault(comp, {"deg": PRIOR_DEG[comp], "n": 0, "source": "prior",
                                    "pace": PRIOR_PACE[comp] - PRIOR_PACE.get(ref, 0)})

    points = defaultdict(list)          # time lost vs the same stint's fresh-tyre pace
    for (code, stint, comp), f in fits.items():
        for a, t in zip(f["ages"], f["tc"]):
            points[comp].append([int(a), round(float(t - f["base"]), 3), code])
    stint_fit = {code: {"compound": comp, "stint": stint, "slope": round(f["slope"], 4), "n": f["n"]}
                 for (code, stint, comp), f in fits.items()}
    return {"compounds": compounds, "stints": stint_fit, "points": dict(points), "fits": fits}


class DriverModel:
    """Lap time = P + pace[compound] + deg·(age−1) − fuel·lap."""

    def __init__(self, code: str, history: list[dict], deg: dict):
        self.code = code
        self.comp = deg["compounds"]
        rows = [r for r in _clean(history) if r["code"] == code]
        own = {(k[2]): f for k, f in deg["fits"].items() if k[0] == code}
        self.own_deg = {}
        for comp, f in own.items():
            cdeg = self.comp.get(comp, {}).get("deg", PRIOR_DEG.get(comp, 0.06))
            self.own_deg[comp] = (f["slope"] * f["n"] + 5 * cdeg) / (f["n"] + 5)
        vals = [r["time"] + FUEL_PER_LAP * r["lap"] - self.pace(r["compound"]) - self.deg(r["compound"]) * (r["age"] - 1) for r in rows]
        if not vals:
            all_rows = [r for r in history if r["code"] == code and r["time"]]
            vals = [float(np.median([r["time"] + FUEL_PER_LAP * r["lap"] for r in all_rows]))] if all_rows else [90.0]
        self.P = float(np.median(vals))

    def pace(self, comp):
        return self.comp.get(comp, {}).get("pace", PRIOR_PACE.get(comp, 0.0))

    def deg(self, comp):
        return self.own_deg.get(comp, self.comp.get(comp, {}).get("deg", PRIOR_DEG.get(comp, 0.06)))

    def lap(self, comp, age, lap):
        return self.P + self.pace(comp) + self.deg(comp) * (age - 1) - FUEL_PER_LAP * lap


# =========================================================================== pit loss
def estimate_pit_loss(history: list[dict]) -> dict:
    by = defaultdict(dict)
    for r in history:
        by[r["code"]][r["lap"]] = r
    samples = []
    for code, laps in by.items():
        clean = [r["time"] for r in laps.values() if not (r["pit_in"] or r["pit_out"] or r["neutral"] or r["lap"] == 1)]
        if len(clean) < 3:
            continue
        for n, r in laps.items():
            nxt = laps.get(n + 1)
            if r["pit_in"] and nxt and nxt["pit_out"] and not r["neutral"] and not nxt["neutral"]:
                near = [x["time"] for k, x in laps.items() if abs(k - n) <= 5 and not (x["pit_in"] or x["pit_out"] or x["neutral"] or k == 1)]
                ref = float(np.median(near or clean))
                loss = r["time"] + nxt["time"] - 2 * ref
                if 8 < loss < 45:
                    samples.append(loss)
    green = float(np.median(samples)) if samples else DEFAULT_PIT_LOSS
    return {"green": round(green, 2), "vsc": round(green * VSC_RATIO, 2), "sc": round(green * SC_RATIO, 2),
            "samples": len(samples), "source": f"measured from {len(samples)} stop{'s' if len(samples) != 1 else ''}" if samples else "default (no clean stops yet)"}


# =========================================================================== rejoin
def rejoin(rows: list[dict], code: str, cost: float) -> dict | None:
    running = [r for r in rows if r["status"] in ("RUN", "PIT")]
    me = next((r for r in running if r["code"] == code), None)
    if me is None:
        return None
    gap_of = lambda r: 0.0 if r["pos"] == running[0]["pos"] else (r["gap"] if r["gap"] is not None else None)
    my_gap = gap_of(me)
    if my_gap is None:
        return None
    proj = my_gap + cost
    others = [(r["code"], gap_of(r)) for r in running if r["code"] != code and gap_of(r) is not None]
    ahead = max(((c, g) for c, g in others if g < proj), key=lambda x: x[1], default=None)
    behind = min(((c, g) for c, g in others if g >= proj), key=lambda x: x[1], default=None)
    pos = 1 + sum(1 for _, g in others if g < proj)
    return {"pos": pos, "places_lost": pos - me["pos"], "cost": round(cost, 2), "projected_gap": round(proj, 2),
            "ahead": ahead[0] if ahead else None, "gap_ahead": round(proj - ahead[1], 2) if ahead else None,
            "behind": behind[0] if behind else None, "gap_behind": round(behind[1] - proj, 2) if behind else None,
            "traffic": bool(ahead and proj - ahead[1] < TRAFFIC_GAP)}


# =========================================================================== compounds
def next_compound(used: set, current: str, laps_left: int) -> str:
    dry_used = {c for c in used if c in DRY}
    if current not in DRY:
        return current
    if laps_left > 22:
        pick = "HARD"
    elif laps_left > 12:
        pick = "MEDIUM"
    else:
        pick = "SOFT"
    if len(dry_used) < 2 and pick == current:
        pick = next(c for c in ("HARD", "MEDIUM", "SOFT") if c != current)
    return pick


# =========================================================================== battles
def battle(ahead: dict, behind: dict, gap: float, mA: DriverModel, mB: DriverModel, lap: int,
           newA: str, newB: str, max_k: int = 4) -> dict:
    """Gap after both cars have stopped (positive = `behind` still behind).

    Undercut: `behind` pits now, `ahead` reacts k laps later.
    Overcut:  `ahead` pits now, `behind` stays out k more laps.
    Pit-lane loss is the same for both and cancels out.
    """
    compA, ageA, compB, ageB = ahead["compound"], ahead["tyre_age"] or 1, behind["compound"], behind["tyre_age"] or 1
    under, over = [], []
    g_u = g_o = gap
    for j in range(1, max_k + 1):
        tA_old, tB_old = mA.lap(compA, ageA + j, lap + j), mB.lap(compB, ageB + j, lap + j)
        tB_new = mB.lap(newB, j, lap + j) + (WARMUP.get(newB, 0.5) if j == 1 else 0)
        tA_new = mA.lap(newA, j, lap + j) + (WARMUP.get(newA, 0.5) if j == 1 else 0)
        g_u -= tA_old - tB_new
        g_o -= tA_new - tB_old
        under.append({"k": j, "gap_after": round(g_u, 2)})
        over.append({"k": j, "gap_after": round(g_o, 2)})
    fresh_gain = round(mA.lap(compA, ageA + 1, lap + 1) - mB.lap(newB, 1, lap + 1), 2)
    if under[0]["gap_after"] < 0:
        verdict, tone = "UNDERCUT ON", "go"
    elif any(u["gap_after"] < 0 for u in under):
        k = next(u["k"] for u in under if u["gap_after"] < 0)
        verdict, tone = f"UNDERCUT if {ahead['code']} stays out {k}+ laps", "maybe"
    elif any(o["gap_after"] < 0 for o in over):
        verdict, tone = "OVERCUT FAVOURED", "maybe"
    elif gap < 1.0:
        verdict, tone = "DRS RANGE — attack on track", "info"
    else:
        verdict, tone = "HOLD — gap too big", "hold"
    return {"ahead": ahead["code"], "behind": behind["code"], "gap": round(gap, 3), "lap": lap,
            "tyres": {"ahead": [compA, ageA], "behind": [compB, ageB]}, "new": {"ahead": newA, "behind": newB},
            "fresh_tyre_gain": fresh_gain, "undercut": under, "overcut": over, "verdict": verdict, "tone": tone}


# =========================================================================== pit window
def pit_window(m: DriverModel, comp: str, age: int, lap: int, total: int, loss: float, used: set) -> dict:
    left = total - lap
    if left <= 1:
        return {"curves": {}, "best": None, "no_stop": None, "two_compound_rule": False}
    old = [m.lap(comp, age + i, lap + i) for i in range(1, left + 1)]
    cum_old = np.concatenate([[0], np.cumsum(old)])
    curves, best = {}, None
    must_stop = len({c for c in used if c in DRY}) < 2 and comp in DRY
    for new in DRY:
        if new == comp and must_stop:
            continue
        pts = []
        for p in range(0, left):                      # pit at the end of lap `lap + p`
            rest = sum(m.lap(new, i, lap + p + i) for i in range(1, left - p + 1)) + WARMUP.get(new, 0.5)
            pts.append((lap + p, cum_old[p] + loss + rest))
        curves[new] = pts
        b = min(pts, key=lambda x: x[1])
        if best is None or b[1] < best["total"]:
            best = {"compound": new, "lap": b[0], "total": b[1]}
    no_stop = float(cum_old[-1])
    floor = min([best["total"]] + ([] if must_stop else [no_stop])) if best else no_stop
    out_curves = {c: [[int(l), round(float(v - floor), 2)] for l, v in pts] for c, pts in curves.items()}
    if best:
        best = {**best, "lap": int(best["lap"]), "total": round(float(best["total"] - floor), 2), "vs_no_stop": round(float(best["total"] - no_stop), 2)}
    return {"curves": out_curves, "best": best, "no_stop": None if must_stop else round(float(no_stop - floor), 2), "two_compound_rule": must_stop}


# =========================================================================== ERS estimate
ERS = {"capacity_mj": 4.0, "p_max_kw": 350.0, "mass_kg": 800.0, "regen_share": 0.55, "eff": 0.85,
       "coast_kw": 120.0, "taper_from": 290.0, "taper_to": 345.0, "soc0": 0.6,
       "deploy_cap_mj_lap": 4.0, "harvest_cap_mj_lap": 4.0}


def ers_series(t, speed, throttle, brake, lap_starts=(), p=ERS):
    """State-of-charge estimate (0–1) and mode (+1 deploy, −1 harvest, 0 idle).

    Harvest under braking = min(P_max, eff × share × m·|a|·v); light harvest when coasting.
    Deploy at P_max on full throttle above 100 km/h, tapering at very high speed,
    with each lap's deployment budgeted from the energy harvested the lap before.
    Model only — real ERS data is not published.
    """
    t = np.asarray(t, float); v = np.nan_to_num(np.asarray(speed, float)) / 3.6
    thr = np.nan_to_num(np.asarray(throttle, float)); brk = np.nan_to_num(np.asarray(brake, float))
    n = len(t)
    soc, mode = np.empty(n), np.zeros(n, dtype=np.int8)
    if n == 0:
        return soc, mode
    dt = np.diff(t, prepend=t[0]); dt = np.clip(dt, 0, 1.0)
    a = np.gradient(v, t) if n > 2 else np.zeros(n)
    cap = p["capacity_mj"] * 1e6; pmax = p["p_max_kw"] * 1e3
    dcap, hcap = p["deploy_cap_mj_lap"] * 1e6, p["harvest_cap_mj_lap"] * 1e6
    starts = np.sort(np.asarray(lap_starts, float)); lap_idx = np.searchsorted(starts, t, side="right")
    s, used_d, used_h, cur, budget = p["soc0"], 0.0, 0.0, -1, dcap * 0.5
    for i in range(n):
        if lap_idx[i] != cur:
            # energy-neutral driving: deploy roughly what was harvested last lap, plus any surplus above 50% SoC
            budget = min(dcap, max(0.5e6, used_h) + max(0.0, s - 0.5) * cap) if cur >= 0 else dcap * 0.5
            cur, used_d, used_h = lap_idx[i], 0.0, 0.0
        kmh = v[i] * 3.6
        P = 0.0
        if brk[i] > 0 and a[i] < 0 and used_h < hcap:
            P = -min(pmax, p["eff"] * p["regen_share"] * p["mass_kg"] * -a[i] * v[i])
        elif thr[i] < 5 and kmh > 70 and used_h < hcap:
            P = -p["coast_kw"] * 1e3
        elif thr[i] >= 95 and kmh > 100 and s > 0.01 and used_d < budget:
            taper = 1.0 if kmh < p["taper_from"] else max(0.0, 1 - (kmh - p["taper_from"]) / (p["taper_to"] - p["taper_from"]))
            P = pmax * taper
        e = P * dt[i]
        if e > 0:
            e = min(e, s * cap); used_d += e; mode[i] = 1 if e > 0 else 0
        elif e < 0:
            e = max(e, -(1 - s) * cap); used_h -= e; mode[i] = -1 if e < 0 else 0
        s = min(1.0, max(0.0, s - e / cap))
        soc[i] = s
    return soc, mode


_ers_cache: dict = {}


def ers_at(rd: RaceData, code: str, t: float, window: float = 30.0) -> dict | None:
    c = rd.car.get(code)
    if not c or not len(c.get("t", [])):
        return None
    key = (id(rd), code, len(c["t"]))
    if key not in _ers_cache:
        starts = [l["start"] for l in rd.laps.get(code, []) if l.get("start") is not None]
        _ers_cache[key] = ers_series(c["t"], c["speed"], c["throttle"], c["brake"], starts)
    soc, mode = _ers_cache[key]
    tt = c["t"]
    i = int(np.searchsorted(tt, t, side="right")) - 1
    if i < 0:
        return None
    a = int(np.searchsorted(tt, t - window))
    step = max(1, (i + 1 - a) // 120)
    lap_rows = [l for l in rd.laps.get(code, []) if l.get("start") is not None and l["start"] <= t]
    lap_start = lap_rows[-1]["start"] if lap_rows else tt[0]
    j = int(np.searchsorted(tt, lap_start))
    seg = mode[j:i + 1]
    dts = np.diff(tt[j:i + 1], prepend=tt[j]) if i >= j else np.array([])
    return {"soc": round(float(soc[i]) * 100, 1), "mode": int(mode[i]),
            "deploy_s_lap": round(float(dts[seg == 1].sum()), 1) if len(seg) else 0.0,
            "harvest_s_lap": round(float(dts[seg == -1].sum()), 1) if len(seg) else 0.0,
            "trace": {"t": [round(float(x) - t, 2) for x in tt[a:i + 1:step]], "soc": [round(float(x) * 100, 1) for x in soc[a:i + 1:step]]},
            "estimate": True}


# =========================================================================== orchestrator
def live_strategy(rd: RaceData, t: float, focus: list[str] | None = None) -> dict:
    snap = snapshot(rd, t)
    hist = lap_history(rd, t)
    deg = fit_degradation(hist)
    loss = estimate_pit_loss(hist)
    code_status = snap["status"]["code"]
    now_mode = "sc" if code_status == "4" else "vsc" if code_status in ("6", "7") else "green"
    rows = snap["drivers"]
    total, lap_now = rd.total_laps, snap["lap"]
    used = defaultdict(set)
    for r in hist:
        if r.get("compound"):
            used[r["code"]].add(r["compound"])
    models = {r["code"]: DriverModel(r["code"], hist, deg) for r in rows if r["status"] in ("RUN", "PIT")}

    drivers = {}
    for r in rows:
        if r["code"] not in models:
            continue
        m = models[r["code"]]
        comp, age = r["compound"] or "MEDIUM", r["tyre_age"] or 1
        laps_left = max(0, total - r["laps_done"])
        if r["compound"]:
            used[r["code"]].add(r["compound"])
        nxt = next_compound(used[r["code"]], comp, laps_left)
        d = {
            "deg": round(m.deg(comp), 3), "pace_model": round(m.lap(comp, age, r["laps_done"] + 1), 3),
            "next_laps": [round(m.lap(comp, age + i, r["laps_done"] + i), 3) for i in range(1, 6)],
            "next_compound": nxt, "compounds_used": sorted(used[r["code"]]),
            "rejoin": {k: rejoin(rows, r["code"], loss[k]) for k in ("green", "vsc", "sc")},
        }
        if focus and r["code"] in focus:
            d["pit_window"] = pit_window(m, comp, age, r["laps_done"], total, loss[now_mode], used[r["code"]])
            d["ers"] = ers_at(rd, r["code"], t)
        drivers[r["code"]] = d

    battles = []
    running = [r for r in rows if r["code"] in models and r["status"] == "RUN"]
    for a, b in zip(running, running[1:]):
        if b["interval"] is None or b["interval"] > 8 or b["laps_down"] != a["laps_down"]:
            continue
        battles.append(battle(a, b, b["interval"], models[a["code"]], models[b["code"]], b["laps_done"],
                              drivers[a["code"]]["next_compound"], drivers[b["code"]]["next_compound"]))

    deg_public = {"compounds": deg["compounds"], "stints": deg["stints"], "points": deg["points"]}
    return {"t": snap["t"], "lap": lap_now, "total_laps": total, "status": snap["status"], "mode": now_mode,
            "pit_loss": loss, "cheap_stop_saving": {"vsc": round(loss["green"] - loss["vsc"], 2), "sc": round(loss["green"] - loss["sc"], 2)},
            "degradation": deg_public, "drivers": drivers, "battles": battles, "fuel_per_lap": FUEL_PER_LAP,
            "assumptions": {"vsc_ratio": VSC_RATIO, "sc_ratio": SC_RATIO, "warmup": WARMUP, "ers": ERS}}
