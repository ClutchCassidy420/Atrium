"""One definition of the checks a signed-in feature router needs.

Order is always: signed in (401) -> role (403) -> same origin for anything that changes data (403) -> ``request.state.who``.
Routers with extra rules pass ``after`` (runs last, with the person) instead of copying the first three checks.
"""
from __future__ import annotations

from fastapi import HTTPException, Request


def role_guard(cfg, current, same_origin, permission: str, *, denied: str = "Not allowed.", after=None):
    """Build a FastAPI dependency. ``denied`` is the 403 text for a wrong role; ``after(request, who)`` may add checks or state."""

    async def guard(request: Request):
        who = current(request)
        if not who:
            raise HTTPException(status_code=401, detail="Not signed in.")
        if not cfg.allows(who["role"], permission):
            raise HTTPException(status_code=403, detail=denied)
        if request.method not in ("GET", "HEAD") and not same_origin(request):
            raise HTTPException(status_code=403, detail="Bad origin.")
        request.state.who = who
        if after:
            after(request, who)

    return guard


OWNERS_ONLY = "Only owners can use that."
