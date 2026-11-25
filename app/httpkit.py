"""Reused HTTP clients for the short calls to the services this app fronts (coding workspace, REA workbench, pipeline).

A new client per call means a new connection, and for HTTPS a new TLS handshake, every time. ``shared`` keeps one client per running
event loop (a client belongs to the loop it was made on) and per set of settings, so a page that makes several calls in a row reuses
one connection. Pass the timeout per request. Streaming calls and uploads should keep their own client.
"""
from __future__ import annotations

import asyncio
import weakref

import httpx

_CLIENTS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()       # loop -> {stamp: client}


def shared(**settings) -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    stamp = (tuple(sorted(settings.items())), httpx.AsyncClient)           # the class is part of the key so a test that swaps it gets its own
    held = _CLIENTS.setdefault(loop, {})
    if stamp not in held:
        held[stamp] = httpx.AsyncClient(**settings)
    return held[stamp]
