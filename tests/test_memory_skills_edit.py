"""Settings > Memory and Settings > Skills: view, add, change and remove (owners only, only where the folder is writable)."""
import os
import stat

import pytest

from conftest import PW, PW2, login
from hermes import Hermes

H = {"origin": "https://app.test"}
MEM = "/api/plugins/atrium-memory"
SK = "/api/plugins/atrium-skills"
SKILL = "---\nname: web-search\ndescription: Look things up on the web when asked\n---\n# Web search\nSteps go here.\n"


@pytest.fixture()
def owner(make, cfg, tmp_path):
    home = tmp_path / "home"
    (home / "obsidian-vault" / "Notes").mkdir(parents=True)
    (home / "obsidian-vault" / "Notes" / "Alpha.md").write_text("# Alpha note\nSee [[Beta]].\n")
    (home / "skills" / "research" / "summarize").mkdir(parents=True)
    (home / "skills" / "research" / "summarize" / "SKILL.md").write_text("---\nname: summarize\ndescription: Summarise a long text\n---\nBody\n")
    (home / "skills" / "research" / "summarize" / "notes.txt").write_text("extra")
    cfg.hermes_data_dir = str(home)
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), hermes=Hermes("", "", "", "", str(home)), config=cfg)
    c.home = home
    assert login(c, "alice", PW, ip="10.8.0.1").status_code == 303
    return c


def test_notes_can_be_added_changed_and_removed(owner):
    r = owner.post(MEM + "/note", json={"path": "Notes/Gamma", "body": "# Gamma\nHello [[Alpha]]\n", "create": True}, headers=H)
    assert r.status_code == 200 and r.json()["id"] == "Notes/Gamma.md"
    assert (owner.home / "obsidian-vault" / "Notes" / "Gamma.md").read_text().startswith("# Gamma")
    assert owner.post(MEM + "/note", json={"path": "Notes/Gamma.md", "body": "x", "create": True}, headers=H).status_code == 409      # no silent overwrite
    n = owner.get(MEM + "/note", params={"path": "Notes/Gamma.md"}).json()
    assert n["editable"] is True and "Alpha" in [x["title"] for x in n["links"]] or n["links"]
    assert owner.post(MEM + "/note", json={"path": "Notes/Gamma.md", "body": "# Gamma\nChanged\n"}, headers=H).status_code == 200
    assert "Changed" in (owner.home / "obsidian-vault" / "Notes" / "Gamma.md").read_text()
    assert owner.post(MEM + "/note", json={"path": "Notes/Nope.md", "body": "x"}, headers=H).status_code == 404                      # changing needs an existing note
    d = owner.delete(MEM + "/note", params={"path": "Notes/Gamma.md"}, headers=H)
    assert d.status_code == 200 and d.json()["kept_in"] == "Notes/.trash/Gamma.md"
    assert (owner.home / "obsidian-vault" / "Notes" / ".trash" / "Gamma.md").exists()              # kept, not erased
    ids = [x["id"] for x in owner.get(MEM + "/graph").json()["nodes"]]
    assert "Notes/Gamma.md" not in ids and not any(".trash" in i for i in ids)                       # and gone from the graph
    assert owner.delete(MEM + "/note", params={"path": "Notes/Gamma.md"}, headers=H).status_code == 404


def test_note_paths_cannot_escape_the_vault(owner):
    for bad in ("../x", "../../etc/passwd", ".hidden/x", "Notes/.secret", "a/b/c/d/e/f", "Notes/a:b", ""):
        r = owner.post(MEM + "/note", json={"path": bad, "body": "x", "create": True}, headers=H)
        assert r.status_code == 400, bad
        assert owner.delete(MEM + "/note", params={"path": bad}, headers=H).status_code in (400, 404), bad
    assert not (owner.home / "etc").exists() and not (owner.home.parent / "x.md").exists()
    assert owner.post(MEM + "/note", json={"path": "Notes/big", "body": "x" * 500_001, "create": True}, headers=H).status_code in (413, 422)


def test_a_new_folder_and_the_status(owner):
    assert owner.post(MEM + "/note", json={"path": "People/Ana", "body": "# Ana\n", "create": True}, headers=H).status_code == 200
    s = owner.get(MEM + "/status").json()
    assert s["exists"] and s["root"] and "Notes" in s["folders"] and "People" in s["folders"]


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs real file permissions")
def test_a_read_only_folder_says_so(owner):
    d = owner.home / "obsidian-vault" / "Notes"
    d.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        r = owner.post(MEM + "/note", json={"path": "Notes/New", "body": "x", "create": True}, headers=H)
        assert r.status_code == 403 and "read-only" in r.json()["detail"]
        assert owner.post(MEM + "/note", json={"path": "Notes/Alpha.md", "body": "x"}, headers=H).status_code == 403
        assert owner.get(MEM + "/note", params={"path": "Notes/Alpha.md"}).json()["editable"] is False
        assert "Notes" not in owner.get(MEM + "/status").json()["folders"]
    finally:
        d.chmod(stat.S_IRWXU)


def test_skills_list_view_add_change_remove(owner):
    lst = owner.get(SK + "/list").json()
    assert lst["exists"] and lst["writable"]
    s = [x for x in lst["skills"] if x["id"] == "research/summarize"][0]
    assert s["name"] == "summarize" and s["description"] == "Summarise a long text" and s["category"] == "research" and s["writable"]
    g = owner.get(SK + "/skill", params={"id": "research/summarize"}).json()
    assert g["body"].startswith("---") and g["files"] == ["notes.txt"]
    assert owner.post(SK + "/skill", json={"id": "research/web-search", "body": SKILL, "create": True}, headers=H).status_code == 200
    assert (owner.home / "skills" / "research" / "web-search" / "SKILL.md").read_text() == SKILL
    assert owner.post(SK + "/skill", json={"id": "research/web-search", "body": SKILL, "create": True}, headers=H).status_code == 409
    changed = SKILL.replace("Steps go here.", "New steps.")
    assert owner.post(SK + "/skill", json={"id": "research/web-search", "body": changed}, headers=H).status_code == 200
    assert "New steps." in owner.get(SK + "/skill", params={"id": "research/web-search"}).json()["body"]
    assert owner.post(SK + "/skill", json={"id": "research/missing", "body": SKILL}, headers=H).status_code == 404
    d = owner.delete(SK + "/skill", params={"id": "research/web-search"}, headers=H)
    assert d.status_code == 200 and d.json()["kept_in"].startswith(".trash/research__web-search-")
    assert not (owner.home / "skills" / "research" / "web-search").exists()
    assert (owner.home / "skills" / "research" / "summarize" / "notes.txt").exists()                  # other skills untouched
    assert [x["id"] for x in owner.get(SK + "/list").json()["skills"]] == ["research/summarize"]    # trash is not listed


def test_a_skill_must_have_a_proper_header_and_id(owner):
    for body in ("no header", "---\nname: x\n---\nmissing description", "---\ndescription: d\n---\nmissing name", "---\nname: Bad Name\ndescription: d\n---\n", "---\nname: x\ndescription: d\n"):
        assert owner.post(SK + "/skill", json={"id": "tools/x", "body": body, "create": True}, headers=H).status_code == 400, body
    for bad in ("../x", "Tools/X", "a/b/c/d", ".trash/x", "x y"):
        assert owner.post(SK + "/skill", json={"id": bad, "body": SKILL, "create": True}, headers=H).status_code == 400, bad
        assert owner.get(SK + "/skill", params={"id": bad}).status_code == 400, bad
        assert owner.delete(SK + "/skill", params={"id": bad}, headers=H).status_code == 400, bad
    assert owner.get(SK + "/skill", params={"id": "tools/none"}).status_code == 404


def test_only_owners_and_same_origin_may_change_these(owner):
    body = {"path": "Notes/Zed", "body": "x", "create": True}
    assert owner.post(MEM + "/note", json=body, headers={"origin": "https://evil.test"}).status_code == 403
    assert owner.post(SK + "/skill", json={"id": "tools/x", "body": SKILL, "create": True}, headers={"origin": "https://evil.test"}).status_code == 403
    owner.post("/logout", headers=H)
    assert login(owner, "bob", PW2, ip="10.8.0.2").status_code == 303
    assert owner.post(MEM + "/note", json=body, headers=H).status_code == 403
    assert owner.delete(MEM + "/note", params={"path": "Notes/Alpha.md"}, headers=H).status_code == 403
    assert owner.get(SK + "/list").status_code == 403
    assert owner.post(SK + "/skill", json={"id": "tools/x", "body": SKILL, "create": True}, headers=H).status_code == 403
    assert owner.delete(SK + "/skill", params={"id": "research/summarize"}, headers=H).status_code == 403
    assert (owner.home / "skills" / "research" / "summarize" / "SKILL.md").exists()
    assert not (owner.home / "obsidian-vault" / "Notes" / "Zed.md").exists()


def test_no_skills_folder_is_reported_not_an_error(make, cfg, tmp_path):
    cfg.hermes_data_dir = str(tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    c = make(hermes=Hermes("", "", "", "", str(tmp_path / "empty")), config=cfg)
    assert login(c, "alice", PW, ip="10.8.0.3").status_code == 303
    j = c.get(SK + "/list").json()
    assert j == {"skills": [], "exists": False, "writable": False}
    assert c.post(SK + "/skill", json={"id": "tools/x", "body": SKILL, "create": True}, headers=H).status_code == 404


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs real file permissions")
def test_a_file_that_is_not_writable_can_still_be_replaced_when_its_folder_is(owner):
    note = owner.home / "obsidian-vault" / "Notes" / "Alpha.md"
    skill = owner.home / "skills" / "research" / "summarize" / "SKILL.md"
    note.chmod(0o644)
    skill.chmod(0o644)
    n = owner.get(MEM + "/note", params={"path": "Notes/Alpha.md"}).json()
    assert n["editable"] is True
    assert owner.post(MEM + "/note", json={"path": "Notes/Alpha.md", "body": "# Alpha\nEdited\n"}, headers=H).status_code == 200
    assert "Edited" in note.read_text() and note.stat().st_mode & 0o060 == 0o060         # the agent's group can still edit it
    s = [x for x in owner.get(SK + "/list").json()["skills"] if x["id"] == "research/summarize"][0]
    assert s["writable"] is True
    body = SKILL.replace("web-search", "summarize")
    assert owner.post(SK + "/skill", json={"id": "research/summarize", "body": body}, headers=H).status_code == 200
    assert skill.read_text() == body and skill.stat().st_mode & 0o060 == 0o060
    assert owner.post(MEM + "/note", json={"path": "Notes/Fresh", "body": "x", "create": True}, headers=H).status_code == 200
    assert (owner.home / "obsidian-vault" / "Notes" / "Fresh.md").stat().st_mode & 0o060 == 0o060
