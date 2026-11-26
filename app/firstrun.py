"""First run: the owner account is made in the browser, so nobody has to run a command to start using the app.

While no user exists, `/setup` shows a form. A one-time setup code, printed in the container log and saved in the data folder, proves
the person at the form is the one who started the app (a fresh install that is already reachable must not let the first stranger
claim it). Once the owner exists the code file is removed and `/setup` stops answering.
"""
from __future__ import annotations

import hmac
import html
import secrets
import threading
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from users import MIN_PASSWORD, UserError

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no 0/O or 1/I to mix up when copying from a log


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(12))        # 60 bits: too many to guess even from many addresses
    return raw[:4] + "-" + raw[4:8] + "-" + raw[8:]


def _norm(text: str) -> str:
    return "".join(ch for ch in (text or "").upper() if ch.isalnum())


class FirstRun:
    def __init__(self, data_dir, users, announce=print):
        self.path = data_dir / "setup-code.txt"
        self.users = users
        self.announce = announce
        self.lock = threading.Lock()

    def open(self) -> bool:
        """True while the app still needs its first owner."""
        return self.users.empty()

    def code(self) -> str | None:
        """The setup code while the app needs its owner (made and announced once), else None. The file is removed when no longer needed."""
        with self.lock:
            if not self.open():
                try:
                    self.path.unlink()
                except OSError:
                    pass
                return None
            try:
                found = self.path.read_text(encoding="utf-8").strip()
                if found:
                    return found
            except OSError:
                pass
            code = new_code()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(code + "\n", encoding="utf-8")
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
            self.announce("\n  First start: open this app in your browser and go to /setup\n  Setup code: %s\n" % code, flush=True)
            return code

    def create_owner(self, supplied_code: str, username: str, password: str, again: str) -> None:
        """Make the first owner. Raises UserError with a message fit to show."""
        with self.lock:
            if not self.open():
                raise UserError("This app already has an owner. Sign in instead.")
            expected = self.code_locked()
            if not (expected and hmac.compare_digest(_norm(supplied_code).encode(), _norm(expected).encode())):
                raise PermissionError("That setup code is not right. It is shown in the app's log (docker compose logs).")
            if password != again:
                raise UserError("The two passwords are not the same.")
            self.users.add(username, "owner", password=password)
            try:
                self.path.unlink()
            except OSError:
                pass

    def code_locked(self) -> str | None:
        try:
            return self.path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None


def mount(app, cfg, users, throttle, page_doc, same_origin, client_address) -> FirstRun:
    first = FirstRun(cfg.data_dir, users)
    if first.open():
        first.code()                                      # make and announce the code now, so it is in the log from the start

    def form(error: str = "", status: int = 200, username: str = "") -> HTMLResponse:
        err = '<p class="err" role="alert">%s</p>' % html.escape(error) if error else ""
        return page_doc(
            '<main class="card"><h1>%s</h1><p class="sub">Welcome. Make the owner account to get started.</p>%s'
            '<form method="post" action="/setup">'
            '<label for="sc">Setup code</label>'
            '<input id="sc" name="code" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="XXXX-XXXX-XXXX" required autofocus>'
            '<p class="sub">Shown in the app\'s log when it started: <code>docker compose logs</code></p>'
            '<label for="un">Your username</label>'
            '<input id="un" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" value="%s" required>'
            '<label for="pw">Password (at least %d characters)</label>'
            '<input id="pw" name="password" type="password" autocomplete="new-password" minlength="%d" required>'
            '<label for="pw2">Password again</label>'
            '<input id="pw2" name="again" type="password" autocomplete="new-password" minlength="%d" required>'
            '<button type="submit">Create the owner account</button></form></main>'
            % (html.escape(cfg.brand), err, html.escape(username), MIN_PASSWORD, MIN_PASSWORD, MIN_PASSWORD),
            title="Set up - " + cfg.brand, status=status)

    @app.get("/setup")
    async def setup_get():
        if not first.open():
            return RedirectResponse("/login", status_code=303)
        first.code()
        return form()

    @app.post("/setup")
    async def setup_post(request: Request):
        if not same_origin(request):
            return PlainTextResponse("Bad origin.", status_code=403)
        if not first.open():
            return RedirectResponse("/login", status_code=303)
        key = "setup:" + client_address(request)
        left = max(throttle.locked(key), throttle.locked("setup:all"))        # one address, and all addresses together
        if left:
            return form("Too many wrong codes. Try again in %d minutes." % ((left + 59) // 60), 429)
        body = (await request.body())[:4096].decode("utf-8", "replace")
        f = parse_qs(body)
        pick = lambda n: (f.get(n) or [""])[0]
        username = pick("username").strip().lower()[:64]
        try:
            first.create_owner(pick("code"), username, pick("password"), pick("again"))
        except PermissionError as exc:
            throttle.failed(key)
            throttle.failed("setup:all", max_fails=50)
            return form(str(exc), 403, username)
        except UserError as exc:
            return form(str(exc), 400, username)
        throttle.ok(key)
        return RedirectResponse("/login", status_code=303)

    return first
