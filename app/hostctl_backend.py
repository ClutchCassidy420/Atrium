"""Server actions for the owner: install updates, reboot, shut down. Settings > Server.

The web app never runs these itself and never holds root. It forwards a request to the small host helper (host/atrium-hostctl.py),
which only knows four fixed actions. The helper checks the shared key and, for every change, asks this app (GET /api/gate-check)
whether the person's session cookie belongs to an owner, so a change needs both this app's guard and the helper's own check.

Routes (all owner-only, mounted under /api/host):
  GET  /status   what the helper reports, plus whether the agent is busy; {"enabled": false} when no helper is configured
  POST /action   {"action": "update_safe"|"update_care"|"reboot"|"shutdown", "force": bool}
  POST /cancel   cancel a reboot or shutdown during its countdown
  GET  /containers                the container groups, newer images, restore points, the running job
  POST /containers/check          look for newer images now
  POST /containers/update         {"project": name, "force": bool}   update one group (restore point first, automatic rollback if it fails)
  POST /containers/rollback       {"project": name}                  put the previous build back
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()
CFG = None          # set by configure()
HERMES = None

TIMEOUT_S = 20.0


PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,60}$")


class Project(BaseModel):
    project: str
    force: bool = False


class Action(BaseModel):
    action: Literal["update_safe", "update_care", "reboot", "shutdown"]
    force: bool = False


def configure(cfg, hermes) -> None:
    global CFG, HERMES
    CFG, HERMES = cfg, hermes


def _enabled() -> bool:
    return bool(CFG and CFG.host_action_url and CFG.host_action_key)


def _call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(CFG.host_action_url.rstrip("/") + path, method=method, headers={"X-Hostctl-Key": CFG.host_action_key, "Content-Type": "application/json"},
                                 data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, json.loads(r.read(200_000) or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read(20_000) or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return 0, {}


async def _agent_busy() -> dict:
    """How much the agent is doing right now: chat runs and board cards. Advisory: it only drives a warning."""
    runs = cards = 0
    if HERMES is not None and getattr(HERMES, "dash_base", ""):
        st, data = await HERMES.dash_call("GET", "/api/status")
        if st == 200 and isinstance(data, dict):
            runs = int((((data.get("gateway_platforms") or {}).get("api_server") or {}).get("metrics") or {}).get("active_runs") or 0)
        st, board = await HERMES.dash_call("GET", "/api/plugins/kanban/board")
        if st == 200 and isinstance(board, dict):
            for c in board.get("columns", []) or []:
                if isinstance(c, dict) and c.get("name") == "running":
                    cards = len(c.get("tasks") or [])
    return {"runs": runs, "cards": cards}


@router.get("/status")
async def status() -> dict:
    if not _enabled():
        return {"enabled": False}
    code, data = await asyncio.to_thread(_call, "GET", "/status")
    busy = await _agent_busy()
    if code != 200:
        return {"enabled": True, "reachable": False, "busy": busy}
    return {"enabled": True, "reachable": True, "busy": busy, **data}


def _warnings(busy: dict, status_data: dict) -> list[str]:
    out = []
    if busy.get("runs"):
        out.append("The agent is in the middle of a chat run.")
    if busy.get("cards"):
        out.append("A board card is running.")
    if status_data.get("backup_running"):
        out.append("A backup is running.")
    return out


@router.post("/action")
async def action(body: Action, request: Request) -> dict:
    if not _enabled():
        raise HTTPException(status_code=404, detail="Server actions are not set up on this install.")
    cookie = request.headers.get("cookie", "")
    code, st = await asyncio.to_thread(_call, "GET", "/status")
    if code != 200:
        raise HTTPException(status_code=502, detail="The host helper did not answer.")
    warn = _warnings(await _agent_busy(), st)
    if warn and not body.force:
        raise HTTPException(status_code=409, detail="Busy: " + " ".join(warn))
    code, data = await asyncio.to_thread(_call, "POST", "/do", {"action": body.action, "cookie": cookie})
    if code != 200:
        raise HTTPException(status_code=code if code in (401, 403, 409) else 502, detail=str(data.get("error") or "The host helper refused that."))
    return data


@router.post("/cancel")
async def cancel(request: Request) -> dict:
    if not _enabled():
        raise HTTPException(status_code=404, detail="Server actions are not set up on this install.")
    code, data = await asyncio.to_thread(_call, "POST", "/cancel", {"cookie": request.headers.get("cookie", "")})
    if code != 200:
        raise HTTPException(status_code=code if code in (401, 403, 409) else 502, detail=str(data.get("error") or "The host helper refused that."))
    return data


# --- containers ---------------------------------------------------------------------------------------------------------------

_summary = {"t": 0.0, "n": 0}


def update_count() -> int:
    """How many container groups have a newer image, for the Home list. Cached for a few minutes; 0 when there is no helper."""
    import time
    if not _enabled():
        return 0
    if time.time() - _summary["t"] > 300:
        _summary["t"] = time.time()
        code, data = _call("GET", "/status")
        _summary["n"] = int(((data or {}).get("containers") or {}).get("updates") or 0) if code == 200 else 0
    return _summary["n"]


@router.get("/containers")
async def containers() -> dict:
    if not _enabled():
        raise HTTPException(status_code=404, detail="Server actions are not set up on this install.")
    code, data = await asyncio.to_thread(_call, "GET", "/containers")
    if code != 200:
        raise HTTPException(status_code=502, detail="The host helper did not answer.")
    data["busy"] = await _agent_busy()
    return data


async def _forward(path: str, payload: dict, request: Request) -> dict:
    payload = {**payload, "cookie": request.headers.get("cookie", "")}
    code, data = await asyncio.to_thread(_call, "POST", path, payload)
    if code != 200:
        raise HTTPException(status_code=code if code in (401, 403, 409) else 502, detail=str(data.get("error") or "The host helper refused that."))
    return data


@router.post("/containers/check")
async def containers_check(request: Request) -> dict:
    if not _enabled():
        raise HTTPException(status_code=404, detail="Server actions are not set up on this install.")
    _summary["t"] = 0.0
    return await _forward("/containers/check", {}, request)


@router.post("/containers/update")
async def containers_update(body: Project, request: Request) -> dict:
    if not _enabled():
        raise HTTPException(status_code=404, detail="Server actions are not set up on this install.")
    if not PROJECT_RE.match(body.project):
        raise HTTPException(status_code=422, detail="Bad group name.")
    warn = _warnings(await _agent_busy(), {})
    if warn and not body.force:
        raise HTTPException(status_code=409, detail="Busy: " + " ".join(warn))
    _summary["t"] = 0.0
    return await _forward("/containers/update", {"project": body.project}, request)


@router.post("/containers/rollback")
async def containers_rollback(body: Project, request: Request) -> dict:
    if not _enabled():
        raise HTTPException(status_code=404, detail="Server actions are not set up on this install.")
    if not PROJECT_RE.match(body.project):
        raise HTTPException(status_code=422, detail="Bad group name.")
    _summary["t"] = 0.0
    return await _forward("/containers/rollback", {"project": body.project}, request)
