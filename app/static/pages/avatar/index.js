/* Avatar: pick and customise the agent's drawn character, with a live preview beside the controls.
   Same two-pane layout as Settings (section list at the left), plus a preview pane that stays in view. The look is kept in this browser. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__, UI = window.__APP_UI__, A = window.__AVATARS__;
  if (!SDK || !REG || !UI || !A) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect;
  var THEMES = A.THEMES, Orb = A.Orb;

  function any(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
  var HATCH = "repeating-linear-gradient(45deg,#3a3f55,#3a3f55 3px,#161a2c 3px,#161a2c 6px)";
  var EYES = [["auto", "linear-gradient(135deg,#ff9a3c,#ff4c6a)", "Match colour"], ["gold", "#ffd45a", "Gold"], ["white", "#ffffff", "White"], ["green", "#6dff9e", "Green"], ["violet", "#c79bff", "Violet"]];
  var AVATARS = [["watcher", "The Watcher"], ["core", "Energy core"], ["devil", "Devil woman"], ["devilman", "Devil man"]];
  var SECTIONS = [
    { id: "choose", label: "Choose", icon: "★", note: "Pick a character, or start from a ready-made look." },
    { id: "colours", label: "Colours", icon: "◐", note: "The main colour, the eyes, the bow and the background." },
    { id: "features", label: "Features", icon: "✦", note: "The parts that make up the character." },
    { id: "extras", label: "Extras", icon: "✧", note: "Glow, a surprise, and saving a picture." }
  ];

  function Chips(p) {
    return h("div", { className: "av-chips" }, p.options.map(function (o) {
      return h("button", { key: String(o[0]), type: "button", "aria-pressed": p.value === o[0] ? "true" : "false", className: "av-chip" + (p.value === o[0] ? " av-on" : ""), onClick: function () { p.onPick(o[0]); } }, o[1]);
    }));
  }
  function Swatches(p) {
    return h("div", { className: "av-swatches" }, p.options.map(function (o) {
      return h("button", { key: o[0], type: "button", title: o[2] || o[0], "aria-label": o[2] || o[0], "aria-pressed": p.value === o[0] ? "true" : "false", className: "av-swatch" + (p.value === o[0] ? " av-on" : ""), style: { background: o[1] }, onClick: function () { p.onPick(o[0]); } });
    }));
  }
  function Field(p) { return h("div", { className: "av-field" }, h("div", { className: "av-label" }, p.label), p.children); }
  function Check(p) {
    return h("label", { className: "av-check" }, h("input", { type: "checkbox", checked: !!p.value, onChange: function (e) { p.onChange(e.target.checked); } }), " " + p.label);
  }

  function useLook() {
    var s = useState(A.getLook()), look = s[0], set = s[1];
    useEffect(function () {
      function on() { set(A.getLook()); }
      window.addEventListener("atrium:look", on);
      return function () { window.removeEventListener("atrium:look", on); };
    }, []);
    return look;
  }

  function savePicture(square, agent) {
    var cv = document.querySelector(".av-preview canvas");
    if (!cv) return;
    var out = cv;
    if (square) {
      var sq = document.createElement("canvas"); sq.width = 512; sq.height = 512;
      var sc = sq.getContext("2d"), bg = sc.createRadialGradient(256, 256, 40, 256, 256, 330);
      bg.addColorStop(0, "#14233a"); bg.addColorStop(1, "#05070f");
      sc.fillStyle = bg; sc.fillRect(0, 0, 512, 512);
      var hh = 470, ww = hh * cv.width / cv.height;
      sc.drawImage(cv, (512 - ww) / 2, 22, ww, hh);
      out = sq;
    }
    out.toBlob(function (b) {
      if (!b) return;
      var a = document.createElement("a"); a.href = URL.createObjectURL(b);
      a.download = String(agent || "avatar").toLowerCase().replace(/[^a-z0-9]+/g, "-") + (square ? "-profile.png" : "-face.png");
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
    }, "image/png");
  }

  function randomize(look) {
    var p = { theme: any(Object.keys(THEMES)), glow: 0.7 + Math.random() * 0.8 };
    if (look.avatar === "watcher") { p.tentacles = any([6, 9, 12]); p.eyes = any(Object.keys(A.EYE_COLORS)); p.books = Math.random() > 0.2; p.skills = any(["off", "few", "many"]); p.runes = Math.random() > 0.2; }
    else if (look.avatar === "core") { p.rings = any([1, 2, 3]); p.lightning = Math.random() > 0.2; }
    else {
      var man = look.avatar === "devilman";
      p.hair = man ? any(["short", "slick", "long", "flame"]) : any(["bob", "long", "short", "flame"]);
      if (man) p.beard = any(["none", "goatee", "full"]);
      p.hairColor = any(Object.keys(A.HAIR_COLORS)); p.horns = any(["curved", "long", "small", "crown"]); p.wings = any(["bat", "large", "none"]);
      p.tail = Math.random() > 0.3; p.prop = any(["fork", "staff", "orb", "none"]); p.outfit = man ? any(["suit", "coat", "casual"]) : any(["dress", "gown", "short"]);
      p.eyes = any(Object.keys(A.EYE_COLORS)); p.flames = Math.random() > 0.2;
    }
    A.setLook(p);
  }

  function pickAvatar(look, v) {
    var pp = { avatar: v };
    if ((v === "devil" || v === "devilman") && look.theme === "abyss") pp.theme = "crimson";
    if (v === "watcher" && look.theme === "crimson") pp.theme = "abyss";
    if (v === "devilman") { if (look.hair === "bob") pp.hair = "short"; if (["dress", "gown", "short"].indexOf(look.outfit) >= 0) pp.outfit = "suit"; }
    if (v === "devil") { if (look.hair === "slick") pp.hair = "bob"; if (["suit", "coat", "casual"].indexOf(look.outfit) >= 0) pp.outfit = "dress"; }
    A.setLook(pp);
  }

  function panelFor(id, look, agent) {
    function pick(key) { return function (v) { var p = {}; p[key] = v; A.setLook(p); }; }
    var av = look.avatar, man = av === "devilman", devil = av === "devil" || man;
    var swatchFor = function (c) { var t = THEMES[c]; return "linear-gradient(135deg,rgb(" + t.core.join(",") + "),rgb(" + t.b.join(",") + "))"; };
    var cap = function (k) { return k.charAt(0).toUpperCase() + k.slice(1); };
    if (id === "choose") {
      return [
        h(Field, { key: "av", label: "Character" }, h(Chips, { value: av, onPick: function (v) { pickAvatar(look, v); }, options: AVATARS })),
        h(Field, { key: "pre", label: "Ready-made looks" }, h("div", { className: "av-chips" }, A.LOOK_PRESETS.filter(function (p) { return p.look.avatar === av; }).map(function (p) {
          return h("button", { key: p.name, type: "button", className: "av-chip", onClick: function () { A.setLook(A.mergeLook(A.LOOK_DEFAULT, p.look)); } }, p.name);
        })))
      ];
    }
    if (id === "colours") {
      return [
        h(Field, { key: "c", label: "Colour" }, h(Swatches, { value: look.theme, onPick: pick("theme"), options: Object.keys(THEMES).map(function (c) { return [c, swatchFor(c), THEMES[c].label]; }) })),
        av === "core" ? null : h(Field, { key: "eyes", label: "Eyes" }, h(Swatches, { value: look.eyes, onPick: pick("eyes"), options: EYES })),
        devil && look.hair !== "flame" ? h(Field, { key: "hc", label: "Hair colour" }, h(Swatches, { value: look.hairColor, onPick: pick("hairColor"), options: [["auto", "linear-gradient(135deg,#555,#222)", "Match colour"], ["jet", "#0b0b12", "Jet"], ["silver", "#c9cde0", "Silver"], ["blonde", "#e3c068", "Blonde"], ["crimson", "#8a1030", "Crimson"], ["white", "#f2f2f7", "White"]] })) : null,
        h(Field, { key: "bg", label: "Background" }, h(Chips, { value: look.scene, onPick: pick("scene"), options: [["galaxy", "Galaxy"], ["cosmic", "Cosmic"], ["night", "Cosmic at night"], ["cyberpunk", "Cyberpunk"], ["matrix", "Matrix"], ["synthwave", "Synthwave"], ["aurora", "Aurora"], ["deepsea", "Deep sea"], ["inferno", "Inferno"], ["gothic", "Gothic"]] })),
        h(Field, { key: "bow", label: "Bow" }, h(Swatches, { value: look.bow, onPick: pick("bow"), options: [["none", HATCH, "No bow"]].concat(Object.keys(A.BOW_COLORS).map(function (k) { return [k, A.BOW_COLORS[k], cap(k) + " bow"]; })) })),
        h(Field, { key: "tie", label: "Bow tie" }, h(Swatches, { value: look.tie, onPick: pick("tie"), options: [["none", HATCH, "No bow tie"]].concat(Object.keys(A.TIE_COLORS).map(function (k) { return [k, A.TIE_COLORS[k], cap(k) + " bow tie"]; })) }))
      ];
    }
    if (id === "features") {
      if (av === "watcher") return [
        h(Field, { key: "t", label: "Tentacles" }, h(Chips, { value: look.tentacles, onPick: pick("tentacles"), options: [[6, "Few"], [9, "Many"], [12, "Swarm"]] })),
        h(Field, { key: "s", label: "Skill icons" }, h(Chips, { value: look.skills, onPick: pick("skills"), options: [["off", "Off"], ["few", "Few"], ["many", "Many"]] })),
        h(Field, { key: "k", label: "Around it" }, h(Check, { label: "Floating tomes", value: look.books, onChange: pick("books") }), h(Check, { label: "Drifting runes", value: look.runes, onChange: pick("runes") }))
      ];
      if (av === "core") return [
        h(Field, { key: "r", label: "Rings" }, h(Chips, { value: look.rings, onPick: pick("rings"), options: [[1, "One"], [2, "Two"], [3, "Three"]] })),
        h(Field, { key: "l", label: "Effects" }, h(Check, { label: "Lightning", value: look.lightning, onChange: pick("lightning") }))
      ];
      return [
        h(Field, { key: "h", label: "Hair" }, h(Chips, { value: look.hair, onPick: pick("hair"), options: man ? [["short", "Short"], ["slick", "Slick"], ["long", "Long"], ["flame", "Flame"]] : [["bob", "Bob"], ["long", "Long"], ["short", "Short"], ["flame", "Flame"]] })),
        man ? h(Field, { key: "b", label: "Facial hair" }, h(Chips, { value: look.beard, onPick: pick("beard"), options: [["none", "None"], ["goatee", "Goatee"], ["full", "Full beard"]] })) : null,
        h(Field, { key: "ho", label: "Horns" }, h(Chips, { value: look.horns, onPick: pick("horns"), options: [["curved", "Curved"], ["long", "Long"], ["small", "Small"], ["crown", "Crown"]] })),
        h(Field, { key: "w", label: "Wings" }, h(Chips, { value: look.wings, onPick: pick("wings"), options: [["bat", "Bat"], ["large", "Large"], ["none", "None"]] })),
        h(Field, { key: "p", label: "Holding" }, h(Chips, { value: look.prop, onPick: pick("prop"), options: [["fork", "Pitchfork"], ["staff", "Staff"], ["orb", "Fire orb"], ["none", "Nothing"]] })),
        h(Field, { key: "o", label: "Outfit" }, h(Chips, { value: look.outfit, onPick: pick("outfit"), options: man ? [["suit", "Suit"], ["coat", "Long coat"], ["casual", "Casual"]] : [["dress", "Dress"], ["gown", "Gown"], ["short", "Short"]] })),
        h(Field, { key: "x", label: "Also" }, h(Check, { label: "Tail", value: look.tail, onChange: pick("tail") }), h(Check, { label: "Flames at the feet", value: look.flames, onChange: pick("flames") }))
      ];
    }
    return [
      h(Field, { key: "g", label: "Glow" }, h("input", { type: "range", min: 0.4, max: 1.6, step: 0.05, value: look.glow, "aria-label": "Glow", className: "av-range", onChange: function (e) { A.setLook({ glow: parseFloat(e.target.value) }); } })),
      h(Field, { key: "a", label: "Actions" }, h("div", { className: "av-actions" },
        h("button", { type: "button", className: "av-btn", onClick: function () { randomize(look); } }, "Surprise me"),
        h("button", { type: "button", className: "av-btn", onClick: function () { savePicture(false, agent); } }, "Save picture"),
        h("button", { type: "button", className: "av-btn", onClick: function () { savePicture(true, agent); } }, "Profile picture"),
        h("button", { type: "button", className: "av-btn", onClick: A.resetLook }, "Back to default"))),
      h("p", { className: "av-note" }, "Changes apply at once and are remembered in this browser.")
    ];
  }

  function startSection() {
    var id = (window.location.hash || "").replace(/^#/, "");
    return SECTIONS.some(function (x) { return x.id === id; }) ? id : SECTIONS[0].id;
  }

  function Page() {
    var agent = UI.config().agentName || "your agent";
    var look = useLook();
    var s_ = useState(startSection()), cur = s_[0], setCur = s_[1];
    var st_ = useState("idle"), state = st_[0], setState = st_[1];
    useEffect(function () {
      function onHash() { setCur(startSection()); }
      window.addEventListener("hashchange", onHash);
      return function () { window.removeEventListener("hashchange", onHash); };
    }, []);
    function pick(id) { setCur(id); try { window.history.replaceState(null, "", "#" + id); } catch (x) { /* ignore */ } }
    var sec = SECTIONS.filter(function (x) { return x.id === cur; })[0] || SECTIONS[0];
    return h("div", { className: "av-page" },
      h("div", { className: "av-title" }, "Avatar"),
      h("div", { className: "av-layout" },
        h("nav", { className: "av-nav", "aria-label": "Avatar sections" }, SECTIONS.map(function (x) {
          return h("button", { key: x.id, type: "button", className: "av-navitem" + (x.id === sec.id ? " av-current" : ""), "aria-current": x.id === sec.id ? "page" : null, onClick: function () { pick(x.id); } },
            h("span", { className: "av-navicon", "aria-hidden": "true" }, x.icon), x.label);
        })),
        h("section", { className: "av-panel", key: sec.id }, h("h2", { className: "av-panel-h" }, sec.label), h("div", { className: "av-sub" }, sec.note), panelFor(sec.id, look, agent)),
        h("aside", { className: "av-preview", "aria-label": "Preview" },
          h("div", { className: "av-stage" }, h(A.Galaxy, { state: state }), h(Orb, { state: state })),
          h("div", { className: "av-previewbar" }, h("span", { className: "av-label" }, "Preview"),
            h(Chips, { value: state, onPick: setState, options: [["idle", "Idle"], ["working", "Working"], ["waiting", "Waiting"], ["offline", "Offline"]] })))));
  }

  window.__APP_SUBPAGES__ = Object.assign(window.__APP_SUBPAGES__ || {}, { avatar: Page });     // shown inside Settings
})();
