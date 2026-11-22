// Install as an app from any page (jsdom). usage: NODE_PATH=<folder with node_modules for jsdom> node tests/install.test.js
const fs = require("fs"), path = require("path"), assert = require("assert");
const { JSDOM } = require("jsdom");
const S = path.join(__dirname, "..", "app", "static");
const read = (f) => fs.readFileSync(path.join(S, f), "utf8");
const SHELL = '<!doctype html><body><div id="vn-shell"><aside><nav id="vn-nav"></nav><div id="vn-install"></div></aside><main id="vn-root"></main></div>';
const PWA = { manifest: "/p/manifest.webmanifest", sw: "/p/sw.js", icon: "/p/icon-192.png" };

async function boot({ pwa, ua, standalone }) {
  const dom = new JSDOM(SHELL, { url: "https://app.test/", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window, registered = [];
  Object.defineProperty(w.navigator, "userAgent", { value: ua || "Mozilla/5.0 (Windows NT 10.0) Chrome/150" });
  w.fetch = () => Promise.resolve(new Response("{}"));
  w.matchMedia = (q) => ({ matches: !!standalone && /standalone/.test(q), addEventListener() {}, removeEventListener() {} });
  Object.defineProperty(w.navigator, "serviceWorker", { value: { register: (url, o) => { registered.push([url, o]); return Promise.resolve({}); } } });
  w.__APP_CONFIG__ = { brand: "T", agentName: "R", user: "a", role: "owner", pwa, pages: [{ id: "home", path: "/", label: "Home", plugin: "home", scripts: [], styles: [] }] };
  w.eval(read("vendor/react.production.min.js"));
  w.eval(read("vendor/react-dom.production.min.js"));
  w.eval(read("shim.js"));
  await new Promise((r) => setTimeout(r, 60));      // the page script starts once the document has finished loading
  return { w, registered };
}
const offer = (w) => { const e = new w.Event("beforeinstallprompt", { cancelable: true }); e.prompted = 0; e.prompt = () => { e.prompted++; }; w.dispatchEvent(e); return e; };

(async () => {
  // 1. the phone app is on: the manifest is linked, the service worker registered, and the browser's offer becomes a button
  let { w, registered } = await boot({ pwa: PWA });
  let d = w.document;
  assert.strictEqual(d.querySelector("link[rel=manifest]").getAttribute("href"), PWA.manifest);
  assert.strictEqual(d.querySelector("link[rel=manifest]").crossOrigin, "use-credentials");
  assert.strictEqual(d.querySelector("link[rel=apple-touch-icon]").getAttribute("href"), PWA.icon);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(registered)), [[PWA.sw, { scope: "/" }]]);
  assert.strictEqual(d.querySelector("#vn-install button"), null, "no button until the browser offers installation");
  const ev = offer(w);
  assert.strictEqual(ev.defaultPrevented, true, "the browser's own mini-bar is held back so the button can use it");
  const btn = d.querySelector("#vn-install button");
  assert(btn && btn.textContent === "Install app");
  btn.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert.strictEqual(ev.prompted, 1, "the click shows the browser's install dialog");
  assert.strictEqual(d.querySelector("#vn-install button"), null, "the button goes away once used");
  w.close();

  // 2. installed: the button disappears
  ({ w } = await boot({ pwa: PWA })); offer(w);
  assert(w.document.querySelector("#vn-install button"));
  w.dispatchEvent(new w.Event("appinstalled"));
  assert.strictEqual(w.document.querySelector("#vn-install button"), null);
  w.close();

  // 3. already running as an app: nothing to offer
  ({ w } = await boot({ pwa: PWA, standalone: true })); offer(w);
  assert.strictEqual(w.document.querySelector("#vn-install button"), null);
  w.close();

  // 4. iPhone: no browser offer exists, so there is a hint instead
  ({ w } = await boot({ pwa: PWA, ua: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari" }));
  assert(/Add to Home Screen/.test(w.document.querySelector("#vn-install .vn-hint").textContent));
  w.close();

  // 5. the phone app is off: the page is left alone
  ({ w, registered } = await boot({ pwa: null }));
  assert.strictEqual(w.document.querySelector("link[rel=manifest]"), null);
  assert.strictEqual(registered.length, 0);
  offer(w);
  assert.strictEqual(w.document.querySelector("#vn-install button"), null);
  w.close();
  console.log("install OK: links, registration, offer to button, installed, standalone, iPhone hint, off");
})().catch((e) => { console.error("FAIL:", e.stack || e.message); process.exit(1); });
