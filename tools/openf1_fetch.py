"""Download historical sessions from OpenF1 (free, no login) into a local JSON cache.

    python -m tools.openf1_fetch CACHE_DIR YEAR [SESSION_NAME ...]

Every response is stored once (gzip JSON) so re-runs and the converter never re-download.
Free-tier friendly: a client-side limit of 3 requests/s and 30/min, with back-off on 429/5xx.
"""
from __future__ import annotations

import gzip
import http.client
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.openf1.org/v1"
PER_DRIVER = ("car_data", "location")
SESSION_EPS = ("drivers", "session_result", "laps", "stints", "pit", "race_control", "weather", "position", "starting_grid")
WANT = ("Qualifying", "Sprint Qualifying", "Sprint", "Race")


class Client:
    def __init__(self, cache: Path, per_sec: int = 3, per_min: int = 30):
        self.cache, self.per_sec, self.per_min = Path(cache), per_sec, per_min
        self.stamps: list[float] = []
        self.requests = 0

    def _wait(self):
        while True:
            now = time.monotonic()
            self.stamps = [t for t in self.stamps if now - t < 60]
            recent = [t for t in self.stamps if now - t < 1]
            if len(self.stamps) < self.per_min and len(recent) < self.per_sec:
                self.stamps.append(now)
                return
            time.sleep(0.25)

    def path(self, endpoint: str, params: dict) -> Path:
        key = "&".join(f"{k}={params[k]}" for k in sorted(params))
        safe = urllib.parse.quote(key, safe="=&").replace("&", "__").replace("=", "-").replace("%", "")
        return self.cache / endpoint / f"{safe}.json.gz"

    def get(self, endpoint: str, **params):
        f = self.path(endpoint, params)
        if f.exists():
            return json.loads(gzip.decompress(f.read_bytes()))
        url = f"{API}/{endpoint}?" + "&".join(f"{k}={urllib.parse.quote(str(v), safe='<>=:+-.')}" for k, v in params.items())
        delay = 5.0
        for attempt in range(8):
            self._wait()
            self.requests += 1
            try:
                with urllib.request.urlopen(url, timeout=180) as r:
                    data = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:                       # OpenF1 answers 404 "No results found." for empty lists
                    data = []
                    break
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(delay); delay = min(delay * 2, 120); continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead,
                    http.client.RemoteDisconnected, json.JSONDecodeError):
                time.sleep(delay); delay = min(delay * 2, 120)
        else:
            raise RuntimeError(f"giving up on {url}")
        if isinstance(data, dict) and "detail" in data:
            data = []
        if data == []:
            return data          # not cached: a session that hasn't happened yet will have data later
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(gzip.compress(json.dumps(data).encode()))
        return data


def fetch_session(c: Client, session: dict) -> int:
    sk = session["session_key"]
    out = {ep: c.get(ep, session_key=sk) for ep in SESSION_EPS}
    if not out["laps"]:
        return 0
    c.get("meetings", meeting_key=session["meeting_key"])
    for d in out["drivers"]:
        for ep in PER_DRIVER:
            c.get(ep, session_key=sk, driver_number=d["driver_number"])
    return len(out["laps"])


def main(cache: str, year: int, names=WANT, first: tuple = ()):
    c = Client(Path(cache))
    sessions = c.get("sessions", year=year)
    todo = [s for s in sessions if s["session_name"] in names and not s.get("is_cancelled")]
    todo.sort(key=lambda s: (s["session_key"] not in first, s["date_start"]))   # `first` session keys go first
    t0 = time.time()
    for i, s in enumerate(todo):
        try:
            n = fetch_session(c, s)
        except Exception as e:   # keep going: one bad session must not stop the season
            print(f"[{i+1}/{len(todo)}] {s['session_key']} {s['country_name']} {s['session_name']}: ERROR {e}", flush=True)
            continue
        print(f"[{i+1}/{len(todo)}] {s['session_key']} {s['date_start'][:10]} {s['location']} {s['session_name']}: "
              f"{n} laps · {c.requests} requests · {time.time()-t0:.0f}s", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    keys = tuple(int(a) for a in sys.argv[3:] if a.isdigit())
    names = tuple(a for a in sys.argv[3:] if not a.isdigit()) or WANT
    main(sys.argv[1], int(sys.argv[2]), names, keys)
