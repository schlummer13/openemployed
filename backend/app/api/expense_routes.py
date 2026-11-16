"""API: Lieferanten und Belege (Ausgaben). Anmeldung erforderlich, keine Rollen."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.core_routes import run, tenant
from app.auth.deps import CurrentUser
from app.core.money import MoneyOut, StrictDecimal
from app.domain.expenses import CATEGORIES
from app.domain.tax_cases import Direction, active_cases
from app.services import extraction as ex
from app.services import receipts as rc
from app.services.invoices import today_berlin
from app.services.ledger_client import LedgerClient, to_ledger_payload
from app.services.masterdata import NotFoundError

router = APIRouter(prefix="/api", tags=["ausgaben"])

InTaxCase = Literal["IN_DE_19", "IN_DE_7", "IN_13B_EU_SERVICE", "IN_13B_DRITTLAND_SERVICE", "IN_IG_ERWERB",
                    "IN_EINFUHR", "IN_OHNE_VST", "IN_BEWIRTUNG", "IN_NICHT_ABZIEHBAR"]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SupplierIn(In):
    name: str = Field(min_length=1, max_length=200)
    country_code: str = Field(default="DE", pattern=r"^[A-Z]{2}$")
    vat_id: str | None = Field(default=None, max_length=14)
    default_tax_case: InTaxCase = "IN_DE_19"
    default_category: str = "other"
    default_account: str | None = Field(default=None, pattern=r"^[0-9]{4}$", description="Standardkonto (Aufwand/Anlage)")
    notes: str | None = Field(default=None, max_length=2000)
    iban: str | None = Field(default=None, max_length=42)
    bic: str | None = Field(default=None, max_length=11)

    @field_validator("iban")
    @classmethod
    def _iban(cls, v: str | None) -> str | None:
        from app.api.core_schemas import iban_valid

        if v and not iban_valid(v):
            raise ValueError("IBAN ungültig (Prüfziffer)")
        return v

    @field_validator("vat_id", "notes", "default_account", "iban", "bic", mode="before")
    @classmethod
    def _empty(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("vat_id")
    @classmethod
    def _vat(cls, v: str | None) -> str | None:
        if v is None:
            return None
        compact = v.replace(" ", "").upper()
        if not (2 < len(compact) <= 14 and compact[:2].isalpha()):
            raise ValueError("USt-IdNr mit Länderkennung, z. B. IE1234567X")
        return compact


class SupplierOut(BaseModel):
    id: UUID
    name: str
    country_code: str
    vat_id: str | None
    default_tax_case: str
    default_category: str
    default_account: str | None = None
    needs_review: bool = False
    notes: str | None
    archived: bool


class ReceiptIn(In):
    supplier_id: UUID
    file_id: UUID | None = None
    receipt_date: dt.date
    service_date: dt.date | None = None
    invoice_number: str | None = Field(default=None, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    category: str
    tax_case: InTaxCase
    net_amount: StrictDecimal
    tax_amount: StrictDecimal = Decimal("0")
    paid_on: dt.date | None = None
    expense_account: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    remember_rule: bool = Field(default=False, description="Konto, Kategorie und Steuerfall als Regel des Lieferanten speichern")

    @field_validator("invoice_number", "expense_account", mode="before")
    @classmethod
    def _empty(cls, v: str | None) -> str | None:
        return v or None

    @model_validator(mode="after")
    def _dates(self) -> ReceiptIn:
        if self.receipt_date > dt.date.today() + dt.timedelta(days=1):  # noqa: DTZ011
            raise ValueError("Belegdatum liegt in der Zukunft")
        return self


class ReceiptOut(BaseModel):
    id: UUID
    status: Literal["draft", "booked"]
    kind: Literal["expense", "reversal"]
    supplier_id: UUID
    supplier_name: str
    file_id: UUID | None
    receipt_date: dt.date
    service_date: dt.date | None
    invoice_number: str | None
    description: str
    category: str
    tax_case: str
    net_amount: MoneyOut
    tax_amount: MoneyOut
    gross_amount: MoneyOut
    rc_tax_amount: MoneyOut
    input_tax_amount: MoneyOut
    expense_amount: MoneyOut
    deductible_amount: MoneyOut
    paid_on: dt.date | None
    reverses_id: UUID | None
    reversed_by: UUID | None
    reversal_reason: str | None
    ledger_hash: str | None
    booked_at: dt.datetime | None
    booked_by: str | None
    vat_period: str | None = None
    expense_account: str | None = None
    account: str | None = None
    account_name: str | None = None
    source: str | None = None
    supplier_needs_review: bool = False
    ready: bool = False
    hints: list[str] = []
    blockers: list[str] = []
    warnings: list[str] = []


class BookReadyIn(In):
    receipt_ids: list[UUID] = Field(min_length=1, max_length=200)


class ReverseIn(In):
    reason: str = Field(min_length=3, max_length=500)


@router.get("/expense-meta")
async def expense_meta(_u: CurrentUser) -> dict[str, Any]:
    return {
        "categories": [{"code": c.code, "label": c.label, "skr04": c.skr04} for c in CATEGORIES],
        "tax_cases": [{"code": c.code, "label": c.label, "rate": str(c.rate), "reverse_charge": c.reverse_charge}
                      for c in active_cases(Direction.INCOMING)],
    }


@router.get("/suppliers", response_model=list[SupplierOut])
async def list_suppliers(request: Request, _u: CurrentUser) -> list[dict[str, Any]]:
    async def op(conn: AsyncConnection) -> list[dict[str, Any]]:
        return [rc.supplier_out(r) for r in await rc.list_suppliers(conn, tenant())]
    return await run(request, op)


@router.post("/suppliers", response_model=SupplierOut, status_code=201)
async def create_supplier(request: Request, data: SupplierIn, user: CurrentUser) -> dict[str, Any]:
    async def op(conn: AsyncConnection) -> dict[str, Any]:
        return rc.supplier_out(await rc.save_supplier(conn, tenant(), data.model_dump(), user.email))
    return await run(request, op)


@router.put("/suppliers/{supplier_id}", response_model=SupplierOut)
async def update_supplier(request: Request, supplier_id: UUID, data: SupplierIn, user: CurrentUser) -> dict[str, Any]:
    async def op(conn: AsyncConnection) -> dict[str, Any]:
        return rc.supplier_out(await rc.save_supplier(conn, tenant(), data.model_dump(), user.email, supplier_id))
    return await run(request, op)


@router.post("/receipt-files", status_code=201)
async def upload_receipt_file(request: Request, user: CurrentUser, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    content = await file.read(rc.MAX_FILE_BYTES + 1)
    return await run(request, lambda conn: rc.store_file(conn, tenant(), content, file.filename or "beleg", user.email))


@router.post("/receipt-files/{file_id}/capture")
async def capture_receipt(request: Request, file_id: UUID, user: CurrentUser) -> Any:
    """Handy-Erfassung: Beleg lesen und als Entwurf anlegen – wie das Belegpostfach (bekannte Lieferanten nach ihrer Regel)."""
    from app.services import automation as au
    from app.services.masterdata import current_company_profile

    ctx = au.Context(engine=request.app.state.engine, ledger=request.app.state.ledger, mailer=None,
                     extractor=getattr(request.app.state, "extractor", None), tenant_id=tenant(), actor=user.email)

    async def fn(conn: AsyncConnection) -> dict[str, Any]:
        f = await rc.get_file(conn, tenant(), file_id)
        if await conn.scalar(text("SELECT 1 FROM receipts WHERE file_id = :f"), {"f": file_id}):
            return {"draft": False, "notes": ["Diese Datei ist bereits einem Beleg zugeordnet."]}
        profile = await current_company_profile(conn, tenant(), today_berlin())
        notes: list[str] = []
        ok = await au._draft_from_file(conn, ctx, file_id, profile["legal_name"] if profile else "", notes, f["file_name"], source="mobile")
        receipt = None
        if ok:
            rid = await conn.scalar(text("SELECT id FROM receipts WHERE file_id = :f"), {"f": file_id})
            receipt = await rc.get_receipt(conn, tenant(), rid)
        return {"draft": ok, "notes": notes, "receipt": receipt}
    return jsonable_encoder(to_ledger_payload(await run(request, fn)))


@router.get("/receipt-files/{file_id}", response_class=Response)
async def get_receipt_file(request: Request, file_id: UUID, _u: CurrentUser) -> Response:
    row = await run(request, lambda conn: rc.get_file(conn, tenant(), file_id))
    inline = row["mime_type"] in ("application/pdf", "image/png", "image/jpeg")
    return Response(bytes(row["content"]), media_type=row["mime_type"], headers={
        "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{row["file_name"]}"',
        "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; plugin-types application/pdf",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    })


@router.get("/receipt-files/{file_id}/preview", response_class=Response)
async def preview_receipt_file(request: Request, file_id: UUID, _u: CurrentUser, page: int = 1) -> Response:
    """Seite als Bild für die Prüfansicht (PDF wird serverseitig gerendert, keine eingebetteten Dokumente nötig)."""
    row = await run(request, lambda conn: rc.get_file(conn, tenant(), file_id))
    try:
        content, mime, pages = rc.render_preview(bytes(row["content"]), row["mime_type"], page)
    except rc.ReceiptError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content, media_type=mime, headers={"X-Page-Count": str(pages), "Cache-Control": "private, max-age=3600",
                                                       "X-Content-Type-Options": "nosniff"})


@router.post("/receipt-files/{file_id}/extract")
async def extract_receipt_file(request: Request, file_id: UUID, user: CurrentUser) -> Any:
    """Erkennt Belegdaten (E-Rechnung ohne KI, sonst Claude). Ergebnis ist nur ein Vorschlag – keine Buchung."""
    from fastapi.encoders import jsonable_encoder

    from app.services.invoices import today_berlin
    from app.services.masterdata import current_company_profile

    extractor = getattr(request.app.state, "extractor", None)

    async def fn(conn: AsyncConnection) -> Any:
        profile = await current_company_profile(conn, tenant(), today_berlin())
        try:
            return await ex.extract_file(conn, extractor, tenant(), file_id, user.email, profile["legal_name"] if profile else "")
        except ex.ExtractionNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ex.ExtractionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return jsonable_encoder(await run(request, fn))


@router.get("/receipts", response_model=list[ReceiptOut])
async def list_receipts(request: Request, _u: CurrentUser) -> list[dict[str, Any]]:
    return await run(request, lambda conn: rc.list_receipts(conn, tenant()))


@router.get("/receipts/{receipt_id}", response_model=ReceiptOut)
async def get_receipt(request: Request, receipt_id: UUID, _u: CurrentUser) -> dict[str, Any]:
    return await run(request, lambda conn: rc.get_receipt(conn, tenant(), receipt_id))


@router.post("/receipts", response_model=ReceiptOut, status_code=201)
async def create_receipt(request: Request, data: ReceiptIn, user: CurrentUser) -> dict[str, Any]:
    return await run(request, lambda conn: rc.save_draft(conn, tenant(), data.model_dump(), user.email))


@router.put("/receipts/{receipt_id}", response_model=ReceiptOut)
async def update_receipt(request: Request, receipt_id: UUID, data: ReceiptIn, user: CurrentUser) -> dict[str, Any]:
    return await run(request, lambda conn: rc.save_draft(conn, tenant(), data.model_dump(), user.email, receipt_id))


@router.delete("/receipts/{receipt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_receipt(request: Request, receipt_id: UUID, user: CurrentUser) -> Response:
    await run(request, lambda conn: rc.delete_draft(conn, tenant(), receipt_id, user.email))
    return Response(status_code=204)


@router.get("/receipt-accounts")
async def receipt_accounts(request: Request, _u: CurrentUser) -> list[dict[str, str]]:
    accounts = await run(request, lambda conn: rc.expense_accounts(conn, tenant()))
    return [{"number": n, "name": name} for n, name in accounts.items()]


@router.post("/receipts/book-ready")
async def book_ready(request: Request, data: BookReadyIn, user: CurrentUser) -> dict[str, Any]:
    """Sammelbuchung per Klick: nur Belege, die „bereit zum Buchen“ sind; alles in einem Vorgang (alles oder nichts)."""
    ledger: LedgerClient = request.app.state.ledger
    return await run(request, lambda conn: rc.book_ready(conn, ledger, tenant(), data.receipt_ids, user.email))


@router.post("/receipts/{receipt_id}/book", response_model=ReceiptOut)
async def book_receipt(request: Request, receipt_id: UUID, user: CurrentUser) -> dict[str, Any]:
    ledger: LedgerClient = request.app.state.ledger
    return await run(request, lambda conn: rc.book_receipt(conn, ledger, tenant(), receipt_id, user.email))


@router.post("/receipts/{receipt_id}/reverse", response_model=ReceiptOut)
async def reverse_receipt(request: Request, receipt_id: UUID, data: ReverseIn, user: CurrentUser) -> dict[str, Any]:
    ledger: LedgerClient = request.app.state.ledger
    return await run(request, lambda conn: rc.reverse_receipt(conn, ledger, tenant(), receipt_id, data.reason, user.email))
