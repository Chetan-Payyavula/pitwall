"""FastAPI app: JSON API under /api, the dashboard served at /.

Run from the project root:
    uvicorn backend.main:app --reload
Use year=0 anywhere in the API for the built-in demo circuit (works offline).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from . import data, live_demo, live_sources
from .livecore import car_window, snapshot
from .strategy import ers_at, live_strategy
from .analysis import (add_g_forces, braking_zones, clean_lap, corner_analysis, lap_delta,
                       lap_summary, match_braking_zones, rotate, to_series, track_dominance)

app = FastAPI(title="F1 Telemetry", version="1.0")


@app.middleware("http")
async def _no_stale_files(request, call_next):
    """Browsers must re-check the page/scripts each visit, so an update is never mixed with old cached JS."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/") and not request.url.path.startswith("/vendor/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
TRACE_COLS = ["Distance", "Time", "Speed", "Throttle", "Brake", "nGear", "RPM", "DRS", "X", "Y", "Z", "LongG", "LatG"]


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except data.DataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/schedule")
async def get_schedule(year: int = Query(..., ge=2018, le=2100)):
    return await run_in_threadpool(_guard, data.schedule, year)


@app.get("/api/overview")
async def get_overview(year: int, event: str = "demo", session: str = "Q"):
    return await run_in_threadpool(_guard, data.overview, year, event, session)


@app.get("/api/drivers")
async def get_drivers(year: int, event: str = "demo", session: str = "Q"):
    return await run_in_threadpool(_guard, data.drivers, year, event, session)


def _compare(year: int, event: str, session: str, codes: list[str], laps: list[str],
             sectors: int) -> dict:
    if not 1 <= len(codes) <= 4:
        raise data.DataError("Pick between 1 and 4 drivers")
    if len(set(codes)) != len(codes):
        raise data.DataError("Each driver can only be picked once")
    laps = (laps + ["fastest"] * len(codes))[: len(codes)]

    # telemetry for each driver (sessions are cached so this is cheap after the first load)
    with ThreadPoolExecutor(max_workers=len(codes)) as pool:
        results = list(pool.map(lambda a: data.lap_telemetry(year, event, session, *a), zip(codes, laps)))
    tels = {c: add_g_forces(t) for c, (t, _) in zip(codes, results)}
    infos = {c: i for c, (_, i) in zip(codes, results)}
    # the reference (first) driver is always the fastest of the chosen laps
    lap_of = dict(zip(codes, laps))
    # (officially timed laps rank ahead of untimed ones; ties keep the picked order)
    codes = sorted(codes, key=lambda c: (not infos[c].get("timed", True), infos[c]["time"], codes.index(c)))
    laps = [lap_of[c] for c in codes]

    circ = data.circuit(year, event, session)
    rot = circ["rotation"]
    for t in tels.values():
        if {"X", "Y"}.issubset(t.columns):
            t["X"], t["Y"] = rotate(t["X"].to_numpy(float), t["Y"].to_numpy(float), rot)

    ref = codes[0]
    drv_meta = {d["code"]: d for d in data.drivers(year, event, session)["drivers"]}
    zones = {c: braking_zones(t) for c, t in tels.items()}

    drivers_out = []
    for c in codes:
        meta = drv_meta.get(c, {"name": c, "team": "", "color": "#888888"})
        drivers_out.append({
            "code": c,
            "name": meta["name"],
            "team": meta["team"],
            "color": meta["color"],
            "lap": infos[c],
            "gap": None if c == ref else round(infos[c]["time"] - infos[ref]["time"], 3),  # ms resolution
            "summary": lap_summary(tels[c]),
            "trace": to_series(tels[c], TRACE_COLS),
            "braking": [z.as_dict() for z in zones[c]],
        })

    deltas = {c: lap_delta(tels[ref], tels[c]) for c in codes[1:]}
    dominance = track_dominance(tels, n_sectors=sectors) if len(codes) > 1 else None

    return {
        "reference": ref,
        "selection": [{"code": c, "lap": l} for c, l in zip(codes, laps)],
        "drivers": drivers_out,
        "delta": deltas,
        "dominance": dominance,
        "braking": match_braking_zones(zones, circ["corners"]),
        "corner_stats": corner_analysis(tels, circ["corners"]),
        "corners": circ["corners"],
        "lap_length": round(float(min(t["Distance"].max() for t in tels.values())), 1),
    }


@app.get("/api/compare")
async def compare(
    year: int,
    event: str = "demo",
    session: str = "Q",
    drivers: str = Query(..., description="Comma-separated codes, first is the reference, e.g. VER,LEC"),
    laps: str = Query("", description="Comma-separated lap numbers or 'fastest', matching drivers"),
    sectors: int = Query(25, ge=5, le=60),
):
    codes = [c.strip().upper() for c in drivers.split(",") if c.strip()]
    lap_list = [l.strip() or "fastest" for l in laps.split(",")] if laps else []
    for l in lap_list:
        if l != "fastest" and not l.isdigit():
            raise HTTPException(422, f"Invalid lap '{l}'")
    return await run_in_threadpool(_guard, _compare, year, event, session, codes, lap_list, sectors)


# ============================================================== live pit wall
def _race(source: str, year: int, event: str, session: str):
    if source == "demo":
        return live_demo.build(), None
    if source == "replay":
        if year < 2018:
            raise data.DataError("Pick a season from 2018 onwards for a replay.")
        return live_sources.fastf1_race(year, event, session), None
    if source == "openf1":
        feed = live_sources.openf1_feed(event if event not in ("", "demo") else "latest")
        return feed.race(), feed.now_t()
    raise data.DataError(f"Unknown source '{source}'")


def _live_meta(source, year, event, session):
    rd, now = _race(source, year, event, session)
    return {"name": rd.name, "total_laps": rd.total_laps, "start": rd.start, "end": rd.end,
            "race_start": rd.meta.get("race_start", rd.start), "now": now, "simulated": rd.simulated, "live": rd.live,
            "drivers": rd.drivers, "track": rd.track, "typical_lap": round(rd.typical_lap, 3)}


def _live_snapshot(source, year, event, session, t, focus):
    rd, now = _race(source, year, event, session)
    t = now if now is not None else t
    snap = snapshot(rd, t)
    snap["focus"] = {c: {"window": car_window(rd, c, t, 30.0), "ers": ers_at(rd, c, t)} for c in focus if c in rd.by_code}
    return snap


def _live_strategy(source, year, event, session, t, focus):
    rd, now = _race(source, year, event, session)
    return live_strategy(rd, now if now is not None else t, focus)


def _focus(s: str) -> list[str]:
    return [c.strip().upper() for c in s.split(",") if c.strip()][:4]


@app.get("/api/live/sources")
def live_sources_info():
    return {"openf1_configured": live_sources.openf1_configured()}


@app.get("/api/live/meta")
async def live_meta(source: str = "demo", year: int = 0, event: str = "demo", session: str = "Race"):
    return await run_in_threadpool(_guard, _live_meta, source, year, event, session)


@app.get("/api/live/snapshot")
async def live_snapshot(source: str = "demo", year: int = 0, event: str = "demo", session: str = "Race",
                        t: float = 0.0, focus: str = ""):
    return await run_in_threadpool(_guard, _live_snapshot, source, year, event, session, t, _focus(focus))


@app.get("/api/live/strategy")
async def live_strategy_ep(source: str = "demo", year: int = 0, event: str = "demo", session: str = "Race",
                           t: float = 0.0, focus: str = ""):
    return await run_in_threadpool(_guard, _live_strategy, source, year, event, session, t, _focus(focus))


# the dashboard (mounted last so /api routes take precedence)
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
