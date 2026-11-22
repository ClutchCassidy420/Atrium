"""Email backend: synthetic tests only (no real mailbox, no network). Each test names the defect it guards against."""
import asyncio
import imaplib
import smtplib
import ssl
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import pytest
from fastapi import HTTPException

import config as configmod
import mail_backend as mb
from conftest import PW, login

UID = "0f0e0d0c-0b0a-4909-8807-060504030201"
H = {"origin": "https://app.test"}


def run(coro):
    return asyncio.run(coro)


class Box:
    """A scripted IMAP connection that records every command."""

    def __init__(self, caps=("IMAP4REV1", "MOVE", "UIDPLUS"), size=100, raw=b"raw-message", folders=None, append_ok=True):
        self.capabilities = caps
        self.calls = []
        self.size = size
        self.raw = raw
        self.append_ok = append_ok
        self.folders = folders or [
            b'(\\HasNoChildren) "/" "INBOX"', b'(\\Sent \\HasNoChildren) "/" "Sent"',
            b'(\\Trash \\HasNoChildren) "/" "Trash"', b'(\\Junk \\HasNoChildren) "/" "Junk"']

    def list(self):
        return "OK", self.folders

    def select(self, *a, **k):
        self.calls.append(("select",) + a)
        return "OK", [b"1"]

    def expunge(self):
        self.calls.append(("MAILBOX-WIDE-EXPUNGE",))
        return "OK", []

    def append(self, *a):
        self.calls.append(("append",) + a[:1])
        return ("OK" if self.append_ok else "NO"), []

    def uid(self, command, *a):
        self.calls.append((command,) + a)
        if command == "FETCH" and "RFC822.SIZE" in a[-1] and "BODY" not in a[-1]:
            return "OK", [b"1 (UID 7 RFC822.SIZE %d)" % self.size]
        if command == "FETCH":
            return "OK", [(b"1 (UID 7 FLAGS ())", self.raw)]
        return "OK", []

    def names(self):
        return [c[0] for c in self.calls]


def use(monkeypatch, box):
    @contextmanager
    def connection():
        yield box
    monkeypatch.setattr(mb, "_imap", connection)
    return box


@pytest.fixture()
def mailcfg(tmp_path):
    c = SimpleNamespace(mail_address="me@example.test", mail_token="tok", mail_imap_host="imap.example.test", mail_imap_port=993,
                        mail_imap_tls_server_name="", mail_smtp_host="smtp.example.test", mail_smtp_port=465, mail_smtp_tls_server_name="",
                        mail_webmail_url="", data_dir=tmp_path)
    mb.configure(c)
    return c


# ---------------------------------------------------------------- basics

def test_addresses_are_checked():
    assert mb._addrs("A <a@x.com>, b@y.org") == ["a@x.com", "b@y.org"]
    with pytest.raises(HTTPException):
        mb._addrs("not-an-address")


def test_html_is_cleaned_and_remote_images_blocked():
    out = mb.clean_html('<p onclick="x()">hi</p><script>alert(1)</script><form><input></form><img src="https://t.example/p.gif"><a href="javascript:x()">a</a>')
    assert "<script" not in out and "onclick" not in out and "<form" not in out and "javascript:" not in out
    assert " src=" not in out and "<img data-blocked-src=" in out          # the remote image is kept only as an inert note, never loaded


def test_configure_needs_a_mailbox_token_and_host(mailcfg):
    assert mb.configure(mailcfg) is True
    mailcfg.mail_token = ""
    assert mb.configure(mailcfg) is False


def test_status_lists_features_an_install_added(mailcfg):
    assert run(mb.status())["features"] == []
    mb.FEATURES.add("blocklist")
    try:
        assert run(mb.status())["features"] == ["blocklist"]
    finally:
        mb.FEATURES.discard("blocklist")


# ---------------------------------------------------------------- verified TLS (no switch to turn it off)

def test_imap_requires_verified_implicit_tls():
    with pytest.raises(ValueError):
        mb._VerifiedIMAP4SSL("imap.example.test", 143, "imap.example.test")        # only port 993
    with pytest.raises(ValueError):
        mb._VerifiedIMAP4SSL("", 993, "imap.example.test")
    with pytest.raises(ValueError):
        mb._VerifiedIMAP4SSL("imap.example.test", 993, "")


def test_smtp_requires_a_host_and_identity():
    with pytest.raises(ValueError):
        mb._VerifiedSMTPSSL("", 465, "smtp.example.test")
    with pytest.raises(ValueError):
        mb._VerifiedSMTPSSL("smtp.example.test", 465, "")


def test_smtp_context_verifies_certificates(monkeypatch):
    seen = {}

    def fake_init(self, host, port, timeout=None, context=None, **k):
        seen.update(host=host, port=port, check=context.check_hostname, mode=context.verify_mode)
    monkeypatch.setattr(smtplib.SMTP_SSL, "__init__", fake_init)
    mb._VerifiedSMTPSSL("10.0.0.5", 465, "mail.example.test")
    assert seen["check"] is True and seen["mode"] == ssl.CERT_REQUIRED


def test_source_has_no_way_to_disable_verification():
    text = Path(mb.__file__).read_text(encoding="utf-8")
    assert "CERT_NONE" not in text and "check_hostname = False" not in text


# ---------------------------------------------------------------- reading: never cut short

def test_large_message_is_returned_whole(mailcfg, monkeypatch):
    raw = b"x" * (mb.MAX_BODY * 4 + 128)               # past the old silent cut-off
    box = use(monkeypatch, Box(size=len(raw), raw=raw))
    assert mb._fetch_message("INBOX", 7, mark_seen=False) == raw


def test_oversize_message_gets_a_clear_error_not_a_partial_download(mailcfg, monkeypatch):
    box = use(monkeypatch, Box(size=mb.MAX_MESSAGE + 1))
    with pytest.raises(HTTPException) as e:
        mb._fetch_message("INBOX", 7)
    assert e.value.status_code == 413
    assert not any(c[0] == "FETCH" and "BODY.PEEK[]" in c[-1] for c in box.calls)       # the body was never downloaded


# ---------------------------------------------------------------- move and delete touch only the chosen message

def test_move_uses_uid_move_and_never_a_mailbox_wide_expunge(mailcfg, monkeypatch):
    box = use(monkeypatch, Box())
    run(mb.move(7, mb.MoveIn(folder="INBOX", to="Junk")))
    assert ("MOVE", "7", '"Junk"') in box.calls and "MAILBOX-WIDE-EXPUNGE" not in box.names()


def test_move_without_move_extension_copies_then_expunges_only_that_uid(mailcfg, monkeypatch):
    box = use(monkeypatch, Box(caps=("IMAP4REV1", "UIDPLUS")))
    run(mb.move(7, mb.MoveIn(folder="INBOX", to="Junk")))
    assert ("COPY", "7", '"Junk"') in box.calls and ("EXPUNGE", "7") in box.calls and "MAILBOX-WIDE-EXPUNGE" not in box.names()


def test_capabilities_are_read_whether_text_or_bytes(mailcfg, monkeypatch):
    assert mb._caps(Box(caps=(b"MOVE", "uidplus"))) == {"MOVE", "UIDPLUS"}
    box = use(monkeypatch, Box(caps=(b"IMAP4REV1", b"MOVE")))
    run(mb.move(7, mb.MoveIn(folder="INBOX", to="Junk")))
    assert ("MOVE", "7", '"Junk"') in box.calls


def test_move_is_refused_when_the_server_cannot_do_it_safely(mailcfg, monkeypatch):
    box = use(monkeypatch, Box(caps=("IMAP4REV1",)))
    with pytest.raises(HTTPException):
        run(mb.move(7, mb.MoveIn(folder="INBOX", to="Junk")))
    assert "COPY" not in box.names() and "MAILBOX-WIDE-EXPUNGE" not in box.names()


def test_delete_moves_to_trash_and_only_expunges_that_uid_from_trash(mailcfg, monkeypatch):
    box = use(monkeypatch, Box())
    run(mb.delete(7, "INBOX"))
    assert ("MOVE", "7", '"Trash"') in box.calls
    box2 = use(monkeypatch, Box())
    run(mb.delete(9, "Trash"))
    assert ("STORE", "9", "+FLAGS", "(\\Deleted)") in box2.calls and ("EXPUNGE", "9") in box2.calls and "MAILBOX-WIDE-EXPUNGE" not in box2.names()


def test_delete_needs_a_trash_folder(mailcfg, monkeypatch):
    use(monkeypatch, Box(folders=[b'(\\HasNoChildren) "/" "INBOX"']))
    with pytest.raises(HTTPException) as e:
        run(mb.delete(7, "INBOX"))
    assert e.value.status_code == 409


# ---------------------------------------------------------------- sending

class SMTP:
    """Stands in for the verified SMTP connection. `mode` scripts what happens."""
    mode = "ok"
    sent = []

    def __init__(self, host, port, name):
        self.args = (host, port, name)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, token):
        if SMTP.mode == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"no")

    def send_message(self, msg, from_addr, to_addrs):
        if SMTP.mode == "refused":
            raise smtplib.SMTPRecipientsRefused({a: (550, b"no") for a in to_addrs})
        if SMTP.mode == "dropped":
            raise smtplib.SMTPServerDisconnected("lost after DATA")
        SMTP.sent.append((from_addr, tuple(to_addrs)))
        return {"bad@example.test": (550, b"no")} if SMTP.mode == "partial" else {}


@pytest.fixture()
def smtp(monkeypatch, mailcfg):
    SMTP.mode, SMTP.sent = "ok", []
    monkeypatch.setattr(mb, "_VerifiedSMTPSSL", SMTP)
    use(monkeypatch, Box())
    return SMTP


def mail(**k):
    d = dict(to="you@example.test", subject="Hello", body="Hi")
    d.update(k)
    return mb.SendIn(**d)


def test_smtp_uses_the_configured_certificate_name(mailcfg, smtp, monkeypatch):
    seen = []
    monkeypatch.setattr(mb, "_VerifiedSMTPSSL", lambda h, p, n: seen.append((h, p, n)) or SMTP(h, p, n))
    mailcfg.mail_smtp_tls_server_name = "mail.example.test"
    run(mb.send(mail()))
    assert seen == [("smtp.example.test", 465, "mail.example.test")]


def test_a_retry_with_the_same_submission_id_sends_one_message(mailcfg, smtp):
    first = run(mb.send(mail(submission_id=UID)))
    again = run(mb.send(mail(submission_id=UID)))
    assert len(SMTP.sent) == 1 and first["ok"] and again["duplicate"] is True


def test_failure_filing_in_sent_after_acceptance_is_not_reported_as_a_failed_send(mailcfg, smtp, monkeypatch):
    use(monkeypatch, Box(append_ok=False))
    out = run(mb.send(mail(submission_id=UID)))
    assert out == {"ok": True, "filed": False} and len(SMTP.sent) == 1
    again = run(mb.send(mail(submission_id=UID)))                  # the user retries: still one delivered copy
    assert len(SMTP.sent) == 1 and again["duplicate"] is True


def test_imap_down_after_acceptance_is_not_reported_as_a_failed_send(mailcfg, smtp, monkeypatch):
    @contextmanager
    def down():
        raise HTTPException(status_code=502, detail="mail server")
        yield
    monkeypatch.setattr(mb, "_imap", down)
    assert run(mb.send(mail(submission_id=UID)))["filed"] is False and len(SMTP.sent) == 1


def test_refused_recipients_allow_a_corrected_retry(mailcfg, smtp):
    SMTP.mode = "refused"
    with pytest.raises(HTTPException) as e:
        run(mb.send(mail(submission_id=UID)))
    assert e.value.status_code == 400
    SMTP.mode = "ok"
    assert run(mb.send(mail(submission_id=UID)))["ok"] and len(SMTP.sent) == 1


def test_sign_in_failure_allows_a_retry(mailcfg, smtp):
    SMTP.mode = "auth"
    with pytest.raises(HTTPException):
        run(mb.send(mail(submission_id=UID)))
    SMTP.mode = "ok"
    assert run(mb.send(mail(submission_id=UID)))["ok"]


def test_unconfirmed_delivery_blocks_a_blind_second_send(mailcfg, smtp):
    SMTP.mode = "dropped"
    with pytest.raises(HTTPException) as e:
        run(mb.send(mail(submission_id=UID)))
    assert e.value.status_code == 502 and "unconfirmed" in e.value.detail.lower()
    SMTP.mode = "ok"
    with pytest.raises(HTTPException) as e2:
        run(mb.send(mail(submission_id=UID)))
    assert e2.value.status_code == 409 and SMTP.sent == []


def test_partly_refused_recipients_are_reported(mailcfg, smtp):
    SMTP.mode = "partial"
    out = run(mb.send(mail(to="you@example.test, bad@example.test")))
    assert out["refused"] == ["bad@example.test"]


# ---------------------------------------------------------------- the pages exist only when a mailbox is configured

def test_email_pages_and_routes_appear_only_when_configured(make, tmp_path):
    plain = make()
    login(plain)
    assert "mail-inbox" not in plain.get("/shell-config.js").text and plain.get("/api/mail/status").status_code == 404
    c = configmod.Config(data_dir=tmp_path / "d2", instance_dir=tmp_path / "i2", trusted_proxies=["testclient"], mail_address="me@example.test",
                         mail_token="tok", mail_imap_host="imap.example.test")
    both = make(config=c)
    login(both)
    assert "mail-inbox" in both.get("/shell-config.js").text
    assert both.get("/api/mail/status").status_code == 200


# ---------------------------------------------------------------- capabilities after sign-in (a front-end proxy lists fewer before it)

def test_capabilities_are_asked_again_after_sign_in_because_a_proxy_lists_fewer_before(mailcfg, monkeypatch):
    class Proxy:
        capabilities = ("IMAP4REV1",)             # what the greeting said: no MOVE, no UIDPLUS

        def __init__(self, *a, **k):
            pass

        def login(self, user, token):
            return "OK", []

        def _get_capabilities(self):
            self.capabilities = ("IMAP4REV1", "MOVE", "UIDPLUS")

        def logout(self):
            return "BYE", []
    monkeypatch.setattr(mb, "_VerifiedIMAP4SSL", Proxy)
    with mb._imap() as m:
        assert {"MOVE", "UIDPLUS"} <= mb._caps(m)


def test_a_server_that_cannot_report_capabilities_again_keeps_the_greeting_ones(mailcfg, monkeypatch):
    class Quiet:
        capabilities = ("IMAP4REV1", "MOVE")

        def __init__(self, *a, **k):
            pass

        def login(self, user, token):
            return "OK", []

        def _get_capabilities(self):
            raise imaplib.IMAP4.error("no")

        def logout(self):
            return "BYE", []
    monkeypatch.setattr(mb, "_VerifiedIMAP4SSL", Quiet)
    with mb._imap() as m:
        assert mb._caps(m) == {"IMAP4REV1", "MOVE"}
