"""Password hashing, server-side sessions and login throttling. Standard library only.

Passwords are stored as scrypt hashes ("scrypt$N$r$p$salt$hash"). The password itself is never logged, stored or
seen by anyone but the person typing it. Sessions are random ids kept server side (only a SHA-256 of each id is
stored), so a session can be revoked and a stolen database does not hand out usable cookies.
"""
from __future__ import annotations

from contextlib import contextmanager
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

LOG = logging.getLogger(__name__)

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
SESSION_IDLE_S = 7 * 24 * 3600        # signed out after 7 days without use
SESSION_MAX_S = 30 * 24 * 3600        # and never longer than 30 days in total
MAX_FAILS = 5                         # wrong passwords before an address is locked out
LOCK_S = 15 * 60


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)
    # Colons, not dollar signs: Docker Compose treats "$" in an env file as a variable reference and eats part of the hash.
    return "scrypt:%d:%d:%d:%s:%s" % (n, r, p, _b64(salt), _b64(h))


def _parts(stored: str):
    sep = ":" if stored.startswith("scrypt:") else "$"
    parts = stored.split(sep)
    return parts if len(parts) == 6 and parts[0] == "scrypt" else None


def hash_looks_valid(stored: str) -> bool:
    """False if the stored hash is empty or damaged (for example, mangled on its way into the container)."""
    return bool(stored) and _parts(stored) is not None


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, want = _parts(stored)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=128 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(got, base64.b64decode(want))
    except Exception:
        return False


class Sessions:
    """Server-side sessions, saved to a small JSON file so a restart does not sign everyone out."""

    def __init__(self, path: Path | None):
        self.path = path
        self.lock = threading.Lock()
        self.items: dict[str, dict] = {}

    @contextmanager
    def _transaction(self):
        """Serialize web and CLI access and reload after acquiring the lock."""
        with self.lock:
            if self.path is None:
                yield
                return
            with self.path.with_suffix(".lock").open("a+b") as handle:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    try:
                        loaded = json.loads(self.path.read_text())
                        self.items = loaded if isinstance(loaded, dict) else {}
                    except FileNotFoundError:
                        self.items = {}
                    except (ValueError, OSError):                 # unreadable or damaged: start empty, as before (people sign in again), never fail every request
                        LOG.warning("The sessions file could not be read; starting with no sessions.")
                        self.items = {}
                    yield
                finally:
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def _key(sid: str) -> str:
        return hashlib.sha256(sid.encode()).hexdigest()

    def _save(self) -> None:
        if not self.path:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def create(self, user: str, credential: str = "") -> str:
        sid = secrets.token_urlsafe(32)
        now = time.time()
        with self._transaction():
            self._purge(now)
            self.items[self._key(sid)] = {"user": user, "created": now, "last": now, "credential": credential}
            self._save()
        return sid

    def _purge(self, now: float) -> None:
        dead = [k for k, v in self.items.items() if now - v["last"] > SESSION_IDLE_S or now - v["created"] > SESSION_MAX_S]
        for k in dead:
            del self.items[k]

    def get(self, sid: str | None) -> dict | None:
        if not sid:
            return None
        now = time.time()
        with self._transaction():
            s = self.items.get(self._key(sid))
            if not s:
                return None
            if now - s["last"] > SESSION_IDLE_S or now - s["created"] > SESSION_MAX_S:
                del self.items[self._key(sid)]
                self._save()
                return None
            if now - s["last"] > 600:          # refresh the idle clock at most every ten minutes
                s["last"] = now
                self._save()
            return dict(s)

    def delete(self, sid: str | None) -> None:
        if not sid:
            return
        with self._transaction():
            if self.items.pop(self._key(sid), None) is not None:
                self._save()

    def delete_user(self, user: str) -> int:
        """Sign a user out everywhere (after a password change or removal). Returns how many sessions ended."""
        with self._transaction():
            dead = [k for k, v in self.items.items() if v.get("user") == user]
            for k in dead:
                del self.items[k]
            if dead:
                self._save()
            return len(dead)

    def delete_all(self) -> None:
        with self._transaction():
            self.items = {}
            self._save()


class Throttle:
    """Counts wrong passwords per address. MAX_FAILS in a row locks that address out for LOCK_S."""

    def __init__(self, max_fails: int = MAX_FAILS, lock_s: int = LOCK_S):
        self.max_fails = max_fails
        self.lock_s = lock_s
        self.lock = threading.Lock()
        self.fails: dict[str, list] = {}   # key -> [count, locked_until]

    def locked(self, who: str) -> int:
        """Seconds left on the lockout, or 0."""
        with self.lock:
            rec = self.fails.get(who)
            if rec and rec[1] > time.time():
                return int(rec[1] - time.time()) + 1
            return 0

    def failed(self, who: str) -> None:
        with self.lock:
            rec = self.fails.setdefault(who, [0, 0.0])
            if rec[1] and rec[1] <= time.time():
                rec[0], rec[1] = 0, 0.0
            rec[0] += 1
            if rec[0] >= self.max_fails:
                rec[1] = time.time() + self.lock_s
                rec[0] = 0

    def ok(self, who: str) -> None:
        with self.lock:
            self.fails.pop(who, None)
