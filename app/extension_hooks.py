"""Extension hooks: how a private layer adds its own settings, routes and pages' backends to Atrium without patching any of Atrium's files.

A layer puts a file at  app/extensions/<name>/extension.py  (its own folder, which is not part of Atrium). Atrium loads every such file at
start and calls whichever of these functions it defines, at these moments:

  read_settings(cfg, data)           while the settings are read; data is the parsed instance.toml ({} when there is none). Set your own
                                     attributes on cfg (a plain object), for example cfg.my_switch = data.get("my_switch") is True.
  on_app(app, cfg)                   right after the web app is created (add routes or middleware that need nothing else).
  mount_pages(app, cfg, ctx)         while the owner pages' routes are mounted, after Files and Calendar and before Email. ctx.guard is the
                                     owner-only dependency: app.include_router(router, prefix="/api/mine", dependencies=[Depends(ctx.guard)]).
  before_mail(app, cfg, ctx)         just before the Email routes are mounted, so a layer can add routes to the mail router. ctx.mail_backend is
                                     the mail module and ctx.server_mail says whether a server-wide mailbox is configured.

ctx also carries hermes, current (who is signed in) and same_origin. A hook that raises stops Atrium from starting with that error: a layer's
own code failing should be loud, never a silently missing feature. Atrium itself does nothing here when there are no extensions.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

DIRS = [Path(__file__).resolve().parent / "extensions"]       # where layers put their folders (tests point this somewhere else)
_loaded: dict = {}


def modules() -> list[ModuleType]:
    key = tuple(str(d) for d in DIRS)
    if key not in _loaded:
        found = []
        for base in DIRS:
            if not base.is_dir():
                continue
            for folder in sorted(base.iterdir()):
                f = folder / "extension.py"
                if folder.is_dir() and not folder.name.startswith((".", "_")) and f.is_file():
                    spec = importlib.util.spec_from_file_location("atrium_extension_" + folder.name.replace("-", "_").replace(".", "_"), f)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    found.append(module)
        _loaded[key] = found
    return _loaded[key]


def call(name: str, *args) -> None:
    """Run one hook in every extension that defines it, in folder-name order."""
    for module in modules():
        fn = getattr(module, name, None)
        if callable(fn):
            fn(*args)
