import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import pytest

from hermes import DATA_FOLDERS, Hermes

KEY, TOKEN = "test-api-key-123", "test-dash-token-456"


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

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"status": "ok"})
        if self.path == "/v1/models":
            return self.reply(200, {"data": []}) if self.headers.get("Authorization") == "Bearer " + KEY else self.reply(401)
        if self.path == "/api/status":
            return self.reply(200, {"ok": True})
        if self.path.startswith("/api/sessions"):
            return self.reply(200, []) if self.headers.get("X-Hermes-Session-Token") == TOKEN else self.reply(401)
        self.reply(404)


@pytest.fixture()
def fake():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


@pytest.fixture()
def data(tmp_path):
    for n in DATA_FOLDERS:
        (tmp_path / "hermes" / n).mkdir(parents=True)
    return str(tmp_path / "hermes")


def run(coro):
    return asyncio.run(coro)


def test_everything_connected(fake, data):
    h = run(Hermes(fake, KEY, fake, TOKEN, data).health())
    assert h["ok"] is True
    assert h["api"] == {"reachable": True, "key_ok": True}
    assert h["dashboard"] == {"reachable": True, "token_ok": True}
    assert all(h["data"].values())


def test_wrong_key_and_wrong_token_are_reported(fake, data):
    h = run(Hermes(fake, "nope", fake, "nope", data).health())
    assert h["ok"] is False
    assert h["api"] == {"reachable": True, "key_ok": False}
    assert h["dashboard"] == {"reachable": True, "token_ok": False}


def test_unreachable_servers(data):
    h = run(Hermes("http://127.0.0.1:1", KEY, "http://127.0.0.1:1", TOKEN, data).health())
    assert h["ok"] is False and h["api"]["reachable"] is False and h["dashboard"]["reachable"] is False


def test_missing_data_folder(fake, tmp_path):
    h = run(Hermes(fake, KEY, fake, TOKEN, str(tmp_path)).health())
    assert h["ok"] is False and not any(h["data"].values())


def test_not_configured_is_not_ok():
    assert run(Hermes().health())["ok"] is False


def test_health_route_needs_an_owner_and_never_returns_secrets(fake, data, make):
    from conftest import PW, PW2, login
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), hermes=Hermes(fake, KEY, fake, TOKEN, data))
    assert c.get("/api/health").status_code == 401
    login(c, "bob", PW2, ip="10.0.0.8")
    assert c.get("/api/health").status_code == 403          # an ordinary user does not see infrastructure
    c.post("/logout", headers={"origin": "https://app.test"})
    login(c, "alice", PW, ip="10.0.0.9")
    r = c.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert KEY not in r.text and TOKEN not in r.text
