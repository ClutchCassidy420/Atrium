// REA page: upload, pick an analysis, run it, read the result. Synthetic only (no network).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "../app/static");
const settle = (ms = 120) => new Promise(resolve => setTimeout(resolve, ms));
const FID = "aaaaaaaaaaaa", JID = "bbbbbbbbbbbb";

async function open(handler) {
  const dom = new JSDOM('<main id="root"></main>', { url: "https://app.test/rea", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window, load = f => w.eval(fs.readFileSync(path.join(ROOT, f), "utf8"));
  load("vendor/react.production.min.js"); load("vendor/react-dom.production.min.js");
  const calls = [], pages = {};
  w.__HERMES_PLUGINS__ = { register: (n, c) => { pages[n] = c; }, registerSlot() {} };
  w.__HERMES_PLUGIN_SDK__ = { React: w.React, hooks: w.React, fetchJSON: async (url, o) => { calls.push([(o && o.method) || "GET", url, o && o.body ? JSON.parse(o.body) : null]); return handler(url, o || {}); } };
  w.__APP_CONFIG__ = { brand: "", agentName: "Robo", name: "", user: "" };
  w.atriumEvery = () => () => {};
  w.confirm = () => true;
  ["pages/common.js", "pages/kit.js", "pages/rea/index.js"].forEach(load);
  w.ReactDOM.createRoot(w.document.getElementById("root")).render(w.React.createElement(pages["rea-page"]));
  await settle(250);
  return { w, calls };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const btn = (w, re) => [...w.document.querySelectorAll("button, a.hm-btn")].find(b => re.test(b.textContent));

function fake({ up = true } = {}) {
  const state = { files: [{ id: FID, name: "app.exe", kind: "file", size: 2048, created: 1 }], jobs: [] };
  const commands = [
    { id: "pe-imports", group: "Windows programs", label: "Windows program: imports", help: "Which DLLs a program uses.", target: "file", arg_label: "", arg_optional: false },
    { id: "ghidra-decompile", group: "Deep analysis (Ghidra)", label: "Decompile at an address", help: "Pseudo-code.", target: "file", arg_label: "Address", arg_optional: false }];
  const handler = async (url, o) => {
    if (url === "/api/rea/status") return { configured: true, up };
    if (url === "/api/rea/commands") return { commands };
    if (url === "/api/rea/files") return { files: state.files };
    if (url === "/api/rea/jobs" && o.method === "POST") { const b = JSON.parse(o.body); state.jobs = [{ id: JID, command: b.command, label: "Windows program: imports", file: b.file, file_name: "app.exe", arg: b.arg, status: "done", created: 2, size: 500 }]; return { job: state.jobs[0] }; }
    if (url === "/api/rea/jobs") return { jobs: state.jobs };
    if (url === "/api/rea/jobs/" + JID + "/summary") return { size: 500, preview: '{"imports": []}', truncated: false, outline: { type: "object", keys: 1, items: { imports: { type: "list", length: 2, sample: [{ type: "object", keys: 1, items: { dll: { type: "text", value: "kernel32.dll", length: 12 } } }] } }, more: 0 } };
    if (url === "/api/rea/jobs/" + JID && o.method === "DELETE") { state.jobs = []; return { ok: true }; }
    return {};
  };
  return { handler, state };
}

(async () => {
  // the workbench is not running: a plain message, nothing to click
  const down = await open(fake({ up: false }).handler);
  assert.match(down.w.document.body.textContent, /REA workbench is not running/);

  const { handler, state } = fake();
  const { w, calls } = await open(handler);
  const text = () => w.document.body.textContent;
  assert.match(text(), /only read, never run/);
  assert.match(text(), /app\.exe/);

  // Run needs both an uploaded file and an analysis
  assert.ok(btn(w, /^Run$/).disabled);
  click(w, w.document.querySelector('input[type="radio"]'));
  const select = w.document.querySelector("select");
  assert.deepEqual([...select.querySelectorAll("optgroup")].map(g => g.label), ["Windows program" + "s", "Deep analysis (Ghidra)"]);
  select.value = "pe-imports"; select.dispatchEvent(new w.Event("change", { bubbles: true }));
  await settle();
  assert.match(text(), /Which DLLs a program uses\./);
  assert.ok(!btn(w, /^Run$/).disabled);

  // an analysis that needs a typed value shows its field
  select.value = "ghidra-decompile"; select.dispatchEvent(new w.Event("change", { bubbles: true }));
  await settle();
  assert.ok([...w.document.querySelectorAll("label")].some(l => /Address/.test(l.textContent)));
  select.value = "pe-imports"; select.dispatchEvent(new w.Event("change", { bubbles: true }));
  await settle();

  w.document.querySelector("form.rea-run").dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
  await settle(300);
  const post = calls.find(c => c[0] === "POST" && c[1] === "/api/rea/jobs");
  assert.deepEqual(post[2], { command: "pe-imports", file: FID, arg: "" });

  // the result opens with an outline and a download link; the raw start is one click away
  assert.match(text(), /imports/); assert.match(text(), /\[2 items\]/);
  const dl = btn(w, /Download/);
  assert.equal(dl.getAttribute("href"), "/api/rea/jobs/" + JID + "/download");
  click(w, btn(w, /Raw start/));
  await settle();
  assert.match(text(), /"imports": \[\]/);

  // deleting a result removes it
  click(w, btn(w, /Delete this result/));
  await settle(250);
  assert.ok(calls.some(c => c[0] === "DELETE" && c[1] === "/api/rea/jobs/" + JID));
  assert.match(text(), /Nothing run yet/);

  console.log("rea tests passed");
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
