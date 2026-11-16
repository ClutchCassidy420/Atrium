"""The host helper (host/atrium-hostctl.py): grouping packages, its own access checks and its refusals, with no real system calls."""
import importlib.util
import json
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

KEY = "k" * 32
SAMPLE = """Listing...
vim/resolute-updates 2:9.1.2141-1ubuntu4.10 amd64 [upgradable from: 2:9.1.2141-1ubuntu4.9]
docker-ce/resolute 5:29.9.3-1~ubuntu.26.04~resolute amd64 [upgradable from: 5:29.9.2-1~ubuntu.26.04~resolute]
containerd.io/resolute 2.4.2-4 amd64 [upgradable from: 2.4.2-3]
Evil Name/x 1 amd64 [upgradable from: 0]
base-files/resolute-updates 14ubuntu6.3 amd64 [upgradable from: 14ubuntu6.2]
"""


@pytest.fixture()
def hc(monkeypatch, tmp_path):
    for k, v in {"HOSTCTL_KEY": KEY, "HOSTCTL_ALLOWED": "127.0.0.1", "HOSTCTL_STATE": str(tmp_path), "HOSTCTL_CARE": "docker,containerd,tailscale",
                 "HOSTCTL_APP_CHECK": "http://127.0.0.1:1/x", "HOSTCTL_DRYRUN": "1"}.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("hostctl_under_test", Path(__file__).resolve().parent.parent / "host" / "atrium-hostctl.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_packages_are_grouped_and_odd_names_are_ignored(hc, monkeypatch):
    monkeypatch.setattr(hc, "run", lambda cmd, timeout=60, env=None: (0, SAMPLE))
    pk = hc.packages()
    assert [p["name"] for p in pk["care"]] == ["docker-ce", "containerd.io"]
    assert [p["name"] for p in pk["safe"]] == ["vim", "base-files"]       # "Evil Name" never becomes a package argument


def test_two_actions_cannot_overlap(hc, monkeypatch):
    calls = []
    monkeypatch.setattr(hc, "run", lambda cmd, timeout=60, env=None: calls.append(cmd) or (0, ""))
    monkeypatch.setattr(hc, "unit_active", lambda unit: unit.startswith("atrium-update"))
    assert hc.schedule_power("reboot", "t")[0] == 409
    assert hc.start_update("update_safe", "t")[0] == 409
    assert calls == []


def test_nothing_waiting_means_nothing_starts(hc, monkeypatch):
    monkeypatch.setattr(hc, "unit_active", lambda unit: False)
    monkeypatch.setattr(hc, "run", lambda cmd, timeout=60, env=None: (0, "Listing...\n"))
    assert hc.start_update("update_safe", "t")[0] == 409


def test_a_power_action_is_scheduled_with_a_countdown_and_logged(hc, monkeypatch):
    calls = []
    state = {"on": False}

    def fake_run(cmd, timeout=60, env=None):
        calls.append(cmd)
        if cmd[0] == "systemd-run":
            state["on"] = True
        if cmd[:2] == ["systemctl", "stop"]:
            state["on"] = False
        return 0, ""
    monkeypatch.setattr(hc, "run", fake_run)
    monkeypatch.setattr(hc, "unit_active", lambda unit: state["on"] and unit.endswith(".timer"))
    code, out = hc.schedule_power("shutdown", "alice")
    assert code == 200 and out["at"] > 0
    cmd = next(c for c in calls if c[0] == "systemd-run")
    assert "--on-active=%ds" % hc.COUNTDOWN in cmd and cmd[-1] == "/bin/true"       # dry run: the real command is never given
    assert hc.cancel_power("alice")[0] == 200
    assert hc.power_state() is None
    assert [x["action"] for x in hc.recent_log()] == ["cancel", "shutdown"]


def _start(hc, monkeypatch, who):
    monkeypatch.setattr(hc, "person", lambda cookie: who if cookie == "good" else None)
    srv = HTTPServer(("127.0.0.1", 0), hc.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def _req(base, method, path, body=None, key=KEY):
    r = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers={"X-Hostctl-Key": key})
    try:
        with urllib.request.urlopen(r, timeout=10) as x:
            return x.status, json.load(x)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_the_helper_checks_the_key_and_the_person(hc, monkeypatch):
    monkeypatch.setattr(hc, "status", lambda: {"ok": True})
    ran = []
    monkeypatch.setattr(hc, "schedule_power", lambda action, user: ran.append((action, user)) or (200, {"ok": True}))
    srv, base = _start(hc, monkeypatch, "alice")
    try:
        assert _req(base, "GET", "/status")[0] == 200
        assert _req(base, "GET", "/status", key="x" * 32)[0] == 403
        assert _req(base, "POST", "/do", {"action": "reboot", "cookie": "nope"})[0] == 401
        assert _req(base, "POST", "/do", {"action": "format_disk", "cookie": "good"})[0] == 400
        assert _req(base, "POST", "/do", {"action": "reboot", "cookie": "good"})[0] == 200
        assert ran == [("reboot", "alice")]
    finally:
        srv.shutdown()


def test_it_refuses_to_start_without_a_long_key(hc, monkeypatch):
    monkeypatch.setattr(hc, "KEY", "short")
    monkeypatch.setattr(hc.sys, "argv", ["x"])
    assert hc.main() == 2
