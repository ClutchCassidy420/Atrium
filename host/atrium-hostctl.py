#!/usr/bin/env python3
"""atrium-hostctl: a tiny helper that runs on the HOST (as root) and does four fixed things for the Atrium web app:

    update the "safe" packages, update the "care" packages, reboot, shut down.

Nothing else can be asked of it. The web container never gets root or the Docker socket; it only sends a request here.

    GET  /status   pending packages (split into safe and care), reboot-needed flag, a running update, a scheduled power action, the recent log
    POST /do       {"action": "update_safe" | "update_care" | "reboot" | "shutdown", "cookie": "<the person's session cookie>"}
    POST /cancel   {"cookie": "..."}   cancel a scheduled reboot or shutdown during its countdown

Who may call it: only the listed client addresses, only with the shared key (header X-Hostctl-Key), and, for every change, only if the
person's own session cookie is accepted by the web app's gate-check route (it answers 200 for owners). Every change is logged.
A reboot or shutdown waits out a countdown first so it can be cancelled. Updates run as their own systemd job, so they survive the
web app being restarted by an update.

Settings come from the environment (a systemd EnvironmentFile); see host/hostctl.env.example. Standard library only.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LISTEN = os.environ.get("HOSTCTL_LISTEN", "127.0.0.1:8766")
ALLOWED = {a.strip() for a in os.environ.get("HOSTCTL_ALLOWED", "127.0.0.1").split(",") if a.strip()}
KEY = os.environ.get("HOSTCTL_KEY", "")
APP_CHECK = os.environ.get("HOSTCTL_APP_CHECK", "")
CARE = [p.strip() for p in os.environ.get("HOSTCTL_CARE", "docker,containerd").split(",") if p.strip()]    # name prefixes that may restart services
STATE = Path(os.environ.get("HOSTCTL_STATE", "/var/lib/atrium-hostctl"))
DRY = os.environ.get("HOSTCTL_DRYRUN", "") == "1"
COUNTDOWN = int(os.environ.get("HOSTCTL_COUNTDOWN", "30"))
BACKUP_UNITS = [u.strip() for u in os.environ.get("HOSTCTL_BACKUP_UNITS", "").split(",") if u.strip()]   # systemd units that mean "a backup is running"
UNIT_UPDATE, UNIT_POWER = "atrium-update", "atrium-power"
PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.\-]{0,100}$")
LOCK = threading.Lock()
_cache: dict = {"t": 0.0, "v": None}


def now() -> float:
    return time.time()


def run(cmd: list[str], timeout: int = 60, env: dict | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})})
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def log(user: str, action: str, result: str) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now(), "user": user, "action": action, "result": result[:300]}) + "\n")


def recent_log(n: int = 10) -> list[dict]:
    try:
        lines = (STATE / "log.jsonl").read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(x) for x in reversed(lines)]
    except (OSError, ValueError):
        return []


# --- packages -----------------------------------------------------------------------------------------------------------------

def upgradable() -> list[dict]:
    code, out = run(["apt", "list", "--upgradable"], 60, {"LC_ALL": "C"})
    rows = []
    for line in out.splitlines():
        m = re.match(r"^([^/\s]+)/\S+\s+(\S+)\s+\S+\s+\[upgradable from: ([^\]]+)\]", line)
        if m and PKG_RE.match(m.group(1)):
            rows.append({"name": m.group(1), "to": m.group(2), "from": m.group(3)})
    return rows


def is_care(name: str) -> bool:
    return any(name.startswith(p) for p in CARE)


def packages() -> dict:
    if _cache["v"] is not None and now() - _cache["t"] < 20:
        return _cache["v"]
    rows = upgradable()
    v = {"safe": [r for r in rows if not is_care(r["name"])], "care": [r for r in rows if is_care(r["name"])]}
    _cache.update({"t": now(), "v": v})
    return v


# --- state ----------------------------------------------------------------------------------------------------------------------

def unit_active(unit: str) -> bool:
    return run(["systemctl", "is-active", "--quiet", unit], 10)[0] == 0


def update_state() -> dict:
    running = unit_active(UNIT_UPDATE + ".service")
    last = None
    try:
        last = json.loads((STATE / "last-update.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    prog = None
    try:
        prog = json.loads((STATE / "progress.json").read_text(encoding="utf-8")) if running else None
    except (OSError, ValueError):
        pass
    return {"running": running, "progress": prog, "last": last}


def power_state() -> dict | None:
    if not unit_active(UNIT_POWER + ".timer"):
        return None
    try:
        sched = json.loads((STATE / "scheduled.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"action": "unknown", "at": now() + COUNTDOWN}
    return sched if float(sched.get("at", 0)) > now() - 15 else None      # an old note about a countdown that has already run out


def status() -> dict:
    uptime = None
    try:
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return {"dry_run": DRY, "countdown": COUNTDOWN, "packages": packages(), "reboot_required": Path("/var/run/reboot-required").exists(),
            "update": update_state(), "scheduled": power_state(), "backup_running": any(unit_active(u) for u in BACKUP_UNITS),
            "uptime_seconds": uptime, "log": recent_log()}


# --- actions --------------------------------------------------------------------------------------------------------------------

def start_update(kind: str, user: str) -> tuple[int, dict]:
    if unit_active(UNIT_UPDATE + ".service"):
        return 409, {"error": "An update is already running."}
    if power_state():
        return 409, {"error": "A reboot or shutdown is scheduled. Cancel it first."}
    _cache["v"] = None
    names = [p["name"] for p in packages()["care" if kind == "update_care" else "safe"]]
    if not names:
        return 409, {"error": "Nothing is waiting in that group."}
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "progress.json").write_text(json.dumps({"kind": kind, "started": now(), "total": len(names), "step": "starting"}), encoding="utf-8")
    # the job gets the same settings as this process (so a dry run stays a dry run), except the key
    setenv = ["--setenv=%s=%s" % (k, v) for k, v in os.environ.items() if k.startswith("HOSTCTL_") and k != "HOSTCTL_KEY"]
    cmd = ["systemd-run", "--unit=" + UNIT_UPDATE, "--collect", "--no-block"] + setenv + [
           "--setenv=HOSTCTL_USER=" + re.sub(r"[^A-Za-z0-9._-]", "", user)[:40], sys.executable, os.path.abspath(__file__), "--run-update", kind]
    code, out = run(cmd, 30)
    log(user, kind, ("started %d packages" % len(names)) if code == 0 else "could not start: " + out)
    return (200, {"ok": True, "started": len(names)}) if code == 0 else (500, {"error": "Could not start the update job."})


def schedule_power(action: str, user: str) -> tuple[int, dict]:
    if unit_active(UNIT_UPDATE + ".service"):
        return 409, {"error": "An update is running. Wait for it to finish."}
    if power_state():
        return 409, {"error": "A reboot or shutdown is already scheduled."}
    verb = {"reboot": "reboot", "shutdown": "poweroff"}[action]
    target = ["/bin/true"] if DRY else ["/bin/systemctl", verb]
    at = now() + COUNTDOWN
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "scheduled.json").write_text(json.dumps({"action": action, "at": at, "user": user}), encoding="utf-8")
    code, out = run(["systemd-run", "--unit=" + UNIT_POWER, "--collect", "--on-active=%ds" % COUNTDOWN] + target, 30)
    log(user, action, ("scheduled in %ds%s" % (COUNTDOWN, " (dry run)" if DRY else "")) if code == 0 else "could not schedule: " + out)
    return (200, {"ok": True, "at": at}) if code == 0 else (500, {"error": "Could not schedule that."})


def cancel_power(user: str) -> tuple[int, dict]:
    if not power_state():
        return 409, {"error": "Nothing is scheduled."}
    run(["systemctl", "stop", UNIT_POWER + ".timer"], 15)
    run(["systemctl", "stop", UNIT_POWER + ".service"], 15)
    try:
        (STATE / "scheduled.json").unlink()
    except OSError:
        pass
    log(user, "cancel", "cancelled the scheduled power action")
    return 200, {"ok": True}


def containers() -> list[str]:
    code, out = run(["docker", "ps", "--format", "{{.Names}}"], 30)
    return sorted(x for x in out.splitlines() if x.strip()) if code == 0 else []


def run_update(kind: str) -> int:
    """Runs inside its own systemd unit: install the group's packages, then check that the containers came back."""
    user = os.environ.get("HOSTCTL_USER", "")
    STATE.mkdir(parents=True, exist_ok=True)

    def progress(step: str, **extra) -> None:
        (STATE / "progress.json").write_text(json.dumps({"kind": kind, "started": started, "step": step, **extra}), encoding="utf-8")

    started = now()
    before = containers()
    env = {"DEBIAN_FRONTEND": "noninteractive", "NEEDRESTART_MODE": "l", "LC_ALL": "C"}
    progress("refreshing the package lists")
    run(["apt-get", "update", "-qq"], 300, env)
    _cache["v"] = None
    names = [p["name"] for p in packages()["care" if kind == "update_care" else "safe"]]
    result = {"kind": kind, "user": user, "started": started, "packages": names, "ok": False, "dry_run": DRY}
    if not names:
        result.update(ok=True, note="Nothing was waiting.")
    else:
        progress("installing %d packages" % len(names), total=len(names))
        cmd = ["apt-get", "install", "-y", "--only-upgrade", "-o", "Dpkg::Options::=--force-confold"] + (["-s"] if DRY else []) + names
        code, out = run(cmd, 1800, env)
        result["ok"] = code == 0
        result["output_tail"] = out.strip().splitlines()[-6:]
    if kind == "update_care" and not DRY:
        progress("waiting for services to settle")
        time.sleep(45)
    after = containers()
    result["containers_before"], result["containers_after"] = len(before), len(after)
    result["containers_missing"] = sorted(set(before) - set(after))
    result["reboot_required"] = Path("/var/run/reboot-required").exists()
    result["finished"] = now()
    (STATE / "last-update.json").write_text(json.dumps(result), encoding="utf-8")
    try:
        (STATE / "progress.json").unlink()
    except OSError:
        pass
    log(user, kind, ("updated %d packages" % len(names) if result["ok"] else "failed") + (", missing containers: " + ", ".join(result["containers_missing"]) if result["containers_missing"] else ""))
    return 0 if result["ok"] else 1


# --- web -------------------------------------------------------------------------------------------------------------------------

def person(cookie: str) -> str | None:
    """Ask the web app whether this session cookie belongs to an owner. Returns the username or None."""
    if not APP_CHECK or not cookie or len(cookie) > 2000:
        return None
    try:
        req = urllib.request.Request(APP_CHECK, headers={"Cookie": cookie})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read(4000) or b"{}")
            return str(data.get("user") or "owner")[:40] if r.status == 200 and data.get("ok") else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        pass

    def reply(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def allowed(self) -> bool:
        if self.client_address[0] not in ALLOWED or not KEY or not hmac.compare_digest(self.headers.get("X-Hostctl-Key", ""), KEY):
            self.reply(403, {"error": "Not allowed."})
            return False
        return True

    def body(self) -> dict:
        n = min(int(self.headers.get("Content-Length") or 0), 10000)
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}

    def do_GET(self):
        if not self.allowed():
            return
        if self.path.split("?")[0] == "/status":
            return self.reply(200, status())
        self.reply(404, {"error": "No such route."})

    def do_POST(self):
        if not self.allowed():
            return
        data = self.body()
        who = person(str(data.get("cookie") or ""))
        if not who:
            return self.reply(401, {"error": "Only a signed-in owner can do that."})
        path = self.path.split("?")[0]
        with LOCK:
            if path == "/cancel":
                return self.reply(*cancel_power(who))
            if path == "/do":
                action = data.get("action")
                if action in ("update_safe", "update_care"):
                    return self.reply(*start_update(action, who))
                if action in ("reboot", "shutdown"):
                    return self.reply(*schedule_power(action, who))
                return self.reply(400, {"error": "Unknown action."})
        self.reply(404, {"error": "No such route."})


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--run-update":
        return run_update(sys.argv[2])
    if not KEY or len(KEY) < 24:
        print("HOSTCTL_KEY must be set to a long random value", file=sys.stderr)
        return 2
    host, _, port = LISTEN.rpartition(":")
    srv = ThreadingHTTPServer((host, int(port)), H)
    print("atrium-hostctl on %s (dry run: %s)" % (LISTEN, DRY), flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
