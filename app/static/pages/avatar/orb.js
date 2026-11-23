/* Atrium avatars: the drawn characters (The Watcher, Energy core, Devil woman, Devil man) and the live canvas that animates them.
   Moved from the Mission Control plugin as it was (labelled adaptation: wrapped as a module that exposes window.__AVATARS__, look kept in this
   browser's localStorage under the same key). Everything is drawn with canvas paths: no images, no downloads. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;
  var h = SDK.React.createElement;
  var useRef = SDK.hooks.useRef, useEffect = SDK.hooks.useEffect, useState = SDK.hooks.useState;
  var AGENT = String(((window.__APP_CONFIG__ || {}).agentName) || "Assistant");   // the cyberpunk scene shows the agent's name
  /* ---------- the mascot: a stylised horned devil woman drawn with canvas paths (no images, no downloads) ---------- */
  // Her look is a small settings object kept in the browser (localStorage "atrium:look") and shared by every page.
  // Idle: sway and a lazy tail. Working: brighter, props swing faster. Waiting: amber, perked up. Offline: grey and still.

  var THEMES = {
    nebula:  { label: "Nebula",  core: [150, 100, 255], a: [130, 180, 255], b: [255, 150, 225], skinTop: "#8a63e8", skinBot: "#3e2a8c", wing: "rgba(30,16,70,0.85)", hair: "#140a30", outfit: "#0a0618", hornTop: "#e8dcff", hornBot: "#3a2a9a" },
    abyss:   { label: "Abyss",   core: [30, 215, 185], a: [90, 240, 215], b: [255, 196, 80], skinTop: "#12332e", skinBot: "#06161a", wing: "rgba(6,34,32,0.85)", hair: "#031210", outfit: "#020a09", hornTop: "#bfffee", hornBot: "#0a3a36" },
    crimson: { label: "Crimson", core: [255, 60, 90], a: [255, 120, 140], b: [255, 170, 60], skinTop: "#e0405a", skinBot: "#9c1d3a", wing: "rgba(70,10,34,0.85)", hair: "#2a0a22", outfit: "#14060f", hornTop: "#ffd9a0", hornBot: "#8a1030" },
    violet:  { label: "Violet",  core: [150, 90, 255], a: [185, 140, 255], b: [255, 140, 220], skinTop: "#a763e8", skinBot: "#5b2a9c", wing: "rgba(40,14,80,0.85)", hair: "#1d0a3a", outfit: "#0c0618", hornTop: "#f3d9ff", hornBot: "#4a1a8a" },
    emerald: { label: "Emerald", core: [40, 220, 150], a: [110, 240, 190], b: [220, 255, 120], skinTop: "#2fbf86", skinBot: "#146a4a", wing: "rgba(8,50,36,0.85)", hair: "#062a1e", outfit: "#03140e", hornTop: "#f0ffd0", hornBot: "#0f6a45" },
    ice:     { label: "Frost",   core: [80, 170, 255], a: [140, 205, 255], b: [200, 240, 255], skinTop: "#5aa7e8", skinBot: "#24508c", wing: "rgba(14,32,70,0.85)", hair: "#0a1a3a", outfit: "#060c1c", hornTop: "#ffffff", hornBot: "#2a5aa0" },
    gold:    { label: "Gold",    core: [255, 190, 60], a: [255, 215, 120], b: [255, 245, 190], skinTop: "#e8a73a", skinBot: "#9a5c12", wing: "rgba(70,40,6,0.85)", hair: "#2a1604", outfit: "#140a02", hornTop: "#fff3c0", hornBot: "#8a5a10" },
    rose:    { label: "Rose",    core: [255, 90, 190], a: [255, 150, 215], b: [255, 210, 120], skinTop: "#f06aa8", skinBot: "#a02a68", wing: "rgba(80,10,52,0.85)", hair: "#3a0a2e", outfit: "#1a0614", hornTop: "#ffe0f0", hornBot: "#a02a68" },
    shadow:  { label: "Shadow",  core: [200, 200, 230], a: [230, 230, 255], b: [255, 255, 255], skinTop: "#8a8aa8", skinBot: "#3e3e58", wing: "rgba(20,20,34,0.9)", hair: "#0e0e18", outfit: "#08080e", hornTop: "#e8e8f8", hornBot: "#4a4a66" }
  };
  var BOW_COLORS = { pink: "#ff6fb5", red: "#ff3b5c", purple: "#a77bff", blue: "#4da3ff", gold: "#ffc94d", white: "#f4f4ff" };
  var TIE_COLORS = { black: "#2e2e40", red: "#d62839", blue: "#2e6bd6", green: "#1fa56b", gold: "#e0a82e", purple: "#7b4fd1" };
  var HAIR_COLORS = { auto: null, jet: "#0b0b12", silver: "#c9cde0", blonde: "#e3c068", crimson: "#8a1030", white: "#f2f2f7" };
  var EYE_COLORS = { auto: null, gold: [255, 212, 90], white: [255, 255, 255], green: [109, 255, 158], violet: [199, 155, 255] };
  var LOOK_DEFAULT = { avatar: "watcher", theme: "abyss", hair: "bob", hairColor: "auto", horns: "curved", wings: "bat", tail: true, prop: "fork", outfit: "dress", eyes: "auto", flames: true, glow: 1, bow: "pink", tie: "none", beard: "goatee", scene: "galaxy", tentacles: 9, books: true, skills: "many", runes: true, rings: 3, lightning: true };
  var LOOK_PRESETS = [
    { name: "Classic", look: { avatar: "watcher", theme: "abyss", bow: "pink" } },
    { name: "Cosmic", look: { avatar: "watcher", theme: "nebula", bow: "pink", scene: "cosmic", eyes: "gold" } },
    { name: "Cyber", look: { avatar: "watcher", theme: "rose", bow: "pink", scene: "cyberpunk" } },
    { name: "Matrix", look: { avatar: "watcher", theme: "emerald", bow: "none", eyes: "green", scene: "matrix" } },
    { name: "Hellfire", look: { avatar: "devil", theme: "crimson", scene: "inferno", hair: "flame", horns: "curved", glow: 1.3, eyes: "gold" } },
    { name: "Gothic Count", look: { avatar: "devilman", theme: "violet", scene: "gothic", hair: "slick", hairColor: "silver", horns: "small", beard: "none", outfit: "coat", prop: "none", tie: "purple", eyes: "violet" } },
    { name: "Synth", look: { avatar: "watcher", theme: "violet", bow: "pink", scene: "synthwave" } },
    { name: "Abyss", look: { avatar: "watcher", theme: "abyss", bow: "none" } },
    { name: "Archivist", look: { avatar: "watcher", theme: "gold", eyes: "gold", tentacles: 12 } },
    { name: "Void", look: { avatar: "watcher", theme: "violet", eyes: "violet", books: false } },
    { name: "Verdant", look: { avatar: "watcher", theme: "emerald", eyes: "green", tentacles: 6 } },
    { name: "Plasma", look: { avatar: "core", theme: "rose" } },
    { name: "Frostcore", look: { avatar: "core", theme: "ice", rings: 2 } },
    { name: "Solar", look: { avatar: "core", theme: "gold", rings: 3, glow: 1.3 } },
    { name: "Dapper", look: { avatar: "devilman", theme: "crimson", hair: "slick", horns: "curved", beard: "goatee", outfit: "suit", prop: "none", tie: "black", eyes: "gold" } },
    { name: "Duke", look: { avatar: "devilman", theme: "shadow", hair: "long", horns: "long", beard: "goatee", outfit: "coat", prop: "staff", wings: "large", eyes: "white" } },
    { name: "Brute", look: { avatar: "devilman", theme: "emerald", hair: "short", horns: "curved", beard: "full", outfit: "casual", prop: "fork", eyes: "green" } },
    { name: "Infernal", look: { avatar: "devilman", theme: "crimson", hair: "flame", horns: "crown", beard: "none", outfit: "suit", prop: "orb", glow: 1.4, eyes: "gold", tie: "red" } },
    { name: "Count", look: { avatar: "devilman", theme: "violet", hair: "slick", hairColor: "silver", horns: "small", beard: "none", outfit: "coat", prop: "none", tie: "purple", eyes: "violet" } },
    { name: "Classic", look: { avatar: "devil", theme: "crimson" } },
    { name: "Noir", look: { avatar: "devil", theme: "shadow", hair: "long", horns: "long", wings: "large", prop: "staff", outfit: "gown", eyes: "white" } },
    { name: "Inferno", look: { avatar: "devil", theme: "crimson", hair: "flame", horns: "curved", prop: "orb", outfit: "short", glow: 1.4, eyes: "gold" } },
    { name: "Frost", look: { avatar: "devil", theme: "ice", hair: "long", hairColor: "silver", horns: "crown", wings: "none", tail: false, prop: "staff", outfit: "gown", eyes: "white" } },
    { name: "Siren", look: { avatar: "devil", theme: "violet", hair: "long", horns: "small", wings: "large", prop: "none", outfit: "dress", eyes: "violet" } },
    { name: "Empress", look: { avatar: "devil", theme: "gold", hair: "short", hairColor: "jet", horns: "crown", prop: "staff", outfit: "gown", eyes: "gold" } },
    { name: "Venom", look: { avatar: "devil", theme: "emerald", hair: "short", hairColor: "blonde", horns: "long", prop: "orb", outfit: "short", eyes: "green" } }
  ];
  var configuredLooks = (window.__APP_CONFIG__ || {}).avatarPresets;
  if (Array.isArray(configuredLooks) && configuredLooks.length) LOOK_PRESETS = configuredLooks;
  var LOOK_KEY = "atrium:look";

  function mergeLook(base, patch) {
    var out = {}, k;
    for (k in base) out[k] = base[k];
    for (k in patch) if (k in LOOK_DEFAULT) out[k] = patch[k];
    return out;
  }
  function loadLook() {
    try {
      var raw = window.localStorage.getItem(LOOK_KEY);
      if (raw) return mergeLook(LOOK_DEFAULT, JSON.parse(raw));
    } catch (x) { /* storage blocked or bad data: use the default look */ }
    return mergeLook(LOOK_DEFAULT, {});
  }
  var LOOK = loadLook();
  function getLook() { return LOOK; }
  function setLook(patch) {
    LOOK = mergeLook(LOOK, patch);
    try { window.localStorage.setItem(LOOK_KEY, JSON.stringify(LOOK)); } catch (x) { /* storage blocked */ }
    try { window.dispatchEvent(new CustomEvent("atrium:look")); } catch (x) { /* old browser */ }
    pushLook();
  }
  function resetLook() { setLook(LOOK_DEFAULT); }
  /* The look is also saved on the server (one look for the agent, set by an owner), so it follows you to every device. A browser's own copy
     is only a cache. Saving is refused for people who are not owners; that is ignored. */
  var pushTimer = 0;
  function pushLook() {
    if (pushTimer) clearTimeout(pushTimer);
    pushTimer = setTimeout(function () {
      pushTimer = 0;
      try { fetch("/api/avatar-look", { method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ look: LOOK }) }).catch(function () { /* offline */ }); } catch (x) { /* ignore */ }
    }, 600);
  }
  function syncLook() {
    try {
      fetch("/api/avatar-look", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        if (!d) return;
        if (d.look && Object.keys(d.look).length) {
          LOOK = mergeLook(LOOK_DEFAULT, d.look);
          try { window.localStorage.setItem(LOOK_KEY, JSON.stringify(LOOK)); } catch (x) { /* storage blocked */ }
          try { window.dispatchEvent(new CustomEvent("atrium:look")); } catch (x) { /* old browser */ }
        } else if (JSON.stringify(LOOK) !== JSON.stringify(mergeLook(LOOK_DEFAULT, {}))) {
          pushLook();                                   // nothing saved yet: keep this browser's look as the saved one
        }
      }).catch(function () { /* offline: the cached look stays */ });
    } catch (x) { /* ignore */ }
  }
  syncLook();
  var POINTER = { cx: Math.round((window.innerWidth || 1000) / 2), cy: Math.round((window.innerHeight || 700) / 2) };
  try { window.addEventListener("pointermove", function (ev) { POINTER.cx = ev.clientX; POINTER.cy = ev.clientY; }, { passive: true }); } catch (x) { /* no pointer events */ }

  function openLab() { try { window.dispatchEvent(new CustomEvent("atrium:avatarlab")); } catch (x) { /* old browser */ } }

  function lerpC(c, to, f) { return [Math.round(c[0] + (to[0] - c[0]) * f), Math.round(c[1] + (to[1] - c[1]) * f), Math.round(c[2] + (to[2] - c[2]) * f)]; }

  function statePalette(st, th) {
    if (st === "offline") return { core: [90, 96, 120], a: [120, 126, 150], b: [90, 96, 120] };
    if (st === "waiting" || st === "perk") return { core: [255, 200, 87], a: [255, 214, 120], b: [255, 160, 60] };
    if (st === "working") return { core: lerpC(th.core, [255, 255, 255], 0.2), a: lerpC(th.a, [255, 255, 255], 0.3), b: lerpC(th.b, [255, 255, 255], 0.45) };
    return { core: th.core, a: th.a, b: th.b };
  }

  function Orb(props) {
    var ref = useRef(null);
    var stateRef = useRef(props.state);
    stateRef.current = props.state;
    var lookRef = useRef(LOOK);
    var drawRef = useRef(null);

    useEffect(function () {
      var cv = ref.current;
      if (!cv || !cv.getContext) return undefined;
      var ctx = cv.getContext("2d");
      var dpr = Math.min(window.devicePixelRatio || 1, 2) * 1.5;   // drawn at 1.5x so the avatar stays sharp when shown large
      var W = 240, H = 300;
      cv.width = W * dpr; cv.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var reduce = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      var t = 0, raf = 0, last = 0, lastDt = 0.033;
      // keep the "tool in use" signal fresh while she works, so the matching icon glows
      var stopTool = window.__AVATAR_TOOL__ ? window.__AVATAR_TOOL__.poll(function () { return stateRef.current === "working"; }) : null;

      function rgba(c, a) { return "rgba(" + c.join(",") + "," + a + ")"; }

      function flame(x, y, w, hgt, ph, pal) {
        var sway = Math.sin(t * 5 + ph) * w * 0.35, tall = hgt * (0.8 + 0.2 * Math.sin(t * 7 + ph * 2));
        var g = ctx.createLinearGradient(0, y, 0, y - tall);
        g.addColorStop(0, rgba(pal.b, 0.9)); g.addColorStop(0.5, rgba(pal.a, 0.7)); g.addColorStop(1, rgba(pal.core, 0));
        ctx.fillStyle = g; ctx.beginPath();
        ctx.moveTo(x - w, y); ctx.quadraticCurveTo(x - w * 0.6, y - tall * 0.5, x + sway, y - tall);
        ctx.quadraticCurveTo(x + w * 0.6, y - tall * 0.5, x + w, y); ctx.closePath(); ctx.fill();
      }

      /* ----- shared helpers for the two newer avatars ----- */
      var ptrLook = { x: 0, y: 0 };
      function updatePointerLook() {
        var r = cv.getBoundingClientRect();
        var dx = (POINTER.cx - (r.left + r.width / 2)) / 420, dy = (POINTER.cy - (r.top + r.height / 2)) / 320;
        dx = Math.max(-1, Math.min(1, dx)); dy = Math.max(-1, Math.min(1, dy));
        ptrLook.x += (dx - ptrLook.x) * 0.12; ptrLook.y += (dy - ptrLook.y) * 0.12;
      }
      function blinkOpen(phase, off) {
        if (off) return 0.06;
        var p = (t * 0.7 + phase) % 5;
        return p > 4.85 ? Math.min(1, Math.abs(p - 4.925) / 0.075) : 1;
      }
      function smallEye(x, y, r, phase, col, off) {
        var open = blinkOpen(phase, off);
        ctx.save(); ctx.translate(x, y);
        ctx.shadowColor = rgba(col, 0.9); ctx.shadowBlur = off ? 0 : 6;
        ctx.fillStyle = rgba(col, off ? 0.5 : 0.95); ctx.beginPath(); ctx.ellipse(0, 0, r, r * 0.62 * open + 0.2, 0, 0, 6.2832); ctx.fill();
        ctx.shadowBlur = 0; ctx.fillStyle = "#040707"; ctx.beginPath();
        ctx.ellipse(eyeLook.x * r * 0.35, eyeLook.y * r * 0.2, r * 0.2, r * 0.5 * open + 0.1, 0, 0, 6.2832); ctx.fill();
        ctx.restore();
      }
      var GLYPHS = [
        [0, 0, 1, 0, 1, 1], [0, 1, 0.5, 0, 1, 1], [0, 0.5, 1, 0.5, 0.5, 1, 0.5, 0], [0, 0, 0, 1, 1, 1], [0.5, 0, 0.5, 1, 0, 0.5, 1, 0.5], [0, 0, 1, 1, 0, 1, 1, 0]
      ];
      var runes = [], rn;
      for (rn = 0; rn < 12; rn++) runes.push({ x: 10 + Math.random() * 220, y: Math.random() * 300, g: rn % GLYPHS.length, v: 8 + Math.random() * 14, s: 6 + Math.random() * 4, ph: Math.random() * 6.28 });

      /* ----- cosmic mode: nebula, stars, a ringed planet with a moon, the odd comet ----- */
      var cosmicNow = false, cosmicStars = [], csi, comet = null, nextComet = 3;
      for (csi = 0; csi < 70; csi++) cosmicStars.push({ x: Math.random() * 240, y: Math.random() * 300, r: 0.5 + Math.random() * 1.3, ph: Math.random() * 6.28, c: Math.random() });
      function isCosmic(L) {
        return sceneName(L) === "cosmic";
      }
      function drawCosmic(st) {
        var off = st === "offline", k = off ? 0.35 : 1;
        [[70, 90, 120, [110, 70, 230], 0.28], [180, 210, 130, [40, 110, 255], 0.22], [175, 70, 95, [220, 60, 180], 0.2]].forEach(function (n, i) {
          var nx = n[0] + Math.sin(t * 0.12 + i * 2) * 14, ny = n[1] + Math.cos(t * 0.1 + i * 3) * 12;
          var g = ctx.createRadialGradient(nx, ny, 4, nx, ny, n[2]); g.addColorStop(0, rgba(n[3], n[4] * k)); g.addColorStop(1, rgba(n[3], 0));
          ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
        });
        cosmicStars.forEach(function (sp) {
          var a = (0.25 + 0.65 * Math.abs(Math.sin(t * 1.2 + sp.ph))) * k;
          ctx.fillStyle = sp.c > 0.8 ? rgba([255, 214, 150], a) : sp.c > 0.5 ? rgba([160, 190, 255], a) : rgba([255, 255, 255], a);
          ctx.beginPath(); ctx.arc(sp.x, sp.y, sp.r, 0, 6.2832); ctx.fill();
        });
        // ringed planet with a moon
        var px = 208, py = 52;
        ctx.save(); ctx.translate(px, py); ctx.globalAlpha = k;
        ctx.strokeStyle = "rgba(225,205,255,0.55)"; ctx.lineWidth = 1.4; ctx.beginPath(); ctx.ellipse(0, 0, 16, 4.4, -0.4, Math.PI, 6.2832); ctx.stroke();
        var pg = ctx.createRadialGradient(-3, -3, 1, 0, 0, 9); pg.addColorStop(0, "#c7b3ff"); pg.addColorStop(1, "#3a2a8a");
        ctx.fillStyle = pg; ctx.beginPath(); ctx.arc(0, 0, 8.5, 0, 6.2832); ctx.fill();
        ctx.beginPath(); ctx.ellipse(0, 0, 16, 4.4, -0.4, 0, Math.PI); ctx.stroke();
        var ma = t * 0.6; ctx.fillStyle = "#d9d4ee"; ctx.beginPath(); ctx.arc(Math.cos(ma) * 22, Math.sin(ma) * 9, 2.2, 0, 6.2832); ctx.fill();
        ctx.restore();
        // a comet now and then
        if (!off) {
          if (!comet && t > nextComet) comet = { x: -12, y: 20 + Math.random() * 90, vx: 170, vy: 75 };
          if (comet) {
            comet.x += comet.vx * lastDt; comet.y += comet.vy * lastDt;
            var cg = ctx.createLinearGradient(comet.x, comet.y, comet.x - comet.vx * 0.22, comet.y - comet.vy * 0.22);
            cg.addColorStop(0, "rgba(255,255,255,0.95)"); cg.addColorStop(1, "rgba(150,170,255,0)");
            ctx.strokeStyle = cg; ctx.lineWidth = 1.8; ctx.lineCap = "round"; ctx.beginPath(); ctx.moveTo(comet.x, comet.y); ctx.lineTo(comet.x - comet.vx * 0.22, comet.y - comet.vy * 0.22); ctx.stroke();
            if (comet.x > W + 30 || comet.y > H + 30) { comet = null; nextComet = t + 7 + Math.random() * 9; }
          }
        }
      }

      /* ----- a little bow worn on top of the head (shows as a pink bow by default; colours in BOW_COLORS) ----- */
      function hexRgb(hex) { return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)]; }
      function mixHex(hex, to, f) { var c = lerpC(hexRgb(hex), to, f); return "rgb(" + c.join(",") + ")"; }
      function drawBow(x, y, sc, rot, hex, off) {
        var base = off ? "#7b8096" : hex;
        ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(sc, sc);
        ctx.shadowColor = off ? "rgba(0,0,0,0)" : base; ctx.shadowBlur = 9;
        var g = ctx.createLinearGradient(0, -9, 0, 9); g.addColorStop(0, mixHex(base, [255, 255, 255], 0.45)); g.addColorStop(0.55, base); g.addColorStop(1, mixHex(base, [0, 0, 0], 0.25));
        ctx.fillStyle = g; ctx.strokeStyle = mixHex(base, [0, 0, 0], 0.4); ctx.lineWidth = 1;
        [-1, 1].forEach(function (s2) {
          ctx.save(); ctx.scale(s2, 1);
          ctx.beginPath(); ctx.moveTo(2, 0); ctx.bezierCurveTo(8, -10, 19, -10, 18, 0); ctx.bezierCurveTo(19, 10, 8, 10, 2, 0); ctx.closePath(); ctx.fill(); ctx.stroke();
          ctx.shadowBlur = 0; ctx.strokeStyle = "rgba(255,255,255,0.45)"; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(6, -2); ctx.quadraticCurveTo(11, -6, 15, -2); ctx.stroke();
          ctx.strokeStyle = mixHex(base, [0, 0, 0], 0.4);
          ctx.restore();
        });
        ctx.shadowBlur = 0;
        ctx.beginPath(); ctx.moveTo(-2, 3); ctx.lineTo(-8, 13); ctx.lineTo(-3.5, 11.5); ctx.lineTo(-0.5, 5); ctx.closePath(); ctx.fill(); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(2, 3); ctx.lineTo(8, 13); ctx.lineTo(3.5, 11.5); ctx.lineTo(0.5, 5); ctx.closePath(); ctx.fill(); ctx.stroke();
        ctx.fillStyle = mixHex(base, [0, 0, 0], 0.15);
        ctx.beginPath(); ctx.ellipse(0, 0, 3.6, 4.2, 0, 0, 6.2832); ctx.fill(); ctx.stroke();
        ctx.restore();
      }

      /* ----- a bow tie (the "he" counterpart to the bow) ----- */
      function drawBowTie(x, y, sc, rot, hex, off) {
        var base = off ? "#6a6f85" : hex;
        ctx.save(); ctx.translate(x, y); ctx.rotate(rot); ctx.scale(sc, sc);
        ctx.shadowColor = off ? "rgba(0,0,0,0)" : "rgba(0,0,0,0.6)"; ctx.shadowBlur = 4;
        var g = ctx.createLinearGradient(0, -8, 0, 8); g.addColorStop(0, mixHex(base, [255, 255, 255], 0.3)); g.addColorStop(1, mixHex(base, [0, 0, 0], 0.3));
        ctx.fillStyle = g; ctx.strokeStyle = "rgba(255,255,255,0.38)"; ctx.lineWidth = 1;
        [-1, 1].forEach(function (s2) {
          ctx.beginPath(); ctx.moveTo(s2 * 2, 0); ctx.lineTo(s2 * 15, -8); ctx.quadraticCurveTo(s2 * 12, 0, s2 * 15, 8); ctx.closePath(); ctx.fill(); ctx.stroke();
        });
        ctx.shadowBlur = 0;
        ctx.fillStyle = mixHex(base, [0, 0, 0], 0.15);
        ctx.beginPath(); if (ctx.roundRect) ctx.roundRect(-3.6, -5, 7.2, 10, 2); else ctx.rect(-3.6, -5, 7.2, 10); ctx.fill(); ctx.stroke();
        ctx.restore();
      }

      /* ----- little icons for the things the agent can do, orbiting like the tomes (simplified lookalikes, drawn in code) ----- */
      var SKILL_KINDS = ["docker", "js", "python", "terminal", "git", "db", "web", "search", "mail", "code", "clock", "vault"];

      function drawSkillIcon(kind, x, y, sc, pal, off, alpha) {
        function c(hex) { return off ? "#5a5f70" : hex; }
        function rr(rx, ry, rw, rh, rad) { ctx.beginPath(); if (ctx.roundRect) ctx.roundRect(rx, ry, rw, rh, rad); else ctx.rect(rx, ry, rw, rh); ctx.fill(); }
        ctx.save(); ctx.translate(x, y); ctx.scale(sc, sc); ctx.globalAlpha = alpha;
        ctx.fillStyle = off ? "#161922" : "#08141a";
        ctx.strokeStyle = rgba(pal.a, off ? 0.2 : 0.55 + 0.25 * Math.sin(t * 3 + x * 0.1));
        ctx.lineWidth = 1.1;
        ctx.beginPath(); if (ctx.roundRect) ctx.roundRect(-10, -10, 20, 20, 5); else ctx.rect(-10, -10, 20, 20); ctx.fill(); ctx.stroke();
        ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.lineWidth = 1.6;
        if (kind === "docker") {
          ctx.fillStyle = c("#2496ed");
          [[-6, -4], [-2.5, -4], [1, -4], [-4.2, -7.4], [-0.7, -7.4]].forEach(function (q) { ctx.fillRect(q[0], q[1], 3, 3); });
          ctx.beginPath(); ctx.moveTo(-8, -0.5); ctx.lineTo(6.5, -0.5); ctx.quadraticCurveTo(8.5, -2.5, 9, -1); ctx.quadraticCurveTo(8, 3, 3, 5.5); ctx.lineTo(-3, 5.5); ctx.quadraticCurveTo(-8, 4, -8, -0.5); ctx.closePath(); ctx.fill();
        } else if (kind === "js") {
          ctx.fillStyle = c("#f7df1e"); ctx.fillRect(-7, -7, 14, 14);
          ctx.fillStyle = off ? "#222" : "#1a1a1a"; ctx.font = "bold 8.5px sans-serif"; ctx.textAlign = "right"; ctx.textBaseline = "alphabetic"; ctx.fillText("JS", 6.2, 6);
        } else if (kind === "python") {
          ctx.fillStyle = c("#3776ab"); rr(-7, -7, 10, 9, 3);
          ctx.fillStyle = c("#ffd43b"); rr(-3, -2, 10, 9, 3);
          ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(-3.8, -4.2, 1.1, 0, 6.2832); ctx.fill();
          ctx.fillStyle = "#222"; ctx.beginPath(); ctx.arc(3.8, 4.6, 1.1, 0, 6.2832); ctx.fill();
        } else if (kind === "terminal") {
          ctx.strokeStyle = c("#7dff9e"); ctx.beginPath(); ctx.moveTo(-6, -4); ctx.lineTo(-1.5, 0); ctx.lineTo(-6, 4); ctx.moveTo(0.5, 5); ctx.lineTo(6, 5); ctx.stroke();
        } else if (kind === "git") {
          ctx.strokeStyle = c("#f05133"); ctx.beginPath(); ctx.moveTo(-3, -5); ctx.lineTo(-3, 5); ctx.moveTo(-3, 1); ctx.quadraticCurveTo(3, 1, 3, -3); ctx.stroke();
          ctx.fillStyle = c("#f05133"); [[-3, -5], [-3, 5], [3, -3]].forEach(function (q) { ctx.beginPath(); ctx.arc(q[0], q[1], 1.9, 0, 6.2832); ctx.fill(); });
        } else if (kind === "db") {
          ctx.strokeStyle = c("#5bd0ff"); ctx.lineWidth = 1.4;
          ctx.beginPath(); ctx.ellipse(0, -4.5, 6, 2.4, 0, 0, 6.2832); ctx.moveTo(-6, -4.5); ctx.lineTo(-6, 4.5); ctx.moveTo(6, -4.5); ctx.lineTo(6, 4.5);
          ctx.ellipse(0, 4.5, 6, 2.4, 0, 0, Math.PI); ctx.moveTo(-6, 0); ctx.ellipse(0, 0, 6, 2.4, 0, Math.PI, 0, true); ctx.stroke();
        } else if (kind === "web") {
          ctx.strokeStyle = c("#6dd3ff"); ctx.lineWidth = 1.4;
          ctx.beginPath(); ctx.arc(0, 0, 6.5, 0, 6.2832); ctx.moveTo(-6.5, 0); ctx.lineTo(6.5, 0); ctx.moveTo(2.8, 0); ctx.ellipse(0, 0, 2.8, 6.5, 0, 0, 6.2832); ctx.stroke();
        } else if (kind === "search") {
          ctx.strokeStyle = c("#d8e6ff"); ctx.lineWidth = 1.8;
          ctx.beginPath(); ctx.arc(-1.5, -1.5, 4.4, 0, 6.2832); ctx.moveTo(1.8, 1.8); ctx.lineTo(6.5, 6.5); ctx.stroke();
        } else if (kind === "mail") {
          ctx.strokeStyle = c("#ffd166"); ctx.lineWidth = 1.4;
          ctx.beginPath(); ctx.rect(-7, -5, 14, 10); ctx.moveTo(-7, -5); ctx.lineTo(0, 1); ctx.lineTo(7, -5); ctx.stroke();
        } else if (kind === "code") {
          ctx.strokeStyle = c("#b48bff"); ctx.lineWidth = 1.8;
          ctx.beginPath(); ctx.moveTo(-2.5, -5); ctx.lineTo(-7, 0); ctx.lineTo(-2.5, 5); ctx.moveTo(2.5, -5); ctx.lineTo(7, 0); ctx.lineTo(2.5, 5); ctx.moveTo(1, -6); ctx.lineTo(-1, 6); ctx.stroke();
        } else if (kind === "clock") {
          ctx.strokeStyle = c("#ff9bd2"); ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.arc(0, 0, 6.5, 0, 6.2832); ctx.moveTo(0, 0); ctx.lineTo(0, -4); ctx.moveTo(0, 0); ctx.lineTo(Math.sin(t * 0.9) * 4.4, -Math.cos(t * 0.9) * 4.4); ctx.stroke();
        } else if (kind === "vault") {
          ctx.fillStyle = c("#a78bfa");
          ctx.beginPath(); ctx.moveTo(0, -7); ctx.lineTo(6, -2); ctx.lineTo(3.5, 6); ctx.lineTo(-3.5, 6); ctx.lineTo(-6, -2); ctx.closePath(); ctx.fill();
          ctx.strokeStyle = "rgba(255,255,255,0.5)"; ctx.lineWidth = 0.9;
          ctx.beginPath(); ctx.moveTo(-6, -2); ctx.lineTo(6, -2); ctx.moveTo(0, -7); ctx.lineTo(-1.6, -2); ctx.lineTo(0, 6); ctx.stroke();
        }
        ctx.restore();
      }

      function drawOrbiter(o, pal, off, alpha) {
        var sc = 0.85 + o.z * 0.2;
        if (o.kind === "book") { ctx.save(); ctx.globalAlpha = alpha; drawBook(o.x, o.y, o.tilt, sc * (o.z < 0 ? 0.9 : 1), pal, off); ctx.restore(); }
        else {
          // while working, the icon for the tool she is really using right now glows (from the live tool activity)
          var lt = window.__ATRIUM_TOOL__, hot = !!(stateRef.current === "working" && lt && lt.kind && lt.kind === o.kind && (Date.now() - lt.at) < 25000);
          if (hot) {
            var hp = 0.65 + 0.35 * Math.sin(t * 6), hg = ctx.createRadialGradient(o.x, o.y, 2, o.x, o.y, 28);
            hg.addColorStop(0, rgba(pal.b, 0.8 * hp)); hg.addColorStop(1, rgba(pal.b, 0));
            ctx.save(); ctx.fillStyle = hg; ctx.fillRect(o.x - 32, o.y - 32, 64, 64); ctx.restore();
          }
          drawSkillIcon(o.kind, o.x, o.y, sc * (hot ? 1.3 : 1.1), pal, off, alpha);
        }
      }

      function drawBook(x, y, tilt, scale, pal, off) {
        ctx.save(); ctx.translate(x, y); ctx.rotate(tilt); ctx.scale(scale, scale);
        ctx.fillStyle = off ? "#1a1c24" : "#0c1a19"; ctx.strokeStyle = rgba(pal.a, off ? 0.25 : 0.75); ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.rect(-9, -12, 18, 24); ctx.fill(); ctx.stroke();
        ctx.fillStyle = rgba(pal.core, off ? 0.2 : 0.55); ctx.fillRect(-9, -12, 3.5, 24);
        ctx.strokeStyle = rgba(pal.b, off ? 0.3 : 0.9); ctx.lineWidth = 1;
        if (cosmicNow) {
          var cpts = [[-2, -7], [3, -4], [6, 1], [0, 3], [-3, 6]];
          ctx.beginPath(); cpts.forEach(function (q, qi) { if (qi) ctx.lineTo(q[0], q[1]); else ctx.moveTo(q[0], q[1]); }); ctx.stroke();
          ctx.fillStyle = rgba(pal.b, off ? 0.3 : 1); cpts.forEach(function (q) { ctx.beginPath(); ctx.arc(q[0], q[1], 1, 0, 6.2832); ctx.fill(); });
        } else {
          ctx.beginPath(); ctx.arc(2, -1, 4, 0, 6.2832); ctx.moveTo(2, -6); ctx.lineTo(2, 4); ctx.moveTo(-3, -1); ctx.lineTo(7, -1); ctx.stroke();
        }
        ctx.restore();
      }

      /* ----- The Watcher: an eldritch keeper of knowledge. Original design: a dark mass, one great eye, writhing tentacles
         with small watching eyes, orbiting tomes and drifting runes. When the agent works, a tentacle drags in a laptop,
         several tentacles type on it, and an envelope flies off when the answer is sent. ----- */
      var lapAmt = 0, prevSt = "idle", sendT = 2, eyeLook = { x: 0, y: 0 };
      function smooth(x) { x = Math.max(0, Math.min(1, x)); return x * x * (3 - 2 * x); }
      function lerp(a, b, f) { return a + (b - a) * f; }

      function ikPath(x0, y0, x1, y1, S, bx, by) {
        var mx = (x0 + x1) / 2, my = (y0 + y1) / 2, dx = x1 - x0, dy = y1 - y0, L = Math.sqrt(dx * dx + dy * dy) || 1;
        var nx = -dy / L, ny = dx / L;
        if (nx * (mx - bx) + ny * (my - by) < 0) { nx = -nx; ny = -ny; }
        var bend = Math.min(36, L * 0.38), qx = mx + nx * bend, qy = my + ny * bend, out = [];
        for (var k = 0; k <= S; k++) { var u = k / S, a = (1 - u) * (1 - u), b = 2 * u * (1 - u), c = u * u; out.push([a * x0 + b * qx + c * x1, a * y0 + b * qy + c * y1]); }
        return out;
      }

      function drawLaptop(px, py, sc, rot, pal, st) {
        var work = st === "working", wait = st === "waiting";
        ctx.save(); ctx.translate(px, py); ctx.rotate(rot); ctx.scale(sc, sc);
        var sg = ctx.createRadialGradient(0, -30, 4, 0, -30, 72); sg.addColorStop(0, rgba(pal.a, 0.3)); sg.addColorStop(1, rgba(pal.a, 0));
        ctx.fillStyle = sg; ctx.fillRect(-84, -104, 168, 150);
        ctx.fillStyle = "#060b10"; ctx.strokeStyle = rgba(pal.a, 0.9); ctx.lineWidth = 2;
        ctx.beginPath(); ctx.rect(-42, -54, 84, 52); ctx.fill(); ctx.stroke();
        var flash = sendT < 0.25 ? 1 - sendT / 0.25 : 0;
        ctx.fillStyle = flash > 0 ? rgba(lerpC(pal.core, [255, 255, 255], 0.6 * flash), 0.35 + 0.45 * flash) : rgba(pal.core, 0.18);
        ctx.fillRect(-37, -49, 74, 42);
        if (wait) {
          ctx.fillStyle = "rgba(255,200,87,0.95)"; ctx.font = "bold 30px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
          ctx.fillText("?", 0, -17);
        } else {
          ctx.save(); ctx.beginPath(); ctx.rect(-37, -49, 74, 42); ctx.clip();
          var scroll = work ? (t * 6) % 8 : 0, base = work ? Math.floor(t * 6 / 8) : 0;
          for (var li = 0; li < 6; li++) {
            var lw = 16 + (((li + base) * 37) % 44);
            ctx.fillStyle = rgba(li % 2 ? pal.a : pal.b, 0.78);
            ctx.fillRect(-33, -47 + li * 8 - scroll, lw, 3);
          }
          if (work && Math.sin(t * 9) > 0) { ctx.fillStyle = rgba(pal.b, 1); ctx.fillRect(-30 + 16 + (((4 + base) * 37) % 44), -15, 3, 5); }
          ctx.restore();
        }
        ctx.fillStyle = "#0b1518"; ctx.strokeStyle = rgba(pal.a, 0.75); ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(-46, -2); ctx.lineTo(46, -2); ctx.lineTo(56, 12); ctx.lineTo(-56, 12); ctx.closePath(); ctx.fill(); ctx.stroke();
        for (var r = 0; r < 3; r++) for (var c = 0; c < 9; c++) {
          var hit = work && Math.sin(t * 17 + c * 1.9 + r * 2.7) > 0.93;
          ctx.fillStyle = hit ? rgba(pal.b, 1) : rgba(pal.a, 0.28);
          ctx.fillRect(-38 + c * 8.6 + r * 1.5, 1 + r * 3.3, 6, 2);
        }
        ctx.restore();
      }

      /* ----- the devils' laptop: pulled out from behind the back, opened, typed on, sent, closed and put away.
         Seen from the front, so you see the back of the lid (dark, with a glowing sigil), not the screen. ----- */
      function devilLap(st) {
        var work = st === "working";
        if (prevSt !== st) { if (prevSt === "working" && st !== "working" && st !== "offline") sendT = 0; prevSt = st; }
        if (sendT < 2) sendT += lastDt;
        var want = (work || st === "waiting") ? 1 : (sendT < 0.85 ? 1 : 0);
        var d = want - lapAmt, step = lastDt * 0.7;
        if (reduce) lapAmt = want; else lapAmt += Math.abs(d) < step ? d : (d > 0 ? step : -step);
        var ep = lapAmt, pull = smooth(ep / 0.42), open = smooth((ep - 0.45) / 0.4), type = work ? smooth((ep - 0.85) / 0.15) : 0;
        var lp = { ep: ep, pull: pull, open: open, type: type, behind: ep > 0 && pull < 0.45 };
        lp.lx = lerp(28, 0, pull) + Math.sin(pull * Math.PI) * 30;
        lp.ly = lerp(180, 164, pull);
        lp.rot = lerp(0.35, 0, pull);
        lp.hh = 34 * Math.sin(open * 1.85);
        lp.armBlend = smooth(ep / 0.25);
        var join = smooth((pull - 0.5) / 0.5), c = Math.cos(lp.rot), sn = Math.sin(lp.rot);
        var bobR = type * Math.sin(t * 17) * 1.5, bobL = type * Math.sin(t * 17 + 2.2) * 1.5;
        lp.rh = [lp.lx + 29 * c, lp.ly + 29 * sn - open * 3 + bobR];
        lp.lh = [lerp(-17, lp.lx - 29 * c, join), lerp(158, lp.ly - 29 * sn - open * 3 + bobL, join)];
        return lp;
      }

      function drawDevilLaptop(lp, pal, st) {
        var wait = st === "waiting", flash = sendT < 0.25 ? 1 - sendT / 0.25 : 0, hh = lp.hh;
        ctx.save(); ctx.translate(lp.lx, lp.ly); ctx.rotate(lp.rot);
        if (hh > 4) {
          var sg = ctx.createRadialGradient(0, -hh / 2, 4, 0, -hh / 2, 50); sg.addColorStop(0, rgba(pal.a, 0.28 + 0.3 * flash)); sg.addColorStop(1, rgba(pal.a, 0));
          ctx.fillStyle = sg; ctx.fillRect(-64, -hh - 50, 128, hh + 100);
        }
        if (hh > 0.6) {
          var lg = ctx.createLinearGradient(0, -hh, 0, 0); lg.addColorStop(0, "#252a3c"); lg.addColorStop(1, "#0d0f19");
          ctx.fillStyle = lg; ctx.strokeStyle = rgba(lerpC(pal.a, [255, 255, 255], 0.5 * flash), 0.9); ctx.lineWidth = 1.5;
          ctx.beginPath(); if (ctx.roundRect) ctx.roundRect(-25, -hh - 3, 50, hh, 2.5); else ctx.rect(-25, -hh - 3, 50, hh); ctx.fill(); ctx.stroke();
          if (hh > 16) {
            var ey = -3 - hh / 2;
            if (wait) { ctx.fillStyle = "rgba(255,200,87,0.95)"; ctx.font = "bold 20px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic"; ctx.fillText("?", 0, ey + 7); }
            else {
              var ea = 0.55 + (lp.type > 0.5 ? 0.35 * Math.sin(t * 9) : 0.15 * Math.sin(t * 2));
              ctx.strokeStyle = rgba(pal.core, ea); ctx.fillStyle = rgba(pal.core, ea); ctx.lineWidth = 1.4;
              ctx.beginPath(); ctx.arc(0, ey + 2, 5.5, 0, 6.2832); ctx.stroke();
              [-1, 1].forEach(function (sd) { ctx.beginPath(); ctx.moveTo(sd * 2, ey - 2.5); ctx.lineTo(sd * 6.5, ey - 10); ctx.lineTo(sd * 5.5, ey - 1); ctx.closePath(); ctx.fill(); });
            }
          }
        }
        // the base: its front edge and a hinge line (a closed laptop is just this slab)
        ctx.fillStyle = "#0b0d16"; ctx.strokeStyle = rgba(pal.a, 0.75); ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.moveTo(-25, -3); ctx.lineTo(25, -3); ctx.lineTo(27, 3); ctx.lineTo(-27, 3); ctx.closePath(); ctx.fill(); ctx.stroke();
        if (hh <= 0.6) { ctx.strokeStyle = rgba(pal.core, 0.7); ctx.beginPath(); ctx.moveTo(-22, -0.2); ctx.lineTo(22, -0.2); ctx.stroke(); }
        if (lp.type > 0.5) {
          for (var q = 0; q < 4; q++) {
            var u = ((t * 1.2 + q * 0.27) % 1), sx = (q - 1.5) * 11 + Math.sin(t * 3 + q) * 3;
            ctx.fillStyle = rgba(pal.b, 0.8 * (1 - u)); ctx.beginPath(); ctx.arc(sx, -hh - 6 - u * 22, 1.5, 0, 6.2832); ctx.fill();
          }
        }
        ctx.restore();
      }

      function drawEnvelope(px, py, pal) {
        var s = sendT;
        function at(u) {
          var a = (1 - u) * (1 - u), b = 2 * u * (1 - u), c = u * u;
          return [a * px + b * (px + 70) + c * (W + 34), a * (py - 30) + b * (py - 150) + c * (-34)];
        }
        for (var tr = 4; tr >= 1; tr--) {
          var q = at(Math.max(0, s - tr * 0.045));
          ctx.fillStyle = rgba(pal.b, 0.5 - tr * 0.1); ctx.beginPath(); ctx.arc(q[0], q[1], 3.2 - tr * 0.5, 0, 6.2832); ctx.fill();
        }
        var p = at(s);
        ctx.save(); ctx.translate(p[0], p[1]); ctx.rotate(-0.5 * s); ctx.scale(1 - 0.35 * s, 1 - 0.35 * s); ctx.globalAlpha = Math.max(0, 1 - s * s);
        ctx.shadowColor = rgba(pal.b, 1); ctx.shadowBlur = 10;
        ctx.fillStyle = "#f2f7ff"; ctx.fillRect(-9, -6, 18, 12);
        ctx.shadowBlur = 0; ctx.strokeStyle = "#8aa0b8"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.rect(-9, -6, 18, 12); ctx.moveTo(-9, -6); ctx.lineTo(0, 1); ctx.lineTo(9, -6); ctx.stroke();
        ctx.restore();
      }

      function paintTentacle(tn, eyeCol, pal, off, S) {
        var pts = tn.pts, i = tn.i;
        ctx.lineCap = "round"; ctx.lineJoin = "round";
        for (var k2 = 1; k2 <= S; k2++) {
          ctx.strokeStyle = off ? "#10131a" : "#04100f"; ctx.lineWidth = 11 * (1 - k2 / S) + 1.6;
          ctx.beginPath(); ctx.moveTo(pts[k2 - 1][0], pts[k2 - 1][1]); ctx.lineTo(pts[k2][0], pts[k2][1]); ctx.stroke();
        }
        ctx.strokeStyle = rgba(pal.a, off ? 0.18 : 0.5); ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(pts[0][0] - 1.2, pts[0][1] - 1.2);
        for (var k3 = 1; k3 <= S; k3++) ctx.lineTo(pts[k3][0] - 1.2 * (1 - k3 / S) - 0.4, pts[k3][1] - 1.2 * (1 - k3 / S) - 0.4);
        ctx.stroke();
        for (var k4 = 3; k4 < S; k4 += 3) {
          ctx.fillStyle = rgba(eyeCol, off ? 0.12 : 0.5 * (0.6 + 0.4 * Math.sin(t * 3 + i + k4)));
          ctx.beginPath(); ctx.arc(pts[k4][0] + 1.2, pts[k4][1] + 1.2, 1.1, 0, 6.2832); ctx.fill();
        }
        if (i % 3 === 0) smallEye(pts[S][0], pts[S][1], 4.2, i * 1.3, eyeCol, off);
      }

      function drawWatcher(L, th, st, pal, glow) {
        var off = st === "offline", work = st === "working", wait = st === "waiting" || st === "perk";
        var speed = work ? 2.2 : wait ? 1.3 : off ? 0.15 : 1;
        var cx = 120, cy = 138, R = 36;
        var eyeCol = off ? [150, 150, 150] : (EYE_COLORS[L.eyes] || th.b);
        updatePointerLook();

        // laptop timeline: out while working or waiting, and for a moment after the answer is sent
        if (prevSt !== st) { if (prevSt === "working" && st !== "working" && st !== "offline") sendT = 0; prevSt = st; }
        if (sendT < 2) sendT += lastDt;
        var want = (work || st === "waiting") ? 1 : (sendT < 0.85 ? 1 : 0);
        lapAmt += (want - lapAmt) * Math.min(1, lastDt * 4.2);
        if (lapAmt < 0.002 && want === 0) lapAmt = 0;
        var ep = smooth(lapAmt);
        var lpx = lerp(300, 120, ep), lpy = lerp(290, 214, ep), lsc = lerp(0.5, 1, ep), lrot = lerp(0.7, 0, ep);
        eyeLook.x = ptrLook.x * (1 - ep) + 0.1 * ep; eyeLook.y = ptrLook.y * (1 - ep) + 0.95 * ep;

        // abyss glow
        var g0 = ctx.createRadialGradient(cx, cy, 8, cx, cy, 135);
        g0.addColorStop(0, rgba(pal.core, 0.34 * glow)); g0.addColorStop(0.55, rgba(pal.core, 0.09 * glow)); g0.addColorStop(1, rgba(pal.core, 0));
        ctx.fillStyle = g0; ctx.fillRect(0, 0, W, H);

        // drifting runes
        if (L.runes && !off) {
          ctx.lineCap = "round"; ctx.lineJoin = "round";
          runes.forEach(function (r) {
            r.y -= r.v * lastDt * speed; if (r.y < -12) { r.y = 306; r.x = 10 + Math.random() * 220; }
            var a = Math.max(0, Math.min(1, (r.y / 300))) * 0.55 * (0.6 + 0.4 * Math.sin(t * 2 + r.ph));
            ctx.strokeStyle = rgba(pal.a, a * glow); ctx.lineWidth = 1.1;
            var gl = GLYPHS[r.g]; ctx.beginPath();
            for (var q = 0; q < gl.length; q += 2) { var px = r.x + gl[q] * r.s, py = r.y + gl[q + 1] * r.s; if (q === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py); }
            ctx.stroke();
          });
        }

        // things orbiting the Watcher: its tomes, and little icons for what it can do
        var orbiters = [];
        if (L.books) {
          for (var bj = 0; bj < 3; bj++) {
            var ba = t * 0.55 * speed + bj * 2.094;
            orbiters.push({ kind: "book", x: cx + Math.cos(ba) * 100, y: cy + 8 + Math.sin(ba) * 40, z: Math.sin(ba), tilt: Math.cos(ba) * 0.45 });
          }
        }
        var nSk = L.skills === "off" ? 0 : (L.skills === "few" ? 6 : 12);
        if (nSk) {
          var perRing = [0, 0], seen = [0, 0];
          for (var q = 0; q < nSk; q++) perRing[q % 2]++;
          for (var si = 0; si < nSk; si++) {
            var rg = si % 2, kidx = seen[rg]++, nn = perRing[rg];
            var sa = (kidx / nn) * 6.2832 + rg * 0.6 + t * (rg === 0 ? 0.5 : -0.36) * speed;
            orbiters.push({ kind: SKILL_KINDS[si], x: cx + Math.cos(sa) * (rg === 0 ? 76 : 110), y: cy + 8 + Math.sin(sa) * (rg === 0 ? 24 : 42), z: Math.sin(sa), tilt: 0 });
          }
        }
        orbiters.forEach(function (o) { if (o.z < 0) drawOrbiter(o, pal, off, 0.8); });

        // tentacles: work out every path, then let the three lowest ones reach for the laptop
        var N = L.tentacles || 9, S = 16;
        var amp = off ? 0.05 : (work ? 0.34 : wait ? 0.16 : 0.22);
        var tents = [];
        for (var i = 0; i < N; i++) {
          var th0 = -Math.PI / 2 + (i + 0.5) * (6.2832 / N);
          var down = (Math.sin(th0) + 1) / 2;
          var len = 56 + 54 * down + (i % 3) * 9 + (wait ? 12 * (1 - down) : 0);
          var seg = len / S;
          var x = cx + Math.cos(th0) * R * 0.85, y = cy + Math.sin(th0) * R * 0.85, ang = th0;
          var pts = [[x, y]];
          for (var k = 1; k <= S; k++) {
            ang += Math.sin(t * speed * 1.3 + i * 2.1 + k * 0.55) * amp * (0.35 + k / S);
            if (off) { var diff = ((Math.PI / 2 - ang + Math.PI) % 6.2832 + 6.2832) % 6.2832 - Math.PI; ang += diff * 0.06; }
            x += Math.cos(ang) * seg; y += Math.sin(ang) * seg; pts.push([x, y]);
          }
          tents.push({ i: i, th0: th0, down: down, pts: pts, typer: -1 });
        }
        if (ep > 0.001 && N >= 6) {
          var order = tents.slice().sort(function (a, b) { return b.down - a.down; }).slice(0, 3).sort(function (a, b) { return Math.cos(b.th0) - Math.cos(a.th0); });
          order.forEach(function (tn, j) {
            tn.typer = j;
            var tx, ty;
            if (j === 0) { tx = lpx + 44 * lsc; ty = lpy + 6 * lsc; }
            else { tx = lpx + (j === 1 ? -6 : -30) * lsc + (j === 1 ? 0 : 0); ty = lpy + 4 * lsc; }
            if (work && j > 0) ty += Math.max(0, Math.sin(t * 16 + j * 2.3)) * 4;
            if (work && j === 0) { tx += Math.sin(t * 9) * 2; }
            var ik = ikPath(tn.pts[0][0], tn.pts[0][1], tx, ty, S, cx, cy);
            for (var kk = 0; kk <= S; kk++) { tn.pts[kk] = [lerp(tn.pts[kk][0], ik[kk][0], ep), lerp(tn.pts[kk][1], ik[kk][1], ep)]; }
          });
        }
        tents.forEach(function (tn) { if (tn.typer < 0 || ep < 0.05) paintTentacle(tn, eyeCol, pal, off, S); });

        // the body: an ink-dark mass with a rim of light
        ctx.beginPath();
        for (var bp = 0; bp <= 18; bp++) {
          var ba2 = bp / 18 * 6.2832, rr = R + Math.sin(ba2 * 3 + t * 1.2 * speed) * 2.6 + Math.sin(ba2 * 5 - t * 0.9 * speed) * 1.6;
          var bx = cx + Math.cos(ba2) * rr, by = cy + Math.sin(ba2) * rr * 0.96;
          if (bp === 0) ctx.moveTo(bx, by); else ctx.lineTo(bx, by);
        }
        ctx.closePath();
        var bg = ctx.createRadialGradient(cx - 8, cy - 10, 4, cx, cy, R + 6);
        bg.addColorStop(0, off ? "#1b1e28" : "#0f2926"); bg.addColorStop(1, off ? "#07080c" : "#020807");
        ctx.fillStyle = bg; ctx.fill();
        ctx.strokeStyle = rgba(pal.a, off ? 0.2 : 0.55); ctx.lineWidth = 1.3; ctx.stroke();

        if (BOW_COLORS[L.bow]) {
          // centred and level, riding the real top edge of the wobbling body (same formula as the body outline at the top, angle 3pi/2)
          var rTop = R + Math.sin(14.1372 + t * 1.2 * speed) * 2.6 + Math.sin(23.5619 - t * 0.9 * speed) * 1.6;
          drawBow(cx, cy - rTop * 0.96 + 0.5, 0.95, Math.sin(t * 0.9 * speed) * 0.025, BOW_COLORS[L.bow], off);
        }

        // the laptop (in front of the body), then the tentacles that hold and type on it
        if (lapAmt > 0.004) drawLaptop(lpx, lpy, lsc, lrot, pal, st);
        tents.forEach(function (tn) { if (tn.typer >= 0 && ep >= 0.05) paintTentacle(tn, eyeCol, pal, off, S); });
        if (sendT < 1 && lapAmt > 0.2) drawEnvelope(lpx, lpy, pal);

        // small eyes scattered over the mass
        [[-26, -16, 4.6], [27, -13, 4.2], [-29, 12, 3.8], [29, 16, 4.6], [-13, 29, 3.6], [15, 30, 4.2], [0, -33, 3.8]].forEach(function (e, ei) {
          if (e[0] === 0 && e[1] === -33 && BOW_COLORS[L.bow]) return;
          smallEye(cx + e[0], cy + e[1], e[2], ei * 0.9 + 0.4, eyeCol, off);
        });

        if (TIE_COLORS[L.tie]) drawBowTie(cx, cy + R - 2 + Math.sin(t * 1.2 * speed) * 0.8, 0.95, Math.sin(t * 0.9 * speed) * 0.04, TIE_COLORS[L.tie], off);

        // the great eye
        var open = blinkOpen(0, off);
        var ex = cx, ey = cy - 2, erx = 24, ery = 17 * open + 0.6;
        ctx.save();
        ctx.shadowColor = rgba(eyeCol, off ? 0 : 0.95); ctx.shadowBlur = (work ? 26 : wait ? 22 : 14) * glow;
        ctx.beginPath(); ctx.ellipse(ex, ey, erx, ery, 0, 0, 6.2832); ctx.fillStyle = rgba(lerpC(eyeCol, [0, 0, 0], 0.55), 1); ctx.fill();
        ctx.restore();
        ctx.save();
        ctx.beginPath(); ctx.ellipse(ex, ey, erx - 1, Math.max(0.4, ery - 1), 0, 0, 6.2832); ctx.clip();
        var sc = ctx.createRadialGradient(ex, ey, 2, ex, ey, erx);
        sc.addColorStop(0, rgba(lerpC(eyeCol, [255, 255, 255], 0.5), 1)); sc.addColorStop(0.55, rgba(eyeCol, 1)); sc.addColorStop(1, rgba(lerpC(eyeCol, [0, 0, 0], 0.5), 1));
        ctx.fillStyle = sc; ctx.fillRect(ex - erx, ey - 20, erx * 2, 40);
        var ox = eyeLook.x * 7, oy = eyeLook.y * 5;
        if (cosmicNow && !off) {
          var gx = ex + ox, gy = ey + oy;
          var gc = ctx.createRadialGradient(gx, gy, 0, gx, gy, 17); gc.addColorStop(0, "rgba(255,255,255,0.95)"); gc.addColorStop(0.3, rgba(pal.b, 0.7)); gc.addColorStop(1, rgba(pal.a, 0));
          ctx.fillStyle = gc; ctx.beginPath(); ctx.arc(gx, gy, 17, 0, 6.2832); ctx.fill();
          for (var arm = 0; arm < 3; arm++) for (var gi = 0; gi < 26; gi++) {
            var gr = 2.5 + gi * 0.55, ga = gi * 0.3 + arm * 2.094 + t * 0.5 * speed;
            ctx.fillStyle = rgba(gi % 3 ? pal.a : [255, 255, 255], 0.3 + 0.55 * (1 - gi / 26));
            ctx.beginPath(); ctx.arc(gx + Math.cos(ga) * gr, gy + Math.sin(ga) * gr * 0.9, 0.7 + (gi % 4 === 0 ? 0.6 : 0), 0, 6.2832); ctx.fill();
          }
        } else {
          ctx.strokeStyle = rgba(lerpC(eyeCol, [0, 0, 0], 0.5), 0.7); ctx.lineWidth = 1;
          for (var ring = 0; ring < 3; ring++) { ctx.beginPath(); ctx.arc(ex + ox, ey + oy, 6 + ring * 3, 0, 6.2832); ctx.stroke(); }
        }
        var pw = work ? 2.2 : wait ? 5.4 : 3.6;
        ctx.fillStyle = "#030606"; ctx.beginPath(); ctx.ellipse(ex + ox, ey + oy, pw, 12, 0, 0, 6.2832); ctx.fill();
        ctx.fillStyle = "rgba(255,255,255,0.75)"; ctx.beginPath(); ctx.ellipse(ex + ox - 6, ey + oy - 6, 2.2, 1.6, -0.5, 0, 6.2832); ctx.fill();
        ctx.restore();
        ctx.strokeStyle = off ? "#222633" : "#020706"; ctx.lineWidth = 2.4; ctx.beginPath(); ctx.ellipse(ex, ey, erx, ery, 0, 0, 6.2832); ctx.stroke();

        // orbiters passing in front (a little see-through while the laptop is out so they never hide the screen)
        var frontAlpha = 1 - 0.5 * ep;
        orbiters.forEach(function (o) { if (o.z >= 0) drawOrbiter(o, pal, off, frontAlpha); });
      }

      /* ----- Energy core: swirling plasma sphere, tilted rings with sparks, lightning and streaming particles ----- */
      var coreParts = [], cp;
      for (cp = 0; cp < 40; cp++) coreParts.push({ a: Math.random() * 6.2832, r: 58 + Math.random() * 52, v: 0.2 + Math.random() * 0.6, s: 0.8 + Math.random() * 1.6, ph: Math.random() * 6.28 });
      var arcs = [], nextArc = 1;

      function drawCore(L, th, st, pal, glow) {
        var off = st === "offline", work = st === "working", wait = st === "waiting" || st === "perk";
        var speed = work ? 2.4 : wait ? 1.4 : off ? 0.12 : 1;
        var cx = 120, cy = 142, R = 50;
        var pulse = 0.85 + 0.15 * Math.sin(t * 2.4 * speed);

        var halo = ctx.createRadialGradient(cx, cy, 12, cx, cy, 128);
        halo.addColorStop(0, rgba(pal.core, 0.45 * glow * pulse)); halo.addColorStop(0.5, rgba(pal.core, 0.12 * glow)); halo.addColorStop(1, rgba(pal.core, 0));
        ctx.fillStyle = halo; ctx.fillRect(0, 0, W, H);

        // particles
        coreParts.forEach(function (p) {
          p.a += p.v * 0.03 * speed;
          var px = cx + Math.cos(p.a) * p.r, py = cy + Math.sin(p.a) * p.r * 0.7;
          ctx.fillStyle = rgba(pal.a, (0.25 + 0.5 * Math.abs(Math.sin(t * 2 + p.ph))) * (off ? 0.3 : 1));
          ctx.beginPath(); ctx.arc(px, py, p.s, 0, 6.2832); ctx.fill();
        });

        var nR = L.rings || 3, sats = [];
        function ringGeom(i) { return { rx: 74 + i * 13, ry: 20 + i * 15, rot: -0.55 + i * 0.95 + t * 0.14 * speed * (i % 2 ? 1 : -1) }; }
        function ringArc(i, from, to, alpha) {
          var g = ringGeom(i);
          ctx.save(); ctx.translate(cx, cy); ctx.rotate(g.rot);
          ctx.strokeStyle = rgba(i % 2 ? pal.b : pal.a, alpha * (off ? 0.4 : 1)); ctx.lineWidth = 1.6 - i * 0.2;
          ctx.shadowColor = rgba(pal.a, 0.8 * glow); ctx.shadowBlur = off ? 0 : 6 * glow;
          ctx.beginPath(); ctx.ellipse(0, 0, g.rx, g.ry, 0, from, to); ctx.stroke();
          ctx.restore();
        }
        function satPos(i, a) {
          var g = ringGeom(i), lx = Math.cos(a) * g.rx, ly = Math.sin(a) * g.ry;
          return { x: cx + lx * Math.cos(g.rot) - ly * Math.sin(g.rot), y: cy + lx * Math.sin(g.rot) + ly * Math.cos(g.rot), z: Math.sin(a) };
        }
        var ri;
        for (ri = 0; ri < nR; ri++) { ringArc(ri, Math.PI, 6.2832, 0.38); sats.push({ i: ri, a: t * (1.1 + ri * 0.35) * speed + ri * 2.1 }); }
        sats.forEach(function (sa) {
          var p = satPos(sa.i, sa.a);
          if (p.z < 0) { ctx.fillStyle = rgba(pal.b, 0.8); ctx.beginPath(); ctx.arc(p.x, p.y, 2.2, 0, 6.2832); ctx.fill(); }
        });

        // the sphere
        ctx.save();
        ctx.beginPath(); ctx.arc(cx, cy, R, 0, 6.2832); ctx.clip();
        var sg = ctx.createRadialGradient(cx - 16, cy - 18, 4, cx, cy, R);
        sg.addColorStop(0, rgba(lerpC(pal.a, [255, 255, 255], 0.55), 1)); sg.addColorStop(0.35, rgba(pal.a, 1)); sg.addColorStop(0.8, rgba(lerpC(pal.core, [0, 0, 0], 0.55), 1)); sg.addColorStop(1, rgba(lerpC(pal.core, [0, 0, 0], 0.85), 1));
        ctx.fillStyle = sg; ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
        for (var bd = 0; bd < 6; bd++) {
          var dirn = bd % 2 ? 1 : -1;
          ctx.save(); ctx.translate(cx, cy); ctx.rotate(t * 0.5 * speed * dirn + bd * 1.05);
          var bgd = ctx.createLinearGradient(-R, 0, R, 0);
          bgd.addColorStop(0, rgba(pal.b, 0)); bgd.addColorStop(0.5, rgba(pal.b, off ? 0.12 : 0.32)); bgd.addColorStop(1, rgba(pal.b, 0));
          ctx.strokeStyle = bgd; ctx.lineWidth = 9 - bd;
          ctx.beginPath(); ctx.ellipse(Math.sin(t * 0.7 * speed + bd) * 12, 0, R * 1.1, 10 + bd * 7, 0, 0, 6.2832); ctx.stroke();
          ctx.restore();
        }
        var core = ctx.createRadialGradient(cx, cy, 1, cx, cy, 24 * pulse);
        core.addColorStop(0, "rgba(255,255,255,0.95)"); core.addColorStop(0.35, rgba(pal.b, 0.8)); core.addColorStop(1, rgba(pal.b, 0));
        ctx.fillStyle = core; ctx.beginPath(); ctx.arc(cx, cy, 26 * pulse, 0, 6.2832); ctx.fill();
        ctx.fillStyle = "rgba(255,255,255,0.22)"; ctx.beginPath(); ctx.ellipse(cx - 17, cy - 22, 15, 8, -0.6, 0, 6.2832); ctx.fill();
        ctx.restore();
        ctx.strokeStyle = rgba(pal.a, off ? 0.3 : 0.8); ctx.lineWidth = 1.6; ctx.shadowColor = rgba(pal.a, 0.9 * glow); ctx.shadowBlur = off ? 0 : 12 * glow;
        ctx.beginPath(); ctx.arc(cx, cy, R, 0, 6.2832); ctx.stroke(); ctx.shadowBlur = 0;

        if (BOW_COLORS[L.bow]) drawBow(cx, cy - R + 1.5, 0.95, Math.sin(t * speed) * 0.025, BOW_COLORS[L.bow], off);

        if (TIE_COLORS[L.tie]) drawBowTie(cx, cy + R - 3, 1, Math.sin(t * speed) * 0.04, TIE_COLORS[L.tie], off);

        // front halves of the rings and the sparks passing in front
        for (ri = 0; ri < nR; ri++) ringArc(ri, 0, Math.PI, 0.85);
        sats.forEach(function (sa) {
          var p = satPos(sa.i, sa.a);
          if (p.z >= 0) {
            ctx.shadowColor = rgba(pal.b, 1); ctx.shadowBlur = off ? 0 : 10;
            ctx.fillStyle = rgba(lerpC(pal.b, [255, 255, 255], 0.5), 1); ctx.beginPath(); ctx.arc(p.x, p.y, 3, 0, 6.2832); ctx.fill(); ctx.shadowBlur = 0;
          }
        });

        // lightning from the sphere to the rings
        if (L.lightning && !off) {
          if (t > nextArc && sats.length) {
            var sp = sats[Math.floor(Math.random() * sats.length)], tp = satPos(sp.i, sp.a), a0 = Math.random() * 6.2832;
            var sx = cx + Math.cos(a0) * R * 0.9, sy = cy + Math.sin(a0) * R * 0.9, pts = [[sx, sy]];
            for (var sg2 = 1; sg2 < 7; sg2++) { var f = sg2 / 7; pts.push([sx + (tp.x - sx) * f + (Math.random() - 0.5) * 16, sy + (tp.y - sy) * f + (Math.random() - 0.5) * 16]); }
            pts.push([tp.x, tp.y]); arcs.push({ pts: pts, life: 1 });
            nextArc = t + (work ? 0.15 : 0.5) + Math.random() * (work ? 0.25 : 0.9);
          }
          for (var ai = arcs.length - 1; ai >= 0; ai--) {
            var ar = arcs[ai]; ar.life -= 0.09;
            if (ar.life <= 0) { arcs.splice(ai, 1); continue; }
            ctx.strokeStyle = rgba(lerpC(pal.b, [255, 255, 255], 0.6), ar.life); ctx.lineWidth = 1.4; ctx.lineJoin = "round";
            ctx.shadowColor = rgba(pal.a, 1); ctx.shadowBlur = 8; ctx.beginPath();
            ar.pts.forEach(function (q, qi) { if (qi === 0) ctx.moveTo(q[0], q[1]); else ctx.lineTo(q[0], q[1]); });
            ctx.stroke(); ctx.shadowBlur = 0;
          }
        }
      }

      function draw() {
        var L = lookRef.current || LOOK;
        var th = THEMES[L.theme] || THEMES.crimson;
        var st = stateRef.current || "idle";
        var pal = statePalette(st, th);
        var work = st === "working", wait = st === "waiting" || st === "perk", off = st === "offline";
        var glow = L.glow == null ? 1 : L.glow;
        ctx.clearRect(0, 0, W, H);
        cosmicNow = isCosmic(L);
        if (L.avatar === "watcher") { drawWatcher(L, th, st, pal, glow); return; }
        if (L.avatar === "core") { drawCore(L, th, st, pal, glow); return; }

        // ground glow and flames
        var gg = ctx.createRadialGradient(120, 292, 5, 120, 292, 120);
        gg.addColorStop(0, rgba(pal.core, Math.min(1, (work ? 0.55 : 0.35) * glow))); gg.addColorStop(1, rgba(pal.core, 0));
        ctx.fillStyle = gg; ctx.fillRect(0, 160, W, 140);
        if (!off && L.flames) {
          var nf = work ? 9 : 6;
          for (var f = 0; f < nf; f++) flame(30 + f * (180 / (nf - 1)), 296, work ? 15 : 11, ((work ? 60 : 38) + (f % 3) * 12) * (0.8 + 0.2 * glow), f * 1.7, pal);
        }

        var sw = off ? 0 : Math.sin(t * 1.3) * 4;
        var k = sw / 260;
        ctx.save();
        ctx.translate(120, 0);
        ctx.transform(1, 0, -k, 1, k * 300, 0);

        var skinTop = off ? "#7a7f99" : th.skinTop, skinBot = off ? "#555a70" : th.skinBot;
        var skin = ctx.createLinearGradient(0, 40, 0, 290); skin.addColorStop(0, skinTop); skin.addColorStop(1, skinBot);
        var rim = rgba(pal.a, 0.9);
        var outfitColor = off ? "#1c1d28" : th.outfit;
        var lap = devilLap(st);

        // wings (behind)
        if (L.wings !== "none") {
          var ws = L.wings === "large" ? 1.12 : 1;
          var flap = off ? 0.2 : 0.2 + Math.sin(t * (work ? 4 : 1.6)) * 0.12;
          [-1, 1].forEach(function (s) {
            ctx.save(); ctx.scale(s, 1);
            ctx.translate(16, 98); ctx.scale(ws, ws); ctx.translate(-16, -98);
            ctx.fillStyle = off ? "rgba(60,64,84,0.8)" : th.wing; ctx.strokeStyle = rgba(pal.core, 0.7); ctx.lineWidth = 1.2;
            ctx.beginPath(); ctx.moveTo(16, 98);
            ctx.quadraticCurveTo(70, 50 - flap * 60, 108, 62 - flap * 70);
            ctx.quadraticCurveTo(100, 82 - flap * 30, 92, 96 - flap * 30);
            ctx.quadraticCurveTo(84, 104 - flap * 20, 76, 118);
            ctx.quadraticCurveTo(64, 112, 52, 128);
            ctx.quadraticCurveTo(38, 118, 18, 130);
            ctx.closePath(); ctx.fill(); ctx.stroke(); ctx.restore();
          });
        }

        // tail (behind body)
        if (L.tail && L.outfit !== "gown" && L.outfit !== "coat") {
          var ts = Math.sin(t * 1.9) * (work ? 16 : 10);
          ctx.strokeStyle = skinBot; ctx.lineWidth = 5; ctx.lineCap = "round";
          ctx.beginPath(); ctx.moveTo(10, 196); ctx.bezierCurveTo(60, 200 + ts, 70, 250, 92 + ts, 236 - ts * 0.4); ctx.stroke();
          ctx.fillStyle = skinTop; ctx.beginPath();
          ctx.moveTo(92 + ts, 236 - ts * 0.4); ctx.lineTo(100 + ts, 222 - ts * 0.4); ctx.lineTo(108 + ts, 240 - ts * 0.4); ctx.lineTo(96 + ts, 244 - ts * 0.4); ctx.closePath(); ctx.fill();
        }

        // hair (behind head and shoulders)
        var hs = off ? 0 : Math.sin(t * 1.6) * 5;
        var hairColor = off ? "#2a2c3a" : (HAIR_COLORS[L.hairColor] || th.hair);
        var male = L.avatar === "devilman";
        var hairStyle = (male && L.hair === "bob") ? "short" : L.hair;
        if (hairStyle === "flame") {
          for (var hf = 0; hf < 7; hf++) {
            var hx = -26 + hf * (52 / 6), hh = 38 + (hf === 3 ? 22 : (hf % 2) * 10);
            flame(hx, 62, 9, off ? 0 : hh, hf * 1.3, pal);
          }
        } else {
          ctx.fillStyle = hairColor; ctx.beginPath();
          if (hairStyle === "long") {
            ctx.moveTo(-18, 52); ctx.quadraticCurveTo(-42, 100, -38 + hs, 190); ctx.quadraticCurveTo(-20 + hs, 182, -12, 104);
            ctx.lineTo(12, 104); ctx.quadraticCurveTo(20 + hs, 182, 38 + hs, 190); ctx.quadraticCurveTo(42, 100, 18, 52);
            ctx.quadraticCurveTo(0, 36, -18, 52);
          } else if (hairStyle === "slick") {
            ctx.moveTo(-18, 54); ctx.quadraticCurveTo(-27, 30, -6, 25); ctx.quadraticCurveTo(17, 21, 25, 38);
            ctx.quadraticCurveTo(29, 54, 19, 62); ctx.quadraticCurveTo(8, 42, -18, 54);
          } else if (hairStyle === "short") {
            ctx.moveTo(-17, 56); ctx.quadraticCurveTo(-25, 70, -21 + hs * 0.3, 88); ctx.quadraticCurveTo(-12, 78, -10, 70);
            ctx.lineTo(10, 70); ctx.quadraticCurveTo(12, 78, 21 + hs * 0.3, 88); ctx.quadraticCurveTo(25, 70, 17, 56);
            ctx.quadraticCurveTo(0, 36, -17, 56);
          } else {
            ctx.moveTo(-18, 52); ctx.quadraticCurveTo(-34, 90, -30 + hs, 132); ctx.quadraticCurveTo(-18 + hs, 126, -10, 100);
            ctx.lineTo(10, 100); ctx.quadraticCurveTo(18 + hs, 126, 30 + hs, 132); ctx.quadraticCurveTo(34, 90, 18, 52);
            ctx.quadraticCurveTo(0, 36, -18, 52);
          }
          ctx.closePath(); ctx.fill();
        }

        // legs and boots (a gown hides them)
        if (male) {
          [-1, 1].forEach(function (s) {
            ctx.fillStyle = outfitColor; ctx.strokeStyle = rim; ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(s * 2, 198); ctx.lineTo(s * 25, 198); ctx.quadraticCurveTo(s * 27, 240, s * 20, 270); ctx.lineTo(s * 7, 270); ctx.quadraticCurveTo(s * 8, 240, s * 2, 212); ctx.closePath(); ctx.fill(); ctx.stroke();
            ctx.fillStyle = "#14060f"; ctx.beginPath(); ctx.moveTo(s * 6, 262); ctx.lineTo(s * 21, 262); ctx.lineTo(s * 28, 288); ctx.lineTo(s * 5, 288); ctx.closePath(); ctx.fill();
          });
        } else if (L.outfit !== "gown") {
          var y0 = L.outfit === "short" ? 186 : 215;
          [-1, 1].forEach(function (s) {
            ctx.fillStyle = skin;
            ctx.beginPath(); ctx.moveTo(s * 5, y0); ctx.quadraticCurveTo(s * 26, y0 + 25, s * 18, 270);
            ctx.lineTo(s * 8, 270); ctx.quadraticCurveTo(s * 10, y0 + 25, s * 2, y0 + 7); ctx.closePath(); ctx.fill();
            ctx.fillStyle = "#14060f"; ctx.beginPath(); ctx.moveTo(s * 7, 262); ctx.lineTo(s * 19, 262); ctx.lineTo(s * 26, 288); ctx.lineTo(s * 6, 288); ctx.closePath(); ctx.fill();
          });
        }

        if (lap.behind) drawDevilLaptop(lap, pal, st);

        // dress / torso (dark with a glowing rim); for the man: broad shoulders in a suit, a long coat or an open shirt
        if (male) {
          var casual = L.outfit === "casual", longCoat = L.outfit === "coat";
          ctx.fillStyle = outfitColor; ctx.strokeStyle = rim; ctx.lineWidth = 1.4;
          ctx.beginPath(); ctx.moveTo(-9, 78); ctx.lineTo(-32, 97); ctx.quadraticCurveTo(-37, 126, -27, 160);
          if (longCoat) { ctx.quadraticCurveTo(-37, 208, -43, 262); ctx.lineTo(43, 262); ctx.quadraticCurveTo(37, 208, 27, 160); }
          else { ctx.lineTo(-25, 202); ctx.lineTo(25, 202); ctx.lineTo(27, 160); }
          ctx.quadraticCurveTo(37, 126, 32, 97); ctx.lineTo(9, 78); ctx.closePath(); ctx.fill(); ctx.stroke();
          ctx.fillStyle = casual ? skin : (off ? "#8a8fa5" : "#e9edf5");
          ctx.beginPath(); ctx.moveTo(-9, 78); ctx.lineTo(9, 78); ctx.lineTo(3.5, 124); ctx.lineTo(0, 138); ctx.lineTo(-3.5, 124); ctx.closePath(); ctx.fill();
          ctx.strokeStyle = rim; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(-9, 78); ctx.lineTo(-3.5, 124); ctx.moveTo(9, 78); ctx.lineTo(3.5, 124); ctx.stroke();
          if (!longCoat) { ctx.fillStyle = off ? "#555a70" : "#2a2230"; ctx.fillRect(-25, 196, 50, 6); ctx.fillStyle = off ? "#777777" : "#d8b45a"; ctx.fillRect(-4, 196, 8, 6); }
        } else {
        ctx.fillStyle = outfitColor; ctx.strokeStyle = rim; ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.moveTo(-6, 78); ctx.lineTo(-24, 98);
        ctx.quadraticCurveTo(-30, 120, -15, 134); ctx.quadraticCurveTo(-11, 146, -13, 152);
        if (L.outfit === "gown") {
          ctx.quadraticCurveTo(-46, 204, -52, 284); ctx.lineTo(52, 284);
          ctx.quadraticCurveTo(46, 204, 13, 152);
        } else if (L.outfit === "short") {
          ctx.quadraticCurveTo(-32, 166, -29, 190); ctx.lineTo(29, 190);
          ctx.quadraticCurveTo(32, 166, 13, 152);
        } else {
          ctx.quadraticCurveTo(-34, 178, -30, 226); ctx.lineTo(30, 226);
          ctx.quadraticCurveTo(34, 178, 13, 152);
        }
        ctx.quadraticCurveTo(11, 146, 15, 134);
        ctx.quadraticCurveTo(30, 120, 24, 98); ctx.lineTo(6, 78); ctx.closePath(); ctx.fill(); ctx.stroke();
        }

        // neck and head
        ctx.fillStyle = skin;
        if (male) {
          ctx.fillRect(-7, 70, 14, 10);
          ctx.beginPath(); ctx.ellipse(0, 59, 16.5, 18.5, 0, 0, 6.2832); ctx.fill();
          ctx.beginPath(); ctx.moveTo(-14, 64); ctx.quadraticCurveTo(-14, 79, 0, 79); ctx.quadraticCurveTo(14, 79, 14, 64); ctx.closePath(); ctx.fill();
        } else {
          ctx.fillRect(-5, 70, 10, 10);
          ctx.beginPath(); ctx.ellipse(0, 60, 15, 18, 0, 0, 6.2832); ctx.fill();
        }

        // horns
        var hg = ctx.createLinearGradient(0, 20, 0, 50); hg.addColorStop(0, off ? "#999999" : th.hornTop); hg.addColorStop(1, off ? "#555555" : th.hornBot);
        ctx.fillStyle = hg;
        if (L.horns === "crown") {
          [[-16, 12], [-8, 18], [0, 24], [8, 18], [16, 12]].forEach(function (sp) {
            ctx.beginPath(); ctx.moveTo(sp[0] - 4, 45); ctx.lineTo(sp[0], 45 - sp[1]); ctx.lineTo(sp[0] + 4, 45); ctx.closePath(); ctx.fill();
          });
        } else {
          [-1, 1].forEach(function (s) {
            ctx.beginPath();
            if (L.horns === "long") { ctx.moveTo(s * 7, 46); ctx.quadraticCurveTo(s * 28, 38, s * 31, 2); ctx.quadraticCurveTo(s * 18, 30, s * 3, 40); }
            else if (L.horns === "small") { ctx.moveTo(s * 8, 44); ctx.lineTo(s * 16, 29); ctx.lineTo(s * 4, 40); }
            else { ctx.moveTo(s * 7, 46); ctx.quadraticCurveTo(s * 22, 40, s * 20, 18); ctx.quadraticCurveTo(s * 14, 34, s * 3, 40); }
            ctx.closePath(); ctx.fill();
          });
        }

        if (BOW_COLORS[L.bow]) drawBow(-5, 40, 0.62, -0.3 + (off ? 0 : Math.sin(t * 1.6) * 0.04), BOW_COLORS[L.bow], off);

        if (TIE_COLORS[L.tie]) drawBowTie(0, male ? 84 : 81, male ? 0.62 : 0.5, 0, TIE_COLORS[L.tie], off);

        // his heavier brows and facial hair
        if (male) {
          ctx.fillStyle = hairColor; ctx.strokeStyle = hairColor; ctx.lineWidth = 2; ctx.lineCap = "round";
          if (L.beard === "full") {
            ctx.beginPath(); ctx.moveTo(-16, 62); ctx.quadraticCurveTo(-17, 86, 0, 90); ctx.quadraticCurveTo(17, 86, 16, 62); ctx.quadraticCurveTo(9, 74, 0, 72); ctx.quadraticCurveTo(-9, 74, -16, 62); ctx.closePath(); ctx.fill();
          } else if (L.beard === "goatee") {
            ctx.beginPath(); ctx.moveTo(-4.5, 72); ctx.quadraticCurveTo(0, 91, 4.5, 72); ctx.quadraticCurveTo(0, 75, -4.5, 72); ctx.fill();
            ctx.beginPath(); ctx.moveTo(0, 70); ctx.quadraticCurveTo(-6, 67, -11, 70); ctx.moveTo(0, 70); ctx.quadraticCurveTo(6, 67, 11, 70); ctx.stroke();
          }
          ctx.strokeStyle = off ? "#1c1d28" : "#2a0a14"; ctx.lineWidth = 2.4;
          ctx.beginPath(); ctx.moveTo(-14, 51.5); ctx.lineTo(-4, 55); ctx.moveTo(14, 51.5); ctx.lineTo(4, 55); ctx.stroke();
        }

        // eyes (glow, blink) and a small smirk
        ctx.save(); ctx.translate(0, 1.8 * lap.open);
        var blink = (Math.floor(t * 10) % 37) === 0;
        var eyeCol = off ? [170, 170, 170] : (EYE_COLORS[L.eyes] || pal.b);
        ctx.fillStyle = rgba(eyeCol, 1);
        if (!blink) [-1, 1].forEach(function (s) {
          ctx.beginPath(); ctx.moveTo(s * 4, 59); ctx.quadraticCurveTo(s * 9, 55, s * 12, 58); ctx.quadraticCurveTo(s * 9, 61, s * 4, 59); ctx.fill();
        });
        ctx.strokeStyle = "#5a0a1e"; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(-5, 70); ctx.quadraticCurveTo(1, 74, 6, 69); ctx.stroke();
        ctx.restore();
        if (lap.open > 0.05 && !off) {
          var fgl = ctx.createRadialGradient(0, 80, 2, 0, 74, 34); fgl.addColorStop(0, rgba(pal.a, 0.32 * lap.open)); fgl.addColorStop(1, rgba(pal.a, 0));
          ctx.fillStyle = fgl; ctx.beginPath(); ctx.ellipse(0, 60, 17, 19, 0, 0, 6.2832); ctx.fill();
        }

        // left arm on hip
        var armW = male ? 10 : 8, sleeve = (male && L.outfit !== "casual") ? outfitColor : skin;
        ctx.lineCap = "round";
        var ab = lap.armBlend, lD = [lerp(-17, lap.lh[0], ab), lerp(158, lap.lh[1], ab)];
        function leftArmPath() { ctx.beginPath(); ctx.moveTo(male ? -30 : -23, 100); ctx.quadraticCurveTo(lerp(-40, -44, ab), lerp(128, 126, ab), lerp(-30, -38, ab), lerp(152, 142, ab)); ctx.lineTo(lD[0], lD[1]); }
        if (sleeve !== skin) { ctx.strokeStyle = rim; ctx.lineWidth = armW + 2.6; leftArmPath(); ctx.stroke(); }
        ctx.strokeStyle = sleeve; ctx.lineWidth = armW; leftArmPath(); ctx.stroke();
        if (sleeve !== skin) { ctx.fillStyle = skin; ctx.beginPath(); ctx.arc(lD[0], lD[1], 5.5, 0, 6.2832); ctx.fill(); }

        // what she holds in her right hand
        var ang = off ? 0.02 : wait ? -0.08 : Math.sin(t * (work ? 3.2 : 1.1)) * (work ? 0.16 : 0.05);
        var metal = off ? "#777777" : "#d9dbe8";
        if (L.prop === "fork") {
          ctx.save(); ctx.translate(46 + 24 * lap.ep, 148 - 18 * lap.ep + lap.ep * Math.sin(t * 1.8) * 3); ctx.rotate(ang + 0.22 * lap.ep);
          ctx.strokeStyle = metal; ctx.lineWidth = 3.2; ctx.lineCap = "round";
          ctx.shadowColor = rgba(pal.a, Math.min(1, (work ? 0.95 : 0.6) * glow)); ctx.shadowBlur = (work ? 14 : 7) * glow;
          ctx.beginPath(); ctx.moveTo(0, 126); ctx.lineTo(0, -92); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(-15, -64); ctx.lineTo(-15, -100);
          ctx.moveTo(15, -64); ctx.lineTo(15, -100);
          ctx.moveTo(-15, -64); ctx.quadraticCurveTo(0, -52, 15, -64); ctx.moveTo(0, -92); ctx.lineTo(0, -108); ctx.stroke();
          [[-15, -100], [0, -108], [15, -100]].forEach(function (p) {
            ctx.fillStyle = off ? "#777777" : "#ffffff"; ctx.beginPath(); ctx.moveTo(p[0] - 3, p[1]); ctx.lineTo(p[0], p[1] - 9); ctx.lineTo(p[0] + 3, p[1]); ctx.closePath(); ctx.fill();
          });
          ctx.restore();
        } else if (L.prop === "staff") {
          ctx.save(); ctx.translate(46 + 24 * lap.ep, 148 - 18 * lap.ep + lap.ep * Math.sin(t * 1.8) * 3); ctx.rotate(ang * 0.6 + 0.22 * lap.ep);
          ctx.strokeStyle = metal; ctx.lineWidth = 3.2; ctx.lineCap = "round";
          ctx.shadowColor = rgba(pal.a, Math.min(1, 0.6 * glow)); ctx.shadowBlur = 7 * glow;
          ctx.beginPath(); ctx.moveTo(0, 126); ctx.lineTo(0, -80); ctx.stroke();
          ctx.beginPath(); ctx.arc(0, -92, 12, 0.5, 5.8); ctx.stroke();
          var pulse = 0.75 + 0.25 * Math.sin(t * 3);
          var og = ctx.createRadialGradient(0, -92, 1, 0, -92, 11 * pulse + 3);
          og.addColorStop(0, rgba(pal.b, 1)); og.addColorStop(0.6, rgba(pal.a, 0.8)); og.addColorStop(1, rgba(pal.core, 0));
          ctx.fillStyle = og; ctx.beginPath(); ctx.arc(0, -92, 14 * pulse + 3, 0, 6.2832); ctx.fill();
          ctx.restore();
        } else if (L.prop === "orb") {
          var oy = 126 + Math.sin(t * 2.2) * 4, op = 0.8 + 0.2 * Math.sin(t * 4);
          ctx.save(); ctx.translate(46 + 24 * lap.ep, oy - 16 * lap.ep);
          var og2 = ctx.createRadialGradient(0, 0, 1, 0, 0, 22 * op);
          og2.addColorStop(0, rgba(pal.b, 1)); og2.addColorStop(0.45, rgba(pal.a, 0.85)); og2.addColorStop(1, rgba(pal.core, 0));
          ctx.fillStyle = og2; ctx.beginPath(); ctx.arc(0, 0, 22 * op, 0, 6.2832); ctx.fill();
          if (!off) { flame(-6, -4, 5, 18, 0.5, pal); flame(6, -4, 5, 22, 1.7, pal); }
          ctx.restore();
        }

        // right arm: up to the prop, or on the hip when she holds nothing
        ctx.shadowBlur = 0; ctx.lineCap = "round";
        var none = L.prop === "none", rB0 = none ? [40, 128] : [44, 118], rC0 = none ? [30, 152] : [46, 146], rD0 = none ? [17, 158] : [46, 146];
        var rB = [lerp(rB0[0], 44, ab), lerp(rB0[1], 126, ab)], rC = [lerp(rC0[0], 40, ab), lerp(rC0[1], 142, ab)], rD = [lerp(rD0[0], lap.rh[0], ab), lerp(rD0[1], lap.rh[1], ab)];
        function rightArmPath() {
          ctx.beginPath(); ctx.moveTo(male ? 30 : 23, 100); ctx.quadraticCurveTo(rB[0], rB[1], rC[0], rC[1]);
          if (none || ab > 0.001) ctx.lineTo(rD[0], rD[1]);
        }
        if (sleeve !== skin) { ctx.strokeStyle = rim; ctx.lineWidth = armW + 2.6; rightArmPath(); ctx.stroke(); }
        ctx.strokeStyle = sleeve; ctx.lineWidth = armW; rightArmPath(); ctx.stroke();
        if (sleeve !== skin) { ctx.fillStyle = skin; ctx.beginPath(); ctx.arc(rD[0], rD[1], 5.5, 0, 6.2832); ctx.fill(); }

        if (lap.ep > 0 && !lap.behind) drawDevilLaptop(lap, pal, st);

        ctx.restore();
        if (sendT < 1 && lap.ep > 0.3) drawEnvelope(120 + lap.lx, lap.ly - lap.hh + 26, pal);
      }

      drawRef.current = draw;
      function onLook() { lookRef.current = LOOK; if (reduce) draw(); }
      window.addEventListener("atrium:look", onLook);

      function frame(ts) {
        raf = requestAnimationFrame(frame);
        if (document.hidden || ts - last < 33) return;
        var dt = Math.min(0.1, (ts - last) / 1000); last = ts; lastDt = dt;
        t += dt * (stateRef.current === "offline" ? 0.05 : 1);
        draw();
      }
      lookRef.current = LOOK;
      draw();
      if (!reduce) raf = requestAnimationFrame(frame);
      return function () { if (raf) cancelAnimationFrame(raf); window.removeEventListener("atrium:look", onLook); if (stopTool) stopTool(); };
    }, []);

    useEffect(function () {
      var still = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      if (still && drawRef.current) drawRef.current();
    }, [props.state]);

    return h("canvas", { ref: ref, className: "atl-orb", width: 240, height: 300, "aria-hidden": "true" });
  }

  /* ---------- hero backdrop scenes (canvas, no images): galaxy, cosmic, cyberpunk, matrix, synthwave, aurora, deep sea ---------- */
  // Style panel > Background picks one (look.scene). Each maker returns { draw(ctx, W, H, t, dt, speed, state) }.
  // To add a scene: write a maker, add it to SCENE_MAKERS and to the chips in the Style panel.

  function rnd(a, b) { return a + Math.random() * (b - a); }
  function glowAt(ctx, x, y, r, rgb, a) {
    var g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, "rgba(" + rgb + "," + a + ")"); g.addColorStop(1, "rgba(" + rgb + ",0)");
    ctx.fillStyle = g; ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }
  var STAR_COLS = ["255,255,255", "170,195,255", "255,214,170", "255,255,255", "255,190,225"];
  function mkStars(n) { var a = [], i; for (i = 0; i < n; i++) a.push({ x: Math.random(), y: Math.random(), s: 0.4 + Math.random() * 1.3, p: Math.random() * 6.28, c: i % 5 }); return a; }
  function drawStars(ctx, W, H, t, stars, a0) {
    for (var i = 0; i < stars.length; i++) {
      var s = stars[i];
      ctx.fillStyle = "rgba(" + STAR_COLS[s.c] + "," + (a0 * (0.25 + 0.75 * Math.abs(Math.sin(t * 1.1 + s.p)))).toFixed(3) + ")";
      ctx.beginPath(); ctx.arc(s.x * W, s.y * H, s.s, 0, 6.2832); ctx.fill();
    }
  }
  function streak(ctx, x, y, vx, vy, life, max) {
    var fade = Math.max(0, 1 - life / max), tx = x - vx * 0.13, ty = y - vy * 0.13;
    var g = ctx.createLinearGradient(x, y, tx, ty);
    g.addColorStop(0, "rgba(255,255,255," + (0.9 * fade).toFixed(3) + ")"); g.addColorStop(1, "rgba(150,170,255,0)");
    ctx.strokeStyle = g; ctx.lineWidth = 1.6; ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(tx, ty); ctx.stroke();
  }

  /* default: the slow drifting galaxy */
  function mkGalaxy() {
    var stars = [], i, COLS = [[139, 123, 255], [61, 214, 245], [255, 92, 200], [255, 255, 255], [255, 200, 120]];
    for (i = 0; i < 260; i++) stars.push({ a: Math.random() * 6.2832, r: Math.pow(Math.random(), 0.7), s: 0.4 + Math.random() * 1.6, c: i % 5, p: Math.random() * 6.2832 });
    return { draw: function (ctx, W, H, t, dt, speed, st) {
      var cx = W * 0.74, cy = H * 0.5, R = Math.max(W, H) * 0.62;
      var g = ctx.createRadialGradient(cx, cy, 10, cx, cy, R);
      g.addColorStop(0, st === "working" ? "rgba(255,120,60,0.22)" : "rgba(255,60,120,0.16)");
      g.addColorStop(0.5, "rgba(122,96,255,0.10)"); g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      for (var k = 0; k < stars.length; k++) {
        var s = stars[k], ang = s.a + t * 0.04 * speed * (1.2 - s.r);
        var x = cx + Math.cos(ang) * s.r * R * 1.15, y = cy + Math.sin(ang) * s.r * R * 0.55;
        var tw = 0.45 + 0.55 * Math.sin(t * 1.5 + s.p);
        ctx.fillStyle = "rgba(" + COLS[s.c].join(",") + "," + (0.18 + 0.6 * tw * (1 - s.r * 0.6)).toFixed(3) + ")";
        ctx.beginPath(); ctx.arc(x, y, s.s, 0, 6.2832); ctx.fill();
      }
    } };
  }

  /* cosmic: nebula, stars, planets (ringed, banded, rocky, icy), UFOs now and then, shooting stars */
  function drawSphere(ctx, x, y, r, c1, c2, c3) {
    var g = ctx.createRadialGradient(x - r * 0.4, y - r * 0.4, r * 0.1, x, y, r);
    g.addColorStop(0, c1); g.addColorStop(0.6, c2); g.addColorStop(1, c3);
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r, 0, 6.2832); ctx.fill();
  }
  function drawShade(ctx, x, y, r) {
    ctx.save(); ctx.beginPath(); ctx.arc(x, y, r, 0, 6.2832); ctx.clip();
    var g = ctx.createRadialGradient(x - r * 0.5, y - r * 0.5, r * 0.6, x - r * 0.5, y - r * 0.5, r * 1.7);
    g.addColorStop(0, "rgba(0,0,0,0)"); g.addColorStop(1, "rgba(0,0,12,0.78)");
    ctx.fillStyle = g; ctx.fillRect(x - r, y - r, r * 2, r * 2); ctx.restore();
  }
  function drawRing(ctx, x, y, r, front) {
    ctx.save(); ctx.translate(x, y); ctx.rotate(-0.38);
    ctx.strokeStyle = front ? "rgba(244,230,255,0.7)" : "rgba(226,205,255,0.4)"; ctx.lineWidth = r * 0.16;
    ctx.beginPath(); ctx.ellipse(0, 0, r * 1.9, r * 0.46, 0, front ? 0 : Math.PI, front ? Math.PI : 6.2832); ctx.stroke();
    ctx.restore();
  }
  function drawUfo(ctx, u, t) {
    ctx.save(); ctx.translate(u.x, u.y); ctx.scale(u.sc, u.sc); ctx.rotate(Math.sin(t * 2 + u.ph) * 0.05);
    glowAt(ctx, 0, 4, 34, "120,255,200", 0.22);
    if (u.age > 2.2 && u.age < 4.6) {
      var bg = ctx.createLinearGradient(0, 6, 0, 80); bg.addColorStop(0, "rgba(150,255,200,0.40)"); bg.addColorStop(1, "rgba(150,255,200,0)");
      ctx.fillStyle = bg; ctx.beginPath(); ctx.moveTo(-7, 6); ctx.lineTo(7, 6); ctx.lineTo(28, 80); ctx.lineTo(-28, 80); ctx.closePath(); ctx.fill();
    }
    ctx.fillStyle = "rgba(150,235,255,0.6)"; ctx.beginPath(); ctx.ellipse(0, -2, 8, 7, 0, Math.PI, 6.2832); ctx.fill();
    var b = ctx.createLinearGradient(0, -4, 0, 9); b.addColorStop(0, "#d4dbee"); b.addColorStop(1, "#555d7c");
    ctx.fillStyle = b; ctx.beginPath(); ctx.ellipse(0, 2, 21, 6.5, 0, 0, 6.2832); ctx.fill();
    for (var i = -2; i <= 2; i++) {
      var on = Math.sin(t * 6 + i * 1.3 + u.ph) > 0;
      ctx.fillStyle = on ? "#fff39a" : "#ff6fb5"; ctx.beginPath(); ctx.arc(i * 7, 3.2, 1.5, 0, 6.2832); ctx.fill();
    }
    ctx.restore();
  }
  function mkCosmic() {
    var stars = mkStars(170), ufos = [], star = null, nextStar = 2, nextUfo = 3;
    var planets = [
      { fx: 0.46, fy: 0.22, r: 0.11, kind: "ringed" },
      { fx: 0.66, fy: 1.04, r: 0.34, kind: "banded" },
      { fx: 0.92, fy: 0.80, r: 0.07, kind: "red" },
      { fx: 0.07, fy: 0.84, r: 0.05, kind: "ice" }
    ];
    return { draw: function (ctx, W, H, t, dt, sp, st) {
      var off = st === "offline", k = off ? 0.4 : 1, i;
      var bg = ctx.createLinearGradient(0, 0, 0, H); bg.addColorStop(0, "#04051a"); bg.addColorStop(1, "#0d0a2c");
      ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
      glowAt(ctx, W * 0.2 + Math.sin(t * 0.1) * 20, H * 0.3, W * 0.4, "110,70,230", 0.20 * k);
      glowAt(ctx, W * 0.8, H * 0.7 + Math.cos(t * 0.12) * 14, W * 0.38, "40,110,255", 0.16 * k);
      glowAt(ctx, W * 0.55, H * 0.12, W * 0.28, "230,70,180", 0.12 * k);
      drawStars(ctx, W, H, t, stars, 0.9 * k);
      ctx.globalAlpha = k;
      for (i = 0; i < planets.length; i++) {
        var p = planets[i], r = p.r * Math.min(H, 300), x = p.fx * W + Math.sin(t * 0.05 + i * 2) * 6, y = p.fy * H + Math.cos(t * 0.04 + i) * 3;
        if (p.kind === "ringed") {
          drawRing(ctx, x, y, r, false);
          drawSphere(ctx, x, y, r, "#d4c2ff", "#7a5ad8", "#2c1d6e"); drawShade(ctx, x, y, r);
          drawRing(ctx, x, y, r, true);
          var ma = t * 0.25; drawSphere(ctx, x + Math.cos(ma) * r * 2.5, y + Math.sin(ma) * r * 0.6, r * 0.2, "#fff", "#c9c4de", "#6a6688");
        } else if (p.kind === "banded") {
          drawSphere(ctx, x, y, r, "#ffd9a0", "#d98a4e", "#5a2c1a");
          ctx.save(); ctx.beginPath(); ctx.arc(x, y, r, 0, 6.2832); ctx.clip();
          for (var b = 0; b < 9; b++) { ctx.fillStyle = b % 2 ? "rgba(120,50,20,0.28)" : "rgba(255,230,190,0.20)"; ctx.fillRect(x - r, y - r + (b / 9) * r * 2, r * 2, r * 0.13); }
          ctx.fillStyle = "rgba(190,70,40,0.55)"; ctx.beginPath(); ctx.ellipse(x - r * 0.25, y + r * 0.05, r * 0.2, r * 0.1, 0, 0, 6.2832); ctx.fill();
          ctx.restore(); drawShade(ctx, x, y, r);
        } else if (p.kind === "red") {
          drawSphere(ctx, x, y, r, "#ff9a7a", "#c23c2c", "#3b0e10"); drawShade(ctx, x, y, r);
        } else {
          drawSphere(ctx, x, y, r, "#e8fbff", "#7cc8ea", "#1e4a6e"); drawShade(ctx, x, y, r);
        }
      }
      ctx.globalAlpha = 1;
      if (!off) {
        if (ufos.length < 2 && t > nextUfo) {
          var dir = Math.random() < 0.5 ? 1 : -1;
          ufos.push({ x: dir > 0 ? -50 : W + 50, y: rnd(0.12, 0.6) * H, vx: dir * rnd(50, 85), ph: Math.random() * 6.28, sc: rnd(0.8, 1.3), age: 0 });
          nextUfo = t + rnd(9, 18);
        }
        for (i = ufos.length - 1; i >= 0; i--) {
          var u = ufos[i]; u.age += dt; u.x += u.vx * dt * Math.max(sp, 0.5); u.y += Math.sin(t * 2.2 + u.ph) * 0.35;
          drawUfo(ctx, u, t);
          if (u.x < -80 || u.x > W + 80) ufos.splice(i, 1);
        }
        if (!star && t > nextStar) star = { x: W * rnd(0.4, 1), y: H * rnd(0, 0.35), vx: -rnd(420, 620), vy: rnd(150, 240), life: 0 };
        if (star) {
          star.x += star.vx * dt; star.y += star.vy * dt; star.life += dt;
          streak(ctx, star.x, star.y, star.vx, star.vy, star.life, 0.9);
          if (star.life > 0.9 || star.x < -40 || star.y > H + 40) { star = null; nextStar = t + rnd(5, 12); }
        }
      }
    } };
  }

  /* cyberpunk: layered neon skyline with lit windows, flickering signs, rain and flying cars */
  function mkCyber(W, H) {
    var layers = [], cars = [], drops = [], nextCar = 1.5, i;
    var cfg = [
      { sp: 3, hMin: 0.32, hMax: 0.6, col: "#150a35", win: "255,70,170", wA: 0.55 },
      { sp: 7, hMin: 0.26, hMax: 0.52, col: "#0c0722", win: "70,220,255", wA: 0.65 },
      { sp: 14, hMin: 0.16, hMax: 0.44, col: "#05030f", win: "255,210,90", wA: 0.75 }
    ];
    var NEON = [["255,60,170", "ネオン"], ["60,230,255", "電脳"], ["255,210,90", AGENT.toUpperCase().slice(0, 8)], ["170,110,255", "サイバー"]];
    cfg.forEach(function (c, li) {
      var b = [], x = -20, hh, w, cols, rows, wins, q, r;
      while (x < W + 260) {
        w = rnd(34, 80); hh = rnd(c.hMin, c.hMax) * H; cols = Math.floor((w - 6) / 9); rows = Math.floor((hh - 8) / 12); wins = [];
        for (r = 0; r < rows; r++) for (q = 0; q < cols; q++) if (Math.random() < 0.32) wins.push({ x: 4 + q * 9, y: 6 + r * 12, ph: Math.random() * 6.28, f: Math.random() < 0.12 });
        var nb = { x: x, w: w, h: hh, wins: wins, ant: Math.random() < 0.4, sign: null };
        if (li === 2 && Math.random() < 0.35) { var n = NEON[b.length % NEON.length]; nb.sign = { rgb: n[0], txt: n[1], ph: Math.random() * 6.28 }; }
        b.push(nb); x += w + rnd(2, 10);
      }
      layers.push({ c: c, b: b, total: x });
    });
    for (i = 0; i < 110; i++) drops.push({ x: Math.random() * W, y: Math.random() * H, l: rnd(8, 18), v: rnd(380, 560) });
    return { draw: function (ctx, W, H, t, dt, sp, st) {
      var off = st === "offline", k = off ? 0.45 : 1;
      var g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, "#07021c"); g.addColorStop(0.55, "#1d0a45"); g.addColorStop(1, "#4a0f5a");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      glowAt(ctx, W * 0.5, H, W * 0.6, "255,50,150", 0.32 * k);
      glowAt(ctx, W * 0.12, H * 0.25, W * 0.3, "60,200,255", 0.12 * k);
      layers.forEach(function (L) {
        var offx = (t * L.c.sp * (sp ? Math.min(sp, 1.6) : 0)) % L.total;
        L.b.forEach(function (b) {
          var bx = b.x - (L.shift || 0) - offx; if (bx + b.w < -70) bx += L.total; if (bx > W + 70) return;
          var top = H - b.h;
          ctx.fillStyle = L.c.col; ctx.fillRect(bx, top, b.w, b.h);
          ctx.fillStyle = "rgba(" + L.c.win + "," + (0.35 * k) + ")"; ctx.fillRect(bx, top, b.w, 1);
          b.wins.forEach(function (w) {
            var a = L.c.wA * (w.f ? (Math.sin(t * 4 + w.ph) > 0 ? 1 : 0.12) : 0.85) * k;
            ctx.fillStyle = "rgba(" + L.c.win + "," + a.toFixed(2) + ")"; ctx.fillRect(bx + w.x, top + w.y, 4, 5);
          });
          if (b.ant) { ctx.fillStyle = L.c.col; ctx.fillRect(bx + b.w * 0.5, top - 12, 1.5, 12); ctx.fillStyle = "rgba(255,60,60," + (Math.sin(t * 3 + b.x) > 0 ? 0.95 : 0.2) + ")"; ctx.fillRect(bx + b.w * 0.5 - 1, top - 14, 3.5, 3); }
          if (b.sign) {
            var fl = Math.sin(t * 9 + b.sign.ph) > -0.88 ? 1 : 0.15;
            ctx.save(); ctx.shadowColor = "rgb(" + b.sign.rgb + ")"; ctx.shadowBlur = 12; ctx.fillStyle = "rgba(" + b.sign.rgb + "," + (0.95 * fl * k) + ")";
            ctx.font = "bold 12px sans-serif"; ctx.textBaseline = "top"; ctx.fillText(b.sign.txt, bx + 4, top + 5); ctx.restore();
          }
        });
      });
      if (!off) {
        if (cars.length < 3 && t > nextCar) {
          var d = Math.random() < 0.5 ? 1 : -1;
          cars.push({ x: d > 0 ? -30 : W + 30, y: rnd(0.12, 0.55) * H, vx: d * rnd(110, 200), c: Math.random() < 0.5 ? "255,80,160" : "120,230,255" }); nextCar = t + rnd(2.5, 6);
        }
        for (var ci = cars.length - 1; ci >= 0; ci--) {
          var c = cars[ci]; c.x += c.vx * dt;
          var tg = ctx.createLinearGradient(c.x, c.y, c.x - c.vx * 0.3, c.y); tg.addColorStop(0, "rgba(" + c.c + ",0.9)"); tg.addColorStop(1, "rgba(" + c.c + ",0)");
          ctx.strokeStyle = tg; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(c.x, c.y); ctx.lineTo(c.x - c.vx * 0.3, c.y); ctx.stroke();
          glowAt(ctx, c.x, c.y, 9, c.c, 0.5);
          if (c.x < -60 || c.x > W + 60) cars.splice(ci, 1);
        }
      }
      ctx.strokeStyle = "rgba(170,190,255,0.22)"; ctx.lineWidth = 1; ctx.beginPath();
      for (var di = 0; di < drops.length; di++) {
        var p = drops[di]; if (!off) { p.y += p.v * dt; p.x -= p.v * dt * 0.18; }
        if (p.y > H) { p.y = -10; p.x = Math.random() * (W + 60); }
        ctx.moveTo(p.x, p.y); ctx.lineTo(p.x + p.l * 0.18, p.y - p.l);
      }
      ctx.stroke();
    } };
  }

  /* matrix: falling green glyphs */
  function mkMatrix(W, H) {
    var fs = 14, n = Math.ceil(W / fs), cols = [], i, j;
    var GL = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789Z:.=*+-<>";
    function ch() { return GL.charAt(Math.floor(Math.random() * GL.length)); }
    function fresh(c, init) { c.len = Math.floor(rnd(8, 26)); c.v = rnd(45, 130); c.y = init ? rnd(-H, H) : rnd(-H * 0.5, 0); c.ch = []; for (j = 0; j < c.len; j++) c.ch.push(ch()); }
    for (i = 0; i < n; i++) { var c = { x: i * fs }; fresh(c, true); cols.push(c); }
    return { draw: function (ctx, W, H, t, dt, sp, st) {
      var off = st === "offline", k = off ? 0.4 : 1;
      ctx.fillStyle = "#010a03"; ctx.fillRect(0, 0, W, H);
      ctx.font = fs + "px monospace"; ctx.textBaseline = "top";
      for (var i2 = 0; i2 < cols.length; i2++) {
        var c = cols[i2]; c.y += c.v * dt * sp;
        if (c.y - c.len * fs > H) fresh(c, false);
        for (var q = 0; q < c.len; q++) {
          var yy = c.y - q * fs; if (yy < -fs || yy > H) continue;
          if (!off && Math.random() < 0.012) c.ch[q] = ch();
          var a = (1 - q / c.len) * k;
          ctx.fillStyle = q === 0 ? "rgba(215,255,228," + (0.95 * k) + ")" : "rgba(0," + Math.round(150 + 105 * a) + ",60," + (a * 0.8).toFixed(2) + ")";
          ctx.fillText(c.ch[q], c.x, yy);
        }
      }
    } };
  }

  /* synthwave: striped sun, mountains and a rolling neon grid */
  function mkSynth() {
    var stars = mkStars(70), mt = [], i;
    for (i = 0; i <= 14; i++) mt.push(Math.random() * 0.5 + (i % 2 ? 0.1 : 0.25));
    return { draw: function (ctx, W, H, t, dt, sp, st) {
      var off = st === "offline", k = off ? 0.5 : 1, hz = H * 0.6, cx = W * 0.62, g, i2;
      g = ctx.createLinearGradient(0, 0, 0, hz); g.addColorStop(0, "#0d0128"); g.addColorStop(0.6, "#4b0f6e"); g.addColorStop(1, "#ff3d94");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, hz);
      drawStars(ctx, W, hz * 0.7, t, stars, 0.8 * k);
      var sr = H * 0.34, sy = hz - sr * 0.2;
      glowAt(ctx, cx, sy, sr * 1.9, "255,80,160", 0.35 * k);
      ctx.save(); ctx.beginPath(); ctx.rect(0, 0, W, hz); ctx.clip();
      var sg = ctx.createLinearGradient(0, sy - sr, 0, sy + sr); sg.addColorStop(0, "#ffe45e"); sg.addColorStop(0.5, "#ff7a45"); sg.addColorStop(1, "#ff2f9a");
      ctx.globalAlpha = k; ctx.fillStyle = sg; ctx.beginPath(); ctx.arc(cx, sy, sr, 0, 6.2832); ctx.fill();
      ctx.fillStyle = "#5a1280"; var step = sr * 0.17, ph = (t * sr * 0.05 * sp) % step;
      for (i2 = 1; i2 < 8; i2++) ctx.fillRect(cx - sr, sy + i2 * step - ph, sr * 2, step * 0.09 * i2);
      ctx.restore(); ctx.globalAlpha = 1;
      ctx.fillStyle = "#13022f"; ctx.beginPath(); ctx.moveTo(0, hz);
      for (i2 = 0; i2 < mt.length; i2++) ctx.lineTo(i2 * W / (mt.length - 1), hz - mt[i2] * H * 0.3);
      ctx.lineTo(W, hz); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = "rgba(255,70,170," + (0.6 * k) + ")"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(0, hz);
      for (i2 = 0; i2 < mt.length; i2++) ctx.lineTo(i2 * W / (mt.length - 1), hz - mt[i2] * H * 0.3);
      ctx.stroke();
      g = ctx.createLinearGradient(0, hz, 0, H); g.addColorStop(0, "#1a0238"); g.addColorStop(1, "#050010");
      ctx.fillStyle = g; ctx.fillRect(0, hz, W, H - hz);
      ctx.lineWidth = 1;
      for (i2 = 0; i2 < 12; i2++) {
        var z = ((i2 + t * 0.5 * sp) % 12) / 12, y = hz + (H - hz) * z * z;
        ctx.strokeStyle = "rgba(255,60,170," + ((0.25 + 0.6 * z) * k).toFixed(2) + ")"; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
      }
      for (i2 = -16; i2 <= 16; i2++) {
        ctx.strokeStyle = "rgba(90,210,255," + (0.35 * k) + ")"; ctx.beginPath(); ctx.moveTo(cx + i2 * W * 0.012, hz); ctx.lineTo(cx + i2 * W * 0.11, H); ctx.stroke();
      }
      glowAt(ctx, cx, hz, W * 0.5, "255,60,170", 0.12 * k);
    } };
  }

  /* aurora: green and violet curtains under a starry sky */
  function mkAurora() {
    var stars = mkStars(120);
    var cur = [{ y: 0.18, h: 0.5, rgb: "70,255,170", s: 0.9 }, { y: 0.28, h: 0.45, rgb: "110,150,255", s: 1.3 }, { y: 0.1, h: 0.42, rgb: "200,110,255", s: 0.7 }];
    return { draw: function (ctx, W, H, t, dt, sp, st) {
      var k = st === "offline" ? 0.4 : 1, g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, "#02040f"); g.addColorStop(1, "#06202a");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      drawStars(ctx, W, H * 0.7, t, stars, 0.85 * k);
      var tt = t * (sp ? Math.min(sp, 1.6) : 0.0001) * 0.5;
      cur.forEach(function (c, ci) {
        var hh = c.h * H, gg = ctx.createLinearGradient(0, 0, 0, hh);
        gg.addColorStop(0, "rgba(" + c.rgb + ",0)"); gg.addColorStop(0.35, "rgba(" + c.rgb + "," + (0.5 * k) + ")"); gg.addColorStop(1, "rgba(" + c.rgb + ",0)");
        ctx.fillStyle = gg;
        for (var x = 0; x < W; x += 5) {
          var y0 = c.y * H + Math.sin(x * 0.012 * c.s + tt * 1.2 + ci) * H * 0.1 + Math.sin(x * 0.03 + tt * 2.1 + ci * 2) * H * 0.03;
          var amp = 0.55 + 0.45 * Math.sin(x * 0.02 + tt * 1.6 + ci * 3);
          ctx.save(); ctx.translate(0, y0); ctx.globalAlpha = amp; ctx.fillRect(x, 0, 5, hh); ctx.restore();
        }
      });
      ctx.fillStyle = "#03080c"; ctx.beginPath(); ctx.moveTo(0, H);
      for (var x2 = 0; x2 <= W; x2 += 24) ctx.lineTo(x2, H - 10 - 8 * Math.abs(Math.sin(x2 * 0.021)) - (x2 % 72 === 0 ? 14 : 0));
      ctx.lineTo(W, H); ctx.closePath(); ctx.fill();
    } };
  }

  /* deep sea: light rays, bubbles, glowing jellyfish and the odd school of fish */
  function mkDeepSea(W, H) {
    var bubbles = [], jelly = [], fish = null, nextFish = 4, i;
    for (i = 0; i < 40; i++) bubbles.push({ x: Math.random() * W, y: Math.random() * H, r: rnd(1, 3.4), v: rnd(10, 34), ph: Math.random() * 6.28 });
    var JC = ["120,200,255", "255,130,210", "150,255,200"];
    for (i = 0; i < 3; i++) jelly.push({ x: W * (0.25 + i * 0.28), y: H * rnd(0.3, 0.6), s: rnd(0.8, 1.25), ph: Math.random() * 6.28, rgb: JC[i] });
    return { draw: function (ctx, W, H, t, dt, sp, st) {
      var off = st === "offline", k = off ? 0.45 : 1, g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, "#073559"); g.addColorStop(1, "#010812");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      for (i = 0; i < 5; i++) {
        var x0 = W * (0.05 + 0.22 * i) + Math.sin(t * 0.2 + i) * 30, rg = ctx.createLinearGradient(0, 0, 0, H);
        rg.addColorStop(0, "rgba(140,210,255," + (0.16 * k) + ")"); rg.addColorStop(1, "rgba(140,210,255,0)");
        ctx.fillStyle = rg; ctx.beginPath(); ctx.moveTo(x0, 0); ctx.lineTo(x0 + 46, 0); ctx.lineTo(x0 + 46 - H * 0.35, H); ctx.lineTo(x0 - H * 0.35, H); ctx.closePath(); ctx.fill();
      }
      jelly.forEach(function (j) {
        var jy = j.y + Math.sin(t * 0.35 + j.ph) * 22 * (off ? 0 : 1), pulse = 1 + 0.09 * Math.sin(t * 2 + j.ph), r = 20 * j.s;
        glowAt(ctx, j.x, jy, r * 2.6, j.rgb, 0.18 * k);
        ctx.strokeStyle = "rgba(" + j.rgb + "," + (0.35 * k) + ")"; ctx.lineWidth = 1.2;
        for (var q = 0; q < 7; q++) {
          ctx.beginPath(); var bx = j.x + (q - 3) * r * 0.28; ctx.moveTo(bx, jy + 2);
          for (var s = 1; s <= 8; s++) ctx.lineTo(bx + Math.sin(t * 1.6 + s * 0.7 + q) * 4 * s * 0.25, jy + 2 + s * r * 0.28);
          ctx.stroke();
        }
        var bg = ctx.createRadialGradient(j.x, jy, 2, j.x, jy, r * pulse); bg.addColorStop(0, "rgba(255,255,255," + (0.5 * k) + ")"); bg.addColorStop(1, "rgba(" + j.rgb + "," + (0.18 * k) + ")");
        ctx.fillStyle = bg; ctx.beginPath(); ctx.ellipse(j.x, jy, r * pulse, r * 0.75 * (2 - pulse), 0, Math.PI, 6.2832); ctx.fill();
      });
      ctx.strokeStyle = "rgba(190,235,255," + (0.35 * k) + ")"; ctx.lineWidth = 1;
      bubbles.forEach(function (b) {
        if (!off) b.y -= b.v * dt; if (b.y < -6) { b.y = H + 6; b.x = Math.random() * W; }
        ctx.beginPath(); ctx.arc(b.x + Math.sin(t + b.ph) * 4, b.y, b.r, 0, 6.2832); ctx.stroke();
      });
      if (!off) {
        if (!fish && t > nextFish) { var d = Math.random() < 0.5 ? 1 : -1; fish = { x: d > 0 ? -60 : W + 60, y: rnd(0.55, 0.85) * H, d: d, f: [] }; for (i = 0; i < 9; i++) fish.f.push({ dx: -d * i * rnd(10, 18), dy: rnd(-14, 14), ph: Math.random() * 6.28 }); }
        if (fish) {
          fish.x += fish.d * 70 * dt;
          ctx.fillStyle = "rgba(110,190,235,0.5)";
          fish.f.forEach(function (f) {
            var fx = fish.x + f.dx, fy = fish.y + f.dy + Math.sin(t * 3 + f.ph) * 2;
            ctx.beginPath(); ctx.ellipse(fx, fy, 6, 2.4, 0, 0, 6.2832); ctx.fill();
            ctx.beginPath(); ctx.moveTo(fx - fish.d * 5, fy); ctx.lineTo(fx - fish.d * 10, fy - 3); ctx.lineTo(fx - fish.d * 10, fy + 3); ctx.closePath(); ctx.fill();
          });
          if (fish.x < -140 || fish.x > W + 140) { fish = null; nextFish = t + rnd(14, 24); }
        }
      }
    } };
  }

  /* inferno: a lava cavern. Glowing cracks, rock spires, rising embers and the odd burst of flame */
  function mkInferno(W, H) {
    var embers = [], spires = [], i, burst = null, nextBurst = 3, cracks = [];
    for (i = 0; i < 70; i++) embers.push({ x: Math.random() * W, y: Math.random() * H, r: rnd(0.8, 2.6), v: rnd(18, 60), sw: Math.random() * 6.28, hue: Math.random() });
    for (i = 0; i <= 16; i++) spires.push(rnd(0.14, 0.46) * (i % 2 ? 0.7 : 1));
    for (i = 0; i < 9; i++) cracks.push({ x: Math.random() * W, w: rnd(30, 90), ph: Math.random() * 6.28 });
    return { draw: function (ctx, W, H, t, dt, sp, st) {
      var off = st === "offline", k = off ? 0.4 : 1, g, i2, fl = 0.85 + 0.15 * Math.sin(t * 3.1) * Math.sin(t * 1.7);
      g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, "#0a0204"); g.addColorStop(0.6, "#33070a"); g.addColorStop(1, "#8a1c06");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      glowAt(ctx, W * 0.5, H * 1.02, W * 0.7, "255,90,20", 0.42 * k * fl);
      glowAt(ctx, W * 0.15, H, W * 0.3, "255,50,20", 0.25 * k);
      // rock spires rising from the lava, darker toward the back
      ctx.fillStyle = "#12030a"; ctx.beginPath(); ctx.moveTo(0, H);
      for (i2 = 0; i2 < spires.length; i2++) { var sx = i2 * W / (spires.length - 1); ctx.lineTo(sx - 6, H - spires[i2] * H * 0.5); ctx.lineTo(sx + 5, H - spires[i2] * H * 0.2); }
      ctx.lineTo(W, H); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = "rgba(255,110,40," + (0.35 * k * fl) + ")"; ctx.lineWidth = 1; ctx.stroke();
      // the lava floor with glowing cracks
      var lh = H * 0.12, lg = ctx.createLinearGradient(0, H - lh, 0, H); lg.addColorStop(0, "#ff6a1a"); lg.addColorStop(1, "#ffd05a");
      ctx.globalAlpha = k; ctx.fillStyle = lg; ctx.fillRect(0, H - lh, W, lh); ctx.globalAlpha = 1;
      cracks.forEach(function (c) {
        ctx.fillStyle = "rgba(60,8,4,0.8)"; ctx.fillRect(c.x, H - lh, c.w, 2);
        ctx.fillStyle = "rgba(255,230,140," + (0.5 + 0.4 * Math.sin(t * 2 + c.ph)) * k + ")"; ctx.fillRect(c.x + c.w * 0.2, H - lh + 3, c.w * 0.5, 2);
      });
      if (!off) {
        if (!burst && t > nextBurst) burst = { x: rnd(0.1, 0.9) * W, life: 0, h: rnd(0.25, 0.5) * H };
        if (burst) {
          burst.life += dt; var u = burst.life / 1.4, hh = burst.h * Math.sin(Math.min(1, u) * 3.14);
          var fg = ctx.createLinearGradient(0, H - lh - hh, 0, H - lh); fg.addColorStop(0, "rgba(255,200,80,0)"); fg.addColorStop(0.5, "rgba(255,120,30,0.55)"); fg.addColorStop(1, "rgba(255,60,20,0.2)");
          ctx.fillStyle = fg; ctx.beginPath(); ctx.moveTo(burst.x - 14, H - lh); ctx.quadraticCurveTo(burst.x - 6 + Math.sin(t * 9) * 6, H - lh - hh * 0.6, burst.x, H - lh - hh); ctx.quadraticCurveTo(burst.x + 6 + Math.sin(t * 8) * 6, H - lh - hh * 0.6, burst.x + 14, H - lh); ctx.closePath(); ctx.fill();
          if (u > 1) { burst = null; nextBurst = t + rnd(4, 9); }
        }
      }
      for (i2 = 0; i2 < embers.length; i2++) {
        var e = embers[i2]; if (!off) { e.y -= e.v * dt * Math.max(sp, 0.6); }
        if (e.y < -8) { e.y = H + 6; e.x = Math.random() * W; }
        var a = Math.max(0, Math.min(1, e.y / H)) * (0.55 + 0.4 * Math.sin(t * 5 + e.sw)) * k;
        ctx.fillStyle = "rgba(255," + Math.round(120 + e.hue * 100) + "," + Math.round(30 + e.hue * 40) + "," + a.toFixed(3) + ")";
        ctx.beginPath(); ctx.arc(e.x + Math.sin(t * 0.9 + e.sw) * 10, e.y, e.r, 0, 6.2832); ctx.fill();
      }
    } };
  }

  /* gothic: blood moon, castle on a crag, bats, drifting fog, a flash of lightning */
  function mkGothic(W, H) {
    var bats = [], nextBat = 1, flash = 0, nextFlash = 6, stars = mkStars(50), towers = [], i;
    for (i = 0; i < 9; i++) towers.push({ x: rnd(0.45, 0.95), w: rnd(0.02, 0.05), h: rnd(0.2, 0.5), spire: Math.random() < 0.7, win: Math.random() < 0.6 });
    function bat(ctx, b, t) {
      var f = Math.sin(t * 14 + b.ph) * 5;
      ctx.save(); ctx.translate(b.x, b.y); ctx.scale(b.sc * (b.vx > 0 ? 1 : -1), b.sc);
      ctx.fillStyle = "#05010a"; ctx.beginPath(); ctx.moveTo(0, 0);
      ctx.quadraticCurveTo(6, -8 + f, 15, -2 + f); ctx.quadraticCurveTo(10, 0, 8, 3); ctx.quadraticCurveTo(4, 1, 0, 4);
      ctx.quadraticCurveTo(-4, 1, -8, 3); ctx.quadraticCurveTo(-10, 0, -15, -2 + f); ctx.quadraticCurveTo(-6, -8 + f, 0, 0); ctx.fill();
      ctx.beginPath(); ctx.arc(0, 1, 2.6, 0, 6.2832); ctx.fill(); ctx.restore();
    }
    return { draw: function (ctx, W, H, t, dt, sp, st) {
      var off = st === "offline", k = off ? 0.45 : 1, g, i2;
      g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, "#07020c"); g.addColorStop(0.7, "#240a1c"); g.addColorStop(1, "#43101c");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      drawStars(ctx, W, H * 0.6, t, stars, 0.5 * k);
      var mx = W * 0.7, my = H * 0.3, mr = Math.min(H, 300) * 0.2;
      glowAt(ctx, mx, my, mr * 4, "200,30,50", 0.3 * k);
      var mg = ctx.createRadialGradient(mx - mr * 0.3, my - mr * 0.3, mr * 0.1, mx, my, mr); mg.addColorStop(0, "#ffb09a"); mg.addColorStop(0.6, "#d8283c"); mg.addColorStop(1, "#6a0c1c");
      ctx.globalAlpha = k; ctx.fillStyle = mg; ctx.beginPath(); ctx.arc(mx, my, mr, 0, 6.2832); ctx.fill(); ctx.globalAlpha = 1;
      // castle on a crag
      var by = H - Math.min(H, 300) * 0.04;
      ctx.fillStyle = "#07020d"; ctx.beginPath(); ctx.moveTo(W * 0.4, H);
      ctx.lineTo(W * 0.46, by - H * 0.1); ctx.lineTo(W * 0.62, by - H * 0.18); ctx.lineTo(W * 0.82, by - H * 0.12); ctx.lineTo(W, by - H * 0.05); ctx.lineTo(W, H); ctx.closePath(); ctx.fill();
      towers.forEach(function (tw) {
        var tx = tw.x * W, th = tw.h * Math.min(H, 300), tww = tw.w * W, tb = by - H * 0.1;
        ctx.fillStyle = "#07020d"; ctx.fillRect(tx, tb - th, tww, th + H * 0.1);
        if (tw.spire) { ctx.beginPath(); ctx.moveTo(tx - 2, tb - th); ctx.lineTo(tx + tww / 2, tb - th - tww * 1.9); ctx.lineTo(tx + tww + 2, tb - th); ctx.closePath(); ctx.fill(); }
        if (tw.win) { ctx.fillStyle = "rgba(255,170,70," + ((0.5 + 0.4 * Math.sin(t * 2 + tx)) * k).toFixed(2) + ")"; ctx.fillRect(tx + tww * 0.38, tb - th + th * 0.3, 3, 6); }
      });
      if (!off) {
        if (bats.length < 5 && t > nextBat) { var d = Math.random() < 0.5 ? 1 : -1; bats.push({ x: d > 0 ? -20 : W + 20, y: rnd(0.1, 0.6) * H, vx: d * rnd(60, 120), ph: Math.random() * 6.28, sc: rnd(0.8, 1.5) }); nextBat = t + rnd(1.5, 4); }
        for (i2 = bats.length - 1; i2 >= 0; i2--) { var b = bats[i2]; b.x += b.vx * dt; b.y += Math.sin(t * 3 + b.ph) * 0.6; bat(ctx, b, t); if (b.x < -40 || b.x > W + 40) bats.splice(i2, 1); }
      }
      // fog banks
      for (i2 = 0; i2 < 3; i2++) glowAt(ctx, ((t * (6 + i2 * 3) + i2 * W * 0.4) % (W * 1.4)) - W * 0.2, H * (0.82 + i2 * 0.05), W * 0.32, "150,120,170", 0.14 * k);
      if (!off) {
        if (t > nextFlash) { flash = 1; nextFlash = t + rnd(8, 18); }
        if (flash > 0) { ctx.fillStyle = "rgba(200,190,255," + (flash * 0.18 * (Math.sin(t * 60) > 0 ? 1 : 0.4)).toFixed(3) + ")"; ctx.fillRect(0, 0, W, H); flash -= dt * 3; }
      }
    } };
  }

  var SCENE_MAKERS = { galaxy: mkGalaxy, cosmic: mkCosmic, cyberpunk: mkCyber, matrix: mkMatrix, synthwave: mkSynth, aurora: mkAurora, deepsea: mkDeepSea, inferno: mkInferno, gothic: mkGothic };
  function sceneName(L) {
    var s = (L && L.scene) || "galaxy";
    if (s === "night") { var hr = new Date().getHours(); return hr >= 19 || hr < 7 ? "cosmic" : "galaxy"; }
    return SCENE_MAKERS[s] ? s : "galaxy";
  }

  function Galaxy(props) {
    var ref = useRef(null);
    var stateRef = useRef(props.state);
    stateRef.current = props.state;
    useEffect(function () {
      var cv = ref.current;
      if (!cv || !cv.getContext) return undefined;
      var ctx = cv.getContext("2d");
      var reduce = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      var W = 0, H = 0, dpr = Math.min(window.devicePixelRatio || 1, 2), raf = 0, last = 0, t = 0, name = "", scene = null;
      function size() {
        var b = cv.parentNode.getBoundingClientRect();
        W = Math.max(300, Math.floor(b.width)); H = Math.max(200, Math.floor(b.height));
        cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + "px"; cv.style.height = H + "px";
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        scene = null;
      }
      function draw(dt) {
        var n = sceneName(getLook());
        if (!scene || n !== name) { name = n; scene = SCENE_MAKERS[n](W, H); }
        var st = stateRef.current;
        var speed = st === "working" ? 3 : st === "offline" ? 0 : 1;
        ctx.clearRect(0, 0, W, H);
        scene.draw(ctx, W, H, t, dt, speed, st);
        if (name !== "galaxy") {
          // keep the greeting readable: a soft shade behind the text on the left
          var sg = ctx.createLinearGradient(0, 0, W * 0.65, 0);
          sg.addColorStop(0, "rgba(5,6,16,0.55)"); sg.addColorStop(1, "rgba(5,6,16,0)");
          ctx.fillStyle = sg; ctx.fillRect(0, 0, W, H);
        }
      }
      function onLook() { if (reduce) draw(0.016); }
      function frame(ts) {
        raf = requestAnimationFrame(frame);
        if (document.hidden || ts - last < 40) return;
        var dt = Math.min(0.1, (ts - last) / 1000); last = ts; t += dt; draw(dt);
      }
      size(); draw(0.016);
      window.addEventListener("resize", size);
      window.addEventListener("atrium:look", onLook);
      if (!reduce) raf = requestAnimationFrame(frame);
      return function () { if (raf) cancelAnimationFrame(raf); window.removeEventListener("resize", size); window.removeEventListener("atrium:look", onLook); };
    }, []);
    return h("canvas", { ref: ref, className: "atl-galaxy", "aria-hidden": "true" });
  }

  window.__AVATARS__ = { Orb: Orb, THEMES: THEMES, BOW_COLORS: BOW_COLORS, TIE_COLORS: TIE_COLORS, HAIR_COLORS: HAIR_COLORS, EYE_COLORS: EYE_COLORS,
    Galaxy: Galaxy, LOOK_DEFAULT: LOOK_DEFAULT, LOOK_PRESETS: LOOK_PRESETS, mergeLook: mergeLook, getLook: getLook, setLook: setLook, resetLook: resetLook };
  // the chat page draws the character beside a conversation from window.__ATRIUM_UI__; an install that supplies its own Orb keeps it
  window.__ATRIUM_UI__ = Object.assign({ Orb: Orb }, window.__ATRIUM_UI__ || {});
})();
