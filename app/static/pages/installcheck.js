/* Install check: runs the checks a browser makes before it offers "install as an app", in the browser you are using, and says in plain
   words what passes and what does not. A framework page; it only reads from the app's own phone-app files. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__, UI = window.__APP_UI__;
  if (!SDK || !REG || !UI) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect, Card = UI.Card, Dot = UI.Dot;

  function loadImage(url) {
    return fetch(url, { credentials: "include" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.blob();
    }).then(function (b) { return createImageBitmap(b).then(function (bmp) { return { type: b.type, w: bmp.width, h: bmp.height }; }); });
  }

  function run(pwa, add) {
    var ua = navigator.userAgent || "", ios = /iPad|iPhone|iPod/.test(ua), firefox = /Firefox\//.test(ua) && !/Seamonkey/.test(ua);
    add(window.isSecureContext ? "ok" : "bad", "Secure connection (https)", window.isSecureContext ? "Yes." : "No. Browsers only install apps from https addresses.");
    var standalone = !!((window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || window.navigator.standalone);
    add(standalone ? "ok" : "info", "Running as an installed app", standalone ? "Yes, you are already using the installed app." : "No, this is a normal browser tab.");
    add("info", "Your browser", ua.slice(0, 140) + (ios ? "  (iPhone or iPad: no install button exists; use Share, then Add to Home Screen)" : firefox ? "  (Firefox on a computer cannot install web apps)" : ""));
    if (!pwa) { add("bad", "Phone app switched on", "No. This install has not turned the phone app on (PUSH_ENABLED=1), so the browser is never offered the app."); return Promise.resolve(); }
    add(("serviceWorker" in navigator) ? "ok" : "bad", "Service workers supported", ("serviceWorker" in navigator) ? "Yes." : "No. This browser (or this window, for example an embedded browser) cannot run the part an installable app needs.");
    add(("PushManager" in window) ? "ok" : "info", "Push notifications supported", ("PushManager" in window) ? "Yes." : "No (notifications will not work here, but install can).");
    var link = document.querySelector("link[rel=manifest]");
    add(link ? "ok" : "bad", "Manifest linked on this page", link ? link.getAttribute("href") : "No link found.");
    var chain = fetch(pwa.manifest, { credentials: "include" }).then(function (r) {
      add(r.ok ? "ok" : "bad", "Manifest loads", "HTTP " + r.status + " " + (r.headers.get("content-type") || ""));
      return r.ok ? r.json() : null;
    }).catch(function (e) { add("bad", "Manifest loads", String(e.message || e)); return null; });
    return chain.then(function (m) {
      if (m) {
        var sizes = (m.icons || []).map(function (i) { return i.sizes; });
        add(m.name && m.start_url && m.display === "standalone" ? "ok" : "bad", "Manifest has a name, a start page and standalone display", JSON.stringify({ name: m.name, start_url: m.start_url, display: m.display, scope: m.scope }));
        add(sizes.indexOf("192x192") >= 0 && sizes.indexOf("512x512") >= 0 ? "ok" : "bad", "Icons of 192 and 512 pixels declared", sizes.join(", "));
        return Promise.all((m.icons || []).map(function (i) {
          return loadImage(new URL(i.src, location.href)).then(function (p) { add("ok", "Icon loads: " + i.sizes, p.type + " " + p.w + "x" + p.h); }, function (e) { add("bad", "Icon loads: " + i.sizes, String(e.message || e)); });
        }));
      }
    }).then(function () {
      if (!("serviceWorker" in navigator)) return;
      return navigator.serviceWorker.register(pwa.sw, { scope: "/" }).then(function () { return navigator.serviceWorker.ready; }).then(function (reg) {
        add("ok", "Service worker registered and active", reg.scope);
      }, function (e) { add("bad", "Service worker registered and active", String(e && e.message || e)); });
    });
  }

  function Page() {
    var cfg = UI.config(), a = useState([]), rows = a[0], setRows = a[1];
    var b = useState("waiting"), offer = b[0], setOffer = b[1];
    var c = useState(false), done = c[0], setDone = c[1];
    useEffect(function () {
      var alive = true, list = [], timer = null;
      function add(level, title, detail) { list = list.concat([{ level: level, title: title, detail: detail }]); if (alive) setRows(list); }
      function onOffer() { if (alive) setOffer("yes"); }
      window.addEventListener("beforeinstallprompt", onOffer);
      var started = Date.now();
      run(cfg.pwa, add).then(function () {
        if (alive) setDone(true);
        timer = setTimeout(function () { if (alive) setOffer(function (o) { return o === "yes" ? o : "no"; }); }, Math.max(0, 8000 - (Date.now() - started)));
      });
      return function () { alive = false; window.removeEventListener("beforeinstallprompt", onOffer); if (timer) clearTimeout(timer); };
    }, []);
    var bad = rows.filter(function (r) { return r.level === "bad"; });
    var installed = rows.some(function (r) { return r.title === "Running as an installed app" && r.level === "ok"; });
    var verdict = installed ? ["ok", "You are already using the installed app, so the browser has nothing more to offer. (A shortcut-style install, with a small browser badge on the icon, behaves the same way for you.)"] : offer === "yes" ? ["ok", "Your browser is offering to install the app. Use the Install app button in the sidebar."]
      : bad.length ? ["bad", "Something above is blocking installation: " + bad[0].title + "."]
      : offer === "no" ? ["warn", "Everything checks out, but the browser has not offered to install yet. Some browsers wait a little, or have already been told no; try the browser's own menu (Install app, or Add to Home screen)."]
      : ["warn", "Checking…"];
    return h("div", { className: "hm" },
      h("header", { className: "hm-top" }, h("div", null, h("h1", null, "Install check"), h("p", { className: "hm-sub" }, "What this browser needs before it will install the app, and what it finds."))),
      h("section", { className: "hm-card" }, h("div", { className: "hm-ok", style: { color: "var(--color-" + (verdict[0] === "ok" ? "success" : verdict[0] === "bad" ? "destructive" : "warning") + ")" } }, h(Dot, { tone: verdict[0] }), verdict[1])),
      h(Card, { title: "Checks", aside: done ? "done" : "running" },
        h("ul", { className: "hm-list" }, rows.map(function (r, i) {
          return h("li", { key: i, className: "hm-item" }, h(Dot, { tone: r.level === "ok" ? "ok" : r.level === "bad" ? "bad" : "mute" }),
            h("div", null, h("div", { className: "hm-item-t" }, r.title), h("div", { className: "hm-sub", style: { wordBreak: "break-word" } }, r.detail)));
        }).concat([h("li", { key: "offer", className: "hm-item" }, h(Dot, { tone: offer === "yes" ? "ok" : offer === "no" ? "warn" : "mute" }),
          h("div", null, h("div", { className: "hm-item-t" }, "The browser offers to install"), h("div", { className: "hm-sub" }, offer === "yes" ? "Yes." : offer === "no" ? "Not yet (waited 8 seconds)." : "Waiting up to 8 seconds…")))]))));
  }

  REG.register("install-check-page", Page);
})();
