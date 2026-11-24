// Chat: emoji reactions. A person clicks; the agent writes [[react: 👍]] in a reply and it shows on the message before. Synthetic only.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "../app/static");
const settle = (ms = 150) => new Promise(resolve => setTimeout(resolve, ms));
const SID = "atrium-chat-abcdef123";

(async () => {
  const dom = new JSDOM('<main id="root"></main>', { url: "https://app.test/chat", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window, load = f => w.eval(fs.readFileSync(path.join(ROOT, f), "utf8"));
  w.HTMLElement.prototype.scrollIntoView = function () {};
  const noop = () => {};
  w.HTMLCanvasElement.prototype.getContext = () => new Proxy({ canvas: {}, measureText: () => ({ width: 0 }), createLinearGradient: () => ({ addColorStop: noop }), createRadialGradient: () => ({ addColorStop: noop }) }, { get: (t, k) => (k in t ? t[k] : noop), set: () => true });
  w.requestAnimationFrame = () => 0; w.cancelAnimationFrame = noop;
  w.matchMedia = w.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
  load("vendor/react.production.min.js"); load("vendor/react-dom.production.min.js");
  const calls = [], pages = {};
  let mine = {};
  w.__HERMES_PLUGINS__ = { register: (n, c) => { pages[n] = c; }, registerSlot() {} };
  w.__HERMES_PLUGIN_SDK__ = {
    React: w.React, hooks: w.React,
    fetchJSON: async (url, o) => {
      o = o || {};
      calls.push([o.method || "GET", url, o.body ? JSON.parse(o.body) : null]);
      if (url.startsWith("/api/sessions?")) return { sessions: [{ id: SID, title: "Greetings", message_count: 4, last_active: Date.now() / 1000, source: "atrium" }], total: 1 };
      if (url.includes("/messages")) return { messages: [
        { role: "user", content: "You did great today" },
        { role: "assistant", content: "Thank you!\n[[react: ❤️ 🎉]]" },
        { role: "user", content: "second question" },
        { role: "assistant", content: "An answer." }] };
      if (url.endsWith("/reactions/" + SID) && o.method === "POST") {
        const b = JSON.parse(o.body), key = String(b.index);
        const row = (mine[key] || []).filter(r => r.emoji === b.emoji)[0];
        if (row) mine[key] = mine[key].filter(r => r !== row); else mine[key] = (mine[key] || []).concat([{ emoji: b.emoji, count: 1, mine: true }]);
        if (!mine[key].length) delete mine[key];
        return { reactions: JSON.parse(JSON.stringify(mine)) };
      }
      if (url.endsWith("/reactions/" + SID)) return { reactions: mine };
      if (url.endsWith("/status")) return { ready: true };
      if (url === "/api/auth/me") return { user: "bob", display_name: "Bob", role: "user" };
      return {};
    },
  };
  w.__APP_CONFIG__ = { brand: "", agentName: "Robo", name: "", user: "bob" };
  w.atriumEvery = () => () => {};
  const scripts = JSON.parse(fs.readFileSync(path.join(__dirname, "../app/pages.json"), "utf8")).filter(p => p.id === "chat")[0].scripts;
  scripts.forEach(s => load(s.replace(/^\/static\//, "")));
  w.ReactDOM.createRoot(w.document.getElementById("root")).render(w.React.createElement(pages["atrium-chat"]));
  await settle(400);

  const text = () => w.document.body.textContent;
  const click = el => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const item = [...w.document.querySelectorAll(".atc-chatitem button, .atc-chatitem")].find(b => /Greetings/.test(b.textContent));
  assert.ok(item, "the past chat is listed");
  click(item.querySelector("button") || item);
  await settle(400);

  // the marker is never shown; the agent's reaction sits on the message before it, marked as the agent's
  assert.match(text(), /Thank you!/);
  assert.ok(!/\[\[react/.test(text()), "the marker is hidden");
  const agentChips = [...w.document.querySelectorAll(".atc-chip-agent")].map(c => c.textContent);
  assert.equal(agentChips.length, 2);
  assert.match(agentChips[0], /❤️/); assert.match(agentChips[1], /🎉/);
  const firstUser = w.document.querySelectorAll(".atc-msg-user")[0];
  assert.ok(firstUser.querySelector(".atc-chip-agent"), "it is on the first user message");
  assert.ok(!w.document.querySelectorAll(".atc-msg-user")[1].querySelector(".atc-chip-agent"));

  // a person adds a reaction from the picker, then clicks it to take it back
  click(w.document.querySelectorAll(".atc-msg-agent")[1].querySelector(".atc-chip-add"));
  await settle();
  const pick = [...w.document.querySelectorAll(".atc-pick")].find(b => b.textContent === "👍");
  assert.ok(pick, "the picker offers 👍");
  click(pick);
  await settle(250);
  const post = calls.filter(c => c[0] === "POST" && c[1].endsWith("/reactions/" + SID));
  assert.deepEqual(post[0][2], { index: 3, emoji: "👍" });
  const chip = w.document.querySelectorAll(".atc-msg-agent")[1].querySelector(".atc-chip-mine");
  assert.ok(chip, "the reaction shows as yours");
  click(chip);
  await settle(250);
  assert.equal(w.document.querySelectorAll(".atc-chip-mine").length, 0);
  assert.equal(calls.filter(c => c[0] === "POST").length >= 2, true);

  console.log("chat reactions tests passed");
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
