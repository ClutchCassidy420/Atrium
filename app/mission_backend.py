"""Atrium mission backend, vendored from the Atrium Loom Mission Control plugin and adapted to run inside the Atrium web app.

Only three groups of its routes are mounted by the app (see pagesmount.py): today's focus, the engine switch and the soul editor.
The rest (status, ask, runs, health, radar, daylog) is replaced by the app's Home page.
Adaptations: the focus list is saved with an atomic replace (so it works whatever the permissions of the day's file);
the soul is read and saved through the Hermes dashboard's profile route, with the version history kept in <data>/atrium/soul-history.

Original description follows.

Atrium Loom: Mission Control (dashboard plugin backend).

A thin, locked-down proxy between the dashboard's "Ask" box and the Hermes Agent API server
running in the same container. It exists so the API key stays on the server: the browser only
ever talks to these routes (behind the dashboard's own login), never to the API server.

What it can do (and nothing else):
  GET  /status                      is the agent API reachable?
  POST /ask                         start one run (one at a time) in a fixed session
  GET  /runs/{run_id}               status, output, pending approvals of a run
  POST /runs/{run_id}/stop          ask a run to stop
  POST /runs/{run_id}/approval      answer a pending approval with "once" or "deny" only
  GET/POST /focus, /focus/toggle, /focus/remove   Today's Focus checklist inside today's vault Daily note
  GET  /radar                       latest Radar note (Radar/YYYY-MM-DD.md in the vault), parsed
  GET  /health                      up/down + latency for the services listed in <HERMES_HOME>/atrium-health.json

Safety notes:
  * The API key is read from the environment or the Hermes .env file and is never returned,
    logged, or placed in an error message.
  * Inputs are length-limited and validated; run ids must match a strict pattern.
  * Only one run started from this box can be active at a time.
  * The agent behind the API has its normal tools. Anything asked here carries the same
    authority as typing in the dashboard chat, and risky actions still go through approvals.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import datetime
import http.client
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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

SESSION_ID = "atrium-mission-control"
MAX_MESSAGE_CHARS = 6000
UPSTREAM_TIMEOUT_S = 15
MIN_SECONDS_BETWEEN_ASKS = 1.0
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")

_state: dict[str, Any] = {"last_run_id": None, "last_ask_at": 0.0}
_ask_lock = asyncio.Lock()


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


# --- upstream call -----------------------------------------------------------

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
    """Return only the fields the UI needs; never pass through anything unexpected."""
    out = {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "session_id": run.get("session_id"),
        "model": run.get("model"),
        "output": run.get("output") if isinstance(run.get("output"), str) else None,
        "usage": run.get("usage") if isinstance(run.get("usage"), dict) else None,
        "execution_generation": run.get("execution_generation"),
        "pending_controls": run.get("pending_controls") if isinstance(run.get("pending_controls"), list) else [],
    }
    err = run.get("error")
    if isinstance(err, dict):
        err = err.get("message")
    out["error"] = err[:400] if isinstance(err, str) else None
    return out


# --- adaptation: the dashboard connection, for the soul ------------------------------------------------------------
HERMES = None          # set by the app


def _dash(method: str, path: str, payload=None, timeout: float = 20.0):
    if HERMES is None or not getattr(HERMES, "dash_base", ""):
        raise HTTPException(status_code=503, detail="The agent dashboard is not connected.")
    status, body = HERMES._send(method, HERMES.dash_base + path, HERMES._dash_headers(), payload, timeout)
    try:
        return status, (json.loads(body) if body else None)
    except ValueError:
        return status, None


# --- models ------------------------------------------------------------------

class AskBody(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


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
    return {"ready": ready, "session_id": SESSION_ID, "max_message_chars": MAX_MESSAGE_CHARS}


@router.post("/ask")
async def ask(body: AskBody) -> dict:
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Type a question first.")
    async with _ask_lock:
        now = time.monotonic()
        if now - _state["last_ask_at"] < MIN_SECONDS_BETWEEN_ASKS:
            raise HTTPException(status_code=429, detail="Slow down a moment.")
        last = _state.get("last_run_id")
        if last:
            try:
                _, run = await _call("GET", "/v1/runs/" + last)
                if run.get("status") not in TERMINAL:
                    raise HTTPException(status_code=409, detail="{{agent}} is still working on your last question.")
            except HTTPException as exc:
                if exc.status_code == 409 and "still working" in str(exc.detail):
                    raise
                # last run unknown or gone: fine to start a new one
        payload = {"input": message, "session_id": SESSION_ID}
        payload.update(engine_override())
        _, run = await _call("POST", "/v1/runs", payload)
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise HTTPException(status_code=502, detail="The agent API returned an unexpected answer.")
        _state["last_run_id"] = run_id
        _state["last_ask_at"] = now
        return {"run_id": run_id, "status": run.get("status", "started")}


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


# ---------- service health ----------
# Targets come from <HERMES_HOME>/atrium-health.json, a list written by the owner (never by a request):
#   [{"label": "Nextcloud", "url": "https://cloud.example.com/status.php", "via": "172.20.0.30:443"}, ...]
# "via" is optional: connect to that host:port but still send the URL's host name (SNI + Host header),
# which lets the dashboard check sites through a reverse proxy it can reach. Only the HTTP status
# code and the timing are used; response bodies are discarded. "up" means any status below 500.

HEALTH_TIMEOUT = 4.0
MAX_TARGETS = 24


def _health_targets() -> list:
    try:
        data = json.loads((_hermes_home() / "atrium-health.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for t in data if isinstance(data, list) else []:
        if isinstance(t, dict) and isinstance(t.get("url"), str) and isinstance(t.get("label"), str):
            out.append(t)
    return out[:MAX_TARGETS]


def _probe_sync(t: dict) -> dict:
    label = t["label"][:40]
    start = time.monotonic()
    ok, code = False, None
    try:
        u = urllib.parse.urlsplit(t["url"])
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("bad url")
        port = u.port or (443 if u.scheme == "https" else 80)
        host, hport = u.hostname, port
        if isinstance(t.get("via"), str) and ":" in t["via"]:
            host, p = t["via"].rsplit(":", 1)
            hport = int(p)
        sock = socket.create_connection((host, hport), timeout=HEALTH_TIMEOUT)
        if u.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
        conn = http.client.HTTPConnection(u.hostname, port, timeout=HEALTH_TIMEOUT)
        conn.sock = sock
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        conn.request("GET", path, headers={"Host": u.netloc, "User-Agent": "atrium-health/1"})
        resp = conn.getresponse()
        code = resp.status
        resp.read(1)
        conn.close()
        ok = code < 500
    except Exception:
        ok = False
    return {"label": label, "group": str(t.get("group", ""))[:24], "ok": ok, "code": code,
            "ms": int((time.monotonic() - start) * 1000)}


@router.get("/health")
async def health() -> dict:
    targets = _health_targets()
    results = await asyncio.gather(*[asyncio.to_thread(_probe_sync, t) for t in targets])
    return {"configured": bool(targets), "services": list(results)}


# ---------- Today's Focus + Radar (both live in the Obsidian vault as plain Markdown) ----------
# The vault folder comes from OBSIDIAN_VAULT_PATH (environment or the Hermes .env). Focus items are
# "- [ ] text" lines under the "## Today's Focus" heading of Daily/YYYY-MM-DD.md, so Obsidian,
# the agent and this page all edit the same file. Radar items are read from Radar/YYYY-MM-DD.md.

FOCUS_HEADING = "## Today's Focus"
MAX_FOCUS_TEXT = 200
MAX_FOCUS_ITEMS = 40
_focus_lock = asyncio.Lock()
_TASK_RE = re.compile(r"^- \[( |x|X)\] (.*)$")


def _vault() -> Path:
    raw = os.environ.get("OBSIDIAN_VAULT_PATH") or _dotenv_value("OBSIDIAN_VAULT_PATH") or ""
    if not raw:
        raise HTTPException(status_code=503, detail="OBSIDIAN_VAULT_PATH is not set")
    root = Path(raw)
    if not root.is_dir():
        raise HTTPException(status_code=503, detail="vault folder not found")
    return root


def _today() -> str:
    return datetime.date.today().isoformat()


def _daily_path() -> Path:
    return _vault() / "Daily" / (_today() + ".md")


def _focus_split(text: str):
    """Return (lines, start, end): the line range of the focus section, or start=None if absent."""
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.strip() == FOCUS_HEADING), None)
    if start is None:
        return lines, None, None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("#"):
            end = j
            break
    return lines, start, end


def _focus_items(text: str) -> list:
    lines, start, end = _focus_split(text)
    if start is None:
        return []
    out = []
    for ln in lines[start + 1:end]:
        m = _TASK_RE.match(ln)
        if m:
            out.append({"i": len(out), "text": m.group(2), "done": m.group(1) != " "})
    return out


def _read_daily() -> str:
    try:
        return _daily_path().read_text(encoding="utf-8")
    except OSError:
        return ""


def _write_focus(items: list) -> None:
    path = _daily_path()
    text = _read_daily()
    block = [FOCUS_HEADING, ""] + ["- [%s] %s" % ("x" if it["done"] else " ", it["text"]) for it in items] + [""]
    lines, start, end = _focus_split(text)
    if start is None:
        base = text.rstrip("\n")
        if not base:
            base = "# %s\n\nDaily note." % _today()
        new = base + "\n\n" + "\n".join(block)
    else:
        new = "\n".join(lines[:start] + block + lines[end:])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")          # written beside it and swapped in, so the file's own permissions do not matter
    tmp.write_text(new.rstrip("\n") + "\n", encoding="utf-8")
    os.replace(tmp, path)


class FocusAdd(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_FOCUS_TEXT)


class FocusIndex(BaseModel):
    index: int = Field(ge=0, le=MAX_FOCUS_ITEMS)


@router.get("/focus")
async def focus_get() -> dict:
    _vault()
    return {"date": _today(), "items": _focus_items(_read_daily())}


@router.post("/focus")
async def focus_add(body: FocusAdd) -> dict:
    _vault()
    text = " ".join(body.text.split())
    if not text:
        raise HTTPException(status_code=400, detail="empty item")
    async with _focus_lock:
        items = _focus_items(_read_daily())
        if len(items) >= MAX_FOCUS_ITEMS:
            raise HTTPException(status_code=400, detail="focus list is full")
        items.append({"i": len(items), "text": text, "done": False})
        _write_focus(items)
    return {"date": _today(), "items": items}


@router.post("/focus/toggle")
async def focus_toggle(body: FocusIndex) -> dict:
    _vault()
    async with _focus_lock:
        items = _focus_items(_read_daily())
        if body.index >= len(items):
            raise HTTPException(status_code=404, detail="no such item")
        items[body.index]["done"] = not items[body.index]["done"]
        _write_focus(items)
    return {"date": _today(), "items": items}


@router.post("/focus/remove")
async def focus_remove(body: FocusIndex) -> dict:
    _vault()
    async with _focus_lock:
        items = _focus_items(_read_daily())
        if body.index >= len(items):
            raise HTTPException(status_code=404, detail="no such item")
        del items[body.index]
        for n, it in enumerate(items):
            it["i"] = n
        _write_focus(items)
    return {"date": _today(), "items": items}


# Radar note format (written by the agent), one item per line:
#   1. **Title** | Category | why it matters | https://source
_RADAR_RE = re.compile(r"^\s*(\d+)[.)]\s+\*\*(.+?)\*\*\s*\|\s*([^|]*)\|\s*([^|]*)(?:\|\s*(\S+))?\s*$")


_WIKI_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")


@router.get("/radar")
async def radar_get() -> dict:
    folder = _vault() / "Radar"
    try:
        files = sorted(p for p in folder.glob("????-??-??.md") if p.is_file())
    except OSError:
        files = []
    if not files:
        return {"date": None, "items": []}
    latest = files[-1]
    try:
        text = latest.read_text(encoding="utf-8")
    except OSError:
        return {"date": None, "items": []}
    items = []
    for ln in text.split("\n"):
        m = _RADAR_RE.match(ln)
        if m and len(items) < 10:
            url = (m.group(5) or "").strip()
            items.append({
                "rank": int(m.group(1)), "title": m.group(2).strip()[:140],
                "tag": m.group(3).strip()[:24], "why": _WIKI_RE.sub(lambda mm: mm.group(1), m.group(4)).strip()[:300],
                "url": url if url.startswith(("https://", "http://")) else "",
            })
    return {"date": latest.stem, "items": items}


# ---------- Daily log: the "## Log" section the nightly job writes into Daily/YYYY-MM-DD.md ----------

LOG_HEADING = "## Log"


@router.get("/daylog")
async def daylog_get() -> dict:
    """Latest day (up to 7 days back) that has a Log section, as plain bullet lines."""
    daily = _vault() / "Daily"
    today = datetime.date.today()
    for back in range(0, 7):
        day = (today - datetime.timedelta(days=back)).isoformat()
        try:
            text = (daily / (day + ".md")).read_text(encoding="utf-8")
        except OSError:
            continue
        lines = text.split("\n")
        start = next((i for i, ln in enumerate(lines) if ln.strip() == LOG_HEADING), None)
        if start is None:
            continue
        items = []
        for ln in lines[start + 1:]:
            if ln.startswith("#"):
                break
            s = ln.strip()
            if s.startswith("- ") and len(items) < 12:
                items.append(_WIKI_RE.sub(lambda mm: mm.group(1), s[2:]).strip()[:300])
        if items:
            return {"date": day, "items": items}
    return {"date": None, "items": []}


# --- engine: which model answers interactive chats (local by default; Claude in the cloud on request) ---------------
#
# State lives in <HERMES_HOME>/atrium/engine.json. It only changes the model used for chats started from the app
# (Chat page and the Ask box). Scheduled jobs always use the configured local model. A cloud engine expires on its own.

CLAUDE_MODELS = {
    "claude-sonnet-5-5": "Claude Sonnet 5.5 (balanced)",
    "claude-opus-5-5": "Claude Opus 5.5 (strongest)",
    "claude-haiku-5-5": "Claude Haiku 5.5 (fast, cheapest)",
    "claude-fable-5-2": "Claude Fable 5.2 (frontier)",
}


def _engine_path() -> Path:
    return _hermes_home() / "atrium" / "engine.json"


def _claude_key_present() -> bool:
    key = _dotenv_value("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    return bool(key and len(key) > 20)


def engine_state() -> dict:
    st: dict = {"engine": "local"}
    try:
        data = json.loads(_engine_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("engine") == "catalog":
            if all(isinstance(data.get(key), str) and data[key] for key in ("model", "provider")):
                return {key: data[key] for key in ("engine", "model", "provider", "default") if key in data}
        if isinstance(data, dict) and data.get("engine") == "claude" and data.get("model") in CLAUDE_MODELS:
            st = {"engine": "claude", "model": data["model"], "until": data.get("until"), "since": data.get("since")}
    except (OSError, ValueError):
        pass
    if st["engine"] == "claude" and st.get("until") and time.time() > float(st["until"]):
        st = {"engine": "local"}                       # the cloud time is up: back to local
        _write_engine(st)
    return st


def _write_engine(st: dict) -> None:
    path = _engine_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        try:
            json.dump(st, tmp)
            tmp.flush()
            os.replace(tmp.name, path)
        finally:
            Path(tmp.name).unlink(missing_ok=True)


def engine_override() -> dict:
    """Extra fields for a /v1/runs request when a cloud engine is on."""
    st = engine_state()
    if st["engine"] == "catalog":
        return {"model": st["model"], "provider": st["provider"], "require_model_lock": True}
    return {"model": st["model"], "provider": "anthropic"} if st["engine"] == "claude" else {}


class EngineBody(BaseModel):
    engine: Literal["local", "claude"]
    model: Optional[str] = None
    minutes: int = Field(default=60, ge=0, le=1440)     # 0 = until switched back
    confirm: bool = False


@router.get("/engine")
async def engine_get() -> dict:
    st = await asyncio.to_thread(engine_state)
    left = None
    if st["engine"] == "claude" and st.get("until"):
        left = max(0, int(float(st["until"]) - time.time()))
    return {"engine": st["engine"], "model": st.get("model"), "seconds_left": left,
            "claude_available": _claude_key_present(), "claude_models": [{"id": k, "label": v} for k, v in CLAUDE_MODELS.items()],
            "local": {"model": "agent-main", "name": "Your local model"}}


@router.post("/engine")
async def engine_set(body: EngineBody) -> dict:
    if body.engine == "local":
        current = await asyncio.to_thread(engine_state)
        selected = _catalog_default(await _model_catalog()) if current["engine"] == "catalog" else {"engine": "local"}
        if not selected:
            raise HTTPException(503, "Could not resolve the Hermes default model.")
        await asyncio.to_thread(_write_engine, selected)
        return {"ok": True}
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Confirm the switch to the cloud engine first.")
    if body.model not in CLAUDE_MODELS:
        raise HTTPException(status_code=400, detail="Choose one of the listed Claude models.")
    if not _claude_key_present():
        raise HTTPException(status_code=409, detail="No Anthropic key is set up on the server.")
    now = time.time()
    st = {"engine": "claude", "model": body.model, "since": now, "until": (now + body.minutes * 60) if body.minutes else None}
    await asyncio.to_thread(_write_engine, st)
    return {"ok": True}


async def _model_catalog() -> dict:
    if HERMES is None or not HERMES.dash_base:
        return {}
    code, data = await HERMES.dash_call("GET", "/api/model/options", timeout=25)
    return data if code == 200 and isinstance(data, dict) else {}


def _catalog_routes(catalog: dict) -> list[dict]:
    routes = []
    for provider in catalog.get("providers") or []:
        if not isinstance(provider, dict) or provider.get("authenticated") is not True:
            continue
        slug = provider.get("slug")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,80}", slug):
            continue
        for model in provider.get("models") or []:
            if isinstance(model, str) and 0 < len(model) <= 240 and not re.search(r"[\x00\r\n]", model):
                routes.append({"provider": slug, "model": model, "label": provider.get("name") or slug})
    return routes


def _catalog_default(catalog: dict) -> dict:
    provider, model = catalog.get("provider"), catalog.get("model")
    if isinstance(provider, str) and provider and isinstance(model, str) and model:
        return {"engine": "catalog", "provider": provider, "model": model, "default": True}
    return {}


def _provider_settings_url() -> str:
    from urllib.parse import urlsplit

    try:
        value = os.environ.get("HERMES_PROVIDER_SETTINGS_URL", "").strip()
        parsed = urlsplit(value)
        if parsed.hostname and not parsed.username and not parsed.password and (
            parsed.scheme == "https" or
            (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"))
        ):
            return value
    except ValueError:
        pass
    return ""


class CatalogModel(BaseModel):
    provider: str = Field(default="", max_length=80)
    model: str = Field(default="", max_length=240)
    confirm: bool = False


@router.get("/models")
async def model_options() -> dict:
    catalog = await _model_catalog()
    return {"available": bool(catalog), "models": _catalog_routes(catalog),
            "selected": await asyncio.to_thread(engine_state),
            "default_model": _catalog_default(catalog).get("model"),
            "provider_settings_url": _provider_settings_url()}


@router.post("/models")
async def model_select(body: CatalogModel) -> dict:
    catalog = await _model_catalog()
    default = _catalog_default(catalog)
    if not default:
        raise HTTPException(503, "Could not resolve the Hermes default model.")
    if not body.provider and not body.model:
        selected = default
    else:
        if not body.confirm:
            raise HTTPException(400, "Confirm changing the model for interactive chats.")
        if not any(r["provider"] == body.provider and r["model"] == body.model
                   for r in _catalog_routes(catalog)):
            raise HTTPException(409, "Choose a model from a connected Hermes provider.")
        selected = {"engine": "catalog", "provider": body.provider, "model": body.model}
    await asyncio.to_thread(_write_engine, selected)
    return {"selected": selected}


# --- soul: The agent's identity and rules (SOUL.md) ------------------------------------------------------------------
#
# The owner can read and edit it here. Every save keeps the previous version (the last 40) so one click restores it.
# It is read when a chat or job starts, so a change applies from the next one.

SOUL_MAX = 12000
_SOUL_ID = re.compile(r"^SOUL-\d{8}-\d{6}\.md$")


def _soul_read() -> Optional[str]:
    status, data = _dash("GET", "/api/profiles/default/soul")
    if status == 200 and isinstance(data, dict) and isinstance(data.get("content"), str):
        return data["content"]
    return None


def _soul_hist() -> Path:
    return _hermes_home() / "atrium" / "soul-history"


def _soul_backup() -> Optional[str]:
    cur = _soul_read()
    if not cur:
        return None
    d = _soul_hist()
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.time()
    name = "SOUL-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(stamp)) + ".md"
    while (d / name).exists():                         # two saves in one second must not overwrite each other
        stamp += 1
        name = "SOUL-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(stamp)) + ".md"
    (d / name).write_text(cur, encoding="utf-8")
    old = sorted(d.glob("SOUL-*.md"))
    for p in old[:-40]:
        try:
            p.unlink()
        except OSError:
            pass
    return name


def _soul_write(text: str) -> None:
    status, _ = _dash("PUT", "/api/profiles/default/soul", {"content": text})
    if status != 200:
        raise HTTPException(status_code=502, detail="The agent refused that change.")


class SoulBody(BaseModel):
    text: str = Field(min_length=20, max_length=SOUL_MAX)


class SoulRestore(BaseModel):
    id: str


@router.get("/soul")
async def soul_get() -> dict:
    def read() -> dict:
        text, mtime = _soul_read() or "", 0
        hist = []
        try:
            for p in sorted(_soul_hist().glob("SOUL-*.md"), reverse=True)[:40]:
                hist.append({"id": p.name, "when": p.name[5:20], "size": p.stat().st_size})
        except OSError:
            pass
        return {"text": text, "mtime": mtime, "max": SOUL_MAX, "history": hist}
    return await asyncio.to_thread(read)


@router.post("/soul")
async def soul_set(body: SoulBody) -> dict:
    text = body.text.replace("\r\n", "\n").replace("\x00", "").strip() + "\n"

    def write() -> None:
        if (_soul_read() or "") == text:
            return
        _soul_backup()
        _soul_write(text)
    await asyncio.to_thread(write)
    return {"ok": True}


@router.get("/soul/version")
async def soul_version(id: str) -> dict:
    if not _SOUL_ID.fullmatch(id):
        raise HTTPException(status_code=400, detail="Invalid version.")
    try:
        return {"text": await asyncio.to_thread((_soul_hist() / id).read_text, "utf-8")}
    except OSError:
        raise HTTPException(status_code=404, detail="Not found.") from None


@router.post("/soul/restore")
async def soul_restore(body: SoulRestore) -> dict:
    if not _SOUL_ID.fullmatch(body.id):
        raise HTTPException(status_code=400, detail="Invalid version.")

    def go() -> None:
        try:
            text = (_soul_hist() / body.id).read_text(encoding="utf-8")
        except OSError:
            raise HTTPException(status_code=404, detail="Not found.") from None
        _soul_backup()
        _soul_write(text)
    await asyncio.to_thread(go)
    return {"ok": True}
