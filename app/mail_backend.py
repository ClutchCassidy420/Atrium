"""Email: read, search, send, reply, move and delete mail in one mailbox, over IMAP and SMTP.

The app signs in with a revocable app token (never the mailbox password), only from its own address on the mail network. The browser never sees
the token. Owners only (the app mounts it behind the "admin" permission).

  GET    /folders                         folders with unread counts
  GET    /messages?folder=&page=&q=       one page of messages, newest first (q searches subject, sender and text)
  GET    /messages/{uid}?folder=          one message: headers, text, a cleaned HTML version, attachments
  GET    /messages/{uid}/attachment/{n}?folder=
  POST   /messages/{uid}/flags            {folder, seen?, flagged?}
  POST   /messages/{uid}/move             {folder, to}
  DELETE /messages/{uid}?folder=          to Trash (permanently, if it is already in Trash)
  POST   /send                            {to, cc, bcc, subject, body, attachments, reply_uid, reply_folder, submission_id}
HTML mail is cleaned here (no scripts, forms, frames or event handlers), and the page shows it in a sandboxed frame that also blocks remote
images, so a message cannot track you or run code.

Connections to the mail server are always verified: IMAP is implicit TLS on port 993 and SMTP is implicit TLS (default port 465), both with a
trusted, matching certificate. If you reach the server by an address that is not on its certificate, set MAIL_IMAP_TLS_SERVER_NAME and
MAIL_SMTP_TLS_SERVER_NAME to the name the certificate carries. There is no switch that turns verification off.

An install can add routes of its own to the same /api/mail prefix (for example a server-wide block list). It imports the helpers below, and
lists what it adds in FEATURES so the page can show the matching controls, and adds fields to an opened message through READ_HOOKS.
"""
from __future__ import annotations

import asyncio
import base64
import email
import hashlib
import imaplib
import logging
import re
import smtplib
import socket
import sqlite3
import ssl
import threading
import time
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import timezone
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, make_msgid, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

import mail_accounts as ma
import mail_images as mi

router = APIRouter()
CFG = None
PAGE = 30
MAX_BODY = 6 * 1024 * 1024
MAX_ATTACH_TOTAL = 20 * 1024 * 1024
MAX_MESSAGE = 64 * 1024 * 1024          # a message bigger than this is refused with a clear error, never cut short
READ_HOOKS: list = []                   # an install may add functions that take the parsed message and return extra fields for the page (for example block targets)
FEATURES: set[str] = set()              # extra controls an install has added (for example "blocklist"); the page reads them from /status
LOG = logging.getLogger(__name__)

# Per-person mailboxes (MAIL_USER_ACCOUNTS=1). The mount sets ACCOUNTS to the encrypted store and, for each request, ACCOUNT to the signed-in
# person's checked settings and SERVER_OK to whether that person may use the server-wide mailbox (owners, when they have none of their own).
ACCOUNTS = None
SAFE = None          # the senders each person has chosen to always see images from (mail_images.SafeSenders)
SIGNER = None        # signs the links the cleaned message uses for images it shows (mail_images.Signer)
ACCOUNT: ContextVar = ContextVar("mail_account", default=None)
SERVER_OK: ContextVar = ContextVar("mail_server_ok", default=True)


def configure(cfg) -> bool:
    global CFG
    CFG = cfg
    return server_configured()


def server_configured() -> bool:
    return bool(CFG and CFG.mail_address and CFG.mail_token and CFG.mail_imap_host)


def _cfg():
    """The mailbox this request works on: the person's own, else the server-wide one when this person may use it."""
    own = ACCOUNT.get()
    if own is not None:
        return own
    if SERVER_OK.get() and server_configured():
        return CFG
    raise HTTPException(status_code=409, detail="Connect your email first.")


class _VerifiedIMAP4SSL(imaplib.IMAP4_SSL):
    """Connect via a service name and verify the configured TLS identity."""

    def __init__(self, host, port, tls_server_name, timeout=25):
        if port != 993 or not host or not tls_server_name:
            raise ValueError("Verified implicit IMAP TLS requires a host, identity and port 993")
        self._tls_server_name = tls_server_name
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        if not ctx.check_hostname or ctx.verify_mode != ssl.CERT_REQUIRED:
            raise RuntimeError("IMAP certificate verification is required")
        super().__init__(host, port, ssl_context=ctx, timeout=timeout)

    def _create_socket(self, timeout):
        sock = imaplib.IMAP4._create_socket(self, timeout)
        try:
            return self.ssl_context.wrap_socket(sock, server_hostname=self._tls_server_name)
        except BaseException:
            sock.close()
            raise


class _VerifiedSMTPSSL(smtplib.SMTP_SSL):
    """Implicit-TLS SMTP that verifies the certificate against the configured identity."""

    def __init__(self, host, port, tls_server_name, timeout=30):
        if not host or not tls_server_name:
            raise ValueError("Verified SMTP TLS requires a host and an identity")
        self._tls_server_name = tls_server_name
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        if not ctx.check_hostname or ctx.verify_mode != ssl.CERT_REQUIRED:
            raise RuntimeError("SMTP certificate verification is required")
        super().__init__(host, port, timeout=timeout, context=ctx)

    def _get_socket(self, host, port, timeout):
        raw = socket.create_connection((host, port), timeout, self.source_address)
        try:
            return self.context.wrap_socket(raw, server_hostname=self._tls_server_name)
        except BaseException:
            raw.close()
            raise


def _refresh_caps(m) -> None:
    """A front-end proxy (Mailu's) lists fewer capabilities before sign-in than the mail store behind it does after, so ask again once signed in.
    Without this, MOVE and UIDPLUS look missing and moving or deleting a message is refused."""
    try:
        m._get_capabilities()
    except (imaplib.IMAP4.error, OSError, AttributeError, ValueError):
        pass                                  # keep what the greeting said


# --- reused connections --------------------------------------------------------------------------------------------
# Every mail action used to open a new TLS connection, sign in and sign out. A finished connection that is still healthy is now kept for
# a short while and handed to the next request for the same mailbox, so a page that makes several calls in a row (list, open, mark read,
# move) pays for one handshake and one sign-in. One request owns a connection at a time (imaplib is not thread-safe). A connection is kept
# only when the request ended cleanly; after any error it is closed. A kept connection that has been idle a few seconds is pinged first and
# replaced with a fresh one if the server has dropped it. Set MAX_IDLE to 0 to turn this off.
IDLE_TTL_S = 45.0          # close a kept connection that has not been used for this long
RECHECK_S = 3.0            # ping a kept connection that has been idle longer than this before using it
MAX_IDLE = 2               # kept connections per mailbox
_POOL: dict = {}           # mailbox key -> [(time it was put back, connection)]
_POOL_LOCK = threading.Lock()


def _pool_key(c) -> tuple:
    token = hashlib.sha256(str(c.mail_token).encode("utf-8")).hexdigest()[:16]
    return (c.mail_imap_host, c.mail_imap_port, c.mail_imap_tls_server_name or c.mail_imap_host, c.mail_address, token)


def _close(m) -> None:
    try:
        m.logout()
    except Exception:
        pass


def _take(key):
    """A kept, working connection for this mailbox, or None. Also closes any that have been idle too long."""
    now = time.monotonic()
    expired, found = [], None
    with _POOL_LOCK:
        for k in list(_POOL):
            alive = []
            for stamp, m in _POOL[k]:
                (alive if now - stamp <= IDLE_TTL_S else expired).append((stamp, m))
            if alive:
                _POOL[k] = alive
            else:
                del _POOL[k]
        if key in _POOL:
            found = _POOL[key].pop()
            if not _POOL[key]:
                del _POOL[key]
    for _, m in expired:
        _close(m)
    if found is None:
        return None
    stamp, m = found
    if now - stamp > RECHECK_S:
        try:
            if m.noop()[0] != "OK":
                raise OSError("not ok")
        except Exception:
            _close(m)
            return None
    return m


def _give(key, m) -> None:
    with _POOL_LOCK:
        held = _POOL.setdefault(key, [])
        if MAX_IDLE > 0 and len(held) < MAX_IDLE:
            held.append((time.monotonic(), m))
            return
    _close(m)


def _clear_pool() -> None:
    with _POOL_LOCK:
        held = [m for lst in _POOL.values() for _, m in lst]
        _POOL.clear()
    for m in held:
        _close(m)


def _open_imap(c):
    try:
        m = _VerifiedIMAP4SSL(
            c.mail_imap_host,
            c.mail_imap_port,
            c.mail_imap_tls_server_name or c.mail_imap_host,
            timeout=25,
        )
        m._encoding = "utf-8"
        m.login(c.mail_address, c.mail_token)
        _refresh_caps(m)
        return m
    except (imaplib.IMAP4.error, OSError, ValueError):
        raise HTTPException(status_code=502, detail="The mail server did not accept the sign-in.")


@contextmanager
def _imap():
    c = _cfg()
    key = _pool_key(c)
    m = _take(key) if MAX_IDLE > 0 else None
    if m is None:
        m = _open_imap(c)
    clean = False
    try:
        yield m
        clean = True
    except imaplib.IMAP4.error:
        raise HTTPException(status_code=502, detail="The mail server refused that.")
    finally:
        if clean and MAX_IDLE > 0:
            _give(key, m)
        else:
            _close(m)


def _dec(v) -> str:
    if v is None:
        return ""
    try:
        return str(make_header(decode_header(str(v)))).strip()
    except Exception:
        return str(v).strip()


def _folders(m) -> list[dict]:
    typ, data = m.list()
    out = []
    for raw in data or []:
        line = raw.decode("utf-8", "replace")
        mt = re.match(r'\((?P<flags>[^)]*)\) "(?P<sep>[^"]*)" (?P<name>.+)$', line)
        if not mt:
            continue
        name = mt.group("name").strip().strip('"')
        flags = mt.group("flags").lower()
        if "\\noselect" in flags:
            continue
        role = next((r for r in ("junk", "drafts", "trash", "sent", "archive") if "\\" + r in flags), "")
        if name.upper() == "INBOX":
            role = "inbox"
        out.append({"name": name, "role": role})
    order = {"inbox": 0, "drafts": 1, "sent": 2, "archive": 3, "junk": 4, "trash": 5, "": 6}
    out.sort(key=lambda f: (order.get(f["role"], 6), f["name"].lower()))
    return out


def _check_folder(m, folder: str) -> str:
    if not folder or len(folder) > 200 or "\n" in folder or "\r" in folder:
        raise HTTPException(status_code=400, detail="Bad folder.")
    if folder not in {f["name"] for f in _folders(m)}:
        raise HTTPException(status_code=404, detail="No such folder.")
    return folder


def _q(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _role_name(m, role: str) -> Optional[str]:
    return next((f["name"] for f in _folders(m) if f["role"] == role), None)


# ---------------------------------------------------------------- reading

def _addr_list(v) -> list[dict]:
    return [{"name": _dec(n), "email": a} for n, a in getaddresses([_dec(v)]) if a]


def _iso(date_header) -> str:
    try:
        d = parsedate_to_datetime(str(date_header))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def _list_page(folder: str, page: int, q: str) -> dict:
    with _imap() as m:
        _check_folder(m, folder)
        m.select(_q(folder), readonly=True)
        if q:
            qb = q.strip()[:120]
            typ, data = m.uid("SEARCH", "CHARSET", "UTF-8", "OR", "OR", "SUBJECT", _q(qb), "FROM", _q(qb), "TEXT", _q(qb))
        else:
            typ, data = m.uid("SEARCH", None, "ALL")
        uids = [int(x) for x in (data[0] or b"").split()]
        uids.sort(reverse=True)
        total = len(uids)
        chunk = uids[page * PAGE:(page + 1) * PAGE]
        items = []
        if chunk:
            typ, rows = m.uid("FETCH", ",".join(str(u) for u in chunk), "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE CONTENT-TYPE)])")
            for part in rows or []:
                if not isinstance(part, tuple):
                    continue
                head = part[0].decode("utf-8", "replace")
                uid = int(re.search(r"UID (\d+)", head).group(1))
                flags = (re.search(r"FLAGS \(([^)]*)\)", head) or [None, ""])[1]
                size = int((re.search(r"RFC822\.SIZE (\d+)", head) or [None, 0])[1])
                msg = email.message_from_bytes(part[1], policy=policy.default)
                frm = _addr_list(msg.get("From"))
                to = _addr_list(msg.get("To"))
                items.append({"uid": uid, "from": frm[0] if frm else {"name": "", "email": ""}, "to": to[:3], "subject": _dec(msg.get("Subject")) or "(no subject)",
                              "date": _iso(msg.get("Date")), "seen": "\\Seen" in flags, "flagged": "\\Flagged" in flags, "answered": "\\Answered" in flags,
                              "attach": str(msg.get("Content-Type", "")).lower().startswith("multipart/mixed"), "size": size})
            items.sort(key=lambda i: -i["uid"])
        return {"folder": folder, "page": page, "total": total, "pages": max(1, -(-total // PAGE)), "items": items}


# ---------------------------------------------------------------- HTML cleaning

ALLOWED = {"a", "abbr", "b", "blockquote", "br", "caption", "code", "col", "colgroup", "dd", "div", "dl", "dt", "em", "font", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img",
           "li", "ol", "p", "pre", "q", "s", "small", "span", "strike", "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul", "center", "style"}
DROP_WITH_CONTENT = {"script", "iframe", "object", "embed", "form", "title", "head", "noscript", "svg", "math", "template", "textarea", "select", "button", "audio", "video", "canvas"}
GOOD_ATTRS = {"href", "src", "alt", "title", "width", "height", "colspan", "rowspan", "align", "valign", "bgcolor", "color", "size", "face", "border", "cellpadding", "cellspacing", "style", "class", "dir", "lang"}
VOID = {"br", "hr", "img", "col"}


class _Clean(HTMLParser):
    def __init__(self, rewrite=None):
        super().__init__(convert_charrefs=True)
        self.rewrite = rewrite            # None: remote images stay blocked; else a function that turns a web address into a link on this server
        self.out: list[str] = []
        self.skip = 0
        self.stack: list[str] = []

    @staticmethod
    def _esc(v: str) -> str:
        return v.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        bits = []
        for k, v in attrs:
            k = (k or "").lower()
            v = v or ""
            if k not in GOOD_ATTRS:
                continue
            if k in ("href", "src"):
                vv = re.sub(r"[\x00-\x20]+", "", v).lower()
                if not (vv.startswith(("http://", "https://", "mailto:", "cid:", "data:image/")) or (k == "href" and vv.startswith("#"))):
                    continue
                if k == "src" and vv.startswith(("http://", "https://")):
                    link = self.rewrite(v) if self.rewrite else None
                    if link:
                        bits.append('src="%s"' % self._esc(link))             # shown through this server, never straight from the sender
                    else:
                        bits.append('data-blocked-src="%s"' % self._esc(v))   # remote images are not loaded
                    continue
            if k == "style" and re.search(r"expression|url\s*\(|@import|behavior|javascript", v, re.I):
                continue
            bits.append('%s="%s"' % (k, self._esc(v)))
        if tag == "a":
            bits.append('target="_blank" rel="noopener noreferrer nofollow"')
        self.out.append("<%s%s>" % (tag, (" " + " ".join(bits)) if bits else ""))
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in ALLOWED or tag in VOID:
            return
        if tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                self.out.append("</%s>" % t)
                if t == tag:
                    break

    def handle_data(self, data):
        if self.skip:
            return
        if self.stack and self.stack[-1] == "style":
            if re.search(r"@import|expression|javascript|url\s*\(", data, re.I):
                return
            self.out.append(data.replace("</", "<\\/"))
            return
        self.out.append(self._esc(data).replace("&quot;", '"'))

    def close(self):
        super().close()
        while self.stack:
            self.out.append("</%s>" % self.stack.pop())


def clean_html(html: str, rewrite=None) -> str:
    p = _Clean(rewrite)
    try:
        p.feed(html[:MAX_BODY])
        p.close()
    except Exception:
        return ""
    return "".join(p.out)


def _walk(msg):
    """(text, html, attachments, inline images by cid) from a parsed message."""
    text, html, atts, inline = [], [], [], {}
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = (part.get_content_disposition() or "").lower()
        fname = _dec(part.get_filename())
        cid = str(part.get("Content-ID") or "").strip("<> ")
        if ctype == "text/plain" and disp != "attachment" and not fname:
            try:
                text.append(part.get_content())
            except Exception:
                text.append(part.get_payload(decode=True).decode("utf-8", "replace") if part.get_payload(decode=True) else "")
        elif ctype == "text/html" and disp != "attachment" and not fname:
            try:
                html.append(part.get_content())
            except Exception:
                html.append((part.get_payload(decode=True) or b"").decode("utf-8", "replace"))
        elif cid and ctype.startswith("image/") and (part.get_payload(decode=True) or b"") and len(part.get_payload(decode=True)) < 600000:
            inline[cid] = "data:%s;base64,%s" % (ctype, base64.b64encode(part.get_payload(decode=True)).decode())
        else:
            payload = part.get_payload(decode=True) or b""
            atts.append({"n": len(atts), "name": fname or ("attachment." + (ctype.split("/")[-1] or "bin")), "type": ctype, "size": len(payload)})
    return "\n".join(text), "\n".join(html), atts, inline


def _fetch_message(folder: str, uid: int, mark_seen: bool = True):
    with _imap() as m:
        _check_folder(m, folder)
        m.select(_q(folder), readonly=False)
        typ, rows = m.uid("FETCH", str(uid), "(RFC822.SIZE)")
        head = " ".join(r.decode("utf-8", "replace") if isinstance(r, bytes) else r[0].decode("utf-8", "replace") for r in rows or [] if r)
        size = re.search(r"RFC822\.SIZE (\d+)", head)
        if size and int(size.group(1)) > MAX_MESSAGE:
            raise HTTPException(status_code=413, detail="That message is over %d MB, too big to open here. Use the full webmail." % (MAX_MESSAGE // (1024 * 1024)))
        typ, rows = m.uid("FETCH", str(uid), "(FLAGS BODY.PEEK[])")
        raw = next((p[1] for p in rows or [] if isinstance(p, tuple)), None)
        if raw is None:
            raise HTTPException(status_code=404, detail="That message is gone.")
        if mark_seen:
            m.uid("STORE", str(uid), "+FLAGS", "(\\Seen)")
        return raw


def _sole_sender(msg) -> str:
    """The one address a message says it is from, or "" when that is not clear. A trusted sender is matched on this, so a From line with
    several addresses, or one a parser had to guess at (an address inside the display name), is never trusted."""
    header = msg.get("From")
    addresses = getattr(header, "addresses", ())
    if header is None or len(addresses) != 1 or getattr(header, "defects", ()):
        return ""
    return (addresses[0].addr_spec or "").lower()


def _read(folder: str, uid: int, user: str = "", images: bool = False) -> dict:
    """One message, cleaned. Remote images stay blocked unless this person asked for them (images) or always allows this sender."""
    raw = _fetch_message(folder, uid)
    msg = email.message_from_bytes(raw, policy=policy.default)
    text, html, atts, inline = _walk(msg)
    sender = _sole_sender(msg)
    safe = bool(SAFE is not None and user and SAFE.has(user, sender))
    show = bool(SIGNER is not None and user and (images or safe))
    cleaned = ""
    if html:
        cleaned = clean_html(html, (lambda url: mi.proxied(SIGNER, user, url)) if show else None)
        for cid, uri in inline.items():
            cleaned = cleaned.replace('src="cid:%s"' % cid, 'src="%s"' % uri)
    out = {"uid": uid, "folder": folder, "subject": _dec(msg.get("Subject")) or "(no subject)", "from": (_addr_list(msg.get("From")) or [{"name": "", "email": ""}])[0],
            "to": _addr_list(msg.get("To")), "cc": _addr_list(msg.get("Cc")), "reply_to": _addr_list(msg.get("Reply-To")), "date": _iso(msg.get("Date")),
            "message_id": str(msg.get("Message-ID") or ""), "references": str(msg.get("References") or ""),
            "text": text[:MAX_BODY], "html": cleaned, "blocked_images": "data-blocked-src" in cleaned, "images_shown": "/api/mail-image/" in cleaned,
            "sender_email": sender, "safe_sender": safe, "attachments": atts}
    for hook in READ_HOOKS:
        try:
            out.update(hook(msg) or {})
        except Exception:                   # an install's extra field must never stop a message from opening
            LOG.exception("mail read hook failed")
    return out


# ---------------------------------------------------------------- routes (blocking IMAP work runs in a thread)

class FlagsIn(BaseModel):
    folder: str = Field(..., max_length=200)
    seen: Optional[bool] = None
    flagged: Optional[bool] = None


class MoveIn(BaseModel):
    folder: str = Field(..., max_length=200)
    to: str = Field(..., max_length=200)


class AttachIn(BaseModel):
    name: str = Field(..., max_length=200)
    type: str = Field("application/octet-stream", max_length=100)
    data: str = Field(..., max_length=30_000_000)       # base64


class SendIn(BaseModel):
    to: str = Field(..., max_length=2000)
    cc: str = Field("", max_length=2000)
    bcc: str = Field("", max_length=2000)
    subject: str = Field("", max_length=500)
    body: str = Field("", max_length=400_000)
    attachments: list[AttachIn] = []
    submission_id: Optional[str] = Field(None, pattern=r"^[a-f0-9-]{36}$")      # one id per Send click: a retry never sends twice
    reply_uid: Optional[int] = None
    reply_folder: str = Field("", max_length=200)


def _presets() -> list:
    """The providers a person can pick when they connect a mailbox: what the form shows, never the server addresses."""
    return [{"id": p["id"], "label": p["label"], "hint": p["hint"], "help_url": p["help_url"], "steps": p["steps"]} for p in ma.PRESETS]


@router.get("/status")
async def status(request: Request = None):        # the default keeps an install's own tests that call status() directly working
    own = ACCOUNT.get()
    server_ok = bool(SERVER_OK.get() and server_configured())
    mine = []
    who = getattr(getattr(request, "state", None), "who", None)
    if ACCOUNTS is not None and who:
        mine = await asyncio.to_thread(ACCOUNTS.list, who["user"])
    choices = ([{"id": "server", "address": CFG.mail_address, "kind": "server"}] if server_ok else []) +         [{"id": a["id"], "address": a["address"], "kind": "personal"} for a in mine]
    if own is None and not server_ok:                                          # signed in, but no mailbox to work on
        return {"connected": False, "account": None, "accounts": choices, "user_accounts": ACCOUNTS is not None, "features": [],
                "presets": _presets()}
    c = own or CFG
    return {"connected": True, "account": "personal" if own is not None else "server", "active": getattr(own, "account_id", "") if own is not None else "server",
            "accounts": choices, "address": c.mail_address, "webmail": c.mail_webmail_url,
            "features": sorted(FEATURES) if own is None else [], "user_accounts": ACCOUNTS is not None, "max_accounts": ma.MAX_ACCOUNTS,
            "presets": _presets()}


@router.get("/folders")
async def folders():
    def work():
        with _imap() as m:
            out = _folders(m)
            for f in out:
                try:
                    typ, d = m.status(_q(f["name"]), "(MESSAGES UNSEEN)")
                    s = d[0].decode()
                    f["total"] = int(re.search(r"MESSAGES (\d+)", s).group(1))
                    f["unseen"] = int(re.search(r"UNSEEN (\d+)", s).group(1))
                except Exception:
                    f["total"], f["unseen"] = 0, 0
            return out
    return {"folders": await asyncio.to_thread(work), "address": _cfg().mail_address}


@router.get("/messages")
async def messages(folder: str = "INBOX", page: int = 0, q: str = ""):
    return await asyncio.to_thread(_list_page, folder, max(0, min(page, 500)), q.strip())


def _user_of(request) -> str:
    who = getattr(getattr(request, "state", None), "who", None)
    return who["user"] if who else ""


@router.get("/messages/{uid}")
async def message(uid: int, request: Request = None, folder: str = "INBOX", images: int = 0):
    return await asyncio.to_thread(_read, folder, uid, _user_of(request), bool(images))


class SafeIn(BaseModel):
    address: str = Field(..., max_length=320)


@router.get("/safe-senders")
async def safe_senders(request: Request):
    user = _user_of(request)
    if SAFE is None or not user:
        raise HTTPException(status_code=404, detail="Not available.")
    return {"senders": await asyncio.to_thread(SAFE.list, user), "max": mi.MAX_SAFE}


@router.post("/safe-senders")
async def add_safe_sender(body: SafeIn, request: Request):
    user = _user_of(request)
    if SAFE is None or not user:
        raise HTTPException(status_code=404, detail="Not available.")
    try:
        await asyncio.to_thread(SAFE.add, user, body.address)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    return {"senders": await asyncio.to_thread(SAFE.list, user)}


@router.delete("/safe-senders")
async def remove_safe_sender(address: str, request: Request):
    user = _user_of(request)
    if SAFE is None or not user:
        raise HTTPException(status_code=404, detail="Not available.")
    await asyncio.to_thread(SAFE.remove, user, address)
    return {"senders": await asyncio.to_thread(SAFE.list, user)}


# The signed links a cleaned message uses for its images. Mounted without a session check on purpose: the page that shows the message is a
# sandboxed frame that does not send the session cookie. The link itself proves the right (a signature naming the person and the address,
# valid for an hour), so this is not an open proxy.
image_router = APIRouter()
_image_slots = None


@image_router.get("/{token}")
async def remote_image(token: str):
    global _image_slots
    claims = SIGNER.verify(token) if SIGNER is not None else None
    if claims is None:
        raise HTTPException(status_code=403, detail="That image link is not valid or has expired.")
    if _image_slots is None:
        _image_slots = asyncio.Semaphore(6)                # at most six images are fetched at once
    async with _image_slots:
        try:
            kind, data = await asyncio.to_thread(mi.fetch, claims["u"])
        except mi.ImageError as error:
            raise HTTPException(status_code=error.status, detail=error.message) from None
    return Response(content=data, media_type=kind, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})


@router.get("/messages/{uid}/attachment/{n}")
async def attachment(uid: int, n: int, folder: str = "INBOX"):
    def work():
        raw = _fetch_message(folder, uid, mark_seen=False)
        msg = email.message_from_bytes(raw, policy=policy.default)
        i = 0
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = part.get_content_type()
            disp = (part.get_content_disposition() or "").lower()
            fname = _dec(part.get_filename())
            cid = str(part.get("Content-ID") or "").strip("<> ")
            if (ctype in ("text/plain", "text/html") and disp != "attachment" and not fname) or (cid and ctype.startswith("image/") and len(part.get_payload(decode=True) or b"") < 600000):
                continue
            if i == n:
                return fname or "attachment", part.get_payload(decode=True) or b""
            i += 1
        raise HTTPException(status_code=404, detail="No such attachment.")
    name, data = await asyncio.to_thread(work)
    from urllib.parse import quote
    return Response(content=data, media_type="application/octet-stream", headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name), "X-Content-Type-Options": "nosniff"})


@router.post("/messages/{uid}/flags")
async def set_flags(uid: int, body: FlagsIn):
    def work():
        with _imap() as m:
            _check_folder(m, body.folder)
            m.select(_q(body.folder), readonly=False)
            for flag, val in (("\\Seen", body.seen), ("\\Flagged", body.flagged)):
                if val is not None:
                    m.uid("STORE", str(uid), "+FLAGS" if val else "-FLAGS", "(%s)" % flag)
    await asyncio.to_thread(work)
    return {"ok": True}


def _caps(m) -> set[str]:
    """The server's capabilities as upper-case text (imaplib reports text; bytes are accepted too)."""
    return {(c.decode("ascii", "replace") if isinstance(c, bytes) else str(c)).upper() for c in (getattr(m, "capabilities", None) or ())}


def _move_one(m, uid: int, dest: str, error: str) -> None:
    """Move one message by UID. Never uses a mailbox-wide EXPUNGE, which would also remove other messages already marked deleted."""
    caps = _caps(m)
    if "MOVE" in caps:
        if m.uid("MOVE", str(uid), _q(dest))[0] != "OK":
            raise HTTPException(status_code=502, detail=error)
        return
    if "UIDPLUS" not in caps:
        raise HTTPException(status_code=502, detail="This mail server cannot move one message safely (it lacks MOVE and UIDPLUS).")
    if m.uid("COPY", str(uid), _q(dest))[0] != "OK" or m.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")[0] != "OK" or m.uid("EXPUNGE", str(uid))[0] != "OK":
        raise HTTPException(status_code=502, detail=error)


@router.post("/messages/{uid}/move")
async def move(uid: int, body: MoveIn):
    def work():
        with _imap() as m:
            _check_folder(m, body.folder)
            _check_folder(m, body.to)
            m.select(_q(body.folder), readonly=False)
            _move_one(m, uid, body.to, "Could not move it.")
    await asyncio.to_thread(work)
    return {"ok": True}


@router.delete("/messages/{uid}")
async def delete(uid: int, folder: str = "INBOX"):
    """To Trash; permanently only when it is already in Trash."""
    def work():
        with _imap() as m:
            _check_folder(m, folder)
            trash = _role_name(m, "trash")
            if not trash:
                raise HTTPException(status_code=409, detail="This mailbox has no Trash folder.")
            m.select(_q(folder), readonly=False)
            if folder != trash:
                _move_one(m, uid, trash, "Could not move it to Trash.")
            elif "UIDPLUS" not in _caps(m):
                raise HTTPException(status_code=409, detail="This mail server cannot remove one message safely.")
            elif m.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")[0] != "OK" or m.uid("EXPUNGE", str(uid))[0] != "OK":
                raise HTTPException(status_code=502, detail="Could not remove it from Trash.")
    await asyncio.to_thread(work)
    return {"ok": True}


def _addrs(v: str) -> list[str]:
    out = [a for _, a in getaddresses([v or ""]) if a]
    for a in out:
        if not re.fullmatch(r"[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+", a):
            raise HTTPException(status_code=400, detail="That address does not look right: %s" % a[:80])
    return out


@router.post("/send")
async def send(body: SendIn):
    to, cc, bcc = _addrs(body.to), _addrs(body.cc), _addrs(body.bcc)
    if not to:
        raise HTTPException(status_code=400, detail="Add at least one recipient.")
    total = 0
    msg = EmailMessage()
    c = _cfg()
    msg["From"] = c.mail_address
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = body.subject.strip().replace("\r", " ").replace("\n", " ")
    msg["Date"] = email.utils.formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=c.mail_address.split("@")[-1])
    ref = None
    if body.reply_uid and body.reply_folder:
        try:
            orig = await asyncio.to_thread(_read, body.reply_folder, body.reply_uid)
            ref = orig
            if orig["message_id"]:
                msg["In-Reply-To"] = orig["message_id"]
                msg["References"] = (orig["references"] + " " + orig["message_id"]).strip()[-900:]
        except HTTPException:
            ref = None
    msg.set_content(body.body or "")
    for a in body.attachments:
        try:
            data = base64.b64decode(a.data, validate=False)
        except Exception:
            raise HTTPException(status_code=400, detail="An attachment could not be read.")
        total += len(data)
        if total > MAX_ATTACH_TOTAL:
            raise HTTPException(status_code=413, detail="Attachments are over 20 MB together.")
        mt, _, st = (a.type or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=mt or "application", subtype=st or "octet-stream", filename=re.sub(r"[\r\n\"]", "_", a.name))

    def record(action):
        """Remember each submission so a retry cannot send twice. States: pending (sent, outcome unknown), accepted, filed."""
        if not body.submission_id:
            return None
        with closing(sqlite3.connect(CFG.data_dir / "mail-submissions.sqlite3")) as db, db:
            db.execute("create table if not exists submissions (id text primary key, state text not null, at real not null)")
            if action == "claim":
                if db.execute("insert or ignore into submissions values (?, 'pending', ?)", (body.submission_id, time.time())).rowcount:
                    return None
                return db.execute("select state from submissions where id=?", (body.submission_id,)).fetchone()[0]
            if action == "release":
                db.execute("delete from submissions where id=? and state='pending'", (body.submission_id,))
            else:
                db.execute("update submissions set state=? where id=?", (action, body.submission_id))

    def work():
        previous = record("claim")
        if previous == "pending":
            raise HTTPException(status_code=409, detail="The first attempt may or may not have been delivered. Check your Sent folder before sending again.")
        if previous:
            return {"ok": True, "filed": previous == "filed", "duplicate": True}
        refused = {}
        try:
            with _VerifiedSMTPSSL(c.mail_smtp_host, c.mail_smtp_port, c.mail_smtp_tls_server_name or c.mail_smtp_host) as s:
                s.login(c.mail_address, c.mail_token)
                refused = s.send_message(msg, from_addr=c.mail_address, to_addrs=to + cc + bcc)
        except smtplib.SMTPRecipientsRefused:
            record("release")
            raise HTTPException(status_code=400, detail="The mail server refused those recipients.")
        except (ssl.SSLCertVerificationError, ConnectionRefusedError, ValueError, smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused, smtplib.SMTPConnectError):
            record("release")                       # nothing was handed over, so a retry is safe
            raise HTTPException(status_code=502, detail="The mail server did not accept the message.")
        except (smtplib.SMTPException, OSError):
            # the message may already be on its way: keep the claim so a retry cannot send a second copy
            raise HTTPException(status_code=502, detail="Delivery is unconfirmed. Check your Sent folder before sending again.")
        record("accepted")
        filed = False
        try:
            with _imap() as m:                       # keep a copy in Sent, and mark the original answered
                sent = _role_name(m, "sent")
                if sent and m.append(_q(sent), "(\\Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())[0] == "OK":
                    filed = True
                if ref:
                    try:
                        _check_folder(m, ref["folder"])
                        m.select(_q(ref["folder"]), readonly=False)
                        m.uid("STORE", str(ref["uid"]), "+FLAGS", "(\\Answered)")
                    except Exception:
                        pass
        except (HTTPException, OSError, imaplib.IMAP4.error):
            LOG.warning("The mail server accepted the message, but saving a copy in Sent failed.")
        if filed:
            record("filed")
        out = {"ok": True, "filed": filed}
        if refused:
            out["refused"] = sorted(refused)        # accepted for the others, refused for these
        return out
    return await asyncio.to_thread(work)


# --- a person's own mailbox (MAIL_USER_ACCOUNTS=1) --------------------------------------------------------------------------------------
# The mount puts the signed-in person in request.state.who. These routes only ever act on that person, never on a name taken from the request.

class AccountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: StrictStr = Field(max_length=320)
    password: StrictStr = Field(max_length=ma.MAX_PASSWORD)
    preset: StrictStr = Field("", max_length=20)
    imap_host: StrictStr = Field("", max_length=253)
    smtp_host: StrictStr = Field("", max_length=253)


def _me(request: Request) -> str:
    who = getattr(request.state, "who", None)
    if ACCOUNTS is None or not who:
        raise HTTPException(status_code=404, detail="Personal mailboxes are not turned on.")
    return who["user"]


def _test_login(c) -> None:
    """Sign in to the incoming and the outgoing server once, so a wrong password or host is found before anything is saved."""
    # A refused sign-in is told apart from a server that cannot be reached, because the fix is different: a provider from the list nearly always
    # refuses because a normal password was used. The messages never contain anything the person typed.
    refused = ("That provider needs an app password, not your normal password." if getattr(c, "preset", "")
               else "The mail server did not accept the sign-in. Check the address and the password.")
    unreachable = "Could not reach the mail server securely. Check the server names and try again."
    try:
        m = _VerifiedIMAP4SSL(c.mail_imap_host, c.mail_imap_port, c.mail_imap_tls_server_name, timeout=25)
    except (OSError, ValueError, imaplib.IMAP4.error):
        raise HTTPException(status_code=502, detail=unreachable) from None
    try:
        m.login(c.mail_address, c.mail_token)
    except imaplib.IMAP4.error:
        raise HTTPException(status_code=502, detail=refused) from None
    except (OSError, ValueError):
        raise HTTPException(status_code=502, detail=unreachable) from None
    finally:
        try:
            m.logout()
        except Exception:
            pass
    try:
        with _VerifiedSMTPSSL(c.mail_smtp_host, c.mail_smtp_port, c.mail_smtp_tls_server_name) as s:
            s.login(c.mail_address, c.mail_token)
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(status_code=502, detail=refused) from None
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected):
        raise HTTPException(status_code=502, detail=unreachable) from None
    except smtplib.SMTPException:                         # (checked before OSError, which SMTPException is a kind of)
        raise HTTPException(status_code=502, detail="The outgoing mail server did not accept the sign-in.") from None
    except (ssl.SSLError, OSError, ValueError):
        raise HTTPException(status_code=502, detail=unreachable) from None


@router.get("/account")
async def account(request: Request):
    user = _me(request)
    return {"accounts": await asyncio.to_thread(ACCOUNTS.list, user), "server": bool(SERVER_OK.get() and server_configured()), "max": ma.MAX_ACCOUNTS}


@router.put("/account")
async def connect_account(request: Request):
    user = _me(request)
    raw = bytearray()
    async for chunk in request.stream():                     # parsed by hand so a validation message can never echo the password back
        raw.extend(chunk)
        if len(raw) > 8192:
            raise HTTPException(status_code=413, detail="That is too large.")
    try:
        body = AccountIn.model_validate_json(bytes(raw))
    except ValidationError:
        raise HTTPException(status_code=400, detail="Fill in your address and app password.") from None
    if not ACCOUNTS.allow_attempt(user):
        raise HTTPException(status_code=429, detail="Too many tries. Wait a few minutes and try again.")

    def work():
        try:
            c = ACCOUNTS.build(body.address, body.password, body.preset, body.imap_host, body.smtp_host)
            if any(a["address"].lower() == c.mail_address.lower() for a in ACCOUNTS.list(user)):
                raise ma.AccountError("That address is already connected.")
            if len(ACCOUNTS.list(user)) >= ma.MAX_ACCOUNTS:
                raise ma.AccountError("You can connect up to %d mailboxes. Disconnect one first." % ma.MAX_ACCOUNTS)
        except ma.AccountError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        _test_login(c)
        try:
            new_id = ACCOUNTS.save(user, c)
        except ma.AccountError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return next(a for a in ACCOUNTS.list(user) if a["id"] == new_id)
    return {"account": await asyncio.to_thread(work)}


@router.delete("/account/{account_id}")
async def disconnect_account(account_id: str, request: Request):
    user = _me(request)
    if not await asyncio.to_thread(ACCOUNTS.delete, user, account_id):
        raise HTTPException(status_code=404, detail="That mailbox is not connected.")
    return {"ok": True}
