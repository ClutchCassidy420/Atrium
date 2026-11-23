"""Shared plumbing for the agent-facing backends (chat, mission, memory, room, settings, workflows).

These pieces used to be copied, line for line, into each backend. They live here once so a fix lands everywhere:

* the agent's name and the ``{{agent}}`` placeholder that is filled in on the way out (``AgentRoute``);
* where Hermes keeps its data and how to read one value from its ``.env`` without exporting it;
* the one blocking call to the Hermes API server (``call`` runs it in a worker thread);
* the run-id check and a small atomic-write helper.

Standard library plus FastAPI only. Nothing here logs or returns a secret.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from fastapi.routing import APIRoute

# --- the agent's name ----------------------------------------------------------------------------------------------
# Text in a backend says {{agent}} wherever it means the agent's name. It is filled in on the way out (JSON, errors,
# streams, files) from <data>/atrium/agent.json, which the Settings tab writes. Without it the name is "Assistant".

_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._'-]{0,39}$")
_AGENT_NAME_TTL_S = 5
_agent_cache: dict = {"t": 0.0, "v": "Assistant", "home": None}


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or "/opt/data")


def agent_name() -> str:
    home = hermes_home()
    if _agent_cache["home"] == home and time.time() - _agent_cache["t"] < _AGENT_NAME_TTL_S:
        return _agent_cache["v"]
    name = "Assistant"
    try:
        value = json.loads((home / "atrium" / "agent.json").read_text(encoding="utf-8")).get("name")
        if isinstance(value, str) and _AGENT_NAME_RE.match(value.strip()):
            name = value.strip()
    except (OSError, ValueError, AttributeError):
        pass
    _agent_cache.update({"t": time.time(), "v": name, "home": home})
    return name


def valid_agent_name(name: str) -> bool:
    return bool(_AGENT_NAME_RE.match(name))


def save_agent_name(name: str) -> None:
    """Persist the name (already checked with valid_agent_name) and make every backend see it at once."""
    f = hermes_home() / "atrium" / "agent.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(f, json.dumps({"name": name}))
    _agent_cache["t"] = 0.0


def fill_text(text):
    """Replace {{agent}} in a string; anything else passes through untouched."""
    return text.replace("{{agent}}", agent_name()) if isinstance(text, str) and "{{agent}}" in text else text


def fill_response(resp):
    body = getattr(resp, "body", None)
    if isinstance(body, (bytes, bytearray)) and b"{{agent}}" in body:
        resp.body = bytes(body).replace(b"{{agent}}", agent_name().encode("utf-8"))
        resp.headers["content-length"] = str(len(resp.body))
    elif hasattr(resp, "body_iterator"):
        inner = resp.body_iterator

        async def _gen():
            async for chunk in inner:
                if isinstance(chunk, (bytes, bytearray)):
                    yield bytes(chunk).replace(b"{{agent}}", agent_name().encode("utf-8"))
                elif isinstance(chunk, str):
                    yield fill_text(chunk)
                else:
                    yield chunk
        resp.body_iterator = _gen()
    return resp


class AgentRoute(APIRoute):
    """A route class that fills {{agent}} into every response and every error message."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                resp = await original(request)
            except HTTPException as exc:
                detail = fill_text(exc.detail) if isinstance(exc.detail, str) else exc.detail
                raise HTTPException(status_code=exc.status_code, detail=detail, headers=exc.headers) from None
            return fill_response(resp)
        return handler


# --- reading Hermes' configuration ---------------------------------------------------------------------------------

def dotenv_value(name: str) -> Optional[str]:
    """Read one value from the Hermes .env file without exporting it anywhere."""
    try:
        for line in (hermes_home() / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip('"').strip("'") or None
    except OSError:
        return None
    return None


def api_key() -> Optional[str]:
    return os.environ.get("API_SERVER_KEY") or dotenv_value("API_SERVER_KEY")


def api_base() -> str:
    host = os.environ.get("API_SERVER_HOST") or dotenv_value("API_SERVER_HOST") or "127.0.0.1"
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port = os.environ.get("API_SERVER_PORT") or dotenv_value("API_SERVER_PORT") or "8642"
    if not re.fullmatch(r"\d{2,5}", str(port)):
        port = "8642"
    return f"http://{host}:{port}"


# --- the call to the Hermes API server -----------------------------------------------------------------------------

UPSTREAM_TIMEOUT_S = 15
_REFUSED_STATUSES = (400, 404, 409, 429)       # passed through as they are; every other upstream error becomes a 502


def call_sync(method: str, path: str, payload: Optional[dict] = None) -> tuple[int, dict]:
    """Blocking call to the Hermes API server. Raises HTTPException with a short, safe message on any failure."""
    key = api_key()
    if not key:
        raise HTTPException(status_code=503, detail="The agent API key is not configured.")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        api_base() + path,
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
        code = exc.code if exc.code in _REFUSED_STATUSES else 502
        raise HTTPException(status_code=code, detail=detail) from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Could not reach the agent API.") from None


async def call(method: str, path: str, payload: Optional[dict] = None) -> tuple[int, dict]:
    """The same call, run in a worker thread so it never blocks the web server."""
    return await asyncio.to_thread(call_sync, method, path, payload)


# --- small shared helpers ------------------------------------------------------------------------------------------

RUN_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")


def check_run_id(run_id: str) -> str:
    if not RUN_ID_RE.fullmatch(run_id or ""):
        raise HTTPException(status_code=400, detail="Invalid run id.")
    return run_id


def atomic_write_text(path: Path, text: str) -> None:
    """Write beside the target and swap it in, so a crash never leaves a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
