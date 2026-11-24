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
    info = store.info("bob")
    assert info["address"] == "Bob@Gmail.com" and SECRET not in json.dumps(info) and "sealed" not in info
    back = store.settings("bob", resolver=pub)
    assert back.mail_token == SECRET and back.mail_imap_tls_server_name == "imap.gmail.com" and back.personal is True
    data = json.loads(text)
    data["carol"] = data["bob"]                                  # a stolen entry moved to another person does not open
    store.path.write_text(json.dumps(data))
    with pytest.raises(ma.AccountError, match="Connect your email again"):
        store.settings("carol", resolver=pub)
    assert store.info("nobody") is None and store.settings("nobody", resolver=pub) is None


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
    assert store.delete("bob") and not store.delete("bob") and store.info("bob") is None
    assert store.purge(["Carol"]) == 1 and store.info("carol") and store.info("dave") is None
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
    assert r.status_code == 200 and r.json()["personal"]["address"] == "bob@gmail.com" and SECRET not in r.text
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
    assert c.delete(API + "/account", headers=H).json() == {"ok": True}
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
    assert c.delete(API + "/account", headers={"origin": "https://other.test"}).status_code == 403


def test_an_owner_keeps_the_server_mailbox_and_people_still_need_their_own(mailapp):
    c = mailapp(server=True)
    as_(c, "alice", PW, "10.9.5.1")
    s = c.get(API + "/status").json()
    assert s["connected"] and s["account"] == "server" and s["address"] == "owner@server.test"
    assert c.get(API + "/folders").json()["address"] == "owner@server.test"
    assert connect(c).status_code == 409                                              # no second mailbox for an owner who has the server's
    as_(c, "bob", PW2, "10.9.5.2")
    assert c.get(API + "/status").json()["connected"] is False                         # the server mailbox is never offered to anyone else
    assert c.get(API + "/folders").status_code == 409
    assert connect(c).status_code == 200 and c.get(API + "/folders").json()["address"] == "bob@gmail.com"


def test_other_routes_an_install_adds_stay_owner_only(mailapp):
    c = mailapp()
    guard = pagesmount.mail_guard(c.cfg, lambda r: r.who, lambda r: True, ma.Accounts(c.cfg.data_dir))

    def ask(role, path, method="GET"):
        req = SimpleNamespace(who={"user": "x", "role": role}, url=SimpleNamespace(path=API + path), method=method, state=SimpleNamespace())
        try:
            asyncio.run(guard(req))
            return 200
        except HTTPException as e:
            return e.status_code
    for path in ("/status", "/folders", "/messages", "/messages/5", "/messages/5/attachment/1", "/send", "/account"):
        assert ask("user", path) == 200, path
    for path in ("/blocklist/add", "/block", "/admin"):
        assert ask("user", path) == 403, path
    assert ask("owner", "/blocklist/add") == 200


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
    assert ma.Accounts(data).info("ghost") is None
    mb.ACCOUNTS = None
