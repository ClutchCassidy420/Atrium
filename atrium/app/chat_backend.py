"""Atrium chat backend, vendored from the Atrium Loom chat plugin and adapted to run inside the Atrium web app.

Adaptations (everything else is unchanged; see docs/FRAMEWORK.md):
  * the page address is /chat (it was /agent-chat in the dashboard): notification links, the phone-app manifest and its start page
  * the voice and approval-service addresses come from the environment (TTS_URL, ACTION_GATE_URL)
  * push messages go out through an outbound proxy when PUSH_PROXY is set (the app's network has no direct internet)
  * the push-notification watcher only runs when PUSH_ENABLED=1; the app's own routes
    decide which of these routes are mounted, and wrap all of them in the app's login and per-person chat ownership

Original description follows.

Atrium Loom: Chat (dashboard plugin backend).

Routes (mounted by the Hermes dashboard under /api/plugins/atrium-chat):
  GET  /status                     is the agent API reachable
  POST /send                       start one run inside a chat session (conversation memory comes from the session id)
  POST /doc/upload?name=&keep=     receive a document (PDF, Word, Excel, PowerPoint, big text/CSV, up to 50 MB) and return a preview
  POST /speak {text, voice?}       read a short piece of text aloud (audio/mpeg from the local Kokoro voice); GET/POST /voice picks the voice
  GET  /stream/{run_id}            server-sent events for that run (words as they are written, tool activity, the end)
  GET  /runs/{run_id}              run state (used to find approval requests)
  POST /runs/{run_id}/stop         stop a run
  POST /runs/{run_id}/approval     approve once / deny a pending request
  GET  /manifest.webmanifest       the app manifest (so the page can be installed as an app)
  GET  /sw.js                      the service worker (shows notifications; scope "/")
  GET  /icon-{192,512,maskable}.png  the app icon (drawn in code)
  GET  /push/key, POST /push/subscribe, /push/unsubscribe, /push/test, GET /push/status
  GET  /actions, POST /actions/{id}/decide   the agent's proposals (such as an email to send) waiting for your approval
                                   Web Push: tells your phone or browser when a run finishes or needs approval

Design rules, same as the Mission Control plugin:
  * The agent API key stays on the server (read from API_SERVER_KEY, environment or the Hermes .env file).
  * Only the agent API on this host is ever contacted.
  * A chat is just a Hermes session whose id starts with "atrium-chat-". The browser lists and reloads past chats
    through the dashboard's own /api/sessions routes, so nothing is stored by this plugin.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
try:
    import fcntl          # Linux only: locks the push store. Absent on Windows (development machines), where push is not used.
except ImportError:
    fcntl = None
import json
import math
import os
import re
import struct
import threading
import time
import zlib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

import chat_model

# --- the agent's name --------------------------------------------------------------------------------------------
# Text in this file says {{agent}} wherever it means the agent's name. It is filled in on the way out (JSON, errors,
# streams, files) from <data>/atrium/agent.json, which the Settings tab writes. Without it the name is "Assistant".

_AGENT_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9 ._'-]{0,39}$")
_agent_cache: dict = {"t": 0.0, "v": "Assistant"}


def _agent_name() -> str:
    import json as _json
    import os as _os
    import time as _time
    from pathlib import Path as _Path
    if _time.time() - _agent_cache["t"] < 5:
        return _agent_cache["v"]
    name = "Assistant"
    try:
        home = _Path(_os.environ.get("HERMES_HOME") or "/opt/data")
        value = _json.loads((home / "atrium" / "agent.json").read_text(encoding="utf-8")).get("name")
        if isinstance(value, str) and _AGENT_NAME_RE.match(value.strip()):
            name = value.strip()
    except (OSError, ValueError, AttributeError):
        pass
    _agent_cache.update({"t": _time.time(), "v": name})
    return name


def _T(text):
    return text.replace("{{agent}}", _agent_name()) if isinstance(text, str) and "{{agent}}" in text else text


def _fill(resp):
    body = getattr(resp, "body", None)
    if isinstance(body, (bytes, bytearray)) and b"{{agent}}" in body:
        resp.body = bytes(body).replace(b"{{agent}}", _agent_name().encode("utf-8"))
        resp.headers["content-length"] = str(len(resp.body))
    elif hasattr(resp, "body_iterator"):
        inner = resp.body_iterator

        async def _gen():
            async for chunk in inner:
                if isinstance(chunk, (bytes, bytearray)):
                    yield bytes(chunk).replace(b"{{agent}}", _agent_name().encode("utf-8"))
                elif isinstance(chunk, str):
                    yield _T(chunk)
                else:
                    yield chunk
        resp.body_iterator = _gen()
    return resp


from fastapi.routing import APIRoute as _APIRoute  # noqa: E402


class _AgentRoute(_APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                resp = await original(request)
            except HTTPException as exc:
                detail = _T(exc.detail) if isinstance(exc.detail, str) else exc.detail
                raise HTTPException(status_code=exc.status_code, detail=detail, headers=exc.headers) from None
            return _fill(resp)
        return handler


router = APIRouter(route_class=_AgentRoute)

MAX_MESSAGE_CHARS = 60000
PUSH_ENABLED = os.environ.get("PUSH_ENABLED") == "1"      # the run watcher below only exists to send push notifications
PUSH_PROXY = os.environ.get("PUSH_PROXY") or None          # an outbound proxy for push messages (the app has no direct internet)
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 6 * 1024 * 1024      # per image, after decoding (the page shrinks images well below this)
_IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
_DATA_URL_RE = re.compile(r"^data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=]+)$")
UPSTREAM_TIMEOUT_S = 15
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")
_SESSION_RE = re.compile(r"^atrium-chat-[a-z0-9]{6,40}$")


# --- configuration (server side only) ----------------------------------------

def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or "/opt/data")


def _dotenv_value(name: str) -> Optional[str]:
    """Read one value from the Hermes .env file without exporting it anywhere."""
    try:
        for line in (_hermes_home() / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip('"').strip("'") or None
    except OSError:
        return None
    return None


def _api_key() -> Optional[str]:
    return os.environ.get("API_SERVER_KEY") or _dotenv_value("API_SERVER_KEY")


def _api_base() -> str:
    host = os.environ.get("API_SERVER_HOST") or _dotenv_value("API_SERVER_HOST") or "127.0.0.1"
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port = os.environ.get("API_SERVER_PORT") or _dotenv_value("API_SERVER_PORT") or "8642"
    if not re.fullmatch(r"\d{2,5}", str(port)):
        port = "8642"
    return f"http://{host}:{port}"


# --- upstream calls ----------------------------------------------------------

def _call_sync(method: str, path: str, payload: Optional[dict] = None) -> tuple[int, dict]:
    key = _api_key()
    if not key:
        raise HTTPException(status_code=503, detail="The agent API key is not configured.")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        _api_base() + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT_S) as resp:
            body = resp.read(2_000_000)
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        detail = "The agent API refused the request."
        try:
            parsed = json.loads(exc.read(20_000) or b"{}")
            err = parsed.get("error") if isinstance(parsed, dict) else None
            if isinstance(err, dict):
                err = err.get("message")
            if isinstance(err, str) and err:
                detail = err[:200]
        except Exception:
            pass
        code = exc.code if exc.code in (400, 404, 409, 429) else 502
        raise HTTPException(status_code=code, detail=detail) from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Could not reach the agent API.") from None


async def _call(method: str, path: str, payload: Optional[dict] = None) -> tuple[int, dict]:
    return await asyncio.to_thread(_call_sync, method, path, payload)


def _check_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.fullmatch(run_id or ""):
        raise HTTPException(status_code=400, detail="Invalid run id.")
    return run_id


def _public_run(run: dict) -> dict:
    """Only the fields the page needs."""
    out = {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "session_id": run.get("session_id"),
        "output": run.get("output") if isinstance(run.get("output"), str) else None,
        "execution_generation": run.get("execution_generation"),
        "pending_controls": run.get("pending_controls") if isinstance(run.get("pending_controls"), list) else [],
    }
    err = run.get("error")
    if isinstance(err, dict):
        err = err.get("message")
    out["error"] = err[:400] if isinstance(err, str) else None
    return out


# --- models ------------------------------------------------------------------

class SendBody(BaseModel):
    session_id: str = Field(min_length=10, max_length=60)
    message: str = Field(default="", max_length=MAX_MESSAGE_CHARS)
    images: list[str] = Field(default_factory=list, max_length=MAX_IMAGES)


class ApprovalBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=200)
    execution_generation: Optional[int] = Field(default=None, ge=0)
    choice: Literal["once", "deny"]


# --- routes ------------------------------------------------------------------

@router.get("/status")
async def status() -> dict:
    ready = False
    if _api_key():
        try:
            await _call("GET", "/health")
            ready = True
        except HTTPException:
            ready = False
    return {"ready": ready, "max_message_chars": MAX_MESSAGE_CHARS}


@router.post("/send")
async def send(body: SendBody, request: Request) -> dict:
    if not _SESSION_RE.fullmatch(body.session_id):
        raise HTTPException(status_code=400, detail="Invalid chat id.")
    message = body.message.strip()
    parts: list[dict] = []
    for url in body.images:
        m = _DATA_URL_RE.match(url or "")
        if not m:
            raise HTTPException(status_code=400, detail="That image type is not supported. Use PNG, JPEG, WebP or GIF.")
        try:
            raw = base64.b64decode(m.group(2), validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="An attached image is damaged.") from None
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="An attached image is too large.")
        parts.append({"type": "image_url", "image_url": {"url": url}})
    if not message and not parts:
        raise HTTPException(status_code=400, detail="Type a message first.")
    if parts:
        text = message or "Please look at the attached image and tell me what you see."
        run_input: Any = [{"role": "user", "content": [{"type": "text", "text": text}] + parts}]
    else:
        run_input = message
    payload: dict[str, Any] = {"input": run_input, "session_id": body.session_id}
    payload.update(chat_model.override(request.state.who["user"], _engine_now()))
    _, run = await _call("POST", "/v1/runs", payload)
    run_id = run.get("run_id")
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise HTTPException(status_code=502, detail="The agent API returned an unexpected answer.")
    if PUSH_ENABLED:
        _spawn(_watch_run(run_id, message or "(a picture)"))
    return {"run_id": run_id, "status": run.get("status", "started")}


@router.get("/image")
async def image(path: str) -> FileResponse:
    """Serve an image the agent cached for a chat message (so reloaded chats can show it).

    Only files inside the agent's own native-inputs cache folder with an image extension are served."""
    root = (_hermes_home() / "cache" / "documents" / "native-inputs").resolve()
    try:
        target = Path(path).resolve()
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(status_code=404, detail="Not found.") from None
    if root not in target.parents or target.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        if not target.is_file() or target.stat().st_size > 20 * 1024 * 1024:
            raise HTTPException(status_code=404, detail="Not found.")
    except OSError:
        raise HTTPException(status_code=404, detail="Not found.") from None
    ext = target.suffix.lower()
    media = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/" + ext.lstrip(".")
    return FileResponse(str(target), media_type=media, headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


# --- documents in chat -----------------------------------------------------------------------------------------------
# A document (PDF, Word, Excel, PowerPoint, or a big text/CSV file) is uploaded here first. The page then sends the agent a
# short note (name, place, size, pages) and a text preview; she reads the rest with her own tools. The extraction only
# reads text: no macros, no OCR, nothing inside the document is ever run.

MAX_DOC_BYTES = 50 * 1024 * 1024
MAX_UNPACKED_BYTES = 400 * 1024 * 1024      # zip-bomb guard for Word / Excel / PowerPoint
PREVIEW_CHARS = 1500
_DOC_EXTS = {".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".tsv", ".txt", ".md", ".json", ".log"}


def _doc_dirs() -> tuple[Path, Path]:
    base = _hermes_home() / "atrium"
    return base / "uploads", base / "kept-docs"


def _scrub(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _safe_name(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = re.sub(r"[^\w.\- ()&,]", "_", name)[:120].strip(" .")
    return name or "document"


def _check_zip(path: Path) -> None:
    import zipfile
    with zipfile.ZipFile(path) as z:
        total = sum(i.file_size for i in z.infolist())
        if total > MAX_UNPACKED_BYTES or len(z.infolist()) > 20000:
            raise ValueError("That file unpacks to something far too big to be a normal document.")
        if any(i.filename.lower().endswith("vbaproject.bin") for i in z.infolist()):
            raise ValueError("That file contains macros. Save a copy without macros and attach that.")


def _extract(path: Path, ext: str) -> dict:
    """Return {"kind", "pages", "preview"}; raises ValueError with a plain message when the file is not usable."""
    head = path.read_bytes()[:8] if path.stat().st_size else b""
    if ext == ".pdf":
        if not head.startswith(b"%PDF"):
            raise ValueError("That does not look like a real PDF.")
        from pypdf import PdfReader
        rd = PdfReader(str(path))
        if rd.is_encrypted:
            raise ValueError("That PDF is password protected. Remove the password and try again.")
        n = len(rd.pages)
        out = ""
        for page in rd.pages[:6]:
            out += (page.extract_text() or "") + "\n\n"
            if len(out) > PREVIEW_CHARS * 2:
                break
        return {"kind": "PDF", "pages": n, "preview": _scrub(out)[:PREVIEW_CHARS],
                "scanned": not _scrub(out) and n > 0}
    if ext in (".docx", ".xlsx", ".pptx"):
        if not head.startswith(b"PK"):
            raise ValueError("That does not look like a real %s file." % ext.lstrip("."))
        _check_zip(path)
        if ext == ".docx":
            import docx
            d = docx.Document(str(path))
            paras = [p.text for p in d.paragraphs if p.text.strip()]
            text = "\n".join(paras)
            return {"kind": "Word document", "pages": None, "preview": _scrub(text)[:PREVIEW_CHARS],
                    "extra": "%d paragraphs, %d tables" % (len(paras), len(d.tables))}
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            try:
                names = wb.sheetnames
                lines = []
                ws = wb[names[0]] if names else None
                if ws is not None:
                    for i, row in enumerate(ws.iter_rows(values_only=True)):
                        if i >= 12:
                            break
                        lines.append(" | ".join("" if c is None else str(c) for c in row[:12]))
                return {"kind": "Excel workbook", "pages": None, "preview": _scrub("\n".join(lines))[:PREVIEW_CHARS],
                        "extra": "sheets: " + ", ".join(names[:10]) + ("…" if len(names) > 10 else "")}
            finally:
                wb.close()
        from pptx import Presentation
        pr = Presentation(str(path))
        lines = []
        for idx, slide in enumerate(pr.slides, 1):
            if idx > 8:
                break
            bits = [sh.text_frame.text.strip() for sh in slide.shapes if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip()]
            if bits:
                lines.append("Slide %d: %s" % (idx, " / ".join(bits)[:300]))
        return {"kind": "PowerPoint deck", "pages": len(pr.slides), "preview": _scrub("\n".join(lines))[:PREVIEW_CHARS]}
    # plain text family
    raw = path.read_bytes()[:200_000]
    if b"\x00" in raw:
        raise ValueError("That looks like a binary file, not text.")
    text = raw.decode("utf-8", errors="replace")
    return {"kind": "text file", "pages": None, "preview": _scrub(text)[:PREVIEW_CHARS],
            "extra": "%d lines in the part read" % (text.count("\n") + 1)}


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
    st = chat_model.selection(request.state.who["user"]) or await asyncio.to_thread(_engine_now)
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


@router.get("/stream/{run_id}")
async def stream(run_id: str) -> StreamingResponse:
    run_id = _check_run_id(run_id)
    key = _api_key()
    if not key:
        raise HTTPException(status_code=503, detail="The agent API key is not configured.")
    try:
        import httpx
    except Exception:
        raise HTTPException(status_code=501, detail="Streaming is not available on this install.") from None

    url = _api_base() + "/v1/runs/" + run_id + "/events"

    async def gen() -> AsyncIterator[bytes]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None)) as client:
                async with client.stream(
                    "GET", url, headers={"Authorization": "Bearer " + key, "Accept": "text/event-stream"}
                ) as resp:
                    if resp.status_code != 200:
                        err = json.dumps({"event": "stream.error", "status": resp.status_code})
                        yield ("data: " + err + "\n\n").encode("utf-8")
                        return
                    async for chunk in resp.aiter_raw():
                        if chunk:
                            yield chunk
        except Exception:
            err = json.dumps({"event": "stream.error", "status": 0})
            yield ("data: " + err + "\n\n").encode("utf-8")

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    _, run = await _call("GET", "/v1/runs/" + _check_run_id(run_id))
    return _public_run(run)


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str) -> dict:
    _, res = await _call("POST", "/v1/runs/" + _check_run_id(run_id) + "/stop", {})
    return {"ok": True, "status": res.get("status")}


@router.post("/runs/{run_id}/approval")
async def approve_run(run_id: str, body: ApprovalBody) -> dict:
    payload: dict[str, Any] = {"request_id": body.request_id, "choice": body.choice}
    if body.execution_generation is not None:
        payload["execution_generation"] = body.execution_generation
    _, res = await _call("POST", "/v1/runs/" + _check_run_id(run_id) + "/approval", payload)
    return {"ok": True, "status": res.get("status")}


# --- Web Push: the app manifest, service worker, icon and notifications -------
#
# Pushes are encrypted for the one device that subscribed (RFC 8291) and signed with this server's own VAPID key
# (RFC 8292); the push service (Google, Apple or Mozilla) only relays them. Keys and subscriptions live in
# <HERMES_HOME>/atrium/push.json (private to the agent's user). Nothing else leaves this host.

PUSH_SUBJECT = "mailto:atrium-push@example.org"      # VAPID contact; push services want a mailto: or https: URL
MAX_SUBSCRIPTIONS = 10
_ENDPOINT_RE = re.compile(r"^https://[A-Za-z0-9.\-]{3,120}(?::\d{2,5})?/[\x21-\x7e]{1,1500}$")
_B64U_RE = re.compile(r"^[A-Za-z0-9_\-]{8,200}$")
_tlock = threading.Lock()


@contextlib.contextmanager
def _locked():
    """One writer at a time across threads and processes (dashboard, host watcher, the agent's notify command)."""
    path = _push_path().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with _tlock:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                if fcntl:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
_tasks: set = set()
_icons: dict[str, bytes] = {}


def _spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _push_path() -> Path:
    return _hermes_home() / "atrium" / "push.json"


def _push_load() -> dict:
    try:
        data = json.loads(_push_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _push_save(data: dict) -> None:
    path = _push_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    try:
        os.chmod(str(tmp), 0o660)  # the agent's host side shares this file through the group
    except OSError:
        pass
    os.replace(str(tmp), str(path))


def _push_state() -> dict:
    """Load the state, creating the VAPID key pair the first time."""
    data = _push_load()
    if not data.get("private_pem") or not data.get("public_key"):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        data["private_pem"] = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode("ascii")
        data["public_key"] = _b64u(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        data.setdefault("subscriptions", [])
        _push_save(data)
    if not isinstance(data.get("subscriptions"), list):
        data["subscriptions"] = []
    return data


def _vapid_header(endpoint: str, data: dict) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    origin = "/".join(endpoint.split("/")[:3])
    head = _b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = _b64u(json.dumps({"aud": origin, "exp": int(time.time()) + 12 * 3600, "sub": PUSH_SUBJECT}, separators=(",", ":")).encode())
    signing_input = (head + "." + claims).encode("ascii")
    key = serialization.load_pem_private_key(data["private_pem"].encode("ascii"), password=None)
    r, s_ = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    jwt = head + "." + claims + "." + _b64u(r.to_bytes(32, "big") + s_.to_bytes(32, "big"))
    return "vapid t=" + jwt + ", k=" + data["public_key"]


def _encrypt(sub: dict, payload: bytes) -> bytes:
    """aes128gcm content encoding for Web Push (RFC 8188 / 8291)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    ua_pub = _b64u_decode(sub["keys"]["p256dh"])
    auth = _b64u_decode(sub["keys"]["auth"])
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    as_priv = ec.generate_private_key(ec.SECP256R1())
    as_pub = as_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = as_priv.exchange(ec.ECDH(), ua_key)
    prk = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    salt = os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(prk)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(prk)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack(">IB", 4096, len(as_pub)) + as_pub + ciphertext


def _send_one(client, sub: dict, data: dict, payload: bytes, urgency: str) -> int:
    body = _encrypt(sub, payload)
    headers = {
        "Authorization": _vapid_header(sub["endpoint"], data),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": "3600",
        "Urgency": urgency,
    }
    return client.post(sub["endpoint"], content=body, headers=headers).status_code


def notify_sync(title: str, text: str, kind: str = "note", urgency: str = "normal", force: bool = False, url: str = "/chat") -> dict:
    """Send one notification to every subscribed device, unless the owner's preferences say otherwise (a kind switched off,
    or quiet hours: then it is held and delivered later as one digest). Approvals, action results and tests always go
    through. Also the entry point for the host watcher and for the agent's own notify command (see notify.py)."""
    title, text = _T(title), _T(text)
    group = KIND_GROUP.get(kind, "agent")
    if group != "approvals" and kind != "test":
        prefs = load_prefs()
        if not prefs["groups"].get(group, True):
            return {"sent": 0, "failed": 0, "removed": 0, "skipped": "switched off"}
        if _in_quiet(prefs) and not (urgency == "high" and prefs["quiet"].get("allow_urgent", True)):
            _hold(title, text, kind)
            return {"sent": 0, "failed": 0, "removed": 0, "held": True}
    return _deliver(title, text, kind, urgency, force, url)


def _deliver(title: str, text: str, kind: str, urgency: str, force: bool, url: str) -> dict:
    import httpx
    with _locked():
        data = _push_state()
        subs = [x for x in data["subscriptions"] if isinstance(x, dict) and x.get("endpoint")]
        if not subs:
            return {"sent": 0, "failed": 0, "removed": 0}
        payload = json.dumps({"title": title[:80], "body": text[:260], "url": url if url.startswith("/") else "/chat", "tag": "agent-" + kind, "kind": kind, "force": force}).encode("utf-8")
        sent = failed = 0
        dead: list[str] = []
        with httpx.Client(timeout=15.0, proxy=PUSH_PROXY, trust_env=False) as client:
            for sub in subs:
                try:
                    code = _send_one(client, sub, data, payload, urgency)
                except Exception:
                    failed += 1
                    continue
                if code in (200, 201, 202):
                    sent += 1
                elif code in (404, 410):
                    dead.append(sub["endpoint"])
                else:
                    failed += 1
        if dead:
            data["subscriptions"] = [x for x in data["subscriptions"] if x.get("endpoint") not in dead]
            _push_save(data)
        return {"sent": sent, "failed": failed, "removed": len(dead)}


async def _notify(title: str, text: str, kind: str, urgency: str = "normal", force: bool = False) -> dict:
    return await asyncio.to_thread(notify_sync, title, text, kind, urgency, force)


# --- notification preferences: what may ping, and quiet hours ----------------------------------------------------
# <HERMES_HOME>/atrium/notify-prefs.json (edited on the Settings page); notifications held during quiet hours wait in
# <HERMES_HOME>/atrium/notify-held.json and go out as one digest when quiet hours end (the host watcher triggers that).

KIND_GROUP = {"approval": "approvals", "action": "approvals", "done": "chat", "error": "chat", "note": "agent", "digest": "agent",
              "alert": "services", "recovered": "services", "job": "jobs", "jobfail": "jobs", "kanban": "kanban", "test": "approvals"}
GROUPS = [
    {"id": "approvals", "label": "Approvals and results of what you approved", "note": "Always on: {{agent}} waiting for your tap, and what happened after.", "locked": True},
    {"id": "agent", "label": "{{agent}}'s own updates", "note": "Morning brief, Check-ins, mail pings and anything she tells you herself."},
    {"id": "services", "label": "Service alerts", "note": "A service going down or coming back."},
    {"id": "jobs", "label": "Scheduled jobs", "note": "A job finishing with something to show, or failing."},
    {"id": "kanban", "label": "Task board", "note": "A card done, blocked or ready for your review."},
    {"id": "chat", "label": "Chat replies", "note": "{{agent}} finishing or failing a message when the app is closed."},
]
DEFAULT_PREFS = {"groups": {g["id"]: True for g in GROUPS}, "quiet": {"enabled": False, "start": "22:00", "end": "07:00", "allow_urgent": True}}
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _prefs_path() -> Path:
    return _hermes_home() / "atrium" / "notify-prefs.json"


def _held_path() -> Path:
    return _hermes_home() / "atrium" / "notify-held.json"


def load_prefs() -> dict:
    prefs = json.loads(json.dumps(DEFAULT_PREFS))
    try:
        data = json.loads(_prefs_path().read_text(encoding="utf-8"))
        for g, v in (data.get("groups") or {}).items():
            if g in prefs["groups"] and isinstance(v, bool):
                prefs["groups"][g] = v
        q = data.get("quiet") or {}
        if isinstance(q.get("enabled"), bool):
            prefs["quiet"]["enabled"] = q["enabled"]
        if isinstance(q.get("allow_urgent"), bool):
            prefs["quiet"]["allow_urgent"] = q["allow_urgent"]
        for k in ("start", "end"):
            if isinstance(q.get(k), str) and _TIME_RE.match(q[k]):
                prefs["quiet"][k] = q[k]
    except (OSError, ValueError):
        pass
    prefs["groups"]["approvals"] = True
    return prefs


def _in_quiet(prefs: dict, now: Optional[float] = None) -> bool:
    q = prefs["quiet"]
    if not q["enabled"]:
        return False
    lt = time.localtime(now if now is not None else time.time())
    cur = lt.tm_hour * 60 + lt.tm_min
    a = int(q["start"][:2]) * 60 + int(q["start"][3:])
    b = int(q["end"][:2]) * 60 + int(q["end"][3:])
    if a == b:
        return False
    return (a <= cur < b) if a < b else (cur >= a or cur < b)


def _hold(title: str, text: str, kind: str) -> None:
    with _locked():
        try:
            held = json.loads(_held_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            held = []
        held.append({"t": int(time.time()), "title": title[:80], "text": text[:200], "kind": kind})
        held = held[-50:]
        path = _held_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(held, fh)
        os.replace(str(tmp), str(path))


def flush_held() -> dict:
    """Quiet hours are over: send what was held as one digest."""
    prefs = load_prefs()
    if _in_quiet(prefs):
        return {"sent": 0, "held": "still quiet"}
    with _locked():
        try:
            held = json.loads(_held_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            held = []
        if not held:
            return {"sent": 0, "held": 0}
        try:
            _held_path().unlink()
        except OSError:
            pass
    lines = ["%s: %s" % (h["title"], h["text"]) if h.get("text") else h["title"] for h in held[:6]]
    body = " | ".join(lines)[:230] + (" …" if len(held) > 6 else "")
    res = _deliver("While you were away (%d)" % len(held), body, "digest", "normal", False, "/chat")
    res["held"] = len(held)
    return res


def _preview(text: Optional[str]) -> str:
    t = re.sub(r"```.*?```", " (code) ", text or "", flags=re.S)
    t = re.sub(r"[*_`#>\[\]]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:140] + "…") if len(t) > 140 else t


async def _watch_run(run_id: str, asked: str = "") -> None:
    """Follow one run on the server so a notification goes out even when the page is closed."""
    seen: set[str] = set()
    started = time.time()
    ask = _preview(asked)
    ask = (ask[:70] + "…") if len(ask) > 70 else ask
    about = ("Re: " + ask) if ask else ""
    try:
        while time.time() - started < 2 * 3600:
            await asyncio.sleep(2.0)
            try:
                _, run = await _call("GET", "/v1/runs/" + run_id)
            except HTTPException as exc:
                if exc.status_code == 404:
                    return
                continue
            status = run.get("status")
            pending = run.get("pending_controls") if isinstance(run.get("pending_controls"), list) else []
            for ctl in pending:
                key = json.dumps(ctl, sort_keys=True, default=str)[:300]
                if key not in seen:
                    seen.add(key)
                    await _notify("{{agent}} needs your approval", (about + "\n" if about else "") + "Open the chat to approve or deny.", "approval", "high")
            if status == "completed":
                out = _preview(run.get("output") if isinstance(run.get("output"), str) else "")
                await _notify("{{agent}} finished", (about + "\n" if about else "") + (out or "Her answer is ready."), "done")
                return
            if status == "failed":
                await _notify("{{agent}} hit an error", (about + "\n" if about else "") + "Open the chat to see what went wrong.", "error")
                return
            if status in ("cancelled", "interrupted"):
                return
    except Exception:
        return


# --- routes: install and notifications ---------------------------------------

SW_JS = r"""/* Atrium Loom: Chat service worker. Shows push notifications; caches nothing. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function () { /* pass through to the network */ });
self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {}; }
  e.waitUntil((async function () {
    var wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    // the page is open and visible: she is already showing it, no need to ping
    if (!d.force && wins.some(function (w) { return w.visibilityState === 'visible'; })) return;
    await self.registration.showNotification(d.title || '{{agent}}', {
      body: d.body || '',
      tag: d.tag || 'agent',
      renotify: true,
      silent: false,
      vibrate: d.kind === 'approval' ? [200, 100, 200, 100, 200] : [150, 80, 150],
      timestamp: Date.now(),
      requireInteraction: d.kind === 'approval',
      icon: '/api/plugins/atrium-chat/icon-192.png',
      badge: '/api/plugins/atrium-chat/icon-badge.png',
      data: { url: d.url || '/chat' }
    });
  })());
});
self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = new URL((e.notification.data && e.notification.data.url) || '/chat', self.location.origin).href;
  e.waitUntil((async function () {
    var wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (var i = 0; i < wins.length; i++) {
      var w = wins[i];
      try {
        if ('navigate' in w && w.url !== url) { var n = await w.navigate(url); if (n) w = n; }
        if ('focus' in w) { await w.focus(); return; }
      } catch (x) { /* fall through to a new window */ }
    }
    if (self.clients.openWindow) await self.clients.openWindow(url);
  })());
});
"""


@router.get("/sw.js")
async def service_worker() -> Response:
    return Response(SW_JS, media_type="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@router.get("/manifest.webmanifest")
async def manifest() -> JSONResponse:
    base = "/api/plugins/atrium-chat/"
    return JSONResponse({
        "id": "/chat",
        "name": "{{agent}}",
        "short_name": "{{agent}}",
        "description": "Chat with {{agent}}.",
        "start_url": "/chat",
        "scope": "/",
        "display": "standalone",
        "background_color": "#06061a",
        "theme_color": "#06061a",
        "icons": [
            {"src": base + "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": base + "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": base + "icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


def _draw_icon(size: int, scale: float) -> bytes:
    """The Watcher's great eye on a deep-space square, as a PNG (no image libraries needed)."""
    ss = 2
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            acc = [0.0, 0.0, 0.0]
            for sy in range(ss):
                for sx in range(ss):
                    u = ((x + (sx + 0.5) / ss) / size - 0.5) / scale
                    v = ((y + (sy + 0.5) / ss) / size - 0.5) / scale
                    r2 = u * u + v * v
                    g = math.exp(-r2 / 0.07)
                    col = [7 + 105 * 0.5 * g, 9 + 75 * 0.5 * g, 26 + 255 * 0.34 * g]
                    ax = abs(u)
                    lid = 0.21 * (1 - (ax / 0.40) ** 2) if ax < 0.40 else -1.0
                    if lid > 0:
                        d = lid - abs(v)
                        if d > 0.012:
                            col = [10, 12, 30]
                            ir = math.hypot(u, v * 1.0)
                            if ir < 0.15:
                                t = ir / 0.15
                                col = [255 * (1 - 0.15 * t) , 130 + 110 * (1 - t), 195 + 60 * (1 - t)]
                                if abs(u) < 0.022 and abs(v) < 0.125:
                                    col = [6, 4, 14]
                        elif d > -0.01:
                            col = [175, 140, 255]
                    elif lid > -1.0 and abs(v) < 0.012 + 0.0 and ax < 0.42:
                        col = [150, 120, 255]
                    # a small pink bow on top
                    bx, by = u - 0.0, v + 0.26
                    if abs(by) < 0.07 and ((abs(bx) - 0.03) / 0.10) ** 2 + (by / 0.065) ** 2 < 1 and abs(bx) > 0.012:
                        col = [255, 111, 181]
                    elif bx * bx + by * by < 0.0007:
                        col = [214, 70, 140]
                    for i in range(3):
                        acc[i] += col[i]
            for i in range(3):
                rows.append(max(0, min(255, int(acc[i] / (ss * ss)))))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")


def _draw_badge(size: int = 96) -> bytes:
    """A white eye on a transparent background: the status-bar badge Android expects."""
    ss = 3
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            cov = 0
            for sy in range(ss):
                for sx in range(ss):
                    u = ((x + (sx + 0.5) / ss) / size - 0.5) / 0.92
                    v = ((y + (sy + 0.5) / ss) / size - 0.5) / 0.92
                    ax = abs(u)
                    lid = 0.24 * (1 - (ax / 0.46) ** 2) if ax < 0.46 else -1.0
                    inside = False
                    if lid > 0:
                        d = lid - abs(v)
                        ring = 0 < d < 0.06
                        iris = math.hypot(u, v) < 0.17 and not (abs(u) < 0.028 and abs(v) < 0.14)
                        inside = ring or iris
                    if inside:
                        cov += 1
            rows += bytes((255, 255, 255, int(255 * cov / (ss * ss))))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")


@router.get("/icon-{name}.png")
async def icon(name: str) -> Response:
    spec = {"192": (192, 1.0), "512": (512, 1.0), "maskable": (512, 0.72), "badge": (96, 1.0)}.get(name)
    if not spec:
        raise HTTPException(status_code=404, detail="Not found.")
    if name not in _icons:
        _icons[name] = await asyncio.to_thread(_draw_badge if name == "badge" else _draw_icon, *((spec[0],) if name == "badge" else spec))
    return Response(_icons[name], media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


class SubscribeBody(BaseModel):
    subscription: dict
    label: str = Field(default="", max_length=80)


class UnsubscribeBody(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1700)


@router.get("/push/key")
async def push_key() -> dict:
    data = await asyncio.to_thread(_push_state)
    return {"public_key": data["public_key"]}


@router.get("/push/status")
async def push_status() -> dict:
    data = await asyncio.to_thread(_push_state)
    return {"devices": len(data["subscriptions"])}


@router.post("/push/subscribe")
async def push_subscribe(body: SubscribeBody) -> dict:
    sub = body.subscription
    endpoint = sub.get("endpoint") if isinstance(sub, dict) else None
    keys = sub.get("keys") if isinstance(sub, dict) else None
    if not isinstance(endpoint, str) or not _ENDPOINT_RE.fullmatch(endpoint) or not isinstance(keys, dict):
        raise HTTPException(status_code=400, detail="That subscription is not valid.")
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (isinstance(p256dh, str) and isinstance(auth, str) and _B64U_RE.fullmatch(p256dh) and _B64U_RE.fullmatch(auth)):
        raise HTTPException(status_code=400, detail="That subscription is not valid.")
    label = body.label.strip()[:80]

    def add() -> int:
        with _locked():
            data = _push_state()
            subs = [x for x in data["subscriptions"] if x.get("endpoint") != endpoint]
            subs.append({"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}, "label": label, "added": int(time.time())})
            data["subscriptions"] = subs[-MAX_SUBSCRIPTIONS:]
            _push_save(data)
            return len(data["subscriptions"])

    return {"ok": True, "devices": await asyncio.to_thread(add)}


@router.post("/push/unsubscribe")
async def push_unsubscribe(body: UnsubscribeBody) -> dict:
    def remove() -> int:
        with _locked():
            data = _push_state()
            data["subscriptions"] = [x for x in data["subscriptions"] if x.get("endpoint") != body.endpoint]
            _push_save(data)
            return len(data["subscriptions"])

    return {"ok": True, "devices": await asyncio.to_thread(remove)}


@router.post("/push/test")
async def push_test() -> dict:
    res = await _notify("{{agent}}", "Notifications are working.", "test", "normal", True)
    return {"ok": res["sent"] > 0, **res}


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


# --- the agent's name (read by every Atrium plugin and the host helpers) -----------------------------------------

class AgentBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)


@router.get("/agent")
async def agent_info() -> dict:
    return {"name": _agent_name(), "default": "Assistant"}


@router.post("/agent")
async def agent_set(body: AgentBody) -> dict:
    name = " ".join(body.name.split())
    if not _AGENT_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Use letters, numbers, spaces, dots, apostrophes or hyphens (up to 40 characters).")
    f = _hermes_home() / "atrium" / "agent.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"name": name}), encoding="utf-8")
    os.replace(tmp, f)
    _agent_cache["t"] = 0.0
    return {"name": name}


# --- voice: The agent reads replies aloud --------------------------------------------------------------------------------
# The words go to Kokoro (the text-to-speech server on this host, internal network only) and the audio comes straight
# back; nothing leaves the server, whichever engine wrote the reply. The page cuts a reply into short pieces so playback
# starts after the first sentence.

KOKORO_URL = os.environ.get("TTS_URL") or "http://atrium-kokoro:8880/v1/audio/speech"
VOICE_CHOICES = [
    ("af_heart", "Heart (warm, the default)"), ("af_bella", "Bella"), ("af_nicole", "Nicole (soft)"), ("af_sarah", "Sarah"),
    ("af_sky", "Sky"), ("bf_emma", "Emma (British)"), ("bf_isabella", "Isabella (British)"),
]
_VOICE_IDS = {v for v, _ in VOICE_CHOICES}
_speak_gate = asyncio.Semaphore(1)                  # one voice request at a time: the graphics card is shared


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=700)
    voice: Optional[str] = Field(default=None, max_length=40)


class VoiceBody(BaseModel):
    voice: str = Field(max_length=40)


def _voice_file() -> Path:
    return _hermes_home() / "atrium" / "voice.json"


def _voice_now() -> str:
    try:
        v = json.loads(_voice_file().read_text(encoding="utf-8")).get("voice")
        if v in _VOICE_IDS:
            return v
    except (OSError, ValueError, AttributeError):
        pass
    return "af_heart"


@router.get("/voice")
async def voice_info() -> dict:
    return {"voice": _voice_now(), "choices": [{"id": v, "label": n} for v, n in VOICE_CHOICES]}


@router.post("/voice")
async def voice_set(body: VoiceBody) -> dict:
    if body.voice not in _VOICE_IDS:
        raise HTTPException(status_code=400, detail="That voice is not available.")
    f = _voice_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"voice": body.voice}), encoding="utf-8")
    os.replace(tmp, f)
    return {"voice": body.voice}


def _synth_sync(text: str, voice: str) -> bytes:
    payload = json.dumps({"model": "kokoro", "input": text, "voice": voice, "response_format": "mp3", "speed": 1.0}).encode("utf-8")
    req = urllib.request.Request(KOKORO_URL, data=payload, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read(8_000_000)


@router.post("/speak")
async def speak(body: SpeakBody) -> Response:
    voice = body.voice if body.voice in _VOICE_IDS else _voice_now()
    text = re.sub(r"\s+", " ", body.text).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Nothing to read.")
    async with _speak_gate:
        try:
            audio = await asyncio.to_thread(_synth_sync, text, voice)
        except Exception:
            raise HTTPException(status_code=502, detail="The voice is not available right now.") from None
    if not audio:
        raise HTTPException(status_code=502, detail="The voice returned nothing.")
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


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


# --- engine (read side): which model answers chats started here -------------------------------------------------
# The switch itself lives in the Mission Control plugin; both read <HERMES_HOME>/atrium/engine.json.

_CLAUDE_IDS = {"claude-sonnet-5-5": "Claude Sonnet 5.5", "claude-opus-5-5": "Claude Opus 5.5", "claude-haiku-5-5": "Claude Haiku 5.5", "claude-fable-5-2": "Claude Fable 5.2"}


def _engine_now() -> dict:
    try:
        data = json.loads((_hermes_home() / "atrium" / "engine.json").read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("engine") == "claude" and data.get("model") in _CLAUDE_IDS:
            until = data.get("until")
            if until and time.time() > float(until):
                return {"engine": "local"}
            return {"engine": "claude", "model": data["model"], "until": until}
    except (OSError, ValueError):
        pass
    return {"engine": "local"}


def _engine_override() -> dict:
    st = _engine_now()
    return {"model": st["model"], "provider": "anthropic"} if st["engine"] == "claude" else {}


@router.get("/engine")
async def engine_info(request: Request) -> dict:
    st = chat_model.selection(request.state.who["user"]) or await asyncio.to_thread(_engine_now)
    if st.get("engine") == "cloud":
        return {"engine": "claude", "label": st.get("model", ""), "seconds_left": None}
    left = max(0, int(float(st["until"]) - time.time())) if st["engine"] == "claude" and st.get("until") else None
    return {"engine": st["engine"], "label": _CLAUDE_IDS.get(st.get("model", ""), ""), "seconds_left": left}


# --- settings routes: notification preferences and the devices that receive them ----------------------------------

class PrefsBody(BaseModel):
    groups: dict[str, bool] = Field(default_factory=dict)
    quiet: dict[str, Any] = Field(default_factory=dict)


@router.get("/settings/notify")
async def get_notify_settings() -> dict:
    prefs = await asyncio.to_thread(load_prefs)
    try:
        held = len(json.loads(_held_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        held = 0
    return {"prefs": prefs, "groups": GROUPS, "held": held, "quiet_now": _in_quiet(prefs)}


@router.post("/settings/notify")
async def set_notify_settings(body: PrefsBody) -> dict:
    prefs = await asyncio.to_thread(load_prefs)
    for g, v in body.groups.items():
        if g in prefs["groups"] and g != "approvals":
            prefs["groups"][g] = bool(v)
    q = body.quiet
    if "enabled" in q:
        prefs["quiet"]["enabled"] = bool(q["enabled"])
    if "allow_urgent" in q:
        prefs["quiet"]["allow_urgent"] = bool(q["allow_urgent"])
    for k in ("start", "end"):
        if k in q:
            if not (isinstance(q[k], str) and _TIME_RE.match(q[k])):
                raise HTTPException(status_code=400, detail="Times look like 22:00.")
            prefs["quiet"][k] = q[k]

    def write() -> None:
        path = _prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(prefs), encoding="utf-8")
        os.replace(str(tmp), str(path))
    await asyncio.to_thread(write)
    return {"prefs": prefs, "quiet_now": _in_quiet(prefs)}


def _device_id(endpoint: str) -> str:
    import hashlib
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:10]


@router.get("/push/devices")
async def push_devices() -> dict:
    data = await asyncio.to_thread(_push_state)
    out = []
    for sub in data["subscriptions"]:
        ep = sub.get("endpoint", "")
        out.append({"id": _device_id(ep), "label": sub.get("label") or "Device", "added": sub.get("added"), "service": ep.split("/")[2] if ep.count("/") >= 2 else ""})
    return {"devices": out}


class DeviceBody(BaseModel):
    id: str = Field(min_length=10, max_length=10)


@router.post("/push/devices/remove")
async def push_device_remove(body: DeviceBody) -> dict:
    def remove() -> int:
        with _locked():
            data = _push_state()
            data["subscriptions"] = [x for x in data["subscriptions"] if _device_id(x.get("endpoint", "")) != body.id]
            _push_save(data)
            return len(data["subscriptions"])
    return {"ok": True, "devices": await asyncio.to_thread(remove)}
