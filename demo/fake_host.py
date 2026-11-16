"""A stand-in for the host helper, so the demo's Settings > Server section works with made-up packages. Nothing real happens:
updates are simulated and reboot or shutdown only count down and then reset."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = "demo-key-demo-key-demo-key-demo-key"
S = {"safe": [{"name": n, "from": "1.%d" % i, "to": "1.%d" % (i + 1)} for i, n in enumerate(["base-files", "distro-info", "snapd", "vim", "vim-common", "xxd"])],
     "care": [{"name": n, "from": "5.1", "to": "5.2"} for n in ("docker-ce", "docker-ce-cli", "containerd.io")],
     "update": None, "last": None, "sched": None, "log": [], "reboot": False}
LOCK = threading.Lock()


def log(action: str, result: str) -> None:
    S["log"].insert(0, {"ts": time.time(), "user": "demo", "action": action, "result": result})
    del S["log"][10:]


def finish_update(kind: str, names: list) -> None:
    time.sleep(1.5)
    with LOCK:
        S["update"]["progress"]["step"] = "installing %d packages" % len(names)
    time.sleep(3.5)
    with LOCK:
        S["last"] = {"ok": True, "kind": kind, "packages": names, "finished": time.time(), "dry_run": True, "containers_after": 31, "containers_missing": [], "reboot_required": False}
        S["safe" if kind == "update_safe" else "care"] = []
        S["update"] = None
        log(kind, "updated %d packages (demo)" % len(names))


def power_timer(action: str, at: float) -> None:
    time.sleep(max(0, at - time.time()) + 1)
    with LOCK:
        if S["sched"] and S["sched"]["at"] == at:
            S["sched"] = None
            log(action, "demo: nothing really happened")



# ---- containers (made-up groups) ---------------------------------------------------------------------------------------------
def _svc(name, kind, update=False, newer=""):
    return {"service": name, "ref": name, "kind": kind, "update": update, "newer": newer, "note": ""}


C = {"projects": {
    "voice-out": {"project": "voice-out", "tier": "low", "update": True, "unknown": False, "services": [_svc("tts", "floating", True, "new build")]},
    "voice-in": {"project": "voice-in", "tier": "low", "update": False, "unknown": False, "services": [_svc("stt", "floating")]},
    "media": {"project": "media", "tier": "medium", "update": True, "unknown": False, "services": [_svc("player", "pinned", True, "4.11.0")]},
    "monitoring": {"project": "monitoring", "tier": "medium", "update": False, "unknown": False, "services": [_svc("kuma", "pinned")]},
    "files": {"project": "files", "tier": "high", "update": True, "unknown": False, "services": [_svc("app", "pinned", True, "35.0.3"), _svc("db", "pinned")]},
    "web": {"project": "web", "tier": "high", "update": False, "unknown": False, "services": [_svc("proxy", "built")]}},
    "op": None, "last": None, "points": {}}


def finish_container_op(op: str, project: str) -> None:
    for step in ("saving a restore point", "pulling the new build", "replacing the containers", "waiting for health checks"):
        with LOCK:
            C["op"]["step"] = step
        time.sleep(1.2)
    with LOCK:
        p = C["projects"][project]
        if op == "container_update":
            for s in p["services"]:
                s["update"], s["newer"] = False, ""
            p["update"] = False
            C["points"][project] = {"id": "demo", "created": time.time(), "services": [s["service"] for s in p["services"]]}
        else:
            C["points"].pop(project, None)
            p["update"] = True
            p["services"][0].update(update=True, newer="new build")
        C["last"] = {"op": op, "project": project, "ok": True, "note": "demo: nothing really changed", "finished": time.time(), "dry_run": True}
        C["op"] = None
        log(op, "%s (demo)" % project)


def containers_state() -> dict:
    return {"checked_at": time.time() - 1500, "projects": list(C["projects"].values()), "op": C["op"], "last": C["last"], "checking": False,
            "restore_points": C["points"], "keep_days": 7}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("X-Hostctl-Key") != KEY:
            return self.reply(403, {"error": "Not allowed."})
        if self.path.split("?")[0] == "/containers":
            with LOCK:
                return self.reply(200, containers_state())
        with LOCK:
            sched = S["sched"]
            if sched and sched["at"] < time.time() - 15:
                S["sched"] = sched = None
            self.reply(200, {"dry_run": True, "countdown": 8, "packages": {"safe": S["safe"], "care": S["care"]}, "reboot_required": S["reboot"],
                             "update": {"running": bool(S["update"]), "progress": (S["update"] or {}).get("progress"), "last": S["last"]},
                             "containers": {"updates": sum(1 for p in C["projects"].values() if p["update"]), "checked_at": time.time() - 1500},
                             "scheduled": sched, "backup_running": False, "uptime_seconds": 90000.0, "log": S["log"]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.headers.get("X-Hostctl-Key") != KEY:
            return self.reply(403, {"error": "Not allowed."})
        with LOCK:
            if self.path == "/cancel":
                if not S["sched"]:
                    return self.reply(409, {"error": "Nothing is scheduled."})
                S["sched"] = None
                log("cancel", "cancelled the scheduled power action")
                return self.reply(200, {"ok": True})
            if self.path in ("/containers/update", "/containers/rollback", "/containers/check"):
                if self.path == "/containers/check":
                    return self.reply(200, {"ok": True})
                project = str(body.get("project") or "")
                p = C["projects"].get(project)
                if C["op"] or not p or p["tier"] == "high":
                    return self.reply(409, {"error": "That cannot be done right now."})
                op = "container_update" if self.path.endswith("update") else "container_rollback"
                C["op"] = {"op": op, "project": project, "started": time.time(), "step": "starting"}
                threading.Thread(target=finish_container_op, args=(op, project), daemon=True).start()
                return self.reply(200, {"ok": True})
            action = body.get("action")
            if S["update"] or S["sched"]:
                return self.reply(409, {"error": "Something is already running."})
            if action in ("update_safe", "update_care"):
                names = [p["name"] for p in S["safe" if action == "update_safe" else "care"]]
                if not names:
                    return self.reply(409, {"error": "Nothing is waiting in that group."})
                S["update"] = {"progress": {"kind": action, "step": "refreshing the package lists", "total": len(names)}}
                log(action, "started %d packages" % len(names))
                threading.Thread(target=finish_update, args=(action, names), daemon=True).start()
                return self.reply(200, {"ok": True, "started": len(names)})
            if action in ("reboot", "shutdown"):
                at = time.time() + 8
                S["sched"] = {"action": action, "at": at, "user": "demo"}
                log(action, "scheduled in 8s (demo)")
                threading.Thread(target=power_timer, args=(action, at), daemon=True).start()
                return self.reply(200, {"ok": True, "at": at})
        self.reply(400, {"error": "Unknown action."})


def serve(port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
