"""Container entry point: `uvicorn run:app`."""
import os

import config as configmod
from main import make_app

os.umask(0o002)      # files this app creates are group-writable, so the agent (same group) can read and clean up shared files

_cfg = configmod.load()
app = make_app(_cfg, secure_cookie=not _cfg.plain_http)      # plain HTTP only when the install asks for it (ALLOW_PLAIN_HTTP=1)
