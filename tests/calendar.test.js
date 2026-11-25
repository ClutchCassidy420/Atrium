// Calendar page on the UI kit: loading, empty and error states. Synthetic only (no network).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "../app/static");
const settle = () => new Promise(resolve => setTimeout(resolve, 80));

async function cal(fetchJSON) {
  const dom = new JSDOM('<main id="root"></main>', { url: "https://app.test/calendar", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window, load = f => w.eval(fs.readFileSync(path.join(ROOT, f), "utf8"));
  load("vendor/react.production.min.js"); load("vendor/react-dom.production.min.js");
  let Page;
  w.__HERMES_PLUGINS__ = { register: (_, c) => { Page = c; } };
  w.__HERMES_PLUGIN_SDK__ = { React: w.React, hooks: w.React, fetchJSON };
  ["pages/common.js", "pages/kit.js", "pages/calendar/index.js"].forEach(load);
  w.ReactDOM.createRoot(w.document.getElementById("root")).render(w.React.createElement(Page));
  return w;
}

(async () => {
  const ok = async url => url.includes("/status") ? { calendars: [{ id: "a", name: "Main", writable: true }] } : { events: [] };

  // an empty day shows the kit empty state
  let w = await cal(ok);
  await settle();
  assert.match(w.document.querySelector(".kit-empty-title").textContent, /Nothing on this day/);

  // a failed first load shows a danger alert, and "Try again" loads the calendar
  let fail = true;
  w = await cal(async url => { if (fail) throw new Error("500: nope"); return ok(url); });
  await settle();
  assert.ok(w.document.querySelector("[role=alert]"));
  fail = false;
  [...w.document.querySelectorAll("button")].find(b => /Try again/.test(b.textContent)).click();
  await settle();
  assert.ok(w.document.querySelector(".cal-grid"));

  // the event form has a "Remind me 15 minutes before" box: ticked from the event, saved in the request, hidden for all-day events
  const now = new Date(), at = (h, m) => new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m).toISOString();
  const ev = { uid: "u", name: "u.ics", calendar: "a", title: "Haircut", start: at(13, 30), end: at(14, 0), all_day: false, writable: true, remind: false };
  let sent = null;
  w = await cal(async (url, opt) => {
    if (opt && opt.method === "PUT") { sent = JSON.parse(opt.body); return { ok: true }; }
    return url.includes("/status") ? { calendars: [{ id: "a", name: "Main", writable: true }] } : { events: [ev] };
  });
  await settle();
  w.document.querySelector(".cal-chip").click();
  await settle();
  const box = [...w.document.querySelectorAll("label.cal-check")].find(l => /Remind me 15 minutes before/.test(l.textContent)).querySelector("input");
  assert.equal(box.checked, false);
  box.click(); await settle();
  w.document.querySelector("form button[type=submit]").click();
  await settle();
  assert.equal(sent && sent.remind, true);
  w.document.querySelector(".cal-chip").click(); await settle();
  const allDay = [...w.document.querySelectorAll("label.cal-check")].find(l => /All day/.test(l.textContent)).querySelector("input");
  allDay.click(); await settle();
  assert.ok(![...w.document.querySelectorAll("label.cal-check")].some(l => /Remind me/.test(l.textContent)));
  console.log("calendar tests passed");
})().catch(e => { console.error(e); process.exit(1); });
