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
    var status = h("div", { className: "hm-presence-line" }, h("span", { className: "hm-presence-name" }, name), h(Chip, { tone: tone, dot: true }, label), ag.version ? h(Chip, null, "Hermes " + ag.version) : null,
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
    var b = props.board || { columns: [], cards: [] };
    return h(Card, { title: "Idea pipeline", aside: b.cards.length + " open" },
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
        return h("li", { key: it.rank }, h("div", { className: "hm-item-t" }, it.url ? h("a", { href: it.url, target: "_blank", rel: "noopener noreferrer" }, it.title) : it.title,
          it.tag ? h("span", { className: "hm-tag" }, it.tag) : null), it.why ? h("div", { className: "hm-sub" }, it.why) : null);
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
    var now = w && w.now;
    return h(Card, { title: w && w.place ? w.place : "Outside", aside: w ? "NWS" : null },
      now ? h("div", { className: "hm-wx" }, h("div", { className: "hm-wx-t" }, now.temp + "°"), h("div", null, h("div", null, now.short), h("div", { className: "hm-sub" }, "wind " + now.wind + " · " + now.rain + "% rain"))) : null,
      w && w.alerts && w.alerts.length ? h("p", { className: "hm-err" }, w.alerts.length + " weather alert" + (w.alerts.length === 1 ? "" : "s")) : null,
      w && w.periods ? h("div", { className: "hm-periods" }, w.periods.filter(function (p) { return p.day; }).slice(0, 5).map(function (p, i) {
        return h("div", { key: i, className: "hm-period" }, h("div", { className: "hm-sub" }, p.name.slice(0, 3)), h("strong", null, p.temp + "°"), h("div", { className: "hm-sub" }, p.rain + "%"));
      })) : null,
      r && r.sites ? h("ul", { className: "hm-list hm-river" }, Object.keys(r.sites).map(function (k) {
        var s = r.sites[k], d = s.flow_cfs - s.flow_cfs_24h_ago;
        return h("li", { key: k, className: "hm-item" }, h("div", null, h("div", { className: "hm-item-t" }, s.name), h("div", { className: "hm-sub" }, s.flow_cfs + " cfs · " + s.stage_ft + " ft · " + (d >= 0 ? "+" : "") + d.toFixed(1) + " cfs / 24h")));
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
    var ag = props.agent, s = ag.stats || {}, days = ag.days || [], max = Math.max.apply(null, days.map(function (d) { return d.calls; }).concat([1]));
    var tmax = Math.max.apply(null, (ag.tools || []).map(function (t) { return t.count; }).concat([1]));
    return h(Card, { title: "Usage", aside: "last 30 days" },
      h("div", { className: "hm-stats" }, h(Stat, { value: UI.num(s.sessions), label: "chats", sub: s.active + " active" }), h(Stat, { value: UI.num(s.messages), label: "messages" }),
        h(Stat, { value: UI.num(s.calls), label: "model calls" }), h(Stat, { value: UI.num((s.tokens_in || 0) + (s.tokens_out || 0)), label: "tokens", sub: UI.num(s.tokens_in) + " in · " + UI.num(s.tokens_out) + " out" })),
      days.length ? h("div", { className: "hm-spark", "aria-label": "Model calls per day" }, days.map(function (d) {
        return h("div", { key: d.day, className: "hm-spark-col", title: d.day + ": " + d.calls + " calls" }, h("div", { className: "hm-spark-bar", style: { height: Math.max(4, Math.round(100 * d.calls / max)) + "%" } }), h("span", null, (d.day || "").slice(5)));
      })) : null,
      ag.tools && ag.tools.length ? h("div", { className: "hm-tools" }, ag.tools.map(function (t) {
        return h("div", { key: t.tool, className: "hm-tool" }, h("span", null, t.tool), h(Bar, { pct: 100 * t.count / tmax, tone: "accent" }), h("b", null, t.count));
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
      h("div", { className: "hm-stats" },
        h(Stat, { value: s.containers == null ? "-" : s.containers, label: "containers" }), h(Stat, { value: s.monitors || "-", label: "monitors up" }),
        h(Stat, { value: s.gpu_vram_pct == null ? "-" : s.gpu_vram_pct + "%", label: "GPU memory", sub: s.gpu_count + " cards" }),
        h(Stat, { value: s.disk_pct == null ? "-" : s.disk_pct + "%", label: "data disk" }),
        h(Stat, { value: s.mem_pct == null ? "-" : s.mem_pct + "%", label: "memory" }),
        h(Stat, { value: s.backup_ok === false ? "Failed" : s.backup_ok ? "OK" : "-", label: "last backup" })));
  }

  function Page() {
    var cfg = UI.config(), a = UI.usePoll("/api/overview", 30000), d = a[0], err = a[1];
    var name = cfg.agentName || "Assistant", who = cfg.name || cfg.user || "";
    if (!d) return h("div", { className: "hm" }, h("p", { className: "hm-sub" }, err ? "Could not load the overview: " + err : "Loading…"));
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
      h("div", { className: "hm-grid" },
        h(Attention, { items: d.attention }), h(Focus, { focus: d.today.focus, name: name }), h(Pipeline, { board: ag.board, canWork: !!hs }),
        h(Radar, { radar: d.today.radar, name: name }), h(DayLog, { log: d.today.daylog, name: name }), h(Weather, { weather: d.weather, river: d.river }),
        h(Jobs, { jobs: d.jobs }), h(Usage, { agent: ag }), h(Recent, { agent: ag }),
        d.systems_summary ? h(SystemsSummary, { sum: d.systems_summary }) : null));
  }

  REG.register("home-page", Page);
})();
