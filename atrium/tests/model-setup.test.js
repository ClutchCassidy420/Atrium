const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

async function check() {
  const dom = new JSDOM('<main id="root"></main>', {
    url: "https://app.test/settings#model", runScripts: "outside-only", pretendToBeVisual: true
  });
  const w = dom.window;
  const root = path.join(__dirname, "../app/static");
  function load(file) { w.eval(fs.readFileSync(path.join(root, file), "utf8")); }
  load("vendor/react.production.min.js");
  load("vendor/react-dom.production.min.js");
  const requests = [];
  let Page;
  w.__HERMES_PLUGINS__ = { register: (_, page) => { Page = page; } };
  w.__HERMES_PLUGIN_SDK__ = { React: w.React, hooks: w.React, fetchJSON: async (url, options) => {
    if (url === "/api/host/status") return {enabled: false};
    assert.equal(url, "/api/plugins/atrium-settings/model");
    if (!options) return {model: "previous"};
    requests.push(JSON.parse(options.body));
    return requests.length === 1 ? {confirm_required: true} : {ok: true};
  }};
  w.confirm = () => true;
  load("pages/settings/index.js");
  const d = w.document;
  const settle = () => new Promise(resolve => setTimeout(resolve, 60));
  try {
    w.ReactDOM.createRoot(d.querySelector("main")).render(w.React.createElement(Page));
    await settle();
    assert.equal(d.querySelector("h2").textContent, "Chat model");
    const form = d.querySelector("form");
    form.elements.base_url.value = "https://example.invalid/v1";
    form.elements.model.value = "example";
    form.elements.api_key.value = "synthetic";
    form.dispatchEvent(new w.Event("submit", {bubbles: true, cancelable: true}));
    await settle();
    assert.equal(requests.length, 2);
    assert.equal(requests[0].api_key, "synthetic");
    assert.equal(requests[1].confirm, true);
    assert.equal(form.elements.api_key.value, "");
    assert.match(d.querySelector('[role="status"]').textContent, /Saved example/);
  } finally { w.close(); }
}
check().catch(err => { console.error(err); process.exitCode = 1; });
