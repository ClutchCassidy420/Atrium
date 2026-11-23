/* Calendar: a month view of your Nextcloud calendars, with a list for the chosen day. You can add, change and remove events here; they are saved in
   Nextcloud (so your phone and Nextcloud see them too). Talks to the app only through /api/nextcloud (see app/nextcloud_backend.py). */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__, UI = window.__APP_UI__;
  if (!SDK || !REG || !UI) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect, useCallback = SDK.hooks.useCallback;
  var API = "/api/nextcloud", JSONH = { "Content-Type": "application/json" };
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var DEFAULT_COLORS = ["#8b7bff", "#3dd6f5", "#ff6fb5", "#38e1a5", "#ffc857", "#ff8a5c"];

  function errText(e) { var m = String((e && e.message) || e || "Something went wrong"), i = m.indexOf("{"); if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* plain */ } } return m.replace(/^\d+:\s*/, ""); }
  function p2(n) { return (n < 10 ? "0" : "") + n; }
  function ymd(d) { return d.getFullYear() + "-" + p2(d.getMonth() + 1) + "-" + p2(d.getDate()); }
  function localDay(s) { var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s); return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(s); }
  function parse(s, allDay) { return allDay ? localDay(s) : new Date(s); }
  function addDays(d, n) { var x = new Date(d.getFullYear(), d.getMonth(), d.getDate() + n); return x; }
  function timeText(d) { var hr = d.getHours(), ap = hr >= 12 ? "pm" : "am"; hr = hr % 12 || 12; return hr + (d.getMinutes() ? ":" + p2(d.getMinutes()) : "") + ap; }
  function dtLocal(d) { return ymd(d) + "T" + p2(d.getHours()) + ":" + p2(d.getMinutes()); }

  /* each event as local Date objects; `days` are the calendar days it touches */
  function shape(ev, i) {
    var s = parse(ev.start, ev.all_day), e = parse(ev.end, ev.all_day), days = [];
    var last = ev.all_day ? addDays(e, -1) : (e > s ? new Date(e.getTime() - 1) : s);
    if (last < s) last = s;
    for (var d = new Date(s.getFullYear(), s.getMonth(), s.getDate()), n = 0; d <= last && n < 40; d = addDays(d, 1), n++) days.push(ymd(d));
    return Object.assign({}, ev, { s: s, e: e, days: days, key: ev.calendar + "/" + ev.name + "/" + ev.start + i });
  }

  function Modal(p) {
    var ev = p.event, isNew = !ev.name;
    var a = useState(function () {
      var s = ev.s, e = ev.e;
      if (ev.all_day) return { title: ev.title || "", allDay: true, sd: ymd(s), ed: ymd(addDays(e, -1)), st: "09:00", et: "10:00", loc: ev.location || "", desc: ev.description || "", cal: ev.calendar || "" };
      return { title: ev.title || "", allDay: false, sd: ymd(s), ed: ymd(e), st: p2(s.getHours()) + ":" + p2(s.getMinutes()), et: p2(e.getHours()) + ":" + p2(e.getMinutes()), loc: ev.location || "", desc: ev.description || "", cal: ev.calendar || "" };
    }), f = a[0], setF = a[1];
    var b = useState(""), msg = b[0], setMsg = b[1], c = useState(false), busy = c[0], setBusy = c[1];
    var writable = p.calendars.filter(function (x) { return x.writable; });
    var readonly = !isNew && (ev.recurring || ev.writable === false);
    function set(k, v) { var o = {}; o[k] = v; setF(Object.assign({}, f, o)); }
    function body() {
      var cal = f.cal || (writable[0] && writable[0].id) || "";
      if (f.allDay) return { calendar: cal, title: f.title, all_day: true, start: f.sd, end: ymd(addDays(localDay(f.ed || f.sd), 1)), location: f.loc, description: f.desc };
      return { calendar: cal, title: f.title, all_day: false, start: new Date(f.sd + "T" + f.st).toISOString(), end: new Date((f.ed || f.sd) + "T" + f.et).toISOString(), location: f.loc, description: f.desc };
    }
    function save(evn) {
      evn.preventDefault();
      if (!f.title.trim()) { setMsg("Give it a title."); return; }
      if (isNaN(new Date(f.sd + "T" + (f.allDay ? "00:00" : f.st)))) { setMsg("Pick a start date."); return; }
      setBusy(true); setMsg("");
      var req = isNew ? SDK.fetchJSON(API + "/events", { method: "POST", headers: JSONH, body: JSON.stringify(body()) })
        : SDK.fetchJSON(API + "/events/" + encodeURIComponent(ev.calendar) + "/" + encodeURIComponent(ev.name), { method: "PUT", headers: JSONH, body: JSON.stringify(body()) });
      req.then(function () { p.onSaved(); }, function (e) { setBusy(false); setMsg(errText(e)); });
    }
    function del() {
      setBusy(true); setMsg("");
      SDK.fetchJSON(API + "/events/" + encodeURIComponent(ev.calendar) + "/" + encodeURIComponent(ev.name), { method: "DELETE" }).then(function () { p.onSaved(); }, function (e) { setBusy(false); setMsg(errText(e)); });
    }
    return h("div", { className: "cal-scrim", onMouseDown: function (x) { if (x.target === x.currentTarget) p.onClose(); } },
      h("form", { className: "cal-modal", onSubmit: save, role: "dialog", "aria-label": isNew ? "New event" : "Event" },
        h("h2", null, isNew ? "New event" : readonly ? "Event" : "Edit event"),
        readonly ? h("p", { className: "hm-sub" }, ev.recurring ? "This event repeats. Change the whole series in Nextcloud." : "This calendar is read-only.") : null,
        h("input", { className: "hm-input", placeholder: "Title", value: f.title, autoFocus: true, disabled: readonly, maxLength: 300, onChange: function (x) { set("title", x.target.value); } }),
        h("label", { className: "cal-check" }, h("input", { type: "checkbox", checked: f.allDay, disabled: readonly, onChange: function (x) { set("allDay", x.target.checked); } }), "All day"),
        h("div", { className: "cal-row" },
          h("label", null, "From", h("input", { type: "date", className: "hm-input", value: f.sd, disabled: readonly, onChange: function (x) { var o = { sd: x.target.value }; if (!f.ed || f.ed < x.target.value) o.ed = x.target.value; setF(Object.assign({}, f, o)); } })),
          f.allDay ? null : h("label", null, "Time", h("input", { type: "time", className: "hm-input", value: f.st, disabled: readonly, onChange: function (x) { set("st", x.target.value); } }))),
        h("div", { className: "cal-row" },
          h("label", null, "To", h("input", { type: "date", className: "hm-input", value: f.ed, min: f.sd, disabled: readonly, onChange: function (x) { set("ed", x.target.value); } })),
          f.allDay ? null : h("label", null, "Time", h("input", { type: "time", className: "hm-input", value: f.et, disabled: readonly, onChange: function (x) { set("et", x.target.value); } }))),
        isNew && writable.length > 1 ? h("label", null, "Calendar", h("select", { className: "hm-select cal-wide", value: f.cal || writable[0].id, onChange: function (x) { set("cal", x.target.value); } }, writable.map(function (cl) { return h("option", { key: cl.id, value: cl.id }, cl.name); }))) : null,
        h("input", { className: "hm-input", placeholder: "Location", value: f.loc, disabled: readonly, maxLength: 300, onChange: function (x) { set("loc", x.target.value); } }),
        h("textarea", { className: "hm-textarea", placeholder: "Notes", rows: 3, value: f.desc, disabled: readonly, maxLength: 8000, onChange: function (x) { set("desc", x.target.value); } }),
        msg ? h(UI.kit.Alert, { tone: "danger" }, msg) : null,
        h("div", { className: "cal-actions" },
          !isNew && !readonly ? h("button", { type: "button", className: "hm-btn hm-btn-ghost cal-del", disabled: busy, onClick: del }, "Delete") : null,
          h("span", { style: { flex: 1 } }),
          h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: p.onClose }, readonly ? "Close" : "Cancel"),
          readonly ? null : h("button", { type: "submit", className: "hm-btn", disabled: busy }, busy ? "Saving…" : "Save"))));
  }

  function Page() {
    var t = new Date();
    var a = useState({ y: t.getFullYear(), m: t.getMonth() }), cur = a[0], setCur = a[1];
    var b = useState(ymd(t)), sel = b[0], setSel = b[1];
    var c = useState([]), events = c[0], setEvents = c[1];
    var d = useState(null), meta = d[0], setMeta = d[1];
    var e = useState(""), err = e[0], setErr = e[1];
    var f = useState(null), modal = f[0], setModal = f[1];
    var g = useState({}), hidden = g[0], setHidden = g[1];
    var tick = useState(0), n = tick[0], setN = tick[1];
    var first = new Date(cur.y, cur.m, 1), gridStart = addDays(first, -first.getDay()), gridEnd = addDays(gridStart, 42);

    useEffect(function () {
      var live = true;
      SDK.fetchJSON(API + "/events?start=" + encodeURIComponent(gridStart.toISOString()) + "&end=" + encodeURIComponent(gridEnd.toISOString())).then(function (r) {
        if (live) { setEvents((r.events || []).map(shape)); setErr(""); }
      }, function (x) { if (live) setErr(errText(x)); });
      return function () { live = false; };
    }, [cur.y, cur.m, n]);
    useEffect(function () { if (meta) return; SDK.fetchJSON(API + "/status").then(setMeta, function (x) { setErr(errText(x)); }); }, [n]);

    var cals = (meta && meta.calendars) || [];
    cals.forEach(function (cl, i) { if (!cl.color) cl.color = DEFAULT_COLORS[i % DEFAULT_COLORS.length]; });
    var colorOf = {}; cals.forEach(function (cl) { colorOf[cl.id] = cl.color; });
    var shown = events.filter(function (ev) { return !hidden[ev.calendar]; });
    var byDay = {};
    shown.forEach(function (ev) { ev.days.forEach(function (k) { (byDay[k] = byDay[k] || []).push(ev); }); });
    function go(delta) { var d0 = new Date(cur.y, cur.m + delta, 1); setCur({ y: d0.getFullYear(), m: d0.getMonth() }); }
    function today() { var n0 = new Date(); setCur({ y: n0.getFullYear(), m: n0.getMonth() }); setSel(ymd(n0)); }
    function openNew(day) {
      var d0 = localDay(day || sel), now = new Date(), s = new Date(d0.getFullYear(), d0.getMonth(), d0.getDate(), ymd(d0) === ymd(now) ? Math.min(23, now.getHours() + 1) : 9, 0);
      setModal({ title: "", s: s, e: new Date(s.getTime() + 3600000), all_day: false });
    }
    function saved() { setModal(null); setN(n + 1); }

    if (err && !meta && !events.length) return h("div", { className: "cal" }, h("h1", null, "Calendar"), h(UI.kit.Alert, { tone: "danger", title: "Could not load your calendar", action: h(UI.kit.Button, { size: "sm", onClick: function () { setErr(""); setN(n + 1); } }, "Try again") }, err), h("p", { className: "hm-sub" }, "If Nextcloud is not connected to this app yet, add its address and an app password to the app's private config."));
    var cells = [];
    for (var i = 0; i < 42; i++) {
      (function (day) {
        var k = ymd(day), list = byDay[k] || [], other = day.getMonth() !== cur.m, isToday = k === ymd(new Date());
        cells.push(h("div", { key: k, className: "cal-cell" + (other ? " cal-other" : "") + (isToday ? " cal-today" : "") + (k === sel ? " cal-sel" : ""), role: "gridcell", "aria-label": k + ", " + list.length + " events",
          onClick: function () { setSel(k); }, onDoubleClick: function () { openNew(k); } },
          h("span", { className: "cal-num" }, day.getDate()),
          list.slice(0, 3).map(function (ev) {
            return h("button", { key: ev.key, type: "button", className: "cal-chip", style: { "--cc": ev.color || colorOf[ev.calendar] || "#8b7bff" }, title: ev.title,
              onClick: function (x) { x.stopPropagation(); setSel(k); setModal(ev); } }, ev.all_day ? "" : timeText(ev.s) + " ", ev.title);
          }),
          list.length > 3 ? h("span", { className: "cal-more" }, "+" + (list.length - 3) + " more") : null));
      })(addDays(gridStart, i));
    }
    var dayList = (byDay[sel] || []).slice().sort(function (x, y) { return (x.all_day ? 0 : 1) - (y.all_day ? 0 : 1) || x.s - y.s; });
    var selDate = localDay(sel);
    return h("div", { className: "cal" },
      h("header", { className: "cal-top" },
        h("div", { className: "cal-nav" },
          h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: function () { go(-1); }, "aria-label": "Previous month" }, "‹"),
          h("h1", null, MONTHS[cur.m] + " " + cur.y),
          h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: function () { go(1); }, "aria-label": "Next month" }, "›"),
          h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: today }, "Today")),
        h("button", { type: "button", className: "hm-btn", onClick: function () { openNew(); } }, "+ New event")),
      cals.length > 1 ? h("div", { className: "cal-legend" }, cals.map(function (cl) {
        return h("button", { key: cl.id, type: "button", className: "cal-key" + (hidden[cl.id] ? " cal-off" : ""), style: { "--cc": cl.color }, "aria-pressed": String(!hidden[cl.id]),
          onClick: function () { var o = Object.assign({}, hidden); o[cl.id] = !o[cl.id]; setHidden(o); } }, h("i", null), cl.name);
      })) : null,
      err ? h(UI.kit.Alert, { tone: "danger", onDismiss: function () { setErr(""); } }, err) : null,
      h("div", { className: "cal-layout" },
        h("div", { className: "hm-card cal-grid-card" },
          h("div", { className: "cal-head" }, DAYS.map(function (x) { return h("span", { key: x }, x); })),
          h("div", { className: "cal-grid", role: "grid" }, cells)),
        h("aside", { className: "hm-card cal-day" },
          h("div", { className: "hm-card-head" }, h("h2", null, selDate.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })), h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: function () { openNew(sel); } }, "+ Add")),
          dayList.length ? h("ul", { className: "hm-list" }, dayList.map(function (ev) {
            return h("li", { key: ev.key, className: "cal-item", style: { "--cc": ev.color || colorOf[ev.calendar] || "#8b7bff" }, onClick: function () { setModal(ev); }, tabIndex: 0,
              onKeyDown: function (x) { if (x.key === "Enter") setModal(ev); } },
              h("i", { className: "cal-bar" }), h("div", null, h("div", { className: "hm-item-t" }, ev.title),
                h("div", { className: "hm-sub" }, ev.all_day ? "All day" : timeText(ev.s) + " – " + timeText(ev.e)), ev.location ? h("div", { className: "hm-sub" }, "📍 " + ev.location) : null));
          })) : h(UI.kit.EmptyState, { icon: "📅", title: "Nothing on this day", description: "Double-click a day, or press + Add." }))),
      modal ? h(Modal, { event: modal, calendars: cals, onClose: function () { setModal(null); }, onSaved: saved }) : null);
  }

  REG.register("calendar-page", Page);
})();
