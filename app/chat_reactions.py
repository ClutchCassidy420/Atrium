"""Emoji reactions on chat messages, kept per chat in one small JSON file in the app's data folder.

A person reacts to any message in a chat they may open; clicking the same emoji again takes it back. The agent's own
reactions are not stored here: it writes a marker in its reply and the page shows it (see chat/index.js), so they
survive in the transcript by themselves.

Messages are identified by their position in the chat (0 is the first message), which does not change because chats only grow.
"""
from __future__ import annotations

import json
import threading
import unicodedata
from pathlib import Path

from agentkit import atomic_write_text

MAX_INDEX = 20000
MAX_KINDS = 8            # different emojis on one message
MAX_CHATS = 5000
_ALLOWED = {"So", "Sk"}  # pictographs and skin-tone modifiers; joiners and the emoji selector are allowed below


def clean_emoji(value) -> str:
    """The emoji as sent, or "" if it is anything else (letters, digits, spaces, markup, too long)."""
    if not isinstance(value, str) or not value or len(value) > 12:
        return ""
    got_picture = False
    for ch in value:
        cat = unicodedata.category(ch)
        if cat in _ALLOWED or ch in "‍️⃣":
            got_picture = got_picture or cat == "So"
            continue
        return ""
    return value if got_picture else ""


class Reactions:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(data), 0o600)

    @staticmethod
    def view(chat: dict, user: str) -> dict:
        """{"3": [{"emoji": "👍", "count": 2, "mine": true}]} for the page; names are never sent."""
        out = {}
        for idx, kinds in (chat or {}).items():
            row = [{"emoji": e, "count": len(who), "mine": user in who} for e, who in kinds.items() if who]
            if row:
                out[idx] = row
        return out

    def get(self, sid: str, user: str) -> dict:
        return self.view(self._load().get(sid, {}), user)

    def toggle(self, sid: str, index: int, emoji: str, user: str) -> dict:
        """Add the person's reaction, or remove it if they already had it. Returns the page view of that chat."""
        emoji = clean_emoji(emoji)
        if not emoji or not isinstance(index, int) or isinstance(index, bool) or not 0 <= index <= MAX_INDEX:
            raise ValueError("That is not a reaction.")
        with self.lock:
            data = self._load()
            if sid not in data and len(data) >= MAX_CHATS:
                raise ValueError("Too many chats have reactions.")
            chat = data.setdefault(sid, {})
            kinds = chat.setdefault(str(index), {})
            who = kinds.get(emoji, [])
            if user in who:
                who.remove(user)
            else:
                if emoji not in kinds and len(kinds) >= MAX_KINDS:
                    raise ValueError("That message already has as many different reactions as it can hold.")
                who.append(user)
            if who:
                kinds[emoji] = who
            else:
                kinds.pop(emoji, None)
            if not kinds:
                chat.pop(str(index), None)
            if not chat:
                data.pop(sid, None)
            self._save(data)
            return self.view(data.get(sid, {}), user)

    def forget(self, sid: str) -> None:
        with self.lock:
            data = self._load()
            if data.pop(sid, None) is not None:
                self._save(data)
