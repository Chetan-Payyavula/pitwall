# Pitwall Telemetry

Built by Chetan Payyavula, with AI assistance (Claude) for the code.

![Telemetry tab](docs/pit-telemetry.jpg)

Compare Formula 1 laps: speed / throttle / brake / gear traces, lap-time delta,
braking-zone table, speed map and track-dominance map. Data comes from the
official F1 live-timing feed through [FastF1](https://docs.fastf1.dev)
(2018 season onwards).

**Try it online: [chetan-payyavula.github.io/pitwall](https://chetan-payyavula.github.io/pitwall/)** (no install).
The online version has every 2026 qualifying, sprint and race so far (plus three 2024 sessions) with each
driver's fastest lap, and runs the same Python analysis code in your browser. Run it locally (below) for any
session, any lap and live timing.

> **Data note (October 2026).** Since about 15 October 2026 F1's live-timing archive, which FastF1 reads,
> answers 401 unless you log in with an F1 TV subscription
> ([FastF1 #978](https://github.com/theOehrly/Fast-F1/issues/978)). Sessions already in your FastF1 cache
> still load. The online version's 2026 data therefore comes from [OpenF1](https://openf1.org) (free, same
> timing and car feed) with the official classification from [Jolpica](https://github.com/jolpica/jolpica-f1).

## Run it

```bash
cd f1-telemetry
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --reload-dir backend
```

On Windows you can just double-click `run.bat`. Open http://localhost:8000. It opens on the built-in **Demo** circuit (synthetic
data, works offline). Pick a season, event and session to load real data — the
first load of a session takes 30–60 s while FastF1 downloads it; after that it
is cached in `cache/` and loads in a second or two.

Run the tests with `pytest`.

## Online version (GitHub Pages, no server)

The `gh-pages` branch is a static build of the same app:

- Sessions are baked into JSON: the session table, every driver's fastest-lap telemetry and, for races and
  sprints, the full replay data (car channels and session times stored losslessly; map positions as float32,
  under 1 mm error). 2024 sessions come from the FastF1 cache (`tools/bake_static.py`); 2026 sessions from
  OpenF1 (`tools/openf1_fetch.py` downloads, `backend/openf1_hist.py` converts, `tools/bake_openf1.py` bakes).
- The OpenF1 converter reproduces FastF1's outputs: on the sessions both sources have (Kuala Lumpur and
  Singapore 2026), the classification, Q1–Q3 times, best laps, sector bests, speed traps, weather and lap
  times are identical (`tools/check_openf1.py`). On top of the raw feed it:
  - snaps each lap's start to the moment the car crosses the timing line (OpenF1's lap starts can be 10–35 m
    off), keeping the official lap time, so every lap starts within a metre of the line;
  - places pit stops by their pit-lane entry time, and starts a new tyre stint at every stop (OpenF1's stint
    list sometimes opens a phantom stint on lap 2); where a FastF1 bake of the same session exists, its
    official tyre and position data is used instead;
  - takes the timing line from the event's qualifying session when a race's own estimate is off, and removes
    stretches where a race's position feed runs seconds behind the lap clock (Hungary 2026: ~3 s from lap 16);
  - checks every fastest lap's car data and positions. Where the car data is broken (the feed stalls,
    holding speed and RPM for > 3 s, the lap distance is > 1.5% off the track length, or the
    car data doesn't reach the sector lines at the official sector times), the next fastest clean lap is shown and its
    card says so. Where only the positions are missing (Monaco 2026 race: the feed stops after ~20 minutes),
    the lap is kept and drawn along the session's reference lap; the pit-wall replay does the same.
  - Known limit: OpenF1 doesn't say when a used tyre set is refitted, so tyre age counts this session's laps.
- **Lap delta.** Each lap's distance is integrated from its own speed trace, so two laps of the same track
  end metres apart, and one bad speed sample shifts everything after it. Every compared lap is put on the
  reference lap's distance scale by matching its positions to the reference lap's path (ignoring position
  samples that are off the path, run backwards, or disagree by > 25 m), so corners line up for every driver
  and the delta ends exactly at the official lap-time gap.
- `backend/service.py` holds the API logic shared by the server and the static build. In the browser it runs
  in a Web Worker on [Pyodide](https://pyodide.org) (Python + numpy + pandas compiled to WebAssembly), so lap
  comparisons are computed by the same code as the server.
- `tools/check_static.py` checks that the baked path gives the same answers as FastF1 (143 checks, all identical).
  In the browser, lap comparisons, session data and replay timing match exactly; the strategy model's fits can
  differ in the last rounded digit because Pyodide ships an older numpy.

Rebuild it:

```bash
F1_CACHE_DIR=cache python -m tools.bake_static site-data
F1_CACHE_DIR=cache python -m tools.check_static site-data
python -m tools.build_static_site site-data site      # then publish site/ to the gh-pages branch
```

## What's inside

| Tab | What you get |
|---|---|
| **Intro** | Boot log → five red lights → lights out → warp with shift lights → logo. Skip button, Esc/Enter/Space, "don't show again", optional synthesized sound, `?intro=0` / `?intro=1` in the URL, ▶ Intro button to replay it. |
| **Overview** | Event banner with weather, 3D-tilt driver cards (lap, gap, tyre, sectors, stat bars), auto-generated "race engineer's notes", sector-gap bars, track dominance map, speed map, animated top-speed gauges. |
| **3D Replay** | three.js track built from real X/Y/Z positions (elevation exaggeration slider), kerbs, barrier boards, start gantry, low-poly cars racing their real laps with brake glow, trails and labels. Cameras: chase, onboard, TV, helicopter, free orbit, auto director (keys 1–6). Track colouring: speed, dominance, pedals, plain. HUD with live order and gaps, speed/gear/pedals, mini-map, playback speed and scrubber (space = play/pause). |
| **Telemetry** | Speed, delta, throttle, brake, gear and RPM stacked on distance; hover moves dots on a mini-map and fills a live readout. G-G diagram, gear usage, speed distribution. |
| **Corners** | 3D coverflow — one slide per corner with mini-map, speed trace, brake points and entry/min/exit/time table (←/→ or swipe). Braking-zone table underneath. |
| **Pit Wall (live)** | Live timing tower (gap, interval with DRS window, last lap purple/green/deleted, tyre + age, stops, track-limit / penalty / investigation / blue-flag badges), live track map, race-control feed, flag banner (yellow sectors, VSC, SC, red), two driver dashboards (speed, gear, shift lights, throttle/brake, DRS, ERS battery estimate, 30 s trace). Strategy: tyre degradation per compound fitted live, pit loss under green/VSC/SC, rejoin predictor with gap ladder, undercut/overcut for every close battle, pit-window optimiser. Sources: built-in **demo race**, **replay** of any past race on its own clock (1×–60×, scrub, ±1 lap), or **OpenF1 live**. |
| **Session** | Weather, full classification with purple sectors, ideal laps and speed traps; race sessions add position chart, tyre strategy and race pace; other sessions get a lap-time scatter. |

## Live data — what is and isn't possible

* **Replay** (free): any race since 2018 via FastF1, played back as if live.
* **OpenF1 live**: real-time OpenF1 data needs an OpenF1 account (they ask for a
  sponsorship). Put your login in environment variables before starting:
  `set OPENF1_USERNAME=you@example.com` and `set OPENF1_PASSWORD=...` (Windows),
  then run `run.bat`. The server polls every 4 s and shows data ~4 s behind live.
* **Battery / ERS**: teams don't publish state of charge or deployment. The Pit
  Wall shows a clearly labelled *estimate* from speed, throttle and brake
  (harvest under braking/coasting, deploy on full throttle, per-lap budgets).
* Strategy numbers are models, not team data: fuel effect 0.055 s/lap, VSC pit
  loss ≈ 60 % and SC ≈ 50 % of green, tyre warm-up penalties — all constants
  at the top of `backend/strategy.py`.

## How it works

```
backend/
  main.py      FastAPI app: /api/schedule, /api/overview, /api/drivers, /api/compare + serves frontend/
  service.py   the API logic (compare, pit wall) shared by the server and the in-browser build
  static_data.py bake sessions to files, and read them back without FastF1 (online version)
  data.py      FastF1 loading (cached), drivers, lap telemetry, circuit corners, session overview
  analysis.py  pure maths: lap delta, braking zones, mini-sector dominance, g-forces, corner analysis
  demo.py      synthetic circuit with elevation, lap-time-sim speed profiles, race simulation
  livecore.py  live timing engine: one RaceData model → snapshot(t) (order, gaps, flags…)
  strategy.py  tyre deg, pit loss, rejoin, undercut/overcut, pit window, ERS estimate
  live_demo.py simulated race with VSC, SC, yellows, penalties, retirement
  live_sources.py FastF1 replay + OpenF1 live poller → RaceData
frontend/
  index.html, css/style.css
  js/main.js      app shell: pickers, tabs, URL state
  js/intro.js     opening sequence (canvas + Web Audio)
  js/track3d.js   3D replay (three.js)
  js/charts.js    overview + telemetry charts (Plotly)
  js/corners.js   corner coverflow + braking table
  js/session.js   classification, traps, race charts
  js/live.js      Pit Wall tab
  js/static-api.js, js/static-worker.js  online version: answers the API from baked files + Pyodide
  vendor/         plotly, three.js, OrbitControls (no CDN, no build step)
tests/         analysis maths, API, and the FastF1 code path with a fake session
tools/         bake, check and build the online version
```

**Lap delta** — every lap is first put on the reference lap's distance scale (positions matched to the
reference lap's path, see "Online version"), then both are interpolated onto a shared 5 m grid and the
elapsed times subtracted. Positive = slower than the reference (the fastest of the chosen laps). The last
value always equals the official lap-time gap.

**Braking zones** — taken from the brake on/off channel. Signal flicker under
15 m is merged, blips that don't cost ≥10 km/h are ignored, and the minimum
speed is searched past brake release (the apex is usually after it).

**Track dominance** — the lap is cut into equal-distance mini-sectors and each
is coloured by the driver who spent the least *time* in it.

**G-forces** — longitudinal from dv/dt, lateral from v² × path curvature. XY is
converted to metres by comparing path length with distance, so FastF1's
decimetre positions and the demo's metres give the same answer.

## API

| Endpoint | Example |
|---|---|
| `GET /api/schedule?year=2024` | events with sessions that have run |
| `GET /api/overview?year=2024&event=16&session=Qualifying` | classification, sectors, speed traps, every lap, weather |
| `GET /api/drivers?year=2024&event=16&session=Qualifying` | drivers, team colours, best laps |
| `GET /api/live/snapshot?source=replay&year=2024&event=16&session=Race&t=4000&focus=LEC,PIA` | timing screen at session time t |
| `GET /api/live/strategy?source=replay&year=2024&event=16&session=Race&t=4000&focus=LEC` | deg, pit loss, rejoin, battles, pit window, ERS |
| `GET /api/compare?year=2024&event=16&session=Qualifying&drivers=LEC,NOR&laps=fastest,fastest` | everything the dashboard draws |

`year=0` uses the demo circuit. `event` is a round number or event name.
Interactive docs: http://localhost:8000/docs.

## Ideas for next steps

- Race mode: lap-time chart, tyre stints, position changes
- Corner-by-corner minimum-speed bar chart
- Throttle/brake overlap ("trail braking") metric
- Deploy: one Docker container (`uvicorn backend.main:app --host 0.0.0.0`)
  with a persistent volume for `cache/`

## Screenshots

![Pit wall under a safety car](docs/pit-wall.jpg)

![3D lap replay](docs/pit-replay.jpg)
