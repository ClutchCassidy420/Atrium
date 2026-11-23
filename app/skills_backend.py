"""Atrium skills backend: view, add, change and remove the agent's skills from Settings.

A skill is a folder under <HERMES_HOME>/skills (optionally inside a category folder) that holds a SKILL.md: a short header
(name and description between ``---`` lines) followed by the instructions the agent follows. The agent reads them at the start of
her next session, so a change here applies from then on.

Routes (owners only; mounted under /api/plugins/atrium-skills):
  GET    /list            every skill: id, name, description, category, whether it can be changed
  GET    /skill?id=       one skill's SKILL.md and the names of its other files
  POST   /skill           create or replace one skill's SKILL.md   {id, body, create}
  DELETE /skill?id=       move the skill's folder to skills/.trash (nothing is erased)

Writing works only where the skills folder is mounted writable (see deploy/compose.example.yaml); otherwise the routes say so.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from agentkit import (AgentRoute as _AgentRoute, atomic_write_text)

router = APIRouter(route_class=_AgentRoute)

MAX_SKILL_BYTES = 100_000
MAX_SKILLS = 1000
MAX_DEPTH = 3                       # category/name is the usual shape; one more level is tolerated
_PART = r"[a-z0-9][a-z0-9_-]{0,63}"
_ID_RE = re.compile(r"^" + _PART + r"(?:/" + _PART + r"){0,2}$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
READ_ONLY = "The skills folder is read-only on this install, so skills cannot be changed. Ask whoever runs the server to mount it writable."


def _root() -> Path:
    return Path(os.environ.get("HERMES_HOME") or "/opt/data") / "skills"


def _writable_dir(p: Path) -> bool:
    return p.is_dir() and os.access(p, os.W_OK | os.X_OK)


def _header(text: str) -> tuple[dict, bool]:
    """The ``key: value`` lines between the opening and closing ``---``. Returns (fields, whether the header is well formed)."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, False
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields, True
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            fields[m.group(1).lower()] = m.group(2).strip().strip("\"'")
    return fields, False


def _check_body(text: str) -> None:
    fields, ok = _header(text)
    if not ok:
        raise HTTPException(status_code=400, detail="A skill starts with a header: a line of three dashes, then name: and description: lines, then three dashes again.")
    if not _NAME_RE.match(fields.get("name", "")):
        raise HTTPException(status_code=400, detail="The header needs a name: of lowercase letters, numbers, dots, dashes or underscores (no spaces).")
    if not fields.get("description"):
        raise HTTPException(status_code=400, detail="The header needs a description: the one line that tells the agent when to use the skill.")
    if len(fields["description"]) > 1024:
        raise HTTPException(status_code=400, detail="Keep the description under 1,024 characters.")


def _clean_id(sid: str) -> str:
    sid = (sid or "").strip().strip("/")
    if not _ID_RE.match(sid):
        raise HTTPException(status_code=400, detail="A skill id is lowercase letters, numbers, dashes or underscores, optionally inside a category (for example research/web-search).")
    return sid


def _dir_of(sid: str) -> Path:
    root = _root()
    d = root / sid
    try:
        d.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="That path is not allowed.") from None
    return d


def _scan() -> list[dict]:
    root = _root()
    out: list[dict] = []
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        rel = Path(dirpath).relative_to(root)
        if len(rel.parts) > MAX_DEPTH:
            dirnames[:] = []
            continue
        if "SKILL.md" not in filenames or not rel.parts:
            continue
        f = Path(dirpath) / "SKILL.md"
        try:
            st = f.stat()
            text = f.read_text(encoding="utf-8", errors="ignore")[:4000] if st.st_size <= MAX_SKILL_BYTES else ""
        except OSError:
            continue
        fields, _ = _header(text)
        sid = rel.as_posix()
        out.append({"id": sid, "name": fields.get("name") or rel.parts[-1], "description": fields.get("description", ""),
                    "category": rel.parts[0] if len(rel.parts) > 1 else "", "mtime": int(st.st_mtime), "size": st.st_size,
                    "writable": os.access(f, os.W_OK) and _writable_dir(Path(dirpath))})
        if len(out) >= MAX_SKILLS:
            break
    return out


@router.get("/list")
async def list_skills() -> dict:
    def run() -> dict:
        root = _root()
        return {"skills": _scan(), "exists": root.is_dir(), "writable": _writable_dir(root)}
    return await asyncio.to_thread(run)


@router.get("/skill")
async def get_skill(id: str) -> dict:
    sid = _clean_id(id)

    def read() -> dict:
        d = _dir_of(sid)
        f = d / "SKILL.md"
        try:
            if not f.is_file() or f.stat().st_size > MAX_SKILL_BYTES:
                raise HTTPException(status_code=404, detail="Skill not found, or too large to open here.")
            text = f.read_text(encoding="utf-8", errors="ignore")
            others = sorted(p.name for p in d.iterdir() if p.name != "SKILL.md" and not p.name.startswith("."))[:50]
            return {"id": sid, "body": text, "files": others, "mtime": int(f.stat().st_mtime),
                    "writable": os.access(f, os.W_OK) and _writable_dir(d)}
        except OSError:
            raise HTTPException(status_code=404, detail="Skill not found.") from None
    return await asyncio.to_thread(read)


class SkillBody(BaseModel):
    id: str = Field(max_length=200)
    body: str = Field(max_length=MAX_SKILL_BYTES)
    create: bool = False


@router.post("/skill")
async def save_skill(b: SkillBody) -> dict:
    sid = _clean_id(b.id)
    if len(b.body.encode("utf-8")) > MAX_SKILL_BYTES:
        raise HTTPException(status_code=413, detail="That skill is too long.")
    _check_body(b.body)

    def write() -> dict:
        root = _root()
        if not root.is_dir():
            raise HTTPException(status_code=404, detail="There is no skills folder on this install.")
        d = _dir_of(sid)
        f = d / "SKILL.md"
        exists = f.exists()
        if b.create and d.exists():
            raise HTTPException(status_code=409, detail="A skill with that id already exists.")
        if not b.create and not exists:
            raise HTTPException(status_code=404, detail="That skill no longer exists.")
        anchor = d if d.exists() else d.parent
        while not anchor.exists() and anchor != root:
            anchor = anchor.parent
        if not _writable_dir(anchor) or (exists and not os.access(f, os.W_OK)):
            raise HTTPException(status_code=403, detail=READ_ONLY)
        d.mkdir(parents=True, exist_ok=True)
        atomic_write_text(f, b.body)
        return {"id": sid, "mtime": int(f.stat().st_mtime)}
    return await asyncio.to_thread(write)


@router.delete("/skill")
async def delete_skill(id: str) -> dict:
    sid = _clean_id(id)

    def move() -> dict:
        root = _root()
        d = _dir_of(sid)
        if not (d / "SKILL.md").is_file():
            raise HTTPException(status_code=404, detail="That skill no longer exists.")
        if not _writable_dir(root) or not _writable_dir(d.parent):
            raise HTTPException(status_code=403, detail=READ_ONLY)
        trash = root / ".trash"
        trash.mkdir(exist_ok=True)
        dest = trash / (sid.replace("/", "__") + "-" + str(int(time.time())))
        os.replace(d, dest)
        return {"id": sid, "kept_in": dest.relative_to(root).as_posix()}
    return await asyncio.to_thread(move)
