"""Agentic coding: the app's side of the coding workspace.

Projects, files, git checkpoints and the shared terminal live in a separate container (code-sidecar/, see its README comment). This module
is the owner-only door to it, plus the one thing only the app knows how to do: start an agent run that works inside a project folder.

Routes (all owner-only, under /api/code):
  GET  /status                          is the workspace configured and reachable
  GET/POST /projects, GET /tree, GET /files, GET/PUT /file, GET /git/status, GET /git/diff, GET /git/checkpoints, POST /git/checkpoint, POST /git/rewind
                                        passed straight to the workspace service
  POST /send  {project, message, session_id?}   save a checkpoint, then start an agent run in that project; returns {run_id, session_id}
  WS   /pty?project=                    the terminal (a tmux session per project)
  GET  /browser/targets, POST /browser/new, /browser/close, WS /browser?target=
                                        the shared browser: its tabs, and a live picture of one tab with mouse and keyboard going back
The run itself is followed with the Chat routes (stream, runs, approval), which already check who owns which run.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

PREFIX = "/api/code"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
PASS_GET = ("/projects", "/tree", "/files", "/file", "/git/status", "/git/diff", "/git/checkpoints", "/browser/targets")
PASS_POST = ("/projects", "/git/checkpoint", "/git/rewind", "/browser/new", "/browser/close")
SESSION = re.compile(r"^atrium-chat-code[a-z0-9]{6,30}$")

INSTRUCTIONS = (
    "You are coding in the project folder {path} (project \"{name}\"). Treat that folder as your working directory: run commands there, "
    "read and change files there, and create new files there unless told otherwise. Keep your answers short and say plainly what you changed. "
    "A restore point of the folder was saved just before this message, so you can work without fear of breaking things. "
    "A word in the message that starts with @ names a file in the project, relative to that folder (for example @src/app.py): read each file named that way before you answer. "
    "Files the user attached to a message are saved in the project's attachments folder and named in the message the same way (@attachments/name): open each one before you answer. "
    "For a picture use your image tool to look at it; for a PDF or an office document extract its text with a suitable tool; for anything else read it as it is."
)


def mount_code(app, cfg, hermes, current, same_origin, owners) -> None:
    if not (cfg.code_url and cfg.code_token):
        configured = False
    else:
        configured = True
    base = cfg.code_url.rstrip("/")
    headers = {"X-Code-Token": cfg.code_token}
    router = APIRouter()

    async def guard(request: Request):
        who = current(request)
        if not who:
            raise HTTPException(status_code=401, detail="Not signed in.")
        if not cfg.allows(who["role"], "admin"):
            raise HTTPException(status_code=403, detail="Only owners can use that.")
        if request.method not in ("GET", "HEAD") and not same_origin(request):
            raise HTTPException(status_code=403, detail="Bad origin.")
        if not configured and not request.url.path.endswith("/status"):
            raise HTTPException(status_code=503, detail="The coding workspace is not set up on this install.")
        request.state.who = who

    async def upstream(method: str, path: str, params: dict | None = None, body=None, timeout: float = 60):
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.request(method, base + path, params=params, json=body, headers=headers)
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="The coding workspace is not reachable.") from None
        try:
            data = r.json()
        except ValueError:
            data = {"detail": "Unexpected answer from the coding workspace."}
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code if r.status_code in (400, 404, 409) else 502, detail=str(data.get("detail", "Failed."))[:300])
        return data

    @router.get("/status")
    async def status(request: Request):
        who = current(request)
        if not who or not cfg.allows(who["role"], "admin"):
            raise HTTPException(status_code=403, detail="Only owners can use that.")
        if not configured:
            return {"configured": False, "up": False}
        try:
            async with httpx.AsyncClient(timeout=4) as c:
                r = await c.get(base + "/healthz")
            return {"configured": True, "up": r.status_code == 200}
        except httpx.HTTPError:
            return {"configured": True, "up": False}

    def make_get(path):
        async def handler(request: Request):
            return await upstream("GET", path, params=dict(request.query_params))
        return handler

    def make_post(path):
        async def handler(request: Request):
            try:
                body = await request.json()
            except ValueError:
                raise HTTPException(status_code=400, detail="Bad request.") from None
            return await upstream("POST", path, body=body, timeout=300 if path == "/projects" else 60)
        return handler

    for p in PASS_GET:
        router.add_api_route(p, make_get(p), methods=["GET"])
    for p in PASS_POST:
        router.add_api_route(p, make_post(p), methods=["POST"])

    @router.put("/file")
    async def put_file(request: Request):
        return await upstream("PUT", "/file", body=await request.json())

    MAX_UPLOAD = 25_000_000

    @router.post("/upload")
    async def upload(request: Request):
        """An attached file for the next message: the raw body is passed to the workspace service, which saves it in the project's attachments folder."""
        name, fname = str(request.query_params.get("project") or ""), str(request.query_params.get("name") or "")
        if not NAME_RE.fullmatch(name) or not fname or len(fname) > 200:
            raise HTTPException(status_code=400, detail="Pick a project and a file first.")
        if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
            raise HTTPException(status_code=413, detail="That file is too large (the limit is 25 MB).")
        data = await request.body()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(status_code=413, detail="That file is too large (the limit is 25 MB).")
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(base + "/upload", params={"project": name, "name": fname}, content=data, headers=headers)
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="The coding workspace is not reachable.") from None
        try:
            out = r.json()
        except ValueError:
            out = {"detail": "Unexpected answer from the coding workspace."}
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code if r.status_code in (400, 404, 413) else 502, detail=str(out.get("detail", "Failed."))[:300])
        return out

    @router.post("/send")
    async def send(request: Request):
        who = request.state.who
        body = await request.json()
        name, text = str(body.get("project") or ""), str(body.get("message") or "").strip()
        if not NAME_RE.fullmatch(name):
            raise HTTPException(status_code=400, detail="Pick a project first.")
        if not text or len(text) > 12000:
            raise HTTPException(status_code=400, detail="Type a message first (up to 12,000 characters).")
        sid = body.get("session_id")
        if not (isinstance(sid, str) and SESSION.fullmatch(sid)):
            sid = "atrium-chat-code" + secrets.token_hex(6)
        if not owners.claim(sid, who["user"]):
            raise HTTPException(status_code=403, detail="That session belongs to someone else.")
        await upstream("GET", "/git/status", params={"project": name})          # the project must exist
        cp = await upstream("POST", "/git/checkpoint", body={"project": name, "label": text[:40]})
        import chat_backend
        payload = {"input": text, "session_id": sid, "instructions": INSTRUCTIONS.format(path="/workspace/" + name, name=name)}
        payload.update(chat_backend._engine_override())
        _, run = await chat_backend._call("POST", "/v1/runs", payload)
        run_id = run.get("run_id")
        if not isinstance(run_id, str):
            raise HTTPException(status_code=502, detail="The agent API returned an unexpected answer.")
        return {"run_id": run_id, "session_id": sid, "checkpoint": cp.get("ref")}

    @router.get("/messages")
    async def messages(session_id: str, request: Request):
        if not SESSION.fullmatch(session_id):
            raise HTTPException(status_code=400, detail="Bad session.")
        status, data = await hermes.dash_call("GET", "/api/sessions/" + session_id + "/messages", None, timeout=20)
        if status != 200:
            raise HTTPException(status_code=404 if status == 404 else 502, detail="No such session.")
        msgs = data.get("messages", data) if isinstance(data, dict) else data
        out = []
        for m in msgs if isinstance(msgs, list) else []:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant", "tool"):
                continue
            calls = m.get("tool_calls")
            if isinstance(calls, str):
                try:
                    calls = json.loads(calls)
                except ValueError:
                    calls = None
            out.append({"id": m.get("id"), "role": m["role"], "content": str(m.get("content") or "")[:20000], "tool_name": m.get("tool_name"),
                        "tool_call_id": m.get("tool_call_id"),
                        "tool_calls": [{"id": c.get("id") or c.get("call_id"), "name": (c.get("function") or {}).get("name"),
                                        "arguments": str((c.get("function") or {}).get("arguments") or "")[:6000]} for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []})
        return {"messages": out}

    @router.get("/sessions")
    async def sessions(request: Request):
        """The coding sessions: chats whose id starts with the coding prefix, newest first."""
        status, data = await hermes.dash_call("GET", "/api/sessions?limit=80", None, timeout=20)
        ss = data.get("sessions", []) if status == 200 and isinstance(data, dict) else []
        mine = [{"id": s.get("id"), "title": str(s.get("title") or s.get("preview") or "Coding session")[:80], "messages": s.get("message_count", 0), "last_active": s.get("last_active")}
                for s in ss if isinstance(s, dict) and SESSION.fullmatch(str(s.get("id") or ""))]
        return {"sessions": mine[:30]}

    app.include_router(router, prefix=PREFIX, dependencies=[Depends(guard)])

    async def proxy_socket(ws: WebSocket, path: str, params: dict):
        """Pass a websocket through to the workspace service. The same checks as the other routes, made by hand because websockets skip the
        router's guard: signed in, an owner, and (when the browser sends one) a matching origin."""
        who = current(ws)                                     # reads the cookie from the handshake
        origin = ws.headers.get("origin")
        if not who or not cfg.allows(who["role"], "admin") or not configured or (origin and not same_origin(ws)):
            await ws.close(code=4403)
            return
        from websockets.asyncio.client import connect
        url = base.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + path + "?" + urlencode(params)
        await ws.accept()
        try:
            async with connect(url, additional_headers=headers, max_size=2**24) as up:
                async def down():
                    async for msg in up:
                        if isinstance(msg, bytes):
                            await ws.send_bytes(msg)
                        else:
                            await ws.send_text(msg)

                async def forward():
                    while True:
                        m = await ws.receive()
                        if m.get("type") == "websocket.disconnect":
                            break
                        if m.get("bytes") is not None:
                            await up.send(m["bytes"])
                        elif m.get("text") is not None:
                            await up.send(m["text"])

                tasks = [asyncio.create_task(down()), asyncio.create_task(forward())]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in tasks:
                    t.cancel()
        except Exception:
            pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    @app.websocket(PREFIX + "/pty")
    async def pty(ws: WebSocket):
        project = ws.query_params.get("project", "")
        if not NAME_RE.fullmatch(project):
            await ws.close(code=4400)
            return
        await proxy_socket(ws, "/pty", {"project": project, "cols": ws.query_params.get("cols", "100"), "rows": ws.query_params.get("rows", "30")})

    @app.websocket(PREFIX + "/browser")
    async def browser(ws: WebSocket):
        target = ws.query_params.get("target", "")
        if not re.fullmatch(r"[A-Za-z0-9]{8,64}", target):
            await ws.close(code=4400)
            return
        await proxy_socket(ws, "/browser/ws", {"target": target})
