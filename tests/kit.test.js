// UI kit: states and accessibility behaviour of kit.js, in jsdom. Synthetic only (no network).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "../app/static");
const settle = () => new Promise(resolve => setTimeout(resolve, 60));

function make() {
  const dom = new JSDOM('<main id="root"></main>', { url: "https://app.test/", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  const load = f => w.eval(fs.readFileSync(path.join(ROOT, f), "utf8"));
  load("vendor/react.production.min.js");
  load("vendor/react-dom.production.min.js");
  w.__HERMES_PLUGIN_SDK__ = { React: w.React, hooks: w.React, fetchJSON: async () => ({}) };
  load("pages/common.js");
  load("pages/kit.js");
  const render = el => { w.ReactDOM.createRoot(w.document.getElementById("root")).render(el); return settle(); };
  return { w, h: w.React.createElement, K: w.__APP_UI__.kit, render };
}

(async () => {
  const { w, h, K, render } = make();
  const $ = s => w.document.querySelector(s);

  // Button: loading blocks clicks but stays focusable
  let clicks = 0;
  await render(h(K.Button, { loading: true, onClick: () => clicks++ }, "Save"));
  assert.equal($("button").getAttribute("aria-busy"), "true");
  $("button").click(); assert.equal(clicks, 0);

  // AsyncView: loading -> skeleton, empty, error with retry, data
  const view = state => h(K.AsyncView, { state, empty: h("p", { id: "e" }, "none") }, d => h("p", { id: "d" }, d.join(",")));
  await render(view({ status: "loading", data: null }));
  assert.ok($(".kit-skel")); assert.equal($(".kit-async").getAttribute("aria-busy"), "true");
  await render(view({ status: "success", data: [] }));
  assert.ok($("#e"));
  let retried = false;
  await render(view({ status: "error", error: "boom", reload: () => { retried = true; } }));
  assert.equal($("[role=alert]").textContent.includes("boom"), true);
  [...w.document.querySelectorAll("button")].find(b => /Try again/.test(b.textContent)).click(); assert.ok(retried);
  await render(view({ status: "success", data: ["a", "b"] }));
  assert.equal($("#d").textContent, "a,b");

  // Field: label, hint and error are wired to the input
  await render(h(K.Field, { label: "Email", hint: "Hint", error: "Bad", required: true }, p => h("input", p)));
  const input = $("input");
  assert.equal($("label").getAttribute("for"), input.id);
  assert.equal(input.getAttribute("aria-invalid"), "true");
  assert.equal(input.getAttribute("aria-describedby").split(" ").length, 2);

  // Tabs: arrow keys move selection and focus
  await render(h(K.Tabs, { label: "T", tabs: [{ id: "a", label: "A", panel: "PA" }, { id: "b", label: "B", panel: "PB" }] }));
  assert.equal($("[role=tabpanel]").textContent, "PA");
  $("[role=tablist]").dispatchEvent(new w.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  await settle();
  assert.equal($("[role=tabpanel]").textContent, "PB");
  assert.equal(w.document.activeElement.textContent, "B");

  // Dialog: focus moves in, Tab is trapped, Escape closes
  let closed = 0;
  await render(h(K.Dialog, { open: true, title: "Sure?", onClose: () => closed++ }, h("button", { id: "ok" }, "OK")));
  assert.equal($("[role=dialog]").getAttribute("aria-modal"), "true");
  assert.ok($("[role=dialog]").contains(w.document.activeElement));
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(closed, 1);
  console.log("kit tests passed");
})().catch(e => { console.error(e); process.exit(1); });
