#!/usr/bin/env python3
"""atrium-hostctl: a tiny helper that runs on the HOST (as root) and does four fixed things for the Atrium web app:

    update the "safe" packages, update the "care" packages, reboot, shut down.

Nothing else can be asked of it. The web container never gets root or the Docker socket; it only sends a request here.

    GET  /status   pending packages (split into safe and care), reboot-needed flag, a running update, a scheduled power action, the recent log
    POST /do       {"action": "update_safe" | "update_care" | "reboot" | "shutdown", "cookie": "<the person's session cookie>"}
    POST /cancel   {"cookie": "..."}   cancel a scheduled reboot or shutdown during its countdown
    GET  /containers   the compose groups, whether a newer image exists for each, the restore points
    POST /containers/check | /containers/update | /containers/rollback   {"project": "name", "cookie": "..."}

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
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
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
            "uptime_seconds": uptime, "log": recent_log(), "containers": container_summary()}


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


# --- containers: find the compose groups, look for newer images, update one group with a restore point -----------------------------

TIER_LOW = {x.strip() for x in os.environ.get("HOSTCTL_TIER_LOW", "").split(",") if x.strip()}
TIER_MEDIUM = {x.strip() for x in os.environ.get("HOSTCTL_TIER_MEDIUM", "").split(",") if x.strip()}
KEEP_DAYS = int(os.environ.get("HOSTCTL_KEEP_DAYS", "7"))
CHECK_EVERY_S = int(os.environ.get("HOSTCTL_CHECK_HOURS", "6")) * 3600
UNIT_CONTAINER = "atrium-container"
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,60}$")
FLOATING_TAGS = {"latest", "stable", "edge", "nightly", "main", "master", "release"}
CHECK_LOCK = threading.Lock()


def tier_of(project: str) -> str:
    return "low" if project in TIER_LOW else "medium" if project in TIER_MEDIUM else "high"      # anything not listed is treated as high risk


def split_ref(ref: str) -> tuple[str, str, str]:
    """(registry host, repository, tag or digest) of an image reference."""
    digest = ""
    if "@" in ref:
        ref, digest = ref.split("@", 1)
    tag = ""
    last = ref.rsplit("/", 1)[-1]
    if ":" in last:
        ref, tag = ref.rsplit(":", 1)
    parts = ref.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        host, repo = parts[0], "/".join(parts[1:])
    else:
        host, repo = "registry-1.docker.io", ref if "/" in ref else "library/" + ref
    return host, repo, digest or tag or "latest"


def ref_kind(ref: str) -> str:
    if "@sha256:" in ref:
        return "digest"
    tag = split_ref(ref)[2]
    return "pinned" if re.search(r"\d+\.\d+", tag) else "floating"


def version_shape(tag: str):
    """('v', 3, '-alpine') for 'v1.2.3-alpine': the pieces that must stay the same for a tag to count as a newer version of this one."""
    m = re.fullmatch(r"([A-Za-z_-]*)(\d+(?:\.\d+)+)(.*)", tag)
    if not m:
        return None
    return m.group(1), len(m.group(2).split(".")), m.group(3)


def version_key(tag: str):
    nums = re.search(r"\d+(?:\.\d+)+", tag)
    return tuple(int(x) for x in nums.group(0).split(".")) if nums else ()


def newer_tag(current: str, tags: list[str]) -> str | None:
    """The highest tag that is a newer release of the same kind. It stays inside the same major version (and, for 0.x versions, the same
    minor one), because a jump to the next major version can change data in ways a rollback cannot undo."""
    shape = version_shape(current)
    if not shape:
        return None
    cur = version_key(current)
    keep = 2 if cur and cur[0] == 0 else 1
    best, best_key = None, cur
    for t in tags:
        k = version_key(t)
        if version_shape(t) == shape and k[:keep] == cur[:keep] and k > best_key:
            best, best_key = t, k
    return best


def http_json(url: str, headers: dict | None = None, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "atrium-hostctl", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(5_000_000) or b"{}"), dict(r.headers)


def registry_tags(host: str, repo: str) -> list[str]:
    """All tags of a repository through the standard registry API (anonymous pull token when the registry asks for one)."""
    token = ""
    base = "https://%s/v2/%s/tags/list?n=1000" % (host, repo)
    tags: list[str] = []
    url = base
    for _ in range(30):
        try:
            data, hdrs = http_json(url, {"Authorization": "Bearer " + token} if token else None)
        except urllib.error.HTTPError as e:
            if e.code != 401 or token:
                raise
            ch = e.headers.get("WWW-Authenticate", "")
            realm = re.search(r'realm="([^"]+)"', ch)
            if not realm:
                raise
            q = {k: v for k, v in re.findall(r'(\w+)="([^"]*)"', ch) if k in ("service", "scope")}
            q.setdefault("scope", "repository:%s:pull" % repo)
            tok, _h = http_json(realm.group(1) + "?" + urllib.parse.urlencode(q))
            token = tok.get("token") or tok.get("access_token") or ""
            continue
        tags += data.get("tags") or []
        link = hdrs.get("Link") or hdrs.get("link") or ""
        m = re.search(r"<([^>]+)>;\s*rel=\"next\"", link)
        if not m:
            break
        nxt = m.group(1)
        url = nxt if nxt.startswith("http") else "https://%s%s" % (host, nxt)
    return tags


MANIFEST_ACCEPT = ", ".join(["application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
                             "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"])


def remote_digest(ref: str) -> str:
    """The digest the registry has for this tag now. One header-only request (the same one `docker pull` makes first), so it is cheap
    for registries that limit requests."""
    host, repo, tag = split_ref(ref)
    url = "https://%s/v2/%s/manifests/%s" % (host, repo, tag)
    token = ""
    for _ in range(2):
        req = urllib.request.Request(url, method="HEAD", headers={"Accept": MANIFEST_ACCEPT, "User-Agent": "atrium-hostctl",
                                                                  **({"Authorization": "Bearer " + token} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.headers.get("Docker-Content-Digest", "")
        except urllib.error.HTTPError as e:
            if e.code != 401 or token:
                raise
            ch = e.headers.get("WWW-Authenticate", "")
            realm = re.search(r'realm="([^"]+)"', ch)
            if not realm:
                raise
            q = {k: v for k, v in re.findall(r'(\w+)="([^"]*)"', ch) if k in ("service", "scope")}
            q.setdefault("scope", "repository:%s:pull" % repo)
            tok, _h = http_json(realm.group(1) + "?" + urllib.parse.urlencode(q))
            token = tok.get("token") or tok.get("access_token") or ""
    return ""


def local_digests(image_id: str) -> set[str]:
    code, out = run(["docker", "image", "inspect", image_id, "--format", "{{json .RepoDigests}}"], 20)
    try:
        return {x.split("@", 1)[1] for x in json.loads(out.strip() or "[]") if "@" in x}
    except (ValueError, IndexError):
        return set()


def compose_args(info: dict) -> list[str]:
    args = ["docker", "compose", "-p", info["project"], "--project-directory", info["dir"]]
    for f in info["files"]:
        args += ["-f", f]
    return args


def discover() -> dict:
    """Every compose group that has a running container, with its services and the image each one uses."""
    code, ids = run(["docker", "ps", "-q"], 30)
    if code != 0 or not ids.strip():
        return {}
    code, out = run(["docker", "inspect"] + ids.split(), 60)
    try:
        rows = json.loads(out)
    except ValueError:
        return {}
    groups: dict = {}
    for r in rows:
        lab = (r.get("Config") or {}).get("Labels") or {}
        proj, svc = lab.get("com.docker.compose.project"), lab.get("com.docker.compose.service")
        if not proj or not svc or not PROJECT_RE.match(proj):
            continue
        g = groups.setdefault(proj, {"project": proj, "dir": lab.get("com.docker.compose.project.working_dir", ""),
                                     "files": [f for f in (lab.get("com.docker.compose.project.config_files") or "").split(",") if f], "services": {}})
        s = g["services"].setdefault(svc, {"service": svc, "containers": [], "image_id": r.get("Image", "")})
        s["containers"].append(r["Name"].lstrip("/"))
    for g in groups.values():
        code, out = run(compose_args(g) + ["config", "--format", "json"], 40)
        try:
            cfg = json.loads(out).get("services", {})
        except ValueError:
            cfg = {}
        for name, s in g["services"].items():
            c = cfg.get(name, {})
            s["ref"] = c.get("image") or ""
            s["built"] = bool(c.get("build"))
            s["kind"] = "built" if s["built"] or not s["ref"] else ref_kind(s["ref"])
    return groups


def check_service(s: dict) -> dict:
    """Is there something newer for this service's image? Fills 'update' (bool or None) and 'newer' (a tag or 'new build')."""
    out = {"service": s["service"], "ref": s["ref"], "kind": s["kind"], "update": False, "newer": "", "note": ""}
    try:
        if s["kind"] == "floating":
            rd = remote_digest(s["ref"])
            if not rd:
                out.update(update=None, note="could not check (offline or rate limited)")
            elif rd not in local_digests(s["image_id"]):
                out.update(update=True, newer="new build")
        elif s["kind"] == "pinned":
            host, repo, tag = split_ref(s["ref"])
            nt = newer_tag(tag, registry_tags(host, repo))
            if nt:
                out.update(update=True, newer=nt)
        elif s["kind"] == "digest":
            out["note"] = "pinned by digest"
        else:
            out["note"] = "built here"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        out.update(update=None, note="registry limit, try later" if "429" in str(exc) else "check failed: %s" % str(exc)[:80])
    return out


def run_check(only: list[str] | None = None, skip_high: bool = False) -> dict:
    """Look for newer images. `only` re-checks just those groups; `skip_high` leaves out high-risk groups (they are checked less often).
    Registries limit anonymous requests, so every distinct image is looked up once and groups that were not checked keep their last answer."""
    with CHECK_LOCK:
        try:
            result = json.loads((STATE / "containers.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result = {"projects": {}}
        groups = discover()
        seen: dict = {}
        result["projects"] = {k: v for k, v in result.get("projects", {}).items() if k in groups}
        for proj, g in sorted(groups.items()):
            if (only is not None and proj not in only) or (skip_high and tier_of(proj) == "high" and proj in result["projects"]):
                if proj in result["projects"]:
                    result["projects"][proj]["tier"] = tier_of(proj)
                    continue
            svcs = []
            for s_ in g["services"].values():
                key = (s_["ref"], s_["image_id"], s_["kind"])
                if key not in seen:
                    seen[key] = check_service(s_)
                svcs.append({**seen[key], "service": s_["service"]})
            result["projects"][proj] = {"project": proj, "tier": tier_of(proj), "services": svcs,
                                        "update": any(x["update"] for x in svcs), "unknown": any(x["update"] is None for x in svcs)}
        result["checked_at"] = now()
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "containers.json").write_text(json.dumps(result), encoding="utf-8")
        return result


def check_loop() -> None:
    time.sleep(60)
    n = 0
    while True:
        try:
            run_check(skip_high=(n % 4 != 0))       # low and medium groups every time, high-risk ones about once a day
        except Exception as exc:      # noqa: BLE001 - a failed check must never stop the helper
            log("system", "container check", "failed: %s" % str(exc)[:120])
        n += 1
        time.sleep(CHECK_EVERY_S)


# restore points: what an update needs to be undone

def restore_dir() -> Path:
    return STATE / "restore"


def load_points(project: str | None = None) -> list[dict]:
    pts = []
    for f in sorted(restore_dir().glob("*/point.json")) if restore_dir().is_dir() else []:
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if project is None or p.get("project") == project:
            pts.append(p)
    return pts


def prune_points() -> None:
    cutoff = now() - KEEP_DAYS * 86400
    for p in load_points():
        if p.get("created", 0) < cutoff:
            for s in p.get("services", []):
                run(["docker", "rmi", s["restore_tag"]], 30)
            shutil.rmtree(restore_dir() / p["id"], ignore_errors=True)


def container_state() -> dict:
    cached = {}
    try:
        cached = json.loads((STATE / "containers.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    op = None
    try:
        op = json.loads((STATE / "container-op.json").read_text(encoding="utf-8")) if unit_active(UNIT_CONTAINER + ".service") else None
    except (OSError, ValueError):
        pass
    last = None
    try:
        last = json.loads((STATE / "last-container-op.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    points = {}
    for p in load_points():
        if not p.get("rolled_back"):
            points[p["project"]] = {"id": p["id"], "created": p["created"], "services": [s["service"] for s in p["services"]]}
    return {"checked_at": cached.get("checked_at"), "projects": list((cached.get("projects") or {}).values()), "op": op, "last": last,
            "restore_points": points, "keep_days": KEEP_DAYS, "checking": CHECK_LOCK.locked()}


def container_summary() -> dict:
    try:
        c = json.loads((STATE / "containers.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"updates": 0, "checked_at": None}
    return {"updates": sum(1 for p in c.get("projects", {}).values() if p.get("update")), "checked_at": c.get("checked_at")}


def start_container_op(op: str, project: str, user: str) -> tuple[int, dict]:
    if not PROJECT_RE.match(project or ""):
        return 400, {"error": "Bad group name."}
    if unit_active(UNIT_UPDATE + ".service") or unit_active(UNIT_CONTAINER + ".service"):
        return 409, {"error": "Another update is already running."}
    if power_state():
        return 409, {"error": "A reboot or shutdown is scheduled. Cancel it first."}
    if tier_of(project) == "high":
        return 403, {"error": "That group is high risk and is updated by hand."}
    if op == "container_update":
        info = next((p for p in container_state()["projects"] if p["project"] == project), None)
        if not info or not info.get("update"):
            return 409, {"error": "Nothing newer is known for that group. Check for updates first."}
    elif not any(not p.get("rolled_back") for p in load_points(project)):
        return 409, {"error": "There is no restore point for that group."}
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "container-op.json").write_text(json.dumps({"op": op, "project": project, "started": now(), "step": "starting"}), encoding="utf-8")
    setenv = ["--setenv=%s=%s" % (k, v) for k, v in os.environ.items() if k.startswith("HOSTCTL_") and k != "HOSTCTL_KEY"]
    cmd = ["systemd-run", "--unit=" + UNIT_CONTAINER, "--collect", "--no-block"] + setenv + [
           "--setenv=HOSTCTL_USER=" + re.sub(r"[^A-Za-z0-9._-]", "", user)[:40], sys.executable, os.path.abspath(__file__), "--run-container", op, project]
    code, out = run(cmd, 30)
    log(user, op, ("started for " + project) if code == 0 else "could not start: " + out)
    return (200, {"ok": True}) if code == 0 else (500, {"error": "Could not start the job."})


def wait_healthy(project: str, svcs: list[str], baseline: dict, timeout: int = 180, stable: int = 25) -> tuple[bool, str]:
    """Every container of these services running (and healthy if it has a check), not restarting, and stable for a while."""
    deadline, good_since = now() + timeout, None
    while now() < deadline:
        ok, why = True, ""
        for svc in svcs:
            code, out = run(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=" + project, "--filter", "label=com.docker.compose.service=" + svc, "-q"], 20)
            ids = out.split()
            if not ids:
                ok, why = False, svc + ": no container"
                continue
            code, out = run(["docker", "inspect"] + ids, 30)
            try:
                rows = json.loads(out)
            except ValueError:
                ok, why = False, "could not inspect " + svc
                continue
            for r in rows:
                st = r.get("State") or {}
                health = (st.get("Health") or {}).get("Status")
                if not st.get("Running") or st.get("Restarting") or health in ("unhealthy", "starting"):
                    ok, why = False, "%s is %s" % (svc, "restarting" if st.get("Restarting") else health or st.get("Status"))
                elif r.get("RestartCount", 0) > baseline.get(r["Name"].lstrip("/"), 0):
                    ok, why = False, svc + " keeps restarting"
        if ok:
            good_since = good_since or now()
            if now() - good_since >= stable:
                return True, ""
        else:
            good_since = None
        time.sleep(3)
    return False, why or "did not become healthy in time"


def edit_image_line(path: str, old_ref: str, new_ref: str) -> bool:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    pat = re.compile(r"^(\s*image:\s*[\"']?)" + re.escape(old_ref) + r"([\"']?\s*(?:#.*)?)$", re.M)
    new, n = pat.subn(lambda m: m.group(1) + new_ref + m.group(2), text)
    if n:
        p.write_text(new, encoding="utf-8")
    return n > 0


def run_container(op: str, project: str) -> int:
    """Runs in its own systemd unit: update one group (with a restore point and an automatic rollback) or roll it back."""
    user = os.environ.get("HOSTCTL_USER", "")
    started = now()
    STATE.mkdir(parents=True, exist_ok=True)

    def step(msg: str) -> None:
        (STATE / "container-op.json").write_text(json.dumps({"op": op, "project": project, "started": started, "step": msg}), encoding="utf-8")

    def finish(ok: bool, note: str, **extra) -> int:
        (STATE / "last-container-op.json").write_text(json.dumps({"op": op, "project": project, "user": user, "ok": ok, "note": note, "started": started, "finished": now(), "dry_run": DRY, **extra}), encoding="utf-8")
        try:
            (STATE / "container-op.json").unlink()
        except OSError:
            pass
        log(user, op, "%s: %s" % (project, note))
        return 0 if ok else 1

    groups = discover()
    g = groups.get(project)
    if not g:
        return finish(False, "that group is not running")
    cached = {p["project"]: p for p in container_state()["projects"]}.get(project, {})
    base = {r["Name"].lstrip("/"): r.get("RestartCount", 0) for r in json.loads(run(["docker", "inspect"] + [c for s in g["services"].values() for c in s["containers"]], 60)[1] or "[]")}

    def do_rollback(point: dict, why: str) -> tuple[bool, str]:
        step("rolling back")
        files = point.get("files", {})
        for dest, saved in files.items():
            Path(dest).write_text(Path(saved).read_text(encoding="utf-8"), encoding="utf-8")
        svcs = [s["service"] for s in point["services"]]
        for s in point["services"]:
            if point["mode"] == "floating" and not DRY:
                run(["docker", "tag", s["restore_tag"], s["ref"]], 30)       # the old build becomes the tag again
        if not DRY:
            run(compose_args(g) + ["up", "-d", "--no-deps", "--pull", "never"] + svcs, 300)
        ok, bad = (True, "") if DRY else wait_healthy(project, svcs, {}, 120, 15)
        return ok, ("rolled back" if ok else "rolled back but %s" % bad)

    if op == "container_rollback":
        pts = [p for p in load_points(project) if not p.get("rolled_back")]
        if not pts:
            return finish(False, "no restore point")
        point = pts[-1]
        ok, note = do_rollback(point, "asked")
        if ok:
            point["rolled_back"] = True
            (restore_dir() / point["id"] / "point.json").write_text(json.dumps(point), encoding="utf-8")
        return finish(ok, note, restore_point=point["id"])

    # update
    todo = [x for x in cached.get("services", []) if x.get("update")]
    if not todo or tier_of(project) == "high":
        return finish(False, "nothing to update here")
    pid = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    pdir = restore_dir() / pid
    (pdir / "files").mkdir(parents=True, exist_ok=True)
    step("saving a restore point")
    point = {"id": pid, "project": project, "created": now(), "user": user, "mode": "pinned" if any(x["kind"] == "pinned" for x in todo) else "floating", "services": [], "files": {}}
    for i, f in enumerate(g["files"]):
        saved = pdir / "files" / ("%d-%s" % (i, Path(f).name))
        saved.write_text(Path(f).read_text(encoding="utf-8"), encoding="utf-8")
        point["files"][f] = str(saved)
    for x in todo:
        s = g["services"][x["service"]]
        tag = "atrium-restore/%s-%s:%s" % (project, s["service"], pid)
        run(["docker", "tag", s["image_id"], tag], 30)
        point["services"].append({"service": s["service"], "ref": x["ref"], "old_image_id": s["image_id"], "restore_tag": tag, "kind": x["kind"]})
    (pdir / "point.json").write_text(json.dumps(point), encoding="utf-8")
    svcs = [x["service"] for x in todo]
    if not DRY:
        for x in todo:
            if x["kind"] == "pinned":
                host, repo, tag = split_ref(x["ref"])
                new_ref = x["ref"].rsplit(":", 1)[0] + ":" + x["newer"]
                if not any(edit_image_line(f, x["ref"], new_ref) for f in g["files"]):
                    do_rollback(point, "could not edit")
                    return finish(False, "could not change the version in the compose file", restore_point=pid)
                x["new_ref"] = new_ref
        step("pulling the new build")
        code, out = run(compose_args(g) + ["pull"] + svcs, 1800)
        if code != 0:
            ok, note = do_rollback(point, "pull failed")
            return finish(False, "pull failed (%s); %s" % (out.strip().splitlines()[-1][:100] if out.strip() else "", note), restore_point=pid)
        step("replacing the containers")
        code, out = run(compose_args(g) + ["up", "-d", "--no-deps"] + svcs, 600)
        step("waiting for health checks")
        ok, bad = (code == 0), (out.strip()[-120:] if code != 0 else "")
        if ok:
            ok, bad = wait_healthy(project, svcs, base)
        if not ok:
            rok, rnote = do_rollback(point, bad)
            return finish(False, "the new build failed (%s); %s" % (bad, rnote), restore_point=pid, auto_rolled_back=True)
    prune_points()
    try:
        run_check(only=[project])
    except Exception:      # noqa: BLE001
        pass
    return finish(True, "updated %s" % ", ".join(svcs), restore_point=pid)


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
        route = self.path.split("?")[0]
        if route == "/status":
            return self.reply(200, status())
        if route == "/containers":
            prune_points()
            return self.reply(200, container_state())
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
            if path == "/containers/check":
                if CHECK_LOCK.locked():
                    return self.reply(409, {"error": "A check is already running."})
                threading.Thread(target=run_check, daemon=True).start()
                log(who, "container check", "started")
                return self.reply(200, {"ok": True})
            if path in ("/containers/update", "/containers/rollback"):
                return self.reply(*start_container_op("container_update" if path.endswith("update") else "container_rollback", str(data.get("project") or ""), who))
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
    if len(sys.argv) >= 4 and sys.argv[1] == "--run-container":
        return run_container(sys.argv[2], sys.argv[3])
    if not KEY or len(KEY) < 24:
        print("HOSTCTL_KEY must be set to a long random value", file=sys.stderr)
        return 2
    host, _, port = LISTEN.rpartition(":")
    srv = ThreadingHTTPServer((host, int(port)), H)
    threading.Thread(target=check_loop, daemon=True).start()
    print("atrium-hostctl on %s (dry run: %s)" % (LISTEN, DRY), flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
