import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import PW, PW2, login
from hermes import Hermes

KEY, TOKEN = "chat-key", "chat-token"
P = "/api/plugins/atrium-chat"
H = {"origin": "https://app.test"}
SESSIONS = {}          # id -> session dict (what the fake dashboard knows)
RUNS = {}


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

    def api_ok(self):
        return self.headers.get("Authorization") == "Bearer " + KEY

    def dash_ok(self):
        return self.headers.get("X-Hermes-Session-Token") == TOKEN

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/health":
            return self.reply(200, {"status": "ok"})
        if p.startswith("/v1/runs/"):
            run = RUNS.get(p.rsplit("/", 1)[1])
            return self.reply(200, run) if run and self.api_ok() else self.reply(404)
        if not self.dash_ok():
            return self.reply(401)
        if p == "/api/sessions":
            return self.reply(200, {"sessions": list(SESSIONS.values()), "total": len(SESSIONS), "limit": 500, "offset": 0, "storage": {}})
        if p == "/api/sessions/stats":
            return self.reply(200, {"total": len(SESSIONS)})
        if p.startswith("/api/sessions/"):
            parts = p.split("/")
            s = SESSIONS.get(parts[3])
            if not s:
                return self.reply(404)
            return self.reply(200, {"messages": [{"role": "user", "content": "hi"}]} if len(parts) > 4 else s)
        self.reply(404)

    def do_POST(self):
        p, body = self.path.split("?")[0], self.body()
        if p == "/v1/runs":
            if not self.api_ok():
                return self.reply(401)
            rid = "run_%d" % (len(RUNS) + 1)
            RUNS[rid] = {"run_id": rid, "status": "running", "session_id": body.get("session_id"), "output": None, "pending_controls": []}
            SESSIONS.setdefault(body["session_id"], {"id": body["session_id"], "title": "t", "message_count": 1})
            return self.reply(200, {"run_id": rid, "status": "started"})
        if p == "/api/audio/transcribe":
            return self.reply(200, {"transcript": "hello there", "got": body.get("mime_type")}) if self.dash_ok() else self.reply(401)
        self.reply(404)

    def do_PATCH(self):
        s = SESSIONS.get(self.path.split("?")[0].split("/")[3])
        if not self.dash_ok() or not s:
            return self.reply(404)
        s.update(self.body())
        self.reply(200, s)

    def do_DELETE(self):
        sid = self.path.split("?")[0].split("/")[3]
        if not self.dash_ok() or sid not in SESSIONS:
            return self.reply(404)
        del SESSIONS[sid]
        self.reply(200, {"ok": True})


@pytest.fixture()
def fake():
    SESSIONS.clear(); RUNS.clear()
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


@pytest.fixture()
def chat(make, cfg, fake, tmp_path):
    cfg.hermes_api_base, cfg.hermes_api_key = fake, KEY
    cfg.hermes_dashboard_base, cfg.hermes_dashboard_token = fake, TOKEN
    cfg.hermes_data_dir = str(tmp_path / "hermes-home")
    (tmp_path / "hermes-home" / "atrium").mkdir(parents=True)
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2), ("carol", "user", PW2)),
             hermes=Hermes(fake, KEY, fake, TOKEN, cfg.hermes_data_dir), config=cfg)

    def as_(user, pw, ip):
        c.post("/logout", headers=H)
        assert login(c, user, pw, ip=ip).status_code == 303
    c.as_ = as_
    return c


def send(c, sid, text="hello"):
    return c.post(P + "/send", json={"session_id": sid, "message": text}, headers=H)


def test_everything_needs_a_login(chat):
    for path in (P + "/status", "/api/sessions", "/api/sessions/atrium-chat-aaaaaa", "/api/auth/me", P + "/runs/run_1"):
        assert chat.get(path).status_code == 401, path
    assert chat.post("/api/audio/transcribe", json={}, headers=H).status_code == 401
    assert chat.post(P + "/send", json={"session_id": "atrium-chat-aaaaaa", "message": "x"}, headers=H).status_code == 401


def test_status_and_me(chat):
    chat.as_("bob", PW2, "10.5.0.1")
    assert chat.get(P + "/status").json()["ready"] is True
    me = chat.get("/api/auth/me").json()
    assert me["user"] == "bob" and me["display_name"] == "bob" and me["role"] == "user"


def test_a_chat_belongs_to_the_person_who_started_it(chat):
    chat.as_("bob", PW2, "10.5.0.2")
    r = send(chat, "atrium-chat-bobchat1")
    assert r.status_code == 200
    rid = r.json()["run_id"]
    assert RUNS[rid]["session_id"] == "atrium-chat-bobchat1"
    assert chat.app.state.chat_owners.owner("atrium-chat-bobchat1") == "bob"
    assert chat.get(P + "/runs/" + rid).status_code == 200                      # her own run
    chat.as_("carol", PW2, "10.5.0.3")
    assert send(chat, "atrium-chat-bobchat1").status_code == 403                # cannot send into bob's chat
    assert chat.get(P + "/runs/" + rid).status_code == 403                      # or read his run
    assert chat.get(P + "/stream/" + rid).status_code == 403
    assert chat.post(P + "/runs/%s/stop" % rid, headers=H).status_code == 403
    chat.as_("alice", PW, "10.5.0.4")
    assert chat.get(P + "/runs/" + rid).status_code == 200                      # an owner can look


def test_unclaimed_older_chats_are_for_owners_only(chat):
    SESSIONS["atrium-chat-oldone1"] = {"id": "atrium-chat-oldone1", "title": "old", "message_count": 4}
    chat.as_("bob", PW2, "10.5.0.5")
    assert send(chat, "atrium-chat-oldone1").status_code == 403
    assert chat.get("/api/sessions/atrium-chat-oldone1").status_code == 404
    chat.as_("alice", PW, "10.5.0.6")
    assert chat.get("/api/sessions/atrium-chat-oldone1").status_code == 200
    assert send(chat, "atrium-chat-oldone1").status_code == 200                 # and an owner can continue it


def test_session_list_is_filtered_for_plain_users_and_open_for_owners(chat):
    chat.as_("bob", PW2, "10.5.0.7")
    send(chat, "atrium-chat-bobchat2")
    chat.as_("carol", PW2, "10.5.0.8")
    send(chat, "atrium-chat-carolc2")
    SESSIONS["cron-job-1"] = {"id": "cron-job-1", "title": "cron", "message_count": 2}
    mine = chat.get("/api/sessions?limit=100&archived=include&min_messages=1&offset=0").json()
    assert [s["id"] for s in mine["sessions"]] == ["atrium-chat-carolc2"] and mine["total"] == 1
    assert chat.get("/api/sessions/stats").status_code == 403
    chat.as_("alice", PW, "10.5.0.9")
    all_ = chat.get("/api/sessions?limit=100").json()
    assert {s["id"] for s in all_["sessions"]} == {"atrium-chat-bobchat2", "atrium-chat-carolc2", "cron-job-1"}
    assert chat.get("/api/sessions/stats").json()["total"] == 3


def test_rename_delete_and_history_are_limited_to_the_owner_of_the_chat(chat):
    chat.as_("bob", PW2, "10.5.0.10")
    send(chat, "atrium-chat-bobchat3")
    assert chat.get("/api/sessions/atrium-chat-bobchat3/messages?limit=500&order=oldest").json()["messages"][0]["content"] == "hi"
    assert chat.patch("/api/sessions/atrium-chat-bobchat3", json={"title": "renamed"}, headers=H).json()["title"] == "renamed"
    assert chat.patch("/api/sessions/atrium-chat-bobchat3", json={"title": "x"}, headers={"origin": "https://evil.example"}).status_code == 403
    chat.as_("carol", PW2, "10.5.0.11")
    assert chat.patch("/api/sessions/atrium-chat-bobchat3", json={"title": "mine now"}, headers=H).status_code == 404
    assert chat.delete("/api/sessions/atrium-chat-bobchat3", headers=H).status_code == 404
    chat.as_("bob", PW2, "10.5.0.12")
    assert chat.delete("/api/sessions/atrium-chat-bobchat3", headers=H).status_code == 200
    assert chat.app.state.chat_owners.owner("atrium-chat-bobchat3") is None      # forgotten with the chat
    assert chat.get("/api/sessions/bad id!").status_code in (400, 404)


def test_transcription_is_forwarded_and_size_limited(chat):
    chat.as_("bob", PW2, "10.5.0.13")
    r = chat.post("/api/audio/transcribe", json={"data_url": "data:audio/webm;base64,AAAA", "mime_type": "audio/webm"}, headers=H)
    assert r.status_code == 200 and r.json()["transcript"] == "hello there" and r.json()["got"] == "audio/webm"
    assert chat.post("/api/audio/transcribe", content=b"x" * (15 * 1024 * 1024), headers=H).status_code == 413
    assert chat.post("/api/audio/transcribe", json={}, headers={"origin": "https://evil.example"}).status_code == 403


def test_shared_settings_are_for_owners_only(chat):
    chat.as_("bob", PW2, "10.5.0.14")
    assert chat.post(P + "/agent", json={"name": "Nova"}, headers=H).status_code == 403
    assert chat.post(P + "/voice", json={"voice": "x"}, headers=H).status_code == 403
    assert chat.get(P + "/agent").json()["name"] in ("Assistant", "Robo", "Nova")           # reading is fine
    chat.as_("alice", PW, "10.5.0.15")
    assert chat.post(P + "/agent", json={"name": "Nova"}, headers=H).status_code == 200
    assert chat.get(P + "/agent").json()["name"] == "Nova"


def test_push_and_phone_app_routes_are_off_for_now(chat):
    chat.as_("alice", PW, "10.5.0.16")
    for path in ("/push/key", "/push/status", "/sw.js", "/manifest.webmanifest", "/icon-192.png", "/settings/notify"):
        assert chat.get(P + path).status_code == 404, path


def test_bad_input_is_refused_before_anything_is_started(chat):
    chat.as_("bob", PW2, "10.5.0.17")
    assert chat.post(P + "/send", json={"session_id": "not-a-chat-id", "message": "x"}, headers=H).status_code in (400, 422)
    assert chat.post(P + "/send", json={"session_id": "atrium-chat-bobchat9", "message": "x"}, headers={"origin": "https://evil.example"}).status_code == 403
    assert not RUNS


def test_the_browser_rules_allow_recorded_audio_and_nothing_wild(chat):
    csp = chat.get("/login").headers["content-security-policy"]
    assert "media-src 'self' blob:" in csp and "img-src 'self' data: blob:" in csp
    assert "unsafe-eval" not in csp.replace("wasm-unsafe-eval", "") and "script-src 'self' 'wasm-unsafe-eval';" in csp      # scripts stay same-origin only (WebAssembly decoding aside); only styles may be inline
    assert "script-src 'self' 'wasm-unsafe-eval'" in csp


def test_chat_page_is_registered_in_the_agent_section(chat):
    chat.as_("bob", PW2, "10.5.0.18")
    cfg_js = chat.get("/shell-config.js").text
    assert '"label": "Casual chat"' in cfg_js and '"group": "Agent"' in cfg_js
    assert chat.get("/chat").status_code == 200
    assert chat.get("/static/pages/chat/index.js").status_code == 200 and chat.get("/static/pages/chat/style.css").status_code == 200


def test_without_an_approval_service_the_pending_checks_answer_none(chat):
    chat.as_("bob", PW2, "10.5.0.19")
    assert chat.get(P + "/actions").json() == {"pending": [], "recent": []}
    assert chat.get(P + "/actions/log").json() == {"entries": []}
    chat.post("/logout", headers=H)
    assert chat.get(P + "/actions").status_code == 401                     # still behind the login


R = P + "/reactions/"


def test_a_person_can_react_and_take_it_back(chat):
    chat.as_("bob", PW2, "10.6.0.1")
    send(chat, "atrium-chat-bobreact1")
    sid = "atrium-chat-bobreact1"
    assert chat.get(R + sid).json() == {"reactions": {}}
    r = chat.post(R + sid, json={"index": 1, "emoji": "\U0001F44D"}, headers=H)
    assert r.json()["reactions"] == {"1": [{"emoji": "\U0001F44D", "count": 1, "mine": True}]}
    assert chat.get(R + sid).json()["reactions"]["1"][0]["mine"] is True
    r = chat.post(R + sid, json={"index": 1, "emoji": "\U0001F44D"}, headers=H)          # the same again removes it
    assert r.json()["reactions"] == {}


def test_reactions_are_private_to_the_chat_and_checked(chat):
    chat.as_("bob", PW2, "10.6.0.2")
    sid = "atrium-chat-bobreact2"
    send(chat, sid)
    for bad in ({"index": 1, "emoji": "a"}, {"index": 1, "emoji": "<b>"}, {"index": 1, "emoji": ""}, {"index": 1, "emoji": "1"},
                {"index": -1, "emoji": "\U0001F44D"}, {"index": "x", "emoji": "\U0001F44D"}, {"index": True, "emoji": "\U0001F44D"}, {"emoji": "\U0001F44D"}):
        assert chat.post(R + sid, json=bad, headers=H).status_code == 400, bad
    assert chat.post(R + sid, json={"index": 0, "emoji": "\u2764\ufe0f"}, headers=H).status_code == 200          # a heart with its selector
    assert chat.post(R + sid, json={"index": 0, "emoji": "\U0001F44D\U0001F3FD"}, headers=H).status_code == 200   # with a skin tone
    assert chat.post(R + sid, json={"index": 1, "emoji": "\U0001F44D"}, headers={"origin": "https://evil.test"}).status_code == 403   # another site cannot
    chat.as_("carol", PW2, "10.6.0.3")
    assert chat.get(R + sid).status_code == 404                                                                   # not her chat
    assert chat.post(R + sid, json={"index": 1, "emoji": "\U0001F44D"}, headers=H).status_code == 404
    chat.as_("alice", PW, "10.6.0.4")
    assert chat.get(R + sid).status_code == 200                                                                   # owners may look
    chat.post("/logout", headers=H)
    assert chat.get(R + sid).status_code == 401


def test_one_message_holds_a_limited_number_of_kinds_and_reactions_go_with_the_chat(chat):
    chat.as_("bob", PW2, "10.6.0.5")
    sid = "atrium-chat-bobreact3"
    send(chat, sid)
    kinds = ["\U0001F600", "\U0001F601", "\U0001F602", "\U0001F603", "\U0001F604", "\U0001F605", "\U0001F606", "\U0001F607"]
    for e in kinds:
        assert chat.post(R + sid, json={"index": 0, "emoji": e}, headers=H).status_code == 200
    assert chat.post(R + sid, json={"index": 0, "emoji": "\U0001F608"}, headers=H).status_code == 400
    assert chat.delete("/api/sessions/" + sid, headers=H).status_code == 200
    assert chat.app.state.chat_reactions.get(sid, "bob") == {}


def test_voice_choice_is_listed_saved_and_checked(chat):
    chat.as_("alice", PW, "10.5.0.16")
    got = chat.get(P + "/voice").json()
    assert got["voice"] == "af_heart" and {"id": "af_bella", "label": "Bella"} in got["choices"]
    assert chat.post(P + "/voice", json={"voice": "nope"}, headers=H).status_code == 400
    assert chat.post(P + "/voice", json={"voice": "af_bella"}, headers=H).json() == {"voice": "af_bella"}
    assert chat.get(P + "/voice").json()["voice"] == "af_bella"
    assert chat.post(P + "/speak", json={"text": "   "}, headers=H).status_code in (400, 422)
