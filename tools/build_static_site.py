"""Assemble the serverless Pitwall site (GitHub Pages): frontend + backend Python + baked data.

    python -m tools.build_static_site BAKED_DATA_DIR OUT_DIR [PYODIDE_URL]
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://chetan-payyavula.github.io/pitwall/"
PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/"
BACKEND = ["__init__.py", "analysis.py", "data.py", "demo.py", "live_demo.py", "live_sources.py",
           "livecore.py", "strategy.py", "service.py", "static_data.py"]

TITLE = "Pitwall | F1 Telemetry & Race Strategy Lab"
DESC = ("Compare Formula 1 drivers' fastest laps from official timing data: speed traces, lap delta, braking, "
        "corners, track dominance, a 3D lap replay and a race-strategy pit wall.")
KEYWORDS = ("F1 telemetry, Formula 1 lap comparison, F1 speed trace, lap delta, FastF1, F1 race strategy, undercut, "
            "tyre degradation, 3D lap replay, Pitwall, Chetan Payyavula")


def build(baked: Path, out: Path, pyodide: str = PYODIDE) -> None:
    keep_git = out / ".git"
    if out.exists():
        for p in out.iterdir():
            if p != keep_git:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    out.mkdir(parents=True, exist_ok=True)
    front = ROOT / "frontend"
    for sub in ("css", "js", "vendor"):
        shutil.copytree(front / sub, out / sub)
    (out / "py" / "backend").mkdir(parents=True)
    for f in BACKEND:
        shutil.copy2(ROOT / "backend" / f, out / "py" / "backend" / f)
    shutil.copytree(baked, out / "data")
    (out / ".nojekyll").write_text("")

    years = json.loads((baked / "manifest.json").read_text())["years"]
    sessions = sum(len(e["sessions"]) for evs in years.values() for e in evs)
    per_year = {y: sum(len(e["sessions"]) for e in evs) for y, evs in years.items()}
    latest = max(years)
    others = " ".join(f"plus {n} from {y}" for y, n in per_year.items() if y != latest)
    coverage = f"every {latest} qualifying, sprint and race so far ({per_year[latest]} sessions){', ' + others if others else ''}"
    html = (front / "index.html").read_text()
    cfg = json.dumps({"pyodide": pyodide, "data": "data/", "py": "py/"})
    ld = json.dumps({"@context": "https://schema.org", "@type": "WebApplication", "name": "Pitwall", "url": SITE,
                     "description": DESC, "applicationCategory": "SportsApplication", "operatingSystem": "Any (modern browser)",
                     "isAccessibleForFree": True, "image": SITE + "docs/pit-telemetry.jpg",
                     "author": {"@type": "Person", "name": "Chetan Payyavula", "url": "https://chetan-payyavula.github.io/"}})
    head = f"""  <title>{TITLE}</title>
  <meta name="description" content="{DESC}">
  <meta name="keywords" content="{KEYWORDS}">
  <meta name="author" content="Chetan Payyavula">
  <link rel="canonical" href="{SITE}">
  <meta name="theme-color" content="#07080b">
  <meta property="og:type" content="website">
  <meta property="og:title" content="{TITLE}">
  <meta property="og:description" content="{DESC}">
  <meta property="og:url" content="{SITE}">
  <meta property="og:image" content="{SITE}docs/pit-telemetry.jpg">
  <meta name="twitter:card" content="summary_large_image">
  <script type="application/ld+json">{ld}</script>
  <script>window.PITWALL_STATIC = {cfg};</script>"""
    reps = [
        ("""  <title>Pitwall Telemetry</title>
  <meta name="description" content="Formula 1 lap telemetry: 3D replay, speed traces, lap delta, braking, corners and track dominance.">""", head),
        ("""<p>Real data comes from the official F1 live-timing feed via FastF1. The first load of a session downloads it (30–60&nbsp;s); after that it is cached on disk.</p>""",
         f"""<p>Real data from the official F1 timing feed (via OpenF1 and FastF1): {coverage}. Each driver's fastest lap is included, and the analysis runs in your browser. The <a href="https://github.com/Chetan-Payyavula/pitwall">full app on GitHub</a> loads any session, any lap and live timing.</p>"""),
        ("""<footer class="foot">Data: FastF1 / F1 live timing. Unofficial fan project, not associated with Formula 1.</footer>""",
         """<footer class="foot">Data: FastF1 / F1 live timing. Built by <a href="https://chetan-payyavula.github.io/">Chetan Payyavula</a> · <a href="https://github.com/Chetan-Payyavula/pitwall">Code on GitHub</a>. Unofficial fan project, not associated with Formula 1.</footer>"""),
    ]
    for a, b in reps:
        if html.count(a) != 1:
            raise SystemExit(f"index.html changed, can't find: {a[:70]}")
        html = html.replace(a, b)
    (out / "index.html").write_text(html)
    docs = ROOT / "docs"
    if docs.exists():
        (out / "docs").mkdir(exist_ok=True)
        for img in docs.glob("*.jpg"):
            shutil.copy2(img, out / "docs" / img.name)
    print("built", out, "sessions:", sessions)


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else PYODIDE)
