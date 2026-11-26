"""First run: the owner account is made in the browser, guarded by a one-time setup code."""
import re

from conftest import PW, login

H = {"origin": "https://app.test", "x-real-ip": "10.0.0.9"}


def code_of(cfg):
    return (cfg.data_dir / "setup-code.txt").read_text().strip()


def post(c, **f):
    return c.post("/setup", data=f, headers=H)


def test_a_new_install_sends_people_to_setup_and_prints_a_code(make, cfg, capsys):
    c = make(users=())
    assert c.get("/login").status_code == 303 and c.get("/login").headers["location"] == "/setup"
    r = c.get("/setup")
    assert r.status_code == 200 and "Create the owner account" in r.text
    code = code_of(cfg)
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", code) and code in capsys.readouterr().out
    assert oct((cfg.data_dir / "setup-code.txt").stat().st_mode & 0o777) in ("0o600", "0o666")      # Windows has no modes


def test_the_owner_is_made_with_the_right_code_and_setup_then_closes(make, cfg):
    c = make(users=())
    code = code_of(cfg)
    r = post(c, code=code.lower().replace("-", " "), username="Sam", password=PW, again=PW)      # tolerant of how it was copied
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert not (cfg.data_dir / "setup-code.txt").exists()
    assert c.get("/setup").headers["location"] == "/login"
    assert post(c, code=code, username="eve", password=PW, again=PW).headers["location"] == "/login"      # closed: nothing is created
    assert login(c, "sam", PW, ip="10.0.0.9").status_code == 303


def test_a_wrong_code_a_short_password_and_different_passwords_make_nothing(make, cfg):
    c = make(users=())
    code = code_of(cfg)
    assert post(c, code="AAAA-AAAA-AAAA", username="sam", password=PW, again=PW).status_code == 403
    assert post(c, code=code, username="sam", password="short", again="short").status_code == 400
    assert post(c, code=code, username="sam", password=PW, again=PW + "x").status_code == 400
    assert post(c, code=code, username="s", password=PW, again=PW).status_code == 400
    assert c.get("/setup").status_code == 200                       # still open: no owner yet
    assert post(c, code=code, username="sam", password=PW, again=PW).status_code == 303


def test_guessing_the_code_is_throttled_and_a_foreign_origin_is_refused(make, cfg):
    c = make(users=())
    code = code_of(cfg)
    for _ in range(cfg.max_fails):
        assert post(c, code="BBBB-BBBB", username="sam", password=PW, again=PW).status_code == 403
    assert post(c, code=code, username="sam", password=PW, again=PW).status_code == 429       # even the right code waits
    other = make(users=(), config=cfg)
    assert other.post("/setup", data={"code": code}, headers={"origin": "https://evil.example"}).status_code == 403


def test_an_install_with_an_owner_has_no_setup_and_no_code(make, cfg):
    c = make()
    assert c.get("/setup").headers["location"] == "/login"
    assert c.get("/login").status_code == 200
    assert not (cfg.data_dir / "setup-code.txt").exists()


def test_many_addresses_together_are_stopped_too(make, cfg):
    c = make(users=())
    code = code_of(cfg)
    for n in range(50):
        r = c.post("/setup", data={"code": "AAAA-AAAA-AAAA", "username": "sam", "password": PW, "again": PW},
                   headers={**H, "x-real-ip": "10.9.%d.%d" % (n // 200, n % 200 + 1)})
        assert r.status_code in (403, 429)
    r = c.post("/setup", data={"code": code, "username": "sam", "password": PW, "again": PW}, headers={**H, "x-real-ip": "10.8.0.1"})
    assert r.status_code == 429                                      # even the right code waits once the whole install is under attack
