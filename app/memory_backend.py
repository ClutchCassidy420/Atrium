"""Atrium memory backend, vendored from the Atrium Loom plugin and adapted to run inside the Atrium web app.

Adaptations: none (it only reads the notes vault, which the app mounts read-only).

Original description follows.

Atrium Loom: Memory (dashboard plugin backend).

Reads the agent's Obsidian-compatible vault (plain markdown files) and serves it as a graph.

Routes (mounted by the Hermes dashboard under /api/plugins/atrium-memory):
  GET /graph          every note as a node, every [[wikilink]] as an edge, plus folders and recent notes
  GET /note?path=     one note's text with its links and backlinks
  GET /search?q=      notes whose title or text contain the words

Editing (owners only, added in Atrium): the vault's notes can be opened, changed, added and removed from Settings, but only where the
folder is mounted writable (see deploy/compose.example.yaml); everywhere else the routes say so instead of failing.
  GET    /status          which folders can be written to
  POST   /note            create or replace one note   {path, body, create}
  DELETE /note?path=      move one note to that folder's .trash (Obsidian keeps one too); nothing is erased

The vault is <HERMES_HOME>/obsidian-vault.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Optional

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from agentkit import (AgentRoute as _AgentRoute, atomic_write_text)

# --- the agent's name --------------------------------------------------------------------------------------------
# Text in this file says {{agent}} wherever it means the agent's name. It is filled in on the way out (JSON, errors,
# streams, files) from <data>/atrium/agent.json, which the Settings tab writes. Without it the name is "Assistant".

router = APIRouter(route_class=_AgentRoute)

MAX_NOTES = 3000
MAX_NOTE_BYTES = 400_000
BODY_CHARS = 30_000
_WIKI = re.compile(r"\[\[([^\]\n]{1,200})\]\]")
_SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules"}


def _vault() -> Path:
    return Path(os.environ.get("HERMES_HOME") or "/opt/data") / "obsidian-vault"


# One parsed entry per note file, valid while the file's (modification time, size) stay the same. Every request still walks
# the vault and stats each file, so an edit shows up at once; only unchanged notes are not read and parsed again.
# The dict is replaced as a whole at the end of a scan, so scans running in parallel threads never see a half-built one.
_note_cache: dict[str, tuple[tuple[int, int], dict]] = {}


def _scan() -> list[dict]:
    global _note_cache
    root = _vault()
    notes: list[dict] = []
    if not root.is_dir():
        _note_cache = {}
        return notes
    previous, seen = _note_cache, {}
    try:
        return _scan_into(root, notes, previous, seen)
    finally:
        _note_cache = seen


def _scan_into(root: Path, notes: list[dict], previous: dict, seen: dict) -> list[dict]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if not fn.lower().endswith(".md"):
                continue
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            try:
                st = p.stat()
                if st.st_size > MAX_NOTE_BYTES:
                    continue
                stamp = (st.st_mtime_ns, st.st_size)
                hit = previous.get(str(root) + "/" + rel)
                if hit and hit[0] == stamp:
                    seen[str(root) + "/" + rel] = hit
                    notes.append(hit[1])
                    if len(notes) >= MAX_NOTES:
                        return notes
                    continue
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            first = ""
            title = Path(fn).stem
            for line in text.splitlines():
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#") and title == Path(fn).stem and not first:
                    title = s.lstrip("# ").strip() or title
                    continue
                if not s.startswith("#"):
                    first = re.sub(r"[*_`>\[\]]+", "", s)[:140]
                    break
            note = {"id": rel, "title": title[:100], "folder": rel.split("/")[0] if "/" in rel else "(top)", "size": st.st_size,
                    "mtime": int(st.st_mtime), "summary": first, "text": text}
            seen[str(root) + "/" + rel] = (stamp, note)
            notes.append(note)
            if len(notes) >= MAX_NOTES:
                return notes
    return notes


def _resolver(notes: list[dict]):
    by_path = {n["id"].lower(): n["id"] for n in notes}
    by_nopath = {n["id"][:-3].lower(): n["id"] for n in notes}
    by_base: dict[str, list[str]] = {}
    for n in notes:
        by_base.setdefault(Path(n["id"]).stem.lower(), []).append(n["id"])

    def resolve(target: str) -> Optional[str]:
        t = target.split("|")[0].split("#")[0].strip().lower()
        if not t:
            return None
        if t in by_path:
            return by_path[t]
        if t in by_nopath:
            return by_nopath[t]
        cands = by_base.get(Path(t).stem.lower(), [])
        return cands[0] if len(cands) == 1 else None
    return resolve


def _links(notes: list[dict]) -> dict[str, set[str]]:
    resolve = _resolver(notes)
    out: dict[str, set[str]] = {}
    for n in notes:
        targets = set()
        for m in _WIKI.finditer(n["text"]):
            r = resolve(m.group(1))
            if r and r != n["id"]:
                targets.add(r)
        out[n["id"]] = targets
    return out


@router.get("/graph")
async def graph() -> dict:
    def build() -> dict:
        notes = _scan()
        links = _links(notes)
        incoming: dict[str, int] = {}
        edges = []
        for src, targets in links.items():
            for t in targets:
                edges.append([src, t])
                incoming[t] = incoming.get(t, 0) + 1
        folders: dict[str, int] = {}
        for n in notes:
            folders[n["folder"]] = folders.get(n["folder"], 0) + 1
        nodes = [{"id": n["id"], "title": n["title"], "folder": n["folder"], "summary": n["summary"], "mtime": n["mtime"], "size": n["size"],
                  "out": len(links[n["id"]]), "in": incoming.get(n["id"], 0)} for n in notes]
        recent = sorted(nodes, key=lambda x: x["mtime"], reverse=True)[:8]
        return {"nodes": nodes, "edges": edges, "folders": [{"name": k, "count": v} for k, v in sorted(folders.items())],
                "recent": [r["id"] for r in recent], "truncated": len(notes) >= MAX_NOTES}
    return await asyncio.to_thread(build)


@router.get("/note")
async def note(path: str) -> dict:
    def read() -> dict:
        root = _vault().resolve()
        try:
            target = (root / path).resolve()
        except (OSError, ValueError):
            raise HTTPException(status_code=404, detail="Not found.") from None
        if root not in target.parents or target.suffix.lower() != ".md" or any(p.startswith(".") for p in target.relative_to(root).parts):
            raise HTTPException(status_code=404, detail="Not found.")
        try:
            if not target.is_file() or target.stat().st_size > MAX_NOTE_BYTES:
                raise HTTPException(status_code=404, detail="Not found.")
            text = target.read_text(encoding="utf-8", errors="ignore")
            mtime = int(target.stat().st_mtime)
        except OSError:
            raise HTTPException(status_code=404, detail="Not found.") from None
        notes = _scan()
        links = _links(notes)
        rel = target.relative_to(root).as_posix()
        titles = {n["id"]: n["title"] for n in notes}
        return {"id": rel, "title": titles.get(rel, Path(rel).stem), "mtime": mtime, "body": text[:BODY_CHARS], "cut": len(text) > BODY_CHARS,
                "editable": len(text) <= BODY_CHARS and _can_write_dir(target.parent),
                "links": [{"id": t, "title": titles.get(t, t)} for t in sorted(links.get(rel, set()))],
                "backlinks": [{"id": s, "title": titles.get(s, s)} for s, ts in links.items() if rel in ts]}
    return await asyncio.to_thread(read)


@router.get("/search")
async def search(q: str) -> dict:
    words = [w for w in re.split(r"\s+", q.lower().strip()) if w][:6]
    if not words or len(q) > 200:
        return {"results": []}

    def run() -> list[dict]:
        out = []
        for n in _scan():
            hay = (n["title"] + " " + n["id"] + " " + n["text"]).lower()
            if all(w in hay for w in words):
                body = n["text"]
                pos = body.lower().find(words[0])
                snip = re.sub(r"\s+", " ", body[max(0, pos - 40): pos + 120]) if pos >= 0 else n["summary"]
                out.append({"id": n["id"], "title": n["title"], "folder": n["folder"], "snippet": snip.strip()[:160]})
        return out[:30]
    return {"results": await asyncio.to_thread(run)}


# --- editing (owners only; the router is mounted behind the owner check) -----------------------------------------------------------

_NAME_OK = re.compile(r'^[^\x00-\x1f\\:*?"<>|]{1,120}$')
READ_ONLY = "That folder is read-only on this install, so notes in it cannot be changed. Ask whoever runs the server to mount it writable."


def _keep_agent_access(target: Path, old_mode: Optional[int]) -> None:
    """Saving replaces the file, so the new file belongs to the app. Keep it readable and writable by the folder's group (the agent's), as before."""
    try:
        os.chmod(target, ((old_mode if old_mode is not None else 0o664) | 0o060) & 0o777)
    except OSError:
        pass


def _can_write_dir(p: Path) -> bool:
    return p.is_dir() and os.access(p, os.W_OK | os.X_OK)


def _clean_rel(path: str) -> str:
    """A note path such as ``People/Ana.md``: no dots at the start of a part, no ``..``, at most four levels, always ending in .md."""
    p = (path or "").strip().replace("\\", "/").strip("/")
    if p and not p.lower().endswith(".md"):
        p += ".md"
    parts = p.split("/") if p else []
    if not parts or len(parts) > 4 or any((not x) or x.startswith(".") or not _NAME_OK.match(x) for x in parts):
        raise HTTPException(status_code=400, detail="Use a name like People/Ana (no dots at the start, no special characters, at most four folders deep).")
    return p


def _inside(root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _nearest_dir(p: Path, root: Path) -> Path:
    while not p.exists() and p != root and p.parent != p:
        p = p.parent
    return p


class NoteBody(BaseModel):
    path: str = Field(max_length=300)
    body: str = Field(max_length=MAX_NOTE_BYTES)
    create: bool = False


@router.get("/status")
async def status() -> dict:
    def run() -> dict:
        root = _vault()
        folders = []
        if root.is_dir():
            for d in sorted(root.iterdir()):
                if d.is_dir() and d.name not in _SKIP_DIRS and not d.name.startswith(".") and _can_write_dir(d):
                    folders.append(d.name)
        return {"exists": root.is_dir(), "root": _can_write_dir(root), "folders": folders}
    return await asyncio.to_thread(run)


@router.post("/note")
async def save_note(b: NoteBody) -> dict:
    rel = _clean_rel(b.path)
    if len(b.body.encode("utf-8")) > MAX_NOTE_BYTES:
        raise HTTPException(status_code=413, detail="That note is too long.")

    def write() -> dict:
        root = _vault()
        if not root.is_dir():
            raise HTTPException(status_code=404, detail="There is no notes folder on this install.")
        target = root / rel
        if not _inside(root, _nearest_dir(target.parent, root)):
            raise HTTPException(status_code=400, detail="That path is not allowed.")
        exists = target.exists()
        if b.create and exists:
            raise HTTPException(status_code=409, detail="A note with that name already exists.")
        if not b.create and not exists:
            raise HTTPException(status_code=404, detail="That note no longer exists.")
        if exists and not target.is_file():
            raise HTTPException(status_code=400, detail="That path is not a note.")
        if not _can_write_dir(_nearest_dir(target.parent, root)):       # replacing a file needs the folder to be writable, not the file
            raise HTTPException(status_code=403, detail=READ_ONLY)
        old_mode = target.stat().st_mode if exists else None
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, b.body)
        _keep_agent_access(target, old_mode)
        return {"id": rel, "mtime": int(target.stat().st_mtime)}
    return await asyncio.to_thread(write)


@router.delete("/note")
async def delete_note(path: str) -> dict:
    rel = _clean_rel(path)

    def move() -> dict:
        root = _vault()
        target = root / rel
        if not target.is_file() or not _inside(root, target):
            raise HTTPException(status_code=404, detail="That note no longer exists.")
        if not _can_write_dir(target.parent):
            raise HTTPException(status_code=403, detail=READ_ONLY)
        trash = target.parent / ".trash"
        trash.mkdir(exist_ok=True)
        dest = trash / target.name
        if dest.exists():
            dest = trash / (target.stem + "-" + str(int(time.time())) + target.suffix)
        os.replace(target, dest)
        return {"id": rel, "kept_in": dest.relative_to(root).as_posix()}
    return await asyncio.to_thread(move)
