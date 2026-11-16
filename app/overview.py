"""What the Home and Systems pages show, gathered from Hermes and from the files an install keeps.

Sources (all read-only; every one is optional, a missing source just leaves its part empty):
  - Hermes dashboard routes: status, sessions, usage, task board
  - the agent's notes vault: today's focus, the latest radar note, the daily log
  - the scheduled jobs, from the Hermes dashboard route (the jobs file itself is private to Hermes)
  - "deck" data files an install's own jobs write: status.json (monitors), system.json, extra.json, weather.json, river.json
  - the install's systems.json (instance folder): the services, websites and model lists to show

Nothing here names a person or a host. Names come from the files and the instance configuration.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import re
import time
from pathlib import Path

from hermes import Hermes

FOCUS_HEADING = "## Today's Focus"
LOG_HEADING = "## Log"
_TASK_RE = re.compile(r"^- \[( |x|X)\] (.*)$")
_WIKI_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")
_RADAR_RE = re.compile(r"^\s*(?:(\d+)[.)]|[-*])\s+\*\*(.+?)\*\*\s*\|\s*([^|]*)\|\s*([^|]*)(?:\|\s*(\S+))?\s*$")
STALE_S = 30 * 60
LEVEL_ORDER = {"bad": 0, "warn": 1, "info": 2}


def _json(path: Path | None):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _wiki(text: str) -> str:
    return _WIKI_RE.sub(lambda m: m.group(1), text).strip()


# ---- notes vault ---------------------------------------------------------------------------------------------------

def parse_focus(text: str) -> list[dict]:
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.strip() == FOCUS_HEADING), None)
    if start is None:
        return []
    items = []
    for ln in lines[start + 1:]:
        if ln.startswith("#"):
            break
        m = _TASK_RE.match(ln)
        if m:
            items.append({"i": len(items), "text": m.group(2)[:200], "done": m.group(1) != " "})
    return items


def latest_radar(vault: Path | None) -> dict:
    if not vault:
        return {"date": None, "items": []}
    try:
        files = sorted(p for p in (vault / "Radar").glob("????-??-??.md") if p.is_file())
    except OSError:
        files = []
    if not files:
        return {"date": None, "items": []}
    items = []
    for ln in _read(files[-1]).split("\n"):
        m = _RADAR_RE.match(_WIKI_RE.sub(lambda mm: mm.group(1), ln))     # links first: their "|" would split the line
        if m and len(items) < 10:
            url = (m.group(5) or "").strip()
            items.append({"rank": int(m.group(1)) if m.group(1) else len(items) + 1, "title": m.group(2).strip()[:140], "tag": m.group(3).strip()[:24],
                          "why": m.group(4).strip()[:300], "url": url if url.startswith(("https://", "http://")) else ""})
    return {"date": files[-1].stem, "items": items}


def latest_daylog(vault: Path | None, today: datetime.date) -> dict:
    if not vault:
        return {"date": None, "items": []}
    for back in range(0, 7):
        day = (today - datetime.timedelta(days=back)).isoformat()
        lines = _read(vault / "Daily" / (day + ".md")).split("\n")
        start = next((i for i, ln in enumerate(lines) if ln.strip() == LOG_HEADING), None)
        if start is None:
            continue
        items = []
        for ln in lines[start + 1:]:
            if ln.startswith("#"):
                break
            s = ln.strip()
            if s.startswith("- ") and len(items) < 12:
                items.append(_wiki(s[2:])[:300])
        if items:
            return {"date": day, "items": items}
    return {"date": None, "items": []}


# ---- scheduled jobs and the task board -------------------------------------------------------------------------------

def read_jobs(data) -> list[dict]:
    """Jobs as the dashboard API lists them: a list, or {"jobs": [...]}."""
    jobs = data.get("jobs", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    out = []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        out.append({"name": str(j.get("name", "?"))[:60], "enabled": bool(j.get("enabled", True)), "state": j.get("state"),
                    "last_status": j.get("last_status"), "last_error": (str(j["last_error"])[:160] if j.get("last_error") else None),
                    "last_run_at": j.get("last_run_at"), "next_run_at": j.get("next_run_at"),
                    "schedule": j.get("schedule_display"), "streak": int(j.get("failure_streak") or 0)})
    return out


def board_summary(board) -> dict:
    cols = board.get("columns", []) if isinstance(board, dict) else []
    columns = [{"name": str(c.get("name")), "count": len(c.get("tasks", []))} for c in cols if isinstance(c, dict)]
    cards = []
    for c in cols:
        for t in c.get("tasks", []) if isinstance(c, dict) else []:
            cards.append({"id": t.get("id"), "title": str(t.get("title", ""))[:140], "status": t.get("status") or c.get("name"),
                          "assignee": t.get("assignee")})
    done = {"done", "archived"}
    cards = [c for c in cards if c["status"] not in done][:8]
    return {"columns": columns, "cards": cards}


# ---- the "needs you" list -------------------------------------------------------------------------------------------------

def build_attention(jobs, board, monitors, system, extra, agent, now: float, infra: bool) -> list[dict]:
    items: list[dict] = []

    def add(level, kind, title, detail=""):
        items.append({"level": level, "kind": kind, "title": title, "detail": detail})

    for j in jobs:
        if j["enabled"] and (j["last_status"] in ("error", "failed") or j["streak"] > 0):
            add("bad", "job", "Job failed: %s" % j["name"], j["last_error"] or "It failed on its last run.")
    blocked = next((c["count"] for c in board.get("columns", []) if c["name"] == "blocked"), 0)
    if blocked:
        add("warn", "board", "%d blocked card%s on the board" % (blocked, "" if blocked == 1 else "s"), "Someone needs to unblock them.")
    if agent and agent.get("running") is False:
        add("bad", "agent", "The agent gateway is not running")
    if infra:
        for m in monitors:
            if m.get("state") != "up":
                add("bad", "monitor", "%s is down" % m.get("name", "A monitor"))
        for p in (system or {}).get("containers", {}).get("problems", []) or []:
            add("bad", "container", "Container problem: %s" % (p if isinstance(p, str) else p.get("name", "?")))
        for a in (extra or {}).get("ai", []) or []:
            if not a.get("up"):
                add("warn", "ai", "%s is not running" % a.get("role", a.get("name", "A model service")))
        b = (extra or {}).get("backup") or {}
        if b and not b.get("ok", True):
            add("bad", "backup", "The last backup did not succeed", str(b.get("last_result", ""))[:160])
        elif b.get("last_ts") and now - float(b["last_ts"]) > 36 * 3600:
            add("warn", "backup", "No backup in over a day")
        sec = (extra or {}).get("security") or {}
        if sec.get("reboot_required"):
            add("warn", "security", "A restart is needed to finish updates")
        if (sec.get("pending_updates") or 0) > 0:
            add("info", "security", "%d update%s waiting" % (sec["pending_updates"], "" if sec["pending_updates"] == 1 else "s"))
        if sec.get("cert_days_left") is not None and sec["cert_days_left"] < 21:
            add("warn", "security", "A certificate expires in %d days" % sec["cert_days_left"])
        if (sec.get("failed_ssh_24h") or 0) > 0:
            add("warn", "security", "%d failed sign-in attempt(s) in the last day" % sec["failed_ssh_24h"])
    items.sort(key=lambda x: LEVEL_ORDER[x["level"]])
    return items


def health_summary(monitors, attention, infra: bool) -> dict:
    if not infra:
        return {}
    total = len(monitors)
    up = sum(1 for m in monitors if m.get("state") == "up")
    bad = sum(1 for a in attention if a["level"] == "bad")
    warn = sum(1 for a in attention if a["level"] == "warn")
    score = 100 if not total else round(100 * up / total)
    score = max(0, score - 5 * bad - 2 * warn)
    label = "Everything is running." if not bad and up == total else "Something needs attention."
    return {"score": score, "label": label, "monitors_up": up, "monitors_total": total}


# ---- the whole thing -----------------------------------------------------------------------------------------------------

class Overview:
    def __init__(self, hermes: Hermes, hermes_data: str = "", deck_dir: str = "", systems: dict | None = None):
        self.hermes = hermes
        base = Path(hermes_data) if hermes_data else None
        self.vault = base / "obsidian-vault" if base else None
        self.deck = Path(deck_dir) if deck_dir else None
        self.systems = systems or {}

    def deck_file(self, name: str):
        return _json(self.deck / name) if self.deck else None

    async def _agent(self) -> dict:
        calls = {"status": self.hermes.dash_json("/api/status"), "stats": self.hermes.dash_json("/api/sessions/stats"),
                 "usage": self.hermes.dash_json("/api/analytics/usage"), "recent": self.hermes.dash_json("/api/sessions?limit=6"),
                 "board": self.hermes.dash_json("/api/plugins/kanban/board")}
        got = dict(zip(calls, await asyncio.gather(*calls.values())))
        ok = lambda k: got[k][1] if got[k][0] == 200 and got[k][1] is not None else {}
        st, stats, usage, recent, board = ok("status"), ok("stats"), ok("usage"), ok("recent"), ok("board")
        tot = usage.get("totals", {}) if isinstance(usage, dict) else {}
        days = [{"day": d.get("day"), "calls": d.get("api_calls", 0)} for d in (usage.get("daily", []) if isinstance(usage, dict) else [])][-7:]
        tools = [{"tool": t.get("tool"), "count": t.get("count", 0)} for t in (usage.get("tools", []) if isinstance(usage, dict) else [])][:7]
        sessions = [{"id": s.get("id"), "title": (s.get("title") or s.get("preview") or "Untitled")[:90], "source": s.get("source"),
                     "model": s.get("model"), "messages": s.get("message_count", 0), "last_active": s.get("last_active")}
                    for s in (recent.get("sessions", []) if isinstance(recent, dict) else [])]
        plats = st.get("gateway_platforms", {}) if isinstance(st, dict) else {}
        # "working" means something is really running. The gateway's own busy flag and agent count can stay stuck on after the work has
        # finished (and cron jobs leave sessions open), so they are kept only for reference. Real work: a run on the agent API, a card in the
        # board's running column, or (added in home()) a scheduled job that is running.
        api = ((plats.get("api_server") or {}).get("metrics") or {}) if isinstance(plats, dict) else {}
        running_cards = sum(len(c.get("tasks", [])) for c in (board.get("columns", []) if isinstance(board, dict) else []) if isinstance(c, dict) and str(c.get("name")) == "running")
        real = int(api.get("active_runs") or 0) + running_cards
        return {
            "reachable": got["status"][0] == 200,
            "running": st.get("gateway_running") if st else None, "state": st.get("gateway_state"), "busy": real > 0,
            "active_agents": real, "gateway_busy": bool(st.get("gateway_busy")), "gateway_agents": st.get("active_agents", 0), "version": st.get("version"),
            "channels": [{"name": k, "state": (v.get("state") if isinstance(v, dict) else str(v))} for k, v in plats.items()],
            "stats": {"sessions": stats.get("total"), "active": stats.get("active_store"), "archived": stats.get("archived"), "messages": stats.get("messages"),
                      "calls": tot.get("total_api_calls"), "tokens_in": tot.get("total_input"), "tokens_out": tot.get("total_output")},
            "days": days, "tools": tools, "recent": sessions, "board": board_summary(board),
        }

    async def home(self, infra: bool, today: datetime.date | None = None, now: float | None = None, personal: bool = True) -> dict:
        """infra: may see the server's health. personal: may see the install's own notes, jobs, board and chats (owners)."""
        today = today or datetime.date.today()
        now = now or time.time()
        agent = await self._agent()
        if not personal:
            # everyone else gets only whether the agent is up: the notes, jobs, board and everybody's chats belong to the owners
            keep = ("reachable", "running", "state", "busy", "active_agents", "version")
            return {"generated": now, "limited": True, "agent": {k: agent.get(k) for k in keep}, "attention": [], "jobs": [],
                    "today": {"date": today.isoformat(), "focus": [], "radar": {"date": None, "items": []}, "daylog": {"date": None, "items": []}},
                    "weather": None, "river": None}
        jstatus, jdata = await self.hermes.dash_json("/api/cron/jobs")
        jobs = read_jobs(jdata) if jstatus == 200 else []
        running_jobs = sum(1 for j in jobs if str(j.get("state")) == "running")
        if running_jobs:
            agent["active_agents"] += running_jobs
            agent["busy"] = True
        daily = await asyncio.to_thread(_read, (self.vault / "Daily" / (today.isoformat() + ".md")) if self.vault else Path("/nonexistent"))
        radar = await asyncio.to_thread(latest_radar, self.vault)
        daylog = await asyncio.to_thread(latest_daylog, self.vault, today)
        monitors = (self.deck_file("status.json") or {}).get("monitors", []) if infra else []
        system, extra = (self.deck_file("system.json"), self.deck_file("extra.json")) if infra else (None, None)
        attention = build_attention(jobs, agent["board"], monitors, system, extra, agent, now, infra)
        out = {
            "generated": now, "agent": agent, "jobs": jobs, "attention": attention,
            "today": {"date": today.isoformat(), "focus": parse_focus(daily), "radar": radar, "daylog": daylog},
            "weather": self.deck_file("weather.json"), "river": self.deck_file("river.json"),
        }
        if infra:
            out["health"] = health_summary(monitors, attention, True)
            out["systems_summary"] = self._summary(system, extra, monitors)
        return out

    @staticmethod
    def _summary(system, extra, monitors) -> dict:
        system, extra = system or {}, extra or {}
        gpus = system.get("gpus", [])
        used = sum(g.get("mem_used", 0) for g in gpus)
        total = sum(g.get("mem_total", 0) for g in gpus)
        disks = system.get("disks", [])
        data_disk = max(disks, key=lambda d: d.get("total", 0)) if disks else None
        mem = system.get("mem", {})
        return {
            "containers": (system.get("containers") or {}).get("running"),
            "monitors": "%d/%d" % (sum(1 for m in monitors if m.get("state") == "up"), len(monitors)) if monitors else None,
            "gpu_vram_pct": round(100 * used / total) if total else None, "gpu_count": len(gpus),
            "disk_pct": round(100 * data_disk["used"] / data_disk["total"]) if data_disk and data_disk.get("total") else None,
            "mem_pct": round(100 * (1 - mem["available"] / mem["total"])) if mem.get("total") else None,
            "cpu_pct": (system.get("cpu") or {}).get("pct"),
            "backup_ok": (extra.get("backup") or {}).get("ok"),
        }

    async def systems_page(self, now: float | None = None) -> dict:
        now = now or time.time()
        status = self.deck_file("status.json") or {}
        monitors = {m.get("name"): m.get("state") for m in status.get("monitors", [])}

        def state_of(monitor):
            if not monitor:
                return "unmonitored"
            return monitors.get(monitor, "unknown")

        cfg = self.systems
        services = [{"name": s.get("name"), "url": s.get("url"), "state": state_of(s.get("monitor"))} for s in cfg.get("services", [])]
        websites = [{"name": s.get("name"), "url": s.get("url") or ("https://" + str(s.get("name"))), "host": s.get("host"),
                     "state": state_of(s.get("monitor"))} for s in cfg.get("websites", [])]
        files = {n: self.deck_file(n + ".json") for n in ("system", "extra", "weather", "river", "status")}
        ages = {n: (round(now - f["updated"]) if isinstance(f, dict) and f.get("updated") else None) for n, f in files.items()}
        return {"generated": now, "services": services, "websites": websites,
                "local_models": cfg.get("local_models", []), "external": cfg.get("external", []),
                "system": files["system"], "extra": files["extra"], "monitors": status.get("monitors", []),
                "data_age_s": ages, "stale": any(a is not None and a > STALE_S for a in ages.values())}
