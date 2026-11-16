"""Live agent: what the 3D avatar stage needs from the server.

Adapted from the Atrium Loom avatar plugin backend (labelled adaptations: the agent's name is not needed here, the voice server address
comes from TTS_URL like the Chat page, and the routes are mounted by pagesmount.py behind the owner guard).

Routes (under /api/plugins/atrium-avatar):
  GET  /status        is a character file in place, is the voice server up
  GET  /model         the character file (.glb) from <HERMES_HOME>/atrium/avatar/avatar.glb. It is the install's own file and is never part of the project.
  GET  /clips/<name>  a motion-clip library (.glb) from <HERMES_HOME>/atrium/avatar/clips/<name>.glb
  GET  /projects      sticky notes for the whiteboard, from the vault's Projects folder (read-only)
  POST /speak         {"text"} -> {"audio": base64 mp3, "words": [{"w","start","end"}] in milliseconds, "voice"}
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

router = APIRouter()

VOICE_IDS = {"af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "bf_emma", "bf_isabella"}
MAX_TEXT = 1200
MAX_AUDIO_BYTES = 8_000_000
PROJECT_STATUS = {"active", "blocked", "waiting", "done", "idea"}
_gate = asyncio.Semaphore(1)                      # one voice request at a time: the graphics card is shared


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or "/opt/data")


def _tts_base() -> str:
    u = urlsplit(os.environ.get("TTS_URL") or "http://atrium-kokoro:8880/v1/audio/speech")
    return "%s://%s" % (u.scheme, u.netloc)


def _voice_now() -> str:
    try:
        v = json.loads((_home() / "atrium" / "voice.json").read_text(encoding="utf-8")).get("voice")
        if v in VOICE_IDS:
            return v
    except (OSError, ValueError, AttributeError):
        pass
    return "af_heart"


def _voice_up() -> bool:
    try:
        with urllib.request.urlopen(_tts_base() + "/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


@router.get("/status")
async def status() -> dict:
    p = _home() / "atrium" / "avatar" / "avatar.glb"
    has = p.is_file()
    return {"model": has, "model_mb": round(p.stat().st_size / 1e6, 1) if has else 0,
            "model_hint": "atrium/avatar/avatar.glb", "voice": _voice_now(), "voice_up": await asyncio.to_thread(_voice_up)}


@router.get("/model")
async def model():
    p = _home() / "atrium" / "avatar" / "avatar.glb"
    if not p.is_file():
        raise HTTPException(status_code=404, detail="No character file yet. Put a .glb file at atrium/avatar/avatar.glb in the data folder.")
    return FileResponse(p, media_type="model/gltf-binary", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/clips/{name}")
async def clips(name: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name):
        raise HTTPException(status_code=400, detail="Bad clip library name.")
    p = _home() / "atrium" / "avatar" / "clips" / (name + ".glb")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="No such clip library.")
    return FileResponse(p, media_type="model/gltf-binary", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/projects")
async def projects() -> dict:
    """One sticky note per project, from the Projects folder of the vault. The first line of text is the summary; an optional
    `status:` line in the front matter (active, blocked, waiting, done, idea) sets the colour. Read-only."""
    def read() -> list[dict]:
        folder = _home() / "obsidian-vault" / "Projects"
        out: list[dict] = []
        if not folder.is_dir():
            return out
        files = [p for p in folder.glob("*.md") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[:30]:
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")[:4000]
            except OSError:
                continue
            status, body = "active", text
            m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
            if m:
                body = text[m.end():]
                sm = re.search(r"^status:\s*([A-Za-z]+)\s*$", m.group(1), re.M)
                if sm and sm.group(1).lower() in PROJECT_STATUS:
                    status = sm.group(1).lower()
            summary = ""
            for line in body.splitlines():
                t = line.strip()
                if t and not t.startswith("#"):
                    summary = re.sub(r"[*_`>\[\]]+", "", t).strip()
                    break
            out.append({"name": p.stem[:60], "summary": summary[:160], "status": status, "mtime": int(p.stat().st_mtime)})
        return out
    return {"notes": await asyncio.to_thread(read)}


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    voice: Optional[str] = Field(default=None, max_length=40)


def _clean(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)            # code blocks are not read aloud
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)          # [label](link) -> label
    text = re.sub(r"[*_#>~|]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:MAX_TEXT]


def _synth_sync(text: str, voice: str) -> dict:
    payload = json.dumps({"model": "kokoro", "input": text, "voice": voice, "response_format": "mp3",
                          "stream": False, "return_timestamps": True, "speed": 1.0}).encode("utf-8")
    req = urllib.request.Request(_tts_base() + "/dev/captioned_speech", data=payload, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read(MAX_AUDIO_BYTES * 2)
    data = json.loads(raw)
    audio = data.get("audio") or ""
    if not audio or len(audio) > MAX_AUDIO_BYTES * 4 // 3:
        raise ValueError("no audio")
    words = []
    for w in data.get("timestamps") or []:
        word = str(w.get("word") or "")
        if not re.search(r"[A-Za-z0-9]", word):                     # bare punctuation tokens carry no mouth shape
            continue
        s, e = float(w.get("start_time") or 0), float(w.get("end_time") or 0)
        words.append({"w": word, "start": max(0, round(s * 1000)), "end": max(0, round(e * 1000))})
    return {"audio": audio, "words": words}


@router.post("/speak")
async def speak(body: SpeakBody) -> dict:
    text = _clean(body.text)
    if not text:
        raise HTTPException(status_code=400, detail="Nothing to say.")
    voice = body.voice if body.voice in VOICE_IDS else _voice_now()
    async with _gate:
        try:
            out = await asyncio.to_thread(_synth_sync, text, voice)
        except Exception:
            raise HTTPException(status_code=502, detail="The voice is not available right now.") from None
    out["voice"] = voice
    return out
