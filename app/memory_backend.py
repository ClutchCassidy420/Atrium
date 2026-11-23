"""Atrium memory backend, vendored from the Atrium Loom plugin and adapted to run inside the Atrium web app.

Adaptations: none (it only reads the notes vault, which the app mounts read-only).

Original description follows.

Atrium Loom: Memory (dashboard plugin backend).

Reads the agent's Obsidian-compatible vault (plain markdown files) and serves it as a graph.

Routes (mounted by the Hermes dashboard under /api/plugins/atrium-memory):
  GET /graph          every note as a node, every [[wikilink]] as an edge, plus folders and recent notes
  GET /note?path=     one note's text with its links and backlinks
  GET /search?q=      notes whose title or text contain the words

Read-only: nothing here writes to the vault. The vault is <HERMES_HOME>/obsidian-vault.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from agentkit import (AgentRoute as _AgentRoute)

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
