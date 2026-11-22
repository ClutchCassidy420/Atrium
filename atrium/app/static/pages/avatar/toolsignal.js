/* Which tool the agent is really using right now, so the matching tool icon glows (the magnet on her whiteboard, the icon around the drawn Watcher).
   While anything on the page wants it, this looks at /api/tool-activity every few seconds and keeps the answer in window.__ATRIUM_TOOL__
   as { kind, at, name }. Nothing is fetched while the agent is not working or the tab is hidden, and one request serves every part of the page.
     var stop = window.__AVATAR_TOOL__.poll(function () { return isWorking; });                          // just keep the signal fresh
     var stop = window.__AVATAR_TOOL__.feed(function () { return deskController; }, function () { return isWorking; });   // also light the whiteboard magnet */
(function () {
  "use strict";
  var wanted = [], timer = null, GLOW_MS = 25000;

  function anyWorking() { return wanted.some(function (f) { try { return !!f(); } catch (x) { return false; } }); }
  function tick() {
    if (document.hidden || !anyWorking()) return;
    fetch("/api/tool-activity", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
      var l = j && j.latest;
      if (l && l.at) window.__ATRIUM_TOOL__ = { kind: l.kind || "", at: l.at, name: l.name };
    }).catch(function () { /* the glow is optional */ });
  }
  function poll(isWorking) {
    wanted.push(isWorking);
    if (!timer) timer = setInterval(tick, 4000);
    tick();
    return function stop() {
      wanted = wanted.filter(function (f) { return f !== isWorking; });
      if (!wanted.length && timer) { clearInterval(timer); timer = null; }
    };
  }
  function feed(getCtl, isWorking) {
    var stopPoll = poll(isWorking);
    var iv = setInterval(function () {
      var c = getCtl(); if (!c || !c.setTool) return;
      var lt = window.__ATRIUM_TOOL__, hot = (lt && lt.kind && Date.now() - lt.at < GLOW_MS && isWorking()) ? lt.kind : null;
      try { c.setTool(hot); } catch (x) { /* never break the page */ }
    }, 1000);
    return function stop() { stopPoll(); clearInterval(iv); };
  }
  window.__AVATAR_TOOL__ = { poll: poll, feed: feed, GLOW_MS: GLOW_MS };
})();
