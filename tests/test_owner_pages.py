import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import PW, PW2, login
from hermes import Hermes

TOKEN = "sched-token"
H = {"origin": "https://app.test"}
CALLS = []
JOBS = []


def job(i, name, expr, **kw):
    d = {"id": "%012x" % i, "name": name, "schedule": {"kind": "cron", "expr": expr, "display": expr}, "schedule_display": expr, "enabled": True,
         "paused_at": None, "next_run_at": "2026-11-16T05:30:00-06:00", "last_run_at": "2026-11-15T05:30:00-06:00", "last_status": "ok",
         "last_error": None, "live_execution": None, "monitor_script": None, "monitor_url": None}
    d.update(kw)
    return d


class Sched(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj=None):
        body = json.dumps(obj if obj is not None else {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def ok(self):
        return self.headers.get("X-Hermes-Session-Token") == TOKEN

    def do_GET(self):
        p = self.path.split("?")[0]
        if not self.ok():
            return self.reply(401)
        if p == "/api/cron/jobs":
            return self.reply(200, JOBS)
        if p.endswith("/runs"):
            return self.reply(200, {"limit": 8, "runs": [
                {"started_at": 1794655800.5, "ended_at": 1794656174.9, "end_reason": "cron_complete", "is_active": False},
                {"started_at": 1794712500.0, "ended_at": 1794734100.0, "end_reason": "startup_orphan_reap", "is_active": False},
                {"started_at": 1794740000.0, "ended_at": None, "end_reason": None, "is_active": True},
                {"started_at": 1794600000.0, "ended_at": 1794600100.0, "end_reason": "something_broke", "is_active": False},
                {"started_at": 1794500000.0, "ended_at": None, "end_reason": None, "is_active": False}]})
        self.reply(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        CALLS.append(("POST", self.path))
        self.reply(200, {"ok": True}) if self.ok() else self.reply(401)

    def do_PUT(self):
        n = int(self.headers.get("Content-Length") or 0)
        CALLS.append(("PUT", self.path, json.loads(self.rfile.read(n) or b"{}")))
        self.reply(200, {"ok": True}) if self.ok() else self.reply(401)


@pytest.fixture()
def sched():
    CALLS.clear()
    JOBS[:] = [job(0xA1, "Morning brief", "30 5 * * *"), job(0xA2, "Weekly review", "0 18 * * 0"), job(0xA3, "Mail triage", "*/15 7-21 * * *"),
               job(0xA4, "Odd one", "0 9 1 * *"), job(0xA5, "Busy job", "0 3 * * *", live_execution={"id": "x"}), job(0xA6, "Off job", "0 4 * * *", enabled=False)]
    srv = HTTPServer(("127.0.0.1", 0), Sched)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


@pytest.fixture()
def owner(make, cfg, sched, tmp_path):
    home = tmp_path / "home"
    (home / "obsidian-vault" / "Radar").mkdir(parents=True)
    (home / "obsidian-vault" / "Notes").mkdir()
    (home / "atrium").mkdir()
    (home / "cron" / "output").mkdir(parents=True)
    (home / "obsidian-vault" / "Notes" / "Alpha.md").write_text("# Alpha note\nSee [[Beta]] and [[Missing]].\n")
    (home / "obsidian-vault" / "Notes" / "Beta.md").write_text("# Beta note\nBack to [[Alpha]].\n")
    (home / "obsidian-vault" / "Radar" / "Interests.md").write_text("# Radar interests\n\n- local models\n- etsy changes\n")
    (home / "atrium" / "actions.log").write_text(json.dumps({"t": "2026-11-15T10:00:00", "action": "calendar.add", "summary": "Added a meeting"}) + "\n")
    out = home / "cron" / "output" / ("%012x" % 0xA1)
    out.mkdir()
    (out / "2026-11-15.md").write_text("# Job\n## Response\nSent the **morning** brief.\n")
    cfg.hermes_dashboard_base, cfg.hermes_dashboard_token, cfg.hermes_data_dir = sched, TOKEN, str(home)
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), hermes=Hermes("", "", sched, TOKEN, str(home)), config=cfg)
    c.home = home

    def as_(user, pw, ip):
        c.post("/logout", headers=H)
        assert login(c, user, pw, ip=ip).status_code == 303
    c.as_ = as_
    as_("alice", PW, "10.7.0.1")
    return c


def test_only_owners_may_use_these_pages(owner):
    owner.as_("bob", PW2, "10.7.0.2")
    for path in ("/memory/graph", "/workflows/jobs", "/settings/schedule", "/workflows/interests"):
        assert owner.get("/api/plugins/atrium-" + path.lstrip("/").replace("/", "/", 1)).status_code == 403, path
    assert owner.post("/api/plugins/atrium-workflows/jobs/%012x/run" % 0xA1, headers=H).status_code == 403
    owner.post("/logout", headers=H)
    assert owner.get("/api/plugins/atrium-workflows/jobs").status_code == 401
    owner.as_("bob", PW2, "10.7.0.3")
    cfg_js = owner.get("/shell-config.js").text
    for label in ("Workflows", "Settings"):
        assert '"label": "%s"' % label not in cfg_js
    for path in ("/workflows", "/settings"):
        assert owner.get(path).status_code == 403
    owner.as_("alice", PW, "10.7.0.4")
    assert owner.get("/workflows").status_code == 200 and owner.get("/agent-workflows").headers["location"] == "/workflows"
    assert owner.get("/memory").headers["location"] == "/settings" and owner.get("/agent-memory").headers["location"] == "/settings"       # Memory is a Settings section
    assert "/static/pages/memory/index.js" in owner.get("/shell-config.js").text


def test_memory_reads_the_vault(owner):
    g = owner.get("/api/plugins/atrium-memory/graph").json()
    ids = {n["id"] for n in g["nodes"]}
    assert {"Notes/Alpha.md", "Notes/Beta.md"} <= ids
    assert sorted(g["edges"]) == [["Notes/Alpha.md", "Notes/Beta.md"], ["Notes/Beta.md", "Notes/Alpha.md"]]      # the link to a missing note makes no edge
    n = owner.get("/api/plugins/atrium-memory/note", params={"path": "Notes/Alpha.md"}).json()
    assert n["title"] == "Alpha note" and "Beta" in n["body"]
    assert owner.get("/api/plugins/atrium-memory/note", params={"path": "../../etc/passwd"}).status_code == 404
    assert [r["id"] for r in owner.get("/api/plugins/atrium-memory/search", params={"q": "Back to"}).json()["results"]] == ["Notes/Beta.md"]


def test_workflows_jobs_come_from_the_scheduler(owner):
    jobs = {j["name"]: j for j in owner.get("/api/plugins/atrium-workflows/jobs").json()["jobs"]}
    assert jobs["Morning brief"]["when"] == "Every day at 5:30 AM" and jobs["Morning brief"]["enabled"] is True
    assert jobs["Morning brief"]["answer"].startswith("Sent the morning brief")           # last answer from the output folder
    assert jobs["Weekly review"]["when"] == "Sundays at 6:00 PM"
    assert jobs["Odd one"]["when"] == "0 9 1 * *"                                          # shapes it cannot phrase are shown as written
    assert jobs["Busy job"]["running"] is True and jobs["Morning brief"]["running"] is False
    assert jobs["Off job"]["enabled"] is False


def test_workflow_run_history_is_translated(owner):
    runs = owner.get("/api/plugins/atrium-workflows/jobs/%012x/runs" % 0xA1).json()["runs"]
    assert [r["status"] for r in runs] == ["ok", "interrupted", "running", "failed", "interrupted"]
    assert runs[4]["error"] == "Cut off before it finished."
    assert "restarted" in runs[1]["error"] and runs[3]["error"] == "something_broke"
    assert runs[0]["started"].startswith("20") and runs[2]["finished"] is None
    assert owner.get("/api/plugins/atrium-workflows/jobs/zzzzzzzzzzzz/runs").status_code == 400
    assert owner.get("/api/plugins/atrium-workflows/jobs/%012x/runs" % 0xFF).status_code == 404


def test_run_now_pause_and_resume_use_the_scheduler(owner):
    jid = "%012x" % 0xA1
    assert owner.post("/api/plugins/atrium-workflows/jobs/%s/run" % jid, headers=H).json() == {"ok": True}
    assert owner.post("/api/plugins/atrium-workflows/jobs/%s/pause" % jid, headers=H).status_code == 200
    assert owner.post("/api/plugins/atrium-workflows/jobs/%s/resume" % jid, headers=H).status_code == 200
    assert [c[1] for c in CALLS] == ["/api/cron/jobs/%s/%s" % (jid, a) for a in ("trigger", "pause", "resume")]
    assert owner.post("/api/plugins/atrium-workflows/jobs/%012x/run" % 0xA5, headers=H).status_code == 409      # already running
    assert owner.post("/api/plugins/atrium-workflows/jobs/%s/run" % jid, headers={"origin": "https://evil.example"}).status_code == 403
    assert len(CALLS) == 3


def test_radar_topics_can_be_read_and_replaced(owner):
    assert owner.get("/api/plugins/atrium-workflows/interests").json()["topics"] == ["local models", "etsy changes"]
    r = owner.post("/api/plugins/atrium-workflows/interests", json={"topics": ["new topic", "New Topic", "  spaced   out  ", ""]}, headers=H)
    assert r.json()["topics"] == ["new topic", "spaced out"]                                # tidied and de-duplicated
    text = (owner.home / "obsidian-vault" / "Radar" / "Interests.md").read_text()
    assert text.startswith("# Radar interests") and "- new topic" in text and "- spaced out" in text
    assert owner.post("/api/plugins/atrium-workflows/interests", json={"topics": [" "]}, headers=H).status_code == 400


def test_activity_feed(owner):
    items = owner.get("/api/plugins/atrium-workflows/activity").json()["items"]
    assert items and items[0]["action"] == "calendar.add" and items[0]["summary"] == "Added a meeting"


def test_settings_lists_schedules_and_edits_through_the_scheduler(owner):
    s = owner.get("/api/plugins/atrium-settings/schedule").json()
    byname = {j["name"]: j for j in s["jobs"]}
    assert byname["Morning brief"]["editable"] is True and byname["Morning brief"]["values"] == {"kind": "daily", "hour": 5, "minute": 30}
    assert byname["Odd one"]["editable"] is False
    jid = "%012x" % 0xA1
    r = owner.post("/api/plugins/atrium-settings/schedule/" + jid, json={"hour": 6, "minute": 15}, headers=H)
    assert r.json()["when"] == "Every day at 6:15 AM" and r.json()["expr"] == "15 6 * * *"
    assert CALLS[-1] == ("PUT", "/api/cron/jobs/" + jid, {"updates": {"schedule": "15 6 * * *"}})
    assert owner.post("/api/plugins/atrium-settings/schedule/" + jid, json={"hour": 30, "minute": 0}, headers=H).status_code == 400
    assert owner.post("/api/plugins/atrium-settings/schedule/%012x" % 0xA4, json={"hour": 1, "minute": 0}, headers=H).status_code == 409
    weekly = owner.post("/api/plugins/atrium-settings/schedule/%012x" % 0xA2, json={"hour": 19, "minute": 0, "day": 1}, headers=H).json()
    assert weekly["expr"] == "0 19 * * 1"
    window = owner.post("/api/plugins/atrium-settings/schedule/%012x" % 0xA3, json={"interval": 30, "start": 8, "end": 20}, headers=H).json()
    assert window["expr"] == "*/30 8-20 * * *"
    assert owner.post("/api/plugins/atrium-settings/schedule/%012x" % 0xA3, json={"interval": 7, "start": 8, "end": 20}, headers=H).status_code == 400


def test_a_scheduler_that_is_down_is_reported_not_crashed(make, cfg, tmp_path):
    cfg.hermes_data_dir = str(tmp_path / "h")
    c = make(users=(("alice", "owner", PW),), hermes=Hermes(), config=cfg)
    login(c)
    assert c.get("/api/plugins/atrium-workflows/jobs").json() == {"jobs": []} or c.get("/api/plugins/atrium-workflows/jobs").status_code in (200, 503)
    assert c.post("/api/plugins/atrium-workflows/jobs/%012x/pause" % 0xA1, headers=H).status_code in (404, 503)


# --- Room: a talk-only group chat with the local agent and a cloud model -------------------------------------------------

@pytest.fixture()
def owner_live(owner):
    """The test client with one long-lived event loop, so the room's background replies keep running between requests."""
    with owner:
        yield owner


def _wait_room(c, rid):
    import time as _t
    for _ in range(100):
        r = c.get("/api/plugins/atrium-room/rooms/" + rid).json()
        if not r["running"]:
            return r
        _t.sleep(0.05)
    raise AssertionError("room never finished")


def test_room_talks_and_hands_back(owner_live, monkeypatch):
    owner = owner_live
    import room_backend
    asked = []
    monkeypatch.setattr(room_backend, "_ask_agent_sync", lambda m: (asked.append("agent") or "I think so. @claude what do you say?", 10, 5))
    monkeypatch.setattr(room_backend, "_ask_claude_sync", lambda m, model: (asked.append("claude:" + model) or "Agreed.", 100, 20))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert owner.get("/api/plugins/atrium-room/models").json()["claude_available"] is True
    rid = owner.post("/api/plugins/atrium-room/rooms", json={"title": "Plans"}, headers=H).json()["id"]
    assert owner.post("/api/plugins/atrium-room/rooms/%s/send" % rid, json={"text": "@Robo should we?"}, headers=H).json() == {"ok": True}
    room = _wait_room(owner, rid)
    assert [m["who"] for m in room["messages"]] == ["system", "owner", "agent", "claude"]
    assert asked == ["agent", "claude:claude-sonnet-5-5"]
    assert room["usage"] == {"in": 100, "out": 20}
    assert owner.get("/api/plugins/atrium-room/models").json()["today"]["calls"] == 1
    assert owner.post("/api/plugins/atrium-room/rooms/%s/save" % rid, headers=H).json()["path"].startswith("Rooms/")
    assert list((owner.home / "obsidian-vault" / "Rooms").glob("*.md"))
    assert owner.post("/api/plugins/atrium-room/rooms/%s/delete" % rid, json={}, headers=H).status_code == 400
    assert owner.post("/api/plugins/atrium-room/rooms/%s/delete" % rid, json={"confirm": True}, headers=H).status_code == 200
    assert owner.get("/api/plugins/atrium-room/rooms").json()["rooms"] == []


def test_room_is_for_owners_and_claude_has_a_daily_limit(owner_live, monkeypatch):
    owner = owner_live
    import room_backend
    monkeypatch.setattr(room_backend, "_ask_agent_sync", lambda m: ("PASS", 0, 0))
    monkeypatch.setattr(room_backend, "DAILY_CLAUDE_CALLS", 0)
    rid = owner.post("/api/plugins/atrium-room/rooms", json={}, headers=H).json()["id"]
    owner.post("/api/plugins/atrium-room/rooms/%s/send" % rid, json={"text": "@claude hello"}, headers=H)
    room = _wait_room(owner, rid)
    assert "limit" in room["messages"][-1]["text"]
    owner.as_("bob", PW2, "10.7.0.9")
    assert owner.get("/api/plugins/atrium-room/rooms").status_code == 403
    assert owner.get("/room").status_code == 403
    owner.as_("alice", PW, "10.7.0.10")
    assert owner.get("/agent-room").headers["location"] == "/room"
