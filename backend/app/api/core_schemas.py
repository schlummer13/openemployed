"""Ein- und Ausgabemodelle für Firmendaten, Kunden, Produkte und Rechnungen."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.money import MoneyOut, StrictDecimal

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PLZ_RE = re.compile(r"^[0-9]{5}$")
_VAT_RE = re.compile(r"^DE[0-9]{9}$")

LegalForm = Literal["UG (haftungsbeschränkt)", "GmbH"]
CustomerType = Literal["b2b", "b2c"]
UnitCode = Literal["C62", "HUR", "MIN", "DAY", "WEE", "MON", "ANN", "LS", "KMT"]
OutTaxCase = Literal["OUT_DE_19", "OUT_DE_7", "OUT_EU_B2B_SERVICE", "OUT_EU_B2B_GOODS", "OUT_DRITTLAND_SERVICE"]
Billing = Literal["one_time", "monthly", "yearly", "hourly"]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _opt(v: str | None) -> str | None:
    return v or None


def iban_valid(iban: str) -> bool:
    """IBAN-Prüfsumme (ISO 13616, mod 97)."""
    compact = iban.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


# ───────────────────────── Firmendaten ─────────────────────────

class CompanyProfileIn(In):
    valid_from: dt.date
    legal_name: str = Field(min_length=1, max_length=200)
    legal_form: LegalForm
    street: str = Field(min_length=1, max_length=200)
    postal_code: str
    city: str = Field(min_length=1, max_length=100)
    register_court: str | None = Field(default=None, max_length=100)
    register_number: str | None = Field(default=None, max_length=50)
    managing_directors: list[str] = Field(min_length=1, max_length=10)
    tax_number: str | None = Field(default=None, max_length=30)
    vat_id: str | None = None
    iban: str | None = None
    bic: str | None = Field(default=None, max_length=11)
    bank_name: str | None = Field(default=None, max_length=100)
    email: str | None = None
    phone: str | None = Field(default=None, max_length=50)
    website: str | None = Field(default=None, max_length=200)

    _empty_to_none = field_validator(
        "register_court", "register_number", "tax_number", "vat_id", "iban", "bic", "bank_name", "email", "phone",
        "website", mode="before",
    )(_opt)

    @field_validator("postal_code")
    @classmethod
    def _plz(cls, v: str) -> str:
        if not _PLZ_RE.fullmatch(v):
            raise ValueError("Postleitzahl: 5 Ziffern")
        return v

    @field_validator("vat_id")
    @classmethod
    def _vat(cls, v: str | None) -> str | None:
        if v is not None and not _VAT_RE.fullmatch(v.replace(" ", "").upper()):
            raise ValueError("USt-IdNr im Format DE123456789")
        return v.replace(" ", "").upper() if v else None

    @field_validator("iban")
    @classmethod
    def _iban(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not iban_valid(v):
            raise ValueError("IBAN ungültig (Prüfsumme)")
        return v.replace(" ", "").upper()

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v is not None and not _EMAIL_RE.fullmatch(v):
            raise ValueError("E-Mail-Adresse ungültig")
        return v.lower() if v else None

    @field_validator("managing_directors")
    @classmethod
    def _directors(cls, v: list[str]) -> list[str]:
        cleaned = [d.strip() for d in v if d.strip()]
        if not cleaned or any(len(d) > 100 for d in cleaned):
            raise ValueError("Mindestens ein Geschäftsführer (max. 100 Zeichen je Name)")
        return cleaned

    @model_validator(mode="after")
    def _legal_form_in_name(self) -> CompanyProfileIn:
        if self.legal_form not in self.legal_name:
            raise ValueError(f"Der Firmenname muss den Zusatz „{self.legal_form}“ enthalten")
        return self


class CompanyProfileOut(BaseModel):
    id: UUID
    valid_from: dt.date
    legal_name: str
    legal_form: str
    street: str
    postal_code: str
    city: str
    country_code: str
    register_court: str | None
    register_number: str | None
    managing_directors: list[str]
    tax_number: str | None
    vat_id: str | None
    iban: str | None
    bic: str | None
    bank_name: str | None
    email: str | None
    phone: str | None
    website: str | None
    missing_for_invoices: list[str] = []


# ───────────────────────── Kunden ─────────────────────────

class CustomerIn(In):
    customer_type: CustomerType
    company_name: str | None = Field(default=None, max_length=200)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    email: str
    phone: str | None = Field(default=None, max_length=50)
    street: str = Field(min_length=1, max_length=200)
    postal_code: str
    city: str = Field(min_length=1, max_length=100)
    country_code: str = Field(default="DE", pattern=r"^[A-Z]{2}$")
    vat_id: str | None = None
    buyer_reference: str | None = Field(default=None, max_length=100)
    payment_terms_days: int = Field(default=14, ge=0, le=120)
    notes: str | None = Field(default=None, max_length=5000)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    einvoice_format: Literal["facturx", "xrechnung"] = "facturx"
    peppol_id: str | None = Field(default=None, max_length=60)

    _empty_to_none = field_validator(
        "company_name", "first_name", "last_name", "phone", "vat_id", "buyer_reference", "notes", "peppol_id", mode="before"
    )(_opt)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not _EMAIL_RE.fullmatch(v):
            raise ValueError("E-Mail-Adresse ungültig")
        return v.lower()

    @model_validator(mode="after")
    def _by_type(self) -> CustomerIn:
        from app.domain.international import CURRENCIES, vat_id_problem
        from app.einvoice.xrechnung import customer_problems

        if self.country_code == "DE" and not _PLZ_RE.fullmatch(self.postal_code):
            raise ValueError("Postleitzahl: 5 Ziffern")
        if not 1 <= len(self.postal_code.strip()) <= 12:
            raise ValueError("Postleitzahl ungültig")
        if self.vat_id:
            self.vat_id = self.vat_id.replace(" ", "").upper()
            problem = vat_id_problem(self.vat_id, self.country_code)
            if problem:
                raise ValueError(problem)
        if self.currency not in CURRENCIES:
            raise ValueError(f"Währung nicht unterstützt (möglich: {', '.join(CURRENCIES)})")
        problems = customer_problems(self.model_dump())
        if problems:
            raise ValueError("; ".join(problems))
        if self.customer_type == "b2b" and not self.company_name:
            raise ValueError("Firmenkunde: Firmenname ist Pflicht")
        if self.customer_type == "b2c" and not self.last_name:
            raise ValueError("Privatkunde: Nachname ist Pflicht")
        return self


class CustomerOut(BaseModel):
    id: UUID
    number: str
    customer_type: CustomerType
    display_name: str
    company_name: str | None
    first_name: str | None
    last_name: str | None
    email: str
    phone: str | None
    street: str
    postal_code: str
    city: str
    country_code: str
    vat_id: str | None
    buyer_reference: str | None
    payment_terms_days: int
    notes: str | None
    archived: bool
    currency: str = "EUR"
    einvoice_format: str = "facturx"
    peppol_id: str | None = None


# ───────────────────────── Produkte ─────────────────────────

class ProductIn(In):
    sku: str = Field(pattern=r"^[A-Za-z0-9._-]{1,40}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    unit_code: UnitCode = "MON"
    price: StrictDecimal
    price_basis: Literal["net", "gross"] = "net"
    tax_case: OutTaxCase = "OUT_DE_19"
    billing: Billing = "monthly"
    active: bool = True

    _empty_to_none = field_validator("description", mode="before")(_opt)

    @field_validator("price")
    @classmethod
    def _price(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Preis darf nicht negativ sein")
        return v


class ProductOut(BaseModel):
    id: UUID
    sku: str
    name: str
    description: str | None
    unit_code: str
    price: MoneyOut
    price_basis: Literal["net", "gross"]
    tax_case: str
    billing: Billing
    active: bool
    warnings: list[str] = []


# ───────────────────────── Rechnungen ─────────────────────────

class InvoiceLineIn(In):
    product_id: UUID | None = None
    description: str = Field(min_length=1, max_length=1000)
    quantity: StrictDecimal
    unit_code: UnitCode = "C62"
    unit_price: StrictDecimal
    tax_case: OutTaxCase = "OUT_DE_19"


class InvoiceDraftIn(In):
    customer_id: UUID
    service_date_from: dt.date | None = None
    service_date_to: dt.date | None = None
    due_days: int | None = Field(default=None, ge=0, le=120)
    intro_text: str | None = Field(default=None, max_length=2000)
    closing_text: str | None = Field(default=None, max_length=2000)
    lines: list[InvoiceLineIn] = Field(min_length=1, max_length=500)

    _empty_to_none = field_validator("intro_text", "closing_text", mode="before")(_opt)

    @model_validator(mode="after")
    def _period(self) -> InvoiceDraftIn:
        if self.service_date_from and self.service_date_to and self.service_date_to < self.service_date_from:
            raise ValueError("Leistungszeitraum: Ende liegt vor dem Beginn")
        return self


class CancelIn(In):
    reason: str = Field(min_length=3, max_length=500)


class InvoiceLineOut(BaseModel):
    position: int
    product_id: UUID | None
    description: str
    quantity: MoneyOut
    unit_code: str
    unit_price: MoneyOut
    tax_case: str


class InvoiceSummaryOut(BaseModel):
    id: UUID
    kind: Literal["invoice", "cancellation"]
    status: Literal["draft", "issued"]
    number: str | None
    customer_id: UUID
    customer_name: str
    issue_date: dt.date | None
    due_date: dt.date | None
    total_gross: MoneyOut | None
    cancelled_by: UUID | None
    cancels_invoice_id: UUID | None
    created_at: dt.datetime
    vat_period: str | None = None


class InvoiceOut(InvoiceSummaryOut):
    price_mode: Literal["net", "gross"]
    service_date_from: dt.date | None
    service_date_to: dt.date | None
    due_days: int
    intro_text: str | None
    closing_text: str | None
    cancellation_reason: str | None
    total_net: MoneyOut | None
    total_tax: MoneyOut | None
    lines: list[InvoiceLineOut]
    snapshot: dict[str, object] | None
    ledger_record_id: UUID | None
    ledger_hash: str | None
    issued_by: str | None
    issued_at: dt.datetime | None
