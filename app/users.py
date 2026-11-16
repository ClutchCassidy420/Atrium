"""The user list: who may sign in and in what role. A small JSON file in the app's data folder (mode 600).

Passwords are stored only as scrypt hashes (see auth.py). The list is read fresh on every request, so removing a user
or changing a role takes effect at once, even for people who are already signed in.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from auth import hash_looks_valid, hash_password

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
MIN_PASSWORD = 12


class UserError(ValueError):
    pass


class UserStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, users: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(users, indent=1, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    # -- reads ------------------------------------------------------------------------------------------------
    def get(self, username: str) -> dict | None:
        u = self._load().get((username or "").lower())
        return dict(u, username=(username or "").lower()) if u else None

    def all(self) -> list[dict]:
        return [dict(v, username=k) for k, v in sorted(self._load().items())]

    def empty(self) -> bool:
        return not self._load()

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
