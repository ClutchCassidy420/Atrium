"""Contract fixture for Atrium's dashboard and fresh-chat entry points."""
import threading
from http.server import ThreadingHTTPServer
from uuid import uuid4

import pytest

from conftest import PW, PW2, login
from hermes import Hermes
from test_chat import Fake, H, KEY, TOKEN, P as CHAT

API = "/api/plugins/atrium-settings/connections"


@pytest.fixture()
def mcp(make, cfg, tmp_path):
    installed = {}
    received = {}
    synthetic_key = uuid4().hex

    def entry(name, **overrides):
        return dict(name=name, transport="http", url="https://fixture.invalid/mcp",
                    needs_install=False, auth_type="none", installed=name in installed, **overrides)

    class Dashboard(Fake):
        def do_GET(self):
            if not self.path.startswith("/api/mcp/"):
                return super().do_GET()
            if not self.dash_ok():
                return self.reply(401)
            if self.path == "/api/mcp/catalog":
                keyed = entry("keyed")
                keyed.update(auth_type="api_key", required_env=[{"name": "FIXTURE_KEY", "required": True}])
                return self.reply(200, {"entries": [
                    entry("echo"), keyed, entry("scripted", command="ignored"),
                    entry("templated", bootstrap=["ignored"]),
                ]})
            if self.path == "/api/mcp/servers":
                return self.reply(200, {"servers": list(installed.values())})
            return self.reply(404)

        def do_POST(self):
            body = self.body()
            if self.path == "/v1/runs" and self.api_ok():
                if installed.get("echo", {}).get("enabled") and body["session_id"] not in received:
                    received[body["session_id"]] = self.echo(body["input"])
                    return self.reply(200, {"run_id": "fixture_run"})
                return self.reply(400)
            if not self.dash_ok():
                return self.reply(401)
            if self.path == "/api/mcp/catalog/install":
                name = body["name"]
                received[name] = body
                if name == "keyed":
                    if body["env"]["FIXTURE_KEY"] != synthetic_key:
                        return self.reply(400, {"detail": f"upstream error containing {synthetic_key}"})
                installed[name] = {"name": name, "enabled": body["enable"],
                                   "url": f"https://fixture.invalid/{synthetic_key}", "env": {"KEY": synthetic_key}}
                return self.reply(200, {"ok": True})
            if self.path == "/api/mcp/servers/echo/test":
                return self.reply(200, {"ok": True, "tools": [{"name": "echo", "description": synthetic_key}]})
            if self.path == "/api/mcp/servers/legacy/test":
                return self.reply(405)
            return self.reply(404)

        @staticmethod
        def echo(text):
            return "fixture echo: " + text

        def do_PUT(self):
            if self.path == "/api/mcp/servers/echo/enabled" and self.dash_ok():
                installed["echo"]["enabled"] = self.body()["enabled"]
                return self.reply(200, {"ok": True})
            return self.reply(404)

        def do_DELETE(self):
            if self.path == "/api/mcp/servers/echo" and self.dash_ok():
                del installed["echo"]
                return self.reply(200, {"ok": True})
            return self.reply(404)

    with ThreadingHTTPServer(("127.0.0.1", 0), Dashboard) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            cfg.hermes_api_base, cfg.hermes_api_key = base, KEY
            cfg.hermes_dashboard_base, cfg.hermes_dashboard_token = base, TOKEN
            cfg.hermes_data_dir = str(tmp_path / "hermes-home")
            (tmp_path / "hermes-home" / "atrium").mkdir(parents=True)
            client = make(hermes=Hermes(base, KEY, base, TOKEN, cfg.hermes_data_dir),
                          users=(("alice", "owner", PW), ("bob", "user", PW2)), config=cfg)
            yield client, installed, received, synthetic_key
        finally:
            server.shutdown()
            thread.join()


def test_connect_test_and_start_fresh_chat(mcp):
    client, installed, received, synthetic_key = mcp
    assert login(client).status_code == 303
    assert client.get(API + "/catalog").json()["entries"][0]["name"] == "echo"
    assert client.post(API + "/echo/connect", headers=H, json={}).status_code == 200
    assert received["echo"] == {"name": "echo", "enable": True, "env": {}}
    assert synthetic_key not in client.get(API + "/installed").text
    assert client.post(API + "/echo/test", headers=H).json() == {"tools": ["echo"]}
    session = "atrium-chat-connectionfixture"
    response = client.post(CHAT + "/send", headers=H, json={"session_id": session, "message": "hello"})
    assert response.status_code == 200
    assert received[session] == "fixture echo: hello"
    for enabled in (False, True):
        assert client.put(API + "/echo/enabled", headers=H, json={"enabled": enabled}).status_code == 200
        assert installed["echo"]["enabled"] is enabled
    assert client.delete(API + "/echo", headers=H).status_code == 200
    assert client.get(API + "/installed").json() == {"servers": []}


def test_owner_origin_and_credentials(mcp):
    client, _, received, synthetic_key = mcp
    assert client.get(API + "/catalog").status_code == 401
    login(client, "bob", PW2)
    assert client.get(API + "/installed").status_code == 403
    login(client)
    assert client.post(API + "/keyed/connect", headers={"origin": "https://other.test"}, json={}).status_code == 403
    for name in ("scripted", "templated"):
        assert client.post(f"{API}/{name}/connect", headers=H, json={}).status_code == 400
    for env in ({}, {"UNDECLARED": synthetic_key}, {"FIXTURE_KEY": [synthetic_key]},
                {"FIXTURE_KEY": synthetic_key + "\n"}):
        response = client.post(API + "/keyed/connect", headers=H, json={"env": env})
        assert response.status_code == 400
        assert synthetic_key not in response.text
        assert "keyed" not in received
    response = client.post(API + "/keyed/connect", headers=H, json={"env": {"FIXTURE_KEY": "rejected"}})
    assert response.status_code == 502
    assert synthetic_key not in response.text
    response = client.post(API + "/keyed/connect", headers=H, json={"env": {"FIXTURE_KEY": synthetic_key}})
    assert response.json() == {"ok": True}
    assert received["keyed"]["env"] == {"FIXTURE_KEY": synthetic_key}
    assert synthetic_key not in client.get(API + "/installed").text


def test_operation_missing_from_older_dashboard(mcp):
    client, installed, _, _ = mcp
    login(client)
    installed["legacy"] = {"name": "legacy", "enabled": True}
    response = client.post(API + "/legacy/test", headers=H)
    assert response.status_code == 501
    assert "unavailable" in response.json()["detail"]
