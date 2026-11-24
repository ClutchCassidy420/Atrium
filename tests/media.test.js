// Media page: connect a server, browse, search, open an item and play it (falling back to a converted copy). Synthetic only (no network).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "../app/static");
const settle = (ms = 120) => new Promise(resolve => setTimeout(resolve, ms));
const ITEM = "0123456789abcdef0123456789abcdef", SHOW = "fedcba9876543210fedcba9876543210", MUSIC = "aaaabbbbccccddddaaaabbbbccccdddd";
const TRACKS = ["1111", "2222", "3333"].map((n, i) => ({ id: n.repeat(8), name: "Song " + (i + 1), type: "Audio", media: "audio", artist: "The Band", minutes: 3, has_image: false }));

async function open(handler) {
  const dom = new JSDOM('<main id="root"></main>', { url: "https://app.test/media", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window, load = f => w.eval(fs.readFileSync(path.join(ROOT, f), "utf8"));
  load("vendor/react.production.min.js"); load("vendor/react-dom.production.min.js");
  const calls = [], pages = {};
  w.__HERMES_PLUGINS__ = { register: (n, c) => { pages[n] = c; }, registerSlot() {} };
  w.__HERMES_PLUGIN_SDK__ = { React: w.React, hooks: w.React, fetchJSON: async (url, o) => { calls.push([(o && o.method) || "GET", url, o && o.body ? JSON.parse(o.body) : null]); return handler(url, o || {}); } };
  w.__APP_CONFIG__ = { brand: "", agentName: "Robo", name: "", user: "" };
  w.atriumEvery = () => () => {};
  w.confirm = () => true;
  w.HTMLMediaElement.prototype.play = () => Promise.resolve();
  ["pages/common.js", "pages/kit.js", "pages/media/index.js"].forEach(load);
  w.ReactDOM.createRoot(w.document.getElementById("root")).render(w.React.createElement(pages["media-page"]));
  await settle(200);
  return { w, calls };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const btn = (w, re) => [...w.document.querySelectorAll("button")].find(b => re.test(b.textContent));
const typeInto = (w, el, value) => { const set = Object.getOwnPropertyDescriptor(w.HTMLInputElement.prototype, "value").set; set.call(el, value); el.dispatchEvent(new w.Event("input", { bubbles: true })); };

function fake() {
  const state = { servers: [], play: "direct" };
  const handler = async (url, o) => {
    const [p, query = ""] = url.split("?");
    if (p === "/api/media/servers" && !o.method) return { servers: state.servers, max: 8, kinds: [{ id: "jellyfin", label: "Jellyfin" }, { id: "emby", label: "Emby" }], local_ok: false };
    if (p === "/api/media/servers" && o.method === "POST") { state.servers = [{ id: "abcd1234", kind: "jellyfin", label: "Home", url: "https://m.example.org", user_name: "bob" }]; return { server: state.servers[0] }; }
    if (p === "/api/media/servers/abcd1234" && o.method === "DELETE") { state.servers = []; return { ok: true }; }
    if (p.endsWith("/home")) return { libraries: [{ id: SHOW, name: "Shows", type: "CollectionFolder", folder: true, collection: "tvshows", has_image: false }, { id: MUSIC, name: "Songs", type: "CollectionFolder", folder: true, collection: "music", has_image: false }],
      resume: [{ id: ITEM, name: "Half Seen", type: "Movie", media: "video", progress: 40, has_image: true }], latest: [] };
    if (p.endsWith("/items") && new RegExp("parent=" + MUSIC).test(query)) return { items: TRACKS, total: 3 };
    if (p.endsWith("/items") && /parent=/.test(query)) return { items: [{ id: ITEM, name: "Pilot", type: "Episode", series: "Show", season: 1, episode: 1, media: "video", has_image: false }], total: 1 };
    if (p.endsWith("/items") && /q=/.test(query)) return { items: [{ id: ITEM, name: "Found It", type: "Movie", media: "video" }], total: 1 };
    if (p.includes("/item/")) return { id: ITEM, name: "Pilot", type: "Episode", media: "video", overview: "The first one.", genres: ["Drama"], container: "mkv", play: state.play };
    if (p.includes("/played/")) return { ok: true };
    return {};
  };
  return { handler, state };
}

(async () => {
  const { handler, state } = fake();
  const { w, calls } = await open(handler);
  const text = () => w.document.body.textContent;

  // nobody connected yet: the connect form, never a server list
  assert.match(text(), /Connect your media server/);
  assert.ok(w.document.querySelector('input[type="password"]'));
  const inputs = [...w.document.querySelectorAll("input")];
  typeInto(w, inputs[0], "https://m.example.org"); typeInto(w, inputs[1], "bob"); typeInto(w, inputs[2], "pw-123");
  await settle();
  w.document.querySelector("form").dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
  await settle(250);
  const post = calls.find(c => c[0] === "POST");
  assert.deepEqual(post[2], { kind: "jellyfin", url: "https://m.example.org", username: "bob", password: "pw-123", label: "" });

  // connected: libraries, continue watching and a search box; the password is gone from the page
  assert.match(text(), /Home · signed in as bob/);
  assert.match(text(), /Shows/); assert.match(text(), /Continue watching/); assert.match(text(), /Half Seen/);
  assert.ok(!/pw-123/.test(w.document.body.innerHTML));
  const img = w.document.querySelector(".md-hero-art");
  assert.equal(img.getAttribute("src"), "/api/media/servers/abcd1234/image/" + ITEM, "pictures come through this server, never a media server address");
  assert.ok(![...w.document.querySelectorAll("img,video,audio,a")].some(e => /m\.example\.org/.test(e.getAttribute("src") || e.getAttribute("href") || "")));

  // open a library, then an episode
  click(w, [...w.document.querySelectorAll(".md-tile")].find(t => /Shows/.test(t.textContent)));
  await settle();
  assert.ok(calls.some(c => c[1].startsWith("/api/media/servers/abcd1234/items?parent=" + SHOW)));
  assert.match(text(), /Pilot/); assert.match(text(), /Show S1E1/);
  click(w, w.document.querySelector(".md-tile"));
  await settle(250);
  assert.match(text(), /The first one\./);
  assert.ok(btn(w, /Play/));

  // play: the file as it is, then a converted copy when the browser cannot play it
  click(w, btn(w, /Play/));
  await settle();
  let media = w.document.querySelector("video");
  assert.match(media.getAttribute("src"), /\/stream\/0123456789abcdef0123456789abcdef\?mode=direct&kind=video$/);
  media.dispatchEvent(new w.Event("error"));
  await settle();
  media = w.document.querySelector("video");
  assert.match(media.getAttribute("src"), /mode=convert/);
  assert.match(text(), /converted copy/);
  media.dispatchEvent(new w.Event("ended"));
  await settle();
  assert.ok(calls.some(c => c[0] === "POST" && c[1] === "/api/media/servers/abcd1234/played/" + ITEM), "finishing marks it watched");
  click(w, btn(w, /^Close$/));
  await settle();

  // search
  typeInto(w, w.document.querySelector('input[type="search"]'), "found");
  await settle();
  w.document.querySelector("form.md-search").dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
  await settle(250);
  assert.ok(calls.some(c => c[1].includes("/items?q=found")));
  assert.match(text(), /Found It/);

  // an album is a track list; tapping a song plays it in the music bar and the bar goes on to the next one
  click(w, btn(w, /^Home$/));
  await settle(250);
  click(w, [...w.document.querySelectorAll(".md-chip")].find(t => /Songs/.test(t.textContent)));
  await settle(250);
  assert.equal(w.document.querySelectorAll(".md-track").length, 3);
  click(w, w.document.querySelectorAll(".md-track")[1]);
  await settle();
  let song = w.document.querySelector(".md-mini audio");
  assert.match(song.getAttribute("src"), /\/stream\/2{32}\?mode=direct&kind=audio$/);
  assert.match(text(), /Song 2/);
  song.dispatchEvent(new w.Event("ended"));
  await settle();
  assert.ok(calls.some(c => c[0] === "POST" && c[1].endsWith("/played/22222222222222222222222222222222")));
  assert.match(w.document.querySelector(".md-mini audio").getAttribute("src"), /\/stream\/3{32}\?/);
  assert.ok(btn(w, /⏭/).disabled, "no next track after the last one");
  click(w, btn(w, /✕/));
  await settle();
  assert.equal(w.document.querySelector(".md-mini"), null);

  // removing the only server goes back to the connect form
  click(w, btn(w, /^Remove$/));
  await settle(250);
  assert.ok(calls.some(c => c[0] === "DELETE"));
  assert.match(text(), /Connect your media server/);

  console.log("media tests passed");
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
