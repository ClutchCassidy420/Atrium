// The avatar's reactions and the tool-in-use signal (jsdom, a fake character, a fake clock and fake timers).
// usage: NODE_PATH=<folder with node_modules for jsdom> node tests/avatar.test.js
const fs = require("fs"), path = require("path"), assert = require("assert");
const { JSDOM } = require("jsdom");
const S = path.join(__dirname, "..", "app", "static", "pages", "avatar");
const read = (f) => fs.readFileSync(path.join(S, f), "utf8");

// a page whose clock and timers the test controls
function boot(url = "https://app.test/") {
  const dom = new JSDOM("<!doctype html><body></body>", { url, runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  const clock = { now: 1_000_000_000_000, timeouts: [], intervals: [], nextId: 1 };
  w.Date = class extends Date { static now() { return clock.now; } };
  w.setTimeout = (fn, ms) => { const id = clock.nextId++; clock.timeouts.push({ id, fn, at: clock.now + ms }); return id; };
  w.clearTimeout = (id) => { clock.timeouts = clock.timeouts.filter((t) => t.id !== id); };
  w.setInterval = (fn, ms) => { const id = clock.nextId++; clock.intervals.push({ id, fn, ms }); return id; };
  w.clearInterval = (id) => { clock.intervals = clock.intervals.filter((t) => t.id !== id); };
  clock.advance = (ms) => {
    clock.now += ms;
    for (const t of clock.timeouts.filter((x) => x.at <= clock.now)) { clock.timeouts = clock.timeouts.filter((x) => x !== t); t.fn(); }
  };
  clock.tick = () => clock.intervals.slice().forEach((t) => t.fn());
  return { w, clock };
}
const character = () => { const calls = []; return { calls, react: (k) => calls.push(["react", k]), fedUp: (n, o) => { calls.push(["fedUp", n, !!o.sound, o.url]); return Promise.resolve(); }, setTool: (k) => calls.push(["tool", k]) }; };
const kinds = (c) => c.calls.map((x) => x.join(":"));

(async () => {
  // =============================================================== reactions.js
  let { w, clock } = boot();
  w.eval(read("reactions.js"));
  let c = character(), caps = [];
  let r = w.__AVATAR_REACT__.create(() => c, { sound: () => true, speakUrl: "/api/x/speak", onCaption: (x) => caps.push(x) });

  // 1. ordinary cues go straight to the character
  r.event("send"); r.event("done"); r.event("approval");
  assert.deepStrictEqual(kinds(c), ["react:send", "react:done", "react:approval"]);

  // 2. "work" right after a send is swallowed (she has only just looked up), but not later
  c = character(); r = w.__AVATAR_REACT__.create(() => c, {});
  r.event("send"); clock.advance(1000); r.event("work");
  assert.deepStrictEqual(kinds(c), ["react:send"]);
  clock.advance(2000); r.event("work");
  assert.deepStrictEqual(kinds(c), ["react:send", "react:work"]);

  // 3. one failure is a wince; failing again within ten minutes gets worse each time, up to a limit
  c = character(); caps = []; r = w.__AVATAR_REACT__.create(() => c, { sound: () => true, speakUrl: "/speak", onCaption: (x) => caps.push(x) });
  r.event("error");
  assert.deepStrictEqual(kinds(c), ["react:error"]);
  clock.advance(60 * 1000); r.event("error");
  clock.advance(60 * 1000); r.event("error");
  clock.advance(60 * 1000); r.event("error");
  clock.advance(60 * 1000); r.event("error");
  assert.deepStrictEqual(c.calls.slice(1).map((x) => x.slice(0, 2).join(":")), ["fedUp:1", "fedUp:2", "fedUp:3", "fedUp:3"], "1, 2, 3, then it stays at 3");
  assert.strictEqual(c.calls[1][2], true, "sound follows the preference"); assert.strictEqual(c.calls[1][3], "/speak");
  assert.ok(caps.length >= 4 && caps.every((x) => x === ""), "the caption is cleared before she speaks");

  // 4. failures far apart do not add up, and an answer clears the count
  c = character(); r = w.__AVATAR_REACT__.create(() => c, {});
  r.event("error"); clock.advance(11 * 60 * 1000); r.event("error");
  assert.deepStrictEqual(kinds(c), ["react:error", "react:error"], "more than ten minutes apart is a fresh failure");
  r.event("error"); r.answered(); r.event("error");
  assert.deepStrictEqual(c.calls.slice(2).map((x) => x[0] + ":" + (x[1] || "")), ["fedUp:1", "react:error"], "an answer arriving resets the count");

  // 5. sound off means she does not speak
  c = character(); r = w.__AVATAR_REACT__.create(() => c, { sound: () => false });
  r.event("error"); r.event("error");
  assert.strictEqual(c.calls[1][2], false);

  // 6. state changes: work starts, approval needed, work finished (after a short wait, and only if no answer just arrived)
  c = character(); r = w.__AVATAR_REACT__.create(() => c, {});
  r.state("idle", "working"); r.state("working", "waiting");
  assert.deepStrictEqual(kinds(c), ["react:work", "react:approval"]);
  r.state("working", "working"); r.state("offline", "working"); r.state("waiting", "idle");
  assert.strictEqual(c.calls.length, 2, "no cue for no change, or for states that mean nothing here");
  r.state("working", "idle"); assert.strictEqual(c.calls.length, 2, "not at once"); clock.advance(2100);
  assert.deepStrictEqual(c.calls[2], ["react", "done"]);
  c = character(); r = w.__AVATAR_REACT__.create(() => c, {});
  r.answered(); r.state("working", "idle"); clock.advance(2100);
  assert.strictEqual(c.calls.length, 0, "an answer a moment ago already said the work is done");
  r.state("working", "idle"); r.state("idle", "working");
  clock.advance(5000);
  assert.deepStrictEqual(kinds(c), ["react:work"], "going back to work cancels the pending 'done'");

  // 7. the app's own signals and coming back to the tab
  c = character(); r = w.__AVATAR_REACT__.create(() => c, {});
  const stop = r.watch();
  const fire = (detail) => w.dispatchEvent(new w.CustomEvent("atrium:avatar-event", { detail }));
  fire({ kind: "send" }); fire({ kind: "mail" }); fire({ kind: "nonsense" }); fire(null); fire({});
  assert.deepStrictEqual(kinds(c), ["react:send", "react:mail"], "only the three known signals count");
  const visibility = (hidden) => { Object.defineProperty(w.document, "hidden", { configurable: true, get: () => hidden }); w.document.dispatchEvent(new w.Event("visibilitychange")); };
  visibility(true); clock.advance(60 * 1000); visibility(false);
  assert.strictEqual(c.calls.length, 2, "a short absence is not worth a welcome");
  visibility(true); clock.advance(6 * 60 * 1000); visibility(false);
  assert.deepStrictEqual(c.calls[2], ["react", "back"]);
  stop(); fire({ kind: "send" }); visibility(true); clock.advance(6 * 60 * 1000); visibility(false);
  assert.strictEqual(c.calls.length, 3, "stopping removes both listeners");

  // 8. a reaction must never break the page
  const boom = { react() { throw new Error("engine gone"); }, fedUp() { throw new Error("engine gone"); } };
  r = w.__AVATAR_REACT__.create(() => boom, {});
  r.event("send"); r.event("error"); r.event("error");
  r = w.__AVATAR_REACT__.create(() => null, {});
  r.event("send"); r.state("idle", "working"); r.watch()();
  w.close();

  // =============================================================== toolsignal.js
  ({ w, clock } = boot());
  const requests = []; let reply = { latest: { kind: "terminal", at: clock.now, name: "terminal" } }, ok = true;
  w.fetch = (url, opts) => { requests.push(url); return Promise.resolve({ ok, json: () => Promise.resolve(reply) }); };
  w.eval(read("toolsignal.js"));
  const T = w.__AVATAR_TOOL__, flush = () => new Promise((res) => setImmediate(res));
  let hidden = false; Object.defineProperty(w.document, "hidden", { configurable: true, get: () => hidden });
  let working = false;

  // 1. nothing is asked while the agent is idle
  let stopPoll = T.poll(() => working);
  await flush(); clock.tick();
  assert.strictEqual(requests.length, 0, "idle: no request");
  // 2. working: it asks, and keeps the answer where the drawn avatar looks for it
  working = true; clock.tick(); await flush();
  assert.strictEqual(requests.length, 1);
  assert.strictEqual(requests[0], "/api/tool-activity");
  assert.deepStrictEqual({ ...w.__ATRIUM_TOOL__ }, { kind: "terminal", at: clock.now, name: "terminal" });
  // 3. a hidden tab asks nothing
  hidden = true; clock.tick(); await flush();
  assert.strictEqual(requests.length, 1);
  hidden = false;
  // 4. a failed or empty answer is ignored and leaves the last value
  const before = { ...w.__ATRIUM_TOOL__ };
  ok = false; clock.tick(); await flush();
  assert.deepStrictEqual({ ...w.__ATRIUM_TOOL__ }, before);
  ok = true; reply = { latest: null }; clock.tick(); await flush();
  assert.deepStrictEqual({ ...w.__ATRIUM_TOOL__ }, before);
  w.fetch = () => Promise.reject(new Error("offline")); clock.tick(); await flush();
  assert.deepStrictEqual({ ...w.__ATRIUM_TOOL__ }, before, "offline is not an error the page sees");
  // 5. one timer serves everything on the page, and it stops when the last user stops
  w.fetch = (url) => { requests.push(url); return Promise.resolve({ ok: true, json: () => Promise.resolve(reply) }); };
  const stopSecond = T.poll(() => true);
  assert.strictEqual(clock.intervals.length, 1, "a second user shares the timer");
  stopPoll(); assert.strictEqual(clock.intervals.length, 1);
  stopSecond(); assert.strictEqual(clock.intervals.length, 0, "no users left: no timer");

  // 6. feed(): the whiteboard magnet glows for a tool used in the last 25 seconds, only while she is working
  const ch = character(); working = true; reply = { latest: { kind: "web", at: clock.now, name: "browser_navigate" } };
  const stopFeed = T.feed(() => ch, () => working);
  await flush();
  clock.tick();
  assert.deepStrictEqual(ch.calls.filter((x) => x[0] === "tool").pop(), ["tool", "web"], "a fresh tool glows");
  clock.advance(24000); clock.tick();
  assert.deepStrictEqual(ch.calls.filter((x) => x[0] === "tool").pop(), ["tool", "web"], "still glowing 24 seconds after the call");
  clock.advance(2000); clock.tick();
  assert.deepStrictEqual(ch.calls.filter((x) => x[0] === "tool").pop(), ["tool", null], "a tool used more than 25 seconds ago stops glowing");
  w.__ATRIUM_TOOL__ = { kind: "git", at: clock.now, name: "git_status" }; working = false; clock.tick();
  assert.deepStrictEqual(ch.calls.filter((x) => x[0] === "tool").pop(), ["tool", null], "not while she is idle");
  working = true; clock.tick();
  assert.deepStrictEqual(ch.calls.filter((x) => x[0] === "tool").pop(), ["tool", "git"]);
  stopFeed(); assert.strictEqual(clock.intervals.length, 0, "feed cleans up its timers");
  // a character without the method, and one that throws, are both fine
  const stopOdd = T.feed(() => ({}), () => true); clock.tick(); stopOdd();
  const stopBoom = T.feed(() => ({ setTool() { throw new Error("gone"); } }), () => true); clock.tick(); stopBoom();
  w.close();

  console.log("avatar OK: reactions (cues, quiet period, fed-up escalation, state changes, signals, welcome back) and the tool signal (polling rules, glow window, cleanup)");
})().catch((e) => { console.error("FAIL:", e.stack || e.message); process.exit(1); });
