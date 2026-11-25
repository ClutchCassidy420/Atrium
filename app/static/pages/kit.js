/* Atrium UI kit: reusable, accessible components for framework and instance pages. Plain script, no build step, like
   common.js. Load it after common.js and kit.css; everything lives on window.__APP_UI__.kit (alias UI.kit).

   Components:  Button, Spinner, Loading, EditorDialog, Skeleton, Alert, EmptyState, AsyncView, Field, Tabs, Dialog,
                Badge, Switch, SearchInput, ConfirmDialog, ToastProvider, DataTable, Pagination
   Hooks:       useAsync(fn, deps), useId(prefix), useDisclosure(initial), useDebounce(value, ms), useToast()
   Conventions: every component takes `className` (appended) and passes through unknown DOM props via `...rest`-style
                `attrs`; state is expressed as props (`loading`, `disabled`, `invalid`), never by swapping components;
                colors and radii come from tokens.css only. See docs/UI-KIT.md for the full API. */
(function () {
  "use strict";
  var UI = window.__APP_UI__;
  if (!UI || UI.kit) return;
  var React = UI.React, h = UI.h;
  var useState = React.useState, useEffect = React.useEffect, useRef = React.useRef, useCallback = React.useCallback;

  var kit = {};
  function cx() { return Array.prototype.filter.call(arguments, Boolean).join(" "); }
  function merge(a, b) { var o = {}, k; for (k in a) o[k] = a[k]; for (k in (b || {})) o[k] = b[k]; return o; }
  kit.cx = cx;

  /* ---- hooks ------------------------------------------------------------------------------------------------- */
  var idCounter = 0;
  kit.useId = function (prefix) {
    var r = useRef(null);
    if (r.current === null) r.current = (prefix || "kit") + "-" + (++idCounter);
    return r.current;
  };

  kit.useDisclosure = function (initial) {
    var s = useState(!!initial), open = s[0], set = s[1];
    return { open: open, show: useCallback(function () { set(true); }, []), hide: useCallback(function () { set(false); }, []),
      toggle: useCallback(function () { set(function (v) { return !v; }); }, []) };
  };

  /* Runs fn() (returning a promise) on mount and whenever deps change. Ignores stale results, so a slow earlier request
     can never overwrite a newer one. Returns { status: "loading"|"success"|"error", data, error, reload }. */
  kit.useAsync = function (fn, deps) {
    var s = useState({ status: "loading", data: null, error: null }), state = s[0], set = s[1];
    var n = useState(0), tick = n[0], setTick = n[1];
    useEffect(function () {
      var alive = true;
      set(function (p) { return { status: "loading", data: p.data, error: null }; }); // keep old data visible while reloading
      Promise.resolve().then(fn).then(
        function (d) { if (alive) set({ status: "success", data: d, error: null }); },
        function (e) { if (alive) set({ status: "error", data: null, error: e && e.message ? e.message : String(e || "Could not load") }); });
      return function () { alive = false; };
    }, (deps || []).concat([tick]));
    return merge(state, { reload: function () { setTick(function (v) { return v + 1; }); } });
  };

  /* ---- Spinner / Skeleton -------------------------------------------------------------------------------------- */
  /* <Spinner label="Saving" /> : announced politely; decorative when `label` is omitted and a parent already announces. */
  kit.Spinner = function (p) {
    return h("span", merge({ className: cx("kit-spinner", p.className), role: p.label ? "status" : undefined, "aria-hidden": p.label ? undefined : "true" }, p.attrs),
      h("span", { className: "kit-spinner-ring" }), p.label ? h("span", { className: "kit-sr" }, p.label) : null);
  };

  /* <Loading label="Loading files…" /> : a spinner with text, announced politely; for sections that have no skeleton yet. */
  kit.Loading = function (p) {
    return h("div", { className: cx("kit-loading", p.className), role: "status" }, h(kit.Spinner, null), h("span", null, p.label || "Loading…"));
  };

  /* <Skeleton lines={3} /> or <Skeleton height={120} />. Purely visual: aria-hidden; the parent carries aria-busy. */
  kit.Skeleton = function (p) {
    var lines = p.lines || 1, out = [];
    for (var i = 0; i < lines; i++) {
      out.push(h("span", { key: i, className: "kit-skel", style: { height: p.height || "1em", width: (i === lines - 1 && lines > 1) ? "60%" : (p.width || "100%") } }));
    }
    return h("span", { className: cx("kit-skel-group", p.className), "aria-hidden": "true" }, out);
  };

  /* ---- Button -------------------------------------------------------------------------------------------------- */
  /* <Button variant="primary|secondary|ghost|danger" size="sm|md" loading onClick>Save</Button>
     `loading` keeps the button focusable (aria-disabled) so focus is not lost, and blocks double-submits. */
  kit.Button = function (p) {
    var blocked = p.disabled || p.loading;
    return h("button", merge({
      type: p.type || "button",
      className: cx("kit-btn", "kit-btn-" + (p.variant || "secondary"), "kit-btn-" + (p.size || "md"), p.className),
      disabled: p.disabled || undefined,
      "aria-disabled": p.loading ? "true" : undefined,
      "aria-busy": p.loading ? "true" : undefined,
      onClick: function (e) { if (blocked) { e.preventDefault(); return; } if (p.onClick) p.onClick(e); }
    }, p.attrs),
      p.loading ? h(kit.Spinner, null) : (p.icon ? h("span", { className: "kit-btn-icon", "aria-hidden": "true" }, p.icon) : null),
      h("span", null, p.children));
  };

  /* ---- Alert / EmptyState -------------------------------------------------------------------------------------- */
  /* <Alert tone="info|success|warning|danger" title="..." onDismiss>message</Alert>. Danger uses role=alert (assertive). */
  kit.Alert = function (p) {
    var tone = p.tone || "info";
    return h("div", { className: cx("kit-alert", "kit-alert-" + tone, p.className), role: tone === "danger" ? "alert" : "status" },
      h("div", { className: "kit-alert-body" }, p.title ? h("strong", null, p.title) : null, p.children ? h("div", null, p.children) : null),
      p.action || null,
      p.onDismiss ? h("button", { type: "button", className: "kit-alert-x", "aria-label": "Dismiss", onClick: p.onDismiss }, "×") : null);
  };

  /* <EmptyState title description action icon /> : the "nothing here yet" and "nothing matched" state. */
  kit.EmptyState = function (p) {
    return h("div", { className: cx("kit-empty", p.className) },
      p.icon ? h("div", { className: "kit-empty-icon", "aria-hidden": "true" }, p.icon) : null,
      h("h3", { className: "kit-empty-title" }, p.title),
      p.description ? h("p", { className: "kit-empty-desc" }, p.description) : null,
      p.action || null);
  };

  /* ---- AsyncView: one place for loading / error / empty / data -------------------------------------------------- */
  /* <AsyncView state={useAsync(...)} isEmpty={d => !d.length} skeleton={<Skeleton lines={4}/>} empty={<EmptyState .../>}>
       {data => <List items={data} />}
     </AsyncView>
     While reloading with old data, the data stays on screen with aria-busy (no flash back to a skeleton). */
  kit.AsyncView = function (p) {
    var st = p.state, data = st.data;
    var hasData = data !== null && data !== undefined;
    var body;
    if (st.status === "error") {
      body = h(kit.Alert, { tone: "danger", title: p.errorTitle || "Could not load this", action: st.reload ? h(kit.Button, { size: "sm", onClick: st.reload }, "Try again") : null }, st.error);
    } else if (st.status === "loading" && !hasData) {
      body = p.skeleton || h(kit.Skeleton, { lines: 3 });
    } else if (hasData && (p.isEmpty ? p.isEmpty(data) : (Array.isArray(data) && data.length === 0))) {
      body = p.empty || h(kit.EmptyState, { title: "Nothing here yet" });
    } else {
      body = p.children(data);
    }
    return h("div", { className: cx("kit-async", p.className), "aria-busy": st.status === "loading" ? "true" : "false", "aria-live": "polite" }, body);
  };

  /* ---- Field ------------------------------------------------------------------------------------------------- */
  /* <Field label="Email" hint="We never share it" error={err} required>
       {inputProps => <input {...inputProps} type="email" value=... onChange=... />}
     </Field>
     The render prop receives { id, "aria-describedby", "aria-invalid", "aria-required" } so label, hint and error are wired
     for screen readers without the caller doing any bookkeeping. */
  kit.Field = function (p) {
    var autoId = kit.useId("field"), id = p.id || autoId, hintId = id + "-hint", errId = id + "-err";
    var describedBy = [p.hint ? hintId : "", p.error ? errId : ""].filter(Boolean).join(" ") || undefined;
    var inputProps = { id: id, "aria-describedby": describedBy, "aria-invalid": p.error ? "true" : undefined, "aria-required": p.required ? "true" : undefined, className: "kit-input" };
    return h("div", { className: cx("kit-field", p.error && "kit-field-invalid", p.className) },
      h("label", { className: "kit-label", htmlFor: id }, p.label, p.required ? h("span", { className: "kit-req", "aria-hidden": "true" }, " *") : null),
      p.children(inputProps),
      p.hint ? h("div", { id: hintId, className: "kit-hint" }, p.hint) : null,
      p.error ? h("div", { id: errId, className: "kit-error", role: "alert" }, p.error) : null);
  };

  /* ---- Tabs (WAI-ARIA tabs pattern: arrow keys, Home/End, roving tabindex) --------------------------------------- */
  /* <Tabs tabs={[{id, label, panel}]} value={id} onChange={id => ...} label="Sections" />  (controlled; or omit value for uncontrolled) */
  kit.Tabs = function (p) {
    var base = kit.useId("tabs");
    var s = useState(p.defaultValue || (p.tabs[0] && p.tabs[0].id)), inner = s[0], setInner = s[1];
    var value = p.value !== undefined ? p.value : inner;
    var refs = useRef({});
    function select(id, focus) {
      if (p.value === undefined) setInner(id);
      if (p.onChange) p.onChange(id);
      if (focus && refs.current[id]) refs.current[id].focus();
    }
    function onKey(e) {
      var ids = p.tabs.filter(function (t) { return !t.disabled; }).map(function (t) { return t.id; });
      var i = ids.indexOf(value), next = null;
      if (e.key === "ArrowRight") next = ids[(i + 1) % ids.length];
      else if (e.key === "ArrowLeft") next = ids[(i - 1 + ids.length) % ids.length];
      else if (e.key === "Home") next = ids[0];
      else if (e.key === "End") next = ids[ids.length - 1];
      if (next !== null) { e.preventDefault(); select(next, true); }
    }
    var active = p.tabs.filter(function (t) { return t.id === value; })[0];
    return h("div", { className: cx("kit-tabs", p.className) },
      h("div", { className: "kit-tablist", role: "tablist", "aria-label": p.label, onKeyDown: onKey },
        p.tabs.map(function (t) {
          var on = t.id === value;
          return h("button", { key: t.id, type: "button", role: "tab", id: base + "-t-" + t.id, "aria-selected": on ? "true" : "false", "aria-controls": base + "-p-" + t.id,
            tabIndex: on ? 0 : -1, disabled: t.disabled || undefined, className: cx("kit-tab", on && "kit-tab-on"),
            ref: function (el) { refs.current[t.id] = el; }, onClick: function () { select(t.id, false); } }, t.label);
        })),
      active ? h("div", { key: active.id, className: "kit-tabpanel", role: "tabpanel", id: base + "-p-" + active.id, "aria-labelledby": base + "-t-" + active.id, tabIndex: 0 }, active.panel) : null);
  };

  /* ---- Dialog ------------------------------------------------------------------------------------------------ */
  /* <Dialog open onClose title="Delete file?" footer={<Button/>}>body</Dialog>
     Modal: Escape and backdrop click close it, Tab is trapped inside, focus moves in on open and returns to the opener on
     close, page scroll is locked. Rendered in a portal when ReactDOM is available. */
  var FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
  kit.Dialog = function (p) {
    var ref = useRef(null), id = kit.useId("dlg"), open = !!p.open;
    var onClose = useRef(p.onClose); onClose.current = p.onClose;
    useEffect(function () {
      if (!open) return;
      var opener = document.activeElement, root = ref.current;
      var prevOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      var first = root && root.querySelector("[data-autofocus]") || (root && root.querySelector(FOCUSABLE)) || root;
      if (first) first.focus();
      function onKey(e) {
        if (e.key === "Escape") { e.stopPropagation(); if (onClose.current) onClose.current(); return; }
        if (e.key !== "Tab" || !root) return;
        var f = root.querySelectorAll(FOCUSABLE);
        if (!f.length) { e.preventDefault(); root.focus(); return; }
        var a = f[0], z = f[f.length - 1];
        if (e.shiftKey && (document.activeElement === a || document.activeElement === root)) { e.preventDefault(); z.focus(); }
        else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
      }
      document.addEventListener("keydown", onKey, true);
      return function () {
        document.removeEventListener("keydown", onKey, true);
        document.body.style.overflow = prevOverflow;
        if (opener && opener.focus) opener.focus();
      };
    }, [open]);
    if (!open) return null;
    var node = h("div", { className: "kit-backdrop", onMouseDown: function (e) { if (e.target === e.currentTarget && onClose.current) onClose.current(); } },
      h("div", { className: cx("kit-dialog", p.className), role: p.alert ? "alertdialog" : "dialog", "aria-modal": "true", "aria-labelledby": id + "-title", ref: ref, tabIndex: -1 },
        h("header", { className: "kit-dialog-head" }, h("h2", { id: id + "-title" }, p.title),
          h("button", { type: "button", className: "kit-alert-x", "aria-label": "Close", onClick: function () { if (onClose.current) onClose.current(); } }, "×")),
        h("div", { className: "kit-dialog-body" }, p.children),
        p.footer ? h("footer", { className: "kit-dialog-foot" }, p.footer) : null));
    return window.ReactDOM && window.ReactDOM.createPortal ? window.ReactDOM.createPortal(node, document.body) : node;
  };

  /* <EditorDialog open title value onSave(text) onDelete() onClose fields readOnly readOnlyNote placeholder deleteLabel deleteNote />
     A dialog for changing a piece of text (a note, a skill): a large monospace box, optional `fields` above it, Save, and an optional
     two-step Delete. onSave and onDelete return promises; a rejection's message is shown in the dialog and nothing is lost. With
     `readOnly` the text can be read but not changed. */
  kit.EditorDialog = function (p) {
    var s = useState(p.value || ""), text = s[0], setText = s[1];
    var b = useState(false), busy = b[0], setBusy = b[1];
    var e = useState(""), err = e[0], setErr = e[1];
    var c = useState(false), confirm = c[0], setConfirm = c[1];
    var id = kit.useId("ed");
    useEffect(function () { if (p.open) { setText(p.value || ""); setErr(""); setConfirm(false); setBusy(false); } }, [p.open, p.value]);
    function run(fn) {
      setBusy(true); setErr("");
      Promise.resolve().then(fn).then(function () { setBusy(false); }, function (x) { setBusy(false); setErr(x && x.message ? x.message : String(x || "Something went wrong")); });
    }
    var ro = !!p.readOnly;
    var foot = [];
    if (!ro && p.onDelete) {
      foot.push(confirm
        ? h("span", { key: "dc", className: "kit-confirm" }, h("span", null, p.deleteNote || "Delete this?"),
            h(kit.Button, { size: "sm", variant: "danger", loading: busy, onClick: function () { run(p.onDelete); } }, "Yes, delete"),
            h(kit.Button, { size: "sm", variant: "ghost", disabled: busy, onClick: function () { setConfirm(false); } }, "Keep it"))
        : h(kit.Button, { key: "d", variant: "ghost", className: "kit-foot-left", disabled: busy, onClick: function () { setConfirm(true); } }, p.deleteLabel || "Delete"));
    }
    foot.push(h(kit.Button, { key: "c", disabled: busy, onClick: p.onClose }, ro ? "Close" : "Cancel"));
    if (!ro) foot.push(h(kit.Button, { key: "s", variant: "primary", loading: busy, onClick: function () { run(function () { return p.onSave(text); }); } }, p.saveLabel || "Save"));
    return h(kit.Dialog, { open: !!p.open, onClose: p.onClose, title: p.title, className: "kit-dialog-wide", footer: foot },
      ro ? h(kit.Alert, { tone: "info", className: "kit-gap" }, p.readOnlyNote || "This is read-only here.") : null,
      err ? h(kit.Alert, { tone: "danger", className: "kit-gap", onDismiss: function () { setErr(""); } }, err) : null,
      p.fields || null,
      h("label", { className: "kit-label kit-sr", htmlFor: id }, p.title),
      h("textarea", { id: id, className: "kit-input kit-textarea", value: text, readOnly: ro, spellCheck: false, placeholder: p.placeholder || "",
        "data-autofocus": p.fields ? undefined : "true", onChange: function (x) { setText(x.target.value); } }));
  };

  /* ---- useDebounce -------------------------------------------------------------------------------------------- */
  /* const q = useDebounce(text, 300): the value after it has stopped changing for `ms`. Timer is cleared on change/unmount. */
  kit.useDebounce = function (value, ms) {
    var s = useState(value), v = s[0], set = s[1];
    useEffect(function () { var t = setTimeout(function () { set(value); }, ms == null ? 300 : ms); return function () { clearTimeout(t); }; }, [value, ms]);
    return v;
  };

  /* ---- Badge / Switch ------------------------------------------------------------------------------------------ */
  /* <Badge tone="neutral|info|success|warning|danger">Live</Badge>. Meaning is in the text; color is only reinforcement. */
  kit.Badge = function (p) {
    return h("span", merge({ className: cx("kit-badge", "kit-badge-" + (p.tone || "neutral"), p.className) }, p.attrs), p.children);
  };

  /* <Switch checked onChange(bool) label="Notifications" disabled />: a real role=switch button, Space/Enter toggle, label is its name. */
  kit.Switch = function (p) {
    var id = kit.useId("sw");
    return h("span", { className: cx("kit-switch-wrap", p.className) },
      h("button", merge({ type: "button", id: id, role: "switch", "aria-checked": p.checked ? "true" : "false", disabled: p.disabled || undefined,
        className: cx("kit-switch", p.checked && "kit-switch-on"), onClick: function () { if (p.onChange) p.onChange(!p.checked); } }, p.attrs),
        h("span", { className: "kit-switch-knob", "aria-hidden": "true" })),
      p.label ? h("label", { htmlFor: id, className: "kit-switch-label" }, p.label) : null);
  };

  /* ---- SearchInput --------------------------------------------------------------------------------------------- */
  /* <SearchInput label="Search files" onSearch={q => ...} delay={300} placeholder />. Calls onSearch once typing pauses (and
     immediately on Enter or Clear); never on first render. The label is visually hidden but always present for screen readers. */
  kit.SearchInput = function (p) {
    var s = useState(p.defaultValue || ""), text = s[0], setText = s[1];
    var debounced = kit.useDebounce(text, p.delay);
    var first = useRef(true), cb = useRef(p.onSearch); cb.current = p.onSearch;
    var id = kit.useId("search");
    useEffect(function () { if (first.current) { first.current = false; return; } if (cb.current) cb.current(debounced); }, [debounced]);
    function now(v) { setText(v); if (cb.current) cb.current(v); }
    return h("div", { className: cx("kit-search", p.className), role: "search" },
      h("label", { htmlFor: id, className: "kit-sr" }, p.label || "Search"),
      h("input", { id: id, type: "search", className: "kit-input", value: text, placeholder: p.placeholder || "Search…", autoComplete: "off",
        onChange: function (e) { setText(e.target.value); }, onKeyDown: function (e) { if (e.key === "Enter") { e.preventDefault(); now(text); } } }),
      text ? h("button", { type: "button", className: "kit-alert-x kit-search-x", "aria-label": "Clear search", onClick: function () { now(""); } }, "×") : null);
  };

  /* ---- ConfirmDialog ------------------------------------------------------------------------------------------- */
  /* <ConfirmDialog open title="Delete 3 files?" confirmLabel="Delete" danger onConfirm={() => promise} onClose>This cannot be undone.</ConfirmDialog>
     An alertdialog. Focus starts on Cancel (the safe choice). While onConfirm runs the buttons are busy; a rejection shows its
     message inside the dialog and keeps it open so nothing is lost. It closes itself when onConfirm resolves. */
  kit.ConfirmDialog = function (p) {
    var b = useState(false), busy = b[0], setBusy = b[1];
    var e = useState(""), err = e[0], setErr = e[1];
    useEffect(function () { if (p.open) { setBusy(false); setErr(""); } }, [p.open]);
    function go() {
      setBusy(true); setErr("");
      Promise.resolve().then(p.onConfirm).then(function () { setBusy(false); if (p.onClose) p.onClose(); },
        function (x) { setBusy(false); setErr(x && x.message ? x.message : String(x || "Something went wrong")); });
    }
    return h(kit.Dialog, { open: !!p.open, onClose: busy ? function () {} : p.onClose, title: p.title, alert: true, className: p.className,
      footer: [h(kit.Button, { key: "c", disabled: busy, onClick: p.onClose, attrs: { "data-autofocus": "true" } }, p.cancelLabel || "Cancel"),
        h(kit.Button, { key: "ok", variant: p.danger ? "danger" : "primary", loading: busy, onClick: go }, p.confirmLabel || "Confirm")] },
      err ? h(kit.Alert, { tone: "danger", className: "kit-gap" }, err) : null, p.children);
  };

  /* ---- Toasts -------------------------------------------------------------------------------------------------- */
  /* Wrap the page once: <ToastProvider>...</ToastProvider>. Anywhere inside: const toast = useToast(); toast.push("Saved", { tone: "success" }).
     Messages sit in a polite live region (errors are assertive), auto-dismiss after `duration` ms (0 = stay until dismissed; errors
     default to staying), pause while hovered or focused, and each has a Dismiss button. Outside a provider, push is a safe no-op. */
  var ToastCtx = React.createContext({ push: function () { return 0; }, dismiss: function () {} });
  kit.useToast = function () { return React.useContext(ToastCtx); };
  kit.ToastProvider = function (p) {
    var s = useState([]), items = s[0], setItems = s[1];
    var timers = useRef({}), seq = useRef(0);
    var dismiss = useCallback(function (id) { clearTimeout(timers.current[id]); delete timers.current[id]; setItems(function (l) { return l.filter(function (t) { return t.id !== id; }); }); }, []);
    var arm = useCallback(function (id, ms) { clearTimeout(timers.current[id]); if (ms > 0) timers.current[id] = setTimeout(function () { dismiss(id); }, ms); }, [dismiss]);
    var push = useCallback(function (message, o) {
      o = o || {}; var id = ++seq.current, tone = o.tone || "info";
      var ms = o.duration != null ? o.duration : (tone === "danger" ? 0 : 5000);
      setItems(function (l) { return l.concat([{ id: id, message: message, tone: tone, ms: ms }]).slice(-5); });
      arm(id, ms); return id;
    }, [arm]);
    useEffect(function () { return function () { var t = timers.current; for (var k in t) clearTimeout(t[k]); }; }, []);
    var api = React.useMemo(function () { return { push: push, dismiss: dismiss }; }, [push, dismiss]);
    return h(ToastCtx.Provider, { value: api }, p.children,
      h("div", { className: "kit-toasts", "aria-live": "polite", "aria-label": "Notifications", role: "region" },
        items.map(function (t) {
          return h("div", { key: t.id, className: cx("kit-toast", "kit-alert", "kit-alert-" + t.tone), role: t.tone === "danger" ? "alert" : "status",
            onMouseEnter: function () { clearTimeout(timers.current[t.id]); }, onMouseLeave: function () { arm(t.id, t.ms); },
            onFocus: function () { clearTimeout(timers.current[t.id]); }, onBlur: function () { arm(t.id, t.ms); } },
            h("div", { className: "kit-alert-body" }, t.message),
            h("button", { type: "button", className: "kit-alert-x", "aria-label": "Dismiss notification", onClick: function () { dismiss(t.id); } }, "×"));
        })));
  };

  /* ---- DataTable ----------------------------------------------------------------------------------------------- */
  /* <DataTable caption="Files" columns={[{key,label,render?(row),sortable?,align?}]} rows rowKey="id" sort={{key,dir:"asc"|"desc"}} onSort(key,dir)
        loading error onRetry empty={<EmptyState/>} skeletonRows={5} onRowClick(row) />
     A real <table> (caption, scoped headers, aria-sort on the sorted column). Sorting is the caller's (server or client): the table
     only reports intent, so it scales to paged data. Loading with rows already shown keeps them on screen (aria-busy); with none it
     shows skeleton rows. Wide tables scroll inside their own region (keyboard focusable) instead of breaking the page. */
  kit.DataTable = function (p) {
    var cols = p.columns || [], rows = p.rows || [];
    var rk = p.rowKey || "id";
    function keyOf(r, i) { return typeof rk === "function" ? rk(r) : (r[rk] != null ? r[rk] : i); }
    function clickSort(c) {
      if (!p.onSort) return;
      var dir = p.sort && p.sort.key === c.key && p.sort.dir === "asc" ? "desc" : "asc";
      p.onSort(c.key, dir);
    }
    var body;
    if (p.error) {
      return h(kit.Alert, { tone: "danger", title: p.errorTitle || "Could not load this", action: p.onRetry ? h(kit.Button, { size: "sm", onClick: p.onRetry }, "Try again") : null }, p.error);
    }
    if (p.loading && !rows.length) {
      var sk = [];
      for (var i = 0; i < (p.skeletonRows || 5); i++) {
        sk.push(h("tr", { key: i, "aria-hidden": "true" }, cols.map(function (c) { return h("td", { key: c.key }, h(kit.Skeleton, null)); })));
      }
      body = sk;
    } else if (!rows.length) {
      return p.empty || h(kit.EmptyState, { title: "Nothing here yet" });
    } else {
      body = rows.map(function (r, i) {
        var click = p.onRowClick;
        return h("tr", { key: keyOf(r, i), className: click ? "kit-row-click" : undefined, onClick: click ? function () { click(r); } : undefined },
          cols.map(function (c, ci) {
            var head = ci === 0 && p.rowHeader !== false;
            return h(head ? "th" : "td", { key: c.key, scope: head ? "row" : undefined, style: c.align ? { textAlign: c.align } : undefined }, c.render ? c.render(r) : r[c.key]);
          }));
      });
    }
    return h("div", { className: cx("kit-table-wrap", p.className), role: "region", "aria-label": p.caption, tabIndex: 0, "aria-busy": p.loading ? "true" : "false" },
      h("table", { className: "kit-table" },
        p.caption ? h("caption", { className: "kit-sr" }, p.caption) : null,
        h("thead", null, h("tr", null, cols.map(function (c) {
          var sorted = p.sort && p.sort.key === c.key ? p.sort.dir : null;
          return h("th", { key: c.key, scope: "col", style: c.align ? { textAlign: c.align } : undefined,
            "aria-sort": c.sortable ? (sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none") : undefined },
            c.sortable ? h("button", { type: "button", className: "kit-th-btn", onClick: function () { clickSort(c); } }, c.label,
              h("span", { "aria-hidden": "true" }, sorted === "asc" ? " ▲" : sorted === "desc" ? " ▼" : " ↕")) : c.label);
        }))),
        h("tbody", null, body)));
  };

  /* ---- Pagination ---------------------------------------------------------------------------------------------- */
  /* <Pagination page={1} pageCount={12} onChange(page) label="Files pages" />. Previous/Next plus a window of numbers with
     ellipses; the current page has aria-current="page". Renders nothing for a single page. */
  kit.Pagination = function (p) {
    var n = p.pageCount || 1, cur = Math.min(Math.max(p.page || 1, 1), n);
    if (n <= 1) return null;
    var nums = [], last = 0;
    for (var i = 1; i <= n; i++) {
      if (i === 1 || i === n || Math.abs(i - cur) <= 1) { if (last && i - last > 1) nums.push("gap" + i); nums.push(i); last = i; }
    }
    function go(i) { if (i >= 1 && i <= n && i !== cur && p.onChange) p.onChange(i); }
    return h("nav", { className: cx("kit-pager", p.className), "aria-label": p.label || "Pagination" },
      h(kit.Button, { size: "sm", variant: "ghost", disabled: cur === 1, onClick: function () { go(cur - 1); }, attrs: { "aria-label": "Previous page" } }, "‹"),
      nums.map(function (x) {
        return typeof x === "string" ? h("span", { key: x, className: "kit-pager-gap", "aria-hidden": "true" }, "…")
          : h(kit.Button, { key: x, size: "sm", variant: x === cur ? "primary" : "ghost", onClick: function () { go(x); }, attrs: { "aria-current": x === cur ? "page" : undefined, "aria-label": "Page " + x } }, String(x));
      }),
      h(kit.Button, { size: "sm", variant: "ghost", disabled: cur === n, onClick: function () { go(cur + 1); }, attrs: { "aria-label": "Next page" } }, "›"));
  };

  UI.kit = kit;
})();
