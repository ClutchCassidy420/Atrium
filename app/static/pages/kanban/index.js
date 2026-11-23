/* Kanban: the task board. Columns of cards you can open, move, comment on and hand to the agent. An idea you capture waits in the first column
   (nobody is assigned, so nothing starts by itself); "Start with the agent" asks first, then has a spec written and the card assigned.
   The board lives in the agent dashboard; this page talks to it only through the app's /api/kanban routes (see app/kanban_backend.py). */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__, UI = window.__APP_UI__;
  if (!SDK || !REG || !UI) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect, useRef = SDK.hooks.useRef;
  var fetchJSON = SDK.fetchJSON, API = "/api/kanban";
  var JSONH = { "Content-Type": "application/json" };

  function errText(e) { var m = String((e && e.message) || e || "Something went wrong"), i = m.indexOf("{"); if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* plain text */ } } return m.replace(/^\d+:\s*/, ""); }
  // Card notes are lightly formatted text: headings, **bold**, `code`, bullet and checkbox lists. Built as elements, never as HTML.
  function inl(text, base) {
    var out = [], re = /(\*\*[^*]+\*\*|`[^`]+`)/g, last = 0, m, n = 0;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      var tok = m[0];
      out.push(tok.charAt(0) === "`" ? h("code", { key: base + "c" + n++, className: "kb-code" }, tok.slice(1, -1)) : h("strong", { key: base + "b" + n++ }, tok.slice(2, -2)));
      last = m.index + tok.length;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }
  function Notes(p) {
    var lines = String(p.text || "").split(/\r?\n/), out = [], list = null, k = 0;
    function flush() { if (list) { out.push(h("ul", { key: "u" + k++, className: "kb-ul" }, list)); list = null; } }
    lines.forEach(function (ln, i) {
      var li = /^\s*[-*]\s+(\[( |x|X)\]\s+)?(.*)$/.exec(ln);
      if (li) { (list = list || []).push(h("li", { key: i }, li[1] ? (li[2] === " " ? "☐ " : "☑ ") : null, inl(li[3], "l" + i))); return; }
      flush();
      var hd = /^(#{1,4})\s+(.*)$/.exec(ln);
      if (hd) out.push(h("div", { key: "h" + i, className: "kb-nh" }, inl(hd[2], "h" + i)));
      else if (ln.trim()) out.push(h("p", { key: "p" + i, className: "kb-np" }, inl(ln, "p" + i)));
    });
    flush();
    return h("div", { className: "kb-body kb-md" }, out);
  }
  function send(method, url, body) { return fetchJSON(url, { method: method, headers: JSONH, body: body === undefined ? undefined : JSON.stringify(body) }); }
  function when(ts) { return ts ? UI.ago(ts) : ""; }
  var TONE = { triage: "idea", todo: "todo", scheduled: "todo", ready: "go", running: "run", blocked: "bad", review: "warn", done: "ok" };

  /* ---------- one card in a column ---------- */
  function Card(p) {
    var t = p.t;
    return h("button", { type: "button", className: "kb-card" + (t.running ? " kb-running" : "") + (p.dragging ? " kb-dragging" : ""), draggable: p.canDrag,
      onClick: function () { p.onOpen(t.id); },
      onDragStart: function (e) { e.dataTransfer.setData("text/plain", t.id); e.dataTransfer.effectAllowed = "move"; p.onDrag(t.id); }, onDragEnd: function () { p.onDrag(""); } },
      h("div", { className: "kb-card-t" }, t.running ? h("span", { className: "kb-live", "aria-label": "A worker is on it" }) : null, t.title || "(no title)"),
      t.preview ? h("div", { className: "kb-card-p" }, t.preview) : null,
      t.last_error && t.failures ? h("div", { className: "kb-card-err" }, t.last_error) : null,
      h("div", { className: "kb-chips" },
        t.priority ? h("span", { className: "kb-chip kb-pri" }, "P" + t.priority) : null,
        t.assignee ? h("span", { className: "kb-chip" }, t.assignee) : null,
        t.comment_count ? h("span", { className: "kb-chip" }, "💬 " + t.comment_count) : null,
        t.progress ? h("span", { className: "kb-chip" }, typeof t.progress === "object" ? "…" : String(t.progress)) : null,
        h("span", { className: "kb-chip kb-age" }, when(t.completed_at || t.started_at || t.created_at))));
  }

  /* ---------- the card panel ---------- */
  function Panel(p) {
    var id = p.id, cols = p.cols;
    var d_ = useState(null), data = d_[0], setData = d_[1];
    var e_ = useState(""), err = e_[0], setErr = e_[1];
    var bz_ = useState(false), busy = bz_[0], setBusy = bz_[1];
    var ed_ = useState(null), edit = ed_[0], setEdit = ed_[1];          // {title, body} while editing
    var cm_ = useState(""), comment = cm_[0], setComment = cm_[1];
    var lg_ = useState(null), log = lg_[0], setLog = lg_[1];
    var cf_ = useState(""), confirm = cf_[0], setConfirm = cf_[1];      // "start" | "resume" | "unblock" | "done" | "review" | "delete" | "reclaim"
    var sm_ = useState(""), summary = sm_[0], setSummary = sm_[1];     // the hand-off note for done / review
    function load() { return fetchJSON(API + "/tasks/" + id).then(function (r) { setData(r); setErr(""); }, function (e) { setErr(errText(e)); }); }
    useEffect(function () { setData(null); setEdit(null); setLog(null); setConfirm(""); setSummary(""); setComment(""); load(); return window.atriumEvery(load, 15000); }, [id]);
    var t = data && data.task;
    function act(fn, done) { setBusy(true); setErr(""); fn().then(function (r) { setBusy(false); setConfirm(""); if (done) done(r); p.onChanged(); load(); }, function (e) { setBusy(false); setConfirm(""); setErr(errText(e)); }); }
    function move(to) { if (to && t && to !== t.status) act(function () { return send("PATCH", API + "/tasks/" + id, { status: to }); }); }
    function startIt() { act(function () { return send("POST", API + "/tasks/" + id + "/start"); }, function (r) { if (r && r.ok === false) setErr(r.reason || "The board could not start this card."); }); }
    var closeBtn = h("button", { type: "button", className: "kb-btn", onClick: p.onClose, "aria-label": "Close" }, "Close");
    if (!t) return h("aside", { className: "kb-panel", "aria-label": "Card" }, h("div", { className: "kb-panel-top" }, closeBtn), err ? h(window.__APP_UI__.kit.Alert, { tone: "danger" }, err) : h(window.__APP_UI__.kit.Loading, { label: "Loading…" }));
    var canStart = t.status === "triage";
    var resumable = t.status === "triage" && !!t.assignee && (data.runs || []).length > 0;       // paused work that was already started: pick it up, do not rewrite its spec
    var finishable = ["triage", "todo", "ready", "running", "blocked", "review"].indexOf(t.status) >= 0;
    var WORDS = {
      start: ["Hand this to " + p.agent + "? A spec is written first (this rewrites the card's notes), then a worker picks the card up and starts. Only one card runs at a time.", "Yes, start"],
      resume: ["Put this card back in Ready so a worker picks up where it left off? Its notes and files are kept; no new spec is written.", "Yes, resume"],
      unblock: ["Unblock this card? It goes back to Ready and a worker picks it up again. Add a comment first if you have an answer for it.", "Unblock"],
      done: ["Mark this card done? Add a short hand-off note: what was done and how it was checked.", "Mark done"],
      review: ["Send this card to review? Add a short note on what to look at.", "Send to review"],
      reclaim: ["Take this card back from its worker? The work stops.", "Take it back"],
      "delete": ["Delete this card? This cannot be undone.", "Delete"]
    };
    function confirmed() {
      if (confirm === "start") return startIt();
      if (confirm === "resume" || confirm === "unblock") return act(function () { return send("PATCH", API + "/tasks/" + id, { status: "ready" }); });
      if (confirm === "done" || confirm === "review") { var sv = summary.trim(); return act(function () { return send("PATCH", API + "/tasks/" + id, sv ? { status: confirm, summary: sv } : { status: confirm }); }, function () { setSummary(""); }); }
      if (confirm === "reclaim") return act(function () { return send("POST", API + "/tasks/" + id + "/reclaim", {}); });
      return act(function () { return send("DELETE", API + "/tasks/" + id); }, function () { p.onClose(); });
    }
    return h("aside", { className: "kb-panel", "aria-label": "Card: " + t.title },
      h("div", { className: "kb-panel-top" }, h("span", { className: "kb-stage kb-tone-" + (TONE[t.status] || "todo") }, t.status), h("span", { className: "kb-muted" }, t.id), closeBtn),
      edit ? h("div", { className: "kb-edit" },
        h("input", { className: "kb-input", value: edit.title, maxLength: 200, "aria-label": "Title", onChange: function (e) { setEdit({ title: e.target.value, body: edit.body }); } }),
        h("textarea", { className: "kb-input kb-ta", rows: 10, value: edit.body, maxLength: 20000, "aria-label": "Notes", onChange: function (e) { setEdit({ title: edit.title, body: e.target.value }); } }),
        h("div", { className: "kb-row" }, h("button", { type: "button", className: "kb-btn kb-primary", disabled: busy || !edit.title.trim(), onClick: function () { act(function () { return send("PATCH", API + "/tasks/" + id, { title: edit.title.trim(), body: edit.body }); }, function () { setEdit(null); }); } }, "Save"),
          h("button", { type: "button", className: "kb-btn", onClick: function () { setEdit(null); } }, "Cancel")))
        : h("div", null, h("h2", { className: "kb-title" }, t.title), t.body ? h(Notes, { text: t.body }) : h("p", { className: "kb-muted" }, "No notes yet.")),
      err ? h(window.__APP_UI__.kit.Alert, { tone: "danger" }, err) : null,
      h("div", { className: "kb-row kb-wrap" },
        h("label", { className: "kb-lab" }, "Move to ", h("select", { className: "kb-select", value: t.status, disabled: busy || t.status === "running", onChange: function (e) { move(e.target.value); } },
          cols.map(function (c) { return h("option", { key: c.key, value: c.key, disabled: !c.drop && c.key !== t.status }, c.label); }))),
        h("label", { className: "kb-lab" }, "Priority ", h("select", { className: "kb-select", value: String(t.priority || 0), disabled: busy, onChange: function (e) { act(function () { return send("PATCH", API + "/tasks/" + id, { priority: parseInt(e.target.value, 10) }); }); } },
          [0, 1, 2, 3, 4, 5].map(function (n) { return h("option", { key: n, value: String(n) }, n ? "P" + n : "None"); }))),
        t.assignee ? h("span", { className: "kb-chip" }, "Assigned: " + t.assignee) : h("span", { className: "kb-chip" }, "Unassigned"),
        t.workspace_kind === "scratch" || !t.workspace_kind ? h("span", { className: "kb-chip kb-warnchip", title: "Files the worker makes live in a temporary folder that is deleted when the card is done" }, "Temporary workspace")
          : h("span", { className: "kb-chip", title: t.workspace_path || "" }, "Kept in " + String(t.workspace_path || t.workspace_kind).split("/").pop())),
      confirm ? h("div", { className: "kb-confirm", role: "alertdialog" },
        h("p", null, WORDS[confirm][0]),
        confirm === "done" || confirm === "review" ? h("textarea", { className: "kb-input kb-ta", rows: 3, maxLength: 4000, value: summary, "aria-label": "Hand-off note", placeholder: "What was done, how it was checked, what is left…", onChange: function (e) { setSummary(e.target.value); } }) : null,
        h("div", { className: "kb-row" },
          h("button", { type: "button", className: "kb-btn kb-primary", disabled: busy, onClick: confirmed }, WORDS[confirm][1]),
          h("button", { type: "button", className: "kb-btn", onClick: function () { setConfirm(""); } }, "Cancel")))
        : h("div", { className: "kb-row kb-wrap" },
          resumable ? h("button", { type: "button", className: "kb-btn kb-primary", disabled: busy, onClick: function () { setConfirm("resume"); } }, "Resume") : null,
          t.status === "blocked" ? h("button", { type: "button", className: "kb-btn kb-primary", disabled: busy, onClick: function () { setConfirm("unblock"); } }, "Unblock") : null,
          canStart ? h("button", { type: "button", className: "kb-btn" + (resumable ? "" : " kb-primary"), disabled: busy, onClick: function () { setConfirm("start"); } }, (resumable ? "Re-specify with " : "Start with ") + p.agent) : null,
          finishable ? h("button", { type: "button", className: "kb-btn", disabled: busy, onClick: function () { setConfirm("done"); } }, "Mark done") : null,
          finishable && t.status !== "review" ? h("button", { type: "button", className: "kb-btn", disabled: busy, onClick: function () { setConfirm("review"); } }, "Send to review") : null,
          t.status === "running" ? h("button", { type: "button", className: "kb-btn", disabled: busy, onClick: function () { setConfirm("reclaim"); } }, "Take back") : null,
          h("button", { type: "button", className: "kb-btn", disabled: busy || !!edit, onClick: function () { setEdit({ title: t.title || "", body: t.body || "" }); } }, "Edit"),
          h("button", { type: "button", className: "kb-btn", onClick: function () { if (log) setLog(null); else fetchJSON(API + "/tasks/" + id + "/log").then(setLog, function (e) { setErr(errText(e)); }); } }, log ? "Hide log" : "Worker log"),
          h("button", { type: "button", className: "kb-btn kb-danger", disabled: busy || t.status === "running", onClick: function () { setConfirm("delete"); } }, "Delete")),
      log ? h("pre", { className: "kb-log" }, log.exists ? (log.content || "(empty)") : "No log for this card yet.") : null,
      t.result ? h("div", null, h("div", { className: "kb-label" }, "Result"), h("div", { className: "kb-body" }, typeof t.result === "string" ? t.result : JSON.stringify(t.result))) : null,
      data.runs && data.runs.length ? h("div", null, h("div", { className: "kb-label" }, "Runs (" + data.runs.length + ")"),
        h("ul", { className: "kb-list" }, data.runs.slice().reverse().map(function (r, i) {
          return h("li", { key: i }, (r.outcome || r.status || "run") + (r.started_at ? " · " + when(r.started_at) : "") + (r.summary ? " · " + String(r.summary).slice(0, 140) : ""));
        }))) : null,
      h("div", null, h("div", { className: "kb-label" }, "Comments (" + (data.comments ? data.comments.length : 0) + ")"),
        h("ul", { className: "kb-comments" }, (data.comments || []).map(function (c) {
          return h("li", { key: c.id, className: "kb-comment" }, h("div", { className: "kb-comment-h" }, h("strong", null, c.author || "someone"), h("span", { className: "kb-muted" }, " " + when(c.created_at))), h("div", { className: "kb-body" }, c.body));
        })),
        h("form", { className: "kb-row", onSubmit: function (e) { e.preventDefault(); var v = comment.trim(); if (v) act(function () { return send("POST", API + "/tasks/" + id + "/comments", { body: v }); }, function () { setComment(""); }); } },
          h("input", { className: "kb-input", style: { flex: 1 }, value: comment, maxLength: 5000, placeholder: "Add a comment…", "aria-label": "Add a comment", onChange: function (e) { setComment(e.target.value); } }),
          h("button", { type: "submit", className: "kb-btn", disabled: busy || !comment.trim() }, "Comment"))));
  }

  /* ---------- the page ---------- */
  function Page() {
    var cfg = UI.config(), agent = cfg.agentName || "the agent";
    var b_ = useState(null), board = b_[0], setBoard = b_[1];
    var e_ = useState(""), err = e_[0], setErr = e_[1];
    var o_ = useState(""), open = o_[0], setOpen = o_[1];
    var q_ = useState(""), query = q_[0], setQuery = q_[1];
    var as_ = useState(""), who = as_[0], setWho = as_[1];
    var ad_ = useState(false), adding = ad_[0], setAdding = ad_[1];
    var nt_ = useState(""), nTitle = nt_[0], setNTitle = nt_[1];
    var nb_ = useState(""), nBody = nb_[0], setNBody = nb_[1];
    var pj_ = useState([]), projects = pj_[0], setProjects = pj_[1], np_ = useState(""), nProject = np_[0], setNProject = np_[1];     // a coding project to keep the work in
    var dg_ = useState(""), dragId = dg_[0], setDragId = dg_[1];
    var ov_ = useState(""), over = ov_[0], setOver = ov_[1];
    var bz_ = useState(false), busy = bz_[0], setBusy = bz_[1];
    var alive = useRef(true);
    function load() { return fetchJSON(API + "/board").then(function (r) { if (alive.current) { setBoard(r); setErr(""); } }, function (e) { if (alive.current) setErr(errText(e)); }); }
    useEffect(function () { fetchJSON("/api/code/projects").then(function (r) { if (r && r.projects) setProjects(r.projects.map(function (x) { return x.name; })); }, function () { /* the coding workspace is optional */ }); }, []);
    useEffect(function () { alive.current = true; load(); return function () { alive.current = false; }; }, []);
    var anyRunning = board && board.columns.some(function (c) { return c.tasks.some(function (t) { return t.running; }); });
    useEffect(function () { return window.atriumEvery(load, anyRunning ? 7000 : 25000); }, [anyRunning]);
    useEffect(function () { function esc(e) { if (e.key === "Escape") setOpen(""); } window.addEventListener("keydown", esc); return function () { window.removeEventListener("keydown", esc); }; }, []);
    function add(ev) {
      ev.preventDefault();
      if (!nTitle.trim() || busy) return;
      setBusy(true);
      send("POST", API + "/tasks", { title: nTitle.trim(), body: nBody.trim() || null, triage: true, project: nProject || undefined }).then(function () { setBusy(false); setNTitle(""); setNBody(""); setNProject(""); setAdding(false); load(); }, function (e2) { setBusy(false); setErr(errText(e2)); });
    }
    function drop(col, ev) {
      ev.preventDefault(); setOver("");
      var id = ev.dataTransfer.getData("text/plain") || dragId; setDragId("");
      if (!id || !col.drop) return;
      var from = board.columns.filter(function (c) { return c.tasks.some(function (t) { return t.id === id; }); })[0];
      if (!from || from.key === col.key) return;
      send("PATCH", API + "/tasks/" + id, { status: col.key }).then(load, function (e2) { setErr(errText(e2)); load(); });
    }
    if (!board) return h("div", { className: "kb-page" }, err ? h(window.__APP_UI__.kit.Alert, { tone: "danger", title: "Could not load the board" }, err) : h(window.__APP_UI__.kit.Loading, { label: "Loading the board…" }));
    var ql = query.trim().toLowerCase();
    function shown(t) { return (!who || (who === "-" ? !t.assignee : t.assignee === who)) && (!ql || (String(t.title || "") + " " + String(t.preview || "")).toLowerCase().indexOf(ql) >= 0); }
    var total = 0, running = 0;
    board.columns.forEach(function (c) { c.tasks.forEach(function (t) { total++; if (t.running) running++; }); });
    var cols = board.columns.filter(function (c) { return c.tasks.length || c.key !== "scheduled"; });
    return h("div", { className: "kb-page" },
      h("header", { className: "kb-head" },
        h("div", null, h("h1", { className: "kb-h1" }, "Kanban"), h("p", { className: "kb-muted" }, total + " card" + (total === 1 ? "" : "s") + (running ? " · " + running + " running" : "") + (board.auto_decompose ? " · auto-start is ON" : " · nothing starts by itself"))),
        h("div", { className: "kb-tools" },
          h("input", { className: "kb-input", type: "search", value: query, placeholder: "Search cards…", "aria-label": "Search cards", onChange: function (e) { setQuery(e.target.value); } }),
          h("select", { className: "kb-select", value: who, "aria-label": "Filter by assignee", onChange: function (e) { setWho(e.target.value); } },
            h("option", { value: "" }, "Everyone"), h("option", { value: "-" }, "Unassigned"), board.assignees.map(function (a) { return h("option", { key: a, value: a }, a); })),
          h("button", { type: "button", className: "kb-btn", onClick: load }, "Refresh"),
          h("button", { type: "button", className: "kb-btn kb-primary", onClick: function () { setAdding(!adding); } }, adding ? "Cancel" : "+ Add a card"))),
      adding ? h("form", { className: "kb-add", onSubmit: add },
        h("input", { className: "kb-input", value: nTitle, maxLength: 200, autoFocus: true, placeholder: "The idea or task, in a line", "aria-label": "Title", onChange: function (e) { setNTitle(e.target.value); } }),
        h("textarea", { className: "kb-input kb-ta", rows: 4, value: nBody, maxLength: 20000, placeholder: "Notes (optional): what you want, why, links…", "aria-label": "Notes", onChange: function (e) { setNBody(e.target.value); } }),
        projects.length ? h("label", { className: "kb-lab" }, "Where the work goes ", h("select", { className: "kb-select", value: nProject, onChange: function (e) { setNProject(e.target.value); } },
          h("option", { value: "" }, "A temporary folder (deleted when the card is done)"), projects.map(function (n) { return h("option", { key: n, value: n }, "Coding project: " + n + " (kept)"); }))) : null,
        h("div", { className: "kb-row" }, h("button", { type: "submit", className: "kb-btn kb-primary", disabled: busy || !nTitle.trim() }, "Add to the board"),
          h("span", { className: "kb-muted" }, "It waits in " + (board.columns[0] ? board.columns[0].label : "the first column") + ". Nobody is assigned, so nothing starts until you say so."))) : null,
      err ? h(window.__APP_UI__.kit.Alert, { tone: "danger" }, err) : null,
      h("div", { className: "kb-layout" + (open ? " kb-with-panel" : "") },
        h("div", { className: "kb-board", role: "list" }, cols.map(function (c) {
          var list = c.tasks.filter(shown);
          return h("section", { key: c.key, role: "listitem", className: "kb-col kb-tone-" + (TONE[c.key] || "todo") + (over === c.key && c.drop ? " kb-over" : ""), "aria-label": c.label,
            onDragOver: function (e) { if (dragId && c.drop) { e.preventDefault(); setOver(c.key); } }, onDragLeave: function (e) { if (!e.currentTarget.contains(e.relatedTarget)) setOver(""); }, onDrop: function (e) { drop(c, e); } },
            h("header", { className: "kb-col-h" }, h("span", { className: "kb-col-i", "aria-hidden": "true" }, c.icon), h("h2", { className: "kb-col-t" }, c.label), h("span", { className: "kb-count" }, list.length === c.tasks.length ? c.tasks.length : list.length + "/" + c.tasks.length)),
            h("div", { className: "kb-col-b" }, list.length ? list.map(function (t) { return h(Card, { key: t.id, t: t, canDrag: c.drop && !t.running, dragging: dragId === t.id, onOpen: setOpen, onDrag: setDragId }); }) : h("p", { className: "kb-empty" }, c.tasks.length ? "No match." : c.key === "triage" ? "Ideas wait here." : "Empty.")));
        })),
        open ? h(Panel, { id: open, cols: board.columns, agent: agent, onClose: function () { setOpen(""); }, onChanged: load }) : null));
  }

  REG.register("kanban-page", Page);
})();
