"""Atrium workflows backend, vendored from the Atrium Loom plugin and adapted to run inside the Atrium web app.

Adaptations: the jobs, their runs and run-now / pause / resume go through the Hermes dashboard's job routes instead of
jobs.json, the executions database and the `hermes cron` command. Radar topics and the activity feed are unchanged.

Original description follows.

Atrium Loom: Workflows (dashboard plugin backend).

Routes (mounted by the Hermes dashboard under /api/plugins/atrium-workflows):
  GET  /jobs                      the agent's scheduled jobs: schedule in plain words, next and last run, status, last answer
  POST /jobs/{id}/run             start a job now (in the background)
  POST /jobs/{id}/pause|resume    switch a job off or on
  GET  /jobs/{id}/runs            the last few runs of one job
  GET  /interests                 the topics the Nightly Radar searches for
  POST /interests                 replace that list (up to 12 short topics)
  GET  /activity                  what the agent did lately by herself (calendar changes, files)

Everything is read from, or done through, Hermes's own files and `hermes cron` command; nothing is stored here.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from agentkit import (AgentRoute as _AgentRoute)

# --- the agent's name --------------------------------------------------------------------------------------------
# Text in this file says {{agent}} wherever it means the agent's name. It is filled in on the way out (JSON, errors,
# streams, files) from <data>/atrium/agent.json, which the Settings tab writes. Without it the name is "Assistant".

router = APIRouter(route_class=_AgentRoute)

_ID_RE = re.compile(r"^[a-f0-9]{12}$")
MAX_TOPICS = 12
DAYS = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"]


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


def _clock(h: int, m: int) -> str:
    return "%d:%02d %s" % ((h % 12) or 12, m, "AM" if h < 12 else "PM")


def human(expr: str) -> str:
    """A cron expression in plain words (the common shapes; anything else is shown as written)."""
    try:
        m, h, dom, mon, dow = expr.split()
    except ValueError:
        return expr
    if dom != "*" or mon != "*":
        return expr
    when = {"*": "every day"}.get(dow)
    if when is None:
        if re.fullmatch(r"[0-6]", dow):
            when = DAYS[int(dow)]
        elif re.fullmatch(r"[0-6](,[0-6])+", dow):
            when = ", ".join(DAYS[int(x)] for x in dow.split(","))
        else:
            return expr
    mm = re.fullmatch(r"\*/(\d+)", m)
    hr = re.fullmatch(r"(\d+)-(\d+)", h)
    if mm and hr:
        return "%s, every %s minutes from %s to %s" % (when.capitalize(), mm.group(1), _clock(int(hr.group(1)), 0), _clock(int(hr.group(2)), 59))
    if re.fullmatch(r"\d+", m) and re.fullmatch(r"\d+(,\d+)*", h):
        times = ", ".join(_clock(int(x), int(m)) for x in h.split(","))
        return "%s at %s" % (when.capitalize(), times)
    return expr


def _iso(v):
    try:
        import datetime as _dt
        return _dt.datetime.fromtimestamp(float(v), _dt.timezone.utc).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _runs(job_id: str, limit: int) -> list[dict]:
    status, data = _dash("GET", "/api/cron/jobs/%s/runs?limit=%d" % (job_id, limit))
    rows = data.get("runs", []) if status == 200 and isinstance(data, dict) else []
    out = []
    for r in rows[:limit]:
        reason = r.get("end_reason") or ""
        if not r.get("ended_at"):
            st, err = ("running", "") if r.get("is_active") else ("interrupted", "Cut off before it finished.")
        elif reason in ("", "cron_complete"):
            st, err = "ok", ""
        elif reason == "startup_orphan_reap":
            st, err = "interrupted", "Cut off when Hermes restarted."
        else:
            st, err = "failed", reason[:200]
        out.append({"status": st, "started": _iso(r.get("started_at")), "finished": _iso(r.get("ended_at")), "error": err})
    return out


_ANSWERS: dict = {}     # job id -> (run id, answer): a finished run's answer never changes


def _tidy(text: str) -> str:
    _, _, tail = text.rpartition("## Response")
    tail = re.sub(r"```.*?```", " ", tail, flags=re.S)
    tail = re.sub(r"[*_`#>]+", "", tail)
    return re.sub(r"\s+", " ", tail).strip()[:320]


def _answer_from_run(job_id: str) -> str:
    """Hermes keeps the output files private to its own user, so read the last run's final reply through the dashboard."""
    status, data = _dash("GET", "/api/cron/jobs/%s/runs?limit=1" % job_id)
    rows = data.get("runs", []) if status == 200 and isinstance(data, dict) else []
    if not rows or not rows[0].get("ended_at") or not rows[0].get("id"):
        return ""
    rid = str(rows[0]["id"])
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", rid):
        return ""
    cached = _ANSWERS.get(job_id)
    if cached and cached[0] == rid:
        return cached[1]
    status, data = _dash("GET", "/api/sessions/%s/messages" % rid)
    msgs = data.get("messages", []) if status == 200 and isinstance(data, dict) else []
    text = ""
    for m in reversed(msgs):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip():
            text = _tidy(m["content"]) or re.sub(r"\s+", " ", m["content"]).strip()[:320]
            break
    _ANSWERS[job_id] = (rid, text)
    return text


def _last_answer(job_id: str) -> str:
    folder = _home() / "cron" / "output" / job_id
    try:
        files = sorted(folder.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            return _tidy(files[0].read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        pass
    try:
        return _answer_from_run(job_id)
    except Exception:       # noqa: BLE001 - the answer is a nicety; never break the job list for it
        return ""


def _check_job(job_id: str) -> dict:
    if not _ID_RE.fullmatch(job_id or ""):
        raise HTTPException(status_code=400, detail="Invalid job id.")
    for j in _jobs():
        if j["id"] == job_id:
            return j
    raise HTTPException(status_code=404, detail="No such job.")


@router.get("/jobs")
async def jobs() -> dict:
    def build() -> list[dict]:
        out = []
        for j in _jobs():
            sched = j.get("schedule") if isinstance(j.get("schedule"), dict) else {}
            expr = sched.get("expr") or j.get("schedule_display") or ""
            out.append({
                "id": j["id"],
                "name": j.get("name") or j["id"],
                "schedule": expr,
                "when": human(expr) if expr else "",
                "enabled": bool(j.get("enabled", True)) and not j.get("paused_at"),
                "next_run": j.get("next_run_at"),
                "last_run": j.get("last_run_at"),
                "last_status": j.get("last_status"),
                "last_error": (j.get("last_error") or "")[:200] or None,
                "running": bool(j.get("live_execution")),
                "watches": bool(j.get("monitor_script") or j.get("monitor_url")),
                "answer": _last_answer(j["id"]),
            })
        return out
    return {"jobs": await asyncio.to_thread(build)}


@router.get("/jobs/{job_id}/runs")
async def job_runs(job_id: str) -> dict:
    _check_job(job_id)
    return {"runs": await asyncio.to_thread(_runs, job_id, 8)}


@router.post("/jobs/{job_id}/run")
async def run_job(job_id: str) -> dict:
    job = _check_job(job_id)
    if job.get("live_execution"):
        raise HTTPException(status_code=409, detail="That job is already running.")
    status, _ = await asyncio.to_thread(_dash, "POST", "/api/cron/jobs/%s/trigger" % job_id)
    if status != 200:
        raise HTTPException(status_code=502, detail="The scheduler refused that.")
    return {"ok": True}


def _cron(action: str, job_id: str) -> dict:
    status, _ = _dash("POST", "/api/cron/jobs/%s/%s" % (job_id, action))
    if status != 200:
        raise HTTPException(status_code=502, detail="The scheduler refused that.")
    return {"ok": True}


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str) -> dict:
    _check_job(job_id)
    return await asyncio.to_thread(_cron, "pause", job_id)


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str) -> dict:
    _check_job(job_id)
    return await asyncio.to_thread(_cron, "resume", job_id)


# --- Radar topics -------------------------------------------------------------

def _interests_path() -> Path:
    return _home() / "obsidian-vault" / "Radar" / "Interests.md"


def _read_interests() -> tuple[str, list[str]]:
    try:
        text = _interests_path().read_text(encoding="utf-8")
    except OSError:
        return "# Radar interests\n", []
    head, topics = [], []
    for line in text.splitlines():
        if line.startswith("- "):
            topics.append(line[2:].strip())
        elif not topics:
            head.append(line)
    return "\n".join(head).rstrip() + "\n", topics


class InterestsBody(BaseModel):
    topics: list[str] = Field(max_length=MAX_TOPICS)


@router.get("/interests")
async def interests() -> dict:
    _, topics = await asyncio.to_thread(_read_interests)
    return {"topics": topics, "max": MAX_TOPICS}


@router.post("/interests")
async def set_interests(body: InterestsBody) -> dict:
    topics = []
    for t in body.topics:
        t = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]+", " ", t or "")).strip()
        if t and len(t) <= 120 and t.lower() not in [x.lower() for x in topics]:
            topics.append(t)
    if not topics:
        raise HTTPException(status_code=400, detail="Keep at least one topic.")

    def write() -> None:
        head, _ = _read_interests()
        path = _interests_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(head + "\n" + "\n".join("- " + t for t in topics) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    await asyncio.to_thread(write)
    return {"topics": topics}


# --- what the agent did by herself ----------------------------------------------------

@router.get("/activity")
async def activity() -> dict:
    def read() -> list[dict]:
        try:
            lines = (_home() / "atrium" / "actions.log").read_text(encoding="utf-8", errors="ignore").splitlines()[-40:]
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            out.append({"t": str(e.get("t", ""))[:19], "action": str(e.get("action", ""))[:40], "summary": str(e.get("summary", ""))[:200]})
        return out[:25]
    return {"items": await asyncio.to_thread(read)}
