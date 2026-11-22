import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import PW, PW2, login

H = {"origin": "https://app.test"}
TOKEN = "sidecar-secret"
SEEN = []
ROWS = {}                      # session rows per source ("api_server", "kanban", "subagent") for the paging and related-work tests


class Sidecar(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        if self.path.startswith("/upload"):
            raw = self.rfile.read(n)
            if self.headers.get("X-Code-Token") != TOKEN:
                return self.reply(401, {"detail": "Not allowed."})
            SEEN.append((self.command, self.path, raw))
            return self.reply(200, {"path": "attachments/" + self.path.split("name=")[1].split("&")[0], "size": len(raw)})
        body = json.loads(self.rfile.read(n)) if n else None
        if self.path.startswith("/healthz"):
            return self.reply(200, {"ok": True})
        if self.headers.get("X-Code-Token") != TOKEN:
            return self.reply(401, {"detail": "Not allowed."})
        SEEN.append((self.command, self.path, body))
        if self.path.startswith("/projects") and self.command == "GET":
            return self.reply(200, {"projects": [{"name": "shop", "git": True}], "workspace": "/workspace"})
        if self.path.startswith("/git/status"):
            return self.reply(200, {"git": True, "branch": "main", "changes": []})
        if self.path.startswith("/git/checkpoint"):
            return self.reply(200, {"ref": "refs/atrium/checkpoints/1", "label": "x", "commit": "abc"})
        if self.path.startswith("/files"):
            return self.reply(200, {"files": ["README.md", "src/app.py"], "more": False})
        if self.path.startswith("/file"):
            return self.reply(200, {"text": "hello\n", "size": 6})
        return self.reply(404, {"detail": "No such project."})

    do_GET = do_POST = do_PUT = handle_any


class FakeHermes:
    async def dash_call(self, method, path, body=None, timeout=15):
        if path.endswith("/messages"):
            return 200, {"messages": [
                {"id": "1", "role": "user", "content": "make it blue"},
                {"id": "2", "role": "assistant", "content": "", "tool_calls": json.dumps([{"call_id": "c1", "function": {"name": "terminal", "arguments": "{\"command\":\"ls\"}"}}])},
                {"id": "3", "role": "tool", "content": "a.py", "tool_name": "terminal", "tool_call_id": "c1"},
                {"id": "4", "role": "assistant", "content": "Done."}, {"id": "5", "role": "system", "content": "hidden"}]}
        if path.startswith("/api/sessions") and ROWS:
            from urllib.parse import parse_qs, urlsplit
            q = parse_qs(urlsplit(path).query)
            rows, off, lim = ROWS.get((q.get("source") or [""])[0], []), int((q.get("offset") or ["0"])[0]), int((q.get("limit") or ["100"])[0])
            return 200, {"sessions": rows[off:off + min(lim, 100)]}
        if path.startswith("/api/sessions"):
            return 200, {"sessions": [{"id": "atrium-chat-codeabc123", "title": "Make it blue", "message_count": 4, "last_active": 1}, {"id": "atrium-chat-other12", "title": "Chat"}]}
        return 404, {}

    async def dash_json(self, path):
        return 200, {}


@pytest.fixture()
def code(make, cfg, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Sidecar)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg.code_url, cfg.code_token = "http://127.0.0.1:%d" % srv.server_address[1], TOKEN
    SEEN.clear()
    ROWS.clear()
    import chat_backend
    calls = []

    async def fake_call(method, path, payload=None):
        calls.append((method, path, payload))
        return 200, {"run_id": "run_9", "status": "started"}
    monkeypatch.setattr(chat_backend, "_call", fake_call)
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), hermes=FakeHermes(), config=cfg)
    c.calls = calls

    def as_(user, pw, ip):
        c.post("/logout", headers=H)
        assert login(c, user, pw, ip=ip).status_code == 303
    c.as_ = as_
    c.as_("alice", PW, "10.8.0.1")
    yield c
    srv.shutdown()


def test_only_owners_and_only_when_set_up(code, make, cfg):
    code.as_("bob", PW2, "10.8.0.2")
    for path in ("/status", "/projects", "/tree?project=shop", "/sessions"):
        assert code.get("/api/code" + path).status_code == 403, path
    assert code.post("/api/code/send", json={"project": "shop", "message": "hi"}, headers=H).status_code == 403
    code.post("/logout", headers=H)
    assert code.get("/api/code/projects").status_code == 401
    import dataclasses
    bare = dataclasses.replace(cfg, data_dir=cfg.data_dir / "bare", code_url="")
    other = make(users=(("alice", "owner", PW),), config=bare)
    assert login(other, "alice", PW, ip="10.8.0.9").status_code == 303
    assert other.get("/api/code/status").json() == {"configured": False, "up": False}
    assert other.get("/api/code/projects").status_code == 503


def test_requests_reach_the_workspace_with_the_secret(code):
    assert code.get("/api/code/status").json() == {"configured": True, "up": True}
    assert code.get("/api/code/projects").json()["projects"][0]["name"] == "shop"
    assert code.get("/api/code/file", params={"project": "shop", "path": "a.txt"}).json()["text"] == "hello\n"
    assert any(p.startswith("/file?project=shop&path=a.txt") for _, p, _ in SEEN)
    assert code.get("/api/code/files", params={"project": "shop"}).json()["files"] == ["README.md", "src/app.py"]      # the list behind @mentions
    assert code.put("/api/code/file", json={"project": "shop", "path": "a.txt", "text": "x"}, headers=H).status_code == 200
    assert code.put("/api/code/file", json={"project": "shop", "path": "a.txt", "text": "x"}, headers={"origin": "https://evil.example"}).status_code == 403
    assert code.post("/api/code/git/rewind", json={"project": "nope", "ref": "r"}, headers=H).status_code == 404


def test_a_message_saves_a_checkpoint_then_starts_a_run_in_the_project(code):
    r = code.post("/api/code/send", json={"project": "shop", "message": "make the header blue"}, headers=H).json()
    assert r["run_id"] == "run_9" and r["session_id"].startswith("atrium-chat-code") and r["checkpoint"] == "refs/atrium/checkpoints/1"
    cp = [b for c, p, b in SEEN if p == "/git/checkpoint"]
    assert cp == [{"project": "shop", "label": "make the header blue"}]
    method, path, payload = code.calls[0]
    assert (method, path) == ("POST", "/v1/runs") and payload["input"] == "make the header blue" and payload["session_id"] == r["session_id"]
    assert "/workspace/shop" in payload["instructions"]
    assert "starts with @" in payload["instructions"]                                   # how the agent learns what an @file mention means
    again = code.post("/api/code/send", json={"project": "shop", "message": "and bigger", "session_id": r["session_id"]}, headers=H).json()
    assert again["session_id"] == r["session_id"]
    for bad in ({"project": "../etc", "message": "x"}, {"project": "shop", "message": "  "}, {"project": "shop", "message": "x" * 12001}):
        assert code.post("/api/code/send", json=bad, headers=H).status_code == 400, bad


def test_transcript_and_session_list(code):
    m = code.get("/api/code/messages", params={"session_id": "atrium-chat-codeabc123"}).json()["messages"]
    assert [x["role"] for x in m] == ["user", "assistant", "tool", "assistant"]            # system rows are hidden
    assert m[1]["tool_calls"][0] == {"id": "c1", "name": "terminal", "arguments": "{\"command\":\"ls\"}"} and m[2]["tool_call_id"] == "c1"
    assert code.get("/api/code/messages", params={"session_id": "atrium-chat-other12"}).status_code == 400
    assert [s["id"] for s in code.get("/api/code/sessions").json()["sessions"]] == ["atrium-chat-codeabc123"]


def test_attached_files_are_passed_to_the_workspace(code):
    r = code.post("/api/code/upload", params={"project": "shop", "name": "notes.pdf"}, content=b"%PDF-1.4 data", headers={**H, "content-type": "application/octet-stream"})
    assert r.status_code == 200 and r.json() == {"path": "attachments/notes.pdf", "size": 13}
    assert ("POST", "/upload?project=shop&name=notes.pdf", b"%PDF-1.4 data") in SEEN
    assert code.post("/api/code/upload", params={"project": "../x", "name": "a"}, content=b"x", headers=H).status_code == 400
    assert code.post("/api/code/upload", params={"project": "shop", "name": "a"}, content=b"x", headers={"origin": "https://evil.example"}).status_code == 403
    code.as_("bob", PW2, "10.8.0.5")
    assert code.post("/api/code/upload", params={"project": "shop", "name": "a"}, content=b"x", headers=H).status_code == 403
    code.as_("alice", PW, "10.8.0.6")
    sent = code.post("/api/code/send", json={"project": "shop", "message": "look\n\nAttached: @attachments/notes.pdf"}, headers=H).json()
    assert "attachments" in code.calls[-1][2]["instructions"] and sent["run_id"] == "run_9"


# ---------------------------------------------------------------- sessions belong to projects; a card's worker runs show as related work

def coding(i, **k):
    return dict({"id": "atrium-chat-code%06d" % i, "title": "Session %d" % i, "message_count": 2, "last_active": 1000 - i, "source": "api_server"}, **k)


def test_a_session_is_listed_under_its_own_project_only(code):
    sid = code.post("/api/code/send", json={"project": "shop", "message": "hello"}, headers=H).json()["session_id"]
    ROWS["api_server"] = [coding(1, id=sid), coding(2)]                   # session 2 predates projects: it was never tied to one
    mine = {s["id"]: s for s in code.get("/api/code/sessions", params={"project": "shop"}).json()["sessions"]}
    assert mine[sid]["legacy"] is False and mine["atrium-chat-code000002"]["legacy"] is True
    other = {s["id"] for s in code.get("/api/code/sessions", params={"project": "blog"}).json()["sessions"]}
    assert sid not in other and "atrium-chat-code000002" in other         # older sessions stay reachable from every project
    assert code.get("/api/code/sessions", params={"project": "../x"}).status_code == 400


def test_old_sessions_are_not_lost_behind_the_scheduled_check_ins(code):
    noise = [{"id": "cron-%d" % i, "source": "api_server", "message_count": 0, "last_active": 5000 - i} for i in range(120)]      # empty check-ins fill the first pages
    ROWS["api_server"] = noise + [coding(7, last_active=1)]
    ids = [s["id"] for s in code.get("/api/code/sessions", params={"project": "shop"}).json()["sessions"]]
    assert ids == ["atrium-chat-code000007"]


def test_related_work_comes_from_the_linked_card_and_is_read_only(code, make, cfg):
    import dataclasses
    assert code.put("/api/code/links", json={"project": "shop", "card": "t_032dcd3a", "title": "Fix the header"}, headers=H).status_code == 200
    ROWS["kanban"] = [{"id": "local-0123456789abcdef0123", "source": "kanban", "cwd": "/opt/data/kanban/workspaces/t_032dcd3a", "message_count": 5, "last_active": 9},
                      {"id": "local-aaaaaaaaaaaaaaaaaaaa", "source": "kanban", "cwd": "/opt/data/kanban/workspaces/t_other999", "message_count": 5, "last_active": 8},
                      {"id": "local-bbbbbbbbbbbbbbbbbbbb", "source": "kanban", "cwd": "/opt/data/kanban/workspaces/t_032dcd3a", "message_count": 0, "last_active": 7}]
    ROWS["subagent"] = [{"id": "20261121_101500_abcdef12", "source": "subagent", "title": "Check the CSS", "cwd": "/opt/data/kanban/workspaces/t_032dcd3a", "message_count": 3, "last_active": 6}]
    got = code.get("/api/code/sessions", params={"project": "shop"}).json()
    assert [r["id"] for r in got["related"]] == ["local-0123456789abcdef0123", "20261121_101500_abcdef12"]       # not the other card, not an empty run
    assert got["related"][0]["title"] == "Worker run - Fix the header" and got["link"]["card"] == "t_032dcd3a"
    assert code.get("/api/code/messages", params={"session_id": "local-0123456789abcdef0123"}).status_code == 200      # readable
    assert code.get("/api/code/messages", params={"session_id": "anything-else"}).status_code == 400                   # but not any session id
    sent = code.post("/api/code/send", json={"project": "shop", "message": "x", "session_id": "local-0123456789abcdef0123"}, headers=H).json()
    assert sent["session_id"] != "local-0123456789abcdef0123" and sent["session_id"].startswith("atrium-chat-code")      # a worker run is never continued: a fresh session starts
    assert code.calls[-1][2]["session_id"] == sent["session_id"]
    # where the agent keeps card workspaces is a setting, not a built-in path
    custom = make(users=(("alice", "owner", PW),), hermes=FakeHermes(), config=dataclasses.replace(cfg, data_dir=cfg.data_dir / "k", kanban_workspaces_dir="/srv/agent/cards/"))
    assert login(custom, "alice", PW, ip="10.8.0.31").status_code == 303
    custom.put("/api/code/links", json={"project": "shop", "card": "t_032dcd3a"}, headers=H)
    ROWS["kanban"] = [{"id": "local-0123456789abcdef0123", "source": "kanban", "cwd": "/srv/agent/cards/t_032dcd3a", "message_count": 5, "last_active": 9}]
    ROWS["subagent"] = []
    assert [r["id"] for r in custom.get("/api/code/sessions", params={"project": "shop"}).json()["related"]] == ["local-0123456789abcdef0123"]


def test_links_are_validated_and_can_be_removed(code):
    for bad in ({"project": "shop", "card": "not-a-card"}, {"project": "", "card": "t_032dcd3a"}, {"project": "../x", "card": "t_032dcd3a"}):
        assert code.put("/api/code/links", json=bad, headers=H).status_code == 400, bad
    assert code.put("/api/code/links", json={"project": "shop", "card": "t_032dcd3a"}, headers={"origin": "https://evil.example"}).status_code == 403
    code.put("/api/code/links", json={"project": "shop", "card": "t_032dcd3a"}, headers=H)
    assert code.get("/api/code/sessions", params={"project": "shop"}).json()["link"]["card"] == "t_032dcd3a"
    code.put("/api/code/links", json={"project": "shop", "card": None}, headers=H)
    assert code.get("/api/code/sessions", params={"project": "shop"}).json()["link"] is None
    code.as_("bob", PW2, "10.8.0.32")
    assert code.put("/api/code/links", json={"project": "shop", "card": "t_032dcd3a"}, headers=H).status_code == 403
