"""The app's connection to Hermes: its API server, its dashboard server, and a few folders of its data.

Standard library only. Calls run in a worker thread so they never block the web server. Nothing here ever returns or
logs a key or token; the health report holds only yes/no answers and short status codes.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT_S = 5.0
# the folders of Hermes' data directory the app can see (mounted read-only; the database file is not among them)
DATA_FOLDERS = ("atrium", "cron", "obsidian-vault")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Hermes:
    def __init__(self, api_base: str = "", api_key: str = "", dash_base: str = "", dash_token: str = "", data_dir: str = ""):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.dash_base = dash_base.rstrip("/")
        self.dash_token = dash_token
        self.data_dir = Path(data_dir) if data_dir else None

    @classmethod
    def from_env(cls) -> "Hermes":
        e = os.environ.get
        return cls(e("HERMES_API_BASE", ""), e("HERMES_API_KEY", ""), e("HERMES_DASHBOARD_BASE", ""),
                   e("HERMES_DASHBOARD_TOKEN", ""), e("HERMES_DATA_DIR", ""))

    # -- low level (blocking; always called through a thread) -------------------------------------------------
    @staticmethod
    def _get(url: str, headers: dict) -> tuple[int, bytes]:
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return r.status, r.read(2_000_000)
        except urllib.error.HTTPError as e:
            return e.code, b""
        except Exception:
            return 0, b""                      # 0 = could not connect

    def _api_headers(self) -> dict:
        return {"Authorization": "Bearer " + self.api_key} if self.api_key else {}

    def _dash_headers(self) -> dict:
        return {"X-Hermes-Session-Token": self.dash_token} if self.dash_token else {}

    # -- calls the pages will use ---------------------------------------------------------------------------
    async def api_json(self, path: str):
        """GET from the Hermes API server (runs, models, jobs...). Returns (status, parsed json or None)."""
        status, body = await asyncio.to_thread(self._get, self.api_base + path, self._api_headers())
        try:
            return status, json.loads(body) if body else None
        except ValueError:
            return status, None

    @staticmethod
    def _send(method: str, url: str, headers: dict, payload, timeout: float = TIMEOUT_S,
              *, allow_redirects: bool = True) -> tuple[int, bytes]:
        data = json.dumps(payload).encode() if payload is not None else None
        try:
            req = urllib.request.Request(url, data=data, headers=dict(headers, **{"Content-Type": "application/json"}), method=method)
            open_request = urllib.request.urlopen if allow_redirects else urllib.request.build_opener(_NoRedirect()).open
            with open_request(req, timeout=timeout) as r:
                return r.status, r.read(8_000_000)
        except urllib.error.HTTPError as e:
            return e.code, e.read(200_000)
        except Exception:
            return 0, b""

    async def api_call(self, method: str, path: str, payload=None):
        """Any call to the Hermes API server (starting a run, stopping it, answering an approval). Returns (status, json or None)."""
        status, body = await asyncio.to_thread(self._send, method, self.api_base + path, self._api_headers(), payload)
        try:
            return status, json.loads(body) if body else None
        except ValueError:
            return status, None

    async def dash_call(self, method: str, path: str, payload=None, timeout: float = TIMEOUT_S,
                        *, allow_redirects: bool = True):
        """Any call to the Hermes dashboard server (sessions, transcription), with the fixed token. Returns (status, json or None)."""
        status, body = await asyncio.to_thread(
            self._send, method, self.dash_base + path, self._dash_headers(), payload, timeout,
            allow_redirects=allow_redirects,
        )
        try:
            return status, json.loads(body) if body else None
        except ValueError:
            return status, None

    async def dash_json(self, path: str):
        """GET from the Hermes dashboard server (status, sessions, stats, kanban). Returns (status, parsed json or None)."""
        status, body = await asyncio.to_thread(self._get, self.dash_base + path, self._dash_headers())
        try:
            return status, json.loads(body) if body else None
        except ValueError:
            return status, None

    # -- health -----------------------------------------------------------------------------------------------
    async def health(self) -> dict:
        api_up, api_key_ok, dash_up, dash_token_ok = False, False, False, False
        if self.api_base:
            s, _ = await self.api_json("/health")
            api_up = s == 200
            if api_up and self.api_key:
                s, _ = await self.api_json("/v1/models")
                api_key_ok = s == 200
        if self.dash_base:
            s, _ = await self.dash_json("/api/status")
            dash_up = s == 200
            if dash_up and self.dash_token:
                s, _ = await self.dash_json("/api/sessions?limit=1")
                dash_token_ok = s == 200
        data = {}
        for name in DATA_FOLDERS:
            p = self.data_dir / name if self.data_dir else None
            try:
                data[name] = bool(p and p.is_dir() and os.access(p, os.R_OK | os.X_OK))
            except OSError:
                data[name] = False
        ok = api_up and api_key_ok and dash_up and dash_token_ok and all(data.values())
        return {"ok": ok,
                "api": {"reachable": api_up, "key_ok": api_key_ok},
                "dashboard": {"reachable": dash_up, "token_ok": dash_token_ok},
                "data": data}
