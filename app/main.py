"""The web app framework: login with users and roles, a signed-in shell, page manifests, the Hermes link.

Everything specific to one install (names, brand, hosts, extra pages, colours) comes from the instance folder and the
environment, never from this file. See docs/FRAMEWORK.md.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import mimetypes
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

import config as configmod
from agentkit import RUN_ID_RE, atomic_write_text
from auth import Sessions, Throttle, hash_password, verify_password
from hermes import Hermes
from overview import Overview
from chatmount import PREFIX as CHAT_PREFIX, mount_chat
from code_backend import mount_code
import chat_model
import firstrun
import themes as themesmod
from pages import load_pages
from pagesmount import mount_owner_pages
import extension_hooks
from users import UserStore

COOKIE = "__Host-app_session"        # the "__Host-" prefix needs a Secure cookie, i.e. HTTPS (the normal case)
HERE = Path(__file__).parent
STATIC = (HERE / "static").resolve()
# a valid hash of nothing in particular: checked when a username does not exist, so a wrong name and a wrong
# password take the same time
_DUMMY_HASH = hash_password("not-a-real-password", n=2 ** 12)


def make_app(cfg: configmod.Config | None = None, hermes: Hermes | None = None, secure_cookie: bool = True) -> FastAPI:
    cfg = cfg or configmod.load()
    hermes = hermes if hermes is not None else Hermes(cfg.hermes_api_base, cfg.hermes_api_key, cfg.hermes_dashboard_base,
                                                      cfg.hermes_dashboard_token, cfg.hermes_data_dir)
    cookie = COOKIE if secure_cookie else "app_session"      # plain-HTTP local development cannot use the "__Host-" name
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    users = UserStore(cfg.data_dir / "users.json")
    if users.empty() and cfg.bootstrap_hash:
        try:                                   # first start of an install that had a single owner password
            users.add(cfg.bootstrap_user, "owner", password_hash=cfg.bootstrap_hash)
        except ValueError:
            pass
    sessions = Sessions(cfg.data_dir / "sessions.json")
    overview = Overview(hermes, cfg.hermes_data_dir, cfg.deck_dir, cfg.systems)
    throttle = Throttle(cfg.max_fails, cfg.lock_minutes * 60)
    chat_model.configure(cfg.data_dir)
    configured = {"mail": bool(cfg.mail_address and cfg.mail_token and cfg.mail_imap_host) or cfg.mail_user_accounts,
                  "nextcloud": bool(cfg.nextcloud_url and cfg.nextcloud_user and cfg.nextcloud_password),
                  "rea": bool(cfg.rea_url and cfg.rea_token)}
    pages = [p for p in load_pages(HERE / "pages.json", cfg.instance_dir, cfg.pages_disabled) if not p["requires"] or configured[p["requires"]]]
    if cfg.mail_user_accounts:                          # personal mailboxes: the Email pages are for everyone who may use the app, not only owners
        for p in pages:
            if p["requires"] == "mail" and p["permission"] == "admin":
                p["permission"] = "use"
    instance_static = (cfg.instance_dir / "static").resolve()
    theme_file = cfg.instance_dir / "theme.css"
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    extension_hooks.call("on_app", app, cfg)             # a private layer may add its own routes here (app/extension_hooks.py)

    # ---- helpers ---------------------------------------------------------------------------------------------
    def client_address(request: Request) -> str:
        peer = request.client.host if request.client else "?"
        if peer in cfg.trusted_proxies:
            return request.headers.get("x-real-ip") or peer
        return peer

    def current(request: Request):
        """The signed-in user as {user, role}, or None. The role is read fresh, so changes apply at once."""
        s = sessions.get(request.cookies.get(cookie))
        if not s or not s.get("user"):
            return None
        u = users.get(s["user"])
        if not u or not cfg.valid_role(u["role"]):
            return None
        if s.get("credential") and s["credential"] != hashlib.sha256(u["hash"].encode()).hexdigest():
            return None
        return {"user": u["username"], "role": u["role"], "name": u.get("name") or ""}

    def same_origin(request: Request) -> bool:
        origin = request.headers.get("origin")
        if not origin:
            # No Origin header: command-line tools and older requests. A browser that says the request came from another site is refused.
            return request.headers.get("sec-fetch-site", "").lower() != "cross-site"
        return urlsplit(origin).netloc == request.headers.get("host", "")

    def page_doc(body: str, title: str, status: int = 200, shell: bool = False, theme: str = "") -> HTMLResponse:
        # the person's chosen color theme goes on <html> so the very first paint already has it (no flash of the default look)
        attr = ' data-theme="%s"' % html.escape(theme) if theme and theme != themesmod.DEFAULT else ""
        doc = ('<!doctype html><html lang="en"%s><head><meta charset="utf-8">'
               '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark">'
               '<title>%s</title><link rel="stylesheet" href="/static/tokens.css"><link rel="stylesheet" href="/static/themes.css">'
               '<link rel="stylesheet" href="/static/app.css">'
               '%s<link rel="stylesheet" href="/instance/theme.css"></head><body class="%s">%s</body></html>'
               % (attr, html.escape(title), '<link rel="stylesheet" href="/static/shell.css">' if shell else "",
                  "vn" if shell else "", body))
        return HTMLResponse(doc, status_code=status)

    def theme_of(who: dict) -> str:
        return themesmod.clean((users.get(who["user"]) or {}).get("theme")) or themesmod.DEFAULT

    def login_page(error: str = "", status: int = 200) -> HTMLResponse:
        err = '<p class="err" role="alert">%s</p>' % html.escape(error) if error else ""
        return page_doc(
            '<main class="card"><h1>%s</h1><p class="sub">Sign in to continue.</p>%s'
            '<form method="post" action="/login"><label for="un">Username</label>'
            '<input id="un" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required autofocus>'
            '<label for="pw">Password</label>'
            '<input id="pw" name="password" type="password" autocomplete="current-password" required>'
            '<button type="submit">Sign in</button></form></main>' % (html.escape(cfg.brand), err),
            title="Sign in - " + cfg.brand, status=status)

    def shell(who: dict) -> HTMLResponse:
        brand, name = html.escape(cfg.brand), html.escape(who.get("name") or who["user"])
        body = ('<div class="vn-shell" id="vn-shell">'
                '<aside class="vn-side" id="vn-side" aria-label="Main">'
                '<div class="vn-side-head"><span class="vn-brand">%s</span>'
                '<button type="button" class="vn-icon vn-close" id="vn-close" aria-label="Close menu">&times;</button></div>'
                '<nav id="vn-nav" aria-label="Pages"></nav><div id="vn-install"></div>'
                '<div class="vn-side-foot"><span class="vn-user" title="Signed in as %s">%s</span>'
                '<a class="vn-gear" id="vn-gear" href="#" aria-label="Settings" title="Settings" hidden>'
                '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                '<circle cx="12" cy="12" r="3.2"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg></a>'
                '<form class="vn-out" id="vn-out" method="post" action="/logout"><button type="submit">Sign out</button></form></div></aside>'
                '<div class="vn-scrim" id="vn-scrim"></div>'
                '<div class="vn-content"><header class="vn-bar"><button type="button" class="vn-icon" id="vn-menu" aria-label="Open menu" '
                'aria-controls="vn-side" aria-expanded="false">&#9776;</button><span class="vn-brand">%s</span></header>'
                '<main id="vn-root"></main></div></div>'
                '<script src="/static/vendor/react.production.min.js"></script>'
                '<script src="/static/vendor/react-dom.production.min.js"></script>'
                '<script src="/shell-config.js"></script><script src="/static/shim.js"></script>'
                % (brand, html.escape(who["user"]), name, brand))
        return page_doc(body, cfg.brand, shell=True, theme=theme_of(who))

    def allowed_pages(who: dict) -> list[dict]:
        return [p for p in pages if cfg.allows(who["role"], p["permission"])]

    def deny(who, permission: str):
        """None if allowed. Otherwise the right refusal: sign in first, or 'not for your role'."""
        if not who:
            return JSONResponse({"detail": "Not signed in."}, status_code=401)
        if not cfg.allows(who["role"], permission):
            return JSONResponse({"detail": "Not allowed for your role."}, status_code=403)
        return None

    def guard(request: Request, permission: str, *, write: bool = False, plain_origin: bool = False, also=None):
        """The checks every signed-in route needs, in one place and always in the same order:
        signed in -> role -> ``also(who)`` (for example "is this your run") -> same origin (when the route changes something).
        Returns (who, None) when the request may go on, or (None, the refusal to send back)."""
        who = current(request)
        refused = deny(who, permission) or (also(who) if also else None)
        if not refused and write and not same_origin(request):
            refused = PlainTextResponse("Bad origin.", status_code=403) if plain_origin else JSONResponse({"detail": "Bad origin."}, status_code=403)
        return (None, refused) if refused else (who, None)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"      # "no-referrer" would make browsers send "Origin: null"
        resp.headers["X-Frame-Options"] = "DENY"
        # strict by default; audio and images made in the browser (recording, read-aloud, picture previews, the 3D stage's textures) are the only extras.
        # 'wasm-unsafe-eval' lets the 3D stage's model decoder (WebAssembly) run; it does not allow eval() or inline scripts. Inline styles are
        # allowed because the terminal library sets them (styles cannot run code).
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' blob:; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
                                                   "frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        if not request.url.path.startswith(("/static/", "/instance/")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---- open routes -------------------------------------------------------------------------------------------
    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    def serve_from(root: Path, path: str):
        target = (root / path).resolve()
        if root not in target.parents or not target.is_file():
            return PlainTextResponse("Not found.", status_code=404)
        ctype = "text/javascript" if target.suffix == ".js" else (mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        return FileResponse(target, media_type=ctype, headers={"Cache-Control": "no-cache"})

    @app.get("/static/{path:path}")
    async def static_file(path: str):
        return serve_from(STATIC, path)

    @app.get("/instance/theme.css")
    async def instance_theme():
        css = theme_file.read_text(encoding="utf-8") if theme_file.is_file() else "/* no instance theme */"
        return Response(css, media_type="text/css", headers={"Cache-Control": "no-cache"})

    @app.get("/instance/static/{path:path}")
    async def instance_static_file(path: str):
        return serve_from(instance_static, path)

    # ---- login ---------------------------------------------------------------------------------------------------
    first_run = firstrun.mount(app, cfg, users, throttle, page_doc, same_origin, client_address)     # /setup: the first owner, made in the browser

    @app.get("/login")
    async def login_get(request: Request):
        if first_run.open():                      # a brand-new install has nobody to sign in yet
            return RedirectResponse("/setup", status_code=303)
        return RedirectResponse("/", status_code=303) if current(request) else login_page()

    @app.post("/login")
    async def login_post(request: Request):
        if not same_origin(request):
            return PlainTextResponse("Bad origin.", status_code=403)
        ip = client_address(request)
        body = (await request.body())[:4096].decode("utf-8", "replace")
        form = parse_qs(body)
        username = ((form.get("username") or [""])[0]).strip().lower()[:64]
        password = (form.get("password") or [""])[0]
        keys = ["ip:" + ip, "user:" + username]
        left = max(throttle.locked(k) for k in keys)
        if left:
            return login_page("Too many wrong passwords. Try again in %d minutes." % ((left + 59) // 60), 429)
        user = users.get(username) if username else None
        stored = user["hash"] if user else _DUMMY_HASH
        # the slow password check runs in a worker thread so one login never freezes every other request
        good = bool(password) and await asyncio.to_thread(verify_password, password, stored)
        if not (good and user and cfg.valid_role(user["role"])):
            for k in keys:
                throttle.failed(k)
            await asyncio.sleep(0.4)
            return login_page("Wrong username or password.", 401)
        for k in keys:
            throttle.ok(k)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(cookie, sessions.create(user["username"], hashlib.sha256(stored.encode()).hexdigest()), max_age=30 * 24 * 3600, path="/",
                        secure=secure_cookie, httponly=True, samesite="strict")
        return resp

    @app.post("/logout")
    async def logout(request: Request):
        if not same_origin(request):
            return PlainTextResponse("Bad origin.", status_code=403)
        sessions.delete(request.cookies.get(cookie))
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(cookie, path="/", secure=secure_cookie, httponly=True, samesite="strict")
        return resp

    # ---- signed-in routes ----------------------------------------------------------------------------------------
    @app.get("/api/me")
    async def me(request: Request):
        who, refused = guard(request, "use")
        return refused or {"user": who["user"], "role": who["role"], "name": who["name"]}

    def _clean_look(raw):
        """The saved avatar look: a flat dict of short settings. Anything else is refused."""
        if not isinstance(raw, dict) or len(raw) > 40:
            return None
        out = {}
        for k, v in raw.items():
            if not isinstance(k, str) or not re.fullmatch(r"[A-Za-z]{1,24}", k):
                return None
            if isinstance(v, bool) or isinstance(v, (int, float)):
                out[k] = v
            elif isinstance(v, str) and len(v) <= 40:
                out[k] = v
            else:
                return None
        return out

    @app.get("/api/avatar-look")
    async def avatar_look_get(request: Request):
        """How the agent looks (one look for everyone, set by an owner). {"look": null} until one is saved."""
        _, refused = guard(request, "use")
        if refused:
            return refused
        try:
            data = json.loads((cfg.data_dir / "avatar-look.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        return {"look": _clean_look(data)}

    @app.put("/api/avatar-look")
    async def avatar_look_put(request: Request):
        _, refused = guard(request, "admin", write=True, plain_origin=True)
        if refused:
            return refused
        try:
            look = _clean_look((await request.json()).get("look"))
        except Exception:
            look = None
        if look is None:
            return JSONResponse({"detail": "That look is not valid."}, status_code=422)
        atomic_write_text(cfg.data_dir / "avatar-look.json", json.dumps(look))
        return {"ok": True}

    @app.get("/api/themes")
    async def themes_get(request: Request):
        """The color themes on offer and the one this person has picked."""
        who, refused = guard(request, "use")
        if refused:
            return refused
        return {"themes": themesmod.THEMES, "current": theme_of(who), "default": themesmod.DEFAULT}

    @app.put("/api/theme")
    async def theme_put(request: Request):
        """Save this person's own theme (everyone may choose theirs; it changes nothing for anyone else)."""
        who, refused = guard(request, "use", write=True, plain_origin=True)
        if refused:
            return refused
        try:
            theme = themesmod.clean((await request.json()).get("theme"))
        except Exception:
            theme = None
        if theme is None:
            return JSONResponse({"detail": "That theme does not exist."}, status_code=422)
        users.set_theme(who["user"], theme)
        return {"ok": True, "current": theme}

    @app.get("/api/health")
    async def health(request: Request):
        """Yes/no answers about the connections to Hermes. Owners only. Never returns a secret."""
        _, refused = guard(request, "admin")
        return refused or await hermes.health()

    @app.get("/api/overview")
    async def overview_route(request: Request):
        """Everything the Home page shows. Infrastructure parts are included only for roles that may see them."""
        who, refused = guard(request, "use")
        return refused or await overview.home(infra=cfg.allows(who["role"], "admin"), personal=cfg.allows(who["role"], "admin"))

    @app.get("/api/tool-activity")
    async def tool_activity_route(request: Request):
        """The agent's latest tool call (kind, name, time), for the glowing tool icons. Owners only; never any arguments."""
        _, refused = guard(request, "admin")
        return refused or await overview.tool_activity()

    @app.get("/api/systems")
    async def systems_route(request: Request):
        _, refused = guard(request, "admin")
        return refused or await overview.systems_page()

    # ---- asking the agent: start a run, watch it, stop it, answer an approval (one at a time per person) -------------
    RUN_ID = RUN_ID_RE
    TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
    last_run: dict[str, dict] = {}                     # username -> {"id": run id, "at": monotonic time}
    owner_of: dict[str, str] = {}                      # run id -> username (bounded)

    def own_run(who, run_id: str):
        """Refusal unless this run is the person's own (owners may look at any run)."""
        if not RUN_ID.match(run_id or ""):
            return JSONResponse({"detail": "Invalid run id."}, status_code=400)
        if owner_of.get(run_id) != who["user"] and not cfg.allows(who["role"], "admin"):
            return JSONResponse({"detail": "Not your run."}, status_code=403)
        return None

    def public_run(run: dict) -> dict:
        err = run.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        return {"run_id": run.get("run_id"), "status": run.get("status"), "output": run.get("output") if isinstance(run.get("output"), str) else None,
                "execution_generation": run.get("execution_generation"), "error": err[:400] if isinstance(err, str) else None,
                "pending_controls": run.get("pending_controls") if isinstance(run.get("pending_controls"), list) else []}

    @app.post("/api/ask")
    async def ask(request: Request):
        who, refused = guard(request, "use", write=True)
        if refused:
            return refused
        try:
            message = str((await request.json()).get("message", "")).strip()
        except ValueError:
            message = ""
        if not message:
            return JSONResponse({"detail": "Type a question first."}, status_code=400)
        if len(message) > 8000:
            return JSONResponse({"detail": "That message is too long."}, status_code=400)
        prev = last_run.get(who["user"])
        if prev:
            if time.monotonic() - prev["at"] < 1.0:
                return JSONResponse({"detail": "Slow down a moment."}, status_code=429)
            status, run = await hermes.api_call("GET", "/v1/runs/" + prev["id"])
            if status == 200 and run and run.get("status") not in TERMINAL:
                return JSONResponse({"detail": "%s is still working on your last question." % cfg.agent_name}, status_code=409)
        payload = {"input": message, "session_id": "ask-" + who["user"]}
        payload.update(chat_model.override(who["user"]))
        status, run = await hermes.api_call("POST", "/v1/runs", payload)
        rid = run.get("run_id") if isinstance(run, dict) else None
        if status not in (200, 201, 202) or not isinstance(rid, str) or not RUN_ID.match(rid):
            return JSONResponse({"detail": "Could not reach %s right now." % cfg.agent_name}, status_code=502)
        last_run[who["user"]] = {"id": rid, "at": time.monotonic()}
        owner_of[rid] = who["user"]
        if len(owner_of) > 500:
            owner_of.pop(next(iter(owner_of)))
        return {"run_id": rid, "status": run.get("status", "started")}

    @app.get("/api/runs/{run_id}")
    async def run_get(run_id: str, request: Request):
        _, refused = guard(request, "use", also=lambda who: own_run(who, run_id))
        if refused:
            return refused
        status, run = await hermes.api_call("GET", "/v1/runs/" + run_id)
        return public_run(run) if status == 200 and isinstance(run, dict) else JSONResponse({"detail": "Run not found."}, status_code=404)

    @app.post("/api/runs/{run_id}/stop")
    async def run_stop(run_id: str, request: Request):
        _, refused = guard(request, "use", write=True, also=lambda who: own_run(who, run_id))
        if refused:
            return refused
        status, res = await hermes.api_call("POST", "/v1/runs/" + run_id + "/stop", {})
        return {"ok": status == 200, "status": (res or {}).get("status")}

    @app.post("/api/runs/{run_id}/approval")
    async def run_approval(run_id: str, request: Request):
        _, refused = guard(request, "use", write=True, also=lambda who: own_run(who, run_id))
        if refused:
            return refused
        try:
            body = await request.json()
        except ValueError:
            body = {}
        rid, choice = str(body.get("request_id", ""))[:200], body.get("choice")
        if not rid or choice not in ("once", "deny"):
            return JSONResponse({"detail": "Bad approval."}, status_code=400)
        payload = {"request_id": rid, "choice": choice}
        if isinstance(body.get("execution_generation"), int):
            payload["execution_generation"] = body["execution_generation"]
        status, res = await hermes.api_call("POST", "/v1/runs/" + run_id + "/approval", payload)
        return {"ok": status == 200, "status": (res or {}).get("status")}

    @app.get("/api/gate-check")
    async def gate_check(request: Request):
        """For the approval service: 200 only if the session cookie it replays belongs to someone who may approve (owners)."""
        who, refused = guard(request, "admin")
        return refused or {"ok": True, "user": who["user"]}

    @app.get("/shell-config.js")
    async def shell_config(request: Request):
        who = current(request)
        if not who:
            return PlainTextResponse("", status_code=401)
        data = {"brand": cfg.brand, "agentName": cfg.agent_name, "user": who["user"], "name": who["name"], "role": who["role"],
                "navGroups": cfg.nav_groups,
                "accordionNav": cfg.accordion_nav,
                "avatarPresets": cfg.avatar_presets,
                "navIcons": cfg.nav_icons,
                "pwa": ({"manifest": CHAT_PREFIX + "/manifest.webmanifest", "sw": CHAT_PREFIX + "/sw.js", "icon": CHAT_PREFIX + "/icon-192.png"} if cfg.push_enabled else None),
                "pages": [{k: p[k] for k in ("id", "path", "label", "plugin", "scripts", "styles", "group", "subgroup", "order", "nav", "icon")} for p in allowed_pages(who)]}
        return Response("window.__APP_CONFIG__=%s;" % json.dumps(data), media_type="text/javascript")

    @app.get("/")
    async def home(request: Request):
        who = current(request)
        return shell(who) if who else RedirectResponse("/login", status_code=303)

    def add_page(page: dict):
        async def page_route(request: Request):
            who = current(request)
            if not who:
                return RedirectResponse("/login", status_code=303)
            if not cfg.allows(who["role"], page["permission"]):
                return page_doc('<main class="card"><h1>Not available</h1><p class="sub">This page is not part of your role.</p>'
                                '<p><a href="/">Back to the start</a></p></main>', "Not available", status=403)
            return shell(who)
        app.add_api_route(page["path"], page_route, methods=["GET"], response_class=HTMLResponse)

    def add_alias(alias: str, target: str):
        async def alias_route(request: Request):
            return RedirectResponse(target + ("?" + request.url.query if request.url.query else ""), status_code=303)
        app.add_api_route(alias, alias_route, methods=["GET"], include_in_schema=False)

    for pg in pages:
        if pg["path"] != "/":
            add_page(pg)
        for alias in pg.get("aliases", []):
            add_alias(alias, pg["path"])        # old addresses (such as links in earlier notifications) land on the page

    taken = {pg["path"] for pg in pages} | {a for pg in pages for a in pg.get("aliases", [])}
    for old, new in cfg.redirects.items():
        if old not in taken and old != "/" and not old.startswith("//") and not new.startswith("//"):
            add_alias(old, new)                  # an install's own list of old addresses (the instance file's [redirects])

    app.state.chat_owners = mount_chat(app, cfg, hermes, current, same_origin)
    mount_code(app, cfg, hermes, current, same_origin, app.state.chat_owners)
    mount_owner_pages(app, cfg, hermes, current, same_origin)
    app.state.users = users
    app.state.sessions = sessions
    app.state.config = cfg
    return app
