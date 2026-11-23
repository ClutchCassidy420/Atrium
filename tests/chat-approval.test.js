// Chat, the approval card: what it shows and what a click does (jsdom with the real React build).
// usage: NODE_PATH=<folder with node_modules for jsdom> node tests/chat-approval.test.js
const fs = require("fs"), path = require("path"), assert = require("assert");
const { JSDOM } = require("jsdom");
const S = path.join(__dirname, "..", "app", "static");
const read = (f) => fs.readFileSync(path.join(S, f), "utf8");

const dom = new JSDOM("<!doctype html><body><div id='root'></div></body>", { url: "https://app.test/chat", runScripts: "outside-only", pretendToBeVisual: true });
const w = dom.window;
w.eval(read("vendor/react.production.min.js"));
w.eval(read("vendor/react-dom.production.min.js"));
w.eval(read("pages/chat/approval.js"));
const R = w.React, A = w.__APP_CHAT_APPROVAL__;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const click = (el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

// ---- 1. describe(): what a request allows, from plain data -------------------------------------------------------
const cmd = { prompt_id: "p1", command: "rm -rf build && make", description: "Clean and rebuild", choices: ["once", "deny"] };
let d = A.describe(cmd);
assert.ok(d.answerable && d.canApprove && d.canDeny && d.approval && d.rid === "p1");
assert.strictEqual(d.command, "rm -rf build && make");
assert.strictEqual(A.describe({ prompt_id: "a", request_id: "b", id: "c" }).rid, "a", "prompt_id wins, then request_id, then id");
assert.strictEqual(A.describe({ request_id: "b", id: "c" }).rid, "b");
assert.strictEqual(A.describe({ id: "c" }).rid, "c");
d = A.describe({ id: "x", choices: ["deny"], command: "ls" });
assert.ok(!d.canApprove && d.canDeny, "a request that does not offer 'once' cannot be approved once");
d = A.describe({ id: "x", command: "ls" });
assert.ok(d.canApprove && d.canDeny, "no choices listed: once and deny");
d = A.describe({ id: "x", command: "   " });
assert.ok(!d.canApprove && d.canDeny, "an empty command is nothing to approve");
d = A.describe({ id: "x", title: "Use the browser?" });
assert.ok(!d.canApprove && d.canDeny && d.label === "Use the browser?", "no command: it can be denied but not approved");
d = A.describe({ command: "ls" });
assert.ok(!d.answerable && !d.canApprove && !d.canDeny, "no id: nothing to answer");
d = A.describe({ id: "q", kind: "question", question: "Which branch?" });
assert.ok(!d.approval && !d.answerable && !d.canApprove && !d.canDeny && d.label === "Which branch?", "a question is not an approval");
assert.deepStrictEqual(A.describe(null).rid, "");
assert.ok(A.describe({ id: "x", command: "ls" }).label.includes("run this command"));

// ---- 2. the card ----------------------------------------------------------------------------------------------------
function mount(props) {
  const el = w.document.createElement("div");
  w.document.body.appendChild(el);
  const Card = A.makeCard({ h: R.createElement, useState: R.useState, useRef: R.useRef, errText: (e) => String((e && e.message) || e) });
  const root = w.ReactDOM.createRoot(el);
  root.render(R.createElement(Card, props));
  return { el, root };
}
const buttons = (el) => [...el.querySelectorAll("button")];
const names = (el) => buttons(el).map((b) => b.textContent);

(async () => {
  // the full command is shown, however long
  const long = "echo " + "x".repeat(2500);
  let m = mount({ control: { id: "r1", command: long }, runId: "run-1", onChoose: () => Promise.resolve() });
  await sleep(50);
  assert.strictEqual(m.el.querySelector("pre").textContent, long, "never cut: a person approving a command must see all of it");
  assert.strictEqual(m.el.querySelector("pre").getAttribute("aria-label"), "Full command");
  assert.deepStrictEqual(names(m.el), ["Approve once", "Deny"]);
  assert.strictEqual(m.el.querySelector("section").getAttribute("aria-label"), "Approval required");
  m.root.unmount();

  // which buttons appear
  m = mount({ control: { id: "r2", title: "Open the browser?" }, runId: "run-1", onChoose: () => Promise.resolve() });
  await sleep(50);
  assert.deepStrictEqual(names(m.el), ["Deny"], "nothing to approve, so no Approve button");
  m.root.unmount();
  m = mount({ control: { command: "ls" }, runId: "run-1", onChoose: () => Promise.resolve() });
  await sleep(50);
  assert.deepStrictEqual(names(m.el), [], "no id, no buttons");
  assert.ok(/no approval ID/.test(m.el.textContent));
  m.root.unmount();
  m = mount({ control: { id: "q", kind: "question", question: "Which branch?" }, runId: "run-1", onChoose: () => Promise.resolve() });
  await sleep(50);
  assert.deepStrictEqual(names(m.el), []);
  assert.ok(/Answer this in the chat/.test(m.el.textContent) && /waiting for an answer/.test(m.el.textContent));
  m.root.unmount();

  // one click sends one answer, for the run the card was shown for, and the buttons lock while it is sent
  const calls = []; let release;
  m = mount({ control: cmd, runId: "run-7", onChoose: (c, rid, choice, runId) => { calls.push([c.prompt_id, rid, choice, runId]); return new Promise((res) => { release = res; }); } });
  await sleep(50);
  const [approve, deny] = buttons(m.el);
  click(approve); click(approve); click(deny);                   // an impatient double click, then the other button
  await sleep(50);
  assert.deepStrictEqual(calls, [["p1", "p1", "once", "run-7"]], "one answer only, carrying the run id the card was shown for");
  assert.ok(buttons(m.el).every((b) => b.disabled), "locked while sending");
  assert.ok(/Sending approval/.test(m.el.querySelector("[role=status]").textContent));
  release(); await sleep(50);
  assert.ok(/Approved once/.test(m.el.querySelector("[role=status]").textContent) && buttons(m.el).every((b) => b.disabled), "stays answered");
  m.root.unmount();

  // deny sends deny
  const denied = [];
  m = mount({ control: cmd, runId: "run-8", onChoose: (c, rid, choice) => { denied.push(choice); return Promise.resolve(); } });
  await sleep(50);
  click(buttons(m.el)[1]); await sleep(50);
  assert.deepStrictEqual(denied, ["deny"]);
  assert.ok(/Denied/.test(m.el.textContent));
  m.root.unmount();

  // a failure says why and lets the person try again (for example the run has moved on)
  let attempt = 0;
  m = mount({ control: cmd, runId: "run-9", onChoose: () => (++attempt === 1 ? Promise.reject(new Error("This approval is no longer pending.")) : Promise.resolve()) });
  await sleep(50);
  click(buttons(m.el)[0]); await sleep(50);
  assert.ok(/no longer pending/.test(m.el.querySelector("[role=alert]").textContent), "the reason is shown");
  assert.ok(buttons(m.el).every((b) => !b.disabled), "and the buttons work again");
  click(buttons(m.el)[0]); await sleep(50);
  assert.strictEqual(attempt, 2);
  assert.ok(/Approved once/.test(m.el.textContent) && !m.el.querySelector("[role=alert]"), "a later success clears the error");
  m.root.unmount();

  console.log("chat approval OK: what is shown, which buttons, one answer per click, errors and retry");
  w.close();
})().catch((e) => { console.error("FAIL:", e.stack || e.message); process.exit(1); });
