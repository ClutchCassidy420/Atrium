/**
 * App shim: gives the plugin pages the small host interface they were written for.
 *
 * The plugin scripts expect, from the Hermes dashboard that used to host them:
 *   window.__HERMES_PLUGIN_SDK__  React, hooks, fetchJSON (+ authedFetch)
 *   window.__HERMES_PLUGINS__     register(name, Component) and registerSlot(slot, name, Component)
 *   window.__HERMES_SESSION_TOKEN__ (a string; here the app's own cookie does the authenticating, so it is empty)
 * This file provides exactly that, mounts the page whose route matches the address, and shows a nav. Nothing in the
 * plugin scripts has to change. Contract reference: docs/STAGE0-FINDINGS.md.
 */
(function () {
  "use strict";
  var React = window.React, ReactDOM = window.ReactDOM;
  if (!React || !ReactDOM) { document.body.textContent = "The page could not start (React is missing)."; return; }

  /* ---- fetch helpers (same behaviour as the dashboard's) ---------------------------------------------------- */
  function authedFetch(url, init) {
    var o = Object.assign({ credentials: "same-origin" }, init || {});
    return fetch(url, o);
  }
  function fetchJSON(url, init, options) {
    return authedFetch(url, init).then(function (res) {
      if (res.status === 401 && !(options && options.allowUnauthorized)) {
        window.location.assign("/login");
        throw new Error("401: not signed in");
      }
      return res.text().then(function (text) {
        if (!res.ok) throw new Error(res.status + ": " + text);
        if (!text) return null;
        try { return JSON.parse(text); } catch (e) { return text; }
      });
    });
  }
  /* ---- background-friendly polling ------------------------------------------------------------------------- */
  // atriumEvery(fn, ms) runs fn every ms like setInterval, but not while the tab is hidden: a page left open in the
  // background stops asking the server (and Hermes) for data nobody is looking at. When the tab comes back, fn runs once
  // straight away if a tick was skipped, so the page is fresh again. Returns a function that stops it. Use it for pure
  // data refreshes only: anything that must keep working in the background (speech, recording, run watching) keeps setInterval.
  function atriumEvery(fn, ms) {
    var missed = false;
    var timer = setInterval(function () { if (document.hidden) { missed = true; } else { missed = false; fn(); } }, ms);
    function onShow() { if (!document.hidden && missed) { missed = false; fn(); } }
    document.addEventListener("visibilitychange", onShow);
    return function stop() { clearInterval(timer); document.removeEventListener("visibilitychange", onShow); };
  }
  window.atriumEvery = atriumEvery;

  function buildWsAuthParam() { return Promise.resolve(["", ""]); }   // the cookie authenticates sockets too
  function buildWsUrl(path, params) {
    var q = new URLSearchParams(params || {}).toString();
    return Promise.resolve((location.protocol === "https:" ? "wss://" : "ws://") + location.host + path + (q ? "?" + q : ""));
  }

  /* ---- toast hook (the dashboard offers one; plugins here do not use it, but the contract does) --------------- */
  function useToast() {
    var s = React.useState(null), toast = s[0], set = s[1];
    return { toast: toast, showToast: function (message, type) { set({ message: message, type: type || "success" }); } };
  }

  window.__HERMES_SESSION_TOKEN__ = "";
  window.__HERMES_PLUGIN_SDK__ = {
    sdkVersion: "app-shim-1",
    React: React,
    hooks: {
      useState: React.useState, useEffect: React.useEffect, useCallback: React.useCallback, useMemo: React.useMemo,
      useRef: React.useRef, useContext: React.useContext, createContext: React.createContext, useToast: useToast
    },
    api: {},
    fetchJSON: fetchJSON, authedFetch: authedFetch, buildWsUrl: buildWsUrl, buildWsAuthParam: buildWsAuthParam
  };

  /* ---- registry ---------------------------------------------------------------------------------------------- */
  var pages = {};      // plugin name -> component
  var slots = {};      // slot -> [{ name, component }]
  var listeners = [];
  function changed() { listeners.slice().forEach(function (f) { try { f(); } catch (e) { /* ignore */ } }); }
  window.__HERMES_PLUGINS__ = {
    register: function (name, component) { pages[name] = component; changed(); },
    registerSlot: function (slot, name, component) { (slots[slot] = slots[slot] || []).push({ name: name, component: component }); changed(); }
  };

  /* ---- routes: which plugin page lives at which address (same paths the dashboard used) ---------------------- */
  var CONFIG = window.__APP_CONFIG__ || { brand: "", agentName: "Assistant", pages: [] };   // from /shell-config.js
  var ROUTES = CONFIG.pages || [];                 // [{ id, path, plugin, label, scripts[], styles[] }], already limited to this user's role
  var e = React.createElement;

  function currentRoute() {
    var p = window.location.pathname.replace(/\/+$/, "") || "/";
    for (var i = 0; i < ROUTES.length; i++) if (ROUTES[i].path === p) return ROUTES[i];
    return null;
  }

  function loadAssets(route) {
    (route.styles || []).forEach(function (href) {
      if (document.querySelector('link[href="' + href + '"]')) return;
      var l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; document.head.appendChild(l);
    });
    return (route.scripts || []).reduce(function (chain, src) {
      return chain.then(function () {
        return new Promise(function (resolve, reject) {
          var s = document.createElement("script"); s.src = src; s.onload = resolve; s.onerror = function () { reject(new Error("Could not load " + src)); };
          document.head.appendChild(s);
        });
      });
    }, Promise.resolve());
  }

  /* a plugin page that crashes must not take the shell down with it */
  class Boundary extends React.Component {
    constructor(p) { super(p); this.state = { err: null }; }
    static getDerivedStateFromError(err) { return { err: err }; }
    render() {
      if (this.state.err) return e("div", { className: "vn-card" }, e("h2", null, "This page hit a problem"), e("pre", { className: "vn-pre" }, String(this.state.err && this.state.err.message || this.state.err)));
      return this.props.children;
    }
  }

  function Link(props) {
    var ok = props.ok;
    return e("li", null, e("span", { className: "vn-dot " + (ok === null ? "vn-dot-wait" : ok ? "vn-dot-ok" : "vn-dot-bad"), "aria-hidden": "true" }), props.label,
      e("span", { className: "vn-sub" }, ok === null ? " checking…" : ok ? " connected" : " not reachable"));
  }
  function Connections() {
    var st = React.useState(null), h = st[0], setH = st[1];
    React.useEffect(function () {
      fetchJSON("/api/health").then(setH, function (err) { setH(/^403/.test(err.message) ? "hidden" : { api: {}, dashboard: {}, data: {} }); });
    }, []);
    if (h === "hidden") return null;                       // not part of this user's role
    var d = h && h.data || {}, all = h ? Object.keys(d).length > 0 && Object.keys(d).every(function (k) { return d[k]; }) : null;
    return e("div", { className: "vn-card" }, e("h2", null, "Connections"),
      e("ul", { className: "vn-conn" },
        e(Link, { label: "Hermes API", ok: h ? !!(h.api && h.api.reachable && h.api.key_ok) : null }),
        e(Link, { label: "Hermes dashboard data", ok: h ? !!(h.dashboard && h.dashboard.reachable && h.dashboard.token_ok) : null }),
        e(Link, { label: "Hermes data folders", ok: all })));
  }

  function Home() {
    return e(React.Fragment, null,
      e("div", { className: "vn-card" },
        e("h2", null, CONFIG.brand || "Home"),
        e("p", { className: "vn-sub" }, "Signed in as " + (CONFIG.user || "you") + ". " + (ROUTES.length ? "Pick a page." : "No pages are available for your role yet.")),
        ROUTES.length ? e("ul", { className: "vn-list" }, ROUTES.map(function (r) { return e("li", { key: r.path }, e("a", { href: r.path }, r.label)); })) : null),
      e(Connections));
  }

  function Slots(props) {
    var list = slots[props.slot] || [];
    return e(React.Fragment, null, list.map(function (s) { return e(Boundary, { key: s.name }, e(s.component)); }));
  }

  function App() {
    var route = currentRoute();
    var st = React.useState(0), setTick = st[1];
    var ls = React.useState(route ? "loading" : "ready"), load = ls[0], setLoad = ls[1];
    React.useEffect(function () {
      listeners.push(function () { setTick(function (t) { return t + 1; }); });
      if (!route) return;
      loadAssets(route).then(function () { setLoad("ready"); }, function (err) { setLoad(err); });
    }, []);
    var body;
    if (!route) body = e(Home);
    else if (load === "loading") body = e("div", { className: "vn-card vn-sub" }, "Loading…");
    else if (load instanceof Error) body = e("div", { className: "vn-card" }, e("h2", null, "Could not load this page"), e("pre", { className: "vn-pre" }, load.message));
    else if (!pages[route.plugin]) body = e("div", { className: "vn-card" }, e("h2", null, "This page did not start"), e("p", { className: "vn-sub" }, "The script loaded but did not register " + route.plugin + "."));
    else body = e(Boundary, null, e(pages[route.plugin]));
    return e(React.Fragment, null, body, e(Slots, { slot: route ? route.plugin : "" }));
  }

  /* ---- the sidebar: top-level links, then dropdown sections --------------------------------------------------------- */
  function store(key, value) {
    try { if (value === undefined) return JSON.parse(window.localStorage.getItem(key) || "null"); window.localStorage.setItem(key, JSON.stringify(value)); } catch (x) { /* storage blocked */ }
    return null;
  }
  function sortPages(list) {
    return list.slice().sort(function (a, b) { return ((a.order === undefined ? 100 : a.order) - (b.order === undefined ? 100 : b.order)) || String(a.label).localeCompare(String(b.label)); });
  }
  function el(tag, attrs, text) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text !== undefined) n.textContent = text;
    return n;
  }

  /* A colored tile with a symbol or emoji in front of every sidebar entry. The color comes from a small palette, picked by the name, so the same
     entry always has the same color. Entries without an icon get the first letter of their name. */
  var HUES = [330, 205, 150, 35, 270, 8, 180, 52, 300, 120];
  var DEFAULT_ICONS = { Agent: "\uD83E\uDD16", Work: "\uD83D\uDDC2\uFE0F", Server: "\uD83D\uDDA5\uFE0F", Developer: "\uD83E\uDDF0", Tools: "\uD83D\uDEE0\uFE0F", Chat: "\uD83D\uDCAC" };
  function tile(name, icon) {
    var s = 0, i;
    for (i = 0; i < name.length; i++) s = (s * 31 + name.charCodeAt(i)) % 9973;
    var t = el("span", { "class": "vn-ico", "aria-hidden": "true", style: "--h:" + HUES[s % HUES.length] }, icon || name.charAt(0).toUpperCase());
    return t;
  }
  function labelled(name, icon) {
    var w = el("span", { "class": "vn-l" });
    w.appendChild(tile(name, icon));
    w.appendChild(el("span", { "class": "vn-lt" }, name));
    return w;
  }

  function nav() {
    var remembered = CONFIG.accordionNav ? {} : store("nav.open") || {};
    var host = document.getElementById("vn-nav");
    if (!host) return;
    var cur = window.location.pathname.replace(/\/+$/, "") || "/";
    var listed0 = ROUTES.filter(function (r) { return r.nav !== "gear"; });
    var pagesAll = listed0.some(function (r) { return r.path === "/"; }) ? listed0 : [{ path: "/", label: "Home", order: 0 }].concat(listed0);
    /* a page with nav "gear" is reached by the gear icon in the sidebar footer; the Sign out button then lives on that page. Users who cannot open it keep the Sign out button. */
    var gear = ROUTES.filter(function (r) { return r.nav === "gear"; })[0], gearEl = document.getElementById("vn-gear"), outEl = document.getElementById("vn-out");
    if (gear && gearEl) { gearEl.href = gear.path; gearEl.setAttribute("aria-label", gear.label); gearEl.title = gear.label; gearEl.hidden = false; if (gear.path === cur) gearEl.setAttribute("aria-current", "page"); if (outEl) outEl.hidden = true; }
    var top = sortPages(pagesAll.filter(function (p) { return !p.group; }));
    var groups = {};
    pagesAll.forEach(function (p) { if (p.group) (groups[p.group] = groups[p.group] || []).push(p); });
    var listed = (CONFIG.navGroups || []).filter(function (g) { return groups[g]; });
    var names = listed.concat(Object.keys(groups).filter(function (g) { return listed.indexOf(g) < 0; }).sort());
    // accordion: only the folder that holds the current page starts open, and opening one folder closes its siblings
    function setOpen(w, b, on) { w.classList.toggle("vn-open", on); b.setAttribute("aria-expanded", String(on)); }
    function closeSiblings(w, container) {
      Array.prototype.forEach.call(container.children, function (c) {
        if (c !== w && c.classList && c.classList.contains("vn-group")) { var cb = c.firstChild; setOpen(c, cb, false); }
      });
    }
    host.innerHTML = "";

    function link(p, parent) {
      var a = el("a", { href: p.path, title: p.label, "aria-label": p.label });
      a.appendChild(labelled(p.label, p.icon));
      if (p.path === cur) a.setAttribute("aria-current", "page");
      parent.appendChild(a);
    }
    top.forEach(function (p) { link(p, host); });
    names.forEach(function (g, i) {
      var items = sortPages(groups[g]), here = items.some(function (p) { return p.path === cur; });
      var isOpen = here || remembered[g] === true;
      var wrap = el("div", { "class": "vn-group" + (isOpen ? " vn-open vn-current-group" : "") });
      var btn = el("button", { type: "button", "class": "vn-group-btn", title: g, "aria-label": g, "aria-expanded": String(isOpen), "aria-controls": "vn-sub-" + i });
      btn.appendChild(labelled(g, (CONFIG.navIcons || {})[g] || DEFAULT_ICONS[g]));
      btn.appendChild(el("span", { "class": "vn-chev", "aria-hidden": "true" }, "\u25B8"));
      var sub = el("div", { "class": "vn-sub", id: "vn-sub-" + i, role: "group", "aria-label": g });
      var folders = {}, order = [];
      items.forEach(function (p) {
        if (!p.subgroup) { order.push({ page: p }); return; }
        if (!folders[p.subgroup]) { folders[p.subgroup] = { name: p.subgroup, pages: [] }; order.push({ folder: folders[p.subgroup] }); }
        folders[p.subgroup].pages.push(p);
      });
      order.forEach(function (it, k) {
        if (it.page) { link(it.page, sub); return; }
        var f = it.folder, fhere = f.pages.some(function (p) { return p.path === cur; }), fopen = fhere || remembered[g + "/" + f.name] === true;
        var fw = el("div", { "class": "vn-group vn-folder" + (fopen ? " vn-open" : "") });
        var fb = el("button", { type: "button", "class": "vn-group-btn", title: f.name, "aria-label": f.name, "aria-expanded": String(fopen), "aria-controls": "vn-sub-" + i + "-" + k });
        fb.appendChild(labelled(f.name, (CONFIG.navIcons || {})[f.name] || DEFAULT_ICONS[f.name]));
        fb.appendChild(el("span", { "class": "vn-chev", "aria-hidden": "true" }, "▸"));
        var fs = el("div", { "class": "vn-sub", id: "vn-sub-" + i + "-" + k, role: "group", "aria-label": f.name });
        f.pages.forEach(function (p) { link(p, fs); });
        fb.addEventListener("click", function () {
          var on = !fw.classList.contains("vn-open");
          if (CONFIG.accordionNav && on) closeSiblings(fw, sub);
          if (!CONFIG.accordionNav) { remembered[g + "/" + f.name] = on; store("nav.open", remembered); }
          setOpen(fw, fb, on);
        });
        fw.appendChild(fb); fw.appendChild(fs); sub.appendChild(fw);
      });
      btn.addEventListener("click", function () {
        var now = !wrap.classList.contains("vn-open");
        if (CONFIG.accordionNav && now) closeSiblings(wrap, host);
        if (!CONFIG.accordionNav) { remembered[g] = now; store("nav.open", remembered); }
        setOpen(wrap, btn, now);
      });
      wrap.appendChild(btn); wrap.appendChild(sub); host.appendChild(wrap);
    });

    var shellEl = document.getElementById("vn-shell"), menu = document.getElementById("vn-menu");
    var head = shellEl && shellEl.querySelector(".vn-side-head");
    if (head) {
      var toggle = el("button", { type: "button", "class": "vn-icon vn-rail-toggle", "aria-controls": "vn-nav" });
      var glyph = el("span", { "aria-hidden": "true" });
      toggle.appendChild(glyph);
      head.appendChild(toggle);
      function setRail(on) {
        var label = on ? "Expand sidebar" : "Collapse sidebar";
        shellEl.classList.toggle("vn-rail", on);
        glyph.textContent = on ? "›" : "‹";
        toggle.title = label;
        toggle.setAttribute("aria-label", label);
        toggle.setAttribute("aria-expanded", String(!on));
      }
      setRail(store("nav.rail") === true);               // remembered per browser
      toggle.addEventListener("click", function () {
        var on = !shellEl.classList.contains("vn-rail");
        setRail(on);
        store("nav.rail", on);
      });
    }
    var signOut = outEl && outEl.querySelector("button");
    if (signOut) signOut.title = "Sign out";

    /* phone: the sidebar is a drawer */
    var side = document.getElementById("vn-side");
    function setDrawer(on) {
      if (!shellEl) return;
      shellEl.classList.toggle("vn-drawer-open", on);
      if (menu) menu.setAttribute("aria-expanded", String(on));
      if (on) { var first = host.querySelector("a, button"); if (first) first.focus(); } else if (menu && side && side.contains(document.activeElement)) menu.focus();
    }
    if (menu) menu.addEventListener("click", function () { setDrawer(!shellEl.classList.contains("vn-drawer-open")); });
    var scrim = document.getElementById("vn-scrim"), close = document.getElementById("vn-close");
    if (scrim) scrim.addEventListener("click", function () { setDrawer(false); });
    if (close) close.addEventListener("click", function () { setDrawer(false); });
    document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && shellEl && shellEl.classList.contains("vn-drawer-open")) setDrawer(false); });
  }

  /* ---- install as an app, from any page -------------------------------------------------------------------------------------
     When the install has the phone app turned on, every page links the manifest and registers the service worker, so the browser can
     offer installation anywhere. The browser's own offer is kept and shown as a button in the sidebar; on iPhone and iPad (which have
     no such offer) a short hint says how to do it from Share. */
  function setupInstall() {
    var pwa = CONFIG.pwa, box = document.getElementById("vn-install");
    if (!pwa || !box) return;
    try {
      if (!document.querySelector("link[rel=manifest]")) { var l = el("link", { rel: "manifest", href: pwa.manifest }); l.crossOrigin = "use-credentials"; document.head.appendChild(l); }
      if (pwa.icon && !document.querySelector("link[rel=apple-touch-icon]")) document.head.appendChild(el("link", { rel: "apple-touch-icon", href: pwa.icon }));
      if (!document.querySelector("meta[name=theme-color]")) document.head.appendChild(el("meta", { name: "theme-color", content: "#06061a" }));
      if ("serviceWorker" in navigator) navigator.serviceWorker.register(pwa.sw, { scope: "/" }).catch(function () { /* blocked or unsupported: the app still works */ });
    } catch (x) { /* never break the page over this */ }
    var standalone = !!((window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || window.navigator.standalone);
    if (standalone) return;
    var offer = null;
    function show() {
      box.innerHTML = "";
      if (!offer) return;
      var b = el("button", { type: "button", "class": "vn-install-btn", title: "Install app" }, "Install app");
      b.addEventListener("click", function () {
        var e2 = offer; offer = null; show();
        try { e2.prompt(); } catch (x) { /* already used */ }
      });
      box.appendChild(b);
    }
    window.addEventListener("beforeinstallprompt", function (ev) { ev.preventDefault(); offer = ev; show(); });
    window.addEventListener("appinstalled", function () { offer = null; show(); });
    if (/iPad|iPhone|iPod/.test(navigator.userAgent || "")) box.appendChild(el("p", { "class": "vn-hint" }, "To install on iPhone or iPad: Share, then Add to Home Screen."));
  }

  function start() {
    nav();
    setupInstall();
    var root = document.getElementById("vn-root");
    ReactDOM.createRoot(root).render(e(App));
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
