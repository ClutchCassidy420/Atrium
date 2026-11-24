/* Media: browse and play what is on your own Jellyfin or Emby server. Each person connects their own server with their own sign-in there;
   the token stays on the Atrium server, and pictures and playback come through it. Talks to the app only through /api/media
   (see app/media_backend.py). */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__, UI = window.__APP_UI__;
  if (!SDK || !REG || !UI) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect;
  var API = "/api/media", JSONH = { "Content-Type": "application/json" };

  function errText(e) { var m = String((e && e.message) || e || "Something went wrong"), i = m.indexOf("{"); if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* plain */ } } return m.replace(/^\d+:\s*/, ""); }
  function S(sid, path) { return API + "/servers/" + encodeURIComponent(sid) + path; }
  function sub(it) {
    var bits = [];
    if (it.type === "Episode") bits.push((it.series ? it.series + " " : "") + (it.season != null && it.episode != null ? "S" + it.season + "E" + it.episode : ""));
    else if (it.type === "Audio") bits.push(it.artist || it.album);
    else if (it.type === "MusicAlbum") bits.push(it.artist);
    if (it.year && it.type !== "Episode") bits.push(it.year);
    if (it.minutes && !it.folder) bits.push(it.minutes + " min");
    return bits.filter(Boolean).join(" · ");
  }
  function glyph(it) { return it.type === "Audio" ? "🎵" : it.type === "MusicAlbum" || it.collection === "music" ? "💿" : it.collection === "tvshows" || it.type === "Series" ? "📺" : it.folder ? "📁" : "🎞️"; }

  /* ---- connect ------------------------------------------------------------------------------------------------ */
  function Connect(props) {
    var K = UI.kit, kinds = props.info.kinds || [];
    var s = useState({ kind: kinds.length ? kinds[0].id : "jellyfin", url: "", username: "", password: "", label: "" }), f = s[0], setF = s[1];
    var b = useState(false), busy = b[0], setBusy = b[1], e = useState(""), err = e[0], setErr = e[1];
    function set(k, v) { var n = Object.assign({}, f); n[k] = v; setF(n); }
    function submit(ev) {
      ev.preventDefault(); setBusy(true); setErr("");
      SDK.fetchJSON(API + "/servers", { method: "POST", headers: JSONH, body: JSON.stringify({ kind: f.kind, url: f.url.trim(), username: f.username.trim(), password: f.password, label: f.label.trim() }) })
        .then(function (r) { setF(Object.assign({}, f, { password: "" })); props.done(r && r.server ? r.server.id : ""); }, function (x) { setBusy(false); setErr(errText(x)); });
    }
    function field(label, key, opts) {
      return h(K.Field, { label: label, required: !opts.optional, hint: opts.hint }, function (ip) {
        return h("input", Object.assign({}, ip, { className: "hm-input", type: opts.type || "text", autoComplete: opts.type === "password" ? "off" : "on", maxLength: opts.max || 200, required: !opts.optional, value: f[key], onChange: function (x) { set(key, x.target.value); } }));
      });
    }
    return h("div", { className: "md" }, h("h1", null, "Media"),
      h("form", { className: "hm-card md-connect", onSubmit: submit },
        h("h2", null, props.adding ? "Add another server" : "Connect your media server"),
        h("p", { className: "hm-sub" }, "Browse and play your own library here. Use the name and password you sign in with on that server. The password is used once and is not saved; Atrium keeps only a sign-in token, encrypted on this server."),
        err ? h(K.Alert, { tone: "danger" }, err) : null,
        h(K.Field, { label: "Server type" }, function (ip) {
          return h("select", Object.assign({}, ip, { className: "hm-select", value: f.kind, onChange: function (x) { set("kind", x.target.value); } }),
            kinds.map(function (k) { return h("option", { key: k.id, value: k.id }, k.label); }));
        }),
        field("Server address", "url", { hint: props.info.local_ok ? "Such as https://media.example.com, or http://192.168.1.20:8096 for a server on your network." : "Its public https address, such as https://media.example.com.", max: 300 }),
        field("User name", "username", { max: 128 }),
        field("Password", "password", { type: "password", max: 512 }),
        field("Name for this server (optional)", "label", { optional: true, max: 60 }),
        h("div", { className: "md-row" }, h(K.Button, { type: "submit", variant: "primary", loading: busy }, "Connect"),
          props.onCancel ? h(K.Button, { variant: "ghost", disabled: busy, onClick: props.onCancel }, "Cancel") : null)));
  }

  /* ---- tiles and rows ------------------------------------------------------------------------------------------ */
  function Tile(p) {
    var it = p.item;
    return h("button", { type: "button", className: "md-tile", onClick: function () { p.onOpen(it); } },
      h("span", { className: "md-art" }, it.has_image ? h("img", { src: S(p.sid, "/image/" + encodeURIComponent(it.id)), alt: "", loading: "lazy" }) : h("span", { className: "md-glyph", "aria-hidden": "true" }, glyph(it)),
        it.progress ? h("span", { className: "md-bar", style: { width: it.progress + "%" } }) : null, it.played ? h("span", { className: "md-seen", title: "Watched" }, "✓") : null),
      h("span", { className: "md-name" }, it.name), h("span", { className: "md-sub" }, sub(it)));
  }
  function Row(p) {
    if (!p.items || !p.items.length) return null;
    return h("section", { className: "md-section" }, h("h2", null, p.title), h("div", { className: "md-grid" }, p.items.map(function (it) { return h(Tile, { key: it.id, item: it, sid: p.sid, onOpen: p.onOpen }); })));
  }

  /* ---- playing ------------------------------------------------------------------------------------------------- */
  function Player(p) {
    var K = UI.kit, it = p.item, audio = it.media === "audio" || it.type === "Audio";
    var a = useState(it.play === "convert" ? "convert" : "direct"), mode = a[0], setMode = a[1], b = useState(""), note = b[0], setNote = b[1];
    var src = S(p.sid, "/stream/" + encodeURIComponent(it.id) + "?mode=" + mode + "&kind=" + (audio ? "audio" : "video"));
    function failed() { if (mode === "direct") { setNote("Your browser could not play this file as it is. Trying a converted copy…"); setMode("convert"); } else setNote("This could not be played. Open it in your media server's own app."); }
    function ended() { SDK.fetchJSON(S(p.sid, "/played/" + encodeURIComponent(it.id)), { method: "POST" }).catch(function () { /* marking it watched is optional */ }); }
    return h("div", { className: "md-player" }, note ? h(K.Alert, { tone: "info" }, note) : null,
      h(audio ? "audio" : "video", { key: mode, className: "md-media", controls: true, autoPlay: true, preload: "metadata", src: src, onError: failed, onEnded: ended }),
      mode === "convert" ? h("p", { className: "hm-fine" }, "Playing a converted copy: it can start slowly and cannot be skipped ahead.") : null);
  }
  function Detail(p) {
    var K = UI.kit, it = p.item, st = K.useAsync(function () { return SDK.fetchJSON(S(p.sid, "/item/" + encodeURIComponent(it.id))); }, [it.id]);
    var a = useState(false), play = a[0], setPlay = a[1];
    var d = st.data || it;
    return h(K.Dialog, { open: true, onClose: p.onClose, title: it.name, footer: h(K.Button, { onClick: p.onClose }, "Close") },
      st.status === "error" ? h(K.Alert, { tone: "danger" }, errText(st.error)) : null,
      play && st.data ? h(Player, { sid: p.sid, item: st.data }) : h("div", { className: "md-detail" },
        h("div", { className: "md-poster" }, it.has_image ? h("img", { src: S(p.sid, "/image/" + encodeURIComponent(it.id) + "?size=600"), alt: "" }) : h("span", { className: "md-glyph" }, glyph(it))),
        h("div", null, h("p", { className: "hm-sub" }, sub(d)), (d.genres || []).length ? h("p", { className: "hm-fine" }, d.genres.join(", ")) : null,
          d.overview ? h("p", null, d.overview) : null,
          d.media ? h(K.Button, { variant: "primary", disabled: !st.data, onClick: function () { setPlay(true); } }, "▶ Play") : null)));
  }

  /* ---- the page ------------------------------------------------------------------------------------------------ */
  function Library(p) {
    var K = UI.kit, sid = p.server.id;
    var a = useState([]), trail = a[0], setTrail = a[1], q = useState(""), query = q[0], setQuery = q[1], t = useState(""), term = t[0], setTerm = t[1];
    var o = useState(null), open = o[0], setOpen = o[1];
    var here = trail.length ? trail[trail.length - 1] : null;
    var home = K.useAsync(function () { return SDK.fetchJSON(S(sid, "/home")); }, [sid]);
    var list = K.useAsync(function () {
      if (term) return SDK.fetchJSON(S(sid, "/items?q=" + encodeURIComponent(term)));
      if (here) return SDK.fetchJSON(S(sid, "/items?parent=" + encodeURIComponent(here.id) + "&limit=100"));
      return Promise.resolve({ items: [] });
    }, [sid, term, here && here.id]);
    function openItem(it) { if (it.folder || it.collection) { setTerm(""); setQuery(""); setTrail(trail.concat([{ id: it.id, name: it.name }])); } else setOpen(it); }
    function search(ev) { ev.preventDefault(); setTrail([]); setTerm(query.trim()); }
    var showing = term || here;
    return h("div", null,
      h("form", { className: "md-search", onSubmit: search, role: "search" },
        h("input", { className: "hm-input", type: "search", placeholder: "Search this server", "aria-label": "Search this server", maxLength: 100, value: query, onChange: function (x) { setQuery(x.target.value); } }),
        h(K.Button, { type: "submit" }, "Search"), showing ? h(K.Button, { variant: "ghost", onClick: function () { setTrail([]); setTerm(""); setQuery(""); } }, "Home") : null),
      showing ? h("nav", { className: "md-crumbs", "aria-label": "Where you are" }, term ? h("span", null, "Results for “" + term + "”") : trail.map(function (c, i) {
        return h("button", { key: c.id, type: "button", className: "hm-btn hm-btn-ghost", onClick: function () { setTrail(trail.slice(0, i + 1)); } }, (i ? "› " : "") + c.name);
      })) : null,
      showing ? h(K.AsyncView, { state: list, skeleton: h(K.Skeleton, { lines: 4 }), isEmpty: function (d) { return !d || !d.items.length; },
          empty: h(K.EmptyState, { icon: "🎞️", title: term ? "Nothing found" : "This is empty" }) },
        function (d) { return h("div", { className: "md-grid" }, d.items.map(function (it) { return h(Tile, { key: it.id, item: it, sid: sid, onOpen: openItem }); })); })
        : h(K.AsyncView, { state: home, skeleton: h(K.Skeleton, { lines: 5 }), isEmpty: function (d) { return !d.libraries.length; }, empty: h(K.EmptyState, { icon: "🎬", title: "No libraries yet", description: "Add some on your media server." }) },
          function (d) { return h("div", null, h(Row, { title: "Libraries", items: d.libraries, sid: sid, onOpen: openItem }), h(Row, { title: "Continue watching", items: d.resume, sid: sid, onOpen: openItem }),
            h(Row, { title: "Recently added", items: d.latest, sid: sid, onOpen: openItem })); }),
      open ? h(Detail, { key: open.id, sid: sid, item: open, onClose: function () { setOpen(null); if (home.reload) home.reload(); } }) : null);
  }

  function Page() {
    var K = UI.kit;
    var a = useState(null), info = a[0], setInfo = a[1], b = useState(""), pick = b[0], setPick = b[1], c = useState(false), adding = c[0], setAdding = c[1], n = useState(0), tick = n[0], setTick = n[1];
    useEffect(function () { var live = true; SDK.fetchJSON(API + "/servers").then(function (r) { if (live) setInfo(r); }, function (x) { if (live) setInfo({ servers: [], kinds: [], error: errText(x) }); }); return function () { live = false; }; }, [tick]);
    function reload(id) { if (id) setPick(id); setAdding(false); setInfo(null); setTick(tick + 1); }
    if (!info) return h("div", { className: "md" }, h(K.Loading, { label: "Loading…" }));
    if (info.error) return h("div", { className: "md" }, h("h1", null, "Media"), h(K.Alert, { tone: "danger" }, info.error));
    if (!info.servers.length || adding) return h(Connect, { info: info, adding: adding, onCancel: adding ? function () { setAdding(false); } : null, done: reload });
    var server = info.servers.filter(function (s) { return s.id === pick; })[0] || info.servers[0];
    return h("div", { className: "md" }, h("h1", null, "Media"),
      h("p", { className: "hm-fine md-bar-line" },
        info.servers.length > 1 ? h("select", { className: "hm-select", "aria-label": "Media server", value: server.id, onChange: function (x) { setPick(x.target.value); } },
          info.servers.map(function (s) { return h("option", { key: s.id, value: s.id }, s.label + " (" + s.user_name + ")"); })) : h("span", null, server.label + " · signed in as " + server.user_name),
        info.servers.length < info.max ? h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: function () { setAdding(true); } }, "＋ Add another server") : null,
        h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: function () {
          if (window.confirm("Remove " + server.label + "? Atrium forgets your sign-in for it. Nothing changes on the server.")) {
            SDK.fetchJSON(API + "/servers/" + encodeURIComponent(server.id), { method: "DELETE" }).then(function () { setPick(""); reload(""); }, function (x) { window.alert(errText(x)); });
          }
        } }, "Remove")),
      h(Library, { key: server.id, server: server }));
  }
  REG.register("media-page", Page);
})();
