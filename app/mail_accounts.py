"""Per-person mailboxes for the Email pages (switched on with MAIL_USER_ACCOUNTS=1).

Each person connects their own mailbox with an address and an app password. The password is encrypted on disk (AES-GCM, bound to the
person and the address), is never sent back to the browser, and is removed with the account. The key comes from MAIL_ACCOUNTS_KEY in the
server's private settings when that is set (best: a copy of the data folder alone then opens nothing). Otherwise a key file is made in the
data folder (mode 600), which protects only against the saved file leaking on its own. Mail servers are reached over verified implicit TLS only (IMAP 993, SMTP 465), the same as the server-wide mailbox.

Because a person types the mail server's name, Atrium must never be turned into a way to probe its own network. So a host must be a public
DNS name (no IP addresses, no one-word or internal names), it is resolved at connect time, every address it resolves to must be a public
one, and the connection then goes to that checked address while the certificate is still verified against the typed name. That pins the
address, so a name that changes its answer between the check and the connection cannot redirect the connection inward.
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
from types import SimpleNamespace
from typing import Optional

from agentkit import atomic_write_text

IMAP_PORT = 993                       # implicit TLS only, like the server-wide mailbox
SMTP_PORT = 465
MAX_PASSWORD = 512
ATTEMPTS = 6                          # connection attempts per person per window
WINDOW = 600

# Providers that offer app passwords and implicit-TLS SMTP. (Outlook.com and iCloud only offer SMTP with STARTTLS, and Microsoft turns off
# password sign-in on most accounts; they need a different kind of connection and are not offered here.)
PRESETS = [
    {"id": "gmail", "label": "Gmail", "imap": "imap.gmail.com", "smtp": "smtp.gmail.com",
     "hint": "Turn on 2-Step Verification, then create an App password in your Google Account and use that here."},
    {"id": "yahoo", "label": "Yahoo Mail", "imap": "imap.mail.yahoo.com", "smtp": "smtp.mail.yahoo.com",
     "hint": "Create an app password in Yahoo Account Security and use that here."},
    {"id": "fastmail", "label": "Fastmail", "imap": "imap.fastmail.com", "smtp": "smtp.fastmail.com",
     "hint": "Create an app password in Fastmail's Password & Security settings (with IMAP and SMTP access)."},
    {"id": "zoho", "label": "Zoho Mail", "imap": "imap.zoho.com", "smtp": "smtp.zoho.com",
     "hint": "Turn on IMAP in Zoho Mail settings and create an application-specific password."},
]
PRESET_BY_ID = {p["id"]: p for p in PRESETS}

_HOST = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")
_INTERNAL_SUFFIXES = {"local", "localhost", "internal", "lan", "home", "corp", "intranet", "localdomain", "arpa", "invalid", "test", "example"}
_ADDRESS = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@(?=.{4,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")


MIN_KEY_CHARS = 32


def derive_key(secret: str) -> bytes:
    """A 32-byte AES key from the MAIL_ACCOUNTS_KEY setting."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    if not isinstance(secret, str) or len(secret) < MIN_KEY_CHARS:
        raise AccountError("MAIL_ACCOUNTS_KEY must be at least %d characters." % MIN_KEY_CHARS)
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"atrium-mail-accounts-v1", info=b"aes-gcm key").derive(secret.encode("utf-8"))


class AccountError(Exception):
    """A problem the person can fix; the message is safe to show."""


def clean_host(value) -> str:
    host = str(value or "").strip().lower().rstrip(".")
    if not _HOST.match(host) or host.rsplit(".", 1)[-1] in _INTERNAL_SUFFIXES:
        raise AccountError("Use the mail server's public name, such as imap.example.com (not an IP address or an internal name).")
    return host


def clean_address(value) -> str:
    address = str(value or "").strip()
    if not _ADDRESS.match(address) or ".." in address or address.startswith(".") or "@." in address:
        raise AccountError("That does not look like an email address.")
    return address


def resolve_public(host: str, port: int = IMAP_PORT) -> str:
    """The one public address to connect to for a host name. Refuses a name that resolves to anything but public addresses."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise AccountError("Could not find that mail server. Check its name.") from None
    ips = []
    for info in infos:
        text = info[4][0].split("%")[0]
        try:
            ip = ipaddress.ip_address(text)
        except ValueError:
            continue
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback or ip.is_link_local:
            raise AccountError("That mail server name points to a private or internal address, which is not allowed.")
        ips.append(ip)
    if not ips:
        raise AccountError("Could not find that mail server. Check its name.")
    ips.sort(key=lambda ip: ip.version)                 # IPv4 first
    return str(ips[0])


class Accounts:
    """The encrypted store: <data>/mail-accounts.json, keyed by user name."""

    def __init__(self, data_dir: Path, key: str = ""):
        self.path = Path(data_dir) / "mail-accounts.json"
        self.keyfile = Path(data_dir) / "mail-accounts.key"
        self._env_key = derive_key(key) if key else None
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}

    # -- key and file ----------------------------------------------------------------------------------------------
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
        except FileExistsError:                           # another worker made it first
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

    def _seal(self, user: str, address: str, password: str, key: Optional[bytes] = None) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = secrets.token_bytes(12)
        return base64.b64encode(nonce + AESGCM(key or self._key()).encrypt(nonce, password.encode("utf-8"), (user + "\0" + address).encode("utf-8"))).decode()

    def _open(self, user: str, address: str, sealed: str, key: Optional[bytes] = None) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = base64.b64decode(sealed)
        return AESGCM(key or self._key()).decrypt(raw[:12], raw[12:], (user + "\0" + address).encode("utf-8")).decode("utf-8")

    def migrate(self) -> int:
        """With a key from the settings, re-seal logins saved under the old key file, then destroy that file (best effort: overwritten, then
        removed). If any saved login cannot be opened with the old key, nothing is changed and the key file is kept. Returns how many were moved."""
        if self._env_key is None:
            return 0
        try:
            old = self.keyfile.read_bytes()
        except FileNotFoundError:
            return 0
        if len(old) != 32:
            return 0
        with self._lock:
            data = self._load()
            moved = {}
            for user, rec in data.items():
                try:
                    moved[user] = self._seal(user, rec["address"], self._open(user, rec["address"], rec["sealed"], old), self._env_key)
                except Exception:
                    return 0
            for user, sealed in moved.items():
                data[user]["sealed"] = sealed
            if moved:
                self._save(data)
        try:
            with open(self.keyfile, "r+b") as fh:
                fh.write(b"\0" * 32)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            pass
        try:
            self.keyfile.unlink()
        except OSError:
            pass
        return len(moved)

    # -- the accounts ----------------------------------------------------------------------------------------------
    def info(self, user: str) -> Optional[dict]:
        """What the page may know: never the password."""
        rec = self._load().get(user.lower())
        if not isinstance(rec, dict):
            return None
        return {"address": rec.get("address", ""), "preset": rec.get("preset", ""), "imap_host": rec.get("imap_host", ""),
                "smtp_host": rec.get("smtp_host", ""), "created": rec.get("created", 0)}

    def settings(self, user: str, resolver=None) -> Optional[SimpleNamespace]:
        """Connection settings in the shape the mail backend reads, with each host replaced by its checked public address."""
        rec = self._load().get(user.lower())
        if not isinstance(rec, dict):
            return None
        try:
            password = self._open(user.lower(), rec["address"], rec["sealed"])
        except Exception:
            raise AccountError("Your saved email sign-in could not be read. Connect your email again.") from None
        return SimpleNamespace(
            mail_address=rec["address"], mail_token=password,
            mail_imap_host=(resolver or resolve_public)(rec["imap_host"], IMAP_PORT), mail_imap_port=IMAP_PORT, mail_imap_tls_server_name=rec["imap_host"],
            mail_smtp_host=(resolver or resolve_public)(rec["smtp_host"], SMTP_PORT), mail_smtp_port=SMTP_PORT, mail_smtp_tls_server_name=rec["smtp_host"],
            mail_webmail_url="", personal=True)

    def build(self, address, password, preset="", imap_host="", smtp_host="", resolver=None) -> SimpleNamespace:
        """Validate what a person typed and return settings to test with (nothing is saved yet)."""
        address = clean_address(address)
        if not isinstance(password, str) or not password or len(password) > MAX_PASSWORD or any(ord(c) < 32 for c in password):
            raise AccountError("Enter the app password for this mailbox.")
        if preset:
            p = PRESET_BY_ID.get(preset)
            if not p:
                raise AccountError("Unknown provider.")
            imap_host, smtp_host = p["imap"], p["smtp"]
        else:
            imap_host, smtp_host = clean_host(imap_host), clean_host(smtp_host)
        return SimpleNamespace(
            mail_address=address, mail_token=password,
            mail_imap_host=(resolver or resolve_public)(imap_host, IMAP_PORT), mail_imap_port=IMAP_PORT, mail_imap_tls_server_name=imap_host,
            mail_smtp_host=(resolver or resolve_public)(smtp_host, SMTP_PORT), mail_smtp_port=SMTP_PORT, mail_smtp_tls_server_name=smtp_host,
            mail_webmail_url="", personal=True, preset=preset)

    def save(self, user: str, s: SimpleNamespace) -> None:
        user = user.lower()
        with self._lock:
            data = self._load()
            data[user] = {"address": s.mail_address, "preset": getattr(s, "preset", ""), "imap_host": s.mail_imap_tls_server_name,
                          "smtp_host": s.mail_smtp_tls_server_name, "sealed": self._seal(user, s.mail_address, s.mail_token), "created": int(time.time())}
            self._save(data)

    def delete(self, user: str) -> bool:
        with self._lock:
            data = self._load()
            gone = data.pop(user.lower(), None) is not None
            if gone:
                self._save(data)
            return gone

    def purge(self, keep) -> int:
        """Forget the mailboxes of people who no longer exist."""
        keep = {k.lower() for k in keep}
        with self._lock:
            data = self._load()
            drop = [u for u in data if u not in keep]
            for u in drop:
                del data[u]
            if drop:
                self._save(data)
            return len(drop)

    def allow_attempt(self, user: str) -> bool:
        """At most ATTEMPTS connection attempts per person in WINDOW seconds, so the form cannot be used to hammer a mail server."""
        now = time.time()
        with self._lock:
            recent = [t for t in self._attempts.get(user.lower(), []) if now - t < WINDOW]
            if len(recent) >= ATTEMPTS:
                self._attempts[user.lower()] = recent
                return False
            recent.append(now)
            self._attempts[user.lower()] = recent
            return True
