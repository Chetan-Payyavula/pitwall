"""Bake real sessions from the local FastF1 cache into static files for the serverless site.

    F1_CACHE_DIR=cache python -m tools.bake_static OUT_DIR

Only sessions already in the cache are used (FastF1 runs in offline mode).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import fastf1

from backend import data, static_data

# (year, event name, [sessions]) — the cached sessions to publish
SESSIONS = [
    (2024, "Italian Grand Prix", ["Qualifying", "Race"]),
    (2024, "Abu Dhabi Grand Prix", ["Qualifying"]),
    (2026, "Bahrain Grand Prix", ["Qualifying", "Race"]),
    (2026, "Singapore Grand Prix", ["Sprint Qualifying", "Qualifying", "Race"]),
]


def main(out: Path) -> None:
    logging.disable(logging.WARNING)
    fastf1.Cache.offline_mode(True)
    manifest = {"years": {}}
    for year in sorted({y for y, *_ in SESSIONS}):
        sched = data.schedule(year)
        keep = []
        for ev in sched:
            wanted = next((s for y, name, s in SESSIONS if y == year and name == ev["name"]), None)
            if not wanted:
                continue
            done = []
            for sess in wanted:
                info = static_data.bake_session(out, year, ev["round"], sess)
                print(f"{year} R{ev['round']} {ev['name']} {sess}: {len(info['laps'])} laps, race={info['race']}", flush=True)
                done.append(sess)
            keep.append({**ev, "sessions": [s for s in ev["sessions"] if s in done]})
        (out / str(year)).mkdir(parents=True, exist_ok=True)
        (out / str(year) / "schedule.json").write_text(json.dumps(keep, separators=(",", ":")))
        manifest["years"][str(year)] = [{"round": e["round"], "name": e["name"], "sessions": e["sessions"]} for e in keep]
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
