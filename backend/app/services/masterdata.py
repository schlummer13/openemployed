"""Firmendaten (versioniert), Kunden und Produkte."""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.core_schemas import CompanyProfileIn, CustomerIn, ProductIn
from app.domain.invoice_calc import is_gross_price_representable
from app.domain.tax_cases import get_tax_case
from app.services import audit


class NotFoundError(LookupError):
    pass


class ConflictError(RuntimeError):
    pass


# ───────────────────────── Firmendaten ─────────────────────────

COMPANY_COLUMNS = (
    "id, valid_from, legal_name, legal_form, street, postal_code, city, country_code, register_court, "
    "register_number, managing_directors, tax_number, vat_id, iban, bic, bank_name, email, phone, website"
)


def missing_invoice_fields(profile: RowMapping | dict[str, Any]) -> list[str]:
    """Pflichtangaben für Rechnungen (§ 14 Abs. 4 UStG) und Geschäftsbriefe (§ 35a GmbHG)."""
    missing: list[str] = []
    if not profile.get("tax_number") and not profile.get("vat_id"):
        missing.append("Steuernummer oder USt-IdNr")
    if not profile.get("register_court") or not profile.get("register_number"):
        missing.append("Registergericht und Handelsregisternummer")
    if not profile.get("managing_directors"):
        missing.append("Geschäftsführer")
    if not profile.get("iban"):
        missing.append("IBAN (Zahlungsinformationen)")
    return missing


async def current_company_profile(conn: AsyncConnection, tenant_id: UUID, on: dt.date) -> RowMapping | None:
    result = await conn.execute(
        text(
            f"SELECT {COMPANY_COLUMNS} FROM company_profiles WHERE tenant_id = :t AND valid_from <= :on "
            "ORDER BY valid_from DESC LIMIT 1"
        ),
        {"t": tenant_id, "on": on},
    )
    return result.mappings().one_or_none()


async def create_company_profile(conn: AsyncConnection, tenant_id: UUID, data: CompanyProfileIn, actor: str) -> RowMapping:
    exists = await conn.scalar(
        text("SELECT 1 FROM company_profiles WHERE tenant_id = :t AND valid_from = :d"),
        {"t": tenant_id, "d": data.valid_from},
    )
    if exists:
        raise ConflictError(f"Für den {data.valid_from:%d.%m.%Y} gibt es bereits eine Version – anderes Datum wählen")
    values = data.model_dump()
    row = (
        await conn.execute(
            text(
                f"""
                INSERT INTO company_profiles (tenant_id, valid_from, legal_name, legal_form, street, postal_code, city,
                    register_court, register_number, managing_directors, tax_number, vat_id, iban, bic, bank_name,
                    email, phone, website, created_by)
                VALUES (:tenant_id, :valid_from, :legal_name, :legal_form, :street, :postal_code, :city,
                    :register_court, :register_number, :managing_directors, :tax_number, :vat_id, :iban, :bic,
                    :bank_name, :email, :phone, :website, :actor)
                RETURNING {COMPANY_COLUMNS}
                """
            ),
            {**values, "tenant_id": tenant_id, "actor": actor},
        )
    ).mappings().one()
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="company_profile.created",
                       entity_type="company_profile", entity_id=row["id"], after=values)
    return row


# ───────────────────────── Kunden ─────────────────────────

CUSTOMER_COLUMNS = (
    "id, number, customer_type, company_name, first_name, last_name, email, phone, street, postal_code, city, "
    "country_code, vat_id, buyer_reference, payment_terms_days, notes, archived_at, currency, einvoice_format, peppol_id"
)


def customer_display_name(row: RowMapping | dict[str, Any]) -> str:
    if row["customer_type"] == "b2b":
        return str(row["company_name"])
    return " ".join(p for p in (row.get("first_name"), row.get("last_name")) if p)


def customer_out(row: RowMapping) -> dict[str, Any]:
    data = dict(row)
    data["display_name"] = customer_display_name(row)
    data["archived"] = data.pop("archived_at") is not None
    return data


async def list_customers(conn: AsyncConnection, tenant_id: UUID, include_archived: bool) -> list[RowMapping]:
    where = "" if include_archived else "AND archived_at IS NULL"
    result = await conn.execute(
        text(f"SELECT {CUSTOMER_COLUMNS} FROM customers WHERE tenant_id = :t {where} "
             "ORDER BY coalesce(company_name, last_name), number"),
        {"t": tenant_id},
    )
    return list(result.mappings())


async def get_customer(conn: AsyncConnection, tenant_id: UUID, customer_id: UUID) -> RowMapping:
    row = (
        await conn.execute(
            text(f"SELECT {CUSTOMER_COLUMNS} FROM customers WHERE tenant_id = :t AND id = :id"),
            {"t": tenant_id, "id": customer_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Kunde nicht gefunden")
    return row


def _customer_values(data: CustomerIn) -> dict[str, Any]:
    values = data.model_dump()
    # Typfremde Felder leeren, damit keine widersprüchlichen Daten entstehen
    if data.customer_type == "b2c":
        values["vat_id"] = None
    return values


async def create_customer(conn: AsyncConnection, tenant_id: UUID, data: CustomerIn, actor: str) -> RowMapping:
    values = _customer_values(data)
    row = (
        await conn.execute(
            text(
                f"""
                INSERT INTO customers (tenant_id, customer_type, company_name, first_name, last_name, email, phone,
                    street, postal_code, city, country_code, vat_id, buyer_reference, payment_terms_days, notes, currency,
                    einvoice_format, peppol_id)
                VALUES (:tenant_id, :customer_type, :company_name, :first_name, :last_name, :email, :phone,
                    :street, :postal_code, :city, :country_code, :vat_id, :buyer_reference, :payment_terms_days, :notes, :currency,
                    :einvoice_format, :peppol_id)
                RETURNING {CUSTOMER_COLUMNS}
                """
            ),
            {**values, "tenant_id": tenant_id},
        )
    ).mappings().one()
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="customer.created", entity_type="customer",
                       entity_id=row["id"], after=values)
    return row


async def update_customer(conn: AsyncConnection, tenant_id: UUID, customer_id: UUID, data: CustomerIn,
                          actor: str) -> RowMapping:
    before = await get_customer(conn, tenant_id, customer_id)
    values = _customer_values(data)
    row = (
        await conn.execute(
            text(
                f"""
                UPDATE customers SET customer_type = :customer_type, company_name = :company_name,
                    first_name = :first_name, last_name = :last_name, email = :email, phone = :phone,
                    street = :street, postal_code = :postal_code, city = :city, country_code = :country_code, vat_id = :vat_id,
                    buyer_reference = :buyer_reference, payment_terms_days = :payment_terms_days, notes = :notes,
                    currency = :currency, einvoice_format = :einvoice_format, peppol_id = :peppol_id,
                    updated_at = now()
                WHERE tenant_id = :tenant_id AND id = :id
                RETURNING {CUSTOMER_COLUMNS}
                """
            ),
            {**values, "tenant_id": tenant_id, "id": customer_id},
        )
    ).mappings().one()
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="customer.updated", entity_type="customer",
                       entity_id=customer_id, before=dict(before), after=values)
    return row


async def set_customer_archived(conn: AsyncConnection, tenant_id: UUID, customer_id: UUID, archived: bool,
                                actor: str) -> RowMapping:
    await get_customer(conn, tenant_id, customer_id)
    row = (
        await conn.execute(
            text(
                f"UPDATE customers SET archived_at = CASE WHEN :a THEN now() ELSE NULL END, updated_at = now() "
                f"WHERE tenant_id = :t AND id = :id RETURNING {CUSTOMER_COLUMNS}"
            ),
            {"a": archived, "t": tenant_id, "id": customer_id},
        )
    ).mappings().one()
    await audit.record(conn, tenant_id=tenant_id, actor=actor,
                       action="customer.archived" if archived else "customer.restored",
                       entity_type="customer", entity_id=customer_id)
    return row


# ───────────────────────── Produkte ─────────────────────────

PRODUCT_COLUMNS = "id, sku, name, description, unit_code, price, price_basis, tax_case, billing, active"


def product_out(row: RowMapping) -> dict[str, Any]:
    data = dict(row)
    warnings: list[str] = []
    if row["price_basis"] == "gross":
        rate = get_tax_case(row["tax_case"]).rate
        if not is_gross_price_representable(row["price"], rate):
            warnings.append(
                f"Der Bruttopreis {format(row['price'], '.2f').replace('.', ',')} € ist bei {rate} % nicht exakt "
                "darstellbar – "
                "Rechnungen fallen 1 Cent niedriger aus. Empfehlung: Preis um 1 Cent anpassen."
            )
    data["warnings"] = warnings
    return data


async def list_products(conn: AsyncConnection, tenant_id: UUID, include_inactive: bool) -> list[RowMapping]:
    where = "" if include_inactive else "AND active"
    result = await conn.execute(
        text(f"SELECT {PRODUCT_COLUMNS} FROM products WHERE tenant_id = :t {where} ORDER BY name"),
        {"t": tenant_id},
    )
    return list(result.mappings())


async def get_product(conn: AsyncConnection, tenant_id: UUID, product_id: UUID) -> RowMapping:
    row = (
        await conn.execute(
            text(f"SELECT {PRODUCT_COLUMNS} FROM products WHERE tenant_id = :t AND id = :id"),
            {"t": tenant_id, "id": product_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Produkt nicht gefunden")
    return row


async def _sku_taken(conn: AsyncConnection, tenant_id: UUID, sku: str, except_id: UUID | None) -> bool:
    found = await conn.scalar(
        text("SELECT 1 FROM products WHERE tenant_id = :t AND sku = :sku "
             "AND (CAST(:ex AS uuid) IS NULL OR id <> CAST(:ex AS uuid))"),
        {"t": tenant_id, "sku": sku, "ex": except_id},
    )
    return found is not None


async def create_product(conn: AsyncConnection, tenant_id: UUID, data: ProductIn, actor: str) -> RowMapping:
    if await _sku_taken(conn, tenant_id, data.sku, None):
        raise ConflictError(f"Artikelnummer {data.sku} ist bereits vergeben")
    values = data.model_dump()
    row = (
        await conn.execute(
            text(
                f"""
                INSERT INTO products (tenant_id, sku, name, description, unit_code, price, price_basis, tax_case,
                    billing, active)
                VALUES (:tenant_id, :sku, :name, :description, :unit_code, :price, :price_basis, :tax_case,
                    :billing, :active)
                RETURNING {PRODUCT_COLUMNS}
                """
            ),
            {**values, "tenant_id": tenant_id},
        )
    ).mappings().one()
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="product.created", entity_type="product",
                       entity_id=row["id"], after=values)
    return row


async def update_product(conn: AsyncConnection, tenant_id: UUID, product_id: UUID, data: ProductIn,
                         actor: str) -> RowMapping:
    before = await get_product(conn, tenant_id, product_id)
    if await _sku_taken(conn, tenant_id, data.sku, product_id):
        raise ConflictError(f"Artikelnummer {data.sku} ist bereits vergeben")
    values = data.model_dump()
    row = (
        await conn.execute(
            text(
                f"""
                UPDATE products SET sku = :sku, name = :name, description = :description, unit_code = :unit_code,
                    price = :price, price_basis = :price_basis, tax_case = :tax_case, billing = :billing,
                    active = :active, updated_at = now()
                WHERE tenant_id = :tenant_id AND id = :id
                RETURNING {PRODUCT_COLUMNS}
                """
            ),
            {**values, "tenant_id": tenant_id, "id": product_id},
        )
    ).mappings().one()
    # Bereits ausgestellte Rechnungen sind nicht betroffen (Positionen sind dort festgeschrieben)
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="product.updated", entity_type="product",
                       entity_id=product_id, before=dict(before), after=values)
    return row
