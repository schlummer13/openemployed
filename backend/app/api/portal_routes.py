"""API: Kundenportal (/api/portal), öffentliche Kündigung (/api/public) und interne Verwaltung."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.core_routes import run, tenant
from app.auth.deps import CurrentUser
from app.core.config import get_settings
from app.services import documents as docs
from app.services import offers as of
from app.services import portal as pt
from app.services import receivables as rv
from app.services.invoices import get_invoice, today_berlin
from app.services.ledger_client import to_ledger_payload
from app.services.masterdata import current_company_profile

router = APIRouter(tags=["kundenportal"])


def _out(data: Any) -> Any:
    return jsonable_encoder(to_ledger_payload(data))


def client_ip(request: Request) -> str | None:
    # nginx überschreibt X-Forwarded-For mit $remote_addr (eingehende Werte werden nicht vertraut)
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


async def _company(conn: Any) -> str:
    profile = await current_company_profile(conn, tenant(), today_berlin())
    return profile["legal_name"] if profile else "Kundenportal"


def _guard(fn: Any) -> Any:
    async def wrapped(conn: Any) -> Any:
        try:
            return await fn(conn)
        except pt.PortalRateLimited as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except pt.PortalAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except (pt.PortalStateError, of.OfferStateError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return wrapped


# ───────────────────────── Anmeldung ─────────────────────────

class CodeRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CodeVerifyIn(CodeRequestIn):
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")
    remember: bool = False


GENERIC = "Falls für diese Adresse ein Zugang besteht, haben wir Ihnen einen Code gesendet."


@router.post("/api/portal/login/request")
async def request_code(request: Request, data: CodeRequestIn) -> Any:
    mailer = getattr(request.app.state, "mailer", None)

    async def fn(conn: Any) -> Any:
        return await pt.request_code(conn, mailer, tenant(), data.email, client_ip(request), await _company(conn))
    code = await run(request, _guard(fn))
    out: dict[str, Any] = {"message": GENERIC}
    if code is not None:  # nur lokal ohne Postfach (DEV_LOGIN_ENABLED, nie in Produktion)
        out["dev_code"] = code
    return out


@router.post("/api/portal/login/verify")
async def verify_code(request: Request, response: Response, data: CodeVerifyIn) -> Any:
    result = await run(request, _guard(lambda conn: pt.verify_code(
        conn, tenant(), data.email, data.code, remember=data.remember, ip=client_ip(request),
        user_agent=request.headers.get("user-agent"))))
    if result is None:  # erst nach dem Commit des Fehlversuchs antworten
        raise HTTPException(status_code=401, detail="Code ungültig oder abgelaufen")
    token, customers, expires = result
    settings = get_settings()
    max_age = int((expires - dt.datetime.now(dt.UTC)).total_seconds()) if data.remember else None
    response.set_cookie(settings.portal_cookie_name, token, httponly=True, secure=settings.portal_base.startswith("https://"),
                        samesite="lax", path="/", max_age=max_age)
    return _out({"customers": customers, "employee": not customers})


async def portal_session(request: Request) -> dict[str, Any]:
    token = request.cookies.get(get_settings().portal_cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    session = await run(request, lambda conn: pt.resolve_session(conn, tenant(), token))
    if session is None:
        raise HTTPException(status_code=401, detail="Sitzung abgelaufen")
    return session


PortalSession = Annotated[dict[str, Any], Depends(portal_session)]


def _customer(session: dict[str, Any]) -> UUID:
    if session["customer_id"] is None:
        raise HTTPException(status_code=409, detail="Bitte zuerst einen Kunden auswählen")
    cid: UUID = session["customer_id"]
    return cid


@router.get("/api/portal/me")
async def me(session: PortalSession) -> Any:
    emp = session.get("employee")
    return _out({"email": session["email"], "customer_id": session["customer_id"], "customers": session["customers"],
                 "employee": {"name": f"{emp['first_name']} {emp['last_name']}"} if emp else None})


class SelectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: UUID


@router.post("/api/portal/select", status_code=204)
async def select(request: Request, data: SelectIn, session: PortalSession) -> Response:
    await run(request, _guard(lambda conn: pt.select_customer(conn, session, data.customer_id)))
    return Response(status_code=204)


class LogoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    everywhere: bool = False


@router.post("/api/portal/logout", status_code=204)
async def logout(request: Request, data: LogoutIn, session: PortalSession) -> Response:
    await run(request, lambda conn: pt.logout(conn, tenant(), session, everywhere=data.everywhere))
    resp = Response(status_code=204)
    resp.delete_cookie(get_settings().portal_cookie_name, path="/")
    return resp


# ───────────────────────── Daten ─────────────────────────

@router.get("/api/portal/overview")
async def overview(request: Request, session: PortalSession) -> Any:
    cid = _customer(session)
    return _out(await run(request, lambda conn: pt.overview(conn, tenant(), cid, today_berlin())))


def _pdf(content: bytes, filename: str, media: str = "application/pdf", inline: bool = True) -> Response:
    return Response(content, media_type=media, headers={
        "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{filename}"', "Cache-Control": "no-store"})


@router.get("/api/portal/invoices/{invoice_id}/document/{fmt}")
async def invoice_document(request: Request, invoice_id: UUID, fmt: Literal["pdf", "xml"], session: PortalSession) -> Response:
    cid = _customer(session)
    ledger = request.app.state.ledger

    async def fn(conn: Any) -> Any:
        await pt.owns(conn, tenant(), cid, "invoices", invoice_id)
        invoice = await get_invoice(conn, tenant(), invoice_id)
        stored = await docs.ensure_documents(conn, ledger, tenant_id=tenant(), invoice_id=invoice_id, number=invoice["number"],
                                             snapshot=invoice["snapshot"], actor=f"portal:{session['email']}")
        await pt.log_event(conn, tenant(), "invoice.downloaded", email=session["email"], customer_id=cid, subject_id=invoice_id,
                           ip=client_ip(request))
        return invoice["number"], stored
    number, stored = await run(request, fn)
    if fmt == "xml":
        return _pdf(stored.xml, docs.file_name(number, "xml"), "application/xml", inline=False)
    return _pdf(stored.pdf, docs.file_name(number, "pdf"))


@router.get("/api/portal/invoices/{invoice_id}/payment")
async def payment_info(request: Request, invoice_id: UUID, session: PortalSession) -> Any:
    cid = _customer(session)

    async def fn(conn: Any) -> Any:
        await pt.owns(conn, tenant(), cid, "invoices", invoice_id)
        r = await rv.invoice_receivable(conn, tenant(), invoice_id, today_berlin())
        invoice = await get_invoice(conn, tenant(), invoice_id)
        profile = await current_company_profile(conn, tenant(), today_berlin())
        company = {"name": profile["legal_name"], "iban": profile["iban"], "bic": profile["bic"]} if profile else {}
        amount = r["total_open"]
        return {"number": invoice["number"], "amount": amount, "recipient": company.get("name"), "iban": company.get("iban"),
                "bic": company.get("bic"), "reference": invoice["number"], "status": r["status"],
                "girocode": pt.girocode(company, amount, invoice["number"]) if amount > 0 else None}
    return _out(await run(request, _guard(fn)))


@router.get("/api/portal/offers/{offer_id}/pdf")
async def offer_pdf(request: Request, offer_id: UUID, session: PortalSession) -> Response:
    cid = _customer(session)

    async def fn(conn: Any) -> Any:
        await pt.owns(conn, tenant(), cid, "offers", offer_id)
        return await of.render_pdf(conn, tenant(), offer_id, today_berlin())
    number, pdf = await run(request, fn)
    return _pdf(pdf, f"Angebot_{number}.pdf")


class OfferDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    accept: bool
    name: str = Field(min_length=2, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    confirm: bool = False


@router.post("/api/portal/offers/{offer_id}/decision")
async def offer_decision(request: Request, offer_id: UUID, data: OfferDecisionIn, session: PortalSession) -> Any:
    cid = _customer(session)
    if data.accept and not data.confirm:
        raise HTTPException(status_code=422, detail="Bitte bestätigen Sie die verbindliche Annahme")

    async def fn(conn: Any) -> Any:
        await pt.owns(conn, tenant(), cid, "offers", offer_id)
        note = f"{data.note or ''} [Portal: {session['email']}, IP {client_ip(request)}]".strip()
        result = await of.decide(conn, tenant(), offer_id, accept=data.accept, name=data.name, channel="portal", note=note,
                                 actor=f"portal:{session['email']}", today=today_berlin())
        await pt.log_event(conn, tenant(), "offer.accepted" if data.accept else "offer.declined", email=session["email"],
                           customer_id=cid, subject_id=offer_id, ip=client_ip(request), user_agent=request.headers.get("user-agent"))
        return {"status": result["status"], "decided_at": result["decided_at"]}
    return _out(await run(request, _guard(fn)))


@router.get("/api/portal/dunning/{notice_id}/pdf")
async def dunning_pdf(request: Request, notice_id: UUID, session: PortalSession) -> Response:
    cid = _customer(session)

    async def fn(conn: Any) -> Any:
        await pt.owns(conn, tenant(), cid, "dunning_notices", notice_id)
        return await rv.notice_pdf(conn, tenant(), notice_id)
    number, pdf = await run(request, fn)
    return _pdf(pdf, f"{number}.pdf")


class TerminationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["termination", "withdrawal"] = "termination"
    termination_type: Literal["ordinary", "extraordinary"] = "ordinary"
    name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    customer_number: str | None = Field(default=None, max_length=40)
    contract_reference: str | None = Field(default=None, max_length=100)
    requested_date: dt.date | None = None
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("customer_number", "contract_reference", "reason", mode="before")
    @classmethod
    def _empty(cls, v: str | None) -> str | None:
        return v or None


class PortalTerminationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["termination", "withdrawal"] = "termination"
    termination_type: Literal["ordinary", "extraordinary"] = "ordinary"
    name: str = Field(min_length=2, max_length=200)
    requested_date: dt.date | None = None
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/api/portal/contracts/{contract_id}/terminate", status_code=201)
async def portal_terminate(request: Request, contract_id: UUID, data: PortalTerminationIn, session: PortalSession) -> Any:
    cid = _customer(session)
    mailer = getattr(request.app.state, "mailer", None)

    async def fn(conn: Any) -> Any:
        await pt.owns(conn, tenant(), cid, "contracts", contract_id)
        from sqlalchemy import text

        row = (await conn.execute(text("SELECT c.number, cu.number AS customer_number FROM contracts c JOIN customers cu "
                                       "ON cu.id = c.customer_id WHERE c.id = :id"), {"id": contract_id})).one()
        payload = data.model_dump() | {"email": session["email"], "customer_number": row.customer_number,
                                       "contract_reference": row.number}
        return await pt.submit_termination(conn, mailer, tenant(), payload, channel="portal", ip=client_ip(request),
                                           user_agent=request.headers.get("user-agent"), company=await _company(conn),
                                           customer_id=cid, contract_id=contract_id)
    return _out(await run(request, _guard(fn)))


@router.post("/api/public/terminations", status_code=201)
async def public_terminate(request: Request, data: TerminationIn) -> Any:
    """Kündigungsschaltfläche ohne Anmeldung (§ 312k BGB). Eingang wird sofort protokolliert und bestätigt."""
    mailer = getattr(request.app.state, "mailer", None)

    async def fn(conn: Any) -> Any:
        from sqlalchemy import text

        recent = await conn.scalar(text("SELECT count(*) FROM contract_terminations WHERE ip = :ip "
                                        "AND received_at > now() - interval '1 hour'"), {"ip": client_ip(request)})
        if recent >= 10:
            raise pt.PortalRateLimited("Zu viele Anfragen – bitte später erneut versuchen oder per E-Mail kündigen")
        return await pt.submit_termination(conn, mailer, tenant(), data.model_dump(), channel="public", ip=client_ip(request),
                                           user_agent=request.headers.get("user-agent"), company=await _company(conn))
    rec = await run(request, _guard(fn))
    return _out({k: rec[k] for k in ("number", "kind", "received_at", "confirmation_sent")})


@router.get("/api/public/company")
async def public_company(request: Request) -> Any:
    async def fn(conn: Any) -> Any:
        profile = await current_company_profile(conn, tenant(), today_berlin())
        logo = await docs.active_logo(conn, tenant())
        from app.einvoice.pdf import logo_data_uri

        return {"name": profile["legal_name"] if profile else "", "email": profile["email"] if profile else None,
                "street": profile["street"] if profile else None,
                "city": f"{profile['postal_code']} {profile['city']}" if profile else None,
                "logo": logo_data_uri(*logo) if logo else None}
    return _out(await run(request, fn))


# ───────────────────────── intern ─────────────────────────

class AccessIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.get("/api/customers/{customer_id}/portal-access")
async def list_access(request: Request, customer_id: UUID, _u: CurrentUser) -> Any:
    return _out(await run(request, lambda conn: pt.list_access(conn, tenant(), customer_id)))


@router.post("/api/customers/{customer_id}/portal-access", status_code=204)
async def grant_access(request: Request, customer_id: UUID, data: AccessIn, user: CurrentUser) -> Response:
    await run(request, lambda conn: pt.grant_access(conn, tenant(), customer_id, data.email, user.email))
    return Response(status_code=204)


@router.delete("/api/portal-access/{access_id}", status_code=204)
async def revoke_access(request: Request, access_id: UUID, user: CurrentUser) -> Response:
    await run(request, lambda conn: pt.revoke_access(conn, tenant(), access_id, user.email))
    return Response(status_code=204)


@router.get("/api/terminations")
async def list_terminations(request: Request, _u: CurrentUser) -> Any:
    return _out(await run(request, lambda conn: pt.list_terminations(conn, tenant())))


class ProcessIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    accept: bool
    contract_id: UUID | None = None
    note: str | None = Field(default=None, max_length=2000)


@router.post("/api/terminations/{termination_id}/process")
async def process_termination(request: Request, termination_id: UUID, data: ProcessIn, user: CurrentUser) -> Any:
    from app.services import contracts as ct

    async def fn(conn: Any) -> Any:
        try:
            return await pt.process_termination(conn, tenant(), termination_id, accept=data.accept, contract_id=data.contract_id,
                                                note=data.note, actor=user.email)
        except (ct.ContractStateError, ct.ContractValidationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _out(await run(request, _guard(fn)))


# ───────────────────────── Mitarbeiter-Self-Service ─────────────────────────

def _employee(session: dict[str, Any]) -> UUID:
    emp = session.get("employee")
    if not emp:
        raise HTTPException(status_code=403, detail="Kein Mitarbeiter-Zugang")
    eid: UUID = emp["id"]
    return eid


@router.get("/api/portal/employee")
async def employee_overview(request: Request, session: PortalSession) -> Any:
    from app.api.encoding import money_json
    from app.services import payroll as pr

    eid = _employee(session)
    return money_json(await run(request, lambda conn: pr.self_service(conn, tenant(), eid)))


class EmployeeSelfIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    phone: str | None = Field(default=None, max_length=50)
    street: str | None = Field(default=None, max_length=200)
    postal_code: str | None = Field(default=None, max_length=10)
    city: str | None = Field(default=None, max_length=100)
    iban: str | None = Field(default=None, max_length=42)
    account_holder: str | None = Field(default=None, max_length=70)

    @field_validator("iban")
    @classmethod
    def _iban(cls, v: str | None) -> str | None:
        from app.api.core_schemas import iban_valid

        if not v:
            return None
        if not iban_valid(v):
            raise ValueError("IBAN ungültig (Prüfziffer)")
        return v.replace(" ", "").upper()


@router.put("/api/portal/employee", status_code=204)
async def employee_update(request: Request, data: EmployeeSelfIn, session: PortalSession) -> Response:
    from app.services import payroll as pr

    eid = _employee(session)
    await run(request, lambda conn: pr.self_update(conn, tenant(), eid, data.model_dump(exclude_unset=True), session["email"]))
    return Response(status_code=204)


@router.get("/api/portal/employee/payslips/{payslip_id}/pdf")
async def employee_payslip(request: Request, payslip_id: UUID, session: PortalSession) -> Response:
    from app.services import payroll as pr

    eid = _employee(session)

    async def fn(conn: Any) -> Any:
        slip = await pr.get_payslip(conn, tenant(), payslip_id, employee_id=eid, approved_only=True)
        await pt.log_event(conn, tenant(), "payslip.downloaded", email=session["email"], subject_id=payslip_id, ip=client_ip(request))
        return slip, await pr.payslip_pdf(conn, tenant(), slip)
    slip, pdf = await run(request, fn)
    return _pdf(pdf, f"Entgeltabrechnung_{slip['period']}.pdf")
