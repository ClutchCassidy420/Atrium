import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import PW, PW2, login
from hermes import Hermes

TOKEN = "dash-token"
KEY = "k" * 32
H = {"origin": "https://app.test"}
STATE = {"runs": 0, "cards": 0, "backup": False, "calls": []}


def serve(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


class Quiet(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Dash(Quiet):          # a stand-in for the Hermes dashboard
    def do_GET(self):
        if self.headers.get("X-Hermes-Session-Token") != TOKEN:
            return self.reply(401, {})
        p = self.path.split("?")[0]
        if p == "/api/status":
            return self.reply(200, {"gateway_platforms": {"api_server": {"metrics": {"active_runs": STATE["runs"]}}}})
        if p == "/api/plugins/kanban/board":
            return self.reply(200, {"columns": [{"name": "running", "tasks": [{"id": "t_%d" % i} for i in range(STATE["cards"])]}]})
        self.reply(404, {})


class Helper(Quiet):        # a stand-in for the host helper
    def do_GET(self):
        if self.headers.get("X-Hostctl-Key") != KEY:
            return self.reply(403, {"error": "Not allowed."})
        self.reply(200, {"packages": {"safe": [{"name": "vim", "from": "1", "to": "2"}], "care": []}, "backup_running": STATE["backup"],
                         "update": {"running": False}, "scheduled": None, "log": []})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.headers.get("X-Hostctl-Key") != KEY:
            return self.reply(403, {"error": "Not allowed."})
        STATE["calls"].append((self.path, body))
        if not body.get("cookie"):
            return self.reply(401, {"error": "Only a signed-in owner can do that."})
        self.reply(200, {"ok": True})


@pytest.fixture()
def host(make, cfg, tmp_path):
    STATE.update(runs=0, cards=0, backup=False, calls=[])
    dash_srv, dash = serve(Dash)
    helper_srv, helper = serve(Helper)
    cfg.hermes_dashboard_base, cfg.hermes_dashboard_token, cfg.hermes_data_dir = dash, TOKEN, str(tmp_path / "home")
    (tmp_path / "home" / "atrium").mkdir(parents=True)
    cfg.host_action_url, cfg.host_action_key = helper, KEY
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), hermes=Hermes("", "", dash, TOKEN, str(tmp_path / "home")), config=cfg)
    c.cfg = cfg
    assert login(c, "alice", PW, ip="10.9.0.1").status_code == 303
    yield c
    dash_srv.shutdown()
    helper_srv.shutdown()


def test_not_configured_means_the_section_stays_hidden(make, cfg, tmp_path):
    cfg.hermes_data_dir = str(tmp_path)
    c = make(hermes=Hermes("", "", "http://127.0.0.1:1", "t", str(tmp_path)), config=cfg)
    assert login(c, ip="10.9.1.1").status_code == 303
    assert c.get("/api/host/status").json() == {"enabled": False}
    assert c.post("/api/host/action", json={"action": "reboot"}, headers=H).status_code == 404


def test_status_passes_through_and_adds_what_the_agent_is_doing(host):
    STATE["cards"] = 1
    j = host.get("/api/host/status").json()
    assert j["enabled"] and j["reachable"] and j["packages"]["safe"][0]["name"] == "vim"
    assert j["busy"] == {"runs": 0, "cards": 1}


def test_only_owners_can_use_server_actions(host):
    host.post("/logout", headers=H)
    assert login(host, "bob", PW2, ip="10.9.0.2").status_code == 303
    assert host.get("/api/host/status").status_code == 403
    assert host.post("/api/host/action", json={"action": "reboot"}, headers=H).status_code == 403
    assert host.post("/api/host/cancel", headers=H).status_code == 403
    assert STATE["calls"] == []


def test_an_action_reaches_the_helper_with_the_key_and_the_session_cookie(host):
    r = host.post("/api/host/action", json={"action": "update_safe"}, headers=H)
    assert r.status_code == 200 and r.json()["ok"]
    path, body = STATE["calls"][0]
    assert path == "/do" and body["action"] == "update_safe" and "__Host-app_session=" in body["cookie"]


def test_only_the_four_actions_are_accepted(host):
    for bad in ("rm", "restart_hermes", "REBOOT", ""):
        assert host.post("/api/host/action", json={"action": bad}, headers=H).status_code == 422
    assert STATE["calls"] == []


def test_actions_need_a_same_origin_request(host):
    assert host.post("/api/host/action", json={"action": "reboot"}, headers={"origin": "https://evil.example"}).status_code == 403
    assert STATE["calls"] == []


def test_busy_work_blocks_until_forced(host):
    STATE["runs"] = 1
    r = host.post("/api/host/action", json={"action": "reboot"}, headers=H)
    assert r.status_code == 409 and "chat run" in r.json()["detail"]
    assert STATE["calls"] == []
    assert host.post("/api/host/action", json={"action": "reboot", "force": True}, headers=H).status_code == 200
    STATE.update(runs=0, backup=True, calls=[])
    assert host.post("/api/host/action", json={"action": "shutdown"}, headers=H).status_code == 409


def test_cancel_goes_to_the_helper(host):
    assert host.post("/api/host/cancel", headers=H).status_code == 200
    assert STATE["calls"][0][0] == "/cancel"


def test_a_helper_that_is_down_is_reported_not_crashed(host):
    host.cfg.host_action_url = "http://127.0.0.1:1"
    j = host.get("/api/host/status").json()
    assert j["enabled"] and j["reachable"] is False
    assert host.post("/api/host/action", json={"action": "reboot"}, headers=H).status_code == 502
