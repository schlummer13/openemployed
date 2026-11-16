"""Offene Posten, Zahlungseingänge und Mahnwesen."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.domain import dunning as dn
from app.services import audit
from app.services.ledger_client import LedgerClient, to_ledger_payload
from app.services.mail import Attachment, OutgoingMail, WorkspaceMailer
from app.services.masterdata import NotFoundError

log = logging.getLogger("openemployed.receivables")
D = Decimal
DUNNING_LOCK_KEY = 0x5E1B_0002


class ReceivableError(ValueError):
    pass


class ReceivableStateError(RuntimeError):
    pass


# ───────────────────────── Stammdaten ─────────────────────────

async def base_rates(conn: AsyncConnection) -> list[dn.BaseRate]:
    rows = await conn.execute(text("SELECT valid_from, rate FROM base_rates ORDER BY valid_from"))
    return [dn.BaseRate(r.valid_from, D(r.rate)) for r in rows]


def half_year_start(day: dt.date) -> dt.date:
    return day.replace(month=1 if day.month < 7 else 7, day=1)


def missing_rate(rates: list[dn.BaseRate], today: dt.date) -> dt.date | None:
    """Liefert den Halbjahresbeginn, für den der Basiszinssatz noch fehlt (keine stillschweigende Fortschreibung)."""
    needed = half_year_start(today)
    return None if any(r.valid_from == needed for r in rates) else needed


async def save_base_rate(conn: AsyncConnection, tenant_id: UUID, valid_from: dt.date, rate: Decimal, actor: str) -> None:
    if valid_from.day != 1 or valid_from.month not in (1, 7):
        raise ReceivableError("Der Basiszinssatz gilt jeweils ab 1. Januar bzw. 1. Juli")
    await conn.execute(text(
        "INSERT INTO base_rates (valid_from, rate, created_by) VALUES (:v, :r, :a) "
        "ON CONFLICT (valid_from) DO UPDATE SET rate = EXCLUDED.rate, created_by = EXCLUDED.created_by, created_at = now()"),
        {"v": valid_from, "r": rate, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="base_rate.saved", entity_type="base_rate", entity_id=None,
                       after={"valid_from": valid_from, "rate": rate})


SETTINGS_DEFAULT = {"enabled": True, "auto_send": True, "b2c_fee": D("2.50"), "b2b_flat_fee": True, "deadline_days": 7}


async def get_settings(conn: AsyncConnection, tenant_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text(
        "SELECT enabled, auto_send, b2c_fee, b2b_flat_fee, deadline_days FROM dunning_settings WHERE tenant_id = :t"),
        {"t": tenant_id})).mappings().one_or_none()
    return dict(row) if row else dict(SETTINGS_DEFAULT)


async def save_settings(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str) -> dict[str, Any]:
    await conn.execute(text(
        "INSERT INTO dunning_settings (tenant_id, enabled, auto_send, b2c_fee, b2b_flat_fee, deadline_days, updated_by) "
        "VALUES (:t, :enabled, :auto_send, :b2c_fee, :b2b_flat_fee, :deadline_days, :a) ON CONFLICT (tenant_id) DO UPDATE SET "
        "enabled = EXCLUDED.enabled, auto_send = EXCLUDED.auto_send, b2c_fee = EXCLUDED.b2c_fee, "
        "b2b_flat_fee = EXCLUDED.b2b_flat_fee, deadline_days = EXCLUDED.deadline_days, updated_by = EXCLUDED.updated_by, "
        "updated_at = now()"), data | {"t": tenant_id, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="dunning_settings.saved", entity_type="dunning_settings",
                       entity_id=None, after=data)
    return await get_settings(conn, tenant_id)


# ───────────────────────── Forderungsstand ─────────────────────────

INVOICE_SQL = (
    "SELECT i.id, i.number, i.kind, i.status, i.customer_id, i.issue_date, i.due_date, i.total_gross, i.cancelled_by, "
    "i.snapshot, i.sent_at, c.customer_type, c.email, "
    "COALESCE(c.company_name, trim(coalesce(c.first_name, '') || ' ' || coalesce(c.last_name, ''))) AS customer_name "
    "FROM invoices i JOIN customers c ON c.id = i.customer_id WHERE i.tenant_id = :t"
)


async def _invoice(conn: AsyncConnection, tenant_id: UUID, invoice_id: UUID, *, lock: bool = False) -> dict[str, Any]:
    row = (await conn.execute(text(INVOICE_SQL + " AND i.id = :id" + (" FOR UPDATE OF i" if lock else "")),
                              {"t": tenant_id, "id": invoice_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Rechnung nicht gefunden")
    return dict(row)


async def _payments(conn: AsyncConnection, invoice_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT id, paid_on, amount, method, reference, source, created_by, created_at, reversed_at, reversed_by, reversal_reason "
        "FROM payments WHERE invoice_id = :id ORDER BY paid_on, created_at"), {"id": invoice_id})
    return [dict(r) for r in rows.mappings()]


async def _notices(conn: AsyncConnection, invoice_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT id, number, level, notice_date, deadline, default_from, principal_open, interest, fee, total, created_by, "
        "created_at, sent_at, sent_to FROM dunning_notices WHERE invoice_id = :id ORDER BY level"), {"id": invoice_id})
    return [dict(r) for r in rows.mappings()]


async def _block(conn: AsyncConnection, invoice_id: UUID, today: dt.date) -> dict[str, Any] | None:
    row = (await conn.execute(text("SELECT reason, until, created_by, created_at FROM dunning_blocks WHERE invoice_id = :id"),
                              {"id": invoice_id})).mappings().one_or_none()
    if row is None or (row["until"] is not None and row["until"] < today):
        return None
    return dict(row)


def _b2c_notice(snapshot: dict[str, Any] | None) -> bool:
    from app.services.invoices import B2C_DEFAULT_NOTE

    return bool(snapshot) and B2C_DEFAULT_NOTE in (snapshot or {}).get("notes", [])


def compute(inv: dict[str, Any], payments: list[dict[str, Any]], notices: list[dict[str, Any]],
            rates: list[dn.BaseRate], today: dt.date) -> dict[str, Any]:
    """Forderungsstand einer ausgestellten Rechnung am Tag ``today`` (rein, ohne DB)."""
    b2b = inv["customer_type"] == "b2b"
    active = [p for p in payments if p["reversed_at"] is None]
    reminder = next((n for n in notices if n["level"] == 1 and n["sent_at"] is not None), None)
    default_from = dn.default_start(
        due_date=inv["due_date"], b2b=b2b, b2c_notice_on_invoice=_b2c_notice(inv["snapshot"]),
        first_reminder_received=max(reminder["notice_date"], reminder["sent_at"].date()) if reminder else None)
    costs = [dn.Cost(n["notice_date"], D(n["fee"])) for n in notices if D(n["fee"]) > 0]
    state = dn.claim_state(principal=D(inv["total_gross"]), default_from=default_from if default_from and default_from <= today
                           else None, as_of=today, payments=[dn.Payment(p["paid_on"], D(p["amount"])) for p in active],
                           costs=costs, rates=rates, b2b=b2b)
    if inv["cancelled_by"] is not None or inv["kind"] == "cancellation":
        status = "cancelled"
    elif state.principal_open <= 0 and state.total_open <= 0:
        status = "paid"
    elif state.principal_open <= 0:
        status = "costs_open"  # Hauptforderung beglichen, Zinsen/Kosten offen
    elif inv["due_date"] < today:
        status = "overdue"
    elif state.paid_total > 0:
        status = "partial"
    else:
        status = "open"
    return {
        "status": status, "gross": D(inv["total_gross"]), "paid": state.paid_total, "principal_open": state.principal_open,
        "interest_open": state.interest_open, "costs_open": state.costs_open, "total_open": state.total_open,
        "default_from": default_from, "days_overdue": max(0, (today - inv["due_date"]).days),
        "level": max((n["level"] for n in notices), default=0),
    }


async def invoice_receivable(conn: AsyncConnection, tenant_id: UUID, invoice_id: UUID, today: dt.date) -> dict[str, Any]:
    inv = await _invoice(conn, tenant_id, invoice_id)
    if inv["status"] != "issued":
        raise ReceivableStateError("Rechnung ist noch nicht ausgestellt")
    payments = await _payments(conn, invoice_id)
    notices = await _notices(conn, invoice_id)
    rates = await base_rates(conn)
    return compute(inv, payments, notices, rates, today) | {
        "payments": payments, "notices": notices, "block": await _block(conn, invoice_id, today),
        "rate_missing": missing_rate(rates, today)}


async def open_items(conn: AsyncConnection, tenant_id: UUID, today: dt.date) -> dict[str, Any]:
    """Offene-Posten-Liste (alle ausgestellten, nicht stornierten Rechnungen mit Restforderung)."""
    rows = (await conn.execute(text(
        INVOICE_SQL + " AND i.status = 'issued' AND i.kind = 'invoice' AND i.cancelled_by IS NULL ORDER BY i.due_date"),
        {"t": tenant_id})).mappings().all()
    rates = await base_rates(conn)
    items = []
    for r in rows:
        inv = dict(r)
        c = compute(inv, await _payments(conn, inv["id"]), await _notices(conn, inv["id"]), rates, today)
        if c["status"] == "paid":
            continue
        items.append({"invoice_id": inv["id"], "number": inv["number"], "customer_id": inv["customer_id"],
                      "customer_name": inv["customer_name"], "customer_type": inv["customer_type"],
                      "issue_date": inv["issue_date"], "due_date": inv["due_date"],
                      "blocked": await _block(conn, inv["id"], today) is not None} | c)
    total = sum((i["total_open"] for i in items), start=D("0.00"))
    overdue = sum((i["principal_open"] for i in items if i["status"] == "overdue"), start=D("0.00"))
    return {"items": items, "total_open": total, "overdue": overdue, "rate_missing": missing_rate(rates, today)}


# ───────────────────────── Zahlungen ─────────────────────────

async def record_payment(conn: AsyncConnection, ledger: LedgerClient, tenant_id: UUID, invoice_id: UUID, *, paid_on: dt.date,
                         amount: Decimal, method: str, reference: str | None, actor: str, today: dt.date,
                         source: str = "manual", external_id: str | None = None,
                         amount_eur: Decimal | None = None) -> dict[str, Any]:
    """``amount`` in Rechnungswährung; ``amount_eur`` = tatsächlicher Euro-Betrag bei Fremdwährung (Kursdifferenz)."""
    inv = await _invoice(conn, tenant_id, invoice_id, lock=True)
    if inv["status"] != "issued" or inv["kind"] != "invoice":
        raise ReceivableStateError("Zahlungen nur für ausgestellte Rechnungen (keine Entwürfe oder Stornos)")
    if inv["cancelled_by"] is not None:
        raise ReceivableStateError("Rechnung ist storniert – Zahlung bitte erstatten oder einer anderen Rechnung zuordnen")
    if amount <= 0:
        raise ReceivableError("Betrag muss größer als 0 sein")
    if paid_on > today:
        raise ReceivableError("Zahlungsdatum liegt in der Zukunft")
    if paid_on < inv["issue_date"] - dt.timedelta(days=366):
        raise ReceivableError("Zahlungsdatum liegt mehr als ein Jahr vor dem Rechnungsdatum")
    before = compute(inv, await _payments(conn, invoice_id), await _notices(conn, invoice_id), await base_rates(conn), paid_on)
    if amount > before["total_open"] + D("0.005") and source == "manual":
        raise ReceivableError(f"Betrag übersteigt die offene Forderung ({str(before['total_open']).replace('.', ',')} €)")
    if source != "manual" and external_id:
        exists = await conn.scalar(text("SELECT id FROM payments WHERE tenant_id = :t AND source = :s AND external_id = :e"),
                                   {"t": tenant_id, "s": source, "e": external_id})
        if exists:
            return {"id": exists, "duplicate": True}
    pid = await conn.scalar(text(
        "INSERT INTO payments (tenant_id, invoice_id, paid_on, amount, method, reference, source, external_id, created_by, amount_eur) "
        "VALUES (:t, :i, :d, :a, :m, :r, :s, :e, :by, :eur) RETURNING id"),
        {"t": tenant_id, "i": invoice_id, "d": paid_on, "a": amount, "m": method, "r": reference, "s": source, "e": external_id,
         "by": actor, "eur": amount_eur})
    sealed = await ledger.seal(tenant_id=tenant_id, record_type="payment.received", sealed_by=f"user:{actor}", subject_id=invoice_id,
                               payload={"payment_id": str(pid), "invoice_number": inv["number"], "paid_on": paid_on,
                                        "amount": amount, "method": method, "source": source})
    await conn.execute(text("UPDATE payments SET ledger_record_id = :r WHERE id = :id"), {"r": sealed.id, "id": pid})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="payment.recorded", entity_type="invoice",
                       entity_id=invoice_id, after={"amount": amount, "paid_on": paid_on, "method": method})
    return {"id": pid, "duplicate": False}


async def reverse_payment(conn: AsyncConnection, ledger: LedgerClient, tenant_id: UUID, payment_id: UUID, reason: str,
                          actor: str) -> None:
    row = (await conn.execute(text("SELECT p.id, p.invoice_id, p.amount, p.reversed_at, i.number FROM payments p "
                                   "JOIN invoices i ON i.id = p.invoice_id WHERE p.tenant_id = :t AND p.id = :id FOR UPDATE OF p"),
                              {"t": tenant_id, "id": payment_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Zahlung nicht gefunden")
    if row["reversed_at"] is not None:
        raise ReceivableStateError("Zahlung ist bereits storniert")
    await conn.execute(text("UPDATE payments SET reversed_at = now(), reversed_by = :a, reversal_reason = :r WHERE id = :id"),
                       {"a": actor, "r": reason, "id": payment_id})
    await ledger.seal(tenant_id=tenant_id, record_type="payment.reversed", sealed_by=f"user:{actor}", subject_id=row["invoice_id"],
                      payload={"payment_id": str(payment_id), "invoice_number": row["number"], "amount": D(row["amount"]),
                               "reason": reason})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="payment.reversed", entity_type="invoice",
                       entity_id=row["invoice_id"], after={"payment_id": str(payment_id), "reason": reason})


# ───────────────────────── Mahnsperre ─────────────────────────

async def set_block(conn: AsyncConnection, tenant_id: UUID, invoice_id: UUID, reason: str | None, until: dt.date | None,
                    actor: str) -> None:
    await _invoice(conn, tenant_id, invoice_id)
    if reason is None:
        await conn.execute(text("DELETE FROM dunning_blocks WHERE invoice_id = :id"), {"id": invoice_id})
    else:
        await conn.execute(text(
            "INSERT INTO dunning_blocks (invoice_id, tenant_id, reason, until, created_by) VALUES (:i, :t, :r, :u, :a) "
            "ON CONFLICT (invoice_id) DO UPDATE SET reason = EXCLUDED.reason, until = EXCLUDED.until, "
            "created_by = EXCLUDED.created_by, created_at = now()"),
            {"i": invoice_id, "t": tenant_id, "r": reason, "u": until, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="dunning.block" if reason else "dunning.unblock",
                       entity_type="invoice", entity_id=invoice_id, after={"reason": reason, "until": until})


# ───────────────────────── Mahnschreiben ─────────────────────────

async def create_notice(conn: AsyncConnection, tenant_id: UUID, invoice_id: UUID, today: dt.date, actor: str,
                        level: int | None = None) -> dict[str, Any]:
    """Erzeugt das nächste fällige Mahnschreiben (oder die angegebene Stufe) samt PDF."""
    from app.einvoice import pdf as pdf_module
    from app.services.documents import active_logo
    from app.services.invoices import _seller
    from app.services.masterdata import current_company_profile

    inv = await _invoice(conn, tenant_id, invoice_id, lock=True)
    if inv["status"] != "issued" or inv["kind"] != "invoice" or inv["cancelled_by"] is not None:
        raise ReceivableStateError("Mahnungen nur für ausgestellte, nicht stornierte Rechnungen")
    if await _block(conn, invoice_id, today):
        raise ReceivableStateError("Für diese Rechnung besteht eine Mahnsperre")
    settings = await get_settings(conn, tenant_id)
    rates = await base_rates(conn)
    payments = await _payments(conn, invoice_id)
    notices = await _notices(conn, invoice_id)
    last = notices[-1] if notices else None
    if last is not None and last["sent_at"] is None:
        raise ReceivableStateError(f"{dn.LEVEL_TITLES[last['level']]} {last['number']} wurde noch nicht versendet")
    due_level = dn.next_level(due_date=inv["due_date"], today=today, last_level=last["level"] if last else 0,
                              last_notice_on=last["notice_date"] if last else None)
    if level is None:
        level = due_level
    if level is None:
        raise ReceivableStateError("Derzeit ist keine Mahnstufe fällig")
    if level != (last["level"] + 1 if last else 1):
        raise ReceivableStateError("Mahnstufen müssen der Reihe nach erstellt werden")
    if today <= inv["due_date"]:
        raise ReceivableStateError("Rechnung ist noch nicht fällig")
    state = compute(inv, payments, notices, rates, today)
    if state["principal_open"] <= 0:
        raise ReceivableStateError("Rechnung ist bezahlt")
    if level >= 2 and missing_rate(rates, today):
        raise ReceivableStateError(f"Basiszinssatz ab {missing_rate(rates, today):%d.%m.%Y} fehlt – bitte unter Mahnwesen eintragen")
    b2b = inv["customer_type"] == "b2b"
    in_default = state["default_from"] is not None and state["default_from"] <= today
    fee = D("0.00")
    fee_label = None
    if level >= 2 and in_default:
        if b2b and settings["b2b_flat_fee"] and not any(D(n["fee"]) > 0 for n in notices):
            fee, fee_label = dn.B2B_FLAT_FEE, "Verzugspauschale (§ 288 Abs. 5 BGB)"
        elif not b2b and D(settings["b2c_fee"]) > 0:
            fee, fee_label = D(settings["b2c_fee"]), "Mahnkosten (Porto/Material)"
    interest = state["interest_open"] if level >= 2 else D("0.00")
    costs_before = state["costs_open"]
    total = state["principal_open"] + interest + costs_before + fee
    deadline = today + dt.timedelta(days=int(settings["deadline_days"]))
    profile = await current_company_profile(conn, tenant_id, today)
    if profile is None:
        raise ReceivableError("Firmendaten fehlen")
    margin = dn.B2B_MARGIN if b2b else dn.B2C_MARGIN
    snapshot = to_ledger_payload({
        "level": level, "title": dn.LEVEL_TITLES[level], "notice_date": today, "deadline": deadline,
        "seller": _seller(profile), "buyer": (inv["snapshot"] or {}).get("buyer"),
        "invoice": {"number": inv["number"], "issue_date": inv["issue_date"], "due_date": inv["due_date"],
                    "gross": D(inv["total_gross"])},
        "paid": state["paid"], "principal_open": state["principal_open"], "interest": interest,
        "interest_rate_note": f"{margin} Prozentpunkte über dem Basiszinssatz (§ 288 BGB)" if interest > 0 else None,
        "default_from": state["default_from"] if in_default else None, "costs_before": costs_before,
        "fee": fee, "fee_label": fee_label, "total": total, "b2b": b2b,
        "previous": [{"number": n["number"], "title": dn.LEVEL_TITLES[n["level"]], "date": n["notice_date"]} for n in notices],
    })
    row = (await conn.execute(text(
        "INSERT INTO dunning_notices (tenant_id, invoice_id, level, notice_date, deadline, default_from, principal_open, interest, "
        "fee, total, snapshot, created_by) VALUES (:t, :i, :l, :d, :dl, :df, :p, :int, :f, :tot, CAST(:s AS jsonb), :a) "
        "RETURNING id, number"),
        {"t": tenant_id, "i": invoice_id, "l": level, "d": today, "dl": deadline, "df": state["default_from"] if in_default else None,
         "p": state["principal_open"], "int": interest, "f": fee, "tot": total, "s": json.dumps(snapshot), "a": actor})).mappings().one()
    logo = await active_logo(conn, tenant_id)
    pdf = pdf_module.render_dunning_pdf(row["number"], snapshot, logo=pdf_module.logo_data_uri(*logo) if logo else None)
    await conn.execute(text("UPDATE dunning_notices SET pdf = :p, pdf_sha256 = :h WHERE id = :id"),
                       {"p": pdf, "h": hashlib.sha256(pdf).hexdigest(), "id": row["id"]})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="dunning.created", entity_type="invoice", entity_id=invoice_id,
                       after={"number": row["number"], "level": level, "total": total})
    return {"id": row["id"], "number": row["number"], "level": level, "total": total}


async def notice_pdf(conn: AsyncConnection, tenant_id: UUID, notice_id: UUID) -> tuple[str, bytes]:
    row = (await conn.execute(text("SELECT number, pdf FROM dunning_notices WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": notice_id})).one_or_none()
    if row is None or row.pdf is None:
        raise NotFoundError("Mahnschreiben nicht gefunden")
    return row.number, bytes(row.pdf)


def compose_notice_mail(number: str, snapshot: dict[str, Any], pdf: bytes, invoice_pdf: bytes | None) -> OutgoingMail:
    from app.einvoice.pdf import _date, _euro
    from app.services.documents import file_name

    buyer = snapshot["buyer"]
    seller = snapshot["seller"]
    inv = snapshot["invoice"]
    salutation = "Guten Tag" if snapshot["b2b"] else f"Guten Tag {buyer['name']}"
    if snapshot["level"] == 1:
        body = [f"sicher ist es Ihrer Aufmerksamkeit entgangen: Unsere Rechnung {inv['number']} vom {_date(inv['issue_date'])} "
                f"war am {_date(inv['due_date'])} fällig. Offen sind noch {_euro(snapshot['total'])}.",
                f"Bitte überweisen Sie den Betrag bis zum {_date(snapshot['deadline'])}. Sollte sich Ihre Zahlung mit "
                "dieser Nachricht überschnitten haben, betrachten Sie sie bitte als gegenstandslos."]
    else:
        body = [f"trotz unserer Erinnerung ist die Rechnung {inv['number']} weiterhin offen. Einschließlich Verzugszinsen "
                f"und Kosten beträgt die Forderung {_euro(snapshot['total'])}.",
                f"Bitte zahlen Sie bis spätestens {_date(snapshot['deadline'])}."]
        if snapshot["level"] == 3:
            body.append("Nach Ablauf dieser Frist werden wir die Forderung ohne weitere Ankündigung gerichtlich geltend machen "
                        "bzw. an ein Inkassounternehmen übergeben; dadurch entstehen Ihnen weitere Kosten.")
    attachments = [Attachment(file_name(number, "pdf"), "application/pdf", pdf)]
    if invoice_pdf:
        attachments.append(Attachment(file_name(inv["number"], "pdf"), "application/pdf", invoice_pdf))
    return OutgoingMail(to=[buyer["email"]], subject=f"{snapshot['title']} zu Rechnung {inv['number']} – {seller['legal_name']}",
                        text="\n".join([f"{salutation},", "", *body, "", "Die Details finden Sie im angehängten Schreiben.", "",
                                        "Mit freundlichen Grüßen", seller["legal_name"]]),
                        attachments=attachments, reply_to=seller.get("email") or None)


async def send_notice(conn: AsyncConnection, mailer: WorkspaceMailer, tenant_id: UUID, notice_id: UUID, actor: str) -> dict[str, Any]:
    row = (await conn.execute(text("SELECT id, number, invoice_id, snapshot, pdf, sent_at FROM dunning_notices "
                                   "WHERE tenant_id = :t AND id = :id FOR UPDATE"),
                            {"t": tenant_id, "id": notice_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Mahnschreiben nicht gefunden")
    if row["sent_at"] is not None:
        raise ReceivableStateError("Mahnschreiben wurde bereits versendet")
    invoice_pdf = await conn.scalar(text("SELECT pdf FROM invoice_documents WHERE invoice_id = :id"), {"id": row["invoice_id"]})
    mail = compose_notice_mail(row["number"], row["snapshot"], bytes(row["pdf"]), bytes(invoice_pdf) if invoice_pdf else None)
    message_id = await mailer.send(mail)
    await conn.execute(text("UPDATE dunning_notices SET sent_at = now(), sent_to = :to WHERE id = :id"),
                       {"to": mail.to[0], "id": notice_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="dunning.sent", entity_type="invoice",
                       entity_id=row["invoice_id"], after={"number": row["number"], "to": mail.to[0], "message_id": message_id})
    return {"sent_to": mail.to[0], "message_id": message_id}


async def mark_notice_sent(conn: AsyncConnection, tenant_id: UUID, notice_id: UUID, channel: str, actor: str) -> None:
    """Versandvermerk für Mahnungen, die auf anderem Weg (z. B. Post) zugestellt wurden."""
    res = await conn.execute(text("UPDATE dunning_notices SET sent_at = now(), sent_to = :c WHERE tenant_id = :t AND id = :id "
                                  "AND sent_at IS NULL"), {"c": channel, "t": tenant_id, "id": notice_id})
    if res.rowcount == 0:
        raise ReceivableStateError("Mahnschreiben nicht gefunden oder bereits versendet")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="dunning.marked_sent", entity_type="dunning_notice",
                       entity_id=notice_id, after={"channel": channel})


async def run_dunning(engine: AsyncEngine, mailer: WorkspaceMailer | None, tenant_id: UUID, today: dt.date,
                      actor: str) -> dict[str, Any]:
    """Mahnlauf: erzeugt fällige Mahnschreiben; versendet sie, wenn automatischer Versand aktiv und E-Mail eingerichtet ist."""
    async with engine.connect() as lock_conn:
        if not await lock_conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": DUNNING_LOCK_KEY}):
            return {"skipped": True, "created": [], "sent": [], "errors": []}
        try:
            async with engine.connect() as conn:
                settings = await get_settings(conn, tenant_id)
                if not settings["enabled"]:
                    return {"skipped": True, "reason": "Mahnwesen deaktiviert", "created": [], "sent": [], "errors": []}
                items = (await open_items(conn, tenant_id, today))["items"]
            created: list[dict[str, Any]] = []
            sent: list[str] = []
            errors: list[dict[str, str]] = []
            for item in items:
                if item["blocked"] or item["status"] != "overdue":
                    continue
                try:
                    async with engine.begin() as conn:
                        notices = await _notices(conn, item["invoice_id"])
                        last = notices[-1] if notices else None
                        if last is not None and last["sent_at"] is None:
                            continue  # wartet auf manuellen Versand
                        level = dn.next_level(due_date=item["due_date"], today=today, last_level=last["level"] if last else 0,
                                              last_notice_on=last["notice_date"] if last else None)
                        if level is None:
                            continue
                        notice = await create_notice(conn, tenant_id, item["invoice_id"], today, actor, level)
                        created.append({"invoice": item["number"], "number": notice["number"], "level": level})
                    if settings["auto_send"] and mailer is not None:
                        async with engine.begin() as conn:
                            await send_notice(conn, mailer, tenant_id, notice["id"], actor)
                        sent.append(notice["number"])
                except Exception as exc:  # eine Rechnung stoppt nicht den Lauf
                    log.exception("Mahnlauf %s fehlgeschlagen", item["number"])
                    errors.append({"invoice": item["number"], "error": str(exc)})
            return {"skipped": False, "created": created, "sent": sent, "errors": errors}
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": DUNNING_LOCK_KEY})
