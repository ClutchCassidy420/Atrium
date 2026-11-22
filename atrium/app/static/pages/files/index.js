/* Files: browse, upload, download, rename, move and delete the files in your Nextcloud. Deleted files go to Nextcloud's own trash, so they can be
   brought back there. Talks to the app only through /api/nextcloud (see app/nextcloud_backend.py). */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__, UI = window.__APP_UI__;
  if (!SDK || !REG || !UI) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect, useRef = SDK.hooks.useRef;
  var API = "/api/nextcloud", JSONH = { "Content-Type": "application/json" };

  function errText(e) { var m = String((e && e.message) || e || "Something went wrong"), i = m.indexOf("{"); if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* plain */ } } return m.replace(/^\d+:\s*/, ""); }
  function size(n) { if (n === null || n === undefined) return ""; if (n < 1024) return n + " B"; if (n < 1048576) return (n / 1024).toFixed(0) + " KB"; if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB"; return (n / 1073741824).toFixed(2) + " GB"; }
  function icon(it) {
    if (it.folder) return "📁";
    var t = it.type || "", n = it.name.toLowerCase();
    if (/^image\//.test(t)) return "🖼️"; if (/^video\//.test(t)) return "🎞️"; if (/^audio\//.test(t)) return "🎵";
    if (/pdf/.test(t)) return "📕"; if (/zip|compressed|tar|7z/.test(t) || /\.(zip|tar|gz|7z|rar)$/.test(n)) return "🗜️";
    if (/\.(docx?|odt|rtf)$/.test(n)) return "📄"; if (/\.(xlsx?|ods|csv)$/.test(n)) return "📊"; if (/\.(pptx?|odp)$/.test(n)) return "📽️";
    if (/^text\//.test(t) || /\.(md|txt|json|py|js|html|css|yml|yaml|sh)$/.test(n)) return "📝";
    return "📃";
  }
  function when(s) { var d = new Date(s); return isNaN(d) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" }) + " " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }); }
  function parent(p) { var i = p.lastIndexOf("/"); return i < 0 ? "" : p.slice(0, i); }

  function Page() {
    var a = useState(function () { try { return decodeURIComponent((location.hash || "").slice(1)); } catch (x) { return ""; } }), path = a[0], setPath = a[1];
    var b = useState(null), data = b[0], setData = b[1], c = useState(""), err = c[0], setErr = c[1];
    var d = useState(""), note = d[0], setNote = d[1], e = useState(null), editing = e[0], setEditing = e[1];
    var f = useState(""), newName = f[0], setNewName = f[1], g = useState(null), confirm = g[0], setConfirm = g[1];
    var hv = useState(false), over = hv[0], setOver = hv[1], up = useState(null), prog = up[0], setProg = up[1];
    var n = useState(0), tick = n[0], setTick = n[1], inp = useRef(null);

    useEffect(function () {
      var live = true;
      setErr("");
      SDK.fetchJSON(API + "/files?path=" + encodeURIComponent(path)).then(function (r) { if (live) setData(r); }, function (x) { if (live) { setErr(errText(x)); } });
      try { history.replaceState(null, "", path ? "#" + encodeURIComponent(path) : location.pathname); } catch (x) { /* optional */ }
      return function () { live = false; };
    }, [path, tick]);

    function refresh() { setTick(tick + 1); }
    function call(method, url, body) { setNote(""); return SDK.fetchJSON(API + url, { method: method, headers: JSONH, body: body ? JSON.stringify(body) : undefined }).then(function () { refresh(); return true; }, function (x) { setErr(errText(x)); return false; }); }
    function uploadFiles(files) {
      var list = Array.prototype.slice.call(files || []);
      if (!list.length) return;
      var i = 0;
      (function next(file, replace) {
        if (!file) { if (i >= list.length) { setProg(null); refresh(); return; } file = list[i++]; }
        var xhr = new XMLHttpRequest();
        xhr.open("POST", API + "/files/upload?path=" + encodeURIComponent(path) + "&name=" + encodeURIComponent(file.name) + (replace ? "&replace=1" : ""));
        xhr.upload.onprogress = function (ev) { if (ev.lengthComputable) setProg({ name: file.name, pct: Math.round(100 * ev.loaded / ev.total), n: i, of: list.length }); };
        xhr.onload = function () {
          if (xhr.status === 409 && !replace) {
            if (window.confirm(file.name + " is already in this folder. Replace it?\n\nNextcloud keeps the old version in its file history.")) { next(file, true); return; }
            setProg(null); refresh(); return;
          }
          if (xhr.status >= 300) { var m = "Could not upload " + file.name + "."; try { m = JSON.parse(xhr.responseText).detail || m; } catch (x) { /* plain */ } setErr(m); setProg(null); refresh(); } else next();
        };
        xhr.onerror = function () { setErr("Could not upload " + file.name + "."); setProg(null); };
        setProg({ name: file.name, pct: 0, n: i, of: list.length });
        xhr.send(file);
      })();
    }
    function onDrop(ev) { ev.preventDefault(); setOver(false); uploadFiles(ev.dataTransfer && ev.dataTransfer.files); }
    function mkdir(ev) { ev.preventDefault(); var v = newName.trim(); if (!v || /\//.test(v)) return; call("POST", "/files/mkdir", { path: (path ? path + "/" : "") + v }).then(function (ok) { if (ok) setNewName(""); }); }
    function rename(it, ev) { ev.preventDefault(); var v = editing.value.trim(); if (!v || /\//.test(v) || v === it.name) { setEditing(null); return; } call("POST", "/files/move", { path: it.path, to: (parent(it.path) ? parent(it.path) + "/" : "") + v }).then(function () { setEditing(null); }); }

    var crumbs = [{ name: "All files", path: "" }];
    path.split("/").filter(Boolean).forEach(function (seg, i, arr) { crumbs.push({ name: seg, path: arr.slice(0, i + 1).join("/") }); });
    if (err && !data) return h("div", { className: "fl" }, h("h1", null, "Files"), h("p", { className: "hm-err" }, err), h("p", { className: "hm-sub" }, "If Nextcloud is not connected to this app yet, add its address and an app password to the app's private config."));

    return h("div", { className: "fl" + (over ? " fl-over" : ""), onDragOver: function (ev) { ev.preventDefault(); setOver(true); }, onDragLeave: function (ev) { if (ev.currentTarget === ev.target) setOver(false); }, onDrop: onDrop },
      h("header", { className: "fl-top" },
        h("h1", null, "Files"),
        h("div", { className: "fl-tools" },
          h("form", { className: "fl-new", onSubmit: mkdir }, h("input", { className: "hm-input", placeholder: "New folder", value: newName, maxLength: 120, "aria-label": "New folder name", onChange: function (x) { setNewName(x.target.value); } }), h("button", { type: "submit", className: "hm-btn hm-btn-ghost", disabled: !newName.trim() }, "Create")),
          h("input", { type: "file", multiple: true, hidden: true, ref: inp, onChange: function (x) { uploadFiles(x.target.files); x.target.value = ""; } }),
          h("button", { type: "button", className: "hm-btn", onClick: function () { inp.current && inp.current.click(); } }, "⬆ Upload"),
          data && data.url ? h("a", { className: "hm-btn hm-btn-ghost fl-open", href: data.url.replace(/\/$/, "") + "/apps/files/?dir=/" + path.split("/").map(encodeURIComponent).join("/"), target: "_blank", rel: "noopener noreferrer" }, "Open in Nextcloud ↗") : null)),
      h("nav", { className: "fl-crumbs", "aria-label": "Folder path" }, crumbs.map(function (cr, i) {
        return h(SDK.React.Fragment, { key: cr.path }, i ? h("span", { className: "fl-sep" }, "›") : null, i === crumbs.length - 1 ? h("strong", null, cr.name) : h("button", { type: "button", className: "fl-crumb", onClick: function () { setPath(cr.path); } }, cr.name));
      })),
      err ? h("p", { className: "hm-err", role: "alert" }, err) : null,
      prog ? h("div", { className: "fl-prog", role: "status" }, h("span", null, "Uploading " + prog.name + (prog.of > 1 ? " (" + prog.n + " of " + prog.of + ")" : "")), h("div", { className: "hm-bar" }, h("div", { className: "hm-bar-fill", style: { width: prog.pct + "%", animation: "none" } })), h("b", null, prog.pct + "%")) : null,
      h("div", { className: "hm-card fl-card" },
        !data ? h("p", { className: "hm-empty" }, "Loading…") : h("div", { className: "fl-list", role: "table" },
          h("div", { className: "fl-row fl-hd", role: "row" }, h("span", null, "Name"), h("span", { className: "fl-sz" }, "Size"), h("span", { className: "fl-mod" }, "Modified"), h("span", null)),
          path ? h("div", { className: "fl-row fl-up", role: "row" }, h("button", { type: "button", className: "fl-name", onClick: function () { setPath(parent(path)); } }, h("i", null, "↩"), "Up one folder")) : null,
          data.items.length ? data.items.map(function (it) {
            var isEdit = editing && editing.path === it.path, isDel = confirm === it.path;
            return h("div", { key: it.path, className: "fl-row", role: "row" },
              isEdit ? h("form", { className: "fl-edit", onSubmit: function (ev) { rename(it, ev); } }, h("input", { className: "hm-input", autoFocus: true, value: editing.value, maxLength: 200, onChange: function (x) { setEditing({ path: it.path, value: x.target.value }); } }),
                h("button", { type: "submit", className: "hm-btn" }, "Save"), h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: function () { setEditing(null); } }, "Cancel"))
                : it.folder ? h("button", { type: "button", className: "fl-name", onClick: function () { setPath(it.path); } }, h("i", null, icon(it)), it.name)
                  : h("a", { className: "fl-name", href: API + "/files/download?path=" + encodeURIComponent(it.path), download: it.name }, h("i", null, icon(it)), it.name),
              h("span", { className: "fl-sz" }, it.folder ? "" : size(it.size)), h("span", { className: "fl-mod" }, when(it.modified)),
              h("span", { className: "fl-act" }, isDel
                ? [h("button", { key: "y", type: "button", className: "hm-btn fl-danger", onClick: function () { setConfirm(null); call("DELETE", "/files?path=" + encodeURIComponent(it.path)); } }, "Delete"), h("button", { key: "n", type: "button", className: "hm-btn hm-btn-ghost", onClick: function () { setConfirm(null); } }, "Keep")]
                : [h("button", { key: "r", type: "button", className: "fl-ico", title: "Rename", "aria-label": "Rename " + it.name, onClick: function () { setEditing({ path: it.path, value: it.name }); } }, "✏️"),
                  h("button", { key: "d", type: "button", className: "fl-ico", title: "Delete", "aria-label": "Delete " + it.name, onClick: function () { setConfirm(it.path); } }, "🗑️")]));
          }) : h("p", { className: "hm-empty fl-empty" }, "This folder is empty. Drop files here or press Upload."))),
      note ? h("p", { className: "hm-fine" }, note) : h("p", { className: "hm-fine" }, "Deleted files go to Nextcloud's trash, where they can be restored. Drop files anywhere on this page to upload."));
  }

  REG.register("files-page", Page);
})();
