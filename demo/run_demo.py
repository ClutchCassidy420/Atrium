#!/usr/bin/env python3
"""Run Atrium on made-up sample data, with a stand-in for Hermes. No agent, GPU, keys or internet needed.

    pip install -r requirements.txt
    python demo/run_demo.py            # then open http://localhost:8770 and sign in as  demo / demo-demo-demo

Everything it creates lives in demo/.demo-data/ (deleted and rebuilt on every start). Nothing real is read or sent anywhere.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

PORT = int(os.environ.get("DEMO_PORT", "8770"))
HERMES_PORT = int(os.environ.get("DEMO_HERMES_PORT", "8791"))
HOST_PORT = int(os.environ.get("DEMO_HOST_PORT", "8792"))
USER, PASSWORD = "demo", "demo-demo-demo"


def note(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_data(base: Path) -> tuple[Path, Path, Path]:
    if base.exists():
        shutil.rmtree(base)
    data, instance, hermes = base / "data", base / "instance", base / "hermes-data"
    data.mkdir(parents=True)
    shutil.copytree(ROOT / "instance.example", instance)
    (instance / "instance.toml").write_text(
        'brand = "Atrium"\nagent_name = "Robo"\nnav_groups = ["Agent", "Work", "Server"]\n[login]\nmax_fails = 5\nlock_minutes = 15\n', encoding="utf-8")
    today = time.strftime("%Y-%m-%d")
    vault = hermes / "obsidian-vault"
    note(vault / "Daily" / (today + ".md"),
         "# %s\n\n## Today's Focus\n- [x] Review the quarterly numbers\n- [ ] Finish the newsletter draft\n- [ ] Reply to the supplier\n\n## Log\n- 09:10 Morning brief sent\n- 11:40 Outline for [[Projects/Newsletter]] done\n" % today)
    note(vault / "Radar" / (today + ".md"),
         "# Radar %s\n\n- **A fast local notes search** | tool | Could replace the slow search we use now | https://example.com/notes-search\n"
         "- **Open spreadsheet format update** | news | New export option that fixes our report layout | https://example.com/sheets\n"
         "- **Small business tax calendar** | reference | Dates to put in the planner | https://example.com/tax-calendar\n" % today)
    note(vault / "Projects" / "Newsletter.md", "# Newsletter\n\nQuarterly customer update. See [[People/Sam]] for the reviewer and [[Weekly/Review]].\n")
    note(vault / "People" / "Sam.md", "# Sam\n\nReviews the newsletter. Prefers short paragraphs. Part of [[Projects/Newsletter]].\n")
    note(vault / "Weekly" / "Review.md", "# Weekly review\n\nFinished 9 of 12 items. Carried three over. Related: [[Projects/Newsletter]].\n")
    note(vault / "Briefings" / ("Morning brief %s.md" % today), "# Morning brief\n\nTwo meetings today. One invoice due Friday. See [[Weekly/Review]].\n")
    note(vault / "Inbox" / "Ideas.md", "# Ideas\n\n- A recipe scaler\n- A shared packing list\n")
    note(hermes / "atrium" / "agent.json", '{"name": "Robo"}')
    (hermes / "cron").mkdir(parents=True, exist_ok=True)
    return data, instance, hermes


def main() -> int:
    base = Path(__file__).resolve().parent / ".demo-data"
    data, instance, hermes = build_data(base)
    import fake_hermes
    fake_hermes.serve(HERMES_PORT)
    import fake_host
    fake_host.serve(HOST_PORT)
    os.environ.update({
        "DATA_DIR": str(data), "INSTANCE_DIR": str(instance), "HERMES_HOME": str(hermes), "HERMES_DATA_DIR": str(hermes),
        "OBSIDIAN_VAULT_PATH": str(hermes / "obsidian-vault"), "TRUSTED_PROXIES": "127.0.0.1",
        "HERMES_API_BASE": "http://127.0.0.1:%d" % HERMES_PORT, "HERMES_API_KEY": "demo-key",
        "HERMES_DASHBOARD_BASE": "http://127.0.0.1:%d" % HERMES_PORT, "HERMES_DASHBOARD_TOKEN": "demo-token",
        "HOST_ACTION_URL": "http://127.0.0.1:%d" % HOST_PORT, "HOST_ACTION_KEY": fake_host.KEY,
    })
    import auth
    from users import UserStore
    store = UserStore(data / "users.json")
    store.add(USER, "owner", password_hash=auth.hash_password(PASSWORD))
    try:
        store.set_name(USER, "Demo")
    except Exception:       # noqa: BLE001 - the greeting name is optional
        pass
    import socket
    import uvicorn
    from main import make_app
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", PORT))
    except OSError:
        print("\nPort %d is already in use, so the demo cannot start there. Close whatever uses it, or pick another port:\n"
              "  DEMO_PORT=8780 python demo/run_demo.py     (PowerShell: $env:DEMO_PORT=8780; python demo/run_demo.py)\n" % PORT, flush=True)
        return 1
    finally:
        probe.close()
    print("\nAtrium demo is starting. Open  http://127.0.0.1:%d  and sign in as  %s / %s\n(sample data only; press Ctrl+C to stop)\n"
          % (PORT, USER, PASSWORD), flush=True)
    uvicorn.run(make_app(), host="127.0.0.1", port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
