"""Web Push and notifications for the chat: keys, subscriptions, delivery, preferences, quiet hours and the run watcher.

Logic only; the routes that expose it live in chat_backend.py. The two settings below are set once at start-up by
chatmount.py and read at call time, so tests and installs can change them on this module.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
try:
    import fcntl          # Linux only: locks the push store. Absent on Windows (development machines), where push is not used.
except ImportError:
    fcntl = None
import json
import os
import re
import struct
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from agentkit import atomic_write_text, call as _call, fill_text as _T, hermes_home as _hermes_home

PUSH_ENABLED = os.environ.get("PUSH_ENABLED") == "1"      # the run watcher below only exists to send push notifications
PUSH_PROXY = os.environ.get("PUSH_PROXY") or None          # an outbound proxy for push messages (the app has no direct internet)

# --- Web Push: keys, subscriptions, delivery ---------------------------------------
#
# Pushes are encrypted for the one device that subscribed (RFC 8291) and signed with this server's own VAPID key
# (RFC 8292); the push service (Google, Apple or Mozilla) only relays them. Keys and subscriptions live in
# <HERMES_HOME>/atrium/push.json (private to the agent's user). Nothing else leaves this host.

PUSH_SUBJECT = "mailto:atrium-push@example.org"      # VAPID contact; push services want a mailto: or https: URL
MAX_SUBSCRIPTIONS = 10
ENDPOINT_RE = re.compile(r"^https://[A-Za-z0-9.\-]{3,120}(?::\d{2,5})?/[\x21-\x7e]{1,1500}$")
B64U_RE = re.compile(r"^[A-Za-z0-9_\-]{8,200}$")
_tlock = threading.Lock()


@contextlib.contextmanager
def locked():
    """One writer at a time across threads and processes (dashboard, host watcher, the agent's notify command)."""
    path = _push_path().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with _tlock:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                if fcntl:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
_tasks: set = set()


def spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _push_path() -> Path:
    return _hermes_home() / "atrium" / "push.json"


def _push_load() -> dict:
    try:
        data = json.loads(_push_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def push_save(data: dict) -> None:
    path = _push_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    try:
        os.chmod(str(tmp), 0o660)  # the agent's host side shares this file through the group
    except OSError:
        pass
    os.replace(str(tmp), str(path))


def push_state() -> dict:
    """Load the state, creating the VAPID key pair the first time."""
    data = _push_load()
    if not data.get("private_pem") or not data.get("public_key"):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        data["private_pem"] = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode("ascii")
        data["public_key"] = _b64u(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        data.setdefault("subscriptions", [])
        push_save(data)
    if not isinstance(data.get("subscriptions"), list):
        data["subscriptions"] = []
    return data


def _vapid_header(endpoint: str, data: dict) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    origin = "/".join(endpoint.split("/")[:3])
    head = _b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = _b64u(json.dumps({"aud": origin, "exp": int(time.time()) + 12 * 3600, "sub": PUSH_SUBJECT}, separators=(",", ":")).encode())
    signing_input = (head + "." + claims).encode("ascii")
    key = serialization.load_pem_private_key(data["private_pem"].encode("ascii"), password=None)
    r, s_ = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    jwt = head + "." + claims + "." + _b64u(r.to_bytes(32, "big") + s_.to_bytes(32, "big"))
    return "vapid t=" + jwt + ", k=" + data["public_key"]


def _encrypt(sub: dict, payload: bytes) -> bytes:
    """aes128gcm content encoding for Web Push (RFC 8188 / 8291)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    ua_pub = _b64u_decode(sub["keys"]["p256dh"])
    auth = _b64u_decode(sub["keys"]["auth"])
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    as_priv = ec.generate_private_key(ec.SECP256R1())
    as_pub = as_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = as_priv.exchange(ec.ECDH(), ua_key)
    prk = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    salt = os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(prk)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(prk)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack(">IB", 4096, len(as_pub)) + as_pub + ciphertext


def send_one(client, sub: dict, data: dict, payload: bytes, urgency: str) -> int:
    body = _encrypt(sub, payload)
    headers = {
        "Authorization": _vapid_header(sub["endpoint"], data),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": "3600",
        "Urgency": urgency,
    }
    return client.post(sub["endpoint"], content=body, headers=headers).status_code


def notify_sync(title: str, text: str, kind: str = "note", urgency: str = "normal", force: bool = False, url: str = "/chat") -> dict:
    """Send one notification to every subscribed device, unless the owner's preferences say otherwise (a kind switched off,
    or quiet hours: then it is held and delivered later as one digest). Approvals, action results and tests always go
    through. Also the entry point for the host watcher and for the agent's own notify command (see notify.py)."""
    title, text = _T(title), _T(text)
    group = KIND_GROUP.get(kind, "agent")
    if group != "approvals" and kind != "test":
        prefs = load_prefs()
        if not prefs["groups"].get(group, True):
            return {"sent": 0, "failed": 0, "removed": 0, "skipped": "switched off"}
        if in_quiet(prefs) and not (urgency == "high" and prefs["quiet"].get("allow_urgent", True)):
            _hold(title, text, kind)
            return {"sent": 0, "failed": 0, "removed": 0, "held": True}
    return deliver(title, text, kind, urgency, force, url)


def deliver(title: str, text: str, kind: str, urgency: str, force: bool, url: str) -> dict:
    import httpx
    with locked():
        data = push_state()
        subs = [x for x in data["subscriptions"] if isinstance(x, dict) and x.get("endpoint")]
        if not subs:
            return {"sent": 0, "failed": 0, "removed": 0}
        payload = json.dumps({"title": title[:80], "body": text[:260], "url": url if url.startswith("/") else "/chat", "tag": "agent-" + kind, "kind": kind, "force": force}).encode("utf-8")
        sent = failed = 0
        dead: list[str] = []
        with httpx.Client(timeout=15.0, proxy=PUSH_PROXY, trust_env=False) as client:
            for sub in subs:
                try:
                    code = send_one(client, sub, data, payload, urgency)
                except Exception:
                    failed += 1
                    continue
                if code in (200, 201, 202):
                    sent += 1
                elif code in (404, 410):
                    dead.append(sub["endpoint"])
                else:
                    failed += 1
        if dead:
            data["subscriptions"] = [x for x in data["subscriptions"] if x.get("endpoint") not in dead]
            push_save(data)
        return {"sent": sent, "failed": failed, "removed": len(dead)}


async def notify(title: str, text: str, kind: str, urgency: str = "normal", force: bool = False) -> dict:
    return await asyncio.to_thread(notify_sync, title, text, kind, urgency, force)


# --- notification preferences: what may ping, and quiet hours ----------------------------------------------------
# <HERMES_HOME>/atrium/notify-prefs.json (edited on the Settings page); notifications held during quiet hours wait in
# <HERMES_HOME>/atrium/notify-held.json and go out as one digest when quiet hours end (the host watcher triggers that).

KIND_GROUP = {"approval": "approvals", "action": "approvals", "done": "chat", "error": "chat", "note": "agent", "digest": "agent",
              "alert": "services", "recovered": "services", "job": "jobs", "jobfail": "jobs", "kanban": "kanban", "test": "approvals"}
GROUPS = [
    {"id": "approvals", "label": "Approvals and results of what you approved", "note": "Always on: {{agent}} waiting for your tap, and what happened after.", "locked": True},
    {"id": "agent", "label": "{{agent}}'s own updates", "note": "Morning brief, Check-ins, mail pings and anything she tells you herself."},
    {"id": "services", "label": "Service alerts", "note": "A service going down or coming back."},
    {"id": "jobs", "label": "Scheduled jobs", "note": "A job finishing with something to show, or failing."},
    {"id": "kanban", "label": "Task board", "note": "A card done, blocked or ready for your review."},
    {"id": "chat", "label": "Chat replies", "note": "{{agent}} finishing or failing a message when the app is closed."},
]
DEFAULT_PREFS = {"groups": {g["id"]: True for g in GROUPS}, "quiet": {"enabled": False, "start": "22:00", "end": "07:00", "allow_urgent": True}}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def prefs_path() -> Path:
    return _hermes_home() / "atrium" / "notify-prefs.json"


def held_path() -> Path:
    return _hermes_home() / "atrium" / "notify-held.json"


def load_prefs() -> dict:
    prefs = json.loads(json.dumps(DEFAULT_PREFS))
    try:
        data = json.loads(prefs_path().read_text(encoding="utf-8"))
        for g, v in (data.get("groups") or {}).items():
            if g in prefs["groups"] and isinstance(v, bool):
                prefs["groups"][g] = v
        q = data.get("quiet") or {}
        if isinstance(q.get("enabled"), bool):
            prefs["quiet"]["enabled"] = q["enabled"]
        if isinstance(q.get("allow_urgent"), bool):
            prefs["quiet"]["allow_urgent"] = q["allow_urgent"]
        for k in ("start", "end"):
            if isinstance(q.get(k), str) and TIME_RE.match(q[k]):
                prefs["quiet"][k] = q[k]
    except (OSError, ValueError):
        pass
    prefs["groups"]["approvals"] = True
    return prefs


def in_quiet(prefs: dict, now: Optional[float] = None) -> bool:
    q = prefs["quiet"]
    if not q["enabled"]:
        return False
    lt = time.localtime(now if now is not None else time.time())
    cur = lt.tm_hour * 60 + lt.tm_min
    a = int(q["start"][:2]) * 60 + int(q["start"][3:])
    b = int(q["end"][:2]) * 60 + int(q["end"][3:])
    if a == b:
        return False
    return (a <= cur < b) if a < b else (cur >= a or cur < b)


def _hold(title: str, text: str, kind: str) -> None:
    with locked():
        try:
            held = json.loads(held_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            held = []
        held.append({"t": int(time.time()), "title": title[:80], "text": text[:200], "kind": kind})
        held = held[-50:]
        path = held_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(held), 0o600)


def flush_held() -> dict:
    """Quiet hours are over: send what was held as one digest."""
    prefs = load_prefs()
    if in_quiet(prefs):
        return {"sent": 0, "held": "still quiet"}
    with locked():
        try:
            held = json.loads(held_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            held = []
        if not held:
            return {"sent": 0, "held": 0}
        try:
            held_path().unlink()
        except OSError:
            pass
    lines = ["%s: %s" % (h["title"], h["text"]) if h.get("text") else h["title"] for h in held[:6]]
    body = " | ".join(lines)[:230] + (" …" if len(held) > 6 else "")
    res = deliver("While you were away (%d)" % len(held), body, "digest", "normal", False, "/chat")
    res["held"] = len(held)
    return res


def _preview(text: Optional[str]) -> str:
    t = re.sub(r"```.*?```", " (code) ", text or "", flags=re.S)
    t = re.sub(r"[*_`#>\[\]]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:140] + "…") if len(t) > 140 else t


async def watch_run(run_id: str, asked: str = "") -> None:
    """Follow one run on the server so a notification goes out even when the page is closed."""
    seen: set[str] = set()
    started = time.time()
    ask = _preview(asked)
    ask = (ask[:70] + "…") if len(ask) > 70 else ask
    about = ("Re: " + ask) if ask else ""
    try:
        while time.time() - started < 2 * 3600:
            await asyncio.sleep(2.0)
            try:
                _, run = await _call("GET", "/v1/runs/" + run_id)
            except HTTPException as exc:
                if exc.status_code == 404:
                    return
                continue
            status = run.get("status")
            pending = run.get("pending_controls") if isinstance(run.get("pending_controls"), list) else []
            for ctl in pending:
                key = json.dumps(ctl, sort_keys=True, default=str)[:300]
                if key not in seen:
                    seen.add(key)
                    await notify("{{agent}} needs your approval", (about + "\n" if about else "") + "Open the chat to approve or deny.", "approval", "high")
            if status == "completed":
                out = _preview(run.get("output") if isinstance(run.get("output"), str) else "")
                await notify("{{agent}} finished", (about + "\n" if about else "") + (out or "Her answer is ready."), "done")
                return
            if status == "failed":
                await notify("{{agent}} hit an error", (about + "\n" if about else "") + "Open the chat to see what went wrong.", "error")
                return
            if status in ("cancelled", "interrupted"):
                return
    except Exception:
        return
