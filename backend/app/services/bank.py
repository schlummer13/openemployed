"""Bankumsätze (Onlinebanking per FinTS oder Kontoauszug-Import) und Zuordnung zu offenen Rechnungen.

Automatisch zugeordnet wird nur, wenn die Rechnungsnummer im Verwendungszweck steht **und** der Betrag genau der
offenen Forderung entspricht. Alles andere wird als Vorschlag angezeigt und per Klick bestätigt.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services import audit
from app.services import receivables as rv
from app.services.masterdata import NotFoundError

D = Decimal
INVOICE_RE = re.compile(r"RE[-\s]?(\d{4})[-\s]?(\d{5})", re.IGNORECASE)


class BankError(ValueError):
    pass


def parse_amount(v: str) -> Decimal:
    s = v.strip().replace("€", "").replace("EUR", "").replace(" ", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return D(s).quantize(D("0.01"))
    except InvalidOperation as exc:
        raise BankError(f"Betrag nicht lesbar: {v!r}") from exc


def parse_date(v: str) -> dt.date:
    v = v.strip()[:10]
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(v, fmt).date()  # noqa: DTZ007 – reines Datum
        except ValueError:
            continue
    raise BankError(f"Datum nicht lesbar: {v!r}")


COLUMN_ALIASES = {
    "date": ("buchungstag", "buchungsdatum", "datum", "date", "settlement date", "settled at", "valuta", "wertstellung"),
    "amount": ("betrag", "amount", "betrag (eur)", "amount (eur)", "umsatz"),
    "counterparty": ("gegenpartei", "name", "counterparty name", "counterparty", "empfänger/auftraggeber", "auftraggeber/empfänger",
                     "beguenstigter/zahlungspflichtiger", "begünstigter/zahlungspflichtiger"),
    "reference": ("verwendungszweck", "reference", "referenz", "label", "beschreibung", "description"),
    "iban": ("iban", "counterparty iban", "kontonummer/iban"),
    "id": ("transaction id", "transaktions-id", "id"),
}


def parse_csv(content: bytes) -> list[dict[str, Any]]:
    """Liest gängige Bank-CSV (Sparkasse, Volksbank, Direktbanken …): Trennzeichen und Spalten werden erkannt."""
    for enc in ("utf-8-sig", "cp1252"):
        try:
            raw = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        raise BankError("Zeichensatz nicht erkannt")
    dialect = csv.Sniffer().sniff(raw[:4096], delimiters=";,\t")
    reader = csv.reader(io.StringIO(raw), dialect)
    header = [h.strip().lower() for h in next(reader, [])]
    idx: dict[str, int] = {}
    for key, aliases in COLUMN_ALIASES.items():
        for i, h in enumerate(header):
            if h in aliases and key not in idx:
                idx[key] = i
    if "date" not in idx or "amount" not in idx:
        raise BankError("Spalten für Datum und Betrag nicht gefunden (erwartet z. B. „Buchungstag“ und „Betrag“)")
    out = []
    for n, row in enumerate(reader, start=2):
        if not row or all(not c.strip() for c in row):
            continue

        def col(key: str, row: list[str] = row) -> str:
            i = idx.get(key)
            return row[i].strip() if i is not None and i < len(row) else ""
        date, amount = parse_date(col("date")), parse_amount(col("amount"))
        ext = col("id") or f"{date.isoformat()}|{amount}|{col('counterparty')}|{col('reference')}|{n}"
        out.append({"external_id": ext[:200], "booked_on": date, "amount": amount, "counterparty": col("counterparty")[:200] or None,
                    "counterparty_iban": col("iban").replace(" ", "")[:34] or None, "reference": col("reference")[:500] or None})
    return out


async def store(conn: AsyncConnection, tenant_id: UUID, rows: list[dict[str, Any]], source: str, actor: str) -> dict[str, int]:
    new = 0
    for r in rows:
        res = await conn.execute(text(
            "INSERT INTO bank_transactions (tenant_id, source, external_id, booked_on, amount, counterparty, counterparty_iban, reference, "
            "account_iban, imported_by) VALUES (:t, :s, :e, :d, :a, :c, :i, :r, :acc, :by) "
            "ON CONFLICT (tenant_id, source, external_id) DO NOTHING"),
            {"t": tenant_id, "s": source, "e": r["external_id"], "d": r["booked_on"], "a": r["amount"], "c": r["counterparty"],
             "i": r["counterparty_iban"], "r": r["reference"], "acc": r.get("account_iban"), "by": actor})
        new += res.rowcount
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="bank.imported", entity_type="bank", entity_id=None,
                       after={"source": source, "rows": len(rows), "new": new})
    return {"rows": len(rows), "new": new, "duplicates": len(rows) - new}


async def suggestions(conn: AsyncConnection, tenant_id: UUID, today: dt.date) -> dict[str, Any]:
    txs = (await conn.execute(text(
        "SELECT id, source, booked_on, amount, counterparty, counterparty_iban, reference, status, matched_invoice_id, payment_id "
        "FROM bank_transactions WHERE tenant_id = :t ORDER BY (status = 'open') DESC, booked_on DESC LIMIT 300"),
        {"t": tenant_id})).mappings().all()
    items = (await rv.open_items(conn, tenant_id, today))["items"]
    by_number = {i["number"]: i for i in items}
    payables = await open_payables(conn, tenant_id)
    out = []
    for tx in txs:
        entry = dict(tx)
        entry["candidates"] = []
        if tx["status"] == "open" and D(tx["amount"]) > 0:
            numbers = {f"RE-{a}-{b}" for a, b in INVOICE_RE.findall(tx["reference"] or "")}
            cands = [by_number[n] for n in numbers if n in by_number]
            if not cands:
                cands = [i for i in items if i["total_open"] == D(tx["amount"]) or i["principal_open"] == D(tx["amount"])]
            entry["candidates"] = [{"invoice_id": c["invoice_id"], "number": c["number"], "customer_name": c["customer_name"],
                                    "total_open": c["total_open"], "exact": c["total_open"] == D(tx["amount"]),
                                    "by_reference": c["number"] in numbers} for c in cands[:5]]
        entry["receipt_candidates"] = []
        if tx["status"] == "open" and D(tx["amount"]) < 0:
            entry["receipt_candidates"] = receipt_candidates(dict(tx), payables)[:5]
        out.append(entry)
    return {"transactions": out, "open": sum(1 for t in out if t["status"] == "open")}


RC_CASES = ("IN_13B_EU_SERVICE", "IN_13B_DRITTLAND_SERVICE", "IN_IG_ERWERB")
WORD = re.compile(r"[a-z0-9äöüß]{3,}")
GENERIC = {"gmbh", "ltd", "inc", "ireland", "deutschland", "online", "systems", "software", "services", "und", "the", "co"}


async def open_payables(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    """Gebuchte, noch nicht bezahlte Eingangsrechnungen mit dem zu zahlenden Betrag (Reverse Charge: netto)."""
    rows = await conn.execute(text(
        "SELECT r.id, r.receipt_date, r.invoice_number, r.description, r.tax_case, r.net_amount, r.gross_amount, s.name AS supplier_name "
        "FROM receipts r JOIN suppliers s ON s.id = r.supplier_id WHERE r.tenant_id = :t AND r.status = 'booked' AND r.kind = 'expense' "
        "AND r.paid_on IS NULL AND r.reversed_by IS NULL ORDER BY r.receipt_date"), {"t": tenant_id})
    return [dict(r) | {"payable": D(r.net_amount) if r.tax_case in RC_CASES else D(r.gross_amount)} for r in rows.mappings()]


def receipt_candidates(tx: dict[str, Any], payables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Vorschläge für eine Abbuchung: exakter Betrag; Rechnungsnummer im Verwendungszweck bzw. Lieferantenname stärken den Treffer."""
    amount = -D(tx["amount"])
    text_ = f"{tx.get('counterparty') or ''} {tx.get('reference') or ''}".lower()
    words = set(WORD.findall(text_))
    out = []
    for p in payables:
        if p["payable"] != amount:
            continue
        by_ref = bool(p["invoice_number"]) and p["invoice_number"].lower() in text_
        name_words = set(WORD.findall(p["supplier_name"].lower())) - GENERIC
        by_name = bool(name_words & words)
        out.append({"receipt_id": p["id"], "supplier_name": p["supplier_name"], "invoice_number": p["invoice_number"],
                    "description": p["description"], "receipt_date": p["receipt_date"], "amount": p["payable"],
                    "by_reference": by_ref, "by_name": by_name, "score": 2 * by_ref + by_name})
    return sorted(out, key=lambda c: (-c["score"], c["receipt_date"]))


async def match_receipt(conn: AsyncConnection, tenant_id: UUID, tx_id: UUID, receipt_id: UUID, actor: str) -> None:
    """Abbuchung ↔ Eingangsrechnung: Beleg als bezahlt markieren (Zahlungsbuchung Verbindlichkeit an Bank folgt als Regelbuchung)."""
    tx = (await conn.execute(text("SELECT * FROM bank_transactions WHERE tenant_id = :t AND id = :id FOR UPDATE"),
                             {"t": tenant_id, "id": tx_id})).mappings().one_or_none()
    if tx is None:
        raise NotFoundError("Umsatz nicht gefunden")
    if tx["status"] != "open":
        raise BankError("Umsatz ist bereits zugeordnet oder ignoriert")
    if D(tx["amount"]) >= 0:
        raise BankError("Nur Abbuchungen können Eingangsrechnungen zugeordnet werden")
    p = next((x for x in await open_payables(conn, tenant_id) if x["id"] == receipt_id), None)
    if p is None:
        raise BankError("Beleg ist nicht gebucht, bereits bezahlt oder storniert")
    if p["payable"] != -D(tx["amount"]):
        raise BankError(f"Betrag passt nicht: Abbuchung {-D(tx['amount'])} €, Beleg {p['payable']} €")
    await conn.execute(text("UPDATE receipts SET paid_on = :d WHERE id = :id AND paid_on IS NULL"),
                       {"d": tx["booked_on"], "id": receipt_id})
    await conn.execute(text("UPDATE bank_transactions SET status = 'matched', matched_receipt_id = :r WHERE id = :id"),
                       {"r": receipt_id, "id": tx_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="bank.receipt_matched", entity_type="bank_transaction",
                       entity_id=tx_id, after={"receipt_id": str(receipt_id), "paid_on": str(tx["booked_on"])})


async def match(conn: AsyncConnection, ledger: Any, tenant_id: UUID, tx_id: UUID, invoice_id: UUID, actor: str,
                today: dt.date) -> dict[str, Any]:
    tx = (await conn.execute(text("SELECT * FROM bank_transactions WHERE tenant_id = :t AND id = :id FOR UPDATE"),
                             {"t": tenant_id, "id": tx_id})).mappings().one_or_none()
    if tx is None:
        raise NotFoundError("Umsatz nicht gefunden")
    if tx["status"] != "open":
        raise BankError("Umsatz ist bereits zugeordnet oder ignoriert")
    if D(tx["amount"]) <= 0:
        raise BankError("Nur Zahlungseingänge können Rechnungen zugeordnet werden")
    amount, amount_eur = D(tx["amount"]), None
    cur = await conn.scalar(text("SELECT currency FROM invoices WHERE tenant_id = :t AND id = :i"),
                            {"t": tenant_id, "i": invoice_id})
    if cur and cur != "EUR":
        # Fremdwährungsrechnung: Eingang in EUR gleicht die offene Forderung in Rechnungswährung aus, Differenz = Kurs
        open_ = (await rv.invoice_receivable(conn, tenant_id, invoice_id, today))["principal_open"]
        amount, amount_eur = D(open_), D(tx["amount"])
    payment = await rv.record_payment(conn, ledger, tenant_id, invoice_id, paid_on=tx["booked_on"], amount=amount,
                                      amount_eur=amount_eur, method="bank",
                                      reference=(tx["reference"] or tx["counterparty"] or "")[:500] or None,
                                      actor=actor, today=today, source="import",
                                      external_id=f"bank:{tx['id']}")
    await conn.execute(text("UPDATE bank_transactions SET status = 'matched', matched_invoice_id = :i, payment_id = :p WHERE id = :id"),
                       {"i": invoice_id, "p": payment["id"], "id": tx_id})
    return {"payment_id": payment["id"]}


async def ignore(conn: AsyncConnection, tenant_id: UUID, tx_id: UUID, note: str | None, actor: str) -> None:
    res = await conn.execute(text("UPDATE bank_transactions SET status = 'ignored', note = :n WHERE tenant_id = :t AND id = :id "
                                  "AND status = 'open'"), {"n": note, "t": tenant_id, "id": tx_id})
    if res.rowcount == 0:
        raise BankError("Umsatz nicht gefunden oder bereits bearbeitet")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="bank.ignored", entity_type="bank_transaction",
                       entity_id=tx_id, after={"note": note})


async def auto_match(conn: AsyncConnection, ledger: Any, tenant_id: UUID, actor: str, today: dt.date) -> int:
    """Ordnet eindeutige Fälle automatisch zu: Eingänge über Rechnungsnummer + exakten Betrag; Abbuchungen über exakten Betrag
    und Rechnungsnummer oder Lieferantenname, wenn genau ein Beleg passt."""
    data = await suggestions(conn, tenant_id, today)
    count = 0
    for tx in data["transactions"]:
        if tx["status"] != "open":
            continue
        exact = [c for c in tx["candidates"] if c["exact"] and c["by_reference"]]
        if len(exact) == 1:
            await match(conn, ledger, tenant_id, tx["id"], exact[0]["invoice_id"], actor, today)
            count += 1
            continue
        rc = tx.get("receipt_candidates", [])
        strong = [c for c in rc if c["score"] > 0]
        if strong and (len(strong) == 1 or strong[0]["score"] > strong[1]["score"]):
            await match_receipt(conn, tenant_id, tx["id"], strong[0]["receipt_id"], actor)
            count += 1
    return count
