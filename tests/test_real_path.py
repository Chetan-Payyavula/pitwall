"""Exercise the FastF1 code path with a fake session (no network needed)."""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend import data
from backend.demo import demo_circuit, demo_lap, demo_lap_time
from backend.main import app


class FakeLap(pd.Series):
    @property
    def _constructor(self):
        return FakeLap

    def get_telemetry(self):
        tel = demo_lap(self["Driver"]).copy()
        tel["Time"] = pd.to_timedelta(tel["Time"], unit="s")
        return tel


class FakeLaps(pd.DataFrame):
    @property
    def _constructor(self):
        return FakeLaps

    def pick_drivers(self, code):
        return FakeLaps(self[self["Driver"] == code])

    def pick_fastest(self):
        if self.empty:
            return None
        return FakeLap(self.sort_values("LapTime").iloc[0])

    @property
    def iloc(self):  # rows selected by lap number must also be FakeLap
        outer = super().iloc

        class _I:
            def __getitem__(_, k):
                r = outer[k]
                return FakeLap(r) if isinstance(r, pd.Series) else r
        return _I()


class FakeCircuit:
    rotation = 90.0

    def __init__(self):
        *_, corners = demo_circuit()
        self.corners = pd.DataFrame([{"X": c["x"], "Y": c["y"], "Number": c["number"], "Letter": "",
                                      "Distance": c["distance"]} for c in corners])


class FakeSession:
    name = "Qualifying"
    event = pd.Series({"EventName": "Fake Grand Prix", "Country": "Italy", "Location": "Monza",
                       "EventDate": pd.Timestamp("2024-09-01")})
    weather_data = pd.DataFrame({"AirTemp": [25.0, 27.0], "TrackTemp": [40.0, 44.0], "Humidity": [50.0, 50.0],
                                 "WindSpeed": [1.0, 3.0], "Rainfall": [False, False]})

    def __init__(self):
        rows = []
        for code in ("ALO", "NOR"):
            for n, extra in ((1, 1.5), (2, 0.0)):
                lt = demo_lap_time(code) + extra
                rows.append({"Driver": code, "LapNumber": n, "Compound": "SOFT", "TyreLife": n, "Stint": 1,
                             "LapTime": pd.to_timedelta(lt, unit="s"),
                             "Sector1Time": pd.to_timedelta(lt * 0.3, unit="s"),
                             "Sector2Time": pd.to_timedelta(lt * 0.35, unit="s"),
                             "Sector3Time": pd.to_timedelta(lt * 0.35, unit="s"),
                             "SpeedST": 320.0 + n, "SpeedI1": float("nan"), "Position": float("nan"),
                             "PitInTime": pd.NaT})
        self.laps = FakeLaps(rows)
        self.results = pd.DataFrame([
            {"Abbreviation": "ALO", "FullName": "Fernando Alonso", "TeamName": "Aston Martin", "TeamColor": "229971", "Position": 2, "DriverNumber": "14"},
            {"Abbreviation": "NOR", "FullName": "Lando Norris", "TeamName": "McLaren", "TeamColor": "FF8000", "Position": 1, "DriverNumber": "4"},
        ])

    def get_circuit_info(self):
        return FakeCircuit()


@pytest.fixture
def fake(monkeypatch):
    s = FakeSession()
    monkeypatch.setattr(data, "load_session", lambda *a: s)
    return s


def test_drivers_real_path(fake):
    out = data.drivers(2024, "1", "Qualifying")
    assert [d["code"] for d in out["drivers"]] == ["NOR", "ALO"]  # sorted by best lap
    assert out["drivers"][0]["color"] == "#FF8000"
    assert out["drivers"][0]["laps"] == [1, 2]


def test_compare_real_path_rotates_and_picks_laps(fake):
    c = TestClient(app)
    r = c.get("/api/compare", params={"year": 2024, "event": "1", "session": "Qualifying",
                                      "drivers": "ALO,NOR", "laps": "1,fastest"})
    assert r.status_code == 200, r.text
    d = r.json()
    nor, alo = d["drivers"]                         # NOR's fastest lap beats ALO's lap 1 → NOR is the reference
    assert d["reference"] == "NOR" and d["selection"] == [{"code": "NOR", "lap": "fastest"}, {"code": "ALO", "lap": "1"}]
    assert alo["lap"]["lap"] == 1 and nor["lap"]["lap"] == 2
    assert alo["lap"]["time"] == round(demo_lap_time("ALO") + 1.5, 3) and alo["lap"]["timed"]
    assert alo["lap"]["compound"] == "SOFT"
    # 90° rotation: new X = -old Y
    raw = demo_lap("ALO")
    assert alo["trace"]["X"][10] == pytest.approx(-raw["Y"].iloc[10], abs=0.05)
    assert d["corners"][0]["label"] == "T1"
    assert c.get("/api/compare", params={"year": 2024, "event": "1", "session": "Q",
                                         "drivers": "ALO", "laps": "9"}).status_code == 422


def test_overview_real_path(fake):
    o = data.overview(2024, "1", "Qualifying")
    assert o["event"] == "Fake Grand Prix" and not o["is_race"]
    assert o["weather"]["air"] == 26.0 and o["weather"]["rain"] is False
    nor = o["drivers"][0]
    assert nor["code"] == "NOR" and nor["number"] == 4
    assert nor["traps"]["ST"] == 322.0 and nor["traps"]["I1"] is None
    assert nor["ideal"] == pytest.approx(sum(nor["sectors"]), abs=1e-3)
    assert [l["lap"] for l in nor["laps"]] == [1, 2] and nor["laps"][0]["pit"] is False


def test_compare_real_path_has_sectors(fake):
    c = TestClient(app)
    d = c.get("/api/compare", params={"year": 2024, "event": "1", "session": "Q", "drivers": "NOR,ALO"}).json()
    assert len(d["drivers"][0]["lap"]["sectors"]) == 3 and d["corner_stats"]


def test_official_classification_order_and_q_times(fake):
    # ALO classified P1 even though NOR has the quicker single lap: the board must follow the result
    fake.results["Position"] = [1.0, 2.0]
    fake.results["ClassifiedPosition"] = ["1", "2"]
    fake.results["Q1"] = [pd.to_timedelta(80.1234, unit="s"), pd.to_timedelta(80.5, unit="s")]
    fake.results["Q2"] = [pd.NaT, pd.NaT]
    fake.results["Q3"] = [pd.NaT, pd.NaT]
    out = data.drivers(2024, "1", "Qualifying")
    assert out["order"] == "classification"
    assert [d["code"] for d in out["drivers"]] == ["ALO", "NOR"]
    alo = out["drivers"][0]
    assert alo["name"] == "Fernando Alonso" and alo["team"] == "Aston Martin" and alo["number"] == 14
    assert alo["q"] == [80.123, None, None]
    assert alo["best"] == round(demo_lap_time("ALO"), 3) and alo["best_lap"] == 2
    ov = data.overview(2024, "1", "Qualifying")
    assert [d["code"] for d in ov["drivers"]] == ["ALO", "NOR"]


def test_race_classification_and_dnf(fake):
    fake.name = "Race"
    fake.results["Position"] = [2.0, 1.0]
    fake.results["ClassifiedPosition"] = ["R", "1"]
    fake.results["Status"] = ["Retired", "Finished"]
    fake.results["Time"] = [pd.NaT, pd.to_timedelta(4800.12345, unit="s")]
    fake.results["Points"] = [0.0, 25.0]
    fake.results["GridPosition"] = [1.0, 3.0]
    out = data.drivers(2024, "1", "Race")
    nor, alo = out["drivers"]
    assert (nor["code"], nor["race_time"], nor["points"], nor["grid"]) == ("NOR", 4800.123, 25.0, 3)
    assert (alo["code"], alo["classified"], alo["status"], alo["race_time"]) == ("ALO", "DNF", "Retired", None)


def test_deleted_laps_never_count_for_sectors(fake):
    df = pd.DataFrame(fake.laps)
    hit = (df["Driver"] == "NOR") & (df["LapNumber"] == 1)
    df["Deleted"] = hit
    df.loc[hit, "Sector1Time"] = pd.to_timedelta(1.0, unit="s")      # absurdly fast, but deleted
    fake.laps = type(fake.laps)(df)
    nor = next(d for d in data.overview(2024, "1", "Qualifying")["drivers"] if d["code"] == "NOR")
    assert nor["sectors"][0] > 10
    assert [l["deleted"] for l in nor["laps"]] == [True, False]


def test_lapped_finisher_shows_laps_down(fake):
    fake.name = "Race"
    df = pd.DataFrame(fake.laps)
    fake.laps = type(fake.laps)(df[~((df["Driver"] == "ALO") & (df["LapNumber"] == 2))])   # ALO one lap down
    fake.results["Position"] = [2.0, 1.0]
    fake.results["ClassifiedPosition"] = ["2", "1"]
    fake.results["Status"] = ["Lapped", "Finished"]
    fake.results["Time"] = [pd.to_timedelta(12.6, unit="s"), pd.to_timedelta(4800.0, unit="s")]
    nor, alo = data.drivers(2024, "1", "Race")["drivers"]
    assert nor["race_time"] == 4800.0 and alo["race_time"] is None and alo["status"] == "+1 Lap"
