"""Static (serverless) build: bake real sessions to files, and read them back without FastF1.

`bake_session(...)` runs with FastF1 and writes everything the API needs for one session:
    overview.json, drivers.json, circuit.json, laps/<CODE>.json  (each driver's fastest lap)
    race.json + race.npz                                           (races only: the pit-wall replay)
`install(root)` then points backend.data / backend.live_sources at those files, so
backend.service gives the same answers as the server, e.g. inside a browser via Pyodide.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from . import data, live_sources
from .livecore import RaceData

RACE_NAMES = ("Race", "Sprint")
CAR_KEYS = ("t", "speed", "throttle", "brake", "gear", "rpm", "drs")
POS_KEYS = ("t", "x", "y")
DROP_COLS = {"Date", "SessionTime", "DriverAhead", "DistanceToDriverAhead", "Source", "Status"}


def slug(session: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", session.lower()).strip("-")


def session_dir(root, year, event, session) -> Path:
    return Path(root) / str(year) / str(event) / slug(session)


def _clean(o):
    """JSON-safe copy: numpy scalars → Python, NaN/inf → None."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return [_clean(v) for v in o.tolist()]
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return f if math.isfinite(f) else None
    return o


def _dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(obj), separators=(",", ":"), allow_nan=False))


def _pack(kind: str, key: str, v: np.ndarray) -> np.ndarray:
    """Compact, lossless where it matters: session times are whole milliseconds (stored as int32 deltas),
    car channels are integers, map positions are float32 (< 0.1 mm error)."""
    if key == "t":
        ms = np.round(v * 1000).astype(np.int64)
        if not np.array_equal(ms / 1000, v):
            raise ValueError("session times are not whole milliseconds")
        return np.diff(ms, prepend=0).astype(np.int32)
    if kind == "car":
        if not np.array_equal(v, np.round(v)):
            raise ValueError(f"car channel {key} is not integral")
        return v.astype(np.int16 if np.abs(v).max(initial=0) < 32000 else np.int32)
    return v.astype(np.float32)


def _unpack(key: str, v: np.ndarray) -> np.ndarray:
    if key == "t":
        return np.cumsum(v.astype(np.int64)) / 1000
    return v.astype(np.float64)


# =================================================================== bake (FastF1)
def bake_session(root, year: int, event: int, session: str) -> dict:
    """Write one session's files. Returns a summary for the manifest."""
    out = session_dir(root, year, event, session)
    drv = data.drivers(year, str(event), session)
    _dump(out / "drivers.json", drv)
    _dump(out / "overview.json", data.overview(year, str(event), session))
    _dump(out / "circuit.json", data.circuit(year, str(event), session))
    baked = []
    for d in drv["drivers"]:
        try:
            tel, info = data.lap_telemetry(year, str(event), session, d["code"], "fastest")
        except data.DataError:
            continue
        cols = [c for c in tel.columns if c not in DROP_COLS]
        payload = {"info": info, "columns": cols, "dtypes": {c: str(tel[c].dtype) for c in cols},
                   "data": {c: tel[c].tolist() for c in cols}}
        _dump(out / "laps" / f"{d['code']}.json", payload)
        baked.append(d["code"])
    has_race = False
    if drv["session"] in RACE_NAMES:
        rd = live_sources.fastf1_race(year, str(event), session)
        _dump(out / "race.json", {"name": rd.name, "total_laps": rd.total_laps, "start": rd.start, "end": rd.end,
                                  "drivers": rd.drivers, "laps": rd.laps, "messages": rd.messages, "status": rd.status,
                                  "track": rd.track, "meta": rd.meta})
        arrays = {}
        for kind, src, keys in (("car", rd.car, CAR_KEYS), ("pos", rd.pos, POS_KEYS)):
            for code, c in src.items():
                for k in keys:
                    arrays[f"{kind}|{code}|{k}"] = _pack(kind, k, np.asarray(c[k], dtype=np.float64))
        np.savez_compressed(out / "race.npz", **arrays)
        has_race = True
    return {"session": drv["session"], "event": drv["event"], "laps": baked, "race": has_race}


# =================================================================== read back (no FastF1)
_ROOT: Path | None = None


def _dir(year, event, session) -> Path:
    d = session_dir(_ROOT, year, event, session)
    if not (d / "drivers.json").exists():
        raise data.DataError(f"{session} for this event isn't included in the online version.")
    return d


@lru_cache(maxsize=64)
def _json(path: str):
    return json.loads(Path(path).read_text())


def _drivers(year, event, session):
    if int(year) == 0:
        return data._demo_drivers()
    return _json(str(_dir(year, event, session) / "drivers.json"))


def _overview(year, event, session):
    if int(year) == 0:
        return data._demo_overview()
    return _json(str(_dir(year, event, session) / "overview.json"))


def _circuit(year, event, session):
    if int(year) == 0:
        return _orig["circuit"](0, event, session)
    return _json(str(_dir(year, event, session) / "circuit.json"))


def _lap_telemetry(year, event, session, code, lap="fastest"):
    if int(year) == 0:
        return _orig["lap_telemetry"](0, event, session, code, lap)
    if lap != "fastest":
        raise data.DataError("The online version includes each driver's fastest lap only.")
    f = _dir(year, event, session) / "laps" / f"{code}.json"
    if not f.exists():
        raise data.DataError(f"No timed lap for {code} in this session")
    p = _json(str(f))
    df = pd.DataFrame({c: p["data"][c] for c in p["columns"]})
    for c, dt in p["dtypes"].items():
        if dt == "bool":
            df[c] = df[c].astype(bool)
        elif dt.startswith(("int", "float")):
            df[c] = df[c].astype(dt)
    return df, dict(p["info"])


@lru_cache(maxsize=2)
def _fastf1_race(year, event, session):
    d = _dir(year, event, session)
    if not (d / "race.json").exists():
        raise data.DataError("The replay isn't included for this session in the online version.")
    j = json.loads((d / "race.json").read_text())
    car, pos = {}, {}
    with np.load(d / "race.npz") as z:
        for key in z.files:
            kind, code, k = key.split("|")
            (car if kind == "car" else pos).setdefault(code, {})[k] = _unpack(k, z[key])
    return RaceData(name=j["name"], total_laps=j["total_laps"], start=j["start"], end=j["end"], drivers=j["drivers"],
                    laps=j["laps"], car=car, pos=pos, messages=j["messages"], status=j["status"], track=j["track"],
                    meta=j["meta"])


_orig: dict = {}


def install(root) -> None:
    """Serve backend.data / live_sources from baked files under `root`."""
    global _ROOT
    _ROOT = Path(root)
    if not _orig:
        _orig.update(lap_telemetry=data.lap_telemetry, circuit=data.circuit)
    data.drivers = _drivers
    data.overview = _overview
    data.circuit = _circuit
    data.lap_telemetry = _lap_telemetry
    live_sources.fastf1_race = _fastf1_race
    live_sources.openf1_configured = lambda: False
    _json.cache_clear()
    _fastf1_race.cache_clear()
