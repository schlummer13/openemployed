"""SEPA-Zahlungsdateien: Lieferantenrechnungen und Löhne überweisen (pain.001), Kundenrechnungen einziehen (pain.008).

OpenEmployed erzeugt nur die Datei; Freigabe und Ausführung erfolgen im Online-Banking. Die Zahlungen werden wie bisher beim
Kontoauszug-Import zugeordnet (bzw. bei Löhnen über „als bezahlt buchen“) – so entsteht keine Buchung ohne Bankbeleg.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.sepa import Debit, Party, SepaError, Transfer, creditor_id_ok, next_business_day, pain001, pain008
from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

D = Decimal


async def _company(conn: AsyncConnection, tenant_id: UUID) -> Party:
    from app.services.invoices import today_berlin
    from app.services.masterdata import current_company_profile

    p = await current_company_profile(conn, tenant_id, today_berlin())
    if p is None or not p["iban"]:
        raise ConflictError("IBAN der Firma fehlt (Einstellungen → Firma)")
    return Party(p["legal_name"], p["iban"], p["bic"])


async def settings(conn: AsyncConnection, tenant_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text("SELECT creditor_id, batch_booking FROM sepa_settings WHERE tenant_id = :t"),
                              {"t": tenant_id})).mappings().one_or_none()
    return dict(row) if row else {"creditor_id": None, "batch_booking": True}


async def save_settings(conn: AsyncConnection, tenant_id: UUID, creditor_id: str | None, batch_booking: bool, actor: str) -> None:
    cid = (creditor_id or "").replace(" ", "").upper() or None
    if cid and not creditor_id_ok(cid):
        raise ConflictError("Gläubiger-Identifikationsnummer ungültig (Format DE98ZZZ09999999999, Prüfziffer)")
    await conn.execute(text(
        "INSERT INTO sepa_settings (tenant_id, creditor_id, batch_booking, updated_by) VALUES (:t, :c, :b, :a) "
        "ON CONFLICT (tenant_id) DO UPDATE SET creditor_id = :c, batch_booking = :b, updated_by = :a, updated_at = now()"),
        {"t": tenant_id, "c": cid, "b": batch_booking, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="sepa.settings", entity_type="tenant", entity_id=tenant_id,
                       after={"creditor_id": cid, "batch_booking": batch_booking})


def _msg_id(prefix: str) -> str:
    return f"SL-{prefix}-{dt.datetime.now(dt.UTC):%Y%m%d%H%M%S}-{secrets.token_hex(3).upper()}"


async def _log(conn: AsyncConnection, tenant_id: UUID, *, kind: str, purpose: str, msg_id: str, due: dt.date, items: list[dict[str, Any]],
               xml: str, actor: str) -> UUID:
    total = sum((D(str(i["amount"])) for i in items), D(0))
    fid: UUID = await conn.scalar(text(
        "INSERT INTO sepa_files (tenant_id, kind, purpose, msg_id, due_date, count, total, items, xml, created_by) "
        "VALUES (:t, :k, :p, :m, :d, :c, :s, CAST(:i AS jsonb), :x, :a) RETURNING id"),
        {"t": tenant_id, "k": kind, "p": purpose, "m": msg_id, "d": due, "c": len(items), "s": total,
         "i": json.dumps(items, default=str), "x": xml, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=f"sepa.{kind}", entity_type="sepa_file", entity_id=fid,
                       after={"purpose": purpose, "count": len(items), "total": str(total), "due": str(due)})
    return fid


async def files(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT id, kind, purpose, msg_id, due_date, count, total, created_by, created_at FROM sepa_files WHERE tenant_id = :t "
        "ORDER BY created_at DESC LIMIT 100"), {"t": tenant_id})
    return [dict(r) for r in rows.mappings()]


async def file_xml(conn: AsyncConnection, tenant_id: UUID, file_id: UUID) -> tuple[str, str]:
    row = (await conn.execute(text("SELECT msg_id, xml FROM sepa_files WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": file_id})).one_or_none()
    if row is None:
        raise NotFoundError("Zahlungsdatei nicht gefunden")
    return str(row[0]), str(row[1])


# ───────────────────────── Überweisungen ─────────────────────────

async def transfer_candidates(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    """Offene Eingangsrechnungen (gebucht, unbezahlt) mit Bankverbindung des Lieferanten und letzter Zahlungsdatei."""
    from app.services.bank import open_payables

    out = []
    for p in await open_payables(conn, tenant_id):
        s = (await conn.execute(text("SELECT s.id, s.iban, s.bic FROM receipts r JOIN suppliers s ON s.id = r.supplier_id WHERE r.id = :r"),
                                {"r": p["id"]})).one()
        last = await conn.scalar(text(
            "SELECT max(f.created_at) FROM sepa_files f, jsonb_array_elements(f.items) e WHERE f.tenant_id = :t AND f.purpose = "
            "'suppliers' "
            "AND e->>'receipt_id' = :r"), {"t": tenant_id, "r": str(p["id"])})
        out.append(p | {"supplier_id": s[0], "iban": s[1], "bic": s[2], "in_file_at": last})
    return out


async def supplier_transfers(conn: AsyncConnection, tenant_id: UUID, receipt_ids: list[UUID], execution_date: dt.date,
                             actor: str) -> tuple[UUID, str]:
    if not receipt_ids:
        raise ConflictError("Keine Rechnungen ausgewählt")
    debtor = await _company(conn, tenant_id)
    cand = {c["id"]: c for c in await transfer_candidates(conn, tenant_id)}
    transfers, items = [], []
    for rid in receipt_ids:
        c = cand.get(rid)
        if c is None:
            raise ConflictError("Rechnung ist nicht (mehr) offen")
        if not c["iban"]:
            raise ConflictError(f"IBAN fehlt beim Lieferanten {c['supplier_name']} (Belege → Lieferant bearbeiten)")
        ref = f"{c['invoice_number'] or c['description'] or 'Rechnung'} vom {c['receipt_date']:%d.%m.%Y}"
        e2e = f"RC-{str(rid).replace('-', '')[:24]}"
        transfers.append(Transfer(Party(c["supplier_name"], c["iban"], c["bic"]), c["payable"], ref, e2e))
        items.append({"receipt_id": str(rid), "name": c["supplier_name"], "amount": str(c["payable"]), "reference": ref})
    return await _transfer_file(conn, tenant_id, debtor, transfers, items, execution_date, "suppliers", actor)


async def salary_transfers(conn: AsyncConnection, tenant_id: UUID, period: str, execution_date: dt.date, actor: str) -> tuple[UUID, str]:
    from app.services import payroll

    run = await payroll.get_run(conn, tenant_id, period)
    if run is None or run["status"] != "approved":
        raise ConflictError("Bitte den Lohnlauf zuerst freigeben")
    debtor = await _company(conn, tenant_id)
    # aktuelle Bankverbindung (Mitarbeiter können sie im Portal ändern); sonst die zum Abrechnungszeitpunkt
    for p in run["payslips"]:
        table, key = ("employees", p["employee_id"]) if p["employee_id"] else ("shareholders", p["shareholder_id"])
        current = await conn.scalar(text(f"SELECT iban FROM {table} WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": key})
        p["iban"] = current or p["iban"]
    rows = payroll.sepa_transfers(run)
    transfers = [Transfer(Party(r["name"], r["iban"]), r["amount"], r["reference"], r["end_to_end"]) for r in rows]
    items = [{"name": r["name"], "amount": str(r["amount"]), "reference": r["reference"], "period": period} for r in rows]
    return await _transfer_file(conn, tenant_id, debtor, transfers, items, execution_date, "salaries", actor)


async def _transfer_file(conn: AsyncConnection, tenant_id: UUID, debtor: Party, transfers: list[Transfer], items: list[dict[str, Any]],
                         execution_date: dt.date, purpose: str, actor: str) -> tuple[UUID, str]:
    from app.services.invoices import today_berlin

    if execution_date < today_berlin():
        raise ConflictError("Ausführungsdatum liegt in der Vergangenheit")
    cfg = await settings(conn, tenant_id)
    msg_id = _msg_id("UEB" if purpose == "suppliers" else "LOHN")
    try:
        xml = pain001(debtor=debtor, transfers=transfers, execution_date=execution_date, msg_id=msg_id,
                      created=dt.datetime.now(dt.UTC).replace(tzinfo=None), batch_booking=cfg["batch_booking"])
    except SepaError as exc:
        raise ConflictError(str(exc)) from exc
    fid = await _log(conn, tenant_id, kind="transfer", purpose=purpose, msg_id=msg_id, due=execution_date, items=items, xml=xml,
                     actor=actor)
    return fid, xml


# ───────────────────────── Lastschriftmandate ─────────────────────────

async def mandates(conn: AsyncConnection, tenant_id: UUID, customer_id: UUID | None = None) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT m.id, m.customer_id, m.reference, m.scheme, m.signed_on, m.account_holder, m.iban, m.bic, m.recurring, m.first_used_on, "
        "m.last_used_on, m.revoked_on, COALESCE(c.company_name, trim(coalesce(c.first_name,'') || ' ' || coalesce(c.last_name,''))) "
        "AS customer_name FROM sepa_mandates m JOIN customers c ON c.id = m.customer_id WHERE m.tenant_id = :t "
        "AND (CAST(:c AS uuid) IS NULL OR m.customer_id = CAST(:c AS uuid)) ORDER BY m.revoked_on NULLS FIRST, customer_name"),
        {"t": tenant_id, "c": customer_id})
    return [dict(r) for r in rows.mappings()]


async def save_mandate(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str) -> UUID:
    from app.domain.sepa import MANDATE_REF, iban_ok

    if not iban_ok(data["iban"]):
        raise ConflictError("IBAN ungültig")
    exists = await conn.scalar(text("SELECT 1 FROM customers WHERE tenant_id = :t AND id = :c"), {"t": tenant_id, "c": data["customer_id"]})
    if not exists:
        raise NotFoundError("Kunde nicht gefunden")
    ref = data.get("reference") or None
    if ref is None:
        num = await conn.scalar(text("SELECT number FROM customers WHERE id = :c"), {"c": data["customer_id"]})
        count = await conn.scalar(text("SELECT count(*) FROM sepa_mandates WHERE customer_id = :c"), {"c": data["customer_id"]})
        ref = f"SL-{num}-{count + 1}"
    if not MANDATE_REF.fullmatch(ref):
        raise ConflictError("Mandatsreferenz: max. 35 Zeichen, Buchstaben, Ziffern und + ? / - : ( ) . , '")
    active = await conn.scalar(text("SELECT reference FROM sepa_mandates WHERE customer_id = :c AND revoked_on IS NULL"),
                               {"c": data["customer_id"]})
    if active:
        raise ConflictError(f"Für diesen Kunden besteht bereits das Mandat {active} – bitte zuerst widerrufen")
    if await conn.scalar(text("SELECT 1 FROM sepa_mandates WHERE tenant_id = :t AND reference = :r"), {"t": tenant_id, "r": ref}):
        raise ConflictError("Mandatsreferenz bereits vergeben")
    mid: UUID = await conn.scalar(text(
        "INSERT INTO sepa_mandates (tenant_id, customer_id, reference, scheme, signed_on, account_holder, iban, bic, recurring, "
        "created_by) "
        "VALUES (:t, :customer_id, :r, :scheme, :signed_on, :account_holder, :iban, :bic, :recurring, :a) RETURNING id"),
        {**data, "t": tenant_id, "r": ref, "iban": data["iban"].replace(" ", "").upper(), "bic": (data.get("bic") or "").upper() or None,
         "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="sepa.mandate_created", entity_type="sepa_mandate", entity_id=mid,
                       after={"reference": ref, "customer_id": str(data["customer_id"]), "signed_on": str(data["signed_on"])})
    return mid


async def revoke_mandate(conn: AsyncConnection, tenant_id: UUID, mandate_id: UUID, on: dt.date, actor: str) -> None:
    n = (await conn.execute(text("UPDATE sepa_mandates SET revoked_on = :d WHERE tenant_id = :t AND id = :id AND revoked_on IS NULL"),
                            {"t": tenant_id, "id": mandate_id, "d": on})).rowcount
    if not n:
        raise NotFoundError("Aktives Mandat nicht gefunden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="sepa.mandate_revoked", entity_type="sepa_mandate",
                       entity_id=mandate_id, after={"revoked_on": str(on)})


async def debit_candidates(conn: AsyncConnection, tenant_id: UUID, today: dt.date) -> list[dict[str, Any]]:
    """Offene Rechnungen von Kunden mit aktivem Mandat (abzgl. bereits eingezogener, noch nicht verbuchter Beträge)."""
    from app.services.receivables import open_items

    active = {m["customer_id"]: m for m in await mandates(conn, tenant_id) if m["revoked_on"] is None}
    out = []
    for i in (await open_items(conn, tenant_id, today))["items"]:
        m = active.get(i["customer_id"])
        if m is None or i["principal_open"] <= 0 or i["blocked"]:
            continue
        last = await conn.scalar(text(
            "SELECT max(f.created_at) FROM sepa_files f, jsonb_array_elements(f.items) e WHERE f.tenant_id = :t AND f.kind = "
            "'direct_debit' "
            "AND e->>'invoice_id' = :i"), {"t": tenant_id, "i": str(i["invoice_id"])})
        out.append({"invoice_id": i["invoice_id"], "number": i["number"], "customer_name": i["customer_name"], "due_date": i["due_date"],
                    "amount": i["principal_open"], "mandate_id": m["id"], "mandate_reference": m["reference"],
                    "sequence": "RCUR" if m["first_used_on"] else ("FRST" if m["recurring"] else "OOFF"), "in_file_at": last})
    return out


async def direct_debits(conn: AsyncConnection, tenant_id: UUID, invoice_ids: list[UUID], collection_date: dt.date,
                        actor: str) -> tuple[UUID, str]:
    from app.services.invoices import today_berlin

    today = today_berlin()
    cfg = await settings(conn, tenant_id)
    if not cfg["creditor_id"]:
        raise ConflictError("Gläubiger-Identifikationsnummer fehlt (Bundesbank-Antrag; Eintrag unter Zahlungsverkehr → Einstellungen)")
    earliest = next_business_day(today, 1)
    if collection_date < earliest:
        raise ConflictError(f"Fälligkeitsdatum frühestens {earliest:%d.%m.%Y} (ein TARGET2-Geschäftstag Vorlauf)")
    creditor = await _company(conn, tenant_id)
    cand = {c["invoice_id"]: c for c in await debit_candidates(conn, tenant_id, today)}
    all_m = {m["id"]: m for m in await mandates(conn, tenant_id)}
    debits, items, used, schemes = [], [], set(), set()
    for iid in invoice_ids:
        c = cand.get(iid)
        if c is None:
            raise ConflictError("Rechnung ist nicht offen oder ohne aktives Mandat")
        m = all_m[c["mandate_id"]]
        schemes.add(m["scheme"])
        ref = f"Rechnung {c['number']}"
        debits.append(Debit(Party(m["account_holder"], m["iban"], m["bic"]), c["amount"], ref, f"DD-{c['number']}"[:35], m["reference"],
                            m["signed_on"], c["sequence"]))
        items.append({"invoice_id": str(iid), "number": c["number"], "name": c["customer_name"], "amount": str(c["amount"]),
                      "mandate": m["reference"], "sequence": c["sequence"]})
        used.add(m["id"])
    if len(schemes) > 1:
        raise ConflictError("CORE- und B2B-Lastschriften bitte in getrennten Dateien einziehen")
    msg_id = _msg_id("LS")
    try:
        xml = pain008(creditor=creditor, creditor_id=cfg["creditor_id"], debits=debits, collection_date=collection_date, msg_id=msg_id,
                      created=dt.datetime.now(dt.UTC).replace(tzinfo=None), scheme=schemes.pop(), batch_booking=cfg["batch_booking"])
    except SepaError as exc:
        raise ConflictError(str(exc)) from exc
    for mid in used:  # nach der Erstlastschrift folgen Folgelastschriften (RCUR)
        await conn.execute(text("UPDATE sepa_mandates SET first_used_on = COALESCE(first_used_on, :d), last_used_on = :d WHERE id = :id"),
                           {"d": collection_date, "id": mid})
    fid = await _log(conn, tenant_id, kind="direct_debit", purpose="customers", msg_id=msg_id, due=collection_date, items=items, xml=xml,
                     actor=actor)
    return fid, xml
