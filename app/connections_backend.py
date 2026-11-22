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
    if auth == "oauth":
        blocked = "This connection requires OAuth. Set it up in Hermes."
    elif (entry.get("transport") != "http" or entry.get("needs_install") is not False
          or entry.get("command") or entry.get("bootstrap")):
        blocked = "This entry requires setup in Hermes."
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
        elif auth not in ("none", "api_key") or not isinstance(fields, list) or any(
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
        "host": host,
        "fields": [] if blocked else [
            {"name": field["name"], "label": str(field.get("prompt") or field["name"])[:160],
             "required": field.get("required", True) is not False}
            for field in fields
        ],
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

    @router.delete("/connections/{name}")
    async def remove(name: str):
        async with changes:
            path = await server_path(name, modify=True)
            result = await call("DELETE", path)
            if result.get("ok") is not True:
                raise HTTPException(502, "Hermes did not confirm removal.")
        return {"ok": True}

    return router
