/**
 * Atrium Loom: Settings (dashboard tab plugin)
 *
 * One page for the things you would otherwise ask for: which events ping you and quiet hours, when each of the agent's jobs
 * runs, which devices get pings, how much cloud engine you used, and shortcuts to her soul and look. Plain IIFE, no build.
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var REG = window.__HERMES_PLUGINS__;
  if (!SDK || !REG) return;

  /* The agent's name. Text in this file says {{agent}} wherever it means the name; h() fills it in. The name is
     read once from the Chat plugin (set in the Settings tab). A synchronous request keeps this simple: it is one tiny
     local call, cached for the browser session, and falls back to "Assistant". */
  var AGENT = (function () {
    try { var cached = window.sessionStorage.getItem("atrium.agent"); if (cached) return cached; } catch (x) { /* storage blocked */ }
    var name = "Assistant";
    try {
      var xhr = new XMLHttpRequest();
      xhr.open("GET", "/api/plugins/atrium-chat/agent", false);
      xhr.withCredentials = true;
      xhr.send(null);
      if (xhr.status === 200) { var j = JSON.parse(xhr.responseText); if (j && typeof j.name === "string" && j.name) name = j.name; }
    } catch (x) { /* keep the default */ }
    try { window.sessionStorage.setItem("atrium.agent", name); } catch (x) { /* ignore */ }
    return name;
  })();
  function R(v) { return typeof v === "string" && v.indexOf("{{agent}}") >= 0 ? v.split("{{agent}}").join(AGENT) : v; }
  var h = function (type, props) {
    var a = Array.prototype.slice.call(arguments);
    if (props && typeof props === "object" && !Array.isArray(props)) {
      var p = {};
      for (var k in props) if (Object.prototype.hasOwnProperty.call(props, k)) p[k] = R(props[k]);
      a[1] = p;
    }
    for (var i = 2; i < a.length; i++) a[i] = Array.isArray(a[i]) ? a[i].map(R) : R(a[i]);
    return SDK.React.createElement.apply(null, a);
  };
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var fetchJSON = SDK.fetchJSON;
  var API = "/api/plugins/atrium-settings";
  var CHAT = "/api/plugins/atrium-chat";

  function post(url, body) {
    return fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  }
  function errText(e) {
    var m = String((e && e.message) || e || "");
    var i = m.indexOf("{");
    if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* fall through */ } }
    return m.replace(/^\d+:\s*/, "").slice(0, 160) || "Something went wrong.";
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function hourLabel(hh) { return ((hh % 12) || 12) + (hh < 12 ? " AM" : " PM"); }
  function fire(name) { try { window.dispatchEvent(new CustomEvent(name)); } catch (x) { /* old browser */ } }

  function Switch(props) {
    return h("button", { type: "button", role: "switch", "aria-checked": props.on ? "true" : "false", disabled: props.disabled, className: "ats-switch" + (props.on ? " ats-on" : ""), onClick: function () { props.onChange(!props.on); } }, h("span", null));
  }

  /* ---------- notifications ---------- */

  function Notifications() {
    var d_ = useState(null), data = d_[0], setData = d_[1];
    var m_ = useState(""), msg = m_[0], setMsg = m_[1];

    function load() { return fetchJSON(CHAT + "/settings/notify").then(setData).catch(function (e) { setMsg(errText(e)); }); }
    useEffect(function () { load(); }, []);
    if (!data) return h("div", { className: "ats-card" }, h("div", { className: "ats-h" }, "Pings"), h("div", { className: "ats-note" }, msg || "Loading…"));

    var prefs = data.prefs;
    function save(patch) {
      post(CHAT + "/settings/notify", patch).then(function (r) { setData(Object.assign({}, data, { prefs: r.prefs, quiet_now: r.quiet_now })); setMsg("Saved."); }).catch(function (e) { setMsg(errText(e)); });
    }
    function test() {
      setMsg("");
      post(CHAT + "/push/test").then(function (r) { setMsg(r && r.ok ? "Test sent." : "No device received it. Turn notifications on from the bell in Chat."); }).catch(function (e) { setMsg(errText(e)); });
    }
    var q = prefs.quiet;
    return h("div", { className: "ats-card" },
      h("div", { className: "ats-h" }, "Pings"),
      h("div", { className: "ats-note" }, "Choose what is allowed to buzz your phone."),
      data.groups.map(function (g) {
        return h("div", { key: g.id, className: "ats-row" },
          h("div", { className: "ats-row-text" }, h("div", { className: "ats-row-t" }, g.label), h("div", { className: "ats-note" }, g.note)),
          h(Switch, { on: !!prefs.groups[g.id], disabled: !!g.locked, onChange: function (v) { var gs = {}; gs[g.id] = v; save({ groups: gs }); } }));
      }),
      h("div", { className: "ats-sep" }),
      h("div", { className: "ats-row" },
        h("div", { className: "ats-row-text" }, h("div", { className: "ats-row-t" }, "Quiet hours"), h("div", { className: "ats-note" }, "Hold pings at night and send them as one summary when it ends." + (q.enabled ? (data.quiet_now ? " Quiet right now." : " Not quiet right now.") : ""))),
        h(Switch, { on: q.enabled, onChange: function (v) { save({ quiet: { enabled: v } }); } })),
      q.enabled ? h("div", { className: "ats-inline" },
        h("label", null, "From ", h("input", { type: "time", className: "ats-input", value: q.start, onChange: function (e) { if (e.target.value) save({ quiet: { start: e.target.value } }); } })),
        h("label", null, "to ", h("input", { type: "time", className: "ats-input", value: q.end, onChange: function (e) { if (e.target.value) save({ quiet: { end: e.target.value } }); } }))) : null,
      q.enabled ? h("div", { className: "ats-row" },
        h("div", { className: "ats-row-text" }, h("div", { className: "ats-row-t" }, "Let urgent things through"), h("div", { className: "ats-note" }, "Outages, failures and approvals can still buzz you at night.")),
        h(Switch, { on: q.allow_urgent, onChange: function (v) { save({ quiet: { allow_urgent: v } }); } })) : null,
      data.held ? h("div", { className: "ats-note" }, data.held + " ping" + (data.held === 1 ? " is" : "s are") + " being held for the summary.") : null,
      h("div", { className: "ats-actions" }, h("button", { type: "button", className: "ats-btn", onClick: test }, "Send a test ping")),
      msg ? h("div", { className: "ats-msg" }, msg) : null);
  }

  /* ---------- when jobs run ---------- */

  function Hours(props) {
    return h("select", { className: "ats-input", value: props.value, onChange: function (e) { props.onChange(parseInt(e.target.value, 10)); } }, Array.from({ length: 24 }, function (_x, i) { return h("option", { key: i, value: i }, hourLabel(i)); }));
  }

  function JobRow(props) {
    var j = props.job, v0 = j.values;
    var s_ = useState(v0), v = s_[0], setV = s_[1];
    var m_ = useState(""), msg = m_[0], setMsg = m_[1];
    var b_ = useState(false), busy = b_[0], setBusy = b_[1];
    useEffect(function () { setV(j.values); }, [JSON.stringify(j.values)]);
    var dirty = JSON.stringify(v) !== JSON.stringify(v0);
    function set(patch) { setV(Object.assign({}, v, patch)); setMsg(""); }
    function timeVal() { return pad(v.hour) + ":" + pad(v.minute); }
    function setTime(t) { var m = /^(\d\d):(\d\d)$/.exec(t); if (m) set({ hour: parseInt(m[1], 10), minute: parseInt(m[2], 10) }); }
    function save() {
      setBusy(true); setMsg("");
      post(API + "/schedule/" + j.id, v).then(function (r) { setMsg("Saved: " + r.when + ". Applies from the next run."); props.onSaved(); }).catch(function (e) { setMsg(errText(e)); }).then(function () { setBusy(false); });
    }
    var editor = null;
    if (v) {
      if (v.kind === "daily") editor = h("div", { className: "ats-inline" }, h("label", null, "Every day at ", h("input", { type: "time", className: "ats-input", value: timeVal(), onChange: function (e) { setTime(e.target.value); } })));
      else if (v.kind === "weekly") editor = h("div", { className: "ats-inline" }, h("select", { className: "ats-input", value: v.day, onChange: function (e) { set({ day: parseInt(e.target.value, 10) }); } }, props.days.map(function (d, i) { return h("option", { key: i, value: i }, d + "s"); })), h("label", null, "at ", h("input", { type: "time", className: "ats-input", value: timeVal(), onChange: function (e) { setTime(e.target.value); } })));
      else if (v.kind === "times") editor = h("div", { className: "ats-inline" },
        v.hours.map(function (hh) { return h("span", { key: hh, className: "ats-chip" }, pad(hh) + ":" + pad(v.minute), h("button", { type: "button", "aria-label": "Remove", disabled: v.hours.length <= 2, onClick: function () { set({ hours: v.hours.filter(function (x) { return x !== hh; }) }); } }, "✕")); }),
        v.hours.length < 8 ? h("select", { className: "ats-input", value: "", onChange: function (e) { if (e.target.value !== "") set({ hours: v.hours.concat([parseInt(e.target.value, 10)]).filter(function (x, i, a) { return a.indexOf(x) === i; }).sort(function (a, b) { return a - b; }) }); } }, [h("option", { key: "p", value: "" }, "+ add a time")].concat(Array.from({ length: 24 }, function (_x, i) { return v.hours.indexOf(i) >= 0 ? null : h("option", { key: i, value: i }, hourLabel(i)); }))) : null,
        h("label", null, "at minute ", h("input", { type: "number", min: 0, max: 59, className: "ats-input ats-num", value: v.minute, onChange: function (e) { var n = parseInt(e.target.value, 10); if (!isNaN(n)) set({ minute: Math.max(0, Math.min(59, n)) }); } })));
      else if (v.kind === "window") editor = h("div", { className: "ats-inline" },
        h("label", null, "Every ", h("select", { className: "ats-input", value: v.interval, onChange: function (e) { set({ interval: parseInt(e.target.value, 10) }); } }, props.intervals.map(function (n) { return h("option", { key: n, value: n }, n + " minutes"); }))),
        h("label", null, "from ", h(Hours, { value: v.start, onChange: function (n) { set({ start: n }); } })),
        h("label", null, "to ", h(Hours, { value: v.end, onChange: function (n) { set({ end: n }); } })));
    }
    return h("div", { className: "ats-job" },
      h("div", { className: "ats-row-t" }, j.name, !j.enabled ? h("span", { className: "ats-paused" }, "paused") : null),
      j.editable ? editor : h("div", { className: "ats-note" }, "Runs " + j.when + " (a custom schedule; change it with me)."),
      j.editable ? h("div", { className: "ats-actions" }, h("button", { type: "button", className: "ats-btn ats-btn-primary", disabled: !dirty || busy, onClick: save }, "Save"), msg ? h("span", { className: "ats-msg" }, msg) : null) : null);
  }

  function Schedule() {
    var d_ = useState(null), data = d_[0], setData = d_[1];
    var e_ = useState(""), err = e_[0], setErr = e_[1];
    function load() { fetchJSON(API + "/schedule").then(setData).catch(function (e) { setErr(errText(e)); }); }
    useEffect(function () { load(); }, []);
    return h("div", { className: "ats-card" },
      h("div", { className: "ats-h" }, "When things run"),
      h("div", { className: "ats-note" }, "Times are your local time. To pause a job or run it now, use the Workflows tab."),
      err ? h("div", { className: "ats-err" }, err) : null,
      !data ? h("div", { className: "ats-note" }, "Loading…") : data.jobs.map(function (j) { return h(JobRow, { key: j.id, job: j, days: data.days, intervals: data.intervals, onSaved: load }); }));
  }

  /* ---------- devices ---------- */

  function Devices() {
    var d_ = useState(null), list = d_[0], setList = d_[1];
    var m_ = useState(""), msg = m_[0], setMsg = m_[1];
    function load() { fetchJSON(CHAT + "/push/devices").then(function (r) { setList(r.devices || []); }).catch(function (e) { setMsg(errText(e)); }); }
    useEffect(function () { load(); }, []);
    function remove(d) {
      post(CHAT + "/push/devices/remove", { id: d.id }).then(function () { setMsg("Removed. It stops receiving pings."); load(); }).catch(function (e) { setMsg(errText(e)); });
    }
    return h("div", { className: "ats-card" },
      h("div", { className: "ats-h" }, "Your devices"),
      h("div", { className: "ats-note" }, "Devices that receive pings. Add one from the bell in Chat."),
      !list ? h("div", { className: "ats-note" }, "Loading…") : !list.length ? h("div", { className: "ats-note" }, "None yet.") : list.map(function (d) {
        return h("div", { key: d.id, className: "ats-row" },
          h("div", { className: "ats-row-text" }, h("div", { className: "ats-row-t" }, d.label), h("div", { className: "ats-note" }, (d.service || "") + (d.added ? " · added " + new Date(d.added * 1000).toLocaleDateString() : ""))),
          h("button", { type: "button", className: "ats-btn", onClick: function () { remove(d); } }, "Remove"));
      }),
      msg ? h("div", { className: "ats-msg" }, msg) : null);
  }

  /* ---------- cloud use ---------- */

  function fmtK(n) { return n >= 1000 ? (n / 1000).toFixed(1) + "K" : String(n || 0); }

  function Cloud() {
    var e_ = useState(null), eng = e_[0], setEng = e_[1];
    var r_ = useState(null), room = r_[0], setRoom = r_[1];
    useEffect(function () {
      fetchJSON("/api/plugins/atrium-mission/engine").then(setEng).catch(function () { /* optional */ });
      fetchJSON("/api/plugins/atrium-room/models").then(setRoom).catch(function () { /* optional */ });
    }, []);
    var cloud = eng && eng.engine === "claude";
    return h("div", { className: "ats-card" },
      h("div", { className: "ats-h" }, "Cloud use"),
      h("div", { className: "ats-row" }, h("div", { className: "ats-row-text" }, h("div", { className: "ats-row-t" }, "Engine for chats from the app"),
        h("div", { className: "ats-note" }, !eng ? "Loading…" : cloud ? "Claude in the cloud (" + (eng.seconds_left == null ? "until switched back" : Math.max(1, Math.round(eng.seconds_left / 60)) + " min left") + ")" : "Local: your own GPUs. Nothing leaves the server.")),
        h("button", { type: "button", className: "ats-btn", onClick: function () { window.location.hash = "persona"; } }, "Change in Persona")),
      room && room.today ? h("div", { className: "ats-row" }, h("div", { className: "ats-row-text" }, h("div", { className: "ats-row-t" }, "Claude in the Room today"),
        h("div", { className: "ats-note" }, room.today.calls + " of " + room.daily_limit + " replies · " + fmtK(room.today.in) + " tokens in, " + fmtK(room.today.out) + " out"))) : null,
      h("div", { className: "ats-note" }, "Costs are billed by Anthropic; see your Anthropic console for the real numbers."));
  }

  /* ---------- voice ---------- */

  /* ---------- the agent's name ---------- */

  function AgentCard() {
    var d_ = useState(null), data = d_[0], setData = d_[1];
    var v_ = useState(""), val = v_[0], setVal = v_[1];
    var m_ = useState(""), msg = m_[0], setMsg = m_[1];
    useEffect(function () { fetchJSON(CHAT + "/agent").then(function (r) { setData(r); setVal(r.name); }).catch(function (e) { setMsg(errText(e)); }); }, []);
    function save() {
      setMsg("");
      post(CHAT + "/agent", { name: val }).then(function (r) {
        try { window.sessionStorage.setItem("atrium.agent", r.name); } catch (x) { /* ignore */ }
        setMsg("Saved. Reloading so every page uses it…");
        setTimeout(function () { window.location.reload(); }, 700);
      }).catch(function (e) { setMsg(errText(e)); });
    }
    return h("div", { className: "ats-card" },
      h("div", { className: "ats-h" }, "Your agent's name"),
      h("div", { className: "ats-note" }, "What you call your agent. It appears on every page, in notifications and in the Room. Letters, numbers, spaces, dots, apostrophes and hyphens; up to 40 characters."),
      !data ? h("div", { className: "ats-note" }, "Loading…") : h("div", { className: "ats-inline" },
        h("input", { type: "text", className: "ats-input", value: val, maxLength: 40, onChange: function (e) { setVal(e.target.value); } }),
        h("button", { type: "button", className: "ats-btn", disabled: !val.trim() || val.trim() === data.name, onClick: save }, "Save")),
      msg ? h("div", { className: "ats-msg" }, msg) : null);
  }

  function VoiceCard() {
    var d_ = useState(null), data = d_[0], setData = d_[1];
    var m_ = useState(""), msg = m_[0], setMsg = m_[1];
    var b_ = useState(false), busy = b_[0], setBusy = b_[1];
    useEffect(function () { fetchJSON(CHAT + "/voice").then(setData).catch(function (e) { setMsg(errText(e)); }); }, []);
    function choose(id) {
      post(CHAT + "/voice", { voice: id }).then(function (r) { setData(Object.assign({}, data, { voice: r.voice })); setMsg("Saved. {{agent}} will use this voice from now on."); }).catch(function (e) { setMsg(errText(e)); });
    }
    function preview() {
      if (!data) return;
      setBusy(true); setMsg("");
      var tok = typeof window.__HERMES_SESSION_TOKEN__ === "string" ? window.__HERMES_SESSION_TOKEN__ : "";
      fetch(CHAT + "/speak", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Hermes-Session-Token": tok }, body: JSON.stringify({ text: "Hi. This is how I sound when I read my answers to you.", voice: data.voice }) })
        .then(function (r) { if (!r.ok) throw new Error("The voice is not available right now."); return r.blob(); })
        .then(function (b) { var u = URL.createObjectURL(b); var a = new Audio(u); a.onended = function () { URL.revokeObjectURL(u); }; return a.play(); })
        .catch(function (e) { setMsg(errText(e)); })
        .then(function () { setBusy(false); });
    }
    return h("div", { className: "ats-card" },
      h("div", { className: "ats-h" }, "Her voice"),
      h("div", { className: "ats-note" }, "Used when {{agent}} reads her replies aloud in Chat. The voice runs on your own server."),
      !data ? h("div", { className: "ats-note" }, "Loading…") : h("div", { className: "ats-inline" },
        h("select", { className: "ats-input", value: data.voice, onChange: function (e) { choose(e.target.value); } }, data.choices.map(function (c) { return h("option", { key: c.id, value: c.id }, c.label); })),
        h("button", { type: "button", className: "ats-btn", disabled: busy, onClick: preview }, busy ? "Playing…" : "▶ Preview")),
      msg ? h("div", { className: "ats-msg" }, msg) : null);
  }

  /* ---------- shortcuts ---------- */

  function Account() {
    var who = (window.__APP_CONFIG__ && window.__APP_CONFIG__.user) || "you";
    return h("div", { className: "ats-card" },
      h("div", { className: "ats-h" }, "Account"),
      h("div", { className: "ats-note" }, "Signed in as " + who + ". Signing out ends this session on this device only."),
      h("form", { method: "post", action: "/logout", className: "ats-actions" }, h("button", { type: "submit", className: "ats-btn" }, "Sign out")));
  }

  function Shortcuts() {
    return h("div", { className: "ats-card" },
      h("div", { className: "ats-h" }, "Shortcuts"),
      h("div", { className: "ats-actions ats-wrap" },
        h("a", { className: "ats-btn", href: "#persona", title: "Her persona text and the engine switch" }, "✦ Persona"),
        h("a", { className: "ats-btn", href: "#avatar", title: "How she looks" }, "◐ Avatar"),
        h("a", { className: "ats-btn", href: "/agent-chat?approvals=1" }, "✉ Approvals"),
        h("a", { className: "ats-btn", href: "/agent-workflows" }, "Workflows")));
  }

  function embed(key, missing) {      // another framework page shown inside a Settings section
    return function Embedded() {
      var C = window.__APP_SUBPAGES__ && window.__APP_SUBPAGES__[key];
      return C ? h(C, null) : h("p", { className: "ats-note" }, missing);
    };
  }

  function ConnectCard(props) {
    var entry = props.entry;
    function submit(event) {
      event.preventDefault();
      var form = event.currentTarget;
      var env = {};
      new FormData(form).forEach(function (value, key) {
        if (value) env[key] = value;
      });
      form.reset();
      props.connect(entry.name, env);
    }
    return h("div", { className: "ats-row ats-connection" },
      h("h4", { className: "ats-row-t" }, entry.name),
      entry.description && h("p", { className: "ats-note" }, entry.description),
      entry.host && h("p", { className: "ats-note" }, entry.host),
      entry.installed || props.installed ? h("div", { className: "ats-note" }, "Installed") :
        entry.blocked ? h("div", { className: "ats-note" }, entry.blocked) :
          h("form", { onSubmit: submit },
            entry.fields.map(function (field) {
              return h("label", { key: field.name, className: "ats-connection-field" }, field.label,
                h("input", { name: field.name, type: "password", autoComplete: "off",
                  required: field.required, maxLength: 8192, disabled: props.disabled, className: "ats-input" }));
            }),
            h("button", { type: "submit", className: "ats-btn", disabled: props.disabled }, "Connect")));
  }

  function Connections() {
    var catalogState = useState(null), catalog = catalogState[0], setCatalog = catalogState[1];
    var installedState = useState(null), installed = installedState[0], setInstalled = installedState[1];
    var filterState = useState(""), filter = filterState[0], setFilter = filterState[1];
    var busyState = useState(false), busy = busyState[0], setBusy = busyState[1];
    var resultState = useState(null), result = resultState[0], setResult = resultState[1];
    var messageState = useState(""), message = messageState[0], setMessage = messageState[1];
    var base = API + "/connections";

    function read(path, key, set) {
      return fetchJSON(base + path).then(function (data) { set({ rows: data[key] }); },
        function (error) { set({ error: errText(error) }); });
    }
    function refresh() {
      return Promise.all([read("/catalog", "entries", setCatalog), read("/installed", "servers", setInstalled)]);
    }
    useEffect(function () { refresh(); }, []);
    function run(name, suffix, method, body, success) {
      setMessage("");
      setResult(null);
      setBusy(true);
      var options = { method: method };
      if (body) {
        options.headers = { "Content-Type": "application/json" };
        options.body = JSON.stringify(body);
      }
      return fetchJSON(base + "/" + encodeURIComponent(name) + suffix, options).then(function (data) {
        if (suffix === "/test") {
          setResult({ name: name, tools: data.tools });
          return;
        }
        setMessage(success);
        return refresh();
      }).catch(function (error) {
        setMessage(errText(error));
        if (suffix !== "/test") return refresh();
      }).finally(function () { setBusy(false); });
    }
    function connect(name, env) {
      return run(name, "/connect", "POST", { env: env }, name + " connected. Test it, then start a fresh chat to use its tools.");
    }
    var installedNames = new Set(installed && installed.rows ? installed.rows.map(function (server) { return server.name; }) : []);
    var matching = catalog && catalog.rows && catalog.rows.filter(function (entry) {
      return (entry.name + " " + entry.description).toLowerCase().includes(filter.trim().toLowerCase());
    });

    return h(SDK.React.Fragment, null,
      h("div", { role: "status", className: "ats-note" }, busy ? "Working…" : message),
      h("div", { className: "ats-card" },
        h("h3", { className: "ats-h" }, "Installed connections"),
        h("p", { className: "ats-note" }, "Start a fresh chat after Hermes reloads its connections. The gateway may take a moment to pick up changes."),
        h("button", { type: "button", className: "ats-btn", disabled: busy, onClick: function () {
          setBusy(true);
          setMessage("");
          setResult(null);
          refresh().finally(function () { setBusy(false); });
        } }, "Refresh"),
        !installed ? h("p", { className: "ats-note" }, "Loading…") :
          installed.error ? h("p", { role: "alert", className: "ats-note" }, installed.error) :
            !installed.rows.length ? h("p", { className: "ats-note" }, "No installed connections.") :
              installed.rows.map(function (server) {
                return h("div", { key: server.name, className: "ats-row ats-connection" },
                  h("h4", { className: "ats-row-t" }, server.name),
                  h("p", { className: "ats-note" }, (server.enabled ? "Enabled" : "Disabled") +
                    (server.managed ? " · Managed by a Hermes plugin" : "")),
                  h("div", { className: "ats-actions ats-wrap" },
                    h("button", { type: "button", className: "ats-btn", disabled: busy, onClick: function () {
                      run(server.name, "/test", "POST");
                    } }, "Test"),
                    !server.managed && h("button", { type: "button", className: "ats-btn", disabled: busy, onClick: function () {
                      run(server.name, "/enabled", "PUT", { enabled: !server.enabled },
                        server.name + (server.enabled ? " disabled." : " enabled."));
                    } }, server.enabled ? "Disable" : "Enable"),
                    !server.managed && h("button", { type: "button", className: "ats-btn", disabled: busy, onClick: function () {
                      if (window.confirm("Remove " + server.name + "? Hermes may retain saved credentials.")) run(server.name, "", "DELETE", null, server.name + " removed.");
                    } }, "Remove")),
                  result && result.name === server.name && h("div", { role: "status" },
                    h("p", { className: "ats-note" }, result.tools.length ? "Tools discovered by Hermes" : "Connection succeeded. Hermes discovered no tools."),
                    result.tools.length > 0 && h("ul", null, result.tools.map(function (tool, index) {
                      return h("li", { key: index }, tool);
                    }))));
              })),
      h("div", { className: "ats-card" },
        h("h3", { className: "ats-h" }, "Hermes MCP catalog"),
        !catalog ? h("p", { className: "ats-note" }, "Loading…") :
          catalog.error ? h("p", { role: "alert", className: "ats-note" }, catalog.error) :
            h(SDK.React.Fragment, null,
              h("label", { className: "ats-connection-field" }, "Find a connection",
                h("input", { type: "search", className: "ats-input", value: filter,
                  onChange: function (event) { setFilter(event.target.value); } })),
              !matching.length && h("p", { className: "ats-note" }, "No matching connections."),
              matching.map(function (entry) {
                return h(ConnectCard, { key: entry.name, entry: entry, installed: installedNames.has(entry.name),
                  disabled: busy || !installed || !!installed.error, connect: connect });
              }))));
  }

  var SECTIONS = [
    { id: "general", label: "General", icon: "🛠️", note: "Your agent's name and voice, and shortcuts to the other pages.", parts: [AgentCard, VoiceCard, Shortcuts, Account] },
    { id: "notifications", label: "Notifications", icon: "🔔", note: "What is allowed to buzz your phone, and which devices get it.", parts: [Notifications, Devices] },
    { id: "schedule", label: "Schedule", icon: "⏰", note: "When each of the agent's jobs runs.", parts: [Schedule] },
    { id: "persona", label: "Persona", icon: "🎭", note: "Who the agent is and the rules she works by, with every earlier version kept.", parts: [embed("persona", "The persona editor did not load.")] },
    { id: "avatar", label: "Avatar", icon: "🎨", note: "Pick and customise how the agent looks.", parts: [embed("avatar", "The avatar editor did not load.")] },
    { id: "memory", label: "Memory", icon: "🧠", note: "The agent's notes as a graph: every note a dot, every link a line. Read only.", parts: [embed("memory", "The memory view did not load.")] },
    { id: "engine", label: "Cloud use", icon: "☁️", note: "Which engine answers chats from the app, and what the cloud has used.", parts: [Cloud] },
    { id: "connections", label: "Connections", icon: "🔌", note: "Connect external tools to Hermes.", parts: [Connections] },
    { id: "server", label: "Server", icon: "🖥️", note: "Install updates, reboot or shut down the machine this runs on. Owners only.", parts: [embed("server", "The server actions did not load.")], hostOnly: true }
  ];

  function startSection() {
    var id = (window.location.hash || "").replace(/^#/, "");
    return SECTIONS.some(function (x) { return x.id === id; }) ? id : SECTIONS[0].id;
  }

  function Page() {
    var s_ = useState(startSection()), cur = s_[0], setCur = s_[1];
    useEffect(function () {
      function onHash() { setCur(startSection()); }
      window.addEventListener("hashchange", onHash);
      return function () { window.removeEventListener("hashchange", onHash); };
    }, []);
    var hs_ = useState(false), hostOn = hs_[0], setHostOn = hs_[1];       // the Server section appears only when this install has a host helper
    useEffect(function () {
      fetchJSON("/api/host/status").then(function (j) { setHostOn(!!(j && j.enabled)); }).catch(function () { /* stays hidden */ });
    }, []);
    var visible = SECTIONS.filter(function (x) { return !x.hostOnly || hostOn; });
    function pick(id) { setCur(id); try { window.history.replaceState(null, "", "#" + id); } catch (x) { /* ignore */ } }
    var sec = visible.filter(function (x) { return x.id === cur; })[0] || visible[0];
    return h("div", { className: "ats-page" },
      h("div", { className: "ats-title" }, "Settings"),
      h("div", { className: "ats-layout" },
        h("nav", { className: "ats-nav", "aria-label": "Settings sections" }, visible.map(function (x) {
          return h("button", { key: x.id, type: "button", className: "ats-navitem" + (x.id === sec.id ? " ats-current" : ""), "aria-current": x.id === sec.id ? "page" : null, onClick: function () { pick(x.id); } },
            h("span", { className: "ats-navicon", "aria-hidden": "true", style: { "--h": ({ general: 215, notifications: 40, schedule: 150, persona: 320, avatar: 275, memory: 185, engine: 205, server: 25 })[x.id] || 260 } }, x.icon), x.label);
        })),
        h("section", { className: "ats-panel", key: sec.id },
          h("h2", { className: "ats-panel-h" }, sec.label),
          h("div", { className: "ats-sub" }, sec.note),
          sec.parts.map(function (P, n) { return h(P, { key: n }); }))));
  }

  REG.register("atrium-settings", Page);
})();
