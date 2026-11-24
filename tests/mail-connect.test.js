// Email pages with personal mailboxes: the connect form, several mailboxes with a switcher, Disconnect, and the owner's view. Synthetic only (no network).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "../app/static");
const settle = (ms = 100) => new Promise(resolve => setTimeout(resolve, ms));
const PRESETS = [
  { id: "gmail", label: "Gmail", hint: "Create an App password in your Google Account.", help_url: "https://myaccount.google.com/apppasswords",
    steps: ["Turn on 2-Step Verification in your Google Account.", "Create an app password and name it Atrium.", "Paste the 16 characters here (spaces are fine)."] },
  { id: "fastmail", label: "Fastmail", hint: "Use an app password.", help_url: "https://app.fastmail.com/settings/security/devicekeys", steps: ["Open Password & Security in Fastmail and sign in.", "Create an app password named Atrium.", "Paste it here."] }];
const GET_ONE = /Don't have an app password\? Get one/;
const links = w => [...w.document.querySelectorAll("a")].filter(a => GET_ONE.test(a.textContent));

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
const submit = w => w.document.querySelector("form").dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));

// a tiny fake of the server: the mailboxes saved so far, and which one a request names
function fake({ server = false } = {}) {
  const mine = [];
  let nextId = 1, fail = false;
  const state = { mine, setFail: v => { fail = v; } };
  const handler = async (url, o) => {
    const [pathPart, query = ""] = url.split("?");
    const picked = new URLSearchParams(query).get("account") || "";
    const choices = (server ? [{ id: "server", address: "owner@server.test", kind: "server" }] : []).concat(mine.map(a => ({ id: a.id, address: a.address, kind: "personal" })));
    if (pathPart === "/api/mail/status") {
      const active = picked && choices.some(c => c.id === picked) ? picked : (choices[0] || {}).id;
      if (picked && !choices.some(c => c.id === picked)) throw new Error('404: {"detail":"That mailbox is not connected."}');
      if (!active) return { connected: false, account: null, accounts: [], user_accounts: true, features: [], presets: PRESETS };
      const c = choices.find(x => x.id === active);
      return { connected: true, account: c.kind, active, accounts: choices, address: c.address, webmail: "", features: c.kind === "server" ? ["blocklist"] : [], user_accounts: true, max_accounts: 5, presets: PRESETS };
    }
    if (pathPart === "/api/mail/account" && o.method === "PUT") {
      if (fail) throw new Error('502: {"detail":"' + (typeof fail === "string" ? fail : "The mail server did not accept the sign-in.") + '"}');
      const body = JSON.parse(o.body);
      const a = { id: "m" + nextId++, address: body.address };
      mine.push(a);
      return { account: a };
    }
    if (pathPart.startsWith("/api/mail/account/") && o.method === "DELETE") { const i = mine.findIndex(a => a.id === pathPart.split("/").pop()); mine.splice(i, 1); return { ok: true }; }
    if (pathPart === "/api/mail/folders") return { folders: [{ name: "INBOX", role: "inbox", total: 0, unseen: 0 }], address: (choices.find(c => c.id === (picked || (choices[0] || {}).id)) || {}).address };
    if (pathPart.startsWith("/api/mail/messages")) return { items: [], total: 0, page: 0, pages: 1 };
    return {};
  };
  return { handler, state };
}

(async () => {
  const { handler, state } = fake();
  const { w, calls } = await open(handler);
  const $ = s => w.document.querySelector(s);
  const text = () => w.document.body.textContent;

  // nobody connected yet: the form, with the provider's hint and no server fields; no Cancel on the first one
  assert.match(text(), /Connect your email/);
  assert.ok(!/Incoming server/.test(text()) && !btn(w, /^Cancel$/));
  assert.equal($("input[type=password]").autocomplete, "off");

  // under the app password: a link to where the chosen provider makes one (a new tab, no opener), and its steps
  assert.equal(links(w).length, 1);
  assert.equal(links(w)[0].getAttribute("href"), "https://myaccount.google.com/apppasswords");
  assert.equal(links(w)[0].getAttribute("target"), "_blank");
  assert.equal(links(w)[0].getAttribute("rel"), "noopener noreferrer");
  assert.deepEqual([...w.document.querySelectorAll(".ml-help li")].map(li => li.textContent), PRESETS[0].steps);
  setValue(w, $("select"), "fastmail");
  await settle(40);
  assert.equal(links(w)[0].getAttribute("href"), "https://app.fastmail.com/settings/security/devicekeys");
  assert.match($(".ml-help").textContent, /Open Password & Security in Fastmail/);
  assert.ok(!/Turn on 2-Step Verification/.test(text()));

  // "Other" has no page to point to: no link and no steps, but the two server fields
  setValue(w, $("select"), "");
  await settle(40);
  assert.equal(links(w).length, 0);
  assert.ok(!$(".ml-help"));
  assert.match(text(), /Incoming server \(IMAP\)/);
  assert.match(text(), /Outgoing server \(SMTP\)/);
  setValue(w, $("select"), "gmail");
  await settle(40);
  assert.equal(links(w).length, 1);

  // a refused sign-in shows the reason and keeps the form
  state.setFail(true);
  setValue(w, $("input[type=email]"), "bob@gmail.com");
  setValue(w, $("input[type=password]"), "app-pass-123");
  submit(w);
  await settle(120);
  assert.match(text(), /did not accept the sign-in/);
  assert.match(text(), /Connect your email/);
  assert.equal(links(w).length, 1, "an ordinary failure does not add a second link");

  // a normal password refused by the provider: the clear message, with the same link beside it, and the password is never shown back
  state.setFail("That provider needs an app password, not your normal password.");
  submit(w);
  await settle(120);
  assert.match($('[role="alert"]').textContent, /That provider needs an app password, not your normal password\./);
  assert.equal(links(w).length, 2, "the link is repeated next to the message");
  assert.ok([...$('[role="alert"]').querySelectorAll("a")].some(a => a.getAttribute("href") === "https://myaccount.google.com/apppasswords"));
  assert.ok(!text().includes("app-pass-123"), "the password is not echoed anywhere on the page");
  assert.ok([...w.document.querySelectorAll("input")].every(i => i.value !== "app-pass-123" || i.type === "password"));
  assert.deepEqual(calls.find(c => c[0] === "PUT").slice(1), ["/api/mail/account", { address: "bob@gmail.com", password: "app-pass-123", preset: "gmail" }]);

  // a good one opens the mailbox, which says whose it is, and offers Add another and Disconnect
  state.setFail(false);
  submit(w);
  await settle(300);
  assert.ok(!/Connect your email/.test(text()), "the form is gone");
  assert.match(text(), /Signed in as bob@gmail\.com/);
  assert.ok(btn(w, /Add another mailbox/) && btn(w, /^Disconnect$/));
  assert.ok(!$("select[aria-label=Mailbox]"), "one mailbox needs no switcher");
  assert.ok(calls.some(c => c[1].startsWith("/api/mail/folders?account=m1")), "requests name the open mailbox");

  // adding another: the form comes back with Cancel, and Cancel returns to the mailbox
  click(w, btn(w, /Add another mailbox/));
  await settle(80);
  assert.match(text(), /Add another mailbox/);
  click(w, btn(w, /^Cancel$/));
  await settle(300);
  assert.match(text(), /Signed in as bob@gmail\.com/);
  click(w, btn(w, /Add another mailbox/));
  await settle(80);
  setValue(w, $("input[type=email]"), "bob.work@fastmail.com");
  setValue(w, $("input[type=password]"), "second-pass");
  submit(w);
  await settle(350);
  const sw = $("select[aria-label=Mailbox]");
  assert.ok(sw, "two mailboxes get a switcher");
  assert.deepEqual([...sw.options].map(o => o.textContent), ["bob@gmail.com", "bob.work@fastmail.com"]);
  assert.equal(sw.value, "m2", "the new one is open");
  assert.ok(calls.some(c => c[1].startsWith("/api/mail/folders?account=m2")));

  // switching reloads the other mailbox
  setValue(w, $("select[aria-label=Mailbox]"), "m1");
  await settle(350);
  assert.equal($("select[aria-label=Mailbox]").value, "m1");
  assert.equal(calls.filter(c => c[1].startsWith("/api/mail/folders?account=m1")).length >= 2, true);

  // disconnecting the open one removes only that one
  click(w, btn(w, /^Disconnect$/));
  await settle(350);
  assert.deepEqual(calls.filter(c => c[0] === "DELETE").map(c => c[1]), ["/api/mail/account/m1"]);
  assert.match(text(), /Signed in as bob\.work@fastmail\.com/);
  click(w, btn(w, /^Disconnect$/));
  await settle(350);
  assert.match(text(), /Connect your email/);                                  // none left: back to the first form

  // an owner opens on the server mailbox, can add personal ones next to it, and only the server one shows the block menu
  const owner = fake({ server: true });
  const o = await open(owner.handler);
  assert.match(o.w.document.body.textContent, /Signed in as owner@server\.test/);
  assert.ok(!o.w.document.querySelector("select[aria-label=Mailbox]") && !btn(o.w, /^Disconnect$/), "the server mailbox has no Disconnect");
  assert.ok(btn(o.w, /Add another mailbox/));
  click(o.w, btn(o.w, /Add another mailbox/));
  await settle(80);
  setValue(o.w, o.w.document.querySelector("input[type=email]"), "owner@gmail.com");
  setValue(o.w, o.w.document.querySelector("input[type=password]"), "pass-for-gmail");
  o.w.document.querySelector("form").dispatchEvent(new o.w.Event("submit", { bubbles: true, cancelable: true }));
  await settle(350);
  const osw = o.w.document.querySelector("select[aria-label=Mailbox]");
  assert.deepEqual([...osw.options].map(x => x.textContent), ["Server mailbox: owner@server.test", "owner@gmail.com"]);
  assert.ok(btn(o.w, /^Disconnect$/), "a personal mailbox can be disconnected");

  console.log("mail-connect tests passed");
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
