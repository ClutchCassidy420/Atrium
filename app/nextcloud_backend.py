"""Files and Calendar, backed by a Nextcloud server (WebDAV for files, CalDAV for the calendar).

The browser never sees the Nextcloud password: the app holds an app password (revocable in Nextcloud) and only does what is listed here.
Owners only (the app mounts it behind the "admin" permission). Read and write: you can add, change and remove calendar events, and upload,
rename, move and delete files (Nextcloud keeps deleted files in its own trash).

  GET    /status                          whether it is connected, the calendars, the public Nextcloud address
  GET    /events?start=&end=              events in a time range (repeating events come back as separate occurrences)
  POST   /events                          add an event
  PUT    /events/{calendar}/{name}        change an event
  DELETE /events/{calendar}/{name}        remove an event
  GET    /files?path=                     list a folder
  GET    /files/download?path=            download a file
  POST   /files/upload?path=&name=        upload (the request body is the file). A file of the same name is never replaced unless replace=1.
  POST   /files/mkdir                     {path}
  POST   /files/move                      {path, to}   (rename or move)
  DELETE /files?path=
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
import weakref
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree as ET

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter()
CFG = None                       # set by configure(): the app's config
MAX_UPLOAD = 512 * 1024 * 1024
NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav", "cs": "http://calendarserver.org/ns/", "ic": "http://apple.com/ns/ical/", "oc": "http://owncloud.org/ns", "nc": "http://nextcloud.org/ns"}
NAME_RE = re.compile(r"^[A-Za-z0-9._@-]{1,120}$")


def configure(cfg) -> bool:
    global CFG
    CFG = cfg
    return bool(cfg.nextcloud_url and cfg.nextcloud_user and cfg.nextcloud_password)


def _client() -> httpx.AsyncClient:
    headers = {"Host": CFG.nextcloud_host} if CFG.nextcloud_host else {}
    return httpx.AsyncClient(base_url=CFG.nextcloud_url.rstrip("/"), auth=(CFG.nextcloud_user, CFG.nextcloud_password), headers=headers, timeout=30.0, follow_redirects=False)


_SHARED: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()     # running event loop -> (what it was built from, client)


def _shared() -> httpx.AsyncClient:
    """One client per event loop for the short DAV calls, so a page that makes several in a row reuses the TLS connection.
    A client is tied to the loop it was made on, and is rebuilt when the settings change. Uploads and downloads keep their own."""
    loop = asyncio.get_running_loop()
    stamp = (CFG.nextcloud_url, CFG.nextcloud_user, CFG.nextcloud_password, CFG.nextcloud_host, _client)
    held = _SHARED.get(loop)
    if held is None or held[0] != stamp:
        held = _SHARED[loop] = (stamp, _client())
    return held[1]


async def _dav(method: str, path: str, body: Optional[str] = None, headers: Optional[dict] = None, ok=(200, 201, 204, 207)) -> httpx.Response:
    try:
        r = await _shared().request(method, path, content=body.encode("utf-8") if body is not None else None, headers=headers or {})
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Nextcloud did not answer.")
    if r.status_code == 401:
        raise HTTPException(status_code=502, detail="Nextcloud refused the app password.")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Not found.")
    if r.status_code in (409, 412):
        raise HTTPException(status_code=409, detail="That conflicts with something already there.")
    if r.status_code not in ok:
        raise HTTPException(status_code=502, detail="Nextcloud said %d." % r.status_code)
    return r


def _user() -> str:
    return quote(CFG.nextcloud_user, safe="")


# ---------------------------------------------------------------- paths

def _clean_path(p: str) -> str:
    p = (p or "").replace("\\", "/")
    parts = [s for s in p.split("/") if s not in ("", ".")]
    if any(s == ".." for s in parts) or any("\x00" in s or len(s) > 200 for s in parts):
        raise HTTPException(status_code=400, detail="Bad path.")
    return "/".join(parts)


def _files_url(path: str) -> str:
    return "/remote.php/dav/files/%s/%s" % (_user(), "/".join(quote(s, safe="") for s in path.split("/")) if path else "")


def _absolute(path: str) -> str:
    """An absolute URL for `path` on the configured server (WebDAV MOVE needs one). Uses the configured scheme, and the Host the server expects."""
    u = urlsplit(CFG.nextcloud_url)
    return "%s://%s%s%s" % (u.scheme, CFG.nextcloud_host or u.netloc, u.path.rstrip("/"), path)


# ---------------------------------------------------------------- calendar helpers

def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape(v: str) -> str:
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    raw = line.encode("utf-8")
    if len(raw) <= 73:
        return line
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > (73 if not out else 72):
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n ".join(out)


def _when(name: str, params: dict, value: str) -> tuple[str, bool]:
    """An iCalendar date or date-time as (text for the browser, all_day). UTC stays UTC; a named zone is converted to UTC if the zone is known."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return "%s-%s-%s" % (value[0:4], value[4:6], value[6:8]), True
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)", value)
    if not m:
        return value, False
    y, mo, d, hh, mm, ss, z = m.groups()
    dt = datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss))
    if z:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), False
    tzid = params.get("TZID")
    if tzid:
        try:
            from zoneinfo import ZoneInfo
            return dt.replace(tzinfo=ZoneInfo(tzid)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), False
        except Exception:
            pass
    return dt.strftime("%Y-%m-%dT%H:%M:%S"), False


def _parse_event(ics: str) -> list[dict]:
    """Every VEVENT in an iCalendar document, as plain dicts."""
    events, cur = [], None
    for line in _unfold(ics):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, _, value = line.partition(":")
            bits = head.split(";")
            key, params = bits[0].upper(), {}
            for b in bits[1:]:
                k, _, v = b.partition("=")
                params[k.upper()] = v.strip('"')
            cur[key] = (value, params)
    out = []
    for ev in events:
        if "DTSTART" not in ev:
            continue
        start, all_day = _when("DTSTART", ev["DTSTART"][1], ev["DTSTART"][0])
        if "DTEND" in ev:
            end, _ = _when("DTEND", ev["DTEND"][1], ev["DTEND"][0])
        else:
            end = start
        out.append({"uid": ev.get("UID", ("", {}))[0], "title": _unescape(ev.get("SUMMARY", ("", {}))[0]) or "(no title)", "start": start, "end": end, "all_day": all_day,
                    "location": _unescape(ev.get("LOCATION", ("", {}))[0]), "description": _unescape(ev.get("DESCRIPTION", ("", {}))[0]),
                    "recurring": "RECURRENCE-ID" in ev or "RRULE" in ev, "status": ev.get("STATUS", ("", {}))[0]})
    return out


def _ical_time(prop: str, value: str, all_day: bool) -> str:
    if all_day:
        d = date.fromisoformat(value[:10])
        return "%s;VALUE=DATE:%s" % (prop, d.strftime("%Y%m%d"))
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        return "%s:%s" % (prop, dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    return "%s:%s" % (prop, dt.strftime("%Y%m%dT%H%M%S"))


def _event_lines(ev: "EventIn") -> list[str]:
    lines = [_ical_time("DTSTART", ev.start, ev.all_day), _ical_time("DTEND", ev.end, ev.all_day), "SUMMARY:" + _escape(ev.title.strip() or "(no title)")]
    if ev.location.strip():
        lines.append("LOCATION:" + _escape(ev.location.strip()))
    if ev.description.strip():
        lines.append("DESCRIPTION:" + _escape(ev.description.strip()))
    return lines


def _wrap(lines: list[str]) -> str:
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


class EventIn(BaseModel):
    calendar: str = Field("", max_length=120)
    title: str = Field("", max_length=300)
    start: str = Field(..., max_length=40)
    end: str = Field(..., max_length=40)
    all_day: bool = False
    location: str = Field("", max_length=300)
    description: str = Field("", max_length=8000)


def _valid_times(ev: EventIn) -> None:
    try:
        a = date.fromisoformat(ev.start[:10]) if ev.all_day else datetime.fromisoformat(ev.start.replace("Z", "+00:00"))
        b = date.fromisoformat(ev.end[:10]) if ev.all_day else datetime.fromisoformat(ev.end.replace("Z", "+00:00"))
        if (a.tzinfo is None) != (b.tzinfo is None) if not ev.all_day else False:
            raise ValueError("mixed")
        if b < a:
            raise HTTPException(status_code=400, detail="The end is before the start.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Bad date or time.")


def _cal_name(name: str) -> str:
    if not NAME_RE.fullmatch(name or ""):
        raise HTTPException(status_code=400, detail="Bad calendar or event name.")
    return name


async def _calendars() -> list[dict]:
    body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:ic="http://apple.com/ns/ical/">'
            "<d:prop><d:displayname/><d:resourcetype/><ic:calendar-color/><c:supported-calendar-component-set/><d:current-user-privilege-set/></d:prop></d:propfind>")
    r = await _dav("PROPFIND", "/remote.php/dav/calendars/%s/" % _user(), body, {"Depth": "1", "Content-Type": "application/xml"})
    out = []
    for resp in ET.fromstring(r.content).findall("d:response", NS):
        href = unquote(resp.findtext("d:href", "", NS)).rstrip("/")
        prop = resp.find("d:propstat/d:prop", NS)
        if prop is None or prop.find("d:resourcetype/c:calendar", NS) is None:
            continue
        comps = [e.get("name") for e in prop.findall("c:supported-calendar-component-set/c:comp", NS)]
        if comps and "VEVENT" not in comps:
            continue
        privs = {e.tag.split("}")[1] for e in prop.findall("d:current-user-privilege-set/d:privilege/*", NS)}
        out.append({"id": href.rsplit("/", 1)[-1], "name": prop.findtext("d:displayname", "", NS) or href.rsplit("/", 1)[-1],
                    "color": (prop.findtext("ic:calendar-color", "", NS) or "")[:7], "writable": bool({"write", "all", "write-content"} & privs) or not privs})
    return out


def _cal_color(c: str) -> str:
    return c if re.fullmatch(r"#[0-9A-Fa-f]{6}", c or "") else ""


# ---------------------------------------------------------------- routes: calendar

@router.get("/status")
async def status():
    cals = await _calendars()
    for c in cals:
        c["color"] = _cal_color(c["color"])
    return {"connected": True, "user": CFG.nextcloud_user, "calendars": cals, "url": CFG.nextcloud_public_url}


def _utc_stamp(v: str) -> str:
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@router.get("/events")
async def events(start: str, end: str):
    try:
        s, e = _utc_stamp(start), _utc_stamp(end)
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad range.")
    if datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00")) > timedelta(days=100):
        raise HTTPException(status_code=400, detail="That range is too long.")
    cals = await _calendars()
    out = []
    body = ('<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:getetag/>'
            '<c:calendar-data><c:expand start="%s" end="%s"/></c:calendar-data></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
            '<c:time-range start="%s" end="%s"/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>') % (s, e, s, e)
    for cal in cals:
        r = await _dav("REPORT", "/remote.php/dav/calendars/%s/%s/" % (_user(), quote(cal["id"], safe="")), body, {"Depth": "1", "Content-Type": "application/xml"})
        for resp in ET.fromstring(r.content).findall("d:response", NS):
            href = unquote(resp.findtext("d:href", "", NS))
            data = resp.findtext("d:propstat/d:prop/c:calendar-data", "", NS)
            if not data:
                continue
            for ev in _parse_event(data):
                ev.update({"calendar": cal["id"], "calendar_name": cal["name"], "color": _cal_color(cal["color"]), "writable": cal["writable"],
                           "name": href.rsplit("/", 1)[-1]})
                out.append(ev)
    out.sort(key=lambda x: (x["start"], x["title"]))
    return {"events": out}


@router.post("/events")
async def add_event(ev: EventIn):
    _valid_times(ev)
    cals = await _calendars()
    first_writable = next((c["id"] for c in cals if c["writable"]), "")
    cal = ev.calendar or first_writable
    if not any(c["id"] == cal and c["writable"] for c in cals):
        raise HTTPException(status_code=400, detail="Pick a calendar you can write to.")
    uid = str(uuid.uuid4())
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Atrium//Calendar//EN", "BEGIN:VEVENT", "UID:" + uid, "DTSTAMP:" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")] + _event_lines(ev) + ["END:VEVENT", "END:VCALENDAR"]
    name = uid + ".ics"
    await _dav("PUT", "/remote.php/dav/calendars/%s/%s/%s" % (_user(), quote(cal, safe=""), name), _wrap(lines), {"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"})
    return {"ok": True, "calendar": cal, "name": name}


@router.put("/events/{calendar}/{name}")
async def change_event(calendar: str, name: str, ev: EventIn):
    _cal_name(calendar), _cal_name(name)
    _valid_times(ev)
    url = "/remote.php/dav/calendars/%s/%s/%s" % (_user(), quote(calendar, safe=""), quote(name, safe=""))
    cur = await _dav("GET", url)
    text = cur.text
    if "RRULE" in text or "RECURRENCE-ID" in text:
        raise HTTPException(status_code=400, detail="This event repeats. Change it in Nextcloud so the whole series stays right.")
    keep, inside, done = [], False, False
    drop = ("DTSTART", "DTEND", "DURATION", "SUMMARY", "LOCATION", "DESCRIPTION")
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            inside = True
        if inside and line.split(":")[0].split(";")[0].upper() in drop:
            continue
        if inside and line == "END:VEVENT" and not done:
            keep.extend(_event_lines(ev))
            done = True
            inside = False
        if line:
            keep.append(line)
    if not done:
        raise HTTPException(status_code=502, detail="Could not read that event.")
    await _dav("PUT", url, _wrap(keep), {"Content-Type": "text/calendar; charset=utf-8", "If-Match": cur.headers.get("etag", "*")})
    return {"ok": True}


@router.delete("/events/{calendar}/{name}")
async def delete_event(calendar: str, name: str):
    _cal_name(calendar), _cal_name(name)
    await _dav("DELETE", "/remote.php/dav/calendars/%s/%s/%s" % (_user(), quote(calendar, safe=""), quote(name, safe="")))
    return {"ok": True}


# ---------------------------------------------------------------- routes: files

@router.get("/files")
async def list_files(path: str = ""):
    path = _clean_path(path)
    body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop><d:getlastmodified/><d:getcontentlength/><d:getcontenttype/>'
            "<d:resourcetype/><oc:fileid/></d:prop></d:propfind>")
    r = await _dav("PROPFIND", _files_url(path), body, {"Depth": "1", "Content-Type": "application/xml"})
    base = "/remote.php/dav/files/%s/" % unquote(_user())
    items = []
    for resp in ET.fromstring(r.content).findall("d:response", NS):
        href = unquote(resp.findtext("d:href", "", NS))
        rel = href.split(base, 1)[-1].strip("/")
        if rel == path:
            continue
        prop = resp.find("d:propstat/d:prop", NS)
        if prop is None:
            continue
        folder = prop.find("d:resourcetype/d:collection", NS) is not None
        size = prop.findtext("d:getcontentlength", "", NS)
        items.append({"name": rel.rsplit("/", 1)[-1], "path": rel, "folder": folder, "size": int(size) if size.isdigit() else None,
                      "modified": prop.findtext("d:getlastmodified", "", NS), "type": prop.findtext("d:getcontenttype", "", NS) or ""})
    items.sort(key=lambda i: (not i["folder"], i["name"].lower()))
    return {"path": path, "items": items, "url": CFG.nextcloud_public_url}


@router.get("/files/download")
async def download(path: str):
    path = _clean_path(path)
    if not path:
        raise HTTPException(status_code=400, detail="Pick a file.")
    client = _client()
    try:
        req = client.build_request("GET", _files_url(path))
        r = await client.send(req, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Nextcloud did not answer.")
    if r.status_code != 200:
        await r.aclose(); await client.aclose()
        raise HTTPException(status_code=404 if r.status_code == 404 else 502, detail="Could not get that file.")

    async def body():
        try:
            async for chunk in r.aiter_bytes():
                yield chunk
        finally:
            await r.aclose(); await client.aclose()
    fname = path.rsplit("/", 1)[-1]
    heads = {"Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(fname), "X-Content-Type-Options": "nosniff"}
    if r.headers.get("content-length"):
        heads["Content-Length"] = r.headers["content-length"]
    return StreamingResponse(body(), media_type="application/octet-stream", headers=heads)


@router.post("/files/upload")
async def upload(request: Request, path: str = "", name: str = "", replace: bool = False):
    path = _clean_path(path)
    name = _clean_path(name)
    if not name or "/" in name:
        raise HTTPException(status_code=400, detail="Bad file name.")
    declared = int(request.headers.get("content-length") or 0)
    if declared > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="That file is too big for this page.")
    target = (path + "/" if path else "") + name

    async def gen():
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_UPLOAD:
                raise HTTPException(status_code=413, detail="That file is too big for this page.")
            yield chunk
    try:
        async with _client() as c:
            r = await c.put(_files_url(target), content=gen(), headers={} if replace else {"If-None-Match": "*"})
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Nextcloud did not answer.")
    if r.status_code in (409, 412):
        raise HTTPException(status_code=409, detail="A file with that name is already there.")
    if r.status_code not in (200, 201, 204):
        raise HTTPException(status_code=502, detail="Nextcloud said %d." % r.status_code)
    return {"ok": True, "path": target}


class PathIn(BaseModel):
    path: str = Field(..., max_length=1000)


class MoveIn(BaseModel):
    path: str = Field(..., max_length=1000)
    to: str = Field(..., max_length=1000)


@router.post("/files/mkdir")
async def mkdir(body: PathIn):
    p = _clean_path(body.path)
    if not p:
        raise HTTPException(status_code=400, detail="Give the folder a name.")
    await _dav("MKCOL", _files_url(p))
    return {"ok": True}


@router.post("/files/move")
async def move(body: MoveIn):
    src, dst = _clean_path(body.path), _clean_path(body.to)
    if not src or not dst:
        raise HTTPException(status_code=400, detail="Bad path.")
    await _dav("MOVE", _files_url(src), None, {"Destination": _absolute(_files_url(dst)), "Overwrite": "F"})
    return {"ok": True}


@router.delete("/files")
async def remove(path: str):
    p = _clean_path(path)
    if not p:
        raise HTTPException(status_code=400, detail="Pick something to delete.")
    await _dav("DELETE", _files_url(p))
    return {"ok": True}
