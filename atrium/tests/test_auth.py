import time

import auth
from conftest import PW, PW2, fast_hash, login
from main import COOKIE


def test_health_is_open(make):
    assert make().get("/healthz").json() == {"ok": True}


def test_home_and_api_need_login(make):
    c = make()
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/api/me").status_code == 401
    assert c.get("/shell-config.js").status_code == 401


def test_wrong_password_and_unknown_user_look_the_same(make):
    c = make()
    a = login(c, "alice", "nope nope nope nope")
    b = login(c, "nobody", "nope nope nope nope")
    assert a.status_code == b.status_code == 401
    assert "Wrong username or password." in a.text and "Wrong username or password." in b.text
    assert COOKIE not in a.cookies


def test_right_login_sets_a_locked_down_cookie_and_signs_in(make):
    c = make()
    r = login(c)
    assert r.status_code == 303 and r.headers["location"] == "/"
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "secure" in sc and "samesite=strict" in sc and "path=/" in sc
    assert c.get("/").status_code == 200
    assert c.get("/api/me").json() == {"user": "alice", "role": "owner", "name": ""}


def test_username_is_not_case_sensitive(make):
    c = make()
    assert login(c, "ALICE").status_code == 303


def test_logout_ends_the_session(make):
    c = make()
    login(c)
    assert c.post("/logout", headers={"origin": "https://app.test"}).status_code == 303
    assert c.get("/").status_code == 303


def test_lockout_by_address_and_by_username(make):
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)))
    for _ in range(auth.MAX_FAILS):
        assert login(c, "alice", "wrong wrong wrong", ip="10.0.0.1").status_code == 401
    assert login(c, "alice", PW, ip="10.0.0.1").status_code == 429       # right password, still locked
    assert login(c, "alice", PW, ip="10.0.0.2").status_code == 429       # that account is locked from any address
    assert login(c, "bob", PW2, ip="10.0.0.2").status_code == 303        # others are not affected


def test_cross_site_and_null_origin_are_refused(make):
    c = make()
    for origin in ("https://evil.example", "null"):
        r = c.post("/login", data={"username": "alice", "password": PW}, headers={"origin": origin})
        assert r.status_code == 403
    assert c.get("/login").headers["referrer-policy"] == "same-origin"


def test_password_change_signs_a_user_out_everywhere(make):
    c = make()
    login(c)
    assert c.get("/api/me").status_code == 200
    assert c.app.state.sessions.delete_user("alice") == 1
    assert c.get("/api/me").status_code == 401


def test_removed_user_loses_access_at_once(make):
    c = make()
    login(c)
    c.app.state.users.remove("alice")
    assert c.get("/api/me").status_code == 401


def test_role_change_applies_at_once(make):
    c = make(users=(("alice", "owner", PW),))
    login(c)
    assert c.get("/api/health").status_code == 200
    c.app.state.users.set_role("alice", "user")
    assert c.get("/api/health").status_code == 403


def test_sessions_survive_a_restart(make):
    c = make()
    cookie = login(c).cookies.get(COOKIE)
    c2 = make(users=())            # a new app instance reading the same data folder
    c2.cookies.set(COOKIE, cookie)
    assert c2.get("/api/me").status_code == 200


def test_old_style_sessions_without_a_user_are_not_accepted(make, cfg):
    c = make()
    s = auth.Sessions(cfg.data_dir / "sessions.json")
    sid = s.create("alice")
    s.items[s._key(sid)].pop("user")
    s._save()
    c2 = make(users=())
    c2.cookies.set(COOKIE, sid)
    assert c2.get("/api/me").status_code == 401


def test_headers(make):
    r = make().get("/login")
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert "Test Hub" in r.text           # brand comes from the config, not the code


def test_password_hash_roundtrip_formats():
    h = auth.hash_password("a long passphrase here", n=2 ** 10)
    assert "$" not in h and h.startswith("scrypt:")
    assert auth.verify_password("a long passphrase here", h)
    assert auth.verify_password("a long passphrase here", h.replace(":", "$"))     # the old format still verifies
    assert not auth.verify_password("another passphrase", h)
    assert not auth.verify_password("x", "garbage")
    assert auth.hash_looks_valid(h) and not auth.hash_looks_valid("") and not auth.hash_looks_valid("x$y")


def test_a_login_does_not_block_other_requests(make):
    """The password check and the delay run off the event loop, so the app keeps answering while logins are checked."""
    import threading
    c = make()

    def slow_logins():
        for _ in range(3):
            login(c, "alice", "wrong wrong wrong", ip="10.9.9.9")

    t = threading.Thread(target=slow_logins)
    t.start()
    time.sleep(0.15)
    start = time.time()
    status = c.get("/healthz").status_code
    elapsed = time.time() - start
    t.join()
    assert status == 200 and elapsed < 0.35


def test_cli_session_revocation_is_visible_to_running_web_app(tmp_path):
    path = tmp_path / "sessions.json"
    web = auth.Sessions(path)
    cli = auth.Sessions(path)
    old = web.create("alice")
    other = cli.create("bob")
    assert cli.delete_user("alice") == 1
    assert web.get(old) is None
    web.create("carol")
    assert cli.get(old) is None
    assert web.get(other)["user"] == "bob"
