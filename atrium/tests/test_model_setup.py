from unittest.mock import AsyncMock

from conftest import PW, PW2, login
from hermes import Hermes

PATH = "/api/plugins/atrium-settings/model"


def test_model_setup_requires_owner_and_origin(make):
    runtime = Hermes()
    runtime.dash_call = AsyncMock()
    client = make(users=(("alice", "owner", PW), ("bob", "user", PW2)), hermes=runtime)
    assert client.get(PATH).status_code == 401
    login(client, "bob", PW2)
    assert client.post(PATH, json={}).status_code == 403
    login(client)
    assert client.post(PATH, json={}, headers={"origin": "https://other.invalid"}).status_code == 403
    runtime.dash_call.assert_not_awaited()


def test_model_setup_limits_credentials_and_responses(make):
    runtime = Hermes()
    runtime.dash_call = AsyncMock()
    client = make(hermes=runtime)
    login(client)
    body = {"model": "example", "api_key": "synthetic-secret"}
    for url in ("http://models.example", "https://user:secret@example.org", "https://example.org/v1?key=x"):
        assert client.post(PATH, json=dict(body, base_url=url)).status_code == 400
    runtime.dash_call.assert_not_awaited()
    body["base_url"] = "https://models.example/v1"
    runtime.dash_call.return_value = (200, {"model": "example", "provider": "custom", "key": body["api_key"]})
    assert client.get(PATH).json() == {"model": "example", "provider": "custom"}
    runtime.dash_call.return_value = (200, {"confirm_required": True, "ok": False})
    assert client.post(PATH, json=body).json() == {"confirm_required": True}
    runtime.dash_call.return_value = (400, {"detail": body["api_key"]})
    response = client.post(PATH, json=body)
    assert response.status_code == 400 and body["api_key"] not in response.text
    runtime.dash_call.return_value = (200, {"ok": True, "api_key": body["api_key"]})
    assert client.post(PATH, json=dict(body, confirm=True)).json() == {"ok": True}
    runtime.dash_call.assert_awaited_with("POST", "/api/model/set",
        dict(body, scope="main", provider="custom", confirm_expensive_model=True),
        timeout=60, allow_redirects=False)
