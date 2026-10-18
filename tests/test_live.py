"""Live timing engine, strategy models and the data-source adapters."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend import live_demo
from backend.live_sources import OpenF1Feed, RateLimiter, race_from_fastf1, race_from_openf1
from backend.livecore import derive_status, lap_history, message_kind, snapshot
from backend.main import app
from backend.strategy import (DriverModel, battle, ers_series, estimate_pit_loss, fit_degradation, live_strategy,
                              pit_window, rejoin)

RD = live_demo.build()
lead = lambda t: snapshot(RD, t)["drivers"][0]["code"]
L = lambda code, lap, f=0.5: RD.laps[code][lap - 1]["start"] + f * RD.laps[code][lap - 1]["time"]


# ------------------------------------------------------------------ snapshot
@pytest.mark.parametrize("t", [30, 400, 700, 1100, 1250, 1600, 2000])
def test_snapshot_order_and_gaps_are_consistent(t):
    s = snapshot(RD, t)
    rows = s["drivers"]
    assert [r["pos"] for r in rows] == list(range(1, len(rows) + 1))
    running = [r for r in rows if r["status"] != "OUT" and r["gap"] is not None]
    gaps = [r["gap"] for r in running]
    assert gaps == sorted(gaps)                                   # order agrees with gaps
    for a, b in zip(rows, rows[1:]):
        if a["gap"] is not None and b["gap"] is not None and b["interval"] is not None and a["status"] != "OUT" and b["status"] != "OUT":
            # interval is taken at b's own last timing line, the gap difference at a's later one
            assert b["interval"] >= 0 and b["interval"] == pytest.approx(b["gap"] - a["gap"], abs=0.6)


def test_status_and_flags_follow_the_race():
    assert snapshot(RD, L(lead(400), 9, 0.5))["status"]["label"] == "VSC"
    sc = snapshot(RD, L(lead(1000), 17, 0.5))
    assert sc["status"]["label"] == "SAFETY CAR"
    assert snapshot(RD, L(lead(400), 6, 0.45))["yellow_sectors"] == {"2": "YELLOW"}
    assert snapshot(RD, L(lead(400), 7, 0.1))["yellow_sectors"] == {}
    end = snapshot(RD, RD.end)
    assert end["messages"][0]["kind"] == "chequered"
    sai = next(r for r in end["drivers"] if r["code"] == "SAI")
    assert sai["status"] == "OUT" and sai["pos"] == len(end["drivers"])
    assert all(r["status"] == "FIN" for r in end["drivers"] if r["code"] != "SAI")
    by = {r["code"]: r for r in end["drivers"]}
    assert by["LEC"]["track_limits"] >= 1 and by["LEC"]["deleted"] >= 1
    assert by["HAM"]["penalties"] and by["HAM"]["investigations"] >= 1


def test_safety_car_compresses_the_field():
    t = RD.laps[lead(1000)][17]["end"] + 1         # just after the SC lap-18 line
    s = snapshot(RD, t)
    ivs = [r["interval"] for r in s["drivers"][1:] if r["interval"] is not None and r["status"] == "RUN"]
    assert ivs and max(ivs) < 1.6


def test_pit_status_and_tyre_change():
    rus = RD.laps["RUS"]
    pit_lap = next(l for l in rus if l["pit_in_t"])
    s = snapshot(RD, pit_lap["pit_in_t"] + 1)
    r = next(x for x in s["drivers"] if x["code"] == "RUS")
    assert r["in_pit"] and r["status"] == "PIT"
    after = next(x for x in snapshot(RD, pit_lap["end"] + 40)["drivers"] if x["code"] == "RUS")
    assert after["pits"] == 1 and after["compound"] == "HARD" and after["tyre_age"] <= 2


def test_message_classification():
    from backend.livecore import message_codes
    assert message_codes({"message": "CAR 22 (TSU) STOPPED (PIT) ENTRY"}, {"22": "TSU"}) == ["TSU"]
    assert message_kind({"message": "CHEQUERED FLAG", "flag": "CHEQUERED"}) == "chequered"
    assert message_kind({"message": "VIRTUAL SAFETY CAR DEPLOYED"}) == "vsc"
    assert message_kind({"message": "SAFETY CAR IN THIS LAP"}) == "sc"
    assert message_kind({"message": "CAR 1 (VER) TIME 1:31.2 DELETED - TRACK LIMITS AT TURN 4"}) == "track-limits"
    st = derive_status([{"t": 10, "message": "SAFETY CAR DEPLOYED"}, {"t": 50, "message": "TRACK CLEAR", "flag": "CLEAR", "scope": "Track"},
                        {"t": 90, "message": "RED FLAG", "flag": "RED"}], 0)
    assert [s["code"] for s in st] == ["1", "4", "1", "5"]


# ------------------------------------------------------------------ strategy
def test_degradation_recovers_the_simulated_truth():
    deg = fit_degradation(lap_history(RD, RD.end))["compounds"]
    truth = RD.meta["deg_truth"]
    for c in ("SOFT", "MEDIUM", "HARD"):
        assert deg[c]["deg"] == pytest.approx(truth[c], abs=0.02), c
    assert deg["SOFT"]["deg"] > deg["MEDIUM"]["deg"] > deg["HARD"]["deg"]
    assert deg["SOFT"]["pace"] < 0 < deg["HARD"]["pace"]


def test_pit_loss_recovers_the_simulated_truth():
    pl = estimate_pit_loss(lap_history(RD, RD.end))
    assert pl["samples"] >= 2
    assert pl["green"] == pytest.approx(RD.meta["pit_loss_truth"]["green"], abs=1.5)
    assert pl["sc"] < pl["vsc"] < pl["green"]
    early = estimate_pit_loss(lap_history(RD, 200))
    assert early["samples"] == 0 and early["green"] == 22.0


def test_rejoin_prediction():
    rows = snapshot(RD, 900)["drivers"]
    me = rows[0]
    r0 = rejoin(rows, me["code"], 0.0)
    assert r0["pos"] == 1 and r0["places_lost"] == 0
    r = rejoin(rows, me["code"], 22.0)
    expected = 1 + sum(1 for x in rows[1:] if x["status"] in ("RUN", "PIT") and x["gap"] is not None and x["gap"] < 22.0)
    assert r["pos"] == expected
    if r["ahead"]:
        assert r["gap_ahead"] > 0
    assert rejoin(rows, me["code"], 11.0)["pos"] <= r["pos"]


def test_undercut_logic():
    hist = lap_history(RD, 900)
    deg = fit_degradation(hist)
    m = DriverModel("NOR", hist, deg)
    a = {"code": "A", "compound": "MEDIUM", "tyre_age": 25}
    b = {"code": "B", "compound": "MEDIUM", "tyre_age": 25}
    close = battle(a, b, 0.6, m, m, 20, "HARD", "HARD")
    far = battle(a, b, 6.0, m, m, 20, "HARD", "HARD")
    assert close["undercut"][0]["gap_after"] < far["undercut"][0]["gap_after"]
    # with identical cars and worn tyres the undercut gains every lap the leader stays out
    g = [u["gap_after"] for u in close["undercut"]]
    assert all(x > y for x, y in zip(g, g[1:]))
    assert close["verdict"].startswith("UNDERCUT")
    assert far["verdict"].startswith(("HOLD", "UNDERCUT if"))


def test_pit_window():
    hist = lap_history(RD, 700)
    m = DriverModel("NOR", hist, fit_degradation(hist))
    pw = pit_window(m, "MEDIUM", 11, 10, 30, 21.0, {"MEDIUM"})
    assert pw["two_compound_rule"] and pw["no_stop"] is None
    assert "MEDIUM" not in pw["curves"] and pw["best"]["total"] == 0
    assert 10 <= pw["best"]["lap"] < 30
    cheap = pit_window(m, "MEDIUM", 11, 10, 30, 10.0, {"MEDIUM"})
    assert cheap["best"]["lap"] <= pw["best"]["lap"]


def test_ers_estimate_is_bounded_and_charges_under_sc():
    c = RD.car["NOR"]
    starts = [l["start"] for l in RD.laps["NOR"]]
    soc, mode = ers_series(c["t"], c["speed"], c["throttle"], c["brake"], starts)
    assert soc.min() >= 0 and soc.max() <= 1
    assert (mode == 1).any() and (mode == -1).any()
    i_sc = np.searchsorted(c["t"], RD.laps["NOR"][17]["end"])
    i_green = np.searchsorted(c["t"], RD.laps["NOR"][12]["end"])
    assert soc[i_sc] > soc[i_green]


def test_live_strategy_payload():
    s = live_strategy(RD, 1150, focus=["NOR"])
    assert s["mode"] == "sc"
    assert s["cheap_stop_saving"]["sc"] > 5
    nor = s["drivers"]["NOR"]
    assert nor["pit_window"]["best"] and nor["ers"]["estimate"] and 0 <= nor["ers"]["soc"] <= 100
    assert set(nor["rejoin"]) == {"green", "vsc", "sc"}
    assert s["battles"] and all(b["tone"] in ("go", "maybe", "info", "hold") for b in s["battles"])


# ------------------------------------------------------------------ API
def test_live_api():
    c = TestClient(app)
    m = c.get("/api/live/meta").json()
    assert m["total_laps"] == 30 and len(m["drivers"]) == 8 and m["track"]["x"]
    s = c.get("/api/live/snapshot", params={"t": 900, "focus": "NOR,LEC"}).json()
    assert len(s["drivers"]) == 8 and set(s["focus"]) == {"NOR", "LEC"}
    assert s["focus"]["NOR"]["window"]["speed"] and s["focus"]["NOR"]["ers"]["estimate"]
    g = c.get("/api/live/strategy", params={"t": 900, "focus": "NOR"})
    assert g.status_code == 200 and "pit_window" in g.json()["drivers"]["NOR"]
    r = c.get("/api/live/snapshot", params={"source": "openf1"})
    assert r.status_code == 422 and "OPENF1_USERNAME" in r.json()["detail"]
    assert c.get("/api/live/snapshot", params={"source": "nope"}).status_code == 422


# ------------------------------------------------------------------ OpenF1 adapter
T0 = datetime(2026, 5, 3, 13, 0, tzinfo=timezone.utc)
iso = lambda s: (T0 + timedelta(seconds=float(s))).isoformat().replace("+00:00", "Z")


def openf1_payload_from(rd, upto=None):
    """Turn the demo race into OpenF1-shaped records (as the API would return them)."""
    p = {"sessions": [{"session_key": 9999, "date_start": iso(0), "session_name": "Race", "circuit_short_name": "Demo"}],
         "drivers": [{"driver_number": int(d["number"]), "name_acronym": d["code"], "full_name": d["name"], "team_name": d["team"],
                      "team_colour": d["color"].lstrip("#")} for d in rd.drivers],
         "starting_grid": [{"driver_number": int(d["number"]), "position": i + 1} for i, d in enumerate(rd.drivers)],
         "laps": [], "stints": [], "race_control": [], "car_data": [], "location": []}
    for d in rd.drivers:
        rows = rd.laps[d["code"]]
        stint_rows = {}
        for i, l in enumerate(rows):
            if upto and (l["end"] or l["start"]) > upto:
                continue
            prev_pit = i > 0 and rows[i - 1]["pit_in_t"] is not None
            p["laps"].append({"driver_number": int(d["number"]), "lap_number": l["lap"], "date_start": iso(l["start"]),
                              "lap_duration": l["time"], "duration_sector_1": l["s1"] - l["start"] if l["s1"] else None,
                              "duration_sector_2": l["s2"] - l["s1"] if l["s2"] else None,
                              "is_pit_out_lap": prev_pit})
            st = stint_rows.setdefault(l["stint"], {"driver_number": int(d["number"]), "stint_number": l["stint"], "compound": l["compound"],
                                                     "lap_start": l["lap"], "lap_end": l["lap"], "tyre_age_at_start": l["age"] - 1})
            st["lap_end"] = l["lap"]
        p["stints"] += list(stint_rows.values())
        c = rd.car[d["code"]]
        for k in range(0, len(c["t"]), 8):
            if upto and c["t"][k] > upto:
                break
            p["car_data"].append({"driver_number": int(d["number"]), "date": iso(c["t"][k]), "speed": float(c["speed"][k]),
                                  "throttle": float(c["throttle"][k]), "brake": float(c["brake"][k]), "n_gear": int(c["gear"][k]),
                                  "rpm": float(c["rpm"][k]), "drs": int(c["drs"][k])})
            p["location"].append({"driver_number": int(d["number"]), "date": iso(c["t"][k]), "x": float(rd.pos[d["code"]]["x"][k]),
                                  "y": float(rd.pos[d["code"]]["y"][k]), "z": 0})
    for m in rd.messages:
        if upto and m["t"] > upto:
            continue
        p["race_control"].append({"date": iso(m["t"]), "category": m["category"], "flag": m["flag"], "scope": m["scope"],
                                  "sector": m["sector"], "driver_number": m["number"], "message": m["message"], "lap_number": m["lap"]})
    return p


def test_openf1_conversion_matches_the_source():
    rd2 = race_from_openf1(openf1_payload_from(RD), total_laps=30)
    for t in (600, 1100, 1500, RD.end - 40):
        a, b = snapshot(RD, t), snapshot(rd2, t)
        assert [r["code"] for r in a["drivers"]] == [r["code"] for r in b["drivers"]], t
        for x, y in zip(a["drivers"], b["drivers"]):
            if x["gap"] is not None:
                assert y["gap"] == pytest.approx(x["gap"], abs=0.01)
            assert (x["compound"], x["pits"]) == (y["compound"], y["pits"])
        assert a["status"]["code"] == b["status"]["code"], t        # derived from race-control text
    s = live_strategy(rd2, 1500, focus=["NOR"])
    assert s["pit_loss"]["samples"] >= 2


def test_openf1_feed_polls_with_auth_and_reports_errors():
    payload = openf1_payload_from(RD, upto=700)
    calls = []

    def fake_http(url, form=None, headers=None):
        calls.append((url, headers))
        if url.endswith("/token"):
            return {"access_token": "abc", "expires_in": 3600}
        ep = url.split("/v1/")[1].split("?")[0]
        return payload.get(ep, [])

    f = OpenF1Feed("9999", "user", "pw", http=fake_http)
    f.poll_once()
    rd = f.race()
    assert rd.live and len(rd.drivers) == 8 and snapshot(rd, 650)["drivers"][0]["gap"] is None
    assert any(h and h.get("Authorization") == "Bearer abc" for _, h in calls if "/v1/" in _)
    # after the first poll every poll costs 3 requests (car_data, location + one slow list)
    n = f.requests
    for _ in range(15):
        f.poll_once()
    assert (f.requests - n) / 15 == 3
    assert 60 / f.POLL_S * 3 <= 50
    bad = OpenF1Feed("9999", http=lambda *a, **k: (_ for _ in ()).throw(OSError("network down")))
    with pytest.raises(Exception):
        bad.race()


# ------------------------------------------------------------------ FastF1 adapter
class _FakeF1Session:
    """Just enough of a FastF1 race Session for race_from_fastf1."""

    def __init__(self, rd):
        self.name = "Race"
        self.event = {"EventName": "Fake GP"}
        self.total_laps = rd.total_laps
        self.t0_date = pd.Timestamp("2026-05-03 12:00:00")
        td = lambda s: pd.NaT if s is None else pd.to_timedelta(s, unit="s")
        rows = []
        for d in rd.drivers:
            for l in rd.laps[d["code"]]:
                rows.append({"Driver": d["code"], "LapNumber": l["lap"], "LapStartTime": td(l["start"]), "Time": td(l["end"]),
                             "LapTime": td(l["time"]), "Sector1SessionTime": td(l["s1"]), "Sector2SessionTime": td(l["s2"]),
                             "Compound": l["compound"], "TyreLife": l["age"], "Stint": l["stint"], "PitInTime": td(l["pit_in_t"]),
                             "PitOutTime": td(l["pit_out_t"]), "TrackStatus": "4" if l["neutral"] else "1", "Deleted": bool(l.get("deleted"))})
        self.laps = pd.DataFrame(rows)
        self.results = pd.DataFrame([{"Abbreviation": d["code"], "DriverNumber": d["number"], "FullName": d["name"], "TeamName": d["team"],
                                      "TeamColor": d["color"].lstrip("#"), "GridPosition": i + 1} for i, d in enumerate(rd.drivers)])
        self.car_data = {d["number"]: pd.DataFrame({"SessionTime": pd.to_timedelta(rd.car[d["code"]]["t"], unit="s"),
                                                    "Speed": rd.car[d["code"]]["speed"], "Throttle": rd.car[d["code"]]["throttle"],
                                                    "Brake": rd.car[d["code"]]["brake"] > 0, "nGear": rd.car[d["code"]]["gear"],
                                                    "RPM": rd.car[d["code"]]["rpm"], "DRS": rd.car[d["code"]]["drs"]}) for d in rd.drivers}
        self.pos_data = {d["number"]: pd.DataFrame({"SessionTime": pd.to_timedelta(rd.pos[d["code"]]["t"], unit="s"),
                                                    "X": rd.pos[d["code"]]["x"], "Y": rd.pos[d["code"]]["y"]}) for d in rd.drivers}
        self.race_control_messages = pd.DataFrame([{"Time": self.t0_date + pd.to_timedelta(m["t"], unit="s"), "Category": m["category"],
                                                    "Message": m["message"], "Flag": m["flag"], "Scope": m["scope"], "Sector": m["sector"],
                                                    "RacingNumber": m["number"], "Lap": m["lap"]} for m in rd.messages])
        self.track_status = pd.DataFrame([{"Time": pd.to_timedelta(s["t"], unit="s"), "Status": s["code"], "Message": s["message"]} for s in rd.status])

    def get_circuit_info(self):
        raise RuntimeError("no circuit info in the fake")


def test_fastf1_conversion_matches_the_source():
    rd2 = race_from_fastf1(_FakeF1Session(RD))
    assert rd2.total_laps == 30 and rd2.meta["race_start"] == pytest.approx(0.0)
    for t in (500, 1150, 1700):
        a, b = snapshot(RD, t), snapshot(rd2, t)
        assert [r["code"] for r in a["drivers"]] == [r["code"] for r in b["drivers"]]
        assert a["status"]["code"] == b["status"]["code"]
        assert [(r["compound"], r["tyre_age"], r["pits"], r["in_pit"]) for r in a["drivers"]] == \
               [(r["compound"], r["tyre_age"], r["pits"], r["in_pit"]) for r in b["drivers"]]
    s = snapshot(rd2, RD.end)
    assert next(r for r in s["drivers"] if r["code"] == "LEC")["track_limits"] >= 1


def test_rate_limiter_keeps_under_openf1_limits():
    clock = [0.0]
    lim = RateLimiter(per_sec=5, per_min=50, clock=lambda: clock[0], sleep=lambda d: clock.__setitem__(0, clock[0] + d))
    stamps = []
    for _ in range(200):
        lim.wait()
        stamps.append(clock[0])
    for i, t in enumerate(stamps):
        assert sum(1 for u in stamps[i:] if u - t < 1.0) <= 5
        assert sum(1 for u in stamps[i:] if u - t < 60.0) <= 50
