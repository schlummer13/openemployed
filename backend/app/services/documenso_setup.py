# ruff: noqa: E501 – SQL-Anweisungen gegen das Documenso-Schema
"""Documenso-Anbindung ohne Handarbeit: Einrichtung beim Start, Abgleich der Dokumente, Vorlagen.

Einrichtung (idempotent, bei jedem Start):
1. Konto der Geschäftsführung in Documenso – angelegt über Documensos eigene Registrierung (damit Organisation und Team korrekt
   entstehen), mit zufälligem Kennwort, das niemand kennt; die Person setzt ihr Passwort selbst über einen Link aus OpenEmployed.
2. API-Token für das Team (in Documenso nur als SHA-512-Hash gespeichert, bei uns verschlüsselt).
3. Webhook des Teams an OpenEmployed (alle Dokument- und Vorlagen-Ereignisse, gemeinsames Geheimnis).

Abgleich: Dokumente, die im Team direkt in Documenso angelegt oder bearbeitet werden, erscheinen unter „Sonstige Verträge“
(Status, Empfänger); abgeschlossene werden heruntergeladen und revisionssicher archiviert. Vorlagen aus Documenso können aus
OpenEmployed heraus verwendet werden (z. B. Arbeitsvertrag an einen Mitarbeiter).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import secrets
import string
import uuid
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.core.config import get_settings
from app.services import audit
from app.services.masterdata import ConflictError

log = logging.getLogger("selbstlite.documenso")
TOKEN_KEY = "documenso_api_token"  # noqa: S105 – Schlüsselname, kein Geheimnis
EVENTS = ("DOCUMENT_CREATED", "DOCUMENT_SENT", "DOCUMENT_OPENED", "DOCUMENT_SIGNED", "DOCUMENT_RECIPIENT_COMPLETED", "DOCUMENT_COMPLETED",
          "DOCUMENT_REJECTED", "DOCUMENT_CANCELLED", "TEMPLATE_CREATED", "TEMPLATE_UPDATED", "TEMPLATE_DELETED", "TEMPLATE_USED")
STATUS_MAP = {"DRAFT": "draft", "PENDING": "pending", "COMPLETED": "signed", "REJECTED": "rejected"}


class SetupError(RuntimeError):
    pass


class FormAckRequired(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _engine() -> AsyncEngine:
    url = get_settings().documenso_database_url.get_secret_value()
    if not url:
        raise SetupError("DOCUMENSO_DATABASE_URL fehlt")
    url = url.replace("postgres://", "postgresql+asyncpg://", 1).replace("postgresql://", "postgresql+asyncpg://", 1)
    return create_async_engine(url, pool_size=1, max_overflow=1)


def _random_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "Oe!" + "".join(secrets.choice(alphabet) for _ in range(40)) + "9a"


def _alphaid(n: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


async def _stored_token(conn: AsyncConnection) -> str | None:
    from app.services.archive_sync import decrypt

    row = (await conn.execute(text("SELECT value_enc FROM integration_secrets WHERE key = :k"), {"k": TOKEN_KEY})).one_or_none()
    if row is None:
        return None
    return decrypt(row[0]).get("token")


async def _store_token(conn: AsyncConnection, token: str, meta: dict[str, Any]) -> None:
    from app.services.archive_sync import encrypt

    await conn.execute(text(
        "INSERT INTO integration_secrets (key, value_enc, meta) VALUES (:k, :v, CAST(:m AS jsonb)) ON CONFLICT (key) DO UPDATE SET "
        "value_enc = EXCLUDED.value_enc, meta = EXCLUDED.meta, updated_at = now()"),
        {"k": TOKEN_KEY, "v": encrypt({"token": token}), "m": json.dumps(meta)})


async def _token_works(token: str) -> bool:
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{s.documenso_url.rstrip('/')}/api/v2/document", params={"page": 1, "perPage": 1},
                            headers={"Authorization": token})
        return r.status_code == 200
    except httpx.HTTPError:
        return False


async def _admin_identity(app_engine: AsyncEngine, tenant_id: UUID) -> tuple[str, str]:
    s = get_settings()
    async with app_engine.connect() as conn:
        row = (await conn.execute(text("SELECT email, name FROM users WHERE tenant_id = :t AND active AND role = 'admin' "
                                       "ORDER BY created_at LIMIT 1"), {"t": tenant_id})).one_or_none()
        company = await conn.scalar(text("SELECT legal_name FROM company_profiles WHERE tenant_id = :t ORDER BY valid_from DESC LIMIT 1"),
                                    {"t": tenant_id})
    email = s.documenso_admin_email or (row[0] if row else "") or s.mail_mailbox
    if not email:
        raise SetupError("Keine E-Mail-Adresse für das Documenso-Konto (DOCUMENSO_ADMIN_EMAIL oder Benutzer mit Rolle Geschäftsführung)")
    name = (row[1] if row and row[1] else None) or company or "OpenEmployed"
    return email.lower(), str(name)[:100]


async def _wait_for_documenso(timeout_s: int = 5) -> bool:
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.get(f"{s.documenso_url.rstrip('/')}/api/health")
            if r.status_code < 500:
                return True
            r = await c.get(f"{s.documenso_url.rstrip('/')}/signin")
            return r.status_code < 500
    except httpx.HTTPError:
        return False


async def connect(app: Any) -> dict[str, Any]:
    """Stellt die Verbindung her (oder bestätigt sie) und setzt ``app.state.documenso``."""
    from app.services.documenso import HttpDocumensoClient

    s = get_settings()
    if not await _wait_for_documenso():
        raise SetupError("Documenso ist noch nicht erreichbar")
    email, name = await _admin_identity(app.state.engine, s.tenant_id)
    dengine = _engine()
    try:
        async with dengine.begin() as d:
            user = (await d.execute(text('SELECT id FROM "User" WHERE lower(email) = :e'), {"e": email})).one_or_none()
        if user is None:
            async with httpx.AsyncClient(timeout=30) as c:
                # Documenso prüft die Herkunft (CSRF-Schutz) gegen ihre öffentliche Adresse
                origin = s.documenso_public_url.rstrip("/") or s.documenso_url.rstrip("/")
                r = await c.post(f"{s.documenso_url.rstrip('/')}/api/auth/email-password/signup", headers={"Origin": origin},
                                 json={"name": name, "email": email, "password": _random_password()})
            if r.status_code >= 300:
                raise SetupError(f"Documenso-Konto konnte nicht angelegt werden ({r.status_code}): {r.text[:200]}")
            log.info("Documenso-Konto für die Geschäftsführung angelegt")
        async with dengine.begin() as d:
            uid = await d.scalar(text('SELECT id FROM "User" WHERE lower(email) = :e'), {"e": email})
            if uid is None:
                raise SetupError("Documenso-Konto nicht gefunden")
            await d.execute(text('UPDATE "User" SET "emailVerified" = coalesce("emailVerified", now()) WHERE id = :u'), {"u": uid})
            team = (await d.execute(text(
                'SELECT t.id, t.name, t.url FROM "Team" t JOIN "Organisation" o ON o.id = t."organisationId" WHERE o."ownerUserId" = :u '
                "ORDER BY t.id LIMIT 1"), {"u": uid})).mappings().one_or_none()
            if team is None:
                raise SetupError("Kein Team in Documenso gefunden")
            async with app.state.engine.begin() as conn:
                token = await _stored_token(conn)
            if not token or not await _token_works(token):
                token = f"api_{_alphaid(16)}"
                await d.execute(text(
                    'INSERT INTO "ApiToken" (name, token, algorithm, expires, "createdAt", "userId", "teamId") '
                    "VALUES ('OpenEmployed (automatisch)', :h, 'SHA512', NULL, now(), :u, :t)"),
                    {"h": hashlib.sha512(token.encode()).hexdigest(), "u": uid, "t": team["id"]})
                async with app.state.engine.begin() as conn:
                    await _store_token(conn, token, {"team_id": team["id"], "user_id": uid})
                log.info("Documenso-API-Token erzeugt")
            secret = s.documenso_webhook_secret.get_secret_value()
            if secret:
                hook = await d.scalar(text('SELECT id FROM "Webhook" WHERE "teamId" = :t AND "webhookUrl" = :w'),
                                      {"t": team["id"], "w": s.documenso_webhook_url})
                events = list(EVENTS)
                if hook:
                    await d.execute(text('UPDATE "Webhook" SET secret = :s, enabled = true, "eventTriggers" = CAST(CAST(:e AS text[]) AS "WebhookTriggerEvents"[]), '
                                         '"updatedAt" = now() WHERE id = :id'), {"s": secret, "e": events, "id": hook})
                else:
                    await d.execute(text(
                        'INSERT INTO "Webhook" (id, "webhookUrl", "eventTriggers", secret, enabled, "createdAt", "updatedAt", "userId", "teamId") '
                        'VALUES (:id, :w, CAST(CAST(:e AS text[]) AS "WebhookTriggerEvents"[]), :s, true, now(), now(), :u, :t)'),
                        {"id": uuid.uuid4().hex, "w": s.documenso_webhook_url, "e": events, "s": secret, "u": uid, "t": team["id"]})
    finally:
        await dengine.dispose()
    old = getattr(app.state, "documenso", None)
    app.state.documenso = HttpDocumensoClient(s.documenso_url, token)
    if old is not None and hasattr(old, "aclose"):
        await old.aclose()
    public = s.documenso_public_url.rstrip("/")
    status = {"connected": True, "email": email, "team": team["name"], "team_url": f"{public}/t/{team['url']}" if public else None,
              "public_url": public or None, "webhook": bool(secret), "checked_at": dt.datetime.now(dt.UTC).isoformat()}
    app.state.documenso_status = status
    return status


async def connect_in_background(app: Any) -> None:
    """Beim Start: wiederholt versuchen, bis Documenso bereit ist (bis ca. 15 Minuten)."""
    for attempt in range(60):
        try:
            await connect(app)
            log.info("Documenso verbunden")
            return
        except SetupError as exc:
            app.state.documenso_status = {"connected": False, "error": str(exc)}
        except Exception as exc:  # Start darf nie an Documenso scheitern
            log.warning("Documenso-Einrichtung fehlgeschlagen: %s", exc)
            app.state.documenso_status = {"connected": False, "error": str(exc)[:300]}
        await asyncio.sleep(min(60, 5 + attempt * 5))


async def password_link(app: Any) -> dict[str, Any]:
    """Einmal-Link (1 Stunde), mit dem die Geschäftsführung ihr Documenso-Passwort selbst festlegt."""
    s = get_settings()
    status = getattr(app.state, "documenso_status", None) or {}
    if not status.get("connected"):
        raise ConflictError("Documenso ist noch nicht verbunden")
    if not s.documenso_public_url:
        raise ConflictError("DOCUMENSO_PUBLIC_URL fehlt – Link kann nicht erzeugt werden")
    token = secrets.token_hex(18)
    dengine = _engine()
    try:
        async with dengine.begin() as d:
            uid = await d.scalar(text('SELECT id FROM "User" WHERE lower(email) = :e'), {"e": status["email"]})
            await d.execute(text('DELETE FROM "PasswordResetToken" WHERE "userId" = :u'), {"u": uid})
            await d.execute(text('INSERT INTO "PasswordResetToken" (token, "createdAt", expiry, "userId") '
                                 "VALUES (:tk, now(), now() + interval '1 hour', :u)"), {"tk": token, "u": uid})
    finally:
        await dengine.dispose()
    return {"url": f"{s.documenso_public_url.rstrip('/')}/reset-password/{token}", "email": status["email"], "valid_minutes": 60}


# ───────────────────────── Abgleich ─────────────────────────

def _signer(recipients: list[dict[str, Any]]) -> tuple[str, str]:
    signers = [r for r in recipients if r.get("role") in (None, "SIGNER", "APPROVER")] or recipients
    if not signers:
        return "–", "–"
    first = sorted(signers, key=lambda r: r.get("signingOrder") or 0)[0]
    return str(first.get("name") or first.get("email") or "–"), str(first.get("email") or "–")


async def upsert_external(conn: AsyncConnection, client: Any, tenant_id: UUID, doc: dict[str, Any]) -> str:
    """Übernimmt ein in Documenso angelegtes Dokument (ohne eigene externalId) bzw. aktualisiert dessen Status."""
    from app.domain.signature_form import check
    from app.services.sign_documents import mark

    did = int(doc["id"])
    recipients = [{"name": r.get("name"), "email": r.get("email"), "role": r.get("role"), "signingOrder": r.get("signingOrder"),
                   "status": r.get("signingStatus")} for r in doc.get("recipients") or []]
    status = STATUS_MAP.get(str(doc.get("status")), "draft")
    row = (await conn.execute(text("SELECT id, status, signed_content IS NOT NULL AS has FROM sign_documents WHERE tenant_id = :t "
                                   "AND documenso_id = :d"), {"t": tenant_id, "d": did})).mappings().one_or_none()
    name, email = _signer(recipients)
    title = str(doc.get("title") or f"Documenso #{did}")[:200]
    if row is None:
        form = check("general", title)
        sid = await conn.scalar(text(
            "INSERT INTO sign_documents (tenant_id, title, kind, counterparty, signer_name, signer_email, filename, status, form_level, "
            "form_message, source, documenso_id, documenso_status, template_id, recipients, created_by, synced_at) VALUES (:t, :ti, :k, :cp, "
            ":sn, :se, :f, :st, :fl, :fm, 'documenso', :d, :ds, :tpl, CAST(:r AS jsonb), :cb, now()) RETURNING id"),
            {"t": tenant_id, "ti": title, "k": form.kind, "cp": name, "sn": name, "se": email, "f": f"{title}.pdf".replace("/", "-")[:200],
             "st": "pending" if status == "signed" else status, "fl": "ok" if form.level == "ok" else "warn", "fm": form.message, "d": did,
             "ds": doc.get("status"), "tpl": doc.get("templateId"), "r": json.dumps(recipients), "cb": "documenso"})
        await audit.record(conn, tenant_id=tenant_id, actor="system:documenso", action="sign_document.imported", entity_type="sign_document",
                           entity_id=sid, after={"documenso_id": did, "title": title})
        has_signed, current = False, "draft"
    else:
        sid, has_signed, current = row["id"], row["has"], row["status"]
        await conn.execute(text("UPDATE sign_documents SET title = :ti, documenso_status = :ds, recipients = CAST(:r AS jsonb), "
                                "signer_name = :sn, signer_email = :se, synced_at = now(), status = CASE WHEN :st IN ('draft', 'pending') "
                                "AND status NOT IN ('signed', 'rejected') THEN :st ELSE status END WHERE id = :id"),
                           {"ti": title, "ds": doc.get("status"), "r": json.dumps(recipients), "sn": name, "se": email, "st": status, "id": sid})
    if status == "signed" and not has_signed:
        pdf = await client.download(did)
        await mark(conn, tenant_id, sid, "signed", pdf)
        return "archived"
    if status == "rejected" and current != "rejected":
        await mark(conn, tenant_id, sid, "rejected")
    return "updated" if row else "imported"


async def sync(conn: AsyncConnection, client: Any, ledger: Any, tenant_id: UUID, *, pages: int = 5) -> dict[str, int]:
    """Gleicht alle Dokumente des Teams ab (fängt verpasste Webhooks ab)."""
    from app.services.documenso import apply_event

    counts = {"imported": 0, "updated": 0, "archived": 0, "own": 0}
    for page in range(1, pages + 1):
        data = await client.list_documents(page=page, per_page=50)
        for doc in data.get("data", []):
            ext = str(doc.get("externalId") or "")
            if ext.startswith("selbstlite:"):
                status = str(doc.get("status"))
                event = {"COMPLETED": "DOCUMENT_COMPLETED", "REJECTED": "DOCUMENT_REJECTED"}.get(status)
                if event:
                    res = await apply_event(conn, client, ledger, tenant_id, documenso_id=int(doc["id"]), event=event)
                    if not res.get("ignored"):
                        counts["own"] += 1
                continue
            full = await client.get_document(int(doc["id"]))
            counts[await upsert_external(conn, client, tenant_id, full)] += 1
        if page >= int(data.get("totalPages") or 1):
            break
    return counts


# ───────────────────────── Vorlagen ─────────────────────────

async def templates(client: Any) -> list[dict[str, Any]]:
    data = await client.list_templates()
    out = []
    for t in data.get("data", []):
        out.append({"id": t["id"], "title": t.get("title"), "updated_at": t.get("updatedAt"),
                    "recipients": [{"id": r["id"], "name": r.get("name"), "email": r.get("email"), "role": r.get("role"),
                                    "signing_order": r.get("signingOrder")} for r in t.get("recipients") or []]})
    return out


async def use_template(conn: AsyncConnection, client: Any, tenant_id: UUID, *, template_id: int, title: str | None,
                       recipients: list[dict[str, Any]], employee_id: UUID | None, acknowledge_form: bool, actor: str) -> dict[str, Any]:
    """Erzeugt aus einer Documenso-Vorlage ein Dokument, versendet es und verfolgt es in OpenEmployed."""
    from app.domain.signature_form import check

    tpl = next((t for t in await templates(client) if t["id"] == template_id), None)
    if tpl is None:
        raise ConflictError("Vorlage nicht gefunden")
    doc_title = (title or tpl["title"] or "Vertrag")[:200]
    kind = "general"
    if employee_id:
        exit_date = await conn.scalar(text("SELECT exit_date FROM employees WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": employee_id})
        kind = "employment_fixed_term" if exit_date else "employment"
    form = check(kind, f"{doc_title} {tpl['title'] or ''}")
    if form.level == "block":
        raise ConflictError(form.message)
    if form.level == "warn" and not acknowledge_form:
        raise FormAckRequired(form.message)
    if not recipients:
        raise ConflictError("Empfänger angeben")
    sid = uuid.uuid4()
    doc = await client.use_template(template_id=template_id, recipients=recipients, external_id=f"oe-doc:{sid}", title=doc_title)
    did = int(doc["id"])
    name, email = _signer([{"name": r.get("name"), "email": r["email"], "signingOrder": i} for i, r in enumerate(recipients)])
    await conn.execute(text(
        "INSERT INTO sign_documents (id, tenant_id, title, kind, counterparty, signer_name, signer_email, filename, status, form_level, "
        "form_message, form_acknowledged_by, source, documenso_id, documenso_status, template_id, recipients, employee_id, created_by, "
        "synced_at) VALUES (:id, :t, :ti, :k, :cp, :sn, :se, :f, 'pending', :fl, :fm, :ack, 'template', :d, 'PENDING', :tpl, "
        "CAST(:r AS jsonb), :e, :a, now())"),
        {"id": sid, "t": tenant_id, "ti": doc_title, "k": form.kind, "cp": name, "sn": name, "se": email, "f": f"{doc_title}.pdf".replace("/", "-"),
         "fl": form.level, "fm": form.message, "ack": actor if form.level == "warn" else None, "d": did, "tpl": template_id,
         "r": json.dumps(recipients), "e": employee_id, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="sign_document.template_used", entity_type="sign_document",
                       entity_id=sid, after={"template": template_id, "documenso_id": did, "employee": str(employee_id) if employee_id else None})
    return {"id": str(sid), "documenso_id": did, "status": "pending"}
