"""The task board (kanban) page's backend.

A thin, allow-listed bridge to the agent dashboard's board routes (/api/plugins/kanban/...), using the app's own dashboard token, so the
browser never sees that token and can only do what is listed here. Owners only (the app mounts it behind the "admin" permission).

  GET    /board                the columns (with labels and which accept a drop) and slim cards, the assignees, who starts new work
  GET    /tasks/{id}           one card: body, comments, recent events, runs, links, attachments
  GET    /tasks/{id}/log       the tail of the worker's log
  POST   /tasks                create a card (an idea waits in triage: no one is assigned, so no worker starts)
  PATCH  /tasks/{id}           title, body, priority, assignee, status (a move) or a block reason
  DELETE /tasks/{id}
  POST   /tasks/{id}/comments
  POST   /tasks/{id}/start     "Start with the agent": write the spec, then assign the card to the board's default assignee (a worker claims it)
  POST   /tasks/{id}/reclaim   take a card back from a worker
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

HERMES = None        # the app's connection to the agent dashboard (set by the mount)
BASE = "/api/plugins/kanban"
ID_RE = re.compile(r"^t_[A-Za-z0-9]{3,24}$")
COLUMN_RE = re.compile(r"^[a-z_]{2,20}$")
PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
WORKSPACE_ROOT = "/workspace"          # where the agent sees the coding projects
PREVIEW = 220

router = APIRouter()


def _check(task_id: str) -> str:
    if not ID_RE.fullmatch(task_id or ""):
        raise HTTPException(status_code=400, detail="Bad card id.")
    return task_id


async def _dash(method: str, path: str, payload=None):
    if HERMES is None or not getattr(HERMES, "dash_base", ""):
        raise HTTPException(status_code=503, detail="The agent dashboard is not connected.")
    status, data = await HERMES.dash_call(method, BASE + path, payload)
    if status == 404:
        raise HTTPException(status_code=404, detail="No such card.")
    if status in (400, 409, 422):
        detail = data.get("detail") if isinstance(data, dict) else None
        raise HTTPException(status_code=400, detail=(detail if isinstance(detail, str) else "The board refused that.")[:300])
    if status < 200 or status >= 300:
        raise HTTPException(status_code=502, detail="The board did not answer.")
    return data


def _slim(t: dict) -> dict:
    body = str(t.get("body") or "")
    summary = str(t.get("latest_summary") or "")
    return {"id": t.get("id"), "title": t.get("title"), "assignee": t.get("assignee"), "status": t.get("status"), "priority": t.get("priority") or 0,
            "progress": t.get("progress"), "comment_count": t.get("comment_count") or 0, "link_counts": t.get("link_counts"),
            "created_at": t.get("created_at"), "started_at": t.get("started_at"), "completed_at": t.get("completed_at"),
            "preview": re.sub(r"\s+", " ", body)[:PREVIEW], "summary": re.sub(r"\s+", " ", summary)[:PREVIEW],
            "block_kind": t.get("block_kind"), "running": bool(t.get("current_run_id")), "failures": t.get("consecutive_failures") or 0,
            "last_error": str(t.get("last_failure_error") or "")[:160]}


@router.get("/board")
async def board() -> dict:
    data = await _dash("GET", "/board")
    flow = await _dash("GET", "/workflow")
    orch = await _dash("GET", "/orchestration")
    by_name = {c.get("name"): c.get("tasks") or [] for c in (data.get("columns") or []) if isinstance(c, dict)}
    cols = []
    for c in (flow.get("columns") or []):
        key = c.get("key")
        tasks = [_slim(t) for t in by_name.pop(key, [])]
        cols.append({"key": key, "label": c.get("label") or key, "icon": c.get("icon") or "", "drop": bool(c.get("drag_target")), "tasks": tasks})
    for key, tasks in by_name.items():                                    # a column the workflow list did not name
        if tasks:
            cols.append({"key": key, "label": str(key).title(), "icon": "", "drop": False, "tasks": [_slim(t) for t in tasks]})
    who = await _dash("GET", "/assignees")
    names = [a.get("name") for a in ((who or {}).get("assignees") or []) if isinstance(a, dict) and a.get("name")]
    return {"columns": cols, "now": data.get("now"), "assignees": names, "starter": (orch or {}).get("resolved_default_assignee") or "default",
            "auto_decompose": bool((orch or {}).get("auto_decompose"))}


@router.get("/tasks/{task_id}")
async def card(task_id: str) -> dict:
    d = await _dash("GET", "/tasks/" + _check(task_id))
    events = d.get("events") or []
    return {"task": d.get("task"), "comments": (d.get("comments") or [])[-60:], "events": events[-40:], "runs": (d.get("runs") or [])[-10:],
            "links": d.get("links"), "attachments": d.get("attachments") or []}


@router.get("/tasks/{task_id}/log")
async def log(task_id: str) -> dict:
    d = await _dash("GET", "/tasks/" + _check(task_id) + "/log?tail=20000")
    return {"exists": bool(d.get("exists")), "content": str(d.get("content") or "")[-20000:], "size": d.get("size_bytes")}


class NewCard(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: Optional[str] = Field(default=None, max_length=20000)
    priority: int = Field(default=0, ge=0, le=9)
    triage: bool = True
    project: Optional[str] = Field(default=None, max_length=60)      # a coding project: the work happens in its folder and is kept (the default workspace is deleted when the card completes)


class EditCard(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    body: Optional[str] = Field(default=None, max_length=20000)
    priority: Optional[int] = Field(default=None, ge=0, le=9)
    assignee: Optional[str] = Field(default=None, max_length=60)
    status: Optional[str] = None
    block_reason: Optional[str] = Field(default=None, max_length=500)
    summary: Optional[str] = Field(default=None, max_length=4000)    # the hand-off note when a card is marked done or sent to review


class Comment(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


@router.post("/tasks")
async def create(body: NewCard) -> dict:
    payload = {"title": body.title.strip(), "body": (body.body or "").strip() or None, "priority": body.priority, "triage": body.triage}
    if body.project:
        if not PROJECT_RE.fullmatch(body.project):
            raise HTTPException(status_code=400, detail="Bad project name.")
        payload.update({"workspace_kind": "dir", "workspace_path": WORKSPACE_ROOT + "/" + body.project})
    d = await _dash("POST", "/tasks", payload)
    return d if isinstance(d, dict) else {"ok": True}


@router.patch("/tasks/{task_id}")
async def edit(task_id: str, body: EditCard) -> dict:
    _check(task_id)
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    if "status" in patch:
        flow = await _dash("GET", "/workflow")
        drops = {c.get("key") for c in (flow.get("columns") or []) if c.get("drag_target")}
        if not COLUMN_RE.fullmatch(patch["status"]) or patch["status"] not in drops:
            raise HTTPException(status_code=400, detail="A card cannot be moved there by hand.")
    d = await _dash("PATCH", "/tasks/" + task_id, patch)
    return d if isinstance(d, dict) else {"ok": True}


@router.delete("/tasks/{task_id}")
async def remove(task_id: str) -> dict:
    await _dash("DELETE", "/tasks/" + _check(task_id))
    return {"ok": True}


@router.post("/tasks/{task_id}/comments")
async def comment(task_id: str, body: Comment, request: Request) -> dict:
    who = getattr(request.state, "who", None) or {}
    d = await _dash("POST", "/tasks/" + _check(task_id) + "/comments", {"body": body.body.strip(), "author": str(who.get("user") or "owner")[:40]})
    return d if isinstance(d, dict) else {"ok": True}


@router.post("/tasks/{task_id}/start")
async def start(task_id: str) -> dict:
    """Write the spec (the board's Specify action), then assign the card to the default assignee: the dispatcher only claims assigned cards."""
    _check(task_id)
    res = await _dash("POST", "/tasks/" + task_id + "/specify", {"author": "web-app"})
    if isinstance(res, dict) and res.get("ok") is False:
        return {"ok": False, "reason": str(res.get("reason") or res.get("error") or "The board could not write a spec for this card.")[:300]}
    orch = await _dash("GET", "/orchestration")
    who = (orch or {}).get("resolved_default_assignee") or "default"
    await _dash("PATCH", "/tasks/" + task_id, {"assignee": who})
    return {"ok": True, "assignee": who}


@router.post("/tasks/{task_id}/reclaim")
async def reclaim(task_id: str) -> dict:
    d = await _dash("POST", "/tasks/" + _check(task_id) + "/reclaim", {"reason": "taken back in the web app"})
    return d if isinstance(d, dict) else {"ok": True}
