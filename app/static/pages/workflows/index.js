/**
 * Atrium Loom: Workflows (dashboard tab plugin)
 *
 * the agent's scheduled jobs in plain words: when they run, what happened last time, and buttons to run one now or pause
 * and resume it. Also the topics the Nightly Radar searches for (editable here or by telling her in chat), and a
 * feed of what she did by herself. Plain IIFE, no build step.
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
  var API = "/api/plugins/atrium-workflows";

  function post(path, body) {
    return fetchJSON(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  }

  function errText(e) {
    var m = String((e && e.message) || e || "");
    var i = m.indexOf("{");
    if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* fall through */ } }
    return m.replace(/^\d+:\s*/, "").slice(0, 160) || "Something went wrong.";
  }

  function rel(iso) {
    if (!iso) return "never";
    var t = Date.parse(iso);
    if (isNaN(t)) return "";
    var s = Math.round((t - Date.now()) / 1000), a = Math.abs(s), txt;
    if (a < 60) txt = "a moment";
    else if (a < 3600) txt = Math.round(a / 60) + " min";
    else if (a < 86400) txt = Math.round(a / 3600) + " h";
    else txt = Math.round(a / 86400) + " d";
    return s >= 0 ? "in " + txt : txt + " ago";
  }

  function clock(iso) {
    var t = Date.parse(iso);
    if (isNaN(t)) return "";
    return new Date(t).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }

  /* ---------- one job ---------- */

  function JobCard(props) {
    var j = props.job;
    var tone = j.running ? "run" : !j.enabled ? "off" : j.last_status && j.last_status !== "ok" ? "bad" : "ok";
    var label = j.running ? "Running now" : !j.enabled ? "Paused" : j.last_status && j.last_status !== "ok" ? "Last run failed" : j.watches ? "Watching" : "Scheduled";
    return h("div", { className: "atw-card" },
      h("div", { className: "atw-card-head" },
        h("div", { className: "atw-name" }, j.name),
        h("span", { className: "atw-pill atw-pill-" + tone }, label)),
      h("div", { className: "atw-when" }, j.when || j.schedule),
      h("div", { className: "atw-meta" },
        h("span", null, "Next: ", j.enabled && j.next_run ? clock(j.next_run) + " (" + rel(j.next_run) + ")" : "not scheduled"),
        h("span", null, "Last: ", j.last_run ? clock(j.last_run) + " (" + rel(j.last_run) + ")" : "never")),
      j.watches ? h("div", { className: "atw-note" }, "Checks cheaply on schedule and only wakes {{agent}} when something new shows up.") : null,
      j.last_error ? h("div", { className: "atw-err" }, j.last_error) : null,
      j.answer ? h("div", { className: "atw-answer" }, h("span", null, "Last answer: "), j.answer) : null,
      h("div", { className: "atw-actions" },
        h("button", { type: "button", className: "atw-btn atw-btn-primary", disabled: props.busy || j.running, onClick: function () { props.onRun(j); } }, j.running ? "Running…" : "Run now"),
        h("button", { type: "button", className: "atw-btn", disabled: props.busy, onClick: function () { props.onToggle(j); } }, j.enabled ? "Pause" : "Resume")));
  }

  /* ---------- Radar topics ---------- */

  function Interests() {
    var t_ = useState(null), topics = t_[0], setTopics = t_[1];
    var s_ = useState(null), saved = s_[0], setSaved = s_[1];
    var i_ = useState(""), input = i_[0], setInput = i_[1];
    var m_ = useState(""), msg = m_[0], setMsg = m_[1];
    var max_ = useState(12), max = max_[0], setMax = max_[1];
    useEffect(function () {
      fetchJSON(API + "/interests").then(function (r) { setTopics(r.topics); setSaved(r.topics); setMax(r.max || 12); }).catch(function (e) { setMsg(errText(e)); });
    }, []);
    if (!topics) return h("div", { className: "atw-card" }, h("div", { className: "atw-name" }, "Radar topics"), msg ? h("div", { className: "atw-note" }, msg) : h(window.__APP_UI__.kit.Loading, { label: "Loading…" }));
    var dirty = JSON.stringify(topics) !== JSON.stringify(saved);
    function add() {
      var v = input.trim();
      if (!v) return;
      if (topics.length >= max) { setMsg("The list is full (" + max + "). Remove one first."); return; }
      if (topics.some(function (x) { return x.toLowerCase() === v.toLowerCase(); })) { setInput(""); return; }
      setTopics(topics.concat([v])); setInput(""); setMsg("");
    }
    function remove(i) { setTopics(topics.filter(function (_x, k) { return k !== i; })); setMsg(""); }
    function save() {
      post("/interests", { topics: topics }).then(function (r) { setTopics(r.topics); setSaved(r.topics); setMsg("Saved. It applies from tonight's 5:30 AM run."); }).catch(function (e) { setMsg(errText(e)); });
    }
    return h("div", { className: "atw-card" },
      h("div", { className: "atw-card-head" }, h("div", { className: "atw-name" }, "Radar topics")),
      h("div", { className: "atw-note" }, "What the Nightly Radar looks for. You can also just tell {{agent}} in chat to add or remove one."),
      h("div", { className: "atw-chips" }, topics.map(function (t, i) {
        return h("span", { key: t, className: "atw-chip" }, t, h("button", { type: "button", "aria-label": "Remove " + t, onClick: function () { remove(i); } }, "✕"));
      })),
      h("div", { className: "atw-add" },
        h("input", { className: "atw-input", value: input, placeholder: "Add a topic", maxLength: 120, onChange: function (e) { setInput(e.target.value); }, onKeyDown: function (e) { if (e.key === "Enter") add(); } }),
        h("button", { type: "button", className: "atw-btn", onClick: add }, "Add"),
        h("button", { type: "button", className: "atw-btn atw-btn-primary", disabled: !dirty, onClick: save }, "Save")),
      msg ? h("div", { className: "atw-msg" }, msg) : null);
  }

  /* ---------- what she did by herself ---------- */

  var ACTION_LABEL = { "calendar.add": "Added to calendar", "calendar.edit": "Changed an event", "files.put": "Uploaded a file", "files.write": "Saved a file", "files.mkdir": "Made a folder", "files.mv": "Moved a file" };

  function Activity(props) {
    var items = props.items;
    return h("div", { className: "atw-card" },
      h("div", { className: "atw-card-head" }, h("div", { className: "atw-name" }, "What {{agent}} did by herself")),
      !items ? h(window.__APP_UI__.kit.Loading, { label: "Loading…" })
        : !items.length ? h("div", { className: "atw-note" }, "Nothing yet. Calendar changes and saved files will show up here.")
        : h("div", { className: "atw-feed" }, items.map(function (it, i) {
          return h("div", { key: i, className: "atw-feed-row" },
            h("span", { className: "atw-feed-t" }, it.t.slice(5, 16)),
            h("span", { className: "atw-feed-a" }, ACTION_LABEL[it.action] || it.action),
            h("span", { className: "atw-feed-s" }, it.summary));
        })));
  }

  /* ---------- page ---------- */

  function Page() {
    var j_ = useState(null), jobs = j_[0], setJobs = j_[1];
    var a_ = useState(null), act = a_[0], setAct = a_[1];
    var b_ = useState(""), busy = b_[0], setBusy = b_[1];
    var e_ = useState(""), err = e_[0], setErr = e_[1];

    function load() {
      fetchJSON(API + "/jobs").then(function (r) { setJobs(r.jobs || []); setErr(""); }).catch(function (e) { setErr(errText(e)); });
      fetchJSON(API + "/activity").then(function (r) { setAct(r.items || []); }).catch(function () { /* optional */ });
    }
    var running = !!(jobs && jobs.some(function (x) { return x.running; }));
    useEffect(function () {
      load();
      return window.atriumEvery(load, running ? 6000 : 30000);
    }, [running]);

    function run(job) {
      setBusy(job.id);
      post("/jobs/" + job.id + "/run").then(function () { setTimeout(load, 1500); }).catch(function (e) { setErr(errText(e)); }).then(function () { setBusy(""); });
    }
    function toggle(job) {
      setBusy(job.id);
      post("/jobs/" + job.id + (job.enabled ? "/pause" : "/resume")).then(load).catch(function (e) { setErr(errText(e)); }).then(function () { setBusy(""); });
    }

    return h("div", { className: "atw-page" },
      h("div", { className: "atw-title" }, "Workflows"),
      h("div", { className: "atw-sub" }, "{{agent}}'s scheduled jobs. Times are your local time."),
      err ? h("div", { className: "atw-err" }, err) : null,
      h("div", { className: "atw-grid" },
        !jobs ? h(window.__APP_UI__.kit.Loading, { label: "Loading…" }) : jobs.map(function (j) { return h(JobCard, { key: j.id, job: j, busy: busy === j.id, onRun: run, onToggle: toggle }); })),
      h("div", { className: "atw-grid atw-grid-2" }, h(Interests, null), h(Activity, { items: act })));
  }

  REG.register("atrium-workflows", Page);
})();
