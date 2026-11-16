"""Jahresabschluss-Vorbereitung: Bilanz und GuV (Entwurf), Abschluss-Checkliste, Abschlussbuchungen, Festschreibung, Übergabepaket.

Bilanz (§ 266 HGB) und GuV (§ 275 HGB, Gesamtkostenverfahren) werden vereinfacht aus dem Journal abgeleitet. Sie sind
ein Entwurf als Grundlage für den Steuerberater – keine prüfungsfähige Bilanz. Abschlussbuchungen tragen das Datum
31.12. und gehören in die Abschlussperiode JJJJ-13, die mit dem Jahresabschluss festgeschrieben wird.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import uuid
import zipfile
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services import audit
from app.services.masterdata import ConflictError

D = Decimal
ZERO = D("0.00")

KIND_LABEL = {
    "tax_provision": "Steuerrückstellungen (KSt, Soli, GewSt)",
    "closing_costs": "Rückstellung für Abschluss- und Prüfungskosten",
    "other_provision": "Sonstige Rückstellung",
    "prepaid": "Aktive Rechnungsabgrenzung (im Voraus bezahlter Aufwand)",
    "deferred": "Passive Rechnungsabgrenzung (im Voraus erhaltene Erlöse)",
}


def _roles(chart: str) -> dict[str, str]:
    from app.services.accounting import ROLES

    return {role: pair[0 if chart == "SKR03" else 1] for role, pair in ROLES.items()}


async def _balances(conn: AsyncConnection, tenant_id: UUID, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT l.account, a.name, a.type, SUM(l.debit) AS debit, SUM(l.credit) AS credit FROM journal_lines l "
        "JOIN journal_entries j ON j.id = l.entry_id LEFT JOIN accounts a ON a.chart = j.chart AND a.number = l.account "
        "WHERE j.tenant_id = :t AND j.entry_date BETWEEN :s AND :e GROUP BY l.account, a.name, a.type ORDER BY l.account"),
        {"t": tenant_id, "s": start, "e": end})
    return [dict(r) | {"balance": D(r.debit) - D(r.credit)} for r in rows.mappings()]


def _line(label: str, items: list[dict[str, Any]], sign: int) -> dict[str, Any]:
    accts = [{"account": a["account"], "name": a["name"], "amount": a["balance"] * sign} for a in items if a["balance"]]
    return {"label": label, "amount": sum((x["amount"] for x in accts), ZERO), "accounts": accts}


async def statements(conn: AsyncConnection, tenant_id: UUID, year: int) -> dict[str, Any]:
    """Bilanz zum 31.12. und GuV des Jahres (Entwurf)."""
    from app.services.accounting import chart_of

    chart = await chart_of(conn, tenant_id)
    r = _roles(chart)
    start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
    cumulative = await _balances(conn, tenant_id, dt.date(2000, 1, 1), end)
    period = await _balances(conn, tenant_id, start, end)
    before = await _balances(conn, tenant_id, dt.date(2000, 1, 1), start - dt.timedelta(days=1))

    assets = [a for a in cumulative if a["type"] == "asset"]
    liabilities = [a for a in cumulative if a["type"] == "liability"]
    equity = [a for a in cumulative if a["type"] == "equity"]
    intangible = {"0135", "0027"}
    liquid = {r["bank"], "1600" if chart == "SKR04" else "1000"}
    fixed = [a for a in assets if a["account"].startswith("0")]
    aktiva = [
        _line("A. I. Immaterielle Vermögensgegenstände", [a for a in fixed if a["account"] in intangible], 1),
        _line("A. II. Sachanlagen", [a for a in fixed if a["account"] not in intangible], 1),
        _line("B. II. 1. Forderungen aus Lieferungen und Leistungen", [a for a in assets if a["account"] == r["receivables"]], 1),
        _line("B. II. 4. Sonstige Vermögensgegenstände", [a for a in assets if not a["account"].startswith("0")
                                                         and a["account"] not in liquid | {r["receivables"], r["prepaid_expenses"]}], 1),
        _line("B. IV. Kassenbestand, Guthaben bei Kreditinstituten", [a for a in assets if a["account"] in liquid], 1),
        _line("C. Rechnungsabgrenzungsposten", [a for a in assets if a["account"] == r["prepaid_expenses"]], 1),
    ]
    provisions = {r["provision_trade_tax"], r["provision_corp_tax"], r["provision_closing"], r["provision_other"]}
    result_before = -sum((a["balance"] for a in before if a["type"] in ("revenue", "expense")), ZERO)
    result_year = -sum((a["balance"] for a in period if a["type"] in ("revenue", "expense")), ZERO)
    eq_items = _line("A. I. Gezeichnetes Kapital", equity, -1)
    passiva = [
        eq_items,
        {"label": "A. IV. Gewinn-/Verlustvortrag", "amount": result_before, "accounts": []},
        {"label": "A. V. Jahresüberschuss/-fehlbetrag", "amount": result_year, "accounts": []},
        _line("B. Rückstellungen", [a for a in liabilities if a["account"] in provisions], -1),
        _line("C. 4. Verbindlichkeiten aus Lieferungen und Leistungen", [a for a in liabilities if a["account"] == r["payables"]], -1),
        _line("C. 8. Sonstige Verbindlichkeiten (inkl. Steuern)", [a for a in liabilities if a["account"] not in provisions
                                                                   | {r["payables"], r["deferred_income"]}], -1),
        _line("D. Rechnungsabgrenzungsposten", [a for a in liabilities if a["account"] == r["deferred_income"]], -1),
    ]

    revenue = [a for a in period if a["type"] == "revenue"]
    expense = [a for a in period if a["type"] == "expense"]
    personnel = ({"6020", "6024", "6035", "6040", "6110"} if chart == "SKR04" else {"4120", "4127", "4190", "4199", "4130"})
    material = {"5900" if chart == "SKR04" else "3100"}
    depreciation = {r["depreciation"], r["depreciation_intangible"], r["depreciation_pool"], r["depreciation_gwg"]}
    interest_exp = {"7300" if chart == "SKR04" else "2100"}
    interest_inc = {"7100" if chart == "SKR04" else "2650"}
    taxes = {r["corp_tax"], r["soli"], r["trade_tax"]}
    special = personnel | material | depreciation | interest_exp | taxes
    guv = [
        _line("1. Umsatzerlöse", [a for a in revenue if a["account"] not in interest_inc], -1),
        _line("5. Materialaufwand (Fremdleistungen)", [a for a in expense if a["account"] in material], -1),
        _line("6. Personalaufwand", [a for a in expense if a["account"] in personnel], -1),
        _line("7. Abschreibungen", [a for a in expense if a["account"] in depreciation], -1),
        _line("8. Sonstige betriebliche Aufwendungen", [a for a in expense if a["account"] not in special], -1),
        _line("11. Sonstige Zinsen und ähnliche Erträge", [a for a in revenue if a["account"] in interest_inc], -1),
        _line("13. Zinsen und ähnliche Aufwendungen", [a for a in expense if a["account"] in interest_exp], -1),
        _line("14. Steuern vom Einkommen und vom Ertrag", [a for a in expense if a["account"] in taxes], -1),
    ]
    total_a = sum((x["amount"] for x in aktiva), ZERO)
    total_p = sum((x["amount"] for x in passiva), ZERO)
    return {"year": year, "chart": chart, "aktiva": aktiva, "passiva": passiva, "total_aktiva": total_a, "total_passiva": total_p,
            "balanced": total_a == total_p, "guv": guv, "result": result_year,
            "note": "Vereinfachter Entwurf aus dem Journal (§§ 266, 275 HGB) – Grundlage für den Steuerberater, kein geprüfter Abschluss."}


async def checklist(conn: AsyncConnection, tenant_id: UUID, ledger: Any, year: int) -> dict[str, Any]:
    from app.services import assets as av
    from app.services import tax_report

    start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
    q = {"t": tenant_id, "s": start, "e": end}
    drafts = await conn.scalar(text(
        "SELECT count(*) FROM receipts WHERE tenant_id = :t AND status = 'draft' AND receipt_date BETWEEN :s AND :e"), q)
    inv_drafts = await conn.scalar(text(
        "SELECT count(*) FROM invoices WHERE tenant_id = :t AND status = 'draft' AND created_at::date <= :e"), q)
    bank_open = await conn.scalar(text(
        "SELECT count(*) FROM bank_transactions WHERE tenant_id = :t AND status = 'open' AND booked_on BETWEEN :s AND :e"), q)
    open_dep = sum(1 for a in await av.list_assets(conn, tenant_id, year)
                   if not a["posted"] and (a["depreciation"] > 0 or a["disposal_book_value"] > 0))
    locked = set(await ledger.locked_periods(tenant_id=tenant_id)) if ledger is not None else set()
    active = [m for m in await tax_report.active_months(conn, tenant_id) if m.startswith(f"{year}-")]
    vat_open = [m for m in active if m not in locked]
    kinds = {r.kind for r in await conn.execute(text("SELECT DISTINCT kind FROM closing_entries WHERE tenant_id = :t AND year = :y"),
                                                {"t": tenant_id, "y": year})}
    closed = f"{year}-13" in locked
    items = [
        {"key": "receipts", "label": "Alle Belege des Jahres gebucht", "done": not drafts and not inv_drafts,
         "hint": f"{drafts} Belegentwürfe, {inv_drafts} Rechnungsentwürfe offen" if drafts or inv_drafts else "keine Entwürfe",
         "href": "/belege"},
        {"key": "bank", "label": "Bank abgestimmt", "done": not bank_open,
         "hint": f"{bank_open} Bankumsätze ohne Zuordnung" if bank_open else "alle Umsätze zugeordnet", "href": "/buchhaltung"},
        {"key": "vat", "label": "Umsatzsteuer-Voranmeldungen festgeschrieben", "done": not vat_open,
         "hint": f"offen: {', '.join(vat_open)}" if vat_open else "alle Monate gesperrt", "href": "/steuern"},
        {"key": "depreciation", "label": "Abschreibung gebucht", "done": open_dep == 0,
         "hint": f"{open_dep} Anlagegüter offen" if open_dep else "Anlagenspiegel vollständig", "href": "/anlagen"},
        {"key": "tax_provision", "label": "Steuerrückstellungen gebucht", "done": "tax_provision" in kinds,
         "hint": KIND_LABEL["tax_provision"], "href": None},
        {"key": "closing_costs", "label": "Rückstellung für Abschlusskosten gebucht", "done": "closing_costs" in kinds,
         "hint": "Kosten des Steuerberaters für diesen Abschluss", "href": None},
        {"key": "accruals", "label": "Rechnungsabgrenzungen geprüft", "done": bool({"prepaid", "deferred"} & kinds), "optional": True,
         "hint": "Im Voraus bezahlte Jahresbeiträge/Lizenzen bzw. vorab berechnete Leistungen", "href": None},
    ]
    blocking = [i for i in items if not i["done"] and not i.get("optional")]
    return {"year": year, "items": items, "closed": closed, "ready": not blocking}


async def tax_provision_proposal(conn: AsyncConnection, tenant_id: UUID, year: int) -> dict[str, Decimal]:
    from app.core.config import get_settings
    from app.services import tax_report

    o = await tax_report.year_overview(conn, tenant_id, year, trade_tax_multiplier=get_settings().trade_tax_multiplier, is_ug=True)
    t = o["income_tax"]
    return {"kst": D(t["kst"]), "solz": D(t["solz"]), "gewst": D(t["gewst"])}


async def list_entries(conn: AsyncConnection, tenant_id: UUID, year: int) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT c.id, c.kind, c.amount, c.account, c.description, c.created_by, c.created_at, j.number AS entry_number, "
        "r.number AS reversal_number FROM closing_entries c JOIN journal_entries j ON j.id = c.journal_entry_id "
        "LEFT JOIN journal_entries r ON r.id = c.reversal_entry_id WHERE c.tenant_id = :t AND c.year = :y ORDER BY c.created_at"),
        {"t": tenant_id, "y": year})
    return [dict(r) | {"label": KIND_LABEL[r.kind]} for r in rows.mappings()]


async def add_entry(conn: AsyncConnection, ledger: Any, tenant_id: UUID, year: int, data: dict[str, Any], actor: str) -> dict[str, Any]:
    """Abschlussbuchung zum 31.12. (Abschlussperiode). Rechnungsabgrenzungen werden am 1.1. des Folgejahres aufgelöst."""
    from app.services.accounting import Line, accounts, chart_of, post

    chart = await chart_of(conn, tenant_id)
    r = _roles(chart)
    kind, amount, desc = data["kind"], D(data["amount"]), data["description"]
    known = {a["number"]: a["type"] for a in await accounts(conn, chart)}
    account = data.get("account")
    if kind in ("other_provision", "prepaid") and known.get(account or "") != "expense":
        raise ConflictError("Bitte ein Aufwandskonto wählen")
    if kind == "deferred" and known.get(account or "") != "revenue":
        raise ConflictError("Bitte ein Erlöskonto wählen")

    if kind == "tax_provision":
        p = await tax_provision_proposal(conn, tenant_id, year)
        parts = {k: D(str(data[k])) if data.get(k) is not None else p[k] for k in ("kst", "solz", "gewst")}
        amount = sum(parts.values(), ZERO)
        if amount <= 0:
            raise ConflictError("Keine Ertragsteuer zu erwarten – keine Rückstellung nötig")
        lines = [Line(r["corp_tax"], debit=parts["kst"]), Line(r["soli"], debit=parts["solz"]), Line(r["trade_tax"], debit=parts["gewst"]),
                 Line(r["provision_corp_tax"], credit=parts["kst"] + parts["solz"]), Line(r["provision_trade_tax"], credit=parts["gewst"])]
    elif kind == "closing_costs":
        lines = [Line(r["closing_costs"], debit=amount), Line(r["provision_closing"], credit=amount)]
    elif kind == "other_provision":
        lines = [Line(account, debit=amount), Line(r["provision_other"], credit=amount)]  # type: ignore[arg-type]
    elif kind == "prepaid":
        lines = [Line(r["prepaid_expenses"], debit=amount), Line(account, credit=amount)]  # type: ignore[arg-type]
    else:
        lines = [Line(account, debit=amount), Line(r["deferred_income"], credit=amount)]  # type: ignore[arg-type]
    if amount <= 0:
        raise ConflictError("Betrag muss positiv sein")

    cid = uuid.uuid4()
    entry = await post(conn, ledger, tenant_id, chart=chart, entry_date=dt.date(year, 12, 31),
                       description=f"Abschluss {year}: {desc}"[:300], document_ref=f"AB-{year}", source_type="closing",
                       source_id=cid, lines=lines, method="manual", actor=actor, closing=True)
    reversal = None
    if kind in ("prepaid", "deferred"):
        # Auflösung im Folgejahr: Gegenbuchung am 1.1. (normale Periode)
        reversal = await post(conn, ledger, tenant_id, chart=chart, entry_date=dt.date(year + 1, 1, 1),
                              description=f"Auflösung Rechnungsabgrenzung {year}: {desc}"[:300], document_ref=f"AB-{year}",
                              source_type="closing", source_id=uuid.uuid5(cid, "reversal"), method="manual", actor=actor,
                              lines=[Line(ln.account, debit=ln.credit, credit=ln.debit) for ln in lines])
    await conn.execute(text(
        "INSERT INTO closing_entries (id, tenant_id, year, kind, amount, account, description, journal_entry_id, reversal_entry_id, "
        "created_by) VALUES (:id, :t, :y, :k, :a, :acc, :d, :e, :r, :by)"),
        {"id": cid, "t": tenant_id, "y": year, "k": kind, "a": amount, "acc": account, "d": desc, "e": entry, "r": reversal, "by": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="closing.entry", entity_type="closing_entry", entity_id=cid,
                       after={"year": year, "kind": kind, "amount": str(amount)})
    return {"id": str(cid), "amount": amount}


async def lock_year(conn: AsyncConnection, ledger: Any, tenant_id: UUID, year: int, actor: str, confirm_open: bool) -> dict[str, Any]:
    """Schreibt die Abschlussperiode fest: danach keine Abschlussbuchungen mehr für dieses Jahr."""
    c = await checklist(conn, tenant_id, ledger, year)
    if c["closed"]:
        raise ConflictError(f"Jahresabschluss {year} ist bereits festgeschrieben")
    if not c["ready"] and not confirm_open:
        raise ConflictError("Es sind noch Punkte der Abschluss-Checkliste offen")
    rec = await ledger.lock_period(tenant_id=tenant_id, period=f"{year}-13", sealed_by=f"user:{actor}")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="closing.locked", entity_type="year", entity_id=str(year),
                       after={"confirm_open": confirm_open, "open": [i["key"] for i in c["items"] if not i["done"]]})
    return {"year": year, "ledger_hash": rec.hash}


def _csv(rows: list[list[Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    for row in rows:
        w.writerow([str(x).replace(".", ",") if isinstance(x, Decimal) else x for x in row])
    return ("﻿" + buf.getvalue()).encode("utf-8")


async def export_package(conn: AsyncConnection, ledger: Any, tenant_id: UUID, year: int) -> bytes:
    """Übergabepaket für den Steuerberater (ZIP): Bilanz, GuV, Summen- und Saldenliste, Anlagenspiegel, Journal, Checkliste."""
    from app.services import assets as av
    from app.services.accounting import balances, journal

    st = await statements(conn, tenant_id, year)
    susa = await balances(conn, tenant_id, dt.date(year, 1, 1), dt.date(year, 12, 31))
    assets = await av.list_assets(conn, tenant_id, year)
    entries = await journal(conn, tenant_id, dt.date(year, 1, 1), dt.date(year, 12, 31))
    check = await checklist(conn, tenant_id, ledger, year)
    files: dict[str, bytes] = {
        "bilanz.csv": _csv([["Seite", "Position", "Konto", "Bezeichnung", "Betrag"]]
                           + [[side, ln["label"], a["account"], a["name"], a["amount"]]
                              for side, key in (("Aktiva", "aktiva"), ("Passiva", "passiva"))
                              for ln in st[key] for a in (ln["accounts"] or [{"account": "", "name": "", "amount": ln["amount"]}])]),
        "guv.csv": _csv([["Position", "Konto", "Bezeichnung", "Betrag"]]
                        + [[ln["label"], a["account"], a["name"], a["amount"]] for ln in st["guv"] for a in ln["accounts"]]
                        + [["Jahresüberschuss/-fehlbetrag", "", "", st["result"]]]),
        "summen_und_saldenliste.csv": _csv([["Konto", "Bezeichnung", "Soll", "Haben", "Saldo"]]
                                           + [[a["account"], a["name"], D(a["debit"]), D(a["credit"]), a["balance"]]
                                              for a in susa["accounts"]]),
        "anlagenspiegel.csv": _csv([["Nr.", "Bezeichnung", "Konto", "Anschaffung", "AK", "Buchwert 1.1.", "Zugang", "AfA", "Abgang",
                                     "Buchwert 31.12."]]
                                   + [[f"AV-{a['number']:04d}", a["name"], a["account"], a["acquisition_date"], D(a["cost"]),
                                       a["book_value_start"], a["addition"], a["depreciation"], a["disposal_book_value"],
                                       a["book_value_end"]]
                                      for a in assets]),
        "journal.csv": _csv([["Nr.", "Datum", "Belegdatum", "Text", "Konto", "Soll", "Haben", "Abschluss"]]
                            + [[e["number"], e["entry_date"], e["document_date"], e["description"], ln["account"], D(ln["debit"]),
                                D(ln["credit"]), "ja" if e.get("closing") else ""] for e in entries for ln in e["lines"]]),
        "checkliste.csv": _csv([["Punkt", "Erledigt", "Hinweis"]]
                               + [[i["label"], "ja" if i["done"] else "nein", i["hint"]] for i in check["items"]]),
        "LIESMICH.txt": (f"Übergabepaket Jahresabschluss {year} – erstellt mit OpenEmployed am {dt.date.today():%d.%m.%Y}\n\n"  # noqa: DTZ011
                         f"Kontenrahmen: {st['chart']}\nBilanz ausgeglichen: {'ja' if st['balanced'] else 'NEIN'}\n"
                         f"Jahresabschluss festgeschrieben: {'ja' if check['closed'] else 'nein'}\n\n{st['note']}\n"
                         "DATEV-Buchungsstapel separat über Buchhaltung → DATEV-Export erzeugen.\n").encode(),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(f"jahresabschluss_{year}/{name}", content)
    return buf.getvalue()
