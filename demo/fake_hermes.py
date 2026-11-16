#!/usr/bin/env python3
"""A tiny stand-in for Hermes Agent, so the demo needs no agent, GPU or keys.

It answers the same HTTP routes the app calls (the agent API and the dashboard API) with made-up sample data, and
"types" canned replies into chat. Standard library only. Nothing here is real: every name, number and note is invented.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

NOW = time.time()
DAY = 86400


def ts(days_ago: float = 0, hours_ago: float = 0) -> float:
    return NOW - days_ago * DAY - hours_ago * 3600


def iso(days_ago: float = 0, hours_ago: float = 0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts(days_ago, hours_ago)))


SOUL = "You are Robo, a calm and practical assistant. You keep notes in the vault, run the scheduled jobs, and ask before you touch anything outside the workspace.\n"

JOBS = [
    {"id": "a1b2c3d4e5f6", "name": "Nightly Radar", "schedule": {"kind": "cron", "expr": "30 5 * * *"}, "enabled": True, "state": "scheduled",
     "prompt": "Write today's Radar note.", "last_status": "ok", "last_run_at": iso(hours_ago=9), "next_run_at": iso(hours_ago=-15),
     "answer": "Radar note written: three new tools to look at, one worth a closer read."},
    {"id": "b2c3d4e5f6a1", "name": "Morning brief", "schedule": {"kind": "cron", "expr": "0 7 * * *"}, "enabled": True, "state": "scheduled",
     "prompt": "Summarize the day ahead.", "last_status": "ok", "last_run_at": iso(hours_ago=7), "next_run_at": iso(hours_ago=-17),
     "answer": "Two meetings today, one invoice due Friday, and the weekly review is tomorrow."},
    {"id": "c3d4e5f6a1b2", "name": "Weekly review", "schedule": {"kind": "cron", "expr": "0 18 * * 0"}, "enabled": True, "state": "scheduled",
     "prompt": "Review the week.", "last_status": "ok", "last_run_at": iso(days_ago=2), "next_run_at": iso(days_ago=-5),
     "answer": "Finished 9 of 12 planned items. Carried three over to next week."},
    {"id": "d4e5f6a1b2c3", "name": "Clear old chat documents", "schedule": {"kind": "cron", "expr": "10 4 * * *"}, "enabled": False, "state": "paused",
     "paused_at": iso(days_ago=1), "prompt": "Tidy old uploads.", "last_status": "ok", "last_run_at": iso(days_ago=1), "next_run_at": None, "answer": ""},
]

COLUMNS = ["triage", "todo", "ready", "running", "blocked", "review", "done"]
TASKS = {
    "t_demo0001": {"id": "t_demo0001", "title": "Draft the quarterly newsletter", "status": "running", "assignee": "default", "priority": 2,
                   "body": "**Goal**: a short newsletter for customers.\n\n- [x] Outline\n- [ ] First draft\n- [ ] Review\n\nUse `notes/newsletter.md` as the source.",
                   "current_run_id": 1, "created_at": ts(1), "started_at": ts(hours_ago=3), "comment_count": 1, "workspace_kind": "dir"},
    "t_demo0002": {"id": "t_demo0002", "title": "Compare three note-taking apps", "status": "ready", "assignee": "default", "priority": 1,
                   "body": "Look at licence, export formats and sync. Short table at the end.", "created_at": ts(2), "workspace_kind": "scratch"},
    "t_demo0003": {"id": "t_demo0003", "title": "Idea: a recipe scaler", "status": "triage", "assignee": None, "priority": 0,
                   "body": "Scale a recipe up or down and fix the units.", "created_at": ts(0.5), "workspace_kind": "scratch"},
    "t_demo0004": {"id": "t_demo0004", "title": "Set up weekly backups report", "status": "done", "assignee": "default", "priority": 1,
                   "body": "Done last week.", "created_at": ts(6), "completed_at": ts(4), "latest_summary": "Report runs every Sunday and lands in the vault.", "workspace_kind": "dir"},
}
COMMENTS = {"t_demo0001": [{"id": 1, "author": "demo", "body": "Keep it under 400 words.", "created_at": ts(hours_ago=5)}]}

SESSIONS: dict[str, dict] = {}
MESSAGES: dict[str, list] = {}
RUNS: dict[str, dict] = {}
LOCK = threading.Lock()

REPLIES = [
    ("Here is what I found in your notes: the newsletter outline is done and the first draft is next. ",
     "I can start the draft now. Want it short and friendly, or more formal?"),
]


def seed_sessions() -> None:
    rows = [("atrium-chat-demo0001", "Plan the week", 2), ("atrium-chat-demo0002", "Newsletter ideas", 20)]
    for sid, title, hours in rows:
        SESSIONS[sid] = session_row(sid, title, hours)
    MESSAGES["atrium-chat-demo0001"] = [
        {"id": 1, "session_id": "atrium-chat-demo0001", "role": "user", "content": "What is on my plate this week?", "timestamp": ts(hours_ago=2)},
        {"id": 2, "session_id": "atrium-chat-demo0001", "role": "assistant", "timestamp": ts(hours_ago=2),
         "content": "Three things matter this week:\n\n1. **Newsletter draft** (in progress)\n2. **Invoice review** (due Friday)\n3. **Weekly review** on Sunday\n\nI can start on any of them."},
    ]
    MESSAGES["atrium-chat-demo0002"] = [
        {"id": 3, "session_id": "atrium-chat-demo0002", "role": "user", "content": "Give me five newsletter topics.", "timestamp": ts(hours_ago=20)},
        {"id": 4, "session_id": "atrium-chat-demo0002", "role": "assistant", "timestamp": ts(hours_ago=20),
         "content": "1. A behind-the-scenes look\n2. Customer story\n3. Seasonal tips\n4. A small how-to\n5. What is coming next"},
    ]


def session_row(sid: str, title: str, hours_ago: float, source: str = "api_server") -> dict:
    return {"id": sid, "source": source, "title": title, "model": "demo-model", "started_at": ts(hours_ago=hours_ago + 0.2),
            "last_activity_at": ts(hours_ago=hours_ago), "ended_at": None, "message_count": 2, "tool_call_count": 0,
            "input_tokens": 1200, "output_tokens": 340, "cache_read_tokens": 0, "reasoning_tokens": 0, "end_reason": None,
            "runtime_revision": 1, "runtime_generation": 1, "estimated_cost_usd": None, "actual_cost_usd": None}


def usage() -> dict:
    daily = [{"day": time.strftime("%Y-%m-%d", time.gmtime(ts(days_ago=i))), "input_tokens": 90000 + i * 7000 % 40000,
              "output_tokens": 24000 + i * 3100 % 9000, "cache_read_tokens": 300000, "reasoning_tokens": 0, "estimated_cost": 0.0,
              "actual_cost": 0, "sessions": 6 + i % 4, "api_calls": 60 + i * 3 % 25} for i in range(13, -1, -1)]
    return {"daily": daily, "by_model": [{"model": "demo-model", "input_tokens": 1200000, "output_tokens": 340000, "estimated_cost": 0, "sessions": 84, "api_calls": 640, "aux_tasks": []}],
            "by_task": [{"task": "chat", "input_tokens": 700000, "output_tokens": 200000, "estimated_cost": 0, "api_calls": 300, "models": ["demo-model"]}],
            "totals": {"total_input": 1200000, "total_output": 340000, "total_cache_read": 4200000, "total_reasoning": 0, "total_estimated_cost": 0, "total_actual_cost": 0, "total_sessions": 84, "total_api_calls": 640},
            "period_days": 30, "skills": {"summary": {"total_skill_loads": 21, "total_skill_edits": 0, "total_skill_actions": 21, "distinct_skills_used": 6},
                                           "top_skills": [{"skill": "notes", "view_count": 9, "manage_count": 0, "total_count": 9, "percentage": 42.8, "last_used_at": ts(hours_ago=3)}]},
            "tools": [{"tool": "read_file", "count": 410, "percentage": 40.0}, {"tool": "write_file", "count": 220, "percentage": 21.0}, {"tool": "web_search", "count": 150, "percentage": 14.0}]}


def status() -> dict:
    return {"version": "demo", "release_date": "2026-01-01", "gateway_running": True, "gateway_state": "running", "active_agents": 1, "gateway_busy": False,
            "active_sessions": 1, "gateway_platforms": {"api_server": {"state": "connected", "needs_attention": False, "metrics": {"active_runs": 0}}},
            "components": {"gateway": {"status": "ok", "state": "running"}, "dashboard": {"status": "ok"}, "storage": {"status": "ok"}, "platforms": {"status": "ok", "configured": 1, "connected": 1}},
            "overall": "ok", "memory": {"pressure": "low", "gateway_rss_mb": 410, "system_total_mb": 32000, "system_available_mb": 20500},
            "disk": {"pressure": "low", "total_mb": 900000, "free_mb": 700000, "used_percent": 22.0}, "profiles": ["default"]}


def sysstats() -> dict:
    return {"os": "Linux", "hostname": "demo-host", "cpu_count": 8, "cpu_percent": 12.5, "load_avg": [0.6, 0.5, 0.4], "uptime_seconds": 345600,
            "memory": {"total": 32 * 2 ** 30, "available": 21 * 2 ** 30, "used": 11 * 2 ** 30, "percent": 34.4},
            "disk": {"total": 900 * 2 ** 30, "used": 200 * 2 ** 30, "free": 700 * 2 ** 30, "percent": 22.2}, "hermes_version": "demo"}


def board() -> dict:
    cols = [{"name": c, "tasks": [t for t in TASKS.values() if t["status"] == c]} for c in COLUMNS]
    return {"columns": cols, "tenants": [], "assignees": ["default"], "latest_event_id": 1, "now": time.time()}


WORKFLOW = {"columns": [{"key": "triage", "label": "Ideas", "icon": "💡", "drag_target": True}, {"key": "todo", "label": "To do", "icon": "📝", "drag_target": True},
                        {"key": "ready", "label": "Ready", "icon": "✅", "drag_target": True}, {"key": "running", "label": "Running", "icon": "⚙️", "drag_target": False},
                        {"key": "blocked", "label": "Blocked", "icon": "⛔", "drag_target": True}, {"key": "review", "label": "Review", "icon": "👀", "drag_target": True},
                        {"key": "done", "label": "Done", "icon": "🏁", "drag_target": True}],
            "manual": {}, "archived": "archived"}


def run_reply(run_id: str, text: str) -> list[dict]:
    first, second = REPLIES[0]
    return [{"event": "tool.started", "tool_call_id": run_id + "-t1", "tool": "read_file", "preview": "notes/newsletter.md"},
            {"event": "tool.completed", "tool_call_id": run_id + "-t1"},
            {"event": "message.delta", "delta": first}, {"event": "message.delta", "delta": second},
            {"event": "run.completed", "output": first + second}]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if p == "/health":
            return self.send_json({"status": "ok"})
        if p == "/v1/models":
            return self.send_json({"data": [{"id": "demo-model"}]})
        if p == "/api/status":
            return self.send_json(status())
        if p == "/api/system/stats":
            return self.send_json(sysstats())
        if p == "/api/analytics/usage":
            return self.send_json(usage())
        if p == "/api/sessions/stats":
            return self.send_json({"total": len(SESSIONS), "active_store": len(SESSIONS), "archived": 0, "messages": 42, "by_source": {"api_server": len(SESSIONS), "cron": 12}})
        if p == "/api/sessions":
            rows = sorted(SESSIONS.values(), key=lambda s: s["last_activity_at"], reverse=True)
            return self.send_json({"sessions": rows, "total": len(rows), "limit": int(q.get("limit", [50])[0]), "offset": 0, "storage": {}})
        m = re.fullmatch(r"/api/sessions/([A-Za-z0-9_-]+)/messages", p)
        if m:
            return self.send_json({"session_id": m.group(1), "profile": "default", "messages": MESSAGES.get(m.group(1), [])})
        m = re.fullmatch(r"/api/sessions/([A-Za-z0-9_-]+)", p)
        if m:
            return self.send_json(SESSIONS[m.group(1)]) if m.group(1) in SESSIONS else self.send_json({"detail": "no"}, 404)
        if p == "/api/cron/jobs":
            return self.send_json(JOBS)
        m = re.fullmatch(r"/api/cron/jobs/([a-f0-9]{12})/runs", p)
        if m:
            job = next((j for j in JOBS if j["id"] == m.group(1)), None)
            if not job:
                return self.send_json({"runs": []})
            rid = "local-" + job["id"]
            MESSAGES.setdefault(rid, [{"id": 900, "session_id": rid, "role": "assistant", "content": job["answer"], "timestamp": ts(hours_ago=8)}])
            runs = [{"id": rid, "source": "cron", "started_at": ts(hours_ago=9), "ended_at": ts(hours_ago=8.9), "end_reason": "cron_complete", "message_count": 2, "tool_call_count": 3}]
            return self.send_json({"runs": runs})
        if p == "/api/profiles/default/soul":
            return self.send_json({"content": SOUL, "exists": True})
        if p == "/api/plugins/kanban/board":
            return self.send_json(board())
        if p == "/api/plugins/kanban/workflow":
            return self.send_json(WORKFLOW)
        if p == "/api/plugins/kanban/orchestration":
            return self.send_json({"default_assignee": "default", "resolved_default_assignee": "default", "auto_decompose": False, "auto_promote_children": True})
        if p == "/api/plugins/kanban/assignees":
            return self.send_json({"assignees": [{"name": "default", "on_disk": True, "counts": {"running": 1}}]})
        m = re.fullmatch(r"/api/plugins/kanban/tasks/(t_[a-z0-9]+)", p)
        if m and m.group(1) in TASKS:
            t = TASKS[m.group(1)]
            return self.send_json({"task": t, "comments": COMMENTS.get(t["id"], []), "events": [], "runs": [{"id": 1}] if t.get("current_run_id") else [], "links": {"parents": [], "children": []}})
        if re.fullmatch(r"/v1/runs/[A-Za-z0-9_-]+", p):
            r = RUNS.get(p.rsplit("/", 1)[1])
            return self.send_json(r or {"status": "unknown"}, 200 if r else 404)
        m = re.fullmatch(r"/v1/runs/([A-Za-z0-9_-]+)/events", p)
        if m:
            events = RUNS.get(m.group(1), {}).get("events", [])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for ev in events:
                    time.sleep(0.45)
                    self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode())
                    self.wfile.flush()
                self.wfile.write(b'data: {"event": "stream.end"}\n\n')
                self.wfile.flush()
            except OSError:
                pass
            self.close_connection = True
            return
        self.send_json({"detail": "not found"}, 404)

    def do_POST(self):
        p, data = urlparse(self.path).path, self.body()
        if p == "/v1/runs":
            rid = "run_" + uuid.uuid4().hex[:12]
            sid = str(data.get("session_id") or "")
            text = data.get("input") if isinstance(data.get("input"), str) else "(a picture)"
            events = run_reply(rid, text)
            RUNS[rid] = {"run_id": rid, "status": "completed", "events": events, "output": events[-1]["output"]}
            with LOCK:
                if sid and sid not in SESSIONS:
                    SESSIONS[sid] = session_row(sid, (text or "New chat")[:40], 0)
                if sid:
                    MESSAGES.setdefault(sid, []).append({"id": int(time.time() * 1000), "session_id": sid, "role": "user", "content": text, "timestamp": time.time()})
                    MESSAGES[sid].append({"id": int(time.time() * 1000) + 1, "session_id": sid, "role": "assistant", "content": events[-1]["output"], "timestamp": time.time()})
                    SESSIONS[sid]["last_activity_at"] = time.time()
            return self.send_json({"run_id": rid, "status": "started"})
        if re.fullmatch(r"/v1/runs/[A-Za-z0-9_-]+/(stop|approval)", p):
            return self.send_json({"ok": True})
        m = re.fullmatch(r"/api/cron/jobs/([a-f0-9]{12})/(trigger|pause|resume)", p)
        if m:
            for j in JOBS:
                if j["id"] == m.group(1):
                    if m.group(2) == "pause":
                        j.update(enabled=False, state="paused", paused_at=iso())
                    elif m.group(2) == "resume":
                        j.update(enabled=True, state="scheduled", paused_at=None)
            return self.send_json({"ok": True})
        if p == "/api/plugins/kanban/tasks":
            tid = "t_demo" + uuid.uuid4().hex[:4]
            TASKS[tid] = {"id": tid, "title": str(data.get("title") or "Untitled"), "body": data.get("body"), "status": "triage", "assignee": None,
                          "priority": int(data.get("priority") or 0), "created_at": time.time(), "workspace_kind": data.get("workspace_kind") or "scratch"}
            return self.send_json({"task": TASKS[tid]})
        m = re.fullmatch(r"/api/plugins/kanban/tasks/(t_[a-z0-9]+)/(specify|reclaim|comments)", p)
        if m and m.group(1) in TASKS:
            if m.group(2) == "comments":
                COMMENTS.setdefault(m.group(1), []).append({"id": time.time(), "author": data.get("author", "demo"), "body": data.get("body", ""), "created_at": time.time()})
            return self.send_json({"ok": True})
        self.send_json({"ok": True})

    def do_PATCH(self):
        p, data = urlparse(self.path).path, self.body()
        m = re.fullmatch(r"/api/plugins/kanban/tasks/(t_[a-z0-9]+)", p)
        if m and m.group(1) in TASKS:
            TASKS[m.group(1)].update({k: v for k, v in data.items() if k in ("title", "body", "priority", "assignee", "status")})
            return self.send_json({"task": TASKS[m.group(1)]})
        m = re.fullmatch(r"/api/sessions/([A-Za-z0-9_-]+)", p)
        if m and m.group(1) in SESSIONS:
            if data.get("title"):
                SESSIONS[m.group(1)]["title"] = str(data["title"])[:80]
            return self.send_json(SESSIONS[m.group(1)])
        self.send_json({"ok": True})

    def do_PUT(self):
        p, data = urlparse(self.path).path, self.body()
        m = re.fullmatch(r"/api/cron/jobs/([a-f0-9]{12})", p)
        if m:
            for j in JOBS:
                if j["id"] == m.group(1) and isinstance((data.get("updates") or {}).get("schedule"), str):
                    j["schedule"] = {"kind": "cron", "expr": data["updates"]["schedule"]}
            return self.send_json({"ok": True})
        global SOUL
        if p == "/api/profiles/default/soul" and isinstance(data.get("content"), str):
            SOUL = data["content"]
        self.send_json({"ok": True})

    def do_DELETE(self):
        m = re.fullmatch(r"/api/sessions/([A-Za-z0-9_-]+)", urlparse(self.path).path)
        if m:
            SESSIONS.pop(m.group(1), None)
        self.send_json({"ok": True})


def serve(port: int = 8791) -> ThreadingHTTPServer:
    seed_sessions()
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    serve()
    print("stand-in Hermes on http://127.0.0.1:8791 (Ctrl+C to stop)")
    while True:
        time.sleep(3600)
