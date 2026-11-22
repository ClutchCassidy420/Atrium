"""The coding workspace service: projects, files, git checkpoints and a shared terminal, for the Agentic coding page.

It runs in its own container and sees only the workspace folder (the same folder the agent works in). The web app is its only client: every
request carries a shared secret (CODE_TOKEN) and the container has no published port. Working in a project folder is meant to be real work:
nothing here asks permission, and the safety net is git (checkpoints you can rewind to), not walls.
"""
from __future__ import annotations

import asyncio
import base64
import fcntl
import hmac
import json
import os
import pty
import re
import shutil
import signal
import struct
import subprocess
import termios
import time
import urllib.request
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse
from websockets.asyncio.client import connect as ws_connect

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace")).resolve()
TOKEN = os.environ.get("CODE_TOKEN", "")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
MAX_FILE = 1_000_000
MAX_LIST = 5000                        # the most paths /files returns
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"      # what git calls a folder with nothing in it
SKIP = {".git", "node_modules", "__pycache__", ".venv", ".cache"}
GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "Agent", "GIT_AUTHOR_EMAIL": "agent@localhost",
           "GIT_COMMITTER_NAME": "Agent", "GIT_COMMITTER_EMAIL": "agent@localhost"}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def authorised(value: str | None) -> bool:
    return bool(TOKEN) and bool(value) and hmac.compare_digest(value, TOKEN)


def browser_authorised(headers) -> bool:
    secret = os.environ.get("CDP_TOKEN", "")
    value = headers.get("x-cdp-token", "")
    return bool(secret and value) and hmac.compare_digest(secret, value)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path.rstrip("/") == "/json/version":
        if not browser_authorised(request.headers):
            return JSONResponse({"detail": "Not allowed."}, status_code=401)
    elif request.url.path != "/healthz" and not authorised(request.headers.get("x-code-token")):
        return JSONResponse({"detail": "Not allowed."}, status_code=401)
    return await call_next(request)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "workspace": WORKSPACE.is_dir()}


def project_dir(name: str) -> Path:
    if not NAME_RE.fullmatch(name or ""):
        raise HTTPException(status_code=400, detail="Bad project name.")
    p = (WORKSPACE / name).resolve()
    if p.parent != WORKSPACE or not p.is_dir():
        raise HTTPException(status_code=404, detail="No such project.")
    return p


def inside(project: Path, rel: str) -> Path:
    p = (project / (rel or "")).resolve()
    if p != project and project not in p.parents:
        raise HTTPException(status_code=400, detail="That path is outside the project.")
    return p


def git(project: Path, *args: str, check: bool = True, env: dict | None = None, stdin: str | None = None) -> str:
    r = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(project), *args], capture_output=True, text=True, env=env or GIT_ENV, input=stdin, timeout=60)
    if check and r.returncode != 0:
        raise HTTPException(status_code=400, detail=(r.stderr or r.stdout or "git failed").strip()[:300])
    return r.stdout


def is_repo(project: Path) -> bool:
    return (project / ".git").exists()


@app.get("/projects")
async def projects():
    out = []
    for p in sorted(WORKSPACE.iterdir() if WORKSPACE.is_dir() else []):
        if not p.is_dir() or p.name.startswith(".") or not NAME_RE.fullmatch(p.name):
            continue
        info = {"name": p.name, "git": is_repo(p), "mtime": int(p.stat().st_mtime)}
        if info["git"]:
            try:
                info["branch"] = git(p, "symbolic-ref", "--short", "HEAD", check=False).strip() or "(detached)"
                info["dirty"] = len([l for l in git(p, "status", "--porcelain", check=False).splitlines() if l.strip()])
            except Exception:
                pass
        out.append(info)
    return {"projects": out, "workspace": str(WORKSPACE)}


@app.post("/projects")
async def create_project(request: Request):
    body = await request.json()
    name = str(body.get("name") or "")
    if not NAME_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail="Use letters, numbers, dots, dashes and underscores (up to 60).")
    p = WORKSPACE / name
    if p.exists():
        raise HTTPException(status_code=409, detail="A project with that name already exists.")
    url = str(body.get("git_url") or "").strip()
    if url:
        if not re.fullmatch(r"(https://|git@|ssh://)[A-Za-z0-9@:/._~+-]{3,300}", url):
            raise HTTPException(status_code=400, detail="That address does not look like a git address.")
        r = subprocess.run(["git", "clone", "--", url, str(p)], capture_output=True, text=True, env=GIT_ENV, timeout=300)
        if r.returncode != 0:
            raise HTTPException(status_code=400, detail=(r.stderr or "clone failed").strip()[:300])
    else:
        p.mkdir(parents=True)
        git(p, "init", "-q")
    return {"ok": True, "name": name}


@app.get("/tree")
async def tree(project: str, path: str = ""):
    base = project_dir(project)
    d = inside(base, path)
    if not d.is_dir():
        raise HTTPException(status_code=404, detail="Not a folder.")
    items = []
    for e in sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if e.name in SKIP:
            continue
        try:
            st = e.stat()
        except OSError:
            continue
        items.append({"name": e.name, "dir": e.is_dir(), "size": 0 if e.is_dir() else st.st_size})
    return {"path": str(d.relative_to(base)) if d != base else "", "items": items[:500]}


@app.get("/files")
async def files(project: str):
    """Every file in the project as a path from its top (for @mentions in the message box). Skips the usual heavy folders; stops at MAX_LIST."""
    base = project_dir(project)
    out, more = [], False
    for root, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        rel = Path(root).relative_to(base)
        for n in sorted(names):
            if len(out) >= MAX_LIST:
                more = True
                break
            out.append((rel / n).as_posix())
        if more:
            break
    return {"files": out, "more": more}


@app.get("/file")
async def read_file(project: str, path: str):
    f = inside(project_dir(project), path)
    if not f.is_file():
        raise HTTPException(status_code=404, detail="No such file.")
    size = f.stat().st_size
    if size > MAX_FILE:
        return {"text": None, "size": size, "note": "Too large to show here (over 1 MB)."}
    raw = f.read_bytes()
    if b"\0" in raw[:4096]:
        return {"text": None, "size": size, "note": "This looks like a binary file."}
    return {"text": raw.decode("utf-8", errors="replace"), "size": size}


@app.put("/file")
async def write_file(request: Request):
    body = await request.json()
    f = inside(project_dir(str(body.get("project") or "")), str(body.get("path") or ""))
    text = body.get("text")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_FILE or f.is_dir():
        raise HTTPException(status_code=400, detail="Cannot save that.")
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(f)
    return {"ok": True}


MAX_UPLOAD = 25_000_000          # one attached file, in bytes


@app.post("/upload")
async def upload(request: Request, project: str, name: str):
    """Save a file the user attached to a message into <project>/attachments/ (the body is the raw file). Returns the project-relative path."""
    p = project_dir(project)
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name or "").name).lstrip(".")[:80] or "file"
    d = inside(p, "attachments")
    d.mkdir(exist_ok=True)
    stem, suffix = os.path.splitext(clean)
    dest, n = d / clean, 1
    while dest.exists():
        dest = d / ("%s-%d%s" % (stem, n, suffix))
        n += 1
    tmp = dest.with_name(dest.name + ".part")
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(status_code=413, detail="That file is too large (the limit is 25 MB).")
                f.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="That file is empty.")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return {"path": "attachments/" + dest.name, "size": size}


@app.get("/git/status")
async def git_status(project: str):
    p = project_dir(project)
    if not is_repo(p):
        return {"git": False, "changes": []}
    lines = [l for l in git(p, "status", "--porcelain", "-uall", check=False).splitlines() if l.strip()]
    changes = [{"state": l[:2].strip() or "?", "path": l[3:].strip().strip('"')} for l in lines[:300]]
    return {"git": True, "branch": git(p, "symbolic-ref", "--short", "HEAD", check=False).strip() or "(detached)", "changes": changes}


@app.get("/git/diff")
async def git_diff(project: str, path: str = "", base: str = ""):
    """Unified diff of the working folder against HEAD, or against a checkpoint (base). New files are shown as additions."""
    p = project_dir(project)
    if not is_repo(p):
        raise HTTPException(status_code=400, detail="This project is not under git yet.")
    if base and not re.fullmatch(r"refs/atrium/checkpoints/[0-9a-z-]{1,40}", base):
        raise HTTPException(status_code=400, detail="Bad checkpoint.")
    spec = ["--", path] if path else []
    # a temporary index lets untracked files appear in the diff without touching the real index
    tmp_index = f"/tmp/idx-{os.getpid()}-{time.time_ns()}"
    env = {**GIT_ENV, "GIT_INDEX_FILE": tmp_index}
    try:
        has_head = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(p), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True, env=GIT_ENV).returncode == 0
        if has_head:
            git(p, "read-tree", "HEAD", env=env)
        git(p, "add", "-A", env=env)
        target = base or ("HEAD" if has_head else EMPTY_TREE)
        out = git(p, "diff", "--cached", "--no-color", "-U3", target, *spec, env=env)
    finally:
        try:
            os.remove(tmp_index)
        except OSError:
            pass
    return {"diff": out[:400_000], "truncated": len(out) > 400_000}


@app.post("/git/checkpoint")
async def checkpoint(request: Request):
    """Save the whole folder (including files git does not track yet) as a restore point, without touching the branch or the index."""
    body = await request.json()
    p = project_dir(str(body.get("project") or ""))
    label = re.sub(r"[^A-Za-z0-9 ._-]", "", str(body.get("label") or "checkpoint"))[:60] or "checkpoint"
    if not is_repo(p):
        git(p, "init", "-q")
    tmp_index = f"/tmp/idx-{os.getpid()}-{time.time_ns()}"
    env = {**GIT_ENV, "GIT_INDEX_FILE": tmp_index}
    try:
        if git(p, "rev-parse", "--verify", "-q", "HEAD", check=False).strip():
            git(p, "read-tree", "HEAD", env=env)
        git(p, "add", "-A", env=env)
        tree = git(p, "write-tree", env=env).strip()
        parent = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(p), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True, text=True, env=GIT_ENV).stdout.strip()
        commit = git(p, "commit-tree", tree, *(["-p", parent] if parent else []), "-m", "checkpoint: " + label, env=env).strip()
    finally:
        try:
            os.remove(tmp_index)
        except OSError:
            pass
    ref = "refs/atrium/checkpoints/" + uuid.uuid4().hex
    git(p, "update-ref", ref, commit)
    return {"ref": ref, "label": label, "commit": commit[:10]}


@app.get("/git/checkpoints")
async def checkpoints(project: str):
    p = project_dir(project)
    if not is_repo(p):
        return {"checkpoints": []}
    out = git(p, "for-each-ref", "--sort=-creatordate", "--format=%(refname)|%(creatordate:unix)|%(subject)", "refs/atrium/checkpoints", check=False)
    items = []
    for line in out.splitlines()[:30]:
        ref, ts, subject = (line.split("|", 2) + ["", ""])[:3]
        items.append({"ref": ref, "time": int(ts or 0), "label": subject.replace("checkpoint: ", "", 1)})
    return {"checkpoints": items}


@app.post("/git/rewind")
async def rewind(request: Request):
    """Put the folder back exactly as a checkpoint had it (a checkpoint of the present is saved first, so even a rewind can be undone)."""
    body = await request.json()
    p = project_dir(str(body.get("project") or ""))
    ref = str(body.get("ref") or "")
    if not re.fullmatch(r"refs/atrium/checkpoints/[0-9a-z-]{1,40}", ref):
        raise HTTPException(status_code=400, detail="Bad checkpoint.")
    git(p, "rev-parse", "--verify", "-q", ref)
    tmp_index = f"/tmp/idx-{os.getpid()}-{time.time_ns()}"
    env = {**GIT_ENV, "GIT_INDEX_FILE": tmp_index}
    try:
        if git(p, "rev-parse", "--verify", "-q", "HEAD", check=False).strip():
            git(p, "read-tree", "HEAD", env=env)
        git(p, "add", "-A", env=env)
        tree = git(p, "write-tree", env=env).strip()
        now = git(p, "commit-tree", tree, "-m", "checkpoint: before rewind", env=env).strip()
        git(p, "update-ref", "refs/atrium/checkpoints/" + uuid.uuid4().hex + "-pre", now)
        # Use the current snapshot as the old index. Git removes tracked paths
        # absent from the target and handles file/directory transitions.
        git(p, "read-tree", "-m", "-u", now, ref, env=env)
        git(p, "add", "-A", env=env)
        if git(p, "write-tree", env=env).strip() != git(p, "rev-parse", ref + "^{tree}").strip():
            raise HTTPException(status_code=409, detail="Workspace changed during rewind. The pre-rewind checkpoint was retained.")
    finally:
        try:
            os.remove(tmp_index)
        except OSError:
            pass
    return {"ok": True}


# ---- the shared terminal: a tmux session per project, so closing the page does not end your work ----------------------------

def spawn(project: Path, cols: int, rows: int):
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(project)
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", "HOME": os.environ.get("HOME", "/root")}
        session = "p-" + re.sub(r"[^A-Za-z0-9_-]", "_", project.name)
        os.execvpe("tmux", ["tmux", "new-session", "-A", "-s", session, "-c", str(project)], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    return pid, fd


@app.websocket("/pty")
async def pty_ws(ws: WebSocket, project: str, cols: int = 100, rows: int = 30):
    if not authorised(ws.headers.get("x-code-token")):
        await ws.close(code=4401)
        return
    try:
        p = project_dir(project)
    except HTTPException:
        await ws.close(code=4404)
        return
    await ws.accept()
    pid, fd = spawn(p, max(20, min(cols, 400)), max(5, min(rows, 200)))
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def on_readable():
        try:
            data = os.read(fd, 65536)
        except OSError:
            data = b""
        queue.put_nowait(data or None)
        if not data:
            loop.remove_reader(fd)

    loop.add_reader(fd, on_readable)

    async def pump_out():
        while True:
            data = await queue.get()
            if data is None:
                break
            await ws.send_bytes(data)

    out_task = asyncio.create_task(pump_out())
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                os.write(fd, msg["bytes"])
            elif msg.get("text") is not None:
                ctl = None
                if msg["text"].startswith("{"):
                    try:
                        ctl = json.loads(msg["text"])
                    except ValueError:
                        ctl = None
                if not isinstance(ctl, dict):
                    os.write(fd, msg["text"].encode())
                    continue
                if ctl.get("type") == "resize":
                    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", max(5, min(int(ctl.get("rows", 30)), 200)), max(20, min(int(ctl.get("cols", 100)), 400)), 0, 0))
    except WebSocketDisconnect:
        pass
    finally:
        out_task.cancel()
        try:
            loop.remove_reader(fd)
        except Exception:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.kill(pid, signal.SIGHUP)       # leaves the tmux session itself running; only this window's client ends
            os.waitpid(pid, os.WNOHANG)
        except OSError:
            pass


# ---- the shared browser: one real Chromium that you (through the page) and the agent (through the debugging port) both drive ----------
CHROME_PORT = 9223                     # Chromium listens here, on this container's own address only
PROFILE = Path(os.environ.get("HOME", "/home/code")) / "chrome-profile"
ALLOWED_URL = re.compile(r"^(https?://|about:blank$)", re.I)


async def chrome_supervisor():
    """Keep Chromium running. It restarts if it dies; its profile (logins, cookies) persists between restarts."""
    exe = shutil.which("chromium") or shutil.which("chromium-browser")
    if not exe:
        return
    while True:
        PROFILE.mkdir(parents=True, exist_ok=True)
        for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                (PROFILE / lock).unlink()
            except OSError:
                pass
        proc = await asyncio.create_subprocess_exec(
            exe, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
            "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=%d" % CHROME_PORT, "--user-data-dir=%s" % PROFILE,
            "--window-size=1280,943", "--force-device-scale-factor=1", "about:blank",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
        await asyncio.sleep(2)


@app.get("/json/version/")
@app.get("/json/version")
async def browser_version(request: Request):
    """Authenticated CDP discovery for the coding agent."""
    info = await asyncio.to_thread(chrome_http, "/json/version")
    browser_id = info["webSocketDebuggerUrl"].rsplit("/", 1)[-1]
    info["webSocketDebuggerUrl"] = str(request.url_for("agent_browser", browser_id=browser_id))
    return info


@app.websocket("/devtools/browser/{browser_id}")
async def agent_browser(ws: WebSocket, browser_id: str):
    if not browser_authorised(ws.headers) or not re.fullmatch(r"[0-9a-fA-F-]{36}", browser_id):
        await ws.close(code=4403)
        return
    await ws.accept()
    try:
        async with ws_connect(f"ws://127.0.0.1:{CHROME_PORT}/devtools/browser/{browser_id}", max_size=2**25) as upstream:
            async def from_agent():
                async for message in ws.iter_text():
                    await upstream.send(message)

            async def from_browser():
                async for message in upstream:
                    await ws.send_text(message)

            tasks = [asyncio.create_task(from_agent()), asyncio.create_task(from_browser())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass


@app.on_event("startup")
async def start_browser():
    asyncio.create_task(chrome_supervisor())


def chrome_http(path: str, method: str = "GET"):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (CHROME_PORT, path), method=method)
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read() or b"null")


@app.get("/browser/targets")
async def browser_targets():
    try:
        items = await asyncio.to_thread(chrome_http, "/json/list")
    except Exception:
        return {"up": False, "targets": []}
    pages = [{"id": t["id"], "title": str(t.get("title") or "")[:120], "url": str(t.get("url") or "")[:300]} for t in items if t.get("type") == "page"]
    return {"up": True, "targets": pages}


@app.post("/browser/new")
async def browser_new(request: Request):
    body = await request.json()
    url = str(body.get("url") or "about:blank").strip()
    if not ALLOWED_URL.match(url):
        raise HTTPException(status_code=400, detail="Use an http or https address.")
    try:
        t = await asyncio.to_thread(chrome_http, "/json/new?" + urllib.request.quote(url, safe=":/?&=%#"), "PUT")
    except Exception:
        raise HTTPException(status_code=502, detail="The browser is not running.") from None
    return {"id": t.get("id")}


@app.post("/browser/close")
async def browser_close(request: Request):
    body = await request.json()
    tid = str(body.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9]{8,64}", tid):
        raise HTTPException(status_code=400, detail="Bad tab.")
    try:
        await asyncio.to_thread(chrome_http, "/json/close/" + tid)
    except Exception:
        pass
    return {"ok": True}


@app.websocket("/browser/ws")
async def browser_ws(ws: WebSocket, target: str):
    """Pictures of one tab go out (JPEG frames); mouse, keyboard, scroll and address-bar commands come in."""
    if not authorised(ws.headers.get("x-code-token")):
        await ws.close(code=4401)
        return
    if not re.fullmatch(r"[A-Za-z0-9]{8,64}", target):
        await ws.close(code=4400)
        return
    await ws.accept()
    try:
        async with ws_connect("ws://127.0.0.1:%d/devtools/page/%s" % (CHROME_PORT, target), max_size=2**25, ping_interval=None) as cdp:
            seq = 0

            async def call(method, params=None):
                nonlocal seq
                seq += 1
                await cdp.send(json.dumps({"id": seq, "method": method, "params": params or {}}))

            await call("Page.enable")
            await call("Page.bringToFront")
            await call("Page.startScreencast", {"format": "jpeg", "quality": 62, "maxWidth": 1280, "maxHeight": 800, "everyNthFrame": 1})

            async def frames():
                async for raw in cdp:
                    m = json.loads(raw)
                    method = m.get("method")
                    if method == "Page.screencastFrame":
                        p = m["params"]
                        await ws.send_bytes(base64.b64decode(p["data"]))
                        await call("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
                    elif method == "Page.frameNavigated" and not m["params"]["frame"].get("parentId"):
                        await ws.send_text(json.dumps({"type": "url", "url": m["params"]["frame"].get("url", "")}))

            async def inputs():
                while True:
                    msg = await ws.receive()
                    if msg.get("type") == "websocket.disconnect":
                        return
                    try:
                        e = json.loads(msg.get("text") or "{}")
                    except ValueError:
                        continue
                    t = e.get("t")
                    if t == "mouse":
                        await call("Input.dispatchMouseEvent", {"type": e.get("type", "mouseMoved"), "x": float(e.get("x", 0)), "y": float(e.get("y", 0)), "button": e.get("button", "none"),
                                                                "buttons": int(e.get("buttons", 0)), "clickCount": int(e.get("clickCount", 0)), "modifiers": int(e.get("modifiers", 0))})
                    elif t == "wheel":
                        await call("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": float(e.get("x", 0)), "y": float(e.get("y", 0)), "deltaX": float(e.get("dx", 0)), "deltaY": float(e.get("dy", 0))})
                    elif t == "key":
                        p = {"type": e.get("type", "keyDown"), "key": str(e.get("key", ""))[:32], "code": str(e.get("code", ""))[:32], "modifiers": int(e.get("modifiers", 0)),
                             "windowsVirtualKeyCode": int(e.get("vk", 0)), "nativeVirtualKeyCode": int(e.get("vk", 0))}
                        if e.get("text"):
                            p["text"] = str(e["text"])[:8]
                            p["unmodifiedText"] = p["text"]
                        await call("Input.dispatchKeyEvent", p)
                    elif t == "insert":
                        await call("Input.insertText", {"text": str(e.get("text", ""))[:20000]})
                    elif t == "nav":
                        url = str(e.get("url") or "").strip()
                        if url and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", url):
                            url = "https://" + url
                        if ALLOWED_URL.match(url):
                            await call("Page.navigate", {"url": url})
                    elif t == "history" and e.get("dir") in ("back", "forward"):
                        await call("Runtime.evaluate", {"expression": "history.%s()" % e["dir"]})
                    elif t == "reload":
                        await call("Page.reload")

            tasks = [asyncio.create_task(frames()), asyncio.create_task(inputs())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in tasks:
                t.cancel()
    except Exception:
        pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass
