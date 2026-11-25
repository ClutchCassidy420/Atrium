"""Shared HTTP clients: one per event loop and per settings, never carried onto another loop."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from httpkit import shared  # noqa: E402


def test_one_client_per_loop_and_settings():
    async def grab():
        return shared(), shared(), shared(follow_redirects=True)

    a, b, c = asyncio.run(grab())
    assert a is b and c is not a
    assert asyncio.run(grab())[0] is not a
