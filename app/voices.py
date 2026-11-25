"""The voices the app can read aloud with, and which one the install has picked. Chat and Live both use this."""
from __future__ import annotations

import json
from pathlib import Path

from agentkit import hermes_home

VOICE_CHOICES = [
    ("af_heart", "Heart (warm, the default)"), ("af_bella", "Bella"), ("af_nicole", "Nicole (soft)"), ("af_sarah", "Sarah"),
    ("af_sky", "Sky"), ("bf_emma", "Emma (British)"), ("bf_isabella", "Isabella (British)"),
]
VOICE_IDS = {v for v, _ in VOICE_CHOICES}
DEFAULT_VOICE = "af_heart"


def voice_file() -> Path:
    return hermes_home() / "atrium" / "voice.json"


def voice_now() -> str:
    try:
        v = json.loads(voice_file().read_text(encoding="utf-8")).get("voice")
        if v in VOICE_IDS:
            return v
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_VOICE
