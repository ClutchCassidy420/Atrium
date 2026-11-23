// Runs the shim and the self-test plugin in a simulated browser (jsdom) and checks the plugin contract.
// usage: NODE_PATH=<folder with node_modules for jsdom> node tests/shim.test.js
const fs = require("fs"), path = require("path"), assert = require("assert");
const { JSDOM } = require("jsdom");
const S = path.join(__dirname, "..", "app", "static");
const read = (f) => fs.readFileSync(path.join(S, f), "utf8");

const config = { brand: "Test Hub", agentName: "Robo", user: "alice", role: "owner", pages: [{ id: "selftest", path: "/selftest", plugin: "selftest", label: "Self-test", scripts: [], styles: [] }] };
const dom = new JSDOM('<!doctype html><body><nav id="vn-nav"></nav><main id="vn-root"></main></body>',
  { url: "https://app.test/selftest", runScripts: "outside-only", pretendToBeVisual: true });
const w = dom.window;
const calls = [];
w.fetch = (url, init) => {
  calls.push([url, init]);
  if (url === "/api/me") return Promise.resolve(new Response(JSON.stringify({ role: "owner" }), { status: 200 }));
  if (url === "/api/empty") return Promise.resolve(new Response("", { status: 200 }));
  if (url === "/api/auth-gone") return Promise.resolve(new Response("no", { status: 401 }));
  return Promise.resolve(new Response("missing", { status: 404 }));
};
w.__APP_CONFIG__ = config;
w.eval(read("vendor/react.production.min.js"));
w.eval(read("vendor/react-dom.production.min.js"));
w.eval(read("shim.js"));

(async () => {
  const SDK = w.__HERMES_PLUGIN_SDK__, REG = w.__HERMES_PLUGINS__;
  // the contract the Atrium plugins start from
  assert(SDK && REG, "globals exist");
  assert.strictEqual(typeof SDK.React.createElement, "function");
  for (const k of ["useState", "useEffect", "useRef", "useCallback"]) assert.strictEqual(typeof SDK.hooks[k], "function", k);
  assert.strictEqual(typeof REG.register, "function");
  assert.strictEqual(typeof REG.registerSlot, "function");
  assert.strictEqual(typeof w.__HERMES_SESSION_TOKEN__, "string");

  // fetchJSON behaves like the dashboard's
  assert.strictEqual(JSON.stringify(await SDK.fetchJSON("/api/me")), '{"role":"owner"}');
  assert.strictEqual(await SDK.fetchJSON("/api/empty"), null);
  await assert.rejects(SDK.fetchJSON("/api/missing"), /^Error: 404: missing$/);
  assert.strictEqual(calls.find((c) => c[0] === "/api/me")[1].credentials, "same-origin");
  await assert.rejects(SDK.fetchJSON("/api/auth-gone", {}, { allowUnauthorized: true }), /401: no/);
  const raw = await SDK.authedFetch("/api/missing");
  assert.strictEqual(raw.status, 404);          // authedFetch does not throw on non-2xx

  // load the self-test plugin script (like the real ones: starts from the two globals) and mount it
  w.eval(read("plugins/selftest/index.js"));
  await new Promise((r) => setTimeout(r, 400));
  const doc = w.document;
  assert(doc.getElementById("selftest"), "the plugin page rendered");
  assert.strictEqual(doc.getElementById("selftest").getAttribute("data-mounted"), "yes", "useEffect and useRef ran");
  assert(/role: owner/.test(doc.getElementById("st-me").textContent), "fetchJSON result shown: " + doc.getElementById("st-me").textContent);
  assert(/threw as expected: 404/.test(doc.getElementById("st-bad").textContent), "error path shown: " + doc.getElementById("st-bad").textContent);
  doc.getElementById("st-btn").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 100));
  assert(/Clicked 1 times/.test(doc.getElementById("st-btn").textContent), "state update after a click");
  const links = [...doc.querySelectorAll("#vn-nav a")].map((a) => a.querySelector(".vn-lt").textContent);
  assert.strictEqual(links.join(","), "Home,Self-test");
  assert.strictEqual(doc.querySelector('#vn-nav a[aria-current="page"] .vn-lt').textContent, "Self-test");
  // atriumEvery: polls like setInterval, pauses while the tab is hidden, catches up once on return, and stops cleanly
  assert.strictEqual(typeof w.atriumEvery, "function");
  let hidden = false;
  Object.defineProperty(w.document, "hidden", { configurable: true, get: () => hidden });
  let ticks = 0;
  const stop = w.atriumEvery(() => { ticks++; }, 20);
  await new Promise((r) => setTimeout(r, 110));
  assert(ticks >= 3, "it ticks while the tab is visible: " + ticks);
  hidden = true;
  await new Promise((r) => setTimeout(r, 30));          // let any tick already in flight finish
  const before = ticks;
  await new Promise((r) => setTimeout(r, 110));
  assert.strictEqual(ticks, before, "no ticks while the tab is hidden");
  hidden = false;
  w.document.dispatchEvent(new w.Event("visibilitychange"));
  assert.strictEqual(ticks, before + 1, "one catch-up tick when the tab comes back");
  w.document.dispatchEvent(new w.Event("visibilitychange"));
  assert.strictEqual(ticks, before + 1, "no second catch-up when nothing was missed");
  stop();
  const stopped = ticks;
  await new Promise((r) => setTimeout(r, 80));
  hidden = true; w.document.dispatchEvent(new w.Event("visibilitychange")); hidden = false; w.document.dispatchEvent(new w.Event("visibilitychange"));
  assert.strictEqual(ticks, stopped, "stop() ends the timer and the listener");
  console.log("shim OK: contract, fetchJSON, hooks, mount, click, nav, background-friendly polling");
  w.close();
})().catch((e) => { console.error("FAIL:", e.message); process.exit(1); });
