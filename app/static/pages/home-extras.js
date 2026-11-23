/* Home extras moved over from Mission Control: the approvals shortcut with its count, the engine chip (local or cloud), the radar ticker and the
   launcher cards. Exposed as window.__HOME_EXTRAS__ for the Home page. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, UI = window.__APP_UI__;
  if (!SDK || !UI) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect;
  var ENGINE = "/api/plugins/atrium-mission/engine";

  function ApprovalsLink(props) {
    var n_ = useState(0), n = n_[0], setN = n_[1];
    useEffect(function () {
      var dead = false;
      function load() { SDK.fetchJSON("/api/plugins/atrium-chat/actions").then(function (r) { if (!dead) setN((r.pending || []).length); }).catch(function () { /* approval service not available */ }); }
      load();
      var stop = window.atriumEvery(load, 25000);
      return function () { dead = true; stop(); };
    }, []);
    return h("a", { href: "/chat?approvals=1", className: "hm-style hm-appr", title: n ? n + " waiting for your approval" : "Things " + props.name + " wants your OK on" },
      "✉ Approvals", n ? h("span", { className: "hm-appr-n" }, String(n)) : null);
  }

  var DURATIONS = [[30, "30 minutes"], [60, "1 hour"], [120, "2 hours"], [0, "until I switch back"]];
  function leftText(sec) {
    if (sec == null) return "until switched back";
    if (sec >= 3600) return Math.floor(sec / 3600) + " h " + Math.round((sec % 3600) / 60) + " min left";
    return Math.max(1, Math.round(sec / 60)) + " min left";
  }

  function EngineChip() {
    var o = useState(false), open = o[0], setOpen = o[1];
    var d = useState(null), info = d[0], setInfo = d[1];
    var m = useState(""), model = m[0], setModel = m[1];
    var u = useState(60), mins = u[0], setMins = u[1];
    var c = useState(false), sure = c[0], setSure = c[1];
    var b = useState(false), busy = b[0], setBusy = b[1];
    var e = useState(""), err = e[0], setErr = e[1];
    function load() { return SDK.fetchJSON(ENGINE).then(function (r) { setInfo(r); setModel(function (x) { return x || (r.claude_models && r.claude_models[0] && r.claude_models[0].id) || ""; }); }).catch(function () { /* optional */ }); }
    useEffect(function () { load(); var iv = setInterval(load, 30000); return function () { clearInterval(iv); }; }, []);
    if (!info) return null;
    var cloud = info.engine === "claude";
    var label = cloud ? (((info.claude_models || []).filter(function (x) { return x.id === info.model; })[0] || {}).label || info.model).replace(/ \(.*\)$/, "") : ((info.local && info.local.name) || "local");
    function go(body) {
      setBusy(true); setErr("");
      SDK.fetchJSON(ENGINE, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        .then(function () { setSure(false); return load(); })
        .catch(function (x) { var t = String((x && x.message) || x), i = t.indexOf("{"); try { setErr(JSON.parse(t.slice(i)).detail || "Could not switch."); } catch (y) { setErr("Could not switch."); } })
        .then(function () { setBusy(false); });
    }
    return h("span", { className: "hm-eng-wrap" },
      h("button", { type: "button", className: "hm-eng-chip" + (cloud ? " hm-eng-cloud" : ""), title: "Choose the engine", "aria-expanded": open ? "true" : "false", onClick: function () { setOpen(!open); load(); } },
        cloud ? "☁ " + label + " · " + leftText(info.seconds_left) : label, h("span", { className: "hm-eng-caret" }, "▾")),
      open ? h("div", { className: "hm-eng-pop", role: "dialog", "aria-label": "Engine" },
        h("div", { className: "hm-eng-title" }, "Engine"),
        h("div", { className: "hm-eng-opt" + (!cloud ? " hm-eng-on" : "") },
          h("div", { className: "hm-eng-name" }, "Local · " + ((info.local && info.local.name) || "local")),
          h("div", { className: "hm-fine" }, "Private and free. Nothing leaves your server."),
          cloud ? h("button", { type: "button", className: "hm-btn", disabled: busy, onClick: function () { go({ engine: "local" }); } }, "Switch back to local now") : h("div", { className: "hm-fine" }, "In use now.")),
        h("div", { className: "hm-eng-opt" + (cloud ? " hm-eng-on" : "") },
          h("div", { className: "hm-eng-name" }, "Claude · in the cloud"),
          !info.claude_available ? h("div", { className: "hm-fine" }, "No Anthropic key is set up on the server.") : [
            h("div", { key: "n", className: "hm-fine" }, "Everything in the chat, including anything it reads for you, is sent to Anthropic and costs money per use."),
            h("div", { key: "r", className: "hm-eng-row" },
              h("select", { className: "hm-select", "aria-label": "Model", value: model, onChange: function (ev) { setModel(ev.target.value); } }, (info.claude_models || []).map(function (x) { return h("option", { key: x.id, value: x.id }, x.label); })),
              h("select", { className: "hm-select", "aria-label": "For how long", value: mins, onChange: function (ev) { setMins(parseInt(ev.target.value, 10)); } }, DURATIONS.map(function (x) { return h("option", { key: x[0], value: x[0] }, x[1]); }))),
            h("label", { key: "c", className: "hm-check-row" }, h("input", { type: "checkbox", checked: sure, onChange: function (ev) { setSure(ev.target.checked); } }), " I understand: it is sent to Anthropic and costs money"),
            h("button", { key: "b", type: "button", className: "hm-btn", disabled: busy || !sure, onClick: function () { go({ engine: "claude", model: model, minutes: mins, confirm: true }); } }, cloud ? "Change" : "Switch to Claude")]),
        err ? h("div", { className: "hm-err", role: "alert" }, err) : null,
        h("div", { className: "hm-fine" }, "Applies to chats from this app (Chat and the Ask box). Scheduled jobs always use the local engine.")) : null);
  }

  function Ticker(props) {
    var items = (props.radar && props.radar.items) || [];
    if (!items.length) return null;
    var row = items.concat(items);
    return h("div", { className: "hm-ticker", role: "region", "aria-label": "Radar headlines" },
      h("div", { className: "hm-ticker-track" }, row.map(function (it, i) {
        return h("span", { key: i, className: "hm-ticker-item", "aria-hidden": i >= items.length ? "true" : null },
          h("span", { className: "hm-ticker-n" }, it.rank), it.url ? h("a", { href: it.url, target: "_blank", rel: "noopener noreferrer" }, it.title) : it.title, it.tag ? h("span", { className: "hm-tag" }, it.tag) : null);
      })));
  }

  function Launchers(props) {
    var cfg = UI.config(), have = {};
    (cfg.pages || []).forEach(function (p) { have[p.id] = p; });
    var all = [["chat", "Chat with " + props.name, "A full conversation", "◈"], ["workflows", "Workflows", "Scheduled jobs and runs", "◷"], ["memory", "Memory", "Notes and what " + props.name + " remembers", "✦"], ["avatar", "Avatar", "How " + props.name + " looks", "☺"]];
    var list = all.filter(function (x) { return have[x[0]]; });
    if (!list.length) return null;
    return h("div", { className: "hm-launch" }, list.map(function (x) {
      return h("a", { key: x[0], href: have[x[0]].path, className: "hm-launch-card" },
        h("span", { className: "hm-launch-icon", "aria-hidden": "true" }, x[3]), h("span", { className: "hm-launch-label" }, x[1]), h("span", { className: "hm-fine" }, x[2]));
    }));
  }

  window.__HOME_EXTRAS__ = { ApprovalsLink: ApprovalsLink, EngineChip: EngineChip, Ticker: Ticker, Launchers: Launchers };
})();
