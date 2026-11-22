"""Tool activity (which tool the agent is using right now, for the glowing icons) and the look presets an install can offer."""
import asyncio
import datetime
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import pytest

import config as configmod
import overview as ov
from conftest import PW, PW2, login


# ---------------------------------------------------------------- which icon a tool name stands for

@pytest.mark.parametrize("name,kind", [
    ("web_search", "search"), ("browser_navigate", "web"), ("web_extract", "web"), ("docker_ps", "docker"), ("git_commit", "git"),
    ("sql_query", "db"), ("execute_code", "python"), ("terminal", "terminal"), ("send_message", "mail"), ("cronjob", "clock"),
    ("memory_add", "vault"), ("read_file", "code"), ("patch", "code"), ("SOMETHING_ELSE", ""), ("", ""), (None, ""), (5, "")])
def test_a_tool_name_maps_to_a_drawn_icon_or_none(name, kind):
    assert ov.tool_kind(name) == kind


# ---------------------------------------------------------------- reading the latest tool call

NOW_S = time.time()


class Hermes:
    def __init__(self, sessions, messages):
        self.sessions, self.messages, self.calls = sessions, messages, []

    async def dash_json(self, path):
        self.calls.append(path)
        if path.startswith("/api/sessions?"):
            return 200, {"sessions": self.sessions}
        sid = path.split("/")[3]
        return (200, {"messages": self.messages[sid]}) if sid in self.messages else (404, {})


def call(name, args="{}"):
    return {"function": {"name": name, "arguments": args}}


def test_the_latest_call_comes_from_recent_sessions_with_messages_and_leaks_no_arguments():
    secret = '{"command": "cat /etc/shadow", "token": "sk-should-never-appear"}'
    h = Hermes(
        sessions=[{"id": "empty", "message_count": 0, "last_active": NOW_S},                                   # a scheduled check-in: no messages, skipped
                  {"id": "a", "message_count": 4, "last_active": NOW_S - 30}, {"id": "b", "message_count": 2, "last_active": NOW_S - 5},
                  {"id": "old", "message_count": 9, "last_active": NOW_S - 99999}, {"id": "bare", "last_active": NOW_S}],
        messages={"a": [{"timestamp": NOW_S - 40, "tool_calls": [call("read_file", secret)]}, {"role": "user", "content": "hi"}],
                  "b": [{"timestamp": NOW_S - 8, "tool_calls": [call("web_search"), call("terminal", secret)]}, {"timestamp": NOW_S - 9, "tool_calls": "not a list"}, "junk", None],
                  "old": [{"timestamp": NOW_S - 99999, "tool_calls": [call("git_status")]}],
                  "empty": [{"timestamp": NOW_S, "tool_calls": [call("cronjob")]}]})
    out = asyncio.run(ov.Overview(h).tool_activity())
    assert out["latest"]["name"] in ("web_search", "terminal") and out["latest"]["at"] == int((NOW_S - 8) * 1000)
    assert {c["name"] for c in out["recent"]} == {"web_search", "terminal", "read_file", "git_status"}      # the 3 newest sessions that have messages
    assert "cronjob" not in str(out)                                                                           # the empty check-in session was never read
    assert "empty" not in "".join(p for p in h.calls if "/messages" in p)
    assert "shadow" not in str(out) and "sk-should" not in str(out) and "arguments" not in str(out)           # names, kinds and times only
    assert set(out["latest"]) == {"name", "kind", "at"}
    assert all("order=latest" in p and "limit=40" in p for p in h.calls if "/messages" in p)


def test_the_answer_is_newest_first_capped_and_named_by_kind():
    msgs = [{"timestamp": NOW_S - i, "tool_calls": [call("terminal")]} for i in range(12)]
    out = asyncio.run(ov.Overview(Hermes([{"id": "a", "message_count": 12, "last_active": NOW_S}], {"a": msgs})).tool_activity())
    assert len(out["recent"]) == 8 and [c["at"] for c in out["recent"]] == sorted((c["at"] for c in out["recent"]), reverse=True)
    assert out["latest"] == {"name": "terminal", "kind": "terminal", "at": int(NOW_S * 1000)}
    long = asyncio.run(ov.Overview(Hermes([{"id": "a", "message_count": 1, "last_active": NOW_S}], {"a": [{"timestamp": NOW_S, "tool_calls": [call("x" * 200)]}]})).tool_activity())
    assert len(long["latest"]["name"]) == 60


def test_times_may_be_numbers_in_seconds_or_milliseconds_or_text():
    iso = datetime.datetime.fromtimestamp(NOW_S - 100, datetime.timezone.utc).isoformat()
    msgs = [{"timestamp": NOW_S - 300, "tool_calls": [call("read_file")]}, {"timestamp": int((NOW_S - 200) * 1000), "tool_calls": [call("patch")]},
            {"timestamp": iso, "tool_calls": [call("git_diff")]}, {"timestamp": None, "tool_calls": [call("terminal")]}]
    out = asyncio.run(ov.Overview(Hermes([{"id": "a", "message_count": 4, "last_active": NOW_S}], {"a": msgs})).tool_activity())
    assert [c["name"] for c in out["recent"]] == ["git_diff", "patch", "read_file"]                            # a call with no time cannot be placed, so it is left out


def test_nothing_to_report_is_an_empty_answer_not_an_error():
    out = asyncio.run(ov.Overview(Hermes([], {})).tool_activity())
    assert out == {"latest": None, "recent": []}


def test_one_look_at_hermes_serves_every_open_page_for_a_few_seconds():
    h = Hermes([{"id": "a", "message_count": 1, "last_active": NOW_S}], {"a": [{"timestamp": NOW_S, "tool_calls": [call("terminal")]}]})
    o = ov.Overview(h)
    first = asyncio.run(o.tool_activity())
    n = len(h.calls)
    assert asyncio.run(o.tool_activity()) == first and len(h.calls) == n                                       # cached
    o._tool_cache = (time.time() - 10, first)
    asyncio.run(o.tool_activity())
    assert len(h.calls) > n                                                                                      # and refreshed after a few seconds


# ---------------------------------------------------------------- the route

def test_only_owners_may_read_the_tool_activity(make):
    h = Hermes([{"id": "a", "message_count": 1, "last_active": NOW_S}], {"a": [{"timestamp": NOW_S, "tool_calls": [call("web_search")]}]})
    c = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), hermes=h)
    assert c.get("/api/tool-activity").status_code == 401
    assert login(c, "bob", PW2, ip="10.4.0.1").status_code == 303
    assert c.get("/api/tool-activity").status_code == 403
    c.post("/logout", headers={"origin": "https://app.test"})
    assert login(c, "alice", PW, ip="10.4.0.2").status_code == 303
    got = c.get("/api/tool-activity").json()
    assert got["latest"]["kind"] == "search" and got["latest"]["name"] == "web_search"


# ---------------------------------------------------------------- look presets from instance.toml

GOOD = [{"name": "Ember", "look": {"avatar": "watcher", "theme": "gold", "tentacles": 12, "books": False, "glow": 1.3}}]


def test_valid_presets_are_kept_and_trimmed():
    assert configmod.clean_presets(GOOD) == GOOD
    assert configmod.clean_presets([{"name": "  Padded  ", "look": {"theme": "ice"}}])[0]["name"] == "Padded"


@pytest.mark.parametrize("bad", [
    None, "text", {}, [], [1], [{"name": "x"}], [{"look": {"a": 1}}], [{"name": "", "look": {"a": 1}}], [{"name": "x" * 41, "look": {"a": 1}}],
    [{"name": "x", "look": {}}], [{"name": "x", "look": {"a": [1]}}], [{"name": "x", "look": {"a": {"b": 1}}}], [{"name": "x", "look": {"bad key": 1}}],
    [{"name": "x", "look": {"a": "y" * 41}}], [{"name": "x", "look": {"a": 10 ** 9}}], [{"name": "x", "look": {"a": 1}, "extra": 1}],
    [{"name": "x", "look": {"k%d" % i: 1 for i in range(25)}}], GOOD * 41, GOOD + [{"name": "broken"}]])
def test_anything_else_is_ignored_entirely_rather_than_half_used(bad):
    assert configmod.clean_presets(bad) == []


def test_presets_from_instance_toml_reach_the_page(tmp_path):
    inst = tmp_path / "i"
    inst.mkdir()
    env = {"INSTANCE_DIR": str(inst), "DATA_DIR": str(tmp_path / "d")}
    assert configmod.load(env).avatar_presets == []
    (inst / "instance.toml").write_text('[[avatar_presets]]\nname = "Ember"\n[avatar_presets.look]\navatar = "watcher"\ntheme = "gold"\ntentacles = 12\n', encoding="utf-8")
    assert configmod.load(env).avatar_presets == [{"name": "Ember", "look": {"avatar": "watcher", "theme": "gold", "tentacles": 12}}]
    (inst / "instance.toml").write_text('[[avatar_presets]]\nname = "Broken"\n', encoding="utf-8")
    assert configmod.load(env).avatar_presets == []


def test_shell_config_carries_the_presets(make, cfg):
    import json, re
    cfg.avatar_presets = GOOD
    c = make(users=(("alice", "owner", PW),), config=cfg)
    assert login(c, "alice", PW, ip="10.4.0.3").status_code == 303
    data = json.loads(re.search(r"window\.__APP_CONFIG__=(.*);", c.get("/shell-config.js").text).group(1))
    assert data["avatarPresets"] == GOOD
