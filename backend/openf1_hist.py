"""Historical sessions from OpenF1 (+ Jolpica for the official classification, MultiViewer for corners).

Since mid-October 2026 the F1 live-timing archive that FastF1 reads needs an F1 TV login. OpenF1
serves the same timing and car data for free, so sessions that are not in the FastF1 cache are
rebuilt here in exactly the shapes backend.data and backend.live_sources produce:

    S = OF1Session(client, session_record, year, round)
    S.drivers(), S.overview(), S.circuit(), S.lap_telemetry(code), S.race_data()

`client` is tools.openf1_fetch.Client (cached, rate-limited); every response is read from its cache.
"""
from __future__ import annotations

import gzip
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis import rotate
from .data import CLASSIFIED_TEXT, DRIVER_KEYS, DataError, _colour, _fmt_laptime
from .livecore import RaceData

RACE_NAMES = ("Race", "Sprint")
QUALI_NAMES = ("Qualifying", "Sprint Qualifying", "Sprint Shootout")
DELETED_RE = re.compile(r"CAR (\d+)\b.*?\bTIME (\d+:\d{2}\.\d{3})\b.*?\bDELETED\b(?:.*?\bLAP (\d+)\b)?", re.I)
REINSTATED_RE = re.compile(r"CAR (\d+)\b.*?\bTIME (\d+:\d{2}\.\d{3})\b.*?\bREINSTATED\b", re.I)


def _iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _cached_json(cache: Path, name: str, url: str):
    f = cache / "extra" / f"{name}.json.gz"
    if f.exists():
        return json.loads(gzip.decompress(f.read_bytes()))
    req = urllib.request.Request(url, headers={"User-Agent": "pitwall-bake"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(gzip.compress(json.dumps(data).encode()))
    return data


def _lap_str_to_s(s: str) -> float:
    m, rest = s.split(":")
    return round(int(m) * 60 + float(rest), 3)


class OF1Session:
    def __init__(self, client, session: dict, year: int, rnd: int, line_hint=None):
        self.c, self.s, self.year, self.round = client, session, int(year), int(rnd)
        # timing-line (x, y) from another session of the same event (qualifying), used when this
        # session's own estimate is off because its position clock runs off the lap clock
        self.line_hint = None if line_hint is None else np.asarray(line_hint, float)
        self.sk = session["session_key"]
        self.name = session["session_name"]
        self.is_race = self.name in RACE_NAMES
        self.is_quali = self.name in QUALI_NAMES
        # session clock: seconds from one hour before the scheduled start (the data stream starts ~1 h early)
        self.t0 = _iso(session["date_start"]) - timedelta(hours=1)

    # ------------------------------------------------------------------ raw data
    def _get(self, ep, **kw):
        return self.c.get(ep, session_key=self.sk, **kw)

    def sec(self, iso: str | None) -> float | None:
        return None if not iso else (_iso(iso) - self.t0).total_seconds()

    @cached_property
    def meeting(self) -> dict:
        m = self.c.get("meetings", meeting_key=self.s["meeting_key"])
        return m[0] if m else {}

    @cached_property
    def drivers_raw(self) -> dict:
        out = {}
        for d in self._get("drivers"):
            out[str(d["driver_number"])] = d
        return out

    @cached_property
    def jolpica(self) -> dict:
        """Official classification for races and sprints (status, grid, points, positionText)."""
        if not self.is_race:
            return {}
        kind = "results" if self.name == "Race" else "sprint"
        url = f"https://api.jolpi.ca/ergast/f1/{self.year}/{self.round}/{kind}/"
        races = _cached_json(self.c.cache, f"jolpica_{self.year}_{self.round}_{kind}", url)["MRData"]["RaceTable"]["Races"]
        if not races:
            return {}
        rows = races[0].get("Results") or races[0].get("SprintResults") or []
        return {str(r["number"]): r for r in rows}

    @cached_property
    def results(self) -> dict:
        return {str(r["driver_number"]): r for r in self._get("session_result")}

    @cached_property
    def deleted(self) -> set:
        """(driver number, lap number) of laps deleted by race control (track limits etc.)."""
        dels, rein = {}, set()
        for m in sorted(self._get("race_control"), key=lambda m: m.get("date") or ""):
            msg = m.get("message") or ""
            r = REINSTATED_RE.search(msg)
            if r:
                rein.add((r.group(1), _lap_str_to_s(r.group(2))))
                continue
            d = DELETED_RE.search(msg)
            if d:
                dels[(d.group(1), _lap_str_to_s(d.group(2)))] = int(d.group(3)) if d.group(3) else None
        out = set()
        laps_by = {}
        for l in self._get("laps"):
            laps_by.setdefault(str(l["driver_number"]), []).append(l)
        for (num, t), lap in dels.items():
            if (num, t) in rein:
                continue
            if lap is None:   # no lap number in the message: match the lap by its time
                cand = [l for l in laps_by.get(num, []) if l.get("lap_duration") and abs(l["lap_duration"] - t) < 0.0005]
                lap = cand[0]["lap_number"] if cand else None
            if lap is not None:
                out.add((num, int(lap)))
        return out

    @cached_property
    def laps(self) -> dict:
        """number -> list of lap rows (session-clock seconds), sorted by lap number."""
        stints = {}
        for st in self._get("stints"):
            stints.setdefault(str(st["driver_number"]), []).append(st)
        # OpenF1 stamps a stop with its pit-EXIT time and a lane duration (and numbers it by the in-lap in races
        # but by the out-lap in qualifying), so place each stop by its pit-lane ENTRY time instead.
        stops = {}
        for p in self._get("pit"):
            lane = _num(p.get("lane_duration")) or _num(p.get("pit_duration"))
            if p.get("date") and lane:
                out_t = self.sec(p["date"])
                stops.setdefault(str(p["driver_number"]), []).append((out_t - lane, out_t))
        positions = {}
        for p in self._get("position"):
            positions.setdefault(str(p["driver_number"]), []).append((self.sec(p["date"]), p["position"]))
        for v in positions.values():
            v.sort()
        out = {}
        raw = {}
        for l in self._get("laps"):
            raw.setdefault(str(l["driver_number"]), []).append(l)
        for num, ls in raw.items():
            if num not in self.drivers_raw:
                continue
            ls.sort(key=lambda r: r["lap_number"])
            rows = []
            for i, l in enumerate(ls):
                n = int(l["lap_number"])
                dur = _num(l.get("lap_duration"))
                st = self.sec(l.get("date_start"))
                nxt = self.sec(ls[i + 1]["date_start"]) if i + 1 < len(ls) and ls[i + 1].get("date_start") else None
                if st is None and dur is not None and nxt is not None:
                    st = nxt - dur
                en = st + dur if st is not None and dur is not None else nxt
                comp = age = stint = stint_rec = None
                for s in stints.get(num, []):
                    hi = s.get("lap_end") or 10_000
                    if (s.get("lap_start") or 1) <= n <= hi:
                        comp = s.get("compound")
                        stint_rec = s
                        break
                d1, d2, d3 = (_num(l.get(f"duration_sector_{k}")) for k in (1, 2, 3))
                pos = None
                if self.is_race and en is not None and positions.get(num):
                    ts = [t for t, _ in positions[num]]
                    k = int(np.searchsorted(ts, en, side="right")) - 1
                    pos = positions[num][k][1] if k >= 0 else None
                if not self.is_race and l.get("is_pit_out_lap"):
                    dur = None   # a garage out-lap has no lap time (FastF1 shows none either)
                    en = nxt if nxt is not None else en
                rows.append({
                    "lap": n, "start": st, "end": en, "time": dur,
                    "s1": st + d1 if st is not None and d1 else None,
                    "s2": st + d1 + d2 if st is not None and d1 and d2 else None,
                    "sectors": [d1, d2, d3],
                    "traps": {"I1": _num(l.get("i1_speed")), "I2": _num(l.get("i2_speed")), "FL": None, "ST": _num(l.get("st_speed"))},
                    "compound": comp, "age": age, "stint": stint, "stint_ofs": stint_rec, "position": pos,
                    "pit_in_t": None, "pit_out_t": None,
                    "pit_out_lap": bool(l.get("is_pit_out_lap")),
                    "deleted": (num, n) in self.deleted,
                })
            by_lap = {r["lap"]: r for r in rows}
            for entry, exit_ in stops.get(num, []):
                inlap = next((r for r in rows if r["start"] is not None and r["start"] <= entry
                              and (r["end"] is None or entry <= r["end"] + 1.0)), None)
                if inlap is not None:
                    inlap["pit_in_t"] = entry
                    out_lap = by_lap.get(inlap["lap"] + 1)
                    if out_lap is not None:
                        out_lap["pit_out_t"] = exit_
            for r in rows:   # the lap before any pit-out lap is an in-lap, recorded stop or not
                prev = by_lap.get(r["lap"] - 1)
                if r["pit_out_lap"] and prev is not None and prev["pit_in_t"] is None:
                    prev["pit_in_t"] = prev["end"] if prev["end"] is not None else prev["start"]
                    if r["pit_out_t"] is None:
                        r["pit_out_t"] = r["start"]
            # Stints: every pit stop starts one; OpenF1's own stint starts count too, except a phantom stint it
            # sometimes opens on lap 2 with no stop (it carries the compound of the stint after the first stop).
            # Each stint takes the compound OpenF1 shows on its first lap.
            stint_no, age0, comp, start_lap = 0, 0, None, None
            for r in rows:
                st0 = r["stint_ofs"]
                of1_start = st0 is not None and st0.get("lap_start") == r["lap"] and r["lap"] > 1
                phantom = of1_start and r["lap"] == 2 and not r["pit_out_lap"]
                new = start_lap is None or (r["pit_out_lap"] and r["lap"] > 1) or (of1_start and not phantom)
                if new:
                    stint_no += 1
                    start_lap = r["lap"]
                    comp = r["compound"] or comp
                    age0 = (st0.get("tyre_age_at_start") or 0) if st0 and st0.get("lap_start") == r["lap"] else 0
                r["stint"] = stint_no
                r["age"] = age0 + (r["lap"] - start_lap) + 1
                r["compound"] = comp
            for r in rows:
                r.pop("stint_ofs", None)
            if not self.is_race and rows:
                # the last lap of a qualifying run ends in the pits (no stop record follows it)
                times = [r["time"] for r in rows if r["time"]]
                last = rows[-1]
                if last["pit_in_t"] is None and times and (last["time"] is None or last["time"] > 1.15 * min(times)):
                    last["pit_in_t"] = last["end"] if last["end"] is not None else last["start"]
            out[num] = rows
        return out

    def use_official_laps(self, overview: dict, fields=("compound", "stint", "tyre_life", "position")) -> None:
        """Take tyre and position fields from an official FastF1 lap table for the same session."""
        key = {"tyre_life": "age"}
        by_code = {d["code"]: {l["lap"]: l for l in d["laps"]} for d in overview["drivers"]}
        for num, rows in self.laps.items():
            src = by_code.get(self.code(num), {})
            for r in rows:
                o = src.get(r["lap"])
                if o:
                    for f in fields:
                        if o.get(f) is not None:
                            r[key.get(f, f)] = o[f]

    def code(self, num: str) -> str:
        return self.drivers_raw[num].get("name_acronym") or num

    def full_name(self, num: str) -> str:
        d = self.drivers_raw[num]
        full, last = d.get("full_name") or "", d.get("last_name") or ""
        if last and full.upper().endswith(last.upper()):
            first = full[: len(full) - len(last)].strip()
            return f"{first} {last}".strip()
        return full.title() or self.code(num)

    def best(self, num: str) -> dict | None:
        """Personal best: fastest timed lap that wasn't deleted. In qualifying it must be the driver's
        official Q1/Q2/Q3 time (a driver with no Q time has no best lap, whatever laps were logged)."""
        valid = [r for r in self.laps.get(num, []) if r["time"] and not r["deleted"]]
        if self.is_quali:
            dur = self.results.get(num, {}).get("duration")
            qs = [_num(v) for v in dur] if isinstance(dur, list) else []
            qs = [v for v in qs if v is not None]
            if not qs:
                return None
            target = min(qs)
            match = [r for r in valid if abs(r["time"] - target) < 0.0015]
            if match:
                return min(match, key=lambda r: r["lap"])
        return min(valid, key=lambda r: (r["time"], r["lap"])) if valid else None

    # ------------------------------------------------------------------ data.drivers
    def drivers(self) -> dict:
        out = []
        for num, d in self.drivers_raw.items():
            res = self.results.get(num, {})
            jr = self.jolpica.get(num, {})
            b = self.best(num)
            lt = b["time"] if b else None
            pos = res.get("position")
            classified, status, grid, points = "", "", None, None
            if self.is_race:
                pt = str(jr.get("positionText") or (pos if pos else ""))
                classified = CLASSIFIED_TEXT.get(pt, pt)
                status = jr.get("status") or ("Finished" if pos else "")
                grid = int(jr["grid"]) if jr.get("grid") not in (None, "") else None
                points = _num(jr.get("points")) if jr else _num(res.get("points"))
                if jr.get("position"):
                    pos = int(jr["position"])
            if pos is not None and not str(pos).isdigit():
                pos = None   # OpenF1 sometimes reports "RT" etc. instead of a classified place
            race_time = None
            if self.is_race:
                gap = res.get("gap_to_leader")
                race_time = _num(res.get("duration")) if gap in (0, 0.0) else _num(gap)
            q = None
            if self.is_quali:
                dur = res.get("duration") if isinstance(res.get("duration"), list) else [None, None, None]
                q = [round(v, 3) if _num(v) is not None else None for v in (dur + [None, None, None])[:3]]
            out.append({
                "code": self.code(num), "name": self.full_name(num), "team": d.get("team_name") or "",
                "color": _colour(d.get("team_colour")), "position": int(pos) if pos else None,
                "classified": classified, "grid": grid, "status": status, "points": points,
                "race_time": race_time, "q": q, "number": int(num), "best": lt, "best_str": _fmt_laptime(lt),
                "best_lap": b["lap"] if b else None,
                "laps": [r["lap"] for r in self.laps.get(num, [])],
            })
        if self.is_race:
            done = {d["code"]: (max(d["laps"]) if d["laps"] else 0) for d in out}
            lead = max(done.values(), default=0)
            for d in out:
                down = lead - done[d["code"]]
                if down > 0 and str(d["classified"]).isdigit():
                    d["race_time"] = None
                    d["status"] = f"+{down} Lap{'s' if down > 1 else ''}"
        if (self.is_race or self.is_quali) and any(d["position"] is not None for d in out):
            out.sort(key=lambda d: (d["position"] is None, d["position"] or 0, d["best"] is None, d["best"] or 0))
            order = "classification"
        else:
            out.sort(key=lambda d: (d["best"] is None, d["best"] or 0))
            order = "best lap"
        return {"event": self.meeting.get("meeting_name") or "", "session": self.name, "drivers": out, "order": order}

    # ------------------------------------------------------------------ data.overview
    def overview(self, race_date: str | None = None) -> dict:
        drv = self.drivers()
        num_of = {self.code(n): n for n in self.drivers_raw}
        rows = []
        for d in drv["drivers"]:
            laps = self.laps.get(num_of[d["code"]], [])
            valid = [r for r in laps if not r["deleted"]]
            sectors = []
            for k in range(3):
                vals = [r["sectors"][k] for r in valid if r["sectors"][k] is not None]
                sectors.append(round(min(vals), 3) if vals else None)
            traps = {}
            for key in ("I1", "I2", "FL", "ST"):
                vals = [r["traps"][key] for r in laps if r["traps"][key] is not None]
                traps[key] = round(max(vals), 3) if vals else None
            ideal = round(sum(sectors), 3) if all(x is not None for x in sectors) else None
            lap_rows = [{"lap": r["lap"], "time": r["time"], "deleted": r["deleted"], "compound": r["compound"],
                         "stint": r["stint"], "tyre_life": r["age"], "position": r["position"],
                         "pit": r["pit_in_t"] is not None} for r in laps]
            rows.append({**{k: d.get(k) for k in DRIVER_KEYS}, "sectors": sectors, "ideal": ideal, "traps": traps, "laps": lap_rows})
        weather = None
        w = self._get("weather")
        if w:
            f = lambda k: [x[k] for x in w if x.get(k) is not None]
            weather = {"air": round(float(np.mean(f("air_temperature"))), 3), "track": round(float(np.mean(f("track_temperature"))), 3),
                       "track_max": round(float(np.max(f("track_temperature"))), 3), "humidity": round(float(np.mean(f("humidity"))), 3),
                       "wind": round(float(np.mean(f("wind_speed"))), 3), "rain": any(x.get("rainfall") for x in w)}
        return {"event": drv["event"], "country": self.meeting.get("country_name", ""), "location": self.meeting.get("location", ""),
                "date": race_date or self.s["date_start"][:10], "session": self.name, "is_race": self.is_race,
                "simulated": False, "weather": weather, "order": drv["order"], "drivers": rows}

    # ------------------------------------------------------------------ telemetry
    def _series(self, ep: str, num: str) -> pd.DataFrame:
        recs = self.c.get(ep, session_key=self.sk, driver_number=int(num))
        if not recs:
            return pd.DataFrame()
        df = pd.DataFrame(recs)
        df["t"] = (pd.to_datetime(df["date"], format="ISO8601", utc=True) - pd.Timestamp(self.t0)).dt.total_seconds()
        df = df.sort_values("t").drop_duplicates("t", keep="last").reset_index(drop=True)
        if ep == "location":   # drop (0, 0, 0) placeholders sent before a car is on track
            df = df[~((df["x"] == 0) & (df["y"] == 0) & (df["z"] == 0))].reset_index(drop=True)
        return df

    @cached_property
    def _car(self) -> dict:
        return {}

    def car(self, num: str) -> pd.DataFrame:
        """(car_data, location) for one driver, with the car data re-synced to the position clock.

        OpenF1's position samples agree with the official lap times, but its car-data timestamps can run
        off by tenths of a second, and in some sessions by ~20 s for part of a race. Each lap's car speed is
        matched against the speed implied by the positions, and the car timestamps are shifted to fit.
        """
        if num not in self._car:
            car, pos = self._series("car_data", num), self.pos(num)
            if not car.empty and len(pos) > 50:
                shift = self._car_clock_shift(num, car, pos)
                if shift is not None:
                    # whole milliseconds, like the source timestamps
                    car = car.assign(t=np.round(car["t"].to_numpy() - np.interp(car["t"].to_numpy(), shift[0], shift[1]), 3))
                    car = car.sort_values("t").drop_duplicates("t", keep="last").reset_index(drop=True)
            self._car[num] = (car, pos)
        return self._car[num]

    def pos_raw(self, num: str) -> pd.DataFrame:
        if num not in self._pos_raw:
            self._pos_raw[num] = self._series("location", num)
        return self._pos_raw[num]

    @cached_property
    def _pos_raw(self) -> dict:
        return {}

    def pos(self, num: str) -> pd.DataFrame:
        """Position samples on the lap clock. In some sessions the position feed runs seconds behind
        the official lap times for part of the race; the lag is measured at each line crossing and removed."""
        if num not in self._pos:
            p = self.pos_raw(num)
            shift = self._pos_clock_shift(num, p)
            if shift is not None:
                t = p["t"].to_numpy(float)
                p = p.assign(t=np.round(t - np.interp(t, shift[0], shift[1]), 3))
                p = p.sort_values("t").drop_duplicates("t", keep="last").reset_index(drop=True)
            self._pos[num] = p
        return self._pos[num]

    def _pos_clock_shift(self, num: str, p: pd.DataFrame):
        P = self.finish_line
        if P is None or len(p) < 50:
            return None
        pt, px, py = p["t"].to_numpy(float), p["x"].to_numpy(float), p["y"].to_numpy(float)
        at, offs = [], []
        for r in self.laps.get(num, []):
            st = r["start"]
            if st is None or r["lap"] < 2 or not (pt[0] + 10 < st < pt[-1] - 10):
                continue
            g = np.arange(st - 8, st + 8, 0.02)
            d = np.hypot(np.interp(g, pt, px) - P[0], np.interp(g, pt, py) - P[1])
            k = int(np.argmin(d))
            gap = np.diff(pt[(pt > g[0]) & (pt < g[-1])])
            if d[k] < 150 and 0 < k < len(g) - 1 and (len(gap) == 0 or gap.max() < 2):
                at.append(g[k])
                offs.append(g[k] - st)
        if len(offs) < 3:
            return None
        offs = pd.Series(offs).rolling(5, center=True, min_periods=1).median().to_numpy()
        offs[np.abs(offs) < 0.5] = 0.0        # sub-half-second differences: leave the feed as it is
        if not np.any(offs):
            return None
        return np.array(at), offs

    @cached_property
    def _pos(self) -> dict:
        return {}

    @cached_property
    def finish_line(self):
        """(x, y) of the timing line: the median car position at the official start of flying laps
        (or the qualifying session's line, when this session's estimate is more than 15 m from it)."""
        own = self._own_line()
        if self.line_hint is not None and (own is None or np.hypot(*(own - self.line_hint)) > 150):
            return self.line_hint
        return own

    def _own_line(self):
        pts = []
        for num, rows in self.laps.items():
            p = self.pos_raw(num)
            if len(p) < 50:
                continue
            t_lo, t_hi = float(p["t"].iloc[0]), float(p["t"].iloc[-1])
            for r in rows:
                if r["lap"] > 1 and r["time"] and r["start"] is not None and not r["pit_out_lap"] and r["pit_in_t"] is None \
                        and t_lo < r["start"] < t_hi:        # outside the position feed np.interp would clamp
                    pts.append((np.interp(r["start"], p["t"], p["x"]), np.interp(r["start"], p["t"], p["y"])))
        return np.median(np.array(pts), axis=0) if pts else None

    def line_time(self, num: str, t: float, window: float = 2.0, wide: float = 0.0) -> float:
        """The moment near `t` when the car passes the timing line (closest approach), or `t` if it isn't near it."""
        P, p = self.finish_line, self.pos(num)
        if P is None or len(p) < 50 or t is None:
            return t
        g = np.arange(t - window, t + window, 0.01)
        d = np.hypot(np.interp(g, p["t"], p["x"]) - P[0], np.interp(g, p["t"], p["y"]) - P[1])
        k = int(np.argmin(d))
        if d[k] < 250 and 0 < k < len(g) - 1:      # within 25 m (positions are in decimetres)
            return float(g[k])
        if wide and wide > window:
            # some sessions' position clock runs tens of seconds off the lap clock: take the line crossing
            # nearest in time to t, within the lap, if the car really passes the line there
            g = np.arange(t - wide, t + wide, 0.05)
            g = g[(g >= p["t"].iloc[0]) & (g <= p["t"].iloc[-1])]
            if len(g) > 3:
                d = np.hypot(np.interp(g, p["t"], p["x"]) - P[0], np.interp(g, p["t"], p["y"]) - P[1])
                mins = [i for i in range(1, len(g) - 1) if d[i] <= d[i - 1] and d[i] <= d[i + 1] and d[i] < 150]
                if mins:
                    i = min(mins, key=lambda i: abs(g[i] - t))
                    gg = np.arange(g[i] - 0.06, g[i] + 0.06, 0.01)
                    dd = np.hypot(np.interp(gg, p["t"], p["x"]) - P[0], np.interp(gg, p["t"], p["y"]) - P[1])
                    return float(gg[int(np.argmin(dd))])
        return t

    def _car_clock_shift(self, num: str, car: pd.DataFrame, pos: pd.DataFrame):
        """Per-lap offset (car clock − true clock). Returns (car-clock times, offsets) or None."""
        pt, px, py = pos["t"].to_numpy(float), pos["x"].to_numpy(float), pos["y"].to_numpy(float)
        dt = np.diff(pt)
        ok = dt > 0.05
        vt = (pt[1:] + pt[:-1])[ok] / 2
        vv = (np.hypot(np.diff(px), np.diff(py))[ok] / 10.0) / dt[ok] * 3.6     # positions are in decimetres
        vv = pd.Series(vv).rolling(5, center=True, min_periods=1).median().to_numpy()
        ct, cs = car["t"].to_numpy(float), car["speed"].astype(float).to_numpy()
        good = np.isfinite(cs)
        ct, cs = ct[good], cs[good]
        mids, offs = [], []
        for r in self.laps.get(num, []):
            st, en = r["start"], r["end"]
            if st is None or en is None or en - st < 40 or en - st > 400:
                continue
            g = np.arange(st + 2, en - 2, 0.25)
            ref = np.interp(g, vt, vv)
            if np.ptp(ref) < 60:           # needs braking zones to lock on (skips stationary / pit-lane laps)
                continue

            def err(d):
                return float(np.mean(np.abs(np.interp(g + d, ct, cs) - ref)))
            coarse = np.arange(-40.0, 40.01, 0.25)
            e = np.array([err(d) for d in coarse])
            d0 = float(coarse[int(np.argmin(e))])
            fine = np.arange(d0 - 0.3, d0 + 0.301, 0.02)
            ef = np.array([err(d) for d in fine])
            best, best_e = float(fine[int(np.argmin(ef))]), float(ef.min())
            if best_e < 0.25 * float(np.mean(ref)):   # a clear match only
                mids.append((st + en) / 2 + best)
                offs.append(best)
        if not offs:
            return None
        mids, offs = np.array(mids), np.array(offs)
        order = np.argsort(mids)
        mids, offs = mids[order], offs[order]
        # ignore a lone lap that disagrees with both neighbours by > 1 s (a bad match, not a clock step)
        keep = np.ones(len(offs), bool)
        for i in range(1, len(offs) - 1):
            if abs(offs[i] - offs[i - 1]) > 1 and abs(offs[i] - offs[i + 1]) > 1 and abs(offs[i - 1] - offs[i + 1]) < 1:
                keep[i] = False
        return mids[keep], offs[keep]

    def lap_frame(self, num: str, row: dict) -> pd.DataFrame:
        """FastF1-style lap telemetry: car + position samples merged, Time from lap start, Distance integrated."""
        car, pos = self.car(num)
        st, en = row["start"], row["end"]
        if st is not None and en is not None:
            # OpenF1 lap starts can be 10-35 m off the line: snap to the actual crossing, keep the official lap time
            st2 = self.line_time(num, st, wide=0.6 * (row["time"] or en - st))
            en = st2 + (row["time"] if row["time"] else en - st)
            st = st2
        if car.empty or st is None or en is None:
            raise DataError(f"No telemetry for {self.code(num)} lap {row['lap']}")
        ct = car["t"].to_numpy(float)
        pt = pos["t"].to_numpy(float) if not pos.empty else np.array([])
        inside = lambda t: t[(t > st) & (t < en)]
        T = np.unique(np.concatenate([[st, en], inside(ct), inside(pt)]))
        out = {"Time": T - st}
        for col, src in (("RPM", "rpm"), ("Speed", "speed"), ("Throttle", "throttle")):
            v = car[src].astype(float).to_numpy()
            ok = np.isfinite(v)
            out[col] = np.interp(T, ct[ok], v[ok])
        idx = np.clip(np.searchsorted(ct, T, side="right") - 1, 0, len(ct) - 1)   # discrete channels: last value
        out["nGear"] = car["n_gear"].fillna(0).astype(int).to_numpy()[idx]
        out["Brake"] = car["brake"].fillna(0).astype(float).to_numpy()[idx] > 0
        out["DRS"] = car["drs"].fillna(0).astype(int).to_numpy()[idx] if "drs" in car else np.zeros(len(T), int)
        speed = out["Speed"]
        dist = np.concatenate([[0.0], np.cumsum(speed[1:] / 3.6 * np.diff(out["Time"]))])
        out["Distance"] = dist
        out["RelativeDistance"] = dist / dist[-1] if dist[-1] > 0 else dist
        for col in ("X", "Y", "Z"):
            if len(pt):
                out[col] = np.interp(T, pt, pos[col.lower()].astype(float).to_numpy())
        df = pd.DataFrame(out)
        cols = ["Time", "RPM", "Speed", "nGear", "Throttle", "Brake", "DRS", "Distance", "RelativeDistance"] + \
               [c for c in ("X", "Y", "Z") if c in df]
        df = df[cols].copy()
        # how intact the position feed itself is on this lap: the longest wait for a new, different position
        if len(pt):
            m = (pt >= st - 1) & (pt <= en + 1)
            tt = pt[m]
            if len(tt) < 2 or tt[0] > st + 1 or tt[-1] < en - 1:
                df.attrs["pos_gap"] = float("inf")
            else:
                xy = pos[["x", "y"]].to_numpy(float)[m]
                moved = np.r_[True, np.any(np.diff(xy, axis=0) != 0, axis=1)]
                df.attrs["pos_gap"] = float(np.diff(tt[moved]).max()) if moved.sum() > 1 else float("inf")
        else:
            df.attrs["pos_gap"] = float("inf")
        return df

    @staticmethod
    def lap_quality(tel: pd.DataFrame) -> tuple[float, float]:
        """(lap distance m, mean |car speed − speed implied by the positions| km/h) for one lap frame."""
        t, x, y = tel["Time"].to_numpy(float), tel["X"].to_numpy(float), tel["Y"].to_numpy(float)
        g = np.arange(1.0, max(t[-1] - 1.0, 1.5), 0.5)
        xi, yi = np.interp(g, t, x), np.interp(g, t, y)
        vp = np.hypot(np.diff(xi), np.diff(yi)) / 10.0 / 0.5 * 3.6           # positions are in decimetres
        vp = pd.Series(vp).rolling(5, center=True, min_periods=1).median().to_numpy()
        vc = np.interp((g[1:] + g[:-1]) / 2, t, tel["Speed"].to_numpy(float))
        return float(tel["Distance"].iloc[-1]), float(np.mean(np.abs(vc - vp)))

    @staticmethod
    def frozen_s(tel: pd.DataFrame) -> float:
        """Longest stretch (s) over which speed and RPM don't change at all. Live car data never holds both
        still for long (≤ 0.5 s in the 2024 FastF1 data); a stalled feed repeats the last sample for seconds."""
        t = tel["Time"].to_numpy(float)
        same = (np.diff(tel["Speed"].to_numpy(float)) == 0) & (np.diff(tel["RPM"].to_numpy(float)) == 0)
        best = cur = 0.0
        for dt, s_ in zip(np.diff(t), same):
            cur = cur + dt if s_ else 0.0
            best = max(best, cur)
        return best

    @classmethod
    def car_ok(cls, tel: pd.DataFrame, limits: dict | None) -> bool:
        """False when the car data on a lap is broken: the distance from integrating the speed is > 1.5% off
        the session's track length, or the feed stalls (speed and RPM frozen) for more than 3 s."""
        return not limits or (abs(float(tel["Distance"].iloc[-1]) / limits["length"] - 1) <= 0.015
                              and cls.frozen_s(tel) <= 3.0)

    @staticmethod
    def pos_intact(tel: pd.DataFrame, limits: dict | None) -> bool:
        """The position feed itself is there on this lap: never more than 2 s without a new position.
        (Positions that are present but disagree with the lap time or the car speed point to a feed whose
        clock is off on that lap, so such a lap is replaced rather than redrawn.)"""
        return not limits or ("X" in tel and tel.attrs.get("pos_gap", 0.0) <= 2.0)

    @classmethod
    def pos_ok(cls, tel: pd.DataFrame, limits: dict | None) -> bool:
        """False when the positions on a lap are broken: missing (X/Y off the timing line at a lap end),
        frozen, or disagreeing with the car speed far more than on the session's typical lap."""
        if not limits:
            return True
        if "X" not in tel:
            return False
        if limits.get("line") is not None:
            P = limits["line"]
            for i in (0, -1):
                if np.hypot(tel["X"].iloc[i] - P[0], tel["Y"].iloc[i] - P[1]) > 300:      # 30 m (decimetres)
                    return False
        return cls.lap_quality(tel)[1] <= limits["mismatch"]

    @classmethod
    def telemetry_ok(cls, tel: pd.DataFrame, limits: dict | None) -> bool:
        return not limits or (cls.car_ok(tel, limits) and cls.pos_ok(tel, limits))

    def quality_limits(self, codes: list[str] | None = None) -> dict | None:
        if codes is None:
            codes = [self.code(n) for n in self.drivers_raw if self.best(n)]
        lengths, mism = [], []
        for code in codes:
            try:
                d, m = self.lap_quality(self.lap_telemetry(code)[0])
            except DataError:
                continue
            lengths.append(d)
            mism.append(m)
        if len(lengths) < 3:
            return None
        # a session whose positions are broken on most laps must not loosen the limit for everyone
        return {"length": float(np.median(lengths)), "mismatch": min(25.0, max(14.0, 1.5 * float(np.median(mism)))),
                "line": self.finish_line}

    @cached_property
    def limits(self) -> dict | None:
        return self.quality_limits()

    @cached_property
    def ref_path(self) -> pd.DataFrame:
        """The session's fastest lap with intact car data and positions: the track map, and the path laps
        with broken positions are drawn on."""
        L = self.limits
        cands = sorted(((r, n) for n, rows in self.laps.items() for r in rows
                        if r["time"] and r["start"] is not None and r["pit_in_t"] is None and not r["pit_out_lap"]),
                       key=lambda x: x[0]["time"])
        for r, n in cands:
            try:
                tel = self.lap_frame(n, r)
            except DataError:
                continue
            if "X" in tel and self.telemetry_ok(tel, L):
                tel.attrs["sectors"] = r["sectors"][:2]
                return tel
        raise DataError("No lap in this session has usable positions")

    @cached_property
    def sector_marks(self):
        """Where the sector lines are, as fractions of the lap distance (from the reference lap's own
        official sector times), or None."""
        try:
            ref = self.ref_path
        except DataError:
            return None
        sec = ref.attrs.get("sectors")
        if not sec or sec[0] is None or sec[1] is None:
            return None
        t, d = ref["Time"].to_numpy(float), ref["Distance"].to_numpy(float)
        return tuple(float(np.interp(x, t, d) / d[-1]) for x in (sec[0], sec[0] + sec[1]))

    def sectors_ok(self, tel: pd.DataFrame, sectors, tol: float = 0.35) -> bool:
        """The lap's car data must reach the sector lines at the official sector times (± tol s). This catches
        car data whose clock is off when there are no positions to check it against."""
        marks = self.sector_marks
        if not marks or not sectors or sectors[0] is None or sectors[1] is None:
            return True
        t, d = tel["Time"].to_numpy(float), tel["Distance"].to_numpy(float)
        for f, want in zip(marks, (sectors[0], sectors[0] + sectors[1])):
            if abs(float(np.interp(f * d[-1], d, t)) - want) > tol:
                return False
        return True

    def on_ref_path(self, tel: pd.DataFrame) -> pd.DataFrame:
        """X/Y/Z for a lap whose own positions are broken: its distance mapped onto the reference lap's path."""
        ref = self.ref_path
        rd = ref["Distance"].to_numpy(float)
        d = tel["Distance"].to_numpy(float) * (rd[-1] / max(float(tel["Distance"].iloc[-1]), 1.0))
        tel = tel.copy()
        for col in ("X", "Y", "Z"):
            tel[col] = np.interp(d, rd, ref[col].to_numpy(float))
        return tel

    def clean_fastest(self, code: str, limits: dict | None):
        """Fastest lap whose telemetry is intact. If only the positions on the official fastest lap are
        broken, the lap is kept and drawn on the session's reference path (`info["map"] = "reference"`).
        If its car data is broken, the next fastest clean lap is used and `info["substitute_for"]` names
        the official one."""
        tel, info = self.lap_telemetry(code)
        timing = self.sectors_ok(tel, info["sectors"]) if limits else True
        if timing and self.telemetry_ok(tel, limits):
            return tel, info
        if timing and limits and self.car_ok(tel, limits) and not self.pos_intact(tel, limits):
            try:
                tel = self.on_ref_path(tel)
                info["map"] = "reference"
                return tel, info
            except DataError:
                pass
        num = next(n for n in self.drivers_raw if self.code(n) == code)
        cands = sorted((r for r in self.laps.get(num, []) if r["time"] and not r["deleted"] and r["lap"] != info["lap"]
                        and r["pit_in_t"] is None and not r["pit_out_lap"]), key=lambda r: r["time"])
        for r in cands:
            try:
                t2, i2 = self.lap_telemetry(code, str(r["lap"]))
            except DataError:
                continue
            if self.telemetry_ok(t2, limits) and self.sectors_ok(t2, i2["sectors"]):
                i2["substitute_for"] = {"lap": info["lap"], "time": info["time"], "time_str": info["time_str"]}
                return t2, i2
        raise DataError(f"{code}'s laps in this session have incomplete telemetry in the source data")

    def lap_telemetry(self, code: str, lap: str = "fastest"):
        num = next((n for n in self.drivers_raw if self.code(n) == code), None)
        if num is None:
            raise DataError(f"Unknown driver {code}")
        rows = self.laps.get(num, [])
        if lap == "fastest":
            row = self.best(num)
            if row is None:
                raise DataError(f"{code} has no timed lap")
        else:
            row = next((r for r in rows if r["lap"] == int(lap)), None)
            if row is None:
                raise DataError(f"{code} has no lap {lap}")
        tel = self.lap_frame(num, row)
        timed = row["time"] is not None
        lt = round(row["time"] if timed else float(tel["Time"].iloc[-1]), 3)
        info = {"lap": row["lap"], "time": lt, "timed": timed, "deleted": row["deleted"],
                "time_str": _fmt_laptime(lt) if timed else f"~{_fmt_laptime(lt)}",
                "compound": row["compound"], "tyre_life": row["age"],
                "sectors": [round(x, 3) if x is not None else None for x in row["sectors"]]}
        return tel, info

    # ------------------------------------------------------------------ circuit
    @cached_property
    def circuit_raw(self) -> dict:
        """Corner markers from MultiViewer (the source FastF1 uses); {} for a track it doesn't know yet."""
        key = self.s.get("circuit_key") or self.meeting.get("circuit_key")
        for yr in (self.year, self.year - 1, self.year - 2):
            try:
                return _cached_json(self.c.cache, f"circuit_{key}_{yr}", f"https://api.multiviewer.app/api/v1/circuits/{key}/{yr}")
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
        return {}

    def reference_lap(self) -> pd.DataFrame:
        """Fastest lap of the session with intact data (corner distances are measured along it, like FastF1)."""
        return self.ref_path

    def circuit(self) -> dict:
        ci = self.circuit_raw
        rot = float(ci.get("rotation") or 0.0)
        ref = self.reference_lap()
        rx, ry, rd = ref["X"].to_numpy(), ref["Y"].to_numpy(), ref["Distance"].to_numpy()
        corners = []
        for c in ci.get("corners", []):
            x0, y0 = float(c["trackPosition"]["x"]), float(c["trackPosition"]["y"])
            k = int(np.argmin((rx - x0) ** 2 + (ry - y0) ** 2))
            x, y = rotate(np.array([x0]), np.array([y0]), rot)
            letter = c.get("letter") or ""
            corners.append({"number": int(c["number"]), "label": f"T{int(c['number'])}{letter}", "distance": float(rd[k]),
                            "x": float(x[0]), "y": float(y[0])})
        corners.sort(key=lambda c: c["distance"])
        return {"rotation": rot, "corners": corners}

    # ------------------------------------------------------------------ pit-wall replay
    def replay_positions(self, num: str) -> pd.DataFrame:
        """The driver's position samples, with laps whose positions are missing or frozen in the source
        redrawn from the car speed along the reference path (when the car data on that lap is intact)."""
        p = self.pos(num)
        L = self.limits
        if not L:
            return p
        keep = np.ones(len(p), bool)
        add = []
        pt = p["t"].to_numpy(float) if len(p) else np.array([])
        for r in self.laps.get(num, []):
            if r["start"] is None or r["end"] is None or not (40 < r["end"] - r["start"] < 400):
                continue
            try:
                tel = self.lap_frame(num, r)
            except DataError:
                continue
            if self.pos_intact(tel, L):
                continue
            if abs(float(tel["Distance"].iloc[-1]) / L["length"] - 1) > 0.05 or self.frozen_s(tel) > 3.0 \
                    or not self.sectors_ok(tel, r["sectors"], tol=1.0):
                continue                                  # car data broken too (or a pit lap): leave as is
            tel = self.on_ref_path(tel)
            st = self.line_time(num, r["start"], wide=0.6 * (r["time"] or r["end"] - r["start"]))
            t = st + tel["Time"].to_numpy(float)
            if len(pt):
                keep &= ~((pt >= t[0]) & (pt <= t[-1]))
            step = max(1, len(t) // 400)
            add.append(pd.DataFrame({"t": np.round(t[::step], 3), "x": tel["X"].to_numpy()[::step],
                                     "y": tel["Y"].to_numpy()[::step], "z": tel["Z"].to_numpy()[::step]}))
        if not add:
            return p
        out = pd.concat([p.loc[keep, ["t", "x", "y", "z"]]] + add, ignore_index=True)
        return out.sort_values("t").drop_duplicates("t", keep="last").reset_index(drop=True)

    def race_data(self) -> RaceData:
        circ = self.circuit()
        rot = circ["rotation"]
        grid_of = {}
        for num in self.drivers_raw:
            jr = self.jolpica.get(num, {})
            g = jr.get("grid")
            grid_of[num] = 99 if g in (None, "") else (98 if int(g) == 0 else int(g))
        order = sorted(self.drivers_raw, key=lambda n: grid_of[n])
        drivers = [{"code": self.code(n), "number": n, "name": self.full_name(n), "team": self.drivers_raw[n].get("team_name") or "",
                    "color": _colour(self.drivers_raw[n].get("team_colour"))} for n in order]
        msgs = []
        for m in self._get("race_control"):
            if not m.get("date"):
                continue
            msgs.append({"t": self.sec(m["date"]), "category": m.get("category"), "flag": m.get("flag"), "scope": m.get("scope"),
                         "sector": m.get("sector"), "number": None if m.get("driver_number") is None else str(m["driver_number"]),
                         "message": str(m.get("message") or ""), "lap": m.get("lap_number")})
        from .livecore import NEUTRAL_CODES, derive_status
        starts = [r["start"] for ls in self.laps.values() for r in ls if r["lap"] == 1 and r["start"] is not None]
        race_start = min(starts) if starts else 0.0
        status = derive_status(msgs, race_start - 90)
        neutral = []
        for a, b in zip(status, status[1:] + [{"t": 1e12}]):
            if a["code"] in NEUTRAL_CODES:
                neutral.append((a["t"], b["t"]))
        laps, car, pos = {}, {}, {}
        for n in order:
            code = self.code(n)
            rows = []
            for r in self.laps.get(n, []):
                st, en = r["start"], r["end"]
                nt = bool(st is not None and en is not None and any(a < en and b > st for a, b in neutral))
                rows.append({"lap": r["lap"], "start": st, "end": en, "s1": r["s1"], "s2": r["s2"], "time": r["time"],
                             "compound": r["compound"], "age": r["age"], "stint": r["stint"], "pit_in_t": r["pit_in_t"],
                             "pit_out_t": r["pit_out_t"], "neutral": nt, "deleted": r["deleted"]})
            laps[code] = rows
            c, p = self.car(n)
            if not c.empty:
                car[code] = {"t": c["t"].to_numpy(float), "speed": c["speed"].astype(float).to_numpy(),
                             "throttle": c["throttle"].astype(float).to_numpy(),
                             "brake": (c["brake"].fillna(0).astype(float).to_numpy() > 0) * 100.0,
                             "gear": c["n_gear"].fillna(0).astype(float).to_numpy(), "rpm": c["rpm"].astype(float).to_numpy(),
                             "drs": c["drs"].fillna(0).astype(float).to_numpy() if "drs" in c else np.zeros(len(c))}
            p = self.replay_positions(n)
            if not p.empty:
                x, y = rotate(p["x"].to_numpy(float), p["y"].to_numpy(float), rot)
                pos[code] = {"t": p["t"].to_numpy(float), "x": x, "y": y}
        ends = [r["end"] for ls in laps.values() for r in ls if r["end"] is not None]
        winner = next((r for r in self.jolpica.values() if str(r.get("position")) == "1"), None)
        total = int(winner["laps"]) if winner and winner.get("laps") else max((r["lap"] for ls in laps.values() for r in ls), default=1)
        ref = self.reference_lap()
        x, y = rotate(ref["X"].to_numpy(float), ref["Y"].to_numpy(float), rot)
        track = {"x": np.round(x[::2], 1).tolist(), "y": np.round(y[::2], 1).tolist(), "corners": circ["corners"]}
        return RaceData(name=f"{self.meeting.get('meeting_name', '')} · {self.name}", total_laps=total, start=race_start - 90,
                        end=(max(ends) + 60) if ends else race_start + 7200, drivers=drivers, laps=laps, car=car, pos=pos,
                        messages=msgs, status=[], track=track, meta={"race_start": race_start})
