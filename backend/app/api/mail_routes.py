from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.auth.deps import CurrentUser
from app.services.mail import PROVIDERS, MailError, MailNotConfiguredError, OutgoingMail, WorkspaceMailer

log = logging.getLogger("openemployed.mail")
router = APIRouter(prefix="/api/mail", tags=["e-mail"])


def _mailer(request: Request) -> WorkspaceMailer:
    mailer: WorkspaceMailer | None = request.app.state.mailer
    if mailer is None:
        raise HTTPException(status_code=503, detail="E-Mail ist nicht eingerichtet (MAIL_PROVIDER, MAIL_MAILBOX …)")
    return mailer


@router.get("/status")
async def mail_status(request: Request, _user: CurrentUser) -> dict[str, Any]:
    mailer: WorkspaceMailer | None = request.app.state.mailer
    describe = getattr(mailer, "describe", None)
    return {"configured": mailer is not None, "mailbox": mailer.mailbox if mailer else None,
            **(describe() if callable(describe) else {})}


@router.get("/providers")
async def providers(_user: CurrentUser) -> list[dict[str, Any]]:
    """Voreinstellungen der unterstützten Anbieter (Server, Ports, Anmeldeverfahren)."""
    return [{"key": k, "label": p.label, "smtp_host": p.smtp_host, "smtp_port": p.smtp_port, "security": p.security,
             "imap_host": p.imap_host, "imap_port": p.imap_port, "auth": list(p.auth), "hint": p.hint} for k, p in PROVIDERS.items()]


@router.post("/test-inbox")
async def test_inbox(request: Request, _user: CurrentUser) -> dict[str, Any]:
    """Prüft die IMAP-Anmeldung und zählt die abholbaren Mails mit Anhang (ohne etwas zu verändern)."""
    mailer = _mailer(request)
    try:
        mails = await mailer.fetch_unprocessed(limit=20)
    except MailNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except MailError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "pending": len(mails)}


@router.post("/test")
async def send_test_mail(request: Request, user: CurrentUser) -> dict[str, str]:
    """Sendet eine Testnachricht an den angemeldeten Benutzer."""
    mailer = _mailer(request)
    try:
        message_id = await mailer.send(
            OutgoingMail(
                to=[user.email],
                subject="OpenEmployed: Testnachricht",
                text=(
                    "Diese Nachricht bestätigt, dass OpenEmployed Mails versenden kann.\n\n"
                    f"Absender: {mailer.mailbox}\n"
                ),
            )
        )
    except MailNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except MailError as exc:
        log.warning("Testmail fehlgeschlagen: %s", exc)
        raise HTTPException(status_code=502, detail=f"Versand fehlgeschlagen: {exc}") from exc
    log.info("Testmail an %s gesendet (%s)", user.email, message_id)
    return {"status": "sent", "message_id": message_id, "to": user.email}
