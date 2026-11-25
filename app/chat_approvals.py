"""Chat: the approval service (split out of chat_backend.py; the URLs are the same).

  GET  /actions                      what the agent has proposed and is waiting on
  POST /actions/{id}/decide          the owner's decision
  GET  /actions/log                  mailbox tidying the agent did on her own, each with an Undo
  POST /actions/log/{id}/undo
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agentkit import AgentRoute

router = APIRouter(route_class=AgentRoute)

# --- approvals: what the agent proposes, and the owner's decision ---------------------
#
# The agent can only PROPOSE actions (see ops/atrium-actiond.py on the host). They wait there, together with the
# credentials, until the owner approves in this app. The decision carries their dashboard session, which the host
# service checks with the dashboard before it does anything.

ACTIOND = os.environ.get("ACTION_GATE_URL") or "http://172.20.0.1:8765"
_ACTION_ID_RE = re.compile(r"^[a-f0-9]{12}$")


class DecideBody(BaseModel):
    decision: Literal["approve", "deny"]


def _actiond_sync(method: str, path: str, payload: Optional[dict] = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(ACTIOND + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read(500_000) or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read(20_000) or b"{}")
        except ValueError:
            return exc.code, {}
    except Exception:
        raise HTTPException(status_code=502, detail="The approval service is not reachable.") from None


@router.get("/actions")
async def actions() -> dict:
    code, data = await asyncio.to_thread(_actiond_sync, "GET", "/actions")
    if code != 200:
        raise HTTPException(status_code=502, detail="The approval service refused the request.")
    return data


@router.post("/actions/{action_id}/decide")
async def decide_action(action_id: str, body: DecideBody, request: Request) -> dict:
    if not _ACTION_ID_RE.fullmatch(action_id):
        raise HTTPException(status_code=400, detail="Invalid action id.")
    # replay the caller's own session credentials so the host service can confirm it really is a signed-in owner
    auth = {k: v for k, v in request.headers.items() if k.lower() in ("cookie", "authorization") or "token" in k.lower() or "session" in k.lower()}
    code, data = await asyncio.to_thread(_actiond_sync, "POST", "/decide", {"id": action_id, "decision": body.decision, "auth": auth})
    if code == 401:
        raise HTTPException(status_code=401, detail="You are not signed in. Sign in again and retry.")
    if code in (404, 409):
        raise HTTPException(status_code=code, detail=str(data.get("error") or "That request is no longer pending."))
    if code != 200:
        raise HTTPException(status_code=502, detail="The approval service refused the request.")
    return data


@router.get("/actions/log")
async def actions_log() -> dict:
    """Mailbox tidying the agent did on her own (marked, flagged, moved), newest first, each with an Undo."""
    code, data = await asyncio.to_thread(_actiond_sync, "GET", "/log")
    if code != 200:
        raise HTTPException(status_code=502, detail="The action service refused the request.")
    return data


@router.post("/actions/log/{entry_id}/undo")
async def undo_action(entry_id: str, request: Request) -> dict:
    if not _ACTION_ID_RE.fullmatch(entry_id):
        raise HTTPException(status_code=400, detail="Invalid id.")
    auth = {k: v for k, v in request.headers.items() if k.lower() in ("cookie", "authorization") or "token" in k.lower() or "session" in k.lower()}
    code, data = await asyncio.to_thread(_actiond_sync, "POST", "/undo", {"id": entry_id, "auth": auth})
    if code == 401:
        raise HTTPException(status_code=401, detail="You are not signed in. Sign in again and retry.")
    if code in (404, 409):
        raise HTTPException(status_code=code, detail=str(data.get("error") or "That cannot be undone."))
    if code != 200:
        raise HTTPException(status_code=502, detail="The action service refused the request.")
    return data
