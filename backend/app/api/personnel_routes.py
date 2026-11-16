"""Personal: Zeitkonten, On-/Offboarding, Vertragsvorlagen, Personalakte (Bereich Lohn & Personal) und eigene Sicht."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.api.portal_routes import PortalSession
from app.auth.deps import CurrentUser
from app.services import audit, hr
from app.services import time_account as ta
from app.services.invoices import today_berlin

router = APIRouter(tags=["personal"])
P = "/api/personnel"


def _file_response(name: str, mime: str, content: bytes, inline: bool = True) -> Response:
    disp = "inline" if inline and mime in ("application/pdf", "image/png", "image/jpeg") else "attachment"
    return Response(content, media_type=mime, headers={
        "Content-Disposition": f'{disp}; filename="{name}"', "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"})


# ───────── Zeitkonto ─────────

@router.get(f"{P}/time-accounts")
async def accounts(request: Request, _u: CurrentUser) -> Any:
    return money_json(await run(request, lambda conn: ta.overview(conn, tenant())))


@router.get(P + "/{employee_id}/time-account")
async def account(request: Request, employee_id: UUID, _u: CurrentUser) -> Any:
    return money_json(await run(request, lambda conn: ta.compute(conn, tenant(), employee_id)))


class BookingIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["opening", "payout", "adjustment", "expiry"]
    minutes: int = Field(ge=-100_000, le=100_000)
    booked_on: dt.date
    reason: str = Field(min_length=3, max_length=300)
    payroll_period: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    surcharge_percent: int = Field(default=0, ge=0, le=100)


@router.post(P + "/{employee_id}/time-account/bookings", status_code=201)
async def book(request: Request, employee_id: UUID, data: BookingIn, user: CurrentUser) -> Any:
    if data.minutes == 0:
        raise HTTPException(status_code=422, detail="Minuten dürfen nicht 0 sein")
    return {"id": str(await run(request, lambda conn: ta.book(conn, tenant(), employee_id, data.model_dump(), user.email)))}


@router.delete(P + "/time-account/bookings/{booking_id}", status_code=204)
async def unbook(request: Request, booking_id: UUID, user: CurrentUser) -> None:
    await run(request, lambda conn: ta.delete_booking(conn, tenant(), booking_id, user.email))


# ───────── Checklisten ─────────

@router.get(f"{P}/checklists")
async def open_checklists(request: Request, _u: CurrentUser, open: bool = True) -> Any:
    return money_json(await run(request, lambda conn: hr.checklists(conn, tenant(), open_only=open)))


@router.get(P + "/{employee_id}/checklists")
async def employee_checklists(request: Request, employee_id: UUID, _u: CurrentUser) -> Any:
    return money_json(await run(request, lambda conn: hr.checklists(conn, tenant(), employee_id)))


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["onboarding", "offboarding"]
    reference_date: dt.date
    template_id: UUID | None = None


@router.post(P + "/{employee_id}/checklists", status_code=201)
async def start(request: Request, employee_id: UUID, data: StartIn, user: CurrentUser) -> Any:
    cid = await run(request, lambda conn: hr.start_checklist(conn, tenant(), employee_id, data.kind, data.reference_date, user.email,
                                                             data.template_id))
    return {"id": str(cid)}


class ToggleIn(BaseModel):
    done: bool


@router.post(P + "/checklist-items/{item_id}", status_code=204)
async def toggle(request: Request, item_id: UUID, data: ToggleIn, user: CurrentUser) -> None:
    await run(request, lambda conn: hr.toggle_item(conn, tenant(), item_id, data.done, user.email))


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    responsible: str | None = Field(default=None, max_length=60)
    due_date: dt.date | None = None


@router.post(P + "/checklists/{checklist_id}/items", status_code=204)
async def add_item(request: Request, checklist_id: UUID, data: ItemIn, _u: CurrentUser) -> None:
    await run(request, lambda conn: hr.add_item(conn, tenant(), checklist_id, data.model_dump()))


@router.get(f"{P}/checklist-templates")
async def checklist_templates(request: Request, _u: CurrentUser) -> Any:
    return jsonable_encoder(await run(request, lambda conn: hr.templates(conn, tenant())))


class TemplateItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    offset: int | None = Field(default=0, ge=-365, le=365)
    responsible: str | None = Field(default=None, max_length=60)
    legal: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=500)


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["onboarding", "offboarding"]
    name: str = Field(min_length=1, max_length=100)
    items: list[TemplateItem] = Field(max_length=60)


@router.post(f"{P}/checklist-templates", status_code=201)
async def new_template(request: Request, data: TemplateIn, _u: CurrentUser) -> Any:
    return {"id": str(await run(request, lambda conn: hr.save_template(conn, tenant(), None, data.model_dump())))}


@router.put(P + "/checklist-templates/{template_id}", status_code=204)
async def put_template(request: Request, template_id: UUID, data: TemplateIn, _u: CurrentUser) -> None:
    await run(request, lambda conn: hr.save_template(conn, tenant(), template_id, data.model_dump()))


# ───────── Vertragsvorlagen ─────────

@router.get(f"{P}/contract-templates")
async def contract_templates(request: Request, _u: CurrentUser) -> Any:
    rows = await run(request, lambda conn: hr.contract_templates(conn, tenant()))
    return jsonable_encoder({"templates": rows, "placeholders": sorted(hr.placeholders(
        {"first_name": "", "last_name": "", "salary_type": "monthly", "gross_month": 0}, None, today_berlin()))})


class ContractTemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    category: Literal["contract", "amendment", "reference", "termination", "other"] = "contract"
    body: str = Field(min_length=10, max_length=60_000)


@router.post(f"{P}/contract-templates", status_code=201)
async def new_contract_template(request: Request, data: ContractTemplateIn, user: CurrentUser) -> Any:
    return {"id": str(await run(request, lambda conn: hr.save_contract_template(conn, tenant(), None, data.model_dump(), user.email)))}


@router.put(P + "/contract-templates/{template_id}", status_code=204)
async def put_contract_template(request: Request, template_id: UUID, data: ContractTemplateIn, user: CurrentUser) -> None:
    await run(request, lambda conn: hr.save_contract_template(conn, tenant(), template_id, data.model_dump(), user.email))


class GenerateIn(BaseModel):
    template_id: UUID


@router.post(P + "/{employee_id}/contracts", status_code=201)
async def generate(request: Request, employee_id: UUID, data: GenerateIn, user: CurrentUser) -> Any:
    return jsonable_encoder(await run(request, lambda conn: hr.generate_contract(conn, tenant(), employee_id, data.template_id, user.email,
                                                                               today_berlin())))


# ───────── Personalakte ─────────

@router.get(P + "/{employee_id}/documents")
async def documents(request: Request, employee_id: UUID, _u: CurrentUser) -> Any:
    return jsonable_encoder({"documents": await run(request, lambda conn: hr.documents(conn, tenant(), employee_id)),
                             "categories": hr.DOC_CATEGORIES})


@router.post(P + "/{employee_id}/documents", status_code=201)
async def upload(request: Request, employee_id: UUID, user: CurrentUser, file: Annotated[UploadFile, File()],
                 category: Annotated[str, Form()], title: Annotated[str, Form(min_length=1, max_length=200)],
                 visible_to_employee: Annotated[bool, Form()] = False, valid_from: Annotated[dt.date | None, Form()] = None,
                 valid_until: Annotated[dt.date | None, Form()] = None, notes: Annotated[str | None, Form(max_length=2000)] = None) -> Any:
    content = await file.read(hr.MAX_BYTES + 1)
    mime = (file.content_type or "application/octet-stream").split(";")[0]
    meta = {"category": category, "title": title, "filename": file.filename or "datei", "visible_to_employee": visible_to_employee,
            "valid_from": valid_from, "valid_until": valid_until, "notes": notes}
    return {"id": str(await run(request, lambda conn: hr.upload(conn, tenant(), employee_id, meta, mime, content, user.email)))}


@router.get(P + "/documents/{doc_id}/file")
async def doc_file(request: Request, doc_id: UUID, _u: CurrentUser, signed: bool = False) -> Response:
    name, mime, content = await run(request, lambda conn: hr.file(conn, tenant(), doc_id, signed=signed))
    return _file_response(name, mime, content)


class DocMetaIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    category: str
    visible_to_employee: bool = False
    valid_from: dt.date | None = None
    valid_until: dt.date | None = None
    delete_after: dt.date | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.put(P + "/documents/{doc_id}", status_code=204)
async def doc_meta(request: Request, doc_id: UUID, data: DocMetaIn, user: CurrentUser) -> None:
    if data.category not in hr.DOC_CATEGORIES:
        raise HTTPException(status_code=422, detail="Unbekannte Kategorie")
    await run(request, lambda conn: hr.update_document(conn, tenant(), doc_id, data.model_dump(), user.email))


class DeleteIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post(P + "/documents/{doc_id}/delete", status_code=204)
async def doc_delete(request: Request, doc_id: UUID, data: DeleteIn, user: CurrentUser) -> None:
    await run(request, lambda conn: hr.delete_document(conn, tenant(), doc_id, user.email, data.reason))


class SignIn(BaseModel):
    countersign: bool = True
    acknowledge_form: bool = False  # Schriftform-Hinweis gelesen (qualifizierte Signatur oder Papier zusätzlich)


@router.post(P + "/documents/{doc_id}/sign", status_code=201)
async def doc_sign(request: Request, doc_id: UUID, data: SignIn, user: CurrentUser) -> Any:
    from app.api.signature_routes import _client, _guard
    from app.services import documenso as ds

    client = _client(request)

    async def fn(conn: Any) -> Any:
        subj = await hr.signature_subject(conn, tenant(), doc_id)
        form = subj.pop("form")
        if form.level == "block":
            raise HTTPException(status_code=409, detail={"code": "form_blocked", "message": form.message})
        if form.level == "warn" and not data.acknowledge_form:
            raise HTTPException(status_code=409, detail={"code": "form_ack_required", "message": form.message})
        if form.level == "warn":
            await audit.record(conn, tenant_id=tenant(), actor=user.email, action="signature.form_acknowledged",
                               entity_type="personnel_document", entity_id=doc_id, after={"rule": form.kind, "message": form.message})
        if not subj["signer_email"]:
            raise HTTPException(status_code=409, detail="Für die Signatur braucht der Mitarbeiter eine E-Mail-Adresse")
        return await ds.create_request(conn, client, tenant(), subject_type="personnel_document", subject_id=doc_id,
                                       signer_name=subj["signer_name"], signer_email=subj["signer_email"],
                                       countersigner_email=user.email if data.countersign else None, actor=user.email,
                                       today=today_berlin())
    return jsonable_encoder(await run(request, _guard(fn)))


@router.get(f"{P}/deletion-due")
async def deletion_due(request: Request, _u: CurrentUser) -> Any:
    return jsonable_encoder(await run(request, lambda conn: hr.deletion_due(conn, tenant(), today_berlin())))


# ───────── eigene Sicht (App und Portal) ─────────

async def _my_id(conn: Any, email: str) -> UUID:
    from app.services.attendance import employee_by_email

    emp = await employee_by_email(conn, tenant(), email)
    return emp["id"]  # type: ignore[no-any-return]


@router.get("/api/me/time-account")
async def my_account(request: Request, user: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        return await ta.compute(conn, tenant(), await _my_id(conn, user.email))
    return money_json(await run(request, fn))


@router.get("/api/me/documents")
async def my_documents(request: Request, user: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        return await hr.documents(conn, tenant(), await _my_id(conn, user.email), employee_view=True)
    return jsonable_encoder(await run(request, fn))


@router.get("/api/me/documents/{doc_id}/file")
async def my_doc_file(request: Request, doc_id: UUID, user: CurrentUser, signed: bool = False) -> Response:
    async def fn(conn: Any) -> Any:
        return await hr.file(conn, tenant(), doc_id, employee_id=await _my_id(conn, user.email), signed=signed)
    name, mime, content = await run(request, fn)
    return _file_response(name, mime, content)


def _portal_id(session: dict[str, Any]) -> UUID:
    emp = session.get("employee")
    if not emp:
        raise HTTPException(status_code=403, detail="Kein Mitarbeiter-Zugang")
    return UUID(str(emp["id"]))


@router.get("/api/portal/employee/time-account")
async def portal_account(request: Request, session: PortalSession) -> Any:
    eid = _portal_id(session)
    return money_json(await run(request, lambda conn: ta.compute(conn, tenant(), eid)))


@router.get("/api/portal/employee/documents")
async def portal_documents(request: Request, session: PortalSession) -> Any:
    eid = _portal_id(session)
    return jsonable_encoder(await run(request, lambda conn: hr.documents(conn, tenant(), eid, employee_view=True)))


@router.get("/api/portal/employee/documents/{doc_id}/file")
async def portal_doc_file(request: Request, doc_id: UUID, session: PortalSession, signed: bool = False) -> Response:
    eid = _portal_id(session)
    name, mime, content = await run(request, lambda conn: hr.file(conn, tenant(), doc_id, employee_id=eid, signed=signed))
    return _file_response(name, mime, content)


# ───────── Documenso-Vorlagen für Mitarbeiter (z. B. Arbeitsvertrag aus Documenso) ─────────

@router.get(f"{P}/documenso-templates")
async def documenso_templates(request: Request, _u: CurrentUser) -> Any:
    from app.api.signature_routes import _client
    from app.services import documenso as ds
    from app.services import documenso_setup as dsu

    client = _client(request)
    try:
        return jsonable_encoder(await dsu.templates(client))
    except ds.SignatureError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class EmployeeTemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    template_id: int
    title: str | None = Field(default=None, max_length=200)
    recipients: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    acknowledge_form: bool = False


@router.post(P + "/{employee_id}/documenso-template", status_code=201)
async def employee_template(request: Request, employee_id: UUID, data: EmployeeTemplateIn, user: CurrentUser) -> Any:
    from app.api.signature_routes import _client, _guard
    from app.services import documenso_setup as dsu

    client = _client(request)
    recipients = [{"id": int(r["id"]), "email": str(r["email"]).strip().lower(), "name": str(r.get("name") or "")[:200]}
                  for r in data.recipients if r.get("email")]

    async def fn(conn: Any) -> Any:
        try:
            return await dsu.use_template(conn, client, tenant(), template_id=data.template_id, title=data.title, recipients=recipients,
                                          employee_id=employee_id, acknowledge_form=data.acknowledge_form, actor=user.email)
        except dsu.FormAckRequired as exc:
            raise HTTPException(status_code=409, detail={"code": "form_ack_required", "message": exc.message}) from exc
    return jsonable_encoder(await run(request, _guard(fn)))


# ───────── Schriftform-Dokumente: qualifiziert, Papier oder E-Mail ─────────

class HandoffIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    channel: Literal["qes", "email", "paper"]
    note: str | None = Field(default=None, max_length=500)


@router.post(P + "/documents/{doc_id}/handoff", status_code=201)
async def handoff(request: Request, doc_id: UUID, data: HandoffIn, user: CurrentUser) -> Any:
    """Übergibt ein Dokument der Personalakte an einen Schriftform-Weg; das unterschriebene Exemplar kommt in die Akte zurück."""
    from sqlalchemy import text

    from app.services import sign_channels as ch
    from app.services import sign_documents as sd

    mailer = getattr(request.app.state, "mailer", None)

    async def fn(conn: Any) -> Any:
        row = (await conn.execute(text(
            "SELECT d.title, d.filename, d.content, d.category, d.mime_type, e.id AS employee_id, e.first_name, e.last_name, e.email, "
            "e.exit_date FROM personnel_documents d JOIN employees e ON e.id = d.employee_id WHERE d.tenant_id = :t AND d.id = :id "
            "AND d.deleted_at IS NULL"), {"t": tenant(), "id": doc_id})).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
        if row["mime_type"] != "application/pdf":
            raise HTTPException(status_code=409, detail="Nur PDF-Dokumente")
        form = hr.signature_form_check(row["category"], row["title"], row["exit_date"])
        kind = form.kind if form.kind in ("termination", "employment_fixed_term", "non_compete") else "general"
        name = f"{row['first_name']} {row['last_name']}"
        if data.channel != "paper" and not row["email"]:
            raise HTTPException(status_code=409, detail="Für diesen Weg braucht der Mitarbeiter eine E-Mail-Adresse")
        doc = await sd.create(conn, tenant(), {"title": row["title"], "kind": kind, "counterparty": name, "signer_name": name,
                                               "signer_email": row["email"] or "unbekannt@invalid", "filename": row["filename"],
                                               "employee_id": row["employee_id"], "personnel_document_id": doc_id},
                              bytes(row["content"]), user.email)
        if data.channel == "qes":
            res = await ch.send_qes(conn, tenant(), doc["id"], user.email)
        elif data.channel == "email":
            res = await ch.send_mail(conn, mailer, tenant(), doc["id"], user.email, data.note)
        else:
            res = await ch.mark_paper(conn, tenant(), doc["id"], user.email, data.note)
        return res | {"sign_document_id": str(doc["id"])}
    return jsonable_encoder(await run(request, fn))



@router.get(P + "/documents/{doc_id}/form")
async def doc_form(request: Request, doc_id: UUID, _u: CurrentUser) -> Any:
    """Welche Wege für dieses Dokument möglich sind (Formvorschrift, eingerichtete Dienste)."""
    from sqlalchemy import text

    from app.services import qes

    async def fn(conn: Any) -> Any:
        row = (await conn.execute(text("SELECT d.category, d.title, e.exit_date, e.email FROM personnel_documents d JOIN employees e "
                                       "ON e.id = d.employee_id WHERE d.tenant_id = :t AND d.id = :id"),
                                  {"t": tenant(), "id": doc_id})).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
        f = hr.signature_form_check(row[0], row[1], row[2])
        return {"level": f.level, "message": f.message, "label": f.label, "qes": qes.info(), "email": row[3],
                "documenso": getattr(request.app.state, "documenso", None) is not None,
                "mail": getattr(request.app.state, "mailer", None) is not None}
    return await run(request, fn)


@router.post(P + "/documents/{doc_id}/signed-copy")
async def doc_signed_copy(request: Request, doc_id: UUID, user: CurrentUser, file: Annotated[UploadFile, File()]) -> Any:
    from sqlalchemy import text

    from app.services import sign_channels as ch

    content = await file.read(hr.MAX_BYTES + 1)

    async def fn(conn: Any) -> Any:
        sid = await conn.scalar(text("SELECT id FROM sign_documents WHERE tenant_id = :t AND personnel_document_id = :d AND status = "
                                     "'awaiting_paper' ORDER BY created_at DESC LIMIT 1"), {"t": tenant(), "d": doc_id})
        if sid is None:
            raise HTTPException(status_code=409, detail="Für dieses Dokument wird kein unterschriebenes Exemplar erwartet")
        return await ch.upload_signed(conn, tenant(), sid, content, user.email)
    return await run(request, fn)
