"""Owner-facing setup for an OpenAI-compatible chat endpoint."""
import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request


def valid_endpoint(url):
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
        if not host or port == 0 or parts.username is not None or parts.password is not None:
            return False
        if parts.query or parts.fragment or any(c.isspace() or ord(c) < 32 or c == "\\" for c in url):
            return False
        if parts.scheme == "https":
            return True
        if parts.scheme != "http":
            return False
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def model_setup_router(hermes):
    router = APIRouter()

    @router.get("/model")
    async def current_model():
        status, data = await hermes.dash_call("GET", "/api/model/info", timeout=12, allow_redirects=False)
        if status != 200 or not isinstance(data, dict):
            raise HTTPException(502, "The runtime could not report its model.")
        return {k: data[k] for k in ("model", "provider") if isinstance(data.get(k), str)}

    @router.post("/model")
    async def save_model(request: Request):
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "Invalid setup form.") from None
        if not isinstance(body, dict):
            raise HTTPException(400, "Invalid setup form.")
        model, url, key = (body.get(k, "") for k in ("model", "base_url", "api_key"))
        if not all(isinstance(v, str) for v in (model, url, key)):
            raise HTTPException(400, "Invalid setup form.")
        model, url = model.strip(), url.strip()
        if not model or len(model) > 256 or len(key) > 8192 or len(url) > 2048 or not valid_endpoint(url):
            raise HTTPException(400, "Enter a model ID and an HTTPS base URL (HTTP is allowed on loopback).")
        payload = dict(scope="main", provider="custom", model=model, base_url=url, api_key=key,
                       confirm_expensive_model=body.get("confirm") is True)
        status, result = await hermes.dash_call("POST", "/api/model/set", payload, timeout=60, allow_redirects=False)
        if status == 200 and isinstance(result, dict):
            if result.get("confirm_required"):
                return {"confirm_required": True}
            if result.get("ok") is True:
                return {"ok": True}
        raise HTTPException(400 if status in (400, 422) else 502,
                            "The runtime could not save this model. Check the endpoint, model ID and key.")

    return router
