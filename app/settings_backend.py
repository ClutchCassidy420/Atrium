"""Atrium settings backend, vendored from the Atrium Loom plugin and adapted to run inside the Atrium web app.

Adaptations: the jobs list and a schedule change go through the Hermes dashboard's job routes (the `hermes` command only exists inside
Hermes' own container).

Original description follows.

Atrium Loom: Settings (dashboard plugin backend).

Routes (mounted by the Hermes dashboard under /api/plugins/atrium-settings):
  GET  /schedule          the times the agent's scheduled jobs run, as simple fields (not cron text)
  POST /schedule/{id}     change one job's times; the cron text is built here from validated numbers

Notification preferences and push devices live in the Chat plugin (/api/plugins/atrium-chat/settings/notify and
/push/devices), because that is where the push code is. Changing a time uses Hermes's own `hermes cron edit`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from agentkit import (AgentRoute as _AgentRoute)

# --- the agent's name --------------------------------------------------------------------------------------------
# Text in this file says {{agent}} wherever it means the agent's name. It is filled in on the way out (JSON, errors,
# streams, files) from <data>/atrium/agent.json, which the Settings tab writes. Without it the name is "Assistant".

router = APIRouter(route_class=_AgentRoute)
_ID_RE = re.compile(r"^[a-f0-9]{12}$")
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
INTERVALS = [5, 10, 15, 20, 30, 60]


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or "/opt/data")


# --- adaptation for the Atrium web app ---------------------------------------------------------------------------------
# The scheduler is reached through the Hermes dashboard's job routes (the `hermes` command only exists inside Hermes' own
# container). The app sets HERMES to its connection to Hermes.
HERMES = None


def _dash(method: str, path: str, payload=None, timeout: float = 20.0):
    if HERMES is None or not getattr(HERMES, "dash_base", ""):
        raise HTTPException(status_code=503, detail="The scheduler is not connected.")
    status, body = HERMES._send(method, HERMES.dash_base + path, HERMES._dash_headers(), payload, timeout)
    try:
        return status, (json.loads(body) if body else None)
    except ValueError:
        return status, None


def _jobs() -> list[dict]:
    status, data = _dash("GET", "/api/cron/jobs")
    return [j for j in data if isinstance(j, dict) and j.get("id")] if status == 200 and isinstance(data, list) else []


def _expr(job: dict) -> str:
    sched = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
    return sched.get("expr") or job.get("schedule_display") or ""


def _clock(h: int, m: int) -> str:
    return "%d:%02d %s" % ((h % 12) or 12, m, "AM" if h < 12 else "PM")


def parse(expr: str) -> Optional[dict]:
    """Recognise the shapes the app creates; anything else is shown but not editable here."""
    try:
        m, h, dom, mon, dow = expr.split()
    except ValueError:
        return None
    if dom != "*" or mon != "*":
        return None
    if re.fullmatch(r"\d+", m) and re.fullmatch(r"\d+", h) and dow == "*":
        return {"kind": "daily", "hour": int(h), "minute": int(m)}
    if re.fullmatch(r"\d+", m) and re.fullmatch(r"\d+", h) and re.fullmatch(r"[0-6]", dow):
        return {"kind": "weekly", "hour": int(h), "minute": int(m), "day": int(dow)}
    if re.fullmatch(r"\d+", m) and re.fullmatch(r"\d+(,\d+)+", h) and dow == "*":
        return {"kind": "times", "minute": int(m), "hours": [int(x) for x in h.split(",")]}
    mm, hr = re.fullmatch(r"\*/(\d+)", m), re.fullmatch(r"(\d+)-(\d+)", h)
    if mm and hr and dow == "*":
        return {"kind": "window", "interval": int(mm.group(1)), "start": int(hr.group(1)), "end": int(hr.group(2))}
    return None


def human(p: dict) -> str:
    k = p["kind"]
    if k == "daily":
        return "Every day at " + _clock(p["hour"], p["minute"])
    if k == "weekly":
        return DAYS[p["day"]] + "s at " + _clock(p["hour"], p["minute"])
    if k == "times":
        return "Every day at " + ", ".join(_clock(x, p["minute"]) for x in p["hours"])
    return "Every %d minutes from %s to %s" % (p["interval"], _clock(p["start"], 0), _clock(p["end"], 59))


def build(kind: str, body: "ScheduleBody") -> dict:
    def hour(v: Optional[int]) -> int:
        if v is None or not 0 <= v <= 23:
            raise HTTPException(status_code=400, detail="Hours are 0 to 23.")
        return v

    def minute(v: Optional[int]) -> int:
        if v is None or not 0 <= v <= 59:
            raise HTTPException(status_code=400, detail="Minutes are 0 to 59.")
        return v
    if kind == "daily":
        return {"kind": "daily", "hour": hour(body.hour), "minute": minute(body.minute)}
    if kind == "weekly":
        if body.day is None or not 0 <= body.day <= 6:
            raise HTTPException(status_code=400, detail="Choose a day of the week.")
        return {"kind": "weekly", "hour": hour(body.hour), "minute": minute(body.minute), "day": body.day}
    if kind == "times":
        hours = sorted({hour(x) for x in (body.hours or [])})
        if not 2 <= len(hours) <= 8:
            raise HTTPException(status_code=400, detail="Pick between 2 and 8 times a day.")
        return {"kind": "times", "minute": minute(body.minute), "hours": hours}
    if body.interval not in INTERVALS:
        raise HTTPException(status_code=400, detail="Choose one of: " + ", ".join(str(x) for x in INTERVALS) + " minutes.")
    a, b = hour(body.start), hour(body.end)
    if a > b:
        raise HTTPException(status_code=400, detail="The window must end after it starts.")
    return {"kind": "window", "interval": body.interval, "start": a, "end": b}


def to_cron(p: dict) -> str:
    k = p["kind"]
    if k == "daily":
        return "%d %d * * *" % (p["minute"], p["hour"])
    if k == "weekly":
        return "%d %d * * %d" % (p["minute"], p["hour"], p["day"])
    if k == "times":
        return "%d %s * * *" % (p["minute"], ",".join(str(x) for x in p["hours"]))
    return "*/%d %d-%d * * *" % (p["interval"], p["start"], p["end"])


class ScheduleBody(BaseModel):
    hour: Optional[int] = None
    minute: Optional[int] = None
    day: Optional[int] = None
    hours: Optional[list[int]] = Field(default=None, max_length=8)
    interval: Optional[int] = None
    start: Optional[int] = None
    end: Optional[int] = None


@router.get("/schedule")
async def schedule() -> dict:
    def build_list() -> list[dict]:
        out = []
        for j in _jobs():
            expr = _expr(j)
            p = parse(expr)
            out.append({"id": j["id"], "name": j.get("name") or j["id"], "expr": expr, "editable": p is not None,
                        "values": p, "when": human(p) if p else expr, "next_run": j.get("next_run_at"),
                        "enabled": bool(j.get("enabled", True)) and not j.get("paused_at")})
        return out
    return {"jobs": await asyncio.to_thread(build_list), "days": DAYS, "intervals": INTERVALS}


@router.post("/schedule/{job_id}")
async def set_schedule(job_id: str, body: ScheduleBody) -> dict:
    if not _ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="Invalid job id.")
    job = next((j for j in _jobs() if j["id"] == job_id), None)
    if not job:
        raise HTTPException(status_code=404, detail="No such job.")
    current = parse(_expr(job))
    if not current:
        raise HTTPException(status_code=409, detail="That job has a schedule shape this page does not edit.")
    new = build(current["kind"], body)
    cron = to_cron(new)
    def run() -> None:
        status, _ = _dash("PUT", "/api/cron/jobs/%s" % job_id, {"updates": {"schedule": cron}})
        if status != 200:
            raise HTTPException(status_code=502, detail="The scheduler refused that change.")
    await asyncio.to_thread(run)
    return {"ok": True, "when": human(new), "expr": cron}


# --- your own chat model --------------------------------------------------------------------------------------------

class ModelSelection(BaseModel):
    id: str = Field(default="", max_length=240)
    provider: str = Field(default="", max_length=80)
    confirm: bool = False


NOT_CLOUD = ("custom", "ollama", "lmstudio")          # providers that are the machine's own models, not a cloud service


async def cloud_catalog() -> list:
    """The cloud providers that are connected to the agent, with the models each offers."""
    code, catalog = await HERMES.dash_call("GET", "/api/model/options", timeout=25)
    if code != 200 or not isinstance(catalog, dict):
        return []
    out = []
    for p in catalog.get("providers") or []:
        if not isinstance(p, dict) or not p.get("authenticated") or p.get("is_user_defined") or p.get("slug") in NOT_CLOUD:
            continue
        models = [m for m in (p.get("models") or []) if isinstance(m, str)]
        if p.get("slug") and models:
            out.append({"id": p["slug"], "label": p.get("name") or p["slug"], "models": models})
    return out


@router.get("/models")
async def models(request: Request):
    import chat_model
    from mission_backend import engine_state

    return {"cloud": await cloud_catalog(), "engine": chat_model.selection(request.state.who["user"]) or engine_state()}


@router.post("/models")
async def select_model(body: ModelSelection, request: Request):
    """Use a model from a connected cloud provider for your chats. Your chat text is sent to that provider, so it needs an explicit yes."""
    import chat_model

    if not body.provider or not body.id:
        raise HTTPException(400, "Choose a provider and a model.")
    if not body.confirm:
        raise HTTPException(400, "Confirm sending your chats to this cloud provider.")
    if not any(p["id"] == body.provider and body.id in p["models"] for p in await cloud_catalog()):
        raise HTTPException(409, "Choose a model from a connected cloud provider.")
    await asyncio.to_thread(chat_model.save, request.state.who["user"], {"engine": "cloud", "provider": body.provider, "model": body.id})
    return {"ok": True}


@router.post("/models/use-default")
async def use_default_model(request: Request):
    """Go back to the install's default model for your chats."""
    import chat_model

    await asyncio.to_thread(chat_model.save, request.state.who["user"], {"engine": "local"})
    return {"ok": True}

