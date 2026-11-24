"""Per-person media server connections for the Media page (Jellyfin and Emby).

A person connects with the server's web address and their own user name and password on that server. The password is used once, to ask the
server for an access token, and is never saved. The token is encrypted on disk (AES-GCM, bound to the person and the connection), is never
sent back to the browser, and goes when the connection is removed or the person is. The key comes from MAIL_ACCOUNTS_KEY (the same private
setting as saved mailbox logins, derived separately) when that is set; otherwise a key file is made in the data folder (mode 600).

Because a person types the server's address, Atrium must not become a way to probe its own network. By default a server must be reached over
https at a public DNS name that resolves only to public addresses, and the connection goes to that checked address. An owner may also reach
a server on the local network (http, an IP address, or a one-word name) when the install turns on MEDIA_ALLOW_PRIVATE; loopback, link-local and
other special addresses are never allowed, and other people never get this.
"""
from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from agentkit import atomic_write_text
import mail_accounts as ma

KINDS = {"jellyfin": "Jellyfin", "emby": "Emby"}
MAX_SERVERS = 8
ATTEMPTS = 10                         # connection attempts per person per window
WINDOW = 600
_PATH = re.compile(r"^(/[A-Za-z0-9._~-]{1,40}){0,3}$")
_NAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


class MediaError(Exception):
    """A problem the person can fix (or a server that said no); the message is safe to show."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def clean_url(value, private_ok: bool) -> str:
    """The server's address in one tidy form: scheme://host[:port][/path]. Raises MediaError for anything not allowed."""
    text = str(value or "").strip()
    if "://" not in text:
        text = ("http://" if private_ok and not text.startswith("https") else "https://") + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise MediaError("That does not look like a web address.") from None
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower().rstrip(".")
    if scheme not in ("http", "https") or not host or parts.username or parts.password or parts.query or parts.fragment:
        raise MediaError("That does not look like a web address. Use something like https://media.example.com.")
    path = parts.path.rstrip("/")
    if not _PATH.match(path) or any(seg.strip(".") == "" for seg in path.split("/")[1:]):
        raise MediaError("That web address has a path that cannot be used.")
    if private_ok:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not _NAME.match(host):
                raise MediaError("That does not look like a web address.") from None
    else:
        if scheme != "https":
            raise MediaError("Use an https address. (Only the owner can connect a server on the local network.)")
        try:
            ma.clean_host(host)
        except ma.AccountError:
            raise MediaError("Use the server's public name, such as https://media.example.com (not an IP address or an internal name).") from None
    shown = "[%s]" % host if ":" in host else host
    return "%s://%s%s%s" % (scheme, shown, ":%d" % port if port else "", path)


def resolve(host: str, port: int, private_ok: bool) -> str:
    """The one address to connect to for a host. Public servers must resolve only to public addresses; the local network is only for owners
    on installs that allow it, and even then never loopback, link-local or other special addresses."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise MediaError("Could not find that server. Check its address.") from None
    ips = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            continue
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise MediaError("That address is not allowed.")
        if not ip.is_global and not private_ok:
            raise MediaError("That server's name points to a private or internal address, which is not allowed.")
        ips.append(ip)
    if not ips:
        raise MediaError("Could not find that server. Check its address.")
    ips.sort(key=lambda ip: ip.version)
    return str(ips[0])


class Servers:
    """The encrypted store: <data>/media-servers.json, keyed by user name."""

    def __init__(self, data_dir: Path, key: str = ""):
        self.path = Path(data_dir) / "media-servers.json"
        self.keyfile = Path(data_dir) / "media-servers.key"
        self._env_key = self._derive(key) if key else None
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}

    @staticmethod
    def _derive(secret: str) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"atrium-media-servers-v1", info=b"aes-gcm key").derive(secret.encode("utf-8"))

    def _key(self) -> bytes:
        if self._env_key is not None:
            return self._env_key
        try:
            key = self.keyfile.read_bytes()
            if len(key) == 32:
                return key
        except FileNotFoundError:
            pass
        key = secrets.token_bytes(32)
        self.keyfile.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.keyfile), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return self.keyfile.read_bytes()
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
        return key

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(data, indent=1, sort_keys=True), mode=0o600)

    def _aad(self, user: str, sid: str, url: str) -> bytes:
        return ("%s\0%s\0%s" % (user, sid, url)).encode("utf-8")

    def _seal(self, user: str, sid: str, url: str, token: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = secrets.token_bytes(12)
        return base64.b64encode(nonce + AESGCM(self._key()).encrypt(nonce, token.encode("utf-8"), self._aad(user, sid, url))).decode()

    def _open(self, user: str, sid: str, url: str, sealed: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = base64.b64decode(sealed)
        return AESGCM(self._key()).decrypt(raw[:12], raw[12:], self._aad(user, sid, url)).decode("utf-8")

    # -- what the routes use ---------------------------------------------------------------------------------------
    def allow_attempt(self, user: str) -> bool:
        with self._lock:
            now = time.time()
            recent = [t for t in self._attempts.get(user, []) if now - t < WINDOW]
            if len(recent) >= ATTEMPTS:
                self._attempts[user] = recent
                return False
            recent.append(now)
            self._attempts[user] = recent
            return True

    def list(self, user: str) -> list[dict]:
        """The person's connections for showing: never the token."""
        with self._lock:
            return [{k: s[k] for k in ("id", "kind", "label", "url", "user_name")} for s in self._load().get(user, [])]

    def get(self, user: str, sid: str) -> Optional[dict]:
        """One connection with its token opened, for making requests."""
        with self._lock:
            for s in self._load().get(user, []):
                if s["id"] == sid:
                    try:
                        return dict(s, token=self._open(user, s["id"], s["url"], s["sealed"]))
                    except Exception:
                        return None
        return None

    def add(self, user: str, kind: str, url: str, label: str, user_id: str, user_name: str, token: str) -> dict:
        if kind not in KINDS:
            raise MediaError("Choose Jellyfin or Emby.")
        with self._lock:
            data = self._load()
            mine = data.get(user, [])
            if len(mine) >= MAX_SERVERS:
                raise MediaError("You can connect up to %d servers. Remove one first." % MAX_SERVERS)
            if any(s["url"] == url and s["user_id"] == user_id for s in mine):
                raise MediaError("You are already connected to that server as that user.", 409)
            sid = secrets.token_hex(4)
            rec = {"id": sid, "kind": kind, "label": (label or KINDS[kind]).strip()[:60], "url": url, "user_id": user_id,
                   "user_name": user_name[:80], "sealed": self._seal(user, sid, url, token)}
            data[user] = mine + [rec]
            self._save(data)
            return {k: rec[k] for k in ("id", "kind", "label", "url", "user_name")}

    def remove(self, user: str, sid: str) -> bool:
        with self._lock:
            data = self._load()
            mine = data.get(user, [])
            kept = [s for s in mine if s["id"] != sid]
            if len(kept) == len(mine):
                return False
            if kept:
                data[user] = kept
            else:
                data.pop(user, None)
            self._save(data)
            return True

    def purge(self, keep) -> int:
        """Drop the connections of people who no longer exist."""
        keep = set(keep)
        with self._lock:
            data = self._load()
            gone = [u for u in data if u not in keep]
            for u in gone:
                del data[u]
            if gone:
                self._save(data)
            return len(gone)
