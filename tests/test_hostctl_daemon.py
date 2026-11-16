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


# ---- containers -------------------------------------------------------------------------------------------------------------------

def test_image_references_are_understood(hc):
    assert hc.split_ref("emby/embyserver:4.10.2.0") == ("registry-1.docker.io", "emby/embyserver", "4.10.2.0")
    assert hc.split_ref("nextcloud:35.0.2-apache") == ("registry-1.docker.io", "library/nextcloud", "35.0.2-apache")
    assert hc.split_ref("ghcr.io/mailu/admin:2024.06") == ("ghcr.io", "mailu/admin", "2024.06")
    assert hc.split_ref("ghcr.io/x/y:latest@sha256:abc")[2] == "sha256:abc"
    assert hc.split_ref("localhost:5000/app")[:2] == ("localhost:5000", "app")
    kinds = {r: hc.ref_kind(r) for r in ("emby/embyserver:4.10.2.0", "ghcr.io/x/y:latest", "redis:7", "postgres:17", "a/b@sha256:abc", "speaches:latest-cuda", "nousresearch/hermes-agent:v0.23.1")}
    assert kinds == {"emby/embyserver:4.10.2.0": "pinned", "ghcr.io/x/y:latest": "floating", "redis:7": "floating", "postgres:17": "floating",
                     "a/b@sha256:abc": "digest", "speaches:latest-cuda": "floating", "nousresearch/hermes-agent:v0.23.1": "pinned"}


def test_only_a_newer_version_of_the_same_shape_counts(hc):
    tags = ["4.10.2.0", "4.10.3.0", "4.9.9.9", "4.11.0.0-beta", "5.0.0", "4.10.3.0-rc1", "latest", "4.12.0.1"]
    assert hc.newer_tag("4.10.2.0", tags) == "4.12.0.1"            # same four-part shape, highest, no suffix, same major version
    assert hc.newer_tag("v0.23.1", ["v0.22.5", "v0.23.1", "v0.23.4", "v0.24.0", "0.30.0", "v2026.9.24"]) == "v0.23.4"      # 0.x: stays inside the same minor version too
    assert hc.newer_tag("35.0.2-apache", ["35.0.2-apache", "35.0.3-apache", "36.0.0-fpm", "35.1.0-apache", "36.1.0-apache"]) == "35.1.0-apache"
    assert hc.newer_tag("17.11-alpine", ["17.11-alpine", "17.12-alpine", "18.7-alpine"]) == "17.12-alpine"                # no jump to the next major version
    assert hc.newer_tag("2.5.11", ["2.5.11"]) is None
    assert hc.newer_tag("latest", ["1.2.3"]) is None


def test_unlisted_groups_are_high_risk(hc, monkeypatch):
    monkeypatch.setattr(hc, "TIER_LOW", {"voice"})
    monkeypatch.setattr(hc, "TIER_MEDIUM", {"media"})
    assert [hc.tier_of(x) for x in ("voice", "media", "mail", "")] == ["low", "medium", "high", "high"]


def test_checking_a_service_for_a_newer_image(hc, monkeypatch):
    monkeypatch.setattr(hc, "local_digests", lambda image_id: {"sha256:old"})
    monkeypatch.setattr(hc, "remote_digest", lambda ref: "sha256:new")
    float_ = {"service": "a", "ref": "x/y:latest", "kind": "floating", "image_id": "sha256:i"}
    assert hc.check_service(float_)["update"] is True
    monkeypatch.setattr(hc, "remote_digest", lambda ref: "sha256:old")
    assert hc.check_service(float_)["update"] is False
    monkeypatch.setattr(hc, "remote_digest", lambda ref: "")                       # rate limited or offline: unknown, never "up to date"
    assert hc.check_service(float_)["update"] is None
    monkeypatch.setattr(hc, "registry_tags", lambda host, repo: ["4.10.2.0", "4.10.3.0"])
    r = hc.check_service({"service": "b", "ref": "emby/embyserver:4.10.2.0", "kind": "pinned", "image_id": "i"})
    assert r["update"] is True and r["newer"] == "4.10.3.0"
    assert hc.check_service({"service": "c", "ref": "a@sha256:1", "kind": "digest", "image_id": "i"})["update"] is False
    assert hc.check_service({"service": "d", "ref": "", "kind": "built", "image_id": "i"})["update"] is False


def test_a_registry_that_limits_requests_is_reported_as_unknown(hc, monkeypatch):
    import urllib.error

    def limited(host, repo):
        raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
    monkeypatch.setattr(hc, "registry_tags", limited)
    r = hc.check_service({"service": "b", "ref": "emby/embyserver:4.10.2.0", "kind": "pinned", "image_id": "i"})
    assert r["update"] is None and "limit" in r["note"]


def test_the_version_in_a_compose_file_is_changed_exactly(hc, tmp_path):
    f = tmp_path / "compose.yaml"
    f.write_text('services:\n  a:\n    image: "emby/embyserver:4.10.2.0"  # media\n  b:\n    image: emby/embyserver:4.10.2.01\n  c:\n    image: other:1.0\n')
    assert hc.edit_image_line(str(f), "emby/embyserver:4.10.2.0", "emby/embyserver:4.10.3.0")
    t = f.read_text()
    assert 'image: "emby/embyserver:4.10.3.0"  # media' in t and "4.10.2.01" in t and "other:1.0" in t        # only the exact reference changed
    assert not hc.edit_image_line(str(f), "nothing:1.0", "nothing:2.0")


def _ready(hc, monkeypatch, tmp_path, project="voice", tier="low", update=True):
    monkeypatch.setattr(hc, "TIER_LOW", {project} if tier == "low" else set())
    monkeypatch.setattr(hc, "unit_active", lambda unit: False)
    monkeypatch.setattr(hc, "run", lambda cmd, timeout=60, env=None: (0, ""))
    (tmp_path / "containers.json").write_text(json.dumps({"checked_at": 1, "projects": {project: {"project": project, "tier": tier, "update": update, "services": [{"service": "s", "ref": "x/y:latest", "kind": "floating", "update": update, "newer": "new build"}]}}}))


def test_container_jobs_are_refused_when_they_should_be(hc, monkeypatch, tmp_path):
    _ready(hc, monkeypatch, tmp_path)
    assert hc.start_container_op("container_update", "../etc", "u")[0] == 400
    assert hc.start_container_op("container_update", "mail", "u")[0] == 403                # not listed, so high risk
    _ready(hc, monkeypatch, tmp_path, update=False)
    assert hc.start_container_op("container_update", "voice", "u")[0] == 409               # nothing newer
    assert hc.start_container_op("container_rollback", "voice", "u")[0] == 409             # no restore point
    _ready(hc, monkeypatch, tmp_path)
    monkeypatch.setattr(hc, "unit_active", lambda unit: unit.startswith("atrium-update"))
    assert hc.start_container_op("container_update", "voice", "u")[0] == 409               # another update is running


def _pinned_setup(hc, monkeypatch, tmp_path):
    compose = tmp_path / "compose.yaml"
    compose.write_text("services:\n  s:\n    image: x/y:1.0.0\n")
    group = {"project": "voice", "dir": str(tmp_path), "files": [str(compose)],
             "services": {"s": {"service": "s", "containers": ["voice-s-1"], "image_id": "sha256:old", "ref": "x/y:1.0.0", "kind": "pinned", "built": False}}}
    monkeypatch.setattr(hc, "discover", lambda: {"voice": group})
    monkeypatch.setattr(hc, "TIER_LOW", {"voice"})
    monkeypatch.setattr(hc, "DRY", False)
    (tmp_path / "containers.json").write_text(json.dumps({"projects": {"voice": {"project": "voice", "tier": "low", "update": True,
                                                          "services": [{"service": "s", "ref": "x/y:1.0.0", "kind": "pinned", "update": True, "newer": "1.1.0"}]}}}))
    return compose


def test_an_update_saves_a_restore_point_and_a_rollback_restores_the_compose_file(hc, monkeypatch, tmp_path):
    compose = _pinned_setup(hc, monkeypatch, tmp_path)
    calls = []

    def fake_run(cmd, timeout=60, env=None):
        calls.append(cmd)
        return (0, "[]") if cmd[:2] == ["docker", "inspect"] else (0, "")
    monkeypatch.setattr(hc, "run", fake_run)
    monkeypatch.setattr(hc, "wait_healthy", lambda *a, **k: (True, ""))
    monkeypatch.setenv("HOSTCTL_USER", "alice")
    assert hc.run_container("container_update", "voice") == 0
    assert "image: x/y:1.1.0" in compose.read_text()                                          # the version moved
    pts = hc.load_points("voice")
    assert len(pts) == 1 and pts[0]["services"][0]["old_image_id"] == "sha256:old"
    assert any(c[:3] == ["docker", "tag", "sha256:old"] and c[3].startswith("atrium-restore/voice-s:") for c in calls)      # the old build is kept under its own tag
    assert any("pull" in c for c in calls) and any("--no-deps" in c for c in calls)
    assert hc.run_container("container_rollback", "voice") == 0
    assert "image: x/y:1.0.0" in compose.read_text()                                          # and moved back
    assert hc.load_points("voice")[0]["rolled_back"] is True
    assert hc.container_state()["restore_points"] == {}


def test_a_failed_health_check_rolls_the_update_back_by_itself(hc, monkeypatch, tmp_path):
    compose = _pinned_setup(hc, monkeypatch, tmp_path)
    monkeypatch.setattr(hc, "run", lambda cmd, timeout=60, env=None: (0, "[]") if cmd[:2] == ["docker", "inspect"] else (0, ""))
    healthy = iter([(False, "s is unhealthy"), (True, "")])           # the new build fails, the rolled back one is fine
    monkeypatch.setattr(hc, "wait_healthy", lambda *a, **k: next(healthy))
    assert hc.run_container("container_update", "voice") == 1
    last = json.loads((tmp_path / "last-container-op.json").read_text())
    assert last["ok"] is False and last["auto_rolled_back"] is True and "unhealthy" in last["note"]
    assert "image: x/y:1.0.0" in compose.read_text()


def test_old_restore_points_are_pruned(hc, monkeypatch, tmp_path):
    for pid, age in (("old", 9 * 86400), ("new", 1 * 86400)):
        d = tmp_path / "restore" / pid
        d.mkdir(parents=True)
        (d / "point.json").write_text(json.dumps({"id": pid, "project": "voice", "created": hc.now() - age,
                                                  "services": [{"service": "s", "restore_tag": "atrium-restore/voice-s:" + pid}]}))
    monkeypatch.setattr(hc, "run", lambda cmd, timeout=60, env=None: (0, ""))
    hc.prune_points()
    assert [p["id"] for p in hc.load_points()] == ["new"]


def test_the_page_always_shows_the_current_tiers(hc, monkeypatch, tmp_path):
    (tmp_path / "containers.json").write_text(json.dumps({"checked_at": 1, "projects": {"files": {"project": "files", "tier": "high", "update": True, "services": []}}}))
    monkeypatch.setattr(hc, "unit_active", lambda unit: False)
    monkeypatch.setattr(hc, "TIER_MEDIUM", set())
    assert hc.container_state()["projects"][0]["tier"] == "high"
    monkeypatch.setattr(hc, "TIER_MEDIUM", {"files"})                      # the settings changed after the last check
    assert hc.container_state()["projects"][0]["tier"] == "medium"
