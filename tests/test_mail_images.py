"""Remote images in mail: blocked by default, shown on request (one message) or always for a sender a person trusts, always through Atrium."""
import json
import os
import re
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import pytest

import config as configmod
import mail_accounts as ma
import mail_backend as mb
import mail_images as mi
from conftest import PW, PW2, login

H = {"origin": "https://app.test"}
API = "/api/mail"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
HTML = '<p>Hello</p><img src="http://img.shop.example.org/a.png" alt="a"><img src="https://img.shop.example.org/b.png"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">'


def raw(html=HTML, sender="news@shop.test", name="Shop"):
    return ("From: %s <%s>\r\nTo: me@x.test\r\nSubject: Sale\r\nDate: Mon, 24 Nov 2026 10:00:00 +0000\r\nMessage-ID: <1@shop.test>\r\n"
            "MIME-Version: 1.0\r\nContent-Type: text/html; charset=utf-8\r\n\r\n%s" % (name, sender, html)).encode()


def pub(host, port=443):
    return "93.184.216.34"


# ---------------------------------------------------------------- signed links

def test_a_signed_link_names_the_person_and_the_address_and_cannot_be_changed(tmp_path):
    s = mi.Signer(tmp_path)
    token = s.sign("Bob", "https://img.shop.example.org/a.png")
    claims = s.verify(token)
    assert claims["u"] == "https://img.shop.example.org/a.png" and claims["p"] == "bob"
    body, mac = token.split(".")
    for bad in (body + "." + mac[:-2] + "AA", "x" + body + "." + mac, token + "x", body, "", ".", "a.b", "!!.!!"):
        assert s.verify(bad) is None, bad
    other = mi.Signer(tmp_path / "elsewhere")
    assert other.verify(token) is None                                         # another key does not accept it
    assert s.verify(s.sign("bob", "https://img.shop.example.org/a.png", ttl=-5)) is None  # expired


@pytest.mark.skipif(os.name == "nt", reason="needs real file permissions")
def test_the_signing_key_and_the_safe_list_are_private(tmp_path):
    mi.Signer(tmp_path).sign("bob", "https://a.test/x.png")
    store = mi.SafeSenders(tmp_path)
    store.add("bob", "a@b.test")
    for f in (tmp_path / "mail-images.key", tmp_path / "mail-safe-senders.json"):
        assert f.stat().st_mode & 0o077 == 0, f


def test_only_web_addresses_get_a_link_and_plain_http_is_upgraded(tmp_path):
    s = mi.Signer(tmp_path)
    link = mi.proxied(s, "bob", " http://img.shop.example.org/a.png ")
    assert link.startswith("/api/mail-image/") and s.verify(link.split("/")[-1])["u"] == "https://img.shop.example.org/a.png"
    assert mi.proxied(s, "bob", "https://img.shop.example.org/b.png")
    for bad in ("data:image/png;base64,AAA", "cid:x", "javascript:alert(1)", "ftp://a.test/x.png", "//a.test/x.png", "", "https://a.test/" + "x" * 2100):
        assert mi.proxied(s, "bob", bad) is None, bad


# ---------------------------------------------------------------- fetching: only plain pictures, only from public hosts

def fake_request(responses):
    calls = []

    def _request(host, ip, target):
        calls.append((host, ip, target))
        return responses.pop(0) if isinstance(responses, list) else responses(host, ip, target)
    _request.calls = calls
    return _request


def test_a_picture_is_fetched_from_the_checked_address_and_served_as_what_it_is(monkeypatch):
    req = fake_request([(200, {}, PNG)])
    monkeypatch.setattr(mi, "_request", req)
    assert mi.fetch("https://img.shop.example.org/a.png?x=1", resolver=pub) == ("image/png", PNG)
    assert req.calls == [("img.shop.example.org", "93.184.216.34", "/a.png?x=1")]           # connects to the pinned address, not the name
    monkeypatch.setattr(mi, "_request", fake_request([(200, {"content-type": "text/html"}, JPEG)]))
    assert mi.fetch("https://img.shop.example.org/a", resolver=pub)[0] == "image/jpeg"       # judged by its bytes, not by what the server says


@pytest.mark.parametrize("body", [b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", b"<html><script>alert(1)</script></html>",
                                  b"GIF", b"", b"%PDF-1.4 ...", b"MZ\x90\x00"])
def test_anything_that_is_not_a_plain_picture_is_refused(monkeypatch, body):
    monkeypatch.setattr(mi, "_request", fake_request([(200, {"content-type": "image/png"}, body)]))
    with pytest.raises(mi.ImageError) as e:
        mi.fetch("https://img.shop.example.org/a.png", resolver=pub)
    assert e.value.status == 415


@pytest.mark.parametrize("url", ["http://img.shop.example.org/a.png", "https://img.shop.example.org:8443/a.png", "https://user:pw@img.shop.example.org/a.png", "ftp://img.shop.example.org/a.png",
                                 "https://10.0.0.5/a.png", "https://localhost/a.png", "https://mail.internal/a.png", "https:///a.png", "https://169.254.169.254/latest"])
def test_only_https_on_the_normal_port_to_a_public_name_is_fetched(monkeypatch, url):
    req = fake_request([(200, {}, PNG)])
    monkeypatch.setattr(mi, "_request", req)
    with pytest.raises(mi.ImageError) as e:
        mi.fetch(url, resolver=pub)
    assert e.value.status == 400 and req.calls == []                                   # nothing was even contacted


def test_a_name_that_resolves_inward_is_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", port))])
    req = fake_request([(200, {}, PNG)])
    monkeypatch.setattr(mi, "_request", req)
    with pytest.raises(mi.ImageError) as e:
        mi.fetch("https://img.shop.example.org/a.png")
    assert e.value.status == 400 and req.calls == []


def test_redirects_are_checked_at_every_step_and_limited(monkeypatch):
    seen = []

    def resolver(host, port=443):
        seen.append(host)
        if host == "inside.shop.example.org":
            raise ma.AccountError("private")
        return "93.184.216.34"
    monkeypatch.setattr(mi, "_request", fake_request([(302, {"location": "https://cdn.shop.example.org/real.png"}, b""), (200, {}, PNG)]))
    assert mi.fetch("https://img.shop.example.org/a.png", resolver=resolver)[0] == "image/png" and seen == ["img.shop.example.org", "cdn.shop.example.org"]
    monkeypatch.setattr(mi, "_request", fake_request([(301, {"location": "https://inside.shop.example.org/x.png"}, b""), (200, {}, PNG)]))
    with pytest.raises(mi.ImageError) as e:
        mi.fetch("https://img.shop.example.org/a.png", resolver=resolver)                      # a redirect to an internal name
    assert e.value.status == 400
    monkeypatch.setattr(mi, "_request", fake_request([(302, {"location": "http://img.shop.example.org/plain.png"}, b"")]))
    with pytest.raises(mi.ImageError) as e:
        mi.fetch("https://img.shop.example.org/a.png", resolver=resolver)                      # or one that downgrades to http
    assert e.value.status == 400
    monkeypatch.setattr(mi, "_request", lambda *a: (302, {"location": "/again"}, b""))
    with pytest.raises(mi.ImageError) as e:
        mi.fetch("https://img.shop.example.org/a.png", resolver=resolver)
    assert e.value.status == 502
    monkeypatch.setattr(mi, "_request", fake_request([(404, {}, b"")]))
    with pytest.raises(mi.ImageError) as e:
        mi.fetch("https://img.shop.example.org/a.png", resolver=resolver)
    assert e.value.status == 404


def test_network_trouble_and_oversize_are_reported_without_detail(monkeypatch):
    class Boom:
        def __init__(self, host, ip):
            pass

        def request(self, *a, **k):
            raise OSError("connection reset by 93.184.216.34")

        def close(self):
            pass
    monkeypatch.setattr(mi, "_Pinned", Boom)
    with pytest.raises(mi.ImageError) as e:
        mi._request("img.shop.example.org", "93.184.216.34", "/a.png")
    assert e.value.status == 502 and "93.184" not in e.value.message

    class Big:
        def __init__(self, host, ip):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            class R:
                status = 200

                def getheaders(self):
                    return [("Content-Length", str(mi.MAX_IMAGE + 1))]

                def read(self, n):
                    return b"x" * n
            return R()

        def close(self):
            pass
    monkeypatch.setattr(mi, "_Pinned", Big)
    with pytest.raises(mi.ImageError) as e:
        mi._request("img.shop.example.org", "93.184.216.34", "/a.png")
    assert e.value.status == 413


# ---------------------------------------------------------------- the list of senders a person trusts

def test_trusted_senders_are_addresses_per_person_and_limited(tmp_path):
    s = mi.SafeSenders(tmp_path)
    s.add("Bob", "News@Shop.test")
    s.add("bob", "news@shop.test")                                                     # no duplicate
    assert s.list("bob") == ["news@shop.test"] and s.has("BOB", "NEWS@shop.test") and not s.has("carol", "news@shop.test")
    assert not s.has("bob", "") and not s.has("bob", "other@shop.test")
    for bad in ("", "nope", "a@b", "Shop <news@shop.test>", "a b@shop.test"):
        with pytest.raises(ValueError):
            s.add("bob", bad)
    assert s.remove("bob", "News@shop.test") and not s.remove("bob", "news@shop.test") and s.list("bob") == []
    for n in range(mi.MAX_SAFE):
        s.add("carol", "s%d@shop.test" % n)
    with pytest.raises(ValueError, match="up to"):
        s.add("carol", "one-more@shop.test")
    s.add("dave", "a@shop.test")
    assert s.purge(["carol"]) == 1 and s.list("dave") == [] and len(s.list("carol")) == mi.MAX_SAFE


# ---------------------------------------------------------------- in the app

@pytest.fixture()
def app(make, tmp_path, monkeypatch):
    monkeypatch.setattr(ma, "resolve_public", lambda host, port=993: "93.184.216.34")
    monkeypatch.setattr(mb, "_test_login", lambda c: None)
    state = {"raw": raw()}
    monkeypatch.setattr(mb, "_fetch_message", lambda folder, uid: state["raw"])
    c0 = configmod.Config(data_dir=tmp_path / "data", instance_dir=tmp_path / "instance", trusted_proxies=["testclient"], mail_user_accounts=True,
                          mail_address="owner@server.test", mail_token="tok", mail_imap_host="imap.server.test")
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), config=c0)
    c.state = state
    yield c
    mb.ACCOUNTS = None


def as_(c, user, pw, ip):
    c.post("/logout", headers=H)
    assert login(c, user, pw, ip=ip).status_code == 303


def tokens(html):
    return re.findall(r'src="/api/mail-image/([^"]+)"', html)


def test_images_are_blocked_until_the_person_asks(app):
    as_(app, "alice", PW, "10.20.0.1")
    m = app.get(API + "/messages/7?folder=INBOX").json()
    assert m["blocked_images"] is True and m["images_shown"] is False and m["safe_sender"] is False and m["sender_email"] == "news@shop.test"
    assert "data-blocked-src" in m["html"] and "/api/mail-image/" not in m["html"] and "img.shop.example.org" in m["html"]        # nothing loads
    shown = app.get(API + "/messages/7?folder=INBOX&images=1").json()
    assert shown["images_shown"] is True and shown["blocked_images"] is False and "data-blocked-src" not in shown["html"]
    assert len(tokens(shown["html"])) == 2 and "data:image/gif" in shown["html"]                                        # the inline picture stays
    assert "http://img.shop.example.org" not in shown["html"] and "https://img.shop.example.org" not in shown["html"]                 # the sender is never contacted by the page
    urls = sorted(mb.SIGNER.verify(t)["u"] for t in tokens(shown["html"]))
    assert urls == ["https://img.shop.example.org/a.png", "https://img.shop.example.org/b.png"]                                        # http was upgraded
    assert app.get(API + "/messages/7?folder=INBOX").json()["images_shown"] is False                                    # asking once does not stick


def test_the_image_link_works_without_a_session_serves_only_pictures_and_cannot_be_forged(app, monkeypatch):
    as_(app, "alice", PW, "10.20.0.2")
    token = tokens(app.get(API + "/messages/7?folder=INBOX&images=1").json()["html"])[0]
    app.post("/logout", headers=H)                                                                                    # the sandboxed frame sends no cookie
    monkeypatch.setattr(mi, "fetch", lambda url, resolver=None: ("image/png", PNG))
    r = app.get("/api/mail-image/" + token)
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    body, mac = token.split(".")
    for bad in (body + "." + mac[:-1] + ("A" if mac[-1] != "A" else "B"), "garbage", body, mac + "." + body):
        assert app.get("/api/mail-image/" + bad).status_code == 403, bad
    assert app.get("/api/mail-image/" + mb.SIGNER.sign("alice", "https://a.test/x.png", ttl=-1)).status_code == 403

    def refuse(url, resolver=None):
        raise mi.ImageError(415, "That is not a picture Atrium will show.")
    monkeypatch.setattr(mi, "fetch", refuse)
    r = app.get("/api/mail-image/" + token)
    assert r.status_code == 415 and "picture" in r.json()["detail"]


def test_always_show_images_from_a_sender_and_stop(app):
    as_(app, "alice", PW, "10.20.0.3")
    assert app.get(API + "/safe-senders").json()["senders"] == []
    assert app.post(API + "/safe-senders", headers=H, json={"address": "News@Shop.test"}).json()["senders"] == ["news@shop.test"]
    m = app.get(API + "/messages/7?folder=INBOX").json()
    assert m["safe_sender"] is True and m["images_shown"] is True and m["blocked_images"] is False and len(tokens(m["html"])) == 2
    app.state_raw = app.state["raw"]
    app.state["raw"] = raw(sender="other@shop.test")                                                                  # a different sender is still blocked
    other = app.get(API + "/messages/7?folder=INBOX").json()
    assert other["safe_sender"] is False and other["images_shown"] is False and other["blocked_images"] is True
    app.state["raw"] = raw(sender="evil@bad.test", name='"news@shop.test"')                                           # a display name proves nothing
    spoof = app.get(API + "/messages/7?folder=INBOX").json()
    assert spoof["sender_email"] == "evil@bad.test" and spoof["safe_sender"] is False and spoof["blocked_images"] is True
    app.state["raw"] = raw(sender="evil@bad.test", name="news@shop.test")                                             # a From line a parser has to guess at
    guessed = app.get(API + "/messages/7?folder=INBOX").json()
    assert guessed["sender_email"] == "" and guessed["safe_sender"] is False and guessed["blocked_images"] is True
    app.state["raw"] = raw().replace(b"From: Shop <news@shop.test>", b"From: Shop <news@shop.test>, Other <other@bad.test>")     # two addresses
    two = app.get(API + "/messages/7?folder=INBOX").json()
    assert two["sender_email"] == "" and two["safe_sender"] is False and two["blocked_images"] is True
    assert app.delete(API + "/safe-senders?address=NEWS@shop.test", headers=H).json()["senders"] == []
    app.state["raw"] = app.state_raw
    assert app.get(API + "/messages/7?folder=INBOX").json()["blocked_images"] is True


def test_each_person_has_their_own_list_and_the_routes_are_checked(app):
    as_(app, "alice", PW, "10.20.0.4")
    app.post(API + "/safe-senders", headers=H, json={"address": "news@shop.test"})
    as_(app, "bob", PW2, "10.20.0.5")
    assert connect_bob(app).status_code == 200
    assert app.get(API + "/safe-senders").json()["senders"] == []                                                      # alice's trust is not bob's
    assert app.get(API + "/messages/7?folder=INBOX").json()["blocked_images"] is True
    assert app.post(API + "/safe-senders", headers=H, json={"address": "news@shop.test"}).status_code == 200            # a normal person may use it
    assert app.get(API + "/messages/7?folder=INBOX").json()["images_shown"] is True
    for bad in ("nope", "", "a@b", "x" * 400 + "@shop.test"):
        assert app.post(API + "/safe-senders", headers=H, json={"address": bad}).status_code in (400, 422), bad
    assert app.post(API + "/safe-senders", headers={"origin": "https://other.test"}, json={"address": "a@shop.test"}).status_code == 403
    assert app.delete(API + "/safe-senders?address=news@shop.test", headers={"origin": "https://other.test"}).status_code == 403
    app.post("/logout", headers=H)
    assert app.get(API + "/safe-senders").status_code == 401 and app.post(API + "/safe-senders", headers=H, json={"address": "a@shop.test"}).status_code == 401
    as_(app, "alice", PW, "10.20.0.6")
    assert app.get(API + "/safe-senders").json()["senders"] == ["news@shop.test"]                                     # and bob's change never touched hers


def connect_bob(app):
    return app.put(API + "/account", headers=H, json={"address": "bob@gmail.com", "password": "bob-app-pass", "preset": "gmail"})


def test_the_image_and_safe_sender_routes_are_in_the_non_owner_allowlist():
    import pagesmount
    for method, path in (("GET", "/safe-senders"), ("POST", "/safe-senders"), ("DELETE", "/safe-senders")):
        assert pagesmount.personal_mail_route(method, path), (method, path)
    for method, path in (("PUT", "/safe-senders"), ("GET", "/safe-senders/x"), ("GET", "/mail-image/abc"), ("GET", "/remote-image")):
        assert not pagesmount.personal_mail_route(method, path), (method, path)


def test_the_page_has_no_other_way_to_load_a_remote_image(app):
    """The page's own security policy still only allows images from this site, so a link that bypassed the proxy would simply not load."""
    as_(app, "alice", PW, "10.20.0.7")
    csp = app.get("/").headers.get("content-security-policy", "")
    assert re.search(r"img-src 'self' data: blob:(;|$)", csp) and "https:" not in csp.split("img-src")[1].split(";")[0]
