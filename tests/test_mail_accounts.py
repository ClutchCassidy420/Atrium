"""Personal mailboxes for the Email pages: each person connects their own, safely. Synthetic only (no mail server, no network)."""
import asyncio
import json
import os
import socket
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import pytest
from fastapi import HTTPException

import config as configmod
import mail_accounts as ma
import mail_backend as mb
import pagesmount
from conftest import PW, PW2, login
from test_mail import Box

H = {"origin": "https://app.test"}
API = "/api/mail"
SECRET = "app-password-" + "x9q7z4"


def addrinfo(*ips):
    return lambda host, port, **k: [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]


def pub(host, port=993):
    return "93.184.216.34"


# ---------------------------------------------------------------- the host checks (nobody may aim Atrium at its own network)

@pytest.mark.parametrize("host", ["imap.gmail.com", "IMAP.Fastmail.COM", "mail.example.org.", "xn--mail-8na.example.net"])
def test_public_looking_names_are_accepted(host):
    assert ma.clean_host(host) == host.lower().rstrip(".")


@pytest.mark.parametrize("host", ["", "localhost", "mail", "10.0.0.5", "169.254.169.254", "[::1]", "a.local", "mail.internal", "x.lan", "host.home.arpa",
                                  "imap.gmail.com:993", "imap.gmail.com/x", "user@imap.gmail.com", "imap gmail.com", "a..b.com", "-x.example.com",
                                  "x" * 300 + ".com", "mail.invalid", "mail.test", "foo.example", "1.2.3.4", None, 5])
def test_ip_addresses_internal_and_malformed_names_are_refused(host):
    with pytest.raises(ma.AccountError):
        ma.clean_host(host)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1", "fe80::1%eth0",
                                "fc00::1", "::ffff:127.0.0.1", "224.0.0.1"])
def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", addrinfo(ip))
    with pytest.raises(ma.AccountError, match="private or internal"):
        ma.resolve_public("imap.example.org")


def test_one_private_answer_among_public_ones_refuses_the_name(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", addrinfo("93.184.216.34", "10.0.0.1"))
    with pytest.raises(ma.AccountError):
        ma.resolve_public("imap.example.org")


def test_public_answers_pin_one_address_and_prefer_ipv4(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", addrinfo("2606:4700:4700::1111", "93.184.216.34"))
    assert ma.resolve_public("imap.example.org") == "93.184.216.34"
    monkeypatch.setattr(socket, "getaddrinfo", addrinfo())
    with pytest.raises(ma.AccountError):
        ma.resolve_public("imap.example.org")

    def boom(*a, **k):
        raise socket.gaierror("no such host")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(ma.AccountError, match="Could not find"):
        ma.resolve_public("imap.example.org")


# ---------------------------------------------------------------- the encrypted store

def test_the_password_is_encrypted_bound_to_the_person_and_never_listed(tmp_path):
    store = ma.Accounts(tmp_path)
    s = store.build("Bob@Gmail.com", SECRET, preset="gmail", resolver=pub)
    assert (s.mail_imap_host, s.mail_imap_tls_server_name, s.mail_imap_port, s.mail_smtp_port) == ("93.184.216.34", "imap.gmail.com", 993, 465)
    store.save("Bob", s)
    text = store.path.read_text()
    assert SECRET not in text and "93.184.216.34" not in text
    info = store.list("bob")[0]
    assert info["address"] == "Bob@Gmail.com" and SECRET not in json.dumps(info) and "sealed" not in info
    back = store.settings("bob", resolver=pub)
    assert back.mail_token == SECRET and back.mail_imap_tls_server_name == "imap.gmail.com" and back.personal is True
    data = json.loads(text)
    data["carol"] = data["bob"]                                  # a stolen entry moved to another person does not open
    store.path.write_text(json.dumps(data))
    with pytest.raises(ma.AccountError, match="Connect your email again"):
        store.settings("carol", resolver=pub)
    assert store.list("nobody") == [] and store.settings("nobody", resolver=pub) is None


@pytest.mark.skipif(os.name == "nt", reason="needs real file permissions")
def test_the_key_and_the_file_are_private(tmp_path):
    store = ma.Accounts(tmp_path)
    store.save("bob", store.build("bob@gmail.com", SECRET, preset="gmail", resolver=pub))
    for f in (store.path, store.keyfile):
        assert f.stat().st_mode & 0o077 == 0, f


def test_checks_on_what_a_person_types(tmp_path):
    store = ma.Accounts(tmp_path)
    for address in ("", "nope", "a@b", "@x.com", "a b@x.com", "a..b@x.com", "a@" + "x" * 300 + ".com", None):
        with pytest.raises(ma.AccountError):
            store.build(address, SECRET, preset="gmail", resolver=pub)
    for password in ("", None, "x" * 600, "a\nb"):
        with pytest.raises(ma.AccountError):
            store.build("bob@gmail.com", password, preset="gmail", resolver=pub)
    with pytest.raises(ma.AccountError):
        store.build("bob@gmail.com", SECRET, preset="evil", resolver=pub)
    with pytest.raises(ma.AccountError):
        store.build("bob@gmail.com", SECRET, imap_host="127.0.0.1", smtp_host="smtp.example.org", resolver=pub)
    custom = store.build("bob@example.org", SECRET, imap_host="Mail.Example.org", smtp_host="smtp.example.org", resolver=pub)
    assert custom.mail_imap_tls_server_name == "mail.example.org"


def test_delete_purge_and_the_attempt_limit(tmp_path):
    store = ma.Accounts(tmp_path)
    for u in ("bob", "carol", "dave"):
        store.save(u, store.build(u + "@gmail.com", SECRET, preset="gmail", resolver=pub))
    bob_id = store.list("bob")[0]["id"]
    assert store.delete("bob", bob_id) and not store.delete("bob", bob_id) and store.list("bob") == []
    assert store.purge(["Carol"]) == 1 and store.list("carol") and store.list("dave") == []
    assert [store.allow_attempt("bob") for _ in range(ma.ATTEMPTS + 2)] == [True] * ma.ATTEMPTS + [False] * 2
    assert store.allow_attempt("carol")


# ---------------------------------------------------------------- the routes

@pytest.fixture()
def mailapp(make, tmp_path, monkeypatch):
    seen = SimpleNamespace(logins=[], used=[], box=Box())
    monkeypatch.setattr(ma, "resolve_public", pub)

    def test_login(c):
        seen.logins.append((c.mail_address, c.mail_imap_tls_server_name, c.mail_smtp_tls_server_name, c.mail_token))
        if c.mail_token == "wrong":
            raise HTTPException(status_code=502, detail="The mail server did not accept the sign-in.")
    monkeypatch.setattr(mb, "_test_login", test_login)

    @contextmanager
    def connection():
        seen.used.append(mb._cfg().mail_address)
        yield seen.box
    monkeypatch.setattr(mb, "_imap", connection)

    def build(server=False):
        c = configmod.Config(data_dir=tmp_path / "data", instance_dir=tmp_path / "instance", trusted_proxies=["testclient"], mail_user_accounts=True)
        if server:
            c.mail_address, c.mail_token, c.mail_imap_host = "owner@server.test", "tok", "imap.server.test"
        client = make(users=(("alice", "owner", PW), ("bob", "user", PW2), ("carol", "user", PW2)), config=c)
        client.seen = seen
        client.cfg = c
        return client

    yield build
    mb.ACCOUNTS = None


def as_(client, user, pw, ip):
    client.post("/logout", headers=H)
    assert login(client, user, pw, ip=ip).status_code == 303


def connect(client, **k):
    body = {"address": "bob@gmail.com", "password": SECRET, "preset": "gmail"}
    body.update(k)
    return client.put(API + "/account", headers=H, json=body)


def test_a_person_connects_their_own_mailbox_and_only_that_one_is_used(mailapp):
    c = mailapp()
    as_(c, "bob", PW2, "10.9.0.1")
    s = c.get(API + "/status").json()
    assert s["connected"] is False and s["account"] is None and s["user_accounts"] is True and [p["id"] for p in s["presets"]][0] == "gmail"
    assert c.get(API + "/folders").status_code == 409 and "Connect your email" in c.get(API + "/folders").json()["detail"]
    r = connect(c)
    assert r.status_code == 200 and r.json()["account"]["address"] == "bob@gmail.com" and SECRET not in r.text
    assert c.seen.logins == [("bob@gmail.com", "imap.gmail.com", "smtp.gmail.com", SECRET)]
    s = c.get(API + "/status").json()
    assert s["connected"] and s["account"] == "personal" and s["address"] == "bob@gmail.com" and SECRET not in json.dumps(s)
    assert c.get(API + "/folders").json()["address"] == "bob@gmail.com" and c.seen.used[-1] == "bob@gmail.com"
    assert SECRET not in c.get(API + "/account").text
    as_(c, "carol", PW2, "10.9.0.2")                                                  # another person sees none of it
    assert c.get(API + "/status").json()["connected"] is False and c.get(API + "/folders").status_code == 409
    as_(c, "alice", PW, "10.9.0.3")                                                   # not even an owner without a server mailbox
    assert c.get(API + "/status").json()["connected"] is False
    as_(c, "bob", PW2, "10.9.0.4")
    bob_id = c.get(API + "/account").json()["accounts"][0]["id"]
    assert c.delete(API + "/account/" + bob_id, headers=H).json() == {"ok": True}
    assert c.get(API + "/status").json()["connected"] is False
    assert "bob" not in json.loads((c.cfg.data_dir / "mail-accounts.json").read_text())


def test_a_wrong_password_or_a_bad_host_saves_nothing(mailapp):
    c = mailapp()
    as_(c, "bob", PW2, "10.9.1.1")
    r = connect(c, password="wrong")
    assert r.status_code == 502 and "wrong" not in r.text
    saved = c.cfg.data_dir / "mail-accounts.json"
    assert not saved.exists() or "bob" not in saved.read_text()
    for bad in ("127.0.0.1", "localhost", "mail.internal", "10.0.0.1", "imap.example.org:993"):
        r = connect(c, preset="", imap_host=bad, smtp_host="smtp.example.org")
        assert r.status_code == 400, bad
    for body in ({}, {"address": "bob@gmail.com"}, {"address": "bob@gmail.com", "password": ["x"]}, {"address": "bob@gmail.com", "password": SECRET, "extra": 1}):
        r = c.put(API + "/account", headers=H, json=body)
        assert r.status_code == 400 and SECRET not in r.text
    r = c.put(API + "/account", headers=H, content=b"not json " + SECRET.encode())
    assert r.status_code == 400 and SECRET not in r.text
    assert c.get(API + "/status").json()["connected"] is False


def test_a_name_that_resolves_inward_is_refused_at_connect_time(mailapp, monkeypatch):
    c = mailapp()
    monkeypatch.undo()                                                                 # use the real resolver, with a lying DNS answer
    monkeypatch.setattr(mb, "_test_login", lambda s: None)
    monkeypatch.setattr(socket, "getaddrinfo", addrinfo("192.168.1.10"))
    as_(c, "bob", PW2, "10.9.2.1")
    r = connect(c, preset="", imap_host="imap.example.org", smtp_host="smtp.example.org")
    assert r.status_code == 400 and "private or internal" in r.json()["detail"]


def test_connection_attempts_are_limited(mailapp):
    c = mailapp()
    as_(c, "bob", PW2, "10.9.3.1")
    codes = [connect(c, password="wrong").status_code for _ in range(ma.ATTEMPTS + 1)]
    assert codes == [502] * ma.ATTEMPTS + [429]


def test_only_signed_in_people_from_this_site_may_connect(mailapp):
    c = mailapp()
    assert connect(c).status_code == 401 and c.get(API + "/status").status_code == 401
    as_(c, "bob", PW2, "10.9.4.1")
    assert c.put(API + "/account", headers={"origin": "https://other.test"}, json={"address": "a@b.com", "password": "x"}).status_code == 403
    assert c.delete(API + "/account/ab12cd34", headers={"origin": "https://other.test"}).status_code == 403


def test_an_owner_keeps_the_server_mailbox_by_default_and_may_add_their_own_next_to_it(mailapp):
    c = mailapp(server=True)
    as_(c, "alice", PW, "10.9.5.1")
    s = c.get(API + "/status").json()
    assert s["connected"] and s["account"] == "server" and s["active"] == "server" and s["address"] == "owner@server.test"
    assert [a["id"] for a in s["accounts"]] == ["server"]
    assert c.get(API + "/folders").json()["address"] == "owner@server.test"
    r = connect(c, address="alice@gmail.com")
    assert r.status_code == 200
    mine = r.json()["account"]["id"]
    s = c.get(API + "/status").json()
    assert s["account"] == "server" and [a["id"] for a in s["accounts"]] == ["server", mine]          # still opens on the server mailbox
    assert c.get(API + "/folders?account=" + mine).json()["address"] == "alice@gmail.com"
    s = c.get(API + "/status?account=" + mine).json()
    assert s["account"] == "personal" and s["active"] == mine and s["address"] == "alice@gmail.com" and s["features"] == []
    assert c.get(API + "/folders?account=server").json()["address"] == "owner@server.test"
    as_(c, "bob", PW2, "10.9.5.2")
    assert c.get(API + "/status").json()["connected"] is False                                      # the server mailbox is never offered to anyone else
    assert c.get(API + "/status").json()["accounts"] == []
    assert c.get(API + "/folders").status_code == 409
    assert c.get(API + "/folders?account=server").status_code == 403                                # nor reachable by asking for it
    assert connect(c).status_code == 200 and c.get(API + "/folders").json()["address"] == "bob@gmail.com"
    assert c.get(API + "/folders?account=" + mine).status_code == 404                               # nor can they open the owner's personal one


def test_a_person_can_keep_several_mailboxes_switch_between_them_and_never_see_anyone_elses(mailapp):
    c = mailapp()
    as_(c, "bob", PW2, "10.9.7.1")
    addresses = ("bob@gmail.com", "bob.work@fastmail.com", "bob.old@yahoo.com")
    ids = []
    for address in addresses:
        r = connect(c, address=address, preset="gmail")
        assert r.status_code == 200, address
        ids.append(r.json()["account"]["id"])
    assert len(set(ids)) == 3
    s = c.get(API + "/status").json()
    assert s["active"] == ids[0] and [a["address"] for a in s["accounts"]] == list(addresses)
    for i, address in zip(ids, addresses):                                                          # each request names its mailbox
        assert c.get(API + "/folders?account=" + i).json()["address"] == address
        assert c.get(API + "/status?account=" + i).json()["address"] == address
    assert c.get(API + "/folders").json()["address"] == "bob@gmail.com"                              # no choice: the first one
    dup = connect(c, address="BOB@gmail.com")                                                       # the same address twice
    assert dup.status_code == 400 and "already connected" in dup.json()["detail"]
    for n in range(2):
        assert connect(c, address="more%d@gmail.com" % n).status_code == 200
    r = connect(c, address="onetoomany@gmail.com")
    assert r.status_code == 400 and "up to %d" % ma.MAX_ACCOUNTS in r.json()["detail"]
    assert c.delete(API + "/account/" + ids[1], headers=H).json() == {"ok": True}
    assert ids[1] not in [a["id"] for a in c.get(API + "/status").json()["accounts"]]
    assert c.get(API + "/folders?account=" + ids[1]).status_code == 404
    assert c.delete(API + "/account/" + ids[1], headers=H).status_code == 404
    as_(c, "carol", PW2, "10.9.7.2")                                                                # other people's ids open nothing
    assert connect(c, address="carol@gmail.com").status_code == 200
    for i in ids:
        assert c.get(API + "/folders?account=" + i).status_code == 404
        assert c.delete(API + "/account/" + i, headers=H).status_code == 404
    assert [a["address"] for a in c.get(API + "/status").json()["accounts"]] == ["carol@gmail.com"]
    assert SECRET not in json.dumps(c.get(API + "/account").json())


def test_a_person_saved_before_there_could_be_several_keeps_their_mailbox(tmp_path):
    store = ma.Accounts(tmp_path)
    s = store.build("old@gmail.com", SECRET, preset="gmail", resolver=pub)
    store.path.write_text(json.dumps({"olduser": {"address": s.mail_address, "preset": "gmail", "imap_host": "imap.gmail.com", "smtp_host": "smtp.gmail.com",
                                                   "sealed": store._seal("olduser", s.mail_address, SECRET), "created": 1}}))
    legacy = store.list("olduser")
    assert [a["address"] for a in legacy] == ["old@gmail.com"] and legacy[0]["id"]
    assert store.settings("olduser", resolver=pub).mail_token == SECRET
    assert store.settings("olduser", legacy[0]["id"], resolver=pub).mail_address == "old@gmail.com"
    store.save("olduser", store.build("new@gmail.com", SECRET, preset="gmail", resolver=pub))          # saving rewrites it in the new shape
    assert "accounts" in json.loads(store.path.read_text())["olduser"]
    assert [a["address"] for a in store.list("olduser")] == ["old@gmail.com", "new@gmail.com"]
    assert store.settings("olduser", store.list("olduser")[0]["id"], resolver=pub).mail_token == SECRET
    with pytest.raises(ma.NoSuchAccount):
        store.settings("olduser", "nope1234", resolver=pub)
    with pytest.raises(ma.NoSuchAccount):
        store.settings("nobody", "nope1234", resolver=pub)


def test_a_legacy_login_moves_to_the_settings_key_too(tmp_path):
    store = ma.Accounts(tmp_path)
    store.path.write_text(json.dumps({"olduser": {"address": "old@gmail.com", "preset": "gmail", "imap_host": "imap.gmail.com", "smtp_host": "smtp.gmail.com",
                                                   "sealed": store._seal("olduser", "old@gmail.com", SECRET), "created": 1}}))
    assert store.keyfile.exists() or store.settings("olduser", resolver=pub)                         # the key file exists once it has been used
    new = ma.Accounts(tmp_path, "q" * 48)
    assert new.migrate() == 1 and not new.keyfile.exists()
    assert new.settings("olduser", resolver=pub).mail_token == SECRET


def test_the_picker_cannot_point_a_route_an_install_adds_at_a_personal_mailbox(mailapp):
    c = mailapp()
    store = ma.Accounts(c.cfg.data_dir)
    store.save("alice", store.build("alice@gmail.com", SECRET, preset="gmail", resolver=pub))
    mine = store.list("alice")[0]["id"]
    guard = pagesmount.mail_guard(c.cfg, lambda r: r.who, lambda r: True, store)

    async def run():
        req = SimpleNamespace(who={"user": "alice", "role": "owner"}, url=SimpleNamespace(path=API + "/messages/5/spam"), method="POST",
                              state=SimpleNamespace(), query_params={"account": mine})
        mb.ACCOUNT.set(None)
        await guard(req)
        return mb.ACCOUNT.get(), mb.SERVER_OK.get()
    own, server_ok = asyncio.run(run())
    assert own is None and server_ok is True                                                         # it still acts on the server mailbox, never the picked one


def test_other_routes_an_install_adds_stay_owner_only(mailapp):
    c = mailapp()
    guard = pagesmount.mail_guard(c.cfg, lambda r: r.who, lambda r: True, ma.Accounts(c.cfg.data_dir))

    def ask(role, path, method="GET"):
        req = SimpleNamespace(who={"user": "x", "role": role}, url=SimpleNamespace(path=API + path), method=method, state=SimpleNamespace(), query_params={})
        try:
            asyncio.run(guard(req))
            return 200
        except HTTPException as e:
            return e.status_code
    allowed = [("GET", "/status"), ("GET", "/folders"), ("GET", "/messages"), ("GET", "/messages/5"), ("GET", "/messages/5/attachment/1"),
               ("POST", "/messages/5/flags"), ("POST", "/messages/5/move"), ("DELETE", "/messages/5"), ("POST", "/send"),
               ("GET", "/account"), ("PUT", "/account"), ("DELETE", "/account/ab12cd34"), ("HEAD", "/status")]
    for method, path in allowed:
        assert ask("user", path, method) == 200, (method, path)
    # a route an install adds, even one that starts like a core route (the block list acts on the server-wide mailbox), is owner-only
    denied = [("POST", "/messages/5/spam"), ("GET", "/messages/5/spam"), ("POST", "/blocklist/add"), ("GET", "/block"), ("GET", "/admin"),
              ("POST", "/messages/5/flags/x"), ("GET", "/messages/abc"), ("GET", "/messages/5/"), ("GET", "/status/"), ("DELETE", "/send"),
              ("GET", "/send"), ("POST", "/status"), ("PUT", "/messages/5"), ("POST", "/account/x"), ("DELETE", "/account"), ("DELETE", "/account/"), ("DELETE", "/account/AB12CD34"), ("DELETE", "/account/ab12cd34/x"), ("GET", "/messages/5/attachment/x"),
              ("POST", "/messages"), ("GET", ""), ("GET", "/")]
    for method, path in denied:
        assert ask("user", path, method) == 403, (method, path)
        assert ask("owner", path, method) == 200, (method, path)                        # owners are not restricted


def test_the_email_pages_show_for_everyone_only_when_personal_mailboxes_are_on(mailapp, make, tmp_path):
    on = mailapp()
    as_(on, "bob", PW2, "10.9.6.1")
    assert "mail-inbox" in on.get("/shell-config.js").text and on.get("/email/inbox").status_code == 200
    plain_cfg = configmod.Config(data_dir=tmp_path / "d2", instance_dir=tmp_path / "i2", trusted_proxies=["testclient"],
                                 mail_address="me@server.test", mail_token="tok", mail_imap_host="imap.server.test")
    off = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), config=plain_cfg)
    as_(off, "bob", PW2, "10.9.6.2")
    assert "mail-inbox" not in off.get("/shell-config.js").text and off.get("/email/inbox").status_code == 403
    assert off.get(API + "/status").status_code == 403 and off.get(API + "/account").status_code in (403, 404)


def test_a_removed_person_loses_their_saved_sign_in_at_startup(make, tmp_path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    store = ma.Accounts(data)
    store.save("ghost", store.build("ghost@gmail.com", SECRET, preset="gmail", resolver=pub))
    c = configmod.Config(data_dir=data, instance_dir=tmp_path / "instance", trusted_proxies=["testclient"], mail_user_accounts=True)
    make(users=(("alice", "owner", PW),), config=c)
    assert ma.Accounts(data).list("ghost") == []
    mb.ACCOUNTS = None


# ---------------------------------------------------------------- the key can live in the settings, away from the data folder

KEY = "k" * 20 + "E" * 20 + "9" * 8
OTHER_KEY = "z" * 48


def test_a_key_from_the_settings_means_no_key_file_and_a_copied_data_folder_opens_nothing(tmp_path):
    store = ma.Accounts(tmp_path, KEY)
    store.save("bob", store.build("bob@gmail.com", SECRET, preset="gmail", resolver=pub))
    assert not store.keyfile.exists()
    assert store.settings("bob", resolver=pub).mail_token == SECRET
    stolen = ma.Accounts(tmp_path)                                     # someone with only the data folder: it makes its own key
    with pytest.raises(ma.AccountError, match="Connect your email again"):
        stolen.settings("bob", resolver=pub)
    with pytest.raises(ma.AccountError, match="Connect your email again"):
        ma.Accounts(tmp_path, OTHER_KEY).settings("bob", resolver=pub)  # and a wrong key opens nothing either
    assert SECRET not in store.path.read_text()


def test_the_key_must_be_long_enough(tmp_path):
    for short in ("x", "x" * 31):
        with pytest.raises(ma.AccountError):
            ma.Accounts(tmp_path, short)
        with pytest.raises(ValueError, match="at least 32"):
            configmod.load({"DATA_DIR": str(tmp_path), "MAIL_ACCOUNTS_KEY": short})
    assert configmod.load({"DATA_DIR": str(tmp_path), "MAIL_ACCOUNTS_KEY": KEY}).mail_accounts_key == KEY
    assert configmod.load({"DATA_DIR": str(tmp_path)}).mail_accounts_key == ""


def test_logins_saved_under_the_old_key_file_move_to_the_new_key_and_the_file_is_destroyed(tmp_path):
    old = ma.Accounts(tmp_path)
    for u in ("bob", "carol"):
        old.save(u, old.build(u + "@gmail.com", SECRET + u, preset="gmail", resolver=pub))
    assert old.keyfile.exists()
    new = ma.Accounts(tmp_path, KEY)
    assert new.migrate() == 2
    assert not new.keyfile.exists()
    assert new.settings("bob", resolver=pub).mail_token == SECRET + "bob" and new.settings("carol", resolver=pub).mail_token == SECRET + "carol"
    assert new.migrate() == 0                                          # nothing left to do
    with pytest.raises(ma.AccountError):
        ma.Accounts(tmp_path).settings("bob", resolver=pub)             # the old key is gone: a plain store cannot open them


def test_a_migration_that_cannot_open_every_login_changes_nothing(tmp_path):
    old = ma.Accounts(tmp_path)
    old.save("bob", old.build("bob@gmail.com", SECRET, preset="gmail", resolver=pub))
    old.save("carol", old.build("carol@gmail.com", SECRET, preset="gmail", resolver=pub))
    data = json.loads(old.path.read_text())
    data["carol"]["accounts"][0]["sealed"] = data["bob"]["accounts"][0]["sealed"]       # bound to bob, so it does not open for carol
    old.path.write_text(json.dumps(data))
    before = old.path.read_text()
    new = ma.Accounts(tmp_path, KEY)
    assert new.migrate() == 0 and new.keyfile.exists() and old.path.read_text() == before


def test_the_app_uses_the_key_from_the_settings_and_moves_existing_logins_at_start(make, tmp_path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    old = ma.Accounts(data)
    old.save("alice", old.build("alice@gmail.com", SECRET, preset="gmail", resolver=pub))
    c = configmod.Config(data_dir=data, instance_dir=tmp_path / "instance", trusted_proxies=["testclient"], mail_user_accounts=True, mail_accounts_key=KEY)
    make(users=(("alice", "owner", PW),), config=c)
    assert not (data / "mail-accounts.key").exists()
    assert ma.Accounts(data, KEY).settings("alice", resolver=pub).mail_token == SECRET
    mb.ACCOUNTS = None
