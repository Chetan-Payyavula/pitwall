"""Real data sources for the live Pit Wall, both producing a RaceData.

* FastF1 replay — any past session, replayed on its own clock.
* OpenF1 live — polls api.openf1.org during a session. Real-time OpenF1 data
  needs a (paid) OpenF1 account: set OPENF1_USERNAME / OPENF1_PASSWORD.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
import pandas as pd

from . import data
from .analysis import rotate
from .livecore import RaceData


def _s(v) -> float | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "total_seconds"):
        return float(v.total_seconds())
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# =============================================================== FastF1 replay
@lru_cache(maxsize=2)
def fastf1_race(year: int, event: str, session: str) -> RaceData:
    f = data.ff1()
    try:
        ev = int(event) if str(event).isdigit() else event
        s = f.get_session(year, ev, session)
        s.load(laps=True, telemetry=True, weather=False, messages=True)
        _ = s.laps
    except Exception as exc:
        raise data.DataError(f"Could not load {year} {event} {session}: {exc}") from exc
    return race_from_fastf1(s)


def race_from_fastf1(s) -> RaceData:
    res = s.results.copy()
    grid = pd.to_numeric(res.get("GridPosition"), errors="coerce").fillna(99).replace(0, 98)
    res = res.assign(_g=grid.values).sort_values("_g")
    drivers = [{"code": r["Abbreviation"], "number": str(r["DriverNumber"]), "name": r["FullName"], "team": r["TeamName"],
                "color": data._colour(r["TeamColor"])} for _, r in res.iterrows() if isinstance(r["Abbreviation"], str)]
    try:
        ci = s.get_circuit_info()
        rot = float(ci.rotation or 0.0)
        corners = []
        for _, c in ci.corners.iterrows():
            x, y = rotate(np.array([c["X"]]), np.array([c["Y"]]), rot)
            corners.append({"number": int(c["Number"]), "label": f"T{int(c['Number'])}{c.get('Letter') or ''}",
                            "distance": float(c["Distance"]), "x": float(x[0]), "y": float(y[0])})
    except Exception:
        rot, corners = 0.0, []

    laps, car, pos = {}, {}, {}
    all_laps = s.laps
    for d in drivers:
        dl = all_laps[all_laps["Driver"] == d["code"]].sort_values("LapNumber")
        rows = []
        for _, r in dl.iterrows():
            st, en = _s(r.get("LapStartTime")), _s(r.get("Time"))
            lt = _s(r.get("LapTime"))
            if lt is None and st is not None and en is not None:
                lt = en - st
            ts = str(r.get("TrackStatus") or "")
            rows.append({"lap": int(r["LapNumber"]), "start": st, "end": en, "s1": _s(r.get("Sector1SessionTime")),
                         "s2": _s(r.get("Sector2SessionTime")), "time": lt,
                         "compound": None if pd.isna(r.get("Compound")) else str(r.get("Compound")),
                         "age": None if pd.isna(r.get("TyreLife")) else int(r.get("TyreLife")),
                         "stint": None if pd.isna(r.get("Stint")) else int(r.get("Stint")),
                         "pit_in_t": _s(r.get("PitInTime")),
                         "pit_out_t": _s(r.get("PitOutTime")) if int(r["LapNumber"]) > 1 else None,
                         "neutral": any(ch in ts for ch in "4567"), "deleted": bool(r.get("Deleted")) if not pd.isna(r.get("Deleted")) else False})
        laps[d["code"]] = rows
        num = d["number"]
        try:
            c = s.car_data[num]
            car[d["code"]] = {"t": c["SessionTime"].dt.total_seconds().to_numpy(), "speed": c["Speed"].to_numpy(float),
                              "throttle": c["Throttle"].to_numpy(float), "brake": c["Brake"].astype(float).to_numpy() * 100,
                              "gear": c["nGear"].to_numpy(float), "rpm": c["RPM"].to_numpy(float), "drs": c["DRS"].to_numpy(float)}
        except Exception:
            pass
        try:
            p = s.pos_data[num]
            x, y = rotate(p["X"].to_numpy(float), p["Y"].to_numpy(float), rot)
            pos[d["code"]] = {"t": p["SessionTime"].dt.total_seconds().to_numpy(), "x": x, "y": y}
        except Exception:
            pass

    msgs = []
    try:
        rcm = s.race_control_messages
        t0 = pd.Timestamp(s.t0_date)
        for _, m in rcm.iterrows():
            tm = pd.Timestamp(m["Time"])
            msgs.append({"t": (tm - t0).total_seconds(), "category": m.get("Category"), "flag": None if pd.isna(m.get("Flag")) else m.get("Flag"),
                         "scope": None if pd.isna(m.get("Scope")) else m.get("Scope"), "sector": None if pd.isna(m.get("Sector")) else m.get("Sector"),
                         "number": None if pd.isna(m.get("RacingNumber")) else m.get("RacingNumber"), "message": str(m.get("Message")),
                         "lap": None if pd.isna(m.get("Lap")) else int(m.get("Lap"))})
    except Exception:
        pass
    status = []
    try:
        for _, r in s.track_status.iterrows():
            status.append({"t": _s(r["Time"]), "code": str(r["Status"]), "message": str(r.get("Message", ""))})
    except Exception:
        pass

    starts = [l["start"] for ls in laps.values() for l in ls if l["lap"] == 1 and l["start"] is not None]
    ends = [l["end"] for ls in laps.values() for l in ls if l["end"] is not None]
    race_start = min(starts) if starts else 0.0
    total = int(getattr(s, "total_laps", None) or max((l["lap"] for ls in laps.values() for l in ls), default=1))
    track = {"x": [], "y": [], "corners": corners}
    try:
        fl = s.laps.pick_fastest()
        pd_ = fl.get_pos_data()
        x, y = rotate(pd_["X"].to_numpy(float), pd_["Y"].to_numpy(float), rot)
        track = {"x": np.round(x[::2], 1).tolist(), "y": np.round(y[::2], 1).tolist(), "corners": corners}
    except Exception:
        pass
    return RaceData(name=f"{s.event['EventName']} · {s.name}", total_laps=total, start=race_start - 90, end=(max(ends) + 60) if ends else race_start + 7200,
                    drivers=drivers, laps=laps, car=car, pos=pos, messages=msgs, status=status, track=track,
                    meta={"race_start": race_start})


# =============================================================== OpenF1 live
OPENF1 = "https://api.openf1.org/v1"


def _iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def race_from_openf1(p: dict, total_laps: int | None = None) -> RaceData:
    """Pure conversion of OpenF1 payloads → RaceData. `p` maps endpoint → list of records."""
    sess = p["sessions"][0]
    t0 = _iso(sess["date_start"])
    sec = lambda iso: None if iso is None else (_iso(iso) - t0).total_seconds()
    drv = {}
    for d in p.get("drivers", []):
        drv[str(d["driver_number"])] = {"code": d.get("name_acronym") or str(d["driver_number"]), "number": str(d["driver_number"]),
                                         "name": d.get("full_name") or d.get("broadcast_name") or "", "team": d.get("team_name") or "",
                                         "color": "#" + (d.get("team_colour") or "888888").lstrip("#")}
    stints = {}
    for st in p.get("stints", []):
        stints.setdefault(str(st["driver_number"]), []).append(st)

    def tyre(num, lap):
        for st in stints.get(num, []):
            hi = st.get("lap_end") or 10_000
            if st.get("lap_start", 1) <= lap <= hi:
                return st.get("compound"), (st.get("tyre_age_at_start") or 0) + (lap - st.get("lap_start", 1)) + 1, st.get("stint_number")
        return None, None, None

    laps = {d["code"]: [] for d in drv.values()}
    by_num = {}
    for l in p.get("laps", []):
        by_num.setdefault(str(l["driver_number"]), []).append(l)
    for num, ls in by_num.items():
        if num not in drv:
            continue
        ls.sort(key=lambda r: r["lap_number"])
        rows = []
        for i, l in enumerate(ls):
            st = sec(l.get("date_start"))
            dur = l.get("lap_duration")
            if st is None and dur is not None and i + 1 < len(ls) and ls[i + 1].get("date_start"):
                st = sec(ls[i + 1]["date_start"]) - dur
            en = st + dur if (st is not None and dur is not None) else None
            d1, d2 = l.get("duration_sector_1"), l.get("duration_sector_2")
            comp, age, stint = tyre(num, l["lap_number"])
            rows.append({"lap": l["lap_number"], "start": st, "end": en, "time": dur,
                         "s1": st + d1 if st is not None and d1 else None, "s2": st + d1 + d2 if st is not None and d1 and d2 else None,
                         "compound": comp, "age": age, "stint": stint, "pit_in_t": None,
                         "pit_out_t": (st + 5) if l.get("is_pit_out_lap") and l["lap_number"] > 1 and st is not None else None,
                         "neutral": None})
        for a, b in zip(rows, rows[1:]):
            if b["pit_out_t"] is not None and a["end"] is not None:
                a["pit_in_t"] = a["end"] - 5
        laps[drv[num]["code"]] = rows

    def series(endpoint, fields):
        out = {}
        for r in p.get(endpoint, []):
            out.setdefault(str(r["driver_number"]), []).append(r)
        res = {}
        for num, rs in out.items():
            if num not in drv:
                continue
            rs.sort(key=lambda r: r["date"])
            res[drv[num]["code"]] = {"t": np.array([sec(r["date"]) for r in rs], float),
                                     **{k: np.array([r.get(src) if r.get(src) is not None else np.nan for r in rs], float) for k, src in fields.items()}}
        return res

    car = series("car_data", {"speed": "speed", "throttle": "throttle", "brake": "brake", "gear": "n_gear", "rpm": "rpm", "drs": "drs"})
    pos = series("location", {"x": "x", "y": "y"})
    msgs = [{"t": sec(m["date"]), "category": m.get("category"), "flag": m.get("flag"), "scope": m.get("scope"),
             "sector": m.get("sector"), "number": m.get("driver_number"), "message": m.get("message") or "", "lap": m.get("lap_number")}
            for m in p.get("race_control", []) if m.get("date")]
    order = sorted(drv.values(), key=lambda d: next((x["position"] for x in p.get("starting_grid", []) if str(x.get("driver_number")) == d["number"]), 99))
    ends = [l["end"] for ls in laps.values() for l in ls if l["end"] is not None]
    max_lap = max((l["lap"] for ls in laps.values() for l in ls), default=1)
    track = {"x": [], "y": [], "corners": []}
    if pos:
        code, pp = max(pos.items(), key=lambda kv: len(kv[1]["t"]))
        lead_laps = [l for l in laps.get(code, []) if l["start"] is not None and l["end"] is not None and not l.get("pit_out_t")]
        if lead_laps:
            l = lead_laps[-1]
            m = (pp["t"] >= l["start"]) & (pp["t"] <= l["end"])
            track = {"x": pp["x"][m][::2].round(1).tolist(), "y": pp["y"][m][::2].round(1).tolist(), "corners": []}
    if total_laps is None:
        total_laps = max_lap
        if track["x"]:
            length_m = float(np.hypot(np.diff(track["x"]), np.diff(track["y"])).sum()) / 10.0
            if length_m > 1000:
                total_laps = max(max_lap, int(np.ceil(305_000 / length_m)))
    return RaceData(name=f"{sess.get('circuit_short_name', '')} · {sess.get('session_name', '')} (live)", total_laps=total_laps,
                    start=0.0, end=max(ends + [0.0]) + 60, drivers=order, laps=laps, car=car, pos=pos, messages=msgs, status=[],
                    track=track, live=True, meta={"t0": sess["date_start"], "total_laps_estimated": True})


class RateLimiter:
    """Client-side limiter for OpenF1's sponsor tier (6 requests/s, 60 requests/min).

    Keeps a safety margin (default 5/s and 50/min) and blocks until a request is allowed.
    """

    def __init__(self, per_sec: int = 5, per_min: int = 50, clock=time.monotonic, sleep=time.sleep):
        self.per_sec, self.per_min, self.clock, self.sleep = per_sec, per_min, clock, sleep
        self.stamps: list[float] = []
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            while True:
                now = self.clock()
                self.stamps = [t for t in self.stamps if now - t < 60.0]
                last_sec = [t for t in self.stamps if now - t < 1.0]
                if len(self.stamps) < self.per_min and len(last_sec) < self.per_sec:
                    self.stamps.append(now)
                    return
                wait_min = 60.0 - (now - self.stamps[0]) if len(self.stamps) >= self.per_min else 0.0
                wait_sec = 1.0 - (now - last_sec[0]) if len(last_sec) >= self.per_sec else 0.0
                self.sleep(max(0.01, wait_min, wait_sec))


class OpenF1Feed:
    """Background poller: keeps a RaceData up to date for one OpenF1 session.

    Request budget (stays under the sponsor limit of 60/min): every 4 s it fetches the new
    car_data + location samples and ONE of the slower lists in rotation, i.e. 3 requests
    per poll = 45/min. Laps and race control come round every 8 s, stints every 12 s,
    the driver list every 24 s. On HTTP 429 it backs off (8 s, 16 s, … up to 60 s).
    """

    POLL_S = 4.0
    DELAY_S = 4.0
    KEEP_S = 900          # keep 15 minutes of high-rate car/location data
    FAST = ("car_data", "location")
    SLOW = ("laps", "race_control", "stints", "laps", "race_control", "drivers")

    def __init__(self, session_key: str = "latest", username: str | None = None, password: str | None = None,
                 http=None, limiter: RateLimiter | None = None):
        self.session_key = session_key
        self.username, self.password = username, password
        self.http = http or self._http
        self.limiter = limiter or RateLimiter()
        self.token, self.token_exp = None, 0.0
        self.payload: dict = {}
        self.cursor: dict = {}
        self.rd: RaceData | None = None
        self.error: str | None = None
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.t0: datetime | None = None
        self.turn = 0
        self.backoff = 0.0
        self.requests = 0

    # ---------------------------------------------------------------- http
    def _http(self, url: str, form: dict | None = None, headers: dict | None = None):
        req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode() if form else None, headers=headers or {})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())

    def _call(self, url: str, **kw):
        self.limiter.wait()
        self.requests += 1
        return self.http(url, **kw)

    def _auth(self) -> dict:
        if not (self.username and self.password):
            return {}
        if not self.token or time.time() > self.token_exp - 120:
            tok = self._call("https://api.openf1.org/token", form={"username": self.username, "password": self.password},
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
            self.token, self.token_exp = tok["access_token"], time.time() + float(tok.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, endpoint: str, **params):
        q = "&".join(f"{k}={urllib.parse.quote(str(v), safe='<>=:+-.')}" for k, v in params.items())
        return self._call(f"{OPENF1}/{endpoint}?{q}", headers=self._auth())

    # ---------------------------------------------------------------- polling
    def poll_once(self):
        sk = self.session_key
        if "sessions" not in self.payload or self.payload["sessions"][0].get("session_key") is None:
            self.payload["sessions"] = self.get("sessions", session_key=sk)
            if not self.payload["sessions"]:
                raise RuntimeError("OpenF1 returned no session")
            sk = self.session_key = str(self.payload["sessions"][0]["session_key"])
            self.t0 = _iso(self.payload["sessions"][0]["date_start"])
        # first poll: everything once; afterwards one slow list per poll (see class docstring)
        missing = [ep for ep in dict.fromkeys(self.SLOW) if ep not in self.payload]
        slow = missing or [self.SLOW[self.turn % len(self.SLOW)]]
        self.turn += 1
        for ep in slow:
            self.payload[ep] = self.get(ep, session_key=sk)
        if "starting_grid" not in self.payload:
            try:
                self.payload["starting_grid"] = self.get("starting_grid", session_key=sk)
            except Exception:
                self.payload["starting_grid"] = []
        for ep in self.FAST:
            since = self.cursor.get(ep) or (datetime.now(timezone.utc).timestamp() - 120)
            iso = datetime.fromtimestamp(since, timezone.utc).isoformat().replace("+00:00", "Z")
            new = self.get(ep, session_key=sk, **{"date>": iso})
            rows = self.payload.get(ep, []) + new
            if rows:
                last = max(_iso(r["date"]).timestamp() for r in rows)
                self.cursor[ep] = last
                rows = [r for r in rows if _iso(r["date"]).timestamp() > last - self.KEEP_S]
            self.payload[ep] = rows
        rd = race_from_openf1(self.payload)
        with self.lock:
            self.rd, self.error = rd, None

    def _loop(self):
        while True:
            try:
                self.poll_once()
                self.backoff = 0.0
            except Exception as exc:  # network, auth, rate limit …
                if getattr(exc, "code", None) == 429:          # too many requests: slow down
                    self.backoff = min(60.0, max(8.0, self.backoff * 2))
                with self.lock:
                    self.error = str(exc)
            time.sleep(self.POLL_S + self.backoff)

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def race(self) -> RaceData:
        with self.lock:
            if self.rd is None:
                raise data.DataError(self.error or "Connecting to OpenF1… try again in a few seconds.")
            return self.rd

    def now_t(self) -> float:
        if self.t0 is None:
            return 0.0
        return (datetime.now(timezone.utc) - self.t0).total_seconds() - self.DELAY_S


_feeds: dict = {}


def openf1_configured() -> bool:
    return bool(os.environ.get("OPENF1_USERNAME") and os.environ.get("OPENF1_PASSWORD"))


def openf1_feed(session_key: str = "latest") -> OpenF1Feed:
    if not openf1_configured():
        raise data.DataError("Live OpenF1 data needs an OpenF1 account (a sponsor login). Set OPENF1_USERNAME and "
                             "OPENF1_PASSWORD before starting the server, or use Replay to run a past race as if it were live.")
    if session_key not in _feeds:
        f = OpenF1Feed(session_key, os.environ["OPENF1_USERNAME"], os.environ["OPENF1_PASSWORD"])
        f.start()
        _feeds[session_key] = f
    return _feeds[session_key]
