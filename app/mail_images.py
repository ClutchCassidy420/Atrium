"""Remote images in mail: shown only when the person asks, and only through Atrium, never straight from the sender's server.

Mail is cleaned with every remote image blocked, so a sender cannot tell when (or from where) a message is read. A person can choose to
show the images of one message, or to always show them for a sender they trust. Either way the page never contacts the sender: the cleaned
message points each image at a signed link on this server, and this server fetches it (/api/mail-image/<token>). That keeps the page's
security policy unchanged (images only from this site), hides the person's address and browser from the sender, and lets the server refuse
anything that is not a plain picture. The sender can still tell that the message was opened, which the page says.

The signed link names the person and the exact address, and expires, so the route is not an open proxy: it fetches only images that were in a
message that person opened. Fetching follows the same rule as personal mailboxes: a public DNS name, every address it resolves to public, the
connection pinned to the checked address with the certificate verified, port 443 only, and redirects re-checked at each step.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlsplit

import mail_accounts as ma
from agentkit import atomic_write_text

TOKEN_TTL = 3600                 # a signed image link works for an hour
MAX_URL = 2000
MAX_IMAGE = 5 * 1024 * 1024
MAX_REDIRECTS = 3
FETCH_TIMEOUT = 10
MAX_SAFE = 500                   # safe senders one person may keep
USER_AGENT = "Mozilla/5.0 (compatible; Atrium image proxy)"


class ImageError(Exception):
    """Why an image was not served: an HTTP status and a short message that is safe to show."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------------------------------------------------------------- signed links

class Signer:
    """Signs and checks the links the cleaned message uses for its images. The key sits in <data>/mail-images.key (mode 600); it only proves a
    link came from this server, so it protects no secret."""

    def __init__(self, data_dir: Path):
        self.keyfile = Path(data_dir) / "mail-images.key"
        self._key_cache: Optional[bytes] = None

    def _key(self) -> bytes:
        if self._key_cache is None:
            try:
                key = self.keyfile.read_bytes()
            except FileNotFoundError:
                key = b""
            if len(key) != 32:
                key = secrets.token_bytes(32)
                self.keyfile.parent.mkdir(parents=True, exist_ok=True)
                try:
                    fd = os.open(str(self.keyfile), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(key)
                except FileExistsError:                       # another worker made it first
                    key = self.keyfile.read_bytes()
            self._key_cache = key
        return self._key_cache

    def _mac(self, body: str) -> str:
        return _b64(hmac.new(self._key(), body.encode(), hashlib.sha256).digest()[:16])

    def sign(self, user: str, url: str, ttl: int = TOKEN_TTL) -> str:
        body = _b64(json.dumps({"u": url, "p": user.lower(), "e": int(time.time()) + ttl}, separators=(",", ":")).encode())
        return body + "." + self._mac(body)

    def verify(self, token: str) -> Optional[dict]:
        try:
            body, mac = token.split(".", 1)
            if not hmac.compare_digest(mac, self._mac(body)):
                return None
            claims = json.loads(_unb64(body))
            if not isinstance(claims, dict) or int(claims["e"]) < time.time() or not isinstance(claims["u"], str):
                return None
            return claims
        except (ValueError, KeyError, TypeError):
            return None


def proxied(signer: Signer, user: str, url: str) -> Optional[str]:
    """The link a cleaned message uses for one remote image, or None when it cannot be shown (not a web address, too long)."""
    url = url.strip()
    if len(url) > MAX_URL or not re.match(r"^https?://", url, re.I):
        return None
    if url[:7].lower() == "http://":                          # try the secure address; a server that has none simply shows no image
        url = "https://" + url[7:]
    return "/api/mail-image/" + signer.sign(user, url)


# ---------------------------------------------------------------- fetching

class _Pinned(http.client.HTTPSConnection):
    """HTTPS to one checked address, with the certificate verified against the name in the web address."""

    def __init__(self, host: str, ip: str):
        super().__init__(host, 443, timeout=FETCH_TIMEOUT, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        raw = socket.create_connection((self._ip, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _request(host: str, ip: str, target: str):
    """One GET. Returns (status, lowercase headers, body up to MAX_IMAGE). No cookies, no referrer, no compression."""
    conn = _Pinned(host, ip)
    try:
        conn.request("GET", target, headers={"User-Agent": USER_AGENT, "Accept": "image/*", "Accept-Encoding": "identity", "Connection": "close"})
        response = conn.getresponse()
        headers = {k.lower(): v for k, v in response.getheaders()}
        if response.status != 200:
            return response.status, headers, b""
        try:
            declared = int(headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > MAX_IMAGE:
            raise ImageError(413, "That image is too large.")
        body = response.read(MAX_IMAGE + 1)
        if len(body) > MAX_IMAGE:
            raise ImageError(413, "That image is too large.")
        return 200, headers, body
    except ImageError:
        raise
    except (OSError, http.client.HTTPException, ssl.SSLError, ValueError):
        raise ImageError(502, "Could not load that image.") from None
    finally:
        conn.close()


def sniff(data: bytes) -> Optional[str]:
    """What a file really is, from its first bytes. Only plain pictures: never SVG, which can carry script."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM" and len(data) > 14:
        return "image/bmp"
    return None


def fetch(url: str, resolver=None) -> tuple[str, bytes]:
    """Fetch one remote image safely. Returns (content type, bytes) or raises ImageError."""
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.username is not None or parts.password is not None or parts.port not in (None, 443):
            raise ImageError(400, "That image address is not allowed.")
        try:
            host = ma.clean_host(parts.hostname or "")
            ip = (resolver or ma.resolve_public)(host, 443)
        except ma.AccountError:
            raise ImageError(400, "That image address is not allowed.") from None
        target = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        status, headers, body = _request(host, ip, target)
        if status in (301, 302, 303, 307, 308):
            location = headers.get("location", "")
            if not location:
                raise ImageError(502, "Could not load that image.")
            url = urljoin(url, location)
            continue
        if status != 200:
            raise ImageError(404, "That image is not available.")
        kind = sniff(body)
        if kind is None:
            raise ImageError(415, "That is not a picture Atrium will show.")
        return kind, body
    raise ImageError(502, "That image redirects too many times.")


# ---------------------------------------------------------------- senders a person trusts

class SafeSenders:
    """<data>/mail-safe-senders.json: for each person, the sender addresses whose images are always shown. Addresses only (a display name proves
    nothing), lowercase, at most MAX_SAFE each."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "mail-safe-senders.json"
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(data, indent=1, sort_keys=True), mode=0o600)

    @staticmethod
    def _clean(address: str) -> str:
        try:
            return ma.clean_address(address).lower()
        except ma.AccountError:
            raise ValueError("That does not look like an email address.") from None

    def list(self, user: str) -> list:
        return sorted(a for a in self._load().get(user.lower(), []) if isinstance(a, str))

    def has(self, user: str, address: str) -> bool:
        return bool(address) and address.lower() in self._load().get(user.lower(), [])

    def add(self, user: str, address: str) -> None:
        address = self._clean(address)
        with self._lock:
            data = self._load()
            mine = [a for a in data.get(user.lower(), []) if isinstance(a, str)]
            if address in mine:
                return
            if len(mine) >= MAX_SAFE:
                raise ValueError("You can keep up to %d trusted senders. Remove some first." % MAX_SAFE)
            data[user.lower()] = sorted(mine + [address])
            self._save(data)

    def remove(self, user: str, address: str) -> bool:
        address = (address or "").strip().lower()
        with self._lock:
            data = self._load()
            mine = [a for a in data.get(user.lower(), []) if isinstance(a, str)]
            if address not in mine:
                return False
            mine.remove(address)
            if mine:
                data[user.lower()] = mine
            else:
                data.pop(user.lower(), None)
            self._save(data)
            return True

    def purge(self, keep) -> int:
        """Forget the lists of people who no longer exist."""
        keep = {k.lower() for k in keep}
        with self._lock:
            data = self._load()
            drop = [u for u in data if u not in keep]
            for u in drop:
                del data[u]
            if drop:
                self._save(data)
            return len(drop)
