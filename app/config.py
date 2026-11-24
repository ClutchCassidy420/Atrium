"""Settings for one install, in three layers (a later layer wins):

  1. built-in defaults (generic: nothing here names a person, a host or a product)
  2. the instance file  <instance dir>/instance.toml   (this install's name, brand, proxies, pages, roles)
  3. the environment    (secrets and deployment wiring: Hermes address and keys, folders)

The framework never contains anything personal. Everything personal lives in the instance folder, which is not part of
the framework's repository. See docs/FRAMEWORK.md.
"""
from __future__ import annotations

import re

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import extension_hooks

# What each role may do. "*" means everything. Pages name the permission they need. An instance can change these.
DEFAULT_ROLES = {
    "owner": ["*"],
    "user": ["use"],
}


def clean_presets(raw) -> list:
    """The look presets from instance.toml, or [] when they are not in the expected shape (a name and a small map of plain values)."""
    if not isinstance(raw, list) or not 0 < len(raw) <= 40:
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"name", "look"} or not isinstance(item.get("name"), str) or not 0 < len(item["name"].strip()) <= 40:
            return []
        look = item.get("look")
        if not isinstance(look, dict) or not look or len(look) > 24:
            return []
        for k, v in look.items():
            if not isinstance(k, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", k):
                return []
            if not (isinstance(v, bool) or (isinstance(v, (int, float)) and abs(v) < 1e6) or (isinstance(v, str) and len(v) <= 40)):
                return []
        out.append({"name": item["name"].strip(), "look": dict(look)})
    return out


@dataclass
class Config:
    avatar_presets: list = field(default_factory=list)       # extra looks for Settings > Avatar: [{"name": ..., "look": {...}}] from instance.toml
    accordion_nav: bool = False           # sidebar sections behave as an accordion: opening one closes the others, and nothing is remembered
    brand: str = "Atrium"                 # shown in the title and the top bar
    agent_name: str = "Assistant"            # what the pages call the agent
    data_dir: Path = Path("/data")           # the app's own writable folder (users, sessions)
    instance_dir: Path = Path("/instance")   # this install's files: instance.toml, theme.css, extra pages
    trusted_proxies: list[str] = field(default_factory=list)   # addresses allowed to tell us the real client address
    roles: dict[str, list[str]] = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_ROLES.items()})
    pages_disabled: list[str] = field(default_factory=list)    # page ids to hide
    redirects: dict = field(default_factory=dict)              # old address -> current address (links in old notifications and bookmarks keep working)
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
    nextcloud_url: str = ""                    # Files and Calendar: where the app reaches Nextcloud (e.g. over a private network)
    nextcloud_host: str = ""                   # the Host header Nextcloud expects, if the address above is an IP
    nextcloud_user: str = ""
    nextcloud_password: str = ""               # an app password, revocable in Nextcloud
    nextcloud_public_url: str = ""             # the address you use in a browser (for "Open in Nextcloud" links)
    mail_address: str = ""                     # Email: the mailbox the app reads and sends from
    mail_token: str = ""                       # an app token for that mailbox (revocable), not the mailbox password
    mail_imap_host: str = ""
    mail_imap_port: int = 993                  # implicit TLS only
    mail_imap_tls_server_name: str = ""        # the name on the server's certificate, if you reach it by another address
    mail_smtp_host: str = ""                   # SMTP over implicit TLS
    mail_smtp_port: int = 465
    mail_smtp_tls_server_name: str = ""
    mail_webmail_url: str = ""                 # optional: the full webmail, for an "Open webmail" link
    mail_accounts_key: str = ""                # Email: secret that encrypts people's saved mailbox logins (kept in the settings, not beside the data)
    rea_url: str = ""                          # the REA workbench service (optional; see rea-sidecar/)
    rea_token: str = ""                        # the secret shared with it
    media_allow_private: bool = False         # Media: let owners connect a Jellyfin or Emby server on the local network (http, IP addresses, one-word names)
    mail_user_accounts: bool = False          # Email: let every signed-in person connect their own mailbox (an app password), not only the server-wide one
    kanban_workspaces_dir: str = ""            # where the agent's container keeps kanban card workspaces (default /opt/data/kanban/workspaces)
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
    data: dict = {}
    if f.is_file():
        data = tomllib.loads(f.read_text(encoding="utf-8"))
        cfg.accordion_nav = data.get("accordion_nav") is True
        cfg.avatar_presets = clean_presets(data.get("avatar_presets"))
        cfg.brand = str(data.get("brand", cfg.brand))
        cfg.agent_name = str(data.get("agent_name", cfg.agent_name))
        cfg.trusted_proxies = [str(x) for x in data.get("trusted_proxies", cfg.trusted_proxies)]
        cfg.pages_disabled = [str(x) for x in data.get("pages_disabled", cfg.pages_disabled)]
        cfg.nav_groups = [str(x) for x in data.get("nav_groups", cfg.nav_groups)]
        cfg.nav_icons = {str(k)[:40]: str(v)[:8] for k, v in dict(data.get("nav_icons", {}) or {}).items()}
        cfg.redirects = {str(k)[:80]: str(v)[:80] for k, v in dict(data.get("redirects", {}) or {}).items()
                         if str(k).startswith("/") and str(v).startswith("/") and not str(k).startswith("//") and not str(v).startswith("//") and str(k) != str(v)}
        login = data.get("login", {})
        cfg.max_fails = int(login.get("max_fails", cfg.max_fails))
        cfg.lock_minutes = int(login.get("lock_minutes", cfg.lock_minutes))
        if isinstance(data.get("roles"), dict):
            cfg.roles = {str(k): [str(p) for p in v] for k, v in data["roles"].items()}
        cfg.bootstrap_user = str(data.get("bootstrap_user", cfg.bootstrap_user))
    extension_hooks.call("read_settings", cfg, data)            # a private layer reads its own keys from instance.toml (app/extension_hooks.py)

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
    cfg.kanban_workspaces_dir = e.get("HERMES_KANBAN_WORKSPACES", "")
    cfg.nextcloud_url = e.get("NEXTCLOUD_URL", "")
    cfg.nextcloud_host = e.get("NEXTCLOUD_HOST", "")
    cfg.nextcloud_user = e.get("NEXTCLOUD_USER", "")
    cfg.nextcloud_password = e.get("NEXTCLOUD_APP_PASSWORD", "")
    cfg.nextcloud_public_url = e.get("NEXTCLOUD_PUBLIC_URL", "")
    cfg.mail_address = e.get("MAIL_ADDRESS", "")
    cfg.mail_token = e.get("MAIL_TOKEN", "")
    cfg.mail_imap_host = e.get("MAIL_IMAP_HOST", "")
    cfg.mail_imap_port = int(e.get("MAIL_IMAP_PORT", "993") or 993)
    cfg.mail_imap_tls_server_name = e.get("MAIL_IMAP_TLS_SERVER_NAME", "")
    cfg.mail_smtp_host = e.get("MAIL_SMTP_HOST", "")
    cfg.mail_smtp_port = int(e.get("MAIL_SMTP_PORT", "465") or 465)
    cfg.mail_smtp_tls_server_name = e.get("MAIL_SMTP_TLS_SERVER_NAME", "")
    cfg.mail_webmail_url = e.get("MAIL_WEBMAIL_URL", "")
    cfg.mail_user_accounts = e.get("MAIL_USER_ACCOUNTS", "").strip().lower() in ("1", "true", "yes", "on")
    cfg.mail_accounts_key = e.get("MAIL_ACCOUNTS_KEY", "").strip()
    cfg.rea_url = e.get("REA_URL", "")
    cfg.rea_token = e.get("REA_TOKEN", "")
    cfg.media_allow_private = e.get("MEDIA_ALLOW_PRIVATE", "").strip().lower() in ("1", "true", "yes", "on")
    if cfg.mail_accounts_key and len(cfg.mail_accounts_key) < 32:
        raise ValueError("MAIL_ACCOUNTS_KEY must be at least 32 characters.")
    sysf = cfg.instance_dir / "systems.json"
    if sysf.is_file():
        try:
            data = json.loads(sysf.read_text(encoding="utf-8"))
            cfg.systems = data if isinstance(data, dict) else {}
        except ValueError:
            cfg.systems = {}
    return cfg
