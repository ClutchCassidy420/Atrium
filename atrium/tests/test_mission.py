import datetime
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import PW, PW2, login
from hermes import Hermes

TOKEN = "mission-token"
H = {"origin": "https://app.test"}
P = "/api/plugins/atrium-mission"
SOUL = {"content": "You are a careful assistant. Be kind, be brief, and say when you are unsure.\n"}
PUTS = []
TODAY = datetime.date.today().isoformat()


class Dash(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj=None):
        body = json.dumps(obj if obj is not None else {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("X-Hermes-Session-Token") != TOKEN:
            return self.reply(401)
        if self.path.startswith("/api/profiles/default/soul"):
            return self.reply(200, {"content": SOUL["content"], "exists": True})
        self.reply(404)

    def do_PUT(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.headers.get("X-Hermes-Session-Token") != TOKEN or not self.path.startswith("/api/profiles/default/soul"):
            return self.reply(404)
        PUTS.append(body["content"])
        SOUL["content"] = body["content"]
        self.reply(200, {"ok": True})


@pytest.fixture()
def dash():
    PUTS.clear()
    SOUL["content"] = "You are a careful assistant. Be kind, be brief, and say when you are unsure.\n"
    srv = HTTPServer(("127.0.0.1", 0), Dash)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


@pytest.fixture()
def mission(make, cfg, dash, tmp_path):
    home = tmp_path / "home"
    (home / "obsidian-vault" / "Daily").mkdir(parents=True)
    (home / "atrium").mkdir()
    cfg.hermes_dashboard_base, cfg.hermes_dashboard_token, cfg.hermes_data_dir = dash, TOKEN, str(home)
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), hermes=Hermes("", "", dash, TOKEN, str(home)), config=cfg)
    c.home = home

    def as_(user, pw, ip):
        c.post("/logout", headers=H)
        assert login(c, user, pw, ip=ip).status_code == 303
    c.as_ = as_
    as_("alice", PW, "10.8.0.1")
    return c


def daily(c):
    return (c.home / "obsidian-vault" / "Daily" / (TODAY + ".md")).read_text()


def test_only_owners_may_use_the_mission_controls(mission):
    mission.as_("bob", PW2, "10.8.0.2")
    for path in ("/focus", "/engine", "/soul"):
        assert mission.get(P + path).status_code == 403, path
    assert mission.post(P + "/focus", json={"text": "x"}, headers=H).status_code == 403
    assert mission.post(P + "/soul", json={"text": "x" * 40}, headers=H).status_code == 403
    mission.post("/logout", headers=H)
    assert mission.get(P + "/focus").status_code == 401
    mission.as_("bob", PW2, "10.8.0.3")
    assert mission.get("/settings").status_code == 403                                  # Persona and Avatar live inside Settings now


def test_the_parts_the_home_page_replaces_are_not_mounted(mission):
    for path in ("/status", "/ask", "/health", "/radar", "/daylog"):
        assert mission.get(P + path).status_code in (404, 405), path


def test_focus_add_toggle_and_remove(mission):
    assert mission.get(P + "/focus").json() == {"date": TODAY, "items": []}
    r = mission.post(P + "/focus", json={"text": "  first   thing  "}, headers=H).json()
    assert [(i["text"], i["done"]) for i in r["items"]] == [("first thing", False)]
    mission.post(P + "/focus", json={"text": "second thing"}, headers=H)
    assert "## Today's Focus" in daily(mission) and "- [ ] first thing" in daily(mission)        # saved in the day's note
    r = mission.post(P + "/focus/toggle", json={"index": 0}, headers=H).json()
    assert r["items"][0]["done"] is True and "- [x] first thing" in daily(mission)
    r = mission.post(P + "/focus/remove", json={"index": 0}, headers=H).json()
    assert [i["text"] for i in r["items"]] == ["second thing"] and r["items"][0]["i"] == 0
    assert mission.post(P + "/focus/toggle", json={"index": 5}, headers=H).status_code == 404
    assert mission.post(P + "/focus", json={"text": "   "}, headers=H).status_code in (400, 422)
    assert mission.post(P + "/focus", json={"text": "x"}, headers={"origin": "https://evil.example"}).status_code == 403
    assert not list((mission.home / "obsidian-vault" / "Daily").glob("*.tmp"))                   # no leftovers from the atomic save


def test_focus_keeps_the_rest_of_the_note(mission):
    note = mission.home / "obsidian-vault" / "Daily" / (TODAY + ".md")
    note.write_text("# %s\n\nSome notes.\n\n## Today's Focus\n\n- [ ] old\n\n## Log\n- kept this line\n" % TODAY)
    mission.post(P + "/focus", json={"text": "new"}, headers=H)
    text = note.read_text()
    assert "Some notes." in text and "- [ ] old" in text and "- [ ] new" in text and "## Log\n- kept this line" in text


def test_soul_is_read_through_the_dashboard_and_saves_keep_history(mission):
    s = mission.get(P + "/soul").json()
    assert s["text"].startswith("You are a careful assistant") and s["history"] == [] and s["max"] == 12000
    new = "You are a warm, careful assistant who always checks the date before acting.\n"
    assert mission.post(P + "/soul", json={"text": new}, headers=H).json() == {"ok": True}
    assert PUTS == [new]
    hist = list((mission.home / "atrium" / "soul-history").glob("SOUL-*.md"))
    assert len(hist) == 1 and hist[0].read_text().startswith("You are a careful assistant")      # the old text is kept
    assert mission.post(P + "/soul", json={"text": new}, headers=H).json() == {"ok": True} and len(PUTS) == 1   # no change: nothing written
    assert mission.post(P + "/soul", json={"text": "too short"}, headers=H).status_code == 422
    assert mission.post(P + "/soul", json={"text": "x" * 12001}, headers=H).status_code == 422


def test_soul_versions_can_be_read_and_restored(mission):
    old = SOUL["content"]
    mission.post(P + "/soul", json={"text": "Version two of the persona, with some different rules to follow.\n"}, headers=H)
    vid = mission.get(P + "/soul").json()["history"][0]["id"]
    assert mission.get(P + "/soul/version", params={"id": vid}).json()["text"] == old
    assert mission.get(P + "/soul/version", params={"id": "../../etc/passwd"}).status_code == 400
    assert mission.get(P + "/soul/version", params={"id": "SOUL-20000101-000000.md"}).status_code == 404
    assert mission.post(P + "/soul/restore", json={"id": vid}, headers=H).json() == {"ok": True}
    assert SOUL["content"] == old
    assert len(list((mission.home / "atrium" / "soul-history").glob("SOUL-*.md"))) == 2          # the replaced text was kept too
    assert mission.post(P + "/soul/restore", json={"id": "nonsense"}, headers=H).status_code == 400


def test_the_engine_stays_local_without_a_cloud_key(mission):
    e = mission.get(P + "/engine").json()
    assert e["engine"] == "local" and e["claude_available"] is False and e["claude_models"]
    assert mission.post(P + "/engine", json={"engine": "local"}, headers=H).json() == {"ok": True}
    assert mission.post(P + "/engine", json={"engine": "claude", "model": e["claude_models"][0]["id"], "minutes": 15}, headers=H).status_code == 400   # needs confirm
    assert mission.post(P + "/engine", json={"engine": "claude", "model": e["claude_models"][0]["id"], "minutes": 15, "confirm": True}, headers=H).status_code == 409
    assert mission.get(P + "/engine").json()["engine"] == "local"


def test_everyone_else_gets_a_minimal_home(mission):
    mission.post(P + "/focus", json={"text": "private thing"}, headers=H)
    full = mission.get("/api/overview").json()
    assert full.get("limited") is None and full["today"]["focus"][0]["text"] == "private thing"
    mission.as_("bob", PW2, "10.8.0.4")
    small = mission.get("/api/overview").json()
    assert small["limited"] is True and small["today"]["focus"] == [] and small["jobs"] == [] and small["attention"] == []
    assert set(small["agent"]) <= {"reachable", "running", "state", "busy", "active_agents", "version"}
    assert "private thing" not in json.dumps(small)


def test_persona_and_avatar_are_sections_of_settings_and_old_addresses_still_work(mission):
    for old in ("/persona", "/avatar", "/agent-avatar"):
        assert mission.get(old).headers["location"] == "/settings", old                   # old bookmarks land on Settings
    page = mission.get("/settings")
    assert page.status_code == 200
    cfg = mission.get("/shell-config.js").text
    assert '"label": "Persona"' not in cfg and '"label": "Avatar"' not in cfg               # no separate sidebar entries any more
    for script in ("/static/pages/persona.js", "/static/pages/avatar/orb.js", "/static/pages/avatar/index.js"):
        assert script in cfg, script                                                       # Settings loads what its sections need
    assert mission.get("/mission-control").headers["location"] == "/"
