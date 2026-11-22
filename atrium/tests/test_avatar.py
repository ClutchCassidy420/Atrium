import json

from conftest import PW, PW2, login

H = {"origin": "https://app.test"}
LOOK = {"avatar": "devil", "theme": "crimson", "glow": 1.2, "tail": True, "hair": "bob"}


def two_users(make):
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)))

    def as_(user, pw, ip):
        c.post("/logout", headers=H)
        assert login(c, user, pw, ip=ip).status_code == 303
    c.as_ = as_
    return c


def test_the_look_is_saved_by_an_owner_and_seen_by_everyone(make):
    c = two_users(make)
    c.as_("alice", PW, "10.8.0.1")
    assert c.get("/api/avatar-look").json() == {"look": None}
    assert c.put("/api/avatar-look", json={"look": LOOK}, headers=H).json() == {"ok": True}
    assert c.get("/api/avatar-look").json() == {"look": LOOK}
    c.as_("bob", PW2, "10.8.0.2")
    assert c.get("/api/avatar-look").json() == {"look": LOOK}                       # everyone sees the agent's look
    assert c.put("/api/avatar-look", json={"look": {"avatar": "core"}}, headers=H).status_code == 403   # only owners change it
    assert c.get("/api/avatar-look").json() == {"look": LOOK}


def test_bad_looks_and_strangers_are_refused(make):
    c = two_users(make)
    assert c.get("/api/avatar-look").status_code == 401
    c.as_("alice", PW, "10.8.0.3")
    for bad in ({"look": "x"}, {"look": {"a b": 1}}, {"look": {"avatar": {"x": 1}}}, {"look": {"avatar": "x" * 41}}, {"look": {("k%d" % i).replace("0", "a").replace("1", "b"): 1 for i in range(60)}}, {}):
        assert c.put("/api/avatar-look", json=bad, headers=H).status_code == 422, bad
    assert c.put("/api/avatar-look", json={"look": LOOK}, headers={"origin": "https://evil.example"}).status_code == 403


def test_home_loads_the_avatar_drawing(make):
    c = two_users(make)
    c.as_("alice", PW, "10.8.0.4")
    cfg = c.get("/shell-config.js").text
    assert "/static/pages/avatar/orb.js" in cfg and cfg.index("avatar/orb.js") < cfg.index("pages/home.js")     # the drawing loads before Home
