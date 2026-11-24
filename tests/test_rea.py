"""REA page: uploads and read-only analyses in the workbench container, reached only by owners through the web app. Synthetic only: a fake `rea` program, no Ghidra, no network."""
import importlib.util
import io
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import httpx
import pytest
from fastapi.testclient import TestClient

import config as configmod
import rea_backend
from conftest import PW, PW2, login

H = {"origin": "https://app.test"}
TOKEN = "t" * 40
FAKE = '''
import json, sys, time
args = sys.argv[1:]
open(LOG, "a").write(json.dumps(args) + "\\n")
if args[0] == "inspect-pe-imports":
    print(json.dumps({"imports": [{"dll": "kernel32.dll", "functions": ["CreateFileW", "ReadFile"]}], "note": "x" * 300}))
elif args[0] == "doctor":
    print(json.dumps({"healthy": True}))
elif args[0] == "analyze-javascript-application":
    print(json.dumps({"modules": [1, 2, 3]}))
elif args[0] == "inspect-go-binary":
    sys.stderr.write("boom: not a Go binary"); sys.exit(3)
elif args[0] == "inspect-rust-binary":
    time.sleep(60)
else:
    print(json.dumps({"args": args}))
'''


@pytest.fixture()
def side(tmp_path, monkeypatch):
    """The workbench service, with REA replaced by a fake that logs how it was called."""
    spec = importlib.util.spec_from_file_location("rea_server", Path(__file__).resolve().parent.parent / "rea-sidecar" / "server.py")
    monkeypatch.setenv("REA_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("REA_TOKEN", TOKEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    log = tmp_path / "calls.log"
    script = tmp_path / "fake_rea.py"
    script.write_text("LOG = %r\n%s" % (str(log), FAKE))
    real = subprocess.Popen
    monkeypatch.setattr(mod.subprocess, "Popen", lambda argv, **k: real([sys.executable, str(script)] + argv[1:], **k))
    client = TestClient(mod.app, headers={"x-rea-token": TOKEN})
    client.mod, client.log = mod, log
    client.calls = lambda: [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return client


def upload(c, name="app.exe", data=b"MZ" + b"\0" * 100, **q):
    return c.post("/files", params=dict({"name": name}, **q), content=data)


def wait(c, jid, until=("done", "failed")):
    for _ in range(100):
        job = c.get("/jobs/" + jid).json()["job"]
        if job["status"] in until:
            return job
        time.sleep(0.1)
    raise AssertionError("the analysis did not finish")


# ---------------------------------------------------------------- the container's own service
def test_only_the_web_app_with_the_secret_may_use_it(side):
    anon = TestClient(side.mod.app)
    assert anon.get("/files").status_code == 401 and anon.get("/healthz").status_code == 200
    assert TestClient(side.mod.app, headers={"x-rea-token": "wrong"}).get("/files").status_code == 401


def test_uploads_are_named_safely_listed_and_deleted(side):
    r = upload(side, name="..\\..\\evil name?.exe")
    assert r.status_code == 200, r.text
    f = r.json()["file"]
    assert f["name"] == "evil name_.exe" and f["kind"] == "file" and f["size"] == 102
    assert Path(f["path"]).parent.parent.parent == Path(side.mod.WORK) / "files"
    assert [x["id"] for x in side.get("/files").json()["files"]] == [f["id"]]
    assert upload(side, data=b"").status_code == 400
    assert side.delete("/files/" + f["id"]).status_code == 200 and side.get("/files").json()["files"] == []
    assert side.delete("/files/..%2F..").status_code in (404, 405) and side.delete("/files/" + "0" * 12).status_code == 404


def zipped(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_a_zip_is_unpacked_safely(side):
    ok = upload(side, name="myapp.zip", data=zipped({"app/main.js": "x", "app/lib/a.js": "y"}), unpack=1).json()["file"]
    assert ok["kind"] == "folder" and ok["name"] == "myapp" and (Path(ok["path"]) / "app" / "lib" / "a.js").read_text() == "y"
    for evil in ("../escape.txt", "/abs.txt", "a/../../b.txt", "C:/x.txt"):
        r = upload(side, name="bad.zip", data=zipped({evil: "boom"}), unpack=1)
        assert r.status_code == 400, evil
    assert not (Path(side.mod.WORK) / "escape.txt").exists()
    assert upload(side, name="bad.zip", data=b"not a zip", unpack=1).status_code == 400
    assert [x["kind"] for x in side.get("/files").json()["files"]] == ["folder"], "failed uploads leave nothing behind"
    kept = upload(side, name="plain.zip", data=zipped({"a": "b"})).json()["file"]
    assert kept["kind"] == "file", "without the box ticked a zip stays a zip"


def test_the_uploads_have_a_size_limit(side, monkeypatch):
    monkeypatch.setattr(side.mod, "MAX_UPLOAD", 50)
    assert upload(side, data=b"x" * 80).status_code == 413
    assert side.get("/files").json()["files"] == []


def test_only_listed_analyses_run_with_a_fixed_argument_list(side):
    f = upload(side).json()["file"]
    assert side.post("/jobs", json={"command": "rm -rf", "file": f["id"]}).status_code == 400
    assert side.post("/jobs", json={"command": "pe-imports", "file": "0" * 12}).status_code == 404
    assert side.post("/jobs", json={"command": "pe-imports"}).status_code == 404
    jid = side.post("/jobs", json={"command": "pe-imports", "file": f["id"], "arg": "ignored; rm -rf /"}).json()["job"]["id"]
    job = wait(side, jid)
    assert job["status"] == "done"
    call = side.calls()[-1]
    assert call == ["inspect-pe-imports", f["path"], "--json"], "the typed text is not passed to an analysis that takes none"
    s = side.get("/jobs/%s/summary" % jid).json()
    assert s["outline"]["items"]["imports"]["length"] == 1 and "kernel32.dll" in s["preview"]
    d = side.get("/jobs/%s/download" % jid)
    assert d.status_code == 200 and json.loads(d.content)["imports"][0]["dll"] == "kernel32.dll"


def test_typed_values_must_match_their_pattern(side):
    f = upload(side).json()["file"]
    for bad in ("0xZZ", "main; id", "$(id)", "a" * 40, ""):
        assert side.post("/jobs", json={"command": "ghidra-decompile", "file": f["id"], "arg": bad}).status_code == 400, bad
    ok = side.post("/jobs", json={"command": "ghidra-decompile", "file": f["id"], "arg": "0x401000"})
    assert ok.status_code == 200
    wait(side, ok.json()["job"]["id"])
    assert side.calls()[-1] == ["decompile", f["path"], "0x401000", "--provider", "ghidra", "--json"]
    one = side.post("/jobs", json={"command": "ghidra-search", "file": f["id"], "arg": "CreateFile"}).json()["job"]["id"]
    wait(side, one)
    assert side.calls()[-1][:3] == ["search", f["path"], "CreateFile"]


def test_a_folder_only_works_with_analyses_that_take_one_and_nothing_with_doctor(side):
    folder = upload(side, name="a.zip", data=zipped({"x.js": "1"}), unpack=1).json()["file"]
    assert side.post("/jobs", json={"command": "pe-imports", "file": folder["id"]}).status_code == 400
    ok = side.post("/jobs", json={"command": "js-app", "file": folder["id"]})
    assert ok.status_code == 200 and wait(side, ok.json()["job"]["id"])["status"] == "done"
    d = side.post("/jobs", json={"command": "doctor"}).json()["job"]["id"]
    assert wait(side, d)["status"] == "done" and side.calls()[-1] == ["doctor", "--provider", "ghidra", "--json"]


def test_a_failed_analysis_says_why_without_a_result(side):
    f = upload(side).json()["file"]
    jid = side.post("/jobs", json={"command": "go", "file": f["id"]}).json()["job"]["id"]
    job = wait(side, jid)
    assert job["status"] == "failed" and "exit 3" in job["note"] and "not a Go binary" in job["error"]
    assert side.get("/jobs/%s/summary" % jid).status_code == 409


@pytest.mark.skipif(sys.platform == "win32", reason="stopping a job needs process groups (Linux)")
def test_a_slow_analysis_is_stopped(side, monkeypatch):
    monkeypatch.setattr(side.mod, "JOB_SECONDS", 1)
    f = upload(side).json()["file"]
    job = wait(side, side.post("/jobs", json={"command": "rust", "file": f["id"]}).json()["job"]["id"])
    assert job["status"] == "failed" and "stopped" in job["note"]


def test_old_uploads_and_results_are_removed(side, monkeypatch):
    f = upload(side).json()["file"]
    wait(side, side.post("/jobs", json={"command": "pe-imports", "file": f["id"]}).json()["job"]["id"])
    monkeypatch.setattr(side.mod, "KEEP_SECONDS", -1)
    assert side.get("/files").json()["files"] == []
    assert side.get("/jobs").json()["jobs"] == []


def test_the_command_list_has_nothing_that_runs_a_program(side):
    names = {c["argv"][0] for c in side.mod.COMMANDS}
    assert not names & {"capture-process", "run", "trace-process", "frida", "attach", "execute", "run-process", "process-capture"}
    assert all("{path" not in " ".join(c["argv"]) or "{path}" in c["argv"] for c in side.mod.COMMANDS), "every command has a well-formed target"
    assert side.get("/commands").json()["commands"][0]["id"] == "js-app"


# ---------------------------------------------------------------- the web app in front of it
@pytest.fixture()
def app(make, side, tmp_path, monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(rea_backend.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.ASGITransport(app=side.mod.app), *a, **k))

    count = [0]

    def build(configured=True):
        count[0] += 1
        c = configmod.Config(data_dir=tmp_path / ("data%d" % count[0]), instance_dir=tmp_path / "instance", trusted_proxies=["testclient"],
                             rea_url="http://rea:9100" if configured else "", rea_token=TOKEN if configured else "")
        client = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), config=c)
        client.side = side
        return client
    return build


def as_(client, user, pw, ip):
    client.post("/logout", headers=H)
    assert login(client, user, pw, ip=ip).status_code == 303


def test_the_page_and_routes_exist_only_when_the_workbench_is_set_up(app):
    off = app(configured=False)
    as_(off, "alice", PW, "10.0.0.1")
    assert off.get("/api/rea/commands").status_code == 404 and off.get("/rea").status_code == 404
    on = app()
    as_(on, "alice", PW, "10.0.0.1")
    assert on.get("/rea").status_code == 200 and on.get("/api/rea/status").json() == {"configured": True, "up": True}


def test_only_owners_signed_in_from_this_site_may_use_it(app):
    c = app()
    assert c.get("/api/rea/files").status_code == 401
    as_(c, "bob", PW2, "10.0.0.2")
    assert c.get("/api/rea/files").status_code == 403 and c.get("/rea").status_code in (302, 303, 403, 404)
    as_(c, "alice", PW, "10.0.0.1")
    assert c.get("/api/rea/files").status_code == 200
    assert c.post("/api/rea/jobs", headers={"origin": "https://evil.test"}, json={}).status_code == 403
    assert c.delete("/api/rea/files/" + "0" * 12, headers={"origin": "https://evil.test"}).status_code == 403


def test_an_owner_uploads_runs_reads_and_downloads_through_the_web_app(app):
    c = app()
    as_(c, "alice", PW, "10.0.0.1")
    assert c.post("/api/rea/files?name=x.exe", headers=H, content=b"MZ" * 40).status_code == 200
    f = c.get("/api/rea/files").json()["files"][0]
    assert c.get("/api/rea/commands").json()["commands"]
    jid = c.post("/api/rea/jobs", headers=H, json={"command": "pe-imports", "file": f["id"]}).json()["job"]["id"]
    wait(c.side, jid)
    assert c.get("/api/rea/jobs").json()["jobs"][0]["id"] == jid
    assert "kernel32.dll" in c.get("/api/rea/jobs/%s/summary" % jid).json()["preview"]
    d = c.get("/api/rea/jobs/%s/download" % jid)
    assert d.status_code == 200 and d.headers["x-content-type-options"] == "nosniff" and "attachment" in d.headers["content-disposition"]
    assert json.loads(d.content)["imports"]
    assert c.delete("/api/rea/jobs/" + jid, headers=H).status_code == 200 and c.get("/api/rea/jobs/" + jid).status_code == 404
    assert c.delete("/api/rea/files/" + f["id"], headers=H).status_code == 200


def test_the_web_app_refuses_bad_ids_names_and_sizes_and_hides_the_secret(app):
    c = app()
    as_(c, "alice", PW, "10.0.0.1")
    assert c.get("/api/rea/jobs/not-an-id").status_code == 404 and c.get("/api/rea/jobs/..%2Fx/download").status_code in (404, 400)
    assert c.post("/api/rea/files?name=a%2Fb.exe", headers=H, content=b"x").status_code == 400
    assert c.post("/api/rea/files?name=x.exe", headers=dict(H, **{"content-length": str(2 * 1024 ** 3)}), content=b"x").status_code in (400, 413)
    assert TOKEN not in c.get("/api/rea/status").text and TOKEN not in c.get("/rea").text
    assert c.post("/api/rea/jobs", headers=H, json={"command": "pe-imports", "file": "0" * 12}).status_code == 404
