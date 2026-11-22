/**
 * Settings > Server: one page, in the order things happen.
 *
 *   check  ->  what is waiting  ->  update (confirm, progress and result right in the row)  ->  recently updated, with Roll back  ->  restart if needed
 *
 * Package updates, container updates, reboot and shutdown all go through the host helper (owners only; shown only when the install has one).
 * Shown inside the Settings page as window.__APP_SUBPAGES__.server. Plain IIFE, no build.
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;
  var h = SDK.React.createElement;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var fetchJSON = SDK.fetchJSON;
  var API = "/api/host";
  var expecting = false, downSeen = false, expectingSince = 0;      // after a reboot or shutdown countdown ends: wait until the server has gone away and come back

  function errText(e) {
    var m = String((e && e.message) || e || "");
    var i = m.indexOf("{");
    if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* fall through */ } }
    return m.replace(/^\d+:\s*/, "").slice(0, 200) || "Something went wrong.";
  }
  function post(url, body) {
    return fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  }
  function ago(ts) {
    var s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 90) return "just now";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    if (s < 172800) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " days ago";
  }
  function uptime(sec) {
    if (sec == null) return "";
    var d = Math.floor(sec / 86400), hrs = Math.floor((sec % 86400) / 3600);
    return d ? d + " d " + hrs + " h" : hrs + " h " + Math.floor((sec % 3600) / 60) + " min";
  }
  function plural(n, w) { return n + " " + w + (n === 1 ? "" : "s"); }

  function describe(p) {
    // what is available, and for which part of the group: "nextcloud, cron to 35.0.3; redis to 7.4.12; db to 17.12"
    var groups = {}, order = [], one = (p.services || []).filter(function (s) { return s.update; });
    one.forEach(function (s) {
      var tag = s.newer && s.newer !== "new build" ? s.newer : "";
      var label = s.service === p.project ? p.project : s.service.indexOf(p.project + "-") === 0 ? s.service.slice(p.project.length + 1) : s.service;
      if (!(tag in groups)) { groups[tag] = []; order.push(tag); }
      if (groups[tag].indexOf(label) < 0) groups[tag].push(label);
    });
    if (!order.length) return "";
    if (one.length === 1 && one[0].service === p.project) return (order[0] ? "version " + order[0] : "new build") + " available";
    return order.map(function (t) { return groups[t].join(", ") + (t ? " to " + t : ": new build"); }).join("; ") + " available";
  }

  var RISK = { low: "Low", medium: "Medium", high: "High", care: "Care" };

  function Row(p) {
    var r = p.row, live = p.running, open = p.open;
    return h("div", { className: "sv-row" },
      h("div", { className: "sv-main" }, h("div", { className: "sv-name" }, r.name), h("div", { className: "sv-sub" }, r.sub)),
      h("div", { className: "sv-chips" }, h("span", { className: "sv-chip" }, r.type), r.risk ? h("span", { className: "sv-chip sv-risk-" + r.risk }, RISK[r.risk] || r.risk) : null),
      h("div", { className: "sv-act" }, live ? null : r.button ? h("button", { type: "button", className: "ats-btn" + (r.primary ? " ats-btn-primary" : ""), disabled: p.locked, onClick: p.onOpen }, r.button)
        : r.note ? h("span", { className: "sv-note" }, r.note) : null),
      live ? h("div", { className: "sv-x" }, h("div", { className: "sv-step" }, live), h("div", { className: "sv-bar" }, h("i", null))) : null,
      open && !live ? h("div", { className: "sv-x" },
        h("div", null, open.text),
        open.warn ? h("div", { className: "sv-warn" }, open.warn) : null,
        open.lines && open.lines.length ? h("ul", { className: "sv-lines" }, open.lines.map(function (l) { return h("li", { key: l }, l); })) : null,
        h("div", { className: "sv-btns" },
          h("button", { type: "button", className: "ats-btn" + (open.danger ? " ats-btn-danger" : " ats-btn-primary"), disabled: p.busy, onClick: p.onGo }, open.go),
          h("button", { type: "button", className: "ats-btn", disabled: p.busy, onClick: p.onCancel }, "Cancel"))) : null);
  }

  function Server() {
    var d_ = useState(null), data = d_[0], setData = d_[1];
    var c_ = useState(null), cdata = c_[0], setCdata = c_[1];
    var e_ = useState(""), err = e_[0], setErr = e_[1];
    var o_ = useState(""), openKey = o_[0], setOpenKey = o_[1];
    var b_ = useState(false), busy = b_[0], setBusy = b_[1];
    var m_ = useState(""), msg = m_[0], setMsg = m_[1];
    var n_ = useState(0), tick = n_[0], setTick = n_[1];
    var w_ = useState(null), went = w_[0], setWent = w_[1];      // a power action ran out its countdown: waiting for the server to come back

    function load() {
      var a = fetchJSON(API + "/status").then(function (j) {
        setData(j); setErr("");
        if (expecting && j && !j.reachable) downSeen = true;
        if (expecting && downSeen && j && j.reachable) { expecting = false; downSeen = false; setWent(null); setMsg("The server is back."); }
        else if (expecting && !downSeen && j && j.reachable && Date.now() - expectingSince > 60000) { expecting = false; setWent(null); setMsg("The server never went down (is the helper in test mode?)."); }
      }).catch(function (e) { if (expecting) { downSeen = true; return; } setErr(errText(e)); });
      var b = fetchJSON(API + "/containers").then(setCdata).catch(function () { setCdata(function (x) { return x || { off: true }; }); });
      return Promise.all([a, b]);
    }
    useEffect(function () { load(); }, []);
    useEffect(function () {      // a notice fades after a few seconds
      if (!msg) return undefined;
      var id = setTimeout(function () { setMsg(""); }, 6000);
      return function () { clearTimeout(id); };
    }, [msg]);
    var live = !!((data && ((data.update && data.update.running) || data.scheduled)) || (cdata && (cdata.op || cdata.checking)) || went);
    useEffect(function () {
      var id = setInterval(function () { load(); setTick(function (n) { return n + 1; }); }, live ? 2500 : 20000);
      return function () { clearInterval(id); };
    }, [live]);
    useEffect(function () {      // a half second beat for the countdown text
      if (!(data && data.scheduled)) return undefined;
      var id = setInterval(function () { setTick(function (n) { return n + 1; }); }, 500);
      return function () { clearInterval(id); };
    }, [!!(data && data.scheduled)]);

    if (!data) return h("div", { className: "ats-note" }, err || "Loading…");
    if (!data.enabled) return h("div", { className: "ats-note" }, "Server actions are not set up on this install.");
    if (!data.reachable && !went) return h("div", { className: "ats-err" }, "The host helper is not answering, so server actions are unavailable.");

    var pk = data.packages || { safe: [], care: [] };
    var up = data.update || {};
    var sched = data.scheduled;
    var cd = cdata && !cdata.off ? cdata : null;
    var warnings = [];
    if (data.busy && data.busy.runs) warnings.push("The agent is in the middle of a chat run.");
    if (data.busy && data.busy.cards) warnings.push("A board card is running.");
    if (data.backup_running) warnings.push("A backup is running.");

    /* ---- the rows: what can be updated ---- */
    var rows = [];
    if (pk.safe.length) rows.push({ key: "sys:safe", name: "System packages", sub: plural(pk.safe.length, "small package") + " · no restarts expected", type: "System", risk: "low", button: "Update", primary: true, kind: "action", action: "update_safe", text: "Installs the small system packages that are waiting. No restarts are expected.", go: "Update now", rank: 0 });
    if (pk.care.length) rows.push({ key: "sys:care", name: "Docker, GPU tools and the network tunnel", sub: plural(pk.care.length, "package") + " · can briefly drop containers and remote access", type: "System", risk: "care", button: "Update", primary: false, kind: "action", action: "update_care", text: "Services restart for a moment and remote access can drop. The update runs as its own job on the server, so this page can reconnect and show the result.", go: "Update now", rank: 1 });
    ((cd && cd.projects) || []).forEach(function (p) {
      if (!p.update) return;
      var names = (p.services || []).filter(function (s) { return s.update; }).map(function (s) { return s.service; });
      rows.push({ key: "ct:" + p.project, name: p.project, sub: describe(p), type: "Container", risk: p.tier, button: "Update", primary: false, kind: "container", project: p.project,
        text: "The group is updated together (" + names.join(", ") + "). The current build is kept for " + cd.keep_days + " days, and if the new one is not healthy it rolls back by itself.",
        warn: p.tier === "high" ? "High risk: this update could stop working properly, for example if the new version changes its data files. Your current build is saved and Roll back puts it back, but a rollback restores the program, not its data. If the data matters, back it up first." : "",
        go: "Update now", rank: p.tier === "low" ? 2 : p.tier === "medium" ? 3 : 4 });
    });
    rows.sort(function (a, b) { return a.rank - b.rank; });

    /* ---- what is running right now (shown inside its row) ---- */
    var liveText = {};
    if (up.running) liveText[(up.progress && up.progress.kind) === "update_care" ? "sys:care" : "sys:safe"] = ((up.progress && up.progress.step) || "working") + (up.progress && up.progress.total ? " (" + plural(up.progress.total, "package") + ")" : "") + ". It runs on the server, so you can leave this page.";
    if (cd && cd.op) liveText["ct:" + cd.op.project] = (cd.op.op === "container_rollback" ? "Rolling back: " : "Updating: ") + cd.op.step + ". It runs on the server, so you can leave this page.";
    var locked = !!(up.running || sched || (cd && cd.op));

    /* ---- recently updated (with Roll back) ---- */
    var recent = [];
    if (cd) Object.keys(cd.restore_points || {}).forEach(function (proj) {
      recent.push({ key: "rb:" + proj, project: proj, created: cd.restore_points[proj].created });
    });
    recent.sort(function (a, b) { return b.created - a.created; });

    /* ---- the panel that opens under a row ---- */
    function openFor(key) {
      if (openKey !== key) return null;
      var row = rows.filter(function (r) { return r.key === key; })[0];
      var notes = warnings.map(function (w) { return "⚠ " + w; });
      if (row) return { text: row.text, warn: row.warn, go: row.go, lines: notes, danger: false };
      if (key.indexOf("rb:") === 0) return { text: "Puts back the build you had before the last update and recreates the containers. Your data stays as it is now: if the newer version changed its data files, the program rolls back but the data does not.", go: "Roll back", lines: [], danger: false };
      if (key === "power:reboot") return { text: "Everything stops and then starts again by itself, usually within three minutes. You get " + (data.countdown || 30) + " seconds to cancel.", go: "Reboot", lines: notes, danger: false };
      if (key === "power:shutdown") return { text: "Everything stops and the server stays off until someone presses its power button. You cannot start it again from this app. You get " + (data.countdown || 30) + " seconds to cancel.", go: "Shut down", lines: notes, danger: true };
      return null;
    }
    function go() {
      setBusy(true);
      var key = openKey, p;
      var row = rows.filter(function (r) { return r.key === key; })[0];
      if (row && row.kind === "action") p = post(API + "/action", { action: row.action, force: true });
      else if (row && row.kind === "container") p = post(API + "/containers/update", { project: row.project, force: true });
      else if (key.indexOf("rb:") === 0) p = post(API + "/containers/rollback", { project: key.slice(3) });
      else p = post(API + "/action", { action: key === "power:reboot" ? "reboot" : "shutdown", force: true });
      p.then(function () { setOpenKey(""); setMsg(key.indexOf("power:") === 0 ? "Scheduled." : "Started."); return load(); })
        .catch(function (e) { setErr(errText(e)); }).then(function () { setBusy(false); });
    }
    function cancelPower() {
      setBusy(true);
      post(API + "/cancel", {}).then(function () { setMsg("Cancelled. Nothing changed."); return load(); })
        .catch(function (e) { setErr(errText(e)); }).then(function () { setBusy(false); });
    }
    function check() {
      setBusy(true);
      post(API + "/containers/check", {}).then(function () { setMsg("Checking for newer images…"); return load(); })
        .catch(function (e) { setErr(errText(e)); }).then(function () { setBusy(false); });
    }

    var left = sched ? Math.max(0, Math.round(sched.at - Date.now() / 1000)) : 0;
    if (sched && left <= 0 && !went) { expecting = true; downSeen = false; expectingSince = Date.now(); setTimeout(function () { setWent({ action: sched.action }); }, 0); }

    /* ---- header ---- */
    var times = [];
    if (cd && cd.checked_at) times.push("Checked " + ago(cd.checked_at));
    if (data.uptime_seconds != null) times.push("Up for " + uptime(data.uptime_seconds));
    var headLine = rows.length ? plural(rows.length, "update") + " waiting" : "Everything is up to date";

    /* ---- banners ---- */
    var banner = null;
    if (went) banner = h("div", { className: "sv-banner sv-banner-danger" }, h("div", null, h("b", null, went.action === "shutdown" ? "Shutting down." : "Rebooting."), " ", went.action === "shutdown" ? "This page will not be reachable until someone turns the server back on." : "This page reconnects when the server is back, usually within three minutes."));
    else if (sched) banner = h("div", { className: "sv-banner sv-banner-danger" }, h("div", null, h("b", null, (sched.action === "shutdown" ? "Shutting down" : "Rebooting") + " in " + left + " s."), " Requested by " + (sched.user || "an owner") + "."),
      h("button", { type: "button", className: "ats-btn ats-btn-primary", disabled: busy, onClick: cancelPower }, "Cancel"));
    else if (data.reboot_required) banner = h("div", { className: "sv-banner" }, h("div", null, "A restart is needed to finish the system updates."),
      h("button", { type: "button", className: "ats-btn", disabled: locked, onClick: function () { setOpenKey("power:reboot"); } }, "Restart…"));

    /* ---- results of the last run: a line, not a card ---- */
    var results = [];
    var lc = cd && cd.last && Date.now() / 1000 - cd.last.finished < 900 ? cd.last : null;
    if (lc) results.push({ ok: lc.ok, text: (lc.op === "container_rollback" ? "Rolled back " : "Updated ") + lc.project + (lc.ok ? ": " : " did not succeed: ") + lc.note + (lc.auto_rolled_back ? ". The previous build is running again." : "") });
    var lu = up.last && Date.now() / 1000 - up.last.finished < 900 && !up.running ? up.last : null;
    if (lu) results.push({ ok: lu.ok, text: (lu.kind === "update_care" ? "Docker, GPU tools and the network tunnel" : "System packages") + (lu.ok ? ": " + plural((lu.packages || []).length, "package") + " installed" + ((lu.containers_missing || []).length ? ", missing containers: " + lu.containers_missing.join(", ") : ", all " + lu.containers_after + " containers running") + (lu.reboot_required ? ". A restart is needed to finish." : ".") : " failed. See the activity log.") });

    /* ---- the quiet part: up to date, built here, couldn't check ---- */
    var current = [], built = [], unknown = [];
    ((cd && cd.projects) || []).forEach(function (p) {
      var kinds = (p.services || []).map(function (s) { return s.kind; });
      if (p.update || recent.some(function (r) { return r.project === p.project; })) return;       // already shown above
      if (kinds.length && kinds.every(function (k) { return k === "built"; })) built.push(p.project);
      else if (p.unknown) unknown.push(p.project);
      else current.push(p.project);
    });

    function rowFor(r, extra) {
      return h(Row, { key: r.key, row: r, locked: locked, busy: busy, running: liveText[extra || r.key] || "", open: openFor(r.key),
        onOpen: function () { setOpenKey(r.key); setErr(""); }, onGo: go, onCancel: function () { setOpenKey(""); setErr(""); } });
    }
    var powerOpen = openKey.indexOf("power:") === 0 && !locked ? openFor(openKey) : null;

    return h("div", { className: "sv" },
      h("div", { className: "sv-top" },
        h("div", null, h("div", { className: "sv-head" }, headLine), h("div", { className: "sv-sub" }, times.join(" · "))),
        cd ? h("button", { type: "button", className: "ats-btn", disabled: busy || locked, onClick: check }, cd.checking ? "Checking…" : "Check now") : null),
      banner,
      msg ? h("div", { className: "ats-msg" }, msg) : null,
      err ? h("div", { className: "ats-err" }, err) : null,
      data.dry_run ? h("div", { className: "sv-sub" }, "Test mode: updates are simulated and reboot and shutdown do nothing.") : null,
      results.map(function (x, i) { return h("div", { key: i, className: "sv-result " + (x.ok ? "sv-ok" : "sv-bad") }, (x.ok ? "✓ " : "✗ ") + x.text); }),

      rows.length ? h("div", { className: "sv-sec" }, "What can be updated") : null,
      rows.map(function (r) { return rowFor(r); }),

      recent.length ? h("div", { className: "sv-sec" }, "Updated recently") : null,
      recent.map(function (r) {
        return rowFor({ key: r.key, name: r.project, sub: "Updated " + ago(r.created) + " · the previous build is kept for " + cd.keep_days + " days", type: "Container", risk: "", button: "Roll back" }, "ct:" + r.project);
      }),

      (current.length || built.length || unknown.length) ? h("div", { className: "sv-sec" }, "Up to date") : null,
      current.length ? h("div", { className: "sv-quiet" }, current.join(", ") + ". Nothing to do.") : null,
      unknown.length ? h("div", { className: "sv-quiet" }, "Couldn't check right now: " + unknown.join(", ") + ". The registry may be limiting requests; try again later.") : null,
      built.length ? h("div", { className: "sv-quiet" }, "Built on this server, not updated here: " + built.join(", ") + ".") : null,

      h("div", { className: "sv-sec" }, "Power"),
      h("div", { className: "sv-power" },
        h("button", { type: "button", className: "ats-btn", disabled: locked, onClick: function () { setOpenKey("power:reboot"); } }, "Reboot"),
        h("button", { type: "button", className: "ats-btn ats-btn-danger", disabled: locked, onClick: function () { setOpenKey("power:shutdown"); } }, "Shut down")),
      powerOpen ? h("div", { className: "sv-x sv-x-block" }, h("div", null, powerOpen.text),
        powerOpen.lines.length ? h("ul", { className: "sv-lines" }, powerOpen.lines.map(function (l) { return h("li", { key: l }, l); })) : null,
        h("div", { className: "sv-btns" },
          h("button", { type: "button", className: "ats-btn" + (powerOpen.danger ? " ats-btn-danger" : " ats-btn-primary"), disabled: busy, onClick: go }, powerOpen.go),
          h("button", { type: "button", className: "ats-btn", disabled: busy, onClick: function () { setOpenKey(""); } }, "Cancel"))) : null,

      (data.log && data.log.length) ? h("details", { className: "sv-log" },
        h("summary", null, "Recent activity"),
        h("ul", null, data.log.map(function (x, i) { return h("li", { key: i }, h("span", { className: "sv-sub" }, ago(x.ts)), " · ", x.user || "?", " · ", x.action, " · ", x.result); }))) : null);
  }

  window.__APP_SUBPAGES__ = Object.assign(window.__APP_SUBPAGES__ || {}, { server: Server });
})();
