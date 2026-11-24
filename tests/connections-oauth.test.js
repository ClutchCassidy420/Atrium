// Settings > Connections: signing in to OAuth tools from Atrium (start, open the provider page, watch, finish, cancel, errors). Synthetic only (no network).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "../app/static");
const settle = (ms = 100) => new Promise(resolve => setTimeout(resolve, ms));
const BASE = "/api/plugins/atrium-settings/connections";
const CATALOG = { entries: [
  { name: "plain", description: "no login", installed: false, blocked: "", oauth: false, host: "a.invalid", fields: [] },
  { name: "signin", description: "needs a login", installed: false, blocked: "", oauth: true, host: "b.invalid", fields: [] },
  { name: "needsapp", description: "app first", installed: false, blocked: "This connection needs an app registered with the provider first. Set it up in Hermes.", oauth: false, host: null, fields: [] },
] };

async function open(handler) {
  const dom = new JSDOM('<main id="root"></main>', { url: "https://app.test/settings#connections", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window, load = f => w.eval(fs.readFileSync(path.join(ROOT, f), "utf8"));
  load("vendor/react.production.min.js"); load("vendor/react-dom.production.min.js");
  const calls = [], opened = [];
  let Page;
  w.__HERMES_PLUGINS__ = { register: (_, c) => { Page = c; }, registerSlot() {} };
  w.__HERMES_PLUGIN_SDK__ = { React: w.React, hooks: w.React, fetchJSON: async (url, o) => { calls.push([(o && o.method) || "GET", url]); return handler(url, o || {}); } };
  w.__APP_CONFIG__ = { brand: "", agentName: "Robo", name: "", user: "" };
  w.atriumEvery = () => () => {};
  w.open = (...args) => { opened.push(args); return null; };
  const realSet = w.setInterval; w.setInterval = (fn, ms) => realSet(fn, 30);        // poll fast
  ["pages/common.js", "pages/kit.js", "pages/settings/index.js"].forEach(load);
  w.ReactDOM.createRoot(w.document.getElementById("root")).render(w.React.createElement(Page));
  await settle(150);
  return { w, calls, opened };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const btn = (w, re) => [...w.document.querySelectorAll("button, a")].find(b => re.test(b.textContent));

function hermes({ pollsToApprove = 2, startFails = false } = {}) {
  let polls = 0, approved = false;
  return async (url, o) => {
    if (url === "/api/host/status") return { enabled: false };
    if (url === BASE + "/catalog") return CATALOG;
    if (url === BASE + "/installed") return { servers: approved ? [{ name: "signin", enabled: true, managed: false }] : [] };
    if (url === BASE + "/signin/oauth/start") { if (startFails) throw new Error('502: {"detail":"Hermes could not complete the MCP request."}'); return { flow_id: "f1", status: "authorization_required", authorization_url: "https://provider.invalid/authorize", error: null, tools: [] }; }
    if (url === BASE + "/oauth/flows/f1" && o.method === "DELETE") return { ok: true };
    if (url === BASE + "/oauth/flows/f1") { polls += 1; if (polls >= pollsToApprove) approved = true; return polls >= pollsToApprove ? { flow_id: "f1", status: "approved", authorization_url: null, error: null, tools: ["search", "create"] } : { flow_id: "f1", status: "authorization_required", authorization_url: "https://provider.invalid/authorize", error: null, tools: [] }; }
    return {};
  };
}

(async () => {
  // OAuth entries get a Sign in button, plain ones the usual Connect form, blocked ones their reason
  let { w, calls, opened } = await open(hermes());
  const text = () => w.document.body.textContent;
  assert.ok(btn(w, /^Sign in$/), "sign in button");
  assert.equal([...w.document.querySelectorAll("button")].filter(b => /^Sign in$/.test(b.textContent)).length, 1);
  assert.ok(btn(w, /^Connect$/), "plain entry keeps its Connect button");
  assert.match(text(), /needs an app registered with the provider/);

  // sign in: opens the provider page, shows the link, then finishes by itself and lists the tools
  click(w, btn(w, /^Sign in$/));
  await settle(60);
  assert.deepEqual(calls.find(c => c[1] === BASE + "/signin/oauth/start"), ["POST", BASE + "/signin/oauth/start"]);
  assert.equal(opened.length, 1);
  assert.equal(opened[0][0], "https://provider.invalid/authorize");
  assert.match(text(), /Signing in to signin/);
  assert.equal(btn(w, /Open the sign-in page/).getAttribute("href"), "https://provider.invalid/authorize");
  await settle(250);
  assert.ok(!/Signing in to signin/.test(text()), "the progress card is gone once approved");
  assert.match(text(), /signin connected/);
  assert.match(text(), /search/);

  // cancelling stops the flow on Hermes and clears the card
  ({ w, calls, opened } = await open(hermes({ pollsToApprove: 9999 })));
  click(w, btn(w, /^Sign in$/));
  await settle(60);
  click(w, btn(w, /^Cancel$/));
  await settle(60);
  assert.ok(calls.some(c => c[0] === "DELETE" && c[1] === BASE + "/oauth/flows/f1"));
  assert.ok(!/Signing in to signin/.test(w.document.body.textContent));

  // a failed start shows the reason and can be closed
  ({ w, calls, opened } = await open(hermes({ startFails: true })));
  click(w, btn(w, /^Sign in$/));
  await settle(80);
  assert.match(w.document.body.textContent, /Hermes could not complete the MCP request/);
  assert.equal(opened.length, 0);
  click(w, btn(w, /^Close$/));
  await settle(40);
  assert.ok(!/Signing in to signin/.test(w.document.body.textContent));

  console.log("connections-oauth tests passed");
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
