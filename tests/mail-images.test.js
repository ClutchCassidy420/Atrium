// Email: images in a message are blocked until the person asks (this message, or always for the sender), and can be switched off again. Synthetic only (no network).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "../app/static");
const settle = (ms = 120) => new Promise(resolve => setTimeout(resolve, ms));
const SENDER = "news@shop.example.org";

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
  await settle(200);
  return { w, calls };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const btn = (w, re) => [...w.document.querySelectorAll("button")].find(b => re.test(b.textContent));

function fake({ senderEmail = SENDER } = {}) {
  const state = { safe: false, last: null };
  const message = images => {
    const shown = images || state.safe;
    return { uid: 1, folder: "INBOX", subject: "Sale", from: { name: "Shop", email: senderEmail }, to: [{ name: "", email: "me@x.test" }], cc: [], reply_to: [], date: "2026-11-24T10:00:00+00:00",
             message_id: "<1@x>", references: "", text: "Hello", attachments: [], sender_email: senderEmail, safe_sender: state.safe,
             html: shown ? '<p>Hello</p><img src="/api/mail-image/abc.def">' : '<p>Hello</p><img data-blocked-src="http://img.shop.example.org/a.png">',
             blocked_images: !shown, images_shown: shown };
  };
  const handler = async (url, o) => {
    const [p, query = ""] = url.split("?");
    if (p === "/api/mail/status") return { connected: true, account: "server", active: "server", accounts: [{ id: "server", address: "me@x.test", kind: "server" }], address: "me@x.test", webmail: "", features: [], user_accounts: false, presets: [] };
    if (p === "/api/mail/folders") return { folders: [{ name: "INBOX", role: "inbox", total: 1, unseen: 1 }], address: "me@x.test" };
    if (p === "/api/mail/messages") return { items: [{ uid: 1, subject: "Sale", from: { name: "Shop", email: senderEmail }, to: [], date: "2026-11-24T10:00:00+00:00", seen: false, flagged: false, attach: false, answered: false }], total: 1, page: 0, pages: 1 };
    if (p === "/api/mail/messages/1") { state.last = query; return message(/(^|&)images=1(&|$)/.test(query)); }
    if (p === "/api/mail/safe-senders" && o.method === "POST") { state.safe = true; return { senders: [senderEmail] }; }
    if (p === "/api/mail/safe-senders" && o.method === "DELETE") { state.safe = false; return { senders: [] }; }
    return {};
  };
  return { handler, state };
}

(async () => {
  const { handler, state } = fake();
  const { w, calls } = await open(handler);
  const text = () => w.document.body.textContent;
  const frame = () => w.document.querySelector("iframe.ml-frame");

  // opening a message: its images are blocked, with a plain reason and two choices
  click(w, w.document.querySelector(".ml-item"));
  await settle();
  assert.match(text(), /Images are blocked so the sender cannot see when you read this\./);
  assert.ok(btn(w, /^Show images$/));
  assert.ok(btn(w, new RegExp("Always show from " + SENDER.replace(/\./g, "\\."))));
  assert.match(frame().getAttribute("srcdoc"), /<img[^>]*data-blocked-src/);
  assert.ok(!/\/api\/mail-image\//.test(frame().getAttribute("srcdoc")));
  assert.equal(frame().getAttribute("sandbox"), "allow-popups allow-popups-to-escape-sandbox", "the frame stays sandboxed");

  // Show images: this message is asked for again with images on; nothing is remembered
  click(w, btn(w, /^Show images$/));
  await settle();
  assert.match(state.last, /images=1/);
  assert.match(frame().getAttribute("srcdoc"), /\/api\/mail-image\/abc\.def/);
  assert.ok(!/<img[^>]*data-blocked-src/.test(frame().getAttribute("srcdoc")), "no blocked picture is left in the message");
  assert.match(text(), /Images are loaded through this server, so the sender does not see your address, but may see that the message was opened\./);
  assert.ok(btn(w, /Always show from/), "you can still choose to always show them");
  assert.ok(!btn(w, /^Show images$/));
  assert.ok(!calls.some(c => c[1].includes("safe-senders")), "showing once changes no list");

  // Always show from this sender: the sender is saved, then the message reloads with the images and a way back
  click(w, btn(w, /Always show from/));
  await settle(250);
  const post = calls.find(c => c[0] === "POST" && c[1].startsWith("/api/mail/safe-senders"));
  assert.deepEqual(post[2], { address: SENDER });
  assert.match(text(), new RegExp("Images from " + SENDER.replace(/\./g, "\\.") + " are always shown\\."));
  assert.match(frame().getAttribute("srcdoc"), /\/api\/mail-image\//);
  assert.ok(btn(w, /Stop showing images from this sender/));

  // Stop: the sender is removed and the message is blocked again
  click(w, btn(w, /Stop showing images from this sender/));
  await settle(250);
  const del = calls.find(c => c[0] === "DELETE" && c[1].startsWith("/api/mail/safe-senders"));
  assert.ok(del[1].startsWith("/api/mail/safe-senders?address=" + encodeURIComponent(SENDER)), del[1]);
  assert.match(text(), /Images are blocked so the sender cannot see when you read this\./);
  assert.match(frame().getAttribute("srcdoc"), /<img[^>]*data-blocked-src/);

  // a message whose sender cannot be told for sure offers one-time images but never Always
  const odd = fake({ senderEmail: "" });
  const o = await open(odd.handler);
  click(o.w, o.w.document.querySelector(".ml-item"));
  await settle();
  assert.ok(btn(o.w, /^Show images$/));
  assert.ok(!btn(o.w, /Always show from/));

  console.log("mail-images tests passed");
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
