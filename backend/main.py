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

from . import data, live_sources, service
from .service import TRACE_COLS  # noqa: F401  (re-exported for tests)

app = FastAPI(title="F1 Telemetry", version="1.0")


@app.middleware("http")
async def _no_stale_files(request, call_next):
    """Browsers must re-check the page/scripts each visit, so an update is never mixed with old cached JS."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/") and not request.url.path.startswith("/vendor/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


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
    # load the drivers' sessions in parallel on the server
    with ThreadPoolExecutor(max_workers=max(1, len(codes))) as pool:
        return service.compare(year, event, session, codes, laps, sectors, mapper=pool.map)


@app.get("/api/compare")
async def compare(
    year: int,
    event: str = "demo",
    session: str = "Q",
    drivers: str = Query(..., description="Comma-separated codes, first is the reference, e.g. VER,LEC"),
    laps: str = Query("", description="Comma-separated lap numbers or 'fastest', matching drivers"),
    sectors: int = Query(25, ge=5, le=60),
):
    codes = service.parse_codes(drivers)
    lap_list = _guard(service.parse_laps, laps)
    return await run_in_threadpool(_guard, _compare, year, event, session, codes, lap_list, sectors)


# ============================================================== live pit wall
_race = service.race
_live_meta = service.live_meta
_live_snapshot = service.live_snapshot
_live_strategy = service.live_strategy_at
_focus = service.parse_focus


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
