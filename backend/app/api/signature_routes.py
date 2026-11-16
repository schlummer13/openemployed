"""API: Digitale Signatur (Documenso) inkl. Webhook."""

from __future__ import annotations

import hmac
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run, tenant
from app.auth.deps import CurrentUser
from app.core.config import get_settings
from app.services import contracts as ct
from app.services import documenso as ds
from app.services import offers as of
from app.services.invoices import today_berlin
from app.services.ledger_client import to_ledger_payload

router = APIRouter(tags=["signatur"])


class SignatureIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject_type: Literal["offer", "contract"]
    subject_id: UUID
    signer_name: str = Field(min_length=2, max_length=200)
    signer_email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    countersign: bool = False


def _client(request: Request) -> ds.DocumensoClient:
    client = getattr(request.app.state, "documenso", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Documenso ist nicht eingerichtet (DOCUMENSO_API_TOKEN fehlt)")
    return client  # type: ignore[no-any-return]


def _guard(fn: Any) -> Any:
    async def wrapped(conn: Any) -> Any:
        try:
            return await fn(conn)
        except (ds.SignatureStateError, of.OfferStateError, ct.ContractStateError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ds.SignatureError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return wrapped


@router.post("/api/signatures", status_code=201)
async def create_signature(request: Request, data: SignatureIn, user: CurrentUser) -> Any:
    client = _client(request)
    return jsonable_encoder(await run(request, _guard(lambda conn: ds.create_request(
        conn, client, tenant(), subject_type=data.subject_type, subject_id=data.subject_id, signer_name=data.signer_name,
        signer_email=data.signer_email, countersigner_email=user.email if data.countersign else None, actor=user.email,
        today=today_berlin()))))


@router.get("/api/signatures")
async def list_signatures(request: Request, _u: CurrentUser, subject_type: str | None = None, subject_id: UUID | None = None) -> Any:
    rows = await run(request, lambda conn: ds.list_requests(conn, tenant(), subject_type, subject_id))
    return {"configured": getattr(request.app.state, "documenso", None) is not None, "items": jsonable_encoder(to_ledger_payload(rows))}


@router.get("/api/signatures/{request_id}/document")
async def signature_document(request: Request, request_id: UUID, _u: CurrentUser, signed: bool = False) -> Response:
    title, pdf = await run(request, lambda conn: ds.document(conn, tenant(), request_id, signed))
    name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in title)[:80]
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{name}{"_signiert" if signed else ""}.pdf"', "Cache-Control": "no-store"})


@router.post("/api/signatures/{request_id}/refresh")
async def refresh_signature(request: Request, request_id: UUID, _u: CurrentUser) -> Any:
    client = _client(request)
    ledger = request.app.state.ledger
    return jsonable_encoder(await run(request, _guard(lambda conn: ds.refresh(conn, client, ledger, tenant(), request_id))))


@router.post("/api/webhooks/documenso")
async def documenso_webhook(request: Request) -> Any:
    """Von Documenso aufgerufen (CSRF-ausgenommen); Authentifizierung über das gemeinsame Geheimnis."""
    secret = get_settings().documenso_webhook_secret.get_secret_value()
    given = request.headers.get("x-documenso-secret", "")
    if not secret or not hmac.compare_digest(given.encode(), secret.encode()):
        raise HTTPException(status_code=401, detail="Ungültige Signatur")
    try:
        body = await request.json()
        event = str(body["event"])
        payload = body.get("payload") or {}
        documenso_id = int(payload.get("id") or payload["documentId"])
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Ungültige Nutzlast") from exc
    client = _client(request)
    ledger = request.app.state.ledger
    return await run(request, _guard(lambda conn: ds.apply_event(conn, client, ledger, tenant(), documenso_id=documenso_id, event=event)))


# ───────── Beliebige Verträge (Lieferanten, Vermieter, NDA, Partner) ─────────

@router.get("/api/signatures/documents")
async def sign_documents(request: Request, _u: CurrentUser) -> Any:
    from app.domain.signature_form import options
    from app.services import sign_documents as sd

    rows = await run(request, lambda conn: sd.list_all(conn, tenant()))
    from app.services import qes

    configured = getattr(request.app.state, "documenso", None) is not None
    st = getattr(request.app.state, "documenso_status", None) or {}
    return jsonable_encoder({"documents": rows, "kinds": options(), "configured": configured, "team_url": st.get("team_url"),
                             "qes": qes.info(), "mail": getattr(request.app.state, "mailer", None) is not None})


@router.post("/api/signatures/documents/check")
async def sign_check(_u: CurrentUser, data: dict[str, str]) -> Any:
    from app.domain.signature_form import check

    c = check(data.get("kind", "general"), data.get("title", ""))
    return {"kind": c.kind, "level": c.level, "label": c.label, "message": c.message}


@router.post("/api/signatures/documents", status_code=201)
async def sign_document_create(request: Request, user: CurrentUser, file: Annotated[UploadFile, File()],
                               title: Annotated[str, Form(min_length=1, max_length=200)], kind: Annotated[str, Form()],
                               counterparty: Annotated[str, Form(min_length=1, max_length=200)],
                               signer_name: Annotated[str, Form(min_length=1, max_length=200)],
                               signer_email: Annotated[str, Form(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")],
                               notes: Annotated[str | None, Form(max_length=1000)] = None) -> Any:
    from app.services import sign_documents as sd

    content = await file.read(sd.MAX_BYTES + 1)
    data = {"title": title, "kind": kind, "counterparty": counterparty, "signer_name": signer_name, "signer_email": signer_email,
            "notes": notes, "filename": file.filename or "vertrag.pdf"}
    return jsonable_encoder(await run(request, lambda conn: sd.create(conn, tenant(), data, content, user.email)))


class SendIn(BaseModel):
    countersign: bool = True
    acknowledge_form: bool = False


@router.post("/api/signatures/documents/{doc_id}/send", status_code=201)
async def sign_document_send(request: Request, doc_id: UUID, data: SendIn, user: CurrentUser) -> Any:
    from app.services import sign_documents as sd

    client = _client(request)

    async def fn(conn: Any) -> Any:
        doc = await sd.get(conn, tenant(), doc_id)
        if doc["form_level"] == "block":
            raise HTTPException(status_code=409, detail={"code": "form_blocked", "message": doc["form_message"]})
        if doc["form_level"] == "warn" and not (data.acknowledge_form or doc["form_acknowledged_by"]):
            raise HTTPException(status_code=409, detail={"code": "form_ack_required", "message": doc["form_message"]})
        if doc["form_level"] == "warn" and not doc["form_acknowledged_by"]:
            await sd.acknowledge(conn, tenant(), doc_id, user.email)
        return await ds.create_request(conn, client, tenant(), subject_type="document", subject_id=doc_id, signer_name=doc["signer_name"],
                                       signer_email=doc["signer_email"], countersigner_email=user.email if data.countersign else None,
                                       actor=user.email, today=today_berlin())
    return jsonable_encoder(await run(request, _guard(fn)))


@router.get("/api/signatures/documents/{doc_id}/file")
async def sign_document_file(request: Request, doc_id: UUID, _u: CurrentUser, signed: bool = False) -> Response:
    from app.services import sign_documents as sd

    name, pdf = await run(request, lambda conn: sd.file(conn, tenant(), doc_id, signed))
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"',
                                                                 "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


# ───────── Documenso: Verbindung, Abgleich, Vorlagen ─────────

@router.get("/api/admin/documenso")
async def documenso_status(request: Request, _u: CurrentUser) -> Any:
    status = getattr(request.app.state, "documenso_status", None)
    client = getattr(request.app.state, "documenso", None)
    if status is None:
        status = {"connected": client is not None, "manual": client is not None,
                  "error": None if client else "Nicht eingerichtet (DOCUMENSO_DATABASE_URL oder DOCUMENSO_API_TOKEN fehlt)"}
    return status


@router.post("/api/admin/documenso/connect")
async def documenso_connect(request: Request, _u: CurrentUser) -> Any:
    from app.services import documenso_setup as dsu

    try:
        return await dsu.connect(request.app)
    except dsu.SetupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/admin/documenso/password-link")
async def documenso_password_link(request: Request, user: CurrentUser) -> Any:
    from app.services import documenso_setup as dsu

    async def fn(conn: Any) -> Any:
        res = await dsu.password_link(request.app)
        await audit_record(conn, user.email, "documenso.password_link", {"email": res["email"]})
        return res
    return await run(request, fn)


async def audit_record(conn: Any, actor: str, action: str, after: dict[str, Any]) -> None:
    from app.services import audit

    await audit.record(conn, tenant_id=tenant(), actor=actor, action=action, entity_type="documenso", entity_id=None, after=after)


@router.post("/api/signatures/sync")
async def documenso_sync(request: Request, _u: CurrentUser) -> Any:
    from app.services import documenso_setup as dsu

    client = _client(request)
    ledger = request.app.state.ledger
    return await run(request, _guard(lambda conn: dsu.sync(conn, client, ledger, tenant())))


@router.get("/api/signatures/templates")
async def documenso_templates(request: Request, _u: CurrentUser) -> Any:
    from app.services import documenso_setup as dsu

    client = _client(request)
    try:
        return jsonable_encoder(await dsu.templates(client))
    except ds.SignatureError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class TemplateRecipientIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: int
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str | None = Field(default=None, max_length=200)


class UseTemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, max_length=200)
    recipients: list[TemplateRecipientIn] = Field(min_length=1, max_length=20)
    employee_id: UUID | None = None
    acknowledge_form: bool = False


@router.post("/api/signatures/templates/{template_id}/use", status_code=201)
async def documenso_use_template(request: Request, template_id: int, data: UseTemplateIn, user: CurrentUser) -> Any:
    from app.services import documenso_setup as dsu

    client = _client(request)

    async def fn(conn: Any) -> Any:
        try:
            return await dsu.use_template(conn, client, tenant(), template_id=template_id, title=data.title,
                                          recipients=[r.model_dump() for r in data.recipients], employee_id=data.employee_id,
                                          acknowledge_form=data.acknowledge_form, actor=user.email)
        except dsu.FormAckRequired as exc:
            raise HTTPException(status_code=409, detail={"code": "form_ack_required", "message": exc.message}) from exc
    return jsonable_encoder(await run(request, _guard(fn)))


# ───────── Weitere Versandwege: qualifiziert (externer Anbieter), Papier, E-Mail ─────────

class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    note: str | None = Field(default=None, max_length=500)


@router.post("/api/signatures/documents/{doc_id}/qes", status_code=201)
async def sign_qes(request: Request, doc_id: UUID, user: CurrentUser) -> Any:
    from app.services import sign_channels as ch

    return await run(request, lambda conn: ch.send_qes(conn, tenant(), doc_id, user.email))


@router.post("/api/signatures/documents/{doc_id}/mail")
async def sign_mail(request: Request, doc_id: UUID, data: NoteIn, user: CurrentUser) -> Any:
    from app.services import sign_channels as ch

    mailer = getattr(request.app.state, "mailer", None)
    return await run(request, lambda conn: ch.send_mail(conn, mailer, tenant(), doc_id, user.email, data.note))


@router.post("/api/signatures/documents/{doc_id}/paper")
async def sign_paper(request: Request, doc_id: UUID, data: NoteIn, user: CurrentUser) -> Any:
    from app.services import sign_channels as ch

    return await run(request, lambda conn: ch.mark_paper(conn, tenant(), doc_id, user.email, data.note))


@router.post("/api/signatures/documents/{doc_id}/signed-copy")
async def sign_upload(request: Request, doc_id: UUID, user: CurrentUser, file: Annotated[UploadFile, File()]) -> Any:
    from app.services import sign_channels as ch
    from app.services import sign_documents as sd

    content = await file.read(sd.MAX_BYTES + 1)
    return await run(request, lambda conn: ch.upload_signed(conn, tenant(), doc_id, content, user.email))


@router.post("/api/signatures/qes-sync")
async def sign_qes_sync(request: Request, _u: CurrentUser) -> Any:
    from app.services import sign_channels as ch

    return await run(request, lambda conn: ch.sync_qes(conn, tenant()))
