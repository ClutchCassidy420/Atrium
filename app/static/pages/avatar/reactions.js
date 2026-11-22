/* The 3D character's reactions to what happens (shared by the Live agent page and the chat's 3D companion).
   She notices: you send a message, work starts, an approval is needed, a job finishes, a failure (one failure: a wince; failing again within ten
   minutes: she bangs the desk or shakes the laptop and swears, a bit more each time), an answer arrives, and you coming back after a while away.
   Adapted from the Atrium Loom Mission Control hero. Usage:
     var r = window.__AVATAR_REACT__.create(function () { return deskController; }, { sound: function () { return true; }, speakUrl: "/api/...", onCaption: function (word) {} });
     r.event("send" | "work" | "approval" | "done" | "error" | "back" | "mail");  r.answered();  r.state(prev, cur);  r.watch() -> stop function */
(function () {
  "use strict";
  var ERROR_WINDOW_MS = 10 * 60 * 1000;     // failures this close together count as "again"
  var BACK_AFTER_MS = 5 * 60 * 1000;        // away at least this long = "welcome back"
  var QUIET_AFTER_SEND_MS = 2500;           // she has only just looked up at you: no second move straight away
  var DONE_QUIET_MS = 5000;                 // an answer a moment ago already told you the work is done

  function create(getCtl, opts) {
    opts = opts || {};
    var errTimes = [], answerAt = 0, lastSend = 0, doneTimer = null;

    function event(kind) {
      var c = getCtl(); if (!c) return;
      try {
        if (kind === "error") {
          var now = Date.now();
          errTimes = errTimes.filter(function (t) { return now - t < ERROR_WINDOW_MS; }); errTimes.push(now);
          var n = errTimes.length;
          if (n < 2) { c.react("error"); return; }
          if (opts.onCaption) opts.onCaption("");
          var p = c.fedUp(Math.min(3, n - 1), { sound: !!(opts.sound && opts.sound()), url: opts.speakUrl, onCaption: opts.onCaption || function () {} });
          if (p && p.catch) p.catch(function () { if (opts.onCaption) opts.onCaption(""); });
          return;
        }
        if (kind === "send") lastSend = Date.now();
        if (kind === "work" && Date.now() - lastSend < QUIET_AFTER_SEND_MS) return;
        c.react(kind);
      } catch (x) { /* a reaction must never break the page */ }
    }

    // an answer arrived: it clears the failure count and means the "done" cue would be redundant
    function answered() { answerAt = Date.now(); errTimes = []; }

    // what the agent is doing changed (idle | working | waiting | offline)
    function state(prev, cur) {
      if (doneTimer) { clearTimeout(doneTimer); doneTimer = null; }
      if (prev === cur) return;
      if (cur === "working" && prev === "idle") event("work");
      else if (cur === "waiting") event("approval");
      else if (cur === "idle" && prev === "working") doneTimer = setTimeout(function () { doneTimer = null; if (Date.now() - answerAt > DONE_QUIET_MS) event("done"); }, 2000);
    }

    // the app's own signals (the chat sends these), and you coming back to the tab
    function watch() {
      var hiddenAt = 0;
      function onEv(ev) { var d = ev && ev.detail, k = d && d.kind; if (k === "send" || k === "error" || k === "mail") event(k); }
      function onVis() { if (document.hidden) hiddenAt = Date.now(); else if (hiddenAt && Date.now() - hiddenAt > BACK_AFTER_MS) { hiddenAt = 0; event("back"); } else hiddenAt = 0; }
      window.addEventListener("atrium:avatar-event", onEv); document.addEventListener("visibilitychange", onVis);
      return function () {
        window.removeEventListener("atrium:avatar-event", onEv); document.removeEventListener("visibilitychange", onVis);
        if (doneTimer) { clearTimeout(doneTimer); doneTimer = null; }
      };
    }

    return { event: event, answered: answered, state: state, watch: watch };
  }

  window.__AVATAR_REACT__ = { create: create };
})();
