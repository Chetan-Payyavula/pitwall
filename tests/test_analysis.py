import numpy as np
import pandas as pd
import pytest

from backend.analysis import braking_zones, lap_delta, match_braking_zones, track_dominance
from backend.demo import DEMO_DRIVERS, demo_circuit, demo_lap, demo_lap_time


def straight_lap(speed_kmh, length=1000, step=5.0):
    """Constant-speed lap; time follows exactly from distance / speed."""
    d = np.arange(0, length + step, step)
    v = np.broadcast_to(np.asarray(speed_kmh, float), d.shape) / 3.6
    t = np.concatenate([[0], np.cumsum(step / ((v[:-1] + v[1:]) / 2))])
    return pd.DataFrame({"Distance": d, "Time": t, "Speed": v * 3.6, "Brake": False,
                         "X": d, "Y": np.zeros_like(d)})


def test_delta_constant_speeds():
    ref, slow = straight_lap(360), straight_lap(300)  # 100 m/s vs 83.3 m/s
    out = lap_delta(ref, slow)
    # the delta runs to the end of the lap: 1000/83.333 - 1000/100
    assert out["delta"][-1] == pytest.approx(1000 / (300 / 3.6) - 1000 / 100, abs=1e-3)
    assert all(np.diff(out["delta"]) >= 0)  # slower car only loses time


def test_delta_of_identical_laps_is_zero():
    lap = demo_lap("ALO")
    out = lap_delta(lap, lap)
    assert max(abs(v) for v in out["delta"]) == 0


def test_delta_matches_lap_time_gap():
    a, b = demo_lap("ALO"), demo_lap("NOR")
    end = lap_delta(a, b)["delta"][-1]
    assert end == pytest.approx(demo_lap_time("NOR") - demo_lap_time("ALO"), abs=0.002)


def test_delta_ends_at_lap_gap_when_lap_lengths_differ():
    # same track, but the slower lap's integrated distance came out 2% long (different line / drift)
    ref = straight_lap(360, length=1000)
    slow = straight_lap(300, length=1000)
    slow["Distance"] = slow["Distance"] * 1.02
    end = lap_delta(ref, slow)["delta"][-1]
    assert end == pytest.approx(slow["Time"].iloc[-1] - ref["Time"].iloc[-1], abs=1e-3)


def test_braking_zone_detection():
    d = np.arange(0, 1000, 5.0)
    speed = np.full(d.shape, 300.0)
    brake = np.zeros(d.shape, bool)
    zone = (d >= 400) & (d < 500)
    speed[zone] = np.linspace(300, 100, zone.sum())
    speed[d >= 500] = np.minimum(300, 100 + (d[d >= 500] - 500) * 1.5)
    brake[zone] = True
    brake[np.flatnonzero(zone)[5]] = False          # flicker in the signal → must merge
    brake[(d >= 800) & (d < 810)] = True            # blip with no speed loss → must drop
    v = speed / 3.6
    t = np.concatenate([[0], np.cumsum(5 / ((v[:-1] + v[1:]) / 2))])
    lap = pd.DataFrame({"Distance": d, "Time": t, "Speed": speed, "Brake": brake})

    zones = braking_zones(lap)
    assert len(zones) == 1
    z = zones[0]
    assert z.start == 400 and z.end == 495
    assert z.entry_speed == pytest.approx(300)
    assert z.min_speed == pytest.approx(100, abs=3)


def test_demo_braking_zones_line_up_across_drivers():
    zones = {c: braking_zones(demo_lap(c)) for c in ("ALO", "LEC")}
    *_, corners = demo_circuit()
    rows = match_braking_zones(zones, corners)
    assert len(rows) == len(zones["ALO"]) >= 4
    assert all("LEC" in r["drivers"] for r in rows)
    assert all(r["corner"] for r in rows)


def test_dominance_sector_times_sum_to_lap():
    laps = {c: demo_lap(c) for c in DEMO_DRIVERS}
    dom = track_dominance(laps, n_sectors=20)
    assert len(dom["sectors"]) == 20
    assert sum(dom["share"].values()) == 20
    for code, lap in laps.items():
        total = sum(s["times"][code] for s in dom["sectors"])
        assert total == pytest.approx(lap["Time"].iloc[-1], abs=0.05)
    for s in dom["sectors"]:
        assert s["times"][s["winner"]] == min(s["times"].values())
        assert len(s["x"]) == len(s["y"]) > 2


def test_dominance_faster_everywhere_wins_everything():
    dom = track_dominance({"A": straight_lap(320), "B": straight_lap(310)}, n_sectors=10)
    assert dom["share"] == {"A": 10, "B": 0}


def test_g_forces_are_physical():
    from backend.analysis import add_g_forces
    lap = add_g_forces(demo_lap("NOR"))
    assert {"LongG", "LatG"} <= set(lap.columns)
    assert -6 <= lap["LongG"].min() < -2.5          # heavy braking
    assert 0.5 < lap["LongG"].max() < 3             # traction-limited acceleration
    assert 1.8 < lap["LatG"].abs().quantile(0.97) < 4.5
    # straights → almost no lateral load
    straight = lap[(lap["Distance"] > 100) & (lap["Distance"] < 700)]
    assert straight["LatG"].abs().max() < 0.3


def test_g_forces_survive_unit_scaling():
    """FastF1 positions are in decimetres; result must not depend on units."""
    from backend.analysis import add_g_forces
    a = demo_lap("LEC")
    b = a.copy()
    b["X"], b["Y"] = b["X"] * 10, b["Y"] * 10
    assert np.allclose(add_g_forces(a)["LatG"], add_g_forces(b)["LatG"], atol=1e-6)


def test_corner_analysis():
    from backend.analysis import corner_analysis
    *_, corners = demo_circuit()
    rows = corner_analysis({"NOR": demo_lap("NOR"), "LEC": demo_lap("LEC")}, corners)
    assert len(rows) == len(corners)
    for r in rows:
        nor, lec = r["drivers"]["NOR"], r["drivers"]["LEC"]
        assert nor["delta"] == 0
        assert lec["delta"] == pytest.approx(lec["time"] - nor["time"], abs=1e-3)
        assert nor["min"] <= nor["entry"] and nor["min"] <= nor["exit"] + 0.5
        assert len(nor["speed"]) == len(r["axis"]) and len(r["x"]) == 40
    # corner deltas can't add up to more than the whole-lap gap explains (windows overlap a bit)
    assert abs(sum(r["drivers"]["LEC"]["delta"] for r in rows)) < 1.0


def test_demo_race_is_consistent():
    from backend.demo import demo_race
    race = demo_race(20)
    for n in range(20):
        assert sorted(race[c][n]["position"] for c in race) == list(range(1, len(race) + 1))
    for rows in race.values():
        assert sum(r["pit"] for r in rows) == 1
        assert rows[-1]["compound"] == "HARD"
