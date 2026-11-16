import asyncio
import datetime
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import overview as ov
from conftest import PW, PW2, login
from hermes import Hermes

KEY, TOKEN = "k-123", "t-456"
TODAY = datetime.date(2026, 11, 15)
NOW = time.time()

BOARD = {"columns": [{"name": "triage", "tasks": [{"id": "t1", "title": "An idea", "status": "triage", "assignee": "agent"}]},
                     {"name": "blocked", "tasks": [{"id": "t2", "title": "Stuck", "status": "blocked"}]},
                     {"name": "done", "tasks": [{"id": "t3", "title": "Finished", "status": "done"}]}]}
RUNS = {}
JOBS = [
    {"name": "Good job", "enabled": True, "state": "scheduled", "last_status": "ok", "failure_streak": 0, "schedule_display": "0 5 * * *"},
    {"name": "Bad job", "enabled": True, "state": "scheduled", "last_status": "error", "last_error": "interrupted", "failure_streak": 1},
    {"name": "Off job", "enabled": False, "last_status": "error", "failure_streak": 3}]


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj=None):
        body = json.dumps(obj if obj is not None else {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed_api(self):
        return self.headers.get("Authorization") == "Bearer " + KEY

    def do_GET(self):
        p = self.path
        if p == "/health":
            return self.reply(200, {"status": "ok"})
        if p.startswith("/v1/runs/"):
            run = RUNS.get(p.rsplit("/", 1)[1])
            return self.reply(200, run) if run and self.authed_api() else self.reply(404)
        if p == "/v1/models":
            return self.reply(200, {}) if self.authed_api() else self.reply(401)
        if self.headers.get("X-Hermes-Session-Token") != TOKEN and p != "/api/status":
            return self.reply(401)
        if p == "/api/status":
            return self.reply(200, {"version": "9.9", "gateway_running": True, "gateway_state": "running", "gateway_busy": False,
                                    "active_agents": 0, "gateway_platforms": {"api_server": {"state": "connected"}}})
        if p == "/api/sessions/stats":
            return self.reply(200, {"total": 5, "active_store": 4, "archived": 1, "messages": 99})
        if p.startswith("/api/sessions"):
            return self.reply(200, {"sessions": [{"id": "s1", "title": None, "preview": "hello there", "source": "tui", "model": "m", "message_count": 3, "last_active": NOW - 600}]})
        if p == "/api/analytics/usage":
            return self.reply(200, {"daily": [{"day": "2026-11-14", "api_calls": 10}], "tools": [{"tool": "terminal", "count": 7}],
                                    "totals": {"total_api_calls": 10, "total_input": 1000, "total_output": 500}})
        if p == "/api/cron/jobs":
            return self.reply(200, JOBS)
        if p == "/api/plugins/kanban/board":
            return self.reply(200, BOARD)
        self.reply(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if not self.authed_api():
            return self.reply(401)
        if self.path == "/v1/runs":
            rid = "run_%d" % (len(RUNS) + 1)
            RUNS[rid] = {"run_id": rid, "status": "running", "output": None, "execution_generation": 1,
                         "pending_controls": [{"request_id": "q1", "command": "rm -rf /tmp/x"}], "session_id": body.get("session_id")}
            return self.reply(200, {"run_id": rid, "status": "started"})
        if self.path.endswith("/approval"):
            rid = self.path.split("/")[3]
            RUNS[rid].update(status="completed", output="done", pending_controls=[])
            return self.reply(200, {"status": "ok"})
        if self.path.endswith("/stop"):
            RUNS[self.path.split("/")[3]]["status"] = "cancelled"
            return self.reply(200, {"status": "cancelled"})
        self.reply(404)


@pytest.fixture()
def fake():
    RUNS.clear()
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


@pytest.fixture()
def files(tmp_path):
    hd = tmp_path / "hermes"
    (hd / "obsidian-vault" / "Daily").mkdir(parents=True)
    (hd / "obsidian-vault" / "Radar").mkdir(parents=True)
    (hd / "cron").mkdir()
    for n in ("atrium", "cron", "obsidian-vault"):
        (hd / n).mkdir(exist_ok=True)
    (hd / "obsidian-vault" / "Daily" / "2026-11-15.md").write_text(
        "# 2026-11-15\n\n## Today's Focus\n\n- [ ] first thing\n- [x] done thing\n\n## Log\n- wrote a note about [[Some Page|the page]]\n- second line\n")
    (hd / "obsidian-vault" / "Daily" / "2026-11-13.md").write_text("## Log\n- older entry\n")
    (hd / "obsidian-vault" / "Radar" / "2026-11-14.md").write_text(
        "1. **Big news** | Tech | because [[X|it matters]] | https://example.com/a\n- **Quiet item** | Misc | meh\nnot a radar line\n")
    deck = tmp_path / "deck"
    deck.mkdir()
    (deck / "status.json").write_text(json.dumps({"updated": NOW, "monitors": [{"name": "Web", "state": "up"}, {"name": "Mail", "state": "down"}]}))
    (deck / "system.json").write_text(json.dumps({"updated": NOW, "cpu": {"pct": 5, "cores": 4}, "mem": {"total": 100, "available": 60},
        "gpus": [{"index": 0, "name": "G", "util": 1, "mem_used": 5, "mem_total": 10, "temp": 30, "power": 5}],
        "disks": [{"label": "Data", "used": 25, "total": 100}], "containers": {"running": 7, "problems": ["db"]}}))
    (deck / "extra.json").write_text(json.dumps({"updated": NOW, "backup": {"ok": True, "last_ts": NOW - 3600}, "ai": [{"role": "Main", "name": "m", "up": True}, {"role": "Spare", "name": "s", "up": False}],
        "mail": {"containers": 3, "unhealthy": []}, "security": {"failed_ssh_24h": 2, "pending_updates": 4, "reboot_required": True, "cert_days_left": 10}}))
    (deck / "weather.json").write_text(json.dumps({"updated": NOW, "place": "Somewhere", "now": {"temp": 50, "short": "Clear", "wind": "N 5", "rain": 0}, "periods": [], "alerts": []}))
    return hd, deck


SYSTEMS = {"services": [{"name": "Web app", "url": "https://x.example", "monitor": "Web"}, {"name": "Other", "url": "https://y.example", "monitor": None}],
           "websites": [{"name": "site.example", "monitor": "Mail"}], "local_models": [{"name": "Model A", "role": "main"}], "external": [{"name": "Mail host", "role": "mail"}]}


# ---- parsers ----------------------------------------------------------------------------------------------------------

def test_focus_radar_and_daylog_parsing(files):
    hd, _ = files
    vault = hd / "obsidian-vault"
    f = ov.parse_focus((vault / "Daily" / "2026-11-15.md").read_text())
    assert [(x["text"], x["done"]) for x in f] == [("first thing", False), ("done thing", True)]
    r = ov.latest_radar(vault)
    assert r["date"] == "2026-11-14" and [i["title"] for i in r["items"]] == ["Big news", "Quiet item"]
    assert r["items"][0]["why"] == "because it matters" and r["items"][0]["url"].startswith("https://")
    assert r["items"][1]["url"] == "" and r["items"][1]["rank"] == 2      # bullet lines count too
    log = ov.latest_daylog(vault, TODAY)
    assert log["date"] == "2026-11-15" and log["items"][0] == "wrote a note about the page"
    assert ov.latest_daylog(vault, TODAY + datetime.timedelta(days=10))["items"] == []      # nothing within 7 days
    assert ov.latest_radar(None)["items"] == [] and ov.latest_daylog(None, TODAY)["items"] == []


def test_attention_for_an_owner_and_for_a_plain_user(files):
    hd, deck = files
    o = ov.Overview(Hermes(), str(hd), str(deck), SYSTEMS)
    jobs = ov.read_jobs(JOBS)
    board = ov.board_summary(BOARD)
    status, system, extra = o.deck_file("status.json"), o.deck_file("system.json"), o.deck_file("extra.json")
    full = ov.build_attention(jobs, board, status["monitors"], system, extra, {"running": True}, NOW, True)
    titles = [i["title"] for i in full]
    assert "Job failed: Bad job" in titles and "Job failed: Off job" not in titles       # disabled jobs are ignored
    assert "Mail is down" in titles and "Container problem: db" in titles and "Spare is not running" in titles
    assert any("blocked card" in t for t in titles) and any("restart is needed" in t for t in titles)
    assert any("certificate expires in 10 days" in t for t in titles)
    assert [i["level"] for i in full] == sorted([i["level"] for i in full], key=lambda l: ov.LEVEL_ORDER[l])   # worst first
    plain = ov.build_attention(jobs, board, [], None, None, {"running": True}, NOW, False)
    assert [i["title"] for i in plain] == ["Job failed: Bad job", "1 blocked card on the board"]   # no infrastructure for plain users


def test_health_score(files):
    hd, deck = files
    mons = [{"state": "up"}, {"state": "up"}, {"state": "down"}, {"state": "up"}]
    assert ov.health_summary(mons, [{"level": "bad"}], True)["score"] == 70       # 75 minus 5 for the bad item
    assert ov.health_summary(mons, [], False) == {}
    assert ov.health_summary([{"state": "up"}], [], True)["label"] == "Everything is running."


# ---- the overview and systems payloads ---------------------------------------------------------------------------

def test_overview_home_and_systems(fake, files):
    hd, deck = files
    o = ov.Overview(Hermes(fake, KEY, fake, TOKEN, str(hd)), str(hd), str(deck), SYSTEMS)
    d = asyncio.run(o.home(infra=True, today=TODAY, now=NOW))
    assert d["agent"]["running"] is True and d["agent"]["stats"]["messages"] == 99 and d["agent"]["recent"][0]["title"] == "hello there"
    assert d["agent"]["board"]["cards"][0]["title"] == "An idea" and all(c["status"] != "done" for c in d["agent"]["board"]["cards"])
    assert d["today"]["focus"][0]["text"] == "first thing" and d["weather"]["place"] == "Somewhere"
    assert [j["name"] for j in d["jobs"]] == ["Good job", "Bad job", "Off job"]
    assert d["systems_summary"]["containers"] == 7 and d["systems_summary"]["gpu_vram_pct"] == 50 and d["systems_summary"]["mem_pct"] == 40
    assert d["health"]["monitors_total"] == 2
    plain = asyncio.run(o.home(infra=False, today=TODAY, now=NOW))
    assert "systems_summary" not in plain and "health" not in plain
    sp = asyncio.run(o.systems_page(now=NOW))
    assert [(s["name"], s["state"]) for s in sp["services"]] == [("Web app", "up"), ("Other", "unmonitored")]
    assert sp["websites"][0]["state"] == "down" and sp["websites"][0]["url"] == "https://site.example"
    assert sp["local_models"][0]["name"] == "Model A" and sp["stale"] is False


def test_everything_missing_still_works():
    o = ov.Overview(Hermes(), "", "", {})
    d = asyncio.run(o.home(infra=True, today=TODAY, now=NOW))
    assert d["today"]["focus"] == [] and d["jobs"] == [] and d["agent"]["reachable"] is False
    assert asyncio.run(o.systems_page())["services"] == []


def test_stale_data_is_flagged(files):
    hd, deck = files
    (deck / "system.json").write_text(json.dumps({"updated": NOW - 7200, "gpus": []}))
    assert asyncio.run(ov.Overview(Hermes(), str(hd), str(deck), {}).systems_page(now=NOW))["stale"] is True


# ---- routes ---------------------------------------------------------------------------------------------------------------

def routes(make, cfg, fake, files):
    hd, deck = files
    cfg.hermes_data_dir, cfg.deck_dir, cfg.systems = str(hd), str(deck), SYSTEMS
    return make(users=(("alice", "owner", PW), ("bob", "user", PW2)), hermes=Hermes(fake, KEY, fake, TOKEN, str(hd)), config=cfg)


def test_overview_and_systems_routes_respect_roles(make, cfg, fake, files):
    c = routes(make, cfg, fake, files)
    assert c.get("/api/overview").status_code == 401 and c.get("/api/systems").status_code == 401
    login(c, "bob", PW2, ip="10.1.1.1")
    plain = c.get("/api/overview")
    assert plain.status_code == 200 and "systems_summary" not in plain.json()
    assert c.get("/api/systems").status_code == 403
    c.post("/logout", headers={"origin": "https://app.test"})
    login(c, "alice", PW, ip="10.1.1.2")
    full = c.get("/api/overview").json()
    assert full["systems_summary"]["containers"] == 7
    assert c.get("/api/systems").json()["services"][0]["name"] == "Web app"
    text = c.get("/api/overview").text
    assert KEY not in text and TOKEN not in text


def test_ask_flow_with_approval_and_ownership(make, cfg, fake, files):
    c = routes(make, cfg, fake, files)
    H = {"origin": "https://app.test"}
    assert c.post("/api/ask", json={"message": "hi"}, headers=H).status_code == 401
    login(c, "bob", PW2, ip="10.2.2.2")
    assert c.post("/api/ask", json={"message": " "}, headers=H).status_code == 400
    assert c.post("/api/ask", json={"message": "x" * 9000}, headers=H).status_code == 400
    assert c.post("/api/ask", json={"message": "hi"}, headers={"origin": "https://evil.example"}).status_code == 403
    r = c.post("/api/ask", json={"message": "hello"}, headers=H)
    assert r.status_code == 200
    rid = r.json()["run_id"]
    assert RUNS[rid]["session_id"] == "ask-bob"                                   # per-person conversation
    time.sleep(1.1)
    assert c.post("/api/ask", json={"message": "again"}, headers=H).status_code == 409   # still working
    run = c.get("/api/runs/" + rid).json()
    assert run["status"] == "running" and run["pending_controls"][0]["command"] == "rm -rf /tmp/x"
    ok = c.post("/api/runs/%s/approval" % rid, json={"request_id": "q1", "execution_generation": 1, "choice": "once"}, headers=H)
    assert ok.json()["ok"] is True
    assert c.get("/api/runs/" + rid).json()["output"] == "done"
    assert c.post("/api/runs/%s/approval" % rid, json={"request_id": "q1", "choice": "maybe"}, headers=H).status_code == 400
    # another person cannot see or stop bob's run; an owner can look
    c.post("/logout", headers=H)
    login(c, "alice", PW, ip="10.2.2.3")
    assert c.get("/api/runs/" + rid).status_code == 200
    c.post("/logout", headers=H)
    c.app.state.users.add("carol", "user", password_hash=c.app.state.users.get("bob")["hash"])
    login(c, "carol", PW2, ip="10.2.2.4")
    assert c.get("/api/runs/" + rid).status_code == 403
    assert c.post("/api/runs/%s/stop" % rid, headers=H).status_code == 403
    assert c.get("/api/runs/bad id!").status_code in (400, 404)


def test_display_name_reaches_the_pages(make, cfg):
    c = make()
    c.app.state.users.set_name("alice", "Alice A.")
    login(c)
    assert c.get("/api/me").json()["name"] == "Alice A."
    assert '"name": "Alice A."' in c.get("/shell-config.js").text


class StubHermes:
    def __init__(self, status, board=None, jobs=None):
        self.data = {"/api/status": status, "/api/sessions/stats": {}, "/api/analytics/usage": {}, "/api/sessions?limit=6": {"sessions": []},
                     "/api/plugins/kanban/board": board or {"columns": []}, "/api/cron/jobs": {"jobs": jobs or []}}

    async def dash_json(self, path):
        return 200, self.data.get(path, {})


def test_a_stuck_busy_flag_does_not_count_as_working():
    """Hermes can leave its own busy flag and agent count on after work has finished; only real work makes the agent look busy."""
    stuck = {"gateway_running": True, "gateway_busy": True, "active_agents": 1, "active_sessions": 1, "gateway_platforms": {"api_server": {"metrics": {"active_runs": 0}}}}
    a = asyncio.run(ov.Overview(StubHermes(stuck))._agent())
    assert a["busy"] is False and a["active_agents"] == 0 and a["gateway_busy"] is True and a["gateway_agents"] == 1
    run = dict(stuck, gateway_platforms={"api_server": {"metrics": {"active_runs": 2}}})
    assert asyncio.run(ov.Overview(StubHermes(run))._agent())["active_agents"] == 2
    card = {"columns": [{"name": "running", "tasks": [{"id": "t9", "title": "On it"}]}]}
    assert asyncio.run(ov.Overview(StubHermes(stuck, board=card))._agent())["busy"] is True
    home = asyncio.run(ov.Overview(StubHermes(stuck, jobs=[{"name": "Nightly", "state": "running"}])).home(infra=False, today=TODAY, now=NOW))
    assert home["agent"]["busy"] is True and home["agent"]["active_agents"] == 1
    idle = asyncio.run(ov.Overview(StubHermes(stuck, jobs=[{"name": "Nightly", "state": "scheduled"}])).home(infra=False, today=TODAY, now=NOW))
    assert idle["agent"]["busy"] is False
