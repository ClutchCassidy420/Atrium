import fcntl
import json
from pathlib import Path
import subprocess

TOOL = Path(__file__).resolve().parents[1] / "checkpoint"


def test_private_checkpoint_roundtrip_and_collision(tmp_path):
    repo = tmp_path / "source"
    subprocess.run(["git", "init", "-q", repo], check=True)
    (repo / "atrium").mkdir()
    (repo / "atrium/start").write_text("fixture")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fixture"], cwd=repo, check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    original = tmp_path / "original"
    original.mkdir(mode=0o700)
    (original / "web-data").mkdir()
    (original / "web-data/users.json").write_text('{"test":"fixture"}')
    (original / "gateway.key").write_text("synthetic")
    (original / "runtime-env").mkdir()
    saved, recovered = tmp_path / "saved", tmp_path / "recovered"
    save = [TOOL, "save", original, saved, "--release", repo]
    with (original / "start.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert subprocess.run(save, capture_output=True).returncode != 0
        assert not saved.exists()
    subprocess.run(save, check=True)
    assert json.loads((saved / "release.json").read_text())["revision"] == revision
    assert not (saved / "state/runtime-env").exists()
    assert saved.stat().st_mode & 0o077 == 0
    (original / "gateway.key").write_text("updated")
    subprocess.run([TOOL, "restore", saved, recovered], check=True)
    assert (recovered / "gateway.key").read_text() == "synthetic"
    assert (original / "gateway.key").read_text() == "updated"
    assert subprocess.run([TOOL, "restore", saved, original], capture_output=True).returncode != 0
    assert subprocess.run([TOOL, "restore", saved, repo / "private"], capture_output=True).returncode != 0
