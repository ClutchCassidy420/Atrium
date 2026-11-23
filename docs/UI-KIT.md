# Atrium UI kit

Reusable, accessible components for framework and instance pages. Plain script, no build step, built on the same page-host
interface as `common.js`. Nothing in the existing pages uses it yet; it is opt-in, so adopting it is one page at a time.

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

**Components**
- `Button` `{ variant: primary|secondary|ghost|danger, size: sm|md, loading, disabled, icon, type, onClick }`. `loading` blocks double-submit but keeps focus.
- `Spinner` `{ label }`, `Skeleton` `{ lines, width, height }`.
- `Alert` `{ tone: info|success|warning|danger, title, action, onDismiss }`. Danger is announced assertively.
- `EmptyState` `{ icon, title, description, action }`.
- `AsyncView` `{ state, skeleton, empty, isEmpty, errorTitle, children(data) }`: the one place for loading, error (with retry), empty and data.
- `Field` `{ label, hint, error, required, id, children(inputProps) }`: spread `inputProps` onto your input and the label, hint and error are wired for screen readers.
- `Tabs` `{ tabs: [{id,label,panel,disabled}], value, onChange, defaultValue, label }`: arrow keys, Home/End, roving tabindex.
- `Dialog` `{ open, onClose, title, footer, alert }`: focus trap, Escape and backdrop close, focus returns to the opener, scroll lock, bottom sheet on phones.

## Best practices
- Pick the state through `AsyncView` rather than hand-writing `if (loading)` branches, so every list gets all four states.
- Always give `Tabs` and `Spinner` a `label`; give icon-only buttons an `attrs: { "aria-label": ... }`.
- Keep page-specific styling in the page's own stylesheet; add to the kit only what at least two pages need.
- Tests: `tests/kit.test.js` covers the state and keyboard behavior in jsdom; extend it with each new component.
