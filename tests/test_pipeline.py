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
CHECKPOINTS = []
FAIL_CHECKPOINT = [False]
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
        if p == "/git/checkpoint" and self.command == "POST":
            if FAIL_CHECKPOINT[0]:
                return reply(self, 500, {"detail": "git failed"})
            CHECKPOINTS.append(body)
            return reply(self, 200, {"ref": "refs/atrium/checkpoints/%d" % len(CHECKPOINTS)})
        reply(self, 404, {"detail": "no"})

    do_GET = do_POST = do_PUT = go


@pytest.fixture()
def pipe(make, cfg):
    CARDS.clear(); PROJECTS.clear(); FILES.clear(); CALLS.clear(); CHECKPOINTS.clear(); FAIL_CHECKPOINT[0] = False
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


# ---------------------------------------------------------------- ideas belong to a project

def run_to_plan(pipe, iid):
    v = pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()
    finish(v["cards"]["research"])
    v = pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()
    finish(v["cards"]["plan"])
    return v


def test_ideas_are_listed_per_project_and_unknown_projects_are_refused(pipe):
    PROJECTS.append("shop")
    a = pipe.post("/api/pipeline/ideas", json={"title": "Incubator idea"}, headers=H).json()
    b = pipe.post("/api/pipeline/ideas", json={"title": "Shop idea", "project": "shop"}, headers=H).json()
    assert FILES[("shop", "ideas/%s/idea.md" % b["slug"])] and ("idea-pipeline", "ideas/%s/idea.md" % a["slug"]) in FILES
    titles = lambda q: [i["title"] for i in pipe.get("/api/pipeline/ideas" + q).json()["ideas"]]
    assert titles("?project=shop") == ["Shop idea"] and titles("?project=idea-pipeline") == ["Incubator idea"] and sorted(titles("")) == sorted(titles("?project=all")) == ["Incubator idea", "Shop idea"]
    assert pipe.post("/api/pipeline/ideas", json={"title": "x", "project": "ghost"}, headers=H).status_code == 404
    assert pipe.post("/api/pipeline/ideas", json={"title": "x", "project": "../etc"}, headers=H).status_code == 400
    assert pipe.get("/api/pipeline/ideas?project=../etc").status_code == 400


def test_an_idea_in_an_existing_project_is_built_there_after_a_restore_point(pipe):
    PROJECTS.append("shop")
    iid = pipe.post("/api/pipeline/ideas", json={"title": "Dark mode", "project": "shop"}, headers=H).json()["id"]
    v = run_to_plan(pipe, iid)
    assert CARDS[v["cards"]["plan"]]["workspace_path"] == "/workspace/shop"                           # notes live in the project too
    v = pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()
    assert v["stage"] == "build" and v["project"] == "shop" and CARDS[v["cards"]["build"]]["workspace_path"] == "/workspace/shop"
    assert CHECKPOINTS == [{"project": "shop", "label": "before pipeline build: Dark mode"}] and PROJECTS == ["shop"]      # saved first, and no second project


def test_a_failed_restore_point_stops_the_build_instead_of_continuing(pipe):
    PROJECTS.append("shop")
    iid = pipe.post("/api/pipeline/ideas", json={"title": "Risky", "project": "shop"}, headers=H).json()["id"]
    run_to_plan(pipe, iid)
    cards_before = len(CARDS)
    FAIL_CHECKPOINT[0] = True
    assert pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).status_code >= 400
    got = pipe.get("/api/pipeline/ideas").json()["ideas"][0]
    assert got["stage"] == "plan" and got["next"] == "build" and len(CARDS) == cards_before         # nothing started, nothing changed
    FAIL_CHECKPOINT[0] = False
    assert pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()["stage"] == "build"


# ---------------------------------------------------------------- an interrupted step can be retried safely

def test_a_retry_after_a_lost_save_adopts_the_card_instead_of_making_a_second(pipe, monkeypatch):
    import pipeline_backend as pb
    iid = pipe.post("/api/pipeline/ideas", json={"title": "Flaky"}, headers=H).json()["id"]
    real = pb._save

    def save_that_loses_the_stage_change(d):
        if d["ideas"][iid]["stage"] == "research":
            raise OSError("disk full")                      # the card was made on the board, then the save failed
        return real(d)
    monkeypatch.setattr(pb, "_save", save_that_loses_the_stage_change)
    with pytest.raises(OSError):
        pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H)
    assert len([c for c in CARDS.values() if c["title"].startswith("Research:")]) == 1
    monkeypatch.setattr(pb, "_save", real)
    v = pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()
    research = [cid for cid, c in CARDS.items() if c["title"].startswith("Research:")]
    assert len(research) == 1 and v["cards"]["research"] == research[0]                            # the same card, adopted


def test_a_retry_of_the_build_step_reuses_the_project_it_chose(pipe, monkeypatch):
    import pipeline_backend as pb
    from fastapi import HTTPException
    iid = pipe.post("/api/pipeline/ideas", json={"title": "Wiki"}, headers=H).json()["id"]
    run_to_plan(pipe, iid)
    real, fails = pb._create_card, [True]

    async def card_that_fails_once(stage, e, body, i):
        if stage == "build" and fails[0]:
            fails[0] = False
            raise HTTPException(status_code=502, detail="The board did not answer.")
        return await real(stage, e, body, i)
    monkeypatch.setattr(pb, "_create_card", card_that_fails_once)
    assert pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).status_code == 502
    made = [p for p in PROJECTS if p.startswith("wiki-")]
    assert len(made) == 1                                                                           # the project exists, the card does not
    v = pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()
    assert [p for p in PROJECTS if p.startswith("wiki-")] == made and v["project"] == made[0]       # no "-2" duplicate
    assert CARDS[v["cards"]["build"]]["workspace_path"] == "/workspace/" + made[0]


def test_when_approve_everything_cannot_continue_it_says_so(pipe, monkeypatch):
    import pipeline_backend as pb
    from fastapi import HTTPException
    iid = pipe.post("/api/pipeline/ideas", json={"title": "Auto", "auto": True}, headers=H).json()["id"]
    v = pipe.post("/api/pipeline/ideas/%s/advance" % iid, headers=H).json()
    finish(v["cards"]["research"])
    real = pb._advance

    async def broken(i, by_auto=False):
        raise HTTPException(status_code=502, detail="The board did not answer.")
    monkeypatch.setattr(pb, "_advance", broken)
    assert asyncio.run(pb.chain_once()) == 0
    got = pipe.get("/api/pipeline/ideas").json()["ideas"][0]
    assert got["auto_error"]["detail"] == "The board did not answer."                               # visible on the idea, not a silent stall
    monkeypatch.setattr(pb, "_advance", real)
    assert asyncio.run(pb.chain_once()) == 1
    assert pipe.get("/api/pipeline/ideas").json()["ideas"][0]["auto_error"] is None
