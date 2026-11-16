"""The owner's pages: Memory (the notes), Workflows (the scheduled jobs and the nightly radar topics) and Settings (when jobs run) and Room (a talk-only group chat with the local agent and a cloud model).

These show and change things that belong to the whole install (the agent's notes, her schedule), so every route needs an owner
(the "admin" permission). Their backends are vendored from the Atrium Loom plugins; see the notes at the top of each file.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request

OWNER_PLUGINS = ("memory", "workflows", "settings", "room")
# the only routes of the Mission Control backend that the app mounts (the rest is replaced by the Home page)
MISSION_ROUTES = ("/focus", "/focus/toggle", "/focus/remove", "/engine", "/soul", "/soul/version", "/soul/restore")


def prefix_for(name: str) -> str:
    return "/api/plugins/atrium-" + name


def mount_owner_pages(app, cfg, hermes, current, same_origin) -> None:
    if cfg.hermes_data_dir:
        os.environ["HERMES_HOME"] = cfg.hermes_data_dir
    import kanban_backend, pipeline_backend, live_backend, memory_backend, mission_backend, room_backend, settings_backend, workflows_backend, hostctl_backend
    vault = os.path.join(cfg.hermes_data_dir or "/opt/data", "obsidian-vault")
    os.environ["OBSIDIAN_VAULT_PATH"] = vault
    mission_backend.HERMES = hermes
    kanban_backend.HERMES = hermes
    workflows_backend.HERMES = hermes
    settings_backend.HERMES = hermes
    room_backend.SOUL_READER = mission_backend._soul_read      # the soul is read through the Hermes dashboard
    backends = {"memory": memory_backend, "workflows": workflows_backend, "settings": settings_backend, "room": room_backend}

    async def guard(request: Request):
        who = current(request)
        if not who:
            raise HTTPException(status_code=401, detail="Not signed in.")
        if not cfg.allows(who["role"], "admin"):
            raise HTTPException(status_code=403, detail="Only owners can use that.")
        if request.method not in ("GET", "HEAD") and not same_origin(request):
            raise HTTPException(status_code=403, detail="Bad origin.")
        request.state.who = who

    for name in OWNER_PLUGINS:
        app.include_router(backends[name].router, prefix=prefix_for(name), dependencies=[Depends(guard)])

    hostctl_backend.configure(cfg, hermes)
    import overview
    overview.HOST_ACTIONS = bool(cfg.host_action_url and cfg.host_action_key)
    app.include_router(hostctl_backend.router, prefix="/api/host", dependencies=[Depends(guard)])
    app.include_router(kanban_backend.router, prefix="/api/kanban", dependencies=[Depends(guard)])
    pipeline_backend.configure(cfg, hermes, app)
    app.include_router(pipeline_backend.router, prefix="/api/pipeline", dependencies=[Depends(guard)])

    mission = APIRouter()
    for route in mission_backend.router.routes:
        if getattr(route, "path", "") in MISSION_ROUTES:
            mission.routes.append(route)
    app.include_router(mission, prefix=prefix_for("mission"), dependencies=[Depends(guard)])
    # the Live agent stage: character file, clips, whiteboard notes and speech with word timing
    app.include_router(live_backend.router, prefix="/api/plugins/atrium-avatar", dependencies=[Depends(guard)])
