"""Compare sessions rebuilt from OpenF1 with the same sessions baked from FastF1.

    python -m tools.check_openf1 OF1_CACHE BAKED_DIR YEAR ROUND "SESSION NAME" SESSION_KEY [...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from backend import openf1_hist, static_data
from tools.openf1_fetch import Client


def main(cache, baked, year, rnd, name, sk):
    c = Client(Path(cache))
    sess = c.get("sessions", session_key=sk)[0]
    S = openf1_hist.OF1Session(c, sess, int(year), int(rnd))
    d = Path(baked) / str(year) / str(rnd) / static_data.slug(name)
    import os
    if os.environ.get("OFFICIAL"):
        S.use_official_laps(json.loads((d / "overview.json").read_text()))
    want_d = json.loads((d / "drivers.json").read_text())
    got_d = json.loads(json.dumps(static_data._clean(S.drivers())))
    print(f"== {year} R{rnd} {name} (OpenF1 {sk})")
    print("event", want_d["event"], "|", got_d["event"], "order", want_d["order"], got_d["order"])
    wd = {x["code"]: x for x in want_d["drivers"]}
    keys = ("position", "classified", "grid", "status", "points", "race_time", "q", "number", "best", "best_lap", "team", "color", "name")
    mism = {}
    for x in got_d["drivers"]:
        w = wd.get(x["code"])
        if not w:
            print("  extra driver", x["code"]); continue
        for k in keys:
            if w.get(k) != x.get(k):
                mism.setdefault(k, []).append(f"{x['code']}: {w.get(k)!r} vs {x.get(k)!r}")
        if w["laps"] != x["laps"]:
            mism.setdefault("laps", []).append(f"{x['code']}: {len(w['laps'])} vs {len(x['laps'])}")
    print("  order same:", [x["code"] for x in want_d["drivers"]] == [x["code"] for x in got_d["drivers"]])
    for k, v in mism.items():
        print(f"  {k}: {len(v)} differ, e.g. {v[:3]}")
    # overview
    wo = json.loads((d / "overview.json").read_text())
    go = json.loads(json.dumps(static_data._clean(S.overview())))
    wr = {r["code"]: r for r in wo["drivers"]}
    om = {}
    for r in go["drivers"]:
        w = wr[r["code"]]
        for k in ("sectors", "ideal"):
            if w[k] != r[k]:
                om.setdefault(k, []).append(f"{r['code']}: {w[k]} vs {r[k]}")
        for t in ("I1", "I2", "ST"):
            if w["traps"][t] != r["traps"][t]:
                om.setdefault("trap " + t, []).append(f"{r['code']}: {w['traps'][t]} vs {r['traps'][t]}")
        wl = {l["lap"]: l for l in w["laps"]}
        for l in r["laps"]:
            x = wl.get(l["lap"])
            if not x:
                continue
            for k in ("time", "deleted", "compound", "stint", "tyre_life", "position", "pit"):
                if x[k] != l[k]:
                    om.setdefault("lap." + k, []).append(f"{r['code']} L{l['lap']}: {x[k]} vs {l[k]}")
    print("  weather", wo["weather"], "|", go["weather"])
    for k, v in om.items():
        print(f"  overview {k}: {len(v)} differ, e.g. {v[:4]}")
    # circuit
    wc = json.loads((d / "circuit.json").read_text())
    gc = S.circuit()
    dd = [abs(a["distance"] - b["distance"]) for a, b in zip(wc["corners"], gc["corners"])]
    print("  circuit rotation", wc["rotation"], gc["rotation"], "corners", len(wc["corners"]), len(gc["corners"]),
          "max corner distance diff m", round(max(dd), 1) if dd else None)
    # telemetry of each driver's fastest lap
    errs = []
    for x in got_d["drivers"][:22]:
        f = d / "laps" / f"{x['code']}.json"
        if not f.exists() or x["best"] is None:
            continue
        w = json.loads(f.read_text())
        tel, info = S.lap_telemetry(x["code"])
        wspeed = np.interp(np.linspace(0, 1, 400), np.array(w["data"]["RelativeDistance"]), np.array(w["data"]["Speed"]))
        gspeed = np.interp(np.linspace(0, 1, 400), tel["RelativeDistance"].to_numpy(), tel["Speed"].to_numpy())
        P = S.finish_line
        line = lambda X, Y: round(float(np.hypot(X - P[0], Y - P[1])) / 10, 1)
        errs.append((x["code"], w["info"]["time"], info["time"], round(w["data"]["Distance"][-1] - tel["Distance"].iloc[-1], 1),
                     round(float(np.mean(np.abs(wspeed - gspeed))), 2), round(float(tel["Time"].iloc[-1] - info["time"]), 3),
                     "ff1 line m", line(w["data"]["X"][0], w["data"]["Y"][0]), line(w["data"]["X"][-1], w["data"]["Y"][-1]),
                     "of1 line m", line(tel.X.iloc[0], tel.Y.iloc[0]), line(tel.X.iloc[-1], tel.Y.iloc[-1])))
    print("  laps: code, ff1 time, of1 time, dist diff m, mean |speed diff| km/h, tel end - laptime")
    for e in errs[:6]:
        print("   ", e)
    print("  worst mean speed diff", max(e[4] for e in errs), "median", float(np.median([e[4] for e in errs])),
          "lap times equal:", all(e[1] == e[2] for e in errs), "worst of1 line m", max(max(e[10], e[11]) for e in errs))


if __name__ == "__main__":
    a = sys.argv[1:]
    for i in range(4, len(a), 2):
        main(a[0], a[1], a[2], a[3], a[i], int(a[i + 1]))
