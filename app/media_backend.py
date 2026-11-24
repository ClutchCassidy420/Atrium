"""Media: browse and play what is on a person's own Jellyfin or Emby server, from inside Atrium.

Every route works on the signed-in person's own connections (app/media_servers.py). The browser never receives the server's token or
address-bearing links: pictures and playback go through this server, which adds the token itself. The browser names only a connection
and an item id (letters, digits and dashes), never a web address.

Playback is the file as the server stores it ("direct", with Range so seeking works). A format a browser cannot play (most .mkv files) can be
"converted": the media server turns it into H.264/AAC (video) or MP3 (audio) as it streams, which cannot be seeked.
"""
from __future__ import annotations

import asyncio
import re
import secrets
from typing import Optional
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

import media_servers as ms
from media_servers import MediaError

TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=20.0, pool=10.0)
MAX_IMAGE = 3 * 1024 * 1024
ID = re.compile(r"^[A-Za-z0-9-]{4,64}$")
SID = re.compile(r"^[0-9a-f]{8}$")
TRANSPORT: Optional[httpx.AsyncBaseTransport] = None        # tests put a fake server here
CLIENT = 'MediaBrowser Client="Atrium", Device="Atrium", DeviceId="%s", Version="1.0"'
DIRECT_OK = {"mp4", "m4v", "webm", "mov", "mp3", "m4a", "aac", "ogg", "oga", "opus", "flac", "wav"}
VIDEO_CONVERT = "VideoCodec=h264&AudioCodec=aac&MaxStreamingBitrate=8000000&TranscodingMaxAudioChannels=2&SubtitleMethod=Hide"
AUDIO_CONVERT = "AudioCodec=mp3&AudioBitRate=192000&TranscodingMaxAudioChannels=2"
IMAGE_MAGIC = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"GIF8", "image/gif"), (b"BM", "image/bmp"))
_DEVICE = {}                                                 # a steady device id per person, so the server does not list many "devices"


def _sniff(data: bytes) -> Optional[str]:
    for magic, kind in IMAGE_MAGIC:
        if data.startswith(magic):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _device(user: str) -> str:
    return _DEVICE.setdefault(user, "atrium-" + secrets.token_hex(6))


class Link:
    """One checked way to reach a media server: requests go to the resolved address, with the name kept for the Host header and the certificate."""

    def __init__(self, url: str, private_ok: bool):
        self.url, self.private_ok = url, private_ok
        parts = urlsplit(url)
        self.scheme, self.host, self.port, self.base = parts.scheme, parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80), parts.path

    async def build(self, client: httpx.AsyncClient, method: str, path: str, params=None, headers=None, json=None):
        ip = await asyncio.to_thread(ms.resolve, self.host, self.port, self.private_ok)
        target = "[%s]" % ip if ":" in ip else ip
        hostport = self.host if (self.scheme, self.port) in (("https", 443), ("http", 80)) else "%s:%d" % (self.host, self.port)
        head = dict(headers or {}, Host=hostport)
        extensions = {"sni_hostname": self.host} if self.scheme == "https" else {}
        return client.build_request(method, "%s://%s:%d%s%s" % (self.scheme, target, self.port, self.base, path), params=params, headers=head, json=json,
                                    extensions=extensions)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=TRANSPORT, timeout=TIMEOUT, follow_redirects=False)


def _fail(response: httpx.Response) -> MediaError:
    if response.status_code in (401, 403):
        return MediaError("The server no longer accepts your sign-in. Remove this connection and connect again.", 409)
    if response.status_code == 404:
        return MediaError("The server does not have that.", 404)
    return MediaError("The media server answered with an error (%d)." % response.status_code, 502)


async def ask(link: Link, srv: dict, method: str, path: str, params=None, json=None, user: str = "") -> object:
    headers = {"X-Emby-Authorization": CLIENT % _device(user) + ', Token="%s"' % srv["token"], "X-Emby-Token": srv["token"], "Accept": "application/json"}
    try:
        async with _client() as client:
            response = await client.send(await link.build(client, method, path, params, headers, json))
    except MediaError:
        raise
    except (httpx.HTTPError, OSError, ValueError):
        raise MediaError("Could not reach the media server.", 502) from None
    if response.status_code >= 300:
        raise _fail(response)
    try:
        return response.json() if response.content else {}
    except ValueError:
        raise MediaError("The media server sent something unexpected.", 502) from None


def norm(it: dict) -> dict:
    data = it.get("UserData") or {}
    ticks, pos = it.get("RunTimeTicks") or 0, data.get("PlaybackPositionTicks") or 0
    media = (it.get("MediaType") or "").lower()
    artists = it.get("Artists") or ([it["AlbumArtist"]] if it.get("AlbumArtist") else [])
    return {"id": str(it.get("Id") or ""), "name": str(it.get("Name") or "")[:300], "type": str(it.get("Type") or ""),
            "year": it.get("ProductionYear") if isinstance(it.get("ProductionYear"), int) else None,
            "overview": str(it.get("Overview") or "")[:1500], "minutes": round(ticks / 6e8) if ticks else None,
            "played": bool(data.get("Played")), "progress": min(100, round(100 * pos / ticks)) if ticks and pos else 0,
            "folder": bool(it.get("IsFolder")), "has_image": bool((it.get("ImageTags") or {}).get("Primary")),
            "series": str(it.get("SeriesName") or "")[:200], "season": it.get("ParentIndexNumber") if isinstance(it.get("ParentIndexNumber"), int) else None,
            "episode": it.get("IndexNumber") if isinstance(it.get("IndexNumber"), int) else None, "album": str(it.get("Album") or "")[:200],
            "artist": ", ".join(str(a) for a in artists)[:200], "media": media if media in ("video", "audio") else "",
            "collection": str(it.get("CollectionType") or "")}


def _items(result) -> list[dict]:
    rows = result if isinstance(result, list) else (result or {}).get("Items") or []
    return [norm(r) for r in rows if isinstance(r, dict) and ID.match(str(r.get("Id") or ""))]


def media_router(cfg, current, same_origin, store: "ms.Servers") -> APIRouter:
    router = APIRouter()
    private_allowed = bool(getattr(cfg, "media_allow_private", False))

    async def guard(request: Request):
        who = current(request)
        if not who:
            raise HTTPException(status_code=401, detail="Not signed in.")
        if not cfg.allows(who["role"], "use"):
            raise HTTPException(status_code=403, detail="Not allowed.")
        if request.method not in ("GET", "HEAD") and not same_origin(request):
            raise HTTPException(status_code=403, detail="Bad origin.")
        request.state.who = who
        request.state.private_ok = private_allowed and cfg.allows(who["role"], "admin")

    api = APIRouter(dependencies=[Depends(guard)])

    def mine(request: Request, sid: str):
        if not SID.match(sid):
            raise HTTPException(status_code=404, detail="No such connection.")
        srv = store.get(request.state.who["user"], sid)
        if not srv:
            raise HTTPException(status_code=404, detail="No such connection.")
        return srv, Link(srv["url"], request.state.private_ok)

    def wrap(error: MediaError):
        return HTTPException(status_code=error.status, detail=str(error))

    def item_id(value: str) -> str:
        if not ID.match(value or ""):
            raise HTTPException(status_code=400, detail="Bad item.")
        return value

    @api.get("/servers")
    def servers(request: Request):
        return {"servers": store.list(request.state.who["user"]), "max": ms.MAX_SERVERS, "kinds": [{"id": k, "label": v} for k, v in ms.KINDS.items()],
                "local_ok": request.state.private_ok}

    @api.post("/servers")
    async def connect(request: Request):
        user = request.state.who["user"]
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="Bad request.")
        body = body if isinstance(body, dict) else {}
        kind = str(body.get("kind") or "")
        username, password = str(body.get("username") or "").strip(), str(body.get("password") or "")
        if kind not in ms.KINDS:
            raise HTTPException(status_code=400, detail="Choose Jellyfin or Emby.")
        if not username or len(username) > 128 or len(password) > 512:
            raise HTTPException(status_code=400, detail="Enter your user name and password on that server.")
        if not store.allow_attempt(user):
            raise HTTPException(status_code=429, detail="Too many tries. Wait a few minutes.")
        try:
            url = ms.clean_url(body.get("url"), request.state.private_ok)
            link = Link(url, request.state.private_ok)
            async with _client() as client:
                try:
                    pub = await client.send(await link.build(client, "GET", "/System/Info/Public", headers={"Accept": "application/json"}))
                    info = pub.json() if pub.status_code == 200 else {}
                    if not isinstance(info, dict) or not (info.get("ServerName") or info.get("Version")):
                        raise MediaError("That address did not answer like a %s server." % ms.KINDS[kind], 400)
                    login = await client.send(await link.build(client, "POST", "/Users/AuthenticateByName",
                                                               headers={"X-Emby-Authorization": CLIENT % _device(user), "Accept": "application/json"},
                                                               json={"Username": username, "Pw": password}))
                except (httpx.HTTPError, OSError, ValueError):
                    raise MediaError("Could not reach that server. Check the address (and the port).", 502) from None
            if login.status_code in (400, 401, 403):
                raise MediaError("The server did not accept that user name and password.", 400)
            if login.status_code != 200:
                raise MediaError("The media server answered with an error (%d)." % login.status_code, 502)
            try:
                result = login.json()
                token, account = str(result["AccessToken"]), result["User"]
                user_id, user_name = str(account["Id"]), str(account.get("Name") or username)
            except (ValueError, KeyError, TypeError):
                raise MediaError("The media server sent something unexpected.", 502) from None
            if not ID.match(user_id) or not token or len(token) > 512:
                raise MediaError("The media server sent something unexpected.", 502)
            label = str(body.get("label") or "").strip() or str(info.get("ServerName") or "")
            return {"server": store.add(user, kind, url, label, user_id, user_name, token)}
        except MediaError as error:
            raise wrap(error)

    @api.delete("/servers/{sid}")
    def disconnect(sid: str, request: Request):
        if not SID.match(sid) or not store.remove(request.state.who["user"], sid):
            raise HTTPException(status_code=404, detail="No such connection.")
        return {"ok": True}

    @api.get("/servers/{sid}/home")
    async def home(sid: str, request: Request):
        srv, link = mine(request, sid)
        user = request.state.who["user"]
        uid = srv["user_id"]
        try:
            views, resume, latest = await asyncio.gather(
                ask(link, srv, "GET", "/Users/%s/Views" % uid, user=user),
                ask(link, srv, "GET", "/Users/%s/Items/Resume" % uid, {"Limit": 12, "MediaTypes": "Video,Audio", "Fields": "Overview"}, user=user),
                ask(link, srv, "GET", "/Users/%s/Items/Latest" % uid, {"Limit": 16, "Fields": "Overview"}, user=user), return_exceptions=True)
        except MediaError as error:
            raise wrap(error)
        if isinstance(views, BaseException):                  # the libraries are the one thing the page cannot do without
            raise wrap(views) if isinstance(views, MediaError) else HTTPException(status_code=502, detail="Could not reach the media server.")
        return {"libraries": _items(views), "resume": [] if isinstance(resume, BaseException) else _items(resume),
                "latest": [] if isinstance(latest, BaseException) else _items(latest)}

    @api.get("/servers/{sid}/items")
    async def items(sid: str, request: Request, parent: str = "", q: str = "", start: int = 0, limit: int = 60):
        srv, link = mine(request, sid)
        params = {"Fields": "Overview", "StartIndex": max(0, min(start, 100000)), "Limit": max(1, min(limit, 100)),
                  "SortBy": "ParentIndexNumber,IndexNumber,SortName", "SortOrder": "Ascending"}
        query = q.strip()[:100]
        if query:
            params.update({"SearchTerm": query, "Recursive": "true", "SortBy": "SortName", "IncludeItemTypes": "Movie,Series,Episode,Audio,MusicAlbum,MusicArtist,Video"})
        elif parent:
            params["ParentId"] = item_id(parent)
        else:
            raise HTTPException(status_code=400, detail="Choose a library or search.")
        try:
            result = await ask(link, srv, "GET", "/Users/%s/Items" % srv["user_id"], params, user=request.state.who["user"])
        except MediaError as error:
            raise wrap(error)
        return {"items": _items(result), "total": result.get("TotalRecordCount") if isinstance(result, dict) else None}

    @api.get("/servers/{sid}/item/{iid}")
    async def item(sid: str, iid: str, request: Request):
        srv, link = mine(request, sid)
        try:
            raw = await ask(link, srv, "GET", "/Users/%s/Items/%s" % (srv["user_id"], item_id(iid)), user=request.state.who["user"])
        except MediaError as error:
            raise wrap(error)
        if not isinstance(raw, dict):
            raise HTTPException(status_code=502, detail="The media server sent something unexpected.")
        out = norm(dict(raw, Id=raw.get("Id") or iid))
        sources = raw.get("MediaSources") or []
        container = str((sources[0].get("Container") if sources and isinstance(sources[0], dict) else raw.get("Container")) or "").lower().split(",")[0]
        out["container"] = container
        out["play"] = "direct" if container in DIRECT_OK else "convert"
        out["genres"] = [str(g)[:40] for g in (raw.get("Genres") or [])[:8]]
        return out

    @api.get("/servers/{sid}/image/{iid}")
    async def image(sid: str, iid: str, request: Request, size: int = 360):
        srv, link = mine(request, sid)
        try:
            async with _client() as client:
                req = await link.build(client, "GET", "/Items/%s/Images/Primary" % item_id(iid), {"maxHeight": max(60, min(size, 900)), "quality": 85},
                                       {"X-Emby-Token": srv["token"], "Accept": "image/*"})
                response = await client.send(req)
        except MediaError as error:
            raise wrap(error)
        except (httpx.HTTPError, OSError, ValueError):
            raise HTTPException(status_code=502, detail="Could not reach the media server.")
        data = response.content
        kind = _sniff(data) if response.status_code == 200 and len(data) <= MAX_IMAGE else None
        if not kind:
            raise HTTPException(status_code=404, detail="No picture.")
        return Response(content=data, media_type=kind, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})

    @api.get("/servers/{sid}/stream/{iid}")
    async def stream(sid: str, iid: str, request: Request, mode: str = "direct", kind: str = "video"):
        srv, link = mine(request, sid)
        iid = item_id(iid)
        audio = kind == "audio"
        if mode == "convert":
            path = ("/Audio/%s/stream.mp3?%s" if audio else "/Videos/%s/stream.mp4?%s") % (iid, AUDIO_CONVERT if audio else VIDEO_CONVERT)
        else:
            path = ("/Audio/%s/stream?static=true" if audio else "/Videos/%s/stream?static=true") % iid
        headers = {"X-Emby-Token": srv["token"], "Accept-Encoding": "identity"}
        if mode != "convert" and request.headers.get("range"):
            headers["Range"] = request.headers["range"]
        client = _client()
        try:
            response = await client.send(await link.build(client, "GET", path, headers=headers), stream=True)
        except MediaError as error:
            await client.aclose()
            raise wrap(error)
        except (httpx.HTTPError, OSError, ValueError):
            await client.aclose()
            raise HTTPException(status_code=502, detail="Could not reach the media server.")
        ctype = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if response.status_code not in (200, 206) or not (ctype.startswith("video/") or ctype.startswith("audio/")):
            code = response.status_code
            await response.aclose()
            await client.aclose()
            raise HTTPException(status_code=409 if code in (401, 403) else 404 if code == 404 else 502,
                                detail="The server would not play that." if code != 404 else "The server does not have that.")
        out = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
        for name in ("content-length", "content-range", "accept-ranges"):
            if response.headers.get(name):
                out[name.title()] = response.headers[name]

        async def body():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()
        return StreamingResponse(body(), status_code=response.status_code, media_type=ctype, headers=out)

    @api.post("/servers/{sid}/played/{iid}")
    async def played(sid: str, iid: str, request: Request):
        srv, link = mine(request, sid)
        try:
            await ask(link, srv, "POST", "/Users/%s/PlayedItems/%s" % (srv["user_id"], item_id(iid)), user=request.state.who["user"])
        except MediaError as error:
            raise wrap(error)
        return {"ok": True}

    router.include_router(api)
    return router
