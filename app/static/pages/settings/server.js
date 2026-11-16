/**
 * Settings > Server: install updates, reboot, shut down (owners only; shown only when the install has a host helper).
 *
 * Every action asks first, lists what could go wrong right now, and reboot and shutdown wait out a countdown you can cancel.
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
  var expecting = false, downSeen = false, expectingSince = 0;     // after a reboot or shutdown countdown ends: wait until the server has gone away and come back

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

  var ACTIONS = {
    update_safe: { title: "Update system and editor", button: "Update now", body: "Installs the small system packages that are waiting. No restarts expected.", typed: "" },
    update_care: { title: "Update Docker, GPU tools and the network tunnel", button: "Update now", body: "These can restart services for a moment, and remote access can drop. The update runs as its own job on the server, and this page reconnects and shows the result.", typed: "" },
    reboot: { title: "Reboot the server", button: "Reboot", body: "Everything stops and then starts again by itself. Expect about three minutes offline. The containers come back on their own.", typed: "reboot", countdown: true },
    shutdown: { title: "Shut down the server", button: "Shut down", body: "Everything stops and the server stays off until someone presses its power button. You cannot start it again from this app.", typed: "shut down", countdown: true, danger: true }
  };

  function PackageList(props) {
    return h("details", { className: "ats-pkgs" },
      h("summary", null, props.items.length + " package" + (props.items.length === 1 ? "" : "s")),
      h("ul", null, props.items.map(function (p) { return h("li", { key: p.name }, h("code", null, p.name), " ", p.from, " → ", p.to); })));
  }

  function Server() {
    var d_ = useState(null), data = d_[0], setData = d_[1];
    var e_ = useState(""), err = e_[0], setErr = e_[1];
    var c_ = useState(null), confirm = c_[0], setConfirm = c_[1];
    var t_ = useState(""), typed = t_[0], setTyped = t_[1];
    var f_ = useState(false), force = f_[0], setForce = f_[1];
    var b_ = useState(false), busy = b_[0], setBusy = b_[1];
    var m_ = useState(""), msg = m_[0], setMsg = m_[1];
    var n_ = useState(0), tick = n_[0], setTick = n_[1];
    var w_ = useState(null), went = w_[0], setWent = w_[1];      // set when a power action ran out its countdown: waiting for the server to come back

    function load() {
      return fetchJSON(API + "/status").then(function (j) {
        setData(j);
        setErr("");
        if (expecting && j && !j.reachable) downSeen = true;
        if (expecting && downSeen && j && j.reachable) { expecting = false; downSeen = false; setWent(null); setMsg("The server is back."); }
        else if (expecting && !downSeen && j && j.reachable && Date.now() - expectingSince > 60000) { expecting = false; setWent(null); setMsg("The server never went down (is the helper in test mode?)."); }
      }).catch(function (e) {
        if (expecting) { downSeen = true; return; }
        setErr(errText(e));
      });
    }
    useEffect(function () { load(); }, []);
    useEffect(function () {      // a notice fades after a few seconds
      if (!msg) return undefined;
      var id = setTimeout(function () { setMsg(""); }, 6000);
      return function () { clearTimeout(id); };
    }, [msg]);
    var live = !!(data && ((data.update && data.update.running) || data.scheduled)) || !!went;
    useEffect(function () {
      var id = setInterval(function () { load(); setTick(function (n) { return n + 1; }); }, live ? 2500 : 20000);
      return function () { clearInterval(id); };
    }, [live]);
    useEffect(function () {      // a one second beat for the countdown text
      if (!(data && data.scheduled)) return undefined;
      var id = setInterval(function () { setTick(function (n) { return n + 1; }); }, 500);
      return function () { clearInterval(id); };
    }, [!!(data && data.scheduled)]);

    if (!data) return h("div", { className: "ats-card" }, h("div", { className: "ats-note" }, err || "Loading…"));
    if (!data.enabled) return h("div", { className: "ats-card" }, h("div", { className: "ats-note" }, "Server actions are not set up on this install."));
    if (!data.reachable && !went) return h("div", { className: "ats-card" }, h("div", { className: "ats-h" }, "Server"), h("div", { className: "ats-err" }, "The host helper is not answering, so server actions are unavailable."));

    var pk = data.packages || { safe: [], care: [] };
    var up = data.update || {};
    var sched = data.scheduled;
    var warnings = [];
    if (data.busy && data.busy.runs) warnings.push("The agent is in the middle of a chat run.");
    if (data.busy && data.busy.cards) warnings.push("A board card is running.");
    if (data.backup_running) warnings.push("A backup is running.");
    var locked = !!(up.running || sched);

    function open(k) { setConfirm(k); setTyped(""); setForce(false); setMsg(""); setErr(""); }
    function go() {
      var a = ACTIONS[confirm];
      if (a.typed && typed.trim().toLowerCase() !== a.typed) { setErr("Type “" + a.typed + "” first."); return; }
      if (warnings.length && !force) { setErr("Tick the box to go ahead while those are happening."); return; }
      setBusy(true);
      post(API + "/action", { action: confirm, force: force }).then(function () {
        setConfirm(null); setMsg(a.countdown ? "Scheduled." : "Update started."); return load();
      }).catch(function (e) { setErr(errText(e)); }).then(function () { setBusy(false); });
    }
    function cancel() {
      setBusy(true);
      post(API + "/cancel", {}).then(function () { setMsg("Cancelled. Nothing changed."); return load(); })
        .catch(function (e) { setErr(errText(e)); }).then(function () { setBusy(false); });
    }

    var left = sched ? Math.max(0, Math.round(sched.at - Date.now() / 1000)) : 0;
    if (sched && left <= 0 && !went) { expecting = true; downSeen = false; expectingSince = Date.now(); setTimeout(function () { setWent({ action: sched.action }); }, 0); }

    var last = up.last;
    var lastLine = last ? (last.ok ? "Last update " + ago(last.finished) + ": " + (last.packages || []).length + " packages" + (last.dry_run ? " (test run)" : "") + (last.containers_missing && last.containers_missing.length ? ". Missing containers: " + last.containers_missing.join(", ") : ". All " + last.containers_after + " containers running.") + (last.reboot_required ? " A restart is needed to finish." : "")
      : "Last update " + ago(last.finished) + " failed. See the log below.") : "";

    var confirmPanel = null;
    if (confirm && !locked) {
      var a = ACTIONS[confirm];
      confirmPanel = h("div", { className: "ats-confirm" + (a.danger ? " ats-confirm-danger" : "") },
        h("div", { className: "ats-h" }, a.title),
        h("div", { className: "ats-note" }, a.body),
        h("ul", { className: "ats-checks" },
          warnings.length ? warnings.map(function (w) { return h("li", { key: w, className: "ats-warn" }, "⚠ " + w); })
            : [h("li", { key: "a", className: "ats-okline" }, "✓ No chat run in progress"), h("li", { key: "b", className: "ats-okline" }, "✓ No board card running"), h("li", { key: "c", className: "ats-okline" }, "✓ No backup running")]),
        warnings.length ? h("label", { className: "ats-inline" }, h("input", { type: "checkbox", checked: force, onChange: function (ev) { setForce(ev.target.checked); } }), "Go ahead anyway") : null,
        a.typed ? h("div", { className: "ats-inline" }, h("span", { className: "ats-note" }, "Type “" + a.typed + "” to confirm"),
          h("input", { className: "ats-input", value: typed, onChange: function (ev) { setTyped(ev.target.value); setErr(""); }, "aria-label": "Confirmation" })) : null,
        a.countdown ? h("div", { className: "ats-note" }, "You will get " + (data.countdown || 30) + " seconds to cancel before it happens.") : null,
        h("div", { className: "ats-actions" },
          h("button", { type: "button", className: "ats-btn" + (a.danger ? " ats-btn-danger" : " ats-btn-primary"), disabled: busy, onClick: go }, a.button),
          h("button", { type: "button", className: "ats-btn", disabled: busy, onClick: function () { setConfirm(null); setErr(""); } }, "Cancel")));
    }

    var statusBanner = null;
    if (went) statusBanner = h("div", { className: "ats-confirm ats-confirm-danger" }, h("div", { className: "ats-h" }, went.action === "shutdown" ? "Shutting down" : "Rebooting"),
      h("div", { className: "ats-note" }, went.action === "shutdown" ? "The server is going off. This page will not be reachable until someone turns it back on." : "This page reconnects when the server is back, usually within three minutes."));
    else if (sched) statusBanner = h("div", { className: "ats-confirm ats-confirm-danger" }, h("div", { className: "ats-h" }, (sched.action === "shutdown" ? "Shutting down" : "Rebooting") + " in " + left + " s"),
      h("div", { className: "ats-note" }, "Requested by " + (sched.user || "an owner") + ". Cancel now to stop it."),
      h("div", { className: "ats-actions" }, h("button", { type: "button", className: "ats-btn ats-btn-primary", disabled: busy, onClick: cancel }, "Cancel")));
    else if (up.running) statusBanner = h("div", { className: "ats-confirm" }, h("div", { className: "ats-h" }, "Updating"),
      h("div", { className: "ats-note" }, ((up.progress && up.progress.step) || "working") + (up.progress && up.progress.total ? " (" + up.progress.total + " packages)" : "") + ". It runs on the server, so you can leave this page."));

    var total = pk.safe.length + pk.care.length;
    return h("div", { className: "ats-server" },
      statusBanner,
      msg ? h("div", { className: "ats-msg" }, msg) : null,
      err ? h("div", { className: "ats-err" }, err) : null,
      data.dry_run ? h("div", { className: "ats-note" }, "Test mode: updates are simulated and reboot and shutdown do nothing.") : null,
      h("div", { className: "ats-card" },
        h("div", { className: "ats-row" }, h("div", { className: "ats-h" }, "Updates"), h("span", { className: "ats-paused" }, total ? total + " waiting" : "up to date")),
        lastLine ? h("div", { className: "ats-note" }, lastLine) : null,
        h("div", { className: "ats-grid ats-grid-2" },
          h("div", { className: "ats-sub-card" },
            h("div", { className: "ats-row-t" }, "System and editor"),
            h("div", { className: "ats-note" }, "Small Ubuntu packages. Low risk, no restarts expected."),
            pk.safe.length ? h(PackageList, { items: pk.safe }) : h("div", { className: "ats-note" }, "Nothing waiting."),
            h("button", { type: "button", className: "ats-btn ats-btn-primary", disabled: locked || !pk.safe.length, onClick: function () { open("update_safe"); } }, "Update now")),
          h("div", { className: "ats-sub-card ats-sub-care" },
            h("div", { className: "ats-row-t" }, "Docker, GPU tools and the network tunnel"),
            h("div", { className: "ats-note" }, "Can briefly drop containers and remote access."),
            pk.care.length ? h(PackageList, { items: pk.care }) : h("div", { className: "ats-note" }, "Nothing waiting."),
            h("button", { type: "button", className: "ats-btn", disabled: locked || !pk.care.length, onClick: function () { open("update_care"); } }, "Update with care")))),
      h("div", { className: "ats-card" },
        h("div", { className: "ats-h" }, "Power"),
        h("div", { className: "ats-note" }, "Owners only. Every action is logged with who and when." + (data.uptime_seconds != null ? " Up for " + uptime(data.uptime_seconds) + "." : "") + (data.reboot_required ? " A restart is needed to finish earlier updates." : "")),
        h("div", { className: "ats-actions" },
          h("button", { type: "button", className: "ats-btn", disabled: locked, onClick: function () { open("reboot"); } }, "Reboot server"),
          h("button", { type: "button", className: "ats-btn ats-btn-danger", disabled: locked, onClick: function () { open("shutdown"); } }, "Shut down server"))),
      confirmPanel,
      (data.log && data.log.length) ? h("div", { className: "ats-card" },
        h("div", { className: "ats-h" }, "Recent server actions"),
        h("ul", { className: "ats-log" }, data.log.map(function (x, i) { return h("li", { key: i }, h("span", { className: "ats-note" }, ago(x.ts)), " ", h("b", null, x.user || "?"), " · ", x.action, " · ", x.result); }))) : null);
  }

  window.__APP_SUBPAGES__ = Object.assign(window.__APP_SUBPAGES__ || {}, { server: Server });
})();
