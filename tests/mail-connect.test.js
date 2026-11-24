// Email pages with personal mailboxes: the connect form, a failed connect, a connected mailbox, and Disconnect. Synthetic only (no network).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "../app/static");
const settle = (ms = 100) => new Promise(resolve => setTimeout(resolve, ms));
const PRESETS = [{ id: "gmail", label: "Gmail", hint: "Create an App password in your Google Account." }, { id: "fastmail", label: "Fastmail", hint: "Use an app password." }];

async function open(handler) {
  const dom = new JSDOM('<main id="root"></main>', { url: "https://app.test/email/inbox", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window, load = f => w.eval(fs.readFileSync(path.join(ROOT, f), "utf8"));
  load("vendor/react.production.min.js"); load("vendor/react-dom.production.min.js");
  const calls = [], pages = {};
  w.__HERMES_PLUGINS__ = { register: (n, c) => { pages[n] = c; }, registerSlot() {} };
  w.__HERMES_PLUGIN_SDK__ = { React: w.React, hooks: w.React, fetchJSON: async (url, o) => { calls.push([(o && o.method) || "GET", url, o && o.body ? JSON.parse(o.body) : null]); return handler(url, o || {}); } };
  w.__APP_CONFIG__ = { brand: "", agentName: "Robo", name: "", user: "" };
  w.atriumEvery = () => () => {};
  w.confirm = () => true;
  ["pages/common.js", "pages/kit.js", "pages/mail/index.js"].forEach(load);
  w.ReactDOM.createRoot(w.document.getElementById("root")).render(w.React.createElement(pages["mail-inbox"]));
  await settle(150);
  return { w, calls };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const btn = (w, re) => [...w.document.querySelectorAll("button")].find(b => re.test(b.textContent));
function setValue(w, el, value) {
  const proto = el.tagName === "SELECT" ? w.HTMLSelectElement.prototype : w.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
  el.dispatchEvent(new w.Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
}

(async () => {
  let connected = false, fail = false;
  const handler = async (url, o) => {
    if (url === "/api/mail/status") return connected ? { connected: true, account: "personal", address: "bob@gmail.com", webmail: "", features: [], user_accounts: true } : { connected: false, account: null, user_accounts: true, features: [], presets: PRESETS };
    if (url === "/api/mail/account" && o.method === "PUT") { if (fail) throw new Error('502: {"detail":"The mail server did not accept the sign-in."}'); connected = true; return { personal: { address: "bob@gmail.com" } }; }
    if (url === "/api/mail/account" && o.method === "DELETE") { connected = false; return { ok: true }; }
    if (url === "/api/mail/folders") return { folders: [{ name: "INBOX", role: "inbox", total: 0, unseen: 0 }], address: "bob@gmail.com" };
    if (url.startsWith("/api/mail/messages")) return { items: [], total: 0, page: 0, pages: 1 };
    return {};
  };
  const { w, calls } = await open(handler);
  const $ = s => w.document.querySelector(s);
  const text = () => w.document.body.textContent;

  // nobody connected yet: the form, with the provider's hint and no server fields
  assert.match(text(), /Connect your email/);
  assert.match(text(), /Create an App password in your Google Account/);
  assert.ok(!/Incoming server/.test(text()));
  assert.equal($("input[type=password]").autocomplete, "off");

  // choosing Other asks for the two servers
  setValue(w, $("select"), "");
  await settle(40);
  assert.match(text(), /Incoming server \(IMAP\)/);
  assert.match(text(), /Outgoing server \(SMTP\)/);
  setValue(w, $("select"), "gmail");
  await settle(40);

  // a refused sign-in shows the reason, keeps the form, and sends nothing but the typed fields
  fail = true;
  setValue(w, $("input[type=email]"), "bob@gmail.com");
  setValue(w, $("input[type=password]"), "app-pass-123");
  w.document.querySelector("form").dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
  await settle(120);
  assert.match(text(), /did not accept the sign-in/);
  assert.match(text(), /Connect your email/);
  assert.deepEqual(calls.find(c => c[0] === "PUT").slice(1), ["/api/mail/account", { address: "bob@gmail.com", password: "app-pass-123", preset: "gmail" }]);

  // a good one switches to the mailbox, which says whose it is and offers Disconnect
  fail = false;
  w.document.querySelector("form").dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
  await settle(250);
  assert.ok(!/Connect your email/.test(text()), "the form is gone");
  assert.match(text(), /Signed in as bob@gmail\.com/);
  assert.ok(btn(w, /^Disconnect$/));

  // disconnecting returns to the form
  click(w, btn(w, /^Disconnect$/));
  await settle(250);
  assert.deepEqual(calls.filter(c => c[0] === "DELETE").map(c => c[1]), ["/api/mail/account"]);
  assert.match(text(), /Connect your email/);

  // the server-wide mailbox (an owner's) shows no Disconnect and no form
  const owner = await open(async (url, o) => url === "/api/mail/status" ? { connected: true, account: "server", address: "owner@server.test", webmail: "", features: [], user_accounts: true } : handler(url, o));
  assert.match(owner.w.document.body.textContent, /Signed in as owner@server\.test/);
  assert.ok(!btn(owner.w, /^Disconnect$/));

  console.log("mail-connect tests passed");
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
