"""Fachliche Ereignisse (Ereignis-Bus): entstehen zentral aus dem Audit-Log und speisen Webhooks und Workflows.

Ereignisse werden in derselben Transaktion vorgemerkt wie die fachliche Änderung – rollt diese zurück, gibt es auch kein
Ereignis. Zugestellt wird asynchron durch den Automatisierungs-Takt (at-least-once, Empfänger prüfen die Delivery-ID).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection

# Audit-Aktion → öffentlicher Ereignisname (stabile Schnittstelle)
EVENTS: dict[str, tuple[str, str]] = {
    "invoice.issued": ("invoice.issued", "Rechnung ausgestellt"),
    "invoice.cancellation": ("invoice.cancelled", "Rechnung storniert"),
    "invoice.sent": ("invoice.sent", "Rechnung versendet"),
    "payment.recorded": ("invoice.payment_received", "Zahlung zu einer Rechnung erfasst"),
    "dunning.sent": ("dunning.sent", "Mahnung versendet"),
    "receipt.booked": ("receipt.booked", "Beleg gebucht"),
    "receipt_file.uploaded": ("receipt.uploaded", "Belegdatei eingegangen"),
    "offer.sent": ("offer.sent", "Angebot versendet"),
    "offer.accepted": ("offer.accepted", "Angebot angenommen"),
    "offer.declined": ("offer.declined", "Angebot abgelehnt"),
    "lead.created": ("lead.created", "Lead angelegt"),
    "lead.moved": ("lead.stage_changed", "Lead in andere Phase verschoben"),
    "customer.created": ("customer.created", "Kunde angelegt"),
    "customer.updated": ("customer.updated", "Kunde geändert"),
    "contract.activated": ("contract.activated", "Vertrag aktiviert"),
    "contract.terminated": ("contract.terminated", "Vertrag gekündigt"),
    "signature.completed": ("signature.completed", "Dokument signiert"),
    "payroll.approved": ("payroll.approved", "Lohnlauf freigegeben"),
    "employee.created": ("employee.created", "Mitarbeiter angelegt"),
    "project.created": ("project.created", "Projekt angelegt"),
    "time.saved": ("time.saved", "Zeit erfasst"),
    "travel.booked": ("travel.booked", "Reisekosten gebucht"),
    "bank.imported": ("bank.imported", "Kontoauszug importiert"),
    "month.closed": ("month.closed", "Monat festgeschrieben"),
}
PUBLIC = {v[0]: v[1] for v in EVENTS.values()}

async def _enrich(conn: AsyncConnection, entity_type: str, entity_id: Any) -> dict[str, Any]:
    """Wichtige Felder des betroffenen Datensatzes ergänzen (Beträge, Kunde, Fälligkeit …) – für Bedingungen und Platzhalter."""
    from sqlalchemy import text

    if entity_id is None:
        return {}
    queries = {
        "invoice": "SELECT i.number, i.kind, i.issue_date, i.due_date, i.total_net, i.total_tax, i.total_gross, i.currency, "
                   "COALESCE(c.company_name, trim(coalesce(c.first_name,'') || ' ' || coalesce(c.last_name,''))) AS customer_name, "
                   "c.email AS customer_email, c.number AS customer_number, c.country_code AS customer_country "
                   "FROM invoices i JOIN customers c ON c.id = i.customer_id WHERE i.id = CAST(:id AS uuid)",
        "receipt": "SELECT r.receipt_date, r.invoice_number, r.description, r.net_amount, r.gross_amount, r.category, r.expense_account, "
                   "s.name AS supplier_name FROM receipts r JOIN suppliers s ON s.id = r.supplier_id WHERE r.id = CAST(:id AS uuid)",
        "offer": "SELECT o.number, o.title, o.total_net, o.total_gross, o.valid_until, "
                 "COALESCE(c.company_name, trim(coalesce(c.first_name,'') || ' ' || coalesce(c.last_name,''))) AS customer_name "
                 "FROM offers o JOIN customers c ON c.id = o.customer_id WHERE o.id = CAST(:id AS uuid)",
        "customer": "SELECT number, customer_type, company_name, first_name, last_name, email, city, country_code FROM customers "
                    "WHERE id = CAST(:id AS uuid)",
    }
    sql = queries.get(entity_type)
    if sql is None:
        return {}
    try:
        row = (await conn.execute(text(sql), {"id": str(entity_id)})).mappings().one_or_none()
    except Exception:  # z. B. keine UUID – Ereignis ohne Zusatzdaten
        return {}
    return json.loads(json.dumps(dict(row), default=str)) if row else {}


Listener = Callable[[AsyncConnection, UUID, str, dict[str, Any]], Awaitable[None]]
_listeners: list[Listener] = []


def listen(fn: Listener) -> Listener:
    _listeners.append(fn)
    return fn


async def from_audit(conn: AsyncConnection, *, tenant_id: UUID, actor: str, action: str, entity_type: str, entity_id: Any,
                     after: dict[str, Any] | None) -> None:
    mapped = EVENTS.get(action)
    if mapped is None or not _listeners:
        return
    data = json.loads(json.dumps(after or {}, default=str))
    data.update(await _enrich(conn, entity_type, entity_id))
    payload = {"event": mapped[0], "occurred_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "entity_type": entity_type,
               "entity_id": str(entity_id) if entity_id is not None else None, "actor": actor, "data": data}
    for fn in _listeners:
        await fn(conn, tenant_id, mapped[0], payload)


# Grundfelder je Ereignis (für die Feldauswahl im Workflow-Editor, solange noch keine echten Daten vorliegen)
FIELD_HINTS: dict[str, tuple[str, ...]] = {
    "invoice.issued": ("number", "total_net", "total_tax", "total_gross", "currency", "due_date", "customer_name", "customer_email",
                       "customer_country"),
    "invoice.sent": ("number", "total_gross", "customer_name", "customer_email"),
    "offer.sent": ("number", "title", "total_net", "customer_name"),
    "invoice.payment_received": ("amount", "paid_on", "method", "reference"),
    "receipt.booked": ("supplier_name", "gross_amount", "net_amount", "receipt_date", "category", "invoice_number", "expense_account"),
    "customer.created": ("company_name", "first_name", "last_name", "email", "customer_type", "city", "country_code"),
    "offer.accepted": ("number", "title", "total_net", "total_gross", "customer_name"),
    "lead.created": ("title", "company", "value", "email"),
    "lead.stage_changed": ("stage", "title", "value"),
    "payroll.approved": ("period", "journal_entry_id"),
    "month.closed": ("confirmed",),
}
