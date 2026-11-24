/* Email: a real mailbox in the page. A list of messages (search, unread, star, attachments), a reader, and a composer for new mail, replies and
   forwards. Mail is cleaned on the server and HTML mail is shown in a sandboxed frame with remote images blocked.
   Talks to the app only through /api/mail (see app/mail_backend.py). The same script serves several menu entries (Inbox, Sent, Drafts, Spam,
   Trash, Write): each registers its own page name and opens on its folder. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__, UI = window.__APP_UI__;
  if (!SDK || !REG || !UI) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect, useRef = SDK.hooks.useRef;
  var API = "/api/mail", JSONH = { "Content-Type": "application/json" };
  var cur = "";      // which of the person's mailboxes the page works on ("" = the default one); every mail request carries it
  function U(path) { return API + path + (cur ? (path.indexOf("?") < 0 ? "?" : "&") + "account=" + encodeURIComponent(cur) : ""); }

  function errText(e) { var m = String((e && e.message) || e || "Something went wrong"), i = m.indexOf("{"); if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* plain */ } } return m.replace(/^\d+:\s*/, ""); }
  function who(a) { return a ? (a.name || a.email || "") : ""; }
  function size(n) { return n < 1024 ? n + " B" : n < 1048576 ? Math.round(n / 1024) + " KB" : (n / 1048576).toFixed(1) + " MB"; }
  function short(iso) {
    if (!iso) return "";
    var d = new Date(iso), n = new Date();
    if (d.toDateString() === n.toDateString()) return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    if (d.getFullYear() === n.getFullYear()) return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }
  function full(iso) { return iso ? new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }) : ""; }
  function initial(a) { var s = who(a).replace(/[^A-Za-z0-9]/g, ""); return (s[0] || "?").toUpperCase(); }
  function hue(s) { var n = 0; s = String(s || ""); for (var i = 0; i < s.length; i++) n = (n * 31 + s.charCodeAt(i)) % 360; return n; }
  function addrLine(list) { return (list || []).map(function (a) { return a.name ? a.name + " <" + a.email + ">" : a.email; }).join(", "); }
  function quote(msg) { var t = (msg.text || "").split(/\r?\n/).map(function (l) { return "> " + l; }).join("\n"); return "\n\nOn " + full(msg.date) + ", " + (who(msg.from)) + " wrote:\n" + t; }

  var FRAME_CSS = "<base target=\"_blank\"><style>html,body{margin:0}body{font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif;color:#1b1f2a;background:#fff;padding:14px;word-wrap:break-word;overflow-wrap:anywhere}img{max-width:100%;height:auto}a{color:#2563eb}table{max-width:100%}blockquote{margin:8px 0;padding-left:12px;border-left:3px solid #cbd5e1;color:#475569}pre{white-space:pre-wrap}[data-blocked-src]{display:inline-block;min-width:40px;min-height:24px;background:#eef1f6;border:1px dashed #b6bfcc}</style>";

  function fileToB64(file) {
    return new Promise(function (res, rej) { var r = new FileReader(); r.onload = function () { res(String(r.result).split(",")[1] || ""); }; r.onerror = rej; r.readAsDataURL(file); });
  }

  function Composer(p) {
    var init = p.draft || {};
    var a = useState({ to: init.to || "", cc: init.cc || "", bcc: "", subject: init.subject || "", body: init.body || "" }), f = a[0], setF = a[1];
    var b = useState(!!init.cc), showCc = b[0], setShowCc = b[1], c = useState([]), files = c[0], setFiles = c[1];
    var d = useState(""), msg = d[0], setMsg = d[1], e = useState(false), busy = e[0], setBusy = e[1], inp = useRef(null);
    var subId = useRef(null);
    if (!subId.current) subId.current = (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : "xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx".replace(/x/g, function () { return Math.floor(Math.random() * 16).toString(16); });
    function set(k, v) { var o = {}; o[k] = v; setF(Object.assign({}, f, o)); }
    function pick(list) { setFiles(files.concat(Array.prototype.slice.call(list || []))); }
    function send(ev) {
      ev.preventDefault();
      if (!f.to.trim()) { setMsg("Add a recipient."); return; }
      if (!f.subject.trim() && !window.confirm("Send without a subject?")) return;
      setBusy(true); setMsg("");
      Promise.all(files.map(function (fl) { return fileToB64(fl).then(function (data) { return { name: fl.name, type: fl.type || "application/octet-stream", data: data }; }); })).then(function (atts) {
        return SDK.fetchJSON(U("/send"), { method: "POST", headers: JSONH, body: JSON.stringify({ to: f.to, cc: f.cc, bcc: f.bcc, subject: f.subject, body: f.body, attachments: atts, submission_id: subId.current, reply_uid: init.reply_uid || null, reply_folder: init.reply_folder || "" }) });
      }).then(function () { p.onSent(); }, function (x) { setBusy(false); setMsg(errText(x)); });
    }
    return h("div", { className: "ml-scrim", onMouseDown: function (x) { if (x.target === x.currentTarget && !busy) p.onClose(); } },
      h("form", { className: "ml-compose", onSubmit: send, role: "dialog", "aria-label": "New message" },
        h("div", { className: "ml-ch" }, h("strong", null, init.title || "New message"), h("button", { type: "button", className: "ml-x", "aria-label": "Close", onClick: p.onClose }, "×")),
        h("label", { className: "ml-fl" }, h("span", null, "To"), h("input", { className: "hm-input", value: f.to, autoFocus: !init.to, placeholder: "name@example.com, another@example.com", onChange: function (x) { set("to", x.target.value); } }),
          showCc ? null : h("button", { type: "button", className: "ml-link", onClick: function () { setShowCc(true); } }, "Cc / Bcc")),
        showCc ? h("label", { className: "ml-fl" }, h("span", null, "Cc"), h("input", { className: "hm-input", value: f.cc, onChange: function (x) { set("cc", x.target.value); } })) : null,
        showCc ? h("label", { className: "ml-fl" }, h("span", null, "Bcc"), h("input", { className: "hm-input", value: f.bcc, onChange: function (x) { set("bcc", x.target.value); } })) : null,
        h("label", { className: "ml-fl" }, h("span", null, "Subject"), h("input", { className: "hm-input", value: f.subject, onChange: function (x) { set("subject", x.target.value); } })),
        h("textarea", { className: "hm-textarea ml-body", rows: 14, value: f.body, autoFocus: !!init.to, placeholder: "Write your message…", onChange: function (x) { set("body", x.target.value); } }),
        files.length ? h("div", { className: "ml-files" }, files.map(function (fl, i) { return h("span", { key: i, className: "ml-file" }, "📎 " + fl.name + " (" + size(fl.size) + ")", h("button", { type: "button", "aria-label": "Remove " + fl.name, onClick: function () { setFiles(files.filter(function (_, j) { return j !== i; })); } }, "×")); })) : null,
        msg ? h(window.__APP_UI__.kit.Alert, { tone: "danger" }, msg) : null,
        h("div", { className: "ml-ca" },
          h("input", { type: "file", multiple: true, hidden: true, ref: inp, onChange: function (x) { pick(x.target.files); x.target.value = ""; } }),
          h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: function () { inp.current && inp.current.click(); } }, "📎 Attach"),
          h("span", { style: { flex: 1 } }),
          h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: p.onClose, disabled: busy }, "Discard"),
          h("button", { type: "submit", className: "hm-btn", disabled: busy }, busy ? "Sending…" : "Send"))));
  }

  function Reader(p) {
    var m = p.msg, a = useState(false), plain = a[0], setPlain = a[1];
    useEffect(function () { setPlain(false); }, [m && m.uid]);
    if (!m) return h("div", { className: "ml-empty" }, h("div", { className: "ml-empty-ico" }, "✉️"), h("p", null, p.loading ? "Opening…" : "Pick a message to read it."));
    var body = !plain && m.html ? h("iframe", { className: "ml-frame", title: "Message", sandbox: "allow-popups allow-popups-to-escape-sandbox", srcDoc: FRAME_CSS + m.html })
      : h("pre", { className: "ml-text" }, m.text || "(this message has no text)");
    var isJunk = p.role === "junk";
    var targets = m.block_targets || {};
    function block(scope) {
      var target = targets[scope];
      if (scope === "domain" && !window.confirm("Block all mail from " + target + " server-wide?")) return;
      p.onBlock(scope, target);
    }
    return h("article", { className: "ml-reader" },
      h("header", { className: "ml-rh" },
        h("button", { type: "button", className: "ml-back hm-btn hm-btn-ghost", onClick: p.onBack }, "‹ Back"),
        h("h2", null, m.subject),
        h("div", { className: "ml-meta" },
          h("span", { className: "ml-av", style: { "--h": hue(m.from.email) } }, initial(m.from)),
          h("div", { className: "ml-who" }, h("div", null, h("strong", null, who(m.from)), m.from.name ? h("span", { className: "hm-sub" }, " <" + m.from.email + ">") : null),
            h("div", { className: "hm-sub" }, "To " + addrLine(m.to) + (m.cc.length ? " · Cc " + addrLine(m.cc) : "")), h("div", { className: "hm-sub" }, full(m.date)))),
        h("div", { className: "ml-acts" },
          h("button", { type: "button", className: "hm-btn", onClick: function () { p.onReply(false); } }, "↩ Reply"),
          h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: function () { p.onReply(true); } }, "Reply all"),
          h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: p.onForward }, "Forward"),
          h("span", { style: { flex: 1 } }),
          h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: p.onUnread, title: "Mark as unread" }, "● Unread"),
          isJunk || !p.canBlock
            ? h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: p.onJunk }, isJunk ? "Not spam" : "Mark as spam")
            : h("details", { className: "ml-spam" },
              h("summary", { className: "hm-btn hm-btn-ghost" }, "Spam ▾"),
              h("div", { className: "ml-spam-options" },
                h("button", { type: "button", className: "hm-btn hm-btn-ghost", disabled: p.blocking || !targets.sender, onClick: function () { block("sender"); } }, "Block sender", targets.sender ? h("small", null, targets.sender) : null),
                h("button", { type: "button", className: "hm-btn hm-btn-ghost", disabled: p.blocking || !targets.domain, onClick: function () { block("domain"); } }, "Block sender’s domain", targets.domain ? h("small", null, targets.domain) : null))),
          h("button", { type: "button", className: "hm-btn hm-btn-ghost ml-del", onClick: p.onDelete }, p.role === "trash" ? "Delete forever" : "Delete"))),
      m.attachments.length ? h("div", { className: "ml-atts" }, m.attachments.map(function (at) {
        return h("a", { key: at.n, className: "ml-att", href: U("/messages/" + m.uid + "/attachment/" + at.n + "?folder=" + encodeURIComponent(m.folder)), download: at.name }, "📎 " + at.name, h("span", null, size(at.size)));
      })) : null,
      m.blocked_images && !plain ? h("p", { className: "ml-note" }, "Remote images are blocked so the sender cannot see when you read this.") : null,
      h("div", { className: "ml-body-wrap" }, body),
      m.html ? h("p", { className: "ml-toggle" }, h("button", { type: "button", className: "ml-link", onClick: function () { setPlain(!plain); } }, plain ? "Show formatted" : "Show plain text")) : null);
  }

  function Page(props) {
    var startRole = props.role;
    var a = useState([]), folders = a[0], setFolders = a[1], b = useState(null), data = b[0], setData = b[1], c = useState(""), err = c[0], setErr = c[1];
    var d = useState(null), msg = d[0], setMsg = d[1], e = useState(null), openUid = e[0], setOpenUid = e[1], f = useState(0), page = f[0], setPage = f[1];
    var g = useState(""), q = g[0], setQ = g[1], hq = useState(""), query = hq[0], setQuery = hq[1], cm = useState(props.compose ? {} : null), compose = cm[0], setCompose = cm[1];
    var t = useState(0), tick = t[0], setTick = t[1], ld = useState(false), loadingMsg = ld[0], setLoadingMsg = ld[1], nt = useState(""), note = nt[0], setNote = nt[1];
    var blockState = useState(false), blocking = blockState[0], setBlocking = blockState[1];
    var role = startRole === "compose" ? "inbox" : startRole;
    var folder = (folders.filter(function (x) { return x.role === role; })[0] || {}).name || (role === "inbox" ? "INBOX" : "");
    var meta = folders.filter(function (x) { return x.name === folder; })[0] || {};

    useEffect(function () { SDK.fetchJSON(U("/folders")).then(function (r) { setFolders(r.folders || []); }, function (x) { setErr(errText(x)); }); }, [tick]);
    useEffect(function () {
      if (!folder) return undefined;
      var live = true;
      SDK.fetchJSON(U("/messages?folder=" + encodeURIComponent(folder) + "&page=" + page + (query ? "&q=" + encodeURIComponent(query) : ""))).then(function (r) { if (live) { setData(r); setErr(""); } }, function (x) { if (live) setErr(errText(x)); });
      return function () { live = false; };
    }, [folder, page, query, tick]);
    useEffect(function () { var i = setInterval(function () { setTick(function (n) { return n + 1; }); }, 90000); return function () { clearInterval(i); }; }, []);

    function open(it) {
      setOpenUid(it.uid); setLoadingMsg(true); setMsg(null);
      SDK.fetchJSON(U("/messages/" + it.uid + "?folder=" + encodeURIComponent(folder))).then(function (r) {
        setMsg(r); setLoadingMsg(false);
        if (data) setData(Object.assign({}, data, { items: data.items.map(function (x) { return x.uid === it.uid ? Object.assign({}, x, { seen: true }) : x; }) }));
        SDK.fetchJSON(U("/folders")).then(function (rr) { setFolders(rr.folders || []); }, function () { /* optional */ });
      }, function (x) { setLoadingMsg(false); setErr(errText(x)); });
    }
    function after(msgText) { setMsg(null); setOpenUid(null); setNote(msgText || ""); setTick(tick + 1); }
    function api(method, path, body) { return SDK.fetchJSON(U(path), { method: method, headers: JSONH, body: body ? JSON.stringify(body) : undefined }); }
    function del(uid) { api("DELETE", "/messages/" + uid + "?folder=" + encodeURIComponent(folder)).then(function () { after(role === "trash" ? "Deleted for good." : "Moved to Trash."); }, function (x) { setErr(errText(x)); }); }
    function star(it, ev) { ev.stopPropagation(); api("POST", "/messages/" + it.uid + "/flags", { folder: folder, flagged: !it.flagged }).then(function () { setData(Object.assign({}, data, { items: data.items.map(function (x) { return x.uid === it.uid ? Object.assign({}, x, { flagged: !it.flagged }) : x; }) })); }, function (x) { setErr(errText(x)); }); }
    function junk(uid) {
      var target = role === "junk" ? (folders.filter(function (x) { return x.role === "inbox"; })[0] || { name: "INBOX" }).name : (folders.filter(function (x) { return x.role === "junk"; })[0] || {}).name;
      if (!target) { setErr("There is no spam folder."); return; }
      api("POST", "/messages/" + uid + "/move", { folder: folder, to: target }).then(function () { after(role === "junk" ? "Moved back to the inbox." : "Moved to spam."); }, function (x) { setErr(errText(x)); });
    }
    function blockSpam(scope, target) {
      if (blocking) return;
      setBlocking(true); setErr(""); setNote("");
      api("POST", "/messages/" + msg.uid + "/spam", { folder: msg.folder, scope: scope, target: target }).then(function (result) {
        setBlocking(false);
        if (result.moved) after("Blocked " + result.blocked + " server-wide and moved to Spam.");
        else setErr("Blocked " + result.blocked + " server-wide. The message could not be moved to Spam.");
      }, function (x) { setBlocking(false); setErr(errText(x)); });
    }
    function unread(uid) { api("POST", "/messages/" + uid + "/flags", { folder: folder, seen: false }).then(function () { after("Marked as unread."); }, function (x) { setErr(errText(x)); }); }
    function reply(all) {
      var to = (msg.reply_to && msg.reply_to.length ? msg.reply_to : [msg.from]), me = (props.address || "").toLowerCase();
      var toList = to.map(function (x) { return x.email; }), ccList = all ? msg.to.concat(msg.cc).map(function (x) { return x.email; }).filter(function (x) { return x.toLowerCase() !== me && toList.indexOf(x) < 0; }) : [];
      setCompose({ title: "Reply", to: toList.join(", "), cc: ccList.join(", "), subject: /^re:/i.test(msg.subject) ? msg.subject : "Re: " + msg.subject, body: quote(msg), reply_uid: msg.uid, reply_folder: msg.folder });
    }
    function forward() { setCompose({ title: "Forward", subject: /^fwd?:/i.test(msg.subject) ? msg.subject : "Fwd: " + msg.subject, body: "\n\n---------- Forwarded message ----------\nFrom: " + addrLine([msg.from]) + "\nDate: " + full(msg.date) + "\nSubject: " + msg.subject + "\nTo: " + addrLine(msg.to) + "\n\n" + (msg.text || "") }); }

    if (err && !data && !folders.length) return h("div", { className: "ml" }, h("h1", null, "Email"), h(window.__APP_UI__.kit.Alert, { tone: "danger", title: "Could not load your mail" }, err), h("p", { className: "hm-sub" }, "If the mailbox is not connected to this app yet, add its address and an app token to the app's private config."));
    var items = (data && data.items) || [], unseen = meta.unseen || 0;
    var titles = { inbox: "Inbox", sent: "Sent", drafts: "Drafts", junk: "Spam", trash: "Trash" };
    return h("div", { className: "ml" + (openUid ? " ml-open" : "") },
      h("header", { className: "ml-top" },
        h("div", null, h("h1", null, titles[role] || meta.name || "Mail"), h("p", { className: "hm-sub" }, (meta.total != null ? meta.total + " messages" : "") + (unseen ? " · " + unseen + " unread" : ""))),
        h("div", { className: "ml-tools" },
          h("form", { className: "ml-search", onSubmit: function (ev) { ev.preventDefault(); setPage(0); setQuery(q.trim()); } },
            h("input", { className: "hm-input", type: "search", placeholder: "Search this folder", value: q, "aria-label": "Search mail", onChange: function (x) { setQ(x.target.value); if (!x.target.value) { setQuery(""); setPage(0); } } })),
          h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: function () { setTick(tick + 1); }, "aria-label": "Refresh", title: "Refresh" }, "↻"),
          h("button", { type: "button", className: "hm-btn", onClick: function () { setCompose({}); } }, "✎ Write"))),
      err ? h(window.__APP_UI__.kit.Alert, { tone: "danger" }, err) : null,
      note ? h("p", { className: "hm-ok ml-note-ok" }, note) : null,
      h("div", { className: "ml-layout" },
        h("section", { className: "hm-card ml-list" },
          !data ? h(window.__APP_UI__.kit.Loading, { label: "Loading…" }) : items.length ? h("ul", { className: "ml-items" }, items.map(function (it) {
            return h("li", { key: it.uid, className: "ml-item" + (it.seen ? "" : " ml-unread") + (it.uid === openUid ? " ml-active" : ""), onClick: function () { open(it); }, tabIndex: 0, onKeyDown: function (x) { if (x.key === "Enter") open(it); } },
              h("span", { className: "ml-av", style: { "--h": hue(it.from.email) } }, initial(it.from)),
              h("div", { className: "ml-ib" },
                h("div", { className: "ml-r1" }, h("span", { className: "ml-from" }, role === "sent" || role === "drafts" ? "To: " + (who(it.to[0]) || "") : who(it.from)), h("span", { className: "ml-date" }, short(it.date))),
                h("div", { className: "ml-subj" }, it.subject), h("div", { className: "ml-r3" }, it.attach ? h("span", { title: "Has attachments" }, "📎") : null, it.answered ? h("span", { title: "Replied" }, "↩") : null)),
              h("button", { type: "button", className: "ml-star" + (it.flagged ? " ml-on" : ""), "aria-label": it.flagged ? "Remove star" : "Star", onClick: function (ev) { star(it, ev); } }, it.flagged ? "★" : "☆"));
          })) : h(window.__APP_UI__.kit.EmptyState, { icon: "📭", title: query ? "Nothing matched “" + query + "”" : "This folder is empty" }),
          data && data.pages > 1 ? h("div", { className: "ml-pager" }, h("button", { type: "button", className: "hm-btn hm-btn-ghost", disabled: page <= 0, onClick: function () { setPage(page - 1); } }, "‹ Newer"), h("span", { className: "hm-sub" }, "Page " + (page + 1) + " of " + data.pages), h("button", { type: "button", className: "hm-btn hm-btn-ghost", disabled: page + 1 >= data.pages, onClick: function () { setPage(page + 1); } }, "Older ›")) : null),
        h("section", { className: "hm-card ml-read" }, h(Reader, { msg: msg, loading: loadingMsg, role: role, canBlock: !!props.canBlock, blocking: blocking, onBlock: blockSpam, onBack: function () { setOpenUid(null); setMsg(null); }, onReply: reply, onForward: forward, onDelete: function () { del(msg.uid); }, onJunk: function () { junk(msg.uid); }, onUnread: function () { unread(msg.uid); } }))),
      props.bar || null,
      compose ? h(Composer, { draft: compose, onClose: function () { setCompose(null); }, onSent: function () { setCompose(null); after("Sent."); } }) : null);
  }

  /* Shown to a person who has no mailbox yet (when personal mailboxes are on): pick a provider, give the address and an app password. */
  function Connect(props) {
    var K = window.__APP_UI__.kit, presets = props.presets || [];
    var s = useState({ preset: presets.length ? presets[0].id : "", address: "", password: "", imap_host: "", smtp_host: "" }), f = s[0], setF = s[1];
    var b = useState(false), busy = b[0], setBusy = b[1], e = useState(""), err = e[0], setErr = e[1];
    function set(k, v) { var n = Object.assign({}, f); n[k] = v; setF(n); }
    var chosen = presets.filter(function (p) { return p.id === f.preset; })[0];
    // the provider's own page where an app password is made; not offered for "Other", which has no page to point to
    var helpLink = chosen && chosen.help_url ? h("a", { className: "hm-btn hm-btn-ghost", href: chosen.help_url, target: "_blank", rel: "noopener noreferrer", style: { alignSelf: "flex-start" } }, "Don't have an app password? Get one") : null;
    function submit(ev) {
      ev.preventDefault();
      setBusy(true); setErr("");
      var body = { address: f.address.trim(), password: f.password, preset: f.preset };
      if (!f.preset) { body.imap_host = f.imap_host.trim(); body.smtp_host = f.smtp_host.trim(); }
      SDK.fetchJSON(API + "/account", { method: "PUT", headers: JSONH, body: JSON.stringify(body) }).then(function (r) { setF(Object.assign({}, f, { password: "" })); props.done(r && r.account ? r.account.id : ""); },
        function (x) { setBusy(false); setErr(errText(x)); });
    }
    return h("div", { className: "ml" }, h("h1", null, "Email"),
      h("form", { className: "hm-card", onSubmit: submit, style: { maxWidth: "34rem", display: "flex", flexDirection: "column", gap: ".8rem" } },
        h("h2", null, props.adding ? "Add another mailbox" : "Connect your email"),
        h("p", { className: "hm-sub" }, "Read and send from your own mailbox here. Your password is stored encrypted on this server and is never shown again."),
        err ? h(K.Alert, { tone: "danger", action: /needs an app password/.test(err) ? helpLink : null }, err) : null,
        h(K.Field, { label: "Provider" }, function (ip) {
          return h("select", Object.assign({}, ip, { className: "hm-select", value: f.preset, onChange: function (x) { set("preset", x.target.value); } }),
            presets.map(function (p) { return h("option", { key: p.id, value: p.id }, p.label); }), h("option", { key: "", value: "" }, "Other (enter the servers)"));
        }),
        h(K.Field, { label: "Email address", required: true }, function (ip) { return h("input", Object.assign({}, ip, { className: "hm-input", type: "email", autoComplete: "email", maxLength: 320, required: true, value: f.address, onChange: function (x) { set("address", x.target.value); } })); }),
        h(K.Field, { label: "App password", required: true, hint: "Use an app password, not your normal password." }, function (ip) { return h("input", Object.assign({}, ip, { className: "hm-input", type: "password", autoComplete: "off", maxLength: 512, required: true, value: f.password, onChange: function (x) { set("password", x.target.value); } })); }),
        chosen ? h("div", { className: "ml-help", style: { display: "flex", flexWrap: "wrap", gap: ".6rem 1.2rem", alignItems: "flex-start" } },
          helpLink,
          chosen.steps && chosen.steps.length ? h("ol", { className: "hm-fine", style: { margin: 0, paddingLeft: "1.2rem", flex: "1 1 14rem" } }, chosen.steps.map(function (s, i) { return h("li", { key: i }, s); }))
            : chosen.hint ? h("p", { className: "hm-fine", style: { margin: 0, flex: "1 1 14rem" } }, chosen.hint) : null) : null,
        !f.preset ? h(SDK.React.Fragment, null,
          h(K.Field, { label: "Incoming server (IMAP)", required: true, hint: "A public name such as imap.example.com. Port 993 with TLS is used." }, function (ip) { return h("input", Object.assign({}, ip, { className: "hm-input", type: "text", maxLength: 253, required: true, value: f.imap_host, onChange: function (x) { set("imap_host", x.target.value); } })); }),
          h(K.Field, { label: "Outgoing server (SMTP)", required: true, hint: "A public name such as smtp.example.com. Port 465 with TLS is used." }, function (ip) { return h("input", Object.assign({}, ip, { className: "hm-input", type: "text", maxLength: 253, required: true, value: f.smtp_host, onChange: function (x) { set("smtp_host", x.target.value); } })); })) : null,
        h("div", { style: { display: "flex", gap: ".6rem" } }, h(K.Button, { type: "submit", variant: "primary", loading: busy }, "Connect"),
          props.onCancel ? h(K.Button, { variant: "ghost", disabled: busy, onClick: props.onCancel }, "Cancel") : null)));
  }

  /* The line under the mailbox: which one is open, a switch between the person's mailboxes, Add another, and Disconnect for a personal one. */
  function Bar(props) {
    var st = props.st, accounts = st.accounts || [], max = st.max_accounts || 5, personalCount = accounts.filter(function (a) { return a.kind === "personal"; }).length;
    return h("p", { className: "hm-fine", style: { display: "flex", gap: ".6rem", alignItems: "center", flexWrap: "wrap" } },
      accounts.length > 1 ? h("select", { className: "hm-select", "aria-label": "Mailbox", value: st.active, onChange: function (x) { props.onSwitch(x.target.value); } },
        accounts.map(function (a) { return h("option", { key: a.id, value: a.id }, (a.kind === "server" ? "Server mailbox: " : "") + a.address); })) : h("span", null, "Signed in as " + st.address),
      st.webmail ? h("a", { href: st.webmail, target: "_blank", rel: "noopener noreferrer" }, "Open full webmail ↗") : null,
      st.user_accounts && personalCount < max ? h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: props.onAdd }, "＋ Add another mailbox") : null,
      st.account === "personal" ? h("button", { type: "button", className: "hm-btn hm-btn-ghost", onClick: function () {
        if (window.confirm("Disconnect " + st.address + "? Your saved sign-in is removed from this server. Your mail stays where it is.")) {
          SDK.fetchJSON(API + "/account/" + encodeURIComponent(st.active), { method: "DELETE" }).then(props.onDisconnect, function (x) { window.alert(errText(x)); });
        }
      } }, "Disconnect") : null);
  }

  function savedChoice() { try { return sessionStorage.getItem("atrium.mail.account") || ""; } catch (x) { return ""; } }
  function saveChoice(id) { try { sessionStorage.setItem("atrium.mail.account", id || ""); } catch (x) { /* remembering the choice is optional */ } }

  function Wrap(role, compose) {
    return function Entry() {
      var a = useState(null), st = a[0], set = a[1], t = useState(0), n = t[0], setN = t[1], ad = useState(false), adding = ad[0], setAdding = ad[1];
      useEffect(function () {
        cur = savedChoice();
        function load() { SDK.fetchJSON(U("/status")).then(set, function () { if (cur) { cur = ""; saveChoice(""); load(); } else set({}); }); }
        load();
      }, [n]);
      function reload() { setAdding(false); set(null); setN(n + 1); }
      function switchTo(id) { saveChoice(id); reload(); }
      if (!st) return h("div", { className: "ml" }, h(window.__APP_UI__.kit.Loading, { label: "Loading…" }));
      if ((st.connected === false && st.user_accounts) || adding) {
        return h(Connect, { presets: st.presets, adding: adding, onCancel: adding ? function () { setAdding(false); } : null, done: function (id) { if (id) saveChoice(id); reload(); } });
      }
      cur = st.active || "";
      return h(Page, { key: st.active || "x", role: role, compose: compose, address: st.address, webmail: st.webmail, canBlock: (st.features || []).indexOf("blocklist") >= 0,
        bar: st.address ? h(Bar, { st: st, onSwitch: switchTo, onAdd: function () { setAdding(true); }, onDisconnect: function () { saveChoice(""); reload(); } }) : null });
    };
  }
  REG.register("mail-inbox", Wrap("inbox"));
  REG.register("mail-sent", Wrap("sent"));
  REG.register("mail-drafts", Wrap("drafts"));
  REG.register("mail-junk", Wrap("junk"));
  REG.register("mail-trash", Wrap("trash"));
  REG.register("mail-compose", Wrap("compose", true));
})();
