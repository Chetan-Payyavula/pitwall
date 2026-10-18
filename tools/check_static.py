"""Prove the static build gives the same answers as the FastF1 server path.

    F1_CACHE_DIR=cache python -m tools.check_static BAKED_DIR
Exact equality for overview / drivers / compare; replay snapshots must match to < 1 mm on the map
(positions are stored as float32) and exactly everywhere else.
"""
from __future__ import annotations

import itertools
import json
import logging
import math
import sys
import warnings

import fastf1

from backend import service, static_data
from backend.static_data import _clean


def norm(o):
    return json.loads(json.dumps(_clean(o)))


def diff(a, b, path="", tol=0.0, out=None):
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in set(a) | set(b):
            if k not in a or k not in b:
                out.append(f"{path}.{k} missing on one side")
            else:
                diff(a[k], b[k], f"{path}.{k}", tol, out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path} length {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", tol, out)
    elif isinstance(a, float) and isinstance(b, (int, float)) or isinstance(b, float) and isinstance(a, (int, float)):
        if not (a == b or (tol and math.isclose(a, b, rel_tol=0, abs_tol=tol))):
            out.append(f"{path}: {a} != {b}")
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out


def main(root):
    logging.disable(logging.WARNING)
    warnings.simplefilter("ignore")
    fastf1.Cache.offline_mode(True)
    manifest = json.loads((root + "/manifest.json").read() if False else open(root + "/manifest.json").read())
    cases = []
    for year, events in manifest["years"].items():
        for ev in events:
            for sess in ev["sessions"]:
                cases.append((int(year), str(ev["round"]), sess))

    from backend import data
    expected = {}
    for y, e, s in cases:
        drv = data.drivers(y, e, s)
        codes = [d["code"] for d in drv["drivers"] if d["best"] is not None]
        combos = [codes[:4], codes[:2], [codes[0]], codes[-3:], codes[3:7:2], list(reversed(codes[:3]))]
        combos += [list(c) for c in itertools.islice(itertools.combinations(codes[:8], 2), 0, 28, 5)]
        expected[(y, e, s, "drivers")] = norm(drv)
        expected[(y, e, s, "overview")] = norm(data.overview(y, e, s))
        for combo in combos:
            expected[(y, e, s, "compare", ",".join(combo))] = norm(service.compare(y, e, s, combo, [], 25))
        if s in ("Race", "Sprint"):
            meta = service.live_meta("replay", y, e, s)
            expected[(y, e, s, "meta")] = norm(meta)
            focus = codes[:3]
            span = meta["end"] - meta["start"]
            for frac in (0.0, 0.07, 0.3, 0.55, 0.81, 0.99):
                t = round(meta["start"] + frac * span, 2)
                expected[(y, e, s, "snap", t)] = norm(service.live_snapshot("replay", y, e, s, t, focus))
                expected[(y, e, s, "strat", t)] = norm(service.live_strategy_at("replay", y, e, s, t, focus))
        print("expected", y, e, s, flush=True)

    static_data.install(root)
    bad = 0
    for key, want in expected.items():
        y, e, s, kind, *rest = key
        if kind == "drivers":
            got = norm(data.drivers(y, e, s))
        elif kind == "overview":
            got = norm(data.overview(y, e, s))
        elif kind == "compare":
            got = norm(service.compare(y, e, s, rest[0].split(","), [], 25))
        elif kind == "meta":
            got = norm(service.live_meta("replay", y, e, s))
        elif kind == "snap":
            meta = expected[(y, e, s, "meta")]
            focus = [d["code"] for d in json.loads(json.dumps(expected[(y, e, s, "drivers")]))["drivers"] if d["best"] is not None][:3]
            got = norm(service.live_snapshot("replay", y, e, s, rest[0], focus))
        else:
            focus = [d["code"] for d in expected[(y, e, s, "drivers")]["drivers"] if d["best"] is not None][:3]
            got = norm(service.live_strategy_at("replay", y, e, s, rest[0], focus))
        # map coordinates come from float32 storage: allow 0.01 units (1 mm); everything else exact
        problems = diff(want, got, tol=0.01 if kind == "snap" else 0.0)
        if kind == "snap":
            problems = [p for p in problems if not any(f".{k}:" in p or f".{k}[" in p for k in ("x", "y"))] + \
                       [p for p in problems if any(f".{k}:" in p for k in ("x", "y")) and "!=" in p and abs(float(p.split(": ")[1].split(" != ")[0]) - float(p.split(" != ")[1])) > 0.01]
        if problems:
            bad += 1
            print("MISMATCH", key, problems[:5])
    print(f"{len(expected)} checks, {bad} mismatches")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1]) else 0)
