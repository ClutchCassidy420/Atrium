"""Per-person chat model: which model answers a person's chats, when it is not the install's default.

A person (an owner, from Settings) can pick a model from a cloud provider that is already connected to the agent. It is saved per person, and
sent to Hermes with every chat run as a locked model, so nothing else (a profile, another person's choice) can change which model answers.
Choosing "the install's default" removes the override.

What a selection looks like on disk (one small file per person, named by a hash of the user name, never the name itself):
  {"engine": "cloud", "provider": "anthropic", "model": "claude-..."}      a model from a connected provider
  {"engine": "local"}                                                       the install's default model, even if the whole install is switched to cloud

An install that manages its own local models (for example by switching a model server) can set LOCAL_ROUTE to a function
`route(personal: bool) -> dict` that returns the run fields for its default model; it may raise HTTPException (for example 503 while a switch is in progress).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Callable, Optional

DATA: Optional[Path] = None
LOCAL_ROUTE: Optional[Callable[[bool], dict]] = None
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,239}$")          # a model or provider id as Hermes writes them


def configure(data_dir) -> None:
    global DATA
    DATA = Path(data_dir)


def path(user: str) -> Path:
    base = DATA if DATA is not None else Path(os.environ.get("DATA_DIR", "/data"))
    return base / "chat-models" / (hashlib.sha256(str(user).lower().encode()).hexdigest() + ".json")


def _valid(value) -> Optional[dict]:
    """A selection read from disk, or None if it is not one this code could have written (a damaged or hand-edited file is ignored, never trusted)."""
    if not isinstance(value, dict):
        return None
    if value.get("engine") == "local":
        return {"engine": "local"}
    if value.get("engine") == "cloud":
        provider, model = value.get("provider"), value.get("model")
        if isinstance(provider, str) and isinstance(model, str) and NAME.fullmatch(provider) and NAME.fullmatch(model):
            return {"engine": "cloud", "provider": provider, "model": model}
    return None


def selection(user: Optional[str]) -> dict:
    if user:
        try:
            return _valid(json.loads(path(user).read_text(encoding="utf-8"))) or {}
        except (OSError, ValueError):
            pass
    return {}


def save(user: str, value: dict) -> None:
    value = _valid(value)
    if value is None:
        raise ValueError("Not a valid model selection.")
    file = path(user)
    file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(mode="w", dir=file.parent, delete=False, encoding="utf-8")
    try:
        with tmp:                                   # closed before the rename: Windows cannot rename a file that is still open
            json.dump(value, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp.name, file)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def override(user: Optional[str], legacy: Optional[dict] = None) -> dict:
    """The extra fields for a run: the person's own choice first, then the install-wide engine (`legacy`), then nothing (the install's default)."""
    personal = selection(user)
    model = personal or legacy or {}
    if model.get("engine") in ("cloud", "claude") and model.get("model"):
        route = {"model": model["model"], "provider": model.get("provider") or "anthropic"}
        if personal:
            route["require_model_lock"] = True              # Hermes must use exactly this model, whatever else is configured
        return route
    if LOCAL_ROUTE is not None:
        return LOCAL_ROUTE(bool(personal))
    return {}
