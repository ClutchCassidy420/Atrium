"""Color themes: the palettes are complete and readable, pages follow the tokens, and each person's choice is saved and applied on first paint."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import pytest

import themes
from conftest import PW, PW2, fast_hash, login
from users import UserError, UserStore

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
H = {"origin": "https://app.test"}
REQUIRED = ["--color-background", "--color-foreground", "--color-midground", "--color-card", "--color-muted", "--color-muted-foreground", "--color-primary",
            "--color-primary-foreground", "--color-accent", "--color-secondary", "--color-ring", "--color-border", "--primary-rgb", "--accent-rgb",
            "--secondary-rgb", "--line-rgb", "--surface-deep", "--stage-bg", "--page-background"]


def declarations(block: str) -> dict:
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"(--[a-z-]+):\s*([^;]+);", block)}


def blocks() -> dict:
    css = (STATIC / "themes.css").read_text(encoding="utf-8")
    found = {m.group(1): declarations(m.group(2)) for m in re.finditer(r'html\[data-theme="([a-z]+)"\]\s*\{(.*?)\n\}', css, re.S)}
    root = re.search(r":root\s*\{(.*?)\n\}", (STATIC / "tokens.css").read_text(encoding="utf-8"), re.S)
    found[themes.DEFAULT] = declarations(root.group(1))
    return found


def rgb(hexcolor: str):
    h = hexcolor.strip().lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def luminance(c):
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(rgb(a)), luminance(rgb(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ---------------------------------------------------------------- the palettes

def test_the_list_and_the_stylesheet_agree():
    ids = [t["id"] for t in themes.THEMES]
    assert len(ids) == len(set(ids)) and themes.DEFAULT in ids and set(blocks()) == set(ids)
    for t in themes.THEMES:
        assert t["label"] and t["note"] and len(t["swatch"]) == 4 and all(re.fullmatch(r"#[0-9a-f]{6}", c) for c in t["swatch"])


@pytest.mark.parametrize("theme", [t["id"] for t in themes.THEMES])
def test_every_theme_defines_every_token(theme):
    missing = [k for k in REQUIRED if k not in blocks()[theme]]
    assert not missing, missing


@pytest.mark.parametrize("theme", [t["id"] for t in themes.THEMES])
def test_the_rgb_triplets_match_their_colors(theme):
    d = blocks()[theme]
    for hexname, tripname in (("--color-primary", "--primary-rgb"), ("--color-accent", "--accent-rgb"), ("--color-secondary", "--secondary-rgb")):
        assert tuple(int(x) for x in d[tripname].split(",")) == rgb(d[hexname]), (theme, tripname)
    border = re.match(r"rgba\((\d+),\s*(\d+),\s*(\d+),", d["--color-border"])
    assert tuple(int(x) for x in d["--line-rgb"].split(",")) == tuple(int(x) for x in border.groups()), theme


@pytest.mark.parametrize("theme", [t["id"] for t in themes.THEMES])
def test_text_is_readable_in_every_theme(theme):
    d = blocks()[theme]
    assert contrast(d["--color-foreground"], d["--color-background"]) >= 7, "body text"
    assert contrast(d["--color-muted-foreground"], d["--color-background"]) >= 4.5, "secondary text"
    assert contrast(d["--color-primary-foreground"], d["--color-primary"]) >= 4.5, "text on the primary color"


def test_swatches_show_the_theme_they_stand_for():
    for t in themes.THEMES:
        d = blocks()[t["id"]]
        assert t["swatch"][0].lower() == d["--color-background"].lower() and t["swatch"][2].lower() == d["--color-primary"].lower()


# ---------------------------------------------------------------- pages follow the tokens

def test_no_page_hard_codes_a_brand_color_the_themes_would_miss():
    literal = re.compile(r"rgba\(\s*(?:139\s*,\s*123\s*,\s*255|255\s*,\s*111\s*,\s*181|61\s*,\s*214\s*,\s*245|160\s*,\s*170\s*,\s*255)\s*,|(?<!, )#(?:8b7bff|ff6fb5|3dd6f5|0d1226|05070f)\b", re.I)
    offenders = []
    for f in sorted(STATIC.rglob("*.css")):
        if f.name in ("tokens.css", "themes.css") or "vendor" in f.parts:
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if literal.search(line):
                offenders.append("%s:%d" % (f.relative_to(STATIC), n))
    assert not offenders, offenders


# ---------------------------------------------------------------- saving and applying

def test_clean_accepts_only_known_ids():
    assert themes.clean("ember") == "ember" and themes.DEFAULT == "abyss"
    for bad in (None, "", "EMBER", "../x", "ember ", 5, ["ember"], {"id": "ember"}):
        assert themes.clean(bad) is None


def test_the_store_keeps_a_theme_per_person(tmp_path):
    store = UserStore(tmp_path / "users.json")
    store.add("alice", "owner", password_hash=fast_hash(PW))
    store.add("bob", "user", password_hash=fast_hash(PW))
    store.set_theme("Alice", "forest")
    assert store.get("alice")["theme"] == "forest" and "theme" not in store.get("bob")
    with pytest.raises(UserError):
        store.set_theme("nobody", "forest")


def test_routes_need_a_sign_in_and_the_same_origin(make):
    c = make(users=(("alice", "owner", PW),))
    assert c.get("/api/themes").status_code == 401 and c.put("/api/theme", json={"theme": "ember"}, headers=H).status_code == 401
    assert login(c, "alice", PW, ip="10.6.0.1").status_code == 303
    assert c.put("/api/theme", json={"theme": "ember"}, headers={"origin": "https://evil.example"}).status_code == 403
    assert c.get("/api/themes").json()["current"] == "abyss"


def test_a_person_picks_a_theme_and_only_they_get_it(make):
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)))
    assert login(c, "bob", PW2, ip="10.6.0.2").status_code == 303                                       # plain users may choose too
    assert c.put("/api/theme", json={"theme": "rose"}, headers=H).json() == {"ok": True, "current": "rose"}
    got = c.get("/api/themes").json()
    assert got["current"] == "rose" and got["default"] == "abyss" and [t["id"] for t in got["themes"]] == [t["id"] for t in themes.THEMES]
    assert 'data-theme="rose"' in c.get("/").text.split("<head>")[0]                                      # on <html>, before anything paints
    c.post("/logout", headers=H)
    assert login(c, "alice", PW, ip="10.6.0.3").status_code == 303
    assert c.get("/api/themes").json()["current"] == "abyss" and "data-theme" not in c.get("/").text.split("<head>")[0]


@pytest.mark.parametrize("bad", ["", "EMBER", "nope", "../../x", None, 7, ["ember"]])
def test_an_unknown_theme_is_refused_and_nothing_changes(make, bad):
    c = make(users=(("alice", "owner", PW),))
    assert login(c, "alice", PW, ip="10.6.0.4").status_code == 303
    assert c.put("/api/theme", json={"theme": bad}, headers=H).status_code == 422
    assert c.put("/api/theme", content=b"not json", headers=H).status_code == 422
    assert c.get("/api/themes").json()["current"] == "abyss"


def test_a_stale_or_hostile_theme_in_the_users_file_falls_back_to_the_default(make, cfg):
    c = make(users=(("alice", "owner", PW),))
    users_file = cfg.data_dir / "users.json"
    data = json.loads(users_file.read_text())
    data["alice"]["theme"] = '"><script>alert(1)</script>'
    users_file.write_text(json.dumps(data))
    assert login(c, "alice", PW, ip="10.6.0.5").status_code == 303
    page = c.get("/").text
    assert "<script>alert(1)" not in page and c.get("/api/themes").json()["current"] == "abyss"


def test_the_stylesheet_is_served_and_linked_before_the_pages_own_styles(make):
    c = make(users=(("alice", "owner", PW),))
    assert login(c, "alice", PW, ip="10.6.0.6").status_code == 303
    page = c.get("/").text
    assert page.index("/static/tokens.css") < page.index("/static/themes.css") < page.index("/static/app.css") < page.index("/instance/theme.css")
    assert c.get("/static/themes.css").status_code == 200
