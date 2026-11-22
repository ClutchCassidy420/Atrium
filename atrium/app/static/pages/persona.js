/* Persona: who the agent is and the rules she works by (her soul file), with every earlier version kept, and which engine answers chats.
   A framework page, owner only. The text is read and saved through the agent's own dashboard; nothing here edits a file directly. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__, UI = window.__APP_UI__;
  if (!SDK || !REG || !UI) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect, Card = UI.Card, Chip = UI.Chip, Empty = UI.Empty;
  var BASE = "/api/plugins/atrium-mission";
  var JSONH = { "Content-Type": "application/json" };

  function post(path, body) { return SDK.fetchJSON(BASE + path, { method: "POST", headers: JSONH, body: JSON.stringify(body || {}) }); }
  function why(e) {
    var m = String((e && e.message) || e).replace(/^\d+:\s*/, "");
    try { var j = JSON.parse(m); if (j && j.detail) return typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (x) { /* plain text */ }
    return m;
  }
  function when(id) {   // "20261113-034200" -> "Nov 13, 3:42 AM"
    var m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/.exec(id || "");
    if (!m) return id || "";
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }

  function Soul(props) {
    var name = props.name;
    var a = useState(null), soul = a[0], setSoul = a[1];
    var b = useState(""), text = b[0], setText = b[1];
    var c = useState(""), msg = c[0], setMsg = c[1];
    var d = useState(null), preview = d[0], setPreview = d[1];
    var e = useState(false), busy = e[0], setBusy = e[1];
    function load() {
      return SDK.fetchJSON(BASE + "/soul").then(function (s) { setSoul(s); setText(s.text || ""); }, function (x) { setMsg("Could not load: " + why(x)); });
    }
    useEffect(function () { load(); }, []);
    if (!soul) return h(Card, { title: "Persona" }, h("p", { className: "hm-sub" }, msg || "Loading…"));
    var dirty = text !== (soul.text || ""), max = soul.max || 12000;
    function save() {
      setBusy(true); setMsg("");
      post("/soul", { text: text }).then(function () { return load(); }).then(function () { setMsg("Saved. " + name + " reads this at the start of her next chat or job."); }, function (x) { setMsg(why(x)); }).then(function () { setBusy(false); });
    }
    function show(item) {
      SDK.fetchJSON(BASE + "/soul/version?id=" + encodeURIComponent(item.id)).then(function (r) { setPreview({ id: item.id, text: r.text }); }, function (x) { setMsg(why(x)); });
    }
    function restore(item) {
      if (!window.confirm("Restore the version from " + when(item.when) + "? The current text is kept in the history first.")) return;
      setBusy(true); setMsg("");
      post("/soul/restore", { id: item.id }).then(function () { setPreview(null); return load(); }).then(function () { setMsg("Restored."); }, function (x) { setMsg(why(x)); }).then(function () { setBusy(false); });
    }
    return h(Card, { title: name + "'s persona", aside: text.length + " / " + max },
      h("p", { className: "hm-sub" }, "Who " + name + " is and the rules she works by. It changes how she behaves in every chat and job, so edit with care. Every save keeps the earlier version."),
      h("textarea", { className: "hm-textarea", rows: 16, value: text, maxLength: max, spellCheck: false, "aria-label": "Persona text", onChange: function (ev) { setText(ev.target.value); } }),
      h("div", { className: "hm-ask-row", style: { marginTop: "10px", alignItems: "center" } },
        h("button", { type: "button", className: "hm-btn", disabled: busy || !dirty || text.trim().length < 20, onClick: save }, busy ? "Saving…" : "Save"),
        dirty ? h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: function () { setText(soul.text || ""); } }, "Undo changes") : null,
        msg ? h("span", { className: /^(Saved|Restored)/.test(msg) ? "hm-sub" : "hm-err", role: "status" }, msg) : null),
      h("h3", { style: { marginTop: "20px" } }, "Earlier versions"),
      soul.history.length ? h("ul", { className: "hm-list" }, soul.history.slice(0, 12).map(function (it) {
        return h("li", { key: it.id, className: "hm-item" }, h("div", { style: { flex: 1 } }, h("div", { className: "hm-item-t" }, when(it.when)), h("div", { className: "hm-sub" }, it.size + " characters")),
          h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: function () { show(it); } }, "Preview"),
          h("button", { type: "button", className: "hm-btn hm-btn-ghost", disabled: busy, onClick: function () { restore(it); } }, "Restore"));
      })) : h(Empty, null, "No earlier versions yet."),
      preview ? h("div", { className: "hm-answer", "aria-label": "Preview of " + when(preview.id.slice(5, 20)) }, preview.text) : null);
  }

  function Engine(props) {
    var name = props.name;
    var a = useState(null), eng = a[0], setEng = a[1];
    var b = useState(""), msg = b[0], setMsg = b[1];
    var c = useState(""), model = c[0], setModel = c[1];
    var d = useState("60"), minutes = d[0], setMinutes = d[1];
    var e = useState(false), ok = e[0], setOk = e[1];
    function load() { return SDK.fetchJSON(BASE + "/engine").then(function (s) { setEng(s); if (!model && s.claude_models && s.claude_models[0]) setModel(s.claude_models[0].id); }, function (x) { setMsg(why(x)); }); }
    useEffect(function () { load(); }, []);
    if (!eng) return h(Card, { title: "Which brain answers" }, h("p", { className: "hm-sub" }, msg || "Loading…"));
    var cloud = eng.engine === "claude";
    function go(body) { setMsg(""); post("/engine", body).then(function () { setOk(false); return load(); }, function (x) { setMsg(why(x)); }); }
    return h(Card, { title: "Which brain answers", aside: h(Chip, { tone: cloud ? "warn" : "ok", dot: true }, cloud ? "Cloud" : "Local") },
      h("p", { className: "hm-sub" }, cloud ? name + " is answering chats from the app with " + (eng.model || "a cloud model") + (eng.seconds_left ? " for another " + Math.ceil(eng.seconds_left / 60) + " minutes." : " until you switch back.")
        : "Chats from the app are answered by your own local model. Scheduled jobs always use the local model."),
      cloud ? h("button", { type: "button", className: "hm-btn", onClick: function () { go({ engine: "local" }); } }, "Switch back to local")
        : eng.claude_available ? h("div", null,
          h("p", { className: "hm-sub" }, "What you send while the cloud is on goes to Anthropic and is billed to your account."),
          h("div", { className: "hm-ask-row", style: { margin: "8px 0", flexWrap: "wrap" } },
            h("select", { className: "hm-select", value: model, "aria-label": "Cloud model", onChange: function (ev) { setModel(ev.target.value); } }, eng.claude_models.map(function (m) { return h("option", { key: m.id, value: m.id }, m.label); })),
            h("select", { className: "hm-select", value: minutes, "aria-label": "For how long", onChange: function (ev) { setMinutes(ev.target.value); } },
              [["15", "15 minutes"], ["60", "1 hour"], ["240", "4 hours"], ["0", "until I switch back"]].map(function (o) { return h("option", { key: o[0], value: o[0] }, o[1]); }))),
          h("label", { className: "hm-check-row" }, h("input", { type: "checkbox", checked: ok, onChange: function (ev) { setOk(ev.target.checked); } }), " I understand and want to use the cloud."),
          h("button", { type: "button", className: "hm-btn", disabled: !ok, onClick: function () { go({ engine: "claude", model: model, minutes: +minutes, confirm: true }); } }, "Switch to cloud"))
          : h(Empty, null, "No cloud key is set up on this server, so chats stay on your local model."),
      msg ? h("p", { className: "hm-err", role: "alert" }, msg) : null);
  }

  function Page() {
    var cfg = UI.config(), name = cfg.agentName || "Assistant";
    return h("div", { className: "hm" },
      h("header", { className: "hm-top" }, h("div", null, h("h1", null, "Persona"), h("p", { className: "hm-sub" }, "Who " + name + " is, and which brain answers their chats."))),
      h(Soul, { name: name }), h(Engine, { name: name }));
  }

  window.__APP_SUBPAGES__ = Object.assign(window.__APP_SUBPAGES__ || {}, { persona: Page });     // shown inside Settings
})();
