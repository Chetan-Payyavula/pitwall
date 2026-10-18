"""Source-independent live timing engine.

Every data source (built-in demo race, FastF1 replay of a past race, OpenF1
live polling) is converted into one `RaceData` object. `snapshot(rd, t)` then
answers "what does the timing screen look like at session time t": running
order, gaps, intervals, tyres, pit status, car telemetry, positions on the map,
track status, yellow sectors and race-control messages.

All times are seconds on one session clock.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

STATUS_LABEL = {"1": "GREEN", "2": "YELLOW", "4": "SAFETY CAR", "5": "RED FLAG", "6": "VSC", "7": "VSC ENDING"}
NEUTRAL_CODES = {"4", "5", "6", "7"}
CODE_RE = re.compile(r"\(([A-Z]{3})\)")
CAR_RE = re.compile(r"\bCARS? ((?:\d{1,2}(?:,| AND|)\s*)+)")


@dataclass
class RaceData:
    name: str
    total_laps: int
    start: float
    end: float
    drivers: list            # [{code, number, name, team, color}] in grid order
    laps: dict               # code -> [{lap, start, end, s1, s2, time, compound, age, stint, pit_in_t, pit_out_t, neutral}]
    car: dict                # code -> {t, speed, throttle, brake, gear, rpm, drs} (np arrays)
    pos: dict                # code -> {t, x, y}
    messages: list           # [{t, category, flag, scope, sector, number, message, lap}]
    status: list             # [{t, code, message}]
    track: dict              # {x, y, corners}
    simulated: bool = False
    live: bool = False
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.reindex()

    # --------------------------------------------------------------- indexes
    def reindex(self):
        self.codes = [d["code"] for d in self.drivers]
        self.by_code = {d["code"]: d for d in self.drivers}
        self.num2code = {str(d["number"]): d["code"] for d in self.drivers}
        self._pts = {}
        for code in self.codes:
            pts = {}
            for l in self.laps.get(code, []):
                for k, key in enumerate(("s1", "s2", "end")):
                    v = l.get(key)
                    if v is not None and np.isfinite(v):
                        pts[(l["lap"] - 1) * 3 + k + 1] = float(v)
            items = sorted(pts.items(), key=lambda kv: kv[1])
            self._pts[code] = (np.array([p for p, _ in items], dtype=int), np.array([v for _, v in items], dtype=float), pts)
        self.messages.sort(key=lambda m: m["t"])
        for m in self.messages:
            m.setdefault("codes", message_codes(m, self.num2code))
            m.setdefault("kind", message_kind(m))
        if not self.status:
            self.status = derive_status(self.messages, self.start)
        self.status.sort(key=lambda s: s["t"])
        self._status_t = np.array([s["t"] for s in self.status]) if self.status else np.array([])
        self._msg_t = np.array([m["t"] for m in self.messages]) if self.messages else np.array([])
        typical = [l["time"] for ls in self.laps.values() for l in ls if l.get("time") and not l.get("neutral")]
        self.typical_lap = float(np.median(typical)) if typical else 90.0
        for code in self.codes:
            c = self.car.get(code)
            if c is not None and len(c.get("t", [])):
                order = np.argsort(c["t"])
                self.car[code] = {k: np.asarray(v)[order] for k, v in c.items()}
            p = self.pos.get(code)
            if p is not None and len(p.get("t", [])):
                order = np.argsort(p["t"])
                self.pos[code] = {k: np.asarray(v, dtype=float)[order] for k, v in p.items()}

    def status_at(self, t: float) -> dict:
        if not len(self._status_t):
            return {"t": self.start, "code": "1", "message": ""}
        i = int(np.searchsorted(self._status_t, t, side="right")) - 1
        return self.status[max(i, 0)] if i >= 0 else {"t": self.start, "code": "1", "message": ""}

    def neutral_intervals(self) -> list[tuple[float, float, str]]:
        out, cur = [], None
        for s in self.status:
            if s["code"] in NEUTRAL_CODES and cur is None:
                cur = (s["t"], s["code"])
            elif s["code"] not in NEUTRAL_CODES and cur is not None:
                out.append((cur[0], s["t"], cur[1])); cur = None
        if cur is not None:
            out.append((cur[0], float("inf"), cur[1]))
        return out


# ===================================================================== messages
def message_codes(m: dict, num2code: dict) -> list[str]:
    known = set(num2code.values())
    codes = [c for c in CODE_RE.findall(m.get("message", "") or "") if c in known]
    if not codes:
        n = m.get("number")
        if n not in (None, "", "nan"):
            try:
                c = num2code.get(str(int(float(n))))
            except (TypeError, ValueError):
                c = None
            if c:
                codes = [c]
    if not codes:
        hit = CAR_RE.search(m.get("message", "") or "")
        if hit:
            codes = [num2code[n] for n in re.findall(r"\d{1,2}", hit.group(1)) if n in num2code]
    return list(dict.fromkeys(codes))


def message_kind(m: dict) -> str:
    msg = (m.get("message") or "").upper()
    flag = (m.get("flag") or "").upper()
    if flag == "CHEQUERED" or "CHEQUERED" in msg:
        return "chequered"
    if "VIRTUAL SAFETY CAR" in msg or "VSC" in msg:
        return "vsc"
    if "SAFETY CAR" in msg:
        return "sc"
    if flag == "RED" or re.search(r"\bRED FLAG\b", msg):
        return "red"
    if "TRACK LIMITS" in msg:
        return "track-limits"
    if "PENALTY" in msg:
        return "penalty"
    if "INVESTIGATION" in msg or "NOTED" in msg or "INCIDENT" in msg or "OFF TRACK" in msg:
        return "incident"
    if flag == "DOUBLE YELLOW":
        return "double-yellow"
    if flag == "YELLOW":
        return "yellow"
    if flag in ("GREEN", "CLEAR"):
        return "green"
    if flag == "BLUE":
        return "blue"
    if "DRS" in msg:
        return "drs"
    return "info"


def derive_status(messages: list, start: float) -> list:
    """Track status timeline from race-control text (OpenF1 has no status feed)."""
    out = [{"t": start, "code": "1", "message": "TRACK CLEAR"}]
    for m in sorted(messages, key=lambda x: x["t"]):
        msg = (m.get("message") or "").upper()
        flag = (m.get("flag") or "").upper()
        code = None
        if "VIRTUAL SAFETY CAR DEPLOYED" in msg:
            code = "6"
        elif "VIRTUAL SAFETY CAR ENDING" in msg or "VSC ENDING" in msg:
            code = "7"
        elif "SAFETY CAR DEPLOYED" in msg:
            code = "4"
        elif flag == "RED":
            code = "5"
        elif (flag in ("GREEN", "CLEAR") and (m.get("scope") or "").upper() == "TRACK") or "TRACK CLEAR" in msg \
                or ("GREEN LIGHT" in msg and out[-1]["code"] == "5"):
            code = "1"
        elif "SAFETY CAR IN THIS LAP" in msg:
            continue
        if code and code != out[-1]["code"]:
            out.append({"t": m["t"], "code": code, "message": m.get("message", "")})
        # SC "in this lap" → green comes with the next GREEN flag message
    return out


def yellow_sectors(rd: RaceData, t: float) -> dict:
    active: dict[int, str] = {}
    k = int(np.searchsorted(rd._msg_t, t, side="right")) if len(rd._msg_t) else 0
    for m in rd.messages[:k]:
        flag = (m.get("flag") or "").upper()
        scope = (m.get("scope") or "").upper()
        sec = m.get("sector")
        if scope == "SECTOR" and sec not in (None, ""):
            try:
                sec = int(float(sec))
            except (TypeError, ValueError):
                continue
            if flag in ("YELLOW", "DOUBLE YELLOW"):
                active[sec] = flag
            elif flag in ("CLEAR", "GREEN"):
                active.pop(sec, None)
        elif scope == "TRACK" and flag in ("GREEN", "CLEAR", "CHEQUERED"):
            active.clear()
    return active


# ===================================================================== snapshot
def _at(arr_t, arr_v, t, default=None):
    if arr_t is None or not len(arr_t):
        return default
    i = int(np.searchsorted(arr_t, t, side="right")) - 1
    if i < 0:
        return default
    v = arr_v[i]
    try:
        return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else v
    except TypeError:
        return v


def _interp_xy(p: dict, t: float):
    if not p or not len(p.get("t", [])):
        return None, None
    tt, x, y = p["t"], p["x"], p["y"]
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 2:
        return None, None
    tt, x, y = tt[ok], x[ok], y[ok]
    if t < tt[0] - 5 or t > tt[-1] + 90:
        return None, None
    return float(np.interp(t, tt, x)), float(np.interp(t, tt, y))


def lap_rows_done(rd: RaceData, code: str, t: float) -> list:
    return [l for l in rd.laps.get(code, []) if l.get("end") is not None and l["end"] <= t]


def snapshot(rd: RaceData, t: float) -> dict:
    t = float(t)
    state = {}
    for code in rd.codes:
        idx, tt, _ = rd._pts[code]
        k = int(np.searchsorted(tt, t, side="right"))
        state[code] = (int(idx[k - 1]) if k else 0, float(tt[k - 1]) if k else None)
    grid = {c: i for i, c in enumerate(rd.codes)}

    finished_p = rd.total_laps * 3
    retired = set()
    lead_p = max((s[0] for s in state.values()), default=0)
    for code, (p, tp) in state.items():
        if p >= finished_p or p == 0:
            continue
        stale = tp is not None and t - tp > max(2.6 * rd.typical_lap, 240)
        if stale and lead_p - p >= 3:
            retired.add(code)

    def key(c):
        p, tp = state[c]
        return (c in retired, -p, tp if tp is not None else 0.0, grid[c])
    order = sorted(rd.codes, key=key)
    leader = order[0]
    lead_pts = rd._pts[leader][2]

    best_overall = None
    for code in rd.codes:
        for l in lap_rows_done(rd, code, t):
            if l.get("time") and not l.get("deleted") and (best_overall is None or l["time"] < best_overall):
                best_overall = l["time"]

    # race control effects per driver (up to t)
    k_msg = int(np.searchsorted(rd._msg_t, t, side="right")) if len(rd._msg_t) else 0
    msgs = rd.messages[:k_msg]
    per = {c: {"track_limits": 0, "deleted": 0, "penalties": [], "investigations": 0, "blue": False} for c in rd.codes}
    for m in msgs:
        for c in m.get("codes", []):
            if c not in per:
                continue
            up = (m.get("message") or "").upper()
            if m["kind"] == "track-limits":
                per[c]["track_limits"] += 1
                if "DELETED" in up:
                    per[c]["deleted"] += 1
            elif m["kind"] == "penalty":
                per[c]["penalties"].append(m["message"])
            elif m["kind"] == "incident":
                per[c]["investigations"] += 1
            elif m["kind"] == "blue" and t - m["t"] < 20:
                per[c]["blue"] = True

    rows = []
    prev = None
    for pos_i, code in enumerate(order, 1):
        d = rd.by_code[code]
        p, tp = state[code]
        laps_all = rd.laps.get(code, [])
        done = lap_rows_done(rd, code, t)
        cur = next((l for l in laps_all if l.get("start") is not None and l["start"] <= t < (l.get("end") or np.inf)), None)
        if cur is None:
            cur = laps_all[len(done)] if len(done) < len(laps_all) else (done[-1] if done else None)
        last = done[-1] if done else None
        best = min((l["time"] for l in done if l.get("time") and not l.get("deleted")), default=None)

        gap = interval = None
        laps_down = 0
        if code != leader and p and tp is not None:
            lt = lead_pts.get(p)
            if lt is not None:
                gap = tp - lt
                j = 1
                while lead_pts.get(p + 3 * j) is not None and lead_pts[p + 3 * j] <= tp:
                    laps_down += 1; j += 1
            if prev is not None:
                at = rd._pts[prev][2].get(p)
                if at is not None:
                    interval = tp - at
        in_pit = any(l.get("pit_in_t") is not None and l["pit_in_t"] <= t and
                     (nxt.get("pit_out_t") is None or t < nxt["pit_out_t"])
                     for l, nxt in zip(laps_all, laps_all[1:] + [{}]))
        pits = sum(1 for l in laps_all if l.get("pit_in_t") is not None and l["pit_in_t"] <= t)

        c = rd.car.get(code, {})
        ct = c.get("t")
        tel = {k: _at(ct, c.get(k, []), t) for k in ("speed", "throttle", "brake", "gear", "rpm", "drs")} if ct is not None else {}
        x, y = _interp_xy(rd.pos.get(code), t)
        status = "OUT" if code in retired else ("FIN" if p >= finished_p else ("PIT" if in_pit else "RUN"))

        rows.append({
            "pos": pos_i, "code": code, "number": d["number"], "name": d["name"], "team": d["team"], "color": d["color"],
            "status": status, "gap": None if gap is None else round(gap, 3),
            "interval": None if interval is None else round(interval, 3), "laps_down": laps_down,
            "lap": min(rd.total_laps, (cur or {}).get("lap", len(done) + 1)) if status != "FIN" else rd.total_laps,
            "laps_done": len(done),
            "last": None if not last or last.get("time") is None else round(last["time"], 3),
            "last_deleted": bool(last and last.get("deleted")),
            "best": None if best is None else round(best, 3),
            "pb": bool(last and best is not None and last.get("time") == best),
            "fastest": bool(last and best_overall is not None and last.get("time") == best_overall),
            "compound": (cur or last or {}).get("compound"), "tyre_age": (cur or last or {}).get("age"),
            "stint": (cur or last or {}).get("stint"), "pits": pits, "in_pit": in_pit,
            "x": x, "y": y,
            "speed": _num(tel.get("speed")), "throttle": _num(tel.get("throttle")), "brake": _num(tel.get("brake")),
            "gear": _num(tel.get("gear")), "rpm": _num(tel.get("rpm")),
            "drs": bool(tel.get("drs") is not None and _num(tel.get("drs")) is not None and _num(tel["drs"]) >= 10),
            **per[code],
        })
        prev = code

    st = rd.status_at(t)
    lead_lap = rows[0]["lap"] if rows else 0
    return {
        "t": round(t, 2), "start": rd.start, "end": rd.end, "lap": lead_lap, "total_laps": rd.total_laps,
        "status": {"code": st["code"], "label": STATUS_LABEL.get(st["code"], st["code"]), "since": st["t"], "message": st.get("message", "")},
        "yellow_sectors": {str(k): v for k, v in yellow_sectors(rd, t).items()},
        "drivers": rows,
        "messages": [{"t": round(m["t"], 1), "lap": m.get("lap"), "kind": m["kind"], "category": m.get("category"),
                      "flag": m.get("flag"), "message": m.get("message"), "codes": m.get("codes", [])} for m in msgs[-40:]][::-1],
        "simulated": rd.simulated, "live": rd.live,
    }


def _num(v):
    try:
        f = float(v)
        return None if not np.isfinite(f) else round(f, 1)
    except (TypeError, ValueError):
        return None


def lap_history(rd: RaceData, t: float) -> list[dict]:
    """Completed laps up to t, flattened, with a 'neutral' flag for SC/VSC laps."""
    neutral = rd.neutral_intervals()
    out = []
    for code in rd.codes:
        for l in lap_rows_done(rd, code, t):
            if l.get("time") is None:
                continue
            nt = l.get("neutral")
            if nt is None:
                nt = any(a < l["end"] and b > (l.get("start") or l["end"] - l["time"]) for a, b, _ in neutral)
            out.append({"code": code, "lap": l["lap"], "time": l["time"], "compound": l.get("compound"),
                        "age": l.get("age"), "stint": l.get("stint"), "neutral": bool(nt),
                        "pit_in": l.get("pit_in_t") is not None, "pit_out": l.get("pit_out_t") is not None,
                        "end": l["end"]})
    return out


def car_window(rd: RaceData, code: str, t: float, seconds: float = 30.0) -> dict:
    c = rd.car.get(code)
    if not c or not len(c.get("t", [])):
        return {}
    tt = c["t"]
    a, b = np.searchsorted(tt, t - seconds), np.searchsorted(tt, t, side="right")
    sl = slice(a, b)
    step = max(1, (b - a) // 160)
    pick = lambda k: [None if not np.isfinite(v) else round(float(v), 1) for v in np.asarray(c[k][sl], dtype=float)[::step]] if k in c else []
    return {"t": [round(float(v) - t, 2) for v in tt[sl][::step]], "speed": pick("speed"), "throttle": pick("throttle"),
            "brake": pick("brake"), "gear": pick("gear"), "rpm": pick("rpm")}
