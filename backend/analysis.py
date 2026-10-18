"""Pure telemetry analysis — no FastF1 imports, so it is easy to test.

Every function works on a "lap frame": a pandas DataFrame with at least
    Distance [m], Time [s from lap start], Speed [km/h], Brake [bool]
and optionally Throttle, nGear, RPM, DRS, X, Y.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def clean_lap(df: pd.DataFrame) -> pd.DataFrame:
    """Sort by distance, drop duplicates/NaNs in the key columns."""
    out = df.dropna(subset=["Distance", "Time"]).sort_values("Distance")
    out = out.drop_duplicates(subset="Distance", keep="first").reset_index(drop=True)
    return out


def common_grid(laps: list[pd.DataFrame], step: float = 5.0) -> np.ndarray:
    """Distance grid covered by every lap."""
    end = min(float(l["Distance"].max()) for l in laps)
    start = max(float(l["Distance"].min()) for l in laps)
    if end <= start:
        raise ValueError("Laps do not overlap in distance")
    return np.arange(start, end, step)


def time_at(lap: pd.DataFrame, distance: np.ndarray) -> np.ndarray:
    return np.interp(distance, lap["Distance"].to_numpy(float), lap["Time"].to_numpy(float))


# --------------------------------------------------------------------------- #
# lap time delta
# --------------------------------------------------------------------------- #


def lap_delta(ref: pd.DataFrame, cmp: pd.DataFrame, step: float = 5.0) -> dict:
    """Cumulative time gap of `cmp` relative to `ref` along the lap.

    Positive delta → `cmp` is behind (slower) at that point.
    """
    ref, cmp = clean_lap(ref), clean_lap(cmp)
    grid = common_grid([ref, cmp], step)
    delta = time_at(cmp, grid) - time_at(ref, grid)
    return {"distance": grid.round(1).tolist(), "delta": np.round(delta, 3).tolist()}


# --------------------------------------------------------------------------- #
# braking zones
# --------------------------------------------------------------------------- #


@dataclass
class BrakingZone:
    start: float          # distance where brake is applied [m]
    end: float            # distance where brake is released [m]
    entry_speed: float    # km/h at brake application
    min_speed: float      # apex / minimum speed [km/h]
    min_speed_at: float   # distance of minimum speed [m]
    duration: float       # seconds on the brake

    @property
    def length(self) -> float:
        return self.end - self.start

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 1),
            "end": round(self.end, 1),
            "length": round(self.length, 1),
            "entry_speed": round(self.entry_speed, 1),
            "min_speed": round(self.min_speed, 1),
            "min_speed_at": round(self.min_speed_at, 1),
            "duration": round(self.duration, 2),
            "speed_drop": round(self.entry_speed - self.min_speed, 1),
        }


def braking_zones(
    lap: pd.DataFrame,
    min_duration: float = 0.25,
    min_drop: float = 10.0,
    merge_gap: float = 15.0,
) -> list[BrakingZone]:
    """Find braking zones from the boolean Brake channel.

    * Gaps shorter than `merge_gap` metres are merged (brake signal flicker).
    * Zones shorter than `min_duration` s or losing < `min_drop` km/h are dropped.
    * Minimum speed is searched from brake-on until the car accelerates again,
      because the apex usually comes after the driver releases the pedal.
    """
    lap = clean_lap(lap)
    if "Brake" not in lap or lap.empty:
        return []
    brake = lap["Brake"].fillna(False).astype(bool).to_numpy()
    dist = lap["Distance"].to_numpy(float)
    time = lap["Time"].to_numpy(float)
    speed = lap["Speed"].to_numpy(float)

    # raw on/off runs
    edges = np.diff(brake.astype(int), prepend=0, append=0)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1  # inclusive index

    runs: list[list[int]] = []
    for s, e in zip(starts, ends):
        if runs and dist[s] - dist[runs[-1][1]] < merge_gap:
            runs[-1][1] = e
        else:
            runs.append([s, e])

    zones: list[BrakingZone] = []
    n = len(dist)
    for s, e in runs:
        # extend search for the minimum until speed starts rising clearly
        j = e
        while j + 1 < n and speed[j + 1] <= speed[j] + 0.5 and dist[j + 1] - dist[e] < 250:
            j += 1
        seg = slice(s, j + 1)
        k = s + int(np.argmin(speed[seg]))
        zone = BrakingZone(
            start=dist[s],
            end=dist[e],
            entry_speed=float(speed[max(s - 1, 0)] if s > 0 else speed[s]),
            min_speed=float(speed[k]),
            min_speed_at=float(dist[k]),
            duration=float(time[e] - time[s]),
        )
        zone.entry_speed = max(zone.entry_speed, float(speed[s]))
        if zone.duration >= min_duration and zone.entry_speed - zone.min_speed >= min_drop:
            zones.append(zone)
    return zones


def match_braking_zones(
    per_driver: dict[str, list[BrakingZone]],
    corners: list[dict] | None = None,
    tolerance: float = 120.0,
) -> list[dict]:
    """Line up braking zones of several drivers by location.

    The first driver is the reference. Each reference zone becomes a row;
    other drivers' zones are matched if their start is within `tolerance` m.
    If corner positions are known the row is labelled with the nearest corner.
    """
    if not per_driver:
        return []
    codes = list(per_driver)
    ref_code = codes[0]
    rows = []
    for rz in per_driver[ref_code]:
        row = {"at": round(rz.start, 1), "corner": None, "drivers": {ref_code: rz.as_dict()}}
        for code in codes[1:]:
            best = min(
                per_driver[code],
                key=lambda z: abs(z.start - rz.start),
                default=None,
            )
            if best is not None and abs(best.start - rz.start) <= tolerance:
                row["drivers"][code] = best.as_dict()
        if corners:
            near = min(corners, key=lambda c: abs(c["distance"] - rz.min_speed_at))
            if abs(near["distance"] - rz.min_speed_at) < 250:
                row["corner"] = near["label"]
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# track dominance (mini-sectors)
# --------------------------------------------------------------------------- #


def track_dominance(laps: dict[str, pd.DataFrame], n_sectors: int = 25) -> dict:
    """Split the lap into equal-distance mini-sectors; fastest driver per sector.

    Uses actual time spent in each sector (not average speed), so it is
    consistent with the lap delta. Path coordinates come from the first lap.
    """
    codes = list(laps)
    cleaned = {c: clean_lap(l) for c, l in laps.items()}
    grid = common_grid(list(cleaned.values()), step=1.0)
    bounds = np.linspace(grid[0], grid[-1], n_sectors + 1)

    sector_times = {c: np.diff(time_at(l, bounds)) for c, l in cleaned.items()}
    ref = cleaned[codes[0]]
    has_xy = {"X", "Y"}.issubset(ref.columns)

    sectors = []
    for i in range(n_sectors):
        times = {c: float(sector_times[c][i]) for c in codes}
        winner = min(times, key=times.get)
        seg = {
            "index": i + 1,
            "start": round(float(bounds[i]), 1),
            "end": round(float(bounds[i + 1]), 1),
            "winner": winner,
            "times": {c: round(t, 3) for c, t in times.items()},
            "margin": round(sorted(times.values())[1] - times[winner], 3) if len(codes) > 1 else 0.0,
        }
        if has_xy:
            # include the boundary points so drawn segments touch each other
            d = np.linspace(bounds[i], bounds[i + 1], 12)
            seg["x"] = np.round(np.interp(d, ref["Distance"], ref["X"]), 1).tolist()
            seg["y"] = np.round(np.interp(d, ref["Distance"], ref["Y"]), 1).tolist()
        sectors.append(seg)

    share = {c: sum(1 for s in sectors if s["winner"] == c) for c in codes}
    return {"sectors": sectors, "share": share}


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #


def rotate(x: np.ndarray, y: np.ndarray, degrees: float) -> tuple[np.ndarray, np.ndarray]:
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return x * c - y * s, x * s + y * c


def lap_summary(lap: pd.DataFrame) -> dict:
    lap = clean_lap(lap)
    out = {
        "top_speed": round(float(lap["Speed"].max()), 1),
        "min_speed": round(float(lap["Speed"].min()), 1),
        "avg_speed": round(float(lap["Speed"].mean()), 1),
    }
    if "Throttle" in lap:
        out["full_throttle_pct"] = round(float((lap["Throttle"] >= 98).mean() * 100), 1)
    if "Brake" in lap:
        # share of lap *time* spent braking
        t = lap["Time"].to_numpy(float)
        dt = np.diff(t, append=t[-1])
        b = lap["Brake"].fillna(False).astype(bool).to_numpy()
        out["braking_pct"] = round(float(dt[b].sum() / max(t[-1] - t[0], 1e-9) * 100), 1)
    return out


def to_series(lap: pd.DataFrame, columns: list[str]) -> dict:
    """DataFrame → JSON-friendly dict of lists (NaN → None)."""
    out = {}
    for col in columns:
        if col not in lap:
            continue
        s = lap[col]
        if s.dtype == bool:
            out[col] = s.astype(int).tolist()
        else:
            arr = pd.to_numeric(s, errors="coerce").round(2)
            out[col] = [None if pd.isna(v) else float(v) for v in arr]
    return out


# --------------------------------------------------------------------------- #
# g-forces
# --------------------------------------------------------------------------- #


def _smooth(a: np.ndarray, w: int) -> np.ndarray:
    if w <= 1 or len(a) < w:
        return a
    pad = w // 2
    ap = np.pad(a, (pad, w - 1 - pad), mode="edge")
    return np.convolve(ap, np.ones(w) / w, mode="valid")


def add_g_forces(lap: pd.DataFrame, step: float = 5.0) -> pd.DataFrame:
    """Add LongG (acceleration/braking) and LatG (cornering, + = left) in g.

    Longitudinal comes from dv/dt; lateral from v² × path curvature. Position
    units are converted to metres by comparing XY path length with Distance,
    so it works for FastF1 (decimetres) and the demo (metres) alike.
    """
    lap = clean_lap(lap).copy()
    d = lap["Distance"].to_numpy(float)
    if len(d) < 12 or d[-1] - d[0] < 10 * step:
        lap["LongG"] = 0.0
        lap["LatG"] = 0.0
        return lap
    grid = np.arange(d[0], d[-1], step)
    t = np.interp(grid, d, lap["Time"].to_numpy(float))
    v = _smooth(np.interp(grid, d, lap["Speed"].to_numpy(float)) / 3.6, 5)
    dt = np.gradient(t)
    dt[dt <= 1e-6] = 1e-6
    long_g = _smooth(np.gradient(v) / dt / 9.81, 5)

    lat_g = np.zeros_like(grid)
    if {"X", "Y"}.issubset(lap.columns):
        x = np.interp(grid, d, lap["X"].to_numpy(float))
        y = np.interp(grid, d, lap["Y"].to_numpy(float))
        arc = float(np.hypot(np.diff(x), np.diff(y)).sum())
        if arc > 0:
            m = (grid[-1] - grid[0]) / arc
            x, y = _smooth(x * m, 11), _smooth(y * m, 11)
            dx, dy = np.gradient(x), np.gradient(y)
            ddx, ddy = np.gradient(dx), np.gradient(dy)
            k = (dx * ddy - dy * ddx) / np.maximum((dx * dx + dy * dy) ** 1.5, 1e-9)
            lat_g = _smooth(v * v * k / 9.81, 7)

    lap["LongG"] = np.clip(np.interp(d, grid, long_g), -6.0, 4.0)
    lap["LatG"] = np.clip(np.interp(d, grid, lat_g), -6.0, 6.0)
    return lap


# --------------------------------------------------------------------------- #
# corner-by-corner
# --------------------------------------------------------------------------- #


def corner_analysis(laps: dict[str, pd.DataFrame], corners: list[dict],
                    before: float = 200.0, after: float = 120.0, n: int = 60) -> list[dict]:
    """Per-corner comparison: entry/min/exit speed, gear, brake point, time.

    The window runs from `before` metres ahead of the apex marker to `after`
    metres past it. Times are relative to the first (reference) driver.
    """
    if not laps or not corners:
        return []
    codes = list(laps)
    cl = {c: clean_lap(l) for c, l in laps.items()}
    grid = common_grid(list(cl.values()), step=1.0)
    lo, hi = float(grid[0]), float(grid[-1])
    ref = cl[codes[0]]
    has_xy = {"X", "Y"}.issubset(ref.columns)
    out = []
    for c in corners:
        a, b = max(c["distance"] - before, lo), min(c["distance"] + after, hi)
        if b - a < 100:
            continue
        xs = np.linspace(a, b, n)
        fine = np.linspace(a, b, 400)
        row = {"label": c["label"], "number": c["number"], "distance": round(c["distance"], 1),
               "start": round(a, 1), "end": round(b, 1), "axis": np.round(xs, 1).tolist(), "drivers": {}}
        for code in codes:
            lap = cl[code]
            dd = lap["Distance"].to_numpy(float)
            sp = np.interp(fine, dd, lap["Speed"].to_numpy(float))
            k = int(np.argmin(sp))
            seg_t = float(np.interp(b, dd, lap["Time"]) - np.interp(a, dd, lap["Time"]))
            brake_at = None
            if "Brake" in lap:
                m = (dd >= a) & (dd <= c["distance"] + 30)
                br = lap["Brake"].to_numpy(bool)[m]
                if br.any():
                    brake_at = round(float(dd[m][int(np.argmax(br))]), 1)
            gear = None
            if "nGear" in lap:
                gi = int(np.argmin(np.abs(dd - fine[k])))
                g = lap["nGear"].iloc[gi]
                gear = None if pd.isna(g) else int(g)
            row["drivers"][code] = {
                "speed": np.round(np.interp(xs, dd, lap["Speed"].to_numpy(float)), 1).tolist(),
                "time": round(seg_t, 3),
                "entry": round(float(sp[0]), 1),
                "min": round(float(sp[k]), 1),
                "min_at": round(float(fine[k]), 1),
                "exit": round(float(sp[-1]), 1),
                "gear": gear,
                "brake_at": brake_at,
            }
        ref_t = row["drivers"][codes[0]]["time"]
        for code in codes:
            row["drivers"][code]["delta"] = round(row["drivers"][code]["time"] - ref_t, 3)
        if has_xy:
            pts = np.linspace(a, b, 40)
            row["x"] = np.round(np.interp(pts, ref["Distance"], ref["X"]), 1).tolist()
            row["y"] = np.round(np.interp(pts, ref["Distance"], ref["Y"]), 1).tolist()
        out.append(row)
    return out
