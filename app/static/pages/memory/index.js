/**
 * Atrium Loom: Memory (dashboard tab plugin)
 *
 * the agent's notes (an Obsidian-compatible vault) as a graph: every note is a dot, every [[link]] a line, and each folder
 * is a bigger dot its notes cluster around. Click a note to read it and follow its links; search; hide folders.
 * Read-only. Drawn on a canvas with a small built-in force layout (no libraries, no outside requests).
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var REG = window.__HERMES_PLUGINS__;
  if (!SDK || !REG) return;

  /* The agent's name. Text in this file says {{agent}} wherever it means the name; h() fills it in. The name is
     read once from the Chat plugin (set in the Settings tab). A synchronous request keeps this simple: it is one tiny
     local call, cached for the browser session, and falls back to "Assistant". */
  var AGENT = (function () {
    try { var cached = window.sessionStorage.getItem("atrium.agent"); if (cached) return cached; } catch (x) { /* storage blocked */ }
    var name = "Assistant";
    try {
      var xhr = new XMLHttpRequest();
      xhr.open("GET", "/api/plugins/atrium-chat/agent", false);
      xhr.withCredentials = true;
      xhr.send(null);
      if (xhr.status === 200) { var j = JSON.parse(xhr.responseText); if (j && typeof j.name === "string" && j.name) name = j.name; }
    } catch (x) { /* keep the default */ }
    try { window.sessionStorage.setItem("atrium.agent", name); } catch (x) { /* ignore */ }
    return name;
  })();
  function R(v) { return typeof v === "string" && v.indexOf("{{agent}}") >= 0 ? v.split("{{agent}}").join(AGENT) : v; }
  var h = function (type, props) {
    var a = Array.prototype.slice.call(arguments);
    if (props && typeof props === "object" && !Array.isArray(props)) {
      var p = {};
      for (var k in props) if (Object.prototype.hasOwnProperty.call(props, k)) p[k] = R(props[k]);
      a[1] = p;
    }
    for (var i = 2; i < a.length; i++) a[i] = Array.isArray(a[i]) ? a[i].map(R) : R(a[i]);
    return SDK.React.createElement.apply(null, a);
  };
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useRef = SDK.hooks.useRef;
  var fetchJSON = SDK.fetchJSON;
  var API = "/api/plugins/atrium-memory";

  var FOLDER_COLORS = { Daily: "#9a78ff", Radar: "#3dd6f5", Briefings: "#ff6fb5", Weekly: "#ffc857", Projects: "#38e1a5", People: "#ff9a5c", Decisions: "#ff5c7a", Reference: "#6fa8ff", Inbox: "#9aa3c4", "(top)": "#ffffff" };

  function colorFor(folder) {
    if (FOLDER_COLORS[folder]) return FOLDER_COLORS[folder];
    var n = 0;
    for (var i = 0; i < folder.length; i++) n = (n * 31 + folder.charCodeAt(i)) % 360;
    return "hsl(" + n + ",70%,66%)";
  }

  function errText(e) {
    var m = String((e && e.message) || e || "");
    return m.replace(/^\d+:\s*/, "").slice(0, 160) || "Something went wrong.";
  }

  function when(ts) {
    return new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }

  /* ---------- a small markdown renderer for note text ---------- */

  function inline(text, onLink, keyBase) {
    var out = [], re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[\[[^\]]+\]\])|(\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g, last = 0, m, k = 0;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      var tok = m[0];
      if (m[1]) out.push(h("code", { key: keyBase + "c" + k, className: "atm-code" }, tok.slice(1, -1)));
      else if (m[2]) out.push(h("strong", { key: keyBase + "b" + k }, tok.slice(2, -2)));
      else if (m[3]) {
        var inner = tok.slice(2, -2), parts = inner.split("|"), target = parts[0], label = parts[1] || parts[0].split("/").pop();
        out.push(h("button", { key: keyBase + "w" + k, type: "button", className: "atm-wiki", onClick: function (t) { return function () { onLink(t); }; }(target) }, label));
      } else {
        var mm = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(tok);
        out.push(h("a", { key: keyBase + "a" + k, href: mm[2], target: "_blank", rel: "noopener noreferrer", className: "atm-link" }, mm[1]));
      }
      last = m.index + tok.length; k++;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }

  function renderMd(text, onLink) {
    var lines = text.split("\n"), out = [], i = 0, key = 0;
    while (i < lines.length) {
      var line = lines[i], t = line.trim();
      if (/^```/.test(t)) {
        var code = []; i++;
        while (i < lines.length && !/^```/.test(lines[i].trim())) { code.push(lines[i]); i++; }
        i++; out.push(h("pre", { key: key++, className: "atm-pre" }, code.join("\n"))); continue;
      }
      var hm = /^(#{1,4})\s+(.*)$/.exec(t);
      if (hm) { out.push(h("div", { key: key++, className: "atm-h atm-h" + hm[1].length }, inline(hm[2], onLink, "h" + key))); i++; continue; }
      if (/^[-*]\s+/.test(t)) {
        var items = [];
        while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) { items.push(lines[i].trim().replace(/^[-*]\s+/, "")); i++; }
        out.push(h("ul", { key: key++, className: "atm-ul" }, items.map(function (it, n) { return h("li", { key: n }, inline(it, onLink, "l" + key + n)); }))); continue;
      }
      if (/^>\s?/.test(t)) { out.push(h("div", { key: key++, className: "atm-quote" }, inline(t.replace(/^>\s?/, ""), onLink, "q" + key))); i++; continue; }
      if (!t) { i++; continue; }
      var para = [t]; i++;
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|[-*]\s|>)/.test(lines[i].trim())) { para.push(lines[i].trim()); i++; }
      out.push(h("p", { key: key++, className: "atm-p" }, inline(para.join(" "), onLink, "p" + key)));
    }
    return out;
  }

  /* ---------- the graph ---------- */

  function GraphView(props) {
    var cvRef = useRef(null);
    var live = useRef({});
    live.current.props = props;

    useEffect(function () {
      var data = props.data;
      var nodes = [], edges = [], byId = {}, i;
      var hubs = {};
      var folders = data.folders.map(function (f) { return f.name; });
      folders.forEach(function (f, idx) {
        var gal = props.mode === "galaxy", ang = gal ? 0.6 + idx * 2.1 : (idx / Math.max(1, folders.length)) * 6.2832, rad = gal ? 70 + idx * 42 : 160;
        var n = { id: "folder:" + f, title: f, folder: f, kind: "hub", x: Math.cos(ang) * rad, y: Math.sin(ang) * rad, tx: gal ? Math.cos(ang) * rad : null, ty: gal ? Math.sin(ang) * rad : null, vx: 0, vy: 0, r: 10, color: colorFor(f), links: 0 };
        hubs[f] = n; byId[n.id] = n; nodes.push(n);
      });
      data.nodes.forEach(function (d) {
        var hub = hubs[d.folder], n = { id: d.id, title: d.title, folder: d.folder, kind: "note", x: hub.x + (Math.random() - 0.5) * 60, y: hub.y + (Math.random() - 0.5) * 60, vx: 0, vy: 0, r: 4 + Math.min(7, Math.sqrt(d.in * 2 + d.out)) * 1.4, color: colorFor(d.folder), links: d.in + d.out, summary: d.summary, mtime: d.mtime || 0 };
        byId[n.id] = n; nodes.push(n);
        edges.push({ a: n, b: hub, kind: "hub" });
      });
      data.edges.forEach(function (e) { if (byId[e[0]] && byId[e[1]]) edges.push({ a: byId[e[0]], b: byId[e[1]], kind: "link" }); });
      var S = live.current;
      S.nodes = nodes; S.edges = edges; S.byId = byId; S.alpha = 1; S.hover = null; S.fitted = false;

      var cv = cvRef.current, ctx = cv.getContext("2d");
      var reduce = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      var raf = 0, W = 0, H = 0, dpr = Math.min(window.devicePixelRatio || 1, 2);

      function size() {
        var b = cv.parentNode.getBoundingClientRect();
        W = Math.max(280, Math.floor(b.width)); H = Math.max(260, Math.floor(b.height));
        cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + "px"; cv.style.height = H + "px";
        S.W = W; S.H = H;
      }
      function visible(n) { return !S.props.hidden[n.folder]; }

      function fit() {
        var vs = S.nodes.filter(visible);
        if (!vs.length) return;
        var x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
        vs.forEach(function (n) { x0 = Math.min(x0, n.x); x1 = Math.max(x1, n.x); y0 = Math.min(y0, n.y); y1 = Math.max(y1, n.y); });
        var v = S.props.view;
        v.zoom = Math.max(0.3, Math.min(2.2, 0.82 * Math.min(W / Math.max(120, x1 - x0), H / Math.max(120, y1 - y0))));
        v.panX = -((x0 + x1) / 2) * v.zoom; v.panY = -((y0 + y1) / 2) * v.zoom;
      }
      S.fit = fit;

      function step() {
        var N = S.nodes, E = S.edges, a = S.alpha, cell = 70, grid = {}, k, n, m;
        for (k = 0; k < N.length; k++) { n = N[k]; var key = Math.floor(n.x / cell) + "," + Math.floor(n.y / cell); (grid[key] = grid[key] || []).push(n); }
        for (k = 0; k < N.length; k++) {
          n = N[k];
          var cx = Math.floor(n.x / cell), cy = Math.floor(n.y / cell);
          for (var gx = -1; gx <= 1; gx++) for (var gy = -1; gy <= 1; gy++) {
            var bucket = grid[(cx + gx) + "," + (cy + gy)];
            if (!bucket) continue;
            for (var q = 0; q < bucket.length; q++) {
              m = bucket[q];
              if (m === n) continue;
              var dx = n.x - m.x, dy = n.y - m.y, d2 = dx * dx + dy * dy + 0.01;
              if (d2 > cell * cell) continue;
              var f = (n.kind === "hub" || m.kind === "hub" ? 900 : 380) / d2 * a;
              var d = Math.sqrt(d2);
              n.vx += dx / d * f; n.vy += dy / d * f;
            }
          }
        }
        for (k = 0; k < E.length; k++) {
          var e = E[k], ex = e.b.x - e.a.x, ey = e.b.y - e.a.y, ed = Math.sqrt(ex * ex + ey * ey) || 1;
          var rest = e.kind === "hub" ? 46 : 80, str = (e.kind === "hub" ? 0.05 : 0.03) * a;
          var ff = (ed - rest) * str;
          e.a.vx += ex / ed * ff; e.a.vy += ey / ed * ff;
          e.b.vx -= ex / ed * ff; e.b.vy -= ey / ed * ff;
        }
        for (k = 0; k < N.length; k++) {
          n = N[k];
          if (n.fixed) { n.vx = n.vy = 0; continue; }
          if (n.tx != null) { n.vx += (n.tx - n.x) * 0.06 * a; n.vy += (n.ty - n.y) * 0.06 * a; }
          else { n.vx += -n.x * 0.004 * a; n.vy += -n.y * 0.004 * a; }
          n.vx *= 0.82; n.vy *= 0.82;
          n.x += n.vx; n.y += n.vy;
        }
        S.alpha = Math.max(0.0, a * 0.985);
      }

      /* ---- galaxy mode: the same notes and links, drawn as glowing stars on a night sky ---- */
      var stars = [], sprites = {};
      for (i = 0; i < 150; i++) stars.push({ x: ((i * 0.6180339) % 1), y: ((i * 0.7548776 + 0.31) % 1), r: 0.4 + ((i * 37) % 10) / 10 * 1.1, p: 0.02 + ((i * 53) % 10) / 10 * 0.1, f: 0.6 + ((i * 29) % 10) / 6, ph: i });
      function sprite(color) {
        if (sprites[color]) return sprites[color];
        var c = document.createElement("canvas"); c.width = c.height = 96;
        var g = c.getContext("2d"), gr = g.createRadialGradient(48, 48, 0, 48, 48, 48);
        gr.addColorStop(0, color); gr.addColorStop(0.25, color); gr.addColorStop(1, "rgba(0,0,0,0)");
        g.globalAlpha = 0.55; g.fillStyle = gr; g.beginPath(); g.arc(48, 48, 48, 0, 6.2832); g.fill();
        sprites[color] = c; return c;
      }
      function drawGalaxy(t) {
        var P = S.props, v = P.view, hl = P.highlight, sel = P.selected, hov = S.hover, k, n;
        var day = Date.now() / 1000 - 86400;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.globalCompositeOperation = "source-over";
        ctx.clearRect(0, 0, W, H);
        for (k = 0; k < stars.length; k++) {
          var s = stars[k], sx = (((s.x * W + v.panX * s.p + t * 0.006 * s.p * 10) % W) + W) % W, sy = (((s.y * H + v.panY * s.p) % H) + H) % H;
          ctx.globalAlpha = reduce ? 0.45 : 0.3 + 0.3 * Math.sin(t * 0.001 * s.f + s.ph);
          ctx.fillStyle = "#cfd6ff"; ctx.fillRect(sx, sy, s.r, s.r);
        }
        ctx.globalAlpha = 1;
        ctx.setTransform(dpr * v.zoom, 0, 0, dpr * v.zoom, dpr * (W / 2 + v.panX), dpr * (H / 2 + v.panY));
        var near = {};
        if (sel || hov) {
          var focus = hov ? hov.id : sel;
          near[focus] = 1;
          S.edges.forEach(function (e) { if (e.kind === "link" && (e.a.id === focus || e.b.id === focus)) { near[e.a.id] = 1; near[e.b.id] = 1; } });
        }
        S.edges.forEach(function (e) {
          if (!visible(e.a) || !visible(e.b)) return;
          var on = near[e.a.id] && near[e.b.id] && e.kind === "link";
          ctx.strokeStyle = e.kind === "hub" ? "rgba(150,160,255,0.05)" : on ? "rgba(255,150,205,0.85)" : "rgba(170,180,255,0.2)";
          ctx.lineWidth = (e.kind === "hub" ? 0.6 : on ? 1.8 : 1) / v.zoom;
          ctx.beginPath(); ctx.moveTo(e.a.x, e.a.y); ctx.lineTo(e.b.x, e.b.y); ctx.stroke();
        });
        ctx.globalCompositeOperation = "lighter";
        ctx.globalAlpha = 0.5; ctx.drawImage(sprite("rgba(255,214,150,1)"), -120, -120, 240, 240);
        ctx.globalAlpha = 0.8; ctx.drawImage(sprite("rgba(255,244,225,1)"), -34, -34, 68, 68);
        for (k = 0; k < S.nodes.length; k++) {
          n = S.nodes[k];
          if (!visible(n)) continue;
          var dim = (hl && n.kind === "note" && !hl[n.id]) || ((sel || hov) && !near[n.id] && n.kind === "note");
          var fresh = n.kind === "note" && n.mtime > day;
          var pulse = fresh && !reduce ? 1 + 0.25 * Math.sin(t * 0.003 + n.x) : 1;
          var halo = (n.kind === "hub" ? n.r * 5 : n.r * (fresh ? 5 : 3.4)) * pulse;
          ctx.globalAlpha = dim ? 0.12 : (fresh ? 1 : 0.85);
          ctx.drawImage(sprite(n.color), n.x - halo, n.y - halo, halo * 2, halo * 2);
        }
        ctx.globalCompositeOperation = "source-over";
        for (k = 0; k < S.nodes.length; k++) {
          n = S.nodes[k];
          if (!visible(n)) continue;
          var dm = (hl && n.kind === "note" && !hl[n.id]) || ((sel || hov) && !near[n.id] && n.kind === "note");
          ctx.globalAlpha = dm ? 0.2 : 1;
          ctx.fillStyle = n.kind === "hub" ? "#ffffff" : "rgba(255,255,255,0.92)";
          ctx.beginPath(); ctx.arc(n.x, n.y, n.kind === "hub" ? n.r * 0.55 : Math.max(1.6, n.r * 0.42), 0, 6.2832); ctx.fill();
          if (sel === n.id) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.6 / v.zoom; ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 3, 0, 6.2832); ctx.stroke(); }
        }
        ctx.globalAlpha = 1;
        drawLabels(v, hl, sel, hov);
      }

      function draw(t) {
        if (S.props.mode === "galaxy") { drawGalaxy(t || 0); return; }
        var P = S.props, v = P.view, hl = P.highlight, sel = P.selected, hov = S.hover;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, W, H);
        ctx.setTransform(dpr * v.zoom, 0, 0, dpr * v.zoom, dpr * (W / 2 + v.panX), dpr * (H / 2 + v.panY));
        var near = {}, k, n;
        if (sel || hov) {
          var focus = hov ? hov.id : sel;
          near[focus] = 1;
          S.edges.forEach(function (e) { if (e.kind === "link" && (e.a.id === focus || e.b.id === focus)) { near[e.a.id] = 1; near[e.b.id] = 1; } });
        }
        S.edges.forEach(function (e) {
          if (!visible(e.a) || !visible(e.b)) return;
          var on = near[e.a.id] && near[e.b.id] && e.kind === "link";
          ctx.strokeStyle = e.kind === "hub" ? "rgba(160,170,255,0.10)" : on ? "rgba(255,111,181,0.9)" : "rgba(190,200,255,0.38)";
          ctx.lineWidth = (e.kind === "hub" ? 0.8 : on ? 2 : 1.2) / v.zoom;
          ctx.beginPath(); ctx.moveTo(e.a.x, e.a.y); ctx.lineTo(e.b.x, e.b.y); ctx.stroke();
        });
        for (k = 0; k < S.nodes.length; k++) {
          n = S.nodes[k];
          if (!visible(n)) continue;
          var dim = (hl && n.kind === "note" && !hl[n.id]) || ((sel || hov) && !near[n.id] && n.kind === "note");
          ctx.globalAlpha = dim ? 0.18 : 1;
          if (n.kind === "hub") {
            ctx.fillStyle = "rgba(10,13,30,0.9)"; ctx.strokeStyle = n.color; ctx.lineWidth = 2 / v.zoom;
            ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, 6.2832); ctx.fill(); ctx.stroke();
          } else {
            ctx.shadowColor = n.color; ctx.shadowBlur = (sel === n.id ? 18 : 7) / v.zoom;
            ctx.fillStyle = n.color; ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, 6.2832); ctx.fill(); ctx.shadowBlur = 0;
            if (sel === n.id) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 2 / v.zoom; ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 3, 0, 6.2832); ctx.stroke(); }
          }
          ctx.globalAlpha = 1;
        }
        drawLabels(v, hl, sel, hov);
      }

      function drawLabels(v, hl, sel, hov) {
        var k, n;
        ctx.font = (12 / v.zoom) + "px sans-serif"; ctx.textBaseline = "middle";
        for (k = 0; k < S.nodes.length; k++) {
          n = S.nodes[k];
          if (!visible(n)) continue;
          var show = n.kind === "hub" || n.id === sel || (hov && hov.id === n.id) || (hl && hl[n.id]) || v.zoom > 1.5;
          if (!show) continue;
          var label = n.kind === "hub" ? n.title + "/" : n.title;
          ctx.fillStyle = "rgba(6,8,20,0.78)"; var tw = ctx.measureText(label).width;
          ctx.fillRect(n.x + n.r + 3, n.y - 8 / v.zoom, tw + 8 / v.zoom, 16 / v.zoom);
          ctx.fillStyle = n.kind === "hub" ? n.color : "#e6e9ff"; ctx.fillText(label, n.x + n.r + 7 / v.zoom, n.y);
        }
      }
      S.draw = function () { draw(0); };

      function frame() {
        raf = requestAnimationFrame(frame);
        if (document.hidden) return;
        if (S.alpha > 0.01 || S.dragNode) step();
        if (!S.fitted && S.alpha < 0.55) { fit(); S.fitted = true; }
        if (S.fitted && !S.fitted2 && !S.touched && S.alpha < 0.08) { fit(); S.fitted2 = true; }
        draw(performance.now());
      }
      size();
      if (reduce) { for (i = 0; i < 260; i++) step(); fit(); S.fitted = true; draw(0); }
      else raf = requestAnimationFrame(frame);

      function toWorld(ev) {
        var r = cv.getBoundingClientRect(), v = S.props.view;
        return { x: (ev.clientX - r.left - W / 2 - v.panX) / v.zoom, y: (ev.clientY - r.top - H / 2 - v.panY) / v.zoom, sx: ev.clientX - r.left, sy: ev.clientY - r.top };
      }
      function hit(p) {
        var v = S.props.view;
        for (var k = S.nodes.length - 1; k >= 0; k--) {
          var n = S.nodes[k];
          if (!visible(n)) continue;
          var dx = p.x - n.x, dy = p.y - n.y, rr = n.r + 4 / v.zoom;
          if (dx * dx + dy * dy <= rr * rr) return n;
        }
        return null;
      }
      var drag = null;
      function down(ev) {
        var p = toWorld(ev), n = hit(p);
        S.touched = true;
        cv.setPointerCapture(ev.pointerId);
        drag = { node: n, x: ev.clientX, y: ev.clientY, moved: 0 };
        if (n) { n.fixed = true; S.dragNode = n; S.alpha = Math.max(S.alpha, 0.3); }
      }
      function move(ev) {
        var p = toWorld(ev);
        if (!drag) { var hn = hit(p); S.hover = hn && hn.kind === "note" ? hn : hn; cv.style.cursor = hn ? "pointer" : "grab"; return; }
        var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
        drag.moved += Math.abs(dx) + Math.abs(dy); drag.x = ev.clientX; drag.y = ev.clientY;
        if (drag.node) { drag.node.x = p.x; drag.node.y = p.y; S.alpha = Math.max(S.alpha, 0.3); }
        else { var v = S.props.view; v.panX += dx; v.panY += dy; cv.style.cursor = "grabbing"; }
      }
      function up() {
        if (!drag) return;
        var n = drag.node;
        if (n) { n.fixed = false; S.dragNode = null; if (drag.moved < 6 && n.kind === "note") S.props.onSelect(n.id); }
        else if (drag.moved < 6) S.props.onSelect(null);
        drag = null; cv.style.cursor = "grab";
      }
      function wheel(ev) {
        ev.preventDefault();
        var v = S.props.view, p = toWorld(ev), z = Math.max(0.25, Math.min(4, v.zoom * (ev.deltaY < 0 ? 1.12 : 1 / 1.12)));
        v.panX = p.sx - W / 2 - p.x * z; v.panY = p.sy - H / 2 - p.y * z; v.zoom = z;
      }
      function leave() { S.hover = null; }
      cv.addEventListener("pointerdown", down); cv.addEventListener("pointermove", move); cv.addEventListener("pointerup", up);
      cv.addEventListener("pointercancel", up); cv.addEventListener("wheel", wheel, { passive: false }); cv.addEventListener("pointerleave", leave);
      window.addEventListener("resize", size);
      return function () {
        if (raf) cancelAnimationFrame(raf);
        window.removeEventListener("resize", size);
        cv.removeEventListener("pointerdown", down); cv.removeEventListener("pointermove", move); cv.removeEventListener("pointerup", up);
        cv.removeEventListener("pointercancel", up); cv.removeEventListener("wheel", wheel); cv.removeEventListener("pointerleave", leave);
      };
    }, [props.data, props.mode]);

    useEffect(function () { if (props.fitSignal && live.current.fit) live.current.fit(); }, [props.fitSignal]);
    useEffect(function () { if (live.current.draw) live.current.draw(); }, [props.mode]);
    return h("canvas", { ref: cvRef, className: "atm-canvas", "aria-label": "Graph of {{agent}}'s notes" });
  }

  /* ---------- page ---------- */

  function Page() {
    var d_ = useState(null), data = d_[0], setData = d_[1];
    var e_ = useState(""), err = e_[0], setErr = e_[1];
    var s_ = useState(null), selected = s_[0], setSelected = s_[1];
    var n_ = useState(null), note = n_[0], setNote = n_[1];
    var q_ = useState(""), query = q_[0], setQuery = q_[1];
    var r_ = useState(null), results = r_[0], setResults = r_[1];
    var hd_ = useState({}), hidden = hd_[0], setHidden = hd_[1];
    var f_ = useState(0), fitSignal = f_[0], setFit = f_[1];
    var viewRef = useRef({ zoom: 1, panX: 0, panY: 0 });
    var m_ = useState(function () { try { return localStorage.getItem("atm-mode") === "classic" ? "classic" : "galaxy"; } catch (e) { return "galaxy"; } }), mode = m_[0], setModeState = m_[1];
    function setMode(m) { setModeState(m); try { localStorage.setItem("atm-mode", m); } catch (e) { /* remembering the choice is optional */ } }

    useEffect(function () {
      fetchJSON(API + "/graph").then(setData).catch(function (e) { setErr(errText(e)); });
    }, []);

    function openNote(id) {
      setSelected(id);
      if (!id) { setNote(null); return; }
      fetchJSON(API + "/note?path=" + encodeURIComponent(id)).then(setNote).catch(function () { setNote({ id: id, title: id, body: "That note could not be opened.", links: [], backlinks: [] }); });
    }
    function openByLink(target) {
      if (!data) return;
      var t = target.split("|")[0].split("#")[0].trim().toLowerCase();
      var hit = data.nodes.filter(function (n) { var id = n.id.toLowerCase(); return id === t || id === t + ".md" || id.replace(/\.md$/, "").split("/").pop() === t.split("/").pop(); })[0];
      if (hit) openNote(hit.id);
    }
    useEffect(function () {
      var q = query.trim();
      if (q.length < 2) { setResults(null); return undefined; }
      var t = setTimeout(function () { fetchJSON(API + "/search?q=" + encodeURIComponent(q)).then(function (r) { setResults(r.results || []); }).catch(function () { setResults([]); }); }, 250);
      return function () { clearTimeout(t); };
    }, [query]);

    if (err) return h("div", { className: "atm-page" }, h("div", { className: "atm-err" }, err));
    if (!data) return h("div", { className: "atm-page" }, h(window.__APP_UI__.kit.Loading, { label: "Loading {{agent}}'s notes…" }));

    var hl = null;
    if (results) { hl = {}; results.forEach(function (r) { hl[r.id] = 1; }); }
    var byId = {};
    data.nodes.forEach(function (n) { byId[n.id] = n; });

    function toggleFolder(f) { var n = {}; Object.keys(hidden).forEach(function (k) { n[k] = hidden[k]; }); n[f] = !n[f]; setHidden(n); }
    function zoom(f) { var v = viewRef.current; v.zoom = Math.max(0.25, Math.min(4, v.zoom * f)); }

    return h("div", { className: "atm-page" },
      h("div", { className: "atm-top" },
        h("div", null, h("div", { className: "atm-title" }, "Memory"), h("div", { className: "atm-sub" }, data.nodes.length + " notes · " + data.edges.length + " links · click a dot to read it, drag to move, scroll to zoom")),
        h("input", { className: "atm-search", type: "search", placeholder: "Search notes…", value: query, onChange: function (e) { setQuery(e.target.value); } })),
      h("div", { className: "atm-legend" },
        h("div", { className: "atm-seg", role: "group", "aria-label": "View" },
          ["galaxy", "classic"].map(function (m) {
            return h("button", { key: m, type: "button", className: mode === m ? "atm-seg-on" : "", "aria-pressed": mode === m, onClick: function () { setMode(m); } }, m === "galaxy" ? "Galaxy" : "Classic");
          })),
        data.folders.map(function (f) {
          return h("button", { key: f.name, type: "button", className: "atm-fchip" + (hidden[f.name] ? " atm-off" : ""), onClick: function () { toggleFolder(f.name); } },
            h("span", { className: "atm-dot", style: { background: colorFor(f.name) } }), f.name + " (" + f.count + ")");
        })),
      h("div", { className: "atm-main" },
        h("div", { className: "atm-graph" + (mode === "galaxy" ? " atm-galaxy" : "") },
          h(GraphView, { data: data, hidden: hidden, highlight: hl, selected: selected, view: viewRef.current, onSelect: openNote, fitSignal: fitSignal, mode: mode }),
          mode === "galaxy" ? h("div", { className: "atm-stats" },
            h("div", null, h("b", null, data.nodes.length), h("span", null, data.nodes.length === 1 ? "note" : "notes")),
            h("div", null, h("b", null, data.edges.length), h("span", null, data.edges.length === 1 ? "connection" : "connections")),
            h("div", null, h("b", null, data.nodes.filter(function (n) { return n.mtime > Date.now() / 1000 - 86400; }).length), h("span", null, "changed today"))) : null,
          h("div", { className: "atm-zoom" },
            h("button", { type: "button", "aria-label": "Zoom in", onClick: function () { zoom(1.25); } }, "+"),
            h("button", { type: "button", "aria-label": "Zoom out", onClick: function () { zoom(0.8); } }, "−"),
            h("button", { type: "button", "aria-label": "Fit all", onClick: function () { setFit(fitSignal + 1); } }, "⤢"))),
        h("aside", { className: "atm-side" },
          results ? h("div", { className: "atm-results" },
            h("div", { className: "atm-side-title" }, results.length + (results.length === 1 ? " match" : " matches")),
            results.map(function (r) { return h("button", { key: r.id, type: "button", className: "atm-result", onClick: function () { openNote(r.id); } }, h("div", { className: "atm-result-t" }, r.title), h("div", { className: "atm-result-s" }, r.folder + " · " + r.snippet)); })) : null,
          note ? h("div", { className: "atm-note" },
            h("div", { className: "atm-note-title" }, note.title),
            h("div", { className: "atm-note-meta" }, note.id + (note.mtime ? " · changed " + when(note.mtime) : "")),
            h("div", { className: "atm-body" }, renderMd(note.body || "", openByLink)),
            note.cut ? h("div", { className: "atm-muted" }, "(The rest of this note is cut off.)") : null,
            note.links && note.links.length ? h("div", { className: "atm-rel" }, h("div", { className: "atm-side-title" }, "Links to"), note.links.map(function (l) { return h("button", { key: l.id, type: "button", className: "atm-wiki", onClick: function () { openNote(l.id); } }, l.title); })) : null,
            note.backlinks && note.backlinks.length ? h("div", { className: "atm-rel" }, h("div", { className: "atm-side-title" }, "Linked from"), note.backlinks.map(function (l) { return h("button", { key: l.id, type: "button", className: "atm-wiki", onClick: function () { openNote(l.id); } }, l.title); })) : null)
          : !results ? h("div", { className: "atm-recent" },
            h("div", { className: "atm-side-title" }, "Recently changed"),
            data.recent.map(function (id) { var n = byId[id]; return n ? h("button", { key: id, type: "button", className: "atm-result", onClick: function () { openNote(id); } }, h("div", { className: "atm-result-t" }, n.title), h("div", { className: "atm-result-s" }, n.folder + " · " + when(n.mtime))) : null; })) : null)));
  }

  window.__APP_SUBPAGES__ = Object.assign(window.__APP_SUBPAGES__ || {}, { memory: Page });     // shown inside Settings
})();
