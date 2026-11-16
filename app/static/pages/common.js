/* Shared building blocks for the framework's own pages (Home, Systems). Plain script, no build step: it uses only the
   page-host interface (window.__HERMES_PLUGIN_SDK__), like every other page. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || window.__APP_UI__) return;
  var React = SDK.React, h = React.createElement, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect;
  var UI = { h: h, React: React, SDK: SDK };

  UI.config = function () { return window.__APP_CONFIG__ || { brand: "", agentName: "Assistant", name: "", user: "" }; };

  /* "3 min ago". Accepts seconds, milliseconds or an ISO string. */
  UI.ago = function (ts) {
    if (ts === null || ts === undefined || ts === "") return "never";
    var s = typeof ts === "number" ? (ts > 1e12 ? ts / 1000 : ts) : Date.parse(ts) / 1000;
    if (!isFinite(s)) return "unknown";
    var d = Math.max(0, Date.now() / 1000 - s);
    if (d < 90) return "just now";
    if (d < 5400) return Math.round(d / 60) + " min ago";
    if (d < 172800) return Math.round(d / 3600) + " h ago";
    return Math.round(d / 86400) + " days ago";
  };
  UI.until = function (ts) {
    var s = typeof ts === "number" ? ts : Date.parse(ts) / 1000;
    if (!isFinite(s)) return "";
    var d = s - Date.now() / 1000;
    if (d < 60) return "soon";
    if (d < 5400) return "in " + Math.round(d / 60) + " min";
    if (d < 172800) return "in " + Math.round(d / 3600) + " h";
    return "in " + Math.round(d / 86400) + " days";
  };
  UI.gb = function (n, digits) { return (n / 1073741824).toFixed(digits === undefined ? 1 : digits); };
  UI.num = function (n) {
    if (n === null || n === undefined) return "-";
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1e4) return Math.round(n / 1e3) + "K";
    if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
    return String(n);
  };
  UI.greeting = function () {
    var hr = new Date().getHours();
    return hr < 5 ? "Good night" : hr < 12 ? "Good morning" : hr < 18 ? "Good afternoon" : "Good evening";
  };

  UI.Card = function (p) {
    return h("section", { className: "hm-card " + (p.className || "") },
      p.title ? h("header", { className: "hm-card-head" }, h("h2", null, p.title), p.aside ? h("span", { className: "hm-aside" }, p.aside) : null) : null,
      p.children);
  };
  UI.Dot = function (p) { return h("span", { className: "hm-dot hm-" + (p.tone || "mute"), "aria-hidden": "true" }); };
  UI.Chip = function (p) { return h("span", { className: "hm-chip hm-chip-" + (p.tone || "mute") }, p.dot ? h(UI.Dot, { tone: p.tone }) : null, p.children); };
  UI.Stat = function (p) {
    return h("div", { className: "hm-stat" }, h("div", { className: "hm-stat-n" }, p.value), h("div", { className: "hm-stat-l" }, p.label), p.sub ? h("div", { className: "hm-sub" }, p.sub) : null);
  };
  UI.Bar = function (p) {
    var v = Math.max(0, Math.min(100, p.pct || 0));
    var tone = p.tone || (v > 90 ? "bad" : v > 75 ? "warn" : "ok");
    return h("div", { className: "hm-bar", role: "img", "aria-label": (p.label || "") + " " + Math.round(v) + " percent" }, h("div", { className: "hm-bar-fill hm-bg-" + tone, style: { width: v + "%" } }));
  };
  UI.Empty = function (p) { return h("p", { className: "hm-empty" }, p.children); };

  /* a ring gauge, 0 to 100 */
  UI.Ring = function (p) {
    var size = p.size || 168, stroke = 14, r = (size - stroke) / 2, c = 2 * Math.PI * r, v = Math.max(0, Math.min(100, p.value || 0));
    return h("div", { className: "hm-ring", style: { width: size + "px", height: size + "px" } },
      h("svg", { width: size, height: size, viewBox: "0 0 " + size + " " + size, "aria-hidden": "true" },
        h("circle", { cx: size / 2, cy: size / 2, r: r, fill: "none", strokeWidth: stroke, className: "hm-ring-track" }),
        h("circle", { cx: size / 2, cy: size / 2, r: r, fill: "none", strokeWidth: stroke, strokeLinecap: "round", className: "hm-ring-arc",
          strokeDasharray: c, strokeDashoffset: c * (1 - v / 100), transform: "rotate(-90 " + size / 2 + " " + size / 2 + ")" })),
      h("div", { className: "hm-ring-text" }, h("strong", null, Math.round(v)), h("span", null, p.label || "")));
  };

  /* load a URL now and again every `ms`; returns [data, error] */
  UI.usePoll = function (url, ms) {
    var a = useState(null), data = a[0], setData = a[1];
    var b = useState(null), err = b[0], setErr = b[1];
    useEffect(function () {
      var alive = true, timer = null;
      function load() {
        SDK.fetchJSON(url).then(function (d) { if (alive) { setData(d); setErr(null); } }, function (e) { if (alive) setErr(e.message || "Could not load"); })
          .then(function () { if (alive && ms) timer = setTimeout(load, ms); });
      }
      load();
      return function () { alive = false; if (timer) clearTimeout(timer); };
    }, [url]);
    return [data, err];
  };

  window.__APP_UI__ = UI;
})();
