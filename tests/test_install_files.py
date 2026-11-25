"""The quick-start install: the files a newcomer runs must stay consistent with the app."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import config as configmod


def test_plain_http_is_off_unless_asked_for():
    assert configmod.load({}).plain_http is False
    assert configmod.load({"ALLOW_PLAIN_HTTP": "1"}).plain_http is True
    assert configmod.load({"ALLOW_PLAIN_HTTP": "no"}).plain_http is False


def test_the_quick_start_compose_file_needs_no_edits_and_stays_closed():
    text = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "external:" not in text and "/path/to" not in text            # no networks or folders to make first
    assert '"${ATRIUM_BIND:-127.0.0.1}' in text                          # this computer only, unless the person opens it up
    assert "read_only: true" in text and "cap_drop: [ALL]" in text       # same hardening as the production example
    assert "required: false" in text                                     # .env is optional


def test_the_dockerfile_gives_the_app_user_writable_defaults():
    text = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    assert "instance.example/" in text and "chown 10001:10001 /data" in text
    assert "INSTANCE_DIR=/srv/instance-default" in text


def test_the_env_example_is_committed_and_has_no_secrets():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "!.env.example" in ignore
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            assert line.split("=", 1)[1] == "" or line.startswith("ATRIUM_"), line
