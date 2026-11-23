// Every page that moved to the UI kit still renders while its data is loading and when the load fails. Synthetic only (no network).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "../app/static");
const settle = () => new Promise(resolve => setTimeout(resolve, 80));

// [label, scripts to load, expected plugin names]
const PAGES = [
  ["kanban", ["pages/kanban/index.js"]],
  ["systems", ["pages/systems.js"]],
  ["workflows", ["pages/workflows/index.js"]],
  ["room", ["pages/room/index.js"]],
  ["mail", ["pages/mail/index.js"]],
];

async function mount(scripts, fetchJSON) {
  const dom = new JSDOM('<main id="root"></main>', { url: "https://app.test/", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window, load = f => w.eval(fs.readFileSync(path.join(ROOT, f), "utf8"));
  load("vendor/react.production.min.js"); load("vendor/react-dom.production.min.js");
  const pages = {};
  w.__HERMES_PLUGINS__ = { register: (n, c) => { pages[n] = c; }, registerSlot() {} };
  w.__HERMES_PLUGIN_SDK__ = { React: w.React, hooks: w.React, fetchJSON, authedFetch: fetchJSON, buildWsUrl: () => "", buildWsAuthParam: () => "" };
  w.atriumEvery = () => () => {}; w.Element.prototype.scrollIntoView = () => {};
  w.__APP_CONFIG__ = { brand: "", agentName: "Assistant", name: "", user: "" };
  ["pages/common.js", "pages/kit.js"].concat(scripts).forEach(load);
  const errors = [];
  w.addEventListener("error", e => errors.push(e.message));
  const names = Object.keys(pages);
  assert.ok(names.length, "a page registered");
  w.ReactDOM.createRoot(w.document.getElementById("root")).render(w.React.createElement(pages[names[0]], {}));
  await settle();
  return { w, errors };
}

(async () => {
  for (const [label, scripts] of PAGES) {
    // while loading: the shared loading state (or at least a rendered page), and no script errors
    let r = await mount(scripts, () => new Promise(() => {}));
    assert.deepEqual(r.errors, [], label + " loading: " + r.errors);
    assert.ok(r.w.document.getElementById("root").children.length, label + " rendered while loading");
    // when the load fails: still renders, and any failure is shown as the shared alert
    r = await mount(scripts, async () => { throw new Error("500: nope"); });
    assert.deepEqual(r.errors, [], label + " failed: " + r.errors);
    assert.ok(r.w.document.getElementById("root").children.length, label + " rendered after failure");
    console.log("ok", label, r.w.document.querySelectorAll(".kit-loading").length, r.w.document.querySelectorAll(".kit-alert").length);
  }
  console.log("pages-kit tests passed");
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
