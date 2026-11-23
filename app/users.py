"""The user list: who may sign in and in what role. A small JSON file in the app's data folder (mode 600).

Passwords are stored only as scrypt hashes (see auth.py). Reads notice a changed file at once (its modification stamp is
checked on every request, and the file is parsed again only when the stamp changed), so removing a user or changing a
role takes effect at once, even for people who are already signed in. Writes always start from the file itself.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from auth import hash_looks_valid, hash_password
from agentkit import atomic_write_text

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
MIN_PASSWORD = 12


class UserError(ValueError):
    pass


class UserStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self._cache: tuple[tuple | None, dict] | None = None      # (file stamp, parsed users), used by reads only

    def _stamp(self) -> tuple | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def _load(self) -> dict:
        """The users as they are on disk right now (a fresh copy: callers that write may change it)."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _read(self) -> dict:
        """For reads on every request: the parsed file, parsed again only when the file changed. Never modify the result."""
        stamp = self._stamp()
        cached = self._cache
        if stamp is not None and cached is not None and cached[0] == stamp:
            return cached[1]
        data = self._load()
        self._cache = (stamp, data) if stamp is not None else None
        return data

    def _save(self, users: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(users, indent=1, sort_keys=True), 0o600)

    # -- reads ------------------------------------------------------------------------------------------------
    def get(self, username: str) -> dict | None:
        u = self._read().get((username or "").lower())
        return dict(u, username=(username or "").lower()) if u else None

    def all(self) -> list[dict]:
        return [dict(v, username=k) for k, v in sorted(self._read().items())]

    def empty(self) -> bool:
        return not self._read()

    # -- writes -----------------------------------------------------------------------------------------------
    def add(self, username: str, role: str, password: str | None = None, password_hash: str | None = None, name: str = "") -> None:
        username = (username or "").strip().lower()
        if not USERNAME_RE.match(username):
            raise UserError("A username is 2 to 32 characters: lowercase letters, digits, dot, dash, underscore.")
        if password_hash is None:
            if not password or len(password) < MIN_PASSWORD:
                raise UserError("A password needs at least %d characters." % MIN_PASSWORD)
            password_hash = hash_password(password)
        elif not hash_looks_valid(password_hash):
            raise UserError("That password hash is damaged.")
        with self.lock:
            users = self._load()
            if username in users:
                raise UserError("That user already exists.")
            users[username] = {"role": role, "hash": password_hash, "created": int(time.time()), "name": (name or "").strip()[:40]}
            self._save(users)

    def set_password(self, username: str, password: str) -> None:
        if not password or len(password) < MIN_PASSWORD:
            raise UserError("A password needs at least %d characters." % MIN_PASSWORD)
        with self.lock:
            users = self._load()
            u = users.get(username.lower())
            if not u:
                raise UserError("No such user.")
            u["hash"] = hash_password(password)
            self._save(users)

    def set_name(self, username: str, name: str) -> None:
        with self.lock:
            users = self._load()
            if username.lower() not in users:
                raise UserError("No such user.")
            users[username.lower()]["name"] = (name or "").strip()[:40]
            self._save(users)

    def set_theme(self, username: str, theme: str) -> None:
        """The color theme this person picked (an id from themes.THEMES; the caller validates it)."""
        with self.lock:
            users = self._load()
            if username.lower() not in users:
                raise UserError("No such user.")
            users[username.lower()]["theme"] = theme
            self._save(users)

    def set_role(self, username: str, role: str) -> None:
        with self.lock:
            users = self._load()
            if username.lower() not in users:
                raise UserError("No such user.")
            users[username.lower()]["role"] = role
            self._save(users)

    def remove(self, username: str) -> None:
        with self.lock:
            users = self._load()
            if users.pop(username.lower(), None) is None:
                raise UserError("No such user.")
            self._save(users)
