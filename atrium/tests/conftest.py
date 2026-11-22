import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import pytest
from fastapi.testclient import TestClient

import auth
import config as configmod
from main import make_app
from users import UserStore

# cheap hashes keep the tests fast; the real default uses the full cost
FAST = 2 ** 10
PW = "correct horse battery staple"
PW2 = "a second long passphrase here"


def fast_hash(pw):
    return auth.hash_password(pw, n=FAST)


@pytest.fixture()
def cfg(tmp_path):
    return configmod.Config(data_dir=tmp_path / "data", instance_dir=tmp_path / "instance", trusted_proxies=["testclient"],
                            brand="Test Hub", agent_name="Robo")


@pytest.fixture()
def make(cfg):
    """make(users=[(name, role, password)], hermes=None, cfg=cfg) -> TestClient"""
    def build(users=(("alice", "owner", PW),), hermes=None, config=None):
        c = config or cfg
        c.data_dir.mkdir(parents=True, exist_ok=True)
        store = UserStore(c.data_dir / "users.json")
        for name, role, pw in users:
            store.add(name, role, password_hash=fast_hash(pw))
        return TestClient(make_app(c, hermes=hermes), base_url="https://app.test", follow_redirects=False)
    return build


def login(c, user="alice", pw=PW, ip="10.0.0.1"):
    return c.post("/login", data={"username": user, "password": pw},
                  headers={"x-real-ip": ip, "origin": "https://app.test"})
