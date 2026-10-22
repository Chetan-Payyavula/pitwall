"""Bake a season's sessions from the OpenF1 cache (tools.openf1_fetch) into the static site's data folder.

    python -m tools.bake_openf1 OF1_CACHE OUT_DIR YEAR [OFFICIAL_DIR]

OFFICIAL_DIR: an earlier FastF1 bake; where it has the same session, its official tyre and position
data replaces OpenF1's (OpenF1's stint list is less reliable). Sessions whose telemetry isn't fully
downloaded yet are skipped. The year's schedule.json and the manifest are (re)written.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from backend import openf1_hist, static_data
from backend.openf1_hist import _cached_json
from tools.openf1_fetch import PER_DRIVER, WANT, Client


def downloaded(c: Client, s: dict) -> bool:
    drivers = c.get("drivers", session_key=s["session_key"]) if c.path("drivers", {"session_key": s["session_key"]}).exists() else []
    return bool(drivers) and all(c.path(ep, {"session_key": s["session_key"], "driver_number": d["driver_number"]}).exists()
                                 for d in drivers for ep in PER_DRIVER)


def main(cache: str, out: str, year: int, official: str | None = None):
    c, out = Client(Path(cache)), Path(out)
    races = _cached_json(c.cache, f"jolpica_{year}_schedule", f"https://api.jolpi.ca/ergast/f1/{year}/races/")["MRData"]["RaceTable"]["Races"]
    round_by_date = {r["date"]: (int(r["round"]), r["date"]) for r in races}
    sessions = [s for s in c.get("sessions", year=year) if s["session_name"] in WANT and not s.get("is_cancelled")]
    by_meeting = defaultdict(list)
    for s in sessions:
        by_meeting[s["meeting_key"]].append(s)
    events = []
    for mk, ss in sorted(by_meeting.items(), key=lambda kv: min(s["date_start"] for s in kv[1])):
        race = next((s for s in ss if s["session_name"] == "Race"), None)
        if not race or race["date_start"][:10] not in round_by_date:
            continue      # cancelled rounds (no race on the official calendar)
        rnd, date = round_by_date[race["date_start"][:10]]
        done, meeting = [], {}
        q = next((s for s in ss if s["session_name"] == "Qualifying" and downloaded(c, s)), None)
        hint = openf1_hist.OF1Session(c, q, year, rnd).finish_line if q else None
        for s in sorted(ss, key=lambda s: s["date_start"]):
            if not c.path("laps", {"session_key": s["session_key"]}).exists() or not c.get("laps", session_key=s["session_key"]):
                continue
            if not downloaded(c, s):
                print(f"skip R{rnd} {s['session_name']}: telemetry not downloaded yet", flush=True)
                continue
            t0 = time.time()
            S = openf1_hist.OF1Session(c, s, year, rnd, line_hint=hint)
            meeting = S.meeting
            off = Path(official) / str(year) / str(rnd) / static_data.slug(s["session_name"]) / "overview.json" if official else None
            if off and off.exists():
                S.use_official_laps(json.loads(off.read_text()))
            info = static_data.bake_session(out, year, rnd, s["session_name"], src=S, race_date=date)
            done.append(s["session_name"])
            print(f"R{rnd} {info['event']} {s['session_name']}: {len(info['laps'])} laps, replay={info['race']}"
                  f"{' (official tyres)' if off and off.exists() else ''} · {time.time()-t0:.0f}s", flush=True)
        if done:
            name = json.loads((out / str(year) / str(rnd) / static_data.slug(done[0]) / "drivers.json").read_text())["event"]
            events.append({"round": rnd, "name": name, "country": meeting.get("country_name", ""),
                           "location": meeting.get("location", ""), "date": date, "sessions": done})
    (out / str(year)).mkdir(parents=True, exist_ok=True)
    (out / str(year) / "schedule.json").write_text(json.dumps(
        events, separators=(",", ":")))
    mf = out / "manifest.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else {"years": {}}
    manifest["years"][str(year)] = [{"round": e["round"], "name": e["name"], "sessions": e["sessions"]} for e in events]
    manifest["years"] = dict(sorted(manifest["years"].items()))
    mf.write_text(json.dumps(manifest, indent=1))
    print("events", len(events), "sessions", sum(len(e["sessions"]) for e in events))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else None)
