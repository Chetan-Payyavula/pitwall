/* Serverless mode, worker side: runs the project's own backend Python (backend/service.py) in Pyodide,
   on baked session files, so the online build gives the same numbers as the Python server.
   Messages in:  {init: {pyodide, data, py}}   then   {id, fn, args, files}
   Messages out: {status}  ·  {ready}  ·  {id, result} | {id, error} */
let cfg = null, py = null, booting = null;
const fetched = new Map();     // data path -> Promise (each file is fetched once)

const BACKEND = ["__init__.py", "analysis.py", "data.py", "demo.py", "live_demo.py", "live_sources.py",
  "livecore.py", "strategy.py", "service.py", "static_data.py"];

const PY_SETUP = `
import json, sys
sys.path.insert(0, "/app")
from backend import data, service, static_data
static_data.install("/data")

def pitwall_call(fn, args_json):
    a = json.loads(args_json)
    y, ev, ss = int(a.get("year", 0)), str(a.get("event", "demo")), a.get("session", "Race")
    try:
        if fn == "overview":
            r = data.overview(y, ev, ss)
        elif fn == "drivers":
            r = data.drivers(y, ev, ss)
        elif fn == "compare":
            r = service.compare(y, ev, ss, service.parse_codes(a["drivers"]), service.parse_laps(a.get("laps", "")),
                                int(a.get("sectors", 25)))
        elif fn == "meta":
            r = service.live_meta(a["source"], y, ev, ss)
        elif fn == "snapshot":
            r = service.live_snapshot(a["source"], y, ev, ss, float(a.get("t", 0)), service.parse_focus(a.get("focus", "")))
        elif fn == "strategy":
            r = service.live_strategy_at(a["source"], y, ev, ss, float(a.get("t", 0)), service.parse_focus(a.get("focus", "")))
        else:
            raise data.DataError("Unknown request " + fn)
        return json.dumps({"ok": static_data._clean(r)}, allow_nan=False, separators=(",", ":"))
    except data.DataError as exc:
        return json.dumps({"error": str(exc)})
`;

const status = (text) => postMessage({ status: text });

function boot() {
  return (booting ||= (async () => {
    status("Starting the analysis engine…");
    importScripts(cfg.pyodide + "pyodide.js");
    py = await loadPyodide({ indexURL: cfg.pyodide });
    status("Loading numpy and pandas…");
    await py.loadPackage(["numpy", "pandas"]);
    py.FS.mkdirTree("/app/backend");
    py.FS.mkdirTree("/data");
    await Promise.all(BACKEND.map(async (f) => {
      const r = await fetch(cfg.py + "backend/" + f);
      if (!r.ok) throw new Error(`Couldn't load the analysis code (${f})`);
      py.FS.writeFile("/app/backend/" + f, await r.text());
    }));
    py.runPython(PY_SETUP);
    postMessage({ ready: true });
  })());
}

/** Copy baked files into Pyodide's file system. A missing file is skipped: Python then explains what's missing. */
function fetchFile(rel) {
  if (!fetched.has(rel)) {
    fetched.set(rel, (async () => {
      const r = await fetch(cfg.data + rel);
      if (!r.ok) return;
      const buf = new Uint8Array(await r.arrayBuffer());
      py.FS.mkdirTree("/data/" + rel.split("/").slice(0, -1).join("/"));
      py.FS.writeFile("/data/" + rel, buf);
    })().catch((e) => { fetched.delete(rel); throw e; }));
  }
  return fetched.get(rel);
}

self.onmessage = async (e) => {
  const m = e.data;
  if (m.init) { cfg = m.init; boot().catch((err) => postMessage({ bootError: String(err?.message || err) })); return; }
  try {
    await boot();
    await Promise.all((m.files || []).map(fetchFile));
    const out = JSON.parse(py.globals.get("pitwall_call")(m.fn, JSON.stringify(m.args)));
    postMessage(out.error != null ? { id: m.id, error: out.error } : { id: m.id, result: out.ok });
  } catch (err) {
    postMessage({ id: m.id, error: String(err?.message || err) });
  }
};
