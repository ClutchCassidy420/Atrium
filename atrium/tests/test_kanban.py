import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import PW, PW2, login
from hermes import Hermes

TOKEN = "kanban-token"
H = {"origin": "https://app.test"}
CALLS = []
FLOW = [{"key": k, "label": k.title(), "icon": "*", "drag_target": k not in ("running", "scheduled")}
        for k in ("triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done")]


def task(i, title, status, **kw):
    d = {"id": "t_%08x" % i, "title": title, "body": "Some **long** body " * 40, "assignee": None, "status": status, "priority": 0, "progress": None, "comment_count": 2,
         "link_counts": {"parents": 0, "children": 0}, "created_at": 1794594331, "started_at": None, "completed_at": None, "latest_summary": "did a thing",
         "block_kind": None, "current_run_id": None, "consecutive_failures": 0, "last_failure_error": None, "workspace_path": "/secret/path", "claim_lock": "x"}
    d.update(kw)
    return d


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

    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        if self.headers.get("X-Hermes-Session-Token") != TOKEN:
            return self.reply(401)
        p = self.path.split("?")[0]
        CALLS.append((self.command, self.path, body))
        if p == "/api/plugins/kanban/board" and self.command == "GET":
            return self.reply(200, {"columns": [{"name": "triage", "tasks": [task(1, "An idea", "triage")]}, {"name": "running", "tasks": [task(2, "Busy", "running", current_run_id="r1")]},
                                                {"name": "weird", "tasks": [task(3, "Odd", "weird")]}, {"name": "done", "tasks": []}], "now": 1794600000})
        if p == "/api/plugins/kanban/workflow":
            return self.reply(200, {"columns": FLOW})
        if p == "/api/plugins/kanban/orchestration":
            return self.reply(200, {"resolved_default_assignee": "worker-a", "auto_decompose": False})
        if p == "/api/plugins/kanban/assignees":
            return self.reply(200, {"assignees": [{"name": "worker-a"}]})
        if p == "/api/plugins/kanban/tasks" and self.command == "POST":
            return self.reply(200, {"task": task(9, body["title"], "triage")})
        if p.endswith("/specify"):
            return self.reply(200, {"ok": False, "reason": "no spec"} if "t_00000bad" in p else {"ok": True})
        if p.endswith("/log"):
            return self.reply(200, {"exists": True, "content": "line\n" * 5, "size_bytes": 25})
        if p.startswith("/api/plugins/kanban/tasks/t_00000404"):
            return self.reply(404, {"detail": "nope"})
        if p.startswith("/api/plugins/kanban/tasks/") and self.command == "GET":
            return self.reply(200, {"task": task(1, "An idea", "triage"), "comments": [{"id": i, "body": "c%d" % i} for i in range(80)], "events": list(range(100)),
                                    "runs": [], "links": {}, "attachments": []})
        return self.reply(200, {"ok": True})

    do_GET = do_POST = do_PATCH = do_DELETE = handle_any


@pytest.fixture()
def board(make, cfg):
    srv = HTTPServer(("127.0.0.1", 0), Dash)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    cfg.hermes_dashboard_base, cfg.hermes_dashboard_token = base, TOKEN
    CALLS.clear()
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), hermes=Hermes("", "", base, TOKEN, ""), config=cfg)
    assert login(c, "alice", PW, ip="10.9.0.1").status_code == 303
    c.base = base
    yield c
    srv.shutdown()


def test_the_board_comes_back_in_workflow_order_with_slim_cards(board):
    d = board.get("/api/kanban/board").json()
    assert [c["key"] for c in d["columns"]][:3] == ["triage", "todo", "scheduled"] and d["columns"][-1]["key"] == "weird"
    run = next(c for c in d["columns"] if c["key"] == "running")
    assert run["drop"] is False and run["tasks"][0]["running"] is True
    card = next(c for c in d["columns"] if c["key"] == "triage")["tasks"][0]
    assert len(card["preview"]) <= 220 and "workspace_path" not in card and "claim_lock" not in card
    assert d["starter"] == "worker-a" and d["assignees"] == ["worker-a"] and d["auto_decompose"] is False


def test_only_owners_may_touch_the_board(board):
    board.post("/logout", headers=H)
    assert login(board, "bob", PW2, ip="10.9.0.2").status_code == 303
    assert board.get("/api/kanban/board").status_code == 403
    assert board.post("/api/kanban/tasks", json={"title": "x"}, headers=H).status_code == 403
    assert board.get("/kanban").status_code == 403
    board.post("/logout", headers=H)
    assert board.get("/api/kanban/board").status_code == 401


def test_a_card_has_its_detail_trimmed(board):
    d = board.get("/api/kanban/tasks/t_00000001").json()
    assert len(d["comments"]) == 60 and len(d["events"]) == 40
    assert board.get("/api/kanban/tasks/t_00000404").status_code == 404
    assert board.get("/api/kanban/tasks/..%2Fetc").status_code in (400, 404)
    assert board.get("/api/kanban/tasks/t_00000001/log").json()["content"].count("line") == 5


def test_capture_edit_move_comment_delete(board):
    r = board.post("/api/kanban/tasks", json={"title": "  New idea  ", "body": "notes"}, headers=H)
    assert r.status_code == 200
    created = [b for m, p, b in CALLS if m == "POST" and p == "/api/plugins/kanban/tasks"][0]
    assert created == {"title": "New idea", "body": "notes", "priority": 0, "triage": True}               # an idea waits: no assignee
    assert board.post("/api/kanban/tasks", json={"title": ""}, headers=H).status_code == 422
    board.post("/api/kanban/tasks", json={"title": "In a project", "project": "shop"}, headers=H)
    kept = [b for m, p, b in CALLS if m == "POST" and p == "/api/plugins/kanban/tasks"][-1]
    assert kept["workspace_kind"] == "dir" and kept["workspace_path"] == "/workspace/shop"                # kept after the card completes
    assert board.post("/api/kanban/tasks", json={"title": "x", "project": "../etc"}, headers=H).status_code == 400
    assert board.patch("/api/kanban/tasks/t_00000001", json={"status": "done", "summary": "Finished; checked by hand."}, headers=H).status_code == 200
    assert ("PATCH", "/api/plugins/kanban/tasks/t_00000001", {"status": "done", "summary": "Finished; checked by hand."}) in CALLS
    assert board.patch("/api/kanban/tasks/t_00000001", json={"status": "ready", "priority": 2}, headers=H).status_code == 200
    assert ("PATCH", "/api/plugins/kanban/tasks/t_00000001", {"status": "ready", "priority": 2}) in CALLS
    assert board.patch("/api/kanban/tasks/t_00000001", json={"status": "running"}, headers=H).status_code == 400      # not a drop target
    assert board.patch("/api/kanban/tasks/t_00000001", json={"status": "no-such"}, headers=H).status_code == 400
    assert board.patch("/api/kanban/tasks/t_00000001", json={}, headers=H).status_code == 400
    assert board.post("/api/kanban/tasks/t_00000001/comments", json={"body": "hello"}, headers=H).status_code == 200
    assert ("POST", "/api/plugins/kanban/tasks/t_00000001/comments", {"body": "hello", "author": "alice"}) in CALLS
    assert board.delete("/api/kanban/tasks/t_00000001", headers=H).status_code == 200
    assert board.delete("/api/kanban/tasks/t_00000001", headers={"origin": "https://evil.example"}).status_code == 403


def test_start_with_the_agent_writes_a_spec_then_assigns(board):
    r = board.post("/api/kanban/tasks/t_00000001/start", headers=H).json()
    assert r == {"ok": True, "assignee": "worker-a"}
    order = [(m, p.rsplit("/", 1)[-1]) for m, p, _ in CALLS if "/tasks/t_00000001" in p]
    assert order == [("POST", "specify"), ("PATCH", "t_00000001")]
    assert ("PATCH", "/api/plugins/kanban/tasks/t_00000001", {"assignee": "worker-a"}) in CALLS
    CALLS.clear()
    bad = board.post("/api/kanban/tasks/t_00000bad/start", headers=H).json()
    assert bad["ok"] is False and not any(m == "PATCH" for m, _, _ in CALLS)                              # no spec, so nobody is assigned
