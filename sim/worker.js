// 対戦エンジン(Python)をPyodideで動かす
importScripts("pyodide/pyodide.js");
const FILES = ["pengine.py", "pcards.py", "engine.py", "precipes_final.py", "tune.json", "pplay.py", "learn_ai.py", "learn_model.py", "learn_guard_model.json"];
let step = null;
const ready = (async () => {
  const py = await loadPyodide({ indexURL: "./pyodide/", env: { PYTHONHASHSEED: "0", HOME: "/home/pyodide" } });
  for (const f of FILES) {
    const t = await (await fetch("py/" + f + "?v=" + (self.SIM_VER || ""))).text();
    py.FS.writeFile("/home/pyodide/" + f, t);
  }
  py.runPython("import sys; sys.path.insert(0, '/home/pyodide'); import pplay");
  step = py.globals.get("pplay").step;
  postMessage({ type: "ready" });
})().catch(e => postMessage({ type: "fatal", msg: String(e) }));
onmessage = async e => {
  await ready;
  const { id, cfg, answers, cache } = e.data;
  try {
    const r = step(JSON.stringify(cfg), JSON.stringify(answers), JSON.stringify(cache || []));
    postMessage({ type: "res", id, res: r });
  } catch (err) {
    postMessage({ type: "err", id, msg: String(err) });
  }
};
