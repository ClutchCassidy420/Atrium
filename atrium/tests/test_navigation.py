"""Sidebar navigation settings: accordion sections are a per-install choice that reaches the page (the behaviour itself is in nav.test.js)."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import pytest

import config as configmod
from conftest import PW, login


def shell_config(client) -> dict:
    return json.loads(re.search(r"window\.__APP_CONFIG__=(.*);", client.get("/shell-config.js").text).group(1))


def test_accordion_is_off_by_default_and_on_only_for_a_real_true(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir()
    env = {"INSTANCE_DIR": str(inst), "DATA_DIR": str(tmp_path / "data")}
    assert configmod.load(env).accordion_nav is False                                    # no instance.toml at all
    for text, expected in (("accordion_nav = true\n", True), ("accordion_nav = false\n", False), ('accordion_nav = "true"\n', False), ("accordion_nav = 1\n", False)):
        (inst / "instance.toml").write_text(text, encoding="utf-8")
        assert configmod.load(env).accordion_nav is expected, text


@pytest.mark.parametrize("value", [True, False])
def test_the_setting_reaches_the_page(make, cfg, value):
    cfg.accordion_nav = value
    c = make(users=(("alice", "owner", PW),), config=cfg)
    assert login(c, "alice", PW, ip="10.7.0.1").status_code == 303
    assert shell_config(c)["accordionNav"] is value


def test_the_shell_has_the_head_the_rail_toggle_needs(make):
    c = make(users=(("alice", "owner", PW),))
    assert login(c, "alice", PW, ip="10.7.0.2").status_code == 303
    page = c.get("/").text
    for part in ('class="vn-side-head"', 'id="vn-nav"', 'id="vn-side"', 'class="vn-side-foot"'):
        assert part in page, part
