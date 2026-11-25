# Atrium UI kit

Reusable, accessible components for framework and instance pages. Plain script, no build step, built on the same page-host
interface as `common.js`. Adoption is one page at a time; the Files page and the loading, error and empty states elsewhere already use it.

## Architecture

| Layer | File | Role |
|---|---|---|
| Tokens | `app/static/tokens.css` | Colors, radius, font. An install re-themes by changing values only. |
| Helpers | `app/static/pages/common.js` | `UI.Card`, `UI.Chip`, `UI.usePoll`, formatting (unchanged). |
| Kit | `app/static/pages/kit.js` + `kit.css` | Components and hooks below, on `window.__APP_UI__.kit`. |

Rules the kit follows: state is a prop (`loading`, `disabled`, `error`), never a different component; every component takes
`className` and an `attrs` object for extra DOM attributes; colors come from tokens only; behavior follows the WAI-ARIA
authoring patterns (keyboard, focus, roles); motion respects `prefers-reduced-motion`; layouts work down to a phone.

## Using it on a page

In `app/pages.json`, load the kit after `common.js` and add its stylesheet:

```json
"scripts": ["/static/pages/common.js", "/static/pages/kit.js", "/static/pages/mypage.js"],
"styles":  ["/static/pages/home.css", "/static/pages/kit.css"]
```

```js
var K = window.__APP_UI__.kit, h = window.__APP_UI__.h;

function Files() {
  var state = K.useAsync(function () { return SDK.fetchJSON("/api/files"); }, []);
  var dlg = K.useDisclosure();
  return h("div", null,
    h(K.AsyncView, { state: state, skeleton: h(K.Skeleton, { lines: 4 }),
        empty: h(K.EmptyState, { icon: "📁", title: "No files yet", description: "Upload one to get started.",
                                 action: h(K.Button, { variant: "primary", onClick: dlg.show }, "Upload") }) },
      function (files) { return h("ul", null, files.map(function (f) { return h("li", { key: f.id }, f.name); })); }),
    h(K.Dialog, { open: dlg.open, onClose: dlg.hide, title: "Upload", footer: h(K.Button, { onClick: dlg.hide }, "Close") }, "..."));
}
```

## API

**Hooks**
- `useAsync(fn, deps)` -> `{ status: "loading"|"success"|"error", data, error, reload }`. Ignores stale responses; keeps old data while reloading.
- `useDisclosure(initial)` -> `{ open, show, hide, toggle }`.
- `useId(prefix)` -> a stable id for label/description wiring.
- `useDebounce(value, ms)` -> the value once it has stopped changing for `ms` (default 300).
- `useToast()` -> `{ push(message, { tone, duration }), dismiss(id) }`; needs a `ToastProvider` above it (a safe no-op without one).

**Components**
- `Button` `{ variant: primary|secondary|ghost|danger, size: sm|md, loading, disabled, icon, type, onClick }`. `loading` blocks double-submit but keeps focus.
- `Spinner` `{ label }`, `Skeleton` `{ lines, width, height }`.
- `Alert` `{ tone: info|success|warning|danger, title, action, onDismiss }`. Danger is announced assertively.
- `EmptyState` `{ icon, title, description, action }`.
- `AsyncView` `{ state, skeleton, empty, isEmpty, errorTitle, children(data) }`: the one place for loading, error (with retry), empty and data.
- `Field` `{ label, hint, error, required, id, children(inputProps) }`: spread `inputProps` onto your input and the label, hint and error are wired for screen readers.
- `Tabs` `{ tabs: [{id,label,panel,disabled}], value, onChange, defaultValue, label }`: arrow keys, Home/End, roving tabindex.
- `Dialog` `{ open, onClose, title, footer, alert }`: focus trap, Escape and backdrop close, focus returns to the opener, scroll lock, bottom sheet on phones.
- `Badge` `{ tone: neutral|info|success|warning|danger }`. The text carries the meaning; color only reinforces it.
- `Switch` `{ checked, onChange(bool), label, disabled }`: a real `role=switch` button named by its label.
- `SearchInput` `{ label, onSearch(q), delay, placeholder, defaultValue }`: debounced, never fires on first render, Enter and Clear fire at once.
- `ConfirmDialog` `{ open, onClose, onConfirm(), title, confirmLabel, cancelLabel, danger }`: an `alertdialog` with focus on Cancel; a rejected `onConfirm` shows its message and stays open, a resolved one closes it.
- `ToastProvider`: wrap a page once. Success/info toasts clear after 5 s (hover or focus pauses them); errors stay until dismissed. Polite live region, max 5 shown.
- `DataTable` `{ caption, columns: [{key,label,render,sortable,align}], rows, rowKey, sort, onSort(key,dir), loading, error, onRetry, empty, skeletonRows, onRowClick }`: a real table with `aria-sort`; sorting is the caller's so it works with server paging. Scrolls inside its own focusable region on narrow screens.
- `Pagination` `{ page, pageCount, onChange(page), label }`: `aria-current="page"`, ellipsis for long ranges, hidden for one page.

### Example: a searchable, sortable, paged list

```js
function Reports() {
  var toast = K.useToast();
  var q = React.useState(""), sort = React.useState({ key: "name", dir: "asc" }), page = React.useState(1);
  var state = K.useAsync(function () {
    return SDK.fetchJSON("/api/reports?q=" + encodeURIComponent(q[0]) + "&sort=" + sort[0].key + "&dir=" + sort[0].dir + "&page=" + page[0]);
  }, [q[0], sort[0].key, sort[0].dir, page[0]]);
  var d = state.data || { items: [], pages: 1 };
  return h("div", null,
    h(K.SearchInput, { label: "Search reports", onSearch: function (v) { q[1](v); page[1](1); } }),
    h(K.DataTable, { caption: "Reports", rows: d.items, loading: state.status === "loading", error: state.error, onRetry: state.reload,
      sort: sort[0], onSort: function (key, dir) { sort[1]({ key: key, dir: dir }); },
      columns: [{ key: "name", label: "Name", sortable: true }, { key: "status", label: "Status", render: function (r) { return h(K.Badge, { tone: r.ok ? "success" : "warning" }, r.status); } }],
      empty: h(K.EmptyState, { title: "No reports match", description: "Try a different search." }) }),
    h(K.Pagination, { page: page[0], pageCount: d.pages, onChange: page[1], label: "Report pages" }));
}
// Wrap the page once: h(K.ToastProvider, null, h(Reports))
```

## Best practices
- Pick the state through `AsyncView` rather than hand-writing `if (loading)` branches, so every list gets all four states.
- Always give `Tabs` and `Spinner` a `label`; give icon-only buttons an `attrs: { "aria-label": ... }`.
- Keep page-specific styling in the page's own stylesheet; add to the kit only what at least two pages need.
- Tests: `tests/kit.test.js` covers the state and keyboard behavior in jsdom; extend it with each new component.
