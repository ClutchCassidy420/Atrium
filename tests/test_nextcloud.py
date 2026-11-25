"""Files and Calendar over WebDAV and CalDAV: synthetic tests with a scripted server (no real Nextcloud, no network)."""
import sys
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import httpx
import pytest

import config as configmod
import nextcloud_backend as nc
from conftest import login

H = {"origin": "https://app.test"}


class Server:
    """Records every request and answers from a script: {(method, path-suffix): (status, headers, body)}."""

    def __init__(self):
        self.seen, self.script = [], {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        for (method, suffix), (status, headers, body) in self.script.items():
            if request.method == method and request.url.path.endswith(suffix):
                return httpx.Response(status, headers=headers, content=body)
        return httpx.Response(404)

    def last(self, method):
        return [r for r in self.seen if r.method == method][-1]


@pytest.fixture()
def dav(make, tmp_path, monkeypatch):
    c = configmod.Config(data_dir=tmp_path / "d", instance_dir=tmp_path / "i", trusted_proxies=["testclient"],
                         nextcloud_url="https://cloud.example.test/nc", nextcloud_user="alice", nextcloud_password="app-pw", nextcloud_host="")
    client = make(config=c)
    login(client)
    server = Server()
    monkeypatch.setattr(nc, "_client", lambda: httpx.AsyncClient(base_url=c.nextcloud_url.rstrip("/"), auth=(c.nextcloud_user, c.nextcloud_password),
                                                                transport=httpx.MockTransport(server)))
    client.server, client.cfg = server, c
    return client


# ---------------------------------------------------------------- the pages exist only when a Nextcloud is configured

def test_files_and_calendar_appear_only_when_configured(make, dav):
    plain = make()
    login(plain)
    text = plain.get("/shell-config.js").text
    assert "calendar-page" not in text and "files-page" not in text and plain.get("/api/nextcloud/status").status_code == 404
    text = dav.get("/shell-config.js").text
    assert "calendar-page" in text and "files-page" in text


# ---------------------------------------------------------------- uploads never silently replace a file

def test_upload_does_not_replace_an_existing_file_by_default(dav):
    dav.server.script[("PUT", "/report.txt")] = (412, {}, b"")
    r = dav.post("/api/nextcloud/files/upload?path=Docs&name=report.txt", content=b"new", headers=H)
    assert r.status_code == 409 and "already there" in r.json()["detail"]
    assert dav.server.last("PUT").headers["if-none-match"] == "*"


def test_upload_replaces_only_when_asked(dav):
    dav.server.script[("PUT", "/report.txt")] = (204, {}, b"")
    r = dav.post("/api/nextcloud/files/upload?path=Docs&name=report.txt&replace=1", content=b"new", headers=H)
    assert r.status_code == 200 and "if-none-match" not in dav.server.last("PUT").headers


def test_new_file_uploads_under_the_configured_base_path(dav):
    dav.server.script[("PUT", "/a.txt")] = (201, {}, b"")
    assert dav.post("/api/nextcloud/files/upload?name=a.txt", content=b"x", headers=H).status_code == 200
    assert dav.server.last("PUT").url.path == "/nc/remote.php/dav/files/alice/a.txt"


# ---------------------------------------------------------------- move builds its own absolute destination

def test_move_destination_uses_the_configured_scheme_host_and_base_path(dav):
    dav.server.script[("MOVE", "/a.txt")] = (201, {}, b"")
    assert dav.post("/api/nextcloud/files/move", json={"path": "a.txt", "to": "Docs/b.txt"}, headers=H).status_code == 200
    req = dav.server.last("MOVE")
    assert req.headers["destination"] == "https://cloud.example.test/nc/remote.php/dav/files/alice/Docs/b.txt" and req.headers["overwrite"] == "F"


def test_move_destination_honours_the_host_header_setting(dav):
    dav.cfg.nextcloud_host = "files.example.test"
    dav.server.script[("MOVE", "/a.txt")] = (201, {}, b"")
    dav.post("/api/nextcloud/files/move", json={"path": "a.txt", "to": "b.txt"}, headers=H)
    assert dav.server.last("MOVE").headers["destination"].startswith("https://files.example.test/nc/")


def test_move_will_not_overwrite(dav):
    dav.server.script[("MOVE", "/a.txt")] = (412, {}, b"")
    assert dav.post("/api/nextcloud/files/move", json={"path": "a.txt", "to": "b.txt"}, headers=H).status_code == 409


# ---------------------------------------------------------------- paths and access

@pytest.mark.parametrize("bad", ["../etc", "a/../../b", "a/\x00b"])
def test_paths_cannot_climb_out(dav, bad):
    assert dav.get("/api/nextcloud/files", params={"path": bad}).status_code == 400


def test_writes_from_another_origin_are_refused(dav):
    dav.server.script[("PUT", "/a.txt")] = (201, {}, b"")
    r = dav.post("/api/nextcloud/files/upload?name=a.txt", content=b"x", headers={"origin": "https://evil.test"})
    assert r.status_code in (400, 403)
    assert not [q for q in dav.server.seen if q.method == "PUT"]


# ---------------------------------------------------------------- calendar text handling

ICS = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\nDTSTART;TZID=America/Chicago:20261125T090000\r\nDTEND;TZID=America/Chicago:20261125T100000\r\n"
       "SUMMARY:Dentist\\, 9am\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:u2\r\nDTSTART;VALUE=DATE:20261126\r\nSUMMARY:Holiday\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def test_events_are_parsed_with_zones_and_all_day():
    a, b = nc._parse_event(ICS)
    assert a["title"] == "Dentist, 9am" and a["start"] == "2026-11-25T15:00:00Z" and a["all_day"] is False
    assert b["all_day"] is True and b["start"] == "2026-11-26"


def test_event_text_round_trips_through_escaping_and_folding():
    ev = nc.EventIn(title="Lunch; with, commas and a long tail " + "x" * 120, start="2026-11-25T12:00:00Z", end="2026-11-25T13:00:00Z")
    text = nc._wrap(["BEGIN:VEVENT", "UID:u"] + nc._event_lines(ev) + ["END:VEVENT"])
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert nc._parse_event(text)[0]["title"] == ev.title


def test_event_end_before_start_is_refused(dav):
    body = {"title": "x", "start": "2026-11-25T12:00:00Z", "end": "2026-11-25T11:00:00Z"}
    assert dav.post("/api/nextcloud/events", json=body, headers=H).status_code == 400


def test_repeating_events_are_not_edited_in_place(dav):
    ics = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u\r\nRRULE:FREQ=WEEKLY\r\nDTSTART:20261125T120000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    dav.server.script[("GET", "/work/u.ics")] = (200, {"etag": '"1"'}, ics.encode())
    body = {"title": "x", "start": "2026-11-25T12:00:00Z", "end": "2026-11-25T13:00:00Z"}
    r = dav.put("/api/nextcloud/events/work/u.ics", json=body, headers=H)
    assert r.status_code == 400 and not [q for q in dav.server.seen if q.method == "PUT"]


def test_event_range_is_limited(dav):
    r = dav.get("/api/nextcloud/events", params={"start": "2026-01-01T00:00:00Z", "end": "2027-06-01T00:00:00Z"})
    assert r.status_code == 400


def test_short_calls_share_one_client_per_loop_and_rebuild_when_settings_change():
    import asyncio
    c = SimpleNamespace(nextcloud_url="https://nc.test", nextcloud_user="u", nextcloud_password="p", nextcloud_host="")
    nc.CFG = c

    async def two():
        a, b = nc._shared(), nc._shared()
        c.nextcloud_password = "changed"
        return a, b, nc._shared()

    a, b, after = asyncio.run(two())
    assert a is b and after is not a
    other_loop = asyncio.run(two())[0]
    assert other_loop is not a                      # a client is never carried onto another event loop


# ---------------------------------------------------------------- the 15-minute reminder

def _alarm_ics(extra_alarm: str = "") -> str:
    return ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u\r\nDTSTART:20261125T180000Z\r\nDTEND:20261125T190000Z\r\nSUMMARY:Haircut\r\nDESCRIPTION:Smart Style\r\n"
            "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Haircut\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\n" + extra_alarm + "END:VEVENT\r\nEND:VCALENDAR\r\n")


def test_alarm_text_does_not_overwrite_the_event_and_sets_the_remind_flag():
    ev = nc._parse_event(_alarm_ics())[0]
    assert ev["description"] == "Smart Style" and ev["remind"] is True
    assert nc._parse_event(ICS)[0]["remind"] is False


def test_new_event_with_remind_carries_a_15_minute_display_alarm():
    ev = nc.EventIn(title="Haircut", start="2026-11-25T18:00:00Z", end="2026-11-25T19:00:00Z", remind=True)
    text = nc._wrap(["BEGIN:VEVENT", "UID:u"] + nc._event_lines(ev) + ["END:VEVENT"])
    assert "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Haircut\r\nTRIGGER:-PT15M\r\nEND:VALARM" in text
    assert nc._parse_event(text)[0]["remind"] is True
    assert "VALARM" not in nc._wrap(nc._event_lines(ev.model_copy(update={"remind": False})))


def _put_body(dav, ics, remind):
    dav.server.script[("GET", "/work/u.ics")] = (200, {"etag": '"1"'}, ics.encode())
    dav.server.script[("PUT", "/work/u.ics")] = (204, {}, b"")
    body = {"title": "Haircut", "description": "Smart Style", "start": "2026-11-25T18:00:00Z", "end": "2026-11-25T19:00:00Z", "remind": remind}
    assert dav.put("/api/nextcloud/events/work/u.ics", json=body, headers=H).status_code == 200
    return [q for q in dav.server.seen if q.method == "PUT"][-1].content.decode()


def test_unticking_remind_removes_only_the_15_minute_alarm(dav):
    other = "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Leave now\r\nTRIGGER:-PT1H\r\nEND:VALARM\r\n"
    sent = _put_body(dav, _alarm_ics(other), remind=False)
    assert "-PT15M" not in sent and "TRIGGER:-PT1H" in sent and "DESCRIPTION:Leave now" in sent
    assert nc._parse_event(sent)[0]["description"] == "Smart Style"


def test_ticking_remind_on_an_edit_keeps_exactly_one_alarm_and_the_event_text(dav):
    sent = _put_body(dav, _alarm_ics(), remind=True)
    assert sent.count("TRIGGER:-PT15M") == 1
    plain = _put_body(dav, ICS.replace("DTSTART;TZID=America/Chicago:20261125T090000", "DTSTART:20261125T180000Z"), remind=True)
    assert plain.count("BEGIN:VALARM") >= 1 and "TRIGGER:-PT15M" in plain
