"""Einstellungen in der App statt nur in der .env: Werte aus der Datenbank überschreiben die Vorgaben der Umgebung.

Geheimnisse (Passwörter, Client-Secrets) liegen verschlüsselt (Fernet, Schlüssel abgeleitet aus SESSION_SECRET) in der
Datenbank und werden nie an den Browser zurückgegeben. Jede Änderung wird vor dem Speichern gegen die Konfigurationsregeln
geprüft (z. B. Testmodus in Produktion verboten) und im Audit-Log protokolliert.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import SecretStr, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.config import Settings, get_settings
from app.services import audit
from app.services.masterdata import ConflictError

log = logging.getLogger("selbstlite.settings")


@dataclass(frozen=True, slots=True)
class Field:
    key: str
    group: str
    label: str
    kind: str = "text"            # text | secret | bool | int | select
    options: tuple[str, ...] = ()
    hint: str = ""


FIELDS: tuple[Field, ...] = (
    # E-Mail
    Field("mail_provider", "mail", "Anbieter", "select", ("google", "microsoft", "ionos", "strato", "netcup", "hetzner", "allinkl",
                                                          "mailbox_org", "posteo", "gmx", "webde", "telekom", "icloud", "fastmail", "zoho",
                                                          "smtp")),
    Field("mail_auth_mode", "mail", "Anmeldung am Postfach", "select", ("app_password", "oauth_client", "service_account", "local")),
    Field("mail_mailbox", "mail", "Absender-Postfach", hint="z. B. rechnungen@firma.de"),
    Field("mail_from_name", "mail", "Absendername"),
    Field("mail_username", "mail", "Anmeldename", hint="leer = Postfach-Adresse"),
    Field("mail_app_password", "mail", "Passwort / App-Passwort", "secret"),
    Field("ms_tenant_id", "mail", "Microsoft: Verzeichnis-ID"),
    Field("ms_client_id", "mail", "Microsoft: Anwendungs-ID"),
    Field("ms_client_secret", "mail", "Microsoft: geheimer Clientschlüssel", "secret"),
    Field("smtp_host", "mail", "SMTP-Server", hint="leer = Voreinstellung des Anbieters"),
    Field("smtp_port", "mail", "SMTP-Port", "int"),
    Field("smtp_security", "mail", "Verschlüsselung", "select", ("auto", "starttls", "ssl")),
    Field("imap_host", "mail", "IMAP-Server"),
    Field("imap_port", "mail", "IMAP-Port", "int"),
    Field("mail_imap_mailbox", "mail", "Belegpostfach", hint="leer = Absender-Postfach"),
    Field("mail_imap_folder", "mail", "IMAP-Ordner für Belege"),
    # Anmeldung
    Field("auth_allowed_domain", "login", "Erlaubte Domain", hint="nur Konten dieser Domain (z. B. firma.de)"),
    Field("auth_default_role", "login", "Rolle neuer SSO-Benutzer", "select", ("admin", "accounting", "advisor", "employee", "none"),
          hint="none = nur eingeladene Benutzer"),
    Field("auth_password_enabled", "login", "Anmeldung mit E-Mail und Passwort", "bool"),
    Field("auth_require_2fa", "login", "Zwei-Faktor-Code für Passwort-Anmeldung erzwingen", "bool"),
    Field("auth_microsoft_tenant", "login", "Microsoft Entra: Verzeichnis-ID", hint="oder „organizations“"),
    Field("auth_microsoft_client_id", "login", "Microsoft Entra: Anwendungs-ID"),
    Field("auth_microsoft_client_secret", "login", "Microsoft Entra: Clientschlüssel", "secret"),
    Field("auth_oidc_label", "login", "OIDC: Beschriftung", hint="z. B. „Mit Keycloak anmelden“"),
    Field("auth_oidc_discovery_url", "login", "OIDC: Discovery-URL", hint="https://…/.well-known/openid-configuration"),
    Field("auth_oidc_client_id", "login", "OIDC: Client-ID"),
    Field("auth_oidc_client_secret", "login", "OIDC: Client-Secret", "secret"),
    # Steuern und Buchhaltung
    Field("company_state", "tax", "Bundesland (Finanzamt)", "select", ("BW", "BY", "BE", "BB", "HB", "HH", "HE", "MV", "NI", "NW", "RP",
                                                                        "SL", "SN", "ST", "SH", "TH")),
    Field("vat_filing", "tax", "Umsatzsteuer-Voranmeldung", "select", ("monthly", "quarterly")),
    Field("trade_tax_multiplier", "tax", "Gewerbesteuer-Hebesatz (%)", "int"),
    Field("elster_test_mode", "tax", "ELSTER-Testmodus", "bool"),
    Field("elster_hersteller_id", "tax", "ELSTER-Hersteller-ID"),
    # E-Rechnung
    Field("peppol_ap_url", "einvoice", "Peppol-Access-Point: API-URL", hint="vom Dienstleister, https://…"),
    Field("peppol_ap_token", "einvoice", "Peppol-Access-Point: Zugangsschlüssel", "secret"),
    Field("peppol_sender_id", "einvoice", "Eigene Peppol-ID", hint="z. B. 9930:DE123456789 (USt-IdNr.)"),
    # Qualifizierte Signatur
    Field("qes_provider", "signature", "Anbieter für qualifizierte Signatur (QES)", "select", ("none", "skribble"),
          hint="none = Schriftform-Verträge ausdrucken oder per E-Mail senden"),
    Field("qes_api_user", "signature", "API-Benutzer"),
    Field("qes_api_key", "signature", "API-Schlüssel", "secret"),
    Field("qes_base_url", "signature", "API-Adresse", hint="leer = Standard des Anbieters (Skribble: https://api.skribble.com/v2)"),
    # KI
    Field("ai_provider", "ai", "KI-Anbieter", "select", ("auto", "ollama", "anthropic", "none")),
    Field("ollama_model", "ai", "Lokales Modell", hint="z. B. qwen2.5vl:7b"),
)
BY_KEY = {f.key: f for f in FIELDS}
_ENV_DEFAULTS: dict[str, Any] = {}


def _fernet() -> Any:
    from cryptography.fernet import Fernet

    key = hashlib.sha256(b"selbstlite-settings:" + get_settings().session_secret.get_secret_value().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _apply(overrides: dict[str, Any]) -> None:
    """Überträgt Werte auf das (zwischengespeicherte) Settings-Objekt; Umgebungswerte bleiben als Rückfall erhalten."""
    s = get_settings()
    for key in BY_KEY:
        if key not in _ENV_DEFAULTS:
            _ENV_DEFAULTS[key] = getattr(s, key)
    for key, field in BY_KEY.items():
        value = overrides.get(key, _ENV_DEFAULTS[key])
        if field.kind == "secret" and not isinstance(value, SecretStr):
            value = SecretStr(str(value or ""))
        object.__setattr__(s, key, value)


async def _load(conn: AsyncConnection, tenant_id: UUID) -> dict[str, Any]:
    rows = await conn.execute(text("SELECT key, value, secret FROM app_settings WHERE tenant_id = :t"), {"t": tenant_id})
    out: dict[str, Any] = {}
    f = None
    for r in rows:
        if r.key not in BY_KEY:
            continue
        if r.secret is not None:
            f = f or _fernet()
            try:
                out[r.key] = SecretStr(f.decrypt(r.secret.encode()).decode())
            except Exception:  # Schlüssel geändert (SESSION_SECRET neu) – Geheimnis neu eingeben
                log.warning("Einstellung %s konnte nicht entschlüsselt werden", r.key)
        else:
            out[r.key] = r.value
    return out


async def load_into_settings(engine: AsyncEngine, tenant_id: UUID) -> None:
    try:
        async with engine.connect() as conn:
            _apply(await _load(conn, tenant_id))
    except Exception:  # Tabelle fehlt (Migration läuft noch) – Umgebungswerte gelten
        log.info("App-Einstellungen nicht geladen – es gelten die Werte der Umgebung")


def _public(value: Any, field: Field) -> Any:
    if field.kind == "secret":
        return bool(value.get_secret_value() if isinstance(value, SecretStr) else value)
    return value


async def overview(conn: AsyncConnection, tenant_id: UUID) -> dict[str, Any]:
    stored = await _load(conn, tenant_id)
    s = get_settings()
    return {"fields": [{"key": f.key, "group": f.group, "label": f.label, "kind": f.kind, "options": list(f.options), "hint": f.hint,
                        "value": _public(getattr(s, f.key), f), "source": "app" if f.key in stored else "env"} for f in FIELDS]}


def _coerce(field: Field, value: Any) -> Any:
    if value is None:
        return None
    if field.kind == "bool":
        return bool(value)
    if field.kind == "int":
        return int(value) if str(value).strip() else 0
    if field.kind == "select" and str(value) not in field.options:
        raise ConflictError(f"{field.label}: ungültiger Wert")
    return str(value).strip()


async def save(conn: AsyncConnection, tenant_id: UUID, changes: dict[str, Any], actor: str) -> dict[str, Any]:
    """``changes``: Schlüssel → neuer Wert; ``None`` = auf den Wert der Umgebung zurücksetzen. Leere Geheimnisse bleiben unverändert."""
    unknown = set(changes) - set(BY_KEY)
    if unknown:
        raise ConflictError(f"Unbekannte Einstellung: {', '.join(sorted(unknown))}")
    stored = await _load(conn, tenant_id)
    merged = dict(stored)
    for key, raw in changes.items():
        field = BY_KEY[key]
        if raw is None:
            merged.pop(key, None)
        elif field.kind == "secret":
            if str(raw):
                merged[key] = SecretStr(str(raw))
        else:
            merged[key] = _coerce(field, raw)
    # Gesamtkonfiguration prüfen, bevor etwas gespeichert wird
    s = get_settings()
    candidate = {k: getattr(s, k) for k in Settings.model_fields}
    candidate.update({k: _ENV_DEFAULTS.get(k, candidate[k]) for k in BY_KEY})
    candidate.update(merged)
    try:
        Settings(**candidate, _env_file=None)
    except ValidationError as exc:
        raise ConflictError("; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors()[:3])) from exc
    f = _fernet()
    for key in changes:
        field = BY_KEY[key]
        if key not in merged:
            await conn.execute(text("DELETE FROM app_settings WHERE tenant_id = :t AND key = :k"), {"t": tenant_id, "k": key})
            continue
        value = merged[key]
        secret = f.encrypt(value.get_secret_value().encode()).decode() if field.kind == "secret" else None
        await conn.execute(text(
            "INSERT INTO app_settings (tenant_id, key, value, secret, updated_by) VALUES (:t, :k, CAST(:v AS jsonb), :s, :a) "
            "ON CONFLICT (tenant_id, key) DO UPDATE SET value = EXCLUDED.value, secret = EXCLUDED.secret, updated_by = :a, updated_at = "
            "now()"),
            {"t": tenant_id, "k": key, "v": None if secret else json.dumps(value), "s": secret, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="settings.updated", entity_type="settings", entity_id=tenant_id,
                       after={k: ("***" if BY_KEY[k].kind == "secret" else merged.get(k)) for k in changes})
    _apply(merged)
    return await overview(conn, tenant_id)
