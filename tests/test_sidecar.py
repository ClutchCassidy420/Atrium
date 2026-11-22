"""The coding sidecar: checkpoints and rewind (real git in a temporary workspace) and the authenticated agent-browser entry point.

Synthetic only: a throwaway workspace, no network, no Chromium (the browser's own port is stubbed).
"""
import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SIDECAR = Path(__file__).resolve().parent.parent / "code-sidecar"
CODE, CDP = "code-secret", "cdp-secret"
BROWSER_ID = "0f0e0d0c-0b0a-4909-8807-060504030201"


@pytest.fixture()
def sidecar(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("WORKSPACE", str(ws))
    monkeypatch.setenv("CODE_TOKEN", CODE)
    monkeypatch.setenv("CDP_TOKEN", CDP)
    monkeypatch.syspath_prepend(str(SIDECAR))
    sys.modules.pop("server", None)
    mod = importlib.import_module("server")
    client = TestClient(mod.app)            # not entered as a context manager, so the browser supervisor never starts
    client.headers.update({"x-code-token": CODE})
    yield client, mod, ws
    sys.modules.pop("server", None)


def snapshot(folder: Path) -> dict:
    return {str(p.relative_to(folder)): p.read_text() for p in sorted(folder.rglob("*")) if p.is_file() and ".git" not in p.parts}


def make_project(client, ws, name="p"):
    assert client.post("/projects", json={"name": name}).status_code == 200
    return ws / name


# ---------------------------------------------------------------- checkpoints and rewind

def test_rewind_restores_exactly_what_a_checkpoint_had(sidecar):
    client, _, ws = sidecar
    p = make_project(client, ws)
    (p / "a.txt").write_text("one")
    (p / "dir").mkdir()
    (p / "dir" / "b.txt").write_text("two")
    (p / "thing").write_text("a file for now")
    saved = snapshot(p)
    ref = client.post("/git/checkpoint", json={"project": "p", "label": "start"}).json()["ref"]
    # change everything: edit, delete, add, and turn a file into a folder
    (p / "a.txt").write_text("CHANGED")
    (p / "dir" / "b.txt").unlink()
    (p / "dir").rmdir()
    (p / "thing").unlink()
    (p / "thing").mkdir()
    (p / "thing" / "inner.txt").write_text("now a folder")
    (p / "new.txt").write_text("extra")
    assert snapshot(p) != saved
    assert client.post("/git/rewind", json={"project": "p", "ref": ref}).status_code == 200
    assert snapshot(p) == saved


def test_a_rewind_can_itself_be_undone(sidecar):
    client, _, ws = sidecar
    p = make_project(client, ws)
    (p / "a.txt").write_text("v1")
    ref = client.post("/git/checkpoint", json={"project": "p", "label": "v1"}).json()["ref"]
    (p / "a.txt").write_text("v2")
    (p / "later.txt").write_text("added after")
    after = snapshot(p)
    client.post("/git/rewind", json={"project": "p", "ref": ref})
    pre = [c["ref"] for c in client.get("/git/checkpoints", params={"project": "p"}).json()["checkpoints"] if c["ref"].endswith("-pre")]
    assert len(pre) == 1
    client.post("/git/rewind", json={"project": "p", "ref": pre[0]})
    assert snapshot(p) == after


def test_checkpoints_made_in_the_same_second_have_different_names(sidecar):
    client, _, ws = sidecar
    p = make_project(client, ws)
    (p / "a.txt").write_text("x")
    refs = {client.post("/git/checkpoint", json={"project": "p", "label": "again"}).json()["ref"] for _ in range(6)}
    assert len(refs) == 6
    listed = {c["ref"] for c in client.get("/git/checkpoints", params={"project": "p"}).json()["checkpoints"]}
    assert refs <= listed


def test_a_checkpoint_sees_files_git_has_not_tracked_yet(sidecar):
    client, _, ws = sidecar
    p = make_project(client, ws)
    (p / "untracked.txt").write_text("never added")
    ref = client.post("/git/checkpoint", json={"project": "p", "label": "u"}).json()["ref"]
    (p / "untracked.txt").unlink()
    client.post("/git/rewind", json={"project": "p", "ref": ref})
    assert (p / "untracked.txt").read_text() == "never added"


@pytest.mark.parametrize("bad", ["", "refs/heads/main", "refs/atrium/checkpoints/../../x", "refs/atrium/checkpoints/NOPE"])
def test_rewind_refuses_bad_refs(sidecar, bad):
    client, _, ws = sidecar
    make_project(client, ws)
    assert client.post("/git/rewind", json={"project": "p", "ref": bad}).status_code in (400, 404)


def test_every_route_needs_the_code_token(sidecar):
    client, _, ws = sidecar
    make_project(client, ws)
    plain = TestClient(client.app)
    assert plain.get("/projects").status_code == 401
    assert plain.get("/projects", headers={"x-code-token": "wrong"}).status_code == 401
    assert plain.get("/projects", headers={"x-cdp-token": CDP}).status_code == 401          # the browser credential opens nothing else


# ---------------------------------------------------------------- the agent's browser entry point is authenticated

def test_there_is_no_open_debugging_port_forwarder(sidecar):
    _, mod, _ = sidecar
    assert not hasattr(mod, "cdp_forwarder") and not hasattr(mod, "CDP_PUBLIC_PORT")
    assert "0.0.0.0" not in (SIDECAR / "server.py").read_text(encoding="utf-8")


def test_browser_discovery_needs_the_browser_token(sidecar, monkeypatch):
    client, mod, _ = sidecar
    monkeypatch.setattr(mod, "chrome_http", lambda path, method="GET": {"Browser": "Chrome", "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/" + BROWSER_ID})
    plain = TestClient(client.app)
    assert plain.get("/json/version").status_code == 401
    assert plain.get("/json/version", headers={"x-code-token": CODE}).status_code == 401              # the web app's credential is not the browser's
    assert plain.get("/json/version", headers={"x-cdp-token": "wrong"}).status_code == 401
    ok = plain.get("/json/version", headers={"x-cdp-token": CDP})
    assert ok.status_code == 200
    url = ok.json()["webSocketDebuggerUrl"]
    assert url.endswith("/devtools/browser/" + BROWSER_ID) and "127.0.0.1:9223" not in url          # the real port is never handed out


def test_browser_discovery_is_closed_when_no_browser_token_is_configured(sidecar, monkeypatch):
    client, mod, _ = sidecar
    monkeypatch.delenv("CDP_TOKEN", raising=False)
    plain = TestClient(client.app)
    assert plain.get("/json/version").status_code == 401
    assert plain.get("/json/version", headers={"x-cdp-token": ""}).status_code == 401


def test_browser_socket_refuses_a_wrong_token(sidecar):
    client, _, _ = sidecar
    plain = TestClient(client.app)
    with pytest.raises(Exception):
        with plain.websocket_connect("/devtools/browser/" + BROWSER_ID, headers={"x-cdp-token": "wrong"}) as ws:
            ws.receive_text()
