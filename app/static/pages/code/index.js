/* Agentic coding: a coding workspace with the agent, in the style of a coding terminal app.
   Left: projects and past sessions. Middle: the conversation, with every tool the agent uses shown as its own block, and approvals when she
   asks. Right: the project's files, the changes she made (diffs against restore points), and a shared terminal. A restore point of the
   project is saved before every message, so any change can be rewound.
   The agent works through the same API as Chat; the files, git and terminal live in the coding workspace service (see code-sidecar/). */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__, UI = window.__APP_UI__;
  if (!SDK || !REG || !UI || !window.__APP_CODE_COMPOSE__) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect, useRef = SDK.hooks.useRef;
  var fetchJSON = SDK.fetchJSON;
  var CODE = "/api/code", CHAT = "/api/plugins/atrium-chat", C = window.__APP_CODE_COMPOSE__;
  var TERMINAL = { completed: 1, failed: 1, cancelled: 1, interrupted: 1 };

  function errText(e) { var m = String((e && e.message) || e || "Something went wrong"), i = m.indexOf("{"); if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* fall through */ } } return m.replace(/^\d+:\s*/, "").slice(0, 240); }
  function post(url, body) { return fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }); }
  function q(o) { return Object.keys(o).map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(o[k]); }).join("&"); }
  function ago(t) { var s = Math.max(0, Date.now() / 1000 - t); return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 129600 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " d ago"; }
  function pref(k, d) { try { var v = localStorage.getItem("atrium.code." + k); return v === null ? d : v; } catch (x) { return d; } }
  function setPref(k, v) { try { localStorage.setItem("atrium.code." + k, v); } catch (x) { /* optional */ } }

  /* ---------- a tool call as a block ---------- */
  var ICON = { terminal: "▸", shell: "▸", bash: "▸", read_file: "▤", write_file: "✎", patch: "✎", edit: "✎", search_files: "⌕", web_search: "⌕", browser: "◍", todo: "☑", delegate_task: "⇉" };
  function parseArgs(s) { try { var o = JSON.parse(s); return o && typeof o === "object" ? o : {}; } catch (x) { return {}; } }
  function summary(name, args) {
    var v = args.command || args.cmd || args.path || args.file_path || args.file || args.query || args.pattern || args.url || args.goal || args.task || "";
    if (!v) { var k = Object.keys(args)[0]; v = k ? String(args[k]) : ""; }
    return String(v).replace(/\s+/g, " ").slice(0, 140);
  }
  function ToolBlock(p) {
    var o = useState(false), open = o[0], setOpen = o[1];
    var t = p.tool, args = parseArgs(t.arguments), state = t.result === undefined ? (p.live ? "running" : "…") : "done";
    var failed = /\b(error|failed|traceback|no such file|permission denied)\b/i.test(String(t.result || "").slice(0, 300)) && /^\s*(\[?error|traceback|bash:|.*: command not found)/i.test(String(t.result || ""));
    return h("div", { className: "cd-tool" + (failed ? " cd-failed" : "") },
      h("button", { type: "button", className: "cd-tool-head", "aria-expanded": open ? "true" : "false", onClick: function () { setOpen(!open); } },
        h("span", { className: "cd-tool-icon", "aria-hidden": "true" }, ICON[t.name] || "◇"), h("span", { className: "cd-tool-name" }, t.name || "tool"),
        h("span", { className: "cd-tool-sum" }, summary(t.name, args)), h("span", { className: "cd-tool-state" }, state === "running" ? "running…" : failed ? "failed" : state === "done" ? "✓" : "")),
      open ? h("div", { className: "cd-tool-body" },
        h("div", { className: "cd-label" }, "Input"), h("pre", { className: "cd-pre" }, Object.keys(args).length ? Object.keys(args).map(function (k) { return k + ": " + (typeof args[k] === "string" ? args[k] : JSON.stringify(args[k])); }).join("\n").slice(0, 4000) : String(t.arguments || "")),
        t.result !== undefined ? [h("div", { key: "l", className: "cd-label" }, "Result"), h("pre", { key: "r", className: "cd-pre" }, String(t.result).slice(0, 6000))] : null) : null);
  }

  /* messages from the session -> blocks: user text, assistant text, and tool calls paired with their results */
  function build(messages) {
    var out = [], results = {};
    messages.forEach(function (m) { if (m.role === "tool" && m.tool_call_id) results[m.tool_call_id] = m.content; });
    messages.forEach(function (m) {
      if (m.role === "user") { if (m.content && !/^\[CONTEXT COMPACTION/.test(m.content)) out.push({ kind: "user", text: m.content, id: m.id }); }
      else if (m.role === "assistant") {
        if (m.content) out.push({ kind: "assistant", text: m.content, id: m.id });
        (m.tool_calls || []).forEach(function (c) { out.push({ kind: "tool", id: c.id || m.id + c.name, tool: { name: c.name, arguments: c.arguments, result: results[c.id] } }); });
      }
    });
    return out;
  }

  /* ---------- diff display ---------- */
  function Diff(p) {
    if (!p.text) return h("p", { className: "cd-muted" }, "No changes.");
    var files = [], cur = null;
    p.text.split("\n").forEach(function (l) {
      if (l.indexOf("diff --git ") === 0) { cur = { name: l.split(" b/").pop(), lines: [] }; files.push(cur); }
      else if (cur) cur.lines.push(l);
    });
    return h("div", null, files.map(function (f, i) {
      return h("details", { key: i, className: "cd-diff", open: files.length < 6 }, h("summary", null, f.name),
        h("pre", { className: "cd-pre cd-diffpre" }, f.lines.filter(function (l) { return !/^(index |--- |\+\+\+ |new file|deleted file)/.test(l); }).map(function (l, j) {
          return h("div", { key: j, className: l.charAt(0) === "+" ? "cd-add" : l.charAt(0) === "-" ? "cd-del" : l.charAt(0) === "@" ? "cd-hunk" : "" }, l || " ");
        })));
    }));
  }

  /* ---------- the files pane ---------- */
  function Tree(p) {
    var o = useState({}), open = o[0], setOpen = o[1], k = useState({}), kids = k[0], setKids = k[1];
    function load(path) { fetchJSON(CODE + "/tree?" + q({ project: p.project, path: path })).then(function (r) { setKids(function (c) { var n = Object.assign({}, c); n[path] = r.items; return n; }); }, function () { /* ignore */ }); }
    useEffect(function () { setOpen({}); setKids({}); load(""); }, [p.project, p.stamp]);
    function row(path, depth) {
      return (kids[path] || []).map(function (e) {
        var full = path ? path + "/" + e.name : e.name;
        return h("div", { key: full },
          h("button", { type: "button", className: "cd-node" + (p.current === full ? " cd-on" : ""), style: { paddingLeft: (8 + depth * 12) + "px" }, onClick: function () {
            if (e.dir) { var n = Object.assign({}, open); n[full] = !open[full]; setOpen(n); if (!kids[full]) load(full); } else p.onOpen(full); } },
            h("span", { "aria-hidden": "true" }, e.dir ? (open[full] ? "▾ " : "▸ ") : "  "), e.name),
          e.dir && open[full] ? row(full, depth + 1) : null);
      });
    }
    return h("div", { className: "cd-tree" }, (kids[""] || []).length ? row("", 0) : h("p", { className: "cd-muted" }, "This project is empty. Ask the agent to start something."));
  }
  function FileView(p) {
    var s = useState(null), data = s[0], setData = s[1], t = useState(""), text = t[0], setText = t[1], m = useState(""), msg = m[0], setMsg = m[1];
    useEffect(function () { setData(null); setMsg(""); if (!p.path) return; fetchJSON(CODE + "/file?" + q({ project: p.project, path: p.path })).then(function (r) { setData(r); setText(r.text || ""); }, function (e) { setMsg(errText(e)); }); }, [p.project, p.path, p.stamp]);
    if (!p.path) return h("p", { className: "cd-muted" }, "Pick a file to see it.");
    function save() { fetchJSON(CODE + "/file", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project: p.project, path: p.path, text: text }) }).then(function () { setMsg("Saved."); p.onSaved(); }, function (e) { setMsg(errText(e)); }); }
    return h("div", { className: "cd-fileview" },
      h("div", { className: "cd-fileview-head" }, h("strong", null, p.path), data && data.text !== null ? h("button", { type: "button", className: "cd-btn", disabled: text === data.text, onClick: save }, "Save") : null, msg ? h("span", { className: "cd-muted" }, msg) : null),
      !data ? (msg ? h("p", { className: "cd-muted" }, msg) : h(window.__APP_UI__.kit.Loading, { label: "Loading…" })) : data.text === null ? h("p", { className: "cd-muted" }, data.note) :
        h("textarea", { className: "cd-editor", spellCheck: false, value: text, "aria-label": "File contents", onChange: function (e) { setText(e.target.value); } }));
  }

  /* ---------- the terminal ---------- */
  function Term(p) {
    var host = useRef(null), st = useState("connecting"), state = st[0], setState = st[1];
    useEffect(function () {
      if (!window.Terminal || !p.project) { setState("The terminal could not load."); return undefined; }
      var term = new window.Terminal({ cursorBlink: true, fontSize: 13, fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", theme: { background: "#05070f" }, scrollback: 5000 });
      var fit = window.FitAddon ? new window.FitAddon.FitAddon() : null;
      if (fit) term.loadAddon(fit);
      term.open(host.current);
      try { if (fit) fit.fit(); } catch (x) { /* not visible yet */ }
      var ws = null, dead = false;
      function connect() {
        var proto = location.protocol === "https:" ? "wss:" : "ws:";
        ws = new WebSocket(proto + "//" + location.host + CODE + "/pty?" + q({ project: p.project, cols: term.cols, rows: term.rows }));
        ws.binaryType = "arraybuffer";
        ws.onopen = function () { setState("connected"); };
        ws.onmessage = function (ev) { term.write(typeof ev.data === "string" ? ev.data : new Uint8Array(ev.data)); };
        ws.onclose = function () { if (!dead) setState("disconnected"); };
      }
      connect();
      var d1 = term.onData(function (d) { if (ws && ws.readyState === 1) ws.send(d); });
      var d2 = term.onResize(function (s) { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "resize", cols: s.cols, rows: s.rows })); });
      function onSize() { try { if (fit && host.current && host.current.offsetWidth) fit.fit(); } catch (x) { /* hidden */ } }
      var ro = window.ResizeObserver ? new ResizeObserver(onSize) : null; if (ro) ro.observe(host.current);
      window.addEventListener("resize", onSize);
      return function () { dead = true; window.removeEventListener("resize", onSize); if (ro) ro.disconnect(); d1.dispose(); d2.dispose(); try { if (ws) ws.close(); } catch (x) { /* closed */ } term.dispose(); };
    }, [p.project, p.nonce]);
    return h("div", { className: "cd-term-wrap" },
      h("div", { className: "cd-term-bar" }, h("span", { className: "cd-muted" }, state === "connected" ? "Terminal in " + p.project + " (stays running if you close the page)" : state), state === "disconnected" ? h("button", { type: "button", className: "cd-btn", onClick: p.onReconnect }, "Reconnect") : null),
      h("div", { className: "cd-term", ref: host }));
  }

  /* ---------- the shared browser: a live picture of one tab; your mouse and keyboard go to it, and the agent drives the same browser ---------- */
  var VW = 1280, VH = 800;
  function mods(ev) { return (ev.altKey ? 1 : 0) | (ev.ctrlKey ? 2 : 0) | (ev.metaKey ? 4 : 0) | (ev.shiftKey ? 8 : 0); }
  function Browser(p) {
    var ts = useState([]), targets = ts[0], setTargets = ts[1], sel = useState(""), target = sel[0], setTarget = sel[1];
    var fo = useState(true), follow = fo[0], setFollow = fo[1], ur = useState(""), url = ur[0], setUrl = ur[1], ed = useState(false), editing = ed[0], setEditing = ed[1];
    var st = useState("connecting"), state = st[0], setState = st[1];
    var cv = useRef(null), wsRef = useRef(null), known = useRef({}), editRef = useRef(false), moveAt = useRef(0), buttons = useRef(0);
    editRef.current = editing;

    function refresh() {
      fetchJSON(CODE + "/browser/targets").then(function (r) {
        var list = r.targets || [];
        setTargets(list);
        // a tab the agent opens is shown at once (when following); the tab you picked stays until it closes
        var fresh = list.filter(function (t) { return !known.current[t.id]; });
        list.forEach(function (t) { known.current[t.id] = true; });
        setTarget(function (cur) {
          if (cur && list.some(function (t) { return t.id === cur; })) return (follow && fresh.length && Object.keys(known.current).length > 1) ? fresh[fresh.length - 1].id : cur;
          return list.length ? list[list.length - 1].id : "";
        });
        if (!r.up) setState("The browser is not running.");
      }, function (e) { setState(errText(e)); });
    }
    useEffect(function () { refresh(); return window.atriumEvery(refresh, 2500); }, [follow]);

    useEffect(function () {
      if (!target) return undefined;
      var dead = false, ws = new WebSocket((location.protocol === "https:" ? "wss:" : "ws:") + "//" + location.host + CODE + "/browser?" + q({ target: target }));
      ws.binaryType = "arraybuffer"; wsRef.current = ws; setState("connecting");
      ws.onopen = function () { setState("live"); };
      ws.onclose = function () { if (!dead) setState("The picture stopped. Pick the tab again or reload."); };
      ws.onmessage = function (ev) {
        if (typeof ev.data === "string") { try { var m = JSON.parse(ev.data); if (m.type === "url" && !editRef.current) setUrl(m.url); } catch (x) { /* ignore */ } return; }
        var c = cv.current; if (!c || !window.createImageBitmap) return;
        createImageBitmap(new Blob([ev.data], { type: "image/jpeg" })).then(function (bmp) { if (c.width !== bmp.width || c.height !== bmp.height) { c.width = bmp.width; c.height = bmp.height; c.style.aspectRatio = bmp.width + " / " + bmp.height; } c.getContext("2d").drawImage(bmp, 0, 0); if (bmp.close) bmp.close(); }, function () { /* a damaged frame */ });
      };
      return function () { dead = true; wsRef.current = null; try { ws.close(); } catch (x) { /* closed */ } };
    }, [target]);
    useEffect(function () { var t = targets.filter(function (x) { return x.id === target; })[0]; if (t && !editRef.current) setUrl(t.url === "about:blank" ? "" : t.url); }, [target]);

    function send(o) { var ws = wsRef.current; if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }
    function pos(ev) { var r = cv.current.getBoundingClientRect(); return { x: Math.max(0, Math.min(cv.current.width, (ev.clientX - r.left) * (cv.current.width / r.width))), y: Math.max(0, Math.min(cv.current.height, (ev.clientY - r.top) * (cv.current.height / r.height))) }; }
    var BTN = ["left", "middle", "right"];
    function mouse(type) {
      return function (ev) {
        if (type === "mouseMoved") { var now = Date.now(); if (now - moveAt.current < 40) return; moveAt.current = now; }
        var o = pos(ev), btn = type === "mouseMoved" ? "none" : BTN[ev.button] || "left";
        if (type === "mousePressed") { buttons.current |= (1 << ev.button); cv.current.focus(); } if (type === "mouseReleased") buttons.current &= ~(1 << ev.button);
        send({ t: "mouse", type: type, x: o.x, y: o.y, button: btn, buttons: buttons.current, clickCount: type === "mouseMoved" ? 0 : Math.max(1, ev.detail || 1), modifiers: mods(ev) });
      };
    }
    function wheel(ev) { ev.preventDefault(); var o = pos(ev); send({ t: "wheel", x: o.x, y: o.y, dx: ev.deltaX, dy: ev.deltaY }); }
    function key(type) {
      return function (ev) {
        if ((ev.ctrlKey || ev.metaKey) && (ev.key === "v" || ev.key === "V")) return;      // pasting is handled by the paste event
        ev.preventDefault();
        var text = (type === "keyDown" && ev.key.length === 1 && !ev.ctrlKey && !ev.metaKey) ? ev.key : (type === "keyDown" && ev.key === "Enter") ? "\r" : "";
        send({ t: "key", type: type, key: ev.key, code: ev.code, vk: ev.keyCode, text: text, modifiers: mods(ev) });
      };
    }
    function paste(ev) { var t = ev.clipboardData && ev.clipboardData.getData("text"); if (t) { ev.preventDefault(); send({ t: "insert", text: t }); } }
    function go(ev) { ev.preventDefault(); if (url.trim()) send({ t: "nav", url: url.trim() }); setEditing(false); if (cv.current) cv.current.focus(); }
    function openTab() { post(CODE + "/browser/new", { url: "about:blank" }).then(function (r) { known.current[r.id] = true; refresh(); setTimeout(function () { setTarget(r.id); }, 300); }, function (e) { setState(errText(e)); }); }
    function closeTab(id) { post(CODE + "/browser/close", { id: id }).then(function () { refresh(); }, function () { /* gone */ }); }

    return h("div", { className: "cd-browser" },
      h("div", { className: "cd-btabs" }, targets.map(function (t) {
        return h("span", { key: t.id, className: "cd-btab" + (t.id === target ? " cd-on" : "") },
          h("button", { type: "button", className: "cd-btab-main", title: t.url, onClick: function () { setTarget(t.id); } }, (t.title || t.url || "New tab").slice(0, 22)),
          targets.length > 1 ? h("button", { type: "button", className: "cd-btab-x", "aria-label": "Close tab", onClick: function () { closeTab(t.id); } }, "×") : null);
      }), h("button", { type: "button", className: "cd-btn", "aria-label": "New tab", onClick: openTab }, "+")),
      h("form", { className: "cd-urlbar", onSubmit: go },
        h("button", { type: "button", className: "cd-btn", "aria-label": "Back", onClick: function () { send({ t: "history", dir: "back" }); } }, "←"),
        h("button", { type: "button", className: "cd-btn", "aria-label": "Forward", onClick: function () { send({ t: "history", dir: "forward" }); } }, "→"),
        h("button", { type: "button", className: "cd-btn", "aria-label": "Reload", onClick: function () { send({ t: "reload" }); } }, "⟳"),
        h("input", { className: "cd-input cd-url", "aria-label": "Address", placeholder: "Type an address and press Enter", value: url, onFocus: function () { setEditing(true); }, onBlur: function () { setEditing(false); }, onChange: function (e) { setUrl(e.target.value); } })),
      h("div", { className: "cd-bstate" }, h("label", { className: "cd-check" }, h("input", { type: "checkbox", checked: follow, onChange: function (e) { setFollow(e.target.checked); } }), " Show new tabs the agent opens"),
        h("span", { className: "cd-muted" }, state === "live" ? "live" : state)),
      target ? h("canvas", { className: "cd-bview", ref: cv, width: VW, height: VH, tabIndex: 0, "aria-label": "Browser view. Click it to use your keyboard in the page.",
        onMouseMove: mouse("mouseMoved"), onMouseDown: mouse("mousePressed"), onMouseUp: mouse("mouseReleased"), onWheel: wheel, onContextMenu: function (e) { e.preventDefault(); },
        onKeyDown: key("keyDown"), onKeyUp: key("keyUp"), onPaste: paste })
        : h("p", { className: "cd-muted" }, state === "connecting" ? "Starting the browser…" : state));
  }

  /* ---------- the page ---------- */
  function Page() {
    var cfg = UI.config(), agent = cfg.agentName || "Assistant";
    var sc = useState(null), setup = sc[0], setSetup = sc[1];
    var pr = useState([]), projects = pr[0], setProjects = pr[1];
    var cur = useState(pref("project", "")), project = cur[0], setProject = cur[1];
    var ss = useState([]), sessions = ss[0], setSessions = ss[1];
    var rl = useState([]), related = rl[0], setRelated = rl[1], ro_ = useState(false), readOnly = ro_[0], setReadOnly = ro_[1];   // a linked card's worker runs, shown read-only
    var sid = useState(""), session = sid[0], setSession = sid[1];
    var it = useState([]), items = it[0], setItems = it[1];
    var tx = useState(""), text = tx[0], setText = tx[1];
    var lv = useState(null), live = lv[0], setLive = lv[1];                       // { id, status, text, controls, gen }
    var md = useState(pref("mode", "ask")), mode = md[0], setMode = md[1];
    var vw = useState(pref("view", "chat")), view = vw[0], setView = vw[1];                // "chat" | "pipeline"
    var tb = useState("files"), tab = tb[0], setTab = tb[1];
    var fp = useState(""), file = fp[0], setFile = fp[1];
    var gs = useState(null), gstat = gs[0], setGstat = gs[1];
    var df = useState(null), diff = df[0], setDiff = df[1];
    var cps = useState([]), checkpoints = cps[0], setCheckpoints = cps[1];
    var base = useState(""), compare = base[0], setCompare = base[1];
    var stp = useState(0), stamp = stp[0], setStamp = stp[1];
    var tn = useState(0), termNonce = tn[0], setTermNonce = tn[1];
    var er = useState(""), err = er[0], setErr = er[1];
    var np = useState(false), creating = np[0], setCreating = np[1];
    var nn = useState(""), newName = nn[0], setNewName = nn[1];
    var ng = useState(""), newGit = ng[0], setNewGit = ng[1];
    var at_ = useState([]), atts = at_[0], setAtts = at_[1], dr_ = useState(false), dragging = dr_[0], setDragging = dr_[1], fileIn = useRef(null);   // files attached to the next message
    var fl = useState([]), files = fl[0], setFiles = fl[1];                       // every file in the project, for @mentions
    var ca = useState(0), caret = ca[0], setCaret = ca[1];
    var mi = useState(0), menuAt = mi[0], setMenuAt = mi[1];
    var mo = useState(false), menuOff = mo[0], setMenuOff = mo[1];                // Esc hides the menu until the text changes
    var nt = useState(""), note = nt[0], setNote = nt[1];                         // what a command printed (help, status)
    var boxRef = useRef(null);
    var logRef = useRef(null), modeRef = useRef(mode), liveRef = useRef(null), sessionRef = useRef(""), projectRef = useRef(project);
    modeRef.current = mode; liveRef.current = live; sessionRef.current = session; projectRef.current = project;
    var running = !!(live && !TERMINAL[live.status]);

    useEffect(function () { fetchJSON(CODE + "/status").then(setSetup, function (e) { setSetup({ configured: false, up: false, error: errText(e) }); }); }, []);
    function loadProjects() { return fetchJSON(CODE + "/projects").then(function (r) { setProjects(r.projects || []); return r.projects || []; }, function (e) { setErr(errText(e)); return []; }); }
    function loadSessions(p) { fetchJSON(CODE + "/sessions" + (p ? "?" + q({ project: p }) : "")).then(function (r) { setSessions(r.sessions || []); setRelated(r.related || []); }, function () { /* optional */ }); }
    useEffect(function () {
      if (!(setup && setup.configured && setup.up)) return;
      loadProjects().then(function (ps) {
        if (ps.length) { if (!ps.some(function (p) { return p.name === project; })) setProject(ps[0].name); return; }
        // a first visit: make a scratch project so the files, changes and terminal work straight away
        post(CODE + "/projects", { name: "scratch" }).then(function () { return loadProjects(); }).then(function () { setProject("scratch"); }, function (e) { setErr(errText(e)); });
      });
    }, [setup]);
    useEffect(function () { if (setup && setup.configured && setup.up) loadSessions(project); }, [project, setup]);
    useEffect(function () { if (project) setPref("project", project); }, [project]);

    function loadGit() {
      if (!project) return;
      fetchJSON(CODE + "/git/status?" + q({ project: project })).then(setGstat, function () { setGstat(null); });
      fetchJSON(CODE + "/git/checkpoints?" + q({ project: project })).then(function (r) { setCheckpoints(r.checkpoints || []); }, function () { /* optional */ });
      var params = { project: project }; if (compare) params.base = compare;
      fetchJSON(CODE + "/git/diff?" + q(params)).then(setDiff, function (e) { setDiff({ diff: "", error: errText(e) }); });
    }
    useEffect(function () { loadGit(); }, [project, compare, stamp]);
    useEffect(function () { setAtts([]); }, [project]);
    useEffect(function () { setFiles([]); if (project) fetchJSON(CODE + "/files?" + q({ project: project })).then(function (r) { setFiles(r.files || []); }, function () { /* mentions are optional */ }); }, [project, stamp]);
    useEffect(function () { setItems([]); setSession(""); setLive(null); setFile(""); setCompare(""); setReadOnly(false); }, [project]);
    useEffect(function () { var el = logRef.current; if (el) el.scrollTop = el.scrollHeight; }, [items.length, live && live.text, live && live.controls && live.controls.length]);

    function loadMessages(id) { return fetchJSON(CODE + "/messages?" + q({ session_id: id })).then(function (r) { setItems(build(r.messages || [])); }, function () { /* the first message may not be stored yet */ }); }
    function openSession(id, ro) { setSession(id); setLive(null); setReadOnly(!!ro); loadMessages(id); }

    /* follow a run: stream the answer text, poll the stored messages (tool calls and results) and the approvals */
    function follow(runId, id) {
      var alive = true, ctl = window.AbortController ? new AbortController() : null;
      fetch(CHAT + "/stream/" + encodeURIComponent(runId), { credentials: "same-origin", signal: ctl ? ctl.signal : undefined }).then(function (resp) {
        var reader = resp.body.getReader(), dec = new TextDecoder(), buf = "";
        function pump() {
          return reader.read().then(function (r) {
            if (r.done || !alive) return;
            buf += dec.decode(r.value, { stream: true });
            var parts = buf.split("\n\n"); buf = parts.pop();
            parts.forEach(function (chunk) {
              var line = chunk.split("\n").filter(function (l) { return l.indexOf("data:") === 0; }).map(function (l) { return l.slice(5).trim(); }).join("");
              if (!line) return;
              try { var ev = JSON.parse(line); if (ev.event === "message.delta" && typeof ev.delta === "string") setLive(function (c) { return c && c.id === runId ? Object.assign({}, c, { text: (c.text || "") + ev.delta }) : c; }); } catch (x) { /* ignore */ }
            });
            return pump();
          });
        }
        return pump();
      }).catch(function () { /* the polling below still finishes the run */ });
      var timer;
      function tick() {
        if (!alive) return;
        Promise.all([fetchJSON(CHAT + "/runs/" + runId), fetchJSON(CODE + "/messages?" + q({ session_id: id })).catch(function () { return null; })]).then(function (all) {
          if (!alive) return;
          var r = all[0], m = all[1];
          if (m) setItems(build(m.messages || []));
          var controls = r.pending_controls || [];
          if (modeRef.current === "auto" && controls.length) controls.forEach(function (c) { post(CHAT + "/runs/" + runId + "/approval", { request_id: c.request_id || c.prompt_id || c.id, execution_generation: r.execution_generation, choice: "once" }).catch(function () { /* decided already */ }); });
          setLive(function (c) { return c && c.id === runId ? Object.assign({}, c, { status: r.status, controls: modeRef.current === "auto" ? [] : controls, gen: r.execution_generation, error: r.error }) : c; });
          if (TERMINAL[r.status]) {
            alive = false; if (ctl) ctl.abort();
            loadMessages(id).then(function () { setLive(function (c) { return c && c.id === runId ? Object.assign({}, c, { text: "" }) : c; }); });
            setStamp(function (n) { return n + 1; }); loadSessions(projectRef.current); loadProjects();
            return;
          }
          timer = setTimeout(tick, 1400);
        }, function () { if (alive) timer = setTimeout(tick, 2500); });
      }
      timer = setTimeout(tick, 900);
      return function () { alive = false; clearTimeout(timer); if (ctl) ctl.abort(); };
    }
    var stopFollow = useRef(null);
    useEffect(function () { return function () { if (stopFollow.current) stopFollow.current(); }; }, []);

    /* ---- slash commands and @file mentions (the text rules live in compose.js) ---- */
    var cmdHits = menuOff ? [] : C.matchCommands(text);
    var mention = menuOff || cmdHits.length ? null : C.mentionAt(text, caret);
    var fileHits = mention ? C.rankFiles(files, mention.query, 8) : [];
    var menu = cmdHits.length ? cmdHits.map(function (c) { return { key: c.name, label: "/" + c.name + (c.args ? " " + c.args : ""), hint: c.hint, cmd: c }; })
      : fileHits.map(function (f) { return { key: f, label: f, hint: "", file: f }; });
    var menuSel = Math.min(menuAt, Math.max(0, menu.length - 1));
    function typed(e) { setText(e.target.value); setCaret(e.target.selectionStart); setMenuAt(0); setMenuOff(false); }
    function focusBox(pos) { setTimeout(function () { var b = boxRef.current; if (!b) return; b.focus(); if (typeof pos === "number") { try { b.setSelectionRange(pos, pos); } catch (x) { /* not focusable */ } } }, 0); }
    function pick(item, fillOnly) {
      if (item.file) { var r = C.applyMention(text, caret, mention, item.file); setText(r.text); setCaret(r.caret); focusBox(r.caret); return; }
      if (fillOnly || item.cmd.args) { var v = "/" + item.cmd.name + " "; setText(v); setCaret(v.length); focusBox(v.length); return; }
      runCommand({ command: item.cmd, name: item.cmd.name, arg: "" }); focusBox();
    }
    function boxKey(e) {
      if (menu.length) {
        if (e.key === "ArrowDown") { e.preventDefault(); setMenuAt((menuSel + 1) % menu.length); return; }
        if (e.key === "ArrowUp") { e.preventDefault(); setMenuAt((menuSel + menu.length - 1) % menu.length); return; }
        if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); pick(menu[menuSel], e.key === "Tab"); return; }
        if (e.key === "Escape") { e.preventDefault(); setMenuOff(true); return; }
      }
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    }
    function runCommand(p) {
      var c = p.command; setErr("");
      if (!c) { setErr("There is no command called /" + p.name + ". Type /help to see them all."); return; }
      if (c.kind === "ask") { if (running) { setErr("Wait for this run to finish, or stop it first."); return; } sendText(c.prompt + (p.arg ? "\n\n" + p.arg : "")); return; }
      setText(""); setNote("");
      if (c.name === "help") setNote(C.COMMANDS.map(function (x) { var l = "/" + x.name + (x.args ? " " + x.args : ""); return l + new Array(Math.max(2, 20 - l.length)).join(" ") + x.hint; }).join("\n") + "\n\nType @ and part of a file name to point " + agent + " at a file.");
      else if (c.name === "new") newSession();
      else if (c.name === "stop") { if (running) stop(); else setNote("Nothing is running."); }
      else if (c.name === "rewind") { if (running) setErr("Stop the run before rewinding."); else if (checkpoints.length) rewind(checkpoints[0]); else setNote("There are no restore points yet. One is saved before each message."); }
      else if (c.name === "diff") setTab("changes");
      else if (c.name === "files" || c.name === "terminal" || c.name === "browser") setTab(c.name);
      else if (c.name === "mode") {
        var a = p.arg.toLowerCase();
        if (a === "ask" || a === "auto") { pickMode(a); setNote(a === "ask" ? "Permissions: you are asked before each risky step." : "Permissions: everything is approved for you."); }
        else setNote("Permissions are set to: " + (mode === "auto" ? "approve everything (auto)" : "ask me (ask)") + ". Use /mode ask or /mode auto to change it.");
      }
      else if (c.name === "status") {
        var ch = gstat && gstat.changes ? gstat.changes : [];
        setNote("Project: " + (project || "none") + "\nBranch: " + (gstat && gstat.git ? (gstat.branch === "HEAD" ? "no commits yet" : gstat.branch) : "not a git project") +
          "\nChanged files: " + ch.length + "\nRestore points: " + checkpoints.length + "\nPermissions: " + (mode === "auto" ? "approve everything" : "ask me") + "\nSession: " + (session ? "open" : "new"));
      }
    }

    /* ---- attachments: a file is saved in the project's attachments folder and named in the message like an @file ---- */
    function addFiles(list) {
      if (!project || !list || !list.length) return;
      setErr("");
      Array.prototype.slice.call(list, 0, 10).forEach(function (f) {
        var id = "a" + Date.now() + Math.random().toString(36).slice(2, 6), fname = f.name || ("pasted-" + Date.now() + (/^image\//.test(f.type) ? "." + f.type.slice(6).replace("jpeg", "jpg") : ""));
        if (f.size > 25000000) { setErr(fname + " is over the 25 MB limit."); return; }
        if (!f.size) { setErr(fname + " is empty."); return; }
        setAtts(function (c) { return c.concat([{ id: id, name: fname, size: f.size, state: "up" }]); });
        fetchJSON(CODE + "/upload?" + q({ project: project, name: fname }), { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: f }).then(function (r) {
          setAtts(function (c) { return c.map(function (a) { return a.id === id ? Object.assign({}, a, { state: "ok", path: r.path }) : a; }); }); setStamp(function (n) { return n + 1; });
        }, function (e) {
          setAtts(function (c) { return c.filter(function (a) { return a.id !== id; }); }); setErr("Could not attach " + fname + ": " + errText(e));
        });
      });
    }
    function dropped(e) { e.preventDefault(); setDragging(false); if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) addFiles(e.dataTransfer.files); }
    function pastedFiles(e) { var fl2 = e.clipboardData && e.clipboardData.files; if (fl2 && fl2.length) { e.preventDefault(); addFiles(fl2); } }
    var uploading = atts.some(function (a) { return a.state === "up"; });
    function kb(n) { return n < 1024 ? n + " B" : n < 1048576 ? Math.round(n / 1024) + " KB" : (n / 1048576).toFixed(1) + " MB"; }

    function send() {
      var t = text.trim();
      if ((!t && !atts.length) || !project || uploading) return;
      var cmd = C.parseCommand(t);
      if (cmd) { runCommand(cmd); return; }
      if (running) return;
      var ready = atts.filter(function (a) { return a.state === "ok"; });
      if (ready.length) { t = (t || "Please look at the attached file" + (ready.length > 1 ? "s" : "") + ".") + "\n\nAttached: " + ready.map(function (a) { return "@" + a.path; }).join(" "); setAtts([]); }
      sendText(t);
    }
    function sendText(t) {
      setErr(""); setText(""); setNote("");
      setItems(function (c) { return c.concat([{ kind: "user", text: t, id: "tmp" + Date.now() }]); });
      post(CODE + "/send", { project: project, message: t, session_id: session || undefined }).then(function (r) {
        setSession(r.session_id); setLive({ id: r.run_id, status: "started", text: "", controls: [] });
        if (stopFollow.current) stopFollow.current();
        stopFollow.current = follow(r.run_id, r.session_id);
      }, function (e) { setErr(errText(e)); });
    }
    function decide(c, choice) { post(CHAT + "/runs/" + live.id + "/approval", { request_id: c.request_id || c.prompt_id || c.id, execution_generation: live.gen, choice: choice }).catch(function (e) { setErr(errText(e)); }); }
    function stop() { if (live) fetchJSON(CHAT + "/runs/" + live.id + "/stop", { method: "POST" }).catch(function () { /* over already */ }); }
    function newSession() { setSession(""); setItems([]); setLive(null); setReadOnly(false); }
    function create() {
      post(CODE + "/projects", { name: newName.trim(), git_url: newGit.trim() || undefined }).then(function () { setCreating(false); var n = newName.trim(); setNewName(""); setNewGit(""); loadProjects().then(function () { setProject(n); }); }, function (e) { setErr(errText(e)); });
    }
    function rewind(c) {
      if (!window.confirm("Put " + project + " back the way it was at \"" + c.label + "\"? Anything changed since will be undone (a restore point of right now is saved first).")) return;
      post(CODE + "/git/rewind", { project: project, ref: c.ref }).then(function () { setStamp(function (n) { return n + 1; }); }, function (e) { setErr(errText(e)); });
    }
    function pickMode(m) { setMode(m); setPref("mode", m); }

    if (!setup) return h("div", { className: "cd-page" }, h(window.__APP_UI__.kit.Loading, { label: "Loading…" }));
    if (!setup.configured) return h("div", { className: "cd-page" }, h("h1", null, "Agentic coding"), h("div", { className: "cd-note" }, "The coding workspace is not set up on this install yet. It needs the workspace service (see ", h("code", null, "code-sidecar/"), ") and the CODE_URL and CODE_TOKEN settings."));
    if (!setup.up) return h("div", { className: "cd-page" }, h("h1", null, "Agentic coding"), h("div", { className: "cd-note" }, "The coding workspace service is not answering. Check that its container is running."));

    var tabBtn = function (id, label) { return h("button", { type: "button", role: "tab", "aria-selected": tab === id ? "true" : "false", className: "cd-tab" + (tab === id ? " cd-on" : ""), onClick: function () { setTab(id); } }, label); };
    var changes = gstat && gstat.changes ? gstat.changes.length : 0;
    var rows = items.map(function (m) {
      return m.kind === "tool" ? h(ToolBlock, { key: m.id, tool: m.tool, live: running })
        : h("div", { key: m.id, className: "cd-msg cd-" + m.kind }, m.kind === "assistant" ? h("div", { className: "cd-who" }, agent) : null, h("div", { className: "cd-text" }, m.text));
    });
    function pickView(v) { setView(v); setPref("view", v); }
    var viewSwitch = h("div", { className: "cd-views", role: "tablist", "aria-label": "Agentic coding views" },
      h("button", { type: "button", role: "tab", "aria-selected": view === "chat" ? "true" : "false", className: "cd-view" + (view === "chat" ? " cd-on" : ""), onClick: function () { pickView("chat"); } }, "Chat"),
      window.__APP_PIPELINE__ ? h("button", { type: "button", role: "tab", "aria-selected": view === "pipeline" ? "true" : "false", className: "cd-view" + (view === "pipeline" ? " cd-on" : ""), onClick: function () { pickView("pipeline"); } }, "Pipeline") : null);
    if (view === "pipeline" && window.__APP_PIPELINE__) {
      return h("div", { className: "cd-page" }, viewSwitch, h(window.__APP_PIPELINE__, { mode: mode, onProjects: loadProjects, project: project, projects: projects.map(function (p) { return p.name; }), onPick: function (n) { setProject(n); setPref("project", n); },
        onChat: function (name, firstText) { setProject(name); setPref("project", name); setText(firstText || ""); pickView("chat"); } }));
    }
    return h("div", { className: "cd-page" }, viewSwitch,
      h("div", { className: "cd-layout" },
        h("aside", { className: "cd-side", "aria-label": "Projects and sessions" },
          h("div", { className: "cd-label" }, "Project"),
          h("select", { className: "cd-select", value: project, "aria-label": "Project", onChange: function (e) { setProject(e.target.value); } }, projects.length ? projects.map(function (p) { return h("option", { key: p.name, value: p.name }, p.name + (p.dirty ? "  (" + p.dirty + " changed)" : "")); }) : h("option", { value: "" }, "No projects yet")),
          creating ? h("div", { className: "cd-newproj" },
            h("input", { className: "cd-input", placeholder: "Project name", "aria-label": "Project name", value: newName, onChange: function (e) { setNewName(e.target.value); } }),
            h("input", { className: "cd-input", placeholder: "Git address to copy (optional)", "aria-label": "Git address", value: newGit, onChange: function (e) { setNewGit(e.target.value); } }),
            h("div", { className: "cd-row" }, h("button", { type: "button", className: "cd-btn cd-primary", disabled: !newName.trim(), onClick: create }, "Create"), h("button", { type: "button", className: "cd-btn", onClick: function () { setCreating(false); } }, "Cancel")))
            : h("button", { type: "button", className: "cd-btn", onClick: function () { setCreating(true); } }, "+ New project"),
          h("div", { className: "cd-label", style: { marginTop: "14px" } }, "Sessions"),
          h("button", { type: "button", className: "cd-btn cd-primary", disabled: !project, onClick: newSession }, "+ New session"),
          h("div", { className: "cd-sessions" }, sessions.length ? sessions.map(function (s) {
            return h("button", { key: s.id, type: "button", className: "cd-session" + (s.id === session ? " cd-on" : ""), onClick: function () { openSession(s.id); } }, h("span", null, s.title), h("small", null, s.messages + " msgs" + (s.last_active ? " · " + ago(s.last_active) : "")));
          }) : h("p", { className: "cd-muted" }, "No sessions yet.")),
          related.length ? h("div", { className: "cd-label", style: { marginTop: "14px" } }, "Related work") : null,
          related.length ? h("p", { className: "cd-muted", style: { margin: "0 0 6px" } }, "Worker runs of the linked kanban card. Read only.") : null,
          related.length ? h("div", { className: "cd-sessions" }, related.map(function (s) {
            return h("button", { key: s.id, type: "button", className: "cd-session" + (s.id === session ? " cd-on" : ""), onClick: function () { openSession(s.id, true); } }, h("span", null, s.title), h("small", null, s.messages + " msgs" + (s.last_active ? " · " + ago(s.last_active) : "")));
          })) : null),
        h("section", { className: "cd-main", "aria-label": "Conversation" },
          h("div", { className: "cd-bar" },
            h("strong", null, project || "No project"), gstat && gstat.git ? h("span", { className: "cd-chip" }, "⎇ " + (gstat.branch === "HEAD" ? "no commits yet" : gstat.branch)) : null,
            changes ? h("button", { type: "button", className: "cd-chip cd-chip-btn", onClick: function () { setTab("changes"); } }, changes + " changed") : null,
            h("span", { style: { flex: 1 } }),
            h("label", { className: "cd-mode", title: "Ask: you approve each risky step. Auto: everything is approved for you." }, "Permissions ",
              h("select", { className: "cd-select", value: mode, "aria-label": "Permission mode", onChange: function (e) { pickMode(e.target.value); } }, h("option", { value: "ask" }, "Ask me"), h("option", { value: "auto" }, "Approve everything")))),
          readOnly ? h("div", { className: "cd-cmdnote", role: "status" }, "Read only: this is one of " + agent + "'s worker sessions on the linked card. Start a new session to give her something to do here.") : null,
          h("div", { className: "cd-log", ref: logRef, role: "log", "aria-live": "polite" },
            rows.length || live ? [rows,
              live && live.text ? h("div", { key: "live", className: "cd-msg cd-assistant" }, h("div", { className: "cd-who" }, agent), h("div", { className: "cd-text" }, live.text)) : null,
              running && !(live && live.text) && !(live && live.controls && live.controls.length) ? h("div", { key: "busy", className: "cd-working" }, h("i", { className: "cd-dot" }), agent + " is working…") : null,
              live && live.controls && live.controls.length ? h("div", { key: "ctl", className: "cd-approve", role: "alert" }, live.controls.map(function (c, i) {
                return h("div", { key: i, className: "cd-approve-row" }, h("span", null, agent + " wants to: " + String(c.title || c.description || c.prompt || c.command || "do something")),
                  h("button", { type: "button", className: "cd-btn cd-primary", onClick: function () { decide(c, "once"); } }, "Allow once"), h("button", { type: "button", className: "cd-btn", onClick: function () { decide(c, "deny"); } }, "Deny"));
              })) : null,
              live && live.status && TERMINAL[live.status] && live.status !== "completed" ? h("div", { key: "fail", className: "cd-err" }, "That run ended: " + live.status + (live.error ? " (" + live.error + ")" : "")) : null]
              : h("div", { className: "cd-empty" }, h("h2", null, project ? "What are we building in " + project + "?" : "Create a project to begin"), h("p", { className: "cd-muted" }, "Tell " + agent + " what to do. Every tool she uses shows up here, and a restore point is saved before each message."))),
          err ? h("div", { className: "cd-err", role: "alert" }, err) : null,
          note ? h("div", { className: "cd-cmdnote", role: "status" }, h("pre", { className: "cd-pre" }, note), h("button", { type: "button", className: "cd-btn", "aria-label": "Close", onClick: function () { setNote(""); } }, "×")) : null,
          h("div", { className: "cd-compose" + (dragging ? " cd-drop" : ""), onDragOver: function (e) { if (project) { e.preventDefault(); setDragging(true); } }, onDragLeave: function (e) { if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false); }, onDrop: dropped },
            atts.length ? h("div", { className: "cd-atts", "aria-label": "Attached files" }, atts.map(function (a) { return h("span", { key: a.id, className: "cd-att" + (a.state === "up" ? " cd-att-up" : "") }, h("span", { className: "cd-att-name" }, a.name), h("span", { className: "cd-att-size" }, a.state === "up" ? "uploading…" : kb(a.size)), h("button", { type: "button", className: "cd-att-x", "aria-label": "Remove " + a.name, onClick: function () { setAtts(function (c) { return c.filter(function (x) { return x.id !== a.id; }); }); } }, "×")); })) : null,
            menu.length ? h("div", { className: "cd-menu", role: "listbox", id: "cd-menu", "aria-label": cmdHits.length ? "Commands" : "Files" }, menu.map(function (it, i) {
              return h("button", { key: it.key, type: "button", role: "option", "aria-selected": i === menuSel ? "true" : "false", className: "cd-menu-item" + (i === menuSel ? " cd-on" : ""),
                onMouseDown: function (e) { e.preventDefault(); }, onClick: function () { pick(it, false); } }, h("span", { className: "cd-menu-label" }, it.label), it.hint ? h("span", { className: "cd-menu-hint" }, it.hint) : null);
            })) : null,
            h("textarea", { className: "cd-text-in", ref: boxRef, rows: 3, maxLength: 12000, "aria-label": "Message", "aria-controls": menu.length ? "cd-menu" : undefined, disabled: !project || readOnly,
              placeholder: project ? "Message " + agent + " about " + project + "…  (Enter to send, / for commands, @ for files)" : "Create or pick a project first", value: text,
              onChange: typed, onPaste: pastedFiles, onKeyDown: boxKey, onKeyUp: function (e) { if (e.target.selectionStart !== caret) setCaret(e.target.selectionStart); }, onClick: function (e) { setCaret(e.target.selectionStart); } }),
            h("div", { className: "cd-compose-btns" }, h("input", { type: "file", multiple: true, ref: fileIn, className: "cd-file-in", tabIndex: -1, "aria-hidden": "true", onChange: function (e) { addFiles(e.target.files); e.target.value = ""; } }), h("button", { type: "button", className: "cd-btn", disabled: !project, title: "Attach files (or drop or paste them here)", "aria-label": "Attach files", onClick: function () { if (fileIn.current) fileIn.current.click(); } }, "📎 Attach"), h("button", { type: "button", className: "cd-btn cd-primary", disabled: !project || readOnly || uploading || (!text.trim() && !atts.length) || (running && !C.parseCommand(text.trim())), onClick: send }, running ? "Working…" : "Send"), running ? h("button", { type: "button", className: "cd-btn", onClick: stop }, "Stop") : null))),
        h("aside", { className: "cd-right", "aria-label": "Project tools" },
          h("div", { className: "cd-tabs", role: "tablist" }, tabBtn("files", "Files"), tabBtn("changes", "Changes" + (changes ? " (" + changes + ")" : "")), tabBtn("terminal", "Terminal"), tabBtn("browser", "Browser")),
          !project && tab !== "browser" ? h("div", { className: "cd-pane" }, h("p", { className: "cd-muted" }, "Pick or create a project on the left first. The files, changes and terminal belong to a project.")) : null,
          project && tab === "files" ? h("div", { className: "cd-pane" }, [h(Tree, { key: "t", project: project, stamp: stamp, current: file, onOpen: setFile }), h(FileView, { key: "f", project: project, path: file, stamp: stamp, onSaved: function () { setStamp(function (n) { return n + 1; }); } })]) : null,
          project && tab === "changes" ? h("div", { className: "cd-pane" },
            h("div", { className: "cd-row" }, h("label", { className: "cd-muted" }, "Compare with "),
              h("select", { className: "cd-select", value: compare, "aria-label": "Compare with", onChange: function (e) { setCompare(e.target.value); } }, h("option", { value: "" }, "last commit"), checkpoints.map(function (c) { return h("option", { key: c.ref, value: c.ref }, c.label + " · " + ago(c.time)); }))),
            diff && diff.error ? h("p", { className: "cd-muted" }, diff.error) : h(Diff, { text: diff && diff.diff }),
            checkpoints.length ? h("div", { className: "cd-cps" }, h("div", { className: "cd-label" }, "Restore points"), checkpoints.slice(0, 10).map(function (c) { return h("div", { key: c.ref, className: "cd-cp" }, h("span", null, c.label || "checkpoint", h("small", null, " · " + ago(c.time))), h("button", { type: "button", className: "cd-btn", onClick: function () { rewind(c); } }, "Rewind")); })) : null) : null,
          tab === "browser" ? h("div", { className: "cd-pane cd-pane-term" }, h(Browser, null)) : null,
          project && tab === "terminal" ? h("div", { className: "cd-pane cd-pane-term" }, h(Term, { project: project, nonce: termNonce, onReconnect: function () { setTermNonce(termNonce + 1); } })) : null)));
  }

  REG.register("code-page", Page);
})();
