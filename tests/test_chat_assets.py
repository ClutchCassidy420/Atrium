"""The chat page loads its two helper modules before itself (the page reads them when it starts), and the files exist."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from conftest import PW, login

APP = Path(__file__).resolve().parent.parent / "app"


def chat_page(pages):
    return next(p for p in pages if p["id"] == "chat")


def test_the_helpers_load_before_the_page_and_exist():
    scripts = chat_page(json.loads((APP / "pages.json").read_text(encoding="utf-8")))["scripts"]
    assert scripts[-1] == "/static/pages/chat/index.js"
    for needed in ("/static/pages/chat/approval.js", "/static/pages/chat/livespeech.js"):
        assert needed in scripts and scripts.index(needed) < scripts.index("/static/pages/chat/index.js"), needed
    for s in scripts:
        assert (APP / s.lstrip("/")).is_file(), s


def test_the_page_reaches_the_browser_with_the_helpers_in_that_order(make):
    c = make(users=(("alice", "owner", PW),))
    assert login(c, "alice", PW, ip="10.9.0.1").status_code == 303
    pages = json.loads(c.get("/shell-config.js").text.split("=", 1)[1].rstrip(";"))["pages"]
    scripts = chat_page(pages)["scripts"]
    assert scripts.index("/static/pages/chat/approval.js") < scripts.index("/static/pages/chat/index.js")
    for s in scripts:
        assert c.get(s).status_code == 200, s


def test_the_page_script_only_uses_what_the_helpers_export():
    page = (APP / "static/pages/chat/index.js").read_text(encoding="utf-8")
    assert "window.__APP_CHAT_APPROVAL__.makeCard" in page and "window.__APP_CHAT_LIVE__" in page
    approval = (APP / "static/pages/chat/approval.js").read_text(encoding="utf-8")
    live = (APP / "static/pages/chat/livespeech.js").read_text(encoding="utf-8")
    assert "root.__APP_CHAT_APPROVAL__ = api" in approval and "root.__APP_CHAT_LIVE__ = api" in live
    assert "LIVE.cut" in page and "LIVE.MIN_FIRST" in page and "LIVE.MIN_NEXT" in page and "(L.started ? 60 : 25)" not in page
