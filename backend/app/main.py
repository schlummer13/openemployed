from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.accounting_routes import router as accounting_router
from app.api.admin_routes import router as admin_router
from app.api.archive_routes import router as archive_router
from app.api.asset_routes import router as asset_router
from app.api.attendance_routes import router as attendance_router
from app.api.auth_routes import router as auth_router
from app.api.automation_routes import router as automation_router
from app.api.bank_routes import router as bank_router
from app.api.budget_routes import router as budget_router
from app.api.company_routes import router as company_router
from app.api.contract_routes import router as contract_router
from app.api.core_routes import router as core_router
from app.api.crm_routes import router as crm_router
from app.api.currency_routes import router as currency_router
from app.api.datev_routes import router as datev_router
from app.api.elster_routes import router as elster_router
from app.api.expense_routes import router as expense_router
from app.api.help_routes import router as help_router
from app.api.holiday_routes import router as holiday_router
from app.api.import_routes import router as import_router
from app.api.mail_routes import router as mail_router
from app.api.offer_routes import router as offer_router
from app.api.payroll_extra_routes import router as payroll_extra_router
from app.api.payroll_routes import router as payroll_router
from app.api.personnel_routes import router as personnel_router
from app.api.portal_routes import router as portal_router
from app.api.receivable_routes import router as receivable_router
from app.api.report_routes import router as report_router
from app.api.routes import router
from app.api.sepa_routes import router as sepa_router
from app.api.signature_routes import router as signature_router
from app.api.tax_routes import router as tax_router
from app.api.team_routes import router as team_router
from app.api.time_travel_routes import router as time_travel_router
from app.api.workflow_routes import router as workflow_router
from app.api.year_end_routes import router as year_end_router
from app.auth.deps import OriginCheckMiddleware
from app.auth.google import GoogleOAuthClient
from app.auth.store import PostgresAuthStore
from app.core.config import Settings, get_settings
from app.core.read_cache import InvalidateOnWriteMiddleware
from app.core.timing import ServerTimingMiddleware
from app.db.session import get_engine
from app.services.ledger_client import LedgerClient
from app.services.mail import MailError, MicrosoftTokenProvider, ServiceAccountTokenProvider, TokenProvider, WorkspaceMailer

log = logging.getLogger("openemployed")


def build_mailer(settings: Settings) -> WorkspaceMailer | None:
    if not settings.mail_configured:
        log.info("E-Mail nicht eingerichtet (MAIL_MAILBOX leer)")
        return None
    try:
        tokens: TokenProvider | None = None
        if settings.mail_auth_mode == "service_account":
            tokens = ServiceAccountTokenProvider(settings.mail_service_account_file, settings.mail_mailbox)
        elif settings.mail_auth_mode == "oauth_client":
            tokens = MicrosoftTokenProvider(settings.ms_tenant_id, settings.ms_client_id, settings.ms_client_secret.get_secret_value())
        smtp_host, smtp_port, security, imap_host, imap_port = settings.mail_servers
        return WorkspaceMailer(
            mailbox=settings.mail_mailbox,
            from_name=settings.mail_from_name,
            auth_mode=settings.mail_auth_mode,
            token_provider=tokens,
            app_password=settings.mail_app_password.get_secret_value(),
            imap_mailbox=settings.mail_imap_mailbox,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            imap_host=imap_host,
            imap_port=imap_port,
            provider=settings.mail_provider,
            username=settings.mail_username,
            smtp_security="ssl" if security == "ssl" else "starttls",
            imap_folder=settings.mail_imap_folder,
            processed_folder=settings.mail_processed_folder,
        )
    except MailError as exc:
        log.warning("E-Mail nicht verfügbar: %s", exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if not hasattr(app.state, "ledger"):
        app.state.ledger = LedgerClient(
            settings.ledger_url,
            settings.ledger_internal_token.get_secret_value(),
            timeout=settings.ledger_timeout_seconds,
        )
    # Tests können diese Objekte vorab setzen (z. B. Speicher ohne Datenbank, Google-Attrappe)
    if not hasattr(app.state, "engine"):
        app.state.engine = get_engine()
    if not hasattr(app.state, "auth_store"):
        app.state.auth_store = PostgresAuthStore(
            get_engine(),
            tenant_id=settings.tenant_id,
            max_age=dt.timedelta(hours=settings.session_max_age_hours),
            idle=dt.timedelta(hours=settings.session_idle_hours),
        )
    if not hasattr(app.state, "google"):
        app.state.google = GoogleOAuthClient(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value(),
            redirect_uri=settings.google_redirect_uri,
        )
    from app.services import crm, webhooks, workflows  # noqa: F401 – registrieren die Ereignis-Empfänger
    from app.services.settings_store import load_into_settings

    if settings.app_env != "test":
        from app.services.companies import ensure_home

        await load_into_settings(app.state.engine, settings.tenant_id)
        await ensure_home(app.state.engine, settings.tenant_id)
    if not hasattr(app.state, "mailer"):
        from app.services.archive import ArchivingMailer

        mailer = build_mailer(settings)
        # versendete Nachrichten landen als .eml im revisionssicheren Archiv
        app.state.mailer = ArchivingMailer(mailer, app.state.engine, settings.tenant_id) if mailer else None
    if not hasattr(app.state, "ai"):
        from app.services.ai import build_provider

        app.state.ai = build_provider(settings)
    if not hasattr(app.state, "extractor"):
        from app.services.extraction import ProviderExtractor

        app.state.extractor = ProviderExtractor(app.state.ai) if app.state.ai is not None else None
    if not hasattr(app.state, "documenso"):
        from app.services.documenso import HttpDocumensoClient

        app.state.documenso = (HttpDocumensoClient(settings.documenso_url, settings.documenso_api_token.get_secret_value())
                               if settings.documenso_configured else None)
    documenso_task = None
    if settings.app_env != "test" and settings.documenso_database_url.get_secret_value():
        from app.services.documenso_setup import connect_in_background

        # Verbindung zu Documenso automatisch herstellen (wartet, bis Documenso gestartet ist)
        documenso_task = asyncio.create_task(connect_in_background(app))
    billing_task = asyncio.create_task(_scheduler(app)) if settings.app_env != "test" else None
    try:
        yield
    finally:
        if billing_task is not None:
            billing_task.cancel()
        if documenso_task is not None:
            documenso_task.cancel()
        if isinstance(app.state.ledger, LedgerClient):
            await app.state.ledger.aclose()
        for holder in ("documenso", "ai"):
            closer = getattr(getattr(app.state, holder, None), "aclose", None)
            if closer is not None:
                await closer()


async def _scheduler(app: FastAPI) -> None:
    """Automatisierungen (Abrechnung, Mahnlauf, Belegpostfach, Bank …) – siehe app/services/automation.py."""
    from app.services.automation import Context, scheduler

    def ctx() -> Context:
        st = app.state
        return Context(engine=st.engine, ledger=st.ledger, mailer=getattr(st, "mailer", None),
                       extractor=getattr(st, "extractor", None), tenant_id=get_settings().tenant_id, actor="system:automatisierung")

    await scheduler(ctx)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="OpenEmployed API",
        version="0.1.0",
        lifespan=lifespan,
        # API-Dokumentation nur außerhalb der Produktion
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/api/openapi.json",
    )
    app.add_middleware(OriginCheckMiddleware, allowed_origin=settings.app_origin, extra_origins=(settings.portal_origin,))
    app.add_middleware(InvalidateOnWriteMiddleware)  # Zwischenspeicher nach jeder Änderung ungültig
    app.add_middleware(ServerTimingMiddleware)  # äußerste Schicht: misst die gesamte Verarbeitung
    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(mail_router)
    app.include_router(core_router)
    app.include_router(expense_router)
    app.include_router(contract_router)
    app.include_router(team_router)
    app.include_router(tax_router)
    app.include_router(offer_router)
    app.include_router(receivable_router)
    app.include_router(portal_router)
    app.include_router(signature_router)
    app.include_router(datev_router)
    app.include_router(bank_router)
    app.include_router(automation_router)
    app.include_router(accounting_router)
    app.include_router(budget_router)
    app.include_router(asset_router)
    app.include_router(year_end_router)
    app.include_router(time_travel_router)
    app.include_router(elster_router)
    app.include_router(payroll_extra_router)
    app.include_router(payroll_router)
    app.include_router(import_router)
    app.include_router(sepa_router)
    app.include_router(archive_router)
    app.include_router(report_router)
    app.include_router(admin_router)
    app.include_router(workflow_router)
    app.include_router(currency_router)
    app.include_router(crm_router)
    app.include_router(company_router)
    app.include_router(help_router)
    app.include_router(attendance_router)
    app.include_router(holiday_router)
    app.include_router(personnel_router)
    return app


app = create_app()
