import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import PW, PW2, login
from hermes import Hermes

TOKEN = "pipe-token"
CODE_TOKEN = "code-secret"
H = {"origin": "https://app.test"}
CARDS = {}
PROJECTS = []
FILES = {}
CALLS = []
FLOW = [{"key": k, "label": k.title(), "icon": "*", "drag_target": k not in ("running", "scheduled")}
        for k in ("triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done")]


def reply(h, code, obj):
    b = json.dumps(obj).encode()
    h.send_response(code); h.send_header("Content-Type", "application/json"); h.send_header("Content-Length", str(len(b))); h.end_headers(); h.wfile.write(b)


class Dash(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        if self.headers.get("X-Hermes-Session-Token") != TOKEN:
            return reply(self, 401, {})
        p = self.path.split("?")[0]
        CALLS.append((self.command, p, body))
        if p == "/api/plugins/kanban/board":
            cols = {}
            for c in CARDS.values():
                cols.setdefault(c["status"], []).append(c)
            return reply(self, 200, {"columns": [{"name": k, "tasks": v} for k, v in cols.items()], "now": 1})
        if p == "/api/plugins/kanban/workflow":
            return reply(self, 200, {"columns": FLOW})
        if p == "/api/plugins/kanban/orchestration":
            return reply(self, 200, {"resolved_default_assignee": "worker-a", "auto_decompose": False})
        if p == "/api/plugins/kanban/assignees":
            return reply(self, 200, {"assignees": [{"name": "worker-a"}]})
        if p == "/api/plugins/kanban/tasks" and self.command == "POST":
            cid = "t_%06x" % (len(CARDS) + 1)
            CARDS[cid] = {"id": cid, "title": body["title"], "body": body.get("body"), "assignee": body.get("assignee"), "status": "triage" if body.get("triage") else "ready",
                          "workspace_kind": body.get("workspace_kind"), "workspace_path": body.get("workspace_path"), "priority": body.get("priority", 0)}
            return reply(self, 200, {"task": CARDS[cid]})
        if p.startswith("/api/plugins/kanban/tasks/") and self.command == "PATCH":
            CARDS[p.rsplit("/", 1)[-1]].update({k: v for k, v in body.items() if k in ("status", "title")})
            return reply(self, 200, {"ok": True})
        reply(self, 404, {})

    do_GET = do_POST = do_PATCH = go


class Sidecar(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        if self.headers.get("X-Code-Token") != CODE_TOKEN:
            return reply(self, 401, {"detail": "no"})
        p = self.path.split("?")[0]
        if p == "/projects" and self.command == "GET":
            return reply(self, 200, {"projects": [{"name": x} for x in PROJECTS]})
        if p == "/projects" and self.command == "POST":
            if body["name"] in PROJECTS:
                return reply(self, 409, {"detail": "exists"})
            PROJECTS.append(body["name"])
            return reply(self, 200, {"ok": True})
        if p == "/file" and self.command == "PUT":
            FILES[(body["project"], body["path"])] = body["text"]
            return reply(self, 200, {"ok": True})
        reply(self, 404, {"detail": "no"})

    do_GET = do_POST = do_PUT = go


@pytest.fixture()
def pipe(make, cfg):
    CARDS.clear(); PROJECTS.clear(); FILES.clear(); CALLS.clear()
    d, s = HTTPServer(("127.0.0.1", 0), Dash), HTTPServer(("127.0.0.1", 0), Sidecar)
    for srv in (d, s):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % d.server_address[1]
    cfg.hermes_dashboard_base, cfg.hermes_dashboard_token = base, TOKEN
    cfg.code_url, cfg.code_token = "http://127.0.0.1:%d" % s.server_address[1], CODE_TOKEN
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), hermes=Hermes("", "", base, TOKEN, ""), config=cfg)
    assert login(c, "alice", PW, ip="10.9.1.1").status_code == 303
    yield c
    d.shutdown(); s.shutdown()


def finish(cid):
    CARDS[cid]["status"] = "done"


def test_capture_creates_a_waiting_card_a_notes_project_and_a_file(pipe):
    r = pipe.post("/api/pipeline/ideas", json={"title": "Playlist cleaner", "body": "Clean M3U files in the browser."}, headers=H).json()
    card = CARDS[r["id"]]
    assert card["status"] == "triage" and card["assignee"] is None                         # an idea waits: nobody is assigned
    assert "idea-pipeline" in PROJECTS
    path = "ideas/%s/idea.md" % r["slug"]
    assert path.startswith("ideas/playlist-cleaner-") and "Clean M3U" in FILES[("idea-pipeline", path)]
    d = pipe.get("/api/pipeline/ideas").json()
    i = d["ideas"][0]
    assert (i["stage"], i["state"], i["next"], i["auto"]) == ("idea", "waiting", "research", False)


def test_each_stage_is_a_new_card_and_waits_for_the_one_before(pipe):
    iid = pipe.post("/api/pipeline/ideas", json={"title": "Wiki"}, headers=H).json()["id"]
    v = pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()
    assert (v["stage"], v["state"]) == ("research", "working")
    rc = CARDS[v["cards"]["research"]]
    assert rc["assignee"] == "worker-a" and rc["workspace_kind"] == "dir" and rc["workspace_path"] == "/workspace/idea-pipeline" and rc["title"].startswith("Research:")
    assert "research.md" in rc["body"] and "Do not build anything" in rc["body"]
    assert pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).status_code == 409            # still working
    finish(v["cards"]["research"])
    v = pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()
    assert v["stage"] == "plan" and "plan.md" in CARDS[v["cards"]["plan"]]["body"]
    finish(v["cards"]["plan"])
    v = pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()
    assert v["stage"] == "build" and v["project"] in PROJECTS and v["project"].startswith("wiki-")        # its own coding project, in the menu
    bc = CARDS[v["cards"]["build"]]
    assert bc["workspace_path"] == "/workspace/" + v["project"] and "plan.md" in bc["body"]
    finish(v["cards"]["build"])
    v = pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()
    assert v["stage"] == "review" and CARDS[v["cards"]["review"]]["workspace_path"] == "/workspace/idea-pipeline"
    finish(v["cards"]["review"])
    v = pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()
    assert (v["stage"], v["state"], v["next"]) == ("done", "finished", None) and CARDS[iid]["status"] == "done"


def test_a_blocked_stage_does_not_advance(pipe):
    iid = pipe.post("/api/pipeline/ideas", json={"title": "Stuck"}, headers=H).json()["id"]
    v = pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()
    CARDS[v["cards"]["research"]]["status"] = "blocked"
    got = pipe.get("/api/pipeline/ideas").json()["ideas"][0]
    assert got["state"] == "blocked" and got["next"] is None
    assert pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).status_code == 409


def test_approve_everything_chains_only_after_the_first_stage_was_started(pipe):
    import pipeline_backend as pb
    iid = pipe.post("/api/pipeline/ideas", json={"title": "Auto", "auto": True}, headers=H).json()["id"]
    assert asyncio.run(pb.chain_once()) == 0                                           # capture alone never starts anything
    assert len(CARDS) == 1
    v = pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()           # the owner starts research
    assert asyncio.run(pb.chain_once()) == 0                                           # still running
    finish(v["cards"]["research"])
    assert asyncio.run(pb.chain_once()) == 1                                           # now plan follows by itself
    assert pipe.get("/api/pipeline/ideas").json()["ideas"][0]["stage"] == "plan"
    pipe.post("/api/pipeline/ideas/%s/auto" % iid, json={"on": False}, headers=H)
    finish(pipe.get("/api/pipeline/ideas").json()["ideas"][0]["cards"]["plan"])
    assert asyncio.run(pb.chain_once()) == 0                                           # Ask: waits for a click


def test_only_owners_and_bad_ids(pipe):
    assert pipe.post("/api/pipeline/ideas/nope/advance", headers=H).status_code == 400
    assert pipe.post("/api/pipeline/ideas/t_zzzzzz/advance", headers=H).status_code == 404
    pipe.post("/logout", headers=H)
    assert login(pipe, "bob", PW2, ip="10.9.1.2").status_code == 303
    assert pipe.get("/api/pipeline/ideas").status_code == 403
    assert pipe.post("/api/pipeline/ideas", json={"title": "x"}, headers=H).status_code == 403
