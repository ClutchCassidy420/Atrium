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
import threading
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from agentkit import atomic_write_text
from guards import OWNERS_ONLY, role_guard
from httpkit import shared

PREFIX = "/api/code"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
PASS_GET = ("/projects", "/tree", "/files", "/file", "/git/status", "/git/diff", "/git/checkpoints", "/browser/targets")
PASS_POST = ("/projects", "/git/checkpoint", "/git/rewind", "/browser/new", "/browser/close")
SESSION = re.compile(r"^atrium-chat-code[a-z0-9]{6,30}$")
# a worker run or subagent of a linked kanban card: read-only here (the ids Hermes gives them)
RELATED = re.compile(r"^(local-[0-9a-f]{16,64}|[0-9]{8}_[0-9]{6}_[0-9a-f]{6,12})$")
CARD = re.compile(r"^t_[0-9a-f]{6,16}$")
DEFAULT_CARD_WORKSPACES = "/opt/data/kanban/workspaces/"       # where a card scratch workspace lives, as the agent's container sees it

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
    store_path = cfg.data_dir / "code_sessions.json"
    card_workspaces = (cfg.kanban_workspaces_dir or DEFAULT_CARD_WORKSPACES).rstrip("/") + "/"
    store_lock = threading.Lock()

    def load_store() -> dict:
        """Which project each coding session belongs to, and which kanban card each project is linked to."""
        try:
            d = json.loads(store_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        d = d if isinstance(d, dict) else {}
        if not isinstance(d.get("sessions"), dict):
            d["sessions"] = {}
        if not isinstance(d.get("links"), dict):
            d["links"] = {}
        return d

    def save_store(d: dict) -> None:
        atomic_write_text(store_path, json.dumps(d, indent=1, sort_keys=True))

    def not_set_up(request: Request, who) -> None:
        if not configured and not request.url.path.endswith("/status"):
            raise HTTPException(status_code=503, detail="The coding workspace is not set up on this install.")

    guard = role_guard(cfg, current, same_origin, "admin", denied=OWNERS_ONLY, after=not_set_up)

    async def upstream(method: str, path: str, params: dict | None = None, body=None, timeout: float = 60):
        try:
            r = await shared().request(method, base + path, params=params, json=body, headers=headers, timeout=timeout)
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
            r = await shared().get(base + "/healthz", timeout=4)
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
        with store_lock:                                                          # remember which project this session belongs to
            d = load_store()
            if d["sessions"].get(sid) != name:
                d["sessions"][sid] = name
                save_store(d)
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
        if not (SESSION.fullmatch(session_id) or RELATED.fullmatch(session_id)):
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

    async def list_sessions(source: str) -> list[dict]:
        """Every session of one kind, newest first. Hermes caps a page at 100 and also keeps an empty scheduled check-in session every 15 minutes,
        so asking for the newest 80 of everything (as this page once did) lost anything older than about a day."""
        out: list[dict] = []
        for page in range(8):
            status, data = await hermes.dash_call("GET", "/api/sessions?" + urlencode({"limit": 100, "offset": page * 100, "source": source}), None, timeout=20)
            if status != 200 or not isinstance(data, dict):
                break
            got = [s for s in data.get("sessions", []) if isinstance(s, dict) and s.get("source", source) == source]
            out += got
            if len(data.get("sessions", [])) < 100:
                break
        return out

    def newest(s: dict) -> float:
        try:
            return float(s.get("last_active") or s.get("started_at") or 0)
        except (TypeError, ValueError):
            return 0.0

    @router.get("/sessions")
    async def sessions(request: Request):
        """The coding sessions of one project (and any older ones that were never tied to a project), newest first, plus that project's related
        work: the worker runs and subagents of the kanban card it is linked to (read-only)."""
        project = str(request.query_params.get("project") or "")
        if project and not NAME_RE.fullmatch(project):
            raise HTTPException(status_code=400, detail="Bad project.")
        store = load_store()
        coding = [s for s in await list_sessions("api_server") if SESSION.fullmatch(str(s.get("id") or ""))]
        mine = []
        for s in sorted(coding, key=newest, reverse=True):
            owner = store["sessions"].get(s.get("id"))
            if project and owner and owner != project:
                continue
            mine.append({"id": s.get("id"), "title": str(s.get("title") or s.get("preview") or "Coding session")[:80], "messages": s.get("message_count", 0),
                         "last_active": s.get("last_active"), "legacy": not owner})
        link = store["links"].get(project) if project else None
        related = []
        if isinstance(link, dict) and CARD.fullmatch(str(link.get("card") or "")):
            where = card_workspaces + link["card"]
            found = []
            for source in ("kanban", "subagent"):
                found += [s for s in await list_sessions(source) if str(s.get("cwd") or "") == where and int(s.get("message_count") or 0) > 0]
            for s in sorted(found, key=newest, reverse=True):
                title = str(s.get("title") or "")[:80] if s.get("source") == "subagent" else "Worker run" + (" - " + str(link.get("title"))[:50] if link.get("title") else "")
                related.append({"id": s.get("id"), "title": title, "kind": s.get("source"), "messages": s.get("message_count", 0), "last_active": s.get("last_active")})
        return {"sessions": mine[:30], "related": related[:40], "link": link if isinstance(link, dict) else None}

    @router.put("/links")
    async def set_link(request: Request):
        """Link a project to a kanban card (its worker runs then appear under the project as related work), or unlink with card: null."""
        body = await request.json()
        name, card = str(body.get("project") or ""), body.get("card")
        if not NAME_RE.fullmatch(name):
            raise HTTPException(status_code=400, detail="Pick a project first.")
        if card is not None and not (isinstance(card, str) and CARD.fullmatch(card)):
            raise HTTPException(status_code=400, detail="That is not a card id (for example t_032dcd3a).")
        with store_lock:
            d = load_store()
            if card is None:
                d["links"].pop(name, None)
            else:
                d["links"][name] = {"card": card, "title": str(body.get("title") or "")[:120]}
            save_store(d)
        return {"ok": True}

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

                async def watch_access():
                    while True:
                        await asyncio.sleep(5)
                        person = await asyncio.to_thread(current, ws)
                        if not person or not cfg.allows(person["role"], "admin"):
                            return

                tasks = [asyncio.create_task(down()), asyncio.create_task(forward()), asyncio.create_task(watch_access())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
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
