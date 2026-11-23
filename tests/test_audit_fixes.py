"""Fixes from the security audit: sessions that end when a password changes, a damaged sessions file, chat and run ownership that fails closed,
approval records for owners only, and a cheaper upload check. Synthetic only."""
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import pytest

import auth
from conftest import PW, PW2, fast_hash, login
from main import COOKIE
from users import UserStore

H = {"origin": "https://app.test"}
CHAT = "/api/plugins/atrium-chat"


# ---------------------------------------------------------------- sessions

def test_a_password_changed_outside_the_running_app_ends_that_persons_sessions(make, cfg):
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)))
    assert login(c, "alice", PW, ip="10.3.0.1").status_code == 303
    assert c.get("/api/me").status_code == 200
    UserStore(cfg.data_dir / "users.json").set_password("alice", "a brand new long passphrase")         # the command-line tool: it never tells the running app
    assert c.get("/api/me").status_code == 401, "the old sign-in must not survive its password"
    assert login(c, "alice", "a brand new long passphrase", ip="10.3.0.2").status_code == 303 and c.get("/api/me").status_code == 200


def test_changing_one_persons_password_leaves_everyone_else_signed_in(make, cfg):
    a, b = make(users=(("alice", "owner", PW), ("bob", "user", PW2))), None
    assert login(a, "bob", PW2, ip="10.3.0.3").status_code == 303
    UserStore(cfg.data_dir / "users.json").set_password("alice", "another long passphrase here")
    assert a.get("/api/me").status_code == 200 and a.get("/api/me").json()["user"] == "bob"


def test_sessions_from_before_this_check_still_work_and_a_removed_person_is_out(make, cfg):
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)))
    assert login(c, "bob", PW2, ip="10.3.0.4").status_code == 303
    f = cfg.data_dir / "sessions.json"
    data = json.loads(f.read_text())
    for v in data.values():
        v.pop("credential", None)                                                                          # a session an older version wrote
    f.write_text(json.dumps(data))
    assert c.get("/api/me").status_code == 200
    UserStore(cfg.data_dir / "users.json").remove("bob")
    assert c.get("/api/me").status_code == 401


def test_a_damaged_sessions_file_means_everyone_signs_in_again_not_a_broken_app(tmp_path):
    path = tmp_path / "sessions.json"
    good = auth.Sessions(path)
    sid = good.create("alice", "c")
    for damaged in ("{not json", "", "[]", '"text"', "null", "{\"k\": 1"):
        path.write_text(damaged)
        s = auth.Sessions(path)
        assert s.get(sid) is None, damaged                                                                  # no crash, and the old session is gone
        new = s.create("alice", "c")
        assert json.loads(path.read_text()) and auth.Sessions(path).get(new)["user"] == "alice"


def test_two_writers_never_lose_each_others_sessions(tmp_path):
    path = tmp_path / "sessions.json"
    web, cli = auth.Sessions(path), auth.Sessions(path)
    made, errors = [], []

    def work(store, user):
        try:
            for i in range(25):
                made.append((store, store.create(user, "c")))
        except Exception as e:                                                                              # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=work, args=(web, "a")), threading.Thread(target=work, args=(cli, "b"))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors and len(made) == 50
    fresh = auth.Sessions(path)
    assert all(fresh.get(sid) for _, sid in made), "every session written by either writer is still there"


def test_the_session_file_stays_private(tmp_path):
    s = auth.Sessions(tmp_path / "sessions.json")
    s.create("alice", "c")
    assert (tmp_path / "sessions.json").stat().st_mode & 0o077 == 0 or sys.platform == "win32"


# ---------------------------------------------------------------- chat and run ownership

class Stub:
    """A Hermes whose answers to the ownership lookups are scripted."""

    def __init__(self, session_status=404, run_status=200, run=None):
        self.session_status, self.run_status, self.run = session_status, run_status, run or {}
        self.api_gets = []

    async def dash_call(self, method, path, body=None, timeout=15, **k):
        if method == "GET" and path.startswith("/api/sessions/"):
            return self.session_status, {"id": path.split("/")[3]}
        return 200, {}

    async def dash_json(self, path):
        return 200, {}

    async def api_call(self, method, path, body=None, **k):
        if method == "GET" and path.startswith("/v1/runs/"):
            self.api_gets.append(path)
            return self.run_status, dict(self.run)
        return 404, {}


@pytest.fixture()
def chat(make, cfg, monkeypatch):
    import chat_backend
    sent = []

    async def fake_call(method, path, payload=None):
        sent.append(payload)
        return 200, {"run_id": "run_%d" % (len(sent) + 40), "status": "started"}
    monkeypatch.setattr(chat_backend, "_call", fake_call)
    stub = Stub()
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2), ("carol", "user", PW2)), hermes=stub, config=cfg)
    c.stub, c.sent = stub, sent

    def as_(user, pw, ip):
        c.post("/logout", headers=H)
        assert login(c, user, pw, ip=ip).status_code == 303
    c.as_ = as_
    return c


def send(c, sid):
    return c.post(CHAT + "/send", json={"session_id": sid, "message": "hello"}, headers=H)


def test_a_new_chat_is_started_but_an_unverifiable_one_is_refused(chat):
    chat.as_("bob", PW2, "10.3.1.1")
    chat.stub.session_status = 404                                                    # Hermes says it knows no such chat: a new one
    assert send(chat, "atrium-chat-bobnew1").status_code == 200
    chat.stub.session_status = 200                                                    # it exists and nobody here claimed it: owners only
    assert send(chat, "atrium-chat-oldone1").status_code == 403
    for broken in (500, 502, 503, 401, 429):
        chat.stub.session_status = broken
        r = send(chat, "atrium-chat-unsure%d" % broken)
        assert r.status_code == 503 and "could not be verified" in r.json()["detail"], broken        # never "assume it is new"
    assert len(chat.sent) == 1                                                        # only the first message ever reached the agent


def test_a_run_is_only_shown_to_the_person_who_started_it(chat):
    chat.as_("alice", PW, "10.3.1.2")
    chat.stub.session_status = 404
    assert send(chat, "atrium-chat-alicechat").status_code == 200
    chat.as_("bob", PW2, "10.3.1.3")
    assert send(chat, "atrium-chat-bobchat1").status_code == 200
    chat.stub.run = {"session_id": "atrium-chat-alicechat", "status": "running"}
    assert chat.get(CHAT + "/runs/run_41").status_code == 403                         # bob asks about alice's run
    chat.stub.run = {"session_id": "atrium-chat-bobchat1", "status": "running"}
    assert chat.get(CHAT + "/runs/run_42").status_code not in (403, 503)             # his own is fine
    chat.stub.run_status = 404
    assert chat.get(CHAT + "/runs/run_nope").status_code == 404
    for broken in (500, 502, 503):
        chat.stub.run_status = broken
        r = chat.get(CHAT + "/runs/run_42")
        assert r.status_code == 503 and "could not be verified" in r.json()["detail"], broken        # an unknown owner is not "nobody's"
    chat.stub.run_status, chat.stub.run = 200, {"status": "running"}
    assert chat.get(CHAT + "/runs/run_42").status_code == 403                         # a run with no session recorded is not bob's either


def test_owners_are_not_held_up_by_the_ownership_lookup(chat):
    chat.as_("alice", PW, "10.3.1.4")
    chat.stub.run_status = 500
    chat.stub.api_gets.clear()
    r = chat.get(CHAT + "/runs/run_1")
    assert "could not be verified" not in json.dumps(r.json() if r.headers.get("content-type", "").startswith("application/json") else {})
    assert r.status_code != 503
    assert len(chat.stub.api_gets) <= 1, "the owner check costs no extra look at the agent"


def test_approval_records_are_for_owners_when_an_approval_service_is_set_up(make, cfg):
    cfg.action_gate_url = "http://127.0.0.1:9"                                        # nothing listens there: the point is who may ask
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), hermes=Stub(), config=cfg)
    assert login(c, "bob", PW2, ip="10.3.1.5").status_code == 303
    for path in ("/actions", "/actions/log"):                                          # the records of proposed mail, subjects, senders and files
        r = c.get(CHAT + path)
        assert r.status_code == 403 and "owners" in r.json()["detail"].lower(), path
    for path in ("/actions/a1/decide", "/actions/log/e1/undo"):                        # and deciding or undoing them
        assert c.post(CHAT + path, json={"choice": "approve"}, headers=H).status_code == 403, path
    c.post("/logout", headers=H)
    assert login(c, "alice", PW, ip="10.3.1.6").status_code == 303
    assert c.get(CHAT + "/actions").status_code != 403


# ---------------------------------------------------------------- uploads

def test_the_file_type_check_reads_only_the_start_of_the_file(tmp_path, monkeypatch):
    import chat_backend
    big = tmp_path / "big.pdf"
    big.write_bytes(b"NOTAPDF!" + b"x" * 4_000_000)
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("the whole file was read")))
    with pytest.raises(ValueError, match="real PDF"):
        chat_backend._extract(big, ".pdf")
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    with pytest.raises(ValueError):
        chat_backend._extract(empty, ".pdf")


# ---------------------------------------------------------------- the document readers the chat page imports are installed by the requirements

def test_every_document_reader_the_chat_imports_is_pinned():
    import re
    root = Path(__file__).resolve().parent.parent
    page = "\n".join((root / "app" / name).read_text(encoding="utf-8") for name in ("chat_backend.py", "docreader.py"))   # the readers live in docreader.py
    needed = {"pypdf": "pypdf" if "from pypdf import" in page else None, "python-docx": "python-docx" if re.search(r"^\s+import docx$", page, re.M) else None,
              "openpyxl": "openpyxl" if re.search(r"^\s+import openpyxl$", page, re.M) else None, "python-pptx": "python-pptx" if "from pptx import" in page else None}
    assert all(needed.values()), "this test expects the page to read PDF, Word, Excel and PowerPoint: %s" % needed
    requirements = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    for dist in needed:
        assert re.search(r"^%s==\d" % re.escape(dist), requirements, re.M), dist + " is not pinned in requirements.txt"
