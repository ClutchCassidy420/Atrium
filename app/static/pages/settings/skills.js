/* Settings > Skills: see, read, add, change and remove the agent's skills. Shown inside Settings (window.__APP_SUBPAGES__.skills).
   Talks to /api/plugins/atrium-skills only (see app/skills_backend.py). A skill is a SKILL.md: a header with a name and a description,
   then the instructions the agent follows. She uses a change from her next session. Removing a skill moves it to skills/.trash. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, UI = window.__APP_UI__;
  if (!SDK || !UI || !UI.kit) return;
  var K = UI.kit, h = UI.h, useState = SDK.hooks.useState;
  var API = "/api/plugins/atrium-skills", JSONH = { "Content-Type": "application/json" };

  function errText(e) {
    var m = String((e && e.message) || e || "Something went wrong"), i = m.indexOf("{");
    if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* plain */ } }
    return m.replace(/^\d+:\s*/, "");
  }
  function fail(e) { throw new Error(errText(e)); }
  var TEMPLATE = "---\nname: my-skill\ndescription: One line that tells the agent when to use this skill\n---\n\n# My skill\n\nWhat to do, step by step.\n";
  function headerName(text) { var m = /^name:\s*["']?([^\s"']+)/m.exec(text.split(/\n---/)[0] || ""); return m ? m[1] : ""; }

  function Page() {
    var tick = useState(0), n = tick[0], setN = tick[1];
    var state = K.useAsync(function () { return SDK.fetchJSON(API + "/list"); }, [n]);
    var q_ = useState(""), query = q_[0], setQuery = q_[1];
    var ed_ = useState(null), edit = ed_[0], setEdit = ed_[1];          // null, { mode: "edit", skill, body, writable, files } or { mode: "new", category }
    var msg_ = useState(""), msg = msg_[0], setMsg = msg_[1];
    var opening = useState(""), openErr = opening[0], setOpenErr = opening[1];
    function reload() { setN(n + 1); }

    function open(s) {
      setOpenErr("");
      SDK.fetchJSON(API + "/skill?id=" + encodeURIComponent(s.id)).then(function (r) { setEdit({ mode: "edit", skill: s, body: r.body, writable: r.writable, files: r.files || [] }); },
        function (e) { setOpenErr(errText(e)); });
    }
    function save(text) {
      return SDK.fetchJSON(API + "/skill", { method: "POST", headers: JSONH, body: JSON.stringify({ id: edit.skill.id, body: text }) })
        .then(function () { setEdit(null); setMsg("Saved. The agent uses the change from her next session."); reload(); }, fail);
    }
    function remove() {
      return SDK.fetchJSON(API + "/skill?id=" + encodeURIComponent(edit.skill.id), { method: "DELETE" })
        .then(function () { setEdit(null); setMsg("Removed. It was moved to the skills .trash folder on the server."); reload(); }, fail);
    }
    function create(text) {
      var name = headerName(text), cat = edit.category.trim().toLowerCase();
      if (!name) throw new Error("The header needs a name: line, for example name: my-skill.");
      return SDK.fetchJSON(API + "/skill", { method: "POST", headers: JSONH, body: JSON.stringify({ id: (cat ? cat + "/" : "") + name, body: text, create: true }) })
        .then(function () { setEdit(null); setMsg("Added. The agent can use it from her next session."); reload(); }, fail);
    }

    var info = state.data;
    var needle = query.trim().toLowerCase();
    var skills = info ? info.skills.filter(function (s) { return !needle || (s.name + " " + s.description + " " + s.category).toLowerCase().indexOf(needle) >= 0; })
      .sort(function (a, b) { return (a.category + "/" + a.name).localeCompare(b.category + "/" + b.name); }) : [];

    return h("div", { className: "ats-card" },
      h("div", { className: "ats-inline kit-row" },
        h("input", { type: "search", className: "kit-input", placeholder: "Search skills…", "aria-label": "Search skills", value: query, onChange: function (e) { setQuery(e.target.value); } }),
        h(K.Button, { variant: "primary", disabled: !info || !info.writable, onClick: function () { setEdit({ mode: "new", category: "" }); } }, "＋ New skill")),
      msg ? h(K.Alert, { tone: "success", className: "kit-gap", onDismiss: function () { setMsg(""); } }, msg) : null,
      openErr ? h(K.Alert, { tone: "danger", className: "kit-gap", onDismiss: function () { setOpenErr(""); } }, openErr) : null,
      info && info.exists && !info.writable ? h(K.Alert, { tone: "info", className: "kit-gap" }, "Skills are read-only on this install: you can read them but not change them. Whoever runs the server can mount the skills folder writable.") : null,
      h(K.AsyncView, { state: state, skeleton: h(K.Skeleton, { lines: 4, height: "2.4em" }), errorTitle: "Could not load the skills",
        isEmpty: function (d) { return !d.exists || !d.skills.length; },
        empty: h(K.EmptyState, { icon: "🧩", title: info && !info.exists ? "No skills folder on this install" : "No skills yet",
          description: info && !info.exists ? "The agent's skills folder is not available to this app." : "A skill teaches the agent how to do something. Add the first one." }) },
        function () {
          if (!skills.length) return h(K.EmptyState, { icon: "🔍", title: "No skill matches that search" });
          return h("ul", { className: "ats-skills", "aria-label": "Skills" }, skills.map(function (s) {
            return h("li", { key: s.id, className: "ats-skill" },
              h("div", { className: "ats-skill-text" },
                h("div", { className: "ats-skill-name" }, s.name, s.category ? h("span", { className: "ats-chip" }, s.category) : null),
                h("div", { className: "ats-note" }, s.description || "No description.")),
              h(K.Button, { size: "sm", onClick: function () { open(s); }, attrs: { "aria-label": (s.writable ? "Edit " : "View ") + s.name } }, s.writable ? "Edit" : "View"));
          }));
        }),
      edit && edit.mode === "edit" ? h(K.EditorDialog, { open: true, title: (edit.writable ? "Edit skill: " : "Skill: ") + edit.skill.name, value: edit.body, readOnly: !edit.writable,
        readOnlyNote: "This skill is read-only on this install.", onSave: save, onDelete: remove, deleteLabel: "Delete skill",
        deleteNote: "Delete this skill? It moves to a .trash folder on the server and the agent stops using it." + (edit.files.length ? " Its other files (" + edit.files.join(", ") + ") move with it." : ""),
        onClose: function () { setEdit(null); } }) : null,
      edit && edit.mode === "new" ? h(K.EditorDialog, { open: true, title: "New skill", value: TEMPLATE, saveLabel: "Add skill", onSave: create, onClose: function () { setEdit(null); },
        fields: h(K.Field, { label: "Category (optional)", hint: "A folder to keep it in, for example research. Lowercase letters, numbers and dashes." }, function (ip) {
          return h("input", Object.assign({}, ip, { type: "text", maxLength: 64, value: edit.category, onChange: function (x) { setEdit(Object.assign({}, edit, { category: x.target.value })); } })); }) }) : null);
  }

  window.__APP_SUBPAGES__ = Object.assign(window.__APP_SUBPAGES__ || {}, { skills: Page });     // shown inside Settings
})();
