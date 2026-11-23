import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import PW, PW2, login
from hermes import Hermes
from test_chat import Fake as HermesFake, KEY, TOKEN, H, P, RUNS, SESSIONS

GATE_CALLS = []


class Gate(BaseHTTPRequestHandler):
    """A stand-in for the approval service."""

    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/actions":
            return self.reply(200, {"pending": [{"id": "a1b2c3d4e5f6", "kind": "mail.send", "details": {"to": "x@example.org"}}], "recent": []})
        if self.path == "/log":
            return self.reply(200, {"entries": []})
        self.reply(404, {})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        GATE_CALLS.append((self.path, body))
        if self.path == "/decide":
            return self.reply(200, {"ok": True, "status": body.get("decision")})
        self.reply(404, {})


@pytest.fixture()
def servers():
    GATE_CALLS.clear(); SESSIONS.clear(); RUNS.clear()
    out = []
    for handler in (HermesFake, Gate):
        srv = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        out.append(srv)
    yield tuple("http://127.0.0.1:%d" % s.server_address[1] for s in out)
    for s in out:
        s.shutdown()


@pytest.fixture()
def full(make, cfg, servers, tmp_path):
    hermes_url, gate_url = servers
    cfg.hermes_api_base, cfg.hermes_api_key = hermes_url, KEY
    cfg.hermes_dashboard_base, cfg.hermes_dashboard_token = hermes_url, TOKEN
    cfg.hermes_data_dir = str(tmp_path / "hermes-home")
    (tmp_path / "hermes-home" / "atrium").mkdir(parents=True)
    cfg.push_enabled, cfg.action_gate_url = True, gate_url
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), hermes=Hermes(hermes_url, KEY, hermes_url, TOKEN, cfg.hermes_data_dir), config=cfg)

    def as_(user, pw, ip):
        c.post("/logout", headers=H)
        assert login(c, user, pw, ip=ip).status_code == 303
    c.as_ = as_
    return c


SUB = {"endpoint": "https://push.example.org/send/abcdef123456", "keys": {"p256dh": "A" * 87, "auth": "B" * 22}}


def test_push_belongs_to_owners_and_works(full):
    full.as_("alice", PW, "10.6.0.1")
    key = full.get(P + "/push/key").json()["public_key"]
    assert isinstance(key, str) and len(key) > 40
    assert full.get(P + "/push/status").json() == {"devices": 0}
    assert full.post(P + "/push/subscribe", json={"subscription": SUB, "label": "phone"}, headers=H).json()["devices"] == 1
    assert full.get(P + "/push/status").json() == {"devices": 1}
    assert full.get(P + "/push/key").json()["public_key"] == key                          # the same key every time
    assert full.post(P + "/push/unsubscribe", json={"endpoint": SUB["endpoint"]}, headers=H).json()["devices"] == 0
    assert full.post(P + "/push/subscribe", json={"subscription": {"endpoint": "http://not-https"}}, headers=H).status_code == 400


def test_plain_users_cannot_touch_notifications(full):
    full.as_("bob", PW2, "10.6.0.2")
    for method, path in (("GET", "/push/key"), ("GET", "/push/status"), ("GET", "/push/devices"), ("GET", "/settings/notify")):
        assert full.request(method, P + path).status_code == 403, path
    assert full.post(P + "/push/subscribe", json={"subscription": SUB}, headers=H).status_code == 403
    assert full.post(P + "/push/test", headers=H).status_code == 403
    assert full.post(P + "/settings/notify", json={}, headers=H).status_code == 403


def test_the_phone_app_files(full):
    assert full.get(P + "/manifest.webmanifest").status_code == 401                        # behind the login
    assert full.get(P + "/sw.js").status_code == 401
    assert full.get(P + "/icon-192.png").status_code == 200                                # but the icons are plain pictures
    assert full.get(P + "/icon-192.png").content[:4] == b"\x89PNG"
    assert full.get(P + "/icon-badge.png").status_code == 200
    full.as_("alice", PW, "10.6.0.3")
    m = full.get(P + "/manifest.webmanifest").json()
    assert m["start_url"] == "/chat" and m["id"] == "/chat" and m["display"] == "standalone" and m["name"] == "Assistant"
    sw = full.get(P + "/sw.js")
    assert sw.headers["service-worker-allowed"] == "/" and "/chat" in sw.text and "/agent-chat" not in sw.text


def test_old_dashboard_address_still_lands_on_chat(full):
    r = full.get("/agent-chat?approvals=1")
    assert r.status_code == 303 and r.headers["location"] == "/chat?approvals=1"


def test_page_aliases_are_validated(tmp_path):
    from pages import load_pages
    core = tmp_path / "c.json"
    ok = {"id": "a", "path": "/a", "label": "A", "plugin": "pa", "scripts": ["/static/x.js"]}
    core.write_text(json.dumps([dict(ok, aliases=["/old-a"])]))
    assert load_pages(core, tmp_path, [])[0]["aliases"] == ["/old-a"]
    for bad in (["/"], ["/login"], ["/api/x"], ["/A B"], ["/a"]):
        core.write_text(json.dumps([dict(ok, aliases=bad)]))
        with pytest.raises(ValueError):
            load_pages(core, tmp_path, [])
    core.write_text(json.dumps([dict(ok, aliases=["/dup"]), dict(ok, id="b", path="/b", aliases=["/dup"])]))
    with pytest.raises(ValueError):
        load_pages(core, tmp_path, [])


def test_gate_check_for_the_approval_service(full):
    assert full.get("/api/gate-check").status_code == 401
    full.as_("bob", PW2, "10.6.0.4")
    assert full.get("/api/gate-check").status_code == 403                                  # not an owner: may not approve
    full.as_("alice", PW, "10.6.0.5")
    assert full.get("/api/gate-check").json() == {"ok": True, "user": "alice"}


def test_pending_actions_come_from_the_approval_service_and_decisions_replay_the_session(full):
    full.as_("alice", PW, "10.6.0.6")
    pend = full.get(P + "/actions").json()["pending"]
    assert pend and pend[0]["id"] == "a1b2c3d4e5f6"
    r = full.post(P + "/actions/a1b2c3d4e5f6/decide", json={"decision": "approve"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "approve"
    path, body = GATE_CALLS[-1]
    assert path == "/decide" and body["id"] == "a1b2c3d4e5f6" and body["decision"] == "approve"
    assert any("app_session" in str(v) for v in body["auth"].values()), "the app's session cookie is passed on so the service can verify it"
    assert full.post(P + "/actions/not-an-id/decide", json={"decision": "approve"}, headers=H).status_code == 400
    assert full.post(P + "/actions/a1b2c3d4e5f6/decide", json={"decision": "approve"}, headers={"origin": "https://evil.example"}).status_code == 403


def test_plain_users_cannot_decide(full):
    full.as_("bob", PW2, "10.6.0.7")
    assert full.post(P + "/actions/a1b2c3d4e5f6/decide", json={"decision": "approve"}, headers=H).status_code == 403
    assert not GATE_CALLS


def test_push_stays_off_unless_the_install_turns_it_on(make, cfg, servers, tmp_path):
    hermes_url, _ = servers
    cfg.hermes_api_base, cfg.hermes_api_key = hermes_url, KEY
    cfg.hermes_data_dir = str(tmp_path / "h2")
    c = make(users=(("alice", "owner", PW),), hermes=Hermes(), config=cfg)
    login(c)
    for path in ("/push/key", "/sw.js", "/manifest.webmanifest"):
        assert c.get(P + path).status_code == 404, path
    assert c.get(P + "/actions").json() == {"pending": [], "recent": []}                  # no approval service: answers none


def test_the_shell_is_told_where_the_phone_app_files_are_only_when_it_is_on(full, make, cfg, servers, tmp_path):
    full.as_("alice", PW, "10.6.0.8")
    text = full.get("/shell-config.js").text
    assert '"pwa": {"manifest": "/api/plugins/atrium-chat/manifest.webmanifest"' in text and 'id="vn-install"' in full.get("/").text
    cfg.push_enabled = False
    c = make(users=(), hermes=Hermes(), config=cfg)
    login(c)
    assert '"pwa": null' in c.get("/shell-config.js").text


def test_the_install_check_page_is_there_for_signed_in_people(full):
    assert full.get("/install-check").status_code == 303
    full.as_("bob", PW2, "10.6.0.9")
    assert full.get("/install-check").status_code == 200
    assert '"label": "Install check"' in full.get("/shell-config.js").text
    assert full.get("/static/pages/installcheck.js").status_code == 200


def test_push_goes_out_through_the_configured_proxy_and_only_there(full, monkeypatch):
    """The sender must use the proxy (the app has no direct internet) and must not pick up proxy settings from the environment."""
    import chat_push as b
    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen.update(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import httpx
    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(b, "PUSH_PROXY", "http://egress-proxy:3128")
    full.as_("alice", PW, "10.6.0.11")
    full.post(P + "/push/subscribe", json={"subscription": SUB, "label": "phone"}, headers=H)
    monkeypatch.setattr(b, "send_one", lambda client, sub, data, payload, urgency: 201)
    res = b.deliver("t", "x", "test", "normal", True, "/chat")
    assert res["sent"] == 1
    assert seen.get("proxy") == "http://egress-proxy:3128" and seen.get("trust_env") is False


def test_the_proxy_setting_comes_from_the_environment(tmp_path):
    import config as configmod
    c = configmod.load({"INSTANCE_DIR": str(tmp_path), "DATA_DIR": str(tmp_path / "d"), "PUSH_PROXY": "http://p:3128", "PUSH_ENABLED": "1"})
    assert c.push_proxy == "http://p:3128" and c.push_enabled is True
