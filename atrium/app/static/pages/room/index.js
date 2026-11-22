/**
 * Atrium Loom: Room (dashboard tab plugin)
 *
 * A talk-only group chat for you, the agent (the local model) and Claude (in the cloud). Mention @{{agent}} or @claude; with no
 * mention both answer. They can answer each other. Nobody here uses tools: hand anything that needs doing to the agent's
 * normal chat with "Give this to the agent". Plain IIFE, no build step.
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
  var useRef = SDK.hooks.useRef;
  var fetchJSON = SDK.fetchJSON;
  var API = "/api/plugins/atrium-room";
  var KEY = "atrium:room";

  function post(path, body) {
    return fetchJSON(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  }

  function errText(e) {
    var m = String((e && e.message) || e || "");
    var i = m.indexOf("{");
    if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* fall through */ } }
    return m.replace(/^\d+:\s*/, "").slice(0, 160) || "Something went wrong.";
  }

  function clock(ts) { return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }); }
  function fmtK(n) { return n >= 1000 ? (n / 1000).toFixed(1) + "K" : String(n || 0); }

  var WHO = { owner: "You", agent: "{{agent}}", claude: "Claude", system: "" };

  /* ---------- message text: line breaks, **bold**, `code`, @mentions ---------- */

  function inline(text, key) {
    var out = [], re = new RegExp("(`[^`]+`)|(\\*\\*[^*]+\\*\\*)|(@(?:agent|" + AGENT.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "|claude|owner|all|everyone)\\b)", "gi"), last = 0, m, k = 0;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      var tok = m[0];
      if (m[1]) out.push(h("code", { key: key + "c" + k, className: "atr-code" }, tok.slice(1, -1)));
      else if (m[2]) out.push(h("strong", { key: key + "b" + k }, tok.slice(2, -2)));
      else out.push(h("span", { key: key + "m" + k, className: "atr-mention" }, tok));
      last = m.index + tok.length; k++;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }

  function Body(props) {
    var lines = String(props.text || "").split("\n");
    return h("div", { className: "atr-text" }, lines.map(function (ln, i) {
      return ln.trim() ? h("p", { key: i }, inline(ln, "l" + i)) : null;
    }));
  }

  /* ---------- one message ---------- */

  function Message(props) {
    var m = props.msg;
    if (m.who === "system") return h("div", { className: "atr-sys" }, m.text);
    var label = m.who === "claude" && m.model ? "Claude" : WHO[m.who];
    return h("div", { className: "atr-msg atr-msg-" + m.who },
      h("div", { className: "atr-avatar atr-av-" + m.who }, m.who === "owner" ? "A" : m.who === "agent" ? (AGENT.charAt(0).toUpperCase() || "A") : "C"),
      h("div", { className: "atr-bubble" },
        h("div", { className: "atr-who" }, label, m.who === "claude" ? h("span", { className: "atr-cloud" }, "☁ cloud") : m.who === "agent" ? h("span", { className: "atr-local" }, "local") : null, h("span", { className: "atr-time" }, clock(m.ts))),
        h(Body, { text: m.text }),
        m.who !== "owner" ? h("button", { type: "button", className: "atr-link", onClick: function () { props.onGive(m); } }, "Give this to {{agent}} →") : null));
  }

  /* ---------- page ---------- */

  function Page() {
    var l_ = useState(null), list = l_[0], setList = l_[1];
    var c_ = useState(function () { try { return window.localStorage.getItem(KEY) || ""; } catch (x) { return ""; } }), cur = c_[0], setCur = c_[1];
    var r_ = useState(null), room = r_[0], setRoom = r_[1];
    var mo_ = useState(null), models = mo_[0], setModels = mo_[1];
    var t_ = useState(""), text = t_[0], setText = t_[1];
    var e_ = useState(""), err = e_[0], setErr = e_[1];
    var n_ = useState(""), info = n_[0], setInfo = n_[1];
    var tt_ = useState(""), title = tt_[0], setTitle = tt_[1];
    var sd_ = useState(false), side = sd_[0], setSide = sd_[1];
    var endRef = useRef(null);
    var taRef = useRef(null);

    function loadList() { return fetchJSON(API + "/rooms").then(function (r) { setList(r.rooms || []); return r.rooms || []; }).catch(function (e) { setErr(errText(e)); return []; }); }
    function loadRoom(id) { return fetchJSON(API + "/rooms/" + id).then(function (r) { setRoom(r); setTitle(r.title); return r; }).catch(function () { setRoom(null); }); }

    useEffect(function () {
      fetchJSON(API + "/models").then(setModels).catch(function () { /* optional */ });
      loadList().then(function (rooms) {
        if (cur && rooms.some(function (x) { return x.id === cur; })) return;
        if (rooms.length) setCur(rooms[0].id);
      });
    }, []);

    useEffect(function () {
      if (!cur) { setRoom(null); return undefined; }
      try { window.localStorage.setItem(KEY, cur); } catch (x) { /* ignore */ }
      loadRoom(cur);
      return undefined;
    }, [cur]);

    var running = !!(room && room.running);
    useEffect(function () {
      if (!cur) return undefined;
      var iv = setInterval(function () { loadRoom(cur); }, running ? 1500 : 8000);
      return function () { clearInterval(iv); };
    }, [cur, running]);

    useEffect(function () { if (endRef.current) endRef.current.scrollIntoView({ block: "end" }); }, [room && room.messages.length, running]);

    function newRoom() {
      post("/rooms", {}).then(function (r) { setCur(r.id); loadList(); setSide(false); }).catch(function (e) { setErr(errText(e)); });
    }
    function send() {
      var v = text.trim();
      if (!v || running || !cur) return;
      setText(""); setErr("");
      post("/rooms/" + cur + "/send", { text: v }).then(function () { return loadRoom(cur); }).then(loadList).catch(function (e) { setErr(errText(e)); setText(v); });
    }
    function stop() { post("/rooms/" + cur + "/stop").then(function () { return loadRoom(cur); }).catch(function () { /* ignore */ }); }
    function setting(patch) { return post("/rooms/" + cur + "/settings", patch).then(function (r) { setRoom(r); setTitle(r.title); loadList(); }).catch(function (e) { setErr(errText(e)); }); }
    function insert(tag) {
      setText(function (t) { return (t && !/\s$/.test(t) ? t + " " : t) + tag + " "; });
      if (taRef.current) taRef.current.focus();
    }
    function give(m) {
      var who = m.who === "claude" ? "Claude" : "{{agent}}";
      var draft = "From my room \"" + (room ? room.title : "") + "\" (" + who + " said): " + m.text + "\n\nPlease help me with this. Tell me what you would do before you do anything.";
      window.location.assign("/chat?draft=" + encodeURIComponent(draft.slice(0, 5500)));
    }
    function save() {
      setInfo("");
      post("/rooms/" + cur + "/save").then(function (r) { setInfo("Saved to your notes as " + r.path); }).catch(function (e) { setErr(errText(e)); });
    }
    function del() {
      if (!window.confirm("Delete this room and its messages? This cannot be undone.")) return;
      post("/rooms/" + cur + "/delete", { confirm: true }).then(function () { setCur(""); setRoom(null); return loadList(); }).then(function (rooms) { if (rooms && rooms.length) setCur(rooms[0].id); }).catch(function (e) { setErr(errText(e)); });
    }

    var run = room && room.running;
    var typing = run ? (run.who ? WHO[run.who] + " is thinking…" : "Starting…") : "";

    return h("div", { className: "atr-page" },
      h("aside", { className: "atr-side" + (side ? " atr-side-open" : "") },
        h("button", { type: "button", className: "atr-btn atr-btn-primary atr-new", onClick: newRoom }, "+ New room"),
        h("div", { className: "atr-list" }, !list ? h("div", { className: "atr-muted" }, "Loading…") : !list.length ? h("div", { className: "atr-muted" }, "No rooms yet. Start one.") : list.map(function (r) {
          return h("button", { key: r.id, type: "button", className: "atr-room" + (r.id === cur ? " atr-room-on" : ""), onClick: function () { setCur(r.id); setSide(false); } },
            h("span", { className: "atr-room-t" }, r.title), h("span", { className: "atr-room-s" }, r.count + " messages" + (r.running ? " · answering" : "")));
        }))),
      h("section", { className: "atr-main" },
        h("div", { className: "atr-head" },
          h("button", { type: "button", className: "atr-menu", "aria-label": "Rooms", onClick: function () { setSide(!side); } }, "☰"),
          room ? h("input", { className: "atr-title", value: title, maxLength: 80, onChange: function (e) { setTitle(e.target.value); }, onBlur: function () { if (title.trim() && title !== room.title) setting({ title: title }); } }) : h("div", { className: "atr-title atr-muted" }, "Room")),
        room ? h("div", { className: "atr-members" },
          h("span", { className: "atr-member atr-member-you" }, "You"),
          h("button", { type: "button", className: "atr-member atr-member-agent" + (room.members.agent ? "" : " atr-off"), onClick: function () { setting({ agent: !room.members.agent }); }, title: "{{agent}} answers with her local model" }, "{{agent}} · local"),
          h("button", { type: "button", className: "atr-member atr-member-claude" + (room.members.claude ? "" : " atr-off"), onClick: function () { setting({ claude: !room.members.claude }); }, title: "Claude answers from the cloud and sees only this room" }, "Claude · cloud"),
          models && models.claude_available ? h("select", { className: "atr-select", value: room.claude_model, onChange: function (e) { setting({ claude_model: e.target.value }); } }, models.claude_models.map(function (x) { return h("option", { key: x.id, value: x.id }, x.label); })) : null,
          h("span", { className: "atr-usage", title: "Claude tokens used in this room" }, "Claude used " + fmtK(room.usage.in) + " in / " + fmtK(room.usage.out) + " out")) : null,
        h("div", { className: "atr-feed" },
          !room ? h("div", { className: "atr-muted atr-pad" }, cur ? "Opening…" : "Create a room to begin.") : room.messages.map(function (m) { return h(Message, { key: m.id, msg: m, onGive: give }); }),
          typing ? h("div", { className: "atr-typing" }, h("span", { className: "atr-dots" }, h("i"), h("i"), h("i")), typing, h("button", { type: "button", className: "atr-link", onClick: stop }, "Stop")) : null,
          h("div", { ref: endRef })),
        err ? h("div", { className: "atr-err" }, err) : null,
        info ? h("div", { className: "atr-info" }, info) : null,
        room ? h("div", { className: "atr-composer" },
          h("div", { className: "atr-quick" },
            h("button", { type: "button", onClick: function () { insert("@" + AGENT); } }, "@{{agent}}"),
            h("button", { type: "button", onClick: function () { insert("@claude"); } }, "@claude"),
            h("button", { type: "button", onClick: function () { insert("@all"); } }, "@all"),
            h("span", { className: "atr-hint" }, "No mention: both answer. Talk-only: nobody here can use tools.")),
          h("div", { className: "atr-row" },
            h("textarea", { ref: taRef, className: "atr-input", rows: 2, value: text, placeholder: "Say something to the room…  (Ctrl+Enter to send)", maxLength: 6000, onChange: function (e) { setText(e.target.value); },
              onKeyDown: function (e) { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); } } }),
            h("button", { type: "button", className: "atr-btn atr-btn-primary", disabled: !text.trim() || running, onClick: send }, "Send")),
          h("div", { className: "atr-foot" },
            h("button", { type: "button", className: "atr-link", onClick: save }, "Save to my notes"),
            h("button", { type: "button", className: "atr-link", onClick: del }, "Delete room"))) : null));
  }

  REG.register("atrium-room", Page);
})();
