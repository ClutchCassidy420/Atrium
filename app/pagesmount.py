"""The owner's pages: Memory (the notes), Workflows (the scheduled jobs and the nightly radar topics) and Settings (when jobs run) and Room (a talk-only group chat with the local agent and a cloud model).

These show and change things that belong to the whole install (the agent's notes, her schedule), so every route needs an owner
(the "admin" permission). Their backends are vendored from the Atrium Loom plugins; see the notes at the top of each file.
"""
from __future__ import annotations

import os
import re
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request

import extension_hooks
from connections_backend import connection_router
from model_setup import model_setup_router

OWNER_PLUGINS = ("memory", "workflows", "settings", "room", "skills")
# the only routes of the Mission Control backend that the app mounts (the rest is replaced by the Home page)
MISSION_ROUTES = ("/focus", "/focus/toggle", "/focus/remove", "/engine", "/soul", "/soul/version", "/soul/restore")


def prefix_for(name: str) -> str:
    return "/api/plugins/atrium-" + name


# What a person without the owner role may reach when personal mailboxes are on: exactly these routes (method and whole path), nothing that merely
# starts the same way. Anything else an install adds to the mail router (for example a block-list action under /messages/<uid>/spam, which acts on
# the server-wide mailbox) stays owner-only.
_MSG = r"/messages/\d+"
PERSONAL_MAIL_ROUTES = tuple((method, re.compile(pattern)) for method, pattern in (
    ("GET", r"/status"), ("GET", r"/folders"), ("GET", r"/messages"), ("GET", _MSG), ("GET", _MSG + r"/attachment/\d+"),
    ("POST", _MSG + r"/flags"), ("POST", _MSG + r"/move"), ("DELETE", _MSG), ("POST", r"/send"),
    ("GET", r"/account"), ("PUT", r"/account"), ("DELETE", r"/account/[a-z0-9]{4,16}"),
    ("GET", r"/safe-senders"), ("POST", r"/safe-senders"), ("DELETE", r"/safe-senders")))


def personal_mail_route(method: str, path: str) -> bool:
    method = "GET" if method == "HEAD" else method
    return any(m == method and rx.fullmatch(path) for m, rx in PERSONAL_MAIL_ROUTES)


def mail_guard(cfg, current, same_origin, store):
    """Owners keep the server-wide mailbox when there is one. Everyone else works on their own mailbox, found from their session only."""
    import asyncio
    import mail_accounts
    import mail_backend

    async def guard(request: Request):
        who = current(request)
        if not who:
            raise HTTPException(status_code=401, detail="Not signed in.")
        owner = cfg.allows(who["role"], "admin")
        path = request.url.path[len("/api/mail"):]
        if not owner and not (cfg.allows(who["role"], "use") and personal_mail_route(request.method, path)):
            raise HTTPException(status_code=403, detail="Only owners can use that.")
        if request.method not in ("GET", "HEAD") and not same_origin(request):
            raise HTTPException(status_code=403, detail="Bad origin.")
        request.state.who = who
        mail_backend.SERVER_OK.set(owner)                # only owners may ever work on the server-wide mailbox
        if path.startswith("/account"):                 # connecting or disconnecting must work even when a saved mail server cannot be found right now
            return
        if not personal_mail_route(request.method, path):
            return                                      # a route an install added acts on the server-wide mailbox, whichever mailbox the page has open
        picked = request.query_params.get("account", "")
        if picked == "server" and not owner:
            raise HTTPException(status_code=403, detail="That mailbox is not yours.")
        if picked == "server" or (not picked and owner and mail_backend.server_configured()):
            return                                      # owners start on the server-wide mailbox when there is one
        try:
            own = await asyncio.to_thread(store.settings, who["user"], picked)
        except mail_accounts.NoSuchAccount as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        except mail_accounts.AccountError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        mail_backend.ACCOUNT.set(own)
    return guard


def mount_owner_pages(app, cfg, hermes, current, same_origin) -> None:
    if cfg.hermes_data_dir:
        os.environ["HERMES_HOME"] = cfg.hermes_data_dir
    import kanban_backend, pipeline_backend, live_backend, memory_backend, mission_backend, room_backend, settings_backend, skills_backend, workflows_backend, hostctl_backend
    vault = os.path.join(cfg.hermes_data_dir or "/opt/data", "obsidian-vault")
    os.environ["OBSIDIAN_VAULT_PATH"] = vault
    mission_backend.HERMES = hermes
    kanban_backend.HERMES = hermes
    workflows_backend.HERMES = hermes
    settings_backend.HERMES = hermes
    room_backend.SOUL_READER = mission_backend._soul_read      # the soul is read through the Hermes dashboard
    backends = {"memory": memory_backend, "workflows": workflows_backend, "settings": settings_backend, "room": room_backend, "skills": skills_backend}

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

    app.include_router(model_setup_router(hermes), prefix=prefix_for("settings"), dependencies=[Depends(guard)])
    app.include_router(connection_router(hermes), prefix=prefix_for("settings"), dependencies=[Depends(guard)])

    hostctl_backend.configure(cfg, hermes)
    import overview
    overview.HOST_ACTIONS = bool(cfg.host_action_url and cfg.host_action_key)
    app.include_router(hostctl_backend.router, prefix="/api/host", dependencies=[Depends(guard)])
    app.include_router(kanban_backend.router, prefix="/api/kanban", dependencies=[Depends(guard)])
    import nextcloud_backend
    if nextcloud_backend.configure(cfg):                 # Files and Calendar exist only when a Nextcloud is configured
        app.include_router(nextcloud_backend.router, prefix="/api/nextcloud", dependencies=[Depends(guard)])
    ctx = SimpleNamespace(guard=guard, hermes=hermes, current=current, same_origin=same_origin, mail_backend=None, server_mail=False)
    if cfg.rea_url and cfg.rea_token:                    # the REA workbench page exists only when its container is set up; owners only
        import rea_backend
        app.include_router(rea_backend.rea_router(cfg, current, same_origin), prefix="/api/rea")
    import media_backend, media_servers
    from users import UserStore as _Users
    media_store = media_servers.Servers(cfg.data_dir, cfg.mail_accounts_key)
    media_store.purge([u["username"] for u in _Users(cfg.data_dir / "users.json").all()])
    app.include_router(media_backend.media_router(cfg, current, same_origin, media_store), prefix="/api/media")    # for everyone who may use the app: each person's own servers
    extension_hooks.call("mount_pages", app, cfg, ctx)   # a private layer mounts its own owner-only routes here (app/extension_hooks.py)
    import mail_backend
    server_mail = mail_backend.configure(cfg)
    mail_backend.ACCOUNTS = None
    import mail_images
    from users import UserStore
    mail_backend.SAFE = mail_images.SafeSenders(cfg.data_dir)         # senders each person always sees images from
    mail_backend.SIGNER = mail_images.Signer(cfg.data_dir)            # signs the links a message uses for the images it shows
    mail_backend.SAFE.purge([u["username"] for u in UserStore(cfg.data_dir / "users.json").all()])
    ctx.mail_backend, ctx.server_mail = mail_backend, server_mail
    extension_hooks.call("before_mail", app, cfg, ctx)   # a layer may add routes to the mail router before it is mounted
    if cfg.mail_user_accounts:                          # every signed-in person may connect their own mailbox
        import mail_accounts
        from users import UserStore
        store = mail_accounts.Accounts(cfg.data_dir, cfg.mail_accounts_key)
        store.migrate()                                 # logins saved under the old key file move to the key from the settings
        store.purge([u["username"] for u in UserStore(cfg.data_dir / "users.json").all()])      # a removed person's saved sign-in goes too
        mail_backend.ACCOUNTS = store
        app.include_router(mail_backend.router, prefix="/api/mail", dependencies=[Depends(mail_guard(cfg, current, same_origin, store))])
    elif server_mail:                                   # otherwise Email exists only when a server-wide mailbox is configured, for owners
        app.include_router(mail_backend.router, prefix="/api/mail", dependencies=[Depends(guard)])
    if cfg.mail_user_accounts or server_mail:           # the signed links a message uses for its images (no session: the link proves the right)
        app.include_router(mail_backend.image_router, prefix="/api/mail-image")
    pipeline_backend.configure(cfg, hermes, app)
    app.include_router(pipeline_backend.router, prefix="/api/pipeline", dependencies=[Depends(guard)])

    mission = APIRouter()
    for route in mission_backend.router.routes:
        if getattr(route, "path", "") in MISSION_ROUTES:
            mission.routes.append(route)
    app.include_router(mission, prefix=prefix_for("mission"), dependencies=[Depends(guard)])
    # the Live agent stage: character file, clips, whiteboard notes and speech with word timing
    app.include_router(live_backend.router, prefix="/api/plugins/atrium-avatar", dependencies=[Depends(guard)])
