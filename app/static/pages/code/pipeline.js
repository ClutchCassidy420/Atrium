/* The Pipeline tab of Agentic coding: ideas move through Research, Plan, Build and Review. Each stage is a card on the task board that a worker
   runs; the notes (idea, research, plan, review) are files in the notes project, and Build gets a coding project of its own.
   Ask mode: every stage waits for a click. Approve everything (per idea): the next stage starts when the last one finishes.
   Talks to /api/pipeline (app/pipeline_backend.py) and reads the notes through /api/code/file. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, UI = window.__APP_UI__;
  if (!SDK || !UI) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect, useRef = SDK.hooks.useRef;
  var fetchJSON = SDK.fetchJSON, API = "/api/pipeline", CODE = "/api/code";
  var JSONH = { "Content-Type": "application/json" };
  function errText(e) { var m = String((e && e.message) || e || "Something went wrong"), i = m.indexOf("{"); if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* plain text */ } } return m.replace(/^\d+:\s*/, ""); }
  function send(method, url, body) { return fetchJSON(url, { method: method, headers: JSONH, body: body === undefined ? undefined : JSON.stringify(body) }); }

  var LABEL = { idea: "Idea", research: "Research", plan: "Plan", build: "Build", review: "Review", done: "Done" };
  var DOES = {
    research: "{agent} searches the web, reads the best sources and writes what is found to research.md: what exists, what it would take, risks and a recommendation. Nothing is built at this stage.",
    plan: "{agent} turns the idea and the research into plan.md: goal, scope, steps, how it will be tested and the risks. Nothing is built at this stage.",
    build: "A coding project is created for this idea and {agent} builds it there, following the plan, committing as she goes. This is the long step.",
    review: "{agent} checks the finished project against the plan, tries it, and writes review.md with a verdict.",
    done: "The idea is marked finished on the board."
  };
  var BUTTON = { research: "Start research", plan: "Write the plan", build: "Start building", review: "Review the work", done: "Mark finished" };
  var STATE_TEXT = { waiting: "Waiting for you", working: "Working…", blocked: "Blocked: needs you", finished: "Stage finished", missing: "Card missing" };

  function Steps(p) {
    var order = ["idea", "research", "plan", "build", "review", "done"], at = order.indexOf(p.stage);
    return h("ol", { className: "pl-steps", "aria-label": "Stages" }, order.map(function (s, i) {
      var cls = i < at || (i === at && (p.state === "finished" || s === "done")) ? " pl-done" : i === at ? " pl-now pl-" + p.state : "";
      return h("li", { key: s, className: "pl-step" + cls }, h("span", { className: "pl-dot" }), h("span", null, LABEL[s]));
    }));
  }

  function Notes(p) {
    var f_ = useState(p.first), file = f_[0], setFile = f_[1];
    var t_ = useState(null), text = t_[0], setText = t_[1];
    useEffect(function () { setFile(p.first); }, [p.id]);
    useEffect(function () {
      var dead = false; setText(null);
      var path = p.files[file];
      if (!path) return;
      fetchJSON(CODE + "/file?project=" + encodeURIComponent(p.project) + "&path=" + encodeURIComponent(path)).then(function (r) { if (!dead) setText(r && r.text != null ? r.text : ""); }, function () { if (!dead) setText(false); });
      return function () { dead = true; };
    }, [file, p.id, p.stamp]);
    var names = ["idea", "research", "plan", "review"];
    return h("div", { className: "pl-notes" },
      h("div", { className: "pl-tabs", role: "tablist" }, names.map(function (n) { return h("button", { key: n, type: "button", role: "tab", "aria-selected": file === n ? "true" : "false", className: "pl-tab" + (file === n ? " pl-on" : ""), onClick: function () { setFile(n); } }, LABEL[n]); })),
      text === null ? h(window.__APP_UI__.kit.Loading, { label: "Loading…" }) : text === false || !text ? h("p", { className: "pl-muted" }, "Not written yet.") : h("pre", { className: "pl-text" }, text));
  }

  function Panel(p) {
    var agentName = (UI.config && UI.config().agentName) || "The agent";
    var i = p.idea, c_ = useState(""), confirm = c_[0], setConfirm = c_[1];
    var b_ = useState(false), busy = b_[0], setBusy = b_[1], e_ = useState(""), err = e_[0], setErr = e_[1];
    useEffect(function () { setConfirm(""); setErr(""); }, [i.id]);
    function go(fn) { setBusy(true); setErr(""); fn().then(function () { setBusy(false); setConfirm(""); p.onChanged(); }, function (e) { setBusy(false); setConfirm(""); setErr(errText(e)); p.onChanged(); }); }
    var next = i.next;
    function chatAt(project, file) { p.onChat(project, file ? "@" + file + " " : ""); }
    var latest = i.stage === "research" || i.stage === "plan" || i.stage === "review" ? i.files[i.stage] : i.stage === "idea" ? i.files.idea : null;
    return h("aside", { className: "pl-panel", "aria-label": "Idea: " + i.title },
      h("div", { className: "pl-ptop" }, h("h2", { className: "pl-title" }, i.title), h("button", { type: "button", className: "cd-btn", onClick: p.onClose, "aria-label": "Close" }, "Close")),
      h(Steps, { stage: i.stage, state: i.state }),
      h("p", { className: "pl-status pl-st-" + i.state }, STATE_TEXT[i.state] || i.state, i.card && i.card.summary ? " · " + i.card.summary : "", i.card && i.card.error ? " · " + i.card.error : ""),
      i.auto_error ? h("p", { className: "cd-err", role: "alert" }, "Approve everything could not start the next stage: " + i.auto_error.detail) : null,
      err ? h("div", { className: "cd-err", role: "alert" }, err) : null,
      confirm ? h("div", { className: "pl-confirm", role: "alertdialog" }, h("p", null, DOES[next].split("{agent}").join(agentName)), next === "build" ? h("p", { className: "pl-muted" }, "Make sure you have read the plan first.") : null,
        h("div", { className: "pl-row" }, h("button", { type: "button", className: "cd-btn cd-primary", disabled: busy, onClick: function () { go(function () { return send("POST", API + "/ideas/" + i.id + "/advance"); }); } }, "Yes, " + BUTTON[next].toLowerCase()),
          h("button", { type: "button", className: "cd-btn", onClick: function () { setConfirm(""); } }, "Cancel")))
        : h("div", { className: "pl-row pl-wrap" },
          next ? h("button", { type: "button", className: "cd-btn cd-primary", disabled: busy, onClick: function () { i.auto && i.stage !== "idea" ? go(function () { return send("POST", API + "/ideas/" + i.id + "/advance"); }) : setConfirm("go"); } }, BUTTON[next]) : null,
          h("label", { className: "pl-switch", title: "Ask me: each stage waits for your click. Approve everything: the next stage starts as soon as the last one finishes (after you start the first)." },
            h("input", { type: "checkbox", checked: !!i.auto, onChange: function (e) { var on = e.target.checked; go(function () { return send("POST", API + "/ideas/" + i.id + "/auto", { on: on }); }); } }), " Approve everything"),
          h("button", { type: "button", className: "cd-btn", onClick: function () { chatAt(p.notes, latest); } }, "Talk it over in chat"),
          i.project ? h("button", { type: "button", className: "cd-btn", onClick: function () { chatAt(i.project, ""); } }, "Open project: " + i.project) : null,
          h("a", { className: "cd-btn pl-link", href: "/kanban" }, "Open the board"),
          h("button", { type: "button", className: "cd-btn", disabled: busy, onClick: function () { if (window.confirm("Take this idea off the pipeline? Its card and notes stay.")) { go(function () { return send("POST", API + "/ideas/" + i.id + "/drop"); }); p.onClose(); } } }, "Take off pipeline")),
      i.auto && i.stage !== "done" ? h("p", { className: "pl-muted" }, "Approve everything is on: the next stage starts by itself when this one finishes. It stops if a card is blocked.") : null,
      h(Notes, { id: i.id, project: p.notes, files: i.files, first: i.stage === "idea" ? "idea" : i.stage === "build" ? "plan" : i.stage === "done" ? "review" : i.stage, stamp: p.stamp }));
  }

  function Pipeline(props) {
    var d_ = useState(null), data = d_[0], setData = d_[1], e_ = useState(""), err = e_[0], setErr = e_[1];
    var o_ = useState(""), open = o_[0], setOpen = o_[1], ad_ = useState(false), adding = ad_[0], setAdding = ad_[1];
    var t_ = useState(""), title = t_[0], setTitle = t_[1], b_ = useState(""), body = b_[0], setBody = b_[1], bz_ = useState(false), busy = bz_[0], setBusy = bz_[1];
    var st_ = useState(0), stamp = st_[0], setStamp = st_[1];
    var sc_ = useState(""), scope = sc_[0], setScope = sc_[1];                  // "all" = every project's ideas; otherwise the project picked in the page
    var alive = useRef(true);
    var proj = scope === "all" ? "all" : (props.project || "idea-pipeline");
    var projRef = useRef(proj); projRef.current = proj;
    function load() { return fetchJSON(API + "/ideas?project=" + encodeURIComponent(projRef.current)).then(function (r) { if (alive.current) { setData(r); setErr(""); setStamp(function (n) { return n + 1; }); } }, function (e) { if (alive.current) setErr(errText(e)); }); }
    useEffect(function () { alive.current = true; var stop = window.atriumEvery(load, 15000); return function () { alive.current = false; stop(); }; }, []);
    useEffect(function () { setOpen(""); setAdding(false); setData(null); load(); }, [proj]);                    // another project: its own board
    function add(ev) {
      ev.preventDefault();
      if (!title.trim() || busy) return;
      setBusy(true);
      send("POST", API + "/ideas", { title: title.trim(), body: body.trim() || null, auto: props.mode === "auto", project: proj === "all" ? undefined : proj }).then(function (r) { setBusy(false); setTitle(""); setBody(""); setAdding(false); setOpen(r.id); load(); if (props.onProjects) props.onProjects(); }, function (e2) { setBusy(false); setErr(errText(e2)); });
    }
    var projSel = h("label", { className: "pl-projsel" }, h("span", { className: "pl-muted" }, "Project"),
            h("select", { className: "cd-select", "aria-label": "Project", value: scope === "all" ? "__all__" : proj, onChange: function (e) { var v = e.target.value; if (v === "__all__") setScope("all"); else { setScope(""); if (props.onPick) props.onPick(v); } } },
              h("option", { value: "__all__" }, "All projects"),
              (function () { var names = (props.projects || []).slice(); if (names.indexOf("idea-pipeline") < 0) names.unshift("idea-pipeline"); return names.map(function (n) { return h("option", { key: n, value: n }, n === "idea-pipeline" ? "idea-pipeline (the incubator)" : n); }); })()));
    if (!data) return h("div", { className: "pl-page" }, h("header", { className: "pl-head" }, h("div", null, h("h1", { className: "pl-h1" }, "Idea pipeline"), projSel)),
      err ? h(window.__APP_UI__.kit.Alert, { tone: "danger" }, err) : h(window.__APP_UI__.kit.Loading, { label: "Loading the pipeline…" }));
    var cols = ["idea", "research", "plan", "build", "review", "done"], sel = data.ideas.filter(function (x) { return x.id === open; })[0];
    return h("div", { className: "pl-page" },
      h("header", { className: "pl-head" },
        h("div", null, h("h1", { className: "pl-h1" }, "Idea pipeline"),
          projSel,
          h("p", { className: "pl-muted" }, data.ideas.length + " idea" + (data.ideas.length === 1 ? "" : "s") + " · a stage starts only when you say so" + (props.mode === "auto" ? ", unless an idea is on Approve everything (new ideas start that way)" : ""))),
        h("button", { type: "button", className: "cd-btn cd-primary", onClick: function () { setAdding(!adding); } }, adding ? "Cancel" : "+ Add an idea")),
      adding ? h("form", { className: "pl-add", onSubmit: add },
        h("input", { className: "cd-input", value: title, maxLength: 200, autoFocus: true, placeholder: "The idea, in a line", "aria-label": "Idea", onChange: function (e) { setTitle(e.target.value); } }),
        h("textarea", { className: "cd-input pl-ta", rows: 4, value: body, maxLength: 20000, placeholder: "More detail (optional): what you want, why, links, limits…", "aria-label": "Details", onChange: function (e) { setBody(e.target.value); } }),
        h("div", { className: "pl-row" }, h("button", { type: "submit", className: "cd-btn cd-primary", disabled: busy || !title.trim() }, "Add to the pipeline"), h("span", { className: "pl-muted" }, (proj === "all" ? "It goes to idea-pipeline (the incubator). " : proj === "idea-pipeline" ? "" : "It belongs to " + proj + " and is built there. ") + "It is saved with a notes file and waits. Nothing runs until you start research."))) : null,
      err ? h("div", { className: "cd-err", role: "alert" }, err) : null,
      h("div", { className: "pl-layout" + (sel ? " pl-with" : "") },
        h("div", { className: "pl-board" }, cols.map(function (c) {
          var list = data.ideas.filter(function (x) { return x.stage === c; });
          return h("section", { key: c, className: "pl-col", "aria-label": LABEL[c] },
            h("header", { className: "pl-col-h" }, h("h2", null, LABEL[c]), h("span", { className: "pl-count" }, list.length)),
            list.length ? list.map(function (x) {
              return h("button", { key: x.id, type: "button", className: "pl-card pl-st-" + x.state + (open === x.id ? " pl-sel" : ""), onClick: function () { setOpen(x.id); } },
                h("div", { className: "pl-card-t" }, x.state === "working" ? h("span", { className: "pl-live" }) : null, x.title),
                h("div", { className: "pl-chips" }, scope === "all" ? h("span", { className: "pl-chip" }, x.home) : null, h("span", { className: "pl-chip" }, STATE_TEXT[x.state] || x.state), x.auto ? h("span", { className: "pl-chip" }, "auto") : null, x.next ? h("span", { className: "pl-chip pl-next" }, "next: " + LABEL[x.next].toLowerCase()) : null));
            }) : h("p", { className: "pl-empty" }, c === "idea" ? "New ideas wait here." : "None."));
        })),
        sel ? h(Panel, { idea: sel, notes: sel.home || data.notes_project, stamp: stamp, onClose: function () { setOpen(""); }, onChanged: function () { load(); if (props.onProjects) props.onProjects(); }, onChat: props.onChat }) : null));
  }

  window.__APP_PIPELINE__ = Pipeline;
})();
