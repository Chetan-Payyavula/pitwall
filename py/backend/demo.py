"""Synthetic but physically plausible telemetry for offline/demo use.

Builds a fictional closed circuit, then a speed profile for each driver using
a grip-limited cornering speed plus forward (traction) and backward (braking)
passes — the same method lap-time simulators use. Small per-driver differences
in grip, braking and traction make the dominance map and delta interesting.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

G = 9.81
DS = 5.0  # metres between samples

DEMO_DRIVERS = {
    # code: (full name, team, colour, number, grip, brake decel, traction, grip-wave phase)
    "ALO": ("Fernando Alonso", "Demo Racing Green", "#229971", 14, 1.000, 47.0, 1.000, 0.0),
    "NOR": ("Lando Norris", "Demo Racing Orange", "#ff8700", 4, 1.003, 45.5, 1.012, 2.1),
    "PIA": ("Oscar Piastri", "Demo Racing Orange", "#ff8700", 81, 1.001, 46.5, 1.008, 2.9),
    "LEC": ("Charles Leclerc", "Demo Racing Red", "#e8002d", 16, 0.996, 48.5, 0.990, 4.2),
    "HAM": ("Lewis Hamilton", "Demo Racing Red", "#e8002d", 44, 0.998, 47.5, 0.985, 1.0),
    "VER": ("Max Verstappen", "Demo Racing Navy", "#3671c6", 1, 1.002, 47.8, 1.004, 5.0),
    "RUS": ("George Russell", "Demo Racing Silver", "#27f4d2", 63, 0.999, 46.8, 0.998, 3.6),
    "SAI": ("Carlos Sainz", "Demo Racing Blue", "#64c4ff", 55, 0.995, 47.2, 0.992, 0.6),
}


# (kind, length_or_radius, angle_deg). Left turns positive; angles sum to 360.
LAYOUT = [
    ("S", 950, 0), ("C", 55, 95), ("S", 380, 0), ("C", 110, -50), ("C", 140, 65),
    ("S", 620, 0), ("C", 35, 125), ("S", 520, 0), ("C", 220, -35), ("C", 75, 70),
    ("S", 760, 0), ("C", 65, 85), ("S", 260, 0), ("C", 400, 5),
]


def _circuit():
    """Build the track by 'turtle' from straights and constant-radius arcs."""
    xs, ys, ks, labels = [0.0], [0.0], [0.0], []
    heading, dist = 0.0, 0.0
    step = 0.5
    for kind, a, ang in LAYOUT:
        if kind == "S":
            n, curv = int(a / step), 0.0
        else:
            n = int(abs(np.radians(ang)) * a / step)
            curv = np.sign(ang) / a
            labels.append((dist + n * step / 2, a))
        for _ in range(n):
            heading += curv * step
            xs.append(xs[-1] + np.cos(heading) * step)
            ys.append(ys[-1] + np.sin(heading) * step)
            ks.append(abs(curv))
            dist += step
    xs, ys, ks = map(np.asarray, (xs, ys, ks))
    s = np.arange(len(xs)) * step
    length = float(s[-1])
    # close the loop: spread the small end-point gap linearly over the lap
    frac = s / length
    xs = xs - frac * (xs[-1] - xs[0])
    ys = ys - frac * (ys[-1] - ys[0])
    grid = np.arange(0, length, DS)
    gx, gy = np.interp(grid, s, xs), np.interp(grid, s, ys)
    k = np.interp(grid, s, ks)
    k = np.convolve(np.concatenate([k[-4:], k, k[:4]]), np.ones(9) / 9, mode="same")[4:-4]
    return grid, gx, gy, k, length, labels


def _speed_profile(grid, k, length, grip, decel, traction, phase):
    # grip varies a little around the lap so different drivers win different sectors
    wave = 1 + 0.05 * np.sin(2 * np.pi * grid / length * 3 + phase)
    mu = 2.6 * grip * wave  # effective lateral g incl. downforce
    v_corner = np.sqrt(mu * G / np.maximum(k, 1e-6))
    vmax = 93.0  # m/s ≈ 335 km/h
    v_lim = np.minimum(v_corner, vmax)

    n = len(grid)
    # run three laps so the start/finish wraps around correctly, keep the middle
    lim3 = np.tile(v_lim, 3)
    v = lim3.copy()
    for i in range(1, 3 * n):  # traction-limited acceleration
        a = 13.0 * traction * max(0.05, 1 - (v[i - 1] / vmax) ** 2)
        v[i] = min(v[i], np.sqrt(v[i - 1] ** 2 + 2 * a * DS))
    fwd = v.copy()
    for i in range(3 * n - 2, -1, -1):  # braking-limited deceleration
        b = decel * (0.55 + 0.45 * (v[i + 1] / vmax) ** 2)  # less aero → less braking at low speed
        v[i] = min(v[i], np.sqrt(v[i + 1] ** 2 + 2 * b * DS))
    v, fwd, lim = v[n:2 * n], fwd[n:2 * n], lim3[n:2 * n]

    braking = v < fwd - 0.3
    cornering = (~braking) & (v >= lim - 0.3) & (lim < vmax - 0.5)
    throttle = np.where(braking, 0.0, np.where(cornering, 55.0, 100.0))
    throttle = np.convolve(throttle, np.ones(3) / 3, mode="same").clip(0, 100)
    return v, braking, throttle


def _rpm(kmh, gear):
    bands = np.array([0, 95, 130, 165, 200, 235, 270, 300, 345])
    lo, hi = bands[gear - 1], bands[gear]
    return np.clip(9800 + 2300 * (kmh - lo) / (hi - lo), 6000, 12100)


def _gear(speed_kmh):
    bands = [0, 95, 130, 165, 200, 235, 270, 300]
    return np.clip(np.searchsorted(bands, speed_kmh, side="right"), 1, 8)


@lru_cache(maxsize=1)
def demo_circuit():
    grid, xs, ys, k, length, labels = _circuit()
    corners = []
    for d, radius in labels:
        if radius > 300:  # flat-out kink, not a numbered corner
            continue
        n = len(corners) + 1
        corners.append({"number": n, "label": f"T{n}", "distance": float(d),
                        "x": float(np.interp(d, grid, xs)), "y": float(np.interp(d, grid, ys))})
    return grid, xs, ys, k, length, corners


def demo_lap(code: str, seed: int = 0) -> pd.DataFrame:
    return _demo_lap(code, seed).copy()


@lru_cache(maxsize=32)
def _demo_lap(code: str, seed: int = 0) -> pd.DataFrame:
    name, team, colour, number, grip, decel, traction, phase = DEMO_DRIVERS[code]
    grid, xs, ys, k, length, _ = demo_circuit()
    v, braking, throttle = _speed_profile(grid, k, length, grip, decel, traction, phase)
    rng = np.random.default_rng(seed + sum(map(ord, code)))
    v = v * (1 + rng.normal(0, 0.002, len(v)))
    dt = DS / ((v + np.roll(v, -1)) / 2)
    time = np.concatenate([[0], np.cumsum(dt)[:-1]])
    kmh = v * 3.6
    gear = _gear(kmh)
    return pd.DataFrame({
        "Distance": grid,
        "Time": time,
        "Speed": kmh,
        "Throttle": throttle,
        "Brake": braking,
        "nGear": gear,
        "RPM": _rpm(kmh, gear),
        "DRS": np.zeros(len(grid), dtype=int),
        "X": xs,
        "Y": ys,
        "Z": _elevation(grid, length),
    })


def _elevation(grid, length):
    """Gentle hills in metres so the 3D view has something to show."""
    p = 2 * np.pi * grid / length
    return 7.0 + 6.0 * np.sin(p + 0.5) + 2.5 * np.sin(3 * p + 1.3)


def demo_lap_time(code: str) -> float:
    lap = demo_lap(code)
    return round(float(lap["Time"].iloc[-1] + 5.0 / (lap["Speed"].iloc[-1] / 3.6)), 3)


def demo_sectors(code: str) -> list[float]:
    lap = demo_lap(code)
    total = demo_lap_time(code)
    length = float(lap["Distance"].iloc[-1])
    b = np.interp([length / 3, 2 * length / 3], lap["Distance"], lap["Time"])
    s1, s2 = round(float(b[0]), 3), round(float(b[1] - b[0]), 3)
    return [s1, s2, round(total - s1 - s2, 3)]  # sectors add up exactly to the lap time


def demo_speed_traps(code: str) -> dict:
    lap = demo_lap(code)
    length = float(lap["Distance"].iloc[-1])
    sp = lambda d: float(np.interp(d, lap["Distance"], lap["Speed"]))
    return {"I1": sp(length * 0.30), "I2": sp(length * 0.62), "FL": sp(length - 10),
            "ST": float(lap["Speed"].max())}


def demo_race(n_laps: int = 24) -> dict[str, list[dict]]:
    """A simple race simulation: tyre wear, fuel burn, one pit stop each."""
    rng = np.random.default_rng(7)
    codes = list(DEMO_DRIVERS)
    base = {c: demo_lap_time(c) + 2.2 for c in codes}
    grid = sorted(codes, key=base.get)
    deg = {"SOFT": 0.085, "MEDIUM": 0.05, "HARD": 0.03}
    pace = {"SOFT": -0.35, "MEDIUM": 0.0, "HARD": 0.28}
    laps: dict[str, list[dict]] = {}
    cum = {}
    for gpos, c in enumerate(grid):
        first = "SOFT" if gpos % 3 == 2 else "MEDIUM"
        pit = int(rng.integers(8, 15)) if first == "SOFT" else int(rng.integers(11, 17))
        stint, comp, life, t, rows = 1, first, 1, 0.0, []
        for n in range(1, n_laps + 1):
            lt = base[c] + pace[comp] + deg[comp] * life - 0.045 * n + rng.normal(0, 0.12)
            if n == 1:
                lt += 3.0 + gpos * 0.28
            is_pit = n == pit
            if is_pit:
                lt += 19.5
            if n == pit + 1:
                lt += 1.2
            t += lt
            rows.append({"lap": n, "time": round(float(lt), 3), "compound": comp, "stint": stint,
                         "tyre_life": life, "pit": is_pit, "cum": t})
            if is_pit:
                stint, comp, life = 2, "HARD", 1
            else:
                life += 1
        laps[c] = rows
        cum[c] = [r["cum"] for r in rows]
    for n in range(n_laps):
        order = sorted(codes, key=lambda c: cum[c][n])
        for pos, c in enumerate(order, 1):
            laps[c][n]["position"] = pos
    for rows in laps.values():
        for r in rows:
            r.pop("cum")
    return laps
