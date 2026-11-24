/* REA: upload a program or app and run read-only analyses in the sealed REA workbench. Uploaded programs are only read, never run.
   Talks to the app only through /api/rea (see app/rea_backend.py and rea-sidecar/). Owners only. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__, UI = window.__APP_UI__;
  if (!SDK || !REG || !UI) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect, useRef = SDK.hooks.useRef;
  var API = "/api/rea", JSONH = { "Content-Type": "application/json" };

  function errText(e) { var m = String((e && e.message) || e || "Something went wrong"), i = m.indexOf("{"); if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* plain */ } } return m.replace(/^\d+:\s*/, ""); }
  function size(n) { if (n === null || n === undefined) return ""; if (n < 1024) return n + " B"; if (n < 1048576) return (n / 1024).toFixed(0) + " KB"; if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB"; return (n / 1073741824).toFixed(2) + " GB"; }
  function when(t) { var d = new Date(t * 1000); return isNaN(d) ? "" : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); }
  var GAME = "quick-game";
  var STATUS = { queued: "Waiting", running: "Running…", done: "Done", failed: "Failed" };

  /* A result shown as a tree of names, kinds and counts (the full result is the download). */
  function Node(p) {
    var o = p.node, a = useState(p.depth < 1), open = a[0], setOpen = a[1];
    if (!o) return null;
    var label = p.name !== undefined ? h("strong", null, p.name) : null;
    if (o.type === "object") {
      var keys = Object.keys(o.items || {});
      return h("div", { className: "rea-node" }, h("button", { type: "button", className: "rea-tog", "aria-expanded": open ? "true" : "false", onClick: function () { setOpen(!open); } }, (open ? "▾ " : "▸ "), label, " ", h("span", { className: "hm-fine" }, "{" + o.keys + " fields}")),
        open ? h("div", { className: "rea-kids" }, keys.map(function (k) { return h(Node, { key: k, name: k, node: o.items[k], depth: p.depth + 1 }); }), o.more ? h("p", { className: "hm-fine" }, "…and " + o.more + " more fields in the download.") : null) : null);
    }
    if (o.type === "list") {
      return h("div", { className: "rea-node" }, h("button", { type: "button", className: "rea-tog", "aria-expanded": open ? "true" : "false", onClick: function () { setOpen(!open); } }, (open ? "▾ " : "▸ "), label, " ", h("span", { className: "hm-fine" }, "[" + o.length + " items]")),
        open && o.sample && o.sample.length ? h("div", { className: "rea-kids" }, o.sample.map(function (s, i) { return h(Node, { key: i, name: "#" + (i + 1), node: s, depth: p.depth + 1 }); }), o.length > o.sample.length ? h("p", { className: "hm-fine" }, "Showing the first " + o.sample.length + " of " + o.length + ". The download has all of them.") : null) : null);
    }
    return h("div", { className: "rea-leaf" }, label, label ? ": " : "", h("span", { className: "rea-val" }, o.type === "text" ? o.value + (o.length > 200 ? "…" : "") : String(o.value)));
  }

  function Result(p) {
    var K = UI.kit, j = p.job, st = K.useAsync(function () { return j.status === "done" ? SDK.fetchJSON(API + "/jobs/" + j.id + "/summary") : Promise.resolve(null); }, [j.id, j.status]);
    var a = useState("outline"), view = a[0], setView = a[1];
    var s = st.data;
    return h("div", { className: "hm-card rea-result" },
      h("div", { className: "rea-top" }, h("div", null, h("h2", null, j.label), h("p", { className: "hm-fine" }, (j.file_name ? j.file_name + " · " : "") + (j.arg ? j.arg + " · " : "") + when(j.created))),
        h("div", { className: "rea-actions" }, j.status === "done" ? h("a", { className: "hm-btn", href: API + "/jobs/" + j.id + "/download" }, "Download (" + size(j.size) + ")") : null,
          h(K.Button, { variant: "ghost", onClick: p.onClose }, "Close"))),
      j.status === "failed" ? h(K.Alert, { tone: "danger", title: "That did not finish" }, (j.note || "") + (j.error ? "\n" + j.error : "")) : null,
      j.status === "queued" || j.status === "running" ? h(K.Alert, { tone: "info" }, j.status === "queued" ? "Waiting for its turn." : "Running. Deep analysis of a large program can take many minutes; you can leave this page and come back.") : null,
      j.status === "done" ? h(K.AsyncView, { state: st, skeleton: h(K.Skeleton, { lines: 5 }), isEmpty: function (d) { return !d; } }, function (d) {
        return h("div", null, h("div", { className: "rea-tabs", role: "group", "aria-label": "How to read the result" },
          h(K.Button, { variant: view === "outline" ? "primary" : "secondary", size: "sm", onClick: function () { setView("outline"); } }, "Outline"),
          h(K.Button, { variant: view === "raw" ? "primary" : "secondary", size: "sm", onClick: function () { setView("raw"); } }, "Raw start")),
          view === "outline" ? (d.outline ? h("div", { className: "rea-tree" }, h(Node, { node: d.outline, depth: 0 })) : h("p", { className: "hm-sub" }, "This result is too large to outline here (" + size(d.size) + "). Use the download, or the raw start.")) :
            h("pre", { className: "rea-raw" }, d.preview + (d.truncated ? "\n…" : "")));
      }) : null,
      p.onDelete ? h("p", null, h(K.Button, { variant: "danger", size: "sm", onClick: p.onDelete }, "Delete this result")) : null);
  }

  function Page() {
    var K = UI.kit;
    var a = useState(null), commands = a[0], setCommands = a[1], b = useState([]), files = b[0], setFiles = b[1], c = useState([]), jobs = c[0], setJobs = c[1];
    var d = useState(""), err = d[0], setErr = d[1], e = useState(null), prog = e[0], setProg = e[1], f = useState(false), unpack = f[0], setUnpack = f[1];
    var g = useState({ file: "", command: "", arg: "" }), form = g[0], setForm = g[1], k = useState(false), busy = k[0], setBusy = k[1], m = useState(""), openJob = m[0], setOpenJob = m[1];
    var n = useState(true), up = n[0], setUp = n[1], inp = useRef(null);

    function loadFiles() { return SDK.fetchJSON(API + "/files").then(function (r) { setFiles(r.files || []); }, function (x) { setErr(errText(x)); }); }
    function loadJobs() { return SDK.fetchJSON(API + "/jobs").then(function (r) { setJobs(r.jobs || []); return r.jobs || []; }, function () { return []; }); }
    useEffect(function () {
      SDK.fetchJSON(API + "/status").then(function (r) { setUp(!!r.up); if (!r.up) return; return SDK.fetchJSON(API + "/commands").then(function (r2) { setCommands(r2.commands || []); loadFiles(); loadJobs(); }); }, function (x) { setErr(errText(x)); setCommands([]); });
    }, []);
    var active = jobs.some(function (j) { return j.status === "queued" || j.status === "running"; });
    useEffect(function () { if (!active) return; var t = setInterval(loadJobs, 3000); return function () { clearInterval(t); }; }, [active]);

    function set(key, v) { var next = Object.assign({}, form); next[key] = v; setForm(next); }
    function upload(file) {
      if (!file) return;
      setErr(""); setProg({ name: file.name, pct: 0 });
      var xhr = new XMLHttpRequest();
      xhr.open("POST", API + "/files?name=" + encodeURIComponent(file.name) + (unpack && /\.zip$/i.test(file.name) ? "&unpack=1" : ""));
      xhr.upload.onprogress = function (ev) { if (ev.lengthComputable) setProg({ name: file.name, pct: Math.round(100 * ev.loaded / ev.total) }); };
      xhr.onload = function () {
        setProg(null);
        if (xhr.status >= 300) { var msg = "Could not upload " + file.name + "."; try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (x) { /* plain */ } setErr(msg); return; }
        try { var r = JSON.parse(xhr.responseText); if (r.file) set("file", r.file.id); } catch (x) { /* optional */ }
        loadFiles();
      };
      xhr.onerror = function () { setProg(null); setErr("Could not upload " + file.name + "."); };
      xhr.send(file);
    }
    function post(command, file, arg) { return SDK.fetchJSON(API + "/jobs", { method: "POST", headers: JSONH, body: JSON.stringify({ command: command, file: file, arg: arg }) }); }
    function run(ev) {
      ev.preventDefault(); setBusy(true); setErr("");
      if (form.command === GAME) {
        /* A video game: line up the usual read-only checks for the chosen file; they run one after another. */
        var f2 = files.filter(function (x) { return x.id === form.file; })[0], name = f2 ? f2.name : "", have = {};
        (commands || []).forEach(function (x) { have[x.id] = true; });
        var steps = (/^Assembly-.*\.dll$/i.test(name) ? ["managed", "managed-members"] : ["pe-imports", "pe-certs", "ghidra"]).filter(function (id) { return have[id]; });
        var first = "";
        steps.reduce(function (p, id) { return p.then(function () { return post(id, form.file, "").then(function (r) { if (!first) first = r.job.id; }); }); }, Promise.resolve())
          .then(function () { setBusy(false); if (first) setOpenJob(first); return loadJobs(); }, function (x) { setBusy(false); setErr(errText(x)); return loadJobs(); });
        return;
      }
      post(form.command, form.file, form.arg)
        .then(function (r) { setBusy(false); setOpenJob(r.job.id); return loadJobs(); }, function (x) { setBusy(false); setErr(errText(x)); });
    }
    function removeFile(f2) { if (window.confirm("Delete " + f2.name + " from the workbench?")) SDK.fetchJSON(API + "/files/" + f2.id, { method: "DELETE" }).then(function () { if (form.file === f2.id) set("file", ""); loadFiles(); }, function (x) { setErr(errText(x)); }); }
    function removeJob(j) { SDK.fetchJSON(API + "/jobs/" + j.id, { method: "DELETE" }).then(function () { setOpenJob(""); loadJobs(); }, function (x) { setErr(errText(x)); }); }

    if (!up) return h("div", { className: "rea" }, h("h1", null, "REA"), h(K.Alert, { tone: "warning", title: "The REA workbench is not running" }, "Ask whoever runs this server to start it (see rea-sidecar/ in the Atrium files)."));
    if (!commands) return h("div", { className: "rea" }, h(K.Loading, { label: "Loading…" }));
    var chosen = form.command === GAME ? { id: GAME, target: "file", arg_label: "", arg_optional: true, help: "Upload the game's main program (the .exe), or for a Unity game its Assembly-CSharp.dll (in the _Data/Managed folder), not the whole install. This lines up the usual checks one after another: the libraries it uses, its signature and a Ghidra overview, or for Assembly-CSharp.dll the readable .NET overview and a list of its types and methods. The results appear under Results as each finishes." } : commands.filter(function (x) { return x.id === form.command; })[0];
    var groups = [];
    commands.forEach(function (x) { var gr = groups.filter(function (y) { return y.name === x.group; })[0]; if (!gr) { gr = { name: x.group, items: [] }; groups.push(gr); } gr.items.push(x); });
    var needsFile = chosen && chosen.target !== "none";
    var job = jobs.filter(function (j) { return j.id === openJob; })[0];
    return h("div", { className: "rea" }, h("h1", null, "REA"),
      h("p", { className: "hm-sub" }, "Reverse-engineer programs and apps you own or are allowed to examine. Uploads are only read, never run, inside a sealed container with no internet. Files and results are deleted after a day."),
      err ? h(K.Alert, { tone: "danger", onDismiss: function () { setErr(""); } }, err) : null,
      h("div", { className: "rea-grid" },
        h("div", { className: "hm-card" }, h("h2", null, "1. Upload"),
          h("label", { className: "rea-check" }, h("input", { type: "checkbox", checked: unpack, onChange: function (x) { setUnpack(x.target.checked); } }), " A .zip of an app folder: unpack it"),
          h("input", { ref: inp, type: "file", className: "rea-file", "aria-label": "Choose a file to upload", onChange: function (x) { upload(x.target.files[0]); x.target.value = ""; } }),
          prog ? h("p", { className: "hm-fine", role: "status" }, "Uploading " + prog.name + " " + prog.pct + "%") : null,
          h("ul", { className: "rea-files" }, files.map(function (f2) {
            return h("li", { key: f2.id }, h("label", null, h("input", { type: "radio", name: "rea-file", checked: form.file === f2.id, onChange: function () { set("file", f2.id); } }), " " + (f2.kind === "folder" ? "📁 " : "📄 ") + f2.name, h("span", { className: "hm-fine" }, " · " + size(f2.size))),
              h("button", { type: "button", className: "hm-btn hm-btn-ghost", "aria-label": "Delete " + f2.name, onClick: function () { removeFile(f2); } }, "✕"));
          })), !files.length && !prog ? h("p", { className: "hm-fine" }, "Nothing uploaded yet.") : null),
        h("form", { className: "hm-card rea-run", onSubmit: run }, h("h2", null, "2. Analyze"),
          h(K.Field, { label: "What to find out" }, function (ip) {
            return h("select", Object.assign({}, ip, { className: "hm-select", value: form.command, onChange: function (x) { set("command", x.target.value); } }), h("option", { value: "" }, "Choose…"), h("option", { value: GAME }, "🎮 Video game (runs the usual checks)"),
              groups.map(function (gr) { return h("optgroup", { key: gr.name, label: gr.name }, gr.items.map(function (x) { return h("option", { key: x.id, value: x.id }, x.label); })); }));
          }),
          chosen ? h("p", { className: "hm-fine" }, chosen.help) : null,
          chosen && chosen.arg_label ? h(K.Field, { label: chosen.arg_label, required: !chosen.arg_optional }, function (ip) { return h("input", Object.assign({}, ip, { className: "hm-input", maxLength: 200, value: form.arg, onChange: function (x) { set("arg", x.target.value); } })); }) : null,
          h(K.Button, { type: "submit", variant: "primary", loading: busy, disabled: !chosen || (needsFile && !form.file) }, "Run"),
          chosen && needsFile && !form.file ? h("p", { className: "hm-fine" }, "Upload or choose a file first.") : null)),
      job ? h(Result, { key: job.id + job.status, job: job, onClose: function () { setOpenJob(""); }, onDelete: job.status === "running" || job.status === "queued" ? null : function () { removeJob(job); } }) : null,
      h("div", { className: "hm-card" }, h("h2", null, "Results"), jobs.length ? h("ul", { className: "rea-jobs" }, jobs.map(function (j) {
        return h("li", { key: j.id }, h("button", { type: "button", className: "rea-job", onClick: function () { setOpenJob(j.id); } }, h("span", { className: "rea-st rea-" + j.status }, STATUS[j.status] || j.status), " " + j.label,
          h("span", { className: "hm-fine" }, (j.file_name ? " · " + j.file_name : "") + " · " + when(j.created))));
      })) : h("p", { className: "hm-fine" }, "Nothing run yet.")));
  }
  REG.register("rea-page", Page);
})();
