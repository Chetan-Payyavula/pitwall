"""The API's pure logic, shared by the FastAPI server and the static (in-browser, Pyodide) build.

Nothing here imports FastAPI or starts threads, so it runs unchanged inside a browser.
"""
from __future__ import annotations

from . import data, live_demo, live_sources
from .livecore import car_window, snapshot
from .strategy import ers_at, live_strategy
from .analysis import (add_g_forces, align_distance, braking_zones, corner_analysis, lap_delta,
                       lap_summary, match_braking_zones, rotate, to_series, track_dominance)

TRACE_COLS = ["Distance", "Time", "Speed", "Throttle", "Brake", "nGear", "RPM", "DRS", "X", "Y", "Z", "LongG", "LatG"]


def parse_codes(drivers: str) -> list[str]:
    return [c.strip().upper() for c in drivers.split(",") if c.strip()]


def parse_laps(laps: str) -> list[str]:
    lap_list = [l.strip() or "fastest" for l in laps.split(",")] if laps else []
    for l in lap_list:
        if l != "fastest" and not l.isdigit():
            raise data.DataError(f"Invalid lap '{l}'")
    return lap_list


def compare(year: int, event: str, session: str, codes: list[str], laps: list[str],
            sectors: int = 25, mapper=map) -> dict:
    """Compare up to four laps. `mapper` lets the server load drivers in parallel (a thread pool's map)."""
    if not 1 <= len(codes) <= 4:
        raise data.DataError("Pick between 1 and 4 drivers")
    if len(set(codes)) != len(codes):
        raise data.DataError("Each driver can only be picked once")
    if not 5 <= int(sectors) <= 60:
        raise data.DataError("sectors must be between 5 and 60")
    laps = (laps + ["fastest"] * len(codes))[: len(codes)]

    # telemetry for each driver (sessions are cached so this is cheap after the first load)
    results = list(mapper(lambda a: data.lap_telemetry(year, event, session, *a), zip(codes, laps)))
    raw = {c: t for c, (t, _) in zip(codes, results)}
    infos = {c: i for c, (_, i) in zip(codes, results)}
    # the reference (first) driver is always the fastest of the chosen laps
    lap_of = dict(zip(codes, laps))
    # (officially timed laps rank ahead of untimed ones; ties keep the picked order)
    codes = sorted(codes, key=lambda c: (not infos[c].get("timed", True), infos[c]["time"], codes.index(c)))
    laps = [lap_of[c] for c in codes]
    # every lap on the reference lap's distance scale, so traces, corners and the delta line up
    tels = {c: add_g_forces(raw[c] if c == codes[0] else align_distance(raw[c], raw[codes[0]])) for c in codes}

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


# ============================================================== live pit wall
def race(source: str, year: int, event: str, session: str):
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


def live_meta(source, year, event, session):
    rd, now = race(source, year, event, session)
    return {"name": rd.name, "total_laps": rd.total_laps, "start": rd.start, "end": rd.end,
            "race_start": rd.meta.get("race_start", rd.start), "now": now, "simulated": rd.simulated, "live": rd.live,
            "drivers": rd.drivers, "track": rd.track, "typical_lap": round(rd.typical_lap, 3)}


def live_snapshot(source, year, event, session, t, focus):
    rd, now = race(source, year, event, session)
    t = now if now is not None else t
    snap = snapshot(rd, t)
    snap["focus"] = {c: {"window": car_window(rd, c, t, 30.0), "ers": ers_at(rd, c, t)} for c in focus if c in rd.by_code}
    return snap


def live_strategy_at(source, year, event, session, t, focus):
    rd, now = race(source, year, event, session)
    return live_strategy(rd, now if now is not None else t, focus)


def parse_focus(s: str) -> list[str]:
    return [c.strip().upper() for c in s.split(",") if c.strip()][:4]
