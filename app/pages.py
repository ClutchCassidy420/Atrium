"""Page manifests: which pages exist, where they live, and which permission each one needs.

Core pages come from app/pages.json. An install adds its own in <instance dir>/pages.d/*.json (a list of the same
objects). A page is a script run by the shim (static/shim.js); the framework never needs to know what it does.

  { "id": "chat", "path": "/chat", "label": "Chat", "plugin": "chat-page", "group": "Agent", "order": 10,
    "scripts": ["/static/plugins/chat/index.js"], "styles": [], "permission": "use" }

"group" puts the page under a dropdown section in the sidebar (empty = a top-level link); "subgroup" nests it one level deeper, under a
folder of that name inside the section; "order" sorts within its section.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

RESERVED = ("/login", "/logout", "/api", "/static", "/instance", "/healthz", "/shell-config.js")
PATH_RE = re.compile(r"^(/|/[a-z0-9][a-z0-9/_-]{0,60})$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
ASSET_RE = re.compile(r"^/(static|instance/static)/[A-Za-z0-9._/-]{1,200}$")


def _clean(raw: dict, source: str) -> dict:
    page = {
        "id": str(raw.get("id", "")), "path": str(raw.get("path", "")), "label": str(raw.get("label", "")),
        "plugin": str(raw.get("plugin", "")), "scripts": list(raw.get("scripts", [])), "styles": list(raw.get("styles", [])),
        "permission": raw.get("permission") or "use",
        "group": str(raw.get("group") or "").strip(),
        "subgroup": str(raw.get("subgroup") or "").strip(),
        "aliases": [str(a) for a in (raw.get("aliases") or [])],
        "order": raw.get("order", 100),
        "icon": str(raw.get("icon") or "").strip(),            # a short symbol or emoji shown beside the label in the sidebar
        "nav": str(raw.get("nav") or "").strip(),          # "" = listed in the sidebar; "gear" = reached by the gear icon at the bottom of the sidebar instead
    }
    for a in page["aliases"]:
        if not PATH_RE.match(a) or a == "/" or any(a == r or a.startswith(r + "/") for r in RESERVED):
            raise ValueError("%s: bad alias %r for page %r" % (source, a, page["id"]))
    if len(page["subgroup"]) > 30 or (page["subgroup"] and not page["group"]):
        raise ValueError("%s: the subgroup of page %r is too long, or has no group" % (source, page["id"]))
    if len(page["icon"]) > 8:
        raise ValueError("%s: the icon of page %r is too long (use one symbol or emoji)" % (source, page["id"]))
    if page["nav"] not in ("", "gear"):
        raise ValueError("%s: the nav of page %r must be empty or \"gear\"" % (source, page["id"]))
    if len(page["group"]) > 30:
        raise ValueError("%s: the group name of page %r is too long" % (source, page["id"]))
    if not isinstance(page["order"], int) or isinstance(page["order"], bool):
        raise ValueError("%s: the order of page %r must be a whole number" % (source, page["id"]))
    if not ID_RE.match(page["id"]):
        raise ValueError("%s: bad page id %r" % (source, page["id"]))
    if not PATH_RE.match(page["path"]) or any(page["path"] == r or page["path"].startswith(r + "/") for r in RESERVED):
        raise ValueError("%s: bad or reserved page path %r" % (source, page["path"]))
    if not page["label"] or not page["plugin"]:
        raise ValueError("%s: page %r needs a label and a plugin name" % (source, page["id"]))
    for a in page["scripts"] + page["styles"]:
        if ".." in a or not ASSET_RE.match(str(a)):
            raise ValueError("%s: bad asset path %r" % (source, a))
    return page


def load_pages(core_file: Path, instance_dir: Path, disabled: list[str]) -> list[dict]:
    raw: list[tuple[dict, str]] = []
    for f in [core_file] + sorted((Path(instance_dir) / "pages.d").glob("*.json")):
        if not f.is_file():
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        for item in data if isinstance(data, list) else [data]:
            raw.append((item, f.name))
    pages, seen_ids, seen_paths = [], set(), set()
    for item, src in raw:
        page = _clean(item, src)
        if page["id"] in disabled:
            continue
        paths = [page["path"]] + page["aliases"]
        if page["id"] in seen_ids or any(p in seen_paths for p in paths) or len(set(paths)) != len(paths):
            raise ValueError("%s: page %r repeats an id or a path" % (src, page["id"]))
        seen_ids.add(page["id"])
        seen_paths.update(paths)
        pages.append(page)
    return pages
