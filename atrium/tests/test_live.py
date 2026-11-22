import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import PW, PW2, login

H = {"origin": "https://app.test"}
A = "/api/plugins/atrium-avatar"


class Voice(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Voice.last = json.loads(self.rfile.read(n))
        body = json.dumps({"audio": base64.b64encode(b"fake-mp3").decode(), "timestamps": [
            {"word": "Hello", "start_time": 0.0, "end_time": 0.4}, {"word": ".", "start_time": 0.4, "end_time": 0.5}, {"word": "world", "start_time": 0.5, "end_time": 1.0}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


@pytest.fixture()
def live(make, cfg, tmp_path, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Voice)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("TTS_URL", "http://127.0.0.1:%d/v1/audio/speech" % srv.server_address[1])
    home = tmp_path / "home"
    (home / "atrium" / "avatar" / "clips").mkdir(parents=True)
    (home / "obsidian-vault" / "Projects").mkdir(parents=True)
    cfg.hermes_data_dir = str(home)
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), config=cfg)
    c.home = home

    def as_(user, pw, ip):
        c.post("/logout", headers=H)
        assert login(c, user, pw, ip=ip).status_code == 303
    c.as_ = as_
    c.as_("alice", PW, "10.8.0.1")
    yield c
    srv.shutdown()


def test_only_owners_reach_the_stage(live):
    live.post("/logout", headers=H)
    assert live.get(A + "/status").status_code == 401
    live.as_("bob", PW2, "10.8.0.2")
    for path in ("/status", "/model", "/projects", "/clips/ual1"):
        assert live.get(A + path).status_code == 403, path
    assert live.post(A + "/speak", json={"text": "hi"}, headers=H).status_code == 403


def test_the_character_file_and_clips(live):
    s = live.get(A + "/status").json()
    assert s["model"] is False and s["voice_up"] is True and s["model_hint"] == "atrium/avatar/avatar.glb"
    assert live.get(A + "/model").status_code == 404
    (live.home / "atrium" / "avatar" / "avatar.glb").write_bytes(b"glTF-fake")
    (live.home / "atrium" / "avatar" / "clips" / "ual1.glb").write_bytes(b"glTF-clips")
    assert live.get(A + "/status").json()["model"] is True
    r = live.get(A + "/model")
    assert r.status_code == 200 and r.content == b"glTF-fake" and r.headers["content-type"] == "model/gltf-binary"
    assert live.get(A + "/clips/ual1").content == b"glTF-clips"
    assert live.get(A + "/clips/nope").status_code == 404
    assert live.get(A + "/clips/..%2F..%2Fx").status_code in (400, 404)


def test_projects_become_notes(live):
    p = live.home / "obsidian-vault" / "Projects"
    (p / "Shop.md").write_text("---\nstatus: blocked\n---\n# Shop\n\nOpen the **shop** for the holidays\n")
    (p / "Idea.md").write_text("A plain note without front matter\n")
    notes = {n["name"]: n for n in live.get(A + "/projects").json()["notes"]}
    assert notes["Shop"]["status"] == "blocked" and notes["Shop"]["summary"] == "Open the shop for the holidays"
    assert notes["Idea"]["status"] == "active"


def test_speech_comes_back_with_word_times(live):
    r = live.post(A + "/speak", json={"text": "Hello **world** `code` [link](http://x)"}, headers=H).json()
    assert base64.b64decode(r["audio"]) == b"fake-mp3" and r["voice"] == "af_heart"
    assert r["words"] == [{"w": "Hello", "start": 0, "end": 400}, {"w": "world", "start": 500, "end": 1000}]        # bare punctuation dropped
    assert Voice.last["input"].startswith("Hello world code link") and Voice.last["return_timestamps"] is True
    assert live.post(A + "/speak", json={"text": "```only code```"}, headers=H).status_code == 400
    assert live.post(A + "/speak", json={"text": "hi"}, headers={"origin": "https://evil.example"}).status_code == 403
