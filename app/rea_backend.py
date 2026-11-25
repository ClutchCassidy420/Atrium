"""REA page backend: owners upload programs and apps and run read-only analyses in the REA workbench container (see rea-sidecar/).

Owner-only and off unless REA_URL and REA_TOKEN are set. The browser never sees the container's address or secret. An upload is streamed through
to the container (never held in memory here) and is size-limited on the way. What can be run is decided by the container's fixed list.
"""
from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from guards import OWNERS_ONLY, role_guard

MAX_UPLOAD = 1024 ** 3
ID = re.compile(r"^[0-9a-f]{12}$")
NAME = re.compile(r"^[^\x00-\x1f/\\]{1,150}$")


def rea_router(cfg, current, same_origin) -> APIRouter:
    base = cfg.rea_url.rstrip("/")
    headers = {"X-Rea-Token": cfg.rea_token}

    guard = role_guard(cfg, current, same_origin, "admin", denied=OWNERS_ONLY)

    router = APIRouter(dependencies=[Depends(guard)])

    async def upstream(method: str, path: str, params=None, body=None, timeout: float = 30):
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.request(method, base + path, params=params, json=body, headers=headers)
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="The REA workbench is not reachable.") from None
        try:
            data = r.json()
        except ValueError:
            data = {"detail": "Unexpected answer from the REA workbench."}
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code if r.status_code in (400, 404, 409, 413, 429, 507) else 502, detail=str(data.get("detail", "Failed."))[:300])
        return data

    def job_id(value: str) -> str:
        if not ID.match(value):
            raise HTTPException(status_code=404, detail="Not found.")
        return value

    @router.get("/status")
    async def status():
        try:
            async with httpx.AsyncClient(timeout=4) as c:
                r = await c.get(base + "/healthz")
            return {"configured": True, "up": r.status_code == 200}
        except httpx.HTTPError:
            return {"configured": True, "up": False}

    @router.get("/commands")
    async def commands():
        return await upstream("GET", "/commands")

    @router.get("/files")
    async def files():
        return await upstream("GET", "/files")

    @router.delete("/files/{fid}")
    async def delete_file(fid: str):
        return await upstream("DELETE", "/files/" + job_id(fid))

    @router.post("/files")
    async def upload(request: Request, name: str = "upload", unpack: int = 0):
        if not NAME.match(name):
            raise HTTPException(status_code=400, detail="That file name cannot be used.")
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            raise HTTPException(status_code=411, detail="The upload needs a size.")
        if length > MAX_UPLOAD:
            raise HTTPException(status_code=413, detail="That file is too large (1 GB at most).")

        async def body():
            got = 0
            async for chunk in request.stream():
                got += len(chunk)
                if got > MAX_UPLOAD:
                    return
                yield chunk
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=900, write=900)) as c:
                r = await c.post(base + "/files", params={"name": name, "unpack": 1 if unpack else 0}, content=body(), headers=dict(headers, **{"Content-Length": str(length)}))
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="The REA workbench is not reachable.") from None
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code if r.status_code in (400, 413, 507) else 502, detail=str(data.get("detail", "Could not upload."))[:300])
        return data

    @router.get("/jobs")
    async def jobs():
        return await upstream("GET", "/jobs")

    @router.post("/jobs")
    async def start(request: Request):
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="Bad request.") from None
        body = body if isinstance(body, dict) else {}
        return await upstream("POST", "/jobs", body={"command": str(body.get("command") or ""), "file": str(body.get("file") or ""), "arg": str(body.get("arg") or "")[:200]})

    @router.get("/jobs/{jid}")
    async def job(jid: str):
        return await upstream("GET", "/jobs/" + job_id(jid))

    @router.delete("/jobs/{jid}")
    async def delete_job(jid: str):
        return await upstream("DELETE", "/jobs/" + job_id(jid))

    @router.get("/jobs/{jid}/summary")
    async def summary(jid: str):
        return await upstream("GET", "/jobs/%s/summary" % job_id(jid), timeout=60)

    @router.get("/jobs/{jid}/download")
    async def download(jid: str):
        jid = job_id(jid)
        client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=300))
        try:
            r = await client.send(client.build_request("GET", base + "/jobs/%s/download" % jid, headers=headers), stream=True)
        except httpx.HTTPError:
            await client.aclose()
            raise HTTPException(status_code=502, detail="The REA workbench is not reachable.") from None
        if r.status_code != 200:
            await r.aclose()
            await client.aclose()
            raise HTTPException(status_code=404, detail="No result.")

        async def stream():
            try:
                async for chunk in r.aiter_bytes():
                    yield chunk
            finally:
                await r.aclose()
                await client.aclose()
        out = {"Content-Disposition": 'attachment; filename="rea-%s.json"' % jid, "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
        if r.headers.get("content-length"):
            out["Content-Length"] = r.headers["content-length"]
        return StreamingResponse(stream(), media_type="application/json", headers=out)

    return router
