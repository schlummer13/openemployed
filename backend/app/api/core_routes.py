"""API: Firmendaten, Kunden, Produkte, Rechnungen. Alle Endpunkte erfordern Anmeldung (keine Rollen)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.api.core_schemas import (
    CancelIn,
    CompanyProfileIn,
    CompanyProfileOut,
    CustomerIn,
    CustomerOut,
    InvoiceDraftIn,
    InvoiceOut,
    InvoiceSummaryOut,
    ProductIn,
    ProductOut,
)
from app.auth.deps import CurrentUser
from app.einvoice.validate import EInvoiceValidationError
from app.services import documents as docs
from app.services import invoices as inv
from app.services import masterdata as md
from app.services import receipts as rc
from app.services.ledger_client import LedgerClient, LedgerError
from app.services.mail import MailError, MailNotConfiguredError, WorkspaceMailer

router = APIRouter(prefix="/api")


@asynccontextmanager
async def transaction(request: Request) -> AsyncIterator[AsyncConnection]:
    engine: AsyncEngine = request.app.state.engine
    async with engine.begin() as conn:
        yield conn


async def run[T](request: Request, fn: Callable[[AsyncConnection], Awaitable[T]]) -> T:
    """Führt die Aktion in einer Transaktion aus und übersetzt Fachfehler in HTTP-Antworten.

    Commit erfolgt, bevor die Antwort gesendet wird – der Client sieht nie Erfolg für etwas Nicht-Gespeichertes.
    """
    try:
        async with transaction(request) as conn:
            return await fn(conn)
    except md.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (md.ConflictError, inv.InvoiceStateError, rc.ReceiptStateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (inv.InvoiceValidationError, rc.ReceiptError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LedgerError as exc:
        status_code = 409 if exc.status_code == 409 else 502
        raise HTTPException(status_code=status_code, detail=f"Festschreibung fehlgeschlagen: {exc.message}") from exc
    except EInvoiceValidationError as exc:
        raise HTTPException(status_code=422, detail=f"E-Rechnung verletzt EN 16931: {exc}") from exc
    except docs.LogoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MailNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=f"E-Mail nicht eingerichtet: {exc}") from exc
    except MailError as exc:
        raise HTTPException(status_code=502, detail=f"Versand über Google Workspace fehlgeschlagen: {exc}") from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Datensatz verletzt eine Regel der Datenbank") from exc


def tenant() -> UUID:
    """Aktive Firma der Anfrage (Mandantenwechsel über die Firmenauswahl), sonst die Stammfirma."""
    from app.core.tenancy import current

    return current()


# ───────────────────────── Firmendaten ─────────────────────────

@router.get("/company-profile", response_model=CompanyProfileOut | None, tags=["firma"])
async def get_company_profile(request: Request, _u: CurrentUser) -> dict[str, Any] | None:
    async def op(conn: AsyncConnection) -> dict[str, Any] | None:
        row = await md.current_company_profile(conn, tenant(), inv.today_berlin())
        if row is None:
            # zukünftig gültige Version ebenfalls anzeigen (z. B. vor Gründung erfasst)
            row = (await conn.execute(text(
                f"SELECT {md.COMPANY_COLUMNS} FROM company_profiles WHERE tenant_id = :t ORDER BY valid_from LIMIT 1"
            ), {"t": tenant()})).mappings().one_or_none()
        if row is None:
            return None
        return {**dict(row), "missing_for_invoices": md.missing_invoice_fields(row)}

    return await run(request, op)


@router.post("/company-profile", response_model=CompanyProfileOut, status_code=201, tags=["firma"])
async def create_company_profile(request: Request, data: CompanyProfileIn, user: CurrentUser) -> dict[str, Any]:
    async def op(conn: AsyncConnection) -> dict[str, Any]:
        row = await md.create_company_profile(conn, tenant(), data, user.email)
        return {**dict(row), "missing_for_invoices": md.missing_invoice_fields(row)}

    return await run(request, op)


# ───────────────────────── Kunden ─────────────────────────

@router.get("/customers", response_model=list[CustomerOut], tags=["kunden"])
async def list_customers(request: Request, _u: CurrentUser, include_archived: bool = False) -> list[dict[str, Any]]:
    async def op(conn: AsyncConnection) -> list[dict[str, Any]]:
        return [md.customer_out(r) for r in await md.list_customers(conn, tenant(), include_archived)]

    return await run(request, op)


@router.get("/customers/{customer_id}", response_model=CustomerOut, tags=["kunden"])
async def get_customer(request: Request, customer_id: UUID, _u: CurrentUser) -> dict[str, Any]:
    async def op(conn: AsyncConnection) -> dict[str, Any]:
        return md.customer_out(await md.get_customer(conn, tenant(), customer_id))

    return await run(request, op)


@router.post("/customers", status_code=201, tags=["kunden"])
async def create_customer(request: Request, data: CustomerIn, user: CurrentUser, welcome: bool = True) -> dict[str, Any]:
    async def op(conn: AsyncConnection) -> dict[str, Any]:
        return md.customer_out(await md.create_customer(conn, tenant(), data, user.email))

    created = await run(request, op)
    if welcome:
        # Eigene Transaktion: Ein Mailfehler macht die Kundenanlage nicht rückgängig
        from app.services import portal as pt
        from app.services.invoices import today_berlin

        enabled = await run(request, lambda conn: conn.scalar(text("SELECT welcome_mail FROM finance_settings WHERE tenant_id = :t"),
                                                              {"t": tenant()}))
        if enabled is not False:
            mailer = getattr(request.app.state, "mailer", None)
            try:
                created["welcome"] = await run(request, lambda conn: pt.send_welcome(conn, mailer, tenant(), created["id"], user.email,
                                                                                      today_berlin()))
            except HTTPException as exc:
                created["welcome"] = {"access": False, "mail_sent": False, "reason": str(exc.detail)}
    return created


@router.post("/customers/{customer_id}/welcome", tags=["kunden"])
async def resend_welcome(request: Request, customer_id: UUID, user: CurrentUser) -> dict[str, Any]:
    from app.services import portal as pt
    from app.services.invoices import today_berlin

    mailer = getattr(request.app.state, "mailer", None)
    if mailer is None:
        raise HTTPException(status_code=503, detail="E-Mail nicht eingerichtet")
    return await run(request, lambda conn: pt.send_welcome(conn, mailer, tenant(), customer_id, user.email, today_berlin()))


@router.put("/customers/{customer_id}", response_model=CustomerOut, tags=["kunden"])
async def update_customer(request: Request, customer_id: UUID, data: CustomerIn, user: CurrentUser) -> dict[str, Any]:
    async def op(conn: AsyncConnection) -> dict[str, Any]:
        return md.customer_out(await md.update_customer(conn, tenant(), customer_id, data, user.email))

    return await run(request, op)


@router.post("/customers/{customer_id}/archive", response_model=CustomerOut, tags=["kunden"])
async def archive_customer(request: Request, customer_id: UUID, user: CurrentUser) -> dict[str, Any]:
    async def op(conn: AsyncConnection) -> dict[str, Any]:
        return md.customer_out(await md.set_customer_archived(conn, tenant(), customer_id, True, user.email))

    return await run(request, op)


@router.post("/customers/{customer_id}/restore", response_model=CustomerOut, tags=["kunden"])
async def restore_customer(request: Request, customer_id: UUID, user: CurrentUser) -> dict[str, Any]:
    async def op(conn: AsyncConnection) -> dict[str, Any]:
        return md.customer_out(await md.set_customer_archived(conn, tenant(), customer_id, False, user.email))

    return await run(request, op)


# ───────────────────────── Produkte ─────────────────────────

@router.get("/products", response_model=list[ProductOut], tags=["produkte"])
async def list_products(request: Request, _u: CurrentUser, include_inactive: bool = False) -> list[dict[str, Any]]:
    async def op(conn: AsyncConnection) -> list[dict[str, Any]]:
        return [md.product_out(r) for r in await md.list_products(conn, tenant(), include_inactive)]

    return await run(request, op)


@router.post("/products", response_model=ProductOut, status_code=201, tags=["produkte"])
async def create_product(request: Request, data: ProductIn, user: CurrentUser) -> dict[str, Any]:
    async def op(conn: AsyncConnection) -> dict[str, Any]:
        return md.product_out(await md.create_product(conn, tenant(), data, user.email))

    return await run(request, op)


@router.put("/products/{product_id}", response_model=ProductOut, tags=["produkte"])
async def update_product(request: Request, product_id: UUID, data: ProductIn, user: CurrentUser) -> dict[str, Any]:
    async def op(conn: AsyncConnection) -> dict[str, Any]:
        return md.product_out(await md.update_product(conn, tenant(), product_id, data, user.email))

    return await run(request, op)


# ───────────────────────── Rechnungen ─────────────────────────

@router.get("/invoices", response_model=list[InvoiceSummaryOut], tags=["rechnungen"])
async def list_invoices(request: Request, _u: CurrentUser) -> list[dict[str, Any]]:
    return await run(request, lambda conn: inv.list_invoices(conn, tenant()))


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut, tags=["rechnungen"])
async def get_invoice(request: Request, invoice_id: UUID, _u: CurrentUser) -> dict[str, Any]:
    return await run(request, lambda conn: inv.get_invoice(conn, tenant(), invoice_id))


@router.post("/invoices", response_model=InvoiceOut, status_code=201, tags=["rechnungen"])
async def create_invoice(request: Request, data: InvoiceDraftIn, user: CurrentUser) -> dict[str, Any]:
    return await run(request, lambda conn: inv.create_draft(conn, tenant(), data, user.email))


@router.put("/invoices/{invoice_id}", response_model=InvoiceOut, tags=["rechnungen"])
async def update_invoice(request: Request, invoice_id: UUID, data: InvoiceDraftIn, user: CurrentUser) -> dict[str, Any]:
    return await run(request, lambda conn: inv.update_draft(conn, tenant(), invoice_id, data, user.email))


@router.delete("/invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["rechnungen"])
async def delete_invoice(request: Request, invoice_id: UUID, user: CurrentUser) -> Response:
    await run(request, lambda conn: inv.delete_draft(conn, tenant(), invoice_id, user.email))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invoices/{invoice_id}/issue", response_model=InvoiceOut, tags=["rechnungen"])
async def issue_invoice(request: Request, invoice_id: UUID, user: CurrentUser) -> dict[str, Any]:
    ledger: LedgerClient = request.app.state.ledger
    return await run(request, lambda conn: inv.issue_invoice(conn, ledger, tenant(), invoice_id, user.email))


@router.post("/invoices/{invoice_id}/cancel", response_model=InvoiceOut, tags=["rechnungen"])
async def cancel_invoice(request: Request, invoice_id: UUID, data: CancelIn, user: CurrentUser) -> dict[str, Any]:
    ledger: LedgerClient = request.app.state.ledger
    return await run(request, lambda conn: inv.cancel_invoice(conn, ledger, tenant(), invoice_id, data.reason, user.email))


# ───────────────────────── Dokumente & Versand (Phase 1b) ─────────────────────────

async def _issued(conn: AsyncConnection, invoice_id: UUID) -> dict[str, Any]:
    invoice = await inv.get_invoice(conn, tenant(), invoice_id)
    if invoice["status"] != "issued":
        raise inv.InvoiceStateError("Dokumente gibt es erst nach der Freigabe")
    return invoice


async def _documents(request: Request, invoice_id: UUID, actor: str) -> tuple[dict[str, Any], docs.StoredDocuments]:
    ledger: LedgerClient = request.app.state.ledger

    async def op(conn: AsyncConnection) -> tuple[dict[str, Any], docs.StoredDocuments]:
        invoice = await _issued(conn, invoice_id)
        stored = await docs.ensure_documents(conn, ledger, tenant_id=tenant(), invoice_id=invoice_id,
                                             number=invoice["number"], snapshot=invoice["snapshot"], actor=actor)
        return invoice, stored

    return await run(request, op)


@router.get("/invoices/{invoice_id}/pdf", tags=["rechnungen"], response_class=Response)
async def invoice_pdf(request: Request, invoice_id: UUID, user: CurrentUser, download: bool = False) -> Response:
    invoice, stored = await _documents(request, invoice_id, user.email)
    disposition = "attachment" if download else "inline"
    return Response(stored.pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'{disposition}; filename="{docs.file_name(invoice["number"], "pdf")}"',
        "Cache-Control": "no-store",
        "X-Content-SHA256": stored.pdf_sha256,
    })


@router.get("/invoices/{invoice_id}/xml", tags=["rechnungen"], response_class=Response)
async def invoice_xml(request: Request, invoice_id: UUID, user: CurrentUser) -> Response:
    invoice, stored = await _documents(request, invoice_id, user.email)
    return Response(stored.xml, media_type="application/xml", headers={
        "Content-Disposition": f'attachment; filename="{docs.file_name(invoice["number"], "xml")}"',
        "Cache-Control": "no-store",
        "X-Content-SHA256": stored.xml_sha256,
    })


@router.get("/invoices/{invoice_id}/xrechnung", tags=["rechnungen"], response_class=Response)
async def invoice_xrechnung(request: Request, invoice_id: UUID, user: CurrentUser) -> Response:
    invoice, stored = await _documents(request, invoice_id, user.email)
    if stored.xrechnung is None:
        raise HTTPException(status_code=404, detail="Für diesen Kunden ist keine XRechnung eingestellt")
    return Response(stored.xrechnung, media_type="application/xml", headers={
        "Content-Disposition": f'attachment; filename="{docs.file_name(invoice["number"], "xml").replace(".xml", "_XRechnung.xml")}"',
        "Cache-Control": "no-store"})


@router.post("/invoices/{invoice_id}/peppol", tags=["rechnungen"])
async def send_peppol(request: Request, invoice_id: UUID, user: CurrentUser) -> dict[str, Any]:
    ledger: LedgerClient = request.app.state.ledger

    async def op(conn: AsyncConnection) -> dict[str, Any]:
        invoice = await _issued(conn, invoice_id)
        stored = await docs.ensure_documents(conn, ledger, tenant_id=tenant(), invoice_id=invoice_id,
                                             number=invoice["number"], snapshot=invoice["snapshot"], actor=user.email)
        try:
            return await docs.send_peppol(conn, tenant_id=tenant(), invoice_id=invoice_id, number=invoice["number"],
                                          snapshot=invoice["snapshot"], documents=stored, actor=user.email)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await run(request, op)


@router.post("/invoices/{invoice_id}/send", tags=["rechnungen"])
async def send_invoice(request: Request, invoice_id: UUID, user: CurrentUser) -> dict[str, Any]:
    mailer: WorkspaceMailer | None = request.app.state.mailer
    if mailer is None:
        raise HTTPException(status_code=503, detail="E-Mail ist nicht eingerichtet (Einrichtung → E-Mail)")
    ledger: LedgerClient = request.app.state.ledger

    async def op(conn: AsyncConnection) -> dict[str, Any]:
        invoice = await _issued(conn, invoice_id)
        stored = await docs.ensure_documents(conn, ledger, tenant_id=tenant(), invoice_id=invoice_id,
                                             number=invoice["number"], snapshot=invoice["snapshot"], actor=user.email)
        return await docs.send_invoice(conn, mailer, tenant_id=tenant(), invoice_id=invoice_id, number=invoice["number"],
                                       snapshot=invoice["snapshot"], documents=stored, actor=user.email)

    return await run(request, op)


@router.get("/invoices/{invoice_id}/deliveries", tags=["rechnungen"])
async def invoice_deliveries(request: Request, invoice_id: UUID, _u: CurrentUser) -> list[dict[str, Any]]:
    return await run(request, lambda conn: docs.list_deliveries(conn, invoice_id))


# ───────────────────────── Logo ─────────────────────────

@router.get("/company-profile/logo", tags=["firma"], response_class=Response)
async def get_logo(request: Request, _u: CurrentUser) -> Response:
    logo = await run(request, lambda conn: docs.active_logo(conn, tenant()))
    if logo is None:
        raise HTTPException(status_code=404, detail="Kein Logo hinterlegt")
    content, mime = logo
    # CSP verhindert, dass ein (geprüftes) SVG beim direkten Öffnen Skripte ausführt
    return Response(content, media_type=mime, headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    })


@router.post("/company-profile/logo", tags=["firma"], status_code=201)
async def upload_logo(request: Request, user: CurrentUser, file: Annotated[UploadFile, File()]) -> dict[str, str]:
    content = await file.read(docs.MAX_LOGO_BYTES + 1)
    mime = await run(request, lambda conn: docs.set_logo(conn, tenant(), content, user.email))
    return {"mime_type": mime}


@router.delete("/company-profile/logo", tags=["firma"], status_code=204)
async def delete_logo(request: Request, user: CurrentUser) -> Response:
    await run(request, lambda conn: docs.remove_logo(conn, tenant(), user.email))
    return Response(status_code=204)
