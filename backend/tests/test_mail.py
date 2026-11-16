import base64
import smtplib
from email import policy
from email.parser import BytesParser
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from app.services import mail as mail_module
from app.services.mail import (
    Attachment,
    MailError,
    MailNotConfiguredError,
    OutgoingMail,
    WorkspaceMailer,
    build_message,
    parse_incoming,
    xoauth2_string,
)
from tests.conftest import FakeMailer

PDF = b"%PDF-1.7 test"


def test_build_message_headers_and_attachment() -> None:
    msg = build_message(
        OutgoingMail(to=["kunde@firma.de"], subject="Rechnung RE-2026-00001 – Ü", text="Hallo",
                     html="<p>Hallo</p>", attachments=[Attachment("RE-2026-00001.pdf", "application/pdf", PDF)],
                     reply_to="buchhaltung@beispiel.de"),
        mailbox="rechnungen@beispiel.de",
        from_name="Beispiel UG (haftungsbeschränkt)",
    )
    raw = msg.as_bytes()
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    assert parsed["To"] == "kunde@firma.de"
    assert parsed["Subject"] == "Rechnung RE-2026-00001 – Ü"
    assert parsed["From"].addresses[0].addr_spec == "rechnungen@beispiel.de"
    assert parsed["Message-ID"].endswith("@beispiel.de>")
    attachments = list(parsed.iter_attachments())
    assert attachments[0].get_filename() == "RE-2026-00001.pdf"
    assert attachments[0].get_content() == PDF


@pytest.mark.parametrize(
    "mail",
    [
        OutgoingMail(to=["kunde@firma.de\r\nBcc: alle@evil.de"], subject="x", text="x"),
        OutgoingMail(to=["kunde@firma.de"], subject="x\r\nBcc: alle@evil.de", text="x"),
        OutgoingMail(to=["Kunde <kunde@firma.de>"], subject="x", text="x"),
        OutgoingMail(to=["kein-at-zeichen"], subject="x", text="x"),
        OutgoingMail(to=[], subject="x", text="x"),
        OutgoingMail(to=["a@b.de"], subject="x", text="x", attachments=[Attachment("a.pdf", "kaputt", b"")]),
    ],
)
def test_build_message_rejects_bad_input(mail: OutgoingMail) -> None:
    with pytest.raises(MailError):
        build_message(mail, mailbox="rechnungen@beispiel.de", from_name="X")


def test_xoauth2_format() -> None:
    assert xoauth2_string("a@b.de", "tok") == "user=a@b.de\x01auth=Bearer tok\x01\x01"


def test_parse_incoming_extracts_attachments() -> None:
    msg = build_message(
        OutgoingMail(to=["belege@beispiel.de"], subject="Rechnung Hetzner", text="Anbei",
                     attachments=[Attachment("hetzner.pdf", "application/pdf", PDF)]),
        mailbox="lieferant@hetzner.de", from_name="Hetzner",
    )
    incoming = parse_incoming("42", msg.as_bytes())
    assert incoming.uid == "42"
    assert incoming.subject == "Rechnung Hetzner"
    assert incoming.text.strip() == "Anbei"
    assert [(a.filename, a.content_type, a.data) for a in incoming.attachments] == [("hetzner.pdf", "application/pdf", PDF)]


class _StaticTokens:
    def access_token(self) -> str:
        return "ya29.token"


class _FakeSMTP:
    calls: ClassVar[list[str]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        _FakeSMTP.calls = [f"connect:{host}:{port}"]
        self.tls = False

    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, *args: Any) -> None:
        _FakeSMTP.calls.append("quit")

    def ehlo(self) -> None:
        _FakeSMTP.calls.append("ehlo")

    def starttls(self, context: Any) -> None:
        assert context.verify_mode.name == "CERT_REQUIRED"  # Zertifikat wird geprüft
        self.tls = True
        _FakeSMTP.calls.append("starttls")

    def auth(self, mechanism: str, authobject: Any, initial_response_ok: bool = True) -> None:
        assert self.tls, "Anmeldung nur nach STARTTLS"
        _FakeSMTP.calls.append(f"auth:{mechanism}:{authobject()}")

    def login(self, user: str, password: str) -> None:
        assert self.tls
        _FakeSMTP.calls.append(f"login:{user}")

    def send_message(self, msg: Any) -> None:
        _FakeSMTP.calls.append(f"send:{msg['To']}")


async def test_send_with_service_account_uses_starttls_then_xoauth2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail_module.smtplib, "SMTP", _FakeSMTP)
    mailer = WorkspaceMailer(mailbox="rechnungen@beispiel.de", from_name="UG", auth_mode="service_account",
                             token_provider=_StaticTokens())
    message_id = await mailer.send(OutgoingMail(to=["kunde@firma.de"], subject="Test", text="x"))
    assert message_id.endswith("@beispiel.de>")
    assert _FakeSMTP.calls == [
        "connect:smtp.gmail.com:587", "ehlo", "starttls", "ehlo",
        "auth:XOAUTH2:user=rechnungen@beispiel.de\x01auth=Bearer ya29.token\x01\x01",
        "send:kunde@firma.de", "quit",
    ]


async def test_send_with_app_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail_module.smtplib, "SMTP", _FakeSMTP)
    mailer = WorkspaceMailer(mailbox="rechnungen@beispiel.de", from_name="UG", auth_mode="app_password",
                             app_password="abcd efgh ijkl mnop")
    await mailer.send(OutgoingMail(to=["kunde@firma.de"], subject="Test", text="x"))
    assert "login:rechnungen@beispiel.de" in _FakeSMTP.calls


async def test_smtp_errors_become_mail_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(_FakeSMTP):
        def send_message(self, msg: Any) -> None:
            raise smtplib.SMTPRecipientsRefused({})

    monkeypatch.setattr(mail_module.smtplib, "SMTP", Broken)
    mailer = WorkspaceMailer(mailbox="r@beispiel.de", from_name="UG", auth_mode="service_account",
                             token_provider=_StaticTokens())
    with pytest.raises(MailError):
        await mailer.send(OutgoingMail(to=["kunde@firma.de"], subject="Test", text="x"))


def test_mailer_requires_configuration() -> None:
    with pytest.raises(MailNotConfiguredError):
        WorkspaceMailer(mailbox="", from_name="", auth_mode="app_password", app_password="x")
    with pytest.raises(MailNotConfiguredError):
        WorkspaceMailer(mailbox="r@beispiel.de", from_name="", auth_mode="service_account")
    with pytest.raises(MailNotConfiguredError):
        WorkspaceMailer(mailbox="r@beispiel.de", from_name="", auth_mode="app_password")


def test_test_mail_endpoint(auth_client: TestClient, mailer: FakeMailer) -> None:
    r = auth_client.post("/api/mail/test")
    assert r.status_code == 200, r.text
    assert r.json()["to"] == "anna@beispiel.de"
    assert mailer.sent[0].to == ["anna@beispiel.de"]


def test_test_mail_requires_login(client: TestClient) -> None:
    assert client.post("/api/mail/test").status_code == 401


def test_base64_sanity() -> None:
    # XOAUTH2 wird von imaplib/smtplib selbst base64-kodiert – hier nur Plausibilität
    assert base64.b64encode(xoauth2_string("a@b.de", "t").encode()).startswith(b"dXNlcj1h")


async def test_local_test_mode_sends_plain_to_catcher_and_has_no_inbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail_module.smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.calls.clear()
    mailer = WorkspaceMailer(mailbox="buchhaltung@openemployed.test", from_name="UG", auth_mode="local",
                             smtp_host="mailpit", smtp_port=1025)
    await mailer.send(OutgoingMail(to=["kunde@firma.de"], subject="Test", text="x"))
    assert _FakeSMTP.calls[0] == "connect:mailpit:1025"
    assert "starttls" not in _FakeSMTP.calls and not any(c.startswith(("auth", "login")) for c in _FakeSMTP.calls)
    assert await mailer.fetch_unprocessed() == []


def test_local_mail_mode_forbidden_in_production() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="Test-Mailfänger"):
        Settings(app_env="production", app_origin="https://app.beispiel.de", mail_auth_mode="local",
                 mail_mailbox="x@beispiel.de", _env_file=None)  # type: ignore[call-arg]


# ───────────────────────── Weitere Anbieter (Microsoft 365, IONOS & Co.) ─────────────────────────

class _FakeSMTPSSL(_FakeSMTP):
    def __init__(self, host: str, port: int, timeout: float = 30, context: Any = None) -> None:
        super().__init__(host, port, timeout)
        assert context is not None and context.verify_mode.name == "CERT_REQUIRED"
        self.tls = True  # implizites TLS (Port 465)


async def test_microsoft_365_uses_xoauth2_with_client_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.services.mail import MicrosoftTokenProvider

    seen: dict[str, Any] = {}

    def fake_post(url: str, data: dict[str, str], timeout: float) -> httpx.Response:
        seen.update(url=url, data=data)
        return httpx.Response(200, json={"access_token": "eyJ.ms", "expires_in": 3599}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(mail_module.smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.calls.clear()
    tokens = MicrosoftTokenProvider("tenant-1", "client-1", "geheim")
    mailer = WorkspaceMailer(mailbox="rechnungen@firma.de", from_name="UG", auth_mode="oauth_client", token_provider=tokens,
                             provider="microsoft", smtp_host="smtp.office365.com", smtp_port=587, imap_host="outlook.office365.com")
    await mailer.send(OutgoingMail(to=["kunde@firma.de"], subject="Test", text="x"))
    assert seen["url"] == "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"
    assert seen["data"]["scope"] == "https://outlook.office365.com/.default" and seen["data"]["grant_type"] == "client_credentials"
    assert _FakeSMTP.calls[:4] == ["connect:smtp.office365.com:587", "ehlo", "starttls", "ehlo"]
    assert "auth:XOAUTH2:user=rechnungen@firma.de\x01auth=Bearer eyJ.ms\x01\x01" in _FakeSMTP.calls
    assert tokens.access_token() == "eyJ.ms", "zwischengespeichert"


def test_microsoft_token_error_is_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.services.mail import MicrosoftTokenProvider

    monkeypatch.setattr(httpx, "post", lambda url, data, timeout: httpx.Response(
        401, json={"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret"},
        request=httpx.Request("POST", url)))
    with pytest.raises(MailError, match="AADSTS7000215"):
        MicrosoftTokenProvider("t", "c", "falsch").access_token()
    with pytest.raises(MailNotConfiguredError):
        MicrosoftTokenProvider("", "c", "s")


async def test_generic_provider_with_implicit_tls_and_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail_module.smtplib, "SMTP_SSL", _FakeSMTPSSL)
    _FakeSMTP.calls.clear()
    mailer = WorkspaceMailer(mailbox="info@firma.de", from_name="UG", auth_mode="app_password", app_password="pw",
                             provider="strato", smtp_host="smtp.strato.de", smtp_port=465, username="login-123")
    await mailer.send(OutgoingMail(to=["kunde@firma.de"], subject="Test", text="x"))
    assert _FakeSMTP.calls[0] == "connect:smtp.strato.de:465" and "starttls" not in _FakeSMTP.calls
    assert "login:login-123" in _FakeSMTP.calls
    assert mailer.describe()["security"] == "ssl" and not mailer.uses_gmail_labels


class _FakeIMAP:
    log: ClassVar[list[str]] = []

    def __init__(self, host: str, port: int, ssl_context: Any = None, timeout: float = 30) -> None:
        self.capabilities = ("IMAP4REV1", "MOVE", "UIDPLUS")
        _FakeIMAP.log.append(f"connect:{host}")

    def login(self, user: str, pw: str) -> None:
        _FakeIMAP.log.append(f"login:{user}")

    def select(self, folder: str) -> tuple[str, list[bytes]]:
        _FakeIMAP.log.append(f"select:{folder}")
        return "OK", [b"2"]

    def create(self, folder: str) -> None:
        _FakeIMAP.log.append(f"create:{folder}")

    def uid(self, cmd: str, *args: Any) -> tuple[str, list[Any]]:
        _FakeIMAP.log.append(f"uid:{cmd}:{args[0] if args else ''}")
        if cmd == "SEARCH":
            return "OK", [b"7 8"]
        if cmd == "FETCH" and args[1] == "(BODYSTRUCTURE)":
            if args[0] == b"7":
                return "OK", [b'7 (UID 7 BODYSTRUCTURE (("TEXT" "PLAIN") ("APPLICATION" "PDF" NIL NIL NIL "BASE64" 10 NIL ("ATTACHMENT" ("FILENAME" "r.pdf")))))']
            return "OK", [b'8 (UID 8 BODYSTRUCTURE ("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 5 1))']
        if cmd == "FETCH":
            raw = (b"From: a@b.de\r\nSubject: Rechnung\r\nContent-Type: multipart/mixed; boundary=X\r\n\r\n--X\r\nContent-Type: text/plain\r\n\r\nhi\r\n"
                   b"--X\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=r.pdf\r\n\r\n%PDF\r\n--X--\r\n")
            return "OK", [(b"7 (BODY[] {1}", raw), b")"]
        return "OK", [b""]

    def logout(self) -> None:
        _FakeIMAP.log.append("logout")


async def test_generic_imap_fetches_only_attachments_and_moves_to_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail_module.imaplib, "IMAP4_SSL", _FakeIMAP)
    _FakeIMAP.log.clear()
    mailer = WorkspaceMailer(mailbox="belege@firma.de", from_name="UG", auth_mode="app_password", app_password="pw",
                             provider="ionos", smtp_host="smtp.ionos.de", imap_host="imap.ionos.de", imap_folder="Rechnungen")
    mails = await mailer.fetch_unprocessed()
    assert [m.uid for m in mails] == ["7"] and mails[0].attachments[0].filename == "r.pdf"
    assert 'select:"Rechnungen"' in _FakeIMAP.log and not any("X-GM" in x for x in _FakeIMAP.log)
    assert not any(x == "uid:FETCH:b'8'" and "BODY.PEEK" in x for x in _FakeIMAP.log)
    _FakeIMAP.log.clear()
    await mailer.mark_processed("7")
    assert 'create:"openemployed-verarbeitet"' in _FakeIMAP.log and "uid:MOVE:7" in _FakeIMAP.log


def test_provider_presets_and_validation() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    ms = Settings(mail_provider="microsoft", mail_auth_mode="oauth_client", mail_mailbox="r@firma.de", ms_tenant_id="t",
                  ms_client_id="c", ms_client_secret="s", _env_file=None)  # type: ignore[call-arg]
    assert ms.mail_servers == ("smtp.office365.com", 587, "starttls", "outlook.office365.com", 993) and ms.mail_configured
    strato = Settings(mail_provider="strato", mail_auth_mode="app_password", _env_file=None)  # type: ignore[call-arg]
    assert strato.mail_servers[:3] == ("smtp.strato.de", 465, "ssl")
    own = Settings(mail_provider="smtp", mail_auth_mode="app_password", smtp_host="mail.firma.de", imap_host="mail.firma.de",
                   _env_file=None)  # type: ignore[call-arg]
    assert own.mail_servers == ("mail.firma.de", 587, "starttls", "mail.firma.de", 993)
    with pytest.raises(ValidationError, match="service_account"):
        Settings(mail_provider="microsoft", mail_auth_mode="service_account", _env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="MAIL_PROVIDER"):
        Settings(mail_provider="aol", _env_file=None)  # type: ignore[call-arg]
