"""Per-person chat model: storage, routing into runs, and the Settings routes. Synthetic only (stub Hermes, no network)."""
import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import pytest
from fastapi import HTTPException

import chat_model
from conftest import PW, PW2, login
from test_chat import P as CHAT

H = {"origin": "https://app.test"}
SETTINGS = "/api/plugins/atrium-settings"
OPTIONS = {"providers": [
    {"slug": "anthropic", "name": "Anthropic", "authenticated": True, "models": ["claude-sonnet-x", "claude-haiku-x"]},
    {"slug": "openrouter", "name": "OpenRouter", "authenticated": True, "models": ["vendor/model-1"]},
    {"slug": "unconnected", "name": "Not connected", "authenticated": False, "models": ["nope-1"]},
    {"slug": "mine", "name": "Mine", "authenticated": True, "is_user_defined": True, "models": ["m1"]},
    {"slug": "ollama", "name": "Ollama", "authenticated": True, "models": ["llama-local"]},
    {"slug": "empty", "name": "Empty", "authenticated": True, "models": []},
]}


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    chat_model.configure(tmp_path / "state")
    monkeypatch.setattr(chat_model, "LOCAL_ROUTE", None)
    yield


class Stub:
    """Stands in for Hermes: answers the model catalogue and starts runs (status 202, as the real server does)."""

    def __init__(self):
        self.runs = []

    async def dash_call(self, method, path, body=None, timeout=15, **k):
        if path.startswith("/api/model/options"):
            return 200, OPTIONS
        return 404, {}

    async def dash_json(self, path):
        return 200, {}

    async def api_call(self, method, path, body=None, **k):
        if method == "POST" and path == "/v1/runs":
            self.runs.append(body)
            return 202, {"run_id": "run_%d" % len(self.runs), "status": "started"}
        return 404, {}


@pytest.fixture()
def app(make, cfg, tmp_path, monkeypatch):
    cfg.data_dir = tmp_path / "data"
    stub = Stub()
    c = make(users=(("alice", "owner", PW), ("dana", "owner", PW), ("bob", "user", PW2)), hermes=stub, config=cfg)
    import chat_backend
    sent = []

    async def fake_call(method, path, payload=None):
        sent.append(payload)
        return 200, {"run_id": "run_%d" % (len(sent) + 40), "status": "started"}
    monkeypatch.setattr(chat_backend, "_call", fake_call)
    c.stub, c.sent = stub, sent

    def as_(user, pw=PW, ip="10.5.0.1"):
        c.post("/logout", headers=H)
        assert login(c, user, pw, ip=ip).status_code == 303
    c.as_ = as_
    return c


# ---------------------------------------------------------------- storage

def test_a_selection_is_saved_per_person_and_never_under_their_name(tmp_path):
    chat_model.save("Alice", {"engine": "cloud", "provider": "anthropic", "model": "claude-sonnet-x"})
    assert chat_model.selection("alice")["model"] == "claude-sonnet-x" and chat_model.selection("ALICE")["provider"] == "anthropic"
    assert chat_model.selection("dana") == {} and chat_model.selection(None) == {} and chat_model.selection("") == {}
    files = list((tmp_path / "state" / "chat-models").iterdir())
    assert len(files) == 1 and "alice" not in files[0].name.lower()
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600 and stat.S_IMODE(files[0].parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("bad", [
    [], "text", {"engine": "cloud"}, {"engine": "cloud", "provider": "anthropic"}, {"engine": "cloud", "provider": "anthropic", "model": 5},
    {"engine": "cloud", "provider": "a b", "model": "m"}, {"engine": "cloud", "provider": "x", "model": "m\nn"}, {"engine": "other"}, {"engine": "cloud", "provider": "x" * 300, "model": "m"}])
def test_a_damaged_or_hostile_selection_is_ignored_not_trusted(bad):
    file = chat_model.path("alice")
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps(bad), encoding="utf-8")
    assert chat_model.selection("alice") == {} and chat_model.override("alice") == {}          # no crash, no override
    file.write_text("{not json", encoding="utf-8")
    assert chat_model.selection("alice") == {}


def test_only_valid_selections_can_be_saved():
    with pytest.raises(ValueError):
        chat_model.save("alice", {"engine": "cloud", "provider": "x"})
    with pytest.raises(ValueError):
        chat_model.save("alice", {"engine": "cloud", "provider": "../x", "model": "m"})


# ---------------------------------------------------------------- what goes into a run

def test_the_run_gets_the_persons_model_locked_or_the_install_wide_engine_or_nothing():
    assert chat_model.override("alice") == {}                                                              # the install's default
    claude = {"engine": "claude", "model": "claude-x"}
    assert chat_model.override("alice", claude) == {"model": "claude-x", "provider": "anthropic"}           # install-wide cloud: not locked
    chat_model.save("alice", {"engine": "cloud", "provider": "openrouter", "model": "vendor/model-1"})
    assert chat_model.override("alice", claude) == {"model": "vendor/model-1", "provider": "openrouter", "require_model_lock": True}     # the person's own wins, locked
    chat_model.save("alice", {"engine": "local"})
    assert chat_model.override("alice", claude) == {}                                                       # "the default" beats an install-wide cloud switch
    assert chat_model.override("dana", claude) == {"model": "claude-x", "provider": "anthropic"}           # somebody else is unaffected


def test_an_install_that_manages_local_models_can_supply_the_default_route():
    seen = []
    chat_model.LOCAL_ROUTE = lambda personal: seen.append(personal) or {"model": "local-1", "provider": "custom"}
    assert chat_model.override("alice") == {"model": "local-1", "provider": "custom"} and seen == [False]
    chat_model.save("alice", {"engine": "local"})
    chat_model.override("alice")
    assert seen == [False, True]

    def switching(personal):
        raise HTTPException(503, "The server is switching local models.")
    chat_model.LOCAL_ROUTE = switching
    with pytest.raises(HTTPException) as e:
        chat_model.override("alice")
    assert e.value.status_code == 503
    chat_model.save("alice", {"engine": "cloud", "provider": "anthropic", "model": "claude-sonnet-x"})
    assert chat_model.override("alice")["model"] == "claude-sonnet-x"                                       # a cloud choice does not wait for a local switch


# ---------------------------------------------------------------- the Settings routes

def test_the_catalogue_lists_only_connected_cloud_providers(app):
    app.as_("alice")
    got = app.get(SETTINGS + "/models").json()
    assert [p["id"] for p in got["cloud"]] == ["anthropic", "openrouter"]                                  # not unconnected, user-defined, local or empty
    assert got["cloud"][0]["models"] == ["claude-sonnet-x", "claude-haiku-x"]


def test_choosing_a_cloud_model_needs_an_owner_a_yes_and_a_real_model(app):
    body = {"provider": "anthropic", "id": "claude-sonnet-x", "confirm": True}
    app.as_("bob", PW2, "10.5.0.2")
    assert app.get(SETTINGS + "/models").status_code == 403 and app.post(SETTINGS + "/models", json=body, headers=H).status_code == 403
    app.as_("alice")
    assert app.post(SETTINGS + "/models", json=body, headers={"origin": "https://evil.example"}).status_code == 403
    assert app.post(SETTINGS + "/models", json={**body, "confirm": False}, headers=H).status_code == 400            # chat text leaves the machine: it needs a yes
    assert app.post(SETTINGS + "/models", json={"provider": "", "id": "", "confirm": True}, headers=H).status_code == 400
    for wrong in ({"provider": "unconnected", "id": "nope-1"}, {"provider": "ollama", "id": "llama-local"}, {"provider": "anthropic", "id": "not-a-model"},
                  {"provider": "mine", "id": "m1"}):
        assert app.post(SETTINGS + "/models", json={**wrong, "confirm": True}, headers=H).status_code == 409, wrong
    assert chat_model.selection("alice") == {}                                                              # nothing was saved by a refusal
    assert app.post(SETTINGS + "/models", json=body, headers=H).json() == {"ok": True}
    assert app.get(SETTINGS + "/models").json()["engine"] == {"engine": "cloud", "provider": "anthropic", "model": "claude-sonnet-x"}


def test_the_default_can_be_restored_and_one_owners_choice_is_not_another_owners(app):
    app.as_("alice")
    app.post(SETTINGS + "/models", json={"provider": "openrouter", "id": "vendor/model-1", "confirm": True}, headers=H)
    app.as_("dana", PW, "10.5.0.3")
    assert app.get(SETTINGS + "/models").json()["engine"]["engine"] == "local"                              # dana sees the default, not alice's pick
    app.as_("alice")
    assert app.post(SETTINGS + "/models/use-default", headers=H).json() == {"ok": True}
    assert chat_model.selection("alice") == {"engine": "local"}


# ---------------------------------------------------------------- it reaches the person's chats

def test_chats_use_the_persons_model_and_other_people_are_untouched(app):
    app.as_("alice")
    app.post(SETTINGS + "/models", json={"provider": "anthropic", "id": "claude-haiku-x", "confirm": True}, headers=H)
    assert app.post(CHAT + "/send", json={"session_id": "atrium-chat-alicechat1", "message": "hello"}, headers=H).status_code == 200
    assert {k: app.sent[-1][k] for k in ("model", "provider", "require_model_lock")} == {"model": "claude-haiku-x", "provider": "anthropic", "require_model_lock": True}
    assert app.get(CHAT + "/engine").json() == {"engine": "claude", "label": "claude-haiku-x", "seconds_left": None}
    app.as_("bob", PW2, "10.5.0.4")
    assert app.post(CHAT + "/send", json={"session_id": "atrium-chat-bobchat1", "message": "hello"}, headers=H).status_code == 200
    assert not {"model", "provider", "require_model_lock"} & set(app.sent[-1])
    assert app.get(CHAT + "/engine").json()["engine"] == "local"


def test_the_home_ask_box_uses_the_persons_model_and_accepts_runs_hermes_accepted(app):
    app.as_("alice")
    app.post(SETTINGS + "/models", json={"provider": "anthropic", "id": "claude-sonnet-x", "confirm": True}, headers=H)
    r = app.post("/api/ask", json={"message": "hello"}, headers=H)
    assert r.status_code == 200 and r.json()["run_id"] == "run_1"                                           # Hermes answers 202 for a started run
    assert app.stub.runs[-1]["model"] == "claude-sonnet-x" and app.stub.runs[-1]["require_model_lock"] is True and app.stub.runs[-1]["session_id"] == "ask-alice"


def test_a_damaged_selection_file_never_breaks_a_chat(app):
    app.as_("alice")
    f = chat_model.path("alice")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"engine": "cloud"}), encoding="utf-8")
    assert app.post(CHAT + "/send", json={"session_id": "atrium-chat-alicechat2", "message": "hello"}, headers=H).status_code == 200
    assert "model" not in app.sent[-1]
