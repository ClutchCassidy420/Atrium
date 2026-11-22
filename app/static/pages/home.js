/* Home: the agent and the work on one screen, with the server's health folded in for roles that may see it.
   A framework page: nothing here is specific to one install. Names come from the app config and from the data. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__, UI = window.__APP_UI__;
  if (!SDK || !REG || !UI) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect, useRef = SDK.hooks.useRef, useCallback = SDK.hooks.useCallback;
  var Card = UI.Card, Chip = UI.Chip, Dot = UI.Dot, Stat = UI.Stat, Bar = UI.Bar, Empty = UI.Empty;

  function Clock() {
    var a = useState(new Date()), now = a[0], set = a[1];
    useEffect(function () { var t = setInterval(function () { set(new Date()); }, 1000); return function () { clearInterval(t); }; }, []);
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return h("div", { className: "hm-clock" },
      h("div", { className: "hm-date" }, now.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })),
      h("div", { className: "hm-time" }, p(now.getHours()) + ":" + p(now.getMinutes()), h("span", null, ":" + p(now.getSeconds()))));
  }


  /* ---- motion and chart helpers (all plain SVG and CSS; nothing external) ---- */
  var uid = 0;
  var REDUCE = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  function useCountUp(target, ms) {
    var a = useState(REDUCE ? target : 0), v = a[0], set = a[1], last = useRef(0);
    useEffect(function () {
      if (REDUCE || typeof target !== "number" || !isFinite(target)) { set(target); last.current = target; return undefined; }
      var from = last.current, t0 = null, raf = 0;
      function step(ts) {
        if (t0 === null) t0 = ts;
        var p = Math.min(1, (ts - t0) / (ms || 1100)), e = 1 - Math.pow(1 - p, 3);
        set(from + (target - from) * e);
        if (p < 1) raf = requestAnimationFrame(step); else last.current = target;
      }
      raf = requestAnimationFrame(step);
      return function () { cancelAnimationFrame(raf); };
    }, [target]);
    return v;
  }
  function CountUp(p) {
    var v = useCountUp(p.value, p.ms);
    if (typeof v !== "number") return h("span", null, v === null || v === undefined ? "-" : v);
    var n = Math.round(v);
    return h("span", null, p.fmt ? p.fmt(n) : n);
  }
  function curve(pts) {
    var d = "M" + pts[0][0].toFixed(1) + "," + pts[0][1].toFixed(1);
    for (var i = 1; i < pts.length; i++) {
      var p0 = pts[i - 1], p1 = pts[i], cx = (p0[0] + p1[0]) / 2;
      d += " C" + cx.toFixed(1) + "," + p0[1].toFixed(1) + " " + cx.toFixed(1) + "," + p1[1].toFixed(1) + " " + p1[0].toFixed(1) + "," + p1[1].toFixed(1);
    }
    return d;
  }
  /* a smooth area chart; `big` adds a faint grid and an end dot */
  function Spark(p) {
    var vals = p.values || [], w = p.big ? 320 : 120, ht = p.big ? 96 : 34, pad = 3, idRef = useRef(null);
    if (!idRef.current) idRef.current = "hmg" + (++uid);
    if (vals.length < 2) return null;
    var max = Math.max.apply(null, vals.concat([1])), id = idRef.current;
    var pts = vals.map(function (v, i) { return [pad + i * (w - 2 * pad) / (vals.length - 1), ht - pad - (ht - 2 * pad) * v / max]; });
    var line = curve(pts), area = line + " L" + pts[pts.length - 1][0].toFixed(1) + "," + ht + " L" + pts[0][0].toFixed(1) + "," + ht + " Z";
    var grid = [];
    if (p.big) for (var g = 1; g < 4; g++) grid.push(h("line", { key: g, x1: 0, x2: w, y1: g * ht / 4, y2: g * ht / 4, className: "hm-spark-grid", vectorEffect: "non-scaling-stroke" }));
    return h("svg", { className: "hm-spark-svg" + (p.big ? " hm-spark-big" : ""), viewBox: "0 0 " + w + " " + ht, preserveAspectRatio: "none", role: "img", "aria-label": p.label || "Trend" },
      h("defs", null, h("linearGradient", { id: id, x1: 0, y1: 0, x2: 0, y2: 1 },
        h("stop", { offset: "0%", stopColor: "currentColor", stopOpacity: ".42" }), h("stop", { offset: "100%", stopColor: "currentColor", stopOpacity: "0" }))),
      grid, h("path", { d: area, fill: "url(#" + id + ")", className: "hm-spark-area" }),
      h("path", { d: line, fill: "none", className: "hm-spark-line", pathLength: "1", vectorEffect: "non-scaling-stroke" }));
  }
  /* a small ring gauge that fills in when it appears */
  function Gauge(p) {
    var a = useState(0), v = a[0], set = a[1], size = 84, stroke = 9, r = (size - stroke) / 2, c = 2 * Math.PI * r, val = Math.max(0, Math.min(100, p.value || 0));
    useEffect(function () { var t = setTimeout(function () { set(val); }, 80); return function () { clearTimeout(t); }; }, [val]);
    var tone = p.tone || (val > 90 ? "bad" : val > 75 ? "warn" : "ok");
    return h("div", { className: "hm-gauge hm-gauge-" + tone },
      h("div", { className: "hm-gauge-ring" },
        h("svg", { width: size, height: size, viewBox: "0 0 " + size + " " + size, "aria-hidden": "true" },
          h("circle", { cx: size / 2, cy: size / 2, r: r, fill: "none", strokeWidth: stroke, className: "hm-gauge-track" }),
          h("circle", { cx: size / 2, cy: size / 2, r: r, fill: "none", strokeWidth: stroke, strokeLinecap: "round", className: "hm-gauge-arc", strokeDasharray: c, strokeDashoffset: c * (1 - v / 100), transform: "rotate(-90 " + size / 2 + " " + size / 2 + ")" })),
        h("div", { className: "hm-gauge-n" }, p.text === undefined ? Math.round(val) + "%" : p.text)),
      h("div", { className: "hm-gauge-l" }, p.label), p.sub ? h("div", { className: "hm-sub" }, p.sub) : null);
  }
  function hueOf(s) { var n = 0; s = String(s || ""); for (var i = 0; i < s.length; i++) n = (n * 31 + s.charCodeAt(i)) % 360; return n; }
  function wxIcon(t, night) {
    t = String(t || "").toLowerCase();
    if (/thunder/.test(t)) return "⛈️";
    if (/snow|flurr|sleet|ice/.test(t)) return "❄️";
    if (/rain|shower|drizzle/.test(t)) return "🌧️";
    if (/fog|haz|smoke/.test(t)) return "🌫️";
    if (/cloud|overcast/.test(t)) return /part|few|scatter|mostly sunny|mostly clear/.test(t) ? (night ? "☁️" : "⛅") : "☁️";
    if (/clear|sunny|fair/.test(t)) return night ? "🌙" : "☀️";
    return night ? "🌙" : "🌤️";
  }
  var TAG_ICON = { models: "🧠", agents: "🤖", tools: "🛠️", business: "💼", news: "📰", reference: "📚", tool: "🛠️", security: "🛡️", hardware: "🖥️" };

  function Kpi(p) {
    return h("div", { className: "hm-kpi", style: { "--kh": p.hue } },
      h("div", { className: "hm-kpi-top" }, h("span", { className: "hm-kpi-ico", "aria-hidden": "true" }, p.icon), h("span", { className: "hm-kpi-l" }, p.label)),
      h("div", { className: "hm-kpi-n" }, p.value), p.sub ? h("div", { className: "hm-sub" }, p.sub) : null,
      p.spark ? h(Spark, { values: p.spark, label: p.label + " trend" }) : null, p.bar !== undefined ? h(Bar, { pct: p.bar }) : null);
  }
  function Kpis(props) {
    var ag = props.agent || {}, s = ag.stats || {}, days = ag.days || [], jobs = props.jobs || [], sys = props.sys, tiles = [];
    tiles.push(h(Kpi, { key: "calls", icon: "⚡", label: "Model calls", hue: 265, value: h(CountUp, { value: s.calls || 0, fmt: UI.num }), sub: "last 30 days", spark: days.map(function (d) { return d.calls; }) }));
    tiles.push(h(Kpi, { key: "chats", icon: "💬", label: "Chats", hue: 200, value: h(CountUp, { value: s.sessions || 0 }), sub: (s.active || 0) + " active · " + UI.num(s.messages) + " messages" }));
    tiles.push(h(Kpi, { key: "tok", icon: "🔤", label: "Tokens", hue: 330, value: h(CountUp, { value: (s.tokens_in || 0) + (s.tokens_out || 0), fmt: UI.num }), sub: UI.num(s.tokens_in) + " in · " + UI.num(s.tokens_out) + " out" }));
    if (jobs.length) {
      var bad = jobs.filter(function (j) { return j.enabled && (j.last_status === "error" || j.last_status === "failed"); }).length;
      tiles.push(h(Kpi, { key: "jobs", icon: "⏱️", label: "Scheduled jobs", hue: bad ? 350 : 150, value: h(CountUp, { value: jobs.length - bad, fmt: function (n) { return n + "/" + jobs.length; } }), sub: bad ? bad + " need a look" : "all passing" }));
    }
    if (sys && sys.gpu_vram_pct != null) tiles.push(h(Kpi, { key: "gpu", icon: "🎛️", label: "GPU memory", hue: 35, value: h(CountUp, { value: sys.gpu_vram_pct, fmt: function (n) { return n + "%"; } }), sub: sys.gpu_count + " cards", bar: sys.gpu_vram_pct }));
    return h("div", { className: "hm-kpis" }, tiles);
  }

  /* The hero: the drawn avatar sits at the right over the background scene (as it did in Mission Control). It reacts: working while the agent
     is busy, waiting while an approval needs you, offline when the agent is not running. */
  function useRunStatus() {
    var a = useState(""), st = a[0], set = a[1];
    useEffect(function () {
      function on(ev) { set((ev && ev.detail && ev.detail.status) || ""); }
      window.addEventListener("atrium:run", on);
      return function () { window.removeEventListener("atrium:run", on); };
    }, []);
    return st;
  }
  function Presence(props) {
    var ag = props.agent || {}, name = props.name, A = window.__AVATARS__, X = window.__HOME_EXTRAS__, run = useRunStatus();
    var tone = ag.running === false ? "bad" : ag.busy || ag.active_agents || run ? "warn" : "ok";
    var label = ag.running === false ? "Not running" : run === "waiting_for_approval" ? "Needs your approval" : ag.busy || ag.active_agents || run ? "Working" : "Idle and ready";
    var orbState = ag.running === false ? "offline" : run === "waiting_for_approval" ? "waiting" : (ag.busy || ag.active_agents || run) ? "working" : "idle";
    var status = h("div", { className: "hm-presence-line" }, ag.doing && ag.running !== false ? h("div", { className: "hm-doing", role: "status" }, ag.doing) : null, h("span", { className: "hm-presence-name" }, name), h(Chip, { tone: tone, dot: true }, label), ag.version ? h(Chip, null, "Hermes " + ag.version) : null,
      props.canStyle && X ? h(X.EngineChip, null) : null,
      props.canStyle && X ? h(X.ApprovalsLink, { name: name }) : null);
    if (!A) return h("div", { className: "hm-presence", id: "hm-avatar-slot" }, h("div", { className: "hm-orb hm-orb-" + tone, "aria-hidden": "true" }, h("span", null, (name || "A").slice(0, 1).toUpperCase())), status);
    return h(SDK.React.Fragment, null, h(A.Galaxy, { state: orbState }), h(A.Orb, { state: orbState }), status);
  }

  function Attention(props) {
    var items = props.items || [];
    return h(Card, { title: "Needs you", aside: items.length ? h(Chip, { tone: items[0].level === "bad" ? "bad" : "warn" }, items.length + (items.length === 1 ? " item" : " items")) : null },
      items.length ? h("ul", { className: "hm-list" }, items.map(function (it, i) {
        return h("li", { key: i, className: "hm-item" }, h(Dot, { tone: it.level === "bad" ? "bad" : it.level === "warn" ? "warn" : "mute" }),
          h("div", null, h("div", { className: "hm-item-t" }, it.title), it.detail ? h("div", { className: "hm-sub" }, it.detail) : null));
      })) : h("p", { className: "hm-ok" }, h(Dot, { tone: "ok" }), "Nothing needs you right now."));
  }

  /* The next week from the connected calendar. Shows nothing if no calendar is connected. */
  function Upcoming() {
    var a = useState(null), evs = a[0], set = a[1];
    // the calendar page only exists when a Nextcloud is configured; without one there is nothing to ask for
    var enabled = (UI.config().pages || []).some(function (pg) { return pg.id === "calendar"; });
    useEffect(function () {
      if (!enabled) { set(false); return undefined; }
      var live = true, t0 = new Date(), t1 = new Date(t0.getTime() + 7 * 86400000);
      function load() {
        SDK.fetchJSON("/api/nextcloud/events?start=" + encodeURIComponent(new Date(t0.getFullYear(), t0.getMonth(), t0.getDate()).toISOString()) + "&end=" + encodeURIComponent(t1.toISOString()))
          .then(function (r) { if (live) set(r.events || []); }, function () { if (live) set(false); });
      }
      load();
      var timer = setInterval(load, 300000);
      return function () { live = false; clearInterval(timer); };
    }, [enabled]);
    if (evs === false) return null;
    function dayOf(e) { return e.all_day ? new Date(+e.start.slice(0, 4), +e.start.slice(5, 7) - 1, +e.start.slice(8, 10)) : new Date(e.start); }
    var now = Date.now(), list = (evs || []).filter(function (e) { return e.all_day || new Date(e.end).getTime() >= now; }).slice(0, 6);
    return h(Card, { title: "Coming up", aside: h("a", { href: "/calendar" }, "Open calendar →") },
      evs === null ? h(Empty, null, "Loading…") : list.length ? h("ul", { className: "hm-list hm-up-list" }, list.map(function (e, i) {
        var d = dayOf(e), today = d.toDateString() === new Date().toDateString();
        return h("li", { key: i, className: "hm-item hm-up", style: { "--cc": e.color || "#8b7bff" } },
          h("div", { className: "hm-up-date" + (today ? " hm-up-today" : "") }, h("b", null, d.getDate()), h("span", null, d.toLocaleDateString(undefined, { month: "short" }))),
          h("div", null, h("div", { className: "hm-item-t" }, e.title), h("div", { className: "hm-sub" }, e.all_day ? "All day" : d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) + (e.location ? " · " + e.location : ""))));
      })) : h(Empty, null, "Nothing on the calendar for the next week."));
  }

  function Focus(props) {
    var name = props.name, a = useState(props.focus || []), items = a[0], setItems = a[1];
    var t = useState(""), text = t[0], setText = t[1];
    var m = useState(""), msg = m[0], setMsg = m[1];
    var key = JSON.stringify(props.focus || []);
    useEffect(function () { setItems(props.focus || []); }, [key]);                 // a fresh overview replaces the list
    function call(path, body) {
      setMsg("");
      return SDK.fetchJSON("/api/plugins/atrium-mission/focus" + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        .then(function (r) { setItems(r.items || []); return true; }, function (e) { setMsg(String(e.message || e).replace(/^\d+:\s*/, "").replace(/^\{"detail":"?|"?\}$/g, "")); return false; });
    }
    function add(ev) {
      if (ev) ev.preventDefault();
      var v = text.trim();
      if (!v) return;
      call("", { text: v }).then(function (ok) { if (ok) setText(""); });
    }
    return h(Card, { title: "Today's focus", aside: items.length ? items.filter(function (x) { return x.done; }).length + "/" + items.length + " done" : null },
      items.length ? h("ul", { className: "hm-list" }, items.map(function (it) {
        return h("li", { key: it.i, className: "hm-item" },
          h("button", { type: "button", className: "hm-check" + (it.done ? " hm-done" : ""), role: "checkbox", "aria-checked": String(it.done), "aria-label": (it.done ? "Mark not done: " : "Mark done: ") + it.text,
            onClick: function () { call("/toggle", { index: it.i }); } }, it.done ? "✓" : ""),
          h("span", { className: it.done ? "hm-struck" : "", style: { flex: 1 } }, it.text),
          h("button", { type: "button", className: "hm-x", "aria-label": "Remove: " + it.text, onClick: function () { call("/remove", { index: it.i }); } }, "×"));
      })) : h(Empty, null, "Nothing yet. Add the first thing to knock out today."),
      h("form", { className: "hm-ask-row", onSubmit: add, style: { marginTop: "10px" } },
        h("input", { className: "hm-input", value: text, maxLength: 200, placeholder: "Add something for today…", "aria-label": "Add to today's focus", onChange: function (ev) { setText(ev.target.value); } }),
        h("button", { type: "submit", className: "hm-btn", disabled: !text.trim() }, "Add")),
      msg ? h("p", { className: "hm-err", role: "alert" }, msg) : null,
      h("p", { className: "hm-fine" }, "Saved in today's daily note, so " + name + " and your notes app see it too."));
  }

  function Pipeline(props) {
    var b = props.board || { columns: [], cards: [] }, total = b.columns.reduce(function (n, c) { return n + (c.count || 0); }, 0);
    return h(Card, { title: "Idea pipeline", aside: b.cards.length + " open" },
      h("div", { className: "hm-funnel", role: "img", "aria-label": total + " ideas across the stages" }, total ? b.columns.filter(function (c) { return c.count; }).map(function (c) {
        return h("span", { key: c.name, className: "hm-funnel-seg", style: { flex: c.count, "--fh": hueOf(c.name) }, title: c.name + ": " + c.count }, c.name);
      }) : h("span", { className: "hm-funnel-seg hm-funnel-empty", style: { flex: 1 } }, "empty")),
      h("div", { className: "hm-pills" }, b.columns.map(function (c) { return h("span", { key: c.name, className: "hm-pill" + (c.count ? " hm-pill-on" : "") }, c.name, h("b", null, c.count)); })),
      b.cards.length ? h("ul", { className: "hm-list" }, b.cards.map(function (c) {
        return h("li", { key: c.id, className: "hm-item" }, h("div", null, h("div", { className: "hm-item-t" }, c.title), h("div", { className: "hm-sub" }, c.status + (c.assignee ? " · " + c.assignee : ""))));
      })) : h(Empty, null, "The board is clear."),
      props.canWork ? h("p", { className: "hm-fine" }, h("a", { href: "/code", onClick: function () { try { localStorage.setItem("atrium.code.view", "pipeline"); } catch (x) { /* optional */ } } }, "Open the idea pipeline"), " · ", h("a", { href: "/kanban" }, "Open the board")) : null);
  }

  function Radar(props) {
    var r = props.radar || { items: [] };
    return h(Card, { title: "Radar: what to look at today", aside: r.date || null },
      r.items.length ? h("ol", { className: "hm-radar" }, r.items.slice(0, 6).map(function (it) {
        var tag = String(it.tag || "").toLowerCase();
        return h("li", { key: it.rank },
          h("span", { className: "hm-rthumb", style: { "--th": hueOf(tag || it.title) }, "aria-hidden": "true" }, TAG_ICON[tag] || "✨"),
          h("div", { className: "hm-rbody" }, h("div", { className: "hm-item-t" }, it.url ? h("a", { href: it.url, target: "_blank", rel: "noopener noreferrer" }, it.title) : it.title,
            it.tag ? h("span", { className: "hm-tag", style: { "--th": hueOf(tag) } }, it.tag) : null), it.why ? h("div", { className: "hm-sub" }, it.why) : null));
      })) : h(Empty, null, "No radar yet. " + props.name + " writes one each night."));
  }

  function DayLog(props) {
    var d = props.log || { items: [] };
    return h(Card, { title: "Daily log", aside: d.date || null },
      d.items.length ? h("ul", { className: "hm-bullets" }, d.items.map(function (t, i) { return h("li", { key: i }, t); })) : h(Empty, null, "No log yet."),
      d.items.length ? h("p", { className: "hm-fine" }, "Written by " + props.name + " from the agent's own sessions, so check anything important.") : null);
  }

  function Weather(props) {
    var w = props.weather, r = props.river;
    if (!w && !r) return null;
    var now = w && w.now, hr = new Date().getHours(), night = hr < 6 || hr >= 19;
    var per = w && w.periods ? w.periods.filter(function (p) { return p.day; }).slice(0, 5) : [];
    var lo = Math.min.apply(null, per.map(function (p) { return p.temp; }).concat([999])), hi = Math.max.apply(null, per.map(function (p) { return p.temp; }).concat([-999]));
    return h(Card, { title: w && w.place ? w.place : "Outside", aside: w ? "NWS" : null, className: "hm-wxcard" },
      now ? h("div", { className: "hm-wx" }, h("div", { className: "hm-wx-ico", "aria-hidden": "true" }, wxIcon(now.short, night)), h("div", { className: "hm-wx-t" }, now.temp + "°"),
        h("div", null, h("div", { className: "hm-wx-s" }, now.short), h("div", { className: "hm-sub" }, "wind " + now.wind + " · " + now.rain + "% rain"))) : null,
      w && w.alerts && w.alerts.length ? h("p", { className: "hm-err" }, w.alerts.length + " weather alert" + (w.alerts.length === 1 ? "" : "s")) : null,
      per.length ? h("div", { className: "hm-periods" }, per.map(function (p, i) {
        var pos = hi === lo ? 50 : Math.round(100 * (p.temp - lo) / (hi - lo));
        return h("div", { key: i, className: "hm-period" }, h("div", { className: "hm-sub" }, p.name.slice(0, 3)), h("div", { className: "hm-period-i", "aria-hidden": "true" }, wxIcon(p.short || p.forecast || "", false)),
          h("div", { className: "hm-period-bar", title: p.temp + "°" }, h("i", { style: { bottom: pos + "%" } })), h("strong", null, p.temp + "°"),
          h("div", { className: "hm-drop", title: p.rain + "% chance of rain" }, h("i", { style: { width: Math.max(4, p.rain) + "%" } })), h("div", { className: "hm-sub" }, p.rain + "%"));
      })) : null,
      r && r.sites ? h("ul", { className: "hm-list hm-river" }, Object.keys(r.sites).map(function (k) {
        var s = r.sites[k], d = s.flow_cfs - s.flow_cfs_24h_ago;
        return h("li", { key: k, className: "hm-item" }, h("span", { className: "hm-river-ico", "aria-hidden": "true" }, "🌊"),
          h("div", null, h("div", { className: "hm-item-t" }, s.name), h("div", { className: "hm-sub" }, s.flow_cfs + " cfs · " + s.stage_ft + " ft · ", h("span", { className: d >= 0 ? "hm-up" : "hm-down" }, (d >= 0 ? "▲ +" : "▼ ") + d.toFixed(1)), " cfs / 24h")));
      })) : null);
  }

  function Jobs(props) {
    var jobs = props.jobs || [];
    return h(Card, { title: "Scheduled jobs", aside: jobs.length ? jobs.length + " jobs" : null },
      jobs.length ? h("ul", { className: "hm-list" }, jobs.map(function (j) {
        var tone = !j.enabled ? "mute" : j.last_status === "error" || j.last_status === "failed" ? "bad" : j.last_status === "ok" ? "ok" : "mute";
        return h("li", { key: j.name, className: "hm-item" }, h(Dot, { tone: tone }), h("div", null, h("div", { className: "hm-item-t" }, j.name),
          h("div", { className: "hm-sub" }, (j.last_run_at ? "ran " + UI.ago(j.last_run_at) : "not run yet") + (j.next_run_at ? " · next " + UI.until(j.next_run_at) : ""))));
      })) : h(Empty, null, "No scheduled jobs."));
  }

  function Usage(props) {
    var ag = props.agent, s = ag.stats || {}, days = ag.days || [], tin = s.tokens_in || 0, tout = s.tokens_out || 0, tt = Math.max(1, tin + tout);
    var tmax = Math.max.apply(null, (ag.tools || []).map(function (t) { return t.count; }).concat([1]));
    return h(Card, { title: "Usage", aside: "last 30 days" },
      days.length > 1 ? h("div", { className: "hm-chart" }, h(Spark, { values: days.map(function (d) { return d.calls; }), big: true, label: "Model calls per day" }),
        h("div", { className: "hm-chart-axis" }, h("span", null, (days[0].day || "").slice(5)), h("span", null, "model calls per day"), h("span", null, (days[days.length - 1].day || "").slice(5)))) : null,
      h("div", { className: "hm-split", role: "img", "aria-label": "Tokens in and out" }, h("i", { className: "hm-split-in", style: { flex: tin || 1 } }), h("i", { className: "hm-split-out", style: { flex: tout || 0.01 } })),
      h("div", { className: "hm-split-key" }, h("span", null, h("em", { className: "hm-sw-in" }), "in " + UI.num(tin) + " (" + Math.round(100 * tin / tt) + "%)"), h("span", null, h("em", { className: "hm-sw-out" }), "out " + UI.num(tout))),
      ag.tools && ag.tools.length ? h("div", { className: "hm-tools" }, ag.tools.map(function (t, i) {
        return h("div", { key: t.tool, className: "hm-tool" }, h("span", null, t.tool), h("div", { className: "hm-bar" }, h("div", { className: "hm-bar-fill hm-bar-grow", style: { width: (100 * t.count / tmax) + "%", animationDelay: (i * 70) + "ms" } })), h("b", null, t.count));
      })) : null);
  }

  function Recent(props) {
    var list = props.agent.recent || [];
    return h(Card, { title: "Recent chats" },
      list.length ? h("ul", { className: "hm-list" }, list.map(function (s) {
        return h("li", { key: s.id, className: "hm-item" }, h("div", null, h("div", { className: "hm-item-t" }, s.title), h("div", { className: "hm-sub" }, s.source + " · " + s.messages + " msgs · " + UI.ago(s.last_active))));
      })) : h(Empty, null, "No chats yet."),
      props.agent.channels && props.agent.channels.length ? h("div", { className: "hm-chips" }, props.agent.channels.map(function (c) {
        return h(Chip, { key: c.name, tone: /connect|ok|running/i.test(c.state || "") ? "ok" : "warn", dot: true }, c.name);
      })) : null);
  }

  function SystemsSummary(props) {
    var s = props.sum || {};
    return h(Card, { title: "Systems", aside: h("a", { href: "/systems" }, "Open Systems →") },
      h("div", { className: "hm-gauges" },
        s.gpu_vram_pct == null ? null : h(Gauge, { value: s.gpu_vram_pct, label: "GPU memory", sub: s.gpu_count + " cards" }),
        s.disk_pct == null ? null : h(Gauge, { value: s.disk_pct, label: "Data disk" }),
        s.mem_pct == null ? null : h(Gauge, { value: s.mem_pct, label: "Memory" })),
      h("div", { className: "hm-stats hm-stats-tight" },
        h(Stat, { value: s.containers == null ? "-" : s.containers, label: "containers" }), h(Stat, { value: s.monitors || "-", label: "monitors up" }),
        h(Stat, { value: s.backup_ok === false ? "Failed" : s.backup_ok ? "OK" : "-", label: "last backup" })));
  }

  function Page() {
    var cfg = UI.config(), a = UI.usePoll("/api/overview", 30000), d = a[0], err = a[1];
    var name = cfg.agentName || "Assistant", who = cfg.name || cfg.user || "";
    if (!d) return h("div", { className: "hm" }, err ? h("p", { className: "hm-sub" }, "Could not load the overview: " + err) : h("div", { className: "hm-skel", "aria-busy": "true", "aria-label": "Loading" }, h("i", { className: "hm-skel-a" }), h("i", { className: "hm-skel-b" }), h("i", { className: "hm-skel-c" }), h("i", { className: "hm-skel-c" }), h("i", { className: "hm-skel-c" })));
    var hs = d.health, ag = d.agent, bad = (d.attention || []).filter(function (x) { return x.level === "bad"; }).length;
    if (d.limited) {                                   // everyone who is not an owner: the agent, and the Ask box
      return h("div", { className: "hm" },
        h("header", { className: "hm-top" }, h("div", null, h("h1", null, UI.greeting() + (who ? ", " + who : "")), h("p", { className: "hm-sub" }, "Updated " + UI.ago(d.generated)))),
        h("section", { className: "hm-card hm-hero hm-hero-art" }, h(Presence, { agent: ag, name: name }), h("div", { className: "hm-hero-main" }, h(Clock), h("p", { className: "hm-sub" }, "To talk to " + name + ", open ", h("a", { href: "/chat" }, "Chat"), "."))),
        h("p", { className: "hm-sub" }, "For a longer conversation, open ", h("a", { href: "/chat" }, "Chat"), "."));
    }
    return h("div", { className: "hm" },
      h("header", { className: "hm-top" },
        h("div", null, h("h1", null, UI.greeting() + (who ? ", " + who : "")), h("p", { className: "hm-sub" }, "Updated " + UI.ago(d.generated))),
        hs ? h(Chip, { tone: bad ? "bad" : "ok", dot: true }, bad ? bad + " thing" + (bad === 1 ? "" : "s") + " down" : "All systems up") : null),
      h("section", { className: "hm-card hm-hero hm-hero-art" },
        h(Presence, { agent: ag, name: name, canStyle: true }),
        h("div", { className: "hm-hero-main" },
          hs ? h("div", { className: "hm-health" }, h(UI.Ring, { value: hs.score, label: "healthy" }), h("div", null, h("h2", null, hs.label), h("p", { className: "hm-sub" }, hs.monitors_up + " of " + hs.monitors_total + " monitors passing"))) : null,
          h(Clock), h("p", { className: "hm-sub" }, "To talk to " + name + ", open ", h("a", { href: "/chat" }, "Chat"), "."))),
      window.__HOME_EXTRAS__ ? h(window.__HOME_EXTRAS__.Ticker, { radar: d.today.radar }) : null,
      window.__HOME_EXTRAS__ ? h(window.__HOME_EXTRAS__.Launchers, { name: name }) : null,
      h(Kpis, { agent: ag, jobs: d.jobs, sys: d.systems_summary }),
      h("div", { className: "hm-grid" },
        h(Attention, { items: d.attention }), h(Upcoming, null), h(Focus, { focus: d.today.focus, name: name }), h(Pipeline, { board: ag.board, canWork: !!hs }),
        h(Radar, { radar: d.today.radar, name: name }), h(Weather, { weather: d.weather, river: d.river }), h(DayLog, { log: d.today.daylog, name: name }),
        h(Jobs, { jobs: d.jobs }), h(Usage, { agent: ag }), h(Recent, { agent: ag }),
        d.systems_summary ? h(SystemsSummary, { sum: d.systems_summary }) : null));
  }

  REG.register("home-page", Page);
})();
