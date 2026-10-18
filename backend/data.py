"""Data access: real sessions through FastF1, or the synthetic demo circuit.

Both sources return the same shapes so the API layer does not care which is used.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from . import demo
from .analysis import rotate

CACHE_DIR = Path(os.environ.get("F1_CACHE_DIR", Path(__file__).resolve().parent.parent / "cache"))
SESSION_NAMES = {"FP1": "Practice 1", "FP2": "Practice 2", "FP3": "Practice 3",
                 "SQ": "Sprint Qualifying", "S": "Sprint", "Q": "Qualifying", "R": "Race"}

_fastf1 = None
_load_lock = threading.Lock()


class DataError(Exception):
    """Raised for anything the user should see as a friendly message."""


def ff1():
    """Import FastF1 lazily and enable its on-disk cache once."""
    global _fastf1
    if _fastf1 is None:
        import fastf1
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fastf1.Cache.enable_cache(str(CACHE_DIR))
        _fastf1 = fastf1
    return _fastf1


# --------------------------------------------------------------------------- #
# schedule
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=16)
def schedule(year: int) -> list[dict]:
    try:
        sched = ff1().get_event_schedule(year, include_testing=False)
    except Exception as exc:  # network, unknown year …
        raise DataError(f"Could not load the {year} schedule: {exc}") from exc
    now = datetime.now(timezone.utc)
    events = []
    for _, ev in sched.iterrows():
        sessions = []
        for i in range(1, 6):
            name = ev.get(f"Session{i}")
            date = ev.get(f"Session{i}DateUtc")
            if not isinstance(name, str) or not name or name == "None":
                continue
            if pd.notna(date):
                ts = pd.Timestamp(date)
                ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
                if ts > now:
                    continue  # not run yet
            sessions.append(name)
        events.append({
            "round": int(ev["RoundNumber"]),
            "name": str(ev["EventName"]),
            "country": str(ev["Country"]),
            "location": str(ev["Location"]),
            "date": pd.Timestamp(ev["EventDate"]).strftime("%Y-%m-%d"),
            "sessions": sessions,
        })
    return events


# --------------------------------------------------------------------------- #
# session loading
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=6)
def load_session(year: int, event: str, session: str):
    """Load and keep the most recent sessions in memory (FastF1 also caches on disk)."""
    f = ff1()
    with _load_lock:
        try:
            ev = int(event) if str(event).isdigit() else event
            s = f.get_session(year, ev, session)
            s.load(laps=True, telemetry=True, weather=True, messages=False)
            _ = s.laps  # raises if laps did not load
        except Exception as exc:
            raise DataError(f"Could not load {year} {event} {session}: {exc}") from exc
    return s


def _fmt_laptime(seconds: float | None) -> str | None:
    if seconds is None or not np.isfinite(seconds):
        return None
    m, s = divmod(round(float(seconds), 3), 60)
    return f"{int(m)}:{s:06.3f}"


def _sec(v) -> float | None:
    """Timedelta / number → seconds (rounded), NaN/NaT → None."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    if hasattr(v, "total_seconds"):
        v = v.total_seconds()
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    try:
        return None if v is None or pd.isna(v) else int(v)
    except (TypeError, ValueError):
        return None


def _colour(value) -> str:
    v = str(value or "").strip().lstrip("#")
    return f"#{v}" if len(v) == 6 else "#888888"


RACE_SESSIONS = ("Race", "Sprint")
QUALI_SESSIONS = ("Qualifying", "Sprint Qualifying", "Sprint Shootout")
CLASSIFIED_TEXT = {"R": "DNF", "D": "DSQ", "E": "EXCL", "W": "DNS", "F": "DNQ", "N": "NC"}


def _ms(v) -> float | None:
    """Seconds rounded to the millisecond (timing resolution) or None."""
    x = _sec(v)
    return None if x is None else round(x, 3)


def drivers(year: int, event: str, session: str) -> dict:
    """Drivers in official order: classification for races/qualifying, best lap otherwise.

    Best lap = FastF1 `pick_fastest()`, i.e. the official personal-best lap
    (deleted laps are never counted).
    """
    if year == 0:
        return _demo_drivers()
    s = load_session(year, event, session)
    is_race, is_quali = s.name in RACE_SESSIONS, s.name in QUALI_SESSIONS
    out = []
    for _, r in s.results.iterrows():
        code = r.get("Abbreviation")
        if not isinstance(code, str) or not code:
            continue
        laps = s.laps.pick_drivers(code)
        fastest = laps.pick_fastest() if len(laps) else None
        lt = _ms(fastest["LapTime"]) if fastest is not None and not getattr(fastest, "empty", False) else None
        cls = str(r.get("ClassifiedPosition") or "").strip()
        pts = r.get("Points")
        out.append({
            "code": code,
            "name": str(r.get("FullName") or code),
            "team": str(r.get("TeamName") or ""),
            "color": _colour(r.get("TeamColor")),
            "position": _int(r.get("Position")),
            "classified": CLASSIFIED_TEXT.get(cls, cls),
            "grid": _int(r.get("GridPosition")),
            "status": "" if pd.isna(r.get("Status")) else str(r.get("Status")),
            "points": None if pts is None or pd.isna(pts) else float(pts),
            "race_time": _ms(r.get("Time")) if is_race else None,
            "q": [_ms(r.get(k)) for k in ("Q1", "Q2", "Q3")] if is_quali else None,
            "number": _int(r.get("DriverNumber")),
            "best": lt,
            "best_str": _fmt_laptime(lt),
            "best_lap": _int(fastest["LapNumber"]) if lt is not None else None,
            "laps": [int(n) for n in laps["LapNumber"].dropna().tolist()],
        })
    if is_race:
        # lapped finishers: the results "Time" column is not a gap to the winner for them,
        # so show "+N Lap(s)" like the official classification instead
        done = {d["code"]: (max(d["laps"]) if d["laps"] else 0) for d in out}
        lead = max(done.values(), default=0)
        for d in out:
            down = lead - done[d["code"]]
            if down > 0 and d["classified"].isdigit():
                d["race_time"] = None
                d["status"] = f"+{down} Lap{'s' if down > 1 else ''}"
    if (is_race or is_quali) and any(d["position"] is not None for d in out):
        out.sort(key=lambda d: (d["position"] is None, d["position"] or 0, d["best"] is None, d["best"] or 0))
        order = "classification"
    else:
        out.sort(key=lambda d: (d["best"] is None, d["best"] or 0))
        order = "best lap"
    return {"event": s.event["EventName"], "session": s.name, "drivers": out, "order": order}


def _demo_drivers() -> dict:
    out = []
    for code, (name, team, colour, number, *_rest) in demo.DEMO_DRIVERS.items():
        lt = demo.demo_lap_time(code)
        out.append({"code": code, "name": name, "team": team, "color": colour, "position": None,
                    "number": number, "classified": "", "grid": None, "status": "", "points": None,
                    "race_time": None, "q": None, "best_lap": 1,
                    "best": lt, "best_str": _fmt_laptime(lt), "laps": [1]})
    out.sort(key=lambda d: d["best"])
    for i, d in enumerate(out):
        d["position"] = i + 1
    return {"event": "Demo Circuit", "session": "Demo", "drivers": out, "order": "best lap"}


def lap_telemetry(year: int, event: str, session: str, code: str, lap: str = "fastest"):
    """Return (telemetry DataFrame, lap info dict) for one driver's lap."""
    if year == 0:
        if code not in demo.DEMO_DRIVERS:
            raise DataError(f"Unknown demo driver {code}")
        lt = demo.demo_lap_time(code)
        return demo.demo_lap(code), {"lap": 1, "time": lt, "timed": True, "deleted": False,
                                     "time_str": _fmt_laptime(lt),
                                     "compound": "SOFT", "tyre_life": 2,
                                     "sectors": [round(x, 3) for x in demo.demo_sectors(code)]}

    s = load_session(year, event, session)
    laps = s.laps.pick_drivers(code)
    if laps.empty:
        raise DataError(f"No laps for {code} in this session")
    if lap == "fastest":
        row = laps.pick_fastest()
        if row is None or (hasattr(row, "empty") and row.empty):
            raise DataError(f"{code} has no timed lap")
    else:
        sel = laps[laps["LapNumber"] == int(lap)]
        if sel.empty:
            raise DataError(f"{code} has no lap {lap}")
        row = sel.iloc[0]

    tel = row.get_telemetry().copy()
    tel["Time"] = tel["Time"].dt.total_seconds()
    tel = tel.dropna(subset=["Distance", "Speed"])
    tel["Brake"] = tel["Brake"].fillna(False).astype(bool)

    timed = bool(pd.notna(row["LapTime"]))
    # official timing-screen lap time; only an untimed lap (e.g. an in/out lap) falls
    # back to the telemetry duration, and is flagged so it is never ranked as official
    lt = round(row["LapTime"].total_seconds() if timed else float(tel["Time"].iloc[-1]), 3)
    info = {
        "lap": int(row["LapNumber"]),
        "time": lt,
        "timed": timed,
        "deleted": bool(row.get("Deleted")) if pd.notna(row.get("Deleted")) else False,
        "time_str": _fmt_laptime(lt) if timed else f"~{_fmt_laptime(lt)}",
        "compound": None if pd.isna(row.get("Compound")) else str(row["Compound"]),
        "tyre_life": None if pd.isna(row.get("TyreLife")) else int(row["TyreLife"]),
        "sectors": [_sec(row.get(f"Sector{i}Time")) for i in (1, 2, 3)],
    }
    return tel, info


def circuit(year: int, event: str, session: str) -> dict:
    """Rotation angle and corner markers for the track map."""
    if year == 0:
        *_, corners = demo.demo_circuit()
        return {"rotation": 0.0, "corners": corners}
    s = load_session(year, event, session)
    try:
        ci = s.get_circuit_info()
    except Exception:
        return {"rotation": 0.0, "corners": []}
    rot = float(ci.rotation or 0.0)
    corners = []
    for _, c in ci.corners.iterrows():
        x, y = rotate(np.array([c["X"]]), np.array([c["Y"]]), rot)
        letter = c.get("Letter") or ""
        corners.append({"number": int(c["Number"]), "label": f"T{int(c['Number'])}{letter}",
                        "distance": float(c["Distance"]), "x": float(x[0]), "y": float(y[0])})
    return {"rotation": rot, "corners": corners}


# --------------------------------------------------------------------------- #
# whole-session overview: leaderboard, sectors, speed traps, laps, weather
# --------------------------------------------------------------------------- #


def overview(year: int, event: str, session: str) -> dict:
    if year == 0:
        return _demo_overview()
    s = load_session(year, event, session)
    drv = drivers(year, event, session)
    laps = s.laps
    cols = set(laps.columns)
    rows = []
    for d in drv["drivers"]:
        dl = laps[laps["Driver"] == d["code"]] if "Driver" in cols else laps.iloc[0:0]
        valid = dl[~dl["Deleted"].fillna(False).astype(bool)] if "Deleted" in cols else dl
        sectors = []
        for i in (1, 2, 3):
            c = f"Sector{i}Time"
            sectors.append(_ms(valid[c].min()) if c in cols and len(valid) else None)
        traps = {}
        for key, c in (("I1", "SpeedI1"), ("I2", "SpeedI2"), ("FL", "SpeedFL"), ("ST", "SpeedST")):
            traps[key] = _sec(dl[c].max()) if c in cols and len(dl) else None
        ideal = round(sum(sectors), 3) if all(x is not None for x in sectors) else None
        lap_rows = []
        for _, r in dl.iterrows():
            lap_rows.append({
                "lap": _int(r.get("LapNumber")),
                "time": _ms(r.get("LapTime")),
                "deleted": bool(r.get("Deleted")) if "Deleted" in cols and not pd.isna(r.get("Deleted")) else False,
                "compound": None if pd.isna(r.get("Compound")) else str(r.get("Compound")),
                "stint": _int(r.get("Stint")),
                "tyre_life": _int(r.get("TyreLife")),
                "position": _int(r.get("Position")),
                "pit": bool(pd.notna(r.get("PitInTime"))) if "PitInTime" in cols else False,
            })
        rows.append({**{k: d.get(k) for k in DRIVER_KEYS},
                     "sectors": sectors, "ideal": ideal, "traps": traps, "laps": lap_rows})

    weather = None
    try:
        w = s.weather_data
        if w is not None and len(w):
            weather = {
                "air": _sec(w["AirTemp"].mean()), "track": _sec(w["TrackTemp"].mean()),
                "track_max": _sec(w["TrackTemp"].max()), "humidity": _sec(w["Humidity"].mean()),
                "wind": _sec(w["WindSpeed"].mean()), "rain": bool(w["Rainfall"].any()),
            }
    except Exception:
        weather = None

    ev = s.event
    return {
        "event": str(ev["EventName"]),
        "country": str(ev.get("Country", "")),
        "location": str(ev.get("Location", "")),
        "date": pd.Timestamp(ev["EventDate"]).strftime("%Y-%m-%d") if pd.notna(ev.get("EventDate")) else None,
        "session": s.name,
        "is_race": s.name in ("Race", "Sprint"),
        "simulated": False,
        "weather": weather,
        "order": drv.get("order", "best lap"),
        "drivers": rows,
    }


DRIVER_KEYS = ("code", "name", "team", "color", "number", "position", "classified", "grid", "status", "points",
               "race_time", "q", "best", "best_str", "best_lap")
F1_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]


def _demo_overview() -> dict:
    drv = _demo_drivers()
    race = demo.demo_race()
    total = {c: sum(r["time"] for r in rows) for c, rows in race.items()}
    finish = sorted(total, key=total.get)
    win = total[finish[0]]
    grid = {d["code"]: i + 1 for i, d in enumerate(drv["drivers"])}
    rows = []
    for pos, code in enumerate(finish, 1):
        d = next(x for x in drv["drivers"] if x["code"] == code)
        sectors = [round(x, 3) for x in demo.demo_sectors(code)]
        traps = {k: round(v, 1) for k, v in demo.demo_speed_traps(code).items()}
        rows.append({**{k: d.get(k) for k in DRIVER_KEYS}, "position": pos, "classified": str(pos), "grid": grid[code],
                     "status": "Finished", "points": float(F1_POINTS[pos - 1]) if pos <= 10 else 0.0,
                     "race_time": round(win if pos == 1 else total[code] - win, 3),
                     "sectors": sectors, "ideal": round(sum(sectors), 3), "traps": traps,
                     "laps": [{**l, "deleted": False} for l in race[code]]})
    return {"event": "Demo Circuit", "country": "Nowhere", "location": "Demo Valley", "date": None,
            "session": "Demo", "is_race": True, "simulated": True, "order": "classification",
            "weather": {"air": 24.1, "track": 38.6, "track_max": 41.2, "humidity": 41.0, "wind": 2.3, "rain": False},
            "drivers": rows}
