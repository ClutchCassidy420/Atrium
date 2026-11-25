"""Chat: the agent reads replies aloud (split out of chat_backend.py; the URLs are the same).

The words go to Kokoro (the text-to-speech server on this host, internal network only) and the audio comes straight back; nothing
leaves the server, whichever engine wrote the reply. The page cuts a reply into short pieces so playback starts after the first sentence.

  GET/POST /voice          which voice is picked (owners change it, see chatmount)
  POST     /speak          {text, voice?} -> audio/mpeg
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.request
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from agentkit import AgentRoute, atomic_write_text
from voices import VOICE_CHOICES, VOICE_IDS, voice_file, voice_now

router = APIRouter(route_class=AgentRoute)

KOKORO_URL = os.environ.get("TTS_URL") or "http://atrium-kokoro:8880/v1/audio/speech"     # chatmount sets this from the install's settings
_speak_gate = asyncio.Semaphore(1)                  # one voice request at a time: the graphics card is shared


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=700)
    voice: Optional[str] = Field(default=None, max_length=40)


class VoiceBody(BaseModel):
    voice: str = Field(max_length=40)


@router.get("/voice")
async def voice_info() -> dict:
    return {"voice": voice_now(), "choices": [{"id": v, "label": n} for v, n in VOICE_CHOICES]}


@router.post("/voice")
async def voice_set(body: VoiceBody) -> dict:
    if body.voice not in VOICE_IDS:
        raise HTTPException(status_code=400, detail="That voice is not available.")
    f = voice_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(f, json.dumps({"voice": body.voice}))
    return {"voice": body.voice}


def _synth_sync(text: str, voice: str) -> bytes:
    payload = json.dumps({"model": "kokoro", "input": text, "voice": voice, "response_format": "mp3", "speed": 1.0}).encode("utf-8")
    req = urllib.request.Request(KOKORO_URL, data=payload, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read(8_000_000)


@router.post("/speak")
async def speak(body: SpeakBody) -> Response:
    voice = body.voice if body.voice in VOICE_IDS else voice_now()
    text = re.sub(r"\s+", " ", body.text).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Nothing to read.")
    async with _speak_gate:
        try:
            audio = await asyncio.to_thread(_synth_sync, text, voice)
        except Exception:
            raise HTTPException(status_code=502, detail="The voice is not available right now.") from None
    if not audio:
        raise HTTPException(status_code=502, detail="The voice returned nothing.")
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
