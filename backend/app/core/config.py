from __future__ import annotations

from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Konfiguration ausschließlich über Umgebungsvariablen (siehe docker-compose.yml / .env)."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    app_env: Literal["development", "test", "production"] = "development"
    app_origin: str = Field(description="z. B. https://app.beispiel.de – ohne abschließenden Schrägstrich")
    # Kundenportal (eigene Subdomain empfohlen, z. B. https://portal.beispiel.de); leer = gleiche Herkunft wie die App
    portal_origin: str = ""
    database_url: SecretStr
    migrator_database_url: SecretStr | None = None
    ledger_url: str = "http://ledger:8080"
    ledger_internal_token: SecretStr = Field(min_length=32)
    tenant_id: UUID
    ledger_timeout_seconds: float = 5.0

    # ── Anmeldung mit Google Workspace (keine Rollen) ──
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    auth_allowed_domain: str = ""
    auth_allowed_emails: str = ""
    session_secret: SecretStr = Field(min_length=32)
    session_max_age_hours: int = Field(default=168, ge=1, le=24 * 30)
    session_idle_hours: int = Field(default=12, ge=1, le=24 * 30)
    # Nur für lokale Tests ohne Google: Anmeldung per E-Mail ohne Passwort. In Produktion verboten.
    dev_login_enabled: bool = False
    # Weitere Anmeldeverfahren (auch in der App unter Einstellungen → System pflegbar)
    auth_password_enabled: bool = False      # E-Mail + Passwort (+ TOTP)
    auth_require_2fa: bool = False           # Passwort-Anmeldung nur mit Zwei-Faktor-Code
    auth_default_role: Literal["admin", "accounting", "advisor", "employee", "none"] = "admin"  # neue SSO-Benutzer; none = nur Einladung
    auth_microsoft_tenant: str = ""          # Verzeichnis-ID (oder „organizations“)
    auth_microsoft_client_id: str = ""
    auth_microsoft_client_secret: SecretStr = SecretStr("")
    auth_oidc_discovery_url: str = ""        # https://…/.well-known/openid-configuration
    auth_oidc_client_id: str = ""
    auth_oidc_client_secret: SecretStr = SecretStr("")
    auth_oidc_label: str = "Single Sign-on"

    # ── Steuern (⚠️ mit Steuerberater/Finanzamt abstimmen) ──
    vat_filing: Literal["monthly", "quarterly"] = "quarterly"
    vat_extension: bool = False          # Dauerfristverlängerung
    trade_tax_multiplier: int = Field(default=440, ge=200, le=900)  # Hebesatz Düsseldorf
    company_state: Literal["BW", "BY", "BE", "BB", "HB", "HH", "HE", "MV", "NI", "NW", "RP", "SL", "SN", "ST", "SH", "TH"] = "NW"
    # ELSTER (Umsatzsteuer-Voranmeldung): ERiC der Finanzverwaltung + Hersteller-ID + Organisationszertifikat
    eric_home: str = ""
    elster_hersteller_id: str = ""
    elster_cert_path: str = "/run/secrets/openemployed/elster.pfx"
    elster_test_mode: bool = True

    # ── E-Mail (Google Workspace, Microsoft 365 oder jeder IMAP/SMTP-Anbieter, Voreinstellungen in app/services/mail.py) ──
    mail_provider: str = "google"
    # service_account (Google), oauth_client (Microsoft 365), app_password (Benutzer/Passwort, alle Anbieter),
    # local = Testmodus: unverschlüsseltes SMTP an einen lokalen Mail-Fänger (Mailpit), kein Postfachabruf – in Produktion verboten
    mail_auth_mode: Literal["service_account", "oauth_client", "app_password", "local"] = "service_account"
    mail_mailbox: str = ""
    mail_from_name: str = ""
    mail_username: str = ""              # leer = MAIL_MAILBOX (manche Anbieter melden mit abweichendem Benutzernamen an)
    mail_app_password: SecretStr = SecretStr("")
    mail_service_account_file: str = "/run/secrets/openemployed/google-mail-service-account.json"
    mail_imap_mailbox: str = ""
    mail_imap_folder: str = "INBOX"
    mail_processed_folder: str = "openemployed-verarbeitet"
    ms_tenant_id: str = ""
    ms_client_id: str = ""
    ms_client_secret: SecretStr = SecretStr("")
    smtp_host: str = ""                  # leer = Voreinstellung des Anbieters
    smtp_port: int = 0
    smtp_security: Literal["auto", "starttls", "ssl"] = "auto"
    imap_host: str = ""
    imap_port: int = 0

    # ── E-Rechnung über Peppol: HTTP-API eines Access Points (z. B. Dienstleister mit Peppol-Zertifizierung) ──
    peppol_ap_url: str = ""
    peppol_ap_token: SecretStr = SecretStr("")
    peppol_sender_id: str = ""                # eigene Peppol-ID, z. B. 9930:DE123456789

    # ── Workflows: Python-Schritte laufen im isolierten Sandbox-Container ──
    sandbox_url: str = ""
    sandbox_token: SecretStr = SecretStr("")

    # ── Digitale Signatur (Documenso, selbst gehostet) ──
    documenso_url: str = "http://documenso:3000"
    documenso_api_token: SecretStr = SecretStr("")
    documenso_webhook_secret: SecretStr = SecretStr("")
    # Automatische Einrichtung beim Start: Zugriff auf die Documenso-Datenbank (Konto, Team, API-Token, Webhook)
    documenso_database_url: SecretStr = SecretStr("")
    documenso_admin_email: str = ""            # leer = erste Person mit Rolle Geschäftsführung
    documenso_public_url: str = ""             # für Links (z. B. http://sign.localhost)
    documenso_webhook_url: str = "http://backend:8000/api/webhooks/documenso"
    # Qualifizierte elektronische Signatur über einen externen Anbieter (optional; "none" = Papier/E-Mail)
    qes_provider: str = "none"
    qes_base_url: str = ""
    qes_api_user: str = ""
    qes_api_key: SecretStr = SecretStr("")

    # ── KI-Belegerkennung (Anthropic Claude API) – Belege werden nur auf ausdrückliche Anforderung übermittelt ──
    # Anbieter: "ollama" = lokale KI auf eigener Hardware (empfohlen), "anthropic" = Claude-API, "none" = aus, "auto" = was konfiguriert ist
    ai_provider: Literal["auto", "ollama", "anthropic", "none"] = "auto"
    ollama_url: str = ""
    ollama_model: str = "qwen2.5vl:7b"
    anthropic_api_key: SecretStr = SecretStr("")
    extraction_model: str = "claude-opus-5-5"


    # FinTS/HBCI: registrierte Produkt-ID der Deutschen Kreditwirtschaft (Pflicht für den Produktivbetrieb)
    fints_product_id: str = ""

    @property
    def ai_configured(self) -> bool:
        return bool(self.anthropic_api_key.get_secret_value())


    @property
    def documenso_configured(self) -> bool:
        return bool(self.documenso_api_token.get_secret_value())

    @field_validator("app_origin", "portal_origin")
    @classmethod
    def _origin(cls, v: str) -> str:
        v = v.strip()
        if v == "":
            return v
        if not (v.startswith("https://") or v.startswith("http://")) or v.endswith("/") or v.count("/") != 2:
            raise ValueError("APP_ORIGIN muss z. B. https://app.beispiel.de lauten (ohne Pfad und Schrägstrich)")
        return v

    @field_validator("auth_allowed_domain", "mail_mailbox", "mail_imap_mailbox")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()

    @model_validator(mode="after")
    def _production_rules(self) -> Settings:
        if self.app_env == "production":
            if self.dev_login_enabled:
                raise ValueError("DEV_LOGIN_ENABLED ist in Produktion verboten")
            if self.mail_auth_mode == "local":
                raise ValueError("MAIL_AUTH_MODE=local (Test-Mailfänger) ist in Produktion verboten")
        from app.services.mail import PROVIDERS

        if self.mail_provider not in PROVIDERS:
            raise ValueError(f"MAIL_PROVIDER unbekannt – erlaubt: {', '.join(PROVIDERS)}")
        if self.mail_auth_mode == "service_account" and self.mail_provider != "google":
            raise ValueError("MAIL_AUTH_MODE=service_account gibt es nur bei MAIL_PROVIDER=google")
        if self.mail_auth_mode == "oauth_client" and self.mail_provider != "microsoft":
            raise ValueError("MAIL_AUTH_MODE=oauth_client gibt es nur bei MAIL_PROVIDER=microsoft")
        if self.app_env == "production":
            if not self.app_origin.startswith("https://"):
                raise ValueError("In Produktion muss APP_ORIGIN mit https:// beginnen")
            if self.google_login_configured and not self.auth_allowed_domain:
                raise ValueError("In Produktion muss AUTH_ALLOWED_DOMAIN gesetzt sein")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def google_login_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret.get_secret_value() and self.auth_allowed_domain)

    @property
    def allowed_emails(self) -> frozenset[str]:
        return frozenset(e.strip().lower() for e in self.auth_allowed_emails.split(",") if e.strip())

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.app_origin}/api/auth/google/callback"

    @property
    def session_cookie_name(self) -> str:
        # __Host- verlangt Secure; lokal über http://localhost daher ohne Präfix
        return "__Host-openemployed_session" if self.is_production else "openemployed_session"

    @property
    def portal_base(self) -> str:
        return self.portal_origin or self.app_origin

    @property
    def portal_cookie_name(self) -> str:
        return "__Host-portal_session" if self.portal_base.startswith("https://") else "openemployed_portal"

    @property
    def portal_dev_codes(self) -> bool:
        """Lokal ohne Postfach: Login-Code in der Antwort zurückgeben (nie in Produktion)."""
        return self.dev_login_enabled and not self.is_production

    @property
    def oauth_cookie_name(self) -> str:
        return "__Host-openemployed_oauth" if self.is_production else "openemployed_oauth"

    @property
    def cookie_secure(self) -> bool:
        return self.app_origin.startswith("https://")

    @property
    def microsoft_login_configured(self) -> bool:
        return bool(self.auth_microsoft_tenant and self.auth_microsoft_client_id and self.auth_microsoft_client_secret.get_secret_value())

    @property
    def oidc_login_configured(self) -> bool:
        return bool(self.auth_oidc_discovery_url.startswith("https://") and self.auth_oidc_client_id
                    and self.auth_oidc_client_secret.get_secret_value())

    def oidc_redirect_uri(self, provider: str) -> str:
        return f"{self.app_origin}/api/auth/{provider}/callback"

    @property
    def mail_servers(self) -> tuple[str, int, str, str, int]:
        """(SMTP-Host, -Port, Verschlüsselung, IMAP-Host, -Port) – eigene Angaben vor der Anbieter-Voreinstellung."""
        from app.services.mail import PROVIDERS

        p = PROVIDERS.get(self.mail_provider, PROVIDERS["smtp"])
        smtp_host = self.smtp_host or p.smtp_host
        smtp_port = self.smtp_port or (p.smtp_port if not self.smtp_host else 587)
        security = self.smtp_security if self.smtp_security != "auto" else ("ssl" if smtp_port == 465 else "starttls")
        return smtp_host, smtp_port, security, self.imap_host or p.imap_host, self.imap_port or p.imap_port

    @property
    def mail_configured(self) -> bool:
        if not self.mail_mailbox:
            return False
        if self.mail_auth_mode == "app_password":
            return bool(self.mail_app_password.get_secret_value())
        if self.mail_auth_mode == "local":
            return True
        if self.mail_auth_mode == "oauth_client":
            return bool(self.ms_tenant_id and self.ms_client_id and self.ms_client_secret.get_secret_value())
        return bool(self.mail_service_account_file)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # Werte kommen aus der Umgebung
