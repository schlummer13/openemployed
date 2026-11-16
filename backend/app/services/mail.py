"""E-Mail über beliebige Anbieter: Versand per SMTP, Belegeingang per IMAP.

Anbieter (``MAIL_PROVIDER``) und Anmeldung (``MAIL_AUTH_MODE``):
- ``google`` + ``service_account``: Google-Workspace-Dienstkonto mit domänenweiter Delegation, XOAUTH2 mit kurzlebigem Token
- ``microsoft`` + ``oauth_client``: Microsoft 365 / Exchange Online, App-Registrierung in Entra ID (Client-Credentials),
  XOAUTH2 für SMTP AUTH und IMAP – Microsoft hat die Standardauthentifizierung (Passwort) für Exchange Online abgeschaltet
- jeder Anbieter + ``app_password``: Benutzername/Passwort bzw. App-Passwort (IONOS, Strato, netcup, Hetzner, mailbox.org,
  Posteo, GMX, WEB.DE, T-Online, iCloud, Fastmail, Zoho, eigener Mailserver …)

Server und Verschlüsselung kommen aus der Voreinstellung des Anbieters oder aus SMTP_HOST/IMAP_HOST.
STARTTLS (587) oder implizites TLS (465/993); Zertifikate werden immer geprüft.
"""

from __future__ import annotations

import asyncio
import contextlib
import email.policy
import imaplib
import smtplib
import ssl
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from email.headerregistry import Address
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid, parseaddr
from typing import Literal, Protocol

GMAIL_SCOPE = "https://mail.google.com/"
OUTLOOK_SCOPE = "https://outlook.office365.com/.default"
Security = Literal["starttls", "ssl"]


@dataclass(frozen=True, slots=True)
class Provider:
    label: str
    smtp_host: str
    smtp_port: int
    imap_host: str
    imap_port: int = 993
    auth: tuple[str, ...] = ("app_password",)
    hint: str = ""

    @property
    def security(self) -> Security:
        return "ssl" if self.smtp_port == 465 else "starttls"


# Voreinstellungen; Hosts lassen sich jederzeit per SMTP_HOST/IMAP_HOST überschreiben
PROVIDERS: dict[str, Provider] = {
    "google": Provider("Google Workspace / Gmail", "smtp.gmail.com", 587, "imap.gmail.com", auth=("service_account", "app_password"),
                       hint="Dienstkonto mit domänenweiter Delegation (empfohlen) oder App-Passwort bei 2-Schritt-Bestätigung"),
    "microsoft": Provider("Microsoft 365 / Exchange Online", "smtp.office365.com", 587, "outlook.office365.com", auth=("oauth_client",),
                          hint="App-Registrierung in Entra ID mit SMTP.SendAsApp und IMAP.AccessAsApp; Postfachberechtigung per "
                               "Exchange-PowerShell – Anleitung in docs/betrieb.md"),
    "ionos": Provider("IONOS", "smtp.ionos.de", 587, "imap.ionos.de"),
    "strato": Provider("STRATO", "smtp.strato.de", 465, "imap.strato.de"),
    "netcup": Provider("netcup (Webhosting)", "", 587, "", hint="Server steht im CCP/WCP, z. B. mxe8xx.netcup.net"),
    "hetzner": Provider("Hetzner (KonsoleH)", "mail.your-server.de", 587, "mail.your-server.de"),
    "allinkl": Provider("ALL-INKL.COM", "", 587, "", hint="Server steht im KAS, z. B. w0123456.kasserver.com"),
    "mailbox_org": Provider("mailbox.org", "smtp.mailbox.org", 465, "imap.mailbox.org"),
    "posteo": Provider("Posteo", "posteo.de", 587, "posteo.de"),
    "gmx": Provider("GMX", "mail.gmx.net", 587, "imap.gmx.net", hint="IMAP/SMTP in den GMX-Einstellungen aktivieren"),
    "webde": Provider("WEB.DE", "smtp.web.de", 587, "imap.web.de", hint="IMAP/SMTP in den WEB.DE-Einstellungen aktivieren"),
    "telekom": Provider("Telekom (T-Online)", "securesmtp.t-online.de", 465, "secureimap.t-online.de",
                        hint="eigenes E-Mail-Passwort im Telekom-Kundencenter anlegen"),
    "icloud": Provider("iCloud Mail", "smtp.mail.me.com", 587, "imap.mail.me.com", hint="app-spezifisches Passwort erforderlich"),
    "fastmail": Provider("Fastmail", "smtp.fastmail.com", 465, "imap.fastmail.com", hint="App-Passwort erforderlich"),
    "zoho": Provider("Zoho Mail (EU)", "smtppro.zoho.eu", 465, "imappro.zoho.eu"),
    "smtp": Provider("Eigener Mailserver / anderer Anbieter", "", 587, ""),
}
PROCESSED_LABEL = "openemployed/verarbeitet"
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # Gmail-Grenze ca. 25 MB inkl. Kodierung


class MailError(RuntimeError):
    pass


class MailNotConfiguredError(MailError):
    pass


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class OutgoingMail:
    to: Sequence[str]
    subject: str
    text: str
    html: str | None = None
    attachments: Sequence[Attachment] = field(default_factory=tuple)
    reply_to: str | None = None
    cc: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class IncomingMail:
    uid: str
    message_id: str | None
    sender: str
    subject: str
    date: str | None
    text: str
    attachments: tuple[Attachment, ...]


def _no_header_injection(value: str, what: str) -> str:
    if "\r" in value or "\n" in value:
        raise MailError(f"{what} enthält Zeilenumbrüche")
    return value


def _valid_address(value: str) -> str:
    """Nur reine Adressen (ohne Anzeigenamen) – verhindert Header-Tricks."""
    _no_header_injection(value, "Adresse")
    candidate = value.strip()
    _, addr = parseaddr(candidate)
    local, _, domain = addr.partition("@")
    if addr != candidate or addr.count("@") != 1 or not local or "." not in domain or " " in addr:
        raise MailError(f"Ungültige E-Mail-Adresse: {value!r}")
    return addr


def build_message(mail: OutgoingMail, *, mailbox: str, from_name: str) -> EmailMessage:
    """Erzeugt eine RFC-5322-konforme Nachricht (UTF-8, SMTP-Policy, eigene Message-ID)."""
    if not mail.to:
        raise MailError("Kein Empfänger")
    sender = _valid_address(mailbox)
    msg = EmailMessage(policy=email.policy.SMTP)
    local, _, domain = sender.partition("@")
    msg["From"] = Address(display_name=_no_header_injection(from_name, "Absendername"), username=local, domain=domain)
    msg["To"] = ", ".join(_valid_address(a) for a in mail.to)
    if mail.cc:
        msg["Cc"] = ", ".join(_valid_address(a) for a in mail.cc)
    if mail.reply_to:
        msg["Reply-To"] = _valid_address(mail.reply_to)
    msg["Subject"] = _no_header_injection(mail.subject, "Betreff")
    msg["Date"] = formatdate(usegmt=True)
    msg["Message-ID"] = make_msgid(domain=domain)
    msg.set_content(mail.text)
    if mail.html:
        msg.add_alternative(mail.html, subtype="html")
    total = 0
    for att in mail.attachments:
        total += len(att.data)
        if total > MAX_ATTACHMENT_BYTES:
            raise MailError("Anhänge zu groß (max. 20 MB)")
        maintype, _, subtype = att.content_type.partition("/")
        if not maintype or not subtype:
            raise MailError(f"Ungültiger Inhaltstyp: {att.content_type!r}")
        msg.add_attachment(att.data, maintype=maintype, subtype=subtype,
                           filename=_no_header_injection(att.filename, "Dateiname"))
    return msg


def xoauth2_string(user: str, access_token: str) -> str:
    """SASL XOAUTH2 (Google): user=<mail>^Aauth=Bearer <token>^A^A"""
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01"


def parse_incoming(uid: str, raw: bytes) -> IncomingMail:
    msg = BytesParser(policy=email.policy.default).parsebytes(raw)
    attachments: list[Attachment] = []
    text_part = ""
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        data = payload if isinstance(payload, bytes) else b""
        if filename or part.get_content_disposition() == "attachment":
            attachments.append(Attachment(filename=filename or "anhang", content_type=part.get_content_type(), data=data))
        elif part.get_content_type() == "text/plain" and not text_part:
            text_part = data.decode(part.get_content_charset() or "utf-8", errors="replace")
    return IncomingMail(
        uid=uid,
        message_id=msg.get("Message-ID"),
        sender=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        date=msg.get("Date"),
        text=text_part,
        attachments=tuple(attachments),
    )


class TokenProvider(Protocol):
    def access_token(self) -> str: ...


class ServiceAccountTokenProvider:
    """Zugriffstoken für ein Postfach über domänenweite Delegation; wird bis kurz vor Ablauf zwischengespeichert."""

    def __init__(self, key_file: str, mailbox: str) -> None:
        self._key_file = key_file
        self._mailbox = mailbox
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    def access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at - 120:
                return self._token
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account

            try:
                creds = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                    self._key_file, scopes=[GMAIL_SCOPE], subject=self._mailbox
                )
                creds.refresh(Request())
            except FileNotFoundError as exc:
                raise MailNotConfiguredError("Dienstkonto-Schlüssel nicht gefunden") from exc
            except Exception as exc:
                raise MailError(f"Zugriffstoken konnte nicht geholt werden: {type(exc).__name__}") from exc
            self._token = str(creds.token)
            self._expires_at = creds.expiry.timestamp() if creds.expiry else time.time() + 300
            return self._token


class MicrosoftTokenProvider:
    """Zugriffstoken für Exchange Online per Client-Credentials (App-Registrierung in Entra ID), zwischengespeichert."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str, *, timeout: float = 20.0) -> None:
        if not (tenant_id and client_id and client_secret):
            raise MailNotConfiguredError("MS_TENANT_ID, MS_CLIENT_ID und MS_CLIENT_SECRET sind für Microsoft 365 erforderlich")
        self._url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        self._form = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret,
                      "scope": OUTLOOK_SCOPE}
        self._timeout = timeout
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    def access_token(self) -> str:
        import httpx

        with self._lock:
            if self._token and time.time() < self._expires_at - 120:
                return self._token
            try:
                r = httpx.post(self._url, data=self._form, timeout=self._timeout)
            except httpx.HTTPError as exc:
                raise MailError(f"Microsoft-Anmeldung nicht erreichbar: {type(exc).__name__}") from exc
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code != 200 or "access_token" not in body:
                code = body.get("error_description", body.get("error", r.status_code))
                raise MailError(f"Microsoft-Anmeldung fehlgeschlagen: {str(code)[:300]}")
            self._token = str(body["access_token"])
            self._expires_at = time.time() + float(body.get("expires_in", 3600))
            return self._token


AuthMode = Literal["service_account", "oauth_client", "app_password", "local"]


class WorkspaceMailer:
    """SMTP-Versand und IMAP-Belegeingang – für Google, Microsoft 365 und jeden IMAP/SMTP-Anbieter."""

    def __init__(
        self,
        *,
        mailbox: str,
        from_name: str,
        auth_mode: AuthMode,
        token_provider: TokenProvider | None = None,
        app_password: str = "",
        imap_mailbox: str = "",
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 587,
        imap_host: str = "imap.gmail.com",
        imap_port: int = 993,
        timeout: float = 30.0,
        provider: str = "google",
        username: str = "",
        smtp_security: Security | None = None,
        imap_folder: str = "INBOX",
        processed_folder: str = "openemployed-verarbeitet",
    ) -> None:
        if not mailbox:
            raise MailNotConfiguredError("MAIL_MAILBOX ist nicht gesetzt")
        if auth_mode in ("service_account", "oauth_client") and token_provider is None:
            raise MailNotConfiguredError("Dienstkonto nicht eingerichtet" if auth_mode == "service_account"
                                         else "Microsoft-App-Registrierung nicht eingerichtet")
        if auth_mode == "app_password" and not app_password:
            raise MailNotConfiguredError("MAIL_APP_PASSWORD ist nicht gesetzt")
        if auth_mode != "local" and not smtp_host:
            raise MailNotConfiguredError("SMTP_HOST ist nicht gesetzt (für diesen Anbieter keine Voreinstellung)")
        if any(c in processed_folder for c in '"\\\r\n') or any(c in imap_folder for c in '"\\\r\n'):
            raise MailNotConfiguredError("Ungültiger IMAP-Ordnername")
        self.mailbox = mailbox
        self.from_name = from_name or mailbox
        self.imap_mailbox = imap_mailbox or mailbox
        self.provider = provider
        self._username = username or mailbox
        self._imap_username = username if (username and not imap_mailbox) else self.imap_mailbox
        self._auth_mode = auth_mode
        self._tokens = token_provider
        self._app_password = app_password
        self._smtp = (smtp_host, smtp_port)
        self._smtp_security: Security = smtp_security or ("ssl" if smtp_port == 465 else "starttls")
        self._imap = (imap_host, imap_port)
        self._imap_folder = imap_folder
        self._processed_folder = processed_folder
        self._timeout = timeout
        self._ssl = ssl.create_default_context()

    @property
    def uses_gmail_labels(self) -> bool:
        return self.provider == "google"

    def describe(self) -> dict[str, str]:
        """Für Status-Anzeigen – ohne Geheimnisse."""
        label = PROVIDERS[self.provider].label if self.provider in PROVIDERS else self.provider
        return {"provider": self.provider, "provider_label": label, "auth_mode": self._auth_mode,
                "smtp": f"{self._smtp[0]}:{self._smtp[1]}",
                "security": self._smtp_security, "imap": f"{self._imap[0]}:{self._imap[1]}" if self._imap[0] else "",
                "imap_folder": self._imap_folder}

    def _require_tokens(self) -> TokenProvider:
        if self._tokens is None:
            raise MailNotConfiguredError("Dienstkonto nicht eingerichtet")
        return self._tokens

    # ───────── Versand ─────────
    @property
    def is_local(self) -> bool:
        """Testmodus: lokaler Mail-Fänger, nichts verlässt den Rechner."""
        return self._auth_mode == "local"

    def _send_sync(self, msg: EmailMessage) -> None:
        if self.is_local:
            # Nur für den lokalen Testbetrieb (Mailpit im Docker-Netz) – in Produktion per Konfiguration verboten
            with smtplib.SMTP(*self._smtp, timeout=self._timeout) as plain:
                plain.send_message(msg)
            return
        smtp: smtplib.SMTP
        if self._smtp_security == "ssl":
            smtp = smtplib.SMTP_SSL(*self._smtp, timeout=self._timeout, context=self._ssl)
        else:
            smtp = smtplib.SMTP(*self._smtp, timeout=self._timeout)
        with smtp:
            smtp.ehlo()
            if self._smtp_security == "starttls":
                smtp.starttls(context=self._ssl)
                smtp.ehlo()
            if self._auth_mode in ("service_account", "oauth_client"):
                auth = xoauth2_string(self._username if self._auth_mode == "oauth_client" else self.mailbox,
                                      self._require_tokens().access_token())
                smtp.auth("XOAUTH2", lambda challenge=None: auth, initial_response_ok=True)
            else:
                smtp.login(self._username, self._app_password)
            smtp.send_message(msg)

    async def send(self, mail: OutgoingMail) -> str:
        """Versendet die Nachricht und gibt die Message-ID zurück (für das Versandprotokoll)."""
        msg = build_message(mail, mailbox=self.mailbox, from_name=self.from_name)
        try:
            await asyncio.to_thread(self._send_sync, msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"Versand fehlgeschlagen: {type(exc).__name__}: {exc}") from exc
        return str(msg["Message-ID"])

    # ───────── Empfang (Belegpostfach) ─────────
    def _imap_login(self) -> imaplib.IMAP4_SSL:
        if not self._imap[0]:
            raise MailNotConfiguredError("IMAP_HOST ist nicht gesetzt")
        conn = imaplib.IMAP4_SSL(*self._imap, ssl_context=self._ssl, timeout=self._timeout)
        if self._auth_mode in ("service_account", "oauth_client"):
            auth = xoauth2_string(self.imap_mailbox, self._require_tokens().access_token()).encode()
            conn.authenticate("XOAUTH2", lambda _: auth)
        else:
            conn.login(self._imap_username, self._app_password)
        return conn

    def _fetch_generic(self, conn: imaplib.IMAP4_SSL, limit: int) -> list[IncomingMail]:
        """Standard-IMAP: Mails der letzten 60 Tage im Eingangsordner, nur mit Anhang (vorab per BODYSTRUCTURE geprüft)."""
        import datetime as dt

        status, _ = conn.select(f'"{self._imap_folder}"')
        if status != "OK":
            raise MailError(f"IMAP-Ordner {self._imap_folder!r} nicht gefunden")
        since = (dt.datetime.now(dt.UTC).date() - dt.timedelta(days=60)).strftime("%d-%b-%Y")
        status, data = conn.uid("SEARCH", None, "SINCE", since)  # type: ignore[arg-type]
        if status != "OK":
            raise MailError("IMAP-Suche fehlgeschlagen")
        uids = data[0].split() if data and data[0] else []
        result: list[IncomingMail] = []
        for uid in uids:
            if len(result) >= limit:
                break
            status, struct = conn.uid("FETCH", uid, "(BODYSTRUCTURE)")
            info = b" ".join(x if isinstance(x, bytes) else b" ".join(x) for x in struct if x) if status == "OK" and struct else b""
            if b'"attachment"' not in info.lower() and b'"application"' not in info.lower() and b'"image"' not in info.lower():
                continue
            status, msg_data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            first = msg_data[0] if msg_data else None
            if status == "OK" and isinstance(first, tuple):
                mail = parse_incoming(uid.decode(), first[1])
                if mail.attachments:
                    result.append(mail)
        return result

    def _fetch_sync(self, limit: int) -> list[IncomingMail]:
        conn = self._imap_login()
        try:
            if not self.uses_gmail_labels:
                return self._fetch_generic(conn, limit)
            conn.select("INBOX")
            # Gmail-Suche: noch nicht verarbeitet, mit Anhang
            status, data = conn.uid("SEARCH", "X-GM-RAW", f'"-label:{PROCESSED_LABEL} has:attachment"')
            if status != "OK":
                raise MailError("IMAP-Suche fehlgeschlagen")
            uids = data[0].split()[:limit] if data and data[0] else []
            result: list[IncomingMail] = []
            for uid in uids:
                status, msg_data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                first = msg_data[0] if msg_data else None
                if status == "OK" and isinstance(first, tuple):
                    result.append(parse_incoming(uid.decode(), first[1]))
            return result
        finally:
            with contextlib.suppress(imaplib.IMAP4.error, OSError):
                conn.logout()

    async def fetch_unprocessed(self, limit: int = 50) -> list[IncomingMail]:
        if self.is_local:
            return []  # Testmodus: kein Postfach zum Abrufen
        try:
            return await asyncio.to_thread(self._fetch_sync, limit)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailError(f"IMAP-Abruf fehlgeschlagen: {type(exc).__name__}") from exc

    def _mark_sync(self, uid: str) -> None:
        if not uid.isdigit():
            raise MailError("ungültige UID")
        conn = self._imap_login()
        try:
            if not self.uses_gmail_labels:
                # Standard-IMAP: in den Ordner „openemployed-verarbeitet“ verschieben (wird nicht gelöscht)
                conn.select(f'"{self._imap_folder}"')
                folder = f'"{self._processed_folder}"'
                with contextlib.suppress(imaplib.IMAP4.error):
                    conn.create(folder)
                conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
                caps = {c.upper() for c in conn.capabilities}
                if "MOVE" in caps:
                    status, _ = conn.uid("MOVE", uid, folder)
                else:
                    status, _ = conn.uid("COPY", uid, folder)
                    if status == "OK":
                        conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                        if "UIDPLUS" in caps:
                            conn.uid("EXPUNGE", uid)
                        else:
                            conn.expunge()
                if status != "OK":
                    raise MailError("Verschieben in den Verarbeitet-Ordner fehlgeschlagen")
                return
            conn.select("INBOX")
            conn.uid("STORE", uid, "+X-GM-LABELS", f'("{PROCESSED_LABEL}")')
            conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        finally:
            with contextlib.suppress(imaplib.IMAP4.error, OSError):
                conn.logout()

    async def mark_processed(self, uid: str) -> None:
        """Gmail: Label „openemployed/verarbeitet“; sonst Verschieben in den Ordner „openemployed-verarbeitet“ (nie löschen)."""
        if self.is_local:
            return
        try:
            await asyncio.to_thread(self._mark_sync, uid)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailError(f"IMAP-Markierung fehlgeschlagen: {type(exc).__name__}") from exc
