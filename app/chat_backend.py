"""Atrium chat backend, vendored from the Atrium Loom chat plugin and adapted to run inside the Atrium web app.

Adaptations (everything else is unchanged; see docs/FRAMEWORK.md):
  * the page address is /chat (it was /agent-chat in the dashboard): notification links, the phone-app manifest and its start page
  * the voice and approval-service addresses come from the environment (TTS_URL, ACTION_GATE_URL)
  * push messages go out through an outbound proxy when PUSH_PROXY is set (the app's network has no direct internet)
  * the push-notification watcher only runs when PUSH_ENABLED=1; the app's own routes
    decide which of these routes are mounted, and wrap all of them in the app's login and per-person chat ownership

Original description follows.

Atrium Loom: Chat (dashboard plugin backend).

Routes (mounted by the Hermes dashboard under /api/plugins/atrium-chat):
  GET  /status                     is the agent API reachable
  POST /send                       start one run inside a chat session (conversation memory comes from the session id)
  POST /doc/upload?name=&keep=     receive a document (PDF, Word, Excel, PowerPoint, big text/CSV, up to 50 MB) and return a preview
  (voice: GET/POST /voice and POST /speak live in chat_voice.py; documents in chat_uploads.py; the approval service in chat_approvals.py)
  GET  /stream/{run_id}            server-sent events for that run (words as they are written, tool activity, the end)
  GET  /runs/{run_id}              run state (used to find approval requests)
  POST /runs/{run_id}/stop         stop a run
  POST /runs/{run_id}/approval     approve once / deny a pending request
  GET  /manifest.webmanifest       the app manifest (so the page can be installed as an app)
  GET  /sw.js                      the service worker (shows notifications; scope "/")
  GET  /icon-{192,512,maskable}.png  the app icon (drawn in code)
  GET  /push/key, POST /push/subscribe, /push/unsubscribe, /push/test, GET /push/status
  GET  /actions, POST /actions/{id}/decide   the agent's proposals (such as an email to send) waiting for your approval
                                   Web Push: tells your phone or browser when a run finishes or needs approval

Design rules, same as the Mission Control plugin:
  * The agent API key stays on the server (read from API_SERVER_KEY, environment or the Hermes .env file).
  * Only the agent API on this host is ever contacted.
  * A chat is just a Hermes session whose id starts with "atrium-chat-". The browser lists and reloads past chats
    through the dashboard's own /api/sessions routes, so nothing is stored by this plugin.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

import chat_model
import chat_push
from chat_push import (B64U_RE, ENDPOINT_RE, GROUPS, MAX_SUBSCRIPTIONS, TIME_RE, held_path, in_quiet, load_prefs, locked,
                       notify, prefs_path, push_state, push_save, spawn, watch_run)
from chat_push import flush_held, notify_sync  # noqa: F401  (re-exported: the host watcher and the agent's notify command import them from here)
from pngicons import draw_badge, draw_icon
from agentkit import (atomic_write_text, AgentRoute as _AgentRoute, agent_name as _agent_name, api_base as _api_base,
                      api_key as _api_key, call as _call, check_run_id as _check_run_id, hermes_home as _hermes_home,
                      RUN_ID_RE, valid_agent_name, save_agent_name)

# --- the agent's name --------------------------------------------------------------------------------------------
# Text in this file says {{agent}} wherever it means the agent's name. It is filled in on the way out (JSON, errors,
# streams, files) from <data>/atrium/agent.json, which the Settings tab writes. Without it the name is "Assistant".

router = APIRouter(route_class=_AgentRoute)

MAX_MESSAGE_CHARS = 60000
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 6 * 1024 * 1024      # per image, after decoding (the page shrinks images well below this)
_IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
_DATA_URL_RE = re.compile(r"^data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=]+)$")
UPSTREAM_TIMEOUT_S = 15
_SESSION_RE = re.compile(r"^atrium-chat-[a-z0-9]{6,40}$")


# --- configuration (server side only) ----------------------------------------

def _public_run(run: dict) -> dict:
    """Only the fields the page needs."""
    out = {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "session_id": run.get("session_id"),
        "output": run.get("output") if isinstance(run.get("output"), str) else None,
        "execution_generation": run.get("execution_generation"),
        "pending_controls": run.get("pending_controls") if isinstance(run.get("pending_controls"), list) else [],
    }
    err = run.get("error")
    if isinstance(err, dict):
        err = err.get("message")
    out["error"] = err[:400] if isinstance(err, str) else None
    return out


# --- models ------------------------------------------------------------------

class SendBody(BaseModel):
    session_id: str = Field(min_length=10, max_length=60)
    message: str = Field(default="", max_length=MAX_MESSAGE_CHARS)
    images: list[str] = Field(default_factory=list, max_length=MAX_IMAGES)


class ApprovalBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=200)
    execution_generation: Optional[int] = Field(default=None, ge=0)
    choice: Literal["once", "deny"]


# --- routes ------------------------------------------------------------------

@router.get("/status")
async def status() -> dict:
    ready = False
    if _api_key():
        try:
            await _call("GET", "/health")
            ready = True
        except HTTPException:
            ready = False
    return {"ready": ready, "max_message_chars": MAX_MESSAGE_CHARS}


@router.post("/send")
async def send(body: SendBody, request: Request) -> dict:
    if not _SESSION_RE.fullmatch(body.session_id):
        raise HTTPException(status_code=400, detail="Invalid chat id.")
    message = body.message.strip()
    parts: list[dict] = []
    for url in body.images:
        m = _DATA_URL_RE.match(url or "")
        if not m:
            raise HTTPException(status_code=400, detail="That image type is not supported. Use PNG, JPEG, WebP or GIF.")
        try:
            raw = base64.b64decode(m.group(2), validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="An attached image is damaged.") from None
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="An attached image is too large.")
        parts.append({"type": "image_url", "image_url": {"url": url}})
    if not message and not parts:
        raise HTTPException(status_code=400, detail="Type a message first.")
    if parts:
        text = message or "Please look at the attached image and tell me what you see."
        run_input: Any = [{"role": "user", "content": [{"type": "text", "text": text}] + parts}]
    else:
        run_input = message
    payload: dict[str, Any] = {"input": run_input, "session_id": body.session_id}
    payload.update(chat_model.override(request.state.who["user"], _engine_now()))
    _, run = await _call("POST", "/v1/runs", payload)
    run_id = run.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise HTTPException(status_code=502, detail="The agent API returned an unexpected answer.")
    if chat_push.PUSH_ENABLED:
        spawn(watch_run(run_id, message or "(a picture)"))
    return {"run_id": run_id, "status": run.get("status", "started")}


@router.get("/image")
async def image(path: str) -> FileResponse:
    """Serve an image the agent cached for a chat message (so reloaded chats can show it).

    Only files inside the agent's own native-inputs cache folder with an image extension are served."""
    root = (_hermes_home() / "cache" / "documents" / "native-inputs").resolve()
    try:
        target = Path(path).resolve()
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(status_code=404, detail="Not found.") from None
    if root not in target.parents or target.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        if not target.is_file() or target.stat().st_size > 20 * 1024 * 1024:
            raise HTTPException(status_code=404, detail="Not found.")
    except OSError:
        raise HTTPException(status_code=404, detail="Not found.") from None
    ext = target.suffix.lower()
    media = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/" + ext.lstrip(".")
    return FileResponse(str(target), media_type=media, headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@router.get("/stream/{run_id}")
async def stream(run_id: str) -> StreamingResponse:
    run_id = _check_run_id(run_id)
    key = _api_key()
    if not key:
        raise HTTPException(status_code=503, detail="The agent API key is not configured.")
    try:
        import httpx
    except Exception:
        raise HTTPException(status_code=501, detail="Streaming is not available on this install.") from None

    url = _api_base() + "/v1/runs/" + run_id + "/events"

    async def gen() -> AsyncIterator[bytes]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None)) as client:
                async with client.stream(
                    "GET", url, headers={"Authorization": "Bearer " + key, "Accept": "text/event-stream"}
                ) as resp:
                    if resp.status_code != 200:
                        err = json.dumps({"event": "stream.error", "status": resp.status_code})
                        yield ("data: " + err + "\n\n").encode("utf-8")
                        return
                    async for chunk in resp.aiter_raw():
                        if chunk:
                            yield chunk
        except Exception:
            err = json.dumps({"event": "stream.error", "status": 0})
            yield ("data: " + err + "\n\n").encode("utf-8")

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    _, run = await _call("GET", "/v1/runs/" + _check_run_id(run_id))
    return _public_run(run)


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str) -> dict:
    _, res = await _call("POST", "/v1/runs/" + _check_run_id(run_id) + "/stop", {})
    return {"ok": True, "status": res.get("status")}


@router.post("/runs/{run_id}/approval")
async def approve_run(run_id: str, body: ApprovalBody) -> dict:
    payload: dict[str, Any] = {"request_id": body.request_id, "choice": body.choice}
    if body.execution_generation is not None:
        payload["execution_generation"] = body.execution_generation
    _, res = await _call("POST", "/v1/runs/" + _check_run_id(run_id) + "/approval", payload)
    return {"ok": True, "status": res.get("status")}


_icons: dict[str, bytes] = {}


# --- routes: install and notifications ---------------------------------------

SW_JS = r"""/* Atrium Loom: Chat service worker. Shows push notifications; caches nothing. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function () { /* pass through to the network */ });
self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {}; }
  e.waitUntil((async function () {
    var wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    // the page is open and visible: she is already showing it, no need to ping
    if (!d.force && wins.some(function (w) { return w.visibilityState === 'visible'; })) return;
    await self.registration.showNotification(d.title || '{{agent}}', {
      body: d.body || '',
      tag: d.tag || 'agent',
      renotify: true,
      silent: false,
      vibrate: d.kind === 'approval' ? [200, 100, 200, 100, 200] : [150, 80, 150],
      timestamp: Date.now(),
      requireInteraction: d.kind === 'approval',
      icon: '/api/plugins/atrium-chat/icon-192.png',
      badge: '/api/plugins/atrium-chat/icon-badge.png',
      data: { url: d.url || '/chat' }
    });
  })());
});
self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = new URL((e.notification.data && e.notification.data.url) || '/chat', self.location.origin).href;
  e.waitUntil((async function () {
    var wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (var i = 0; i < wins.length; i++) {
      var w = wins[i];
      try {
        if ('navigate' in w && w.url !== url) { var n = await w.navigate(url); if (n) w = n; }
        if ('focus' in w) { await w.focus(); return; }
      } catch (x) { /* fall through to a new window */ }
    }
    if (self.clients.openWindow) await self.clients.openWindow(url);
  })());
});
"""


@router.get("/sw.js")
async def service_worker() -> Response:
    return Response(SW_JS, media_type="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@router.get("/manifest.webmanifest")
async def manifest() -> JSONResponse:
    base = "/api/plugins/atrium-chat/"
    return JSONResponse({
        "id": "/chat",
        "name": "{{agent}}",
        "short_name": "{{agent}}",
        "description": "Chat with {{agent}}.",
        "start_url": "/chat",
        "scope": "/",
        "display": "standalone",
        "background_color": "#06061a",
        "theme_color": "#06061a",
        "icons": [
            {"src": base + "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": base + "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": base + "icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.get("/icon-{name}.png")
async def icon(name: str) -> Response:
    spec = {"192": (192, 1.0), "512": (512, 1.0), "maskable": (512, 0.72), "badge": (96, 1.0)}.get(name)
    if not spec:
        raise HTTPException(status_code=404, detail="Not found.")
    if name not in _icons:
        _icons[name] = await asyncio.to_thread(draw_badge if name == "badge" else draw_icon, *((spec[0],) if name == "badge" else spec))
    return Response(_icons[name], media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


class SubscribeBody(BaseModel):
    subscription: dict
    label: str = Field(default="", max_length=80)


class UnsubscribeBody(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1700)


@router.get("/push/key")
async def push_key() -> dict:
    data = await asyncio.to_thread(push_state)
    return {"public_key": data["public_key"]}


@router.get("/push/status")
async def push_status() -> dict:
    data = await asyncio.to_thread(push_state)
    return {"devices": len(data["subscriptions"])}


@router.post("/push/subscribe")
async def push_subscribe(body: SubscribeBody) -> dict:
    sub = body.subscription
    endpoint = sub.get("endpoint") if isinstance(sub, dict) else None
    keys = sub.get("keys") if isinstance(sub, dict) else None
    if not isinstance(endpoint, str) or not ENDPOINT_RE.fullmatch(endpoint) or not isinstance(keys, dict):
        raise HTTPException(status_code=400, detail="That subscription is not valid.")
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (isinstance(p256dh, str) and isinstance(auth, str) and B64U_RE.fullmatch(p256dh) and B64U_RE.fullmatch(auth)):
        raise HTTPException(status_code=400, detail="That subscription is not valid.")
    label = body.label.strip()[:80]

    def add() -> int:
        with locked():
            data = push_state()
            subs = [x for x in data["subscriptions"] if x.get("endpoint") != endpoint]
            subs.append({"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}, "label": label, "added": int(time.time())})
            data["subscriptions"] = subs[-MAX_SUBSCRIPTIONS:]
            push_save(data)
            return len(data["subscriptions"])

    return {"ok": True, "devices": await asyncio.to_thread(add)}


@router.post("/push/unsubscribe")
async def push_unsubscribe(body: UnsubscribeBody) -> dict:
    def remove() -> int:
        with locked():
            data = push_state()
            data["subscriptions"] = [x for x in data["subscriptions"] if x.get("endpoint") != body.endpoint]
            push_save(data)
            return len(data["subscriptions"])

    return {"ok": True, "devices": await asyncio.to_thread(remove)}


@router.post("/push/test")
async def push_test() -> dict:
    res = await notify("{{agent}}", "Notifications are working.", "test", "normal", True)
    return {"ok": res["sent"] > 0, **res}


# --- the agent's name (read by every Atrium plugin and the host helpers) -----------------------------------------

class AgentBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)


@router.get("/agent")
async def agent_info() -> dict:
    return {"name": _agent_name(), "default": "Assistant"}


@router.post("/agent")
async def agent_set(body: AgentBody) -> dict:
    name = " ".join(body.name.split())
    if not valid_agent_name(name):
        raise HTTPException(status_code=400, detail="Use letters, numbers, spaces, dots, apostrophes or hyphens (up to 40 characters).")
    save_agent_name(name)
    return {"name": name}


# --- engine (read side): which model answers chats started here -------------------------------------------------
# The switch itself lives in the Mission Control plugin; both read <HERMES_HOME>/atrium/engine.json.

_CLAUDE_IDS = {"claude-sonnet-5-5": "Claude Sonnet 5.5", "claude-opus-5-5": "Claude Opus 5.5", "claude-haiku-5-5": "Claude Haiku 5.5", "claude-fable-5-2": "Claude Fable 5.2"}


def _engine_now() -> dict:
    try:
        data = json.loads((_hermes_home() / "atrium" / "engine.json").read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("engine") == "claude" and data.get("model") in _CLAUDE_IDS:
            until = data.get("until")
            if until and time.time() > float(until):
                return {"engine": "local"}
            return {"engine": "claude", "model": data["model"], "until": until}
    except (OSError, ValueError):
        pass
    return {"engine": "local"}


def _engine_override() -> dict:
    st = _engine_now()
    return {"model": st["model"], "provider": "anthropic"} if st["engine"] == "claude" else {}


@router.get("/engine")
async def engine_info(request: Request) -> dict:
    st = chat_model.selection(request.state.who["user"]) or await asyncio.to_thread(_engine_now)
    if st.get("engine") == "cloud":
        return {"engine": "claude", "label": st.get("model", ""), "seconds_left": None}
    left = max(0, int(float(st["until"]) - time.time())) if st["engine"] == "claude" and st.get("until") else None
    return {"engine": st["engine"], "label": _CLAUDE_IDS.get(st.get("model", ""), ""), "seconds_left": left}


# --- settings routes: notification preferences and the devices that receive them ----------------------------------

class PrefsBody(BaseModel):
    groups: dict[str, bool] = Field(default_factory=dict)
    quiet: dict[str, Any] = Field(default_factory=dict)


@router.get("/settings/notify")
async def get_notify_settings() -> dict:
    prefs = await asyncio.to_thread(load_prefs)
    try:
        held = len(json.loads(held_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        held = 0
    return {"prefs": prefs, "groups": GROUPS, "held": held, "quiet_now": in_quiet(prefs)}


@router.post("/settings/notify")
async def set_notify_settings(body: PrefsBody) -> dict:
    prefs = await asyncio.to_thread(load_prefs)
    for g, v in body.groups.items():
        if g in prefs["groups"] and g != "approvals":
            prefs["groups"][g] = bool(v)
    q = body.quiet
    if "enabled" in q:
        prefs["quiet"]["enabled"] = bool(q["enabled"])
    if "allow_urgent" in q:
        prefs["quiet"]["allow_urgent"] = bool(q["allow_urgent"])
    for k in ("start", "end"):
        if k in q:
            if not (isinstance(q[k], str) and TIME_RE.match(q[k])):
                raise HTTPException(status_code=400, detail="Times look like 22:00.")
            prefs["quiet"][k] = q[k]

    def write() -> None:
        path = prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(prefs))
    await asyncio.to_thread(write)
    return {"prefs": prefs, "quiet_now": in_quiet(prefs)}


def _device_id(endpoint: str) -> str:
    import hashlib
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:10]


@router.get("/push/devices")
async def push_devices() -> dict:
    data = await asyncio.to_thread(push_state)
    out = []
    for sub in data["subscriptions"]:
        ep = sub.get("endpoint", "")
        out.append({"id": _device_id(ep), "label": sub.get("label") or "Device", "added": sub.get("added"), "service": ep.split("/")[2] if ep.count("/") >= 2 else ""})
    return {"devices": out}


class DeviceBody(BaseModel):
    id: str = Field(min_length=10, max_length=10)


@router.post("/push/devices/remove")
async def push_device_remove(body: DeviceBody) -> dict:
    def remove() -> int:
        with locked():
            data = push_state()
            data["subscriptions"] = [x for x in data["subscriptions"] if _device_id(x.get("endpoint", "")) != body.id]
            push_save(data)
            return len(data["subscriptions"])
    return {"ok": True, "devices": await asyncio.to_thread(remove)}
