"""Media page: each person connects their own Jellyfin or Emby server and browses and plays it through Atrium. Synthetic only (a fake server, no network)."""
import json
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import httpx
import pytest

import config as configmod
import media_backend as mbk
import media_servers as ms
from conftest import PW, PW2, login

H = {"origin": "https://app.test"}
API = "/api/media"
SECRET = "media-pass-" + "k3j8w2"
URL = "https://media.example.org"
ITEM = "0123456789abcdef0123456789abcdef"
SHOW = "fedcba9876543210fedcba9876543210"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40


class Fake:
    """A pretend Jellyfin: remembers what it was asked."""

    def __init__(self):
        self.seen = []
        self.password = SECRET
        self.token = "tok-" + "a" * 20

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.seen.append((request.method, path, str(request.url.query, "ascii"), dict(request.headers)))
        if path == "/System/Info/Public":
            return httpx.Response(200, json={"ServerName": "Home", "Version": "10.9"})
        if path == "/Users/AuthenticateByName":
            body = json.loads(request.content)
            if body["Pw"] != self.password:
                return httpx.Response(401)
            return httpx.Response(200, json={"AccessToken": self.token, "User": {"Id": "u" * 32, "Name": body["Username"]}})
        if request.headers.get("x-emby-token") != self.token:
            return httpx.Response(401)
        if path.endswith("/Views"):
            return httpx.Response(200, json={"Items": [{"Id": SHOW, "Name": "Shows", "IsFolder": True, "CollectionType": "tvshows", "ImageTags": {"Primary": "x"}}]})
        if path.endswith("/Items/Resume"):
            return httpx.Response(200, json={"Items": [{"Id": ITEM, "Name": "Movie", "Type": "Movie", "MediaType": "Video", "RunTimeTicks": 6000000000,
                                                         "UserData": {"PlaybackPositionTicks": 1500000000}}]})
        if path.endswith("/Items/Latest"):
            return httpx.Response(500)                                  # a failing extra must not break the page
        if path.endswith("/Items") and request.url.params.get("SearchTerm"):
            return httpx.Response(200, json={"Items": [{"Id": ITEM, "Name": "Found", "Type": "Movie", "MediaType": "Video"}], "TotalRecordCount": 1})
        if path.endswith("/Items"):
            return httpx.Response(200, json={"Items": [{"Id": ITEM, "Name": "Ep 1", "Type": "Episode", "MediaType": "Video", "ParentIndexNumber": 1, "IndexNumber": 1},
                                                       {"Id": "bad id!", "Name": "dropped"}], "TotalRecordCount": 2})
        if path.endswith("/Users/%s/Items/%s" % ("u" * 32, ITEM)):
            return httpx.Response(200, json={"Id": ITEM, "Name": "Movie", "Type": "Movie", "MediaType": "Video", "Genres": ["Drama"],
                                             "MediaSources": [{"Container": "mkv"}]})
        if path.startswith("/Items/") and path.endswith("/Images/Primary"):
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if path.startswith("/Videos/") and path.endswith("/stream"):
            data = b"v" * 100
            if request.headers.get("range"):
                return httpx.Response(206, content=data[10:20], headers={"content-type": "video/mp4", "content-range": "bytes 10-19/100", "accept-ranges": "bytes"})
            return httpx.Response(200, content=data, headers={"content-type": "video/mp4", "accept-ranges": "bytes"})
        if path.endswith("/stream.mp4"):
            return httpx.Response(200, content=b"c" * 50, headers={"content-type": "video/mp4"})
        if "/PlayedItems/" in path:
            return httpx.Response(200, json={})
        return httpx.Response(404)


@pytest.fixture()
def app(make, tmp_path, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(mbk, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(ms, "resolve", lambda host, port, private_ok: "93.184.216.34")

    def build(**k):
        k.setdefault("data_dir", tmp_path / "data")
        c = configmod.Config(instance_dir=tmp_path / "instance", trusted_proxies=["testclient"], **k)
        client = make(users=(("alice", "owner", PW), ("bob", "user", PW2), ("carol", "user", PW2)), config=c)
        client.fake, client.cfg = fake, c
        return client
    return build


def as_(client, user, pw, ip):
    client.post("/logout", headers=H)
    assert login(client, user, pw, ip=ip).status_code == 303


def connect(client, **k):
    body = {"kind": "jellyfin", "url": URL, "username": "bob", "password": SECRET}
    body.update(k)
    return client.post(API + "/servers", headers=H, json=body)


# ---------------------------------------------------------------- the address checks
@pytest.mark.parametrize("value,expected", [("https://media.example.org", "https://media.example.org"), ("media.example.org", "https://media.example.org"),
                                            ("HTTPS://Media.Example.org:8920/jellyfin/", "https://media.example.org:8920/jellyfin")])
def test_public_addresses_are_tidied(value, expected):
    assert ms.clean_url(value, False) == expected


@pytest.mark.parametrize("value", ["http://media.example.org", "https://192.168.1.5:8096", "https://nas", "https://nas.local", "https://[::1]/",
                                   "https://user:pw@media.example.org", "https://media.example.org/?x=1", "ftp://media.example.org", "https://media.example.org/a b",
                                   "https://media.example.org/../x", "", "https://"])
def test_a_person_may_not_aim_atrium_at_anything_but_a_public_https_name(value):
    with pytest.raises(ms.MediaError):
        ms.clean_url(value, False)


def test_an_owner_on_an_install_that_allows_it_may_use_the_local_network():
    assert ms.clean_url("192.168.1.20:8096", True) == "http://192.168.1.20:8096"
    assert ms.clean_url("http://nas/", True) == "http://nas"
    assert ms.clean_url("https://nas:8920", True) == "https://nas:8920"


def _infos(*ips):
    return lambda host, port, **k: [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]


@pytest.mark.parametrize("ip", ["10.0.0.5", "192.168.1.5", "172.16.0.9", "fd00::1", "100.64.0.1"])
def test_private_answers_are_refused_for_people_and_allowed_for_owners(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", _infos(ip))
    with pytest.raises(ms.MediaError):
        ms.resolve("media.example.org", 443, False)
    assert ms.resolve("media.example.org", 443, True) == ip


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "169.254.169.254", "0.0.0.0", "224.0.0.1", "::ffff:127.0.0.1"])
def test_special_addresses_are_never_allowed_not_even_for_owners(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", _infos(ip))
    for owner in (False, True):
        with pytest.raises(ms.MediaError):
            ms.resolve("media.example.org", 443, owner)


def test_one_private_answer_among_public_ones_refuses_a_public_name(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _infos("93.184.216.34", "10.0.0.5"))
    with pytest.raises(ms.MediaError):
        ms.resolve("media.example.org", 443, False)


# ---------------------------------------------------------------- connecting
def test_a_person_connects_and_the_password_and_token_are_never_stored_in_clear(app, tmp_path):
    c = app()
    as_(c, "bob", PW2, "10.0.0.2")
    r = connect(c)
    assert r.status_code == 200, r.text
    server = r.json()["server"]
    assert server["label"] == "Home" and server["user_name"] == "bob" and "token" not in server and "sealed" not in server
    listing = c.get(API + "/servers").json()
    assert [s["id"] for s in listing["servers"]] == [server["id"]] and listing["local_ok"] is False
    disk = (tmp_path / "data" / "media-servers.json").read_text()
    assert SECRET not in disk and c.fake.token not in disk and URL in disk
    assert SECRET not in json.dumps(listing) and c.fake.token not in json.dumps(listing)
    login_call = next(s for s in c.fake.seen if s[1] == "/Users/AuthenticateByName")
    assert login_call[3]["host"] == "media.example.org" and "token" not in login_call[3]["x-emby-authorization"].lower()


def test_wrong_password_unreachable_server_and_not_a_media_server_save_nothing(app, monkeypatch):
    c = app()
    as_(c, "bob", PW2, "10.0.0.2")
    assert connect(c, password="nope").status_code == 400
    assert "user name and password" in connect(c, password="nope").json()["detail"]
    for bad in ({"url": "https://192.168.1.4"}, {"url": "http://media.example.org"}, {"kind": "plex"}, {"username": ""}):
        assert connect(c, **bad).status_code == 400
    monkeypatch.setattr(mbk, "TRANSPORT", httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))))
    assert connect(c).status_code == 502
    monkeypatch.setattr(mbk, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, json={"hello": "world"})))
    assert "did not answer like" in connect(c).json()["detail"]
    assert c.get(API + "/servers").json()["servers"] == []


def test_a_name_that_resolves_inward_is_refused_at_connect_time(app, monkeypatch):
    c = app()
    as_(c, "bob", PW2, "10.0.0.2")
    monkeypatch.undo()
    monkeypatch.setattr(mbk, "TRANSPORT", httpx.MockTransport(c.fake))
    monkeypatch.setattr(socket, "getaddrinfo", _infos("10.0.0.5"))
    r = connect(c)
    assert r.status_code == 400 and "private" in r.json()["detail"]
    assert not c.fake.seen, "nothing was sent to the inside address"


def test_only_owners_on_installs_that_allow_it_may_connect_to_the_local_network(app, tmp_path):
    plain = app()
    as_(plain, "alice", PW, "10.0.0.1")
    assert connect(plain, url="http://192.168.1.20:8096").status_code == 400
    allowed = app(media_allow_private=True, data_dir=tmp_path / "second")
    as_(allowed, "bob", PW2, "10.0.0.2")
    assert connect(allowed, url="http://192.168.1.20:8096").status_code == 400
    as_(allowed, "alice", PW, "10.0.0.1")
    assert allowed.get(API + "/servers").json()["local_ok"] is True
    r = connect(allowed, url="http://192.168.1.20:8096", username="alice")
    assert r.status_code == 200, r.text
    assert next(s for s in allowed.fake.seen if s[1] == "/Users/AuthenticateByName")[3]["host"] == "192.168.1.20:8096"


def test_connection_attempts_are_limited_and_need_sign_in_and_origin(app):
    c = app()
    assert c.get(API + "/servers").status_code == 401
    assert c.post(API + "/servers", json={}).status_code == 401
    as_(c, "bob", PW2, "10.0.0.2")
    assert c.post(API + "/servers", headers={"origin": "https://evil.test"}, json={"kind": "jellyfin"}).status_code == 403
    codes = [connect(c, password="nope").status_code for _ in range(ms.ATTEMPTS + 2)]
    assert codes[-1] == 429 and codes[0] == 400


# ---------------------------------------------------------------- browsing and playing
@pytest.fixture()
def bob(app):
    c = app()
    as_(c, "bob", PW2, "10.0.0.2")
    c.sid = connect(c).json()["server"]["id"]
    return c


def test_home_lists_libraries_and_survives_a_failing_extra(bob):
    r = bob.get("%s/servers/%s/home" % (API, bob.sid))
    assert r.status_code == 200
    home = r.json()
    assert home["libraries"][0]["name"] == "Shows" and home["libraries"][0]["folder"] and home["libraries"][0]["has_image"]
    assert home["resume"][0]["progress"] == 25 and home["latest"] == []
    first = next(s for s in bob.fake.seen if s[1].endswith("/Views"))
    assert first[3]["x-emby-token"] == bob.fake.token and first[3]["host"] == "media.example.org"


def test_browsing_searching_and_item_details(bob):
    base = "%s/servers/%s" % (API, bob.sid)
    items = bob.get(base + "/items?parent=" + SHOW).json()
    assert [i["name"] for i in items["items"]] == ["Ep 1"], "an item with a bad id is dropped"
    assert items["items"][0]["season"] == 1 and items["items"][0]["episode"] == 1
    assert bob.get(base + "/items?q=found").json()["items"][0]["name"] == "Found"
    assert bob.get(base + "/items").status_code == 400
    assert bob.get(base + "/items?parent=../x").status_code == 400
    one = bob.get("%s/item/%s" % (base, ITEM)).json()
    assert one["container"] == "mkv" and one["play"] == "convert" and one["genres"] == ["Drama"]
    assert bob.get("%s/item/%s" % (base, "bad id")).status_code in (400, 404)


def test_pictures_come_through_the_server_and_only_if_they_are_pictures(bob, monkeypatch):
    base = "%s/servers/%s" % (API, bob.sid)
    r = bob.get("%s/image/%s" % (base, ITEM))
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    monkeypatch.setattr(mbk, "TRANSPORT", httpx.MockTransport(lambda req: httpx.Response(200, content=svg, headers={"content-type": "image/svg+xml"})))
    assert bob.get("%s/image/%s" % (base, ITEM)).status_code == 404


def test_playback_streams_with_range_and_converted_copies(bob):
    base = "%s/servers/%s/stream/%s" % (API, bob.sid, ITEM)
    full = bob.get(base + "?mode=direct&kind=video")
    assert full.status_code == 200 and full.content == b"v" * 100 and full.headers["content-type"] == "video/mp4" and full.headers["accept-ranges"] == "bytes"
    part = bob.get(base + "?mode=direct&kind=video", headers={"range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == b"v" * 10 and part.headers["content-range"] == "bytes 10-19/100"
    conv = bob.get(base + "?mode=convert&kind=video", headers={"range": "bytes=10-19"})
    assert conv.status_code == 200 and conv.content == b"c" * 50
    call = [s for s in bob.fake.seen if s[1].endswith("/stream.mp4")][-1]
    assert "VideoCodec=h264" in call[2] and "range" not in call[3], "a converted copy is not seekable"
    assert "x-emby-token" in [k.lower() for k in call[3]] and bob.fake.token not in str(part.headers) and bob.fake.token not in base


def test_a_reply_that_is_not_audio_or_video_is_not_passed_on(bob, monkeypatch):
    html = httpx.Response(200, content=b"<script>x</script>", headers={"content-type": "text/html"})
    monkeypatch.setattr(mbk, "TRANSPORT", httpx.MockTransport(lambda req: html))
    assert bob.get("%s/servers/%s/stream/%s?mode=direct&kind=video" % (API, bob.sid, ITEM)).status_code == 502


def test_marking_watched_and_an_expired_sign_in(bob):
    assert bob.post("%s/servers/%s/played/%s" % (API, bob.sid, ITEM), headers=H).json() == {"ok": True}
    assert bob.post("%s/servers/%s/played/%s" % (API, bob.sid, ITEM), json={}).status_code in (200, 403)    # no Origin header: allowed like other actions
    bob.fake.token = "changed"
    r = bob.get("%s/servers/%s/home" % (API, bob.sid))
    assert r.status_code == 409 and "connect again" in r.json()["detail"]


# ---------------------------------------------------------------- people stay apart
def test_nobody_can_use_or_remove_someone_elses_connection(app):
    c = app()
    as_(c, "bob", PW2, "10.0.0.2")
    sid = connect(c).json()["server"]["id"]
    as_(c, "carol", PW2, "10.0.0.3")
    assert c.get(API + "/servers").json()["servers"] == []
    for path in ("/home", "/items?parent=" + SHOW, "/item/" + ITEM, "/image/" + ITEM, "/stream/" + ITEM):
        assert c.get("%s/servers/%s%s" % (API, sid, path)).status_code == 404
    assert c.delete("%s/servers/%s" % (API, sid), headers=H).status_code == 404
    as_(c, "alice", PW, "10.0.0.1")                           # not even an owner
    assert c.get("%s/servers/%s/home" % (API, sid)).status_code == 404
    as_(c, "bob", PW2, "10.0.0.2")
    assert c.get("%s/servers/%s/home" % (API, sid)).status_code == 200
    assert c.get("%s/servers/%s/home" % (API, "../x")).status_code == 404
    assert c.delete("%s/servers/%s" % (API, sid), headers=H).status_code == 200
    assert c.get("%s/servers/%s/home" % (API, sid)).status_code == 404


def test_the_connection_limit_and_duplicates(app, monkeypatch):
    c = app()
    as_(c, "bob", PW2, "10.0.0.2")
    assert connect(c).status_code == 200
    assert connect(c).status_code == 409, "the same server and user twice"
    monkeypatch.setattr(ms, "ATTEMPTS", 100)
    for i in range(ms.MAX_SERVERS - 1):
        assert connect(c, url="https://m%d.example.org" % i).status_code == 200
    assert "up to" in connect(c, url="https://last.example.org").json()["detail"]


def test_removed_people_lose_their_connections(tmp_path):
    store = ms.Servers(tmp_path)
    store.add("gone", "jellyfin", URL, "Home", "u" * 32, "gone", "tok")
    store.add("kept", "emby", URL, "Home", "u" * 32, "kept", "tok")
    assert store.purge(["kept"]) == 1 and store.list("gone") == [] and len(store.list("kept")) == 1


def test_the_token_is_bound_to_the_person_and_the_key_comes_from_the_settings(tmp_path):
    key = "k" * 40
    store = ms.Servers(tmp_path, key)
    rec = store.add("bob", "jellyfin", URL, "Home", "u" * 32, "bob", "tok-123")
    assert store.get("bob", rec["id"])["token"] == "tok-123"
    data = json.loads((tmp_path / "media-servers.json").read_text())
    data["carol"] = data.pop("bob")                          # moving the saved login to another person must not open it
    (tmp_path / "media-servers.json").write_text(json.dumps(data))
    assert store.get("carol", rec["id"]) is None
    assert not (tmp_path / "media-servers.key").exists(), "no key file when the key is in the settings"
    assert ms.Servers(tmp_path / "other", "z" * 40).path != store.path
    assert (ms.Servers(tmp_path, "z" * 40).get("carol", rec["id"])) is None


def test_the_page_is_listed_for_everyone(app):
    c = app()
    as_(c, "bob", PW2, "10.0.0.2")
    page = c.get("/media")
    assert page.status_code == 200
