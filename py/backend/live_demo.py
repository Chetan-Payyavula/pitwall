"""A complete simulated race on the demo circuit, in the same RaceData shape as
real sources: lap/sector timing, 4 Hz car data, positions, pit stops, a VSC,
a safety car with field compression, yellow flags, track-limit deletions, an
investigation + penalty, a retirement and the chequered flag."""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from . import demo
from .livecore import RaceData

N_LAPS = 30
VSC_LAPS = (9, 10)          # laps run under VSC
SC_LAPS = (16, 17, 18)      # laps run behind the safety car
RETIRE = ("SAI", 21)        # driver, lap he stops on
PIT_LOSS = {"green": 21.0, "vsc": 12.6, "sc": 10.4}
DEG = {"SOFT": 0.085, "MEDIUM": 0.048, "HARD": 0.028}
PACE = {"SOFT": -0.45, "MEDIUM": 0.0, "HARD": 0.32}
FUEL = 0.05


def _plan():
    """(first compound, pit lap) per driver — some stop under the SC."""
    return {
        "NOR": ("MEDIUM", 17), "PIA": ("MEDIUM", 14), "RUS": ("SOFT", 9), "VER": ("MEDIUM", 16),
        "ALO": ("SOFT", 12), "HAM": ("MEDIUM", 17), "SAI": ("MEDIUM", 13), "LEC": ("SOFT", 10),
    }


@lru_cache(maxsize=1)
def build() -> RaceData:
    rng = np.random.default_rng(11)
    codes = sorted(demo.DEMO_DRIVERS, key=demo.demo_lap_time)           # grid = quali order
    base = {c: demo.demo_lap_time(c) + 2.0 for c in codes}
    plan = _plan()
    lap_len = float(demo.demo_lap(codes[0])["Distance"].iloc[-1])
    s1f, s2f = 0.31, 0.66                                                  # sector split fractions

    cum = {c: [0.4 * g] for g, c in enumerate(codes)}                     # staggered grid start
    rows = {c: [] for c in codes}
    comp = {c: plan[c][0] for c in codes}
    age = {c: 1 for c in codes}
    stint = {c: 1 for c in codes}
    alive = {c: True for c in codes}

    for L in range(1, N_LAPS + 1):
        order = sorted([c for c in codes if alive[c]], key=lambda c: cum[c][-1])
        sc, vsc = L in SC_LAPS, L in VSC_LAPS
        lead_end = None
        for rank, c in enumerate(order):
            if c == RETIRE[0] and L == RETIRE[1]:
                alive[c] = False
                # crawls 60% of the lap then stops — no lap end recorded
                st = cum[c][-1]
                rows[c].append({"lap": L, "start": st, "end": None, "s1": st + 0.31 * base[c] * 1.3, "s2": None, "time": None,
                                "compound": comp[c], "age": age[c], "stint": stint[c], "pit_in_t": None, "pit_out_t": None,
                                "neutral": False, "_frac_stop": 0.55})
                continue
            pace = base[c] + PACE[comp[c]] + DEG[comp[c]] * (age[c] - 1) - FUEL * L + rng.normal(0, 0.11)
            if L == 1:
                pace += 3.2 + rank * 0.25
            pit = plan[c][1] == L
            out_lap = L > 1 and plan[c][1] == L - 1
            mode = "sc" if sc else "vsc" if vsc else "green"
            if pit:
                pace += PIT_LOSS[mode] * 0.45
            if out_lap:
                prev_mode = "sc" if (L - 1) in SC_LAPS else "vsc" if (L - 1) in VSC_LAPS else "green"
                pace += PIT_LOSS[prev_mode] * 0.55
            if vsc:
                pace *= 1.36
            st = cum[c][-1]
            end = st + pace
            if sc:
                pace_sc = base[order[0]] * 1.55
                if lead_end is None:
                    lead_end = cum[order[0]][-1] + pace_sc
                    end = lead_end if rank == 0 else end
                if rank > 0:
                    end = max(lead_end + 0.9 * rank + (PIT_LOSS["sc"] * 0.45 if pit else 0), st + base[c] * 1.2)
                if rank == 0:
                    end = lead_end + (PIT_LOSS["sc"] * 0.45 if pit else 0)
            time = end - st
            rows[c].append({"lap": L, "start": st, "end": end, "s1": st + time * s1f, "s2": st + time * s2f, "time": round(time, 3),
                            "compound": comp[c], "age": age[c], "stint": stint[c],
                            "pit_in_t": end - 6.0 if pit else None, "pit_out_t": st + 8.0 if out_lap else None,
                            "neutral": sc or vsc})
            cum[c].append(end)
            if pit:
                comp[c] = "HARD" if comp[c] != "HARD" else "MEDIUM"
                age[c] = 1; stint[c] += 1
            else:
                age[c] += 1

    # ---- deleted lap (track limits) and penalty mark-ups
    t_of = lambda c, L: rows[c][L - 1]["end"]
    lec4 = rows["LEC"][3]; lec4["deleted"] = True

    # ---- car data and positions at 4 Hz, from each driver's demo lap scaled to the real lap time
    car, pos = {}, {}
    _, gx, gy, _, _, _ = demo.demo_circuit()
    for c in codes:
        tr = demo.demo_lap(c)
        tT, tD = tr["Time"].to_numpy(), tr["Distance"].to_numpy()
        aS, aTh, aB = tr["Speed"].to_numpy(), tr["Throttle"].to_numpy(), tr["Brake"].to_numpy()
        aG, aR = tr["nGear"].to_numpy(), tr["RPM"].to_numpy()
        gd = np.arange(len(gx)) * 5.0
        Ttot = float(tT[-1])
        ts, sp, th, br, gr, rp, drs, xs, ys = ([] for _ in range(9))
        for r in rows[c]:
            stop_frac = r.get("_frac_stop")
            lap_time = r["time"] or base[c] * 1.3
            n = int(lap_time * 4)
            for k in range(n):
                f = k / n
                if stop_frac is not None and f > stop_frac:
                    break
                tl = f * Ttot
                i = min(len(tT) - 2, int(np.searchsorted(tT, tl)))
                slow = lap_time / base[c] if r["neutral"] else 1.0
                ts.append(r["start"] + f * lap_time)
                sp.append(aS[i] / max(slow, 1.0))
                th.append(min(100.0, aTh[i] * (0.55 if r["neutral"] else 1.0)))
                br.append(100.0 if aB[i] and not r["neutral"] else 0.0)
                gr.append(aG[i]); rp.append(aR[i] * (0.8 if r["neutral"] else 1.0))
                dist = tD[i]
                drs.append(12 if (not r["neutral"] and r["lap"] > 2 and 120 < dist < 880) else 8)
                xs.append(dist); ys.append(0.0)
        car[c] = {"t": np.array(ts), "speed": np.array(sp), "throttle": np.array(th), "brake": np.array(br),
                  "gear": np.array(gr, dtype=float), "rpm": np.array(rp), "drs": np.array(drs, dtype=float)}
        dd = np.array(xs)
        pos[c] = {"t": np.array(ts), "x": np.interp(dd, gd, gx), "y": np.interp(dd, gd, gy)}

    # ---- race control
    L = lambda c, n, f=0.5: rows[c][n - 1]["start"] + f * (rows[c][n - 1]["time"] or 60)
    lead = codes[0]
    num = {c: demo.DEMO_DRIVERS[c][3] for c in codes}
    msgs = [
        (0.0, "Flag", "GREEN", "Track", None, None, "GREEN LIGHT - PIT EXIT OPEN"),
        (L(lead, 2, 0.1), "Drs", None, None, None, None, "DRS ENABLED"),
        (t_of("LEC", 4) + 2, "Other", None, None, None, num["LEC"], f"CAR {num['LEC']} (LEC) TIME {int(lec4['time'] // 60)}:{lec4['time'] % 60:06.3f} DELETED - TRACK LIMITS AT TURN 4 LAP 4 {_clock(lec4['end'])}"),
        (L(lead, 6, 0.30), "Flag", "YELLOW", "Sector", 2, None, "YELLOW IN TRACK SECTOR 2"),
        (L(lead, 6, 0.62), "Flag", "CLEAR", "Sector", 2, None, "CLEAR IN TRACK SECTOR 2"),
        (L(lead, 8, 0.97), "SafetyCar", None, "Track", None, None, "VIRTUAL SAFETY CAR DEPLOYED"),
        (L(lead, 10, 0.85), "SafetyCar", None, "Track", None, None, "VIRTUAL SAFETY CAR ENDING"),
        (L(lead, 11, 0.0), "Flag", "GREEN", "Track", None, None, "TRACK CLEAR"),
        (L("HAM", 12, 0.6), "Other", None, None, None, num["HAM"], f"CAR {num['HAM']} (HAM) OFF TRACK AT TURN 6 - NOTED"),
        (L("HAM", 13, 0.2), "Other", None, None, None, num["HAM"], f"TURN 6 INCIDENT INVOLVING CAR {num['HAM']} (HAM) UNDER INVESTIGATION - LEAVING THE TRACK AND GAINING AN ADVANTAGE"),
        (L("HAM", 14, 0.8), "Other", None, None, None, num["HAM"], f"FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR {num['HAM']} (HAM) - LEAVING THE TRACK AND GAINING AN ADVANTAGE"),
        (L(lead, 15, 0.70), "Flag", "DOUBLE YELLOW", "Sector", 3, None, "DOUBLE YELLOW IN TRACK SECTOR 3"),
        (L(lead, 15, 0.93), "SafetyCar", None, "Track", None, None, "SAFETY CAR DEPLOYED"),
        (L(lead, 16, 0.2), "Drs", None, None, None, None, "DRS DISABLED"),
        (L(lead, 18, 0.1), "SafetyCar", None, "Track", None, None, "SAFETY CAR IN THIS LAP"),
        (L(lead, 18, 0.99), "Flag", "GREEN", "Track", None, None, "TRACK CLEAR"),
        (L(lead, 20, 0.1), "Drs", None, None, None, None, "DRS ENABLED"),
        (L("RUS", 22, 0.4), "Other", None, None, None, num["RUS"], f"CAR {num['RUS']} (RUS) TIME 1:05.611 DELETED - TRACK LIMITS AT TURN 1 LAP 22"),
        (L("ALO", 25, 0.5), "Flag", "BLUE", "Driver", None, num["ALO"], f"WAVED BLUE FLAG FOR CAR {num['ALO']} (ALO) TIMED AT {_clock(L('ALO', 25, 0.5))}"),
        (cum[lead][-1], "Flag", "CHEQUERED", "Track", None, None, "CHEQUERED FLAG"),
    ]
    sai_stop = rows["SAI"][RETIRE[1] - 1]
    msgs.append((sai_stop["start"] + 0.56 * base["SAI"] * 1.3, "Flag", "DOUBLE YELLOW", "Sector", 2, None, "DOUBLE YELLOW IN TRACK SECTOR 2"))
    msgs.append((sai_stop["start"] + 0.56 * base["SAI"] * 1.3 + 4, "Other", None, None, None, num["SAI"], f"CAR {num['SAI']} (SAI) STOPPED ON TRACK AT TURN 4"))
    msgs.append((sai_stop["start"] + 0.56 * base["SAI"] * 1.3 + 70, "Flag", "CLEAR", "Sector", 2, None, "CLEAR IN TRACK SECTOR 2"))
    messages = [{"t": float(t), "category": cat, "flag": fl, "scope": sc, "sector": sec, "number": n, "message": m,
                 "lap": None} for t, cat, fl, sc, sec, n, m in msgs]
    lap_no = lambda t: next((r["lap"] for r in rows[lead] if r["end"] and r["end"] >= t), N_LAPS)
    for m in messages:
        m["lap"] = lap_no(m["t"])

    status = [{"t": 0.0, "code": "1", "message": ""},
              {"t": L(lead, 6, 0.30), "code": "2", "message": "Yellow"}, {"t": L(lead, 6, 0.62), "code": "1", "message": ""},
              {"t": L(lead, 8, 0.97), "code": "6", "message": "VSCDeployed"}, {"t": L(lead, 10, 0.85), "code": "7", "message": "VSCEnding"},
              {"t": L(lead, 11, 0.0), "code": "1", "message": ""},
              {"t": L(lead, 15, 0.70), "code": "2", "message": "Yellow"},
              {"t": L(lead, 15, 0.93), "code": "4", "message": "SCDeployed"}, {"t": L(lead, 18, 0.99), "code": "1", "message": ""},
              {"t": sai_stop["start"] + 0.56 * base["SAI"] * 1.3, "code": "2", "message": "Yellow"},
              {"t": sai_stop["start"] + 0.56 * base["SAI"] * 1.3 + 70, "code": "1", "message": ""}]

    for rs in rows.values():
        for r in rs:
            r.pop("_frac_stop", None)
    *_, corners = demo.demo_circuit()
    drivers = [{"code": c, "number": demo.DEMO_DRIVERS[c][3], "name": demo.DEMO_DRIVERS[c][0],
                "team": demo.DEMO_DRIVERS[c][1], "color": demo.DEMO_DRIVERS[c][2]} for c in codes]
    end = max(cum[c][-1] for c in codes) + 30
    return RaceData(name="Demo Grand Prix (simulated)", total_laps=N_LAPS, start=0.0, end=end, drivers=drivers,
                    laps=rows, car=car, pos=pos, messages=messages, status=status,
                    track={"x": np.round(gx[::2], 1).tolist(), "y": np.round(gy[::2], 1).tolist(), "corners": corners,
                           "length": lap_len},
                    simulated=True, meta={"pit_loss_truth": PIT_LOSS, "deg_truth": DEG})


def _clock(t: float) -> str:
    h, r = divmod(int(t) + 14 * 3600, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
