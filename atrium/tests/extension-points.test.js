// Extension points: an install can add or replace Settings sections, and the avatar script hands its Orb to the chat page. Synthetic only (no network).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "../app/static");
const settle = () => new Promise(resolve => setTimeout(resolve, 80));

function make(hash) {
  const dom = new JSDOM('<main id="root"></main>', { url: "https://app.test/settings" + hash, runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  const load = file => w.eval(fs.readFileSync(path.join(ROOT, file), "utf8"));
  load("vendor/react.production.min.js");
  load("vendor/react-dom.production.min.js");
  return { w, load };
}

async function settings(hash, setup) {
  const { w, load } = make(hash);
  let Page;
  w.__HERMES_PLUGINS__ = { register: (_, page) => { Page = page; } };
  w.__HERMES_PLUGIN_SDK__ = { React: w.React, hooks: w.React, fetchJSON: async url => {
    if (url === "/api/host/status") return { enabled: false };
    if (url === "/api/plugins/atrium-settings/model") return { model: "previous" };
    return {};
  } };
  setup(w);
  load("pages/settings/index.js");
  w.ReactDOM.createRoot(w.document.querySelector("main")).render(w.React.createElement(Page));
  await settle();
  return w;
}

(async () => {
  // 1. with no additions the page is exactly the core sections
  let w = await settings("#model", () => {});
  let names = [...w.document.querySelectorAll(".ats-navitem")].map(b => b.textContent);
  assert.ok(names.some(t => /Chat model/.test(t)));

  // 2. an addition shows up and works; a malformed one and one that reuses a core id are ignored; "replaces" takes a core section out
  w = await settings("#added", win => {
    const part = () => win.React.createElement("p", { id: "extra" }, "extra part");
    win.__APP_SETTINGS_SECTIONS__ = [
      { id: "added", label: "Added", icon: "+", note: "n", parts: [part] },
      { id: "general", label: "Hijack", icon: "!", note: "n", parts: [part] },
      { id: "broken", label: "Broken" },
      null,
      { id: "swap", label: "Swapped", icon: "s", note: "n", parts: [part], replaces: "model" }];
  });
  names = [...w.document.querySelectorAll(".ats-navitem")].map(b => b.textContent);
  assert.ok(names.some(t => /Added/.test(t)) && names.some(t => /Swapped/.test(t)));
  assert.ok(!names.some(t => /Hijack|Broken/.test(t)), "a section that reuses a core id or has no parts is ignored");
  assert.ok(!names.some(t => /Chat model/.test(t)), "replaces removes the core section it names");
  assert.ok(names.some(t => /General/.test(t)), "the core General section is untouched");
  assert.ok(w.document.querySelector("#extra"), "the added section renders its part");

  // 3. orb.js hands the Orb to the chat page, and never replaces one an install already supplied
  let m = make("");
  m.w.__HERMES_PLUGIN_SDK__ = { React: m.w.React, hooks: m.w.React, fetchJSON: async () => ({}) };
  m.w.__HERMES_PLUGINS__ = { register() {} };
  m.load("pages/avatar/orb.js");
  assert.equal(typeof m.w.__ATRIUM_UI__.Orb, "function");
  assert.equal(m.w.__ATRIUM_UI__.Orb, m.w.__AVATARS__.Orb);
  m = make("");
  m.w.__HERMES_PLUGIN_SDK__ = { React: m.w.React, hooks: m.w.React, fetchJSON: async () => ({}) };
  m.w.__HERMES_PLUGINS__ = { register() {} };
  const mine = () => null;
  m.w.__ATRIUM_UI__ = { Orb: mine };
  m.load("pages/avatar/orb.js");
  assert.equal(m.w.__ATRIUM_UI__.Orb, mine);
  console.log("extension-points.test.js: ok");
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
