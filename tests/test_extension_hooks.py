"""Extension hooks: a private layer adds settings, routes and mail routes from its own folder, without patching Atrium's files."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import pytest

import config as configmod
import extension_hooks
import mail_backend as mb
from conftest import PW, PW2, login

H = {"origin": "https://app.test"}

EXTENSION = '''
from fastapi import APIRouter, Depends

CALLS = []
mine = APIRouter()


@mine.get("/hello")
async def hello():
    return {"hello": "from the layer"}


mail_extra = APIRouter()


def read_settings(cfg, data):
    cfg.layer_switch = data.get("layer_switch") is True
    CALLS.append(("read_settings", sorted(data)))


def on_app(app, cfg):
    @app.get("/layer-open-route")
    async def open_route():
        return {"open": True}
    CALLS.append(("on_app", getattr(cfg, "layer_switch", None)))


def mount_pages(app, cfg, ctx):
    app.include_router(mine, prefix="/api/mine", dependencies=[Depends(ctx.guard)])
    CALLS.append(("mount_pages", ctx.mail_backend is None))


def before_mail(app, cfg, ctx):
    @ctx.mail_backend.router.post("/layer-extra")
    async def layer_extra():
        return {"extra": True}
    ctx.mail_backend.FEATURES.add("layer-feature")
    CALLS.append(("before_mail", ctx.server_mail, ctx.mail_backend.__name__))
'''


@pytest.fixture()
def layer(tmp_path, monkeypatch):
    """A layer folder with one extension, the way a private layer would ship it."""
    base = tmp_path / "extensions"
    (base / "mine").mkdir(parents=True)
    (base / "mine" / "extension.py").write_text(EXTENSION)
    monkeypatch.setattr(extension_hooks, "DIRS", [base])
    yield base
    extension_hooks._loaded.clear()
    mb.FEATURES.discard("layer-feature")
    for r in [r for r in mb.router.routes if getattr(r, "path", "") == "/layer-extra"]:
        mb.router.routes.remove(r)


def test_without_any_extension_nothing_happens(tmp_path, monkeypatch, make):
    monkeypatch.setattr(extension_hooks, "DIRS", [tmp_path / "none"])
    assert extension_hooks.modules() == []
    extension_hooks.call("read_settings", object(), {})                      # a no-op, not an error
    c = make()
    login(c)
    assert c.get("/api/mine/hello").status_code == 404


def test_the_layer_is_found_and_its_settings_are_read_from_instance_toml(layer, tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "instance.toml").write_text('brand = "Mine"\nlayer_switch = true\n')
    cfg = configmod.load({"DATA_DIR": str(tmp_path / "d"), "INSTANCE_DIR": str(inst)})
    assert cfg.brand == "Mine" and cfg.layer_switch is True
    assert ("read_settings", ["brand", "layer_switch"]) in extension_hooks.modules()[0].CALLS
    cfg2 = configmod.load({"DATA_DIR": str(tmp_path / "d"), "INSTANCE_DIR": str(tmp_path / "no-instance")})      # no instance.toml at all
    assert cfg2.layer_switch is False
    assert ("read_settings", []) in extension_hooks.modules()[0].CALLS


def test_the_layer_can_add_open_and_owner_only_routes_without_patching_atrium(layer, make, cfg):
    cfg.layer_switch = True
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)))
    mod = extension_hooks.modules()[0]
    assert ("on_app", True) in mod.CALLS and ("mount_pages", True) in mod.CALLS
    assert c.get("/layer-open-route").json() == {"open": True}
    assert c.get("/api/mine/hello").status_code == 401                        # the guard the layer was given: signed in
    assert login(c, "bob", PW2, ip="10.5.0.1").status_code == 303
    assert c.get("/api/mine/hello").status_code == 403                        # and an owner
    c.post("/logout", headers=H)
    assert login(c, "alice", PW, ip="10.5.0.2").status_code == 303
    assert c.get("/api/mine/hello").json() == {"hello": "from the layer"}


def test_the_layer_can_add_routes_to_the_mail_router_before_it_is_mounted(layer, make, tmp_path):
    c0 = configmod.Config(data_dir=tmp_path / "dm", instance_dir=tmp_path / "im", trusted_proxies=["testclient"],
                          mail_address="me@server.test", mail_token="tok", mail_imap_host="imap.server.test")
    c = make(config=c0)
    assert ("before_mail", True, "mail_backend") in extension_hooks.modules()[0].CALLS
    assert "layer-feature" in mb.FEATURES
    assert c.post("/api/mail/layer-extra", headers=H).status_code == 401      # mounted with the mail router's guard
    login(c)
    assert c.post("/api/mail/layer-extra", headers=H).json() == {"extra": True}
    assert "layer-feature" in c.get("/api/mail/status").json()["features"]


def test_a_layer_that_fails_stops_atrium_loudly(tmp_path, monkeypatch, make):
    base = tmp_path / "extensions"
    (base / "broken").mkdir(parents=True)
    (base / "broken" / "extension.py").write_text("def on_app(app, cfg):\n    raise RuntimeError('the layer is broken')\n")
    monkeypatch.setattr(extension_hooks, "DIRS", [base])
    with pytest.raises(RuntimeError, match="the layer is broken"):
        make()
    (base / "broken" / "extension.py").write_text("this is not python (")
    extension_hooks._loaded.clear()
    with pytest.raises(SyntaxError):
        extension_hooks.modules()
    extension_hooks._loaded.clear()


def test_folders_without_an_extension_file_and_hidden_ones_are_ignored(tmp_path, monkeypatch):
    base = tmp_path / "extensions"
    for name in ("empty", ".hidden", "_private"):
        (base / name).mkdir(parents=True)
    (base / ".hidden" / "extension.py").write_text("raise RuntimeError('must not load')")
    (base / "_private" / "extension.py").write_text("raise RuntimeError('must not load')")
    (base / "note.txt").write_text("not a folder")
    monkeypatch.setattr(extension_hooks, "DIRS", [base])
    assert extension_hooks.modules() == []
    extension_hooks._loaded.clear()


def test_extensions_run_in_folder_name_order(tmp_path, monkeypatch):
    base = tmp_path / "extensions"
    order = []
    for name in ("b-second", "a-first"):
        (base / name).mkdir(parents=True)
        (base / name / "extension.py").write_text("def on_app(app, cfg):\n    cfg.order.append(%r)\n" % name)
    monkeypatch.setattr(extension_hooks, "DIRS", [base])
    cfg = type("C", (), {"order": order})()
    extension_hooks.call("on_app", object(), cfg)
    assert order == ["a-first", "b-second"]
    extension_hooks._loaded.clear()
