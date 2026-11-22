/* Self-test page for the shim. It is written the way the Atrium plugins are: it starts from the two globals, uses only
   the SDK pieces they use (React.createElement, useState/useEffect/useRef/useCallback, fetchJSON) and registers itself. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__;
  if (!SDK || !REG) return;
  var h = SDK.React.createElement, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect, useRef = SDK.hooks.useRef, useCallback = SDK.hooks.useCallback;

  function Page() {
    var a = useState("checking…"), me = a[0], setMe = a[1];
    var b = useState(0), n = b[0], setN = b[1];
    var c = useState("not run"), bad = c[0], setBad = c[1];
    var ref = useRef(null);
    useEffect(function () {
      SDK.fetchJSON("/api/me").then(function (j) { setMe("role: " + j.role); }, function (e) { setMe("error: " + e.message); });
      SDK.fetchJSON("/api/me-does-not-exist").then(function () { setBad("unexpected success"); }, function (e) { setBad("threw as expected: " + e.message.slice(0, 40)); });
      if (ref.current) ref.current.setAttribute("data-mounted", "yes");
    }, []);
    var inc = useCallback(function () { setN(function (x) { return x + 1; }); }, []);
    return h("div", { className: "vn-card", ref: ref, id: "selftest" },
      h("h2", null, "Shim self-test"),
      h("p", { id: "st-me" }, "fetchJSON /api/me → " + me),
      h("p", { id: "st-bad" }, "fetchJSON on a missing route → " + bad),
      h("button", { id: "st-btn", onClick: inc }, "Clicked " + n + " times"));
  }
  REG.register("selftest", Page);
})();
