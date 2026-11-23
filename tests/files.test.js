// Files page on the UI kit: loading, empty and error states. Synthetic only (no network).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "../app/static");
const settle = () => new Promise(resolve => setTimeout(resolve, 80));

async function files(fetchJSON) {
  const dom = new JSDOM('<main id="root"></main>', { url: "https://app.test/files", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window, load = f => w.eval(fs.readFileSync(path.join(ROOT, f), "utf8"));
  load("vendor/react.production.min.js"); load("vendor/react-dom.production.min.js");
  let Page;
  w.__HERMES_PLUGINS__ = { register: (_, c) => { Page = c; } };
  w.__HERMES_PLUGIN_SDK__ = { React: w.React, hooks: w.React, fetchJSON };
  ["pages/common.js", "pages/kit.js", "pages/files/index.js"].forEach(load);
  w.ReactDOM.createRoot(w.document.getElementById("root")).render(w.React.createElement(Page));
  return w;
}

(async () => {
  // loading: skeleton while the request is pending
  let w = await files(() => new Promise(() => {}));
  await settle();
  assert.ok(w.document.querySelector(".kit-skel"));

  // empty folder: kit empty state with an Upload action
  w = await files(async () => ({ items: [], url: "" }));
  await settle();
  assert.match(w.document.querySelector(".kit-empty-title").textContent, /empty/);

  // failure: danger alert with a working retry
  let calls = 0;
  w = await files(async () => { calls++; if (calls === 1) throw new Error("500: nope"); return { items: [], url: "" }; });
  await settle();
  assert.ok(w.document.querySelector("[role=alert]"));
  [...w.document.querySelectorAll("button")].find(b => /Try again/.test(b.textContent)).click();
  await settle();
  assert.ok(w.document.querySelector(".kit-empty"));
  console.log("files tests passed");
})().catch(e => { console.error(e); process.exit(1); });
