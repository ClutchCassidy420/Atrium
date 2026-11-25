"""Chat: documents (split out of chat_backend.py; the URLs are the same).

  POST /doc/upload?name=&keep=   receive a document and return a text preview
  POST /doc/move                 switch an upload between "just for this chat" (auto-removed) and "keep"
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import chat_backend as core
import chat_model
from agentkit import AgentRoute, hermes_home as _hermes_home
from docreader import extract as _extract

router = APIRouter(route_class=AgentRoute)

# --- documents in chat -----------------------------------------------------------------------------------------------
# A document (PDF, Word, Excel, PowerPoint, or a big text/CSV file) is uploaded here first. The page then sends the agent a
# short note (name, place, size, pages) and a text preview; she reads the rest with her own tools. The extraction only
# reads text: no macros, no OCR, nothing inside the document is ever run.

MAX_DOC_BYTES = 50 * 1024 * 1024
_DOC_EXTS = {".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".tsv", ".txt", ".md", ".json", ".log"}


def _doc_dirs() -> tuple[Path, Path]:
    base = _hermes_home() / "atrium"
    return base / "uploads", base / "kept-docs"


def _safe_name(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = re.sub(r"[^\w.\- ()&,]", "_", name)[:120].strip(" .")
    return name or "document"


def _purge_old_uploads(days: int = 30) -> int:
    """Delete chat-only uploads older than `days`. Only regular files directly inside the uploads folder are touched."""
    folder = _doc_dirs()[0]
    cutoff = time.time() - days * 86400
    removed = 0
    try:
        for p in folder.iterdir():
            try:
                if p.is_file() and not p.is_symlink() and p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed


@router.post("/doc/upload")
async def doc_upload(request: Request, name: str, keep: int = 0) -> dict:
    from urllib.parse import unquote
    clean = _safe_name(unquote(name))
    ext = Path(clean).suffix.lower()
    if ext not in _DOC_EXTS:
        raise HTTPException(status_code=400, detail="That file type is not supported. PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), CSV and text files work.")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_DOC_BYTES + 4096:
        raise HTTPException(status_code=413, detail="That file is over 50 MB.")
    folder = _doc_dirs()[1 if keep else 0]
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = folder / ("%s-%s-%s" % (stamp, os.urandom(3).hex(), clean))
    size = 0
    try:
        with open(target, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_DOC_BYTES:
                    raise HTTPException(status_code=413, detail="That file is over 50 MB.")
                fh.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="That file is empty.")
        try:
            info = await asyncio.to_thread(_extract, target, ext)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=400, detail="Could not read that file. It may be damaged.") from None
        os.chmod(target, 0o640)
    except BaseException:
        with contextlib.suppress(OSError):
            target.unlink()
        raise
    await asyncio.to_thread(_purge_old_uploads) if not keep else None
    st = chat_model.selection(request.state.who["user"]) or await asyncio.to_thread(core._engine_now)
    return {
        "name": clean, "path": str(target), "size": size, "kept": bool(keep),
        "kind": info.get("kind"), "pages": info.get("pages"), "extra": info.get("extra"),
        "preview": info.get("preview", ""), "scanned": bool(info.get("scanned")),
        "cloud": st.get("engine") in ("cloud", "claude"),
    }


class DocMoveBody(BaseModel):
    path: str = Field(min_length=5, max_length=400)
    keep: bool


@router.post("/doc/move")
async def doc_move(body: DocMoveBody) -> dict:
    """Switch an uploaded document between 'just for this chat' (auto-removed) and 'keep'. Only files this plugin stored."""
    chat_dir, kept_dir = (d.resolve() for d in _doc_dirs())
    try:
        src = Path(body.path).resolve()
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(status_code=404, detail="Not found.") from None
    here, there = (chat_dir, kept_dir) if body.keep else (kept_dir, chat_dir)
    if src.parent != here or not src.is_file() or src.is_symlink():
        raise HTTPException(status_code=404, detail="Not found.")
    there.mkdir(parents=True, exist_ok=True)
    dest = there / src.name
    if dest.exists():
        raise HTTPException(status_code=409, detail="Already there.")
    os.replace(src, dest)
    return {"path": str(dest), "kept": body.keep}
