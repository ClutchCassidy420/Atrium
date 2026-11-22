"""Chat for signed-in people: the vendored chat backend behind the app's login, with each person's chats kept separate,
plus the few Hermes dashboard routes the chat page itself calls (past chats, transcription, who am I).

Chats are Hermes sessions whose id starts with "atrium-chat-". The page makes up the id; the first person to send in an
id owns it. Plain users only ever see and change their own chats; owners (the "admin" permission) can see them all.
Chats that existed before this app (no recorded owner) belong to owners only.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

PREFIX = "/api/plugins/atrium-chat"
CHAT_PREFIX = "atrium-chat-"
SESSION_ID = re.compile(r"^[A-Za-z0-9_\-:.]{1,100}$")
CHAT_ID = re.compile(r"^atrium-chat-[a-z0-9]{6,40}$")
# push notifications and the installable phone app: off unless the install turns them on (PUSH_ENABLED=1)
PUSH_ROUTES = ("/push", "/sw.js", "/manifest.webmanifest", "/icon-", "/settings/notify")
# these change settings that everyone shares, so they are for owners only
ADMIN_WRITES = ("/agent", "/voice", "/engine", "/actions/")
# the notification store belongs to the install (every subscribed device gets the agent's pings, approvals included): owners only
ADMIN_ANY = ("/push", "/settings")
# the app icons are plain pictures that the browser may fetch without cookies (notification and home-screen icons)
PUBLIC = ("/icon-",)
MAX_TRANSCRIBE_BYTES = 14 * 1024 * 1024


class ChatOwners:
    """Which person owns which chat. A small JSON file in the app's data folder."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def owner(self, sid: str):
        return self._load().get(sid)

    def mine(self, user: str) -> set:
        return {k for k, v in self._load().items() if v == user}

    def claim(self, sid: str, user: str) -> bool:
        """Record the owner of a new chat. False if somebody else already owns it."""
        with self.lock:
            data = self._load()
            if data.get(sid, user) != user:
                return False
            if sid not in data:
                data[sid] = user
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                os.chmod(tmp, 0o600)
                tmp.replace(self.path)
            return True

    def forget(self, sid: str) -> None:
        with self.lock:
            data = self._load()
            if data.pop(sid, None) is not None:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                os.chmod(tmp, 0o600)
                tmp.replace(self.path)


def mount_chat(app, cfg, hermes, current, same_origin) -> ChatOwners:
    # the vendored backend reads its settings from the environment; set them from this app's configuration
    if cfg.hermes_api_key:
        os.environ["API_SERVER_KEY"] = cfg.hermes_api_key
    if cfg.hermes_api_base:
        u = urlsplit(cfg.hermes_api_base)
        os.environ["API_SERVER_HOST"] = u.hostname or ""
        os.environ["API_SERVER_PORT"] = str(u.port or 8642)
    if cfg.hermes_data_dir:
        os.environ["HERMES_HOME"] = cfg.hermes_data_dir
    os.environ["PUSH_ENABLED"] = "1" if cfg.push_enabled else "0"
    os.environ["PUSH_PROXY"] = cfg.push_proxy
    if cfg.tts_url:
        os.environ["TTS_URL"] = cfg.tts_url
    if cfg.action_gate_url:
        os.environ["ACTION_GATE_URL"] = cfg.action_gate_url
    import chat_backend as backend
    backend.PUSH_ENABLED = cfg.push_enabled
    backend.PUSH_PROXY = cfg.push_proxy or None
    if cfg.tts_url:
        backend.KOKORO_URL = cfg.tts_url
    if cfg.action_gate_url:
        backend.ACTIOND = cfg.action_gate_url

    owners = ChatOwners(cfg.data_dir / "chat_owners.json")
    is_admin = lambda who: cfg.allows(who["role"], "admin")

    async def may_use_session(who, sid: str) -> bool:
        """Owners may use any session; others only chats they own."""
        return is_admin(who) or owners.owner(sid) == who["user"]

    async def guard(request: Request):
        rel = request.url.path[len(PREFIX):] or "/"
        if request.method in ("GET", "HEAD") and any(rel.startswith(p) for p in PUBLIC):
            return
        who = current(request)
        if not who:
            raise HTTPException(status_code=401, detail="Not signed in.")
        if not cfg.allows(who["role"], "use"):
            raise HTTPException(status_code=403, detail="Not allowed for your role.")
        if request.method not in ("GET", "HEAD") and not same_origin(request):
            raise HTTPException(status_code=403, detail="Bad origin.")
        if request.method == "POST" and any(rel == p or rel.startswith(p) for p in ADMIN_WRITES) and not is_admin(who):
            raise HTTPException(status_code=403, detail="Only owners can change that.")
        if cfg.action_gate_url and (rel == "/actions" or rel.startswith("/actions/")) and not is_admin(who):
            raise HTTPException(status_code=403, detail="Only owners can view approval records.")
        if any(rel == p or rel.startswith(p + "/") or rel.startswith(p + "?") for p in ADMIN_ANY) and not is_admin(who):
            raise HTTPException(status_code=403, detail="Only owners can use that.")
        if request.method == "POST" and rel == "/send":
            try:
                sid = (await request.json()).get("session_id")
            except ValueError:
                sid = None
            if isinstance(sid, str) and CHAT_ID.fullmatch(sid):
                current_owner = owners.owner(sid)
                if current_owner is None and not is_admin(who):
                    status, _ = await hermes.dash_call("GET", "/api/sessions/" + sid)
                    if status == 200:                              # an older chat nobody has claimed: owners only
                        raise HTTPException(status_code=403, detail="That chat belongs to someone else.")
                    if status != 404:
                        raise HTTPException(status_code=503, detail="Chat ownership could not be verified.")
                if not owners.claim(sid, who["user"]) and not is_admin(who):
                    raise HTTPException(status_code=403, detail="That chat belongs to someone else.")
        m = re.match(r"^/(?:runs|stream)/([A-Za-z0-9_\-]{1,100})", rel)
        if m and not is_admin(who):
            status, run = await hermes.api_call("GET", "/v1/runs/" + m.group(1))
            if status == 404:
                raise HTTPException(status_code=404, detail="Run not found.")
            if status != 200 or not isinstance(run, dict):
                raise HTTPException(status_code=503, detail="Run ownership could not be verified.")
            if not await may_use_session(who, run.get("session_id") or ""):
                raise HTTPException(status_code=403, detail="That run belongs to someone else.")
        request.state.who = who

    router = APIRouter()
    # without an approval service the page's pending-actions checks would fail every few seconds, so they answer "none"
    off = (() if cfg.push_enabled else PUSH_ROUTES) + (() if cfg.action_gate_url else ("/actions",))
    for route in backend.router.routes:
        path = getattr(route, "path", "")
        if any(path == p or path.startswith(p) for p in off):
            continue
        router.routes.append(route)
    if not cfg.action_gate_url:
        @router.get("/actions")
        async def no_actions():
            return {"pending": [], "recent": []}

        @router.get("/actions/log")
        async def no_action_log():
            return {"entries": []}
    app.include_router(router, prefix=PREFIX, dependencies=[Depends(guard)])

    # ---- the dashboard routes the chat page calls (answered here, with the fixed token) ---------------------------------
    def reply(status: int, data):
        return JSONResponse(data if data is not None else {}, status_code=status if status else 502)

    async def signed_in(request: Request, write: bool = False):
        who = current(request)
        if not who:
            return None, JSONResponse({"detail": "Not signed in."}, status_code=401)
        if not cfg.allows(who["role"], "use"):
            return None, JSONResponse({"detail": "Not allowed for your role."}, status_code=403)
        if write and not same_origin(request):
            return None, JSONResponse({"detail": "Bad origin."}, status_code=403)
        return who, None

    @app.get("/api/auth/me")
    async def auth_me(request: Request):
        who, refused = await signed_in(request)
        if refused:
            return refused
        return {"authenticated": True, "user": who["user"], "display_name": who.get("name") or who["user"], "role": who["role"]}

    @app.get("/api/sessions/stats")
    async def sessions_stats(request: Request):
        who, refused = await signed_in(request)
        if refused:
            return refused
        if not is_admin(who):
            return JSONResponse({"detail": "Not allowed for your role."}, status_code=403)
        status, data = await hermes.dash_call("GET", "/api/sessions/stats")
        return reply(status, data)

    @app.get("/api/sessions")
    async def sessions_list(request: Request):
        who, refused = await signed_in(request)
        if refused:
            return refused
        q = parse_qs(request.url.query, keep_blank_values=True)
        if is_admin(who):
            status, data = await hermes.dash_call("GET", "/api/sessions" + ("?" + request.url.query if request.url.query else ""))
            return reply(status, data)
        try:
            limit = max(1, min(200, int((q.get("limit") or ["50"])[0])))
            offset = max(0, int((q.get("offset") or ["0"])[0]))
        except ValueError:
            return JSONResponse({"detail": "Bad paging."}, status_code=400)
        upstream = {"limit": 500, "offset": 0}
        for k in ("archived", "min_messages"):
            if k in q:
                upstream[k] = q[k][0]
        status, data = await hermes.dash_call("GET", "/api/sessions?" + urlencode(upstream))
        if status != 200 or not isinstance(data, dict):
            return reply(status, data)
        mine = owners.mine(who["user"])
        rows = [s for s in data.get("sessions", []) if isinstance(s, dict) and s.get("id") in mine]
        return {"sessions": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset, "storage": {}}

    async def session_call(request: Request, sid: str, method: str, sub: str = "", write: bool = False, with_body: bool = False):
        who, refused = await signed_in(request, write)
        if refused:
            return refused
        if not SESSION_ID.fullmatch(sid):
            return JSONResponse({"detail": "Invalid chat id."}, status_code=400)
        if not await may_use_session(who, sid):
            return JSONResponse({"detail": "Not found."}, status_code=404)
        payload = None
        if with_body:
            try:
                payload = await request.json()
            except ValueError:
                payload = {}
        path = "/api/sessions/" + sid + sub + ("?" + request.url.query if request.url.query else "")
        status, data = await hermes.dash_call(method, path, payload)
        if method == "DELETE" and status == 200:
            owners.forget(sid)
        return reply(status, data)

    @app.get("/api/sessions/{sid}")
    async def session_get(sid: str, request: Request):
        return await session_call(request, sid, "GET")

    @app.get("/api/sessions/{sid}/messages")
    async def session_messages(sid: str, request: Request):
        return await session_call(request, sid, "GET", "/messages")

    @app.patch("/api/sessions/{sid}")
    async def session_patch(sid: str, request: Request):
        return await session_call(request, sid, "PATCH", write=True, with_body=True)

    @app.delete("/api/sessions/{sid}")
    async def session_delete(sid: str, request: Request):
        return await session_call(request, sid, "DELETE", write=True)

    @app.post("/api/audio/transcribe")
    async def transcribe(request: Request):
        who, refused = await signed_in(request, write=True)
        if refused:
            return refused
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_TRANSCRIBE_BYTES:
            return JSONResponse({"detail": "That recording is too long."}, status_code=413)
        raw = await request.body()
        if len(raw) > MAX_TRANSCRIBE_BYTES:
            return JSONResponse({"detail": "That recording is too long."}, status_code=413)
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return JSONResponse({"detail": "Bad request."}, status_code=400)
        status, data = await hermes.dash_call("POST", "/api/audio/transcribe", payload, timeout=90)
        return reply(status, data)

    return owners
