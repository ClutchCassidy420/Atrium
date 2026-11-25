"""The shared router guard: order of checks and what it leaves on the request."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from guards import role_guard  # noqa: E402


def run(method="GET", who=None, role_ok=True, origin_ok=True, after=None):
    cfg = SimpleNamespace(allows=lambda role, perm: role_ok)
    req = SimpleNamespace(method=method, state=SimpleNamespace())
    guard = role_guard(cfg, lambda r: who, lambda r: origin_ok, "admin", denied="No.", after=after)
    asyncio.run(guard(req))
    return req


def refused(**kw):
    with pytest.raises(HTTPException) as e:
        run(**kw)
    return e.value.status_code, e.value.detail


def test_order_and_messages():
    assert refused(who=None) == (401, "Not signed in.")
    assert refused(who={"role": "x"}, role_ok=False) == (403, "No.")
    assert refused(method="POST", who={"role": "x"}, origin_ok=False) == (403, "Bad origin.")
    assert refused(who={"role": "x"}, after=lambda r, w: (_ for _ in ()).throw(HTTPException(503, "off"))) == (503, "off")


def test_reads_skip_origin_and_who_is_set():
    req = run(who={"role": "x", "user": "a"}, origin_ok=False)
    assert req.state.who["user"] == "a"
