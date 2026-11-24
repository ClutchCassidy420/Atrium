"""The REA workbench service: uploads, a fixed list of read-only REA analyses, and saved results, for the REA page.

It runs in its own container with no way out to the internet or the rest of the server. The web app is its only client: every request carries a
shared secret (REA_TOKEN) and the container has no published port. Programs that are uploaded are only ever *read* (static analysis by REA and
Ghidra); nothing here runs them. Only the analyses in COMMANDS can be started, as a fixed argument list (no shell), with a time limit, an output
limit and one job at a time.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

WORK = Path(os.environ.get("REA_WORK", "/work"))
TOKEN = os.environ.get("REA_TOKEN", "")
REA = os.environ.get("REA_BIN", "rea")
MAX_UPLOAD = int(os.environ.get("REA_MAX_UPLOAD", str(1024 ** 3)))              # 1 GB per upload
MAX_STORED = int(os.environ.get("REA_MAX_STORED", str(6 * 1024 ** 3)))          # everything kept at once
MAX_UNPACKED = int(os.environ.get("REA_MAX_UNPACKED", str(3 * 1024 ** 3)))      # one unpacked folder
MAX_FILES = 60000
MAX_OUTPUT = int(os.environ.get("REA_MAX_OUTPUT", str(600 * 1024 ** 2)))        # one result
JOB_SECONDS = int(os.environ.get("REA_JOB_SECONDS", "1500"))
KEEP_SECONDS = int(os.environ.get("REA_KEEP_SECONDS", str(24 * 3600)))
MAX_QUEUE = 6
SUMMARY_LIMIT = 40 * 1024 ** 2                                                    # the largest result that is outlined
ID = re.compile(r"^[0-9a-f]{12}$")
NAME = re.compile(r"[^A-Za-z0-9._() -]")

# The analyses a person can start. {path} is the uploaded file or unpacked folder; {arg} is the one typed value, checked against `arg_pattern`.
# Only analyses that read the target are listed (REA's runtime capture and process commands, which would run a program, are not).
NATIVE = ["--provider", "ghidra"]
COMMANDS = [
    {"id": "js-app", "group": "Applications", "label": "JavaScript / Electron app", "target": "any",
     "help": "Modules, imports, routes and Electron boundaries of an app. Upload the app folder as a .zip, or an .asar file.", "argv": ["analyze-javascript-application", "{path}", "--json"]},
    {"id": "managed", "group": "Applications", "label": ".NET assembly: overview", "target": "file",
     "help": "Metadata, references and structure of a .NET .dll or .exe (read only, nothing is run).", "argv": ["inspect-managed-artifact", "{path}", "--json"]},
    {"id": "managed-members", "group": "Applications", "label": ".NET assembly: types and methods", "target": "file",
     "help": "The types and members inside a .NET assembly. Optionally name one type (for example Example.Parser).", "argv": ["inspect-managed-members", "{path}", "--json"],
     "arg_flag": "--type", "arg_label": "Type name (optional)", "arg_pattern": r"[A-Za-z0-9_.`+<>]{1,200}", "arg_optional": True},
    {"id": "pe-imports", "group": "Windows programs", "label": "Windows program: imports", "target": "file",
     "help": "Which DLLs and functions a Windows .exe or .dll uses.", "argv": ["inspect-pe-imports", "{path}", "--json"]},
    {"id": "pe-certs", "group": "Windows programs", "label": "Windows program: signatures", "target": "file",
     "help": "Authenticode signatures and the certificates behind them.", "argv": ["inspect-pe-certificates", "{path}", "--json"]},
    {"id": "shortcut", "group": "Windows programs", "label": "Windows shortcut (.lnk)", "target": "file",
     "help": "Where a shortcut points and with what arguments.", "argv": ["inspect-windows-shortcut", "{path}", "--json"]},
    {"id": "go", "group": "Programs by language", "label": "Go program", "target": "file",
     "help": "Compiler version, modules and build settings embedded in a Go program.", "argv": ["inspect-go-binary", "{path}", "--json"]},
    {"id": "rust", "group": "Programs by language", "label": "Rust program", "target": "file",
     "help": "Crates and versions embedded in a Rust program.", "argv": ["inspect-rust-binary", "{path}", "--json"]},
    {"id": "pyc", "group": "Programs by language", "label": "Python bytecode (.pyc)", "target": "file",
     "help": "What a compiled Python module contains.", "argv": ["inspect-python-pyc", "{path}", "--json"]},
    {"id": "nuget", "group": "Programs by language", "label": "NuGet package (.nupkg)", "target": "file",
     "help": "Identity, dependencies and files of a NuGet package.", "argv": ["inspect-nuget-nupkg", "{path}", "--json"]},
    {"id": "artifact", "group": "Other files", "label": "Packed file or firmware", "target": "any",
     "help": "What is inside game archives, firmware images and similar packed files.", "argv": ["inspect-artifact", "{path}", "--json"]},
    {"id": "ghidra", "group": "Deep analysis (Ghidra)", "label": "Disassemble and decompile: overview", "target": "file",
     "help": "Ghidra analyzes the program: functions, strings and structure. The first run on a big program can take many minutes.", "argv": ["analyze", "{path}"] + NATIVE + ["--json"]},
    {"id": "ghidra-search", "group": "Deep analysis (Ghidra)", "label": "Search a program", "target": "file",
     "help": "Find a name, string or function in an analyzed program.", "argv": ["search", "{path}", "{arg}"] + NATIVE + ["--json"],
     "arg_label": "Search for", "arg_pattern": r"[A-Za-z0-9_.:@$?<>!~*\\/ -]{1,150}"},
    {"id": "ghidra-function", "group": "Deep analysis (Ghidra)", "label": "Show a function", "target": "file",
     "help": "One function by name or address.", "argv": ["function", "{path}", "{arg}"] + NATIVE + ["--json"],
     "arg_label": "Function name or address", "arg_pattern": r"[A-Za-z0-9_.:@$?<>!~*]{1,150}"},
    {"id": "ghidra-decompile", "group": "Deep analysis (Ghidra)", "label": "Decompile at an address", "target": "file",
     "help": "Readable pseudo-code for the code at an address (for example 0x401000).", "argv": ["decompile", "{path}", "{arg}"] + NATIVE + ["--json"],
     "arg_label": "Address", "arg_pattern": r"(0x)?[0-9A-Fa-f]{1,16}"},
    {"id": "ghidra-xrefs", "group": "Deep analysis (Ghidra)", "label": "Who uses an address", "target": "file",
     "help": "Where an address or function is referenced from.", "argv": ["xrefs", "{path}", "{arg}"] + NATIVE + ["--json"],
     "arg_label": "Address", "arg_pattern": r"(0x)?[0-9A-Fa-f]{1,16}"},
    {"id": "doctor", "group": "This workbench", "label": "Check the analysis engine", "target": "none",
     "help": "Confirms REA and Ghidra are installed and working.", "argv": ["doctor", "--provider", "ghidra", "--json"]},
]
BY_ID = {c["id"]: c for c in COMMANDS}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_pool = ThreadPoolExecutor(max_workers=1)
_lock = threading.Lock()
_running: dict[str, subprocess.Popen] = {}


def authorised(value) -> bool:
    return bool(TOKEN) and bool(value) and hmac.compare_digest(value, TOKEN)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path != "/healthz" and not authorised(request.headers.get("x-rea-token")):
        return JSONResponse({"detail": "Not allowed."}, status_code=401)
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


def _dirs():
    for sub in ("files", "jobs", "home"):
        (WORK / sub).mkdir(parents=True, exist_ok=True)


def _meta(path: Path):
    try:
        return json.loads((path / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_meta(path: Path, data: dict):
    tmp = path / "meta.json.tmp"
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path / "meta.json")


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())


def _stored() -> int:
    return sum(_size(p) for sub in ("files", "jobs") for p in (WORK / sub).glob("*") if p.is_dir())


def _sweep():
    """Old uploads and results are deleted, so nothing sensitive stays around."""
    cutoff = time.time() - KEEP_SECONDS
    for sub in ("files", "jobs"):
        for d in (WORK / sub).glob("*"):
            m = _meta(d) or {}
            if d.is_dir() and (m.get("created") or d.stat().st_mtime) < cutoff and m.get("status") not in ("running", "queued"):
                shutil.rmtree(d, ignore_errors=True)


def clean_name(value: str) -> str:
    name = NAME.sub("_", os.path.basename(str(value or "").replace("\\", "/"))).strip(" .")[:100]
    return name or "upload"


@app.get("/commands")
def commands():
    return {"commands": [{k: c[k] for k in ("id", "group", "label", "help", "target") if k in c} | {"arg_label": c.get("arg_label", ""), "arg_optional": bool(c.get("arg_optional"))}
                         for c in COMMANDS]}


@app.get("/files")
def files():
    _dirs()
    _sweep()
    out = [dict(m, id=d.name) for d in sorted((WORK / "files").glob("*")) if (m := _meta(d))]
    return {"files": sorted(out, key=lambda m: -m["created"]), "stored": _stored(), "limit": MAX_STORED}


def _unpack(archive: Path, into: Path) -> int:
    """Unpack a zip safely: no paths outside the folder, no links, bounded size and count."""
    total = 0
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        if len(infos) > MAX_FILES:
            raise HTTPException(status_code=400, detail="That zip has too many files.")
        for info in infos:
            parts = [p for p in info.filename.replace("\\", "/").split("/") if p not in ("", ".")]
            if not parts or ".." in parts or info.filename.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", info.filename):
                raise HTTPException(status_code=400, detail="That zip has an unsafe path in it.")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise HTTPException(status_code=400, detail="That zip contains a link, which is not allowed.")
            target = into.joinpath(*[NAME.sub("_", p) for p in parts])
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            total += info.file_size
            if total > MAX_UNPACKED:
                raise HTTPException(status_code=400, detail="That zip is too big once unpacked.")
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with z.open(info) as src, open(target, "wb") as dst:
                while chunk := src.read(1 << 20):
                    written += len(chunk)
                    if written > info.file_size + 1 or written > MAX_UNPACKED:
                        raise HTTPException(status_code=400, detail="That zip is not what it says it is.")
                    dst.write(chunk)
    return total


@app.post("/files")
async def upload(request: Request, name: str = "upload", unpack: int = 0):
    _dirs()
    _sweep()
    declared = int(request.headers.get("content-length") or 0)
    if declared > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="That file is too large.")
    if _stored() + declared > MAX_STORED:
        raise HTTPException(status_code=507, detail="The workbench is full. Delete some uploads first.")
    fid = uuid.uuid4().hex[:12]
    folder = WORK / "files" / fid
    data = folder / "data"
    data.mkdir(parents=True)
    safe = clean_name(name)
    target = data / safe
    try:
        got = 0
        with open(target, "wb") as fh:
            async for chunk in request.stream():
                got += len(chunk)
                if got > MAX_UPLOAD:
                    raise HTTPException(status_code=413, detail="That file is too large.")
                fh.write(chunk)
        if not got:
            raise HTTPException(status_code=400, detail="That file is empty.")
        kind, shown, path = "file", safe, target
        if unpack and safe.lower().endswith(".zip"):
            root = data / "unpacked"
            root.mkdir()
            try:
                _unpack(target, root)
            except zipfile.BadZipFile:
                raise HTTPException(status_code=400, detail="That is not a valid zip file.") from None
            target.unlink()
            kind, path = "folder", root
            shown = safe[:-4] or safe
        _write_meta(folder, {"name": shown, "kind": kind, "size": _size(path), "created": time.time(), "path": str(path)})
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return {"file": dict(_meta(folder), id=fid)}


@app.delete("/files/{fid}")
def delete_file(fid: str):
    if not ID.match(fid) or not (WORK / "files" / fid).is_dir():
        raise HTTPException(status_code=404, detail="No such upload.")
    shutil.rmtree(WORK / "files" / fid, ignore_errors=True)
    return {"ok": True}


def _argv(command: dict, path: Path | None, arg: str) -> list[str]:
    out = []
    for part in command["argv"]:
        if part == "{path}":
            out.append(str(path))
        elif part == "{arg}":
            out.append(arg)
        else:
            out.append(part)
    if arg and "arg_flag" in command:
        out += [command["arg_flag"], arg]
    return out


def _run(jid: str):
    folder = WORK / "jobs" / jid
    meta = _meta(folder)
    command = BY_ID[meta["command"]]
    path = Path(meta["target"]) if meta.get("target") else None
    meta.update(status="running", started=time.time())
    _write_meta(folder, meta)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(WORK / "home"), "LANG": "C.UTF-8", "GHIDRA_INSTALL_DIR": os.environ.get("GHIDRA_INSTALL_DIR", "/opt/ghidra"),
           "JAVA_HOME": os.environ.get("JAVA_HOME", ""), "NODE_OPTIONS": "--max-old-space-size=3072", "NO_COLOR": "1", "CI": "1"}
    out, err = folder / "result.json", folder / "stderr.txt"
    status, code, note = "done", None, ""
    try:
        with open(out, "wb") as o, open(err, "wb") as e:
            proc = subprocess.Popen([REA] + _argv(command, path, meta.get("arg", "")), stdout=o, stderr=e, stdin=subprocess.DEVNULL, env=env, cwd=str(WORK / "home"), start_new_session=True)
            _running[jid] = proc
            deadline = time.time() + JOB_SECONDS
            while proc.poll() is None:
                if time.time() > deadline:
                    status, note = "failed", "It took longer than %d minutes and was stopped." % (JOB_SECONDS // 60)
                elif out.stat().st_size > MAX_OUTPUT:
                    status, note = "failed", "The result was too large and was stopped."
                elif _running.get(jid) is None:
                    status, note = "failed", "Stopped."
                if status == "failed":
                    os.killpg(proc.pid, signal.SIGKILL)
                    break
                time.sleep(0.5)
            code = proc.wait()
            if status == "done" and code != 0:
                status = "failed"
                note = "REA reported a problem (exit %s)." % code
    except Exception:
        status, note = "failed", "Could not start the analysis."
    finally:
        _running.pop(jid, None)
    meta.update(status=status, ended=time.time(), exit=code, note=note, size=out.stat().st_size if out.exists() else 0)
    if status == "failed":
        try:
            meta["error"] = (err.read_text(encoding="utf-8", errors="replace") or "")[-1500:]
        except OSError:
            pass
    _write_meta(folder, meta)


@app.post("/jobs")
async def start(request: Request):
    _dirs()
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad request.") from None
    command = BY_ID.get(str((body or {}).get("command") or ""))
    if not command:
        raise HTTPException(status_code=400, detail="Unknown analysis.")
    target, fid = None, str(body.get("file") or "")
    if command["target"] != "none":
        if not ID.match(fid) or not (WORK / "files" / fid).is_dir():
            raise HTTPException(status_code=404, detail="Choose an uploaded file first.")
        fmeta = _meta(WORK / "files" / fid)
        target = fmeta["path"]
        if command["target"] == "file" and fmeta["kind"] != "file":
            raise HTTPException(status_code=400, detail="That analysis needs a single file, not a folder.")
    arg = str(body.get("arg") or "").strip()
    if "arg_pattern" in command:
        if not arg and not command.get("arg_optional"):
            raise HTTPException(status_code=400, detail="%s is needed." % command["arg_label"])
        if arg and not re.fullmatch(command["arg_pattern"], arg):
            raise HTTPException(status_code=400, detail="That %s has characters that are not allowed." % command["arg_label"].lower())
    elif arg:
        arg = ""
    with _lock:
        active = [m for d in (WORK / "jobs").glob("*") if (m := _meta(d)) and m.get("status") in ("queued", "running")]
        if len(active) >= MAX_QUEUE:
            raise HTTPException(status_code=429, detail="Too many analyses are waiting. Try again in a few minutes.")
        if _stored() > MAX_STORED:
            raise HTTPException(status_code=507, detail="The workbench is full. Delete some uploads or results first.")
        jid = uuid.uuid4().hex[:12]
        folder = WORK / "jobs" / jid
        folder.mkdir(parents=True)
        _write_meta(folder, {"id": jid, "command": command["id"], "label": command["label"], "file": fid, "file_name": (fmeta["name"] if target else ""),
                             "target": target, "arg": arg, "status": "queued", "created": time.time()})
    _pool.submit(_run, jid)
    return {"job": _public(_meta(folder))}


def _public(m: dict) -> dict:
    return {k: m.get(k) for k in ("id", "command", "label", "file", "file_name", "arg", "status", "created", "started", "ended", "size", "note", "error")}


@app.get("/jobs")
def jobs():
    _dirs()
    out = [_public(m) for d in (WORK / "jobs").glob("*") if (m := _meta(d))]
    return {"jobs": sorted(out, key=lambda m: -m["created"])[:60]}


def _job(jid: str) -> dict:
    m = _meta(WORK / "jobs" / jid) if ID.match(jid) else None
    if not m:
        raise HTTPException(status_code=404, detail="No such analysis.")
    return m


@app.get("/jobs/{jid}")
def job(jid: str):
    return {"job": _public(_job(jid))}


@app.delete("/jobs/{jid}")
def delete_job(jid: str):
    m = _job(jid)
    proc = _running.get(jid)
    if proc:
        _running[jid] = None
        return {"ok": True, "stopping": True}
    if m.get("status") == "queued":
        return {"ok": False}
    shutil.rmtree(WORK / "jobs" / jid, ignore_errors=True)
    return {"ok": True}


def outline(value, depth=0, limit=60):
    """A short picture of the result: names, kinds and counts, a few levels deep."""
    if isinstance(value, dict):
        if depth >= 3:
            return {"type": "object", "keys": len(value)}
        items = list(value.items())
        return {"type": "object", "keys": len(value), "items": {k: outline(v, depth + 1, limit) for k, v in items[:limit]}, "more": max(0, len(items) - limit)}
    if isinstance(value, list):
        sample = [outline(v, depth + 1, limit) for v in value[:3]] if depth < 3 else []
        return {"type": "list", "length": len(value), "sample": sample}
    if isinstance(value, str):
        return {"type": "text", "value": value[:200], "length": len(value)}
    return {"type": type(value).__name__, "value": value}


@app.get("/jobs/{jid}/summary")
def summary(jid: str):
    m = _job(jid)
    out = WORK / "jobs" / jid / "result.json"
    if m.get("status") != "done" or not out.exists():
        raise HTTPException(status_code=409, detail="There is no result yet.")
    size = out.stat().st_size
    preview = out.read_bytes()[:60000].decode("utf-8", errors="replace")
    outlined = None
    if size <= SUMMARY_LIMIT:
        try:
            outlined = outline(json.loads(out.read_text(encoding="utf-8")))
        except ValueError:
            outlined = None
    return {"size": size, "outline": outlined, "preview": preview, "truncated": size > 60000}


@app.get("/jobs/{jid}/download")
def download(jid: str):
    _job(jid)
    out = WORK / "jobs" / jid / "result.json"
    if not out.exists():
        raise HTTPException(status_code=404, detail="No result.")
    return FileResponse(out, media_type="application/json", filename="rea-%s.json" % jid)
