"""The idea pipeline: an idea moves through Research, Plan, Build and Review, one board card per stage.

Everything lives on the task board (see kanban_backend.py) and in the coding workspace:
  * an idea is a card that waits in the board's first column with nobody assigned (it never starts by itself)
  * a stage is a new card assigned to the board's default assignee; the dispatcher claims it and one worker runs it
  * every idea belongs to a project (its "home"): research.md, plan.md and review.md are written by the workers into home/ideas/<name>/, idea.md by this app
  * the default home is the notes project 'idea-pipeline', the incubator: there Build happens in a coding project of its own, created when the plan is finished
  * an idea whose home is any other existing project is built inside that project (a restore point is saved first)
  * the pipeline's own bookkeeping (which card belongs to which stage) is one small file in the app's data folder

Asking or approving everything: an idea has an "auto" switch. Off (Ask), each stage waits for a click. On, the next stage starts as soon as the
current one finishes, but only after you have started the first stage yourself, and never past a blocked or failed card.

  GET  /ideas[?project=NAME|all] the ideas of one project (all of them when no project or 'all'), each with its stage, the state of the stage's card and the next step
  POST /ideas                    capture an idea (a card in the first column, a notes file, a pipeline entry)
  POST /ideas/{id}/advance       start the next stage
  POST /ideas/{id}/auto          switch Ask / Approve everything for this idea
  POST /ideas/{id}/drop          take an idea off the pipeline (its card and notes stay)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import kanban_backend as kb

NOTES_PROJECT = "idea-pipeline"
WORKSPACE_ROOT = "/workspace"                      # where the agent sees the coding projects
STAGES = ["idea", "research", "plan", "build", "review", "done"]
WORK_STAGES = ["research", "plan", "build", "review"]
FILES = {"idea": "idea.md", "research": "research.md", "plan": "plan.md", "review": "review.md"}
ID_RE = re.compile(r"^t_[A-Za-z0-9]{3,24}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
STAGE_PRIORITY = 1
LOG = logging.getLogger(__name__)

HERMES = None
CFG = None
DATA: Optional[Path] = None
router = APIRouter()
_lock = asyncio.Lock()


# ---- the pipeline's own small file --------------------------------------------------------------------------------

def _file() -> Path:
    return DATA / "pipeline.json"


def _load() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("ideas"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"ideas": {}}


def _save(d: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = _file().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, _file())


# ---- the coding workspace -----------------------------------------------------------------------------------------

async def _code(method: str, path: str, params=None, body=None):
    if not (CFG and CFG.code_url and CFG.code_token):
        raise HTTPException(status_code=503, detail="The coding workspace is not set up on this install.")
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.request(method, CFG.code_url.rstrip("/") + path, params=params, json=body, headers={"X-Code-Token": CFG.code_token})
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="The coding workspace is not reachable.") from None
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code if r.status_code in (400, 404, 409) else 502, detail=str((data or {}).get("detail", "Failed."))[:300])
    return data


async def _projects() -> list:
    return [p.get("name") for p in ((await _code("GET", "/projects")) or {}).get("projects", []) if isinstance(p, dict)]


async def _ensure_project(name: str) -> None:
    if name not in await _projects():
        await _code("POST", "/projects", body={"name": name})


async def _write(project: str, path: str, text: str) -> None:
    await _code("PUT", "/file", body={"project": project, "path": path, "text": text})


def _home(e: dict) -> str:
    """The project an idea belongs to: where its notes live and, unless it is the incubator, where it is built."""
    return e.get("home") or NOTES_PROJECT


def _slug(title: str, card_id: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:36].strip("-") or "idea"
    return base + "-" + card_id.replace("t_", "")[:6]


# ---- what each stage asks of the worker ---------------------------------------------------------------------------

def _brief(stage: str, e: dict, idea_body: str) -> tuple[str, str]:
    d = "ideas/%s" % e["slug"]
    home = _home(e)
    head = "Idea: %s\n\n%s\n\nThe idea's notes live in the project '%s' (this card's workspace), folder %s/. " % (e["title"], idea_body.strip() or "(no further notes)", home, d)
    if stage == "research":
        return "Research: " + e["title"], head + (
            "Research this idea online. Search the web, read the best sources, and write what you find to %s/research.md with these sections: "
            "What it is; What already exists (names, links, how they compare); What it would take (effort, cost, skills, risks); Recommendation (build, adapt something, or skip, and why); Open questions for the owner. "
            "Before recommending anything new, look for existing open-source projects that already do most of the job (search GitHub and the web). For each strong candidate say what it covers, how active it is and its licence. "
            "Prefer adopting or forking a good one and writing only the gaps; recommend building from scratch only when none fits, and say why. "
            "Cite the web address of every source. Do not build anything and do not change other files. Finish the card with a short summary of the recommendation." % d)
    if stage == "plan":
        return "Plan: " + e["title"], head + (
            "Read %s/idea.md and %s/research.md, then write %s/plan.md: Goal; What is in and out of scope; Steps in order (small, each checkable); Files or systems touched; How it will be tested; Risks. "
            "Keep it under two pages and write down every shared choice (names, formats) so a builder who sees only this plan makes the same ones. "
            "If a decision belongs to the owner, put it in the summary and block the card with the question. Do not build anything." % (d, d, d))
    if stage == "build":
        return "Build: " + e["title"], (
            "Idea: %s\n\nBuild this in the project '%s' (this card's workspace). The approved plan is in the notes project '%s' at %s/plan.md "
            "(read it with the file tool at %s/%s/plan.md). Follow it step by step, keep changes small, test as you go, and commit your work with git. "
            "Do not touch other projects. Finish the card with a summary of what you built, how you checked it, and what is still untested. "
            "If a step needs an owner decision or access you do not have, block the card with the question instead of guessing." % (e["title"], e.get("project"), home, d, WORKSPACE_ROOT, home + "/" + d))
    return "Review: " + e["title"], (
        "Idea: %s\n\nReview the finished work in the project '%s' against the plan at %s/%s/%s/plan.md. Run its tests or try it, list what works, what is wrong, what is missing, and what was never tested. "
        "Write the review to %s/%s/%s/review.md (workspace of the notes project) with a clear verdict: ready, needs changes, or not worth keeping. Do not change the project itself. Finish with the verdict in the summary."
        % (e["title"], e.get("project"), WORKSPACE_ROOT, home, d, WORKSPACE_ROOT, home, d))


def _marker(iid: str, stage: str) -> str:
    return "[atrium-pipeline %s %s]" % (iid, stage)


def _find_card(cards: dict, iid: str, stage: str) -> Optional[str]:
    """The card an earlier, interrupted attempt already made for this idea and stage, if any."""
    mark = _marker(iid, stage)
    return next((cid for cid, t in cards.items() if str(t.get("preview") or "").startswith(mark)), None)


async def _create_card(stage: str, e: dict, idea_body: str, iid: str) -> str:
    title, body = _brief(stage, e, idea_body)
    body = _marker(iid, stage) + "\n" + body
    ws = WORKSPACE_ROOT + "/" + ((e.get("project") if stage == "build" else _home(e)))
    orch = await kb._dash("GET", "/orchestration")
    who = (orch or {}).get("resolved_default_assignee") or "default"
    made = await kb._dash("POST", "/tasks", {"title": title, "body": body, "assignee": who, "priority": STAGE_PRIORITY, "triage": False,
                                              "workspace_kind": "dir", "workspace_path": ws})
    task = (made or {}).get("task") or made or {}
    cid = task.get("id")
    if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
        raise HTTPException(status_code=502, detail="The board did not return the new card.")
    return cid


# ---- reading the state --------------------------------------------------------------------------------------------

def _card_state(card: Optional[dict]) -> str:
    """working | blocked | finished | missing"""
    if not card:
        return "missing"
    s = card.get("status")
    if s == "done":
        return "finished"
    if s in ("blocked", "triage") or (card.get("failures") or 0) >= 2:
        return "blocked"
    return "working"


async def _cards() -> dict:
    board = await kb.board()
    out = {}
    for col in board["columns"]:
        for t in col["tasks"]:
            out[t["id"]] = t
    return out


def _view(iid: str, e: dict, cards: dict) -> dict:
    stage = e["stage"]
    cur = cards.get(e["cards"].get(stage)) if stage in WORK_STAGES else None
    state = "waiting" if stage == "idea" else "finished" if stage == "done" else _card_state(cur)
    nxt = None
    if stage == "idea":
        nxt = "research"
    elif state == "finished" and stage in WORK_STAGES:
        nxt = STAGES[STAGES.index(stage) + 1]
    return {"id": iid, "title": e["title"], "slug": e["slug"], "stage": stage, "state": state, "next": nxt, "auto": bool(e.get("auto")), "project": e.get("project"), "home": _home(e),
            "cards": e["cards"], "auto_error": e.get("auto_error"), "created": e.get("created"), "files": {k: "ideas/%s/%s" % (e["slug"], v) for k, v in FILES.items()},
            "card": ({"id": cur["id"], "status": cur["status"], "running": cur.get("running"), "summary": cur.get("summary"), "error": cur.get("last_error")} if cur else None),
            "idea_card": (cards.get(iid) or {}).get("status")}


@router.get("/ideas")
async def ideas(project: Optional[str] = None) -> dict:
    if project and project != "all" and not NAME_RE.fullmatch(project):
        raise HTTPException(status_code=400, detail="Bad project.")
    d = _load()
    cards = await _cards()
    rows = sorted(d["ideas"].items(), key=lambda kv: kv[1].get("created", 0), reverse=True)
    if project and project != "all":
        rows = [(i, e) for i, e in rows if _home(e) == project]
    return {"stages": STAGES, "ideas": [_view(i, e, cards) for i, e in rows], "notes_project": NOTES_PROJECT, "project": project or "all"}


class NewIdea(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: Optional[str] = Field(default=None, max_length=20000)
    auto: bool = False
    project: Optional[str] = Field(default=None, max_length=60)             # the project this idea belongs to; empty = the incubator


@router.post("/ideas")
async def capture(body: NewIdea) -> dict:
    home = (body.project or "").strip() or NOTES_PROJECT
    if not NAME_RE.fullmatch(home):
        raise HTTPException(status_code=400, detail="Bad project.")
    if home != NOTES_PROJECT and home not in await _projects():
        raise HTTPException(status_code=404, detail="That project does not exist.")
    made = await kb._dash("POST", "/tasks", {"title": body.title.strip(), "body": (body.body or "").strip() or None, "priority": 0, "triage": True})
    task = (made or {}).get("task") or made or {}
    cid = task.get("id")
    if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
        raise HTTPException(status_code=502, detail="The board did not return the new card.")
    e = {"title": body.title.strip(), "slug": _slug(body.title, cid), "stage": "idea", "auto": body.auto, "cards": {}, "project": None, "home": home, "created": time.time(), "idea_body": (body.body or "").strip()}
    await _ensure_project(home)
    await _write(home, "ideas/%s/idea.md" % e["slug"], "# %s\n\nCaptured %s.\n\n%s\n" % (e["title"], time.strftime("%Y-%m-%d %H:%M"), e["idea_body"] or "(no further notes yet)"))
    async with _lock:
        d = _load()
        d["ideas"][cid] = e
        _save(d)
    return {"id": cid, "slug": e["slug"]}


async def _advance(iid: str, by_auto: bool = False) -> dict:
    async with _lock:
        d = _load()
        e = d["ideas"].get(iid)
        if not e:
            raise HTTPException(status_code=404, detail="That idea is not on the pipeline.")
        cards = await _cards()
        v = _view(iid, e, cards)
        if not v["next"]:
            raise HTTPException(status_code=409, detail="The current stage is still running." if v["state"] == "working" else "It is blocked: answer the card on the board first." if v["state"] == "blocked" else "Nothing is left to start.")
        nxt = v["next"]
        if nxt == "done":
            await kb._dash("PATCH", "/tasks/" + iid, {"status": "done", "summary": "Finished through the idea pipeline (research, plan, build, review)."})
            e["stage"] = "done"
        else:
            if nxt == "build" and _home(e) != NOTES_PROJECT:
                e["project"] = _home(e)                          # built inside the project the idea belongs to, after a restore point
                await _code("POST", "/git/checkpoint", body={"project": e["project"], "label": ("before pipeline build: " + e["title"])[:40]})
            elif nxt == "build":
                retry = bool(e.get("project"))                  # an earlier attempt already chose (and may have made) the project
                if not retry:
                    taken = set(await _projects())
                    name, n = e["slug"], 2
                    while name in taken:
                        name, n = "%s-%d" % (e["slug"], n), n + 1
                    e["project"] = name
                    _save(d)                                    # remember the name first: a retry must not make a second project
                try:
                    await _code("POST", "/projects", body={"name": e["project"]})
                except HTTPException as x:
                    if not (retry and x.status_code == 409):    # on a retry, "already exists" is our own earlier attempt
                        raise
            made = _find_card(cards, iid, nxt)                  # an interrupted attempt may already have made this stage's card
            e["cards"][nxt] = made or await _create_card(nxt, e, e.get("idea_body", ""), iid)
            e["stage"] = nxt
            e.pop("auto_error", None)
            if not by_auto and nxt == "research":
                e["started_by_user"] = True
        _save(d)
        return _view(iid, e, await _cards())


@router.post("/ideas/{iid}/advance")
async def advance(iid: str) -> dict:
    if not ID_RE.fullmatch(iid):
        raise HTTPException(status_code=400, detail="Bad id.")
    return await _advance(iid)


class AutoBody(BaseModel):
    on: bool


@router.post("/ideas/{iid}/auto")
async def auto(iid: str, body: AutoBody) -> dict:
    if not ID_RE.fullmatch(iid):
        raise HTTPException(status_code=400, detail="Bad id.")
    async with _lock:
        d = _load()
        if iid not in d["ideas"]:
            raise HTTPException(status_code=404, detail="That idea is not on the pipeline.")
        d["ideas"][iid]["auto"] = body.on
        _save(d)
    return {"ok": True, "auto": body.on}


@router.post("/ideas/{iid}/drop")
async def drop(iid: str) -> dict:
    if not ID_RE.fullmatch(iid):
        raise HTTPException(status_code=400, detail="Bad id.")
    async with _lock:
        d = _load()
        d["ideas"].pop(iid, None)
        _save(d)
    return {"ok": True}


# ---- Approve everything: start the next stage when the last one is finished -------------------------------------------

async def chain_once() -> int:
    """One pass: for every idea on Approve everything whose stage card just finished, start the next stage. Returns how many were started."""
    d = _load()
    started = 0
    for iid, e in list(d["ideas"].items()):
        if not e.get("auto") or e["stage"] in ("idea", "done"):          # never starts a first stage by itself
            continue
        try:
            cards = await _cards()
            v = _view(iid, e, cards)
            if v["state"] == "finished" and v["next"]:
                await _advance(iid, by_auto=True)
                started += 1
        except HTTPException as x:
            LOG.warning("Approve-everything could not start the next stage of %s: %s", iid, x.detail)
            async with _lock:                                    # shown on the idea so it is not a silent stall
                d2 = _load()
                if iid in d2["ideas"]:
                    d2["ideas"][iid]["auto_error"] = {"at": time.time(), "detail": str(x.detail)[:200]}
                    _save(d2)
    return started


async def _loop() -> None:
    while True:
        await asyncio.sleep(45)
        try:
            await chain_once()
        except Exception:                                  # noqa: BLE001  (a pass that fails is tried again next time)
            LOG.exception("The Approve-everything pass failed")


def configure(cfg, hermes, app) -> None:
    global CFG, HERMES, DATA
    CFG, HERMES, DATA = cfg, hermes, Path(cfg.data_dir)
    kb.HERMES = hermes
    if hasattr(app, "router"):
        async def start():
            asyncio.get_running_loop().create_task(_loop())
        app.router.on_startup.append(start)
