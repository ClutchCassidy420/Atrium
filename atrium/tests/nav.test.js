// The sidebar: top-level links, dropdown sections, ordering, remembered open state, and the phone drawer (jsdom).
// usage: NODE_PATH=<folder with node_modules for jsdom> node tests/nav.test.js
const fs = require("fs"), path = require("path"), assert = require("assert");
const { JSDOM } = require("jsdom");
const S = path.join(__dirname, "..", "app", "static");
const read = (f) => fs.readFileSync(path.join(S, f), "utf8");

const SHELL = `<!doctype html><body><div class="vn-shell" id="vn-shell"><aside id="vn-side"><nav id="vn-nav"></nav></aside>
<div id="vn-scrim"></div><button id="vn-close"></button><button id="vn-menu" aria-expanded="false"></button><main id="vn-root"></main></div>`;

function boot(url, pages, navGroups, storage) {
  const dom = new JSDOM(SHELL, { url, runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  if (storage) for (const k of Object.keys(storage)) w.localStorage.setItem(k, storage[k]);
  w.fetch = () => Promise.resolve(new Response("{}"));
  w.__APP_CONFIG__ = { brand: "Test", agentName: "Robo", user: "alice", role: "owner", navGroups, pages };
  w.eval(read("vendor/react.production.min.js"));
  w.eval(read("vendor/react-dom.production.min.js"));
  w.eval(read("shim.js"));
  return w;
}
const P = (id, label, group, order) => ({ id, path: id === "home" ? "/" : "/" + id, label, plugin: id, group: group || "", order, scripts: [], styles: [] });
const PAGES = [P("home", "Home", "", 0), P("chat", "Chat", "Agent", 10), P("memory", "Memory", "Agent", 5), P("systems", "Systems", "Server", 10),
  P("logs", "Logs", "Server", 20), P("docs", "Docs", "", 50), P("zeta", "Zeta", "Zeta", 1)];

(async () => {
  // 1. structure and order: top-level links first (by order), then sections in the configured order, others alphabetical
  let w = boot("https://app.test/", PAGES, ["Server", "Agent"]);
  await new Promise((r) => setTimeout(r, 50));
  let d = w.document;
  const top = [...d.querySelectorAll("#vn-nav > a")].map((a) => a.textContent);
  assert.strictEqual(top.join(","), "Home,Docs");
  const sections = [...d.querySelectorAll(".vn-group-btn span:first-child")].map((s) => s.textContent);
  assert.strictEqual(sections.join(","), "Server,Agent,Zeta", "configured order first, then the rest");
  const agent = [...d.querySelectorAll(".vn-group")][1];
  assert.strictEqual([...agent.querySelectorAll(".vn-sub a")].map((a) => a.textContent).join(","), "Memory,Chat", "sorted by order inside a section");

  // 2. closed by default; the button opens and closes it and says so for screen readers
  const btn = agent.querySelector(".vn-group-btn");
  assert.strictEqual(btn.getAttribute("aria-expanded"), "false");
  assert(!agent.classList.contains("vn-open"));
  btn.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert.strictEqual(btn.getAttribute("aria-expanded"), "true");
  assert(agent.classList.contains("vn-open"));
  assert.strictEqual(btn.getAttribute("aria-controls"), agent.querySelector(".vn-sub").id);
  assert.deepStrictEqual(JSON.parse(w.localStorage.getItem("nav.open")), { Agent: true });
  btn.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert.strictEqual(btn.getAttribute("aria-expanded"), "false");
  w.close();

  // 3. the section holding the current page opens by itself and the page is marked; a remembered open section stays open
  w = boot("https://app.test/systems", PAGES, ["Server", "Agent"], { "nav.open": JSON.stringify({ Agent: true }) });
  await new Promise((r) => setTimeout(r, 50));
  d = w.document;
  const groups = [...d.querySelectorAll(".vn-group")];
  assert(groups[0].classList.contains("vn-open"), "Server holds the current page, so it opens");
  assert(groups[1].classList.contains("vn-open"), "Agent was left open last time");
  assert(!groups[2].classList.contains("vn-open"));
  assert.strictEqual(d.querySelector('a[aria-current="page"]').textContent, "Systems");

  // 4. the phone drawer: menu opens it, Escape and the close button and the scrim close it
  const shell = d.getElementById("vn-shell"), menu = d.getElementById("vn-menu");
  menu.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert(shell.classList.contains("vn-drawer-open"));
  assert.strictEqual(menu.getAttribute("aria-expanded"), "true");
  d.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert(!shell.classList.contains("vn-drawer-open"));
  menu.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  d.getElementById("vn-close").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert(!shell.classList.contains("vn-drawer-open"));
  menu.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  d.getElementById("vn-scrim").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert(!shell.classList.contains("vn-drawer-open"));
  w.close();

  // 5. no sections at all: a plain list; and blocked storage must not break anything
  w = boot("https://app.test/", [P("home", "Home", "", 0), P("docs", "Docs", "", 1)], []);
  Object.defineProperty(w, "localStorage", { get() { throw new Error("blocked"); } });
  await new Promise((r) => setTimeout(r, 50));
  assert.strictEqual([...w.document.querySelectorAll("#vn-nav a")].length, 2);
  assert.strictEqual(w.document.querySelectorAll(".vn-group").length, 0);
  w.close();

  // 6. a folder inside a section: "Chat" holds three pages, opens on its own, and opens by itself on one of its pages
  const SUB = (id, label, order) => Object.assign(P(id, label, "Agent", order), { subgroup: "Chat" });
  const FP = [P("home", "Home", "", 0), SUB("chat", "Casual chat", 10), SUB("code", "Agentic coding", 11), SUB("live", "Live agent", 12), P("memory", "Memory", "Agent", 20)];
  w = boot("https://app.test/", FP, ["Agent"]);
  await new Promise((r) => setTimeout(r, 50));
  d = w.document;
  const folder = d.querySelector(".vn-folder");
  assert(folder, "the folder exists");
  assert.strictEqual(folder.querySelector(".vn-group-btn span").textContent, "Chat");
  assert.strictEqual([...folder.querySelectorAll("a")].map((a) => a.textContent).join(","), "Casual chat,Agentic coding,Live agent");
  assert.strictEqual([...d.querySelectorAll(".vn-sub")][0].lastElementChild.textContent, "Memory", "the folder sits where its first page sorts, Memory after it");
  assert(!folder.classList.contains("vn-open"));
  folder.querySelector(".vn-group-btn").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  assert(folder.classList.contains("vn-open") && folder.querySelector(".vn-group-btn").getAttribute("aria-expanded") === "true");
  assert.strictEqual(JSON.parse(w.localStorage.getItem("nav.open"))["Agent/Chat"], true);
  w.close();
  w = boot("https://app.test/live", FP, ["Agent"]);
  await new Promise((r) => setTimeout(r, 50));
  assert(w.document.querySelector(".vn-folder").classList.contains("vn-open") && w.document.querySelector(".vn-group").classList.contains("vn-open"), "on a page inside the folder, both open");
  console.log("nav OK: order, sections, aria, remembered state, drawer, folders, no-section and blocked-storage cases");
  w.close();
})().catch((e) => { console.error("FAIL:", e.stack || e.message); process.exit(1); });
