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

  // Switch: role=switch, label names it, click reports the new value
  let sw = null;
  await render(h(K.Switch, { checked: false, label: "Alerts", onChange: v => { sw = v; } }));
  assert.equal($("[role=switch]").getAttribute("aria-checked"), "false");
  assert.equal($("label").getAttribute("for"), $("[role=switch]").id);
  $("[role=switch]").click(); assert.equal(sw, true);

  // DataTable: skeleton rows, empty state, aria-sort and sort intent, error with retry
  const cols = [{ key: "name", label: "Name", sortable: true }, { key: "n", label: "Size", align: "right" }];
  await render(h(K.DataTable, { caption: "Files", columns: cols, rows: [], loading: true }));
  assert.ok($(".kit-skel")); assert.equal(w.document.querySelectorAll("tbody tr").length, 5);
  await render(h(K.DataTable, { caption: "Files", columns: cols, rows: [], empty: h("p", { id: "none" }, "no files") }));
  assert.ok($("#none"));
  let sorted = null;
  await render(h(K.DataTable, { caption: "Files", columns: cols, rows: [{ id: 1, name: "a", n: 2 }], sort: { key: "name", dir: "asc" }, onSort: (k, d) => { sorted = [k, d]; } }));
  assert.equal($("th[aria-sort]").getAttribute("aria-sort"), "ascending");
  assert.equal($("tbody th").textContent, "a");
  $(".kit-th-btn").click(); assert.deepEqual(sorted, ["name", "desc"]);
  let retry = false;
  await render(h(K.DataTable, { columns: cols, rows: [], error: "down", onRetry: () => { retry = true; } }));
  assert.ok($("[role=alert]").textContent.includes("down"));
  [...w.document.querySelectorAll("button")].find(b => /Try again/.test(b.textContent)).click(); assert.ok(retry);

  // Pagination: hidden for one page, current page marked, ellipsis for gaps, onChange
  await render(h(K.Pagination, { page: 1, pageCount: 1 }));
  assert.equal($("nav"), null);
  let pg = 0;
  await render(h(K.Pagination, { page: 5, pageCount: 20, onChange: v => { pg = v; } }));
  assert.equal($("[aria-current=page]").textContent, "5");
  assert.equal(w.document.querySelectorAll(".kit-pager-gap").length, 2);
  $("[aria-label='Next page']").click(); assert.equal(pg, 6);

  // SearchInput: debounced, not on first render, clear fires immediately
  const seen = [];
  await render(h(K.SearchInput, { label: "Find", delay: 20, onSearch: q => seen.push(q) }));
  assert.deepEqual(seen, []);
  const setVal = (el, v) => { Object.getOwnPropertyDescriptor(w.HTMLInputElement.prototype, "value").set.call(el, v); el.dispatchEvent(new w.Event("input", { bubbles: true })); };
  setVal($("input"), "ab"); await settle();
  assert.deepEqual(seen, ["ab"]);
  $("[aria-label='Clear search']").click(); assert.equal(seen[seen.length - 1], "");

  // Toasts: polite region, danger is an alert and stays, dismiss works, auto-dismiss clears
  let api;
  const Grab = () => { api = K.useToast(); return null; };
  await render(h(K.ToastProvider, null, h(Grab)));
  api.push("Saved", { tone: "success", duration: 30 }); const eid = api.push("Broke", { tone: "danger" }); await settle();
  assert.equal($("[role=region][aria-live=polite]") !== null, true);
  assert.equal(w.document.querySelectorAll(".kit-toast").length, 1); // "Saved" auto-dismissed
  assert.equal($(".kit-toast").getAttribute("role"), "alert");
  $(".kit-toast .kit-alert-x").click(); await settle();
  assert.equal(w.document.querySelectorAll(".kit-toast").length, 0);

  // ConfirmDialog: focus starts on Cancel, failure stays open with the message, success closes
  let shut = 0, fail = true;
  await render(h(K.ConfirmDialog, { open: true, title: "Delete?", danger: true, confirmLabel: "Delete", onClose: () => shut++, onConfirm: () => fail ? Promise.reject(new Error("nope")) : Promise.resolve() }, "Sure?"));
  assert.equal($("[role=alertdialog]") !== null, true);
  assert.equal(w.document.activeElement.textContent, "Cancel");
  const del = () => [...w.document.querySelectorAll("button")].find(b => b.textContent === "Delete");
  del().click(); await settle();
  assert.ok($("[role=alertdialog]").textContent.includes("nope")); assert.equal(shut, 0);
  fail = false; del().click(); await settle(); assert.equal(shut, 1);

  // Dialog: focus moves in, Tab is trapped, Escape closes
  let closed = 0;
  await render(h(K.Dialog, { open: true, title: "Sure?", onClose: () => closed++ }, h("button", { id: "ok" }, "OK")));
  assert.equal($("[role=dialog]").getAttribute("aria-modal"), "true");
  assert.ok($("[role=dialog]").contains(w.document.activeElement));
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(closed, 1);
  console.log("kit tests passed");
})().catch(e => { console.error(e); process.exit(1); });
