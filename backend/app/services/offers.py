"""Angebote: Entwurf → versendet → angenommen/abgelehnt (oder neue Version) → Rechnung und/oder Vertrag."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.core_schemas import InvoiceDraftIn, InvoiceLineIn
from app.domain.invoice_calc import LineInput, PriceMode, calculate_invoice
from app.services import audit
from app.services import contracts as ct
from app.services import invoices as inv
from app.services.masterdata import NotFoundError, customer_display_name, get_customer


class OfferStateError(RuntimeError):
    pass


class OfferValidationError(ValueError):
    pass


COLUMNS = (
    "o.id, o.number, o.customer_id, o.title, o.status, o.price_mode, o.valid_until, o.intro_text, o.closing_text, "
    "o.total_net, o.total_tax, o.total_gross, o.revision_of, o.sent_at, o.decided_at, o.decided_by_name, o.decision_channel, "
    "o.decision_note, o.invoice_id, o.contract_id, o.created_at, c.customer_type, c.company_name, c.first_name, c.last_name, "
    "c.email AS customer_email"
)


async def _lines(conn: AsyncConnection, offer_id: UUID) -> list[dict[str, Any]]:
    result = await conn.execute(text(
        "SELECT position, product_id, description, quantity, unit_code, unit_price, tax_case, recurring FROM offer_lines "
        "WHERE offer_id = :id ORDER BY position"), {"id": offer_id})
    return [dict(r) for r in result.mappings()]


def _decorate(row: dict[str, Any], today: dt.date) -> dict[str, Any]:
    row["customer_name"] = customer_display_name(row)
    row["expired"] = row["status"] == "sent" and row["valid_until"] < today
    return row


async def list_offers(conn: AsyncConnection, tenant_id: UUID, today: dt.date) -> list[dict[str, Any]]:
    result = await conn.execute(text(
        f"SELECT {COLUMNS} FROM offers o JOIN customers c ON c.id = o.customer_id WHERE o.tenant_id = :t "
        "ORDER BY o.created_at DESC LIMIT 500"), {"t": tenant_id})
    return [_decorate(dict(r), today) for r in result.mappings()]


async def get_offer(conn: AsyncConnection, tenant_id: UUID, offer_id: UUID, today: dt.date, *, lock: bool = False) -> dict[str, Any]:
    lock_sql = " FOR UPDATE OF o" if lock else ""
    row = (await conn.execute(text(
        f"SELECT {COLUMNS} FROM offers o JOIN customers c ON c.id = o.customer_id WHERE o.tenant_id = :t AND o.id = :id{lock_sql}"),
        {"t": tenant_id, "id": offer_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Angebot nicht gefunden")
    data = _decorate(dict(row), today)
    data["lines"] = await _lines(conn, offer_id)
    return data


def _totals(lines: list[dict[str, Any]], mode: PriceMode) -> Any:
    try:
        return calculate_invoice([LineInput(x["description"], Decimal(x["quantity"]), x["unit_code"], Decimal(x["unit_price"]),
                                            x["tax_case"]) for x in lines], mode)
    except ValueError as exc:
        raise OfferValidationError(str(exc)) from exc


async def save_draft(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str, today: dt.date,
                     offer_id: UUID | None = None, revision_of: UUID | None = None) -> dict[str, Any]:
    customer = await get_customer(conn, tenant_id, data["customer_id"])
    if customer["archived_at"] is not None:
        raise OfferValidationError("Kunde ist archiviert")
    if data["valid_until"] < today:
        raise OfferValidationError("„Gültig bis“ liegt in der Vergangenheit")
    mode = PriceMode.GROSS if customer["customer_type"] == "b2c" else PriceMode.NET
    totals = _totals(data["lines"], mode)
    params = {"t": tenant_id, "c": data["customer_id"], "title": data["title"], "pm": mode.value, "vu": data["valid_until"],
              "intro": data.get("intro_text"), "closing": data.get("closing_text"), "net": totals.total_net,
              "tax": totals.total_tax, "gross": totals.total_gross, "actor": actor, "rev": revision_of}
    if offer_id is None:
        offer_id = await conn.scalar(text(
            "INSERT INTO offers (tenant_id, customer_id, title, price_mode, valid_until, intro_text, closing_text, total_net, "
            "total_tax, total_gross, revision_of, created_by) VALUES (:t, :c, :title, :pm, :vu, :intro, :closing, :net, :tax, "
            ":gross, :rev, :actor) RETURNING id"), params)
    else:
        current = await get_offer(conn, tenant_id, offer_id, today, lock=True)
        if current["status"] != "draft":
            raise OfferStateError("Nur Entwürfe sind änderbar – für versendete Angebote eine neue Version anlegen")
        await conn.execute(text(
            "UPDATE offers SET customer_id = :c, title = :title, price_mode = :pm, valid_until = :vu, intro_text = :intro, "
            "closing_text = :closing, total_net = :net, total_tax = :tax, total_gross = :gross WHERE id = :id"),
            params | {"id": offer_id})
        await conn.execute(text("DELETE FROM offer_lines WHERE offer_id = :id"), {"id": offer_id})
    for pos, ln in enumerate(data["lines"], start=1):
        await conn.execute(text(
            "INSERT INTO offer_lines (offer_id, position, product_id, description, quantity, unit_code, unit_price, tax_case, recurring) "
            "VALUES (:o, :p, :pid, :d, :q, :u, :pr, :t, :r)"),
            {"o": offer_id, "p": pos, "pid": ln.get("product_id"), "d": ln["description"], "q": ln["quantity"],
             "u": ln["unit_code"], "pr": ln["unit_price"], "t": ln["tax_case"], "r": bool(ln.get("recurring"))})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="offer.saved", entity_type="offer", entity_id=offer_id,
                       after=data)
    return await get_offer(conn, tenant_id, offer_id, today)


async def delete_draft(conn: AsyncConnection, tenant_id: UUID, offer_id: UUID, today: dt.date) -> None:
    current = await get_offer(conn, tenant_id, offer_id, today, lock=True)
    if current["status"] != "draft":
        raise OfferStateError("Versendete Angebote können nicht gelöscht werden")
    await conn.execute(text("DELETE FROM offers WHERE id = :id"), {"id": offer_id})


async def mark_sent(conn: AsyncConnection, tenant_id: UUID, offer_id: UUID, actor: str, today: dt.date) -> dict[str, Any]:
    current = await get_offer(conn, tenant_id, offer_id, today, lock=True)
    if current["status"] != "draft":
        raise OfferStateError("Angebot wurde bereits versendet")
    await conn.execute(text("UPDATE offers SET status = 'sent', sent_at = now() WHERE id = :id"), {"id": offer_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="offer.sent", entity_type="offer", entity_id=offer_id)
    return await get_offer(conn, tenant_id, offer_id, today)


async def decide(conn: AsyncConnection, tenant_id: UUID, offer_id: UUID, *, accept: bool, name: str, channel: str,
                 note: str | None, actor: str, today: dt.date) -> dict[str, Any]:
    current = await get_offer(conn, tenant_id, offer_id, today, lock=True)
    if current["status"] != "sent":
        raise OfferStateError("Nur versendete, offene Angebote können angenommen oder abgelehnt werden")
    if accept and current["valid_until"] < today:
        raise OfferStateError("Angebot ist abgelaufen – neue Version mit neuem Gültigkeitsdatum anlegen")
    await conn.execute(text(
        "UPDATE offers SET status = :s, decided_at = now(), decided_by_name = :n, decision_channel = :ch, decision_note = :note "
        "WHERE id = :id"), {"s": "accepted" if accept else "declined", "n": name, "ch": channel, "note": note, "id": offer_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="offer.accepted" if accept else "offer.declined",
                       entity_type="offer", entity_id=offer_id, after={"name": name, "channel": channel, "note": note})
    return await get_offer(conn, tenant_id, offer_id, today)


async def new_revision(conn: AsyncConnection, tenant_id: UUID, offer_id: UUID, actor: str, today: dt.date,
                       valid_until: dt.date) -> dict[str, Any]:
    current = await get_offer(conn, tenant_id, offer_id, today, lock=True)
    if current["status"] not in ("sent", "declined"):
        raise OfferStateError("Neue Version nur für versendete oder abgelehnte Angebote")
    data = {"customer_id": current["customer_id"], "title": current["title"], "valid_until": valid_until,
            "intro_text": current["intro_text"], "closing_text": current["closing_text"], "lines": current["lines"]}
    copy = await save_draft(conn, tenant_id, data, actor, today, revision_of=offer_id)
    await conn.execute(text("UPDATE offers SET status = 'superseded' WHERE id = :id AND status = 'sent'"), {"id": offer_id})
    return copy


async def convert(conn: AsyncConnection, tenant_id: UUID, offer_id: UUID, actor: str, today: dt.date,
                  contract_start: dt.date | None) -> dict[str, Any]:
    """Angenommenes Angebot → Rechnungsentwurf (einmalige Positionen) und/oder Vertragsentwurf (wiederkehrende)."""
    offer = await get_offer(conn, tenant_id, offer_id, today, lock=True)
    if offer["status"] != "accepted":
        raise OfferStateError("Nur angenommene Angebote können übernommen werden")
    if offer["invoice_id"] or offer["contract_id"]:
        raise OfferStateError("Angebot wurde bereits übernommen")
    one_time = [ln for ln in offer["lines"] if not ln["recurring"]]
    recurring = [ln for ln in offer["lines"] if ln["recurring"]]
    invoice_id = contract_id = None
    if one_time:
        draft = InvoiceDraftIn(customer_id=offer["customer_id"], service_date_from=today,
                               intro_text=f"Gemäß Ihrer Annahme unseres Angebots {offer['number']} vom {offer['sent_at']:%d.%m.%Y}.",
                               lines=[InvoiceLineIn(product_id=ln["product_id"], description=ln["description"], quantity=ln["quantity"],
                                                    unit_code=ln["unit_code"], unit_price=ln["unit_price"], tax_case=ln["tax_case"])
                                      for ln in one_time])
        invoice_id = (await inv.create_draft(conn, tenant_id, draft, actor))["id"]
    if recurring:
        contract = await ct.save_contract(conn, tenant_id, {
            "customer_id": offer["customer_id"], "title": offer["title"], "start_date": contract_start or today,
            "min_term_months": 12 if offer["customer_type"] == "b2b" else 0, "notice_months": 1, "renewal": "monthly",
            "billing_interval": "monthly", "billing_mode": "advance", "auto_issue": True,
            "notes": f"Aus Angebot {offer['number']}", "lines": [
                {k: ln[k] for k in ("product_id", "description", "quantity", "unit_code", "unit_price", "tax_case")} for ln in recurring],
        }, actor)
        contract_id = contract["id"]
    await conn.execute(text("UPDATE offers SET invoice_id = :i, contract_id = :c WHERE id = :id"),
                       {"i": invoice_id, "c": contract_id, "id": offer_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="offer.converted", entity_type="offer", entity_id=offer_id,
                       after={"invoice_id": str(invoice_id) if invoice_id else None,
                              "contract_id": str(contract_id) if contract_id else None})
    return await get_offer(conn, tenant_id, offer_id, today)


async def render_pdf(conn: AsyncConnection, tenant_id: UUID, offer_id: UUID, today: dt.date) -> tuple[str, bytes]:
    """Angebots-PDF aus aktuellen Firmendaten; Datum = Versanddatum (Entwurf: heute, mit Kennzeichnung)."""
    from app.einvoice import pdf as pdf_module
    from app.services.documents import active_logo
    from app.services.masterdata import current_company_profile

    offer = await get_offer(conn, tenant_id, offer_id, today)
    issue = offer["sent_at"].date() if offer["sent_at"] else today
    profile = await current_company_profile(conn, tenant_id, issue)
    if profile is None:
        raise OfferValidationError("Firmendaten fehlen – bitte unter Einstellungen → Firma erfassen")
    customer = await get_customer(conn, tenant_id, offer["customer_id"])
    totals = _totals(offer["lines"], PriceMode(offer["price_mode"]))
    snapshot = inv.build_snapshot(
        kind="invoice", issue_date=issue, due_date=offer["valid_until"],
        row={"service_date_from": None, "service_date_to": None, "intro_text": offer["intro_text"],
             "closing_text": offer["closing_text"], "cancellation_reason": None},
        totals=totals, seller=inv._seller(profile), buyer=inv._buyer(customer), cancels=None)
    snapshot["valid_until"] = offer["valid_until"].isoformat()
    for line, src in zip(snapshot["lines"], offer["lines"], strict=True):
        line["recurring"] = src["recurring"]
    snapshot["notes"] = [n for n in snapshot["notes"] if n != inv.B2C_DEFAULT_NOTE]
    if offer["status"] == "draft":
        snapshot["notes"].insert(0, "ENTWURF – noch nicht versendet.")
    if any(ln["recurring"] for ln in offer["lines"]):
        snapshot["notes"].append("Monatlich wiederkehrende Positionen sind im Gesamtbetrag mit dem Betrag für einen Monat enthalten.")
    snapshot["notes"].append(f"Dieses Angebot ist gültig bis {offer['valid_until']:%d.%m.%Y}.")
    logo = await active_logo(conn, tenant_id)
    number = offer["number"]
    return number, pdf_module.render_offer_pdf(number, snapshot, logo=pdf_module.logo_data_uri(*logo) if logo else None)


async def email_offer(conn: AsyncConnection, mailer: Any, tenant_id: UUID, offer_id: UUID, today: dt.date, actor: str) -> str:
    """Versendet das (bereits als versendet markierte) Angebot als PDF; Annahme im Kundenportal möglich."""
    from app.core.config import get_settings
    from app.einvoice.pdf import _date, _euro
    from app.services.mail import Attachment, OutgoingMail
    from app.services.masterdata import current_company_profile

    offer = await get_offer(conn, tenant_id, offer_id, today)
    number, pdf = await render_pdf(conn, tenant_id, offer_id, today)
    profile = await current_company_profile(conn, tenant_id, today)
    company = profile["legal_name"] if profile else ""
    has_portal = await conn.scalar(text("SELECT 1 FROM portal_users WHERE tenant_id = :t AND customer_id = :c AND status = 'active' "
                                        "LIMIT 1"), {"t": tenant_id, "c": offer["customer_id"]})
    amount = offer["total_gross"] if offer["price_mode"] == "gross" else offer["total_net"]
    lines = ["Guten Tag" + ("" if offer["customer_type"] == "b2b" else f" {offer['customer_name']}") + ",", "",
             f"vielen Dank für Ihr Interesse. Anbei erhalten Sie unser Angebot {number} „{offer['title']}“ über "
             f"{_euro(amount)}{' netto' if offer['price_mode'] == 'net' else ''}.",
             f"Das Angebot ist gültig bis {_date(offer['valid_until'].isoformat())}."]
    if has_portal:
        lines += ["", f"Sie können das Angebot bequem im Kundenportal annehmen: {get_settings().portal_base}/portal"]
    else:
        lines += ["", "Zur Annahme genügt eine kurze Antwort auf diese E-Mail."]
    lines += ["", "Mit freundlichen Grüßen", company]
    mail = OutgoingMail(to=[offer["customer_email"]], subject=f"Angebot {number} – {company}", text="\n".join(lines),
                        attachments=[Attachment(f"Angebot_{number}.pdf", "application/pdf", pdf)],
                        reply_to=(profile["email"] if profile else None) or None)
    message_id: str = await mailer.send(mail)
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="offer.emailed", entity_type="offer", entity_id=offer_id,
                       after={"to": offer["customer_email"], "message_id": message_id})
    return message_id
