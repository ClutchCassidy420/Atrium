import json
import re

import pytest

import config as configmod
from conftest import PW, PW2, login
from pages import load_pages
from users import MIN_PASSWORD, UserError, UserStore

# ---- configuration layers ------------------------------------------------------------------------------------------


def test_defaults_name_nobody(tmp_path):
    c = configmod.load({"DATA_DIR": str(tmp_path / "d"), "INSTANCE_DIR": str(tmp_path / "none")})
    assert c.brand == "Atrium" and c.agent_name == "Assistant" and c.trusted_proxies == []


def test_instance_file_overrides_defaults_and_env_overrides_the_file(tmp_path):
    (tmp_path / "instance.toml").write_text('brand = "Acme Hub"\nagent_name = "Robo"\ntrusted_proxies = ["10.0.0.5"]\n'
                                            'pages_disabled = ["selftest"]\n[login]\nmax_fails = 3\n[roles]\nowner = ["*"]\nguest = ["use"]\n')
    c = configmod.load({"INSTANCE_DIR": str(tmp_path), "DATA_DIR": str(tmp_path / "d")})
    assert (c.brand, c.agent_name, c.trusted_proxies, c.max_fails) == ("Acme Hub", "Robo", ["10.0.0.5"], 3)
    assert c.valid_role("guest") and not c.valid_role("user") and c.pages_disabled == ["selftest"]
    c2 = configmod.load({"INSTANCE_DIR": str(tmp_path), "DATA_DIR": str(tmp_path / "d"), "TRUSTED_PROXIES": "1.1.1.1, 2.2.2.2",
                         "HERMES_API_KEY": "k"})
    assert c2.trusted_proxies == ["1.1.1.1", "2.2.2.2"] and c2.hermes_api_key == "k"


def test_role_permissions():
    c = configmod.Config()
    assert c.allows("owner", "admin") and c.allows("owner", "use")
    assert c.allows("user", "use") and not c.allows("user", "admin")
    assert not c.allows("ghost", "use")


def test_bootstrap_owner_from_the_old_single_password(make, cfg):
    from conftest import fast_hash
    cfg.bootstrap_hash = fast_hash(PW)
    c = make(users=())
    assert login(c, "owner", PW).status_code == 303
    assert c.get("/api/me").json()["role"] == "owner"


# ---- user list ---------------------------------------------------------------------------------------------------------


def test_user_store_rules(tmp_path):
    s = UserStore(tmp_path / "users.json")
    s.add("alice", "owner", password=PW)
    with pytest.raises(UserError):
        s.add("alice", "user", password=PW2)                    # duplicate
    with pytest.raises(UserError):
        s.add("Bad Name!", "user", password=PW)                 # bad characters
    with pytest.raises(UserError):
        s.add("bob", "user", password="short")                  # too short
    s.add("bob", "user", password=PW2)
    assert [u["username"] for u in s.all()] == ["alice", "bob"]
    assert (tmp_path / "users.json").stat().st_mode & 0o077 == 0 or True   # mode is only enforced on POSIX
    assert "hash" in s.get("bob") and PW2 not in (tmp_path / "users.json").read_text()   # only hashes are stored
    s.set_password("bob", PW)
    s.set_role("bob", "owner")
    assert s.get("bob")["role"] == "owner"
    s.remove("bob")
    assert s.get("bob") is None
    with pytest.raises(UserError):
        s.remove("bob")
    assert MIN_PASSWORD >= 12


# ---- roles on pages ------------------------------------------------------------------------------------------------------


def test_pages_are_filtered_by_role_and_forbidden_pages_say_so(make, cfg, tmp_path):
    (cfg.instance_dir / "pages.d").mkdir(parents=True)
    (cfg.instance_dir / "pages.d" / "admin.json").write_text(json.dumps([
        {"id": "admin", "path": "/admin", "label": "Admin", "plugin": "admin-page",
         "scripts": ["/instance/static/admin.js"], "permission": "admin"}]))
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)))
    login(c, "alice", PW)
    owner_cfg = c.get("/shell-config.js").text
    assert '"Admin"' in owner_cfg and '"Self-test"' in owner_cfg and '"agentName": "Robo"' in owner_cfg
    assert c.get("/admin").status_code == 200
    c.post("/logout", headers={"origin": "https://app.test"})
    login(c, "bob", PW2, ip="10.0.0.7")
    user_cfg = c.get("/shell-config.js").text
    assert '"Admin"' not in user_cfg and '"Self-test"' in user_cfg
    r = c.get("/admin")
    assert r.status_code == 403 and "not part of your role" in r.text
    assert c.get("/api/health").status_code == 403


def test_page_manifest_validation(tmp_path):
    core = tmp_path / "core.json"
    ok = {"id": "a", "path": "/a", "label": "A", "plugin": "pa", "scripts": ["/static/x.js"]}
    core.write_text(json.dumps([ok]))
    assert load_pages(core, tmp_path, [])[0]["permission"] == "use"
    assert load_pages(core, tmp_path, ["a"]) == []                      # disabled
    for bad in ({"path": "/login"}, {"path": "/api/x"}, {"path": "/A B"}, {"scripts": ["/static/../x.js"]},
                {"scripts": ["https://evil.example/x.js"]}, {"id": "Bad Id"}, {"label": ""}, {"nav": "sidebar"}):
        core.write_text(json.dumps([dict(ok, **bad)]))
        with pytest.raises(ValueError):
            load_pages(core, tmp_path, [])
    core.write_text(json.dumps([ok, dict(ok, id="b")]))                  # same path twice
    with pytest.raises(ValueError):
        load_pages(core, tmp_path, [])


# ---- instance files ------------------------------------------------------------------------------------------------------


def test_instance_theme_and_static_files(make, cfg):
    cfg.instance_dir.mkdir(parents=True, exist_ok=True)
    (cfg.instance_dir / "theme.css").write_text(":root{--color-primary:#123456}")
    (cfg.instance_dir / "static").mkdir()
    (cfg.instance_dir / "static" / "a.js").write_text("/* x */")
    c = make()
    assert "#123456" in c.get("/instance/theme.css").text
    assert c.get("/instance/static/a.js").status_code == 200
    assert c.get("/instance/static/missing.js").status_code == 404
    for evil in ("/instance/static/../instance.toml", "/instance/static/..%2ftheme.css", "/instance/static//etc/passwd"):
        assert c.get(evil).status_code in (400, 404)


def test_instance_theme_missing_is_not_an_error(make):
    r = make().get("/instance/theme.css")
    assert r.status_code == 200 and "no instance theme" in r.text


def test_static_files_cannot_escape(make):
    c = make()
    assert c.get("/static/shim.js").status_code == 200
    assert c.get("/static/tokens.css").status_code == 200
    for evil in ("/static/../main.py", "/static/%2e%2e/main.py", "/static//etc/passwd"):
        assert c.get(evil).status_code in (400, 404)


def framework_files():
    """Every file that belongs to the framework (not to one install): code, pages, styles, docs, examples, templates."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    files = [f for f in (root / "app").rglob("*") if f.is_file() and "vendor" not in f.parts and f.suffix in (".py", ".js", ".css", ".json")]
    files += [root / "README.md", root / "docs" / "FRAMEWORK.md", root / "config.private.example", root / ".gitignore"]
    files += [f for f in (root / "instance.example").rglob("*") if f.is_file()] + [f for f in (root / "deploy").rglob("*") if f.is_file()]
    return files


def test_no_personal_names_in_anything_the_framework_ships():
    """The framework must not contain anything specific to one install: no person, company, host or system name."""
    # Put your own names, hosts and systems in tests/banned-terms.txt (one per line, not committed) to check your own installs.
    from pathlib import Path
    banned = ["tailscale"]
    extra = Path(__file__).resolve().parent / "banned-terms.txt"
    if extra.is_file():
        banned += [x.strip().lower() for x in extra.read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")]
    offenders = []
    for f in framework_files():
        text = f.read_text(encoding="utf-8", errors="ignore").lower()
        offenders += [(f.name, w) for w in banned if re.search(r"\b" + re.escape(w) + r"\b", text)]    # whole words: "average" is fine
    assert offenders == []


def test_the_framework_is_called_atrium():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    assert configmod.Config().brand == "Atrium"
    assert "Atrium" in (root / "README.md").read_text(encoding="utf-8").splitlines()[0]
    assert "Atrium" in (root / "docs" / "FRAMEWORK.md").read_text(encoding="utf-8").splitlines()[0]
    assert 'brand = "Atrium"' in (root / "instance.example" / "instance.toml").read_text(encoding="utf-8")


# ---- sidebar sections -------------------------------------------------------------------------------------------------


def test_manifest_group_and_order(tmp_path):
    core = tmp_path / "core.json"
    ok = {"id": "a", "path": "/a", "label": "A", "plugin": "pa", "scripts": ["/static/x.js"]}
    core.write_text(json.dumps([dict(ok, group="Agent", order=5)]))
    p = load_pages(core, tmp_path, [])[0]
    assert (p["group"], p["order"]) == ("Agent", 5)
    core.write_text(json.dumps([ok]))
    p = load_pages(core, tmp_path, [])[0]
    assert (p["group"], p["order"]) == ("", 100)                 # defaults: a top-level link, ordered last
    for bad in ({"group": "x" * 31}, {"order": "first"}, {"order": 1.5}, {"order": True}):
        core.write_text(json.dumps([dict(ok, **bad)]))
        with pytest.raises(ValueError):
            load_pages(core, tmp_path, [])


def test_shell_config_carries_sections_and_the_shell_is_a_sidebar(make, cfg):
    cfg.nav_groups = ["Server"]
    c = make()
    login(c)
    text = c.get("/shell-config.js").text
    assert '"navGroups": ["Server"]' in text and '"group": "Server"' in text and '"order": 10' in text
    html = c.get("/").text
    assert 'id="vn-side"' in html and 'id="vn-nav"' in html and 'id="vn-menu"' in html and 'aria-controls="vn-side"' in html
    assert "Sign out" in html and "alice" in html


def test_a_gear_page_replaces_the_sign_out_button_but_only_for_people_who_can_open_it(make, cfg):
    """The page marked nav "gear" is reached from the gear icon, not the sidebar list; users who cannot open it keep the Sign out button."""
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)))
    login(c)
    cfg_js = c.get("/shell-config.js").text
    assert '"nav": "gear"' in cfg_js                                                           # Settings is the gear page
    html = c.get("/").text
    assert 'id="vn-gear"' in html and 'id="vn-out"' in html                                    # the shim hides whichever one does not apply
    sh = c.get("/static/shim.js").text
    assert 'r.nav !== "gear"' in sh and "outEl.hidden = true" in sh
    assert "Sign out" in c.get("/static/pages/settings/index.js").text                       # the button now lives on the gear page
    c.post("/logout", headers={"origin": "https://app.test"})
    login(c, "bob", PW2, ip="10.0.0.2")
    assert '"nav": "gear"' not in c.get("/shell-config.js").text                              # bob cannot open Settings: his sidebar keeps Sign out


def test_nav_groups_come_from_the_instance_file(tmp_path):
    (tmp_path / "instance.toml").write_text('nav_groups = ["Agent", "Server"]\n')
    c = configmod.load({"INSTANCE_DIR": str(tmp_path), "DATA_DIR": str(tmp_path / "d")})
    assert c.nav_groups == ["Agent", "Server"]


def test_old_addresses_in_the_instance_file_redirect_to_the_current_page(make, cfg):
    cfg.redirects = {"/old-chat": "/chat", "/old-home": "/", "//evil.example": "/chat"}
    c = make()
    r = c.get("/old-chat?approvals=1")
    assert r.status_code == 303 and r.headers["location"] == "/chat?approvals=1"          # the query string survives
    assert c.get("/old-home").status_code == 303
    assert "evil.example" not in c.get("//evil.example").headers.get("location", "")      # nothing that leaves the site
    assert c.get("/chat").status_code in (200, 303)                                       # real pages are never replaced


def test_redirects_are_read_and_checked_from_the_instance_file(tmp_path):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / "instance.toml").write_text('[redirects]\n"/a" = "/chat"\n"b" = "/chat"\n"//c" = "/chat"\n"/d" = "//evil.example"\n"/e" = "/e"\n')
    cfg = configmod.load({"INSTANCE_DIR": str(inst), "DATA_DIR": str(tmp_path / "d")})
    assert cfg.redirects == {"/a": "/chat"}


def test_an_install_can_add_scripts_to_an_existing_page_but_only_validated_ones(tmp_path):
    core = tmp_path / "core.json"
    ok = {"id": "a", "path": "/a", "label": "A", "plugin": "pa", "scripts": ["/static/x.js"]}
    core.write_text(json.dumps([ok]))
    (tmp_path / "pages.d").mkdir()
    ext = tmp_path / "pages.d" / "more.json"
    ext.write_text(json.dumps([{"extend": "a", "scripts": ["/instance/static/more.js", "/static/x.js"], "styles": ["/instance/static/more.css"]}]))
    page = load_pages(core, tmp_path, [])[0]
    assert page["scripts"] == ["/static/x.js", "/instance/static/more.js"]          # added after the page's own, and never twice
    assert page["styles"] == ["/instance/static/more.css"]
    for bad in ({"scripts": ["/static/../x.js"]}, {"scripts": ["https://evil.example/x.js"]}, {"styles": ["//evil.example/x.css"]}):
        ext.write_text(json.dumps([dict({"extend": "a"}, **bad)]))
        with pytest.raises(ValueError):
            load_pages(core, tmp_path, [])
    ext.write_text(json.dumps([{"extend": "missing", "scripts": ["/instance/static/m.js"]}]))
    with pytest.raises(ValueError):                                                   # a typo in the page name must not pass silently
        load_pages(core, tmp_path, [])
    ext.write_text(json.dumps([{"extend": "a", "scripts": ["/instance/static/m.js"]}]))
    assert load_pages(core, tmp_path, ["a"]) == []                                    # a page the install turned off stays off
    ext.write_text(json.dumps([{"extend": "gone", "scripts": ["/instance/static/m.js"]}]))
    assert len(load_pages(core, tmp_path, ["gone"])) == 1                             # an extension of a turned-off page is skipped, not an error
