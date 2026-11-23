/* Systems: the server and everything running on it. A framework page; which services, websites and models are listed
   comes from the install's own configuration, and the numbers come from the status files its jobs write. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__, UI = window.__APP_UI__;
  if (!SDK || !REG || !UI) return;
  var h = UI.h, Card = UI.Card, Chip = UI.Chip, Dot = UI.Dot, Bar = UI.Bar, Stat = UI.Stat, Empty = UI.Empty;
  var TONE = { up: "ok", down: "bad", unmonitored: "mute", unknown: "warn" };
  var WORD = { up: "Operational", down: "Down", unmonitored: "Not monitored", unknown: "Unknown" };

  function Tile(p) {
    return h("div", { className: "hm-tile" },
      h("div", { className: "hm-tile-top" }, h("strong", null, p.name), h(Chip, { tone: TONE[p.state] || "mute", dot: true }, WORD[p.state] || p.state)),
      p.url ? h("div", { className: "hm-sub hm-host" }, String(p.url).replace(/^https?:\/\//, "")) : null,
      p.url ? h("a", { className: "hm-open", href: p.url, target: "_blank", rel: "noopener noreferrer" }, "Open ↗") : null);
  }

  function Gpus(props) {
    var gpus = (props.system && props.system.gpus) || [];
    var used = gpus.reduce(function (s, g) { return s + g.mem_used; }, 0), total = gpus.reduce(function (s, g) { return s + g.mem_total; }, 0);
    return h(Card, { title: "GPU cluster", aside: gpus.length + " devices · " + UI.gb(used, 0) + " of " + UI.gb(total, 0) + " GB in use" },
      gpus.length ? h("div", { className: "hm-gpus" }, gpus.map(function (g) {
        var pct = 100 * g.mem_used / g.mem_total;
        return h("div", { key: g.index, className: "hm-gpu" },
          h("div", { className: "hm-tile-top" }, h("strong", null, g.name), h("span", { className: "hm-sub" }, "GPU " + g.index)),
          h("div", { className: "hm-gpu-row" }, h("span", { className: "hm-sub" }, "load"), h(Bar, { pct: g.util, tone: "accent" }), h("b", null, Math.round(g.util) + "%")),
          h("div", { className: "hm-gpu-row" }, h("span", { className: "hm-sub" }, "memory"), h(Bar, { pct: pct }), h("b", null, UI.gb(g.mem_used) + "/" + UI.gb(g.mem_total))),
          h("div", { className: "hm-sub" }, Math.round(g.temp) + "°C · " + Math.round(g.power) + " W"));
      })) : h(Empty, null, "No GPU data."));
  }

  function Machine(props) {
    var s = props.system || {}, cpu = s.cpu || {}, mem = s.mem || {}, disks = s.disks || [];
    var memUsed = mem.total ? 100 * (1 - mem.available / mem.total) : 0, swapUsed = mem.swap_total ? 100 * (1 - mem.swap_free / mem.swap_total) : 0;
    return h(Card, { title: "Machine", aside: s.uptime_s ? "up " + Math.round(s.uptime_s / 86400) + " days" : null },
      h("div", { className: "hm-meter" }, h("span", null, "CPU · " + (cpu.cores || "?") + " cores" + (cpu.temp ? " · " + Math.round(cpu.temp) + "°C" : "")), h(Bar, { pct: cpu.pct || 0 }), h("b", null, Math.round(cpu.pct || 0) + "%")),
      cpu.load ? h("div", { className: "hm-sub hm-meter-sub" }, "load " + cpu.load.join(" · ")) : null,
      h("div", { className: "hm-meter" }, h("span", null, "Memory"), h(Bar, { pct: memUsed }), h("b", null, UI.gb(mem.total - mem.available) + "/" + UI.gb(mem.total, 0) + " GB")),
      mem.swap_total ? h("div", { className: "hm-meter" }, h("span", null, "Swap"), h(Bar, { pct: swapUsed, tone: "accent" }), h("b", null, UI.gb(mem.swap_total - mem.swap_free) + "/" + UI.gb(mem.swap_total, 0) + " GB")) : null,
      disks.map(function (d) {
        return h("div", { className: "hm-meter", key: d.label }, h("span", null, d.label), h(Bar, { pct: 100 * d.used / d.total }), h("b", null, UI.gb(d.used, 0) + " GB / " + (d.total >= 1e12 ? (d.total / 1e12).toFixed(1) + " TB" : UI.gb(d.total, 0) + " GB")));
      }));
  }

  function Ai(props) {
    var ai = (props.extra && props.extra.ai) || [];
    var up = ai.filter(function (a) { return a.up; }).length;
    return h(Card, { title: "Local AI stack", aside: ai.length ? up + "/" + ai.length + " running" : null },
      ai.length ? h("ul", { className: "hm-list" }, ai.map(function (a) {
        return h("li", { key: a.name, className: "hm-item" }, h(Dot, { tone: a.up ? (a.healthy === false ? "warn" : "ok") : "bad" }), h("div", null, h("div", { className: "hm-item-t" }, a.role), h("div", { className: "hm-sub" }, a.status)));
      })) : h(Empty, null, "No AI services reported."));
  }

  function Backups(props) {
    var b = (props.extra && props.extra.backup) || {};
    return h(Card, { title: "Backups", aside: b.ok === undefined ? null : h(Chip, { tone: b.ok ? "ok" : "bad", dot: true }, b.ok ? "OK" : "Failed") },
      b.last_ts ? h("div", { className: "hm-kv" }, h("div", null, h("span", { className: "hm-sub" }, "Last run"), h("strong", null, UI.ago(b.last_ts))),
        b.next_ts ? h("div", null, h("span", { className: "hm-sub" }, "Next run"), h("strong", null, UI.until(b.next_ts))) : null,
        b.disk ? h("div", null, h("span", { className: "hm-sub" }, "Backup disk"), h("strong", null, b.disk)) : null) : h(Empty, null, "No backup information."),
      b.last_result ? h("p", { className: "hm-fine" }, b.last_result) : null);
  }

  function Security(props) {
    var s = (props.extra && props.extra.security) || {}, m = (props.extra && props.extra.mail) || {};
    var items = [["Failed sign-ins (24 h)", s.failed_ssh_24h, s.failed_ssh_24h > 0 ? "warn" : "ok"], ["Soonest certificate expiry", s.cert_days_left == null ? "-" : Math.round(s.cert_days_left) + " days", s.cert_days_left < 21 ? "warn" : "ok"],
      ["Updates waiting", s.pending_updates, s.pending_updates > 0 ? "warn" : "ok"], ["Restart needed", s.reboot_required ? "Yes" : "No", s.reboot_required ? "warn" : "ok"]];
    return h(Card, { title: "Security and mail" },
      h("ul", { className: "hm-list" }, items.map(function (it) { return h("li", { key: it[0], className: "hm-item" }, h(Dot, { tone: it[2] }), h("div", null, h("span", { className: "hm-item-t" }, it[0] + ": "), h("strong", null, String(it[1] === undefined ? "-" : it[1])))); }),
        m.containers !== undefined ? h("li", { className: "hm-item" }, h(Dot, { tone: (m.unhealthy || []).length ? "bad" : "ok" }), h("div", null, h("span", { className: "hm-item-t" }, "Mail containers: "), h("strong", null, (m.containers - (m.unhealthy || []).length) + "/" + m.containers + " healthy"))) : null));
  }

  function Flow(props) {
    var loc = props.local || [], ext = props.external || [];
    if (!loc.length && !ext.length) return null;
    return h(Card, { title: "Data flow", aside: "where your data goes" },
      h("p", { className: "hm-sub" }, "Your data stays on this server. Requests can reach " + loc.length + " local model" + (loc.length === 1 ? "" : "s") + " and " + ext.length + " outside service" + (ext.length === 1 ? "" : "s") + "."),
      h("div", { className: "hm-flow" },
        h("div", null, h("h3", null, "Local"), h("ul", { className: "hm-bullets" }, loc.map(function (x, i) { return h("li", { key: i }, h("strong", null, x.name), x.role ? " · " + x.role : ""); }))),
        h("div", null, h("h3", null, "Outside"), h("ul", { className: "hm-bullets" }, ext.map(function (x, i) { return h("li", { key: i }, h("strong", null, x.name), x.role ? " · " + x.role : ""); })))));
  }

  function Page() {
    var a = UI.usePoll("/api/systems", 30000), d = a[0], err = a[1];
    if (!d) return h("div", { className: "hm" }, err ? h(window.__APP_UI__.kit.Alert, { tone: "danger", title: "Could not load the systems" }, err) : h(window.__APP_UI__.kit.Loading, { label: "Loading…" }));
    var bad = d.services.concat(d.websites).filter(function (s) { return s.state === "down"; }).length;
    return h("div", { className: "hm" },
      h("header", { className: "hm-top" }, h("div", null, h("h1", null, "Systems"), h("p", { className: "hm-sub" }, "Updated " + UI.ago(d.generated))),
        h(Chip, { tone: bad ? "bad" : "ok", dot: true }, bad ? bad + " down" : "All up")),
      d.stale ? h("p", { className: "hm-banner" }, "Some status data is more than 30 minutes old. The jobs that write it may have stopped.") : null,
      h(Card, { title: "Services", aside: d.services.length + " services" }, d.services.length ? h("div", { className: "hm-tiles" }, d.services.map(function (s) { return h(Tile, { key: s.name, name: s.name, url: s.url, state: s.state }); })) : h(Empty, null, "No services are configured for this install.")),
      d.websites.length ? h(Card, { title: "Websites", aside: d.websites.length + " sites" }, h("div", { className: "hm-tiles" }, d.websites.map(function (s) { return h(Tile, { key: s.name, name: s.name, url: s.url, state: s.state }); }))) : null,
      h(Gpus, { system: d.system }),
      h("div", { className: "hm-grid" }, h(Machine, { system: d.system }), h(Ai, { extra: d.extra }), h(Backups, { extra: d.extra }), h(Security, { extra: d.extra }), h(Flow, { local: d.local_models, external: d.external })));
  }

  REG.register("systems-page", Page);
})();
