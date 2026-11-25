import chat_uploads
from conftest import PW, PW2, login

P = "/api/plugins/atrium-chat"
H = {"origin": "https://app.test"}


def _setup(make, cfg, tmp_path, monkeypatch):
    cfg.hermes_data_dir = str(tmp_path / "hermes")
    monkeypatch.setenv("HERMES_HOME", cfg.hermes_data_dir)
    c = make(users=(("alice", "user", PW), ("bob", "user", PW2)))
    monkeypatch.setenv("HERMES_HOME", cfg.hermes_data_dir)      # make_app sets it again; keep it pointing at the test folder
    return c


def _upload(c, who, pw, name="notes.txt", body=b"hello", keep=0):
    login(c, who, pw)
    r = c.post(P + "/doc/upload?name=%s&keep=%d" % (name, keep), content=body, headers=H)
    return r


def test_uploads_live_in_the_persons_own_folder(make, cfg, tmp_path, monkeypatch):
    c = _setup(make, cfg, tmp_path, monkeypatch)
    r = _upload(c, "alice", PW)
    assert r.status_code == 200, r.text
    assert "/uploads/alice/" in r.json()["path"].replace("\\", "/")


def test_nobody_can_move_someone_elses_document(make, cfg, tmp_path, monkeypatch):
    c = _setup(make, cfg, tmp_path, monkeypatch)
    path = _upload(c, "alice", PW).json()["path"]
    login(c, "bob", PW2)
    assert c.post(P + "/doc/move", json={"path": path, "keep": True}, headers=H).status_code == 404
    login(c, "alice", PW)
    moved = c.post(P + "/doc/move", json={"path": path, "keep": True}, headers=H)
    assert moved.status_code == 200 and "/kept-docs/alice/" in moved.json()["path"].replace("\\", "/")


def test_a_person_cannot_fill_the_disk(make, cfg, tmp_path, monkeypatch):
    c = _setup(make, cfg, tmp_path, monkeypatch)
    monkeypatch.setattr(chat_uploads, "MAX_USER_BYTES", 1000)
    assert _upload(c, "alice", PW, body=b"x" * 600, keep=1).status_code == 200
    r = _upload(c, "alice", PW, name="more.txt", body=b"x" * 600, keep=1)
    assert r.status_code == 413 and "limit" in r.json()["detail"]
    assert _upload(c, "bob", PW2, body=b"x" * 600, keep=1).status_code == 200      # the limit is per person
