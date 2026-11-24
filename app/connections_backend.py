"""Owner-facing adapter for the Hermes MCP dashboard API."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, ValidationError

NAME = re.compile(r"[a-zA-Z0-9_-]{1,128}")
MAX_CREDENTIAL_BODY = 128 * 1024
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")


class Connect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    env: dict[StrictStr, StrictStr] = Field(default_factory=dict, max_length=32)


class Enabled(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


def catalog_entry(entry):
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not NAME.fullmatch(entry["name"]):
        return None
    auth = entry.get("auth_type")
    url = entry.get("url")
    fields = entry.get("required_env", [])
    blocked = ""
    host = None
    oauth = auth == "oauth"
    if (entry.get("transport") != "http" or entry.get("needs_install") is not False
            or entry.get("command") or entry.get("bootstrap")):
        blocked = "This entry requires setup in Hermes."
    elif oauth and fields:
        blocked = "This connection needs an app registered with the provider first. Set it up in Hermes."
    else:
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            fixed_url = (parsed and parsed.scheme == "https" and parsed.hostname
                         and parsed.username is None and parsed.password is None
                         and not parsed.fragment and "{" not in url and "$" not in url)
        except ValueError:
            fixed_url = False
        if not fixed_url:
            blocked = "This entry requires endpoint setup in Hermes."
        elif auth not in ("none", "api_key", "oauth") or not isinstance(fields, list) or any(
            not isinstance(field, dict) or not isinstance(field.get("name"), str)
            or not ENV_NAME.fullmatch(field["name"]) for field in fields
        ):
            blocked = "This entry requires authentication setup in Hermes."
        else:
            host = parsed.netloc
    return {
        "name": entry["name"],
        "description": str(entry.get("description") or "")[:1000],
        "installed": entry.get("installed") is True,
        "blocked": blocked,
        "oauth": oauth and not blocked,       # signed in through the provider's own page instead of pasting a key
        "host": host,
        "fields": [] if blocked else [
            {"name": field["name"], "label": str(field.get("prompt") or field["name"])[:160],
             "required": field.get("required", True) is not False}
            for field in fields
        ],
    }


FLOW_ID = re.compile(r"[a-zA-Z0-9_-]{1,128}")
FLOW_STATES = {"starting", "authorization_required", "approved", "error", "cancelled", "expired"}


def check_flow_id(flow_id):
    if not FLOW_ID.fullmatch(flow_id):
        raise HTTPException(400, "Invalid sign-in id.")
    return flow_id


def safe_sign_in_url(value):
    """Only an https address without a password in it is ever passed on to the browser."""
    try:
        parsed = urlsplit(value) if isinstance(value, str) and len(value) <= 4096 else None
        if (parsed and parsed.scheme == "https" and parsed.hostname and parsed.username is None
                and parsed.password is None and not any(ord(c) < 32 or ord(c) == 127 for c in value)):
            return value
    except ValueError:
        pass
    return None


def flow_view(result):
    """What Atrium shows of one Hermes sign-in flow: its id, state, the provider's sign-in address and, when done, the tool names."""
    flow_id, status = result.get("flow_id"), result.get("status")
    if not isinstance(flow_id, str) or not FLOW_ID.fullmatch(flow_id) or status not in FLOW_STATES:
        raise HTTPException(502, "Hermes returned an incompatible sign-in response.")
    error = result.get("error")
    tools = result.get("tools")
    return {
        "flow_id": flow_id,
        "status": status,
        "authorization_url": safe_sign_in_url(result.get("authorization_url")) if status == "authorization_required" else None,
        "error": "".join(c for c in error[:300] if ord(c) >= 32 and ord(c) != 127) if isinstance(error, str) else None,
        "tools": [t["name"] for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str)][:200] if isinstance(tools, list) else [],
    }


def connection_router(hermes):
    router = APIRouter()
    changes = asyncio.Lock()

    async def call(method, path, payload=None, *, timeout=5):
        if not hermes.dash_base or not hermes.dash_token:
            raise HTTPException(503, "Configure Atrium's Hermes dashboard connection to manage MCP servers.")
        status, result = await hermes.dash_call(method, "/api/mcp" + path, payload, timeout=timeout, allow_redirects=False)
        if status in (404, 405, 501):
            raise HTTPException(501, "This MCP operation is unavailable in the connected Hermes dashboard. Check the connection or upgrade Hermes.")
        if status in (401, 403):
            raise HTTPException(502, "Hermes rejected Atrium's dashboard authentication.")
        if status == 409:
            raise HTTPException(409, "Hermes refused the change. Refresh the installed connections and try again.")
        if status != 200 or not isinstance(result, dict):
            raise HTTPException(502, "Hermes could not complete the MCP request. Check its dashboard for details.")
        return result

    async def read_list(path, key):
        result = await call("GET", path)
        items = result.get(key)
        if not isinstance(items, list):
            raise HTTPException(502, "Hermes returned an incompatible MCP response.")
        return items

    async def get_catalog():
        return [entry for item in await read_list("/catalog", "entries") if (entry := catalog_entry(item))]

    async def get_servers():
        return [
            {"name": item["name"], "enabled": item.get("enabled", True) is True,
             "managed": item.get("source") == "plugin" or bool(item.get("plugin"))}
            for item in await read_list("/servers", "servers")
            if isinstance(item, dict) and isinstance(item.get("name"), str) and NAME.fullmatch(item["name"])
        ]

    async def server_path(name, *, modify=False):
        server = next((item for item in await get_servers() if item["name"] == name), None)
        if server is None:
            raise HTTPException(404, "Connection not found. Refresh the installed list.")
        if modify and server["managed"]:
            raise HTTPException(409, "This connection is managed by a Hermes plugin.")
        return "/servers/" + name

    @router.get("/connections/catalog")
    async def catalog():
        return {"entries": await get_catalog()}

    @router.get("/connections/installed")
    async def installed():
        return {"servers": await get_servers()}

    @router.post("/connections/{name}/connect")
    async def connect(name: str, request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            raise HTTPException(415, "Send connection details as JSON.")
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_CREDENTIAL_BODY:
                raise HTTPException(413, "Connection details are too large.")
            raw.extend(chunk)
        # Parse manually so FastAPI's validation response cannot echo submitted credentials.
        try:
            body = Connect.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(400, "Invalid connection details.") from None
        async with changes:
            entry = next((item for item in await get_catalog() if item["name"] == name), None)
            if entry is None:
                raise HTTPException(404, "Catalog entry not found.")
            if entry["blocked"]:
                raise HTTPException(400, entry["blocked"])
            if entry["oauth"]:
                raise HTTPException(400, "This connection signs in through the provider's own page. Use Sign in.")
            if entry["installed"] or any(server["name"] == name for server in await get_servers()):
                raise HTTPException(409, "This connection is already installed.")
            fields = {field["name"]: field for field in entry["fields"]}
            if (body.env.keys() - fields.keys()
                or any(f["required"] and not body.env.get(key) for key, f in fields.items())
                or any(len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value)
                       for value in body.env.values())):
                raise HTTPException(400, "Supply the credential fields declared by this catalog entry.")
            result = await call("POST", "/catalog/install",
                                {"name": name, "env": body.env, "enable": True}, timeout=60)
            if result.get("ok") is not True or result.get("background"):
                raise HTTPException(502, "Hermes did not confirm installation. Refresh before retrying.")
        return {"ok": True}

    @router.post("/connections/{name}/test")
    async def test(name: str):
        path = await server_path(name)
        result = await call("POST", path + "/test", {}, timeout=60)
        tools = result.get("tools")
        if result.get("ok") is not True or not isinstance(tools, list):
            raise HTTPException(502, "Hermes could not discover tools. Check the connection and its authentication in Hermes.")
        return {"tools": [tool["name"] for tool in tools
                          if isinstance(tool, dict) and isinstance(tool.get("name"), str)]}

    @router.put("/connections/{name}/enabled")
    async def enable(name: str, body: Enabled):
        async with changes:
            path = await server_path(name, modify=True)
            result = await call("PUT", path + "/enabled", {"enabled": body.enabled})
            if result.get("ok") is not True:
                raise HTTPException(502, "Hermes did not confirm the change.")
        return {"ok": True}

    # --- sign in with OAuth -------------------------------------------------------------------------------------------
    # Hermes runs the whole OAuth exchange (discovery, registration, the code exchange, saving the token). Atrium only starts a
    # flow, passes the provider's sign-in address to the owner's browser, and watches the flow until it ends. The provider
    # sends the browser back to Hermes's own callback, so no code or token ever passes through Atrium.
    @router.post("/connections/{name}/oauth/start")
    async def oauth_start(name: str):
        async with changes:
            entry = next((item for item in await get_catalog() if item["name"] == name), None)
            if entry is None:
                raise HTTPException(404, "Catalog entry not found.")
            if entry["blocked"]:
                raise HTTPException(400, entry["blocked"])
            if not entry["oauth"]:
                raise HTTPException(400, "This connection does not sign in with OAuth.")
            if not (entry["installed"] or any(server["name"] == name for server in await get_servers())):
                result = await call("POST", "/catalog/install", {"name": name, "env": {}, "enable": True}, timeout=60)
                if result.get("ok") is not True or result.get("background"):
                    raise HTTPException(502, "Hermes did not confirm installation. Refresh before retrying.")
            result = await call("POST", "/servers/" + name + "/auth", {}, timeout=45)
        return flow_view(result)

    async def flow_call(method, flow_id):
        try:
            return await call(method, "/oauth/flows/" + check_flow_id(flow_id))
        except HTTPException as error:
            if error.status_code == 501:       # Hermes answers 404 for a flow it no longer holds (they expire after 15 minutes)
                raise HTTPException(404, "That sign-in is no longer running, or this Hermes is too old to support it.") from None
            raise

    @router.get("/connections/oauth/flows/{flow_id}")
    async def oauth_flow(flow_id: str):
        return flow_view(await flow_call("GET", flow_id))

    @router.delete("/connections/oauth/flows/{flow_id}")
    async def oauth_cancel(flow_id: str):
        await flow_call("DELETE", flow_id)
        return {"ok": True}

    @router.delete("/connections/{name}")
    async def remove(name: str):
        async with changes:
            path = await server_path(name, modify=True)
            result = await call("DELETE", path)
            if result.get("ok") is not True:
                raise HTTPException(502, "Hermes did not confirm removal.")
        return {"ok": True}

    return router
