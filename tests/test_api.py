import math

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_frontend_served():
    r = client.get("/")
    assert r.status_code == 200 and "Pitwall" in r.text
    for path in ("/js/main.js", "/js/track3d.js", "/js/intro.js", "/css/style.css",
                 "/vendor/plotly.min.js", "/vendor/three.module.min.js", "/vendor/OrbitControls.js"):
        assert client.get(path).status_code == 200, path


def test_demo_drivers():
    r = client.get("/api/drivers", params={"year": 0})
    assert r.status_code == 200
    ds = r.json()["drivers"]
    assert [d["position"] for d in ds] == list(range(1, len(ds) + 1))
    assert all(d["best_str"].startswith("1:0") for d in ds)


def test_demo_compare_payload():
    r = client.get("/api/compare", params={"year": 0, "drivers": "LEC,NOR,HAM"})
    assert r.status_code == 200, r.text
    d = r.json()
    # demo lap times: NOR 62.542 < HAM 62.873 < LEC 62.936 → NOR becomes the reference
    assert d["reference"] == "NOR"
    assert [x["code"] for x in d["drivers"]] == ["NOR", "HAM", "LEC"]
    assert d["selection"] == [{"code": c, "lap": "fastest"} for c in ("NOR", "HAM", "LEC")]
    assert set(d["delta"]) == {"HAM", "LEC"}
    assert d["dominance"]["share"] and len(d["dominance"]["sectors"]) == 25
    assert d["corners"] and d["braking"]
    for x in d["drivers"]:
        t = x["trace"]
        n = len(t["Distance"])
        assert all(len(t[k]) == n for k in ("Speed", "Throttle", "Brake", "nGear", "X", "Y"))
        assert all(v is None or math.isfinite(v) for v in t["Speed"])
    # the gap on the cards agrees with the end of the delta trace
    lec = next(x for x in d["drivers"] if x["code"] == "LEC")
    assert abs(lec["gap"] - d["delta"]["LEC"]["delta"][-1]) < 0.02


def test_single_driver_has_no_dominance():
    d = client.get("/api/compare", params={"year": 0, "drivers": "ALO"}).json()
    assert d["dominance"] is None and d["delta"] == {}


def test_validation_errors():
    assert client.get("/api/compare", params={"year": 0, "drivers": "ALO,ALO"}).status_code == 422
    assert client.get("/api/compare", params={"year": 0, "drivers": "ALO,XXX"}).status_code == 422
    assert client.get("/api/compare", params={"year": 0, "drivers": "ALO,NOR,LEC,HAM,VER"}).status_code == 422
    assert client.get("/api/compare", params={"year": 0, "drivers": "ALO", "laps": "abc"}).status_code == 422
    assert client.get("/api/schedule", params={"year": 1999}).status_code == 422


def test_compare_has_g_forces_and_corners():
    d = client.get("/api/compare", params={"year": 0, "drivers": "NOR,LEC"}).json()
    t = d["drivers"][0]["trace"]
    assert {"Time", "Z", "LongG", "LatG"} <= set(t)
    assert d["corner_stats"] and set(d["corner_stats"][0]["drivers"]) == {"NOR", "LEC"}
    assert len(d["drivers"][0]["lap"]["sectors"]) == 3
    assert abs(sum(d["drivers"][0]["lap"]["sectors"]) - d["drivers"][0]["lap"]["time"]) < 0.01


def test_overview_demo():
    r = client.get("/api/overview", params={"year": 0})
    assert r.status_code == 200
    o = r.json()
    assert o["is_race"] and o["weather"]["air"]
    assert len(o["drivers"]) == 8
    d = o["drivers"][0]
    assert d["number"] and len(d["sectors"]) == 3 and d["traps"]["ST"] > 300 and len(d["laps"]) == 24


def test_reference_is_always_the_fastest_lap():
    from itertools import permutations
    times = {d["code"]: d["best"] for d in client.get("/api/drivers", params={"year": 0}).json()["drivers"]}
    for pick in permutations(["LEC", "VER", "PIA", "SAI"]):
        d = client.get("/api/compare", params={"year": 0, "drivers": ",".join(pick)}).json()
        codes = [x["code"] for x in d["drivers"]]
        assert codes == sorted(pick, key=times.get), pick
        assert d["reference"] == codes[0] and [s["code"] for s in d["selection"]] == codes
        for x in d["drivers"]:
            assert x["lap"]["time"] == times[x["code"]]                       # same number as the chips
            assert x["gap"] is None if x["code"] == codes[0] else x["gap"] > 0
            assert round(x["lap"]["time"], 3) == x["lap"]["time"]               # millisecond precision


def test_names_and_times_never_shuffle():
    from backend import demo
    ov = client.get("/api/overview", params={"year": 0}).json()
    for d in ov["drivers"]:
        name, team, colour, number, *_ = demo.DEMO_DRIVERS[d["code"]]
        assert (d["name"], d["team"], d["number"]) == (name, team, number)
        assert d["best"] == demo.demo_lap_time(d["code"])
        assert d["ideal"] == round(sum(d["sectors"]), 3)
    c = client.get("/api/compare", params={"year": 0, "drivers": "SAI,ALO"}).json()
    for x in c["drivers"]:
        assert (x["name"], x["team"]) == demo.DEMO_DRIVERS[x["code"]][:2]
        assert round(sum(x["lap"]["sectors"]), 3) == x["lap"]["time"]


def test_demo_race_is_classified_by_total_time():
    ov = client.get("/api/overview", params={"year": 0}).json()
    assert ov["order"] == "classification"
    rows = ov["drivers"]
    assert [d["position"] for d in rows] == list(range(1, len(rows) + 1))
    totals = [sum(l["time"] for l in d["laps"]) for d in rows]
    assert totals == sorted(totals)
    assert rows[0]["points"] == 25 and rows[1]["points"] == 18
    for d, tot in zip(rows[1:], totals[1:]):
        assert abs(d["race_time"] - (tot - totals[0])) < 0.002               # gap to the winner


def test_lap_time_formatting_rounds_to_the_millisecond():
    from backend.data import _fmt_laptime
    assert _fmt_laptime(119.9996) == "2:00.000"
    assert _fmt_laptime(79.3274) == "1:19.327"
    assert _fmt_laptime(None) is None
