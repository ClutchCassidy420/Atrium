"""Settings for one install, in three layers (a later layer wins):

  1. built-in defaults (generic: nothing here names a person, a host or a product)
  2. the instance file  <instance dir>/instance.toml   (this install's name, brand, proxies, pages, roles)
  3. the environment    (secrets and deployment wiring: Hermes address and keys, folders)

The framework never contains anything personal. Everything personal lives in the instance folder, which is not part of
the framework's repository. See docs/FRAMEWORK.md.
"""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# What each role may do. "*" means everything. Pages name the permission they need. An instance can change these.
DEFAULT_ROLES = {
    "owner": ["*"],
    "user": ["use"],
}


@dataclass
class Config:
    brand: str = "Atrium"                 # shown in the title and the top bar
    agent_name: str = "Assistant"            # what the pages call the agent
    data_dir: Path = Path("/data")           # the app's own writable folder (users, sessions)
    instance_dir: Path = Path("/instance")   # this install's files: instance.toml, theme.css, extra pages
    trusted_proxies: list[str] = field(default_factory=list)   # addresses allowed to tell us the real client address
    roles: dict[str, list[str]] = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_ROLES.items()})
    pages_disabled: list[str] = field(default_factory=list)    # page ids to hide
    nav_icons: dict = field(default_factory=dict)              # sidebar section or folder name -> a symbol or emoji
    nav_groups: list[str] = field(default_factory=list)        # sidebar sections in this order (others follow alphabetically)
    max_fails: int = 5                       # wrong passwords before a lockout
    lock_minutes: int = 15
    bootstrap_hash: str = ""                 # optional: creates the first owner when no users exist yet
    bootstrap_user: str = "owner"
    hermes_api_base: str = ""
    hermes_api_key: str = ""
    hermes_dashboard_base: str = ""
    hermes_dashboard_token: str = ""
    hermes_data_dir: str = ""
    push_proxy: str = ""                       # outbound proxy for push messages, if the app has no direct internet
    push_enabled: bool = False                 # phone notifications and the installable phone app
    tts_url: str = ""                          # text-to-speech endpoint for read-aloud (optional)
    code_url: str = ""                         # the coding workspace service (optional; see code-sidecar/)
    code_token: str = ""                       # the secret shared with it
    action_gate_url: str = ""                  # the approval service for proposed actions (optional)
    host_action_url: str = ""                  # the host helper for server actions: updates, reboot, shutdown (optional)
    host_action_key: str = ""                  # the shared key the helper expects
    deck_dir: str = ""                       # folder of status files written by this install's own jobs (monitors, system, weather...)
    systems: dict = field(default_factory=dict)   # the services, websites and model lists to show (instance systems.json)

    def valid_role(self, role: str) -> bool:
        return role in self.roles

    def allows(self, role: str, permission: str | None) -> bool:
        perms = self.roles.get(role, [])
        return "*" in perms or not permission or permission in perms


def _split(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def load(env: dict | None = None) -> Config:
    e = os.environ if env is None else env
    cfg = Config()
    cfg.data_dir = Path(e.get("DATA_DIR", str(cfg.data_dir)))
    cfg.instance_dir = Path(e.get("INSTANCE_DIR", str(cfg.instance_dir)))

    f = cfg.instance_dir / "instance.toml"
    if f.is_file():
        data = tomllib.loads(f.read_text(encoding="utf-8"))
        cfg.brand = str(data.get("brand", cfg.brand))
        cfg.agent_name = str(data.get("agent_name", cfg.agent_name))
        cfg.trusted_proxies = [str(x) for x in data.get("trusted_proxies", cfg.trusted_proxies)]
        cfg.pages_disabled = [str(x) for x in data.get("pages_disabled", cfg.pages_disabled)]
        cfg.nav_groups = [str(x) for x in data.get("nav_groups", cfg.nav_groups)]
        cfg.nav_icons = {str(k)[:40]: str(v)[:8] for k, v in dict(data.get("nav_icons", {}) or {}).items()}
        login = data.get("login", {})
        cfg.max_fails = int(login.get("max_fails", cfg.max_fails))
        cfg.lock_minutes = int(login.get("lock_minutes", cfg.lock_minutes))
        if isinstance(data.get("roles"), dict):
            cfg.roles = {str(k): [str(p) for p in v] for k, v in data["roles"].items()}
        cfg.bootstrap_user = str(data.get("bootstrap_user", cfg.bootstrap_user))

    if e.get("TRUSTED_PROXIES"):
        cfg.trusted_proxies = _split(e["TRUSTED_PROXIES"])
    cfg.bootstrap_hash = e.get("OWNER_PASSWORD_HASH", "")
    cfg.hermes_api_base = e.get("HERMES_API_BASE", "")
    cfg.hermes_api_key = e.get("HERMES_API_KEY", "")
    cfg.hermes_dashboard_base = e.get("HERMES_DASHBOARD_BASE", "")
    cfg.hermes_dashboard_token = e.get("HERMES_DASHBOARD_TOKEN", "")
    cfg.hermes_data_dir = e.get("HERMES_DATA_DIR", "")
    cfg.deck_dir = e.get("DECK_DATA_DIR", "")
    cfg.tts_url = e.get("TTS_URL", "")
    cfg.code_url = e.get("CODE_URL", "")
    cfg.code_token = e.get("CODE_TOKEN", "")
    cfg.push_enabled = e.get("PUSH_ENABLED", "") == "1"
    cfg.push_proxy = e.get("PUSH_PROXY", "")
    cfg.action_gate_url = e.get("ACTION_GATE_URL", "")
    cfg.host_action_url = e.get("HOST_ACTION_URL", "")
    cfg.host_action_key = e.get("HOST_ACTION_KEY", "")
    sysf = cfg.instance_dir / "systems.json"
    if sysf.is_file():
        try:
            data = json.loads(sysf.read_text(encoding="utf-8"))
            cfg.systems = data if isinstance(data, dict) else {}
        except ValueError:
            cfg.systems = {}
    return cfg
