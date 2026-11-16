"""DATEV-Export (EXTF-Format) für den Steuerberater.

Inhalt eines Exports (ZIP): Buchungsstapel (Kategorie 21), Debitoren/Kreditoren (Kategorie 16), Belegbilder und
E-Rechnungs-XML, Übergabeprotokoll. Jede Buchung wird genau einmal **final** exportiert; spätere Änderungen eines
Zeitraums ergeben einen Korrekturstapel (nie stilles Überschreiben). Archiv und SHA-256 werden unveränderlich
gespeichert und im Ledger festgeschrieben.

⚠️ Konten und BU-Schlüssel sind Vorschläge (SKR04) und müssen vom Steuerberater bestätigt werden; ein Testimport je
Steuerfall ist Abnahmekriterium (siehe docs/konzept/07).
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.expenses import BEWIRTUNG_DEDUCTIBLE
from app.services import audit

D = Decimal
CENT = D("0.01")


class DatevError(ValueError):
    pass


DEFAULT_TAX_KEYS: dict[str, tuple[str | None, str | None]] = {
    # Steuerfall: (Konto, BU-Schlüssel) – Ausgang: Automatikkonten ohne BU; Eingang: BU-Schlüssel, Konto = Kategorie
    "OUT_DE_19": ("4400", None),
    "OUT_DE_7": ("4300", None),
    "IN_DE_19": (None, "9"),
    "IN_DE_7": (None, "8"),
    "IN_BEWIRTUNG": (None, "9"),
    "IN_13B_EU_SERVICE": (None, None),       # vom Steuerberater einzutragen
    "IN_13B_DRITTLAND_SERVICE": (None, None),  # vom Steuerberater einzutragen
    "IN_IG_ERWERB": (None, None),
    "IN_EINFUHR": (None, None),
    "IN_OHNE_VST": (None, None),
    "IN_NICHT_ABZIEHBAR": (None, None),
}
NEEDS_KEY = frozenset({"IN_13B_EU_SERVICE", "IN_13B_DRITTLAND_SERVICE", "IN_IG_ERWERB", "IN_EINFUHR"})
HOSPITALITY_NONDEDUCTIBLE_ACCOUNT = "6644"  # SKR04: nicht abzugsfähige Bewirtungskosten


# ───────────────────────── Einstellungen ─────────────────────────

SETTINGS_FIELDS = ("consultant_number", "client_number", "chart", "account_length", "fiscal_year_start", "bank_account",
                   "receivables_account", "payables_account", "use_personal_accounts", "confirmed_by_advisor")


async def get_settings(conn: AsyncConnection, tenant_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text(f"SELECT {', '.join(SETTINGS_FIELDS)} FROM datev_settings WHERE tenant_id = :t"),
                              {"t": tenant_id})).mappings().one_or_none()
    settings = dict(row) if row else {"consultant_number": None, "client_number": None, "chart": "SKR04", "account_length": 4,
                                      "fiscal_year_start": 1, "bank_account": "1800", "receivables_account": "1200",
                                      "payables_account": "3300", "use_personal_accounts": True, "confirmed_by_advisor": False}
    keys = {r.tax_case: (r.account, r.bu_key) for r in await conn.execute(
        text("SELECT tax_case, account, bu_key FROM datev_tax_keys WHERE tenant_id = :t"), {"t": tenant_id})}
    from app.services.accounting import acc

    chart = str(settings["chart"])
    defaults = dict(DEFAULT_TAX_KEYS) | {"OUT_DE_19": (acc(chart, "revenue_19"), None), "OUT_DE_7": (acc(chart, "revenue_7"), None)}
    settings["tax_keys"] = [{"tax_case": c, "account": keys.get(c, d)[0], "bu_key": keys.get(c, d)[1], "required": c in NEEDS_KEY}
                            for c, d in defaults.items()]
    return settings


async def save_settings(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str) -> dict[str, Any]:
    from app.services.accounting import ROLES

    used = await conn.scalar(text("SELECT chart FROM journal_entries WHERE tenant_id = :t LIMIT 1"), {"t": tenant_id})
    if used and used != data["chart"]:
        raise DatevError(f"Es gibt bereits Buchungen im {used} – der Kontenrahmen kann nicht mehr gewechselt werden")

    # Beim Wechsel des Kontenrahmens Standardkonten mitwechseln (eigene Werte bleiben unangetastet)
    idx = 0 if data["chart"] == "SKR03" else 1
    for field_name, role in (("bank_account", "bank"), ("receivables_account", "receivables"), ("payables_account", "payables")):
        if data[field_name] == ROLES[role][1 - idx]:
            data = data | {field_name: ROLES[role][idx]}
    for k in data.get("tax_keys", []):
        if k["tax_case"] in ("OUT_DE_19", "OUT_DE_7") and k.get("account") == ROLES["revenue_" + k["tax_case"][-2:].lstrip("_")][1 - idx]:
            k["account"] = ROLES["revenue_" + k["tax_case"][-2:].lstrip("_")][idx]
    params = {k: data[k] for k in SETTINGS_FIELDS} | {"t": tenant_id, "a": actor}
    cols = ", ".join(SETTINGS_FIELDS)
    await conn.execute(text(
        f"INSERT INTO datev_settings (tenant_id, {cols}, updated_by) VALUES (:t, {', '.join(':' + f for f in SETTINGS_FIELDS)}, :a) "
        f"ON CONFLICT (tenant_id) DO UPDATE SET {', '.join(f'{f} = EXCLUDED.{f}' for f in SETTINGS_FIELDS)}, "
        "updated_by = EXCLUDED.updated_by, updated_at = now()"), params)
    for k in data.get("tax_keys", []):
        if k["tax_case"] not in DEFAULT_TAX_KEYS:
            raise DatevError(f"Unbekannter Steuerfall {k['tax_case']}")
        await conn.execute(text(
            "INSERT INTO datev_tax_keys (tenant_id, tax_case, account, bu_key, updated_by) VALUES (:t, :c, :acc, :bu, :a) "
            "ON CONFLICT (tenant_id, tax_case) DO UPDATE SET account = EXCLUDED.account, bu_key = EXCLUDED.bu_key, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()"),
            {"t": tenant_id, "c": k["tax_case"], "acc": k.get("account") or None, "bu": k.get("bu_key") or None, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="datev.settings_saved", entity_type="datev_settings",
                       entity_id=None, after=data)
    return await get_settings(conn, tenant_id)


# ───────────────────────── Buchungen ─────────────────────────

@dataclass
class Booking:
    amount: Decimal       # mit Vorzeichen bezogen auf „Konto im Soll“
    account: str
    contra: str
    bu: str | None
    date: dt.date
    doc1: str
    doc2: str
    text: str
    item_type: str
    item_id: UUID

    def row(self) -> list[str]:
        sign = "S" if self.amount >= 0 else "H"
        return [f"{abs(self.amount):.2f}".replace(".", ","), sign, "EUR", "", "", "", self.account, self.contra, self.bu or "",
                f"{self.date:%d%m}", _clean(self.doc1, 36), _clean(self.doc2, 12), "", _clean(self.text, 60)]


def _clean(v: str, n: int) -> str:
    v = re.sub(r"[\r\n;\"]", " ", v or "").strip()
    return v[:n]


BOOKING_HEADER = ["Umsatz (ohne Soll/Haben-Kz)", "Soll/Haben-Kennzeichen", "WKZ Umsatz", "Kurs", "Basis-Umsatz", "WKZ Basis-Umsatz",
                  "Konto", "Gegenkonto (ohne BU-Schlüssel)", "BU-Schlüssel", "Belegdatum", "Belegfeld 1", "Belegfeld 2", "Skonto",
                  "Buchungstext"]
PARTNER_HEADER = ["Konto", "Name (Adressattyp Unternehmen)", "Unternehmensgegenstand", "Name (Adressattyp natürl. Person)",
                  "Vorname (Adressattyp natürl. Person)", "Name (Adressattyp keine Angabe)", "Adressattyp", "Kurzbezeichnung",
                  "EU-Land", "EU-UStID"]


def debitor(customer_number: str) -> str:
    digits = re.sub(r"\D", "", customer_number)
    if not digits or not 10000 <= int(digits) <= 69999:
        raise DatevError(f"Kundennummer {customer_number} passt nicht in den Debitorenbereich 10000–69999")
    return digits


async def _creditor(conn: AsyncConnection, tenant_id: UUID, supplier_id: UUID) -> str:
    acc = await conn.scalar(text("SELECT datev_account FROM suppliers WHERE id = :id"), {"id": supplier_id})
    if acc is None:
        nxt = await conn.scalar(text("SELECT COALESCE(MAX(datev_account), 70000) + 1 FROM suppliers WHERE tenant_id = :t"),
                                {"t": tenant_id})
        await conn.execute(text("UPDATE suppliers SET datev_account = :a WHERE id = :id AND datev_account IS NULL"),
                           {"a": nxt, "id": supplier_id})
        acc = nxt
    return str(acc)


def _rate_case(rate: Decimal) -> str:
    return {D("19"): "OUT_DE_19", D("7"): "OUT_DE_7"}.get(D(rate).normalize(), "")


async def collect(conn: AsyncConnection, tenant_id: UUID, start: dt.date, end: dt.date,
                  settings: dict[str, Any]) -> tuple[list[Booking], list[str], dict[str, Any]]:
    """Sammelt alle noch nicht final exportierten Buchungen des Zeitraums. Rückgabe: Buchungen, Probleme, Partner."""
    keys = {k["tax_case"]: k for k in settings["tax_keys"]}
    personal = settings["use_personal_accounts"]
    bookings: list[Booking] = []
    problems: list[str] = []
    partners: dict[str, Any] = {}
    exported = "NOT EXISTS (SELECT 1 FROM datev_export_items x WHERE x.item_type = :it AND x.item_id = {col})"

    invoices = (await conn.execute(text(
        "SELECT i.id, i.number, i.kind, i.issue_date, i.due_date, i.snapshot, c.number AS customer_number, c.customer_type, "
        "c.company_name, c.first_name, c.last_name, c.vat_id FROM invoices i JOIN customers c ON c.id = i.customer_id "
        f"WHERE i.tenant_id = :t AND i.status = 'issued' AND i.issue_date BETWEEN :s AND :e AND {exported.format(col='i.id')} "
        "ORDER BY i.issue_date, i.number"), {"t": tenant_id, "s": start, "e": end, "it": "invoice"})).mappings().all()
    for inv in invoices:
        deb = debitor(inv["customer_number"]) if personal else settings["receivables_account"]
        if personal:
            partners[deb] = inv
        name = inv["company_name"] or f"{inv['first_name'] or ''} {inv['last_name'] or ''}".strip()
        for group in inv["snapshot"]["tax_breakdown"]:
            case = _rate_case(D(group["rate"]))
            acc = keys.get(case, {}).get("account")
            if not acc:
                problems.append(f"{inv['number']}: kein Erlöskonto für Steuersatz {group['rate']} %")
                continue
            gross = D(group["taxable_amount"]) + D(group["tax_amount"])
            bookings.append(Booking(gross, deb, acc, keys[case]["bu_key"], inv["issue_date"], inv["number"],
                                    f"{inv['due_date']:%d%m%y}" if inv["kind"] == "invoice" else "",
                                    f"{'Storno ' if inv['kind'] == 'cancellation' else ''}{name}", "invoice", inv["id"]))

    receipts = (await conn.execute(text(
        "SELECT r.id, r.kind, r.receipt_date, r.invoice_number, r.description, r.category, r.tax_case, r.net_amount, r.gross_amount, "
        "r.expense_account, r.supplier_id, s.name AS supplier_name, s.country_code, s.vat_id "
        "FROM receipts r JOIN suppliers s ON s.id = r.supplier_id "
        f"WHERE r.tenant_id = :t AND r.status = 'booked' AND r.receipt_date BETWEEN :s AND :e AND {exported.format(col='r.id')} "
        "ORDER BY r.receipt_date"), {"t": tenant_id, "s": start, "e": end, "it": "receipt"})).mappings().all()
    for r in receipts:
        cred = await _creditor(conn, tenant_id, r["supplier_id"]) if personal else settings["payables_account"]
        if personal:
            partners[cred] = r
        key = keys.get(r["tax_case"], {})
        if r["tax_case"] in NEEDS_KEY and not key.get("bu_key"):
            problems.append(f"Beleg {r['invoice_number'] or r['description']}: BU-Schlüssel für {r['tax_case']} fehlt (Steuerberater)")
            continue
        from app.services.accounting import category_account

        # Vorrang: Automatikkonto des Steuerschlüssels (Steuerberater) → Konto am Beleg (Lieferanten-Regel) → Kategorie
        expense_acc = key.get("account") or r["expense_account"] or category_account(settings["chart"], r["category"])
        amount = D(r["net_amount"]) if r["tax_case"] in ("IN_13B_EU_SERVICE", "IN_13B_DRITTLAND_SERVICE", "IN_IG_ERWERB") \
            else D(r["gross_amount"])
        doc = r["invoice_number"] or str(r["id"])[:8]
        label = f"{'Storno ' if r['kind'] == 'reversal' else ''}{r['supplier_name']} {r['description']}"
        if r["tax_case"] == "IN_BEWIRTUNG" or r["category"] == "hospitality":
            deductible = (amount * BEWIRTUNG_DEDUCTIBLE).quantize(CENT)
            bookings.append(Booking(deductible, expense_acc, cred, key.get("bu_key"), r["receipt_date"], doc, "", label,
                                    "receipt", r["id"]))
            nd_account = "4654" if settings["chart"] == "SKR03" else HOSPITALITY_NONDEDUCTIBLE_ACCOUNT
            bookings.append(Booking(amount - deductible, nd_account, cred, key.get("bu_key"), r["receipt_date"],
                                    doc, "", f"{label} (nicht abzf. 30 %)", "receipt", r["id"]))
        else:
            bookings.append(Booking(amount, expense_acc, cred, key.get("bu_key"), r["receipt_date"], doc, "", label, "receipt", r["id"]))

    payments = (await conn.execute(text(
        "SELECT p.id, p.paid_on, p.amount, p.reversed_at, i.number, c.number AS customer_number FROM payments p "
        "JOIN invoices i ON i.id = p.invoice_id JOIN customers c ON c.id = i.customer_id "
        f"WHERE p.tenant_id = :t AND p.paid_on BETWEEN :s AND :e AND {exported.format(col='p.id')} "
        "AND (p.reversed_at IS NULL) ORDER BY p.paid_on"), {"t": tenant_id, "s": start, "e": end, "it": "payment"})).mappings().all()
    for p in payments:
        contra = debitor(p["customer_number"]) if personal else settings["receivables_account"]
        bookings.append(Booking(D(p["amount"]), settings["bank_account"], contra, None, p["paid_on"], p["number"], "",
                                f"Zahlung {p['number']}", "payment", p["id"]))
    # Stornierte Zahlungen, die bereits exportiert wurden → Gegenbuchung
    reversals = (await conn.execute(text(
        "SELECT p.id, p.reversed_at, p.amount, i.number, c.number AS customer_number FROM payments p "
        "JOIN invoices i ON i.id = p.invoice_id JOIN customers c ON c.id = i.customer_id WHERE p.tenant_id = :t "
        "AND p.reversed_at IS NOT NULL AND EXISTS (SELECT 1 FROM datev_export_items x WHERE x.item_type = 'payment' AND x.item_id = p.id) "
        f"AND {exported.format(col='p.id')}"), {"t": tenant_id, "it": "payment_reversal"})).mappings().all()
    from app.services.invoices import BERLIN

    for p in reversals:
        day = p["reversed_at"].astimezone(BERLIN).date()
        if not start <= day <= end:
            continue
        contra = debitor(p["customer_number"]) if personal else settings["receivables_account"]
        bookings.append(Booking(-D(p["amount"]), settings["bank_account"], contra, None, day, p["number"], "",
                                f"Rückbuchung Zahlung {p['number']}", "payment_reversal", p["id"]))
    rpay = (await conn.execute(text(
        "SELECT r.id, r.paid_on, r.gross_amount, r.invoice_number, r.description, r.supplier_id FROM receipts r "
        f"WHERE r.tenant_id = :t AND r.status = 'booked' AND r.kind = 'expense' AND r.reversed_by IS NULL AND r.paid_on BETWEEN :s AND :e "
        f"AND {exported.format(col='r.id')}"), {"t": tenant_id, "s": start, "e": end, "it": "receipt_payment"})).mappings().all()
    for r in rpay:
        cred = await _creditor(conn, tenant_id, r["supplier_id"]) if personal else settings["payables_account"]
        bookings.append(Booking(D(r["gross_amount"]), cred, settings["bank_account"], None, r["paid_on"],
                                r["invoice_number"] or str(r["id"])[:8], "", f"Zahlung {r['description']}", "receipt_payment", r["id"]))
    return bookings, problems, partners


def _extf_header(category: int, name: str, version: int, settings: dict[str, Any], start: dt.date, end: dt.date,
                 label: str) -> list[str]:
    fy = dt.date(start.year if start.month >= settings["fiscal_year_start"] else start.year - 1, settings["fiscal_year_start"], 1)
    now = dt.datetime.now(dt.UTC)
    return ["EXTF", "700", str(category), name, str(version), now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}", "",
            "OE", "OpenEmployed", "", str(settings["consultant_number"]), str(settings["client_number"]), f"{fy:%Y%m%d}",
            str(settings["account_length"]), f"{start:%Y%m%d}" if category == 21 else "", f"{end:%Y%m%d}" if category == 21 else "",
            label[:30], "", "1" if category == 21 else "", "0" if category == 21 else "", "0" if category == 21 else "",
            "EUR" if category == 21 else "", "", "", "", "", "04", "", "", "", ""]


def _csv(header: list[str], columns: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(header)
    w.writerow(columns)
    w.writerows(rows)
    return buf.getvalue().encode("cp1252", errors="replace")


async def build_archive(conn: AsyncConnection, tenant_id: UUID, start: dt.date, end: dt.date, kind: str,
                        bookings: list[Booking], partners: dict[str, Any], settings: dict[str, Any]) -> bytes:
    label = f"OpenEmployed {start:%m/%Y}" + (" Korrektur" if kind == "correction" else "")
    stapel = _csv(_extf_header(21, "Buchungsstapel", 13, settings, start, end, label), BOOKING_HEADER, [b.row() for b in bookings])
    prows = []
    for acc, p in sorted(partners.items()):
        is_company = bool(p.get("company_name")) or "supplier_name" in p
        name = p.get("company_name") or p.get("supplier_name") or ""
        vat = (p.get("vat_id") or "").replace(" ", "")
        prows.append([acc, name if is_company else "", "", "" if is_company else (p.get("last_name") or ""),
                      "" if is_company else (p.get("first_name") or ""), "", "2" if is_company else "1", _clean(name, 15),
                      vat[:2] if vat else "", vat[2:] if vat else ""])
    partner_csv = _csv(_extf_header(16, "Debitoren/Kreditoren", 5, settings, start, end, label), PARTNER_HEADER, prows)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"EXTF_Buchungsstapel_{start:%Y%m}_{end:%Y%m}.csv", stapel)
        z.writestr("EXTF_Debitoren_Kreditoren.csv", partner_csv)
        seen: set[str] = set()
        for b in bookings:
            if b.item_type == "invoice" and b.doc1 not in seen:
                docs = (await conn.execute(text("SELECT pdf, xml FROM invoice_documents WHERE invoice_id = :id"),
                                           {"id": b.item_id})).one_or_none()
                if docs:
                    z.writestr(f"Belege/{_safe(b.doc1)}.pdf", bytes(docs.pdf))
                    z.writestr(f"Belege/{_safe(b.doc1)}.xml", bytes(docs.xml))
                seen.add(b.doc1)
            elif b.item_type == "receipt" and str(b.item_id) not in seen:
                f = (await conn.execute(text("SELECT f.content, f.mime_type FROM receipts r JOIN receipt_files f ON f.id = r.file_id "
                                             "WHERE r.id = :id"), {"id": b.item_id})).one_or_none()
                if f:
                    ext = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "application/xml": "xml"}[f.mime_type]
                    z.writestr(f"Belege/Eingang_{_safe(b.doc1)}_{str(b.item_id)[:8]}.{ext}", bytes(f.content))
                seen.add(str(b.item_id))
        sums: dict[str, Decimal] = {}
        for b in bookings:
            sums[b.account] = sums.get(b.account, D(0)) + b.amount
            sums[b.contra] = sums.get(b.contra, D(0)) - b.amount
        lines = [f"DATEV-Übergabe {label}", f"Zeitraum: {start:%d.%m.%Y} – {end:%d.%m.%Y}", f"Art: {kind}",
                 f"Buchungen: {len(bookings)}", f"Berater/Mandant: {settings['consultant_number']}/{settings['client_number']}", "",
                 "Saldo je Konto (Soll positiv):"]
        lines += [f"  {acc:>8}  {str(v.quantize(CENT)).replace('.', ','):>14}" for acc, v in sorted(sums.items())]
        lines += ["", "Hinweis: Konten/BU-Schlüssel sind vom Steuerberater zu bestätigen." if not settings["confirmed_by_advisor"]
                  else "Kontenzuordnung vom Steuerberater bestätigt.",
                  f"SHA-256 Buchungsstapel: {hashlib.sha256(stapel).hexdigest()}"]
        z.writestr("Protokoll.txt", "\r\n".join(lines).encode("utf-8"))
    return out.getvalue()


def _safe(v: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", v)[:60]


async def export(conn: AsyncConnection, ledger: Any, tenant_id: UUID, start: dt.date, end: dt.date, *, final: bool,
                 actor: str) -> dict[str, Any]:
    if end < start or (end - start).days > 366:
        raise DatevError("Zeitraum ungültig (max. ein Jahr)")
    settings = await get_settings(conn, tenant_id)
    if not settings["consultant_number"] or not settings["client_number"]:
        raise DatevError("Berater- und Mandantennummer fehlen (Einstellungen → DATEV)")
    # Exporte serialisieren: zwei gleichzeitige finale Exporte dürfen keine Buchung doppelt enthalten
    await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": 0x5E1B_0003})
    bookings, problems, partners = await collect(conn, tenant_id, start, end, settings)
    if problems and final:
        raise DatevError("Export nicht möglich: " + "; ".join(problems[:10]))
    if final and not bookings:
        raise DatevError("Keine neuen Buchungen im Zeitraum")
    already = await conn.scalar(text("SELECT 1 FROM datev_exports WHERE tenant_id = :t AND kind IN ('final','correction') "
                                     "AND period_from <= :e AND period_to >= :s LIMIT 1"), {"t": tenant_id, "s": start, "e": end})
    kind = ("correction" if already else "final") if final else "preview"
    archive = await build_archive(conn, tenant_id, start, end, kind, bookings, partners, settings)
    sha = hashlib.sha256(archive).hexdigest()
    result: dict[str, Any] = {"kind": kind, "bookings": len(bookings), "problems": problems, "sha256": sha, "archive": archive,
                              "id": None}
    if final:
        sealed = await ledger.seal(tenant_id=tenant_id, record_type="datev.exported", sealed_by=f"user:{actor}",
                                   payload={"period_from": start, "period_to": end, "kind": kind, "bookings": len(bookings), "sha256": sha})
        export_id = await conn.scalar(text(
            "INSERT INTO datev_exports (tenant_id, period_from, period_to, kind, bookings, archive, sha256, ledger_record_id, created_by) "
            "VALUES (:t, :s, :e, :k, :n, :a, :h, :r, :by) RETURNING id"),
            {"t": tenant_id, "s": start, "e": end, "k": kind, "n": len(bookings), "a": archive, "h": sha, "r": sealed.id, "by": actor})
        for item_type, item_id in {(b.item_type, b.item_id) for b in bookings}:
            await conn.execute(text("INSERT INTO datev_export_items (export_id, item_type, item_id) VALUES (:x, :t, :i)"),
                               {"x": export_id, "t": item_type, "i": item_id})
        await audit.record(conn, tenant_id=tenant_id, actor=actor, action="datev.exported", entity_type="datev_export",
                           entity_id=export_id, after={"kind": kind, "bookings": len(bookings), "sha256": sha})
        result["id"] = export_id
    return result


async def list_exports(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text("SELECT id, period_from, period_to, kind, bookings, sha256, created_by, created_at FROM datev_exports "
                                   "WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 200"), {"t": tenant_id})
    return [dict(r) for r in rows.mappings()]


async def get_archive(conn: AsyncConnection, tenant_id: UUID, export_id: UUID) -> tuple[dict[str, Any], bytes]:
    row = (await conn.execute(text("SELECT period_from, period_to, kind, archive FROM datev_exports WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": export_id})).mappings().one_or_none()
    if row is None:
        from app.services.masterdata import NotFoundError

        raise NotFoundError("Export nicht gefunden")
    return dict(row), bytes(row["archive"])
