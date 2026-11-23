// Settings > Skills and Settings > Memory editing: list, open, add, change, remove, and the read-only case. Synthetic only (no network).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "../app/static");
const settle = (ms = 80) => new Promise(resolve => setTimeout(resolve, ms));

function boot(scripts, handler) {
  const dom = new JSDOM('<main id="root"></main>', { url: "https://app.test/settings", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window, load = f => w.eval(fs.readFileSync(path.join(ROOT, f), "utf8"));
  load("vendor/react.production.min.js"); load("vendor/react-dom.production.min.js");
  const calls = [];
  const fetchJSON = async (url, opts) => { calls.push([(opts && opts.method) || "GET", url, opts && opts.body ? JSON.parse(opts.body) : null]); return handler(url, opts || {}); };
  w.__HERMES_PLUGINS__ = { register() {}, registerSlot() {} };
  w.__HERMES_PLUGIN_SDK__ = { React: w.React, hooks: w.React, fetchJSON };
  w.__APP_CONFIG__ = { brand: "", agentName: "Robo", name: "", user: "" };
  w.atriumEvery = () => () => {};
  // a canvas that accepts every drawing call, so the memory graph can run
  w.HTMLCanvasElement.prototype.getContext = () => new Proxy(function () {}, { get: (t, k) => (k === "canvas" ? {} : k === "measureText" ? () => ({ width: 10 }) : k === "createRadialGradient" || k === "createLinearGradient" ? () => ({ addColorStop() {} }) : () => {}), set: () => true });
  ["pages/common.js", "pages/kit.js"].concat(scripts).forEach(load);
  return { w, calls };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const button = (w, re, root) => [...(root || w.document).querySelectorAll("button")].find(b => re.test(b.textContent) || re.test(b.getAttribute("aria-label") || ""));
function setValue(w, el, value) {
  const proto = el.tagName === "TEXTAREA" ? w.HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? w.HTMLSelectElement.prototype : w.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
  el.dispatchEvent(new w.Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
}

const SKILLS = { exists: true, writable: true, skills: [
  { id: "research/summarize", name: "summarize", description: "Summarise a long text", category: "research", writable: true },
  { id: "tools/locked", name: "locked", description: "Cannot be changed", category: "tools", writable: false }] };
const BODY = "---\nname: summarize\ndescription: Summarise a long text\n---\nBody\n";

async function skillsPage(handler) {
  const { w, calls } = boot(["pages/settings/skills.js"], handler);
  w.ReactDOM.createRoot(w.document.getElementById("root")).render(w.React.createElement(w.__APP_SUBPAGES__.skills));
  await settle();
  return { w, calls };
}

(async () => {
  // ---- Skills: list, search, open, edit and save
  let r = await skillsPage(async (url, o) => url.endsWith("/list") ? SKILLS : url.includes("/skill?id=") ? { id: "x", body: BODY, files: ["notes.txt"], writable: !/locked/.test(url) } : { ok: true });
  let w = r.w, $ = s => w.document.querySelector(s), $$ = s => [...w.document.querySelectorAll(s)];
  assert.equal($$(".ats-skill").length, 2);
  setValue(w, $("input[type=search]"), "summar"); await settle();
  assert.equal($$(".ats-skill").length, 1);
  click(w, button(w, /Edit summarize/)); await settle();
  assert.ok($("[role=dialog]") && /Edit skill/.test($("[role=dialog]").textContent));
  assert.equal($("textarea").value, BODY);
  setValue(w, $("textarea"), BODY + "More\n");
  click(w, button(w, /^Save$/, $("[role=dialog]"))); await settle();
  const post = r.calls.find(c => c[0] === "POST");
  assert.deepEqual(post.slice(1), ["/api/plugins/atrium-skills/skill", { id: "research/summarize", body: BODY + "More\n" }]);
  assert.ok(!$("[role=dialog]"), "closed after saving");
  assert.match($(".kit-alert-success").textContent, /Saved/);

  // ---- Skills: delete needs a second click
  click(w, button(w, /Edit summarize/)); await settle();
  click(w, button(w, /Delete skill/)); await settle();
  assert.ok(!r.calls.some(c => c[0] === "DELETE"), "one click does not delete");
  click(w, button(w, /Yes, delete/)); await settle();
  assert.deepEqual(r.calls.find(c => c[0] === "DELETE").slice(1, 2), ["/api/plugins/atrium-skills/skill?id=research%2Fsummarize"]);

  // ---- Skills: add a new one (the id comes from the header)
  click(w, button(w, /New skill/)); await settle();
  setValue(w, $("[role=dialog] input[type=text]"), "Web");
  setValue(w, $("textarea"), "---\nname: web-search\ndescription: Look things up\n---\nGo\n");
  click(w, button(w, /Add skill/)); await settle();
  assert.deepEqual(r.calls.filter(c => c[0] === "POST").pop().slice(2), [{ id: "web/web-search", body: "---\nname: web-search\ndescription: Look things up\n---\nGo\n", create: true }]);

  // ---- Skills: a server refusal stays in the dialog and keeps the text
  r = await skillsPage(async (url, o) => { if (o.method === "POST") throw new Error('400: {"detail":"The header needs a description."}'); return url.endsWith("/list") ? SKILLS : { id: "x", body: BODY, files: [], writable: true }; });
  w = r.w; $ = s => w.document.querySelector(s);
  click(w, button(w, /Edit summarize/)); await settle();
  setValue(w, $("textarea"), "changed");
  click(w, button(w, /^Save$/, $("[role=dialog]"))); await settle();
  assert.match($("[role=dialog] [role=alert]").textContent, /needs a description/);
  assert.equal($("textarea").value, "changed");

  // ---- Skills: read-only install and read-only skill
  r = await skillsPage(async url => url.endsWith("/list") ? { exists: true, writable: false, skills: SKILLS.skills } : { id: "x", body: BODY, files: [], writable: false });
  w = r.w; $ = s => w.document.querySelector(s);
  assert.match(w.document.body.textContent, /read-only on this install/);
  assert.ok(button(w, /New skill/).disabled);
  click(w, button(w, /View locked/)); await settle();
  assert.equal($("textarea").readOnly, true);
  assert.ok(!button(w, /^Save$/, $("[role=dialog]")) && !button(w, /Delete skill/));

  // ---- Skills: empty and missing folder
  r = await skillsPage(async () => ({ exists: true, writable: true, skills: [] }));
  assert.match(r.w.document.querySelector(".kit-empty-title").textContent, /No skills yet/);
  r = await skillsPage(async () => ({ exists: false, writable: false, skills: [] }));
  assert.match(r.w.document.querySelector(".kit-empty-title").textContent, /No skills folder/);

  // ---- Memory: edit, add and delete a note
  const GRAPH = { nodes: [{ id: "Notes/Alpha.md", title: "Alpha", folder: "Notes", summary: "", mtime: 1, size: 5, out: 0, in: 0 }], edges: [], folders: [{ name: "Notes", count: 1 }], recent: ["Notes/Alpha.md"], truncated: false };
  const NOTE = { id: "Notes/Alpha.md", title: "Alpha", mtime: 1, body: "# Alpha\nHello\n", cut: false, editable: true, links: [], backlinks: [] };
  const mem = async (status, note) => {
    const b = boot(["pages/memory/index.js"], async (url, o) => url.endsWith("/graph") ? GRAPH : url.endsWith("/status") ? status : url.includes("/note?path=") && !o.method ? note : { id: "Notes/Gamma.md" });
    b.w.ReactDOM.createRoot(b.w.document.getElementById("root")).render(b.w.React.createElement(b.w.__APP_SUBPAGES__.memory));
    await settle(150);
    return b;
  };
  r = await mem({ exists: true, root: false, folders: ["Notes"] }, NOTE);
  w = r.w; $ = s => w.document.querySelector(s);
  click(w, button(w, /Alpha/, $(".atm-recent"))); await settle();
  click(w, button(w, /Edit or delete/)); await settle();
  assert.equal($("textarea").value, NOTE.body);
  setValue(w, $("textarea"), "# Alpha\nChanged\n");
  click(w, button(w, /^Save$/, $("[role=dialog]"))); await settle();
  assert.deepEqual(r.calls.find(c => c[0] === "POST").slice(1), ["/api/plugins/atrium-memory/note", { path: "Notes/Alpha.md", body: "# Alpha\nChanged\n" }]);
  click(w, button(w, /Edit or delete/)); await settle();
  click(w, button(w, /Delete note/)); await settle();
  click(w, button(w, /Yes, delete/)); await settle();
  assert.equal(r.calls.find(c => c[0] === "DELETE")[1], "/api/plugins/atrium-memory/note?path=Notes%2FAlpha.md");
  click(w, button(w, /New note/)); await settle();
  assert.equal($("[role=dialog] select").value, "Notes");
  click(w, button(w, /Add note/)); await settle();
  assert.match($("[role=dialog] [role=alert]").textContent, /Give the note a name/);
  setValue(w, $("[role=dialog] input[type=text]"), "Gamma");
  click(w, button(w, /Add note/)); await settle();
  assert.deepEqual(r.calls.filter(c => c[0] === "POST").pop().slice(2), [{ path: "Notes/Gamma", body: "# Gamma\n", create: true }]);

  // ---- Memory: nothing writable means no way in, and a read-only note says so
  r = await mem({ exists: true, root: false, folders: [] }, Object.assign({}, NOTE, { editable: false }));
  w = r.w;
  assert.ok(!button(w, /New note/));
  click(w, button(w, /Alpha/, w.document.querySelector(".atm-recent"))); await settle();
  assert.ok(!button(w, /Edit or delete/));
  assert.match(w.document.querySelector(".atm-note-actions").textContent, /Read-only/);

  console.log("skills-memory tests passed");
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
